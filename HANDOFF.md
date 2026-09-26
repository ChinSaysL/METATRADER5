# HANDOFF — GoldSignalsAI

State as of 2026-09-26. Written so the next person does not repeat work already
done or, worse, trust a number that has already been falsified.

Read this before `README.md`. The README grew by accretion and its earlier
sections describe versions that did not survive; each is marked superseded, but
this file is the short version.

---

## 1. What this is

An MT5 implementation of the signal engine in `pinescriptbot.pine` (a LuxAlgo
"Signals & Overlays" clone plus the author's own chop filter and take-profit
warning), for **XAUUSD only**.

The Pine script only *draws* signals. The signal maths is ported exactly; all
trade management is an addition and is marked as such in the code.

---

## 2. Current state — verified facts

These are validated and can be relied on.

| item | status |
|---|---|
| Pine → MQL5 port | **exact** — parity 0 differences across 17 columns over 99,999 bars |
| Execution | **validated** — 242 tester trades, zero rejects |
| Tester vs Python | agree within 2.5% on trade count |
| Compile | 0 errors, 0 warnings (MetaEditor 6230) |
| Account guards | daily 4% / total 8% / failure breaker, halt survives restart |
| Pre-flight | runs in `OnInit`, refuses to start on anything untrustworthy |
| Config drift check | `python research/shipped.py` — research vs EA, currently **OK** |

**Shipped config** (EA defaults == `research/shipped.py`, verified):

```
chart XAUUSD M15    sensitivity 10.0     ATR length 14->10
chop filter Strong  min strength 1       SL 2.5 x ATR
EMA200 trend on     HTF agreement off    reversal exit off
breakeven ratchet +1.0R                  WaveTrend entry agreement on
risk 0.5% of balance
```

Backtest, 4.25 years M15 (2022-06 → 2026-09), FTMO costs, 0.5% risk:
**192 trades (45/yr), +35.3%, PF 1.720, max DD 8.5%, 4 of 5 years profitable.**

---

## 3. The headline problem — read this before planning anything

**Annual return ~8.3% against a maximum drawdown of ~8.5%. Ratio ≈ 1.0.**

A prop challenge asks for **+10% before −10%**. At a return/drawdown ratio of 1
that is a coin flip stretched over years, and **no parameter choice changes it** —
tuning slides along that frontier, trading return for drawdown.

Measured consequence (1,897 M15 configs scored against the actual challenge
objective, `challenge_search.py`):

| risk | configs with ZERO breaches | fastest median time to +10% |
|---|---|---|
| 0.5% | 126 of 1,897 | **280 calendar days (~9 months)** |
| 1.0% | **none** | — |
| 2.0% | **none** | — |

Nothing reaches +10% inside 120 days at any risk without breaching. Raising risk
to go faster introduces breaches immediately, because return and drawdown scale
together.

**Conclusion: this strategy is a plausible slow retail system and a poor
challenge vehicle.** If the goal is passing a challenge quickly, the edge per
unit time is too small and more parameter search will not fix it.

---

## 4. What was tried and REJECTED — do not redo these

All with numbers, so they need not be re-run.

### Strategy structure

| idea | result | verdict |
|---|---|---|
| Fixed 3×ATR take profit | +2.2% vs +120% | kills the runners that pay |
| Take-Profit-Warning as exit | halves net profit, avg win 339→179 | fires on hints, not reversals |
| Time stop (exit if flat after N bars) | PF 1.44 → 1.15 | trades flat at bar 20 become the runners |
| Partial profit taking (all variants) | best 2/6 folds, worse risk-adjusted | costs more than it saves |
| Re-entry after stop-out | PF 1.89 vs 2.29, DD 10.6% vs 6.6% | leverage disguised as edge |
| Stop/trail multiple tuning | 1.0×ATR nearly doubled train, test PF fell | textbook overfit |
| Per-regime configs (volatility) | −1,557 vs +6,337 single-config | splitting halves the data per config |
| Per-regime configs (trend) | −4,451 vs +6,337 | same |
| Portfolio of 11 sub-strategies | ann/DD 0.98 → 0.21 | see below |
| London-session filter | 2/6 walk-forward folds | 70/30 result was one lucky fold |
| Longs only | — | bull-market artifact |
| M5 timeframe | PF 0.89–0.98 | spread too large a share of the move |

### The portfolio result is the most informative rejection

Sub-strategies are genuinely **decorrelated** (mean pairwise ρ = 0.114,
theoretical DD multiple 0.441 at K=11) — so diversification *should* work.
It doesn't, because **only 2 of 11 configurations are profitable at all**
(sens 10/atr 10 at ann/DD 0.98, sens 6/atr 10 at 0.70). Averaging one winner
with nine losers destroys return: ann 8.29% → 0.28%.

That is strong evidence the M15 edge lives in **one cell of the parameter grid,
not in the mechanism**. A robust strategy has many profitable expressions.

**Caveat / immediate next experiment:** my greedy selection picked members by
*lowest correlation*, not by quality, so it deliberately loaded losers. A
portfolio of only the two profitable ones (10/10 + 6/10) was never tested. Rough
estimate puts it near ann/DD ≈ 1.0 — worth 20 minutes, not more.

### What DID work (kept)

| change | effect |
|---|---|
| **Breakeven ratchet at +1R** | PF 1.406 → 1.439, DD 19.0% → 14.8% |
| **WaveTrend entry agreement** | PF 1.406 → 1.580, DD → 13.8% |
| Both together, 11-yr H1 walk-forward | **−1,679 → +4,691**, 4/8 → 6/8 folds |
| EMA200 trend regime filter | PF 1.63 → 2.29 (on the older M15 config) |
| Chop filter (the author's own) | helps at any strength; Strong is least aggressive |

Both wins came from **measuring trade anatomy**, not from search. See §6.

---

## 5. Bugs found — two are live-environment traps

1. **`tick_value` is wrong on MetaQuotes-Demo.** Reports 0.1 for XAUUSD while
   its own `OrderCalcProfit` says a $1 move on 1 lot is $100. Sizing off it
   would have opened **10× the intended position**. `LossPerLot()` now uses
   `OrderCalcProfit` with a contract-size fallback, and the pre-flight
   cross-checks the two. No backtest could have caught this.
2. **The Strategy Tester persists inputs per EA between runs.** Anything not
   listed in `[TesterInputs]` silently reuses the last saved value, not the
   compiled default — an H1 run executed the previous M15 config while
   reporting success. `run_tester.ps1` now pins the whole config from
   `shipped.py`.
3. **`[TesterInputs]` parses enums numerically.** `InpHtfPeriod=PERIOD_H4` is
   not rejected; it resolves to 0 (`PERIOD_CURRENT`).
4. **`.ini` must be UTF-8 without BOM.** PowerShell 5.1's `Out-File -Encoding
   utf8` prepends `EF BB BF`, so MT5 reads `﻿[Tester]`, ignores the whole
   config and silently starts the last profile. (Found by DeepSeek.)
5. MAE/MFE excursions had inverted signs for shorts — diagnostics only, no P&L
   impact, but it produced 52 phantom FTMO breaches before it was fixed.

Bugs 2–4 share a failure mode: **a run that looks successful while testing
something other than what you think.**

---

## 6. The method that actually produced improvements

Parameter search produced nothing durable. Both real gains came from
`trade_anatomy.py`, which walks every trade bar by bar in R units:

- 71% of trades that reached +0.5R, and 49% of those reaching +1.0R, still
  ended as **full losses** — 59% of the average peak handed back. Cause: the
  trail follows the adaptive line, which sits ~factor×ATR away, so a fresh
  trade is **unprotected for its first bars**. → breakeven ratchet.
- Only 46% of trades were in profit at bar 0 — entries chase extended moves.
  → WaveTrend entry agreement (machinery already ported, previously wired only
  to the harmful exit).

**If you want to improve this further, measure first.** Grid search on 4.25
years of one instrument will keep producing winners that evaporate.

---

## 7. Data available

MetaQuotes-Demo caps at ~100k bars **per timeframe**:

| timeframe | bars | span | file |
|---|---|---|---|
| M15 | 99,999 | 4.25 yr (2022-06→) | `research/data/MQFULL_M15.npy` |
| H1 | 65,000 | 11.0 yr (2015-09→) | `research/data/DEEP_H1.npy` |
| H4 | 34,152 | 22.3 yr (2004-06→) | `research/data/DEEP_H4.npy` |

`hcc_reader.py` also parses MT5's own `.hcc` cache directly (no login needed) —
vectorised, ~2s for a year.

The H1 config (sens 6/atr 14/HTF H4) validated over **11 years**: PF 1.392, DD
12.7%, 8/12 years profitable, walk-forward +4,691 (6/8 folds). It is slower
(22 trades/yr → ~904 days to +10%) but rests on far more history than the M15
config now shipped. **That trade-off is unresolved** — see §9.

---

## 8. How to run things

```powershell
powershell -ExecutionPolicy Bypass -File tools\deploy.ps1
powershell -ExecutionPolicy Bypass -File tools\compile.ps1     # must be 0/0
powershell -ExecutionPolicy Bypass -File tools\run_live_check.ps1   # live pre-flight, ~90s
powershell -ExecutionPolicy Bypass -File tools\run_tester.ps1 -Mode Backtest -Period M15
```

```powershell
cd research
python shipped.py                 # drift check: research vs EA. Run after ANY config change.
python hcc_reader.py              # bars from MT5's cache, no login
python trade_anatomy.py           # where money is made/lost inside a trade
python optimize.py --jobs 14      # parallel walk-forward search
python challenge_search.py        # scores configs by "pass a challenge", not PF
python portfolio.py               # sub-strategy correlation and portfolio effect
python ftmo_challenge.py          # pass rate from many start dates
python parity_check.py            # MQL5 vs Python, after run_dump.ps1
```

`shipped.py` is the single source of truth for the config and is checked against
the EA source. Change parameters there **and** in the EA, then run
`python shipped.py` to confirm they agree.

---

## 9. Open questions / what I would do next

In order of expected value:

1. **Decide M15 vs H1.** M15 has better metrics (PF 1.72 vs 1.39, DD 8.5% vs
   12.7%, 2× the trades) on 4.25 years; H1 has a positive 11-year walk-forward.
   M15's walk-forward is negative (−3,983, 3/8) — though partly because my
   selection rule picks by "most past profit", which is high variance. **Retest
   M15 walk-forward with a consistency-based selection rule** before concluding.
2. **Two-member portfolio** (sens 10/atr 10 + sens 6/atr 10) — the only two
   profitable sub-strategies. Untested. Cheap.
3. **Accept the timeline or change the goal.** At 0.5% the honest expectation is
   ~9 months to +10% with zero observed breaches. If that is unacceptable, this
   strategy is the wrong vehicle and the platform should be pointed at a
   different idea.
4. **Forward test.** The EA has never placed an order against a live server —
   only in the tester. The demo account (5056530776, MetaQuotes-Demo, $10k,
   1:50) is set up; attaching it costs nothing and runs in parallel with any
   decision.

### Not worth doing

- More parameter search on 4.25 years of M15 — 1,897 configs already scored
  against the real objective.
- Per-regime configs — tested twice, both negative.
- Deeper M15 history from MetaQuotes — capped at 100k bars. Would need another
  data source.

---

## 10. Honest summary

The **infrastructure is solid and reusable**: an exact, parity-proven Pine→MQL5
port, validated execution, working account guards, a pre-flight that already
caught a 10× sizing bug, and a research harness (parallel optimiser,
walk-forward, bootstrap, regime tester, trade anatomy, challenge scorer) that
catches its own config drift.

The **strategy is marginal**. Each time the sample grew the edge shrank
(7.5 months PF 1.66 → 2.8 years 1.69 → 4.25 years 1.41 → 11 years H1 1.39), and
its return/drawdown ratio of ~1.0 makes a 10%-target/10%-limit challenge a slow
coin flip. Two structural fixes materially improved it and are real. It is not
ready to be funded on the current evidence.

Anyone continuing should treat §3 as the constraint to beat, and §6 as the
method that actually beats things.
