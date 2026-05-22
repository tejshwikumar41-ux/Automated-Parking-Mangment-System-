# 🚗 SPARK: Premium Parking Management System (Version 2.0.0)

SPARK is a production-grade, highly secure, and real-time Parking Management System. It is designed to handle thousands of parking slot reservations, vehicle entry/exits, dynamic pricing calculations, payment confirmations, and automated ANPR (Automatic Number Plate Recognition) offline failovers.

---

## 🌟 Key Features

*   **Real-time Live Dashboard**: Implements WebSockets to broadcast instant vehicle entries, exits, slot availability changes, and pricing alerts without requiring page refreshes.
*   **Dynamic Pricing Engine**: Maximizes revenue by dynamically adjusting rates based on:
    *   **Peak Hours**: Higher rates during morning/evening rushes.
    *   **Occupancy Surges**: Scale prices when occupancy goes above 50% and 80%.
    *   **Vehicle Types**: Standard, VIP, and Disabled slot categories with appropriate pricing rules.
    *   **Accidental Bypass**: Stays $\le 3$ minutes are automatically treated as accidental drive-throughs and charged ₹0.00 (marked as `BYPASSED`).
*   **Multi-layered Authentication & RBAC**:
    *   **API Keys (`X-API-Key`)**: Authenticates camera nodes and edge ANPR processes.
    *   **OAuth2 JWT Bearer Tokens**: Authenticates operators and admins for administrative endpoints.
    *   **Role-Based Access Control**: Standardizes endpoints for `admin` vs `operator` roles.
*   **Offline Mode & Resilience**: Camera systems detect network outages, queue events locally in `offline_queue.json`, and auto-sync/replay them once connection is restored.
*   **Cryptographic Audit Logging**: Every transaction, rule change, or slot configuration is logged with sequential SHA-256 hash chaining to ensure tamper-evident records. Includes `/api/audit/verify` to detect data tampering.
*   **Production Analytics**: Provides active metrics monitoring under `/api/metrics`.

---

## 🛠️ Tech Stack

*   **Backend**: Python, FastAPI, Uvicorn, Pydantic, python-jose (JWT), bcrypt.
*   **Database**: PostgreSQL (Production) / SQLite (Local development).
*   **Real-Time**: WebSockets.
*   **Frontend**: HTML5, Vanilla CSS, Vanilla JS.
*   **Containerization**: Docker & Docker Compose.

---

## ⚙️ Configuration (.env)

Create a `.env` file in the project root:

```env
# Database Configuration (PostgreSQL URL in production, SQLite locally)
DATABASE_URL=sqlite:///parking.db
DB_FILE=parking.db

# Security & Tokens
PARKING_API_KEY=your_production_api_key_here
PARKING_JWT_SECRET=your_production_jwt_secret_key_here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=1440

# Third-party Integrations (Optional)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1234567890

# Application Configuration
ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
CORS_ENABLED=true
DEBUG_MODE=false
```

---

## 🚀 Local Quickstart

### Prerequisite
*   Python 3.10+
*   Pip

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Initialize Database
```bash
python init_db.py
```

### 3. Run Server
```bash
python -m uvicorn server:app --port 8000 --reload
```
Open `http://localhost:8000/static/index.html` (or the default frontend layout if mounted) and view API documentation at `http://127.0.0.1:8000/docs`.

### 4. Run E2E Tests
To verify all online, offline, rate calculation, and cryptographic hash verification features:
```powershell
python e2e_test.py
```

---

## 🐋 Docker Setup

Run using Docker Compose:
```bash
docker-compose up --build
```
This builds and exposes the system at `http://localhost:8000`.

---

## 🔵 Deployment Guide (Railway - Recommended)

1.  **Prepare Git Repository**:
    Make sure you have committed your files and `.gitignore` prevents committing your local database or `.env`.
    ```bash
    git init
    git add .
    git commit -m "feat: initial production-ready version"
    ```
2.  **Deploy on Railway**:
    *   Sign in to [Railway](https://railway.app).
    *   Click **New Project** -> **Deploy from GitHub**.
    *   Link your repository.
    *   Click **Add Service** -> **Database** -> **Add PostgreSQL**. Railway will automatically populate `DATABASE_URL` for your app service.
    *   Under your main web service, navigate to **Variables** and add all configuration variables specified in the `.env` template (like `PARKING_API_KEY`, `PARKING_JWT_SECRET`, etc.).
    *   Railway will automatically run the `release` phase in `Procfile` (`python init_db.py`) to create database tables and seed initial data, then launch the FastAPI server.
