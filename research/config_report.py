"""Everything about the shipped configuration that a live account needs.

A profit factor is not a plan. This spells out what the config in shipped.py
actually means on an account: starting balance, month-by-month P&L, the lot
sizes it trades, losing streaks, and how it behaves at other balances, risk
levels and cost assumptions.

Parameters come from shipped.py, which is checked against the EA's compiled
defaults - so this always reports on what would actually run.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

import backtest
import dataset
import engine
import shipped

SPREAD = shipped.SPREAD
START = shipped.INITIAL_BALANCE

EP = shipped.engine_params()
TP = shipped.trade_params()


def month_key(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m")


def run(o, h, l, c, t, core, ep=EP, tp=TP, costs=None):
    eng = engine.apply_signals(core, ep)
    return backtest.run(o, h, l, c, t, eng=eng, trade=tp,
                        costs=costs or backtest.ftmo_costs(SPREAD))


def main() -> None:
    o, h, l, c, t = dataset.from_npy(shipped.SYMBOL, shipped.TIMEFRAME)
    core = engine.run_core(o, h, l, c, EP.sensitivity, EP.atr_length, EP.max_data)
    trades, stats, (eq_t, eq_v) = run(o, h, l, c, t, core)

    print("=" * 74)
    print("RECOMMENDED CONFIG - account detail")
    print("XAUUSD M15, 2026-01-02 -> 2026-08-17 (7.5 months, 14,719 bars)")
    print("config: " + shipped.describe())
    print(f"costs : FTMO XAUUSD terms, spread {SPREAD} USD/oz, compounding")
    print("=" * 74)

    print(f"\nstarting balance      {START:>12,.2f} USD")
    print(f"ending balance        {stats['final_balance']:>12,.2f} USD")
    print(f"net profit            {stats['net_profit']:>12,.2f} USD  "
          f"({stats['net_profit_pct']:+.1f}%)")
    print(f"gross profit          {stats['gross_profit']:>12,.2f} USD")
    print(f"gross loss            {-stats['gross_loss']:>12,.2f} USD")
    print(f"profit factor         {stats['profit_factor']:>12.3f}")
    print(f"max drawdown          {stats['max_drawdown']:>12,.2f} USD  "
          f"({stats['max_drawdown_pct']:.1f}% of peak)")

    print(f"\ntrades                {stats['trades']:>12d}")
    print(f"  winners             {int(round(stats['trades'] * stats['win_rate'] / 100)):>12d}"
          f"   ({stats['win_rate']:.1f}%)")
    print(f"  average win         {stats['avg_win']:>12,.2f} USD")
    print(f"  average loss        {stats['avg_loss']:>12,.2f} USD")
    print(f"  expectancy          {stats['expectancy']:>12,.2f} USD per trade")
    print(f"  largest win         {stats['largest_win']:>12,.2f} USD")
    print(f"  largest loss        {stats['largest_loss']:>12,.2f} USD")
    print(f"  avg hold            {stats['avg_bars_held']:>12.1f} bars "
          f"(~{stats['avg_bars_held'] * 15 / 60:.1f} hours)")
    print(f"  trades per month    {stats['trades'] / 7.5:>12.1f}")
    print(f"  exits               {stats['exit_reasons']}")

    #--- lot sizes actually traded
    lots = np.array([tr.lots for tr in trades])
    print(f"\nlot size used         min {lots.min():.2f}  median {np.median(lots):.2f}  "
          f"max {lots.max():.2f}")
    at_min = int((lots <= 0.01).sum())
    if at_min:
        print(f"  {at_min} trade(s) pinned at the 0.01 minimum lot")

    #--- streaks
    wins = np.array([tr.profit > 0 for tr in trades])
    best = cur = 0
    worst = curl = 0
    for w in wins:
        cur = cur + 1 if w else 0
        curl = 0 if w else curl + 1
        best = max(best, cur)
        worst = max(worst, curl)
    print(f"longest win streak    {best:>12d}")
    print(f"longest loss streak   {worst:>12d}")

    #--- monthly P&L
    print("\nmonth       trades      P&L USD     balance")
    print("-" * 46)
    by_month: OrderedDict = OrderedDict()
    for tr in trades:
        by_month.setdefault(month_key(tr.exit_time), []).append(tr.profit)
    bal = START
    for m, ps in by_month.items():
        pnl = float(np.sum(ps))
        bal += pnl
        print(f"{m}      {len(ps):>5d}  {pnl:>+12,.2f}  {bal:>11,.2f}")

    #--- sensitivity to the things you cannot control
    print("\n" + "=" * 74)
    print("WHAT IF THE COSTS ARE WORSE THAN 0.44")
    print("=" * 74)
    print(f"{'spread USD/oz':>14} {'commission/lot':>15} {'net USD':>12} {'PF':>7} {'DD%':>7}")
    print("-" * 60)
    for sp, cm in [(0.44, 0.0), (0.44, 3.0), (0.60, 3.0), (0.80, 3.0), (1.00, 6.0)]:
        _, s, _ = run(o, h, l, c, t, core, costs=backtest.Costs(sp, cm))
        print(f"{sp:>14.2f} {cm:>15.2f} {s['net_profit']:>12,.0f} "
              f"{s['profit_factor']:>7.3f} {s['max_drawdown_pct']:>7.1f}")

    print("\n" + "=" * 74)
    print("RISK PER TRADE  (same signals, different sizing)")
    print("=" * 74)
    print(f"{'risk %':>8} {'net USD':>12} {'end balance':>14} {'DD USD':>11} {'DD %':>7}")
    print("-" * 56)
    for r in (0.5, 1.0, 1.5, 2.0, 3.0):
        _, s, _ = run(o, h, l, c, t, core, tp=replace(TP, risk_pct=r))
        print(f"{r:>8.1f} {s['net_profit']:>12,.0f} {s['final_balance']:>14,.0f} "
              f"{s['max_drawdown']:>11,.0f} {s['max_drawdown_pct']:>7.1f}")

    print("\n" + "=" * 74)
    print("STARTING BALANCE  (1% risk; watch the 0.01 minimum-lot floor)")
    print("=" * 74)
    print(f"{'start USD':>10} {'end USD':>12} {'return':>9} {'DD %':>7} {'min-lot trades':>16}")
    print("-" * 60)
    for b in (500, 1_000, 2_500, 5_000, 10_000, 25_000, 100_000):
        trs, s, _ = run(o, h, l, c, t, core,
                        tp=replace(TP, initial_balance=float(b)))
        lv = np.array([x.lots for x in trs])
        pinned = int((lv <= 0.01).sum())
        print(f"{b:>10,d} {s['final_balance']:>12,.0f} "
              f"{s['net_profit_pct']:>8.1f}% {s['max_drawdown_pct']:>7.1f} {pinned:>16d}")

    print("\n" + "=" * 74)
    print("FIXED LOT instead of % risk")
    print("=" * 74)
    print(f"{'lot':>6} {'net USD':>12} {'DD USD':>11} {'DD %':>7}")
    print("-" * 40)
    for lot in (0.01, 0.05, 0.10, 0.20, 0.50):
        _, s, _ = run(o, h, l, c, t, core,
                      tp=replace(TP, lot_mode="fixed", fixed_lot=lot, compound=False))
        print(f"{lot:>6.2f} {s['net_profit']:>12,.0f} {s['max_drawdown']:>11,.0f} "
              f"{s['max_drawdown_pct']:>7.1f}")


if __name__ == "__main__":
    main()
