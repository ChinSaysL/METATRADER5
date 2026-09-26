"""Re-configure GoldSignalsAI_EA.mq5 for the 2.8-year re-optimisation.

This replaces the inline shell heredoc that corrupted the file: that version
called ``Path.write_text`` without an encoding, which on Windows defaults to
cp1252 and raised UnicodeEncodeError on a stray CJK character *after* the file
had already been opened for writing (truncating it to zero bytes).

Design rules here, so this can never repeat:
  * every substitution is an exact string and must match exactly once, else the
    script aborts BEFORE writing - no fuzzy matching, no guessing;
  * bytes are written as UTF-8 (no BOM) with the file's original CRLF endings.

Usage:  python tools/reconfigure_ea.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
EA = ROOT / "mt5" / "Experts" / "GoldSignalsAI" / "GoldSignalsAI_EA.mq5"

# (label, old, new)
SUBS: list[tuple[str, str, str]] = [
    #
    # --- 1. signal engine: sensitivity 5 -> 10, min strength 1 -> 2 -------------
    (
        "InpSensitivity 5.0 -> 10.0",
        "input double            InpSensitivity       = 5.0;    // Signal Sensitivity (pine line 30)",
        "input double            InpSensitivity       = 10.0;   // Signal Sensitivity (pine line 30)",
    ),
    (
        "InpMinSignalStrength 1 -> 2",
        "input int               InpMinSignalStrength = 1;      // Min AI signal strength 1-4 (1 = keep all)",
        "input int               InpMinSignalStrength = 2;      // Min AI signal strength 1-4 (1 = keep all)",
    ),
    #
    # --- 2. chop filter: rationale + Strong -> Light ----------------------------
    (
        "chop filter rationale",
        "//--- NOTE: pine ships this OFF at Medium. It defaults to ON at Strong here because\n"
        "//--- that is what the backtest supports: on XAUUSD M15 it lifts the profit factor\n"
        "//--- from 1.275 to 1.656 and halves the drawdown, and it holds up out of sample.\n"
        "//--- The indicator keeps the pine defaults so it can still be compared against\n"
        "//--- TradingView bar for bar. See README.md.",
        "//--- Re-selected on 2.8 years (2023-12 to 2026-09), NOT on the 7.5 months the\n"
        "//--- first version used. The chop filter helps at ANY strength here - off/Light/\n"
        "//--- Medium/Strong all score PF 1.60-1.74 - so Light is chosen as the least\n"
        "//--- aggressive setting that captures the effect. The indicator keeps the pine\n"
        "//--- defaults so it can still be compared against TradingView. See README.md.",
    ),
    (
        "InpFakeoutStrength STAI_STRONG -> STAI_LIGHT",
        "input ENUM_STAI_STRENGTH InpFakeoutStrength  = STAI_STRONG; // Filter Strength",
        "input ENUM_STAI_STRENGTH InpFakeoutStrength  = STAI_LIGHT; // Filter Strength",
    ),
    #
    # --- 3. H1 agreement: rationale + true -> false -----------------------------
    (
        "H1 agreement rationale",
        "//--- Best risk-adjusted result found on XAUUSD M15: profit factor 3.04 against\n"
        "//--- 2.29, drawdown 4.7% against 6.6%, 5 of 6 walk-forward folds improved, and in\n"
        "//--- the 78-start FTMO simulation it is strictly better at EVERY risk level -\n"
        "//--- both faster to target and lower drawdown. Defaults ON for that reason.\n"
        "//--- The honest caveat: it cuts the sample to 87 trades in 7.5 months, so the\n"
        "//--- evidence behind it is thinner than for the EMA200 filter (139 trades). Set\n"
        "//--- this false to fall back to EMA200 only. See README.md.",
        "//--- DEFAULTS OFF after re-testing on 2.8 years. On the 7.5-month sample this\n"
        "//--- looked like the best risk-adjusted filter (PF 3.04); on the full sample it\n"
        "//--- cuts the sample to 62 trades and only 5 of 8 folds are profitable, while\n"
        "//--- costing more than it adds. Kept switchable, but the evidence no longer\n"
        "//--- supports it. See README.md.",
    ),
    (
        "InpUseHtfFilter true -> false",
        "input bool              InpUseHtfFilter      = true;   // Require a slower chart to agree",
        "input bool              InpUseHtfFilter      = false;  // Require a slower chart to agree",
    ),
    #
    # --- 4. risk: rationale + 0.75% -> 0.50% ------------------------------------
    (
        "risk rationale",
        "//--- 0.75%: the only level whose bootstrap 95th-percentile drawdown (9.2%) stays\n"
        "//--- inside a 10% prop limit. Observed drawdown 3.6%, median reshuffle 5.4%.\n"
        "//--- 1.0% puts the tail at 12.6% and 1.5% at 18.5%, with 1.5% breaching on the\n"
        "//--- MEDIAN reshuffle - so this is the highest risk the evidence actually supports.",
        "//--- 0.50%: over 2.8 years and 142 simulated FTMO starts this is the only level\n"
        "//--- with ZERO breaches (worst drawdown 6.6%, bootstrap 95th percentile ~8.5%).\n"
        "//--- 0.75% fails 3 of 87, 1.0% fails 8 of 98, 1.5% fails 19 of 113. Median time\n"
        "//--- to a +10% target at this risk is 35 trading days.",
    ),
    (
        "InpRiskPercent 0.75 -> 0.50",
        "input double            InpRiskPercent       = 0.75;   // Risk per trade, % of balance",
        "input double            InpRiskPercent       = 0.50;   // Risk per trade, % of balance",
    ),
    #
    # --- 5. stop loss: 1.5 -> 2.5 x ATR -----------------------------------------
    (
        "InpSLAtrMult 1.5 -> 2.5",
        "input double            InpSLAtrMult          = 1.5;   // Initial SL = n * ATR",
        "input double            InpSLAtrMult          = 2.5;   // Initial SL = n * ATR",
    ),
]

# Values that must be present once the patch is applied, and the old values that
# must be gone. Checked against the patched text before anything is written.
EXPECT_PRESENT = [
    "InpSensitivity       = 10.0;",
    "InpMinSignalStrength = 2;",
    "InpFakeoutStrength  = STAI_LIGHT;",
    "InpUseHtfFilter      = false;",
    "InpRiskPercent       = 0.50;",
    "InpSLAtrMult          = 2.5;",
    # untouched, but must survive the patch
    "InpUseTsTrail        = true;",
    "InpMaxSpreadPoints   = 500;",
    "InpUseTrendFilter    = true;",
    "InpAtrLength         = 10;",
]
EXPECT_ABSENT = [
    "InpSensitivity       = 5.0;",
    "InpMinSignalStrength = 1;",
    "STAI_STRONG; // Filter Strength",
    "InpUseHtfFilter      = true;",
    "InpRiskPercent       = 0.75;",
    "InpSLAtrMult          = 1.5;",
]


def main() -> None:
    if not EA.exists():
        sys.exit(f"ABORT: not found: {EA}")

    raw = EA.read_bytes()
    crlf = b"\r\n" in raw
    s = raw.decode("utf-8")
    normalised = s.replace("\r\n", "\n") if crlf else s

    print(f"patching {EA.name}  ({len(raw):,} bytes, {'CRLF' if crlf else 'LF'})")

    #--- phase 1: match everything, write nothing ---------------------------
    staged = normalised
    for label, old, new in SUBS:
        n = staged.count(old)
        if n != 1:
            sys.exit(f"ABORT: {label}: matched {n} time(s), expected 1 - nothing written")
        staged = staged.replace(old, new)
        print(f"  matched  {label}")

    #--- phase 2: sanity-check the result -----------------------------------
    problems = []
    for needle in EXPECT_PRESENT:
        if needle not in staged:
            problems.append(f"missing after patch: {needle}")
    for needle in EXPECT_ABSENT:
        if needle in staged:
            problems.append(f"stale value still present: {needle}")
    if problems:
        sys.exit("ABORT: " + "; ".join(problems) + " - nothing written")

    if any(b > 127 for b in staged.encode("utf-8")):
        bad = sorted({c for c in staged if ord(c) > 127})
        sys.exit(f"ABORT: non-ASCII character(s) {bad!r} - nothing written")

    #--- phase 3: write, preserving line endings and UTF-8 ------------------
    out = staged.replace("\n", "\r\n" if crlf else "\n").encode("utf-8")
    EA.write_bytes(out)
    print(f"\nwrote {len(out):,} bytes ({len(out) - len(raw):+,} vs before)")
    print(f"{len(staged.splitlines())} lines, UTF-8, {'CRLF' if crlf else 'LF'}, pure ASCII")


if __name__ == "__main__":
    main()
