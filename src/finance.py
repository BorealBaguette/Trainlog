import json
import logging
import requests
import stripe
from collections import defaultdict
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Tuple

from src.pg import pg_session
from py.currency import get_exchange_rate
from py.utils import load_config

logger = logging.getLogger(__name__)

class SimpleFinanceService:
    
    @staticmethod
    def setup_database():
        """Create finance tables"""
        sql_content = """
        -- Create finance schema
        CREATE SCHEMA IF NOT EXISTS finance;

        -- Expenses table (handles both recurring and one-time)
        CREATE TABLE IF NOT EXISTS finance.expenses (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            amount DECIMAL(10,2) NOT NULL CHECK (amount > 0),
            currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
            is_recurring BOOLEAN NOT NULL DEFAULT FALSE,
            
            -- For recurring expenses
            start_date DATE,
            end_date DATE,
            is_active BOOLEAN DEFAULT TRUE,
            
            -- For one-time expenses  
            expense_date DATE,
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Revenue table
        CREATE TABLE IF NOT EXISTS finance.revenue (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            amount DECIMAL(10,2) NOT NULL CHECK (amount > 0),
            currency VARCHAR(3) NOT NULL DEFAULT 'EUR',
            revenue_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Simple indexes
        CREATE INDEX IF NOT EXISTS idx_expenses_recurring ON finance.expenses(is_recurring, is_active);
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON finance.expenses(expense_date);
        CREATE INDEX IF NOT EXISTS idx_expenses_start_date ON finance.expenses(start_date, end_date);
        CREATE INDEX IF NOT EXISTS idx_revenue_date ON finance.revenue(revenue_date);

        -- Add constraints to ensure data integrity
        DO $ BEGIN
            ALTER TABLE finance.expenses ADD CONSTRAINT check_recurring_dates 
                CHECK (
                    (is_recurring = TRUE AND start_date IS NOT NULL AND expense_date IS NULL) OR
                    (is_recurring = FALSE AND expense_date IS NOT NULL AND start_date IS NULL)
                );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $;
        """
        
        with pg_session() as pg:
            pg.execute(sql_content)
        logger.info("Simple finance tables created")

    @staticmethod
    def add_recurring_expense(name: str, amount: float, currency: str = "EUR", 
                             start_date: date = None, end_date: date = None) -> int:
        """Add recurring expense"""
        if not start_date:
            start_date = date.today()
            
        with pg_session() as pg:
            result = pg.execute("""
                INSERT INTO finance.expenses (name, amount, currency, is_recurring, start_date, end_date)
                VALUES (:name, :amount, :currency, TRUE, :start_date, :end_date)
                RETURNING id
            """, {
                "name": name,
                "amount": amount,
                "currency": currency,
                "start_date": start_date,
                "end_date": end_date
            })
            return result.fetchone()[0]

    @staticmethod
    def add_one_time_expense(name: str, amount: float, currency: str = "EUR", 
                            expense_date: date = None) -> int:
        """Add one-time expense"""
        if not expense_date:
            expense_date = date.today()
            
        with pg_session() as pg:
            result = pg.execute("""
                INSERT INTO finance.expenses (name, amount, currency, is_recurring, expense_date)
                VALUES (:name, :amount, :currency, FALSE, :expense_date)
                RETURNING id
            """, {
                "name": name,
                "amount": amount,
                "currency": currency,
                "expense_date": expense_date
            })
            return result.fetchone()[0]

    @staticmethod
    def add_revenue(name: str, amount: float, currency: str = "EUR", 
                    revenue_date: date = None, external_id: str = None) -> int:
        """Add revenue entry, including an optional external ID."""
        if not revenue_date:
            revenue_date = date.today()
            
        with pg_session() as pg:
            result = pg.execute("""
                INSERT INTO finance.revenue (name, amount, currency, revenue_date, external_id)
                VALUES (:name, :amount, :currency, :revenue_date, :external_id)
                RETURNING id
            """, {
                "name": name,
                "amount": amount,
                "currency": currency,
                "revenue_date": revenue_date,
                "external_id": external_id
            })
            return result.fetchone()[0]

    @staticmethod
    def get_all_expenses() -> List[dict]:
        """Get all expenses"""
        with pg_session() as pg:
            result = pg.execute("""
                SELECT id, name, amount, currency, is_recurring, start_date, end_date, 
                       expense_date, is_active, created_at
                FROM finance.expenses
                ORDER BY created_at DESC
            """)
            columns = ['id', 'name', 'amount', 'currency', 'is_recurring', 'start_date', 'end_date', 'expense_date', 'is_active', 'created_at']
            return [dict(zip(columns, row)) for row in result.fetchall()]

    @staticmethod
    def get_all_revenue() -> List[dict]:
        """Get all revenue"""
        with pg_session() as pg:
            result = pg.execute("""
                SELECT id, external_id, name, amount, currency, revenue_date, created_at
                FROM finance.revenue
                ORDER BY revenue_date DESC
            """)
            columns = ['id', 'external_id', 'name', 'amount', 'currency', 'revenue_date', 'created_at']
            return [dict(zip(columns, row)) for row in result.fetchall()]

    @staticmethod
    def toggle_recurring_expense(expense_id: int):
        """Toggle active status of recurring expense"""
        with pg_session() as pg:
            result = pg.execute("""
                UPDATE finance.expenses 
                SET is_active = NOT is_active
                WHERE id = :id AND is_recurring = TRUE
                RETURNING name, is_active
            """, {"id": expense_id})
            row = result.fetchone()
            if row:
                return {"name": row[0], "is_active": row[1]}
            return None

    @staticmethod
    def delete_expense(expense_id: int):
        """Delete an expense"""
        with pg_session() as pg:
            result = pg.execute("""
                DELETE FROM finance.expenses WHERE id = :id
                RETURNING name
            """, {"id": expense_id})
            row = result.fetchone()
            return row[0] if row else None

    @staticmethod
    def delete_revenue(revenue_id: int):
        """Delete a revenue entry"""
        with pg_session() as pg:
            result = pg.execute("""
                DELETE FROM finance.revenue WHERE id = :id
                RETURNING name
            """, {"id": revenue_id})
            row = result.fetchone()
            return row[0] if row else None

    @staticmethod
    def calculate_monthly_data() -> Dict[str, Dict[str, float]]:
        """Calculate monthly financial data"""
        monthly_data = defaultdict(lambda: {"revenue": 0, "expenses": 0, "profit": 0})
        
        # Process revenue
        revenues = SimpleFinanceService.get_all_revenue()
        for revenue in revenues:
            month_key = revenue['revenue_date'].strftime("%Y-%m")
            amount_eur = revenue['amount']
            if revenue['currency'] != 'EUR':
                amount_eur = get_exchange_rate(
                    float(revenue['amount']), revenue['currency'], "EUR", revenue['revenue_date']
                )
            monthly_data[month_key]["revenue"] += float(amount_eur)
        
        # Process expenses
        expenses = SimpleFinanceService.get_all_expenses()
        for expense in expenses:
            amount_eur = expense['amount']
            if expense['currency'] != 'EUR':
                # Use appropriate date for conversion
                conv_date = expense['expense_date'] if not expense['is_recurring'] else expense['start_date']
                amount_eur = get_exchange_rate(
                    float(expense['amount']), expense['currency'], "EUR", conv_date
                )
            
            if expense['is_recurring'] and expense['is_active']:
                # Calculate for each month the recurring expense applies
                start_date = expense['start_date']
                end_date = expense['end_date'] if expense['end_date'] else date.today()
                
                current_date = start_date.replace(day=1)  # Start of month
                while current_date <= end_date:
                    month_key = current_date.strftime("%Y-%m")
                    monthly_data[month_key]["expenses"] += float(amount_eur)
                    current_date += relativedelta(months=1)
            
            elif not expense['is_recurring']:
                # One-time expense
                month_key = expense['expense_date'].strftime("%Y-%m")
                monthly_data[month_key]["expenses"] += float(amount_eur)
        
        # Calculate profit
        for month_key in monthly_data:
            data = monthly_data[month_key]
            data["profit"] = data["revenue"] - data["expenses"]
        
        return dict(monthly_data)

    @staticmethod
    def get_stripe_revenue() -> List[dict]:
        """
        Get all Stripe revenue from Payouts.
        This method fetches all successful payouts, which represent the
        net amount (post-fee) transferred to your bank account.
        """
        try:
            config = load_config()
            stripe.api_key = config["stripe"]["secret_key"]
            
            revenue_data = []

            # Fetch all Payouts that have been paid.
            # This is the single, most accurate source for post-fee revenue
            # as it represents the funds actually transferred to your bank account.
            payouts = stripe.Payout.list(limit=100, status='paid')
            
            for payout in payouts.auto_paging_iter():
                try:
                    # The payout amount is the net amount being transferred.
                    # It's in the smallest unit (e.g., cents), so we convert.
                    net_amount = float(payout.amount) / 100
                    currency = payout.currency.upper()
                    
                    # 'arrival_date' is the date the funds are expected to arrive
                    # in your bank account, which is a good timestamp to use.
                    payout_date = datetime.fromtimestamp(payout.arrival_date).date()
                    
                    # Create a descriptive name using the payout ID.
                    description = f"Stripe Payout"
                    
                    revenue_data.append({
                        "name": f"{description}",
                        "amount": net_amount,
                        "currency": currency,
                        "date": payout_date,
                        "external_id": f"{payout.id}"
                    })
                
                except Exception as e:
                    logger.error(f"Error processing Stripe Payout {payout.id}: {e}")
                    continue

            logger.info(f"Total Stripe revenue entries fetched from Payouts: {len(revenue_data)}")
            return revenue_data
            
        except Exception as e:
            logger.error(f"Error fetching Stripe revenue from Payouts: {e}")
            return []

    @staticmethod
    def sync_stripe_revenue() -> dict:
        """Sync Stripe revenue, avoiding duplicates."""
        try:
            stripe_data = SimpleFinanceService.get_stripe_revenue()
            if not stripe_data:
                return {"added": 0, "skipped": 0, "error": "No Stripe data retrieved"}
            
            existing_revenue = SimpleFinanceService.get_all_revenue()
            
            # Create a set of existing external IDs for fast deduplication lookup.
            # The external_id from `get_stripe_revenue` will be 'po_<id>'.
            existing_external_ids = {
                rev['external_id'] for rev in existing_revenue
                if rev.get('external_id', '').startswith('po_')
            }
            
            added_count = 0
            skipped_count = 0
            total_amount_added = 0
            
            for item in stripe_data:
                # Use the 'external_id' as the unique key for deduplication.
                if item['external_id'] in existing_external_ids:
                    skipped_count += 1
                    logger.debug(f"Skipping duplicate: {item['name']} for {item['date']}")
                    continue
                
                # FIX: Pass the external_id to the add_revenue function to ensure it is stored,
                # which allows the deduplication logic to work on subsequent runs.
                revenue_id = SimpleFinanceService.add_revenue(
                    name=item['name'],
                    amount=item['amount'],
                    currency=item['currency'],
                    revenue_date=item['date'],
                    external_id=item['external_id']
                )
                added_count += 1
                total_amount_added += item['amount']
                logger.info(f"Added Stripe revenue: {item['name']} - {item['amount']}{item['currency']} for {item['date']} (#{revenue_id})")
            
            return {
                "added": added_count,
                "skipped": skipped_count,
                "total_fetched": len(stripe_data),
                "total_amount_added": total_amount_added
            }
            
        except Exception as e:
            logger.error(f"Error syncing Stripe revenue: {e}")
            return {"added": 0, "skipped": 0, "error": str(e)}

    @staticmethod
    def get_stripe_outstanding_balance() -> dict:
        """
        Get the current outstanding balance from Stripe that will be paid out.
        This includes the pending balance that hasn't been transferred yet.
        """
        try:
            config = load_config()
            stripe.api_key = config["stripe"]["secret_key"]
            
            # Get the current balance from Stripe
            balance = stripe.Balance.retrieve()
            
            outstanding_data = {
                "total_pending": 0.0,
                "currency": "EUR",  # Default, will be updated
                "next_payout_date": None,
                "breakdown": []
            }
            
            # Process available balance (money ready to be paid out)
            for balance_item in balance.available:
                if balance_item.amount > 0:  # Only positive balances
                    amount = float(balance_item.amount) / 100  # Convert from cents
                    currency = balance_item.currency.upper()
                    
                    outstanding_data["breakdown"].append({
                        "type": "available",
                        "amount": amount,
                        "currency": currency
                    })
                    
                    # Convert to EUR if needed for totaling
                    if currency == "EUR":
                        outstanding_data["total_pending"] += amount
                        outstanding_data["currency"] = currency
                    else:
                        # Convert to EUR using current exchange rate
                        eur_amount = get_exchange_rate(amount, currency, "EUR", date.today())
                        outstanding_data["total_pending"] += eur_amount
            
            # Process pending balance (money being processed)
            for balance_item in balance.pending:
                if balance_item.amount > 0:  # Only positive balances
                    amount = float(balance_item.amount) / 100  # Convert from cents
                    currency = balance_item.currency.upper()
                    
                    outstanding_data["breakdown"].append({
                        "type": "pending",
                        "amount": amount,
                        "currency": currency
                    })
                    
                    # Convert to EUR if needed for totaling
                    if currency == "EUR":
                        outstanding_data["total_pending"] += amount
                    else:
                        # Convert to EUR using current exchange rate
                        eur_amount = get_exchange_rate(amount, currency, "EUR", date.today())
                        outstanding_data["total_pending"] += eur_amount
            
            # Estimate next payout date based on Stripe's typical schedule
            # Stripe typically pays out around the 20th of each month for the previous month's earnings
            try:
                today = date.today()
                
                # If we're before the 20th of current month, the next payout is likely the 20th of this month
                # If we're after the 20th, the next payout is likely the 20th of next month
                if today.day < 20:
                    # Next payout is this month's 20th
                    estimated_payout = date(today.year, today.month, 20)
                else:
                    # Next payout is next month's 20th
                    if today.month == 12:
                        estimated_payout = date(today.year + 1, 1, 20)
                    else:
                        estimated_payout = date(today.year, today.month + 1, 20)
                
                outstanding_data["next_payout_date"] = estimated_payout
                
            except Exception as e:
                logger.warning(f"Could not estimate next payout date: {e}")
                # Fallback to 20th of next month
                today = date.today()
                if today.month == 12:
                    outstanding_data["next_payout_date"] = date(today.year + 1, 1, 20)
                else:
                    outstanding_data["next_payout_date"] = date(today.year, today.month + 1, 20)
            
            logger.info(f"Outstanding Stripe balance: {outstanding_data['total_pending']:.2f} EUR, next payout: {outstanding_data['next_payout_date']}")
            return outstanding_data
            
        except Exception as e:
            logger.error(f"Error fetching Stripe outstanding balance: {e}")
            return {
                "total_pending": 0.0,
                "currency": "EUR",
                "next_payout_date": None,
                "breakdown": [],
                "error": str(e)
            }

    @staticmethod
    def calculate_monthly_data_with_outstanding() -> Dict[str, Dict[str, float]]:
        """
        Calculate monthly financial data including outstanding Stripe revenue
        for the month it will be transferred.
        """
        # Get the base monthly data
        monthly_data = SimpleFinanceService.calculate_monthly_data()
        
        # Get outstanding Stripe balance
        outstanding = SimpleFinanceService.get_stripe_outstanding_balance()
        
        # Add outstanding amount to the current month (where it was earned),
        # not the future payout month — it should appear as pending now.
        if outstanding["total_pending"] > 0:
            current_month_key = date.today().strftime("%Y-%m")

            # Ensure the month exists in our data
            if current_month_key not in monthly_data:
                monthly_data[current_month_key] = {"revenue": 0, "expenses": 0, "profit": 0}

            # Add outstanding amount to revenue
            monthly_data[current_month_key]["revenue"] += outstanding["total_pending"]
            monthly_data[current_month_key]["profit"] = (
                monthly_data[current_month_key]["revenue"] -
                monthly_data[current_month_key]["expenses"]
            )

            logger.info(f"Added {outstanding['total_pending']:.2f} EUR outstanding revenue to {current_month_key}")
        
        return monthly_data

def get_finances() -> Tuple:
    """Legacy function for existing dashboard compatibility with temporary classification"""
    monthly_data = SimpleFinanceService.calculate_monthly_data()

    # Get raw expenses for classification
    expenses = SimpleFinanceService.get_all_expenses()

    # Sort months and prepare data
    sorted_months = sorted(monthly_data.keys())

    labels = sorted_months
    revenue_data_points = [monthly_data[month]["revenue"] for month in sorted_months]
    hosting_spending_data_points = [0] * len(sorted_months)
    translation_spending_data_points = [0] * len(sorted_months)
    api_subscription_spending_data_points = [0] * len(sorted_months)
    api_topup_spending_data_points = [0] * len(sorted_months)  # Kept for compatibility
    profit_data_points = [monthly_data[month]["profit"] for month in sorted_months]

    # Classify expenses
    for expense in expenses:
        amount_eur = expense["amount"]
        if expense["currency"] != "EUR":
            conv_date = expense["expense_date"] if not expense["is_recurring"] else expense["start_date"]
            amount_eur = get_exchange_rate(
                float(expense["amount"]), expense["currency"], "EUR", conv_date
            )

        # Determine months impacted
        months = []
        if expense["is_recurring"] and expense["is_active"]:
            start_date = expense["start_date"]
            end_date = expense["end_date"] if expense["end_date"] else date.today()
            current_date = start_date.replace(day=1)
            while current_date <= end_date:
                months.append(current_date.strftime("%Y-%m"))
                current_date += relativedelta(months=1)
        else:
            months.append(expense["expense_date"].strftime("%Y-%m"))

        # Classify
        name_lower = expense["name"].lower()
        if "translation" in name_lower:
            target = translation_spending_data_points
        elif "ovh" in name_lower or "infomaniak" in name_lower:
            target = hosting_spending_data_points
        elif "api" in name_lower:
            target = api_subscription_spending_data_points
        else:
            target = hosting_spending_data_points  # Default to hosting for compatibility

        for m in months:
            if m in sorted_months:
                idx = sorted_months.index(m)
                target[idx] -= float(amount_eur)  # Negative for spending

    total_spending_data_points = [
        h + t + a + api_topup
        for h, t, a, api_topup in zip(
            hosting_spending_data_points,
            translation_spending_data_points,
            api_subscription_spending_data_points,
            api_topup_spending_data_points,
        )
    ]

    totals = {
        "revenue": round(sum(revenue_data_points)),
        "hosting_spending": round(sum(hosting_spending_data_points)),
        "translation_spending": round(sum(translation_spending_data_points)),
        "api_subscription_spending": round(sum(api_subscription_spending_data_points)),
        "api_topup_spending": round(sum(api_topup_spending_data_points)),
        "total_spending": round(-sum(total_spending_data_points)),  # convert back to positive
        "profit": round(sum(profit_data_points)),
    }

    return (
        labels,
        revenue_data_points,
        hosting_spending_data_points,
        translation_spending_data_points,
        api_subscription_spending_data_points,
        api_topup_spending_data_points,
        total_spending_data_points,
        profit_data_points,
        totals,
    )
