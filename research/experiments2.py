"""Do the winning filters stack, and do they survive a harder test?

experiments.py tried ~35 variants against one 30% test slice. That is enough
searching that the slice is no longer honestly out of sample - some of those
"winners" are the best of 35 coin flips. So this does three things instead:

  1. combines the filters that won, watching the trade count as closely as the
     profit factor (a filter that leaves 15 trades has proved nothing);
  2. walk-forward: 6 sequential folds, reporting how many folds each candidate
     actually improves rather than one aggregate number;
  3. bootstrap: resamples the trade sequence to ask whether the drawdown was
     skill or luck.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np

import backtest
import dataset
import engine
import pine_math as pm

SPREAD = 0.44
COMMISSION = 3.0
BASE_EP = engine.EngineParams(filter_fakeouts=True, fakeout_strength="Strong")
BASE_TP = backtest.TradeParams(sl_atr_mult=1.5, use_exhaustion_exit=False)
LONDON = "7-15"


def load(tf="M15"):
    return dataset.from_npy("XAUUSD", tf)


def ema_masks(c, length=200):
    e = pm.ema(c, length)
    above = np.zeros(len(c), dtype=bool)
    below = np.zeros(len(c), dtype=bool)
    v = ~np.isnan(e)
    above[v] = c[v] > e[v]
    below[v] = c[v] < e[v]
    return above, below


def htf_masks(times, htf="H1"):
    """Higher-timeframe engine state, mapped forward so a bar only ever sees an
    HTF bar that has already closed."""
    ho, hh, hl, hc, ht = dataset.from_npy("XAUUSD", htf)
    hcore = engine.run_core(ho, hh, hl, hc, BASE_EP.sensitivity, BASE_EP.atr_length, 0)
    minutes = {"M30": 30, "H1": 60, "H4": 240}[htf]
    idx = np.searchsorted(ht + minutes * 60, times, side="right") - 1
    state = np.full(len(times), -1, dtype=np.int8)
    ok = idx >= 0
    state[ok] = hcore["os"][idx[ok]]
    return (state == 1), (state == 0)


def combine(*mask_pairs):
    longs = None
    shorts = None
    for a, b in mask_pairs:
        longs = a if longs is None else (longs & a)
        shorts = b if shorts is None else (shorts & b)
    return longs, shorts


def evaluate(o, h, l, c, t, tp, ml, ms, costs):
    core = engine.run_core(o, h, l, c, BASE_EP.sensitivity, BASE_EP.atr_length, 0)
    eng = engine.apply_signals(core, BASE_EP)
    return backtest.run(o, h, l, c, t, eng=eng, trade=tp, costs=costs,
                        long_ok=ml, short_ok=ms)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--boot", type=int, default=5000)
    args = ap.parse_args()

    o, h, l, c, t = load(args.timeframe)
    n = len(c)
    costs = backtest.Costs(spread_price=SPREAD, commission_per_lot=COMMISSION)
    ema_a, ema_b = ema_masks(c, 200)
    h1_a, h1_b = htf_masks(t, "H1")
    h4_a, h4_b = htf_masks(t, "H4")

    all_true = (np.ones(n, dtype=bool), np.ones(n, dtype=bool))

    CANDIDATES = [
        ("baseline (chop Strong, 1.5xATR, trail)", BASE_TP, all_true),
        ("+ EMA200 regime", BASE_TP, (ema_a, ema_b)),
        ("+ H1 engine agreement", BASE_TP, (h1_a, h1_b)),
        ("+ H4 engine agreement", BASE_TP, (h4_a, h4_b)),
        ("+ London hours only", replace(BASE_TP, allowed_hours=LONDON), all_true),
        ("+ EMA200 + H1", BASE_TP, combine((ema_a, ema_b), (h1_a, h1_b))),
        ("+ EMA200 + London", replace(BASE_TP, allowed_hours=LONDON), (ema_a, ema_b)),
        ("+ H1 + London", replace(BASE_TP, allowed_hours=LONDON), (h1_a, h1_b)),
        ("+ EMA200 + H1 + London", replace(BASE_TP, allowed_hours=LONDON),
         combine((ema_a, ema_b), (h1_a, h1_b))),
        ("+ EMA200 + H4", BASE_TP, combine((ema_a, ema_b), (h4_a, h4_b))),
    ]

    #--- 1. whole period, with trade counts front and centre ----------------
    print("=" * 104)
    print("1. DO THEY STACK?  full period, and the 70/30 split")
    print("=" * 104)
    print(f"{'candidate':<40} {'trades':>7} {'net':>9} {'PF':>7} {'DD%':>6} | "
          f"{'trainPF':>8} {'n':>4} | {'TESTPF':>7} {'n':>4} {'DD%':>6}")
    print("-" * 104)
    cut = int(n * 0.7)
    for label, tp, (ml, ms) in CANDIDATES:
        _, full, _ = evaluate(o, h, l, c, t, tp, ml, ms, costs)
        _, tr, _ = evaluate(o[:cut], h[:cut], l[:cut], c[:cut], t[:cut], tp,
                            ml[:cut], ms[:cut], costs)
        _, te, _ = evaluate(o[cut:], h[cut:], l[cut:], c[cut:], t[cut:], tp,
                            ml[cut:], ms[cut:], costs)
        print(f"{label:<40} {full.get('trades', 0):>7d} {full.get('net_profit', 0):>9,.0f} "
              f"{full.get('profit_factor', 0):>7.3f} {full.get('max_drawdown_pct', 0):>6.1f} | "
              f"{tr.get('profit_factor', 0):>8.3f} {tr.get('trades', 0):>4d} | "
              f"{te.get('profit_factor', 0):>7.3f} {te.get('trades', 0):>4d} "
              f"{te.get('max_drawdown_pct', 0):>6.1f}")

    #--- 2. walk-forward ---------------------------------------------------
    print("\n" + "=" * 104)
    print(f"2. WALK-FORWARD  {args.folds} sequential folds - how many does each candidate improve?")
    print("=" * 104)
    edges = np.linspace(0, n, args.folds + 1).astype(int)
    fold_pf = {}
    hdr = f"{'candidate':<40}"
    for i in range(args.folds):
        hdr += f"{'f' + str(i + 1):>9}"
    hdr += f"{'better':>8}{'trades':>8}"
    print(hdr)
    print("-" * len(hdr))
    for label, tp, (ml, ms) in CANDIDATES:
        pfs = []
        ntot = 0
        for i in range(args.folds):
            a, b = edges[i], edges[i + 1]
            _, st, _ = evaluate(o[a:b], h[a:b], l[a:b], c[a:b], t[a:b], tp,
                                ml[a:b], ms[a:b], costs)
            pf = st.get("profit_factor", 0.0)
            pfs.append(min(pf, 99.0) if np.isfinite(pf) else 99.0)
            ntot += st.get("trades", 0)
        fold_pf[label] = pfs
        base = fold_pf["baseline (chop Strong, 1.5xATR, trail)"]
        better = sum(1 for x, y in zip(pfs, base) if x > y)
        line = f"{label:<40}" + "".join(f"{p:>9.2f}" for p in pfs)
        line += f"{better:>5}/{args.folds}{ntot:>8d}"
        print(line)
    print("\nEach fold is ~1.2 months and only a handful of trades, so a single fold means")
    print("little. The 'better' column is the point: an idea that helps should help in most.")

    #--- 3. bootstrap the drawdown -----------------------------------------
    print("\n" + "=" * 104)
    print(f"3. BOOTSTRAP  {args.boot:,} resamples of the trade sequence")
    print("=" * 104)
    print(f"{'candidate':<40} {'trades':>7} {'median net':>11} {'5th pct':>10} "
          f"{'median DD%':>11} {'95th DD%':>10} {'P(loss)':>8}")
    print("-" * 104)
    rng = np.random.default_rng(12345)
    for label, tp, (ml, ms) in CANDIDATES:
        trades, st, _ = evaluate(o, h, l, c, t, tp, ml, ms, costs)
        if len(trades) < 20:
            print(f"{label:<40} {len(trades):>7d}   too few trades to bootstrap")
            continue
        # express each trade as a return on the balance it was sized against, so
        # reshuffling keeps the compounding honest
        profits = np.array([x.profit for x in trades])
        bal = 10_000.0
        rets = []
        for p in profits:
            rets.append(p / bal)
            bal += p
        rets = np.array(rets)

        nets = np.empty(args.boot)
        dds = np.empty(args.boot)
        for k in range(args.boot):
            seq = rng.choice(rets, size=len(rets), replace=True)
            eq = 10_000.0 * np.cumprod(1.0 + seq)
            eq = np.concatenate(([10_000.0], eq))
            peak = np.maximum.accumulate(eq)
            dds[k] = np.max((peak - eq) / peak) * 100.0
            nets[k] = eq[-1] - 10_000.0
        print(f"{label:<40} {len(trades):>7d} {np.median(nets):>11,.0f} "
              f"{np.percentile(nets, 5):>10,.0f} {np.median(dds):>11.1f} "
              f"{np.percentile(dds, 95):>10.1f} {np.mean(nets < 0) * 100:>7.1f}%")
    print("\n'95th DD%' is the drawdown to plan for, not the one the backtest happened to show.")


if __name__ == "__main__":
    main()
