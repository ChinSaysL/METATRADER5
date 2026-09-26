"""Load XAUUSD bars from whatever source is available.

Three sources, in order of convenience:
  * the .npy cache written by mt5_data.py (needs a logged-in terminal);
  * any OHLC CSV - MT5 "Export Bars", a TradingView export, Dukascopy, ...;
  * the EA's own diagnostics dump, which carries its OHLC columns.

Everything downstream only needs (open, high, low, close, time).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_TIME_KEYS = ("time", "date", "datetime", "timestamp", "<DATE>", "gmt time")
_ALIASES = {
    "open": ("open", "<OPEN>", "o"),
    "high": ("high", "<HIGH>", "h"),
    "low": ("low", "<LOW>", "l"),
    "close": ("close", "<CLOSE>", "c", "price"),
}


def _parse_time(value: str) -> int:
    v = value.strip().strip('"')
    if v.isdigit() and len(v) >= 9:
        return int(v)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d",
        "%Y.%m.%d",
    ):
        try:
            return int(datetime.strptime(v, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise ValueError(f"unrecognised time format: {value!r}")


def from_npy(symbol: str = "XAUUSD", timeframe: str = "M15"):
    path = os.path.join(DATA_DIR, f"{symbol}_{timeframe}.npy")
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} - run: python mt5_data.py")
    r = np.load(path)
    return (
        r["open"].astype(float),
        r["high"].astype(float),
        r["low"].astype(float),
        r["close"].astype(float),
        r["time"].astype(np.int64),
    )


def from_csv(path: str):
    """Read any OHLC CSV. Separator, column order and case are auto-detected."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            sep = dialect.delimiter
        except csv.Error:
            sep = ","
        rows = [line.rstrip("\r\n").split(sep) for line in fh if line.strip()]

    header = [h.strip().strip('"').lower() for h in rows[0]]
    body = rows[1:]

    def find(names) -> int:
        for n in names:
            n = n.lower()
            if n in header:
                return header.index(n)
        for i, h in enumerate(header):
            for n in names:
                if n.lower() in h:
                    return i
        return -1

    idx = {k: find(v) for k, v in _ALIASES.items()}
    missing = [k for k, v in idx.items() if v < 0]
    if missing:
        raise ValueError(f"{path}: cannot find column(s) {missing} in header {header}")

    ti = find(_TIME_KEYS)
    # MT5 exports split date and time into two columns.
    ti2 = -1
    if ti >= 0 and ti + 1 < len(header) and header[ti + 1] in ("time", "<time>"):
        ti2 = ti + 1

    o, h, l, c, t = [], [], [], [], []
    for n, r in enumerate(body):
        if len(r) <= max(idx.values()):
            continue
        try:
            o.append(float(r[idx["open"]]))
            h.append(float(r[idx["high"]]))
            l.append(float(r[idx["low"]]))
            c.append(float(r[idx["close"]]))
        except ValueError:
            continue
        if ti < 0:
            t.append(n)
        elif ti2 >= 0:
            t.append(_parse_time(f"{r[ti]} {r[ti2]}"))
        else:
            t.append(_parse_time(r[ti]))

    arrs = (
        np.array(o, float),
        np.array(h, float),
        np.array(l, float),
        np.array(c, float),
        np.array(t, np.int64),
    )
    if len(arrs[0]) == 0:
        raise ValueError(f"{path}: no usable rows")
    if arrs[4][0] > arrs[4][-1]:          # newest-first exports
        arrs = tuple(a[::-1].copy() for a in arrs)
    return arrs


def load(source: str = "npy", symbol: str = "XAUUSD", timeframe: str = "M15", path: str | None = None):
    if source == "npy":
        return from_npy(symbol, timeframe)
    if source == "csv":
        if not path:
            raise ValueError("source=csv needs path=")
        return from_csv(path)
    raise ValueError(f"unknown source {source}")


def describe(times: np.ndarray) -> str:
    def f(ts):
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return f"{len(times)} bars  {f(times[0])} -> {f(times[-1])}"
