"""Where inside a trade is money actually made and lost?

Tuning knobs assumes the structure is right. This asks whether it is, by walking
every trade bar by bar and recording its P&L in R (multiples of the initial risk)
at each bar since entry. That produces the things you cannot see from a profit
factor:

  * the edge curve - average R by bar since entry. If it peaks early and decays,
    the exit is wrong, not the entry.
  * give-back - how much of the best unrealised profit the trail hands back.
  * long vs short, measured separately, because gold has an upward drift and a
    symmetric system may be paying for one side.
  * how often a trade that was clearly winning ends up a loser.

The output is meant to point at a structural change, not a parameter.
"""

from __future__ import annotations

import argparse

import numpy as np

import backtest
import dataset
import engine
import shipped

MAX_BARS = 60


def anatomy(o, h, l, c, t, trades, atr):
    """Per-trade R path, MFE/MAE in R, and bars to peak."""
    rows = []
    for tr in trades:
        a = atr[tr.entry_bar - 1] if tr.entry_bar >= 1 else atr[tr.entry_bar]
        if not np.isfinite(a) or a <= 0:
            continue
        risk = shipped.SL_ATR_MULT * a            # 1R in price units
        d = tr.direction
        path = []
        peak = -9e9
        peak_bar = 0
        for k, i in enumerate(range(tr.entry_bar, min(tr.exit_bar + 1, len(c)))):
            if k > MAX_BARS:
                break
            r = (c[i] - tr.entry_price) * d / risk
            hi = (h[i] - tr.entry_price) * d / risk
            lo = (l[i] - tr.entry_price) * d / risk
            fav = hi if d > 0 else -lo
            path.append(r)
            if fav > peak:
                peak, peak_bar = fav, k
        if not path:
            continue
        final = (tr.exit_price - tr.entry_price) * d / risk
        rows.append(dict(dir=d, path=path, final=final, peak=peak,
                         peak_bar=peak_bar, bars=len(path) - 1,
                         profit=tr.profit))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=shipped.SYMBOL)
    ap.add_argument("--timeframe", default=shipped.TIMEFRAME)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, args.timeframe)
    print(f"data  : {args.symbol} {args.timeframe}  {dataset.describe(t)}")
    print(f"config: {shipped.describe()}\n")

    core = engine.run_core(o, h, l, c, shipped.SENSITIVITY, shipped.ATR_LENGTH, 0)
    eng = engine.apply_signals(core, shipped.engine_params())
    trades, st, _ = backtest.run(o, h, l, c, t, eng=eng,
                                 trade=shipped.trade_params(risk_pct=0.75),
                                 costs=shipped.costs())
    rows = anatomy(o, h, l, c, t, trades, core["atr"])
    print(f"{len(rows)} trades analysed   net {st['net_profit']:,.0f}  "
          f"PF {st['profit_factor']:.3f}\n")

    #--- 1. long vs short ----------------------------------------------------
    print("=" * 78)
    print("1. DIRECTION - gold drifts up; is the system paying for symmetry?")
    print("=" * 78)
    print(f"{'side':<8} {'trades':>7} {'net':>10} {'PF':>7} {'win%':>7} "
          f"{'avg R':>8} {'medianR':>8}")
    print("-" * 78)
    for lbl, sel in (("long", 1), ("short", -1), ("both", 0)):
        sub = [r for r in rows if sel == 0 or r["dir"] == sel]
        if not sub:
            continue
        p = np.array([r["profit"] for r in sub])
        fr = np.array([r["final"] for r in sub])
        w = p[p > 0].sum()
        ls = -p[p <= 0].sum()
        pf = w / ls if ls > 0 else float("inf")
        print(f"{lbl:<8} {len(sub):>7d} {p.sum():>10,.0f} {pf:>7.3f} "
              f"{(p > 0).mean() * 100:>6.1f}% {fr.mean():>8.3f} {np.median(fr):>8.3f}")

    #--- 2. the edge curve ---------------------------------------------------
    print("\n" + "=" * 78)
    print("2. EDGE CURVE - average open P&L in R by bar since entry")
    print("=" * 78)
    print("   (if this peaks early then decays, the exit is the problem)")
    print(f"\n{'bar':>4} {'alive':>7} {'mean R':>9} {'median R':>9} {'% in profit':>12}")
    print("-" * 46)
    for k in range(0, min(MAX_BARS, 40) + 1):
        vals = [r["path"][k] for r in rows if len(r["path"]) > k]
        if len(vals) < 8:
            break
        v = np.array(vals)
        if k <= 12 or k % 4 == 0:
            print(f"{k:>4d} {len(v):>7d} {v.mean():>9.3f} {np.median(v):>9.3f} "
                  f"{(v > 0).mean() * 100:>11.1f}%")

    #--- 3. give-back --------------------------------------------------------
    print("\n" + "=" * 78)
    print("3. GIVE-BACK - best unrealised profit vs what was actually kept")
    print("=" * 78)
    peaks = np.array([r["peak"] for r in rows])
    finals = np.array([r["final"] for r in rows])
    pbar = np.array([r["peak_bar"] for r in rows])
    held = np.array([r["bars"] for r in rows])
    print(f"  mean peak favourable   {peaks.mean():>7.3f} R")
    print(f"  mean final             {finals.mean():>7.3f} R")
    gave = peaks - finals
    print(f"  mean given back        {gave.mean():>7.3f} R "
          f"({gave.mean() / max(peaks.mean(), 1e-9) * 100:.0f}% of the peak)")
    print(f"  median bars to peak    {np.median(pbar):>7.0f} of {np.median(held):.0f} held")

    for thr in (0.5, 1.0, 2.0, 3.0):
        m = peaks >= thr
        if m.sum() < 5:
            continue
        ended_neg = (finals[m] <= 0).mean() * 100
        print(f"  trades that reached +{thr:.1f}R: {m.sum():>4d}  "
              f"kept {finals[m].mean():>6.3f} R on average, "
              f"{ended_neg:>5.1f}% ended at a LOSS")

    #--- 4. holding time -----------------------------------------------------
    print("\n" + "=" * 78)
    print("4. HOLDING TIME - is longer better, or just more exposure?")
    print("=" * 78)
    print(f"{'bars held':<14} {'trades':>7} {'mean R':>9} {'total R':>9}")
    print("-" * 44)
    for lo, hi in ((0, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 10_000)):
        m = (held >= lo) & (held <= hi)
        if m.sum() == 0:
            continue
        label = f"{lo}-{hi}" if hi < 9999 else f"{lo}+"
        print(f"{label:<14} {m.sum():>7d} "
              f"{finals[m].mean():>9.3f} {finals[m].sum():>9.1f}")


if __name__ == "__main__":
    main()
