"""Does a portfolio of sub-strategies move the return/drawdown frontier?

The binding constraint on M15 is not profit factor. It is that annual return
(~8.3%) is roughly equal to maximum drawdown (~8.5%). A challenge asks for +10%
before -10%, so at a ratio of 1.0 it is close to a coin flip stretched over
years, and no parameter choice fixes that - tuning slides along the frontier,
trading return for drawdown.

Diversification is the one thing that moves it. K sub-strategies each risking
1/K, with pairwise correlation rho, keep the same expected return while their
combined drawdown scales roughly as sqrt((1 + (K-1)*rho) / K). At rho = 0.5 and
K = 4 that is a ~21% drawdown reduction for the same return.

Whether that is available here is an empirical question about how correlated the
configurations actually are, which is what this measures. Near-identical
parameters produce near-identical trades and buy nothing.

P&L is additive whether or not the broker nets the positions - netting only
saves spread and commission - so summing per-config P&L is a fair, slightly
conservative model of the portfolio.
"""

from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp

import numpy as np

import backtest
import dataset
import engine
import reversal_exit
import shipped

#--- candidates chosen to be structurally different, not adjacent:
#--- sensitivity sets the SuperTrend factor band (signal horizon), ATR length
#--- sets how it measures volatility. Those two drive when a flip happens.
CANDIDATES = [
    dict(sens=5.0, atr=7), dict(sens=5.0, atr=14),
    dict(sens=7.0, atr=10), dict(sens=8.0, atr=7),
    dict(sens=10.0, atr=10), dict(sens=10.0, atr=14),
    dict(sens=12.0, atr=7), dict(sens=12.0, atr=14),
    dict(sens=16.0, atr=10), dict(sens=6.0, atr=10),
    dict(sens=14.0, atr=10), dict(sens=4.0, atr=10),
]
FILTER = (True, "Strong")
SL = 2.5
EMA = 200
MINSTR = 1


def _task(spec):
    sens, atr, symbol, tf, risk, balance = spec
    o, h, l, c, t = dataset.from_npy(symbol, tf)
    core = engine.run_core(o, h, l, c, sens, atr, 0)
    rx = reversal_exit.exhaustion(h, l, c, "Normal")
    sn = rx["series_normal"]
    filt, fstr = FILTER
    ep = engine.EngineParams(sensitivity=sens, atr_length=atr,
                             filter_fakeouts=filt, fakeout_strength=fstr,
                             min_signal_strength=MINSTR)
    eng = engine.apply_signals(core, ep)
    tp = backtest.TradeParams(sl_atr_mult=SL, use_exhaustion_exit=False,
                              trend_ema_length=EMA, risk_pct=risk,
                              initial_balance=balance,
                              be_trigger_r=shipped.BE_TRIGGER_R,
                              be_offset_r=shipped.BE_OFFSET_R)
    trades, st, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                                 costs=shipped.costs(), long_ok=sn > 0, short_ok=sn < 0)
    if len(trades) < 40:
        return None
    #--- express each trade as a return on the balance it was sized against, so
    #--- weighting later is meaningful
    bal = balance
    rets = []
    for x in trades:
        rets.append((int(x.exit_time), x.profit / bal))
        bal += x.profit
    return dict(sens=sens, atr=atr, trades=len(trades), pf=st["profit_factor"],
                dd=st["max_drawdown_pct"], net_pct=st["net_profit_pct"], rets=rets)


def daily_series(res, days):
    """Per-config daily return series on a common calendar."""
    idx = {d: i for i, d in enumerate(days)}
    out = np.zeros(len(days))
    for ts, r in res["rets"]:
        d = ts // 86400
        if d in idx:
            out[idx[d]] += r
    return out


def curve_stats(daily, years):
    eq = np.cumprod(1.0 + daily)
    peak = np.maximum.accumulate(eq)
    dd = float(np.max((peak - eq) / peak) * 100.0)
    total = float((eq[-1] - 1.0) * 100.0)
    ann = total / years
    return ann, dd, (ann / dd if dd > 0 else float("inf"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MQFULL")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, args.timeframe)
    years = (t[-1] - t[0]) / 31_557_600
    print(f"data : {args.symbol} {args.timeframe}  {dataset.describe(t)}  ({years:.2f} years)")

    specs = [(cd["sens"], cd["atr"], args.symbol, args.timeframe, args.risk, 10_000.0)
             for cd in CANDIDATES]
    with mp.Pool(min(args.jobs, len(specs))) as pool:
        res = [r for r in pool.map(_task, specs) if r]
    print(f"{len(res)} sub-strategies built\n")

    days = np.array(sorted({ts // 86400 for r in res for ts, _ in r["rets"]}))
    mat = np.vstack([daily_series(r, days) for r in res])

    print("=" * 96)
    print("1. INDIVIDUAL SUB-STRATEGIES  (each at full risk)")
    print("=" * 96)
    print(f"{'sens':>5} {'atr':>4} {'trades':>7} {'PF':>7} {'ann %':>7} {'DD %':>7} {'ann/DD':>7}")
    print("-" * 52)
    for i, r in enumerate(res):
        ann, dd, ratio = curve_stats(mat[i], years)
        r["ann"], r["ddc"], r["ratio"] = ann, dd, ratio
        print(f"{r['sens']:>5.1f} {r['atr']:>4d} {r['trades']:>7d} {r['pf']:>7.3f} "
              f"{ann:>7.2f} {dd:>7.2f} {ratio:>7.2f}")

    #--- correlations between the sub-strategies
    print("\n" + "=" * 96)
    print("2. CORRELATION  (this is what decides whether a portfolio helps)")
    print("=" * 96)
    corr = np.corrcoef(mat)
    off = corr[np.triu_indices(len(res), k=1)]
    print(f"  mean pairwise correlation : {off.mean():.3f}")
    print(f"  min / max                 : {off.min():.3f} / {off.max():.3f}")
    k = len(res)
    theory = np.sqrt((1 + (k - 1) * off.mean()) / k)
    print(f"  theoretical DD multiple at K={k}: {theory:.3f} "
          f"(1.0 = no benefit, lower is better)")

    #--- greedy least-correlated subset
    best_single = max(res, key=lambda r: r["ratio"])
    print(f"\n  best single sub-strategy: sens {best_single['sens']:.0f}/atr "
          f"{best_single['atr']}, ann/DD = {best_single['ratio']:.2f}")

    print("\n" + "=" * 96)
    print("3. PORTFOLIOS  (equal weight, total risk held constant at "
          f"{args.risk}% per trade)")
    print("=" * 96)
    print(f"{'K':>3} {'members':<34} {'ann %':>7} {'DD %':>7} {'ann/DD':>7} {'vs best':>9}")
    print("-" * 74)

    order = [int(np.argmax([r["ratio"] for r in res]))]
    while len(order) < len(res):
        best_add, best_score = None, 1e9
        for j in range(len(res)):
            if j in order:
                continue
            score = float(np.mean([corr[j, i] for i in order]))
            if score < best_score:
                best_add, best_score = j, score
        order.append(best_add)

    base = best_single["ratio"]
    for K in range(1, min(8, len(res)) + 1):
        sel = order[:K]
        combo = mat[sel].mean(axis=0)          # equal weight, risk/K each
        ann, dd, ratio = curve_stats(combo, years)
        members = ",".join(f"{res[i]['sens']:.0f}/{res[i]['atr']}" for i in sel)
        print(f"{K:>3} {members:<34} {ann:>7.2f} {dd:>7.2f} {ratio:>7.2f} "
              f"{(ratio / base - 1) * 100:>8.0f}%")

    print("\nann/DD is the number that matters: a challenge needs +10% before -10%,")
    print("so a ratio near 1.0 is a coin flip however the knobs are set. Only")
    print("decorrelation raises it without adding risk.")


if __name__ == "__main__":
    main()
