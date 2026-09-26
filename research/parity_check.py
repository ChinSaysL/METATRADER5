"""Prove the MQL5 port and the Python reference compute the same thing.

Reads the per-bar CSV the EA writes with InpDumpDiagnostics=true, re-runs the
Python engine over the OHLC columns FROM THAT SAME FILE, and compares every
column bar for bar. Taking the bars from the dump rather than from the terminal
removes any data-source mismatch, so a difference can only be a logic difference.

The EA dumps its warm-up bars too, so row 0 of the CSV is the first bar either
implementation ever saw - which is what makes the comparison meaningful at all,
since the engine is recursive.

Usage:
    python parity_check.py                          # finds the CSV automatically
    python parity_check.py --csv <path> --sensitivity 5 --atr-length 10
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys

import numpy as np

import engine
import reversal_exit

DEFAULT_GLOBS = [
    os.path.expandvars(
        r"%APPDATA%\MetaQuotes\Terminal\*\MQL5\Files\GoldSignalsAI_diag.csv"
    ),
    os.path.expandvars(
        r"%APPDATA%\MetaQuotes\Terminal\*\Tester\**\MQL5\Files\GoldSignalsAI_diag.csv"
    ),
]

# column -> (absolute tolerance, whether it is an integer flag)
TOLERANCES = {
    "atr": (1e-8, False),
    "target_factor": (1e-10, False),
    "perf_idx": (1e-8, False),
    "os": (0, True),
    "ts": (1e-8, False),
    "overlap_ratio": (1e-10, False),
    "overlap_rank": (1e-10, False),
    "body_atr": (1e-9, False),
    "signal_strength": (0, True),
    "raw_buy": (0, True),
    "raw_sell": (0, True),
    "passes_filter": (0, True),
    "buy": (0, True),
    "sell": (0, True),
    # the ported Take-Profit-Warning exit
    "condition": (0, True),
    "bear_exhaustion": (0, True),
    "bull_exhaustion": (0, True),
}

# columns that come from reversal_exit.py rather than engine.py
REVERSAL_COLS = ("condition", "bear_exhaustion", "bull_exhaustion")


def find_csv() -> str | None:
    for pattern in DEFAULT_GLOBS:
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return max(hits, key=os.path.getmtime)
    return None


def read_dump(path: str) -> dict:
    with open(path, "r", encoding="ascii", errors="replace") as fh:
        rows = [line.rstrip("\n").rstrip("\r").split(",") for line in fh if line.strip()]
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    cols: dict[str, list] = {h: [] for h in header}
    for r in body:
        if len(r) < len(header):
            continue
        for h, v in zip(header, r):
            cols[h].append(v)

    out: dict[str, np.ndarray] = {}
    for h, vals in cols.items():
        if h == "time":
            out[h] = np.array([int(v) for v in vals], dtype=np.int64)
        else:
            out[h] = np.array(
                [float(v) if v not in ("", "nan") else float("nan") for v in vals]
            )
    return out


def compare(name: str, mine: np.ndarray, theirs: np.ndarray, tol: float, is_flag: bool):
    """Return (n_diff, first_diff_index, worst_abs_diff)."""
    n = min(len(mine), len(theirs))
    a, b = mine[:n].astype(float), theirs[:n].astype(float)

    both_nan = np.isnan(a) & np.isnan(b)
    one_nan = np.isnan(a) ^ np.isnan(b)

    diff = np.zeros(n, dtype=bool)
    diff |= one_nan
    valid = ~np.isnan(a) & ~np.isnan(b)
    delta = np.zeros(n)
    delta[valid] = np.abs(a[valid] - b[valid])
    if is_flag:
        diff |= valid & (delta > 0)
    else:
        # scale-aware: ATR and price-level columns need a relative allowance
        scale = np.maximum(1.0, np.abs(b))
        diff |= valid & (delta > tol * scale)

    idx = int(np.argmax(diff)) if diff.any() else -1
    worst = float(delta[valid].max()) if valid.any() else 0.0
    return int(diff.sum()), idx, worst, int(both_nan.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--sensitivity", type=float, default=5.0)
    ap.add_argument("--atr-length", type=int, default=10)
    ap.add_argument("--filter-fakeouts", action="store_true")
    ap.add_argument("--fakeout-strength", default="Medium")
    ap.add_argument("--min-signal-strength", type=int, default=1)
    ap.add_argument("--reversal-sensitivity", default="Normal",
                    choices=["Early", "Normal", "Late"])
    ap.add_argument("--show", type=int, default=5, help="rows of context on a mismatch")
    args = ap.parse_args()

    path = args.csv or find_csv()
    if not path or not os.path.exists(path):
        print("no diagnostics CSV found.")
        print("Run: tools\\run_tester.ps1 -Mode Parity     (EA input InpDumpDiagnostics=true)")
        return 2

    print(f"dump   : {path}")
    d = read_dump(path)
    n = len(d["time"])
    print(f"bars   : {n}")
    if n < 300:
        print("WARNING: very few bars; parity on a short series proves little.")

    p = engine.EngineParams(
        sensitivity=args.sensitivity,
        atr_length=args.atr_length,
        filter_fakeouts=args.filter_fakeouts,
        fakeout_strength=args.fakeout_strength,
        min_signal_strength=args.min_signal_strength,
    )
    print(
        f"params : sensitivity={p.sensitivity} atr_length={p.atr_length} "
        f"filter={p.filter_fakeouts} strength={p.fakeout_strength} "
        f"min_strength={p.min_signal_strength}"
    )
    print(f"factors: {engine.factors_for(p.sensitivity)}")

    mine = engine.run(d["open"], d["high"], d["low"], d["close"], p)

    # the ported Take-Profit-Warning exit, so parity covers it as well
    rex = reversal_exit.exhaustion(d["high"], d["low"], d["close"], args.reversal_sensitivity)
    mine["condition"] = rex["condition"]
    mine["bear_exhaustion"] = rex["bearish_exhaustion"]
    mine["bull_exhaustion"] = rex["bullish_exhaustion"]

    print("\n%-16s %8s %10s %12s %10s" % ("column", "diffs", "first@", "worst |d|", "both-nan"))
    print("-" * 62)
    failures = []
    for col, (tol, is_flag) in TOLERANCES.items():
        if col not in d:
            print("%-16s %8s" % (col, "absent"))
            continue
        ndiff, first, worst, nboth = compare(col, mine[col], d[col], tol, is_flag)
        flag = "" if ndiff == 0 else "  <-- MISMATCH"
        print(
            "%-16s %8d %10s %12.3e %10d%s"
            % (col, ndiff, first if first >= 0 else "-", worst, nboth, flag)
        )
        if ndiff:
            failures.append((col, first))

    if not failures:
        print("\nPARITY OK: MQL5 and the Python reference agree on every column.")
        return 0

    print(f"\nPARITY FAILED on {len(failures)} column(s).")
    first_bar = min(f[1] for f in failures)
    lo = max(0, first_bar - args.show)
    hi = min(n, first_bar + args.show + 1)
    cols = [c for c, _ in failures]
    print(f"\ncontext around the first divergence (bar {first_bar}):")
    head = "bar      close    " + "".join(f"{c[:12]:>26}" for c in cols)
    print(head)
    print("-" * len(head))
    for i in range(lo, hi):
        row = f"{i:<8d} {d['close'][i]:<10.3f}"
        for c in cols:
            row += f"{mine[c][i]:>13.5f}{d[c][i]:>13.5f}"
        print(row + ("   <--" if i == first_bar else ""))
    print("\n(each column shows python then mql5)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
