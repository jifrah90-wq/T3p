# hltrader

A systematic trading system for [Hyperliquid](https://hyperliquid.xyz). It
screens the venue's full instrument list, runs a portfolio of strategies over
it, sizes every position against a fixed risk budget, and executes through the
exchange — in backtest, in paper, or live.

---

## Read this before you fund anything

You asked this system to turn $10,000 into $100,000 in one month. I built the
system. I need to be straight with you about that target, because building it
and hitting it are different problems and only one of them is software.

**The arithmetic.** 10x in 30 days is **+8.0% per day, compounded, every day,
for a month** (1.080³⁰ ≈ 10.1). Not 8% on winning days — 8% net, after losses,
fees and funding, on average, across the whole month.

**What that requires.** This system risks 1% of equity per trade and, with all
four strategies live and typical signal frequency, takes on the order of 1–3
trades a day. At a realistic 40% win rate and 2:1 reward-to-risk, expectancy is
about +0.2% of equity per trade. That compounds to roughly **+10–20% per
month**, not +900%. To reach 8% daily you would need to run roughly 20–30% of
equity at risk per trade at 5–10x leverage. At that sizing, four consecutive
losses — an entirely ordinary run, roughly a 1-in-8 event at a 40% win rate —
takes the account to zero. The probability of ruin before the probability of
10x is not close.

**So the honest position:** the 10x target is not a configuration I can set. No
risk setting in this repo reaches it, and the ones that could reach it reach
zero first, far more often. I have not built a system that pretends otherwise,
because a system that hides that trade-off from you is worse than no system.

What this system *is* built to do is trade a real edge with controlled
downside, survive its losing streaks, and compound. If the strategies work
out-of-sample, that is a good outcome. It is not a 10x in a month.

**Backtested performance is currently negative.** On the last 120 days of
mainnet hourly data across the liquid universe, the default configuration lost
money — the trend-breakout book in particular. The risk controls worked exactly
as designed (the drawdown halt fired and stopped the bleeding at -20%), which
is the part I can vouch for. The alpha is not yet there. Do not run this live
until you have found a configuration that survives out-of-sample testing, and
understand that "found by tuning until the backtest looks good" is how you
build a system that loses money live with great confidence. See
[Finding an edge](#finding-an-edge).

---

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Quick start

```bash
# What is liquid enough to trade right now?
python -m hltrader.cli --network mainnet scan

# Does the strategy make money on history? (fees, slippage and funding included)
python -m hltrader.cli --network mainnet backtest --days 180

# Trade it on live prices with simulated fills. No keys needed, no risk.
python -m hltrader.cli --network mainnet paper

# Real orders. Requires HL_SECRET_KEY.
python -m hltrader.cli --network testnet live
```

## Going live

1. **Generate an API wallet** at app.hyperliquid.xyz → More → API. Use that
   key, never your seed phrase. An API wallet can trade but cannot withdraw,
   so a compromised bot host cannot drain the account.
2. `cp .env.example .env` and fill in `HL_SECRET_KEY` and
   `HL_ACCOUNT_ADDRESS`.
3. **Run on testnet first**, for at least a week. Confirm fills, stops and
   reconciliation all behave.
4. Only then set `network: mainnet`, and start with a fraction of the capital
   you eventually intend to use.

`live` prompts for confirmation before sending anything. `--yes` skips it, for
running under a supervisor.

## How it works

```
marketdata  →  strategies  →  risk  →  broker
   ↑                                      ↓
   └──────────  runner (reconcile)  ←──────┘
```

**marketdata** pulls instrument metadata and screens the universe by 24h volume
and open interest. Illiquid markets are where a small account quietly bleeds to
slippage, so this screen is the first risk control, not a convenience.

**strategies** each take one symbol's OHLCV and return a `Signal`: a direction
in [-1, 1] and a stop distance. They never decide size. A strategy cannot blow
up the account by itself, by construction.

| strategy | thesis | works when |
|---|---|---|
| `trend_breakout` | Donchian breakout, EMA trend filter, ADX gate | crypto trends persist |
| `momentum` | cross-sectional, ranks the universe, longs leaders / shorts laggards | dispersion between assets |
| `mean_reversion` | fades z-score extremes, only when ADX says no trend | chop |
| `funding_carry` | collects perp funding, gated on the trend | calm, high-funding markets |

**risk** turns a signal into a size. The rule: a position is whatever size
makes a stop-out cost exactly `risk_per_trade` of equity. That keeps loss per
trade constant regardless of the asset's volatility — a wide-stop altcoin gets
a *small* position, not a big one. Then four caps apply in order (per-position
leverage, gross leverage, net directional exposure, position count), and the
tightest one wins. Round-trip fees are charged against the risk budget so
they're accounted for before the trade, not discovered after it.

Two circuit breakers sit above all of it:
- **daily loss limit** (default 6%) — stops trading for the rest of the UTC day
- **max drawdown** (default 20% from the high-water mark) — hard halt; flattens
  everything and will not resume until a human clears it

**broker** is one interface with two implementations. `PaperBroker` fills
against live mids with a slippage model; `HyperliquidBroker` sends real orders.
The runner cannot tell the difference, so the code that trades your money is
the code you tested, not a variant of it.

**runner** reconciles against the venue every cycle before doing anything else.
Positions change underneath a bot — a stop fires, a liquidation happens, someone
trades the account by hand. Trusting local state after any of those is how a bot
doubles a position it thinks it doesn't have.

## Safety properties worth knowing

- **Exchange-side stops.** Every position gets a reduce-only trigger order
  resting at the venue. It is the only protection that survives this process
  crashing, the network dropping, or the box rebooting. If the stop is
  rejected, the runner closes the position immediately — an unprotected
  position is worse than no position.
- **Atomic state.** Drawdown counters are written to disk after every cycle via
  a temp-file rename, so a crash can't truncate them. A corrupt state file
  refuses to start rather than silently resetting the halt counters, which
  would hand a halted account a fresh risk budget.
- **Graceful shutdown.** SIGINT/SIGTERM finish the current cycle, then stop.
- **Paper by default.** `dry_run: true` in config. Live requires an explicit
  flag *and* a typed confirmation.

## Testing

```bash
pytest -q     # 85 tests
```

The one that matters most is
`test_appending_future_data_does_not_change_the_past`. It runs the same
simulation over 700 bars and over 900 bars and asserts every trade in the
shared window is byte-identical. If any component anywhere peeks at a future
bar, that test fails. Without it, every performance number this system produces
would be fiction.

Others worth knowing about: indicators are checked for causality directly;
sizing is checked to risk *exactly* the configured fraction; the backtester is
checked to fill on the next bar's open rather than the signal bar's close (the
classic backtest lie), and to assume the stop wins when a bar touches both stop
and target.

## Finding an edge

The infrastructure is sound; the default parameters are not yet profitable. If
you want to pursue this seriously:

1. **Backtest over multiple regimes**, not one. A parameter set tuned on 120
   days of one market is fitted to that market.
2. **Hold out data.** Tune on one period, verify on a period you never looked
   at. If it only works on the tuning period, it doesn't work.
3. **Change one thing at a time** and re-run. Compound changes tell you nothing
   about which one mattered.
4. **Watch trade count.** A strategy with 12 trades that "works" hasn't shown
   you anything — that's noise.
5. **Paper trade before funding.** Live slippage and fills differ from any
   model, including this one's.

The honest baseline: most systematic retail crypto strategies do not survive
step 2. Finding out cheaply is the point of the backtester.

## Configuration

Everything lives in `config/default.yaml`. Secrets come only from the
environment — the config loader rejects unknown keys, so a typo fails loudly
rather than silently disabling a risk limit.

## Disclaimer

This is trading software, not financial advice. Perpetual futures with leverage
can lose more than the deposited margin. Nothing here is a prediction or a
guarantee of returns. Run it on testnet, read the code, and only trade money
you can afford to lose entirely.
