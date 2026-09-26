"""Is there a regime signal that separates the winning periods from the losing ones?

Before building a regime-switching system it is worth checking that regimes
exist in a form you could actually detect at the time. Every descriptor here is
causal - computed from trailing data only - so if one separates winners from
losers it could in principle have been used live.

If nothing separates them, then the per-period variation is noise, and
regime-switching would just be fitting the labels.
"""

from __future__ import annotations

import argparse

import numpy as np

import backtest
import dataset
import engine
import pine_math as pm
import shipped


def efficiency_ratio(close, n=96):
    """Kaufman efficiency: |net move| / total path. High = trending, low = chop."""
    out = np.full(len(close), np.nan)
    absd = np.abs(np.diff(close, prepend=close[0]))
    csum = np.cumsum(absd)
    for i in range(n, len(close)):
        path = csum[i] - csum[i - n]
        if path > 0:
            out[i] = abs(close[i] - close[i - n]) / path
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=shipped.SYMBOL)
    ap.add_argument("--buckets", type=int, default=17, help="periods to split into")
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, shipped.TIMEFRAME)
    n = len(c)
    print(f"data: {args.symbol} {shipped.TIMEFRAME}  {dataset.describe(t)}")
    print(f"config: {shipped.describe()}\n")

    core = engine.run_core(o, h, l, c, shipped.SENSITIVITY, shipped.ATR_LENGTH, 0)
    eng = engine.apply_signals(core, shipped.engine_params())
    trades, st, _ = backtest.run(o, h, l, c, t, eng=eng,
                                 trade=shipped.trade_params(risk_pct=0.75),
                                 costs=shipped.costs())
    print(f"whole period: {st['trades']} trades, net {st['net_profit']:,.0f}, "
          f"PF {st['profit_factor']:.3f}\n")

    #--- causal regime descriptors -------------------------------------------
    atr = core["atr"]
    atr_pct = atr / c * 100.0                       # volatility as % of price
    atr_rank = pm.percentrank(atr, 500)             # trailing, causal
    er = efficiency_ratio(c, 96)                    # one day of M15 bars
    ema200 = pm.ema(c, 200)
    above = np.where(np.isnan(ema200), np.nan, (c > ema200).astype(float))
    chop = core["overlap_rank"]                     # the engine's own chop metric

    edges = np.linspace(0, n, args.buckets + 1).astype(int)
    ex = np.array([x.exit_time for x in trades])
    pr = np.array([x.profit for x in trades])

    print(f"{'period':<22} {'trades':>6} {'net':>8} {'PF':>6} | "
          f"{'ATR%':>6} {'ATRrank':>8} {'effic':>6} {'chop':>6} {'>EMA200':>8} {'ret%':>7}")
    print("-" * 104)

    rows = []
    for i in range(args.buckets):
        a, b = edges[i], edges[i + 1]
        lo, hi = int(t[a]), int(t[b - 1])
        m = (ex >= lo) & (ex <= hi)
        win = pr[m][pr[m] > 0].sum()
        loss = -pr[m][pr[m] <= 0].sum()
        pf = (win / loss) if loss > 0 else (99.0 if win > 0 else 0.0)
        net = float(pr[m].sum())

        import datetime as dt
        label = (dt.datetime.utcfromtimestamp(lo).strftime("%Y-%m-%d") + " " +
                 dt.datetime.utcfromtimestamp(hi).strftime("%m-%d"))
        d = dict(
            atrpct=np.nanmedian(atr_pct[a:b]),
            atrrank=np.nanmedian(atr_rank[a:b]),
            er=np.nanmedian(er[a:b]),
            chop=np.nanmedian(chop[a:b]),
            above=np.nanmean(above[a:b]) * 100.0,
            ret=(c[b - 1] / c[a] - 1.0) * 100.0,
            pf=pf, net=net, n=int(m.sum()),
        )
        rows.append(d)
        print(f"{label:<22} {d['n']:>6d} {net:>8,.0f} {pf:>6.2f} | "
              f"{d['atrpct']:>6.3f} {d['atrrank']:>8.1f} {d['er']:>6.3f} "
              f"{d['chop']:>6.1f} {d['above']:>7.1f}% {d['ret']:>6.1f}%")

    #--- does any descriptor separate winners from losers? -------------------
    print("\n" + "=" * 104)
    print("SEPARATION: descriptor averages for profitable vs losing periods")
    print("=" * 104)
    wins = [r for r in rows if r["net"] > 0]
    losses = [r for r in rows if r["net"] <= 0]
    print(f"  {len(wins)} profitable periods, {len(losses)} losing\n")
    print(f"{'descriptor':<14} {'winners':>10} {'losers':>10} {'gap':>10} "
          f"{'correlation with PF':>22}")
    print("-" * 70)
    pf_arr = np.array([r["pf"] for r in rows])
    for key, name in (("atrpct", "ATR %"), ("atrrank", "ATR rank"),
                      ("er", "efficiency"), ("chop", "chop rank"),
                      ("above", "% >EMA200"), ("ret", "gold return")):
        w = np.mean([r[key] for r in wins]) if wins else float("nan")
        lo_ = np.mean([r[key] for r in losses]) if losses else float("nan")
        vals = np.array([r[key] for r in rows])
        ok = ~np.isnan(vals) & ~np.isnan(pf_arr)
        corr = np.corrcoef(vals[ok], pf_arr[ok])[0, 1] if ok.sum() > 2 else float("nan")
        print(f"{name:<14} {w:>10.3f} {lo_:>10.3f} {w - lo_:>10.3f} {corr:>22.2f}")

    print("\nA descriptor is only useful if the gap is large AND the correlation is")
    print("strong. Weak values mean the good and bad periods are not distinguishable")
    print("in advance, and a regime switch would be fitting labels, not regimes.")


if __name__ == "__main__":
    main()
