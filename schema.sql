-- Database Schema for Parking Management System (Phase 2 Enhanced)
-- Compatible with SQLite, PostgreSQL, and MySQL

-- 1. Pricing Rules Table
CREATE TABLE IF NOT EXISTS pricing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name VARCHAR(100) NOT NULL,
    free_minutes INTEGER DEFAULT 15,
    base_fee DECIMAL(10, 2) DEFAULT 50.00,
    base_hours INTEGER DEFAULT 2,
    hourly_rate DECIMAL(10, 2) DEFAULT 20.00,
    is_active BOOLEAN DEFAULT 0
);

-- 2. Vehicles Table
CREATE TABLE IF NOT EXISTS vehicles (
    license_plate VARCHAR(20) PRIMARY KEY,
    status VARCHAR(20) NOT NULL, -- 'PARKED', 'EXITED'
    last_entry_time TIMESTAMP NOT NULL,
    last_exit_time TIMESTAMP
);

-- 3. Parking Slots Table
CREATE TABLE IF NOT EXISTS parking_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(50) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'AVAILABLE', -- 'AVAILABLE', 'OCCUPIED'
    current_vehicle_id VARCHAR(20) NULL,
    slot_type TEXT DEFAULT 'STANDARD',
    reservation_expiry TIMESTAMP NULL,
    FOREIGN KEY (current_vehicle_id) REFERENCES vehicles(license_plate) ON DELETE SET NULL
);

-- 4. Transactions Table
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    license_plate VARCHAR(20) NOT NULL,
    slot_id INTEGER NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    duration_minutes INTEGER,
    amount_paid DECIMAL(10, 2),
    status VARCHAR(20) DEFAULT 'ACTIVE', -- 'ACTIVE', 'COMPLETED'
    vehicle_type TEXT DEFAULT 'STANDARD',
    entry_rate REAL,
    rate_breakdown TEXT,
    payment_status TEXT DEFAULT 'PENDING',
    payment_method TEXT,
    payment_reference TEXT,
    payment_time TIMESTAMP,
    FOREIGN KEY (license_plate) REFERENCES vehicles(license_plate),
    FOREIGN KEY (slot_id) REFERENCES parking_slots(id)
);

-- 5. Audit Logs Table
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action VARCHAR(50) NOT NULL, -- 'ENTRY', 'EXIT', 'SLOT_CREATE', 'SLOT_RENAME', 'SLOT_DELETE', 'PRICING_UPDATE'
    license_plate VARCHAR(20) NULL,
    slot_name VARCHAR(50) NULL,
    details TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hash TEXT NULL
);

-- 6. Mobile Users Table (New for Phase 2)
CREATE TABLE IF NOT EXISTS mobile_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone VARCHAR(20) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. User Registered Plates Table (New for Phase 2)
CREATE TABLE IF NOT EXISTS user_plates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    license_plate VARCHAR(20) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES mobile_users(id) ON DELETE CASCADE
);

-- --- INDEXES FOR PERFORMANCE OPTIMIZATION ---
CREATE INDEX IF NOT EXISTS idx_transactions_plate ON transactions(license_plate);
CREATE INDEX IF NOT EXISTS idx_transactions_entry ON transactions(entry_time);
CREATE INDEX IF NOT EXISTS idx_transactions_exit ON transactions(exit_time);
CREATE INDEX IF NOT EXISTS idx_slots_status ON parking_slots(status);
CREATE INDEX IF NOT EXISTS idx_transactions_vehicle_type ON transactions(vehicle_type);
CREATE INDEX IF NOT EXISTS idx_transactions_entry_rate ON transactions(entry_rate);
CREATE INDEX IF NOT EXISTS idx_transactions_payment_status ON transactions(payment_status);

-- Seed Initial Data
-- Seed standard pricing rule
INSERT OR IGNORE INTO pricing_rules (id, rule_name, free_minutes, base_fee, base_hours, hourly_rate, is_active)
VALUES (1, 'Standard Campus Rate', 15, 40.00, 2, 20.00, 1);

-- Seed some default parking slots
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (1, 'VIP 1', 'AVAILABLE', 'VIP');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (2, 'VIP 2', 'AVAILABLE', 'VIP');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (3, 'Slot A1', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (4, 'Slot A2', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (5, 'Slot A3', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (6, 'Slot A4', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (7, 'Slot A5', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (8, 'Slot A6', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (9, 'Slot A7', 'AVAILABLE', 'STANDARD');
INSERT OR IGNORE INTO parking_slots (id, name, status, slot_type) VALUES (10, 'Slot A8', 'AVAILABLE', 'DISABLED');

-- Seed Mock Mobile Users for testing Mobile APIs
INSERT OR IGNORE INTO mobile_users (id, phone) VALUES (1, '+919876543210');
INSERT OR IGNORE INTO user_plates (user_id, license_plate) VALUES (1, 'MH12QW1234');
INSERT OR IGNORE INTO user_plates (user_id, license_plate) VALUES (1, 'DL3CAY9876');
