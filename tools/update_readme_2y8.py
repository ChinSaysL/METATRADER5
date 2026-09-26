"""Bring README.md in line with the 2.8-year re-optimisation of the EA.

The EA's shipped defaults were re-selected on 65,000 M15 bars (2023-12-15 to
2026-09-25) and its in-code justification comments now say "See README.md".
Until this runs, the README still documents the superseded 7.5-month config
(sensitivity 5, chop Strong, SL 1.5xATR, H1 filter on, "Use 0.75% risk").

Same safety rules as tools/reconfigure_ea.py: every anchor is asserted to exist
exactly once and the file is only written if every check passes. Content is
matched and rewritten as one block per anchor - nothing is fuzzy-matched.

Usage:  python tools/update_readme_2y8.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

M15_HEADING = "### M15 backtest"

# --------------------------------------------------------------------------
# 1. new section, inserted immediately before "### M15 backtest"
# --------------------------------------------------------------------------
NEW_SECTION = """\
### Re-optimised on 2.8 years — this supersedes the 7.5-month numbers below

The tables from `### M15 backtest` down to `### Improvement experiments` are the January–August
2026 sample: 14,719 bars, one trending gold market. This re-test supersedes them where they
disagree. It runs on **65,000 M15 bars (2023-12-15 → 2026-09-25)** from the MetaQuotes-Demo feed
(`MQGOLD`, `research/data/MQGOLD_M15.npy`), same 0.44 USD/oz spread, searching **32 engine builds ×
96 trade configs = 3,072 evaluations** over 8 sequential folds, then re-picking parameters as it
went (anchored walk-forward).

| configuration | trades | net ($10k) | PF | max DD | profitable folds |
|---|---|---|---|---|---|
| shipped before this re-test (sens 5, Strong, SL 1.5, H1 on) | 396 | +5,468 | 1.295 | 27.5% | 4/8 |
| **re-selected (sens 10, Light, SL 2.5, H1 off)** | **138** | **+5,586** | **1.686** | **9.6%** | **8/8** |

The old configuration ranked **#583 of 2,340** qualifying configurations on fold consistency. Three
of the earlier conclusions turned out to be properties of the short sample, not of the strategy:

* **sensitivity 5 → 10** and **SL 1.5×ATR → 2.5×ATR**;
* **minimum signal strength 1 → 2**;
* **the H1 agreement filter is off by default** — on the long sample it cuts the book to 62 trades
  and only 5 of 8 folds stay profitable, at a cost higher than it adds.

The EMA200 regime filter survives the longer test, and the chop filter turns out to help at **any**
strength (off/Light/Medium/Strong all score PF 1.60–1.74), so **Light** is now shipped as the least
aggressive setting that captures the effect.

**Anchored walk-forward: +5,675 on $10,000, 4 of 6 folds profitable.** That is the number to
believe — it re-picks parameters at every step, so there is no hindsight — and it includes two
losing folds (−1,862 and −866). The aggregate is much less impressive than the fitted table above,
which is the point.

Caveats, plainly:

* the long sample is a **different feed** (`MQGOLD` from MetaQuotes-Demo, not the FTMO cache), so
  spread and tick structure are not identical to the 2026 sample;
* `research/validate_config.py` says the selected cell is **spiky on two axes**: sensitivity 10
  scores PF 1.686 with 8/8 folds, but 8.0 falls to 1.285 and 12.0 to 1.020; ATR 10 scores 1.686, but
  7 gives 1.206 and 14 gives 1.056. SL 2.5 is a broader optimum (2.0/3.0/4.0 all stay positive). Do
  not read 10 / 10 as precise values — treat it as a region that is sensitive to small changes;
* net profit is **not** higher than the old config. The gain is drawdown (27.5% → 9.6%) and
  consistency (4/8 → 8/8 profitable folds), which is what makes a prop-account run survivable.
"""

# --------------------------------------------------------------------------
# 2. the "Exact EA inputs" block
# --------------------------------------------------------------------------
EA_INPUTS_FIRST = "InpSensitivity        = 5.0"
EA_INPUTS_LAST = "InpDumpDiagnostics    = false"
NEW_EA_INPUTS = """\
InpSensitivity        = 10.0       InpUseAtrStop        = true
InpAtrLength          = 10         InpSLAtrMult         = 2.5
InpMinSignalStrength  = 2          InpTPAtrMult         = 0.0     (no fixed target)
InpFilterFakeouts     = true       InpUseTsTrail        = true
InpFakeoutStrength    = Light      InpUseReversalExit   = false
InpUseTrendFilter     = true       InpUseHtfFilter      = false
InpTrendEmaLength     = 200        InpHtfPeriod         = PERIOD_H1
                                   InpHtfWarmupBars     = 1500
InpCustomMaxChop      = 60   (unused unless Strength = Custom)
InpCustomMinQuality   = 0.0  (unused unless Strength = Custom)
InpCustomBodyOverride = 0.0  (unused unless Strength = Custom)
InpReverseOnOpposite  = true       InpUseRiskPercent   = true
InpWarmupBars         = 3000       InpRiskPercent      = 0.50  <- shipped default
InpMagic              = 63063001   InpFixedLot         = 0.10  (only if UseRiskPercent=false)
InpSlippagePoints     = 30         InpMaxSpreadPoints  = 500
InpDumpDiagnostics    = false"""

# --------------------------------------------------------------------------
# 3. the FTMO risk conclusion
# --------------------------------------------------------------------------
RISK_FIRST = "**Use 0.75% risk.**"
RISK_LAST = "37 passed, 37 blew up."
NEW_RISK = """\
**Use 0.50% risk.** On the 2.8-year sample `research/ftmo_challenge.py` starts **142 challenges**,
and 0.50% is the only level with **zero breaches**: 87 passed, 0 breached, 55 unfinished, worst
drawdown 6.6%, median **35 trading days** to +10%. Every higher level fails — 0.75% breaches 3 of 87
finished runs, 1.0% breaches 8 of 98, 1.5% breaches 19 of 113, with worst drawdowns of 10.3%, 10.3%
and 12.4%, all at or over a 10% limit."""

# --------------------------------------------------------------------------
# 4. the "adopted filters" block
# --------------------------------------------------------------------------
HTF_FIRST = "**Adopted: the EMA200 trend regime filter AND H1 engine agreement.**"
HTF_LAST = "87 trades is thinner evidence than 139."
NEW_HTF = """\
**Adopted: the EMA200 trend regime filter. The H1 agreement filter is now off by default.**

The EMA200 gate survives the 2.8-year re-test and is the one entry filter the longer sample
supports. It removes the counter-trend half of the signals, which on the short sample came out at
PF 0.95 — no edge at all. That falsification test is why it stays on.

H1 engine agreement (`InpUseHtfFilter`) was the best risk-adjusted filter on the 7.5-month sample
(PF 3.035, DD 4.7%) and is now **false**. On the full 2.8 years it cuts the book to 62 trades with
only 5 of 8 folds profitable, and it costs more than it adds. The code is unchanged and it remains
switchable, but the evidence behind it did not survive the longer test — the cleanest example in the
project of a filter that validates on one trending window and fails on a wider one."""


def find_unique(lines: list[str], needle: str) -> int:
    """Index of the one line containing `needle`; abort if not exactly one."""
    hits = [i for i, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        sys.exit(f"ABORT: anchor {needle!r} matched {len(hits)} lines, expected 1 - nothing written")
    return hits[0]


def main() -> None:
    if not README.exists():
        sys.exit(f"ABORT: not found: {README}")

    raw = README.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8-sig")
    if crlf:
        text = text.replace("\r\n", "\n")
    lines = text.split("\n")

    before_charset = {c for c in text if ord(c) > 127}

    def replace_block(first: str, last: str, block: str, label: str) -> None:
        i = find_unique(lines, first)
        j = find_unique(lines, last)
        if j <= i:
            sys.exit(f"ABORT: {label}: end anchor precedes start anchor - nothing written")
        if j - i > 60:
            sys.exit(f"ABORT: {label}: block spans {j - i} lines, refusing - nothing written")
        lines[i:j + 1] = block.split("\n")
        print(f"  replaced {label}  (was {j - i + 1} lines, now {len(block.splitlines())})")

    print(f"patching {README.name}  ({len(raw):,} bytes, {'CRLF' if crlf else 'LF'})")

    #--- insert the new section before the 7.5-month results --------------
    headline = [i for i, l in enumerate(lines) if l.strip() == M15_HEADING]
    if len(headline) != 1:
        sys.exit(f"ABORT: {M15_HEADING!r} heading matched {len(headline)} lines - nothing written")
    at = headline[0]
    lines[at:at] = NEW_SECTION.split("\n") + [""]
    print(f"  inserted new section  (before {M15_HEADING!r}, {len(NEW_SECTION.splitlines())} lines)")

    #--- the three stale blocks -------------------------------------------
    replace_block(EA_INPUTS_FIRST, EA_INPUTS_LAST, NEW_EA_INPUTS, "EA input defaults")
    replace_block(RISK_FIRST, RISK_LAST, NEW_RISK, "risk recommendation")
    replace_block(HTF_FIRST, HTF_LAST, NEW_HTF, "adopted filters")

    #--- checks ------------------------------------------------------------
    staged = "\n".join(lines)

    stale = [
        "InpSensitivity        = 5.0",
        "InpFakeoutStrength    = Strong",
        "InpSLAtrMult         = 1.5",
        "InpUseHtfFilter      = true",
        "InpRiskPercent      = 0.75",
        "**Use 0.75% risk.**",
        "Both default to on",
    ]
    problems = [f"stale text survived: {s}" for s in stale if s in staged]
    for needed in ("InpSensitivity        = 10.0", "InpFakeoutStrength    = Light",
                   "InpRiskPercent      = 0.50", "### Re-optimised on 2.8 years"):
        if needed not in staged:
            problems.append(f"missing after patch: {needed}")
    if problems:
        sys.exit("ABORT: " + "; ".join(problems) + " - nothing written")

    added = {c for c in staged if ord(c) > 127} - before_charset
    if added:
        sys.exit(f"ABORT: introduced non-ASCII chars {sorted(added)!r} - nothing written")

    #--- write ------------------------------------------------------------
    out_text = staged.replace("\n", "\r\n" if crlf else "\n")
    payload = (b"\xef\xbb\xbf" if bom else b"") + out_text.encode("utf-8")
    README.write_bytes(payload)
    print(f"\nwrote {len(payload):,} bytes ({len(payload) - len(raw):+,} vs before)")
    print(f"{len(lines)} lines, UTF-8{'-BOM' if bom else ''}, {'CRLF' if crlf else 'LF'}, "
          f"{'same' if not added else 'changed'} character set")


if __name__ == "__main__":
    main()
