"""Correct README.md after the Strategy Tester was made to actually run.

The README claimed the tester "never starts a local agent" and "cannot start
without a server session". Neither was true: tools/run_tester.ps1 wrote its .ini
with PowerShell 5.1's `Out-File -Encoding utf8`, which prepends a UTF-8 BOM, so
MT5 saw "\ufeff[Tester]" instead of a section header and silently ignored the
whole /config, starting with the last profile instead.

Verified 2026-09-26: XAUUSD M15, 2026.07.01 .. 08.01, -TickModel 2 -> 2,013 bars,
4 orders, 0 rejects, report written, parse_report.py reads it.

Same safety rules as the other patch scripts: exact match, asserted unique, and
nothing is written unless every check passes.

Usage:  python tools/update_readme_tester.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

SUBS: list[tuple[str, str, str]] = [
    (
        "tester: how-to note",
        '`-Mode Backtest` uses 1-minute OHLC; pass `-TickModel 4` for "every tick based on real ticks" on\n'
        "the finalists. Note `run_tester.ps1` **closes any running terminal**, because MT5 ignores\n"
        "`/config` while an instance of the same installation is up.",
        '`-Mode Backtest` uses 1-minute OHLC; pass `-TickModel 4` for "every tick based on real ticks" on\n'
        "the finalists. Note `run_tester.ps1` **closes any running terminal**, because MT5 ignores\n"
        "`/config` while an instance of the same installation is up.\n"
        "\n"
        "The tester does work, with one trap worth knowing: the `.ini` must be written **without a\n"
        "UTF-8 BOM**. PowerShell 5.1's `Out-File -Encoding utf8` prepends `EF BB BF`, which turns the\n"
        "first line into `\\ufeff[Tester]` — not a section header — so MT5 ignores the entire `/config`\n"
        "and silently starts with the last profile instead. Nothing is logged; it merely looks like the\n"
        "tester refused to start. `run_tester.ps1` now uses `[IO.File]::WriteAllText` with\n"
        "`UTF8Encoding($false)`.\n"
        "\n"
        "Verified end to end on 2026-09-26 (XAUUSD M15, 2026.07.01 → 08.01, `-TickModel 2`): 2,013 bars,\n"
        "history quality 100%, 4 orders sent, 0 rejects, report written and `parse_report.py` reads it.\n"
        "That exercises the whole chain — .ini → tester agent → `CTrade` order send → stop-loss attach\n"
        "→ SL trigger → deal → HTML report → parser.",
    ),
    (
        "tester: 'has not been run' caveat",
        "* The Strategy Tester has **not** been run (it cannot start without a server session), so these\n"
        "  numbers come from the Python backtester, not from tick-level simulation. The parity check proves\n"
        "  the *signals* match the EA exactly; fills, slippage and swap are modelled, not simulated.",
        "* These numbers come from the Python backtester, not from tick-level simulation. The Strategy\n"
        "  Tester now runs (see *Strategy Tester* above), but so far only as a plumbing check on a\n"
        "  one-month range — the headline results here have **not** been reproduced under tick-level\n"
        "  fills. The parity check proves the *signals* match the EA exactly; fills, slippage and swap\n"
        "  are modelled, not simulated.",
    ),
    (
        "tester: dead-account table row",
        "| Strategy Tester via `/config:` | starts, enumerates cloud agents, never starts a local agent |",
        "| Strategy Tester via `/config:` | **works** — the earlier failure was a UTF-8 BOM in the generated `.ini`, not the account |",
    ),
    (
        "tester: 'needs a working account' paragraph",
        "`tools/run_tester.ps1` is kept for when there is a working account — it needs no changes, the\n"
        "Strategy Tester simply refuses to run without a session.",
        "`tools/run_tester.ps1` works — it needed a BOM-free `.ini`, not a particular account. See the\n"
        "*Strategy Tester* section above for the verified run.",
    ),
]


def main() -> None:
    raw = README.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        sys.exit("ABORT: README unexpectedly has a BOM")
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    before_charset = {c for c in text if ord(c) > 127}

    print(f"patching {README.name}  ({len(raw):,} bytes, {'CRLF' if crlf else 'LF'})")
    staged = text
    for label, old, new in SUBS:
        n = staged.count(old)
        if n != 1:
            sys.exit(f"ABORT: {label}: matched {n} time(s), expected 1 - nothing written")
        staged = staged.replace(old, new)
        print(f"  matched  {label}")

    stale = [
        "starts, enumerates cloud agents",
        "has **not** been run (it cannot start without a server session)",
        "Strategy Tester simply refuses to run without a session",
    ]
    problems = [f"stale text survived: {s}" for s in stale if s in staged]
    if "without a\nUTF-8 BOM" not in staged:
        problems.append("BOM explanation missing")
    if problems:
        sys.exit("ABORT: " + "; ".join(problems) + " - nothing written")

    added = {c for c in staged if ord(c) > 127} - before_charset
    if added:
        sys.exit(f"ABORT: introduced non-ASCII chars {sorted(added)!r} - nothing written")

    payload = staged.replace("\n", "\r\n" if crlf else "\n").encode("utf-8")
    README.write_bytes(payload)
    print(f"\nwrote {len(payload):,} bytes ({len(payload) - len(raw):+,} vs before)")


if __name__ == "__main__":
    main()
