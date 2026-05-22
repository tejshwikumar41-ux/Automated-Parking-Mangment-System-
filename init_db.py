import os
import sqlite3
import re
from dotenv import load_dotenv

# Load local environment variables if present
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///parking.db")
DB_FILE = os.getenv("DB_FILE", "parking.db")

def get_db_connection():
    if DATABASE_URL.startswith("postgresql"):
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except ImportError:
            print("[ERROR] psycopg2 is not installed. PostgreSQL connection failed.")
            raise
    
    # Fallback to SQLite
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def translate_to_postgres(sql_script: str) -> list:
    """Translates SQLite-specific SQL commands into PostgreSQL-compatible ones."""
    # Split by semicolon to execute statement by statement
    # Filter out comments and empty lines
    raw_statements = sql_script.split(";")
    clean_statements = []
    
    for stmt in raw_statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        
        # Skip comment-only lines
        if stmt.startswith("--") and "\n" not in stmt:
            continue
            
        # Replace SQLite INTEGER PRIMARY KEY AUTOINCREMENT with SERIAL PRIMARY KEY
        stmt = re.sub(
            r'(\w+)\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            r'\1 SERIAL PRIMARY KEY',
            stmt,
            flags=re.IGNORECASE
        )
        
        # Replace SQLite specific seed constructs (INSERT OR IGNORE)
        # PostgreSQL supports INSERT INTO ... ON CONFLICT (id) DO NOTHING;
        if "INSERT OR IGNORE INTO pricing_rules" in stmt:
            stmt = stmt.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            stmt += " ON CONFLICT (id) DO NOTHING"
        elif "INSERT OR IGNORE INTO parking_slots" in stmt:
            stmt = stmt.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            stmt += " ON CONFLICT (id) DO NOTHING"
        elif "INSERT OR IGNORE INTO mobile_users" in stmt:
            stmt = stmt.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            stmt += " ON CONFLICT (id) DO NOTHING"
        elif "INSERT OR IGNORE INTO user_plates" in stmt:
            stmt = stmt.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            stmt += " ON CONFLICT (id) DO NOTHING"
            
        # Boolean conversions (SQLite uses 0/1, Postgres supports true/false, though standard booleans can accept strings)
        # Also remove any "PRAGMA" sqlite-specific queries if any (there shouldn't be in schema.sql)
        if stmt.lower().startswith("pragma"):
            continue
            
        clean_statements.append(stmt)
        
    return clean_statements

def init_database():
    print(f"[INFO] Initializing database using URL: {DATABASE_URL}")
    conn = get_db_connection()
    cursor = conn.cursor()
    
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        print("[ERROR] schema.sql file not found.")
        conn.close()
        return False
        
    with open(schema_path, "r") as f:
        sql_script = f.read()
        
    try:
        if isinstance(conn, sqlite3.Connection):
            print("[INFO] Running SQLite schema initialization...")
            cursor.executescript(sql_script)
            print("[INFO] SQLite database initialized successfully.")
        else:
            print("[INFO] Running PostgreSQL schema translation and initialization...")
            statements = translate_to_postgres(sql_script)
            for stmt in statements:
                # Basic log of executed DDL/DML
                summary = stmt.split("\n")[0][:60] + "..." if len(stmt.split("\n")[0]) > 60 else stmt.split("\n")[0]
                # print(f"Executing: {summary}")
                cursor.execute(stmt)
            print("[INFO] PostgreSQL database initialized successfully.")
            
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Database initialization failed: {e}")
        raise e
    finally:
        conn.close()

if __name__ == "__main__":
    init_database()
