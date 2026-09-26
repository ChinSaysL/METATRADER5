"""Stress the candidate config before adopting it.

The top of an optimiser ranking is selection-biased by construction: search 2,340
configs and some will look perfect by luck. Three checks that a fluke fails and a
real effect survives:

  1. plateau  - do the NEIGHBOURS of the chosen config also work? A spike
                surrounded by losers is a fitting artifact; a broad plateau is an
                effect.
  2. bootstrap- what drawdown should actually be planned for, not the one this
                particular ordering happened to produce.
  3. FTMO     - pass rate under the real rules from many start dates.
"""

from __future__ import annotations

import argparse
import itertools
from dataclasses import replace

import numpy as np

import backtest
import dataset
import engine
import filters
import shipped

SYMBOL = shipped.SYMBOL

#--- the candidate that topped the 2.8-year walk-forward ranking
BEST = dict(sens=shipped.SENSITIVITY, atr=shipped.ATR_LENGTH,
            filt=shipped.FILTER_FAKEOUTS, fstr=shipped.FAKEOUT_STRENGTH,
            minstr=shipped.MIN_SIGNAL_STRENGTH, sl=shipped.SL_ATR_MULT,
            tema=shipped.TREND_EMA, htf=bool(shipped.HTF))


def build(o, h, l, c, sens, atr):
    return engine.run_core(o, h, l, c, sens, atr, 0)


def _build_task(spec):
    """Worker: build one engine core. Returns (key, core) so the parent can cache."""
    sens, atr, timeframe = spec
    o, h, l, c, _t = dataset.from_npy(SYMBOL, timeframe)
    return (sens, atr, timeframe), engine.run_core(o, h, l, c, sens, atr, 0)


def prebuild(specs, jobs: int):
    """Build every engine core the run needs, in parallel.

    The cores are the only expensive part - each backtest on top of one is ~0.1s.
    """
    import multiprocessing as mp
    specs = sorted(set(specs))
    out = {}
    if jobs > 1 and len(specs) > 1:
        with mp.Pool(min(jobs, len(specs))) as pool:
            for key, core in pool.imap_unordered(_build_task, specs):
                out[key] = core
                print(f"  built engine {key[0]:.0f}/{key[1]} {key[2]} "
                      f"({len(out)}/{len(specs)})", flush=True)
    else:
        for s in specs:
            key, core = _build_task(s)
            out[key] = core
    return out


def evaluate(o, h, l, c, t, cfg, core_cache, htf_masks_cache, risk=0.75,
             spread=0.44, balance=10_000.0):
    key = (cfg["sens"], cfg["atr"])
    if key not in core_cache:
        core_cache[key] = build(o, h, l, c, cfg["sens"], cfg["atr"])
    ep = engine.EngineParams(
        sensitivity=cfg["sens"], atr_length=cfg["atr"],
        filter_fakeouts=cfg["filt"], fakeout_strength=cfg["fstr"],
        min_signal_strength=cfg["minstr"],
    )
    eng = engine.apply_signals(core_cache[key], ep)
    tp = backtest.TradeParams(
        sl_atr_mult=cfg["sl"], use_exhaustion_exit=False,
        trend_ema_length=cfg["tema"], risk_pct=risk,
        initial_balance=balance,
    )
    ml = ms = None
    if cfg["htf"]:
        if key not in htf_masks_cache:
            htf_masks_cache[key] = filters.htf_masks(
                t, "H1", symbol=SYMBOL,
                sensitivity=cfg["sens"], atr_length=cfg["atr"])
        ml, ms = htf_masks_cache[key]
    return backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                        costs=backtest.ftmo_costs(spread), long_ok=ml, short_ok=ms)


def fold_stats(trades, t, folds=8):
    edges = np.linspace(0, len(t), folds + 1).astype(int)
    bounds = [(int(t[edges[i]]), int(t[edges[i + 1] - 1])) for i in range(folds)]
    ex = np.array([x.exit_time for x in trades])
    pr = np.array([x.profit for x in trades])
    out = []
    for lo, hi in bounds:
        m = (ex >= lo) & (ex <= hi)
        w = pr[m][pr[m] > 0].sum()
        losses = -pr[m][pr[m] <= 0].sum()
        out.append((int(m.sum()), float(pr[m].sum()),
                    float(w / losses) if losses > 0 else (99.0 if w > 0 else 0.0)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boot", type=int, default=5000)
    ap.add_argument("--jobs", type=int, default=14)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(SYMBOL, "M15")
    print(f"data: {SYMBOL} M15  {dataset.describe(t)}\n")
    cores: dict = {}
    hmasks: dict = {}

    #--- Every engine core this run needs, built up front in parallel. The
    #--- plateau sweep only varies sensitivity and atr_length across 8 distinct
    #--- pairs; everything else is cheap post-processing on top of them.
    specs = [(BEST["sens"], BEST["atr"], "M15")]
    specs += [(v, BEST["atr"], "M15") for v in (6.0, 8.0, 10.0, 12.0, 16.0)]
    specs += [(BEST["sens"], v, "M15") for v in (7, 10, 14, 20)]
    print(f"building {len(set(specs))} engine cores on {args.jobs} processes...")
    built = prebuild(specs, args.jobs)
    for (sens, atr, tf), core in built.items():
        if tf == "M15":
            cores[(sens, atr)] = core
    print()

    #--- 1) plateau check -----------------------------------------------------
    print("=" * 104)
    print("1. PLATEAU CHECK - vary one parameter at a time around the candidate")
    print("=" * 104)
    print(f"{'variation':<40} {'trades':>7} {'net':>8} {'PF':>7} {'DD%':>7} {'folds+':>7}")
    print("-" * 104)

    def show(label, cfg):
        trades, st, _ = evaluate(o, h, l, c, t, cfg, cores, hmasks)
        if not trades:
            print(f"{label:<40} {'no trades':>7}")
            return None
        fs = fold_stats(trades, t)
        good = sum(1 for n, net, pf in fs if net > 0)
        print(f"{label:<40} {st['trades']:>7d} {st['net_profit']:>8,.0f} "
              f"{st['profit_factor']:>7.3f} {st['max_drawdown_pct']:>7.1f} {good:>5d}/8")
        return st

    show("CANDIDATE", BEST)
    print()
    for v in (6.0, 8.0, 10.0, 12.0, 16.0):
        show(f"  sensitivity {v}", {**BEST, "sens": v})
    print()
    for v in (7, 10, 14, 20):
        show(f"  atr length {v}", {**BEST, "atr": v})
    print()
    for v in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        show(f"  SL {v} x ATR", {**BEST, "sl": v})
    print()
    for filt, fstr in ((False, "Light"), (True, "Light"), (True, "Medium"), (True, "Strong")):
        show(f"  chop filter {'off' if not filt else fstr}",
             {**BEST, "filt": filt, "fstr": fstr})
    print()
    for v in (1, 2, 3):
        show(f"  min signal strength {v}", {**BEST, "minstr": v})
    print()
    for v in (0, 100, 200):
        show(f"  trend EMA {v if v else 'off'}", {**BEST, "tema": v})
    show("  + H1 agreement", {**BEST, "htf": True})

    #--- 2) bootstrap ---------------------------------------------------------
    print("\n" + "=" * 104)
    print(f"2. BOOTSTRAP  ({args.boot:,} resamples, 0.75% risk)")
    print("=" * 104)
    trades, st, _ = evaluate(o, h, l, c, t, BEST, cores, hmasks)
    bal = 10_000.0
    rets = []
    for x in trades:
        rets.append(x.profit / bal)
        bal += x.profit
    rets = np.array(rets)
    rng = np.random.default_rng(7)
    nets = np.empty(args.boot)
    dds = np.empty(args.boot)
    for k in range(args.boot):
        eq = 10_000.0 * np.cumprod(1.0 + rng.choice(rets, size=len(rets), replace=True))
        eq = np.concatenate(([10_000.0], eq))
        peak = np.maximum.accumulate(eq)
        dds[k] = np.max((peak - eq) / peak) * 100.0
        nets[k] = eq[-1] - 10_000.0
    print(f"  observed net {st['net_profit']:,.0f}   observed DD {st['max_drawdown_pct']:.1f}%")
    print(f"  median net   {np.median(nets):,.0f}   5th pct net {np.percentile(nets, 5):,.0f}")
    print(f"  median DD    {np.median(dds):.1f}%   95th pct DD {np.percentile(dds, 95):.1f}%")
    print(f"  probability of an overall loss: {np.mean(nets < 0) * 100:.1f}%")

    #--- 3) per fold ----------------------------------------------------------
    print("\n  fold-by-fold (each ~4 months):")
    for i, (n, net, pf) in enumerate(fold_stats(trades, t), 1):
        print(f"    fold {i}: {n:>4d} trades  net {net:>8,.0f}  PF {pf:>5.2f}")


if __name__ == "__main__":
    main()
