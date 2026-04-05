"""IB connection manager singleton — manages the IB Gateway/TWS connection lifecycle."""

import asyncio
import logging

import nest_asyncio
from ib_insync import IB

nest_asyncio.apply()

logger = logging.getLogger(__name__)


class IBConnectionManager:
    """Singleton that manages a single IB connection with reconnect logic."""

    _instance: "IBConnectionManager | None" = None

    def __new__(cls) -> "IBConnectionManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.ib = IB()
        self._host: str = "127.0.0.1"
        self._port: int = 7497
        self._client_id: int = 1
        self._reconnect_attempts: int = 3
        self._reconnect_delay: float = 2.0

    def configure(self, host: str, port: int, client_id: int) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id

    @property
    def connected(self) -> bool:
        return self.ib.isConnected()

    @property
    def account_id(self) -> str | None:
        accounts = self.ib.managedAccounts()
        return accounts[0] if accounts else None

    @property
    def server_version(self) -> int | None:
        if self.connected:
            return self.ib.client.serverVersion()
        return None

    async def connect(self) -> None:
        if self.connected:
            logger.info("Already connected to IB")
            return

        for attempt in range(1, self._reconnect_attempts + 1):
            try:
                logger.info(
                    "Connecting to IB at %s:%s (client ID %s), attempt %d/%d",
                    self._host,
                    self._port,
                    self._client_id,
                    attempt,
                    self._reconnect_attempts,
                )
                await self.ib.connectAsync(
                    self._host, self._port, clientId=self._client_id, timeout=10
                )
                logger.info("Connected to IB — server version %s", self.server_version)
                self.ib.disconnectedEvent += self._on_disconnect
                return
            except ConnectionRefusedError:
                logger.error(
                    "Connection refused — is IB Gateway / TWS running on %s:%s?",
                    self._host,
                    self._port,
                )
                if attempt < self._reconnect_attempts:
                    await asyncio.sleep(self._reconnect_delay * attempt)
            except Exception as exc:
                # Handle "client ID already in use" by bumping the client ID
                if "already in use" in str(exc).lower():
                    self._client_id += 1
                    logger.warning(
                        "Client ID in use, retrying with client ID %d",
                        self._client_id,
                    )
                else:
                    logger.exception("Unexpected connection error: %s", exc)
                if attempt < self._reconnect_attempts:
                    await asyncio.sleep(self._reconnect_delay * attempt)

        logger.error("Failed to connect after %d attempts", self._reconnect_attempts)

    def _on_disconnect(self) -> None:
        logger.warning("Disconnected from IB — will reconnect on next request")

    async def ensure_connected(self) -> None:
        """Reconnect if the connection was dropped."""
        if not self.connected:
            await self.connect()

    async def disconnect(self) -> None:
        if self.connected:
            self.ib.disconnect()
            logger.info("Disconnected from IB")


# Module-level singleton
ib_manager = IBConnectionManager()
