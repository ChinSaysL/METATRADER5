"""Pine Script v5 primitives, reimplemented exactly.

Every function here mirrors the initialisation and NaN behaviour of the
corresponding TradingView built-in. Library implementations (pandas-ta, talib)
seed their recursions differently, which silently breaks bar-for-bar parity, so
nothing here is delegated to them.

The one deliberate deviation from TradingView is documented on `true_range`.
"""

from __future__ import annotations

import math

import numpy as np

NAN = float("nan")


def _first_valid(src) -> int:
    """Pine's built-in ta.* functions ignore leading na and start counting at the
    first real value. `close - close[1]` is na on bar 0, so this matters for the
    perf_idx denominator."""
    for i, v in enumerate(src):
        if not math.isnan(v):
            return i
    return len(src)


def sma(src: np.ndarray, length: int) -> np.ndarray:
    """ta.sma - NaN until `length` bars are available."""
    out = np.full(len(src), NAN)
    if length <= 0:
        return out
    start = _first_valid(src)
    csum = 0.0
    for i in range(start, len(src)):
        csum += src[i]
        if i - start >= length:
            csum -= src[i - length]
        if i - start >= length - 1:
            out[i] = csum / length
    return out


def rma(src: np.ndarray, length: int) -> np.ndarray:
    """ta.rma - Wilder smoothing, seeded with the SMA of the first `length` bars."""
    out = np.full(len(src), NAN)
    alpha = 1.0 / length
    start = _first_valid(src)
    acc = 0.0
    for i in range(start, len(src)):
        k = i - start
        if k < length - 1:
            acc += src[i]
        elif k == length - 1:
            acc += src[i]
            out[i] = acc / length
        else:
            out[i] = alpha * src[i] + (1.0 - alpha) * out[i - 1]
    return out


def ema(src: np.ndarray, length: int) -> np.ndarray:
    """ta.ema - seeded with the SMA of the first `length` bars."""
    out = np.full(len(src), NAN)
    alpha = 2.0 / (length + 1.0)
    start = _first_valid(src)
    acc = 0.0
    for i in range(start, len(src)):
        k = i - start
        if k < length - 1:
            acc += src[i]
        elif k == length - 1:
            acc += src[i]
            out[i] = acc / length
        else:
            out[i] = alpha * src[i] + (1.0 - alpha) * out[i - 1]
    return out


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """ta.tr - max(h-l, |h-c[1]|, |l-c[1]|).

    On the first bar there is no previous close. TradingView's ta.atr feeds on
    ta.tr(true), which is NaN there; we use high-low instead (ta.tr's own
    handle_na=false behaviour) so the recursion starts one bar earlier. The
    effect is confined to the first few bars of the whole series, thousands of
    bars before any trade, and the MQL5 side uses the identical convention.
    """
    out = np.empty(len(high))
    out[0] = high[0] - low[0]
    for i in range(1, len(high)):
        pc = close[i - 1]
        out[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """ta.atr - RMA of the true range."""
    return rma(true_range(high, low, close), length)


def percentrank(src: np.ndarray, length: int) -> np.ndarray:
    """ta.percentrank - percentage of the previous `length` values that are <= current.

    NaN until `length` prior bars exist, which is what makes the chop filter
    abstain rather than block early in the series (pine line 1550).

    Leading NaN is skipped, as Pine's built-ins do. This matters: overlapRatio is
    na for its first 10 bars (it reaches back to high[i+1]), so on TradingView the
    first rank appears at bar 210, not bar 200, and the window is never polluted
    by na. The MQL5 CPmPercentRank only starts receiving values once the overlap
    window is complete, which gives the same alignment.
    """
    n = len(src)
    out = np.full(n, NAN)
    start = _first_valid(src)
    for i in range(start + length, n):
        count = 0
        for j in range(i - length, i):
            if src[j] <= src[i]:
                count += 1
        out[i] = count / length * 100.0
    return out


def percentile_linear_interpolation(values, percentile: float) -> float:
    """array.percentile_linear_interpolation - sorts, then interpolates.

    Returns NaN for an empty array, as Pine does.
    """
    if len(values) == 0:
        return NAN
    s = sorted(values)
    idx = (len(s) - 1) * percentile / 100.0
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(s[lo])
    return float(s[lo] + (idx - lo) * (s[hi] - s[lo]))


def supersmoother(src: np.ndarray, length: int) -> np.ndarray:
    """2-pole Butterworth (pine lines 599-607).

    Keeps the nz(ss[1], src[1]) fallbacks literally: the output is NaN for the
    first two bars and then heals itself, exactly as it does on TradingView.
    """
    a1 = math.exp(-math.sqrt(2.0) * math.pi / length)
    b1 = 2.0 * a1 * math.cos(math.sqrt(2.0) * math.pi / length)
    c3 = -(a1 ** 2)
    c2 = b1
    c1 = 1.0 - c2 - c3

    n = len(src)
    out = np.full(n, NAN)
    for i in range(n):
        p1 = out[i - 1] if i >= 1 else NAN
        if math.isnan(p1):
            p1 = src[i - 1] if i >= 1 else NAN
        p2 = out[i - 2] if i >= 2 else NAN
        if math.isnan(p2):
            p2 = src[i - 2] if i >= 2 else NAN
        out[i] = c1 * src[i] + c2 * p1 + c3 * p2
    return out


def tanh_pine(src: np.ndarray) -> np.ndarray:
    """pine line 1135: -1 + 2/(1+exp(-2x))."""
    return -1.0 + 2.0 / (1.0 + np.exp(-2.0 * src))


def normalize_deriv(src: np.ndarray, quadratic_mean_length: int) -> np.ndarray:
    """pine lines 1130-1133 - 2-bar derivative over its own quadratic mean."""
    n = len(src)
    deriv = np.full(n, 0.0)
    for i in range(2, n):
        deriv[i] = src[i] - src[i - 2]
    sq = deriv ** 2
    out = np.zeros(n)
    run = 0.0
    for i in range(n):
        run += sq[i]
        if i >= quadratic_mean_length:
            run -= sq[i - quadratic_mean_length]
        # math.sum is NaN until the window fills; nz() turns that into 0, and
        # Pine's x/0 is NaN, so an unfilled window contributes nothing.
        if i < quadratic_mean_length - 1:
            out[i] = 0.0
            continue
        qm = math.sqrt(run / quadratic_mean_length)
        out[i] = deriv[i] / qm if qm > 0 else 0.0
    return out


def dual_pole_filter(src: np.ndarray, lookback: float) -> np.ndarray:
    """pine lines 1138-1147."""
    omega = -99.0 * math.pi / (70.0 * lookback)
    alpha = math.exp(omega)
    beta = -(alpha ** 2)
    gamma = math.cos(omega) * 2.0 * alpha
    delta = 1.0 - gamma - beta

    n = len(src)
    out = np.zeros(n)
    for i in range(n):
        prev = src[i - 1] if i >= 1 else src[i]
        sliding = 0.5 * (src[i] + prev)
        f1 = out[i - 1] if i >= 1 else 0.0
        f2 = out[i - 2] if i >= 2 else 0.0
        out[i] = delta * sliding + gamma * f1 + beta * f2
    return out


def gaussian_kernel(src: np.ndarray, lookback: float, start_at_bar: int) -> np.ndarray:
    """jdehorty's Gaussian kernel regression, as imported by the pine script.

    yhat = sum(src[i] * exp(-i^2 / 2h^2)) / sum(weights), i = 0 .. start_at_bar+1
    """
    span = start_at_bar + 1
    weights = np.array(
        [math.exp(-(i ** 2) / (2.0 * lookback * lookback)) for i in range(span + 1)]
    )
    wsum = weights.sum()
    n = len(src)
    out = np.full(n, NAN)
    for i in range(n):
        acc = 0.0
        for k in range(span + 1):
            j = i - k
            acc += src[j if j >= 0 else 0] * weights[k]
        out[i] = acc / wsum
    return out


def crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """ta.crossover - a was <= b and is now > b."""
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """ta.crossunder - a was >= b and is now < b."""
    out = np.zeros(len(a), dtype=bool)
    out[1:] = (a[1:] < b[1:]) & (a[:-1] >= b[:-1])
    return out


def sign(x: float) -> float:
    """math.sign, with Pine's nz(na) -> 0 already applied by the caller."""
    if math.isnan(x):
        return NAN
    return 0.0 if x == 0 else (1.0 if x > 0 else -1.0)
