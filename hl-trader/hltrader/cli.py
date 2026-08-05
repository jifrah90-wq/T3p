"""Command line interface.

    hltrader scan                 -- show the tradable universe
    hltrader backtest --days 180  -- historical simulation with costs
    hltrader paper                -- live data, simulated fills, no keys
    hltrader live                 -- real orders (requires HL_SECRET_KEY)
    hltrader status               -- account and open positions
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .backtest import Backtester
from .config import Config, load_config
from .marketdata import INTERVAL_MS, MarketData
from .runner import Runner
from .strategies import build_strategy

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("websocket").setLevel(logging.WARNING)


def cmd_scan(cfg: Config, args) -> int:
    market = MarketData(cfg.base_url)
    symbols = market.select_universe(
        min_volume_usd=cfg.universe.min_daily_volume_usd,
        min_open_interest_usd=cfg.universe.min_open_interest_usd,
        max_symbols=cfg.universe.max_symbols,
        blacklist=cfg.universe.blacklist,
        whitelist=cfg.universe.whitelist,
        include_spot=cfg.universe.include_spot,
    )
    print(f"\n{len(symbols)} tradable symbols on {cfg.network}\n")
    print(f"{'symbol':<14}{'mark':>14}{'24h vol $m':>13}{'OI $m':>11}{'fund/8h':>10}{'maxlev':>8}")
    print("-" * 70)
    for symbol in symbols:
        inst = market.instrument(symbol)
        print(
            f"{symbol:<14}{inst.mark_px:>14,.4f}{inst.day_volume_usd / 1e6:>13,.1f}"
            f"{inst.open_interest_usd / 1e6:>11,.1f}{inst.funding_rate * 8:>10.4%}"
            f"{inst.max_leverage:>8}"
        )
    return 0


def cmd_backtest(cfg: Config, args) -> int:
    market = MarketData(cfg.base_url, cache_dir=Path(cfg.state_dir) / "cache")
    symbols = args.symbols or market.select_universe(
        min_volume_usd=cfg.universe.min_daily_volume_usd,
        min_open_interest_usd=cfg.universe.min_open_interest_usd,
        max_symbols=cfg.universe.max_symbols,
        blacklist=cfg.universe.blacklist,
        whitelist=cfg.universe.whitelist,
    )

    bars = int(args.days * 86_400_000 / INTERVAL_MS[cfg.interval])
    warmup = max(build_strategy(s.name, s.params).warmup_bars for s in cfg.strategies if s.enabled)
    bars += warmup

    print(f"loading {bars} x {cfg.interval} bars for {len(symbols)} symbols...")
    frames = {}
    for symbol in symbols:
        try:
            df = market.candles(symbol, cfg.interval, bars, use_cache=not args.no_cache)
        except Exception as exc:
            print(f"  skip {symbol}: {exc}")
            continue
        if len(df) >= warmup + 10:
            frames[symbol] = df
        else:
            print(f"  skip {symbol}: only {len(df)} bars")

    if not frames:
        print("no symbols had enough history to backtest")
        return 1

    # Current funding held flat across history: the venue does not serve a
    # full funding time series per asset, and a flat estimate is closer to the
    # truth than ignoring funding entirely.
    funding = None
    if args.include_funding:
        rates = market.funding_rates()
        funding = {
            symbol: pd.Series(rates[symbol], index=df.index)
            for symbol, df in frames.items()
            if symbol in rates
        }

    strategies = [
        (build_strategy(s.name, s.params), s.weight) for s in cfg.strategies if s.enabled
    ]
    result = Backtester(cfg, strategies).run(frames, funding)

    print(f"\n{'=' * 46}\nBACKTEST -- {len(frames)} symbols, {cfg.interval} bars\n{'=' * 46}")
    print(result.summary())

    if result.trades:
        by_strategy: dict[str, list] = {}
        for trade in result.trades:
            by_strategy.setdefault(trade.strategy, []).append(trade)
        print(f"\n{'strategy':<20}{'trades':>8}{'win%':>8}{'pnl':>12}")
        print("-" * 48)
        for name, trades in sorted(by_strategy.items()):
            wins = sum(1 for t in trades if t.pnl > 0)
            print(
                f"{name:<20}{len(trades):>8}{wins / len(trades):>8.0%}"
                f"{sum(t.pnl for t in trades):>12,.0f}"
            )

    if args.out:
        result.equity_curve.to_csv(args.out, header=["equity"])
        print(f"\nequity curve written to {args.out}")
    return 0


# Grids kept deliberately small. A big grid guarantees something looks good
# on any dataset, which is the failure this command exists to expose.
SWEEP_GRIDS: dict[str, dict[str, list]] = {
    "trend_breakout": {
        "entry_window": [20, 40, 80],
        "atr_stop_mult": [2.0, 3.0],
        "min_adx": [15, 25],
    },
    "mean_reversion": {
        "entry_z": [1.5, 2.0, 2.5],
        "atr_stop_mult": [1.5, 2.5],
        "max_adx": [20, 30],
    },
    "momentum": {
        "lookback": [72, 168, 336],
        "top_fraction": [0.15, 0.3],
    },
    "funding_carry": {
        "min_hourly_funding": [0.0001, 0.0002],
        "trend_window": [50, 100],
    },
}


def cmd_walkforward(cfg: Config, args) -> int:
    from .validation import walk_forward

    market = MarketData(cfg.base_url, cache_dir=Path(cfg.state_dir) / "cache")
    symbols = args.symbols or market.select_universe(
        min_volume_usd=cfg.universe.min_daily_volume_usd,
        min_open_interest_usd=cfg.universe.min_open_interest_usd,
        max_symbols=cfg.universe.max_symbols,
        blacklist=cfg.universe.blacklist,
        whitelist=cfg.universe.whitelist,
    )
    bars = int(args.days * 86_400_000 / INTERVAL_MS[cfg.interval])

    print(f"loading {bars} x {cfg.interval} bars for {len(symbols)} symbols...")
    frames = {}
    for symbol in symbols:
        try:
            df = market.candles(symbol, cfg.interval, bars, use_cache=True)
        except Exception as exc:
            print(f"  skip {symbol}: {exc}")
            continue
        if len(df) > 200:
            frames[symbol] = df
    if not frames:
        print("no usable history")
        return 1

    grid = SWEEP_GRIDS.get(args.strategy, {}) if args.sweep else {}
    combos = len(list(__import__("itertools").product(*grid.values()))) if grid else 1
    print(f"{args.folds} folds x {combos} parameter set(s) on {len(frames)} symbols\n")

    try:
        result = walk_forward(
            cfg, frames, args.strategy, grid, folds=args.folds, train_frac=args.train_frac
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result.report())
    return 0


def cmd_paper(cfg: Config, args) -> int:
    cfg.execution.dry_run = True
    Runner(cfg).run(max_cycles=args.cycles)
    return 0


def cmd_live(cfg: Config, args) -> int:
    if not cfg.secret_key:
        print("HL_SECRET_KEY is not set. Refusing to start live.", file=sys.stderr)
        return 2
    cfg.execution.dry_run = False

    if not args.yes:
        print(f"\n  network:        {cfg.network}")
        print(f"  account:        {cfg.account_address or '(derived from key)'}")
        print(f"  risk per trade: {cfg.risk.risk_per_trade:.1%}")
        print(f"  max gross lev:  {cfg.risk.max_gross_leverage:.1f}x")
        print(f"  daily stop:     {cfg.risk.daily_loss_limit:.0%}")
        print(f"  max drawdown:   {cfg.risk.max_drawdown_limit:.0%}\n")
        if input("Type LIVE to send real orders: ").strip() != "LIVE":
            print("aborted")
            return 1

    Runner(cfg).run(max_cycles=args.cycles)
    return 0


def cmd_status(cfg: Config, args) -> int:
    market = MarketData(cfg.base_url)
    address = cfg.account_address
    if not address and cfg.secret_key:
        from eth_account import Account

        address = Account.from_key(cfg.secret_key).address
    if not address:
        print("no account address configured", file=sys.stderr)
        return 2

    state = market.info.user_state(address)
    summary = state["marginSummary"]
    print(f"\naccount {address} on {cfg.network}")
    print(f"  account value    {float(summary['accountValue']):>14,.2f}")
    print(f"  notional         {float(summary['totalNtlPos']):>14,.2f}")
    print(f"  margin used      {float(summary['totalMarginUsed']):>14,.2f}")
    print(f"  withdrawable     {float(state['withdrawable']):>14,.2f}")

    positions = state.get("assetPositions", [])
    if not positions:
        print("\n  no open positions")
        return 0
    print(f"\n  {'symbol':<12}{'size':>14}{'entry':>12}{'uPnL':>12}{'lev':>8}")
    print("  " + "-" * 58)
    for entry in positions:
        pos = entry["position"]
        print(
            f"  {pos['coin']:<12}{float(pos['szi']):>14,.4f}"
            f"{float(pos['entryPx']):>12,.4f}{float(pos['unrealizedPnl']):>12,.2f}"
            f"{float(pos['leverage']['value']):>8,.1f}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hltrader", description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to YAML config")
    parser.add_argument("--network", choices=["mainnet", "testnet"], help="override network")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="list the tradable universe").set_defaults(func=cmd_scan)

    bt = sub.add_parser("backtest", help="run a historical simulation")
    bt.add_argument("--days", type=float, default=180)
    bt.add_argument("--symbols", nargs="*", help="override the universe screen")
    bt.add_argument("--out", help="write the equity curve to this CSV")
    bt.add_argument("--no-cache", action="store_true")
    bt.add_argument("--include-funding", action="store_true", default=True)
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser(
        "walkforward", help="out-of-sample validation -- does the edge survive?"
    )
    wf.add_argument("strategy", choices=sorted(SWEEP_GRIDS))
    wf.add_argument("--days", type=float, default=300)
    wf.add_argument("--folds", type=int, default=4)
    wf.add_argument("--train-frac", type=float, default=0.6)
    wf.add_argument("--symbols", nargs="*")
    wf.add_argument(
        "--sweep",
        action="store_true",
        help="tune parameters on each training window before testing",
    )
    wf.set_defaults(func=cmd_walkforward)

    paper = sub.add_parser("paper", help="trade on live data with simulated fills")
    paper.add_argument("--cycles", type=int, default=None)
    paper.set_defaults(func=cmd_paper)

    live = sub.add_parser("live", help="trade for real")
    live.add_argument("--cycles", type=int, default=None)
    live.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    live.set_defaults(func=cmd_live)

    sub.add_parser("status", help="show account and positions").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    if args.network:
        cfg.network = args.network
    setup_logging(args.log_level or cfg.log_level)
    try:
        return args.func(cfg, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
