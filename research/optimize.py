"""Re-select parameters on the FULL 2.8 years, with walk-forward, in parallel.

Everything shipped so far was chosen on 7.5 months of 2026 and fell apart on the
wider sample (PF 3.04 -> 1.30, drawdown 4.7% -> 27.5%, with 2024 a losing year).
This redoes the selection honestly:

  * the engine runs ONCE per (sensitivity, atr_length) over the whole series and
    every downstream config slices those results. That is both ~100x faster and
    more realistic than restarting the recursion per fold, because live the
    engine never restarts;
  * each config is scored per FOLD (8 sequential ~4-month blocks), so a config is
    judged on consistency rather than on one aggregate that a single good quarter
    can carry;
  * an anchored walk-forward then asks the only question that matters: if you had
    picked the best config on everything up to fold i, what would it have earned
    on fold i+1? That is the honest out-of-sample number.

Run: python optimize.py --symbol MQGOLD --jobs 12
"""

from __future__ import annotations

import argparse
import itertools
import os
import time
from dataclasses import replace

import numpy as np

import backtest
import dataset
import engine
import filters
import reversal_exit

#--- search space -------------------------------------------------------------
#--- Finer resolution around sensitivity 10 and SL 2.5, because the 2.8-year run
#--- put its winner on a SPIKE there (neighbours 8 and 12 scored far worse). A
#--- spike that survives a denser grid on more data is worth more than one that
#--- does not. Dimensions that showed no sensitivity last time (filter strength,
#--- min strength 3) are trimmed to keep the run near 15 minutes.
SENSITIVITIES = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 14.0, 16.0]
ATR_LENGTHS = [7, 10, 14]
FILTERS = [(False, "Medium"), (True, "Light"), (True, "Strong")]
MIN_STRENGTHS = [1, 2]
SL_MULTS = [1.5, 2.0, 2.5, 3.0, 4.0]
TREND_EMAS = [0, 200]
HTF_FLAGS = [False, True]

N_FOLDS = 10
MIN_TRADES = 150          # over the whole 4.25 years
MIN_FOLD_TRADES = 5

#--- Structural changes found by trade_anatomy.py, applied to EVERY config:
#---   breakeven ratchet at +1R - 49% of trades that reached +1R still ended as
#---     full losses, handing back 59% of the average peak;
#---   WaveTrend agreement on entry - only 46% of trades were in profit at bar 0,
#---     i.e. many entries chase an already-extended move.
#--- Set STRUCTURAL = False to reproduce the earlier, unprotected runs.
STRUCTURAL = True

_DATA: dict = {}


def load(symbol: str, timeframe: str, htf: str):
    key = (symbol, timeframe, htf)
    if key not in _DATA:
        o, h, l, c, t = dataset.from_npy(symbol, timeframe)
        ho, hh, hl_, hc, ht = dataset.from_npy(symbol, htf)
        _DATA[key] = (o, h, l, c, t, ho, hh, hl_, hc, ht)
    return _DATA[key]


def fold_edges(n: int, folds: int):
    return np.linspace(0, n, folds + 1).astype(int)


def pf_of(profits) -> float:
    if len(profits) == 0:
        return 0.0
    p = np.asarray(profits)
    win = p[p > 0].sum()
    loss = -p[p <= 0].sum()
    if loss <= 0:
        return 99.0 if win > 0 else 0.0
    return float(win / loss)


def evaluate_core(task):
    """One (sensitivity, atr_length): build the engine once, score every config."""
    sens, alen, symbol, timeframe, htf, spread = task
    o, h, l, c, t, ho, hh, hl_, hc, ht = load(symbol, timeframe, htf)
    n = len(c)
    costs = backtest.ftmo_costs(spread)

    core = engine.run_core(o, h, l, c, sens, alen, 0)

    wt_long = wt_short = None
    if STRUCTURAL:
        rx = reversal_exit.exhaustion(h, l, c, "Normal")
        sn = rx["series_normal"]
        wt_long, wt_short = sn > 0, sn < 0

    #--- higher-timeframe engine uses the SAME signal parameters
    hcore = engine.run_core(ho, hh, hl_, hc, sens, alen, 0)
    minutes = filters.HTF_MINUTES[htf]
    idx = np.searchsorted(ht + minutes * 60, t, side="right") - 1
    state = np.full(n, -1, dtype=np.int8)
    ok = idx >= 0
    state[ok] = hcore["os"][idx[ok]]
    htf_long, htf_short = (state != 0), (state != 1)

    edges = fold_edges(n, N_FOLDS)
    bounds = [(int(t[edges[i]]), int(t[edges[i + 1] - 1])) for i in range(N_FOLDS)]

    rows = []
    for (filt, fstr), minstr, sl, tema, use_htf in itertools.product(
        FILTERS, MIN_STRENGTHS, SL_MULTS, TREND_EMAS, HTF_FLAGS
    ):
        ep = engine.EngineParams(
            sensitivity=sens, atr_length=alen,
            filter_fakeouts=filt, fakeout_strength=fstr,
            min_signal_strength=minstr,
        )
        eng = engine.apply_signals(core, ep)
        tp = backtest.TradeParams(
            sl_atr_mult=sl, use_exhaustion_exit=False,
            trend_ema_length=tema, risk_pct=0.75,
            be_trigger_r=(1.0 if STRUCTURAL else 0.0),
        )
        ml = htf_long if use_htf else None
        ms = htf_short if use_htf else None
        if STRUCTURAL:
            ml = wt_long if ml is None else (ml & wt_long)
            ms = wt_short if ms is None else (ms & wt_short)
        trades, st, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                                     costs=costs, long_ok=ml, short_ok=ms)
        if st.get("trades", 0) < MIN_TRADES:
            continue

        #--- per-fold profit factors, bucketed by trade exit time
        exits = np.array([x.exit_time for x in trades])
        profits = np.array([x.profit for x in trades])
        #--- each trade as a fraction of the balance it was sized against. Money
        #--- figures compound along each config's own path, so a config that did
        #--- well early trades bigger later; returns are comparable across folds.
        bal_before = tp.initial_balance + np.concatenate(([0.0], np.cumsum(profits)[:-1]))
        rets = profits / bal_before
        fold_pf, fold_net, fold_n, fold_ret = [], [], [], []
        for lo, hi in bounds:
            m = (exits >= lo) & (exits <= hi)
            fold_pf.append(pf_of(profits[m]))
            fold_net.append(float(profits[m].sum()))
            fold_n.append(int(m.sum()))
            fold_ret.append(float(rets[m].sum() * 100.0))

        rows.append({
            "sens": sens, "atr": alen,
            "filter": ("on:" + fstr) if filt else "off",
            "minstr": minstr, "sl": sl, "tema": tema, "htf": use_htf,
            "trades": st["trades"], "net": st["net_profit"],
            "pf": st["profit_factor"], "dd": st["max_drawdown_pct"],
            "win": st["win_rate"],
            "fold_pf": fold_pf, "fold_net": fold_net, "fold_n": fold_n,
            "fold_ret": fold_ret,
        })
    return rows


def describe(r) -> str:
    return (f"sens {r['sens']:>4.1f}  atr {r['atr']:>2d}  filt {r['filter']:>9}  "
            f"minstr {r['minstr']}  sl {r['sl']:.1f}  "
            f"ema {r['tema']:>3d}  htf {'Y' if r['htf'] else 'N'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MQGOLD")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--htf", default="H1")
    ap.add_argument("--spread", type=float, default=0.44)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, args.timeframe)
    print(f"data   : {args.symbol} {args.timeframe}  {dataset.describe(t)}")
    tasks = [(s, a, args.symbol, args.timeframe, args.htf, args.spread)
             for s in SENSITIVITIES for a in ATR_LENGTHS]
    per_core = (len(FILTERS) * len(MIN_STRENGTHS) * len(SL_MULTS)
                * len(TREND_EMAS) * len(HTF_FLAGS))
    print(f"search : {len(tasks)} engine builds x {per_core} configs "
          f"= {len(tasks) * per_core} evaluations, {args.jobs} processes")
    print(f"folds  : {N_FOLDS} sequential blocks of ~{len(c) // N_FOLDS} bars\n")

    t0 = time.time()
    rows = []
    if args.jobs > 1:
        import multiprocessing as mp
        with mp.Pool(args.jobs) as pool:
            for i, res in enumerate(pool.imap_unordered(evaluate_core, tasks), 1):
                rows.extend(res)
                print(f"  {i}/{len(tasks)} engine builds done "
                      f"({time.time() - t0:.0f}s, {len(rows)} configs kept)")
    else:
        for i, task in enumerate(tasks, 1):
            rows.extend(evaluate_core(task))
            print(f"  {i}/{len(tasks)} ({time.time() - t0:.0f}s)")

    if not rows:
        print("nothing met the minimum trade count")
        return
    print(f"\n{len(rows)} configs with >= {MIN_TRADES} trades, "
          f"{time.time() - t0:.0f}s total\n")

    #--- 1) robustness ranking: consistency across folds, not one aggregate
    for r in rows:
        pfs = np.array(r["fold_pf"])
        ns = np.array(r["fold_n"])
        valid = ns >= MIN_FOLD_TRADES
        r["folds_ok"] = int(valid.sum())
        r["folds_win"] = int((pfs[valid] > 1.0).sum()) if valid.any() else 0
        r["fold_med"] = float(np.median(pfs[valid])) if valid.any() else 0.0
        r["fold_min"] = float(pfs[valid].min()) if valid.any() else 0.0

    robust = [r for r in rows if r["folds_ok"] >= 6]
    robust.sort(key=lambda r: (r["folds_win"], r["fold_med"]), reverse=True)

    print("=" * 118)
    print("MOST CONSISTENT CONFIGS  (ranked by folds profitable, then median fold PF)")
    print("=" * 118)
    hdr = (f"{'configuration':<62} {'trades':>6} {'net':>8} {'PF':>6} {'DD%':>6} "
           f"{'winFolds':>9} {'medPF':>6} {'minPF':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in robust[:20]:
        print(f"{describe(r):<62} {r['trades']:>6d} {r['net']:>8,.0f} {r['pf']:>6.3f} "
              f"{r['dd']:>6.1f} {r['folds_win']:>4d}/{r['folds_ok']:<4d} "
              f"{r['fold_med']:>6.2f} {r['fold_min']:>6.2f}")

    #--- 2) how the current shipped config ranks
    ship = [r for r in rows if r["sens"] == 5.0 and r["atr"] == 10
            and r["filter"] == "on:Strong" and r["minstr"] == 1
            and r["sl"] == 1.5 and r["tema"] == 200 and r["htf"]]
    if ship:
        r = ship[0]
        rank = sum(1 for x in robust if (x["folds_win"], x["fold_med"]) >
                   (r["folds_win"], r["fold_med"])) + 1
        print(f"\ncurrently shipped config ranks #{rank} of {len(robust)}:")
        print(f"{describe(r):<62} {r['trades']:>6d} {r['net']:>8,.0f} {r['pf']:>6.3f} "
              f"{r['dd']:>6.1f} {r['folds_win']:>4d}/{r['folds_ok']:<4d} "
              f"{r['fold_med']:>6.2f} {r['fold_min']:>6.2f}")

    #--- 3) anchored walk-forward: pick on the past, measure on the next fold
    print("\n" + "=" * 118)
    print("ANCHORED WALK-FORWARD  (choose on folds 1..i by profit factor, measure on fold i+1)")
    print("=" * 118)
    print(f"{'test fold':<10} {'chosen configuration':<62} {'trades':>7} {'net':>9} {'PF':>7}")
    print("-" * 100)
    wf_net = 0.0
    wf_profits = []
    for i in range(2, N_FOLDS):
        best, best_score = None, -1e9
        for r in rows:
            past_net = sum(r["fold_net"][:i])
            past_n = sum(r["fold_n"][:i])
            if past_n < 40:
                continue
            score = past_net                      # simple, honest: most profit so far
            if score > best_score:
                best, best_score = r, score
        if best is None:
            continue
        net = best["fold_net"][i]
        wf_net += net
        wf_profits.append(net)
        print(f"{i + 1:<10d} {describe(best):<62} {best['fold_n'][i]:>7d} "
              f"{net:>9,.0f} {best['fold_pf'][i]:>7.2f}")
    print("-" * 100)
    print(f"{'TOTAL':<73} {'':>7} {wf_net:>9,.0f}")
    good = sum(1 for x in wf_profits if x > 0)
    print(f"\nwalk-forward folds profitable: {good}/{len(wf_profits)}")
    print("This is the number to believe: it is what re-picking parameters as you go")
    print("would actually have produced, with no hindsight.")


if __name__ == "__main__":
    main()
