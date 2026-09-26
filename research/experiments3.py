"""Partial profit taking and re-entry, judged the same way as everything else.

Both are exit/entry mechanics rather than context filters, and the earlier round
showed mechanics tend not to help this strategy (capping winners was actively
harmful). So the bar is: improve most walk-forward folds, not just the aggregate.

Partial exits are of particular interest for a prop account even if they cost a
little profit, because banking half early shortens the equity dips that a daily
loss limit measures. That trade-off is reported explicitly.
"""

from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np

import backtest
import dataset
import engine

SPREAD = 0.44
COMMISSION = 3.0
EP = engine.EngineParams(filter_fakeouts=True, fakeout_strength="Strong")
BASE = backtest.TradeParams(sl_atr_mult=1.5, use_exhaustion_exit=False,
                            trend_ema_length=200)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--boot", type=int, default=5000)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy("XAUUSD", "M15")
    n = len(c)
    costs = backtest.Costs(spread_price=SPREAD, commission_per_lot=COMMISSION)

    CONFIGS = [
        ("shipped (EMA200, trail, no partial)", BASE),
        ("partial 1xATR, half off", replace(BASE, partial_at_atr=1.0)),
        ("partial 2xATR, half off", replace(BASE, partial_at_atr=2.0)),
        ("partial 3xATR, half off", replace(BASE, partial_at_atr=3.0)),
        ("partial 2xATR, half off + breakeven",
         replace(BASE, partial_at_atr=2.0, partial_to_breakeven=True)),
        ("partial 2xATR, third off",
         replace(BASE, partial_at_atr=2.0, partial_fraction=0.33)),
        ("re-entry x1", replace(BASE, max_reentries=1)),
        ("re-entry x2", replace(BASE, max_reentries=2)),
        ("re-entry x3", replace(BASE, max_reentries=3)),
        ("re-entry x1 + partial 2xATR",
         replace(BASE, max_reentries=1, partial_at_atr=2.0)),
    ]

    def evaluate(lo, hi, tp):
        core = engine.run_core(o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi],
                               EP.sensitivity, EP.atr_length, 0)
        eng = engine.apply_signals(core, EP)
        return backtest.run(o[lo:hi], h[lo:hi], l[lo:hi], c[lo:hi], t[lo:hi],
                            eng=eng, trade=tp, costs=costs)

    #--- whole period + 70/30 -------------------------------------------------
    print("=" * 106)
    print("1. FULL PERIOD and the 70/30 SPLIT")
    print("=" * 106)
    print(f"{'config':<38} {'trades':>7} {'net':>8} {'PF':>6} {'DD%':>6} {'win%':>6} "
          f"{'avgWin':>8} | {'trainPF':>8} {'TESTPF':>7} {'TESTn':>6}")
    print("-" * 106)
    cut = int(n * 0.7)
    for label, tp in CONFIGS:
        trs, full, _ = evaluate(0, n, tp)
        _, tr, _ = evaluate(0, cut, tp)
        _, te, _ = evaluate(cut, n, tp)
        print(f"{label:<38} {full.get('trades', 0):>7d} {full.get('net_profit', 0):>8,.0f} "
              f"{full.get('profit_factor', 0):>6.3f} {full.get('max_drawdown_pct', 0):>6.1f} "
              f"{full.get('win_rate', 0):>6.1f} {full.get('avg_win', 0):>8,.0f} | "
              f"{tr.get('profit_factor', 0):>8.3f} {te.get('profit_factor', 0):>7.3f} "
              f"{te.get('trades', 0):>6d}")

    #--- walk-forward ---------------------------------------------------------
    print("\n" + "=" * 106)
    print(f"2. WALK-FORWARD  {args.folds} folds - profit factor per fold")
    print("=" * 106)
    edges = np.linspace(0, n, args.folds + 1).astype(int)
    base_pfs = None
    hdr = f"{'config':<38}" + "".join(f"{'f' + str(i + 1):>9}" for i in range(args.folds))
    hdr += f"{'better':>9}"
    print(hdr)
    print("-" * len(hdr))
    for label, tp in CONFIGS:
        pfs = []
        for i in range(args.folds):
            _, st, _ = evaluate(edges[i], edges[i + 1], tp)
            pf = st.get("profit_factor", 0.0)
            pfs.append(min(pf, 99.0) if np.isfinite(pf) else 99.0)
        if base_pfs is None:
            base_pfs = pfs
        better = sum(1 for x, y in zip(pfs, base_pfs) if x > y + 1e-9)
        print(f"{label:<38}" + "".join(f"{p:>9.2f}" for p in pfs) +
              f"{better:>6}/{args.folds}")

    #--- drawdown shape, which is what a daily loss limit actually sees --------
    print("\n" + "=" * 106)
    print("3. DRAWDOWN SHAPE  (bootstrap, 1% risk - the case for partials if any)")
    print("=" * 106)
    print(f"{'config':<38} {'trades':>7} {'median net':>11} {'medianDD%':>10} "
          f"{'95th DD%':>9} {'worst trade':>12} {'P(loss)':>8}")
    print("-" * 106)
    rng = np.random.default_rng(4242)
    for label, tp in CONFIGS:
        trs, st, _ = evaluate(0, n, tp)
        if len(trs) < 20:
            print(f"{label:<38} {len(trs):>7d}   too few trades")
            continue
        bal = 10_000.0
        rets = []
        for x in trs:
            rets.append(x.profit / bal)
            bal += x.profit
        rets = np.array(rets)
        nets = np.empty(args.boot)
        dds = np.empty(args.boot)
        for k in range(args.boot):
            eq = 10_000.0 * np.cumprod(1.0 + rng.choice(rets, size=len(rets), replace=True))
            eq = np.concatenate(([10_000.0], eq))
            peak = np.maximum.accumulate(eq)
            dds[k] = np.max((peak - eq) / peak) * 100.0
            nets[k] = eq[-1] - 10_000.0
        print(f"{label:<38} {len(trs):>7d} {np.median(nets):>11,.0f} "
              f"{np.median(dds):>10.1f} {np.percentile(dds, 95):>9.1f} "
              f"{st.get('largest_loss', 0):>12,.0f} {np.mean(nets < 0) * 100:>7.1f}%")


if __name__ == "__main__":
    main()
