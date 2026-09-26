"""SuperTrend AI (k-means clustering) - the actual signal engine of the pine script.

Ports pinescriptbot.pine lines 1310-1491 (engine) and 1509-1606 (the chop /
fakeout filter and the signal conditions) statement for statement, including the
parts that only matter because of Pine's evaluation order:

  * `trend` is updated from the PREVIOUS bar's upper/lower, but the final
    SuperTrend at the bottom updates upper/lower BEFORE `os` - the two blocks are
    genuinely ordered differently in the source (lines 1392-1394 vs 1482-1484).
  * `perf` uses the PREVIOUS bar's `output`.
  * nz(math.sign(na)) -> 0, so `perf` cannot move until ATR is warm.
  * an empty k-means cluster produces a NaN centroid which then stays empty for
    good, because every distance to it is NaN and Pine's array.min ignores NaN.

Deliberate deviation: Pine gates the clustering with
`if last_bar_index - bar_index <= maxData` (2500), a TradingView execution-budget
guard rather than a strategy rule - on a chart it means only the most recent 2500
bars ever produce a signal. max_data=None (the default here) drops that gate so a
long backtest produces signals throughout; pass max_data=2500 to reproduce a
TradingView chart exactly. `perf` itself is never gated in Pine either way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import pine_math as pm

NAN = float("nan")

# Pine hardcoded constants (lines 1328-1340, 1514-1515)
STEP = 0.5
PERF_ALPHA = 10.0
MAX_ITER = 250
CLUSTER_BEST = 2          # fromCluster = 'Best' -> index 2 (line 1461)
CHOP_LOOKBACK = 10
CHOP_RANK_LENGTH = 200


@dataclass
class EngineParams:
    """Every field maps to a pine input; defaults are the pine defaults."""

    sensitivity: float = 5.0           # line 30
    atr_length: int = 10               # line 31
    filter_fakeouts: bool = False      # line 55
    fakeout_strength: str = "Medium"   # line 56
    custom_max_chop: float = 60.0      # line 57
    custom_min_quality: float = 0.0    # line 58
    custom_body_override: float = 0.0  # line 59
    max_data: int = 0                  # 0 = no gate; see module docstring
    min_signal_strength: int = 1       # EA-only: 1 keeps every signal (pine behaviour)


def factors_for(sensitivity: float) -> list:
    """pine lines 1314-1315, 1378-1379."""
    min_mult = max(sensitivity - 4.0, 1.0)
    max_mult = min(sensitivity, 26.0)
    if min_mult > max_mult:
        raise ValueError("Minimum factor is greater than maximum factor in the range")
    count = int((max_mult - min_mult) / STEP)  # Pine's for-to loop is inclusive
    return [min_mult + i * STEP for i in range(count + 1)]


def _kmeans_target(perf: list, factors: list):
    """One bar of the pine k-means block (lines 1405-1473).

    Returns (mean factor of the best cluster, mean perf of the best cluster);
    either may be NaN when that cluster came out empty.
    """
    centroids = [
        pm.percentile_linear_interpolation(perf, 25),
        pm.percentile_linear_interpolation(perf, 50),
        pm.percentile_linear_interpolation(perf, 75),
    ]

    perf_clusters = [[], [], []]
    factor_clusters = [[], [], []]

    for _ in range(MAX_ITER + 1):  # Pine's `for _ = 0 to maxIter` runs 251 times
        perf_clusters = [[], [], []]
        factor_clusters = [[], [], []]

        for i, value in enumerate(perf):
            best_idx = -1
            best_dist = NAN
            for ci in range(3):
                c = centroids[ci]
                if math.isnan(c):
                    continue  # a NaN distance never wins array.min()
                d = abs(value - c)
                if best_idx < 0 or d < best_dist:
                    best_idx = ci
                    best_dist = d
            if best_idx < 0:
                continue
            perf_clusters[best_idx].append(value)
            factor_clusters[best_idx].append(factors[i])

        new_centroids = [
            (sum(cl) / len(cl)) if len(cl) else NAN for cl in perf_clusters
        ]
        # NaN == NaN is False, so a collapsed centroid never lets the loop break,
        # exactly as in Pine.
        if all(new_centroids[k] == centroids[k] for k in range(3)):
            break
        centroids = new_centroids

    best_factors = factor_clusters[CLUSTER_BEST]
    best_perf = perf_clusters[CLUSTER_BEST]
    return (
        sum(best_factors) / len(best_factors) if best_factors else NAN,
        sum(best_perf) / len(best_perf) if best_perf else NAN,
    )


def chop_thresholds(p: EngineParams):
    """pine lines 1537-1547. Medium and Strong share the same chop level; only the
    strong-candle override differs between them."""
    if p.fakeout_strength == "Custom":
        max_overlap = float(p.custom_max_chop)
        min_quality = p.custom_min_quality
        body_override = 1e10 if p.custom_body_override <= 0 else p.custom_body_override
    else:
        max_overlap = 85.0 if p.fakeout_strength == "Light" else 80.0
        min_quality = 0.3
        body_override = {"Light": 0.6, "Medium": 0.7}.get(p.fakeout_strength, 1e10)
    return max_overlap, min_quality, body_override


def run(open_, high, low, close, params=None):
    """Compute the engine over a whole series, one bar at a time.

    Thin wrapper over run_core + apply_signals. The split matters for parameter
    sweeps: only `sensitivity` and `atr_length` affect the expensive part (9
    SuperTrends plus k-means on every bar), while the filter settings and the
    strength floor are pure post-processing of arrays already computed.
    """
    p = params or EngineParams()
    out = run_core(open_, high, low, close, p.sensitivity, p.atr_length, p.max_data)
    apply_signals(out, p)
    return out


def run_core(open_, high, low, close, sensitivity=5.0, atr_length=10, max_data=0):
    """The expensive part: everything that does not depend on the filter inputs."""
    p = EngineParams(sensitivity=sensitivity, atr_length=atr_length, max_data=max_data)
    n = len(close)
    factors = factors_for(p.sensitivity)
    nf = len(factors)

    atr = pm.atr(high, low, close, p.atr_length)

    # perf_idx denominator: ta.ema(|close - close[1]|, 10). NaN on bar 0, which
    # Pine's ta.ema skips (line 1466).
    abs_chg = np.full(n, NAN)
    abs_chg[1:] = np.abs(np.diff(close))
    den = pm.ema(abs_chg, int(PERF_ALPHA))

    hl2 = (high + low) / 2.0

    # --- multi-SuperTrend state, one entry per factor ---
    st_upper = [hl2[0]] * nf
    st_lower = [hl2[0]] * nf
    st_trend = [0] * nf
    st_perf = [0.0] * nf
    st_output = [NAN] * nf
    perf_w = 2.0 / (PERF_ALPHA + 1.0)

    # --- final SuperTrend state ---
    f_upper = hl2[0]
    f_lower = hl2[0]
    f_os = 0
    target_factor = NAN
    perf_idx = NAN

    out = {}
    for key in ("atr", "target_factor", "perf_idx", "ts", "overlap_ratio", "body_atr"):
        out[key] = np.full(n, NAN)
    out["os"] = np.zeros(n, dtype=np.int8)
    out["signal_strength"] = np.zeros(n, dtype=np.int8)

    for i in range(n):
        c = close[i]
        pc = close[i - 1] if i >= 1 else NAN
        a = atr[i]

        # ---- multi-SuperTrend + performance scores (pine lines 1386-1400) ----
        for k in range(nf):
            f = factors[k]
            up = hl2[i] + a * f
            dn = hl2[i] - a * f

            if c > st_upper[k]:
                st_trend[k] = 1
            elif c < st_lower[k]:
                st_trend[k] = 0

            st_upper[k] = min(up, st_upper[k]) if pc < st_upper[k] else up
            st_lower[k] = max(dn, st_lower[k]) if pc > st_lower[k] else dn

            diff = pm.sign(pc - st_output[k])
            if math.isnan(diff):
                diff = 0.0
            chg = c - pc
            if math.isnan(chg):
                chg = 0.0
            st_perf[k] += perf_w * (chg * diff - st_perf[k])

            st_output[k] = st_lower[k] if st_trend[k] == 1 else st_upper[k]

        # ---- k-means over the performance scores (pine lines 1405-1473) ----
        if p.max_data <= 0 or (n - 1 - i) <= p.max_data:
            tf, bp = _kmeans_target(st_perf, factors)
            if not math.isnan(tf):
                target_factor = tf             # nz(..., target_factor)
            d = den[i]
            if not math.isnan(d) and d != 0:
                perf_idx = max(0.0 if math.isnan(bp) else bp, 0.0) / d

        # ---- final SuperTrend (pine lines 1476-1485; note the different order) ----
        up = hl2[i] + a * target_factor
        dn = hl2[i] - a * target_factor
        f_upper = min(up, f_upper) if pc < f_upper else up
        f_lower = max(dn, f_lower) if pc > f_lower else dn
        if c > f_upper:
            f_os = 1
        elif c < f_lower:
            f_os = 0
        ts = f_lower if f_os else f_upper

        out["atr"][i] = a
        out["target_factor"][i] = target_factor
        out["perf_idx"][i] = perf_idx
        out["os"][i] = f_os
        out["ts"][i] = ts

        # ---- chop / fakeout inputs (pine lines 1519-1532) ----
        if i >= CHOP_LOOKBACK:
            overlap_sum = 0.0
            range_sum = 0.0
            for j in range(CHOP_LOOKBACK):
                b0 = i - j
                b1 = b0 - 1
                ov = min(high[b0], high[b1]) - max(low[b0], low[b1])
                overlap_sum += ov if ov > 0.0 else 0.0
                range_sum += high[b0] - low[b0]
            out["overlap_ratio"][i] = overlap_sum / range_sum if range_sum > 0 else 0.0
        out["body_atr"][i] = abs(c - open_[i]) / a if a > 0 else 0.0

        pidx = 0.0 if math.isnan(perf_idx) else perf_idx
        s10 = int(pidx * 10)
        out["signal_strength"][i] = 1 if s10 < 2 else 2 if s10 < 4 else 3 if s10 < 5 else 4

    out["overlap_rank"] = pm.percentrank(out["overlap_ratio"], CHOP_RANK_LENGTH)

    os_arr = out["os"]
    flip_up = np.zeros(n, dtype=bool)
    flip_dn = np.zeros(n, dtype=bool)
    flip_up[1:] = os_arr[1:] > os_arr[:-1]
    flip_dn[1:] = os_arr[1:] < os_arr[:-1]
    out["raw_buy"] = flip_up
    out["raw_sell"] = flip_dn
    return out


def apply_signals(out, p: EngineParams):
    """The cheap part: the chop/quality gate and the signal conditions
    (pine lines 1550-1600). Mutates `out` in place and returns it."""
    n = len(out["os"])

    max_overlap, min_quality, body_override = chop_thresholds(p)
    rank = out["overlap_rank"]
    chop_ok = np.isnan(rank) | (rank <= max_overlap)
    if min_quality > 0:
        quality_ok = (np.nan_to_num(out["perf_idx"]) >= min_quality) | (
            out["body_atr"] >= body_override
        )
    else:
        quality_ok = np.ones(n, dtype=bool)
    passes = np.ones(n, dtype=bool) if not p.filter_fakeouts else (chop_ok & quality_ok)
    out["passes_filter"] = passes
    out["chop_ok"] = chop_ok
    out["quality_ok"] = quality_ok

    flip_up = out["raw_buy"]
    flip_dn = out["raw_sell"]
    strong_enough = out["signal_strength"] >= p.min_signal_strength
    out["buy"] = flip_up & passes & strong_enough
    out["sell"] = flip_dn & passes & strong_enough
    out["blocked_buy"] = flip_up & ~(passes & strong_enough)
    out["blocked_sell"] = flip_dn & ~(passes & strong_enough)
    return out
