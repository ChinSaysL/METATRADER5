"""Port of the script's Take-Profit Warning, used here as an exit rule.

Two independent mechanisms from the pine file, both fully in-file:

  * the Mean Reversion Channel (lines 499-719) - a SuperSmoother mean with bands
    at pi*1.0 and pi*2.415 mean-ranges, and the `condition` code saying how deep
    into the reversal zone price is;
  * WaveTrend 3D (lines 999-1304) - three normalised-derivative oscillators plus a
    Gaussian-kernel trend check, producing "trigger waves".

The exhaustion switch is lines 1649-1657, minus the Smart Trail and Trend Catcher
flips: those come from the closed-source library and were deliberately not
reconstructed, so the warning fires somewhat less often here than on TradingView.
That is a coverage difference, not a logic difference.

Two observed behaviours that are faithful to the original and are NOT bugs:

  * `meanrange` goes negative for roughly the first 50 bars of any series. The
    SuperSmoother has c1 ~ 0.004 against c2 ~ 1.91, so seeding it through Pine's
    nz(ss[1], src[1]) fallback with raw true-range values produces a long
    overshoot before it settles. TradingView does the same thing. It is confined
    to the start of the series, far inside every warm-up window used here.
  * `condition` is almost always +/-4 or +/-5 ("near"/"above"/"below" mean). The
    outer reversal zone sits pi*2.415 ~ 7.6 mean-ranges from the mean and is
    genuinely rarely touched - which is exactly what the script's own comment at
    lines 1624-1627 says. With sensitivity "Normal" the exit is therefore carried
    mostly by the momentum zero-cross and the trigger waves, not by the zone.
"""

from __future__ import annotations

import math

import numpy as np

import pine_math as pm

NAN = float("nan")

# Mean reversion channel inputs (pine lines 500-524)
MRC_LENGTH = 100
MRC_INNER_MULT = 1.0
MRC_OUTER_MULT = 2.415
MRC_GRADSIZE = 0.5

# WaveTrend 3D inputs (pine lines 1006-1024)
OSC_LOOKBACK = 20
QUADRATIC_MEAN_LENGTH = 50
F_LENGTH, F_SMOOTHING = 0.75, 0.45
N_LENGTH, N_SMOOTHING = 1.00, 1.00
S_LENGTH, S_SMOOTHING = 1.75, 2.50

DIV_THRESHOLD = 30    # line 1027
SIZE_PERCENT = 40     # line 1028
KERNEL_LOOKBACK = 6   # yhat0 = kernels.gaussian(seriesEmphasis, 6, 6), line 1288
KERNEL_START_AT_BAR = 6


def mean_reversion_channel(high, low, close):
    """pine get_mrc(), lines 657-719. source = hlc3, type = SuperSmoother."""
    n = len(close)
    source = (high + low + close) / 3.0
    meanline = pm.supersmoother(source, MRC_LENGTH)
    meanrange = pm.supersmoother(pm.true_range(high, low, close), MRC_LENGTH)

    mult = math.pi * MRC_INNER_MULT
    mult2 = math.pi * MRC_OUTER_MULT

    upband1 = meanline + meanrange * mult
    loband1 = meanline - meanrange * mult
    upband2 = meanline + meanrange * mult2
    loband2 = meanline - meanrange * mult2

    condition = np.zeros(n, dtype=np.int8)
    for i in range(n):
        if math.isnan(meanline[i]):
            continue
        if close[i] > meanline[i]:
            up2_1 = upband2[i] + meanrange[i] * MRC_GRADSIZE * 4
            up2_9 = upband2[i] + meanrange[i] * MRC_GRADSIZE * -4
            if up2_9 <= high[i] < upband2[i]:
                condition[i] = 1
            elif upband2[i] <= high[i] < up2_1:
                condition[i] = 2
            elif high[i] >= up2_1:
                condition[i] = 3
            elif close[i] <= meanline[i] + meanrange[i]:
                condition[i] = 4
            else:
                condition[i] = 5
        elif close[i] < meanline[i]:
            lo2_1 = loband2[i] - meanrange[i] * MRC_GRADSIZE * 4
            lo2_9 = loband2[i] - meanrange[i] * MRC_GRADSIZE * -4
            if lo2_9 >= low[i] > loband2[i]:
                condition[i] = -1
            elif loband2[i] >= low[i] > lo2_1:
                condition[i] = -2
            elif low[i] <= lo2_1:
                condition[i] = -3
            # The bearish mirror of the bullish "near mean" test; the original
            # library had the sign wrong here and the user already fixed it
            # in-file (pine lines 708-714).
            elif close[i] >= meanline[i] - meanrange[i]:
                condition[i] = -4
            else:
                condition[i] = -5

    return {
        "meanline": meanline,
        "meanrange": meanrange,
        "upband1": upband1,
        "loband1": loband1,
        "upband2": upband2,
        "loband2": loband2,
        "condition": condition,
    }


def _oscillator(src, smoothing_frequency):
    """pine getOscillator(), line 1149."""
    return pm.dual_pole_filter(
        pm.tanh_pine(pm.normalize_deriv(src, QUADRATIC_MEAN_LENGTH)),
        smoothing_frequency,
    )


def _valuewhen(cond, src, occurrence):
    """ta.valuewhen - occurrence 0 is the most recent hit, 1 the one before it."""
    n = len(src)
    out = np.full(n, NAN)
    hits: list[float] = []
    for i in range(n):
        if cond[i]:
            hits.append(src[i])
        if len(hits) > occurrence:
            out[i] = hits[-(occurrence + 1)]
    return out


def _barssince(cond):
    """ta.barssince - NaN until the condition has been true at least once."""
    n = len(cond)
    out = np.full(n, NAN)
    last = -1
    for i in range(n):
        if cond[i]:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def wavetrend3d(close):
    """pine lines 1158-1304. speedToEmphasize = 'Normal', all offsets 0."""
    series_fast = F_LENGTH * _oscillator(close, F_SMOOTHING * OSC_LOOKBACK)
    series_normal = N_LENGTH * _oscillator(close, N_SMOOTHING * OSC_LOOKBACK)
    series_slow = S_LENGTH * _oscillator(close, S_SMOOTHING * OSC_LOOKBACK)

    zero = np.zeros(len(close))
    bearish_cross = pm.crossunder(series_fast, series_normal) & (series_normal > 0)
    bullish_cross = pm.crossover(series_fast, series_normal) & (series_normal < 0)
    osc_turned_bear = pm.crossunder(series_normal, zero)
    osc_turned_bull = pm.crossover(series_normal, zero)

    # seriesEmphasis = seriesNormal, then the medium-fit Gaussian kernel.
    yhat0 = pm.gaussian_kernel(series_normal, KERNEL_LOOKBACK, KERNEL_START_AT_BAR)
    is_bearish_kernel_trend = yhat0 < series_slow
    is_bullish_kernel_trend = yhat0 > series_slow

    factor = SIZE_PERCENT / 100.0
    last_bear = _valuewhen(bearish_cross, series_normal, 1)
    last_bull = _valuewhen(bullish_cross, series_normal, 1)
    smaller_bear = bearish_cross & (series_normal < last_bear * factor)
    smaller_bull = bullish_cross & (series_normal > last_bull * factor)

    shift_bear = np.zeros(len(close), dtype=bool)
    shift_bull = np.zeros(len(close), dtype=bool)
    shift_bear[1:] = bearish_cross[:-1]
    shift_bull[1:] = bullish_cross[:-1]
    bs_bear = _barssince(shift_bear)
    bs_bull = _barssince(shift_bull)
    in_bear_div = bs_bear < DIV_THRESHOLD   # NaN < x is False, matching Pine
    in_bull_div = bs_bull < DIV_THRESHOLD

    return {
        "series_fast": series_fast,
        "series_normal": series_normal,
        "series_slow": series_slow,
        "bearish_cross": bearish_cross,
        "bullish_cross": bullish_cross,
        "osc_turned_bear": osc_turned_bear,
        "osc_turned_bull": osc_turned_bull,
        "bear_trigger_wave": smaller_bear & in_bear_div & is_bearish_kernel_trend,
        "bull_trigger_wave": smaller_bull & in_bull_div & is_bullish_kernel_trend,
    }


def exhaustion(high, low, close, sensitivity: str = "Normal"):
    """pine lines 1619-1657, without the closed-source trail/catcher flips."""
    mrc = mean_reversion_channel(high, low, close)
    wt = wavetrend3d(close)

    depth = {"Early": 1, "Normal": 2, "Late": 3}[sensitivity]
    cond = mrc["condition"]
    in_upper = (cond >= depth) & (cond <= 3)
    in_lower = (cond <= -depth) & (cond >= -3)

    touched_upper_inner = high >= mrc["upband1"]
    touched_lower_inner = low <= mrc["loband1"]

    if sensitivity == "Early":
        bear = (
            touched_upper_inner
            | in_upper
            | wt["bearish_cross"]
            | wt["bear_trigger_wave"]
            | wt["osc_turned_bear"]
        )
        bull = (
            touched_lower_inner
            | in_lower
            | wt["bullish_cross"]
            | wt["bull_trigger_wave"]
            | wt["osc_turned_bull"]
        )
    elif sensitivity == "Late":
        bear = in_upper & wt["bear_trigger_wave"]
        bull = in_lower & wt["bull_trigger_wave"]
    else:
        bear = in_upper | wt["bear_trigger_wave"] | wt["osc_turned_bear"]
        bull = in_lower | wt["bull_trigger_wave"] | wt["osc_turned_bull"]

    return {
        "bearish_exhaustion": bear,
        "bullish_exhaustion": bull,
        "condition": cond,
        "upband1": mrc["upband1"],
        "loband1": mrc["loband1"],
        "series_normal": wt["series_normal"],
        "series_slow": wt["series_slow"],
    }
