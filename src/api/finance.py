# src/api/finance.py - Fixed routes with proper separation
import csv
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from src.finance import SimpleFinanceService, get_finances
from src.utils import owner_required, getUser, lang
from py.currency import get_exchange_rate
from dateutil.relativedelta import relativedelta


finance_blueprint = Blueprint('finance', __name__, url_prefix='/admin')

@finance_blueprint.route("/finances")
@owner_required
def finances():
    """Main finances dashboard with CHART - now includes outstanding Stripe revenue"""
    try:
        # Auto-sync Stripe whenever the previous month is empty but earlier months have revenue.
        # This catches the "money - gap - money" pattern regardless of when the page is opened.
        revenues = SimpleFinanceService.get_all_revenue()
        today = date.today()
        prev_month = (today.replace(day=1) - relativedelta(days=1)).strftime("%Y-%m")
        revenue_months = {r["revenue_date"].strftime("%Y-%m") for r in revenues}
        has_earlier_revenue = any(m < prev_month for m in revenue_months)
        if has_earlier_revenue and prev_month not in revenue_months:
            try:
                result = SimpleFinanceService.sync_stripe_revenue()
                if result.get("added", 0) > 0:
                    amount = result.get("total_amount_added", 0)
                    flash(
                        f"Auto-synced Stripe: added {result['added']} entries "
                        f"({amount:.2f}€)",
                        "success",
                    )
            except Exception as sync_err:
                flash(f"Auto-sync failed: {sync_err}", "error")

        # Get outstanding Stripe info for display
        outstanding_info = SimpleFinanceService.get_stripe_outstanding_balance()

        # Use the enhanced calculation that includes outstanding revenue
        monthly_data = SimpleFinanceService.calculate_monthly_data_with_outstanding()
        
        # Get raw expenses for classification (same as before)
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
        
        # Classify expenses (same logic as before)
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
        
        return render_template(
            "admin/finances.html",
            labels=labels,
            revenue_data_points=revenue_data_points,
            hosting_spending_data_points=hosting_spending_data_points,
            translation_spending_data_points=translation_spending_data_points,
            api_subscription_spending_data_points=api_subscription_spending_data_points,
            api_topup_spending_data_points=api_topup_spending_data_points,
            total_spending_data_points=total_spending_data_points,
            profit_data_points=profit_data_points,
            totals=totals,
            outstanding_info=outstanding_info,  # Pass outstanding info to template
            username=getUser(),
            title="Finances",
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )
    except Exception as e:
        flash(f"Error loading financial data: {str(e)}", "error")
        return render_template("admin/error.html", error=str(e))

@finance_blueprint.route("/finances/manage")
@owner_required
def manage():
    """Simple finance management page - separate from dashboard"""
    try:
        expenses = SimpleFinanceService.get_all_expenses()
        revenues = SimpleFinanceService.get_all_revenue()
        outstanding_info = SimpleFinanceService.get_stripe_outstanding_balance()
        
        # Separate recurring and one-time expenses
        recurring_expenses = [e for e in expenses if e['is_recurring']]
        one_time_expenses = [e for e in expenses if not e['is_recurring']]
        
        return render_template(
            "admin/manage_finances.html",
            recurring_expenses=recurring_expenses,
            one_time_expenses=one_time_expenses,
            revenues=revenues,
            outstanding_info=outstanding_info,  # Pass outstanding info
            username=getUser(),
            title="Manage Finances",
            **lang[session["userinfo"]["lang"]],
            **session["userinfo"],
        )
    except Exception as e:
        flash(f"Error loading management page: {str(e)}", "error")
        return render_template("admin/error.html", error=str(e))

# Add a new route to get outstanding info via AJAX (optional)
@finance_blueprint.route("/finances/outstanding", methods=["GET"])
@owner_required
def get_outstanding():
    """API endpoint to get current outstanding Stripe balance"""
    try:
        outstanding_info = SimpleFinanceService.get_stripe_outstanding_balance()
        return {
            "success": True,
            "data": outstanding_info
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }, 500

@finance_blueprint.route("/finances/add-recurring", methods=["POST"])
@owner_required
def add_recurring():
    """Add recurring expense"""
    try:
        name = request.form.get("name", "").strip()
        amount = float(request.form.get("amount"))
        currency = request.form.get("currency", "EUR")
        start_date = datetime.strptime(request.form.get("start_date"), "%Y-%m-%d").date()
        end_date = None
        if request.form.get("end_date"):
            end_date = datetime.strptime(request.form.get("end_date"), "%Y-%m-%d").date()
        
        if not name or amount <= 0:
            flash("Name and positive amount required", "error")
            return redirect(url_for("finance.manage"))
        
        expense_id = SimpleFinanceService.add_recurring_expense(name, amount, currency, start_date, end_date)
        flash(f"Added recurring expense: {name} (#{expense_id})", "success")
        
    except Exception as e:
        flash(f"Error adding expense: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/add-onetime", methods=["POST"])
@owner_required
def add_onetime():
    """Add one-time expense"""
    try:
        name = request.form.get("name", "").strip()
        amount = float(request.form.get("amount"))
        currency = request.form.get("currency", "EUR")
        expense_date = datetime.strptime(request.form.get("expense_date"), "%Y-%m-%d").date()
        
        if not name or amount <= 0:
            flash("Name and positive amount required", "error")
            return redirect(url_for("finance.manage"))
        
        expense_id = SimpleFinanceService.add_one_time_expense(name, amount, currency, expense_date)
        flash(f"Added one-time expense: {name} (#{expense_id})", "success")
        
    except Exception as e:
        flash(f"Error adding expense: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/add-revenue", methods=["POST"])
@owner_required
def add_revenue():
    """Add revenue entry"""
    try:
        name = request.form.get("name", "").strip()
        amount = float(request.form.get("amount"))
        currency = request.form.get("currency", "EUR")
        revenue_date = datetime.strptime(request.form.get("revenue_date"), "%Y-%m-%d").date()
        
        if not name or amount <= 0:
            flash("Name and positive amount required", "error")
            return redirect(url_for("finance.manage"))
        
        revenue_id = SimpleFinanceService.add_revenue(name, amount, currency, revenue_date)
        flash(f"Added revenue: {name} (#{revenue_id})", "success")
        
    except Exception as e:
        flash(f"Error adding revenue: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/toggle/<int:expense_id>", methods=["POST"])
@owner_required
def toggle_expense(expense_id):
    """Toggle recurring expense active status"""
    try:
        result = SimpleFinanceService.toggle_recurring_expense(expense_id)
        if result:
            status = "activated" if result['is_active'] else "deactivated"
            flash(f"Expense '{result['name']}' {status}", "success")
        else:
            flash("Expense not found or not recurring", "error")
    except Exception as e:
        flash(f"Error toggling expense: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/delete-expense/<int:expense_id>", methods=["POST"])
@owner_required
def delete_expense(expense_id):
    """Delete expense"""
    try:
        name = SimpleFinanceService.delete_expense(expense_id)
        if name:
            flash(f"Deleted expense: {name}", "success")
        else:
            flash("Expense not found", "error")
    except Exception as e:
        flash(f"Error deleting expense: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/delete-revenue/<int:revenue_id>", methods=["POST"])
@owner_required
def delete_revenue(revenue_id):
    """Delete revenue"""
    try:
        name = SimpleFinanceService.delete_revenue(revenue_id)
        if name:
            flash(f"Deleted revenue: {name}", "success")
        else:
            flash("Revenue not found", "error")
    except Exception as e:
        flash(f"Error deleting revenue: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

@finance_blueprint.route("/finances/sync-stripe", methods=["POST"])
@owner_required
def sync_stripe():
    """Sync Buy Me a Coffee data with duplicate prevention and amount tracking"""
    try:
        result = SimpleFinanceService.sync_stripe_revenue()
        
        if "error" in result:
            flash(f"Stripe sync error: {result['error']}", "error")
        else:
            message_parts = []
            if result["added"] > 0:
                amount_str = f" ({result.get('total_amount_added', 0):.2f}€)" if 'total_amount_added' in result else ""
                message_parts.append(f"Added {result['added']} new entries{amount_str}")
            if result["skipped"] > 0:
                message_parts.append(f"{result['skipped']} already imported")
            
            if result["added"] == 0 and result["skipped"] == 0:
                flash("No Stripe data found to sync", "warning")
            else:
                total_msg = f" (Total fetched: {result['total_fetched']})"
                flash(f"Stripe sync completed: {', '.join(message_parts)}{total_msg}", "success")
        
    except Exception as e:
        flash(f"Error syncing Stripe data: {str(e)}", "error")
    
    return redirect(url_for("finance.manage"))

    import csv
from io import StringIO

@finance_blueprint.route("/finances/ingest-csv", methods=["POST"])
@owner_required
def ingest_csv():
    """Ingest hosting, translation, and API subscription expenses from CSV"""
    try:
        if "file" not in request.files:
            flash("No CSV file uploaded", "error")
            return redirect(url_for("finance.manage"))

        file = request.files["file"]
        if file.filename == "":
            flash("No file selected", "error")
            return redirect(url_for("finance.manage"))

        stream = StringIO(file.stream.read().decode("utf-8"))
        reader = csv.DictReader(stream)

        added_count = 0
        skipped_count = 0

        for row in reader:
            expense_type = row.get("type", "").strip()
            amount = float(row.get("amount", 0))
            currency = row.get("currency", "EUR").upper()
            from_date = row.get("from_date")
            to_date = row.get("to_date")
            date_field = row.get("date")

            if not expense_type or amount <= 0:
                skipped_count += 1
                continue

            # Recurring expense (hosting or API subscription)
            if from_date:
                start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
                end_date = None
                if to_date and to_date.strip().lower() != "none":
                    end_date = datetime.strptime(to_date, "%Y-%m-%d").date()

                SimpleFinanceService.add_recurring_expense(
                    name=expense_type,
                    amount=amount,
                    currency=currency,
                    start_date=start_date,
                    end_date=end_date,
                )
                added_count += 1

            # One-time expense (translation)
            elif date_field:
                expense_date = datetime.strptime(date_field, "%Y-%m-%d %H:%M:%S").date()
                SimpleFinanceService.add_one_time_expense(
                    name=expense_type or "Translation",
                    amount=amount,
                    currency=currency,
                    expense_date=expense_date,
                )
                added_count += 1
            else:
                skipped_count += 1

        flash(f"CSV ingestion complete: {added_count} added, {skipped_count} skipped", "success")
    except Exception as e:
        raise(e)
        flash(f"Error ingesting CSV: {str(e)}", "error")

    return redirect(url_for("finance.manage"))
