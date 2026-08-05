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

**And the strategies do not currently have an edge.** Measured, not assumed:

| run | result |
|---|---|
| backtest, 120 days, 6 symbols | **-15.3%**, 74 trades |
| backtest, 200 days, 18 symbols | **+35.0%**, Sharpe 1.40, 49 trades |
| **walk-forward `trend_breakout`, 3 folds** | **-10.9% mean OOS, 0/3 folds profitable, 26.1% overfit gap** |
| **walk-forward `momentum`, 3 folds** | **-4.9% mean OOS, 1/3 folds profitable, 21.4% overfit gap** |
| **walk-forward `mean_reversion`, 3 folds** | **-6.8% mean OOS, 0/3 folds profitable, 4.4% overfit gap** |

All three testable strategies fail out of sample. (`funding_carry` is the
fourth; it cannot be validated on history at all — see below.)

The +35% row is the one that would tempt you. Ignore it. Walk-forward tuned
parameters on each fold's training window and scored that choice on the window
immediately after — and `trend_breakout` fold 0 trained to **+65.8%** and
delivered **-1.4%**. Both strategies show a >20% train-to-test gap. That is the
signature of curve fitting, not alpha.

Momentum matters most here: it contributed **+$3,030 of the +$3,504** in the
200-day backtest, which made it look like the engine of the whole result. Out
of sample it loses money. That single comparison is the best argument in this
repo for not trusting a backtest.

`mean_reversion` fails differently, and the distinction was worth chasing. Its
overfit gap is only 4.4% — it is *not* fitted to history, it loses about as
much in sample as out. That raised an obvious hypothesis: a strategy that is
steadily wrong may simply be reading a real signal backwards.

So I tested it, which is what `--invert` is for:

```
walk-forward: inverted_mean_reversion
mean out-of-sample return ....   -12.6%
profitable folds .............       0%
```

**The inverse loses more than the original.** That refutes the hypothesis and
settles the question: if both a signal and its opposite lose money, the signal
carries no information, and what you are measuring is the cost of trading.
There is no sign error to fix here and no edge hiding behind one.

This is the most useful thing in this README. A negative result that closes off
a line of enquiry is worth more than another backtest that looks encouraging,
and it took about twenty minutes rather than a funded month.

The verdict the tool printed for all three was "no edge. Do not trade this,"
and I agree with it.

The swing between -15% and +35% depending on which symbols and window you pick
is itself the finding: results that unstable are noise, and a headline backtest
number is not evidence of anything.

What I can vouch for is the risk machinery. In every losing run the drawdown
halt fired and stopped the bleeding at roughly -20%, exactly as configured. The
plumbing works. The alpha is not there yet.

One modelling caveat worth knowing: the backtester holds each asset's *current*
funding rate flat across all history, because the venue does not serve a full
per-asset funding time series. In the 200-day run funding contributed +$400 of
the +$3,504, so that row is softer than it looks.

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

# Does the edge survive on data it was never tuned on? The important one.
python -m hltrader.cli --network mainnet walkforward trend_breakout --sweep

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

The infrastructure is sound; the default parameters are not yet profitable.
The tool for this is `walkforward`, and it is the most useful command here:

```bash
python -m hltrader.cli --network mainnet walkforward trend_breakout \
    --days 300 --folds 4 --sweep
```

It splits history into folds, tunes parameters on each fold's *training*
window, then scores that choice on the window immediately after — data the
tuning never saw. It reports the out-of-sample return, how many folds were
profitable, and the **overfit gap** (how much better training looked than
reality), then gives a blunt verdict.

This is deliberately harder to pass than a backtest. Sweeping parameters over
your whole history and picking the winner does not find an edge — any large
enough grid contains a configuration that looks excellent on any dataset,
including pure noise. Walk-forward asks the only question that matters: if you
had tuned on data available at the time, would it have held up on what came
next?

Rules of thumb when using it:

1. **Keep grids small.** A big grid guarantees a good-looking winner.
2. **Watch the trade count.** A fold with 12 trades has told you nothing.
3. **Prefer consistency over magnitude.** Four modestly profitable folds beat
   one spectacular one and three losers — that pattern is noise, and the
   verdict will say so.
4. **Change one thing at a time.** Compound changes tell you nothing about
   which one mattered.
5. **Paper trade before funding.** Live slippage and fills differ from any
   model, including this one's.

The honest baseline: most systematic retail crypto strategies do not survive
walk-forward. Finding that out for free is the entire point.

### A note on speed

The backtester re-evaluates strategies bar by bar over a rolling window rather
than precomputing indicators across the whole history. That is meaningfully
slower — a 200-day run over 18 symbols takes minutes — and it is a deliberate
trade: it guarantees the strategy sees exactly what the live runner will hand
it. If you need faster sweeps, cut the symbol list rather than the realism.

## Configuration

Everything lives in `config/default.yaml`. Secrets come only from the
environment — the config loader rejects unknown keys, so a typo fails loudly
rather than silently disabling a risk limit.

## Disclaimer

This is trading software, not financial advice. Perpetual futures with leverage
can lose more than the deposited margin. Nothing here is a prediction or a
guarantee of returns. Run it on testnet, read the code, and only trade money
you can afford to lose entirely.
