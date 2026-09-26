# GoldSignalsAI — MetaTrader 5 port of "Lux Algo Signals & Overlays [6.3]"

MT5 implementation of the signal engine in [pinescriptbot.pine](pinescriptbot.pine), for
**XAUUSD only**. Research/backtest build: the EA refuses to run on a real account.

The Pine script only *draws* signals. The signal maths is ported exactly; the trade
management (stops, exits, sizing) is an addition and is clearly marked as such.

---

## What the signal actually is

Reading the 1844-line script, the tradable core needs **none** of its closed-source imports:

| Piece | Pine lines | Ported? |
|---|---|---|
| **SuperTrend AI (k-means clustering)** — the signal | 1310-1491 | yes, exactly |
| **Chop / fakeout filter** — the author's own false-positive cure | 1509-1606 | yes, exactly |
| **Take-Profit Warning** — mean-reversion channel + WaveTrend 3D | 1609-1745 | yes, minus 2 triggers (below) |
| Smart Trail, Trend Catcher, Trend Tracer, Neo Cloud, dashboard metrics | library import | **no** — see below |
| Sessions, TP/SL boxes, colours, hindsight labels | various | no — cosmetic |

### The engine, in one paragraph

`sensitivity` sets a grid of SuperTrend multipliers (`max(sens-4,1)` to `min(sens,26)` in steps of
0.5 — the default 5 gives 9 of them: 1.0 … 5.0), all on `ATR(atr_length=10)`. Each one carries a
running performance score. Every bar those 9 scores are clustered by **k-means into 3 groups**
(centroids seeded from the 25/50/75 percentiles), and the mean multiplier of the *best* cluster
builds one final SuperTrend. **A signal is that final SuperTrend flipping direction** — so these are
trailing-stop flips, i.e. trend-following entries. `perf_idx` (the best cluster's mean score over
`EMA(|Δclose|,10)`) is the "AI signal quality" behind the 1-4 strength labels.

Signals are computed from `close` on **closed bars only** and never repaint.

### What was deliberately not reconstructed

`Smart Trail`, `Trend Catcher`, `Trend Tracer`, `Neo Cloud`, `Trend Strength`, `Volatility` and
`Squeeze` come from `ayvaliktrading/EyopsTelegram`, a closed-source TradingView library that cannot
be read. Guessing them was rejected in favour of an exact port, because:

* `signalPresets` defaults to `"None"`, so **none of them gate entries** at the script's own
  defaults — entry behaviour is unaffected;
* the only real consequence is that the Take-Profit Warning loses its `trailTurnedBear/Bull` and
  `catcherTurnedBear/Bull` triggers (pine lines 1635-1638), so the reversal exit fires **somewhat
  less often** here than on TradingView. That is a coverage difference, not a logic difference.

### Other known deviations, all deliberate

1. **`maxData` gate.** Pine wraps the clustering in `if last_bar_index - bar_index <= 2500`
   (line 1409) — a TradingView execution-budget guard, which on a chart means only the most recent
   2500 bars ever produce a signal. There is no such limit here, so a long backtest produces signals
   throughout. Set `max_data=2500` in the Python engine to reproduce a chart exactly.
2. **First-bar true range.** `ta.atr` feeds on `ta.tr(true)`, which is `na` on bar 0; both
   implementations here use `high-low` instead, starting the recursion one bar earlier. The effect is
   confined to the first few bars of the series, thousands of bars before any trade.
3. **Warm-up dependence.** The engine is recursive from the first bar it is fed, so state depends
   slightly on where warm-up starts. One constant (`InpWarmupBars`, default 3000) is used
   everywhere, and the parity check pins it down.

---

## Trade management (NOT from the Pine script)

```
entry    at the open of the bar AFTER the signal bar   (closed-bar signal, no lookahead)
stop     entry ∓ InpSLAtrMult * ATR(signal bar)        default 1.5
trail    the engine's own adaptive line `ts`, never loosening
exit     optional, when the ported Take-Profit-Warning reversal fires
reverse  on the opposite signal
sizing   % of balance against the stop distance, or a fixed lot
```

Every part is individually switchable, so the backtest can show which ones actually earn anything.

---

## Layout

```
mt5/
  Experts/GoldSignalsAI/GoldSignalsAI_EA.mq5          the EA
  Indicators/GoldSignalsAI/GoldSignalsAI_Signals.mq5  arrows + trail + chop reading + parity dump
  Scripts/GoldSignalsAI/GoldSignalsAI_Dump.mq5        standalone history/engine exporter
  Include/GoldSignalsAI/
    PineMath.mqh        Pine primitives (rma/ema/atr/percentrank/supersmoother/kernels)
    SuperTrendAI.mqh    the engine + chop filter
    ReversalExit.mqh    mean-reversion channel + WaveTrend 3D + exhaustion switch
    Diagnostics.mqh     per-bar CSV dump for the parity check
research/
  pine_math.py          the same primitives in numpy  (mirrors PineMath.mqh)
  engine.py             the same engine               (mirrors SuperTrendAI.mqh)
  reversal_exit.py      the same exit                 (mirrors ReversalExit.mqh)
  backtest.py           bar-close backtester, same trade model as the EA
  filters.py            the EMA200 and higher-timeframe entry gates
  dataset.py            load bars from the MT5 cache or any OHLC CSV
  hcc_reader.py         read M1 bars straight out of MT5's .hcc cache (no login)
  mt5_data.py           connect to the terminal, describe XAUUSD, cache history
  parity_check.py       MQL5 vs Python, column by column
  sweep.py              baseline report + 70/30 train/test parameter sweep
  experiments.py        round 1: ~35 structural variants
  experiments2.py       round 2: stacking, walk-forward, bootstrap
  experiments3.py       round 3: partial exits and re-entry
  final_report.py       the headline M15 / M5 table
  config_report.py      account-level detail for the shipped config
  ftmo_challenge.py     +10% before -10%, simulated from 78 start dates
  parse_report.py       pull the numbers out of a tester HTML report
tools/
  deploy.ps1            copy sources into the MT5 data folder
  compile.ps1           MetaEditor CLI compile, fails on errors
  run_dump.ps1          export bars + engine output via a chart-attached indicator
  run_tester.ps1        generate a tester .ini and drive the Strategy Tester
```

**MT5 built-ins are deliberately avoided.** `iATR`/`iMA` seed their recursions differently from
Pine, which would silently break parity. Every primitive is hand-written in both languages.

---

## How to run it

### 1. Deploy and compile

```powershell
powershell -ExecutionPolicy Bypass -File tools\deploy.ps1
powershell -ExecutionPolicy Bypass -File tools\compile.ps1
```

Currently compiles with **0 errors, 0 warnings** (MetaEditor build 6230).

### 2. Prove the port is faithful — do this before trusting any number

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_dump.ps1 -Symbol XAUUSD -Period M15
cd research; python parity_check.py
```

The indicator dumps every bar it sees, and `parity_check.py` re-runs the Python engine over the
**OHLC columns from that same file**. Taking the bars from the dump removes any data-source
mismatch, so a difference can only be a logic difference. It prints a per-column diff count and the
first divergent bar. (With a working account, `tools\run_tester.ps1 -Mode Parity` does the same
thing through the EA in the Strategy Tester.)

### 3. Research backtest (fast, for parameter work)

```powershell
cd research
python hcc_reader.py                    # bars from the MT5 cache, no login needed
# python mt5_data.py                    # ...or via the bridge, with a logged-in terminal
python sweep.py --baseline --spread 0.44 --commission 0
python sweep.py --sweep --quick --spread 0.44 --commission 0
python final_report.py                  # the headline table in Results, M15 and M5
```

Any OHLC CSV works too, no terminal needed:
```powershell
python sweep.py --source csv --csv path\to\XAUUSD_M15.csv --baseline
```

### 4. Strategy Tester (realistic fills)

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_tester.ps1 -Mode Backtest -Period M15 `
    -FromDate 2024.01.01 -ToDate 2026.09.01
cd research; python parse_report.py
```

`-Mode Backtest` uses 1-minute OHLC; pass `-TickModel 4` for "every tick based on real ticks" on
the finalists. Note `run_tester.ps1` **closes any running terminal**, because MT5 ignores
`/config` while an instance of the same installation is up.

The tester does work, with one trap worth knowing: the `.ini` must be written **without a
UTF-8 BOM**. PowerShell 5.1's `Out-File -Encoding utf8` prepends `EF BB BF`, which turns the
first line into `\ufeff[Tester]` — not a section header — so MT5 ignores the entire `/config`
and silently starts with the last profile instead. Nothing is logged; it merely looks like the
tester refused to start. `run_tester.ps1` now uses `[IO.File]::WriteAllText` with
`UTF8Encoding($false)`.

Verified end to end on 2026-09-26 (XAUUSD M15, 2026.07.01 → 08.01, `-TickModel 2`): 2,013 bars,
history quality 100%, 4 orders sent, 0 rejects, report written and `parse_report.py` reads it.
That exercises the whole chain — .ini → tester agent → `CTrade` order send → stop-loss attach
→ SL trigger → deal → HTML report → parser.

### 5. Visual check against TradingView

Attach `GoldSignalsAI_Signals` to an XAUUSD M15 chart with the same inputs and compare the arrows
against the TradingView original. The engine is identical to the EA's, so this is also the quickest
sanity check on the port.

---

## Results

Primary data: **XAUUSD M15, 99,999 bars, 2022-06-27 → 2026-09-25** (4.25 years), pulled from
MetaQuotes-Demo. Costs throughout are FTMO's real XAUUSD terms: spread 0.44 USD/oz, commission
0.0014% PERCENT/VOLUME, swap -83/-8.3 points per night. Sizing is a fixed % of balance against the
stop, compounding, from $10,000.

Read the section immediately below and treat everything after it as history: earlier sections were
computed on 7.5 months and then 2.8 years, and both overstated the edge.



### CURRENT: the H1 structural rebuild (this is the live config)

Everything below this section describes earlier versions that did not survive.
The strategy was changed structurally, not retuned, after measuring where money
was actually lost inside a trade (`trade_anatomy.py`).

#### What the measurement showed

| trades that reached | % that still ended at a LOSS |
|---|---|
| +0.5R | **71%** |
| +1.0R | **49%** |
| +2.0R | 27% |

Mean peak favourable excursion 0.897R, mean final 0.365R - **59% of every winner
handed back**. Holding time was binary: every bucket under 41 bars averaged
-1.02R (a clean full stop), 41+ bars averaged +1.74R. Nothing in between.

The cause is structural: the trail follows the adaptive line, which sits about
factor x ATR from price, so a freshly flipped trade has **no protection at all
for its first bars** and can round-trip from +1R to a full stop. And only 46% of
trades were in profit at bar 0 - many entries chase an already-extended move.

#### The two changes

1. **Breakeven ratchet at +1R** - once open profit reaches 1R, the stop goes to
   breakeven plus a small offset, independently of the adaptive line. Unlike a
   fixed target this caps losses, not winners. A fixed 3xATR target was tested
   and destroys the edge; so does a time stop (PF 1.44 -> 1.15) because trades
   that are flat at bar 20 become the runners.
2. **WaveTrend entry agreement** - refuse an entry when the WaveTrend oscillator
   disagrees with the signal direction. This machinery was already ported and
   parity-verified but was only wired to the *harmful* reversal exit.

On 4.25 years of M15 these took PF 1.406 -> 1.580 and drawdown 19.0% -> 13.8%.

#### Why H1, not M15

Re-running the full search with the structural fixes in place, on 11 years of H1:

| | without the fixes | **with them** |
|---|---|---|
| anchored walk-forward | **-1,679** | **+4,691** |
| folds profitable | 4/8 | **6/8** |
| best consistent config | PF 1.094, DD 17.4%, 7/10 | **PF 1.392, DD 12.7%, 9/10** |

An 11-year loser became an 11-year winner under honest parameter reselection.
H1 also has 11 years of history against M15's 4.25, trades less often so spread
matters less, and is far less exposed to entry latency.

#### The configuration

```
chart              XAUUSD H1
sensitivity        6.0          ATR length          14
chop filter        Strong       min signal strength 1
SL                 2.5 x ATR    trend filter        EMA200
HTF agreement      H4           reversal exit       off
breakeven ratchet  +1.0R        WaveTrend entry     on
risk               0.5% of balance
```

#### Results

**Python, 11 years, FTMO costs (spread 0.44, commission, swap):**
244 trades, **+3,885**, PF **1.392**, max drawdown **12.7%**, win rate 37.3%,
expectancy +15.92/trade, **8 of 12 years profitable** (losses: 2015 part-year,
2016, 2018 -124, 2026 part-year).

**MT5 Strategy Tester, same window, 1-minute OHLC, 99% history quality:**
242 trades, +4,096, PF **1.67**, balance drawdown **5.04%**. Trade counts agree
within 2.5%; the tester is better only because it replays the demo's ~0.08
spread with no commission, so the Python figures remain the conservative ones.

**FTMO simulation, 283 starts across 11 years, $10k:**

| risk | passed | **failed** | median days to target | worst DD |
|---|---|---|---|---|
| 0.25% | 129 | **0** | 114 | 4.9% |
| **0.50%** | **179** | **0** | **55** | **8.1%** |
| 0.75% | 212 | 7 | 33 | 10.4% |
| 1.00% | 216 | 14 | 28 | 10.6% |

**0.50% is the shipped setting: zero breaches across 283 starts spanning 11
years**, worst drawdown 8.1%, median 55 trading days to +10%.

#### Two tooling bugs found here, both of which had produced false results

* **The Strategy Tester persists input values per expert between runs.** Any
  input not listed in `[TesterInputs]` silently reuses the last saved value
  instead of the newly compiled default - so an H1 run executed the previous
  M15 configuration while reporting success. `run_tester.ps1` now pins the whole
  config, generated from `shipped.py`.
* **`[TesterInputs]` parses enums numerically.** `InpHtfPeriod=PERIOD_H4` is not
  rejected; it resolves to 0 (`PERIOD_CURRENT`). The integers are emitted now.

Both are the same class of failure: a run that looks successful while testing
something other than what you think.


### (superseded) Headline: what 4.25 years says (2022-06 to 2026-09, 99,999 M15 bars)

**This supersedes every result further down.** The numbers below it were computed
on 7.5 months, then on 2.8 years. Each time the sample grew, the edge shrank.
That pattern is the most important fact in this file.

| sample | headline PF | max DD | walk-forward (honest reselection) |
|---|---|---|---|
| 7.5 months (2026) | 1.66 | 9.2% | not run |
| 2.8 years (2024-2026) | 1.69 | 9.6% | **+5,675**, 4/6 folds |
| **4.25 years (2022-2026)** | **1.41** | **19.0%** | **-630, 1/8 folds** |

#### The shipped config, held fixed, over 4.25 years

223 trades, **+4,831**, PF **1.406**, max drawdown **19.0%**, 5 of 8 folds
profitable (at 0.75% risk). Bootstrap: median DD 14.5%, **95th percentile
25.2%**, 6.8% chance of an overall loss.

Fold by fold (~6 months each):

| fold | period | trades | net | PF |
|---|---|---|---|---|
| 1 | 2022 H2 | 24 | **-605** | **0.54** |
| 2 | 2023 H1 | 34 | **-818** | **0.54** |
| 3 | 2023 H2 | 31 | +1,091 | 1.92 |
| 4 | 2024 H1 | 26 | +1,871 | 2.60 |
| 5 | 2024 H2 | 30 | +59 | 1.03 |
| 6 | 2025 H1 | 34 | +974 | 1.56 |
| 7 | 2025 H2 | 26 | +2,305 | 2.39 |
| 8 | 2026 | 18 | -45 | 0.96 |

**Folds 1 and 2 are the cleanest out-of-sample test that exists here** - 2022-06
to 2023-07 was never used to select anything - and the config lost money in both,
at PF 0.54. Everything that looked good was chosen on data from 2024 onward.

#### Re-optimising does not rescue it

A full anchored walk-forward over the 4.25 years - re-picking the best
configuration on everything seen so far, then trading the next fold with it -
returns **-630 with 1 of 8 folds profitable**. And that single winning fold
(+6,527) carries the whole result; without it the total is -7,157.

Searching 2,405 configurations found nothing that is both consistent and safe.
The most consistent (8 of 10 folds) carries a **35.9% drawdown**; the calmest
(8.6% DD) manages 7 of 10 folds at PF 1.23. There is no cell that is both.

#### FTMO pass rate on 4.25 years

219 simulated challenge starts, $10k, real FTMO costs:

| risk | passed | **failed** | pass rate | median days to target | worst DD |
|---|---|---|---|---|---|
| **0.25%** | 133 | **0** | **100%** | 81 | 6.0% |
| 0.50% | 138 | **25** | 85% | 36 | 10.4% |
| 0.75% | 119 | 44 | 73% | 23 | 10.9% |
| 1.00% | 117 | 55 | 68% | 14 | 11.8% |

On 2.8 years, 0.5% showed **zero** failures. On 4.25 years it fails 25 of 163
finished challenges - roughly a 1-in-7 chance of losing the fee. Only 0.25% risk
breaches nothing, and it needs a median of 81 trading days (about four months).

#### Per-regime configs were tested and rejected

The obvious objection to one config across 4.25 years is that gold had several
regimes. It was tested properly (`regime_test.py`), walk-forward, with the
regime classified causally from trailing data only:

| walk-forward | single config | one config per regime |
|---|---|---|
| volatility regimes (ATR vs trailing median) | **+6,337** (3/6 folds) | **-1,557** (1/6) |
| trend regimes (above/below EMA200) | **+6,337** (3/6 folds) | **-4,451** (1/6) |

Per-regime selection loses, and the test was rigged in its favour: trades are
attributed to the regime at entry and each config's trades are simply taken
while its regime is active, ignoring that one account cannot hold two configs'
positions at once. A negative result under those conditions is conclusive.

The mechanism is sample size. Splitting by regime halves the data each config is
chosen on, so each one fits noise harder; the single config uses all of it. The
diagnosis (`regime_diag.py`) says the same thing from the other direction -
across 17 periods, the descriptors that would have to do the switching barely
separate winning periods from losing ones:

| descriptor | winning periods | losing periods | correlation with PF |
|---|---|---|---|
| ATR rank | 49.70 | 49.73 | 0.02 |
| chop rank | 50.08 | 50.07 | -0.28 |
| efficiency ratio | 0.101 | 0.097 | 0.15 |
| **% of bars above EMA200** | **58.1%** | **51.1%** | **0.36** |
| gold's own return | +6.8% | +3.3% | 0.21 |

Only the trend descriptors carry any signal, and the EMA200 filter already uses
that. The losing stretches are contiguous in time (2022-06 to 2023-09), not
scattered across volatility states - which points at a change in gold itself
rather than a regime the system could have detected and adapted to.

#### The walk-forward estimate is itself unstable

Worth stating plainly: the single-config walk-forward came out at **-630** under
`optimize.py` (30 engines x 120 configs, 10 folds) and **+6,337** under
`regime_test.py` (12 engines x 24 configs, 8 folds). Same data, same method,
different grid and fold count.

An edge that swings from clearly negative to clearly positive on those choices
is not a measured edge - it is inside the noise. That instability is the honest
summary of this strategy's evidence.

#### Deep history settles it: 11 years on H1, 22 years on H4

MetaQuotes caps history at ~100k bars *per timeframe*, so H1 reaches back 11
years and H4 reaches 22 - covering 2008, the 2011 peak, the 2013 crash and the
2015-18 range, not just the 2024-26 bull run.

The shipped M15 config transplanted to those timeframes:

| timeframe | span | trades | net | PF | max DD | profitable years |
|---|---|---|---|---|---|---|
| H1 | 11.0 years | 157 | **-2,689** | **0.616** | 39.7% | **2 of 12** |
| H4 | 22.3 years | 82 | **-2,323** | **0.496** | 30.0% | **7 of 22** |

That alone is not conclusive - the config was tuned for M15 and gives only ~14
trades a year there. So the search was re-run from scratch on 11 years of H1
(2,400 configurations, 10 folds):

* the most consistent configuration manages **PF 1.094 with a 17.4% drawdown**,
  7 of 10 folds - and several of the "most consistent" ones **lose money overall**
  (PF 0.877, PF 0.965);
* **anchored walk-forward over 11 years: -1,679, 4 of 8 folds profitable.**

So across every honest test now run:

| test | span | walk-forward result |
|---|---|---|
| M15 | 4.25 years | -630 (1/8 folds), or +6,337 on a different grid - i.e. noise |
| H1 | 11 years | **-1,679** (4/8 folds) |
| H4 (shipped config) | 22 years | PF 0.496, 7/22 profitable years |

**The conclusion is that this strategy does not have a reliable edge on gold.**
The 2024-2026 M15 performance was a favourable-regime artifact, and no amount of
further parameter search fixes it - 2,400 configurations over 11 years produced
nothing that is both consistent and profitable out of sample.

#### What this means

The engine is faithfully ported and the execution is validated - those results
stand. But the *strategy* does not have an edge that survives honest testing
across 4.25 years of gold. It made money in 2024-2025, lost in 2022-2023, and
re-optimising as you go loses money.

Anyone funding a challenge on this should size at 0.25% and expect four months,
or treat the whole thing as unproven. The safety guards in *Going live* exist
precisely because this evidence is weak.

### (Superseded below this point - kept for the record)

### Parity: the port is provably exact

```
column           diffs   worst |d|        column            diffs   worst |d|
atr                  0    5.0e-11         signal_strength       0    0
target_factor        0    3.3e-11         raw_buy / raw_sell    0    0
perf_idx             0    5.0e-11         passes_filter         0    0
os                   0    0               buy / sell            0    0
ts                   0    5.0e-11         condition             0    0
overlap_ratio        0    5.0e-11         bear_exhaustion       0    0
overlap_rank         0    7.1e-15         bull_exhaustion       0    0
body_atr             0    5.0e-11
```

**Zero differences on all 17 columns over 3,085 bars** — engine, k-means target factor, signal
quality, chop filter, signals, and the reversal exit. The residuals are the CSV's 10-decimal
rounding, nothing more. The MQL5 EA and the Python reference are the same calculation.

The M1→M15 resampling was cross-checked independently: 3,084 of 3,085 bars match MT5's own chart
aggregation exactly. The one exception is the high of a Friday-close bar (2026-08-14 22:45), off by
0.54 USD, where MT5's chart had a tick the M1 cache does not.

### Re-optimised on 2.8 years — this supersedes the 7.5-month numbers below

The tables from `### M15 backtest` down to `### Improvement experiments` are the January–August
2026 sample: 14,719 bars, one trending gold market. This re-test supersedes them where they
disagree. It runs on **65,000 M15 bars (2023-12-15 → 2026-09-25)** from the MetaQuotes-Demo feed
(`MQGOLD`, `research/data/MQGOLD_M15.npy`), same 0.44 USD/oz spread, searching **32 engine builds ×
96 trade configs = 3,072 evaluations** over 8 sequential folds, then re-picking parameters as it
went (anchored walk-forward).

| configuration | trades | net ($10k) | PF | max DD | profitable folds |
|---|---|---|---|---|---|
| shipped before this re-test (sens 5, Strong, SL 1.5, H1 on) | 396 | +5,468 | 1.295 | 27.5% | 4/8 |
| **re-selected (sens 10, Light, SL 2.5, H1 off)** | **138** | **+5,586** | **1.686** | **9.6%** | **8/8** |

The old configuration ranked **#583 of 2,340** qualifying configurations on fold consistency. Three
of the earlier conclusions turned out to be properties of the short sample, not of the strategy:

* **sensitivity 5 → 10** and **SL 1.5×ATR → 2.5×ATR**;
* **minimum signal strength 1 → 2**;
* **the H1 agreement filter is off by default** — on the long sample it cuts the book to 62 trades
  and only 5 of 8 folds stay profitable, at a cost higher than it adds.

The EMA200 regime filter survives the longer test, and the chop filter turns out to help at **any**
strength (off/Light/Medium/Strong all score PF 1.60–1.74), so **Light** is now shipped as the least
aggressive setting that captures the effect.

**Anchored walk-forward: +5,675 on $10,000, 4 of 6 folds profitable.** That is the number to
believe — it re-picks parameters at every step, so there is no hindsight — and it includes two
losing folds (−1,862 and −866). The aggregate is much less impressive than the fitted table above,
which is the point.

Caveats, plainly:

* the long sample is a **different feed** (`MQGOLD` from MetaQuotes-Demo, not the FTMO cache), so
  spread and tick structure are not identical to the 2026 sample;
* `research/validate_config.py` says the selected cell is **spiky on two axes**: sensitivity 10
  scores PF 1.686 with 8/8 folds, but 8.0 falls to 1.285 and 12.0 to 1.020; ATR 10 scores 1.686, but
  7 gives 1.206 and 14 gives 1.056. SL 2.5 is a broader optimum (2.0/3.0/4.0 all stay positive). Do
  not read 10 / 10 as precise values — treat it as a region that is sensitive to small changes;
* net profit is **not** higher than the old config. The gain is drawdown (27.5% → 9.6%) and
  consistency (4/8 → 8/8 profitable folds), which is what makes a prop-account run survivable.


### M15 backtest

| configuration | net | PF | trades | win% | max DD |
|---|---|---|---|---|---|
| pine defaults (filter off, reversal exit on) | +53.1% | 1.190 | 559 | 33.3% | 15.0% |
| **recommended: chop Strong, SL 1.5×ATR, no reversal exit** | **+120.8%** | **1.656** | 259 | 34.7% | **9.2%** |
| lower drawdown: same but SL 2.5×ATR | +59.3% | 1.542 | 258 | 38.4% | **6.3%** |
| no filter, reversal exit off (raw signal + stops) | +117.2% | 1.275 | 559 | 31.3% | 17.9% |

Out-of-sample check, 70/30 split (`sweep.py --sweep`): **87 of 256 combinations are profitable in
both halves**, and every one of the top 25 by the weaker half's profit factor uses
**sensitivity 5 and ATR length 10 — the script's own defaults**. The best cell holds up cleanly:
train PF 1.642 (178 trades) / test PF 1.888 (72 trades). This is a broad profitable region, not a
single lucky parameter cell.

### The recommended config on an actual account

`research/config_report.py` regenerates all of this.

```
starting balance      10,000.00 USD      trades                    259
ending balance        22,083.99 USD        winners            90 (34.7%)
net profit           +12,083.99 USD        average win        +339.03 USD
gross profit          30,512.30 USD        average loss       -109.04 USD
gross loss           -18,428.31 USD        expectancy          +46.66 USD
profit factor                1.656         largest win      +1,774.07 USD
max drawdown           1,822.60 USD        largest loss       -237.52 USD
                        (9.2% of peak)     avg hold      15 bars (~3.8 h)
lot size         0.01 / 0.09 / 0.29      trades per month         34.5
                  (min/median/max)        every exit was the stop or trail
longest win streak               5
longest loss streak             15   <-- expect this
```

Month by month — note it lost money in January and was flat in February:

| month | trades | P&L | balance |
|---|---|---|---|
| 2026-01 | 27 | −350 | 9,650 |
| 2026-02 | 36 | +231 | 9,881 |
| 2026-03 | 41 | +2,801 | 12,682 |
| 2026-04 | 35 | +798 | 13,480 |
| 2026-05 | 30 | +1,599 | 15,079 |
| 2026-06 | 31 | +2,092 | 17,170 |
| 2026-07 | 41 | +3,189 | 20,360 |
| 2026-08 | 18 | +1,724 | 22,084 |

**Risk per trade** drives drawdown almost linearly — this is the dial that matters:

| risk % | net | end balance | max DD | max DD % |
|---|---|---|---|---|
| 0.5% | +5,373 | 15,373 | 640 | **4.4%** |
| 1.0% | +12,084 | 22,084 | 1,823 | **9.2%** |
| 1.5% | +23,556 | 33,556 | 4,146 | 13.8% |
| 2.0% | +36,938 | 46,938 | 7,554 | 18.3% |
| 3.0% | +74,500 | 84,500 | 19,809 | 26.6% |

> **If this is for an FTMO challenge, use 0.5% risk, not 1%.** At 1% the measured drawdown is 9.2%,
> which sits right on top of a typical 10% total-loss limit — one slightly worse run fails the
> account. 0.5% halves it to 4.4% and still returned +54% over the period.

**Minimum viable balance.** Below about $2,500 the broker's 0.01 minimum lot stops the sizing from
shrinking, so the effective risk per trade rises and drawdown balloons:

| start | end | return | max DD % | trades stuck at 0.01 lot |
|---|---|---|---|---|
| $500 | $2,186 | +337% | **31.5%** | 235 of 259 |
| $1,000 | $2,841 | +184% | 15.8% | 199 |
| $2,500 | $6,257 | +150% | 8.3% | 86 |
| $5,000 | $11,708 | +134% | 8.5% | 23 |
| $10,000 | $22,084 | +121% | 9.2% | 8 |
| $25,000+ | — | +129% | 9.6% | 0 |

**Costs.** The edge is not fragile to them — even at more than double the spread plus commission:

| spread | commission | net | PF | DD % |
|---|---|---|---|---|
| 0.44 | 0 | +12,084 | 1.656 | 9.2 |
| 0.44 | $3/lot | +11,655 | 1.632 | 9.3 |
| 0.60 | $3/lot | +11,421 | 1.618 | 9.4 |
| 0.80 | $3/lot | +10,551 | 1.574 | 9.6 |
| 1.00 | $6/lot | +9,733 | 1.529 | 9.8 |

**Fixed lot**, if you prefer it to % risk (no compounding): 0.01 lot → +$1,538 (2.1% DD);
0.05 → +$7,690 (8.0%); 0.10 → +$15,380 (15.8%); 0.20 → +$30,760 (31.5%).

### Passing an FTMO challenge: +10% before −10%

A single backtest answers "what happened from January", but a challenge starts the day you buy it.
So `research/ftmo_challenge.py` starts a **fresh challenge on 78 different trading days** and applies
the rules properly: +10% target, −5% max daily loss, −10% max total loss, all measured on **equity**
so floating losses count (approximated from each trade's worst adverse excursion).

| risk/trade | passed | **breached** | ran out of data | median trading days | median calendar days | worst DD |
|---|---|---|---|---|---|---|
| 0.25% | 53 | **0** | 25 | 45 | — | 2.6% |
| 0.50% | 64 | **0** | 14 | 22 | 37 | 5.2% |
| **0.75%** | **70** | **0** | 8 | **14** | **22** | **7.7%** |
| 1.00% | 68 | 5 | 5 | 10 | 15 | 10.2% |
| 1.50% | 63 | 11 | 4 | 7 | — | 11.7% |
| 2.00% | 37 | 37 | 4 | 3 | — | 12.8% |

**With the EMA200 trend filter the same simulation gets strictly better** — `--trend-ema 200`:

| risk/trade | passed | **breached** | ran out | median trading days | median calendar days | worst DD |
|---|---|---|---|---|---|---|
| 0.50% | 65 | **0** | 13 | 19 | 38 | 3.8% |
| 0.75% | 74 | **0** | 4 | 14 | 28 | 5.7% |
| **1.00%** | **74** | **0** | 4 | **12** | **21** | **7.6%** |
| 1.50% | 65 | 9 | 4 | 6 | — | 11.0% |

The filter turns 1.0% risk from *5 breaches and a 10.2% worst drawdown* into *zero breaches and
7.6%*, while getting there faster (median 12 trading days). But weigh that against the bootstrap:
EMA200 at 1% risk still has a 95th-percentile drawdown of **16.7%**, which is over a 10% limit.
Reconciling the two:

* **0.5% risk** — bootstrap 95th percentile ≈ 8.4%, safely inside a 10% wall. Median 19 trading
  days. This is the defensible choice for a paid challenge.
* **0.75%** — bootstrap 95th ≈ 12.5%, observed worst 5.7%. Median 14 days. Reasonable if you accept
  real tail risk.
* **1.0%** — fastest at 12 days, zero breaches observed, but a bootstrap tail of 16.7%. Only if you
  are relaxed about losing the fee.

The bootstrap reshuffles trades independently, which destroys the sequence in which a trend
follower's big wins actually follow its losing runs, so it is a deliberately pessimistic bound —
and the observed path is an optimistic one. The honest answer sits between them.

**Use 0.50% risk.** On the 2.8-year sample `research/ftmo_challenge.py` starts **142 challenges**,
and 0.50% is the only level with **zero breaches**: 87 passed, 0 breached, 55 unfinished, worst
drawdown 6.6%, median **35 trading days** to +10%. Every higher level fails — 0.75% breaches 3 of 87
finished runs, 1.0% breaches 8 of 98, 1.5% breaches 19 of 113, with worst drawdowns of 10.3%, 10.3%
and 12.4%, all at or over a 10% limit.

Read the failure counts, not the speed:

* **78 overlapping windows inside one trending gold market are not 78 independent trials.** "Zero
  breaches" at 0.75% means it never happened *in this sample*, not that it cannot. The real failure
  rate is above zero.
* Floating loss is approximated from **bar highs and lows, not ticks**. A wick inside an M15 bar
  could breach a daily limit where this simulation does not see it.
* "Ran out" runs are unfinished, not failures — the data is only 7.5 months long.
* FTMO has changed its rules over time and they differ by program (time limits, minimum trading
  days, daily-loss basis). **Check your own account's terms** before trusting the table.

### Exact EA inputs for the recommended config

These are the compiled defaults, so a fresh attach already matches — except `InpRiskPercent`,
which you should set deliberately.

```
InpSensitivity        = 10.0       InpUseAtrStop        = true
InpAtrLength          = 10         InpSLAtrMult         = 2.5
InpMinSignalStrength  = 2          InpTPAtrMult         = 0.0     (no fixed target)
InpFilterFakeouts     = true       InpUseTsTrail        = true
InpFakeoutStrength    = Light      InpUseReversalExit   = false
InpUseTrendFilter     = true       InpUseHtfFilter      = false
InpTrendEmaLength     = 200        InpHtfPeriod         = PERIOD_H1
                                   InpHtfWarmupBars     = 1500
InpCustomMaxChop      = 60   (unused unless Strength = Custom)
InpCustomMinQuality   = 0.0  (unused unless Strength = Custom)
InpCustomBodyOverride = 0.0  (unused unless Strength = Custom)
InpReverseOnOpposite  = true       InpUseRiskPercent   = true
InpWarmupBars         = 3000       InpRiskPercent      = 0.50  <- shipped default
InpMagic              = 63063001   InpFixedLot         = 0.10  (only if UseRiskPercent=false)
InpSlippagePoints     = 30         InpMaxSpreadPoints  = 500
InpDumpDiagnostics    = false
```

Chart: **XAUUSD, M15**. Not M5 — see finding 4. The EA needs ~3,000 bars of history before it
trades, and it refuses to start on a real account (research build).

### Improvement experiments

`research/experiments.py` tried ~35 structural variants; `research/experiments2.py` then
re-tested the winners properly, because 35 variants against one test slice produces winners by luck.
Judged on **walk-forward folds improved**, not on a single aggregate.

| idea | trades | PF | DD% | folds improved | verdict |
|---|---|---|---|---|---|
| baseline (chop Strong, 1.5×ATR, trail) | 259 | 1.632 | 9.3 | — | — |
| **EMA200 trend regime** | **139** | **2.290** | **6.6** | **5/6** | **adopted** |
| + H1 engine agreement | 87 | 3.035 | 4.7 | 5/6 | optional, thinner |
| H1 engine agreement alone | 98 | 2.966 | 5.5 | 4/6 | good, fewer trades |
| London hours only (07–15) | 118 | 2.091 | 7.3 | **2/6** | **rejected — see below** |
| H4 agreement | 127 | 1.978 | 10.1 | 3/6 | no |
| longs only | 112 | — | 3.7 | — | rejected: bull-market artifact |
| stop 1.0×ATR | 259 | — | 10.9 | — | rejected: train +11.6k, test PF fell |
| stop 2–4×ATR, trail delays, no trail | 259 | — | — | — | all worse or flat |
| volatility bands | 100–145 | — | — | — | no consistent gain |
| M5 / H1 / H4 timeframes | — | — | — | — | M5 loses; H1/H4 too few trades |

**The wins come from deciding *when* to trade, not from tuning the stop or the trail.** That fits the
fact that every baseline exit is the trail: the exit machinery is already near its optimum, the entry
selection was not.

A third round (`research/experiments3.py`) tested the two remaining exit/entry mechanics, and both
confirmed the same lesson:

| idea | trades | net | PF | DD% | folds improved | verdict |
|---|---|---|---|---|---|---|
| shipped (EMA200, trail) | 139 | +13,577 | 2.290 | 6.6 | — | — |
| partial 1×ATR, half off | 139 | +7,404 | 2.038 | 5.2 | 2/6 | rejected |
| partial 2×ATR, half off | 139 | +9,169 | 1.941 | 4.7 | 1/6 | rejected |
| partial 2×ATR, third off | 139 | +10,992 | 2.108 | 5.3 | 2/6 | rejected |
| partial 2×ATR + breakeven | 139 | +7,570 | 1.820 | 4.5 | 2/6 | rejected |
| re-entry ×1 | 231 | +14,765 | 1.889 | 10.6 | 2/6 | rejected |
| re-entry ×2 / ×3 | 267 / 282 | +14,352 / +12,836 | 1.759 / 1.664 | 11.7 | 0/6 | rejected |

**Partial profit taking** lifts the win rate from 38% to 49%, which *feels* better, and does cut
drawdown — but it costs far more profit than it saves risk. Normalising for risk kills the idea
outright: to reach a 10% tail drawdown the shipped config can run 0.59× risk for a net of ~8,000,
while partial-1×ATR can run 0.75× risk for ~5,500. Worse even on the drawdown-constrained measure
that was its whole justification.

**Re-entry** makes more gross profit (+14,765) purely by taking 231 trades instead of 139, but
profit factor falls to 1.889 and drawdown rises to 10.6% (bootstrap tail 20.9%). It is leverage
disguised as an edge.

Both are implemented and switchable (`partial_at_atr`, `max_reentries` in `TradeParams`) — they are
off because they lost, not because they are unfinished.

**Adopted: the EMA200 trend regime filter. The H1 agreement filter is now off by default.**

The EMA200 gate survives the 2.8-year re-test and is the one entry filter the longer sample
supports. It removes the counter-trend half of the signals, which on the short sample came out at
PF 0.95 — no edge at all. That falsification test is why it stays on.

H1 engine agreement (`InpUseHtfFilter`) was the best risk-adjusted filter on the 7.5-month sample
(PF 3.035, DD 4.7%) and is now **false**. On the full 2.8 years it cuts the book to 62 trades with
only 5 of 8 folds profitable, and it costs more than it adds. The code is unchanged and it remains
switchable, but the evidence behind it did not survive the longer test — the cleanest example in the
project of a filter that validates on one trending window and fails on a wider one.

* profit factor **1.63 → 2.29**, drawdown **9.3% → 6.6%**, net profit up (+11,655 → +13,577) on
  **half the trades** (259 → 139)
* improved **5 of 6** walk-forward folds
* it has a falsification test behind it: taking only the *counter*-trend signals gives PF 0.95 —
  no edge at all. The filter works because it removes trades that never had an edge, not because
  a parameter was tuned.
* and it was already sitting in the script: `ema50`/`ema200` are computed at lines 396-397 purely to
  decorate labels with "+" at line 1582, and never used as a filter.

**Rejected: the London session filter**, and it is worth saying why. On the 70/30 split it looked
excellent (test PF 2.542). Walk-forward improved only **2 of 6** folds, with fold PFs of
1.10, 2.12, 0.65, 1.06, 4.29, 0.94 — one fold carrying the whole result. That is exactly the trap of
testing many ideas against one slice.

### The drawdown to plan for is roughly double the backtest's

Bootstrapping the trade sequence 5,000 times (1% risk):

| config | observed DD | median DD | **95th-percentile DD** | P(losing overall) |
|---|---|---|---|---|
| baseline | 9.3% | 14.0% | **23.9%** | 0.8% |
| + EMA200 | 6.6% | 9.8% | **16.7%** | 0.1% |
| + EMA200 + H1 | 4.7% | 7.3% | **13.0%** | 0.1% |

**The single observed drawdown was on the lucky side of the distribution.** Plan against the 95th
percentile, not the backtest. This directly changes the risk advice below.

### Four findings that matter

1. **The raw signal has a real edge on gold M15.** With no filter, no target and no exit rule other
   than stops, it returns +117% at PF 1.275 after paying the real spread.
2. **Your chop filter works.** It is the single best improvement to signal quality: Strong takes PF
   from 1.275 to 1.656 while cutting the trade count from 559 to 259 and drawdown from 17.9% to
   9.2%. Its measured behaviour matches your own tooltips — Medium keeps ~72% of signals, Strong has
   the highest win rate and fewest signals.
3. **The Take-Profit Warning is a bad mechanical exit.** Turning it on cuts average win from 339 to
   179 and roughly halves net profit. It fires on reversal *hints* long before the trend is done. As
   a psychological prompt on a chart it is fine; as an order it clips winners. It ships **off**.
4. **Do not trade this on M5.** Every M5 configuration loses money (PF 0.90–0.98). At 0.44 USD the
   spread is too big a share of a 5-minute move, and M5 pays it three times as often.

### One honest caveat: entry timing matters a lot

Delaying every entry by one extra bar takes the recommended config from PF 1.656 to 1.238, and the
Pine defaults from 1.190 to 1.021. Results decay gradually and stay above break-even, so there is no
lookahead bug — but the edge is concentrated in the bar right after the flip. Slow fills, a wider
spread than 0.44, or a missed bar will cost more than the table above suggests.

### Also worth knowing

* 7.5 months of one instrument is a **small sample**. 259 trades at PF 1.66 is encouraging, not
  proven. Gold in this window ran from ~3,940 to ~5,600, a strong trending regime that flatters a
  trend-following system.
* **The Strategy Tester now reproduces the Python backtester.** Full-period run, XAUUSD M15,
  2024-01-15 → 2026-09-01, 1-minute-OHLC model, 61,675 bars, 3.69M ticks, 99% history quality,
  **zero order rejects**:

  | metric | Python backtester | MT5 Strategy Tester |
  |---|---|---|
  | trades | 133 | **133** |
  | long / short | 77 / 56 | 76 / 57 |
  | net profit | +3,790 | +4,316 |
  | profit factor | 1.835 | 1.93 |
  | win rate | 31.6% | 32.3% |
  | max drawdown | 5.98% | 4.73% (balance) |
  | largest loss | −69.02 | −70.99 |

  Identical trade count and near-identical bar count; the long/short split differs by one trade at
  a window boundary. The tester comes out ~14% *better*, because the Python model charges a flat
  spread while the tester replays the real variable spread and fills stops against M1 data. So the
  Python backtester is a faithful and slightly **conservative** proxy — its numbers are not
  optimistic relative to simulated execution.

  Two limits on what this proves. It ran on **MetaQuotes-Demo data and costs** (spread ~0.08,
  no commission), which are far tighter than FTMO's — it validates the execution code and the
  backtester, *not* FTMO-realistic profitability. And 1-minute OHLC is not real-tick modelling.

---

## Working around the dead account

`541403263` on `FTMO-Server4` is rejected with *"Invalid account"* (most likely an expired
challenge). That closes every normal route:

| route | result |
|---|---|
| `mt5.initialize()` Python bridge | `(-6, 'Terminal: Authorization failed')` |
| Strategy Tester via `/config:` | **works** — the earlier failure was a UTF-8 BOM in the generated `.ini`, not the account |
| MT5's built-in MCP server on `127.0.0.1:22346` | HTTP 401, needs a token |
| `[StartUp]` script in the start config | silently ignored by build 6230 |

Two routes do work, and both are scripted:

**1. Bars — read the cache directly.** `research/hcc_reader.py` parses
`bases/FTMO-Server4/history/XAUUSD/2026.hcc`. The records are plain `MqlRates` (60 bytes:
`<q4dqiq`); they are located by validating every offset and keeping only those inside a run of
consecutive 60-second-apart bars, which skips the block headers and the per-block summary bar
without having to decode them. Recovered **218,426 M1 bars**, then resampled to M15/M5.

```powershell
cd research; python hcc_reader.py        # -> data/XAUUSD_{M1,M5,M15}.npy
```

**2. Engine output — dump it from an indicator.** An indicator recalculates over cached history the
moment a chart opens, with no ticks and no login. MT5 chart files (`.chr`) are UTF-16 text, so
`tools/run_dump.ps1` writes a chart with the indicator already attached (dump inputs set), launches
the terminal, lets the indicator write the CSV and close the terminal, then restores the profile
untouched.

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_dump.ps1 -Symbol XAUUSD -Period M15
cd research; python parity_check.py
```

`tools/run_tester.ps1` works — it needed a BOM-free `.ini`, not a particular account. See the
*Strategy Tester* section above for the verified run.

### If you log in to any working account

A valid login (your FTMO account, or MT5 → File → Open an Account → MetaQuotes-Demo) would add:
deeper history than 2026, the Strategy Tester with real-tick fills, and `python mt5_data.py` for
live symbol specs. Nothing in the code needs to change.

### Note on the MT5 update

The terminal was on build 6109 with a staged update that pre-empted every scripted launch. It is now
on **6230** and the spent installer sits in
`…\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\liveupdate.applied-6230` (with an empty
`liveupdate.pending-6230` beside it). Both are safe to delete; MT5 recreates `liveupdate` when it
next needs one.

---

## Going live

No forward test was run - a deliberate decision to save 2-4 weeks. The safety
layer below exists to do part of that job instead: the EA checks its environment
before it trades, and stops itself *before* an account limit rather than at it.

### What protects the account

| guard | default | FTMO limit | what it does |
|---|---|---|---|
| `InpAllowLiveTrading` | **false** | - | a real account is refused unless this is deliberately set true |
| `InpMaxDailyLossPct` | 4% | 5% | halts for the rest of the server day, flattening any open position |
| `InpMaxTotalLossPct` | 8% | 10% | halts permanently, measured from the anchor balance |
| `InpMaxConsecFailures` | 5 | - | halts after N consecutive order failures - an execution problem, not a strategy one |

The buffers are deliberate. FTMO measures on **equity including floating P&L**,
and a gap can carry price past a stop, so halting exactly at the limit would
still breach it.

A halt **survives an EA restart** (terminal GlobalVariables keyed on the magic
number). Reattaching does not clear it - that is the failure mode that makes
this kind of guard useless in practice. To clear one deliberately, set
`InpResetGuards = true`, attach once, then set it back to false.

### The startup pre-flight

`Preflight()` runs in `OnInit` and **refuses to start** on anything it cannot
trust, printing a block to the Experts log.

Its decisive test is the **contract cross-check**: `OrderCalcProfit` (the
broker's own contract maths) against `stop_distance x contract_size`
(arithmetic). Two independent routes to the same number, which must agree.

This is not hypothetical. MetaQuotes-Demo reports `tick_value = 0.1` for XAUUSD
while its own calculator says a $1 move on one lot is $100 - sizing off that
figure would have opened **10x the intended position**, risking 7.5% per trade
instead of 0.75%. The Strategy Tester cannot catch it, because it is a
live-server discrepancy. `LossPerLot()` now sizes off `OrderCalcProfit` with a
contract-size fallback and takes the more conservative of the two.

Rounding the lot *down* to the broker's volume step is expected and is not a
fault - it makes the position smaller. Only over-risk fails.

**Verified live** against MetaQuotes-Demo (account 5056530776, 1:50, $10k):

```
algo trading    terminal=on expert=on account=on trading=on
symbol          XAUUSD  trade_mode=SYMBOL_TRADE_MODE_FULL  digits=2  contract=100
history         100000 bars on PERIOD_M15 (need 3000 for warm-up)
spread          40 points (0.40 USD)  limit 500
sizing          ATR 8.06 -> stop 20.14 wide; 0.02 lots; risk 40.29 vs intended 50.00
contract check  OrderCalcProfit 2014.37 vs contract maths 2014.37 (0.0% apart)
risk check      40.29 of 50.00 intended (19.4% under, one lot step = 20.14) - OK
margin          171.66 for 0.02 lots (1.7% of equity), leverage 1:50
news calendar   65 events in +/-24h
---------------- preflight OK ----------------
```

Two things that only show up live: the `tick_value` warning fires (the bug is
real on this server), and the **news calendar works** - it returns -1 in the
Strategy Tester, so the blackout is inert there but live it has data.

### Lot granularity on a $10k account

At the ATR above, one volume step (0.01 lots) is worth ~20 USD, i.e. 0.2% of a
$10k account. With 0.5% intended risk the position is 2-3 steps, so **effective
risk per trade lands between roughly 0.4% and 0.6%**, never exactly 0.5%. It
rounds down, so it errs small. On a larger account the granularity disappears.

### Deployment

```powershell
powershell -ExecutionPolicy Bypass -File tools\deploy.ps1
powershell -ExecutionPolicy Bypass -File tools\compile.ps1        # must be 0 errors, 0 warnings
powershell -ExecutionPolicy Bypass -File tools\run_live_check.ps1 -Symbol XAUUSD -Period M15
```

`run_live_check.ps1` attaches the EA to a live chart for ~90 seconds, captures
the pre-flight output, and restores the chart profile exactly as it was. Run it
against the demo **and** the funded account before trusting either.

Then attach `GoldSignalsAI_EA` to an **XAUUSD M15** chart with Algo Trading on.
Read the pre-flight block in the Experts tab before walking away - if it says
`PREFLIGHT FAILED` the EA is not trading, and the reason is the line above it.

### Before you fund the account

* **The terminal must stay running.** The EA holds state in memory and rebuilds
  it from history on attach; if the machine sleeps or the terminal closes, it
  stops trading. A VPS near the broker is the normal answer.
* **Your default profile auto-loads a third-party EA** (`MyTrader - Container`
  on XAUUSD H1) - it started alongside during the live check. Different magic
  numbers mean they will not fight over positions, but they share margin.
  Decide which you want running.
* `tools\run_tester.ps1` and `run_live_check.ps1` **force-kill the terminal**.
  Never run either with a live position open.

### What is still not known

The EA has never placed an order against a live server - only in the Strategy
Tester. What the tester validated is real (133 trades, zero rejects, results
matching the Python backtester to the trade), but simulated fills are not live
fills, and no backtest finds the next `tick_value`-class surprise.

The strategy evidence is also weaker than the headline numbers suggest: the
chosen `sensitivity 10 / ATR 10` sits on a **spike, not a plateau** (neighbours
score PF 1.02-1.29 against its 1.686), and the unbiased anchored walk-forward
came out at **+5,675 over ~2 years with 4 of 6 folds profitable** - not PF 1.69.
2024 was a losing year for this system.

Given all that, **the first live weeks are the forward test**. Watch it rather
than trusting it, and let the guards do their job if it goes wrong.
