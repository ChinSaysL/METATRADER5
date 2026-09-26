"""Baseline report and parameter sweep for the ported engine.

Two things, because they answer different questions:

  * `--baseline` runs the pine defaults and a handful of single-change variants,
    so you can see which part of the trade management is actually earning
    anything. This is the honest first look.

  * `--sweep` grids the parameters with a 70/30 train/test split. A setting that
    only wins on the training slice is a curve fit, not an edge, so both halves
    are always reported side by side and the ranking is by TEST profit factor.

The engine is the slow part (k-means every bar), so each parameter combination
that changes `sensitivity` or `atr_length` recomputes it, while combinations that
only change trade management reuse a cached engine pass.
"""

from __future__ import annotations

import argparse
import itertools
import time
from dataclasses import replace

import numpy as np

import backtest
import dataset
import engine


def _slice(arrays, lo: int, hi: int):
    return tuple(a[lo:hi] for a in arrays)


def run_one(o, h, l, c, t, eng_params, trade_params, costs, eng_cache=None):
    """Run one configuration, reusing the expensive engine core where possible.

    Only sensitivity / atr_length / max_data change the core; the filter and the
    strength floor are cheap post-processing, so the cache is keyed on the core
    inputs alone. That is what makes the grid below finish in minutes.
    """
    key = (eng_params.sensitivity, eng_params.atr_length, eng_params.max_data)
    core = None if eng_cache is None else eng_cache.get(key)
    if core is None:
        core = engine.run_core(o, h, l, c, eng_params.sensitivity,
                               eng_params.atr_length, eng_params.max_data)
        if eng_cache is not None:
            eng_cache[key] = core
    eng = engine.apply_signals(core, eng_params)
    trades, stats, equity = backtest.run(
        o, h, l, c, t, eng=eng, engine_params=eng_params, trade=trade_params, costs=costs
    )
    return trades, stats, equity, eng


def baseline(o, h, l, c, t, costs):
    print("=" * 78)
    print("BASELINE - pine defaults, then one change at a time")
    print("=" * 78)

    variants = [
        ("pine defaults, full management", {}),
        ("no chop filter change, reversal exit OFF", {"use_exhaustion_exit": False}),
        ("no adaptive trail (ATR stop only)", {"use_ts_trail": False}),
        ("no ATR stop (trail only)", {"use_atr_stop": False}),
        ("pure signal flip (no stop, no exit)",
         {"use_atr_stop": False, "use_ts_trail": False, "use_exhaustion_exit": False}),
        ("fixed 3xATR target as well", {"tp_atr_mult": 3.0}),
        ("entry delayed 1 extra bar (lookahead probe)", {"entry_delay_bars": 1}),
    ]
    eng_cache: dict = {}
    ep = engine.EngineParams()
    for label, overrides in variants:
        tp = replace(backtest.TradeParams(), **overrides)
        _, stats, _, _ = run_one(o, h, l, c, t, ep, tp, costs, eng_cache)
        print(f"\n--- {label}")
        print(backtest.format_stats(stats))

    print("\n" + "=" * 78)
    print("CHOP / FAKEOUT FILTER - the author's own false-positive cure")
    print("=" * 78)
    for label, eo in [
        ("filter OFF (pine default)", {}),
        ("filter ON, Light", {"filter_fakeouts": True, "fakeout_strength": "Light"}),
        ("filter ON, Medium", {"filter_fakeouts": True, "fakeout_strength": "Medium"}),
        ("filter ON, Strong", {"filter_fakeouts": True, "fakeout_strength": "Strong"}),
        ("min signal strength 2", {"min_signal_strength": 2}),
        ("min signal strength 3", {"min_signal_strength": 3}),
    ]:
        ep2 = replace(engine.EngineParams(), **eo)
        _, stats, _, eng = run_one(o, h, l, c, t, ep2, backtest.TradeParams(), costs, eng_cache)
        kept = int(eng["buy"].sum() + eng["sell"].sum())
        raw = int(eng["raw_buy"].sum() + eng["raw_sell"].sum())
        print(f"\n--- {label}   (signals kept {kept}/{raw})")
        print(backtest.format_stats(stats))


def sweep(o, h, l, c, t, costs, quick: bool, min_test_trades: int = 40):
    n = len(c)
    split = int(n * 0.7)
    print("=" * 78)
    print(f"SWEEP - train bars 0..{split}, test bars {split}..{n} (70/30)")
    print("=" * 78)

    sensitivities = [3.0, 5.0, 8.0, 12.0] if quick else [3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0]
    atr_lengths = [10, 14] if quick else [7, 10, 14, 20]
    filters = [(False, "Medium"), (True, "Light"), (True, "Medium"), (True, "Strong")]
    sl_mults = [1.5, 2.5] if quick else [1.0, 1.5, 2.0, 3.0]
    exits = [True, False]
    strengths = [1, 2] if quick else [1, 2, 3]

    combos = list(
        itertools.product(sensitivities, atr_lengths, filters, sl_mults, exits, strengths)
    )
    print(f"{len(combos)} combinations "
          f"({len(sensitivities) * len(atr_lengths)} engine cores per slice)\n")

    train = _slice((o, h, l, c, t), 0, split)
    test = _slice((o, h, l, c, t), split, n)

    rows = []
    cache_train: dict = {}
    cache_test: dict = {}
    t0 = time.time()
    for i, (sens, alen, (filt, fstrength), sl, use_exit, minstr) in enumerate(combos, 1):
        ep = engine.EngineParams(
            sensitivity=sens, atr_length=alen,
            filter_fakeouts=filt, fakeout_strength=fstrength,
            min_signal_strength=minstr,
        )
        tp = backtest.TradeParams(sl_atr_mult=sl, use_exhaustion_exit=use_exit)
        _, s_tr, _, _ = run_one(*train, ep, tp, costs, cache_train)
        _, s_te, _, _ = run_one(*test, ep, tp, costs, cache_test)
        rows.append(
            {
                "sens": sens, "atr": alen,
                "filter": f"{'on:' + fstrength if filt else 'off'}",
                "sl": sl, "exit": "on" if use_exit else "off", "minstr": minstr,
                "tr_pf": s_tr.get("profit_factor", 0.0), "tr_np": s_tr.get("net_profit", 0.0),
                "tr_n": s_tr.get("trades", 0),
                "te_pf": s_te.get("profit_factor", 0.0), "te_np": s_te.get("net_profit", 0.0),
                "te_n": s_te.get("trades", 0),
                "te_dd": s_te.get("max_drawdown_pct", 0.0),
            }
        )
        if i % 10 == 0 or i == len(combos):
            print(f"  {i}/{len(combos)} done ({time.time() - t0:.0f}s)")

    #--- Ranking by raw test PF just surfaces small-sample flukes: a cell with 16
    #--- test trades can show PF 6 while losing money on the training half. So
    #--- require a real sample on BOTH halves and rank by the WEAKER of the two
    #--- profit factors - a config only scores well if it held up in both.
    usable = [
        r for r in rows
        if r["te_n"] >= min_test_trades and r["tr_n"] >= min_test_trades * 2
        and np.isfinite(r["te_pf"]) and np.isfinite(r["tr_pf"])
    ]
    usable.sort(key=lambda r: min(r["tr_pf"], r["te_pf"]), reverse=True)
    print(f"\n{len(usable)}/{len(rows)} combinations have at least "
          f"{min_test_trades} test trades; ranked by the weaker half's profit factor.")

    hdr = (f"\n{'sens':>5} {'atr':>4} {'filter':>11} {'sl':>4} {'exit':>5} {'str':>4} | "
           f"{'train PF':>9} {'train net':>11} {'n':>5} | "
           f"{'TEST PF':>8} {'TEST net':>11} {'n':>5} {'ddown%':>7}")
    print(hdr)
    print("-" * len(hdr))
    for r in usable[:25]:
        print(
            f"{r['sens']:>5.1f} {r['atr']:>4d} {r['filter']:>11} {r['sl']:>4.1f} "
            f"{r['exit']:>5} {r['minstr']:>4d} | "
            f"{r['tr_pf']:>9.3f} {r['tr_np']:>11,.0f} {r['tr_n']:>5d} | "
            f"{r['te_pf']:>8.3f} {r['te_np']:>11,.0f} {r['te_n']:>5d} {r['te_dd']:>7.1f}"
        )
    if not usable:
        print("  nothing with enough out-of-sample trades to rank")
        return

    both = [r for r in usable if r["tr_pf"] > 1.0 and r["te_pf"] > 1.0]
    print(f"\n{len(both)}/{len(rows)} combinations are profitable in BOTH halves.")
    print("Treat anything that only wins on train as a curve fit.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="npy", choices=["npy", "csv"])
    ap.add_argument("--csv", default=None)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--quick", action="store_true", help="smaller sweep grid")
    ap.add_argument("--spread", type=float, default=0.20, help="spread in USD/oz")
    ap.add_argument("--commission", type=float, default=3.0, help="USD per lot per side")
    ap.add_argument("--bars", type=int, default=0, help="use only the most recent N bars")
    ap.add_argument("--min-test-trades", type=int, default=40,
                    help="minimum out-of-sample trades for a combo to be ranked")
    args = ap.parse_args()

    o, h, l, c, t = dataset.load(args.source, args.symbol, args.timeframe, args.csv)
    if args.bars > 0 and len(c) > args.bars:
        o, h, l, c, t = (a[-args.bars:] for a in (o, h, l, c, t))
    print(f"data   : {args.symbol} {args.timeframe}  {dataset.describe(t)}")
    print(f"costs  : spread {args.spread} USD/oz, commission {args.commission} USD/lot/side\n")

    costs = backtest.Costs(spread_price=args.spread, commission_per_lot=args.commission)

    if not args.baseline and not args.sweep:
        args.baseline = True
    if args.baseline:
        baseline(o, h, l, c, t, costs)
    if args.sweep:
        sweep(o, h, l, c, t, costs, args.quick, args.min_test_trades)


if __name__ == "__main__":
    main()
