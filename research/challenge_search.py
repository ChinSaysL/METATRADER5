"""Search M15 configurations against the objective that actually matters.

Profit factor is the wrong target for a funded challenge. PF says nothing about
how long you wait, and the H1 config that scored PF 1.39 needs a median of ~904
calendar days to make +10% because it only trades ~22 times a year.

So this scores every configuration by what the challenge actually asks:

    maximise P(reach +10% before breaching), and minimise the calendar time
    it takes, under the real FTMO rules (-5% daily, -10% total, on equity
    including floating loss).

Each config is backtested once over the whole series, then challenges are
started from ~200 different dates and walked forward through that trade list.
That makes the evaluation cheap enough to run over a full grid.

A config is only reported if it breaches on none of the starts - speed bought
with breach risk is not speed.
"""

from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp

import numpy as np

import backtest
import dataset
import engine
import filters
import reversal_exit
import shipped

SENS = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0]
ATRL = [7, 10, 14]
FILTERS = [(False, "Light"), (True, "Light"), (True, "Strong")]
MINSTR = [1, 2]
SLS = [1.5, 2.0, 2.5, 3.0]
EMAS = [0, 200]
HTFS = [False, True]

PROFIT_TARGET = 0.10
MAX_DAILY = 0.05
MAX_TOTAL = 0.10
CONTRACT = 100.0


def simulate_challenges(trades, initial, start_times):
    """Walk the FTMO rules from each start date. Returns (passes, fails, days)."""
    ex = np.array([t.exit_time for t in trades], dtype=np.int64)
    en = np.array([t.entry_time for t in trades], dtype=np.int64)
    pr = np.array([t.profit for t in trades], dtype=np.float64)
    mae = np.array([t.mae for t in trades], dtype=np.float64)
    lots = np.array([t.lots for t in trades], dtype=np.float64)
    floating = mae * lots * CONTRACT          # worst unrealised, in money

    passes = fails = 0
    days = []
    target = initial * (1 + PROFIT_TARGET)
    floor_t = initial * (1 - MAX_TOTAL)

    for st in start_times:
        i0 = int(np.searchsorted(en, st))
        if i0 >= len(pr) - 3:
            continue
        eq = initial
        day_start = initial
        cur_day = -1
        outcome = None
        for i in range(i0, len(pr)):
            d = ex[i] // 86400
            if d != cur_day:
                cur_day = d
                day_start = eq
            low = eq + floating[i]
            if low <= floor_t or low <= day_start - initial * MAX_DAILY:
                outcome = "fail"
                break
            eq += pr[i]
            if eq <= floor_t or eq <= day_start - initial * MAX_DAILY:
                outcome = "fail"
                break
            if eq >= target:
                outcome = "pass"
                days.append((ex[i] - st) / 86400.0)
                break
        if outcome == "pass":
            passes += 1
        elif outcome == "fail":
            fails += 1
    return passes, fails, days


def _task(spec):
    sens, atrl, symbol, tf, htf_name, risk, balance = spec
    o, h, l, c, t = dataset.from_npy(symbol, tf)
    core = engine.run_core(o, h, l, c, sens, atrl, 0)

    rx = reversal_exit.exhaustion(h, l, c, "Normal")
    wt_l, wt_s = rx["series_normal"] > 0, rx["series_normal"] < 0
    hl, hs = filters.htf_masks(t, htf_name, symbol=symbol,
                               sensitivity=sens, atr_length=atrl)

    # challenge start dates: roughly every 10 trading days across the series
    starts = t[np.linspace(500, len(t) - 2000, 200).astype(int)]
    costs = shipped.costs()

    out = []
    for (filt, fstr), minstr, sl, ema, use_htf in itertools.product(
            FILTERS, MINSTR, SLS, EMAS, HTFS):
        ep = engine.EngineParams(sensitivity=sens, atr_length=atrl,
                                 filter_fakeouts=filt, fakeout_strength=fstr,
                                 min_signal_strength=minstr)
        eng = engine.apply_signals(core, ep)
        ml = wt_l & hl if use_htf else wt_l
        ms = wt_s & hs if use_htf else wt_s
        tp = backtest.TradeParams(
            sl_atr_mult=sl, use_exhaustion_exit=False, trend_ema_length=ema,
            risk_pct=risk, initial_balance=balance,
            be_trigger_r=shipped.BE_TRIGGER_R, be_offset_r=shipped.BE_OFFSET_R,
        )
        trades, st, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                                     costs=costs, long_ok=ml, short_ok=ms)
        if len(trades) < 80:
            continue
        p, f, days = simulate_challenges(trades, balance, starts)
        if p + f < 30:
            continue
        span_years = (t[-1] - t[0]) / 31_557_600
        out.append(dict(
            sens=sens, atr=atrl, filt=("on:" + fstr) if filt else "off",
            minstr=minstr, sl=sl, ema=ema, htf=use_htf,
            trades=st["trades"], per_year=st["trades"] / span_years,
            pf=st["profit_factor"], dd=st["max_drawdown_pct"],
            passes=p, fails=f,
            pass_rate=p / (p + f) * 100.0,
            med_days=float(np.median(days)) if days else 9e9,
            p75_days=float(np.percentile(days, 75)) if days else 9e9,
        ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MQFULL")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--htf", default="H1")
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--jobs", type=int, default=14)
    ap.add_argument("--max-days", type=float, default=120.0)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, args.timeframe)
    print(f"data  : {args.symbol} {args.timeframe}  {dataset.describe(t)}")
    print(f"target: +10% before -10%, {args.risk}% risk, ${args.balance:,.0f}")
    specs = [(s, a, args.symbol, args.timeframe, args.htf, args.risk, args.balance)
             for s in SENS for a in ATRL]
    per = len(FILTERS) * len(MINSTR) * len(SLS) * len(EMAS) * len(HTFS)
    print(f"search: {len(specs)} engines x {per} configs = {len(specs) * per}, "
          f"{args.jobs} processes\n")

    rows = []
    with mp.Pool(min(args.jobs, len(specs))) as pool:
        for i, res in enumerate(pool.imap_unordered(_task, specs), 1):
            rows.extend(res)
            print(f"  {i}/{len(specs)} engines done ({len(rows)} configs)", flush=True)

    if not rows:
        print("\nnothing met the minimum trade count")
        return

    clean = [r for r in rows if r["fails"] == 0]
    print(f"\n{len(rows)} configs evaluated, {len(clean)} with ZERO breaches\n")

    def show(title, sel, n=15):
        print("=" * 112)
        print(title)
        print("=" * 112)
        hdr = (f"{'sens':>5} {'atr':>4} {'filter':>9} {'ms':>3} {'sl':>4} {'ema':>4} "
               f"{'htf':>4} | {'trades':>6} {'/yr':>5} {'PF':>6} {'DD%':>6} | "
               f"{'pass%':>6} {'fails':>6} {'med days':>9} {'p75':>7}")
        print(hdr)
        print("-" * len(hdr))
        for r in sel[:n]:
            print(f"{r['sens']:>5.1f} {r['atr']:>4d} {r['filt']:>9} {r['minstr']:>3d} "
                  f"{r['sl']:>4.1f} {r['ema']:>4d} {'Y' if r['htf'] else 'N':>4} | "
                  f"{r['trades']:>6d} {r['per_year']:>5.0f} {r['pf']:>6.3f} "
                  f"{r['dd']:>6.1f} | {r['pass_rate']:>5.0f}% {r['fails']:>6d} "
                  f"{r['med_days']:>9.0f} {r['p75_days']:>7.0f}")
        print()

    clean.sort(key=lambda r: r["med_days"])
    show("FASTEST configurations with ZERO breaches", clean)

    fast = [r for r in clean if r["med_days"] <= args.max_days]
    if fast:
        fast.sort(key=lambda r: -r["pf"])
        show(f"...of those, within {args.max_days:.0f} calendar days, best profit factor", fast)
    else:
        print(f"NOTHING passes within {args.max_days:.0f} calendar days without breaching.")

    near = [r for r in rows if r["fails"] <= 2 and r["med_days"] <= args.max_days]
    near.sort(key=lambda r: r["med_days"])
    show("Allowing up to 2 breaches (faster, but you would lose some fees)", near)


if __name__ == "__main__":
    main()
