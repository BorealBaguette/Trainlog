import os
import sqlite3

import pandas as pd


def table_exists(cursor, table_name):
    cursor.execute(
        f"SELECT count(name) FROM sqlite_master WHERE type='table' AND name='{table_name}'"
    )
    return cursor.fetchone()[0] == 1


def set_trips_visibility(cursor):
    cursor.execute("""
    UPDATE trip SET visibility =
        CASE TYPE
            WHEN 'accommodation' THEN 'private'
            WHEN 'aerialway' THEN 'public'
            WHEN 'bus' THEN 'public'
            WHEN 'car' THEN 'private'
            WHEN 'cycle' THEN 'private'
            WHEN 'ferry' THEN 'public'
            WHEN 'helicopter' THEN 'public'
            WHEN 'metro' THEN 'public'
            WHEN 'poi' THEN 'private'
            WHEN 'restaurant' THEN 'private'
            WHEN 'train' THEN 'public'
            WHEN 'tram' THEN 'public'
            WHEN 'walk' THEN 'public'
            ELSE 'public'
        END
    WHERE visibility IS NULL;
    """)


def init_data(path):
    lock_file = "init_data.lock"

    if os.path.exists(lock_file):
        print("Initialization already running. Skipping.")
    else:
        with open(lock_file, "w") as f:
            f.write("Initialization in progress")

        # Create a connection to the SQLite database
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        folder_path = "base_data"
        # Iterate over all files in the folder
        for file_name in os.listdir(folder_path):
            if file_name.endswith(".csv"):
                # Define the table name based on the file name
                table_name = os.path.splitext(file_name)[0]

                # Check if the table already exists
                if not table_exists(cursor, table_name):
                    # Read the CSV file into a pandas DataFrame
                    file_path = os.path.join(folder_path, file_name)
                    df = pd.read_csv(file_path)

                    # Write the DataFrame to the SQLite table
                    df.to_sql(table_name, conn, if_exists="replace", index=False)
                    print(f"Table '{table_name}' created successfully.")

        # migrate trip visibility (set default value)
        set_trips_visibility(cursor)

        # Commit the changes
        conn.commit()
        conn.close()
        if os.path.exists(lock_file):
            os.remove(lock_file)


class TableColumn:
    def __init__(self, name, data_type, constraint=""):
        self.name = name
        self.data_type = data_type
        self.constraint = constraint

    def __str__(self):
        return f"{self.name} {self.data_type} {self.constraint}".strip()


class DatabaseTable:
    def __init__(self, name, primary_key, columns=[]):
        self.name = name
        self.columns = []
        self.primary_key = primary_key
        self.add_columns(columns)

    def add_column(self, name, data_type, constraint=""):
        self.columns.append(TableColumn(name, data_type, constraint))

    def add_columns(self, columns):
        for name, data_type, *constraint in columns:
            constraint = constraint[0] if constraint else ""
            self.add_column(name, data_type, constraint)

    def create_table_sql(self):
        column_definitions = ", ".join(str(column) for column in self.columns)
        # Check if primary key is already set in any column
        if not any("PRIMARY KEY" in str(column) for column in self.columns):
            column_definitions += f", PRIMARY KEY ({self.primary_key})"
        return f"CREATE TABLE IF NOT EXISTS {self.name} ({column_definitions});"

    def add_column_sql(self, column):
        return f"ALTER TABLE {self.name} ADD COLUMN {column};"


class DatabaseManager:
    def __init__(self, db_path):
        self.db_connection = sqlite3.connect(db_path)
        self.tables = []

    def add_table(self, table):
        self.tables.append(table)

    def setup_database(self):
        cursor = self.db_connection.cursor()
        for table in self.tables:
            try:
                # Check if the table exists before attempting to create
                cursor.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table.name}';"
                )
                exists_before = cursor.fetchone() is not None

                # This will execute the CREATE TABLE statement
                cursor.execute(table.create_table_sql())

                # Check if the table exists now
                cursor.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table.name}';"
                )
                exists_after = cursor.fetchone() is not None

                if exists_after and not exists_before:
                    print(f"Created table {table.name}")
            except sqlite3.OperationalError as e:
                print(f"Error creating table {table.name}: {e}")
            # Check existing columns and add new columns if necessary
            self.update_table_columns(cursor, table)
        self.db_connection.commit()

    def update_table_columns(self, cursor, table):
        cursor.execute(f"PRAGMA table_info({table.name})")
        existing_columns = {
            row[1] for row in cursor.fetchall()
        }  # Fetch existing column names
        for column in table.columns:
            if column.name not in existing_columns:
                try:
                    cursor.execute(table.add_column_sql(str(column)))
                    print(f"Added new column {column.name} to table {table.name}")
                except sqlite3.OperationalError as e:
                    print(
                        f"Error adding column {column.name} to table {table.name}: {e}"
                    )

    def close(self):
        self.db_connection.close()


def init_main(path):
    db_manager = DatabaseManager(path)

    trip_columns = [
        ("uid", "INTEGER NOT NULL"),
        ("username", "VARCHAR(100) NOT NULL"),
        ("origin_station", "VARCHAR(100) NOT NULL"),
        ("destination_station", "VARCHAR(100) NOT NULL"),
        ("start_datetime", "DATETIME"),
        ("end_datetime", "DATETIME"),
        ("estimated_trip_duration", "INTEGER"),
        ("manual_trip_duration", "INTEGER"),
        ("trip_length", "INTEGER NOT NULL"),
        ("operator", "VARCHAR(100)"),
        ("countries", "VARCHAR(100)"),
        ("utc_start_datetime", "DATETIME"),
        ("utc_end_datetime", "DATETIME"),
        ("line_name", "VARCHAR(100)"),
        ("created", "DATETIME"),
        ("last_modified", "DATETIME"),
        ("type", "VARCHAR(100) DEFAULT 'train'"),
        ("material_type", "VARCHAR(100)"),
        ("seat", "VARCHAR(100)"),
        ("reg", "TEXT"),
        ("waypoints", "TEXT"),
        ("notes", "TEXT"),
        ("price", "FLOAT"),
        ("ticket_id", "INT"),
        ("currency", "TEXT"),
        ("purchasing_date", "DATETIME"),
        ("visibility", "TEXT"),
    ]
    manual_stations_columns = [
        ("uid", "INTEGER NOT NULL UNIQUE"),
        ("name", "TEXT NOT NULL"),
        ("lat", "FLOAT NOT NULL"),
        ("lng", "FLOAT NOT NULL"),
        ("creator", "INTEGER NOT NULL"),
        ("station_type", "TEXT NOT NULL"),
    ]
    percents_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("username", "TEXT NOT NULL"),
        ("cc", "TEXT NOT NULL"),
        ("percent", "INTEGER NOT NULL"),
    ]
    currency_columns = [
        ("rate_date", "DATE NOT NULL UNIQUE"),
        ("AUD", "FLOAT"),
        ("BGN", "FLOAT"),
        ("BRL", "FLOAT"),
        ("CAD", "FLOAT"),
        ("CHF", "FLOAT"),
        ("CNY", "FLOAT"),
        ("CZK", "FLOAT"),
        ("DKK", "FLOAT"),
        ("GBP", "FLOAT"),
        ("HKD", "FLOAT"),
        ("HUF", "FLOAT"),
        ("IDR", "FLOAT"),
        ("ILS", "FLOAT"),
        ("INR", "FLOAT"),
        ("ISK", "FLOAT"),
        ("JPY", "FLOAT"),
        ("KRW", "FLOAT"),
        ("MXN", "FLOAT"),
        ("MYR", "FLOAT"),
        ("NOK", "FLOAT"),
        ("NZD", "FLOAT"),
        ("PHP", "FLOAT"),
        ("PLN", "FLOAT"),
        ("RON", "FLOAT"),
        ("SEK", "FLOAT"),
        ("SGD", "FLOAT"),
        ("THB", "FLOAT"),
        ("TRY", "FLOAT"),
        ("USD", "FLOAT"),
        ("ZAR", "FLOAT"),
        ("RUB", "FLOAT"),
    ]
    tickets_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("name", "TEXT NOT NULL"),
        ("username", "TEXT NOT NULL"),
        ("price", "FLOAT NOT NULL"),
        ("currency", "TEXT NOT NULL"),
        ("purchasing_date", "DATETIME NOT NULL"),
        ("active", "BOOL DEFAULT 1"),
        ("notes", "TEXT"),
        ("active_countries", "TEXT"),
    ]

    tags_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("uuid", "TEXT"),
        ("username", "TEXT"),
        ("name", "TEXT NOT NULL"),
        ("colour", "VARCHAR(7)"),
        ("type", "TEXT DEFAULT 'voyage'"),
    ]

    tags_associations_columns = [
        ("tag_id", "INTEGER NOT NULL"),
        ("trip_id", "INTEGER NOT NULL"),
    ]

    operator_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("operator_type", "VARCHAR(100) NOT NULL"),
        ("short_name", "VARCHAR(100) NOT NULL"),
        ("long_name", "VARCHAR(200)"),
        ("alias_of", "INTEGER NULL"),
        ("effective_date", "DATE NULL"),
    ]

    operator_logos_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("operator_id", "INTEGER NOT NULL"),
        ("logo_url", "TEXT"),
        ("effective_date", "DATE"),
    ]

    ship_pictures_columns = [
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("vessel_name", "TEXT NOT NULL"),
        ("image_url", "TEXT"),
        ("referrer_url", "TEXT"),
        ("local_image_path", "TEXT"),
        ("country_code", "TEXT"),
        ("fetch_date", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ]

    here_api_operators_columns = {
        ("here_operator", "TEXT"),
        ("trainlog_operator", "TEXT"),
    }

    fr24_usage_columns = {
        ("uid", "SERIAL PRIMARY KEY"),
        ("username", "TEXT NOT NULL"),
        ("month_year", "TEXT NOT NULL"),
        ("fr24_calls", "INTEGER DEFAULT 0"),
    }

    ai_usage_columns = {
        ("uid", "SERIAL PRIMARY KEY"),
        ("username", "TEXT NOT NULL"),
        ("month_year", "TEXT NOT NULL"),
        ("ai_calls", "INTEGER DEFAULT 0"),
    }

    daily_active_users_columns = {("date", "DATETIME"), ("number", "INT")}

    gpx_columns = {
        ("uid", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("username", "TEXT"),
        ("source", "TEXT"),
        ("origin", "TEXT"),
        ("destination", "TEXT"),
        ("start_time", "DATETIME"),
        ("end_time", "DATETIME"),
        ("duration", "INTEGER"),
        ("distance", "INTEGER"),
        ("notes", "TEXT"),
        ("path", "TEXT"),
    }

    tables = [
        ("operators", "uid", operator_columns),
        ("operator_logos", "uid", operator_logos_columns),
        ("trip", "uid", trip_columns),
        ("manual_stations", "uid", manual_stations_columns),
        ("percents", "uid", percents_columns),
        ("exchanges", "rate_date", currency_columns),
        ("tickets", "uid", tickets_columns),
        ("tags", "tag_id", tags_columns),
        ("tags_associations", "tag_id, trip_id", tags_associations_columns),
        ("ship_pictures", "uid", ship_pictures_columns),
        ("here_api_operators", "here_operator", here_api_operators_columns),
        ("gpx", "uid", gpx_columns),
        ("daily_active_users", "date", daily_active_users_columns),
        ("fr24_usage", "uid", fr24_usage_columns),
        ("ai_usage", "uid", ai_usage_columns),
    ]

    for table_name, primary_key, columns in tables:
        table = DatabaseTable(table_name, primary_key, columns)
        db_manager.add_table(table)

    # Setup database (create tables and columns if not exist)
    db_manager.setup_database()

    # Close the connection when all operations are done
    db_manager.close()
