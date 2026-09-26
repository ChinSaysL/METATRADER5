"""Structured attempts to improve the strategy, each judged out of sample.

Every experiment is reported on a 70/30 train/test split of the same data. With
only 259 baseline trades in 7.5 months of ONE trending gold regime, any idea that
needs a tuned parameter to work is almost certainly a curve fit, so the ideas
here are deliberately structural - things with a reason to work rather than a
number to optimise:

  1. timeframe            - M5 loses and M15 wins, so where does the edge peak?
  2. session window       - gold rotates in the Asian hours; that is where a
                            trailing-stop flip should produce its fakeouts
  3. trend regime         - the script computes ema50/ema200 for its "+" labels
                            but never filters on them (pine lines 396-397, 1582)
  4. higher-timeframe     - the MTF machinery exists in the script and is unused
     agreement              for signals
  5. direction            - does the edge live in longs, shorts, or both?
  6. stop and trail       - every exit is the trail, so this is the real lever
  7. volatility band      - skip the quietest and wildest conditions

Run: python experiments.py [--timeframe M15] [--quick]
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

_cores: dict = {}


def core_for(o, h, l, c, ep):
    key = (id(c), ep.sensitivity, ep.atr_length, ep.max_data)
    if key not in _cores:
        _cores[key] = engine.run_core(o, h, l, c, ep.sensitivity, ep.atr_length, ep.max_data)
    return _cores[key]


class Split:
    """Holds one dataset and evaluates configs on train / test / whole."""

    def __init__(self, tf: str):
        self.tf = tf
        self.o, self.h, self.l, self.c, self.t = dataset.from_npy("XAUUSD", tf)
        self.n = len(self.c)
        self.cut = int(self.n * 0.7)
        self.costs = backtest.Costs(spread_price=SPREAD, commission_per_lot=COMMISSION)

    def slice(self, lo, hi, mask_l=None, mask_s=None):
        arrs = (self.o[lo:hi], self.h[lo:hi], self.l[lo:hi], self.c[lo:hi], self.t[lo:hi])
        ml = None if mask_l is None else mask_l[lo:hi]
        ms = None if mask_s is None else mask_s[lo:hi]
        return arrs, ml, ms

    def eval(self, ep=None, tp=None, mask_l=None, mask_s=None):
        ep = ep or BASE_EP
        tp = tp or BASE_TP
        out = {}
        for name, (lo, hi) in (("train", (0, self.cut)), ("test", (self.cut, self.n))):
            (o, h, l, c, t), ml, ms = self.slice(lo, hi, mask_l, mask_s)
            core = engine.run_core(o, h, l, c, ep.sensitivity, ep.atr_length, ep.max_data)
            eng = engine.apply_signals(core, ep)
            _, st, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                                    costs=self.costs, long_ok=ml, short_ok=ms)
            out[name] = st
        return out


def row(label: str, r: dict) -> str:
    tr, te = r["train"], r["test"]
    def f(s, k, d=0.0):
        return s.get(k, d)
    return (f"{label:<42} "
            f"{f(tr,'profit_factor'):>8.3f} {f(tr,'net_profit'):>9,.0f} {f(tr,'trades'):>5.0f} | "
            f"{f(te,'profit_factor'):>8.3f} {f(te,'net_profit'):>9,.0f} {f(te,'trades'):>5.0f} "
            f"{f(te,'max_drawdown_pct'):>7.1f}")


def header(title: str) -> None:
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    print(f"{'':<42} {'trainPF':>8} {'trainNet':>9} {'n':>5} | "
          f"{'TESTPF':>8} {'TESTNet':>9} {'n':>5} {'ddown%':>7}")
    print("-" * 100)


def ema_mask(c, length):
    e = pm.ema(c, length)
    above = np.zeros(len(c), dtype=bool)
    below = np.zeros(len(c), dtype=bool)
    valid = ~np.isnan(e)
    above[valid] = c[valid] > e[valid]
    below[valid] = c[valid] < e[valid]
    return above, below


def htf_mask(sp: Split, htf: str):
    """Allow longs only when the engine on a higher timeframe is also long.

    The higher-timeframe state is mapped forward in time with searchsorted, so a
    bar only ever sees an HTF bar that had already CLOSED - no lookahead.
    """
    ho, hh, hl, hc, ht = dataset.from_npy("XAUUSD", htf)
    hcore = engine.run_core(ho, hh, hl, hc, BASE_EP.sensitivity, BASE_EP.atr_length, 0)
    hos = hcore["os"]
    # for each base bar, the last HTF bar whose close time <= this bar's time
    htf_minutes = {"M30": 30, "H1": 60, "H4": 240}[htf]
    close_times = ht + htf_minutes * 60
    idx = np.searchsorted(close_times, sp.t, side="right") - 1
    ok = idx >= 0
    state = np.full(len(sp.t), -1, dtype=np.int8)
    state[ok] = hos[idx[ok]]
    return (state == 1), (state == 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="M15")
    args = ap.parse_args()

    #--- 1. timeframe ------------------------------------------------------
    header("1. TIMEFRAME  (recommended config, unchanged, on each timeframe)")
    for tf in ("M5", "M15", "M30", "H1", "H4"):
        try:
            sp = Split(tf)
        except FileNotFoundError:
            continue
        print(row(f"{tf}  ({sp.n:,} bars)", sp.eval()))

    sp = Split(args.timeframe)
    print(f"\n(everything below is {args.timeframe}; baseline repeated for reference)")

    #--- 2. session window -------------------------------------------------
    header("2. SESSION WINDOW  (entries only; server time, FTMO is UTC+2/+3)")
    print(row("baseline: all hours", sp.eval()))
    for label, spec in [
        ("London+NY 07-21", "7-21"),
        ("London only 07-15", "7-15"),
        ("NY only 13-21", "13-21"),
        ("skip Asian 06-22", "6-22"),
        ("skip late night 02-22", "2-22"),
        ("London open 07-11", "7-11"),
        ("NY open 13-17", "13-17"),
    ]:
        print(row(label, sp.eval(tp=replace(BASE_TP, allowed_hours=spec))))

    #--- 3. trend regime ---------------------------------------------------
    header("3. TREND REGIME FILTER  (ema50/ema200 exist in the script, unused)")
    print(row("baseline: no regime filter", sp.eval()))
    for length in (50, 100, 200):
        a, b = ema_mask(sp.c, length)
        print(row(f"trade with EMA{length} (long above / short below)",
                  sp.eval(mask_l=a, mask_s=b)))
        print(row(f"trade AGAINST EMA{length} (control - should be worse)",
                  sp.eval(mask_l=b, mask_s=a)))

    #--- 4. higher-timeframe agreement -------------------------------------
    header("4. HIGHER-TIMEFRAME AGREEMENT  (same engine on a slower chart)")
    print(row("baseline: no HTF filter", sp.eval()))
    for htf in ("M30", "H1", "H4"):
        try:
            a, b = htf_mask(sp, htf)
        except (FileNotFoundError, KeyError):
            continue
        print(row(f"only with {htf} engine agreeing", sp.eval(mask_l=a, mask_s=b)))

    #--- 5. direction ------------------------------------------------------
    header("5. DIRECTION  (gold rose 3,940 -> 5,600 here, so read this carefully)")
    print(row("baseline: both directions", sp.eval()))
    print(row("longs only", sp.eval(tp=replace(BASE_TP, direction="long"))))
    print(row("shorts only", sp.eval(tp=replace(BASE_TP, direction="short"))))

    #--- 6. stop and trail -------------------------------------------------
    header("6. STOP AND TRAIL  (every baseline exit is the trail, so this is the lever)")
    print(row("baseline: 1.5xATR stop, trail immediately", sp.eval()))
    for m in (1.0, 2.0, 2.5, 3.0, 4.0):
        print(row(f"initial stop {m}xATR", sp.eval(tp=replace(BASE_TP, sl_atr_mult=m))))
    print(row("initial stop on the adaptive line",
              sp.eval(tp=replace(BASE_TP, initial_stop_mode="ts"))))
    for d in (0.5, 1.0, 2.0, 3.0):
        print(row(f"start trailing only after +{d}xATR",
                  sp.eval(tp=replace(BASE_TP, trail_start_atr=d))))
    print(row("no trail at all (fixed stop only)",
              sp.eval(tp=replace(BASE_TP, use_ts_trail=False))))

    #--- 7. volatility band ------------------------------------------------
    header("7. VOLATILITY BAND  (skip the quietest / wildest conditions)")
    print(row("baseline: any volatility", sp.eval()))
    core = core_for(sp.o, sp.h, sp.l, sp.c, BASE_EP)
    atr = core["atr"]
    rank = pm.percentrank(atr, 200)
    for label, lo, hi in [
        ("ATR rank 20-100 (skip quietest 20%)", 20, 100),
        ("ATR rank 0-80 (skip wildest 20%)", 0, 80),
        ("ATR rank 20-80 (middle only)", 20, 80),
        ("ATR rank 40-100 (active only)", 40, 100),
    ]:
        m = np.zeros(len(atr), dtype=bool)
        ok = ~np.isnan(rank)
        m[ok] = (rank[ok] >= lo) & (rank[ok] <= hi)
        print(row(label, sp.eval(mask_l=m, mask_s=m)))

    print("\n" + "=" * 100)
    print("Read the TEST columns. An idea is only interesting if it improves BOTH halves,")
    print("keeps a usable number of trades, and has a reason to work beyond the numbers.")
    print("=" * 100)


if __name__ == "__main__":
    main()
