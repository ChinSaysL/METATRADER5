"""Does a per-regime configuration beat one config for all regimes?

The fair test is walk-forward, because the whole risk of regime switching is
that you fit the labels. So for every fold:

  * classify each bar's regime CAUSALLY (trailing data only);
  * for each regime, pick the config that did best in that regime over the folds
    ALREADY SEEN;
  * trade the next fold routing by the live regime;
  * compare against the same procedure with a single config for all regimes.

If per-regime selection is real it should beat single-config selection here. If
it only wins when the regime configs are chosen with hindsight, it is fitting.

Known approximation, and it favours the regime version: trades are attributed to
the regime at their entry bar, and the per-regime "portfolio" simply takes each
config's trades while its regime is active. It ignores the fact that a real
system cannot be in two configs' positions at once. A negative result here is
therefore conclusive; a positive one would need a stateful re-test.
"""

from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp

import numpy as np

import backtest
import dataset
import engine
import pine_math as pm
import shipped

SENS = [6.0, 8.0, 10.0, 12.0]
ATRL = [7, 10, 14]
FILTERS = [(False, "Light"), (True, "Light")]
MINSTR = [1, 2]
SLS = [1.5, 2.5, 4.0]
EMAS = [0, 200]

N_FOLDS = 8


def regimes(o, h, l, c, kind: str):
    """Causal regime label per bar. 0/1 (or 0..3), -1 = not yet classifiable."""
    atr = pm.atr(h, l, c, 14)
    atrpct = atr / c * 100.0
    lab = np.full(len(c), -1, dtype=np.int8)

    if kind == "vol":
        # high/low volatility vs a TRAILING median - no lookahead
        med = np.full(len(c), np.nan)
        win = 2000
        for i in range(win, len(c)):
            med[i] = np.nanmedian(atrpct[i - win:i])
        ok = ~np.isnan(med) & ~np.isnan(atrpct)
        lab[ok] = (atrpct[ok] > med[ok]).astype(np.int8)
    elif kind == "trend":
        ema = pm.ema(c, 200)
        ok = ~np.isnan(ema)
        lab[ok] = (c[ok] > ema[ok]).astype(np.int8)
    elif kind == "both":
        v = regimes(o, h, l, c, "vol")
        t = regimes(o, h, l, c, "trend")
        ok = (v >= 0) & (t >= 0)
        lab[ok] = (v[ok] * 2 + t[ok]).astype(np.int8)
    return lab


def _task(spec):
    sens, atrl, symbol, tf = spec
    o, h, l, c, t = dataset.from_npy(symbol, tf)
    core = engine.run_core(o, h, l, c, sens, atrl, 0)
    out = []
    for (filt, fstr), minstr, sl, ema in itertools.product(FILTERS, MINSTR, SLS, EMAS):
        ep = engine.EngineParams(sensitivity=sens, atr_length=atrl,
                                 filter_fakeouts=filt, fakeout_strength=fstr,
                                 min_signal_strength=minstr)
        eng = engine.apply_signals(core, ep)
        tp = backtest.TradeParams(sl_atr_mult=sl, use_exhaustion_exit=False,
                                  trend_ema_length=ema, risk_pct=0.75)
        trades, _, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                                    costs=shipped.costs())
        if len(trades) < 60:
            continue
        name = f"s{sens:g}/a{atrl}/{'F' if filt else '-'}/m{minstr}/sl{sl:g}/e{ema}"
        out.append((name,
                    np.array([x.entry_bar for x in trades], dtype=np.int32),
                    np.array([x.exit_time for x in trades], dtype=np.int64),
                    np.array([x.profit for x in trades], dtype=np.float64)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=shipped.SYMBOL)
    ap.add_argument("--kind", default="vol", choices=["vol", "trend", "both"])
    ap.add_argument("--jobs", type=int, default=14)
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, shipped.TIMEFRAME)
    n = len(c)
    print(f"data   : {args.symbol}  {dataset.describe(t)}")
    lab = regimes(o, h, l, c, args.kind)
    nreg = int(lab.max()) + 1
    for r in range(nreg):
        print(f"  regime {r}: {(lab == r).sum() / n * 100:.1f}% of bars")

    specs = [(s, a, args.symbol, shipped.TIMEFRAME) for s in SENS for a in ATRL]
    print(f"\nbuilding {len(specs)} engines x "
          f"{len(FILTERS) * len(MINSTR) * len(SLS) * len(EMAS)} configs "
          f"on {args.jobs} processes...")
    results = []
    with mp.Pool(min(args.jobs, len(specs))) as pool:
        for i, res in enumerate(pool.imap_unordered(_task, specs), 1):
            results.extend(res)
            print(f"  {i}/{len(specs)} done ({len(results)} configs)", flush=True)

    print(f"\n{len(results)} configs with enough trades\n")

    edges = np.linspace(0, n, N_FOLDS + 1).astype(int)
    fold_of_time = [(int(t[edges[i]]), int(t[edges[i + 1] - 1])) for i in range(N_FOLDS)]

    # net[config][fold][regime]
    names = [r[0] for r in results]
    net = np.zeros((len(results), N_FOLDS, nreg))
    for ci, (_, ebar, etime, prof) in enumerate(results):
        reg = lab[np.clip(ebar, 0, n - 1)]
        for fi, (lo, hi) in enumerate(fold_of_time):
            m = (etime >= lo) & (etime <= hi)
            for r in range(nreg):
                net[ci, fi, r] = prof[m & (reg == r)].sum()

    print("=" * 96)
    print("WALK-FORWARD: single config for everything vs one config per regime")
    print("=" * 96)
    print(f"{'test fold':<10} {'single-config':>14} {'per-regime':>12}   chosen per regime")
    print("-" * 96)

    tot_single = tot_regime = 0.0
    wins_single = wins_regime = 0
    for fi in range(2, N_FOLDS):
        past = net[:, :fi, :]

        # single config: best total over all past folds and regimes
        best_s = int(np.argmax(past.sum(axis=(1, 2))))
        got_s = net[best_s, fi, :].sum()

        # per regime: best config within that regime over past folds
        got_r = 0.0
        picks = []
        for r in range(nreg):
            best_r = int(np.argmax(past[:, :, r].sum(axis=1)))
            got_r += net[best_r, fi, r]
            picks.append(f"r{r}:{names[best_r]}")

        tot_single += got_s
        tot_regime += got_r
        wins_single += got_s > 0
        wins_regime += got_r > 0
        print(f"{fi + 1:<10d} {got_s:>14,.0f} {got_r:>12,.0f}   {' '.join(picks)[:52]}")

    print("-" * 96)
    print(f"{'TOTAL':<10} {tot_single:>14,.0f} {tot_regime:>12,.0f}")
    print(f"{'folds +':<10} {wins_single:>13d}/6 {wins_regime:>11d}/6")
    print()
    if tot_regime > tot_single:
        print("Per-regime selection came out ahead. Given the approximation favours it,")
        print("this would need a stateful re-test before being believed.")
    else:
        print("Per-regime selection did NOT beat a single config, even with the")
        print("approximation working in its favour. The per-period variation is not")
        print("something this regime label can predict.")


if __name__ == "__main__":
    main()
