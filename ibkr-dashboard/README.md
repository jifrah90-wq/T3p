# IBKR Dashboard

A read-only dashboard for Interactive Brokers that displays account data, positions, orders, market snapshots, and trade history. Built with **Python / FastAPI** (backend) and **React / Vite / Tailwind** (frontend), using `ib_insync` to communicate with IB Gateway or TWS.

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10+ |
| Node.js | 18+ |
| IB Gateway **or** Trader Workstation (TWS) | Latest stable |

## IB Gateway / TWS configuration

1. Open IB Gateway (or TWS) and log in (paper or live).
2. Go to **Configure > Settings > API > Settings**.
3. Check **Enable ActiveX and Socket Clients**.
4. Set the **Socket port**:
   - **7497** — TWS paper trading (default)
   - **4001** — IB Gateway paper trading
   - **7496** — TWS live trading
   - **4002** — IB Gateway live trading
5. Uncheck **Read-Only API** only if you plan to place orders (this app is read-only).
6. Click **Apply / OK**.

## Quick start

### 1. Backend

```bash
cd ibkr-dashboard/backend

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy the example env and adjust if needed
cp .env.example .env

# Start the API server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive Swagger UI.

### 2. Frontend

```bash
cd ibkr-dashboard/frontend

# Install dependencies
npm install

# Start the dev server
npm run dev
```

The dashboard will be available at `http://localhost:5173`. The Vite dev server proxies all `/api/*` requests to the FastAPI backend on port 8000.

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Connection status, account ID, server version |
| GET | `/account/summary` | NAV, cash, P&L, buying power |
| GET | `/account/pnl-history` | Daily P&L (subscription-based) |
| GET | `/positions` | All open positions with market value and P&L |
| GET | `/orders` | Open / pending orders |
| GET | `/market-data/{symbol}` | Real-time snapshot for a US stock symbol |
| GET | `/trades` | Executed trades (fills) for the current session |
| GET | `/export/{dataset}?fmt=csv` | Export data as CSV (`positions`, `orders`, `trades`, `account_summary`) |
| GET | `/export/{dataset}?fmt=xlsx` | Export data as Excel |

## Project structure

```
ibkr-dashboard/
├── backend/
│   ├── main.py              # FastAPI app, CORS, lifespan
│   ├── connection.py         # IB connection manager singleton
│   ├── models.py             # Pydantic response models
│   ├── routers/
│   │   ├── account.py
│   │   ├── positions.py
│   │   ├── orders.py
│   │   ├── market_data.py
│   │   ├── trades.py
│   │   └── export.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx
│   │   │   ├── Orders.jsx
│   │   │   └── MarketData.jsx
│   │   ├── components/
│   │   │   ├── AccountSummary.jsx
│   │   │   ├── PositionsTable.jsx
│   │   │   ├── OrdersTable.jsx
│   │   │   ├── SnapshotCard.jsx
│   │   │   └── ExportButton.jsx
│   │   └── hooks/
│   │       └── usePolling.js
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── index.html
└── README.md
```

## Paper trading vs live

By default the backend connects to **TWS paper trading** on port **7497**. To use a different mode, edit the `IB_PORT` value in `backend/.env`:

| Mode | Port |
|---|---|
| TWS Paper | 7497 |
| IB Gateway Paper | 4001 |
| TWS Live | 7496 |
| IB Gateway Live | 4002 |

**Always test with paper trading first.**

## Notes

- This application is **read-only** — it never places, modifies, or cancels orders.
- No credentials or account numbers are hardcoded; everything comes from the IB connection.
- The frontend polls the backend every 10 seconds for account and position data.
- A red "Disconnected" banner appears if the backend health check fails.
