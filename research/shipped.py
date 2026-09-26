"""Single source of truth for the shipped configuration.

Every report script imports from here. Before this existed the same parameters
were hard-coded in five places (config_report, ftmo_challenge, final_report,
validate_config, experiments*) and they drifted: after the config changed, most
of them still reported on the old one.

`verify_against_ea()` re-reads the EA source and fails loudly if these values no
longer match its compiled defaults - so research and live cannot silently
diverge. Run `python shipped.py` to check.
"""

from __future__ import annotations

import pathlib
import re

import backtest
import engine

#--- data the research scripts default to
#--- M15. Chosen against the challenge objective rather than profit factor:
#--- of 1,897 M15 configurations scored by "reach +10% before breaching", this
#--- is the fastest with ZERO breaches across ~200 simulated challenge starts.
#--- It also beats the 11-year H1 config on PF (1.72 vs 1.39) and drawdown
#--- (8.5% vs 12.7%) with twice the trade frequency - at the cost of resting on
#--- 4.25 years of history instead of 11.
SYMBOL = "MQFULL"          # 99,999 M15 bars, 2022-06 -> 2026-09
TIMEFRAME = "M15"
HTF = None                 # higher-timeframe agreement costs more than it adds here

#--- costs: FTMO's real XAUUSD terms
SPREAD = 0.44

#--- the configuration itself
SENSITIVITY = 10.0
ATR_LENGTH = 10
FILTER_FAKEOUTS = True
FAKEOUT_STRENGTH = "Strong"
MIN_SIGNAL_STRENGTH = 1
SL_ATR_MULT = 2.5
TREND_EMA = 200
USE_REVERSAL_EXIT = False
RISK_PCT = 0.5
INITIAL_BALANCE = 10_000.0

#--- Structural fixes found by trade_anatomy.py. These are what turned the
#--- 11-year H1 walk-forward from -1,679 to +4,691; they are not knobs.
#---
#---   BE_TRIGGER_R  49% of trades that reached +1R still ended as full losses,
#---                 giving back 59% of the average peak, because the adaptive
#---                 line sits factor*ATR away and protects nothing early on.
#---   WT_AGREEMENT  only 46% of trades were in profit at bar 0 - many entries
#---                 chase an extended move. Require the WaveTrend oscillator
#---                 (already ported and parity-verified) to agree on direction.
BE_TRIGGER_R = 1.0
BE_OFFSET_R = 0.05
WT_AGREEMENT = True

#--- how each maps to an EA input, for the drift check
EA_INPUTS = {
    "InpSensitivity": lambda: SENSITIVITY,
    "InpAtrLength": lambda: ATR_LENGTH,
    "InpMinSignalStrength": lambda: MIN_SIGNAL_STRENGTH,
    "InpSLAtrMult": lambda: SL_ATR_MULT,
    "InpRiskPercent": lambda: RISK_PCT,
    "InpTrendEmaLength": lambda: TREND_EMA,
}

EA_PATH = (pathlib.Path(__file__).resolve().parent.parent
           / "mt5" / "Experts" / "GoldSignalsAI" / "GoldSignalsAI_EA.mq5")


def engine_params(**overrides) -> engine.EngineParams:
    kw = dict(
        sensitivity=SENSITIVITY,
        atr_length=ATR_LENGTH,
        filter_fakeouts=FILTER_FAKEOUTS,
        fakeout_strength=FAKEOUT_STRENGTH,
        min_signal_strength=MIN_SIGNAL_STRENGTH,
    )
    kw.update(overrides)
    return engine.EngineParams(**kw)


def trade_params(**overrides) -> backtest.TradeParams:
    kw = dict(
        sl_atr_mult=SL_ATR_MULT,
        use_exhaustion_exit=USE_REVERSAL_EXIT,
        trend_ema_length=TREND_EMA,
        risk_pct=RISK_PCT,
        initial_balance=INITIAL_BALANCE,
        be_trigger_r=BE_TRIGGER_R,
        be_offset_r=BE_OFFSET_R,
    )
    kw.update(overrides)
    return backtest.TradeParams(**kw)


def wt_masks(high, low, close):
    """WaveTrend entry agreement masks, or (None, None) when disabled."""
    if not WT_AGREEMENT:
        return None, None
    import reversal_exit
    sn = reversal_exit.exhaustion(high, low, close, "Normal")["series_normal"]
    return sn > 0, sn < 0


def costs(spread: float = SPREAD) -> backtest.Costs:
    return backtest.ftmo_costs(spread)


def describe() -> str:
    htf = HTF or "off"
    extra = (f" / BE {BE_TRIGGER_R}R" if BE_TRIGGER_R else "") + \
            (" / WT-agree" if WT_AGREEMENT else "")
    return (f"sens {SENSITIVITY} / atr {ATR_LENGTH} / "
            f"filter {'on:' + FAKEOUT_STRENGTH if FILTER_FAKEOUTS else 'off'} / "
            f"minstr {MIN_SIGNAL_STRENGTH} / sl {SL_ATR_MULT} / "
            f"ema {TREND_EMA} / htf {htf} / risk {RISK_PCT}%" + extra)


def verify_against_ea(path: pathlib.Path | None = None):
    """Compare these values with the EA's compiled defaults.

    Returns a list of mismatch strings; empty means research and live agree.
    """
    src = pathlib.Path(path or EA_PATH)
    if not src.exists():
        return [f"EA source not found: {src}"]
    text = src.read_text(encoding="utf-8", errors="replace")

    problems = []
    for name, getter in EA_INPUTS.items():
        m = re.search(rf"^input\s+\S+\s+{name}\s*=\s*([^;]+);", text, re.M)
        if not m:
            problems.append(f"{name}: not found in the EA source")
            continue
        raw = m.group(1).strip()
        try:
            ea_value = float(raw)
        except ValueError:
            problems.append(f"{name}: cannot parse default {raw!r}")
            continue
        mine = float(getter())
        if abs(ea_value - mine) > 1e-9:
            problems.append(f"{name}: EA has {ea_value:g}, shipped.py has {mine:g}")

    #--- enum / bool inputs, compared textually
    pairs = [
        ("InpFilterFakeouts", "true" if FILTER_FAKEOUTS else "false"),
        ("InpFakeoutStrength", "STAI_" + FAKEOUT_STRENGTH.upper()),
        ("InpUseReversalExit", "true" if USE_REVERSAL_EXIT else "false"),
        ("InpUseTrendFilter", "true" if TREND_EMA else "false"),
        ("InpUseHtfFilter", "true" if HTF else "false"),
    ]
    for name, expect in pairs:
        m = re.search(rf"^input\s+\S+\s+{name}\s*=\s*([^;]+);", text, re.M)
        if not m:
            problems.append(f"{name}: not found in the EA source")
            continue
        got = m.group(1).strip()
        if got != expect:
            problems.append(f"{name}: EA has {got}, shipped.py expects {expect}")
    return problems


def tester_inputs() -> str:
    """The full config as MT5 [TesterInputs] lines.

    Every input is emitted explicitly because the Strategy Tester PERSISTS input
    values per expert between runs: anything omitted silently reuses the last
    saved value rather than the newly compiled default. Omitting them once
    caused a whole H1 run to execute the previous config.
    """
    #--- MT5 [TesterInputs] parses enums NUMERICALLY. A symbolic name like
    #--- "PERIOD_H4" is not rejected - it silently resolves to 0
    #--- (PERIOD_CURRENT), which made a run abort with "incorrect input
    #--- parameters". Emit the integers.
    fs = {"Light": 0, "Medium": 1, "Strong": 2, "Custom": 3}[FAKEOUT_STRENGTH]
    htf_period = {"M15": 15, "M30": 30, "H1": 16385, "H2": 16386,
                  "H4": 16388, "D1": 16408}.get(HTF or "H4", 16388)
    lines = [
        f"InpSensitivity={SENSITIVITY}",
        f"InpAtrLength={ATR_LENGTH}",
        f"InpMinSignalStrength={MIN_SIGNAL_STRENGTH}",
        f"InpFilterFakeouts={'true' if FILTER_FAKEOUTS else 'false'}",
        f"InpFakeoutStrength={fs}",
        f"InpUseTrendFilter={'true' if TREND_EMA else 'false'}",
        f"InpTrendEmaLength={TREND_EMA or 200}",
        f"InpUseHtfFilter={'true' if HTF else 'false'}",
        f"InpHtfPeriod={htf_period}",
        f"InpBreakevenAtR={BE_TRIGGER_R}",
        f"InpBreakevenOffsetR={BE_OFFSET_R}",
        f"InpUseWaveTrendEntry={'true' if WT_AGREEMENT else 'false'}",
        f"InpSLAtrMult={SL_ATR_MULT}",
        f"InpUseReversalExit={'true' if USE_REVERSAL_EXIT else 'false'}",
        f"InpRiskPercent={RISK_PCT}",
        "InpUseNewsFilter=false",
    ]
    return chr(10).join(lines)


if __name__ == "__main__":
    import sys
    if "--tester-inputs" in sys.argv:
        print(tester_inputs())
        raise SystemExit(0)
    print("shipped config : " + describe())
    print("EA source      : " + str(EA_PATH))
    issues = verify_against_ea()
    if not issues:
        print("\nOK - research config matches the EA's compiled defaults.")
    else:
        print("\nMISMATCH between research and the EA:")
        for i in issues:
            print("  " + i)
        raise SystemExit(1)
