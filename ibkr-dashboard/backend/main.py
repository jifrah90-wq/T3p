"""FastAPI application — IBKR Dashboard backend."""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from connection import ib_manager
from models import HealthResponse
from routers import account, export, market_data, orders, positions, trades

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: configure and connect to IB
    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "7497"))
    client_id = int(os.getenv("IB_CLIENT_ID", "1"))

    ib_manager.configure(host, port, client_id)
    logger.info("Starting IBKR Dashboard backend — connecting to %s:%s", host, port)
    await ib_manager.connect()
    yield
    # Shutdown
    await ib_manager.disconnect()
    logger.info("IBKR Dashboard backend shut down")


app = FastAPI(
    title="IBKR Dashboard API",
    description="Read-only API for Interactive Brokers account data",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account.router)
app.include_router(positions.router)
app.include_router(orders.router)
app.include_router(market_data.router)
app.include_router(trades.router)
app.include_router(export.router)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    if ib_manager.connected:
        return HealthResponse(
            status="ok",
            connected=True,
            account_id=ib_manager.account_id,
            server_version=ib_manager.server_version,
        )
    return HealthResponse(
        status="disconnected",
        connected=False,
        message=(
            "Not connected to IB Gateway / TWS. "
            "Ensure it is running and API connections are enabled."
        ),
    )
