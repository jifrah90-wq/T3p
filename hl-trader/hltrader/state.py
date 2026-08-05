"""Durable state.

The runner must be restartable: a crash mid-session cannot be allowed to lose
the drawdown counters, because those are what stop a bad day becoming a blown
account. Anything the risk layer depends on is written to disk after every
cycle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass
class RunState:
    version: int = STATE_VERSION
    high_water_mark: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    hard_halt_reason: str = ""
    last_cycle: str = ""
    cycles: int = 0
    realized_pnl: float = 0.0
    # Stops the runner is responsible for, keyed by symbol.
    stops: dict[str, float] = field(default_factory=dict)
    # Which strategy opened each open position, for attribution after restart.
    position_strategy: dict[str, str] = field(default_factory=dict)

    def roll_day(self, equity: float, now: datetime | None = None) -> bool:
        """Start a new UTC day if needed. Returns True if the day rolled."""
        today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
        if self.day != today.isoformat():
            self.day = today.isoformat()
            self.day_start_equity = equity
            return True
        return False

    @property
    def day_date(self) -> date | None:
        return date.fromisoformat(self.day) if self.day else None


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, starting_equity: float) -> RunState:
        if not self.path.exists():
            return RunState(
                high_water_mark=starting_equity, day_start_equity=starting_equity
            )
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Refuse to start flat rather than silently resetting the drawdown
            # counters, which would hand a halted account fresh risk budget.
            raise RuntimeError(
                f"state file {self.path} is unreadable ({exc}). Inspect it and "
                f"either repair or delete it deliberately before restarting."
            ) from exc

        if raw.get("version") != STATE_VERSION:
            raise RuntimeError(
                f"state file {self.path} is version {raw.get('version')}, "
                f"expected {STATE_VERSION}"
            )
        known = set(RunState.__dataclass_fields__)
        return RunState(**{k: v for k, v in raw.items() if k in known})

    def save(self, state: RunState) -> None:
        state.last_cycle = datetime.now(timezone.utc).isoformat()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(state), indent=2))
        tmp.replace(self.path)  # atomic, so a crash never truncates the file


class TradeLog:
    """Append-only JSONL record of every fill, for reconciliation and taxes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        try:
            with self.path.open("a") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            log.exception("could not append to trade log %s", self.path)
