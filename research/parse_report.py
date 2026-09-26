"""Pull the numbers out of a MetaTrader 5 Strategy Tester HTML report.

The tester writes a single .htm file whose header table is a flat list of
label/value cells. This extracts the ones worth quoting and prints them in the
same shape as backtest.py's summary, so the Python backtest and the Strategy
Tester can be compared line by line.
"""

from __future__ import annotations

import argparse
import glob
import html
import os
import re

WANTED = [
    "Total Net Profit",
    "Gross Profit",
    "Gross Loss",
    "Profit Factor",
    "Expected Payoff",
    "Recovery Factor",
    "Sharpe Ratio",
    "Balance Drawdown Absolute",
    "Balance Drawdown Maximal",
    "Equity Drawdown Maximal",
    "Total Trades",
    "Total Deals",
    "Short Trades (won %)",
    "Long Trades (won %)",
    "Profit Trades (% of total)",
    "Loss Trades (% of total)",
    "Largest profit trade",
    "Largest loss trade",
    "Average profit trade",
    "Average loss trade",
    "Maximum consecutive wins ($)",
    "Maximum consecutive losses ($)",
    "History Quality",
    "Bars",
    "Ticks",
    "Symbol",
    "Period",
]


def parse(path: str) -> dict:
    with open(path, "r", encoding="utf-16", errors="replace") as fh:
        text = fh.read()
    if "<" not in text:                      # some builds write utf-8
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()

    cells = [
        html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", text, re.S | re.I)
    ]

    out: dict[str, str] = {}
    for i, c in enumerate(cells):
        label = c.rstrip(":").strip()
        if label in WANTED and i + 1 < len(cells):
            value = cells[i + 1].strip()
            if value and label not in out:
                out[label] = value
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", default=None)
    args = ap.parse_args()

    path = args.report
    if not path:
        pattern = os.path.expandvars(
            r"%APPDATA%\MetaQuotes\Terminal\*\GoldSignalsAI_*.htm"
        )
        hits = glob.glob(pattern)
        if not hits:
            print("no tester report found; run tools\\run_tester.ps1 -Mode Backtest")
            return
        path = max(hits, key=os.path.getmtime)

    print(f"report : {path}\n")
    data = parse(path)
    if not data:
        print("could not parse any statistics out of that file")
        return
    width = max(len(k) for k in data)
    for k in WANTED:
        if k in data:
            print(f"  {k:<{width}}  {data[k]}")


if __name__ == "__main__":
    main()
