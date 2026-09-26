"""How long would this take to pass an FTMO challenge - and how often would it blow up?

A single backtest answers "what happened from January". A challenge does not start
in January, it starts the day you buy it. So this starts a fresh challenge on
EVERY trading day in the data and reports the distribution: how many days to the
target, how often the target is hit at all, and how often a limit is breached
first.

Rules applied (FTMO Challenge, verify against your own program - FTMO has changed
these over time and they differ by account type):

  * profit target   +10% of the INITIAL balance
  * max daily loss  -5% of the initial balance, measured against the balance at
                    the start of that server day
  * max total loss  -10% of the initial balance (static, not trailing)
  * limits are checked on EQUITY, so floating losses count - approximated here
    from each trade's worst adverse excursion (MAE), which the backtester records

Timestamps are MT5 server time (FTMO is UTC+2/+3), so the day boundaries are the
broker's, which is what FTMO measures against.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

import backtest
import dataset
import engine
import filters
import shipped

PROFIT_TARGET = 0.10
MAX_DAILY_LOSS = 0.05
MAX_TOTAL_LOSS = 0.10
CONTRACT = 100.0


def server_day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def simulate(trades, initial: float):
    """Walk the trades under the challenge rules.

    Returns (outcome, day_index, calendar_days, trades_used, peak_dd_pct) where
    outcome is 'pass', 'fail_daily', 'fail_total' or 'incomplete'.
    """
    target = initial * (1.0 + PROFIT_TARGET)
    floor_total = initial * (1.0 - MAX_TOTAL_LOSS)

    equity = initial
    day_start_equity = initial
    cur_day = None
    days_seen = 0
    worst_equity = initial
    first_day = None

    for i, tr in enumerate(trades, 1):
        day = server_day(tr.exit_time)
        if day != cur_day:
            cur_day = day
            day_start_equity = equity
            days_seen += 1
            if first_day is None:
                first_day = datetime.fromtimestamp(int(tr.exit_time), tz=timezone.utc)

        # floating low while this trade was open (MAE is negative, in price units)
        floating = equity + tr.mae * tr.lots * CONTRACT
        worst_equity = min(worst_equity, floating)
        if floating <= floor_total:
            return ("fail_total", days_seen, _cal(first_day, tr), i,
                    (initial - worst_equity) / initial * 100)
        if floating <= day_start_equity - initial * MAX_DAILY_LOSS:
            return ("fail_daily", days_seen, _cal(first_day, tr), i,
                    (initial - worst_equity) / initial * 100)

        equity += tr.profit
        worst_equity = min(worst_equity, equity)
        if equity <= floor_total:
            return ("fail_total", days_seen, _cal(first_day, tr), i,
                    (initial - worst_equity) / initial * 100)
        if equity <= day_start_equity - initial * MAX_DAILY_LOSS:
            return ("fail_daily", days_seen, _cal(first_day, tr), i,
                    (initial - worst_equity) / initial * 100)
        if equity >= target:
            return ("pass", days_seen, _cal(first_day, tr), i,
                    (initial - worst_equity) / initial * 100)

    return ("incomplete", days_seen, 0, len(trades),
            (initial - worst_equity) / initial * 100)


def _cal(first_dt, tr) -> int:
    if first_dt is None:
        return 0
    last = datetime.fromtimestamp(int(tr.exit_time), tz=timezone.utc)
    return (last - first_dt).days + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--balance", type=float, default=100_000.0,
                    help="FTMO account size (large enough that the 0.01 min lot never binds)")
    ap.add_argument("--timeframe", default="M15")
    ap.add_argument("--spread", type=float, default=shipped.SPREAD)
    ap.add_argument("--commission", type=float, default=3.0)
    ap.add_argument("--risks", default="0.25,0.5,0.75,1.0,1.5,2.0")
    ap.add_argument("--symbol", default=shipped.SYMBOL)
    ap.add_argument("--sensitivity", type=float, default=shipped.SENSITIVITY)
    ap.add_argument("--atr-length", type=int, default=shipped.ATR_LENGTH)
    ap.add_argument("--fakeout-strength", default=shipped.FAKEOUT_STRENGTH)
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--min-strength", type=int, default=shipped.MIN_SIGNAL_STRENGTH)
    ap.add_argument("--sl", type=float, default=shipped.SL_ATR_MULT)
    ap.add_argument("--htf", default=(shipped.HTF or ""), help="higher-timeframe agreement, e.g. H1; '' = off")
    ap.add_argument("--trend-ema", type=int, default=shipped.TREND_EMA,
                    help="EMA trend regime filter length; 0 disables it")
    ap.add_argument("--step-days", type=int, default=2,
                    help="start a fresh challenge every N trading days")
    args = ap.parse_args()

    o, h, l, c, t = dataset.from_npy(args.symbol, args.timeframe)
    print(f"data   : {args.symbol} {args.timeframe}  {dataset.describe(t)}")
    print(f"costs  : spread {args.spread} USD/oz, commission {args.commission} USD/lot/side")
    print(f"account: {args.balance:,.0f} USD")
    print(f"filter : EMA{args.trend_ema} trend regime" if args.trend_ema else "filter : none")
    print(f"rules  : target +{PROFIT_TARGET:.0%}, daily loss -{MAX_DAILY_LOSS:.0%}, "
          f"total loss -{MAX_TOTAL_LOSS:.0%} (equity based, floating losses counted)")

    ep = engine.EngineParams(
        sensitivity=args.sensitivity, atr_length=args.atr_length,
        filter_fakeouts=not args.no_filter, fakeout_strength=args.fakeout_strength,
        min_signal_strength=args.min_strength)
    core = engine.run_core(o, h, l, c, ep.sensitivity, ep.atr_length, ep.max_data)
    eng = engine.apply_signals(core, ep)
    costs = backtest.ftmo_costs(spread_price=args.spread)  # real FTMO XAUUSD terms

    mask_l = mask_s = None
    if args.htf:
        mask_l, mask_s = filters.htf_masks(t, args.htf, symbol=args.symbol,
                                           sensitivity=args.sensitivity,
                                           atr_length=args.atr_length)
        print(f"filter : + {args.htf} engine agreement")
    wl, ws = shipped.wt_masks(h, l, c)
    if wl is not None:
        mask_l = wl if mask_l is None else (mask_l & wl)
        mask_s = ws if mask_s is None else (mask_s & ws)
        print("filter : + WaveTrend entry agreement")

    # candidate start bars: the first bar of each distinct server day
    days = np.array([server_day(x) for x in t])
    day_starts = [0] + list(np.flatnonzero(days[1:] != days[:-1]) + 1)
    day_starts = [b for b in day_starts if b >= 500][:: args.step_days]

    base_tp = backtest.TradeParams(
        sl_atr_mult=args.sl, use_exhaustion_exit=False,
        trend_ema_length=args.trend_ema,
        be_trigger_r=shipped.BE_TRIGGER_R,
        be_offset_r=shipped.BE_OFFSET_R,
        lot_mode="risk", initial_balance=args.balance, compound=True,
    )

    print(f"\nstarting a fresh challenge on {len(day_starts)} different days\n")
    hdr = (f"{'risk':>6} {'starts':>7} {'passed':>7} {'failed':>7} {'ran out':>8} | "
           f"{'days to pass (trading days)':>34} | {'worst DD%':>10}")
    print(hdr)
    print(f"{'':>6} {'':>7} {'':>7} {'':>7} {'':>8} | "
          f"{'p25':>8}{'median':>9}{'p75':>9}{'max':>8} | {'':>10}")
    print("-" * len(hdr))

    summary = {}
    for risk in [float(x) for x in args.risks.split(",")]:
        tp = replace(base_tp, risk_pct=risk)
        outcomes = defaultdict(int)
        pass_days = []
        pass_cal = []
        pass_trades = []
        worst_dds = []

        for sb in day_starts:
            trades, _, _ = backtest.run(o, h, l, c, t, eng=eng,
                                        trade=replace(tp, start_bar=sb), costs=costs,
                                        long_ok=mask_l, short_ok=mask_s)
            if not trades:
                outcomes["incomplete"] += 1
                continue
            outcome, dsee, cal, ntr, dd = simulate(trades, args.balance)
            outcomes[outcome] += 1
            worst_dds.append(dd)
            if outcome == "pass":
                pass_days.append(dsee)
                pass_cal.append(cal)
                pass_trades.append(ntr)

        n = len(day_starts)
        npass = outcomes["pass"]
        nfail = outcomes["fail_daily"] + outcomes["fail_total"]
        if pass_days:
            q = np.percentile(pass_days, [25, 50, 75])
            mx = max(pass_days)
            dstr = f"{q[0]:>8.0f}{q[1]:>9.0f}{q[2]:>9.0f}{mx:>8.0f}"
        else:
            dstr = f"{'-':>8}{'-':>9}{'-':>9}{'-':>8}"
        print(f"{risk:>5.2f}% {n:>7d} {npass:>7d} {nfail:>7d} "
              f"{outcomes['incomplete']:>8d} | {dstr} | {max(worst_dds):>9.1f}%")

        summary[risk] = {
            "n": n, "pass": npass, "fail_daily": outcomes["fail_daily"],
            "fail_total": outcomes["fail_total"], "incomplete": outcomes["incomplete"],
            "days": pass_days, "cal": pass_cal, "trades": pass_trades,
        }

    print("\n'ran out' = the data ended before the account either passed or breached a limit;")
    print("those are not failures, just unfinished - the sample is only 7.5 months long.")

    #--- detail for the two risk levels worth using
    for risk in (0.5, 0.75, 1.0):
        s = summary.get(risk)
        if not s or not s["days"]:
            continue
        print(f"\n--- risk {risk}% detail")
        pr = s["pass"] / max(1, s["pass"] + s["fail_daily"] + s["fail_total"]) * 100
        print(f"  of the challenges that finished, {pr:.0f}% passed")
        print(f"  trading days to target : median {np.median(s['days']):.0f}, "
              f"p25 {np.percentile(s['days'], 25):.0f}, p75 {np.percentile(s['days'], 75):.0f}, "
              f"max {max(s['days'])}")
        print(f"  calendar days to target: median {np.median(s['cal']):.0f}, "
              f"p75 {np.percentile(s['cal'], 75):.0f}, max {max(s['cal'])}")
        print(f"  trades to target       : median {np.median(s['trades']):.0f}, "
              f"max {max(s['trades'])}")
        within30 = int(np.sum(np.array(s["cal"]) <= 30))
        print(f"  hit target within 30 calendar days: {within30}/{len(s['cal'])} "
              f"({within30 / len(s['cal']) * 100:.0f}% of passes)")
        print(f"  breaches: {s['fail_daily']} daily-loss, {s['fail_total']} total-loss")


if __name__ == "__main__":
    main()
