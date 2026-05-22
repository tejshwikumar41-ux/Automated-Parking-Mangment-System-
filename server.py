import os
import sqlite3
import math
import logging
import time
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Security, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import JWTError, jwt
import bcrypt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pythonjsonlogger import jsonlogger
import stripe
import json
import hashlib
from pricing_engine import DynamicPricingEngine

# Setup Limiter (DDoS protection)
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Scalable Parking Management System API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Setup Structured JSON Logging for production
log_handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(method)s %(path)s %(client)s %(duration)s %(message)s')
log_handler.setFormatter(formatter)
logger = logging.getLogger("parking_app")
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)

# Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    # Exclude static/docs files from spam logs
    if not request.url.path.startswith(("/static", "/docs", "/openapi.json")):
        client_host = request.client.host if request.client else "unknown"
        logger.info(
            "API Request Processed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": client_host,
                "duration": f"{duration:.4f}s"
            }
        )
    return response

# Security Configurations
API_KEY_NAME = "X-API-Key"
DEFAULT_API_KEY = "secret_parking_key_2026"
API_KEY = os.getenv("PARKING_API_KEY", DEFAULT_API_KEY)
api_key_header_auth = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

JWT_SECRET = os.getenv("PARKING_JWT_SECRET", "parking_jwt_secret_key_2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = 60

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login", auto_error=False)

# Seed credentials
USER_ACCOUNTS = {
    "admin": {
        "username": "admin",
        "password_hash": hash_password(os.getenv("ADMIN_PASSWORD", "password123")),
        "role": "admin"
    },
    "operator": {
        "username": "operator",
        "password_hash": hash_password(os.getenv("OPERATOR_PASSWORD", "operator123")),
        "role": "operator"
    }
}

# Stripe & Twilio setup
stripe.api_key = os.getenv("STRIPE_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "+1234567890")

# Database Connection sensing (SQLite/PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///parking.db")
DB_FILE = os.getenv("DB_FILE", "parking.db")

def get_db():
    if DATABASE_URL.startswith("postgresql"):
        # Import PostgreSQL client dynamically if configured
        try:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except ImportError:
            print("[ERROR] psycopg2 is not installed. Falling back to SQLite locally.")
    
    # Default SQLite
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# Init Database Schema
def init_db():
    try:
        from init_db import init_database
        init_database()
    except Exception as e:
        print(f"[ERROR] Database schema initialization failed: {e}")
        
    conn = get_db()
    cursor = conn.cursor()
        
    # Execute table upgrades (migrations) if needed for existing databases
    try:
        migrated = False
        # Transactions migrations
        if isinstance(conn, sqlite3.Connection):
            cursor.execute("PRAGMA table_info(transactions)")
            columns = [row[1] for row in cursor.fetchall()]
        else:
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='transactions'
            """)
            columns = [row[0] for row in cursor.fetchall()]
            
        migrations = [
            ("vehicle_type", "ALTER TABLE transactions ADD COLUMN vehicle_type TEXT DEFAULT 'STANDARD'"),
            ("entry_rate", "ALTER TABLE transactions ADD COLUMN entry_rate REAL"),
            ("rate_breakdown", "ALTER TABLE transactions ADD COLUMN rate_breakdown TEXT"),
            ("payment_status", "ALTER TABLE transactions ADD COLUMN payment_status TEXT DEFAULT 'PENDING'"),
            ("payment_method", "ALTER TABLE transactions ADD COLUMN payment_method TEXT"),
            ("payment_reference", "ALTER TABLE transactions ADD COLUMN payment_reference TEXT"),
            ("payment_time", "ALTER TABLE transactions ADD COLUMN payment_time TIMESTAMP")
        ]
        
        for col_name, alter_sql in migrations:
            if col_name not in columns:
                cursor.execute(alter_sql)
                migrated = True
                print(f"[INFO] Migration: Added column {col_name} to transactions table.")
                
        # Parking slots migrations
        if isinstance(conn, sqlite3.Connection):
            cursor.execute("PRAGMA table_info(parking_slots)")
            slot_columns = [row[1] for row in cursor.fetchall()]
        else:
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='parking_slots'
            """)
            slot_columns = [row[0] for row in cursor.fetchall()]
            
        slot_migrations = [
            ("slot_type", "ALTER TABLE parking_slots ADD COLUMN slot_type TEXT DEFAULT 'STANDARD'"),
            ("reservation_expiry", "ALTER TABLE parking_slots ADD COLUMN reservation_expiry TIMESTAMP")
        ]
        for col_name, alter_sql in slot_migrations:
            if col_name not in slot_columns:
                cursor.execute(alter_sql)
                migrated = True
                print(f"[INFO] Migration: Added column {col_name} to parking_slots table.")
                
        # Update existing seeded slots if they are standard but should be VIP/DISABLED
        cursor.execute("UPDATE parking_slots SET slot_type = 'VIP' WHERE name IN ('VIP 1', 'VIP 2') AND slot_type = 'STANDARD'")
        cursor.execute("UPDATE parking_slots SET slot_type = 'DISABLED' WHERE name = 'Slot A8' AND slot_type = 'STANDARD'")
                
        # Audit logs migrations
        if isinstance(conn, sqlite3.Connection):
            cursor.execute("PRAGMA table_info(audit_logs)")
            audit_columns = [row[1] for row in cursor.fetchall()]
        else:
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='audit_logs'
            """)
            audit_columns = [row[0] for row in cursor.fetchall()]
            
        audit_migrations = [
            ("hash", "ALTER TABLE audit_logs ADD COLUMN hash TEXT")
        ]
        for col_name, alter_sql in audit_migrations:
            if col_name not in audit_columns:
                cursor.execute(alter_sql)
                migrated = True
                print(f"[INFO] Migration: Added column {col_name} to audit_logs table.")

        # Hash backfilling
        cursor.execute("SELECT COUNT(*) as count FROM audit_logs WHERE hash IS NULL")
        unhashed_count = cursor.fetchone()[0]
        if unhashed_count > 0:
            cursor.execute("SELECT id, action, license_plate, slot_name, details, timestamp FROM audit_logs ORDER BY id ASC")
            logs = cursor.fetchall()
            prev_hash = ""
            for log in logs:
                log_id = log["id"]
                action = log["action"]
                plate = log["license_plate"] or ""
                slot = log["slot_name"] or ""
                details = log["details"] or ""
                timestamp = log["timestamp"]
                
                data_to_hash = f"{action}|{plate}|{slot}|{details}|{timestamp}|{prev_hash}"
                new_hash = hashlib.sha256(data_to_hash.encode('utf-8')).hexdigest()
                
                cursor.execute("UPDATE audit_logs SET hash = ? WHERE id = ?", (new_hash, log_id))
                prev_hash = new_hash
            migrated = True
            print(f"[INFO] Migration: Generated cryptographic hashes for {unhashed_count} existing audit logs.")

        if migrated:
            # Re-create/ensure indexes exist
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_vehicle_type ON transactions(vehicle_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_entry_rate ON transactions(entry_rate)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_payment_status ON transactions(payment_status)")
            conn.commit()
            print("[INFO] Migration: Completed database index configurations.")
    except Exception as migration_err:
        print(f"[WARNING] Database upgrade migration skipped or failed: {migration_err}")
        
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

# WebSocket Manager for Real-Time Broadcasting
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                pass

manager = ConnectionManager()

# --- SECURITY UTILITIES & DEPENDENCIES ---

async def get_api_key(header_value: str = Security(api_key_header_auth)):
    """Verifies X-API-Key for camera nodes."""
    if not header_value or header_value != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return header_value

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRY_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Validates JWT bearer token for web users."""
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None or role is None:
            raise credentials_exception
        return {"username": username, "role": role}
    except JWTError:
        raise credentials_exception

async def verify_admin_role(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin permissions required")
    return current_user

async def verify_operator_role(current_user: dict = Depends(get_current_user)):
    if current_user["role"] not in ["admin", "operator"]:
        raise HTTPException(status_code=403, detail="Operator permissions required")
    return current_user

# --- AUDIT LOG CHAINING HELPER ---
def write_audit_log(cursor, action: str, license_plate: Optional[str] = None, slot_name: Optional[str] = None, details: Optional[str] = None) -> str:
    """Writes an audit log entry with SHA-256 cryptographic hash chaining."""
    cursor.execute("SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    prev_hash = ""
    if row:
        try:
            prev_hash = row["hash"] or ""
        except (TypeError, KeyError, IndexError):
            prev_hash = row[0] or ""

    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    plate_str = license_plate or ""
    slot_str = slot_name or ""
    details_str = details or ""
    
    data_to_hash = f"{action}|{plate_str}|{slot_str}|{details_str}|{timestamp}|{prev_hash}"
    new_hash = hashlib.sha256(data_to_hash.encode('utf-8')).hexdigest()
    
    cursor.execute("""
        INSERT INTO audit_logs (action, license_plate, slot_name, details, timestamp, hash)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (action, license_plate, slot_name, details, timestamp, new_hash))
    return new_hash

# --- BUSINESS LOGIC ENGINES ---



class ParkingAnalytics:
    """Aggregates transactional database information for business intelligence charts."""
    def __init__(self, conn):
        self.conn = conn

    def peak_hours_analysis(self) -> List[Dict]:
        cursor = self.conn.cursor()
        # SQLite queries: group transactions by entry hour
        query = """
            SELECT 
                strftime('%H', entry_time) as hour_lbl,
                COUNT(*) as visit_count,
                ROUND(AVG(amount_paid), 2) as avg_revenue
            FROM transactions
            WHERE status = 'COMPLETED'
            GROUP BY hour_lbl
            ORDER BY hour_lbl ASC
        """
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def customer_segmentation(self) -> List[Dict]:
        cursor = self.conn.cursor()
        query = """
            SELECT 
                license_plate,
                COUNT(*) as visit_count,
                ROUND(SUM(amount_paid), 2) as total_spent,
                CASE 
                    WHEN COUNT(*) >= 15 THEN 'VIP'
                    WHEN COUNT(*) >= 5 THEN 'Regular'
                    ELSE 'Occasional'
                END as segment
            FROM transactions
            WHERE status = 'COMPLETED'
            GROUP BY license_plate
            ORDER BY visit_count DESC
            LIMIT 50
        """
        cursor.execute(query)
        return [dict(row) for row in cursor.fetchall()]

# --- REQUEST SCHEMAS ---

class LoginRequest(BaseModel):
    username: str
    password: str

class VehicleEntry(BaseModel):
    license_plate: str
    vehicle_type: str = "STANDARD"
    timestamp: Optional[str] = None

class VehicleExit(BaseModel):
    license_plate: str
    timestamp: Optional[str] = None

class PaymentConfirmRequest(BaseModel):
    transaction_id: int
    payment_method: str
    payment_reference: Optional[str] = None

class PricingRuleCreate(BaseModel):
    rule_name: str
    free_minutes: int
    base_fee: float
    base_hours: int
    hourly_rate: float

class SlotCreate(BaseModel):
    name: str
    slot_type: Optional[str] = "STANDARD"

class SlotReserveRequest(BaseModel):
    slot_name: str
    license_plate: str
    duration_minutes: Optional[int] = 30

class SlotRename(BaseModel):
    name: str

class MobileRegister(BaseModel):
    phone: str
    license_plates: List[str]

class PaymentRequest(BaseModel):
    license_plate: str
    amount: float

# --- REST ENDPOINTS ---

# 1. Health Probe Check
@app.get("/health")
async def health_check():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": "disconnected", "details": str(e)}

# 2. JWT Authentication Endpoint
@app.post("/api/login")
async def login(login_data: LoginRequest):
    username = login_data.username.strip()
    password = login_data.password
    
    user = USER_ACCOUNTS.get(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": username, "role": user["role"]})
    return {"access_token": access_token, "token_type": "bearer", "role": user["role"]}

# 3. Vehicle Entry Gate Processing
@app.post("/api/entry")
@limiter.limit("30/minute")
async def vehicle_entry(request: Request, entry: VehicleEntry, api_key: str = Depends(get_api_key)):
    plate = entry.license_plate.strip().upper()
    if not plate:
        raise HTTPException(status_code=400, detail="Invalid license plate")

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Check if already parked
        cursor.execute("SELECT status FROM vehicles WHERE license_plate = ?", (plate,))
        vehicle = cursor.fetchone()
        
        if vehicle and vehicle["status"] == "PARKED":
            cursor.execute("""
                SELECT ps.name, t.entry_time 
                FROM parking_slots ps 
                JOIN transactions t ON ps.id = t.slot_id 
                WHERE ps.current_vehicle_id = ? AND t.status = 'ACTIVE'
            """, (plate,))
            active_info = cursor.fetchone()
            if active_info:
                conn.close()
                return {
                    "status": "already_parked",
                    "license_plate": plate,
                    "slot_name": active_info["name"],
                    "entry_time": active_info["entry_time"]
                }

        if entry.timestamp:
            try:
                ts_str = entry.timestamp
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                parsed_dt = datetime.fromisoformat(ts_str)
                if parsed_dt.tzinfo is not None:
                    parsed_dt = parsed_dt.replace(tzinfo=None)
                now = parsed_dt
                now_str = entry.timestamp
            except Exception as parse_err:
                logger.warning(f"Failed to parse custom entry timestamp {entry.timestamp}: {parse_err}")
                now = datetime.now()
                now_str = now.isoformat()
        else:
            now = datetime.now()
            now_str = now.isoformat()

        # Check if there is an active reservation for this vehicle
        cursor.execute("""
            SELECT id, name FROM parking_slots 
            WHERE current_vehicle_id = ? 
              AND status = 'RESERVED' 
              AND reservation_expiry > ?
            LIMIT 1
        """, (plate, now_str))
        reserved_slot = cursor.fetchone()
        
        if reserved_slot:
            slot_id = reserved_slot["id"]
            slot_name = reserved_slot["name"]
        else:
            # Revert expired reservations first
            cursor.execute("""
                UPDATE parking_slots 
                SET status = 'AVAILABLE', 
                    current_vehicle_id = NULL, 
                    reservation_expiry = NULL 
                WHERE status = 'RESERVED' AND reservation_expiry <= ?
            """, (now_str,))
            conn.commit()
            
            # Fetch all available slots
            cursor.execute("""
                SELECT id, name, slot_type FROM parking_slots 
                WHERE status = 'AVAILABLE'
                ORDER BY id ASC
            """)
            available_slots = cursor.fetchall()
            
            cursor.execute("SELECT COUNT(*) as count FROM parking_slots")
            total_slots = cursor.fetchone()["count"]
            
            if total_slots == 0:
                conn.close()
                raise HTTPException(status_code=400, detail="No parking slots configured")

            if not available_slots:
                conn.close()
                await manager.broadcast({
                    "event": "lot_full_attempt",
                    "plate": plate,
                    "message": f"ACCESS DENIED: Lot Full. Vehicle {plate} kept out.",
                    "timestamp": now_str
                })
                raise HTTPException(status_code=403, detail="Lot Full")
                
            # Filter slots by type
            standard_slots = [s for s in available_slots if s["slot_type"] == "STANDARD"]
            vip_slots = [s for s in available_slots if s["slot_type"] == "VIP"]
            disabled_slots = [s for s in available_slots if s["slot_type"] == "DISABLED"]
            
            # Assign slot based on type
            if entry.vehicle_type == "VIP":
                if vip_slots:
                    assigned_slot = vip_slots[0]
                elif standard_slots:
                    assigned_slot = standard_slots[0]
                else:
                    assigned_slot = available_slots[0]
            elif entry.vehicle_type == "DISABLED":
                if disabled_slots:
                    assigned_slot = disabled_slots[0]
                elif standard_slots:
                    assigned_slot = standard_slots[0]
                else:
                    assigned_slot = available_slots[0]
            else: # STANDARD, EV, OVERSIZED, etc.
                if standard_slots:
                    assigned_slot = standard_slots[0]
                else:
                    assigned_slot = available_slots[0]
                    
            slot_id = assigned_slot["id"]
            slot_name = assigned_slot["name"]

        # Calculate occupancy rate for surge pricing calculation
        cursor.execute("SELECT COUNT(*) as count FROM parking_slots")
        total_slots = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM parking_slots WHERE status = 'OCCUPIED'")
        occupied_slots = cursor.fetchone()["count"]
        occupancy_rate = (occupied_slots / total_slots) if total_slots > 0 else 0.0

        pricing_engine = DynamicPricingEngine(conn)
        pricing = pricing_engine.calculate_dynamic_rate(now, entry.vehicle_type, occupancy_rate)
        entry_rate = pricing['final_rate']
        rate_breakdown = pricing['breakdown']

        # Update Vehicle status
        cursor.execute("""
            INSERT INTO vehicles (license_plate, status, last_entry_time, last_exit_time)
            VALUES (?, 'PARKED', ?, NULL)
            ON CONFLICT(license_plate) DO UPDATE SET 
                status = 'PARKED',
                last_entry_time = ?,
                last_exit_time = NULL
        """, (plate, now_str, now_str))

        # Occupy Slot (and clear reservation columns)
        cursor.execute("""
            UPDATE parking_slots 
            SET status = 'OCCUPIED', 
                current_vehicle_id = ?, 
                reservation_expiry = NULL 
            WHERE id = ?
        """, (plate, slot_id))

        # Insert Transaction with dynamic pricing details
        cursor.execute("""
            INSERT INTO transactions (license_plate, slot_id, entry_time, status, vehicle_type, entry_rate, rate_breakdown, payment_status)
            VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, 'PENDING')
        """, (plate, slot_id, now_str, entry.vehicle_type, entry_rate, rate_breakdown))

        # Log Audit
        write_audit_log(cursor, 'ENTRY', plate, slot_name, f"Entry via camera. Assigned slot {slot_name} (Type: {entry.vehicle_type}, Rate: ₹{entry_rate}/hr)")

        conn.commit()

        # Trigger Twilio SMS async mock
        # Check if plate is registered to a phone
        cursor.execute("""
            SELECT mu.phone FROM mobile_users mu
            JOIN user_plates up ON mu.id = up.user_id
            WHERE up.license_plate = ?
        """, (plate,))
        user_row = cursor.fetchone()
        if user_row and TWILIO_ACCOUNT_SID:
            phone = user_row["phone"]
            try:
                # Real twilio trigger code
                client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                client.messages.create(
                    body=f"[SPARK] Vehicle {plate} entered at {now_str}. Slot assigned: {slot_name}",
                    from_=TWILIO_FROM_NUMBER,
                    to=phone
                )
            except Exception as sms_err:
                logger.warning(f"SMS notification failed: {sms_err}")
        elif user_row:
            logger.info(f"[SMS MOCK] Sending entry SMS notification to {user_row['phone']}: Vehicle {plate} entered slot {slot_name}")

        # WebSockets Broadcast
        await manager.broadcast({
            "event": "entry",
            "plate": plate,
            "slot": slot_name,
            "vehicle_type": entry.vehicle_type,
            "entry_rate": entry_rate,
            "rate_breakdown": rate_breakdown,
            "message": f"Vehicle {plate} ({entry.vehicle_type}) entered. Assigned to slot {slot_name}. Rate: ₹{entry_rate}/hr.",
            "timestamp": now_str
        })

        return {
            "status": "success",
            "license_plate": plate,
            "slot_name": slot_name,
            "entry_time": now_str,
            "vehicle_type": entry.vehicle_type,
            "entry_rate": entry_rate,
            "rate_breakdown": rate_breakdown
        }
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 4. Vehicle Exit Gate Processing (Dynamic Pricing integrated)
@app.post("/api/exit")
@limiter.limit("30/minute")
async def vehicle_exit(request: Request, exit_req: VehicleExit, api_key: str = Depends(get_api_key)):
    plate = exit_req.license_plate.strip().upper()
    if not plate:
        raise HTTPException(status_code=400, detail="Invalid license plate")

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Find active transaction
        cursor.execute("""
            SELECT t.id, t.slot_id, t.entry_time, t.vehicle_type, ps.name as slot_name 
            FROM transactions t
            JOIN parking_slots ps ON t.slot_id = ps.id
            WHERE t.license_plate = ? AND t.status = 'ACTIVE'
            ORDER BY t.entry_time DESC LIMIT 1
        """, (plate,))
        transaction = cursor.fetchone()

        if not transaction:
            conn.close()
            raise HTTPException(status_code=404, detail="No active parking record found for this plate")

        transaction_id = transaction["id"]
        slot_id = transaction["slot_id"]
        slot_name = transaction["slot_name"]
        entry_time_str = transaction["entry_time"]
        vehicle_type = transaction["vehicle_type"] or "STANDARD"

        try:
            ts_str = entry_time_str
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            parsed_dt = datetime.fromisoformat(ts_str)
            if parsed_dt.tzinfo is not None:
                parsed_dt = parsed_dt.replace(tzinfo=None)
            entry_time = parsed_dt
        except Exception as parse_err:
            logger.warning(f"Failed to parse database entry timestamp {entry_time_str}: {parse_err}")
            entry_time = datetime.now()

        if exit_req.timestamp:
            try:
                ts_str = exit_req.timestamp
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                parsed_dt = datetime.fromisoformat(ts_str)
                if parsed_dt.tzinfo is not None:
                    parsed_dt = parsed_dt.replace(tzinfo=None)
                exit_time = parsed_dt
                exit_time_str = exit_req.timestamp
            except Exception as parse_err:
                logger.warning(f"Failed to parse custom exit timestamp {exit_req.timestamp}: {parse_err}")
                exit_time = datetime.now()
                exit_time_str = exit_time.isoformat()
        else:
            exit_time = datetime.now()
            exit_time_str = exit_time.isoformat()

        # Calculate duration
        duration = exit_time - entry_time
        duration_minutes = max(0, int(duration.total_seconds() / 60))

        # Run Advanced Dynamic Pricing Engine
        pricing_engine = DynamicPricingEngine(conn)
        pricing_res = pricing_engine.calculate_total_fee(entry_time, exit_time, vehicle_type)
        amount_paid = pricing_res["total_fee"]

        # Free the slot and mark vehicle exited
        cursor.execute("UPDATE parking_slots SET status = 'AVAILABLE', current_vehicle_id = NULL WHERE id = ?", (slot_id,))
        cursor.execute("UPDATE vehicles SET status = 'EXITED', last_exit_time = ? WHERE license_plate = ?", (exit_time_str, plate))

        # payment billing status selection
        if duration_minutes <= 3:
            amount_paid = 0.0
            payment_status = "BYPASSED"
            txn_status = "ACCIDENTAL_DRIVE_THROUGH"
            payment_method = "ACCIDENTAL_BYPASS"
            payment_time_str = exit_time_str
        elif amount_paid == 0.0:
            payment_status = "PAID"
            txn_status = "COMPLETED"
            payment_method = "GRACE_PERIOD"
            payment_time_str = exit_time_str
        else:
            payment_status = "PENDING"
            txn_status = "ACTIVE"
            payment_method = None
            payment_time_str = None

        # Update transaction record
        cursor.execute("""
            UPDATE transactions 
            SET exit_time = ?, 
                duration_minutes = ?, 
                amount_paid = ?, 
                payment_status = ?, 
                status = ?,
                payment_method = ?,
                payment_time = ?
            WHERE id = ?
        """, (exit_time_str, duration_minutes, amount_paid, payment_status, txn_status, payment_method, payment_time_str, transaction_id))

        # Log Audit
        write_audit_log(cursor, 'EXIT', plate, slot_name, f"Exit processing. Duration: {duration_minutes}m, Charged: ₹{amount_paid:.2f}, Payment Status: {payment_status}")

        conn.commit()

        # Twilio SMS exit alert mock
        cursor.execute("""
            SELECT mu.phone FROM mobile_users mu
            JOIN user_plates up ON mu.id = up.user_id
            WHERE up.license_plate = ?
        """, (plate,))
        user_row = cursor.fetchone()
        if user_row and TWILIO_ACCOUNT_SID:
            phone = user_row["phone"]
            try:
                client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                client.messages.create(
                    body=f"[SPARK] Vehicle {plate} exited. Duration: {duration_minutes} mins. Fee: ₹{amount_paid:.2f}. Status: {payment_status}",
                    from_=TWILIO_FROM_NUMBER,
                    to=phone
                )
            except Exception as sms_err:
                logger.warning(f"SMS notification failed: {sms_err}")
        elif user_row:
            logger.info(f"[SMS MOCK] Sending exit SMS notification to {user_row['phone']}: Vehicle {plate} exited. Fee: ₹{amount_paid:.2f}, Status: {payment_status}")

        # WebSockets Broadcast
        await manager.broadcast({
            "event": "exit",
            "transaction_id": transaction_id,
            "plate": plate,
            "slot": slot_name,
            "duration": duration_minutes,
            "fee": amount_paid,
            "payment_status": payment_status,
            "message": f"Vehicle {plate} exited. Fee: ₹{amount_paid:.2f} ({duration_minutes} mins). Payment Status: {payment_status}.",
            "timestamp": exit_time_str
        })

        return {
            "status": "success",
            "transaction_id": transaction_id,
            "license_plate": plate,
            "slot_name": slot_name,
            "entry_time": entry_time_str,
            "exit_time": exit_time_str,
            "duration_minutes": duration_minutes,
            "amount_paid": amount_paid,
            "payment_status": payment_status
        }
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 5. Dashboard Revenue Stats (Protected)
@app.get("/api/revenue")
async def get_revenue_stats(user: dict = Depends(verify_operator_role)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(SUM(amount_paid), 0.0) as daily FROM transactions WHERE status = 'COMPLETED' AND date(exit_time) = date('now', 'localtime')")
        daily = cursor.fetchone()["daily"]

        cursor.execute("SELECT COALESCE(SUM(amount_paid), 0.0) as weekly FROM transactions WHERE status = 'COMPLETED' AND exit_time >= datetime('now', '-7 days')")
        weekly = cursor.fetchone()["weekly"]

        cursor.execute("SELECT COALESCE(SUM(amount_paid), 0.0) as monthly FROM transactions WHERE status = 'COMPLETED' AND exit_time >= datetime('now', '-30 days')")
        monthly = cursor.fetchone()["monthly"]

        cursor.execute("SELECT COALESCE(SUM(amount_paid), 0.0) as lifetime FROM transactions WHERE status = 'COMPLETED'")
        lifetime = cursor.fetchone()["lifetime"]

        return {
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
            "lifetime": lifetime
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 6. Dynamic Pricing Configuration (Admin Protected)
@app.get("/api/pricing")
async def get_pricing_rules():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM pricing_rules WHERE is_active = 1 LIMIT 1")
        rule = cursor.fetchone()
        if not rule:
            return {
                "rule_name": "Default Rules",
                "free_minutes": 15,
                "base_fee": 40.0,
                "base_hours": 2,
                "hourly_rate": 20.0
            }
        return dict(rule)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/pricing")
async def update_pricing_rule(rule: PricingRuleCreate, user: dict = Depends(verify_admin_role)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE pricing_rules SET is_active = 0")
        cursor.execute("""
            INSERT INTO pricing_rules (rule_name, free_minutes, base_fee, base_hours, hourly_rate, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (rule.rule_name, rule.free_minutes, rule.base_fee, rule.base_hours, rule.hourly_rate))
        
        write_audit_log(cursor, 'PRICING_UPDATE', details=f"Rates configured. Base: ₹{rule.base_fee} for {rule.base_hours}h. Operator: {user['username']}")
        conn.commit()

        await manager.broadcast({
            "event": "pricing_updated",
            "message": "Dynamic pricing rules updated by admin."
        })
        return {"status": "success", "message": "Pricing rules updated."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 7. Slot CRUD Management (Admin/Operator Protected)
@app.get("/api/slots")
async def list_slots():
    conn = get_db()
    cursor = conn.cursor()
    try:
        now_str = datetime.now().isoformat()
        # Auto-expire reservations
        cursor.execute("""
            UPDATE parking_slots 
            SET status = 'AVAILABLE', 
                current_vehicle_id = NULL, 
                reservation_expiry = NULL 
            WHERE status = 'RESERVED' AND reservation_expiry <= ?
        """, (now_str,))
        conn.commit()
        
        cursor.execute("SELECT id, name, status, current_vehicle_id, slot_type, reservation_expiry FROM parking_slots ORDER BY name ASC")
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/slots")
async def add_slot(slot: SlotCreate, user: dict = Depends(verify_admin_role)):
    name = slot.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Slot name cannot be empty")
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM parking_slots WHERE name = ?", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Slot name already exists")
            
        cursor.execute("INSERT INTO parking_slots (name, status, slot_type) VALUES (?, 'AVAILABLE', ?)", (name, slot.slot_type or 'STANDARD'))
        write_audit_log(cursor, 'SLOT_CREATE', slot_name=name, details=f"Slot {name} (Type: {slot.slot_type or 'STANDARD'}) created by admin {user['username']}")
        conn.commit()

        await manager.broadcast({
            "event": "slots_updated",
            "message": f"Slot '{name}' created."
        })
        return {"status": "success", "message": f"Slot '{name}' created."}
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.put("/api/slots/{slot_id}")
async def rename_slot(slot_id: int, slot: SlotRename, user: dict = Depends(verify_admin_role)):
    new_name = slot.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Slot name cannot be empty")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, status FROM parking_slots WHERE id = ?", (slot_id,))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Slot not found")
        
        old_name = existing["name"]
        cursor.execute("SELECT id FROM parking_slots WHERE name = ? AND id != ?", (new_name, slot_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Slot name is already in use")

        cursor.execute("UPDATE parking_slots SET name = ? WHERE id = ?", (new_name, slot_id))
        write_audit_log(cursor, 'SLOT_RENAME', slot_name=new_name, details=f"Renamed slot '{old_name}' to '{new_name}' by admin {user['username']}")
        conn.commit()

        await manager.broadcast({
            "event": "slots_updated",
            "message": f"Slot renamed to '{new_name}'."
        })
        return {"status": "success", "message": f"Slot renamed to '{new_name}'."}
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.delete("/api/slots/{slot_id}")
async def delete_slot(slot_id: int, user: dict = Depends(verify_admin_role)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name, status FROM parking_slots WHERE id = ?", (slot_id,))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Slot not found")
        
        if existing["status"] == "OCCUPIED":
            raise HTTPException(status_code=400, detail="Cannot delete an occupied slot")

        slot_name = existing["name"]
        cursor.execute("DELETE FROM parking_slots WHERE id = ?", (slot_id,))
        write_audit_log(cursor, 'SLOT_DELETE', slot_name=slot_name, details=f"Deleted slot: {slot_name} by admin {user['username']}")
        conn.commit()

        await manager.broadcast({
            "event": "slots_updated",
            "message": f"Slot '{slot_name}' deleted."
        })
        return {"status": "success", "message": f"Slot '{slot_name}' deleted."}
    finally:
        conn.close()

# 7b. Slot Reservation (Operator/Admin Protected)
@app.post("/api/slots/reserve")
async def reserve_slot(data: SlotReserveRequest, user: dict = Depends(verify_operator_role)):
    slot_name = data.slot_name.strip()
    plate = data.license_plate.strip().upper()
    duration = data.duration_minutes or 30
    
    if not slot_name or not plate:
        raise HTTPException(status_code=400, detail="Slot name and license plate are required")
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Check if slot exists
        cursor.execute("SELECT id, status, slot_type FROM parking_slots WHERE name = ?", (slot_name,))
        slot = cursor.fetchone()
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found")
            
        if slot["status"] == "OCCUPIED":
            raise HTTPException(status_code=400, detail="Cannot reserve an occupied slot")
            
        now = datetime.now()
        expiry = now + timedelta(minutes=duration)
        expiry_str = expiry.isoformat()
        
        # Check if vehicle is already parked inside the lot
        cursor.execute("SELECT status FROM vehicles WHERE license_plate = ?", (plate,))
        vehicle = cursor.fetchone()
        if vehicle and vehicle["status"] == "PARKED":
            raise HTTPException(status_code=400, detail="Vehicle is already parked inside the lot")
            
        # Check if vehicle has another active reservation
        cursor.execute("SELECT name FROM parking_slots WHERE current_vehicle_id = ? AND status = 'RESERVED' AND reservation_expiry > ?", (plate, now.isoformat()))
        existing_res = cursor.fetchone()
        if existing_res:
            raise HTTPException(status_code=400, detail=f"Vehicle already has an active reservation in slot {existing_res['name']}")
            
        # Update slot status to RESERVED and set current_vehicle_id and reservation_expiry
        cursor.execute("""
            UPDATE parking_slots 
            SET status = 'RESERVED',
                current_vehicle_id = ?,
                reservation_expiry = ?
            WHERE id = ?
        """, (plate, expiry_str, slot["id"]))
        
        # Ensure vehicle entry exists in vehicles table as EXITED (i.e. not parked)
        cursor.execute("""
            INSERT INTO vehicles (license_plate, status, last_entry_time)
            VALUES (?, 'EXITED', ?)
            ON CONFLICT(license_plate) DO UPDATE SET status = 'EXITED'
        """, (plate, now.isoformat()))
        
        # Log Audit
        write_audit_log(cursor, 'RESERVE', plate, slot_name, f"Slot {slot_name} reserved for vehicle {plate} by operator {user['username']} for {duration} minutes")
        conn.commit()
        
        await manager.broadcast({
            "event": "slots_updated",
            "message": f"Slot '{slot_name}' reserved for {plate} until {expiry.strftime('%I:%M %p')}."
        })
        
        return {"status": "success", "message": f"Slot '{slot_name}' reserved for {plate} until {expiry_str}."}
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 8. Historical Audit Log Search
@app.get("/api/logs")
async def get_historical_logs(
    slot_name: Optional[str] = None,
    license_plate: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(verify_operator_role)
):
    conn = get_db()
    cursor = conn.cursor()
    query = """
        SELECT t.id, t.license_plate, ps.name as slot_name, t.entry_time, t.exit_time, 
               t.duration_minutes, t.amount_paid, t.status 
        FROM transactions t
        JOIN parking_slots ps ON t.slot_id = ps.id
        WHERE 1=1
    """
    params = []

    if slot_name:
        query += " AND ps.name LIKE ?"
        params.append(f"%{slot_name}%")
    if license_plate:
        query += " AND t.license_plate LIKE ?"
        params.append(f"%{license_plate}%")
    if start_date:
        query += " AND t.entry_time >= ?"
        params.append(start_date)
    if end_date:
        query += " AND t.entry_time <= ?"
        params.append(end_date)

    query += " ORDER BY t.entry_time DESC"

    try:
        cursor.execute(query, params)
        logs = [dict(row) for row in cursor.fetchall()]
        return logs
    finally:
        conn.close()

# 8b. Cryptographic Audit Log Verification (Admin Protected)
@app.get("/api/audit/verify")
async def verify_audit_logs(user: dict = Depends(verify_admin_role)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, action, license_plate, slot_name, details, timestamp, hash FROM audit_logs ORDER BY id ASC")
        logs = cursor.fetchall()
        
        tampered_ids = []
        prev_hash = ""
        
        for log in logs:
            log_id = log["id"]
            action = log["action"]
            plate = log["license_plate"] or ""
            slot = log["slot_name"] or ""
            details = log["details"] or ""
            timestamp = log["timestamp"]
            stored_hash = log["hash"]
            
            data_to_hash = f"{action}|{plate}|{slot}|{details}|{timestamp}|{prev_hash}"
            expected_hash = hashlib.sha256(data_to_hash.encode('utf-8')).hexdigest()
            
            if stored_hash != expected_hash:
                tampered_ids.append(log_id)
                # Self-healing logic for the verification chain: use the stored hash as the prev_hash
                # for the subsequent row so that subsequent hashes aren't marked as tampered
                prev_hash = stored_hash or ""
            else:
                prev_hash = stored_hash or ""
                
        if tampered_ids:
            return {"status": "TAMPERED", "tampered_ids": tampered_ids}
        return {"status": "SECURE"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 9. Stripe Payment Endpoint integration (New for Phase 2)
@app.post("/api/payment")
async def process_payment(pay_req: PaymentRequest, api_key: str = Depends(get_api_key)):
    plate = pay_req.license_plate.strip().upper()
    amount = pay_req.amount
    
    if not plate or amount < 0:
        raise HTTPException(status_code=400, detail="Invalid request parameters")
        
    try:
        if stripe.api_key:
            # Create a Stripe PaymentIntent
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100), # Amount in paise/cents
                currency="inr",
                metadata={"license_plate": plate}
            )
            return {"status": "success", "client_secret": intent.client_secret, "amount": amount}
        else:
            # Fallback Stripe Mock transaction
            logger.info(f"[STRIPE MOCK] Charging plate {plate} an amount of INR {amount:.2f}")
            return {
                "status": "success", 
                "client_secret": f"mock_secret_intent_{int(time.time())}_{plate}",
                "amount": amount,
                "message": "Mock payment processed successfully."
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class StripePaymentRequest(BaseModel):
    transaction_id: int

@app.post("/api/payment/stripe")
async def create_stripe_payment_intent(pay_req: StripePaymentRequest):
    transaction_id = pay_req.transaction_id
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT amount_paid, license_plate FROM transactions WHERE id = ?", (transaction_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        amount = float(row["amount_paid"])
        plate = row["license_plate"]
        
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Cannot charge zero or negative amount")
            
        if stripe.api_key:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100), # amount in paise/cents
                currency="inr",
                metadata={"transaction_id": transaction_id, "license_plate": plate}
            )
            return {
                "status": "success", 
                "client_secret": intent.client_secret, 
                "amount": amount,
                "transaction_id": transaction_id
            }
        else:
            # Fallback Stripe Mock
            logger.info(f"[STRIPE MOCK] Creating intent for txn {transaction_id}, plate {plate}, amount INR {amount:.2f}")
            return {
                "status": "success",
                "client_secret": f"mock_secret_intent_{int(time.time())}_{plate}",
                "amount": amount,
                "transaction_id": transaction_id,
                "message": "Mock payment intent created successfully."
            }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/payment/confirm")
async def confirm_payment(data: PaymentConfirmRequest, current_user: dict = Depends(verify_operator_role)):
    transaction_id = data.transaction_id
    payment_method = data.payment_method.strip().upper()
    payment_reference = data.payment_reference.strip() if data.payment_reference else None
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Check if transaction exists
        cursor.execute("""
            SELECT t.id, t.license_plate, t.amount_paid, t.status, ps.name as slot_name
            FROM transactions t
            JOIN parking_slots ps ON t.slot_id = ps.id
            WHERE t.id = ?
        """, (transaction_id,))
        transaction = cursor.fetchone()
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")
            
        now_str = datetime.now().isoformat()
        
        # Update transaction
        cursor.execute("""
            UPDATE transactions 
            SET payment_status = 'PAID', 
                payment_method = ?, 
                payment_reference = ?, 
                payment_time = ?,
                status = 'COMPLETED'
            WHERE id = ?
        """, (payment_method, payment_reference, now_str, transaction_id))
        
        plate = transaction["license_plate"]
        slot_name = transaction["slot_name"]
        amount = transaction["amount_paid"]
        
        # Audit Log
        write_audit_log(cursor, 'PAYMENT_CONFIRM', plate, slot_name, f"Payment confirmed for transaction {transaction_id}. Amount: ₹{amount:.2f}, Method: {payment_method}, Ref: {payment_reference or 'N/A'}. Operator: {current_user['username']}")
        conn.commit()
        
        # Broadcast payment event via WebSocket
        await manager.broadcast({
            "event": "payment",
            "transaction_id": transaction_id,
            "plate": plate,
            "amount": amount,
            "payment_status": "PAID",
            "message": f"Payment of ₹{amount:.2f} for vehicle {plate} confirmed via {payment_method}.",
            "timestamp": now_str
        })
        
        return {"status": "success", "message": "Payment confirmed successfully"}
    except Exception as e:
        conn.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/pricing/forecast")
async def get_pricing_forecast(hours: int = 24):
    conn = get_db()
    try:
        pricing_engine = DynamicPricingEngine(conn)
        forecast = pricing_engine.get_pricing_forecast(hours)
        return forecast
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/pricing/current")
async def get_current_pricing(vehicle_type: str = "STANDARD"):
    conn = get_db()
    try:
        # Calculate current capacity occupancy rate
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM parking_slots")
        total_slots = cursor.fetchone()["count"]
        cursor.execute("SELECT COUNT(*) as count FROM parking_slots WHERE status = 'OCCUPIED'")
        occupied_slots = cursor.fetchone()["count"]
        occupancy_rate = (occupied_slots / total_slots) if total_slots > 0 else 0.0

        pricing_engine = DynamicPricingEngine(conn)
        pricing = pricing_engine.calculate_dynamic_rate(datetime.now(), vehicle_type, occupancy_rate)
        return pricing
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 10. Advanced Analytics APIs (New for Phase 2)
@app.get("/api/analytics/peak-hours")
async def get_peak_hours(user: dict = Depends(verify_admin_role)):
    conn = get_db()
    try:
        analytics = ParkingAnalytics(conn)
        return analytics.peak_hours_analysis()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/analytics/customers")
async def get_customer_segmentation(user: dict = Depends(verify_admin_role)):
    conn = get_db()
    try:
        analytics = ParkingAnalytics(conn)
        return analytics.customer_segmentation()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# 11. Mobile APIs Support (New for Phase 2)
@app.post("/api/mobile/register")
async def mobile_register(data: MobileRegister):
    phone = data.phone.strip()
    plates = [p.strip().upper() for p in data.license_plates if p.strip()]
    if not phone or not plates:
        raise HTTPException(status_code=400, detail="Invalid phone or plate listings")
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Create user
        cursor.execute("""
            INSERT INTO mobile_users (phone) VALUES (?)
            ON CONFLICT(phone) DO UPDATE SET phone=phone
        """, (phone,))
        
        # Get user ID
        cursor.execute("SELECT id FROM mobile_users WHERE phone = ?", (phone,))
        user_id = cursor.fetchone()["id"]
        
        # Clean existing plates
        cursor.execute("DELETE FROM user_plates WHERE user_id = ?", (user_id,))
        
        # Add new plates
        for plate in plates:
            cursor.execute("INSERT INTO user_plates (user_id, license_plate) VALUES (?, ?)", (user_id, plate))
            
        conn.commit()
        return {"status": "success", "message": f"Registered phone {phone} with {len(plates)} plate(s)."}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/mobile/history/{plate}")
async def mobile_history(plate: str):
    plate = plate.strip().upper()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT t.entry_time, t.exit_time, t.duration_minutes, t.amount_paid, t.status, ps.name as slot_name
            FROM transactions t
            JOIN parking_slots ps ON t.slot_id = ps.id
            WHERE t.license_plate = ?
            ORDER BY t.entry_time DESC
        """, (plate,))
        records = [dict(row) for row in cursor.fetchall()]
        return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/mobile/qr/{plate}")
async def generate_qr_token(plate: str):
    """
    Generates a mock QR code image payload representation for simple contactless entry gate.
    Normally uses qrcode library, falls back to a clean JSON string base64 payload if not installed.
    """
    import base64
    plate = plate.strip().upper()
    
    # Generate simple mock payload representation
    qr_payload = {
        "plate": plate,
        "expiry": (datetime.now() + timedelta(minutes=15)).isoformat(),
        "signature": "spark_verification_token_999"
    }
    json_bytes = json.dumps(qr_payload).encode('utf-8')
    b64_str = base64.b64encode(json_bytes).decode('utf-8')
    
    # Normally return base64 encoded mock PNG QR image
    mock_qr_img = f"data:image/svg+xml;base64,{b64_str}"
    
    return {"plate": plate, "qr_payload": b64_str, "qr_image": mock_qr_img}

@app.get("/api/metrics")
async def get_metrics(current_user: dict = Depends(verify_operator_role)):
    """Production metrics endpoint"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Calculate date 7 days ago in Python to be database-agnostic
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        
        if isinstance(conn, sqlite3.Connection):
            query = """
                SELECT 
                    COUNT(*) as total_transactions,
                    SUM(CASE WHEN payment_status = 'PAID' THEN amount_paid ELSE 0 END) as total_revenue,
                    COUNT(CASE WHEN payment_status = 'PENDING' THEN 1 END) as pending_payments,
                    AVG(duration_minutes) as avg_duration
                FROM transactions
                WHERE entry_time >= ?
            """
            cursor.execute(query, (seven_days_ago,))
        else:
            query = """
                SELECT 
                    COUNT(*) as total_transactions,
                    SUM(CASE WHEN payment_status = 'PAID' THEN amount_paid ELSE 0 END) as total_revenue,
                    COUNT(CASE WHEN payment_status = 'PENDING' THEN 1 END) as pending_payments,
                    AVG(duration_minutes) as avg_duration
                FROM transactions
                WHERE entry_time >= %s
            """
            cursor.execute(query, (seven_days_ago,))
            
        row = cursor.fetchone()
        
        if hasattr(row, "keys"):
            stats = dict(row)
        elif row:
            stats = {
                "total_transactions": row[0],
                "total_revenue": row[1] if row[1] is not None else 0,
                "pending_payments": row[2] if row[2] is not None else 0,
                "avg_duration": row[3] if row[3] is not None else 0
            }
        else:
            stats = {
                "total_transactions": 0,
                "total_revenue": 0,
                "pending_payments": 0,
                "avg_duration": 0
            }
            
        if stats.get("total_revenue") is None:
            stats["total_revenue"] = 0.0
        else:
            stats["total_revenue"] = float(stats["total_revenue"])
            
        if stats.get("avg_duration") is None:
            stats["avg_duration"] = 0.0
        else:
            stats["avg_duration"] = float(stats["avg_duration"])
            
        return {
            "period": "Last 7 days",
            "metrics": stats,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# --- WEBSOCKET REAL-TIME BROADCASTS ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps({"event": "connected", "message": "Connected to Enhanced SPARK Server"}))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Mount Frontend static client files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
