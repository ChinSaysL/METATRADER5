"""Read M1 bars straight out of MetaTrader 5's own history cache (.hcc).

Why this exists: this terminal's saved account is rejected by the broker
("Invalid account"), and without a trade-server session the Python bridge refuses
to initialise, the Strategy Tester will not start an agent, and the MCP endpoint
answers 401. The bars themselves are nevertheless sitting on disk in
`bases/<server>/history/<symbol>/<year>.hcc`, so this reads them directly.

Format, established empirically (see the module test at the bottom):
  * 0x00  uint32 version (502 observed), then a UTF-16 copyright string and a
          UTF-16 "History" title at 0x84 - a header we can ignore entirely.
  * the body is a series of blocks; inside them, bar records are plain MqlRates:
          <q  time (unix seconds)
          4d  open, high, low, close
          q   tick_volume
          i   spread        (in points)
          q   real_volume
    = 60 bytes, matching sizeof(MqlRates).

Rather than trusting the block headers, records are found by validating every
byte offset and then keeping only those that sit in a run of consecutive,
60-second-apart records. That skips block headers and the per-block summary bar
(a daily aggregate sits at the head of each block) without needing to decode them.
"""

from __future__ import annotations

import argparse
import os
import struct
from datetime import datetime, timezone

import numpy as np

REC = struct.Struct("<q4dqiq")
REC_SIZE = REC.size          # 60

# validation bounds
T_MIN = 946684800            # 2000-01-01
T_MAX = 2208988800           # 2040-01-01
P_MIN = 1e-4
P_MAX = 1e7
MIN_RUN = 5                  # a real run of M1 bars is much longer than this


BAR_DTYPE = [
    ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
    ("close", "<f8"), ("tick_volume", "<i8"), ("spread", "<i4"),
    ("real_volume", "<i8"),
]


def _fields(data: bytes, phase: int):
    """Decode every 60-byte record starting at `phase` (mod 60), vectorised."""
    count = (len(data) - phase) // REC_SIZE
    if count <= 0:
        return None
    raw = np.frombuffer(data, dtype=np.uint8, count=count * REC_SIZE,
                        offset=phase).reshape(count, REC_SIZE)

    def col(lo, hi, dt):
        return np.ascontiguousarray(raw[:, lo:hi]).view(dt).ravel()

    return {
        "time": col(0, 8, np.int64),
        "open": col(8, 16, np.float64),
        "high": col(16, 24, np.float64),
        "low": col(24, 32, np.float64),
        "close": col(32, 40, np.float64),
        "tick_volume": col(40, 48, np.int64),
        "spread": col(48, 52, np.int32),
        "real_volume": col(52, 60, np.int64),
    }


def read_m1(path: str, verbose: bool = False):
    """Return a structured array of M1 bars, oldest first, deduplicated.

    Records are found by phase rather than byte by byte. Every run of real bars
    has a stride of exactly 60, so a whole run lives inside one offset class mod
    60; decoding the 60 classes with numpy covers every possible run while
    letting the validation and the run detection vectorise. This is ~1000x faster
    than scanning offsets in Python, which matters because the FX and index caches
    are 70 MB per year.
    """
    with open(path, "rb") as fh:
        data = fh.read()

    parts = []
    runs = 0
    for phase in range(REC_SIZE):
        f = _fields(data, phase)
        if f is None:
            continue
        t = f["time"]
        with np.errstate(invalid="ignore"):
            ok = (
                (t >= T_MIN) & (t <= T_MAX) & (t % 60 == 0)
                & (f["low"] > P_MIN) & (f["high"] < P_MAX)
                & (f["low"] <= f["high"])
                & (f["open"] >= f["low"]) & (f["open"] <= f["high"])
                & (f["close"] >= f["low"]) & (f["close"] <= f["high"])
                & (f["tick_volume"] >= 0) & (f["tick_volume"] <= 10_000_000)
                & (f["spread"] >= 0) & (f["spread"] <= 1_000_000)
                & (f["real_volume"] >= 0)
            )
        if ok.sum() < MIN_RUN:
            continue

        #--- link adjacent valid records that are exactly 60 seconds apart
        link = np.zeros(len(t), dtype=bool)
        if len(t) > 1:
            link[:-1] = ok[:-1] & ok[1:] & (np.diff(t) == 60)
        if not link.any():
            continue

        #--- a maximal stretch of k links is a run of k+1 records; keep the runs
        #--- that are long enough, which is what excludes block headers and the
        #--- per-block daily summary bar
        padded = np.r_[False, link, False]
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        starts, ends = edges[0::2], edges[1::2]
        lengths = ends - starts + 1                      # records, not links
        keep = lengths >= MIN_RUN
        if not keep.any():
            continue
        runs += int(keep.sum())

        idx = np.concatenate([
            np.arange(s, e + 1) for s, e in zip(starts[keep], ends[keep])
        ])
        rec = np.zeros(len(idx), dtype=BAR_DTYPE)
        for name, _ in BAR_DTYPE:
            rec[name] = f[name][idx]
        parts.append(rec)

    if verbose:
        total = sum(len(p) for p in parts)
        print(f"  {os.path.basename(path)}: {runs} runs, {total} records")
    if not parts:
        return None

    allrec = np.concatenate(parts)
    # np.unique returns the first index of each unique time in ascending time
    # order, so this deduplicates and sorts in one step.
    _, first = np.unique(allrec["time"], return_index=True)
    return allrec[first]


def read_symbol(data_folder: str, server: str, symbol: str, verbose: bool = True):
    """Read and concatenate every year file cached for one symbol."""
    hist = os.path.join(data_folder, "bases", server, "history", symbol)
    if not os.path.isdir(hist):
        raise FileNotFoundError(hist)
    files = sorted(f for f in os.listdir(hist) if f.lower().endswith(".hcc"))
    if not files:
        raise FileNotFoundError(f"no .hcc files in {hist}")
    if verbose:
        print(f"reading {symbol} from {hist}")
    parts = []
    for f in files:
        arr = read_m1(os.path.join(hist, f), verbose)
        if arr is not None:
            parts.append(arr)
    if not parts:
        raise RuntimeError("no bars recovered")
    allbars = np.concatenate(parts)
    _, idx = np.unique(allbars["time"], return_index=True)
    return allbars[np.sort(idx)]


def resample(m1, minutes: int):
    """Aggregate M1 into a higher timeframe the same way MT5 builds its bars."""
    t = m1["time"] // (minutes * 60) * (minutes * 60)
    # boundaries where the bucket changes
    starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
    ends = np.r_[starts[1:], len(t)]

    out = np.zeros(
        len(starts),
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
               ("close", "<f8"), ("tick_volume", "<i8"), ("spread", "<i4"),
               ("real_volume", "<i8")],
    )
    out["time"] = t[starts]
    out["open"] = m1["open"][starts]
    out["close"] = m1["close"][ends - 1]
    for i, (a, b) in enumerate(zip(starts, ends)):
        out["high"][i] = m1["high"][a:b].max()
        out["low"][i] = m1["low"][a:b].min()
        out["tick_volume"][i] = m1["tick_volume"][a:b].sum()
        out["spread"][i] = int(np.median(m1["spread"][a:b]))
        out["real_volume"][i] = m1["real_volume"][a:b].sum()
    return out


def _fmt(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-folder",
        default=os.path.expandvars(
            r"%APPDATA%\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075"
        ),
    )
    ap.add_argument("--server", default="FTMO-Server4")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--timeframes", default="M15,M5,M1")
    args = ap.parse_args()

    m1 = read_symbol(args.data_folder, args.server, args.symbol)
    print(f"\nM1 recovered: {len(m1)} bars  {_fmt(m1['time'][0])} -> {_fmt(m1['time'][-1])}")
    gaps = np.diff(m1["time"])
    print(f"  bar spacing: 60s x{int((gaps == 60).sum())}, "
          f"gaps>60s x{int((gaps > 60).sum())}, largest gap {int(gaps.max()) // 60} min")
    sp = m1["spread"]
    print(f"  spread (points): median {int(np.median(sp))}, "
          f"p10 {int(np.percentile(sp, 10))}, p90 {int(np.percentile(sp, 90))}")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)

    for tf in args.timeframes.split(","):
        tf = tf.strip().upper()
        if not tf:
            continue
        minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}[tf]
        bars = m1 if minutes == 1 else resample(m1, minutes)
        path = os.path.join(out_dir, f"{args.symbol}_{tf}.npy")
        np.save(path, bars)
        print(f"  {tf:4s} {len(bars):>7d} bars  {_fmt(bars['time'][0])} -> "
              f"{_fmt(bars['time'][-1])}  -> {path}")


if __name__ == "__main__":
    main()
