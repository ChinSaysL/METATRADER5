"""MetaTrader 5 bridge: connect, describe the gold symbol, cache bar history.

The terminal has to be running (or be launchable from TERMINAL_PATH) and logged
in - the Python API only ever returns history the terminal itself holds, so the
first run on a fresh terminal may come back short and need repeating while the
download completes.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import numpy as np

import MetaTrader5 as mt5

TERMINAL_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

GOLD_CANDIDATES = ("XAUUSD", "GOLD", "XAUUSD.r", "XAUUSDm", "XAUUSD_", "GOLD.spot")


def connect(launch: bool = True):
    """Initialise the bridge, launching the terminal if it is not already up."""
    ok = mt5.initialize(path=TERMINAL_PATH) if launch else mt5.initialize()
    if not ok:
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
    return mt5.terminal_info(), mt5.account_info()


def find_gold(explicit: str | None = None) -> str:
    """Resolve the broker's gold symbol and make sure it is selected."""
    if explicit:
        if mt5.symbol_info(explicit) is None:
            raise RuntimeError(f"symbol {explicit} not found on this account")
        mt5.symbol_select(explicit, True)
        return explicit
    for name in GOLD_CANDIDATES:
        if mt5.symbol_info(name) is not None:
            mt5.symbol_select(name, True)
            return name
    # Fall back to a scan of everything the account offers.
    for s in mt5.symbols_get() or []:
        if "XAU" in s.name.upper():
            mt5.symbol_select(s.name, True)
            return s.name
    raise RuntimeError("no gold symbol found on this account")


def describe(symbol: str) -> dict:
    """Everything the backtest needs to cost a trade correctly."""
    i = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    return {
        "symbol": i.name,
        "description": i.description,
        "digits": i.digits,
        "point": i.point,
        "tick_size": i.trade_tick_size,
        "tick_value": i.trade_tick_value,
        "contract_size": i.trade_contract_size,
        "volume_min": i.volume_min,
        "volume_max": i.volume_max,
        "volume_step": i.volume_step,
        "spread_points_now": i.spread,
        "spread_price_now": (tick.ask - tick.bid) if tick else None,
        "stops_level_points": i.trade_stops_level,
        "freeze_level_points": i.trade_freeze_level,
        "swap_long": i.swap_long,
        "swap_short": i.swap_short,
        "currency_profit": i.currency_profit,
    }


def fetch(symbol: str, timeframe: str, bars: int = 200_000, retries: int = 6):
    """Pull the deepest available history for one timeframe, oldest bar first.

    Two failure modes are handled, because both look identical from the caller:

      * asking for more bars than the terminal's "Max bars in chart" setting
        allows returns (-2, 'Invalid params') rather than a short answer, so the
        request is stepped down until one succeeds;
      * history arrives asynchronously, so the first answer for a symbol is
        often empty or short even when the server has years - hence the retries,
        which stop as soon as the answer stops growing.
    """
    tf = TIMEFRAMES[timeframe]
    best = None

    for want in _step_down(bars):
        last_len = 0
        for attempt in range(retries):
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, want)
            if rates is not None and len(rates) > 0:
                if best is None or len(rates) > len(best):
                    best = rates
                if attempt and len(rates) <= last_len:
                    break            # stopped growing; this is all there is
                last_len = len(rates)
            time.sleep(1.0)
        # got essentially everything we asked for: no point asking for less
        if best is not None and len(best) >= want * 0.9:
            break

    if best is None or len(best) == 0:
        raise RuntimeError(f"no {timeframe} history for {symbol}: {mt5.last_error()}")
    return best


def _step_down(bars: int):
    """Descending request sizes to probe the terminal's bar limit."""
    ladder = [bars, 200_000, 150_000, 100_000, 65_000, 50_000, 20_000, 5_000]
    seen, out = set(), []
    for n in ladder:
        n = int(n)
        if n > 0 and n <= bars and n not in seen:
            seen.add(n)
            out.append(n)
    return out or [bars]


def save(symbol: str, timeframe: str, rates) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.npy")
    np.save(path, rates)
    return path


def load(symbol: str, timeframe: str):
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.npy")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} - run mt5_data.py first")
    return np.load(path)


def as_ohlc(rates):
    """Split a rates array into the plain float arrays the engine expects."""
    return (
        rates["open"].astype(float),
        rates["high"].astype(float),
        rates["low"].astype(float),
        rates["close"].astype(float),
        rates["time"].astype(np.int64),
    )


def _fmt(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--timeframes", default="M15,M5,M1")
    ap.add_argument("--bars", type=int, default=200_000)
    args = ap.parse_args()

    term, acct = connect()
    print(f"terminal : {term.name} build {term.build}  connected={term.connected}")
    print(f"           data path {term.data_path}")
    if acct is not None:
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(acct.trade_mode, acct.trade_mode)
        print(
            f"account  : {acct.login} {acct.server} [{mode}] "
            f"{acct.balance:.2f} {acct.currency} leverage 1:{acct.leverage}"
        )
    else:
        print("account  : not logged in")

    symbol = find_gold(args.symbol)
    info = describe(symbol)
    print("\nsymbol info")
    for k, v in info.items():
        print(f"  {k:22s} {v}")

    print("\nhistory")
    for tf in args.timeframes.split(","):
        tf = tf.strip()
        if not tf:
            continue
        try:
            rates = fetch(symbol, tf, args.bars)
        except RuntimeError as exc:
            print(f"  {tf:4s} FAILED: {exc}")
            continue
        path = save(symbol, tf, rates)
        print(
            f"  {tf:4s} {len(rates):>8d} bars  "
            f"{_fmt(rates['time'][0])} -> {_fmt(rates['time'][-1])}  -> {path}"
        )

    mt5.shutdown()


if __name__ == "__main__":
    main()
