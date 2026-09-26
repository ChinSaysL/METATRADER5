"""Bar-close backtester for the ported engine - the research counterpart of the EA.

Trade model (the same one the EA implements):
  * a signal is produced at the CLOSE of a bar and filled at the OPEN of the next
    bar - never on the signal bar itself, so there is no lookahead;
  * initial hard stop at entry -/+ sl_atr_mult * ATR(atr_length);
  * the stop then trails the engine's own adaptive SuperTrend line `ts` and never
    loosens;
  * optional early exit when the ported Take-Profit-Warning exhaustion fires,
    taken at that bar's close;
  * an opposite signal closes and reverses.

Costs: MT5 bars are bid prices, so a buy pays the spread on entry and a sell pays
it on exit, plus a per-lot-per-side commission.

If a bar touches both the stop and the take profit, the stop is assumed first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import engine
import pine_math
import reversal_exit


@dataclass
class Costs:
    """Trading costs. The FTMO XAUUSD defaults are in ftmo_costs() below."""

    spread_price: float = 0.20        # USD per ounce
    commission_per_lot: float = 3.0   # flat USD per lot per side
    contract_size: float = 100.0
    volume_step: float = 0.01
    volume_min: float = 0.01

    #--- MT5 PERCENT/VOLUME commission: percent of notional, charged per side.
    #--- Used instead of commission_per_lot when non-zero.
    commission_percent: float = 0.0

    #--- Swap quoted in POINTS per lot per night, as MT5 reports it. Point value
    #--- for a lot is contract_size * point, so for XAUUSD (point 0.01, contract
    #--- 100) one point is worth 1.00 USD per lot.
    swap_long_points: float = 0.0
    swap_short_points: float = 0.0
    point_value_per_lot: float = 1.0
    triple_swap_weekday: int = 2      # Wednesday (Mon=0): 3x swap for the weekend


def ftmo_costs(spread_price: float = 0.44) -> "Costs":
    """FTMO's actual XAUUSD contract terms."""
    return Costs(
        spread_price=spread_price,
        commission_per_lot=0.0,
        contract_size=100.0,
        commission_percent=0.0014,
        swap_long_points=-83.0,
        swap_short_points=-8.3,
        point_value_per_lot=1.0,
    )


def _swap_cost(entry_time: int, exit_time: int, lots: float, direction: int,
               ct: Costs) -> float:
    """Financing for every rollover the trade was held through.

    MT5 charges at the broker's 00:00, so the number of charges is the number of
    server-day boundaries crossed. Wednesday is charged triple to cover the
    weekend, which is the market convention.
    """
    pts = ct.swap_long_points if direction > 0 else ct.swap_short_points
    if pts == 0.0:
        return 0.0
    d0 = int(entry_time) // 86400
    d1 = int(exit_time) // 86400
    if d1 <= d0:
        return 0.0
    total = 0.0
    for day in range(d0 + 1, d1 + 1):
        # 1970-01-01 was a Thursday, so weekday = (day + 3) % 7 with Mon = 0
        weekday = (day + 3) % 7
        mult = 3.0 if weekday == ct.triple_swap_weekday else 1.0
        total += pts * ct.point_value_per_lot * lots * mult
    return total


@dataclass
class TradeParams:
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 0.0          # 0 = no fixed target
    use_atr_stop: bool = True
    use_ts_trail: bool = True
    use_exhaustion_exit: bool = True
    exhaustion_sensitivity: str = "Normal"
    reverse_on_opposite: bool = True
    entry_delay_bars: int = 0         # >0 only for the no-lookahead sanity check
    lot_mode: str = "risk"            # "risk" or "fixed"
    risk_pct: float = 1.0
    fixed_lot: float = 0.10
    initial_balance: float = 10_000.0
    compound: bool = True
    warmup_bars: int = 500            # bars skipped before the first trade is allowed
    start_bar: int = 0                # begin trading no earlier than this bar

    #--- Trend regime gate: longs only above the EMA, shorts only below. 0 = off.
    #--- Mirrors InpUseTrendFilter / InpTrendEmaLength in the EA.
    trend_ema_length: int = 0

    #--- experiment hooks (all default to off, i.e. the config in the README)
    allowed_hours: str = ""           # server-time entry window, e.g. "7-20"; "" = always
    direction: str = "both"           # "both" | "long" | "short"
    initial_stop_mode: str = "atr"    # "atr" = SLAtrMult*ATR, "ts" = the adaptive line
    trail_start_atr: float = 0.0      # only begin trailing past this much open profit
    #--- Partial profit taking: bank `partial_fraction` of the position once open
    #--- profit reaches `partial_at_atr` * ATR, and run the remainder on the trail.
    #--- 0 disables it. Optionally pull the stop to breakeven at the same moment.
    partial_at_atr: float = 0.0
    partial_fraction: float = 0.5
    partial_to_breakeven: bool = False

    #--- Re-entry: after the trail or stop takes us out, get back in while the
    #--- engine still points the same way. 0 disables it.
    max_reentries: int = 0

    #--- Profit protection, in R (multiples of the initial risk).
    #---
    #--- The adaptive line `ts` sits roughly factor*ATR away from price, so a
    #--- freshly flipped trade has effectively NO protection for its first bars.
    #--- Measured consequence: 71% of trades that reached +0.5R and 49% of those
    #--- that reached +1.0R still ended as full losses, handing back 59% of the
    #--- average peak. These two rules act independently of `ts`:
    #---
    #---   be_trigger_r  once open profit reaches this many R, pull the stop to
    #---                 breakeven plus be_offset_r (offset covers costs).
    #---   r_trail       once in profit, never let the stop sit further than this
    #---                 many R behind the best price reached.
    #---
    #--- Unlike a fixed target these cap losses, not winners.
    be_trigger_r: float = 0.0
    be_offset_r: float = 0.05
    r_trail: float = 0.0

    #--- Time stop. Measured: every holding-time bucket under 41 bars averages
    #--- -1.02R, i.e. a clean full stop, while 41+ bars averages +1.74R. Losers
    #--- go straight to the stop and pay full risk. If a trade has not proved
    #--- itself within `time_stop_bars`, leave at market instead of paying the
    #--- rest of the stop distance. 0 disables.
    time_stop_bars: int = 0
    time_stop_min_r: float = 0.0


@dataclass
class Trade:
    direction: int
    entry_bar: int
    entry_time: int
    entry_price: float
    lots: float
    exit_bar: int = -1
    exit_time: int = 0
    exit_price: float = 0.0
    reason: str = ""
    profit: float = 0.0
    mae: float = 0.0                  # worst excursion, price units
    mfe: float = 0.0                  # best excursion, price units
    swap: float = 0.0                 # financing paid over the held nights
    risk_price: float = 0.0           # 1R in price units, fixed at entry
    orig_lots: float = 0.0            # size at entry, before any partial
    partial_profit: float = 0.0       # banked by a partial exit
    reentry: int = 0                  # 0 = original entry, 1+ = nth re-entry


def _commission(lots: float, price: float, ct: Costs) -> float:
    """One side's commission. PERCENT/VOLUME takes precedence when set."""
    if ct.commission_percent > 0.0:
        notional = lots * ct.contract_size * price
        return notional * ct.commission_percent / 100.0
    return ct.commission_per_lot * lots


def _round_lots(lots: float, costs: Costs) -> float:
    steps = math.floor(lots / costs.volume_step + 1e-9)
    return max(costs.volume_min, steps * costs.volume_step)


def _size(balance: float, stop_distance: float, tp: TradeParams, costs: Costs) -> float:
    if tp.lot_mode == "fixed" or stop_distance <= 0:
        return _round_lots(tp.fixed_lot, costs)
    risk_cash = balance * tp.risk_pct / 100.0
    lots = risk_cash / (stop_distance * costs.contract_size)
    return _round_lots(lots, costs)


def _parse_hours(spec: str):
    """"7-20" -> the set {7..20}; "" -> None (no restriction)."""
    if not spec:
        return None
    hours = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            hours.update(range(int(a), int(b) + 1))
        else:
            hours.add(int(part))
    return hours


def run(
    open_,
    high,
    low,
    close,
    times,
    eng=None,
    engine_params=None,
    trade=None,
    costs=None,
    long_ok=None,
    short_ok=None,
):
    """Run the backtest. Returns (trades, stats, equity_curve).

    long_ok / short_ok are optional per-bar boolean masks used to test regime
    gates (a trend filter, a volatility band, higher-timeframe agreement). They
    veto entries only; an open position is still managed normally.
    """
    tp = trade or TradeParams()
    ct = costs or Costs()
    eng = eng if eng is not None else engine.run(open_, high, low, close, engine_params)

    n = len(close)
    hours_allowed = _parse_hours(tp.allowed_hours)
    bar_hour = None
    if hours_allowed is not None:
        bar_hour = ((np.asarray(times, dtype=np.int64) // 3600) % 24).astype(int)

    #--- trend regime gate, folded into the same masks the experiments use, so it
    #--- composes with any mask a caller passes in
    if tp.trend_ema_length > 0:
        e = pine_math.ema(close, tp.trend_ema_length)
        warm = ~np.isnan(e)
        # abstain while the EMA is warming, matching the EA's TrendAllows()
        t_long = ~warm | (close > e)
        t_short = ~warm | (close < e)
        long_ok = t_long if long_ok is None else (long_ok & t_long)
        short_ok = t_short if short_ok is None else (short_ok & t_short)
    atr = eng["atr"]
    ts = eng["ts"]
    buy = eng["buy"]
    sell = eng["sell"]

    if tp.use_exhaustion_exit:
        ex = reversal_exit.exhaustion(high, low, close, tp.exhaustion_sensitivity)
        bear_ex = ex["bearish_exhaustion"]
        bull_ex = ex["bullish_exhaustion"]
    else:
        bear_ex = np.zeros(n, dtype=bool)
        bull_ex = np.zeros(n, dtype=bool)

    balance = tp.initial_balance
    trades: list[Trade] = []
    equity_time: list[int] = []
    equity_value: list[float] = []

    pos = 0                 # 0 flat, 1 long, -1 short
    cur: Trade | None = None
    stop = 0.0
    target = 0.0
    pending = 0             # signal waiting to be filled at the next open
    pending_exit = False    # reversal exit waiting to be filled at the next open
    pending_atr = float("nan")
    pending_ts = float("nan")
    pending_age = 0
    pending_reentry = 0     # >0 marks the queued entry as the nth re-entry
    partial_level = 0.0     # price at which the partial fills; 0 = none pending
    last_exit_dir = 0       # direction of the last stop-out, for re-entry
    reentries_used = 0      # reset whenever a fresh engine signal arrives
    os_state = eng["os"]

    def close_trade(bar: int, price: float, reason: str) -> None:
        nonlocal pos, cur, balance, last_exit_dir
        assert cur is not None
        # Bars are bid: a short buys back at the ask.
        fill = price + ct.spread_price if cur.direction < 0 else price
        gross = (fill - cur.entry_price) * cur.direction * cur.lots * ct.contract_size
        # entry commission on the original size, exit commission on what is left
        commission = (
            _commission(cur.orig_lots, cur.entry_price, ct)
            + _commission(cur.lots, fill, ct)
        )
        swap = _swap_cost(cur.entry_time, int(times[bar]), cur.lots, cur.direction, ct)
        cur.exit_bar = bar
        cur.exit_time = int(times[bar])
        cur.exit_price = fill
        cur.reason = reason
        cur.swap = swap
        cur.profit = gross - commission + swap + cur.partial_profit
        balance += cur.profit
        trades.append(cur)
        equity_time.append(int(times[bar]))
        equity_value.append(balance)
        last_exit_dir = cur.direction if reason in ("stop", "target") else 0
        cur = None
        pos = 0

    def take_partial(bar: int, price: float) -> None:
        """Bank part of the position at `price` (a limit fill, so the level is
        exact) and leave the rest running on the trail."""
        nonlocal cur, balance, stop
        assert cur is not None
        closed = _round_lots(cur.lots * tp.partial_fraction, ct)
        if closed <= 0 or closed >= cur.lots:
            return
        fill = price + ct.spread_price if cur.direction < 0 else price
        gross = (fill - cur.entry_price) * cur.direction * closed * ct.contract_size
        pnl = gross - _commission(closed, fill, ct)       # exit side only
        cur.partial_profit += pnl
        cur.lots -= closed
        balance += pnl
        equity_time.append(int(times[bar]))
        equity_value.append(balance)
        if tp.partial_to_breakeven:
            be = cur.entry_price
            if cur.direction > 0:
                stop = max(stop, be)
            else:
                stop = be if stop <= 0 else min(stop, be)

    def open_trade(bar: int, direction: int, a: float, sig_ts: float = float("nan"),
                   reentry: int = 0) -> None:
        nonlocal pos, cur, stop, target, partial_level
        price = open_[bar]
        fill = price + ct.spread_price if direction > 0 else price
        if math.isnan(a) or a <= 0:
            return
        stop_dist = tp.sl_atr_mult * a
        stop = (fill - stop_dist) if direction > 0 else (fill + stop_dist)
        if tp.initial_stop_mode == "ts" and not math.isnan(sig_ts):
            # put the initial stop on the adaptive line instead of an ATR multiple
            if (direction > 0 and sig_ts < fill) or (direction < 0 and sig_ts > fill):
                stop = sig_ts
                stop_dist = abs(fill - sig_ts)
        if not tp.use_atr_stop:
            stop = 0.0
        target = 0.0
        if tp.tp_atr_mult > 0:
            t_dist = tp.tp_atr_mult * a
            target = (fill + t_dist) if direction > 0 else (fill - t_dist)
        partial_level = 0.0
        if tp.partial_at_atr > 0:
            pd = tp.partial_at_atr * a
            partial_level = (fill + pd) if direction > 0 else (fill - pd)
        lots = _size(balance if tp.compound else tp.initial_balance, stop_dist, tp, ct)
        cur = Trade(
            direction=direction,
            entry_bar=bar,
            entry_time=int(times[bar]),
            entry_price=fill,
            lots=lots,
            orig_lots=lots,
            reentry=reentry,
            risk_price=stop_dist,
        )
        pos = direction

    start = max(tp.warmup_bars, engine.CHOP_RANK_LENGTH + engine.CHOP_LOOKBACK + 2,
                tp.start_bar)

    for i in range(start, n):
        # 1) fill whatever the previous bar's close asked for, at this open.
        #    A signal outranks a reversal exit, matching the EA's ManageAndTrade().
        if pending != 0:
            if pending_age < tp.entry_delay_bars:
                pending_age += 1
            else:
                if pos != 0 and pos != pending:
                    close_trade(i, open_[i], "reverse")
                if pos == 0:
                    open_trade(i, pending, pending_atr, pending_ts, pending_reentry)
                pending = 0
                pending_age = 0
                pending_reentry = 0
        elif pending_exit and pos != 0:
            close_trade(i, open_[i], "reversal")
            pending_exit = False
        else:
            pending_exit = False

        # 2) intrabar stop / target, using levels fixed before this bar opened
        if pos != 0 and cur is not None:
            # Signed P&L excursions per unit. For a long the bar's high is the
            # favourable end and the low the adverse one; for a short it is the
            # other way round. Getting this backwards on shorts makes a winning
            # excursion look like a floating loss, which matters for the FTMO
            # daily-loss check in ftmo_challenge.py.
            excursion_hi = (high[i] - cur.entry_price) * cur.direction
            excursion_lo = (low[i] - cur.entry_price) * cur.direction
            favourable = excursion_hi if cur.direction > 0 else excursion_lo
            adverse = excursion_lo if cur.direction > 0 else excursion_hi
            cur.mfe = max(cur.mfe, favourable)
            cur.mae = min(cur.mae, adverse)

            hit_stop = stop > 0 and (
                low[i] <= stop if cur.direction > 0 else high[i] >= stop
            )
            hit_target = target > 0 and (
                high[i] >= target if cur.direction > 0 else low[i] <= target
            )
            #    If a bar touches both the stop and something favourable, the stop
            #    is assumed first throughout - so a stop-out cancels the partial.
            if hit_stop:
                close_trade(i, stop, "stop")
            elif hit_target:
                close_trade(i, target, "target")
            elif partial_level > 0 and (
                high[i] >= partial_level if cur.direction > 0 else low[i] <= partial_level
            ):
                take_partial(i, partial_level)
                partial_level = 0.0

        # 3) decide what to do at the next open. The EA returns early after a
        #    signal or a reversal exit, so neither is followed by a trail update;
        #    `decided` reproduces those early returns.
        want = 1 if buy[i] else (-1 if sell[i] else 0)

        #    Entry vetoes live in the trade layer, not the engine: a vetoed signal
        #    is treated as no signal at all, so an open position keeps being
        #    managed and trailed normally.
        if want != 0:
            if tp.direction == "long" and want < 0:
                want = 0
            elif tp.direction == "short" and want > 0:
                want = 0
        if want != 0 and hours_allowed is not None and bar_hour[i] not in hours_allowed:
            want = 0
        if want > 0 and long_ok is not None and not long_ok[i]:
            want = 0
        if want < 0 and short_ok is not None and not short_ok[i]:
            want = 0

        decided = False
        if want != 0:
            if pos == 0 or (pos != want and tp.reverse_on_opposite):
                pending, pending_age = want, 0
                pending_atr = atr[i]
                pending_ts = ts[i]
                pending_reentry = 0
            decided = True
            reentries_used = 0          # a fresh signal starts a new leg
            last_exit_dir = 0

        #    Re-entry: the trail took us out but the engine still points the same
        #    way, so get back in. Uses the same vetoes as a normal entry, and is
        #    capped per leg so one trend cannot generate endless churn.
        if (
            not decided
            and pos == 0
            and pending == 0
            and tp.max_reentries > 0
            and last_exit_dir != 0
            and reentries_used < tp.max_reentries
        ):
            d = last_exit_dir
            still_trending = (os_state[i] == 1) if d > 0 else (os_state[i] == 0)
            allowed = still_trending
            if allowed and tp.direction == "long" and d < 0:
                allowed = False
            if allowed and tp.direction == "short" and d > 0:
                allowed = False
            if allowed and hours_allowed is not None and bar_hour[i] not in hours_allowed:
                allowed = False
            if allowed and d > 0 and long_ok is not None and not long_ok[i]:
                allowed = False
            if allowed and d < 0 and short_ok is not None and not short_ok[i]:
                allowed = False
            if allowed:
                pending, pending_age = d, 0
                pending_atr = atr[i]
                pending_ts = ts[i]
                reentries_used += 1
                pending_reentry = reentries_used
                decided = True
            else:
                # trend is over; stop trying to get back into this leg
                if not still_trending:
                    last_exit_dir = 0

        if not decided and pos != 0 and tp.use_exhaustion_exit:
            if (pos > 0 and bear_ex[i]) or (pos < 0 and bull_ex[i]):
                pending_exit = True
                decided = True

        # 3a) Time stop, evaluated at this bar's close and filled at the next
        #     open like every other decision.
        if (pos != 0 and cur is not None and tp.time_stop_bars > 0
                and cur.risk_price > 0 and not pending_exit and pending == 0):
            if i - cur.entry_bar >= tp.time_stop_bars:
                open_r = (close[i] - cur.entry_price) * pos / cur.risk_price
                if open_r < tp.time_stop_min_r:
                    pending_exit = True

        # 3b) Profit protection in R. Runs REGARDLESS of `decided`, because a
        #     trade that is giving back profit must be protected even on a bar
        #     that also produced a signal. Only ever tightens.
        if pos != 0 and cur is not None and cur.risk_price > 0:
            risk = cur.risk_price
            if tp.be_trigger_r > 0:
                open_r = (close[i] - cur.entry_price) * pos / risk
                if open_r >= tp.be_trigger_r:
                    be = cur.entry_price + pos * tp.be_offset_r * risk
                    if pos > 0 and be < close[i]:
                        stop = max(stop, be)
                    elif pos < 0 and be > close[i]:
                        stop = be if stop <= 0 else min(stop, be)
            if tp.r_trail > 0 and cur.mfe > 0:
                peak_price = cur.entry_price + pos * cur.mfe
                cand = peak_price - pos * tp.r_trail * risk
                if pos > 0 and cand < close[i]:
                    stop = max(stop, cand)
                elif pos < 0 and cand > close[i]:
                    stop = cand if stop <= 0 else min(stop, cand)

        # 4) otherwise trail the stop with the adaptive line, in force from the
        #    next bar on - exactly when the EA issues its PositionModify
        if not decided and pos != 0 and tp.use_ts_trail and not math.isnan(ts[i]):
            open_profit = (close[i] - cur.entry_price) * pos if cur is not None else 0.0
            ready = (
                tp.trail_start_atr <= 0
                or (not math.isnan(atr[i]) and open_profit >= tp.trail_start_atr * atr[i])
            )
            if ready:
                if pos > 0 and ts[i] < close[i]:
                    stop = max(stop, ts[i])
                elif pos < 0 and ts[i] > close[i]:
                    stop = ts[i] if stop <= 0 else min(stop, ts[i])

    if pos != 0 and cur is not None:
        close_trade(n - 1, close[n - 1], "end")

    stats = summarize(trades, tp.initial_balance, equity_value)
    return trades, stats, (np.array(equity_time), np.array(equity_value))


def summarize(trades, initial_balance: float, equity_value) -> dict:
    if not trades:
        return {"trades": 0, "net_profit": 0.0}

    profits = np.array([t.profit for t in trades])
    wins = profits[profits > 0]
    losses = profits[profits <= 0]
    eq = np.concatenate(([initial_balance], np.array(equity_value)))
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    dd_pct = np.where(peak > 0, dd / peak * 100.0, 0.0)
    held = np.array([t.exit_bar - t.entry_bar for t in trades])
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1

    return {
        "trades": len(trades),
        "net_profit": float(profits.sum()),
        "net_profit_pct": float(profits.sum() / initial_balance * 100.0),
        "gross_profit": gross_win,
        "gross_loss": gross_loss,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": float(profits.mean()),
        "win_rate": float(len(wins) / len(trades) * 100.0),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "largest_win": float(profits.max()),
        "largest_loss": float(profits.min()),
        "max_drawdown": float(dd.max()),
        "max_drawdown_pct": float(dd_pct.max()),
        "final_balance": float(eq[-1]),
        "avg_bars_held": float(held.mean()),
        "exit_reasons": reasons,
    }


def format_stats(stats: dict) -> str:
    if not stats.get("trades"):
        return "  no trades"
    lines = [
        f"  trades          {stats['trades']}",
        f"  net profit      {stats['net_profit']:>12,.2f}  ({stats['net_profit_pct']:.1f}%)",
        f"  profit factor   {stats['profit_factor']:>12.3f}",
        f"  expectancy      {stats['expectancy']:>12,.2f} per trade",
        f"  win rate        {stats['win_rate']:>12.1f}%",
        f"  avg win / loss  {stats['avg_win']:>12,.2f} / {stats['avg_loss']:,.2f}",
        f"  max drawdown    {stats['max_drawdown']:>12,.2f}  ({stats['max_drawdown_pct']:.1f}%)",
        f"  final balance   {stats['final_balance']:>12,.2f}",
        f"  avg bars held   {stats['avg_bars_held']:>12.1f}",
        f"  exits           {stats['exit_reasons']}",
    ]
    return "\n".join(lines)
