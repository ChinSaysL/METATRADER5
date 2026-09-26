"""Entry-veto masks shared by the research scripts.

These mirror the EA's two gates:
  ema_masks   <-> InpUseTrendFilter / InpTrendEmaLength
  htf_masks   <-> InpUseHtfFilter / InpHtfPeriod

They are kept out of backtest.py on purpose: backtest.py takes plain boolean
masks and should not need to know how to load a second timeframe.
"""

from __future__ import annotations

import numpy as np

import dataset
import engine
import pine_math as pm

HTF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def ema_masks(close, length: int = 200):
    """Longs allowed above the EMA, shorts below. Abstains while it warms up,
    matching the EA's TrendAllows()."""
    e = pm.ema(close, length)
    warm = ~np.isnan(e)
    return (~warm | (close > e)), (~warm | (close < e))


def htf_masks(times, htf: str = "H1", symbol: str = "XAUUSD",
              sensitivity: float = 5.0, atr_length: int = 10):
    """Run the same engine on a slower timeframe and map its state forward.

    A bar only ever sees an HTF bar that has already CLOSED - the searchsorted is
    on `bar open + duration`, i.e. the close time - so there is no lookahead. This
    is the same rule the EA follows by only ever reading shift >= 1 on the HTF.
    """
    ho, hh, hl, hc, ht = dataset.from_npy(symbol, htf)
    core = engine.run_core(ho, hh, hl, hc, sensitivity, atr_length, 0)
    close_times = ht + HTF_MINUTES[htf] * 60
    idx = np.searchsorted(close_times, times, side="right") - 1
    state = np.full(len(times), -1, dtype=np.int8)
    ok = idx >= 0
    state[ok] = core["os"][idx[ok]]
    # state -1 means no closed HTF bar yet; abstain rather than block
    return (state != 0), (state != 1)


def combine(*pairs):
    """AND together several (long_mask, short_mask) pairs."""
    longs = shorts = None
    for a, b in pairs:
        longs = a if longs is None else (longs & a)
        shorts = b if shorts is None else (shorts & b)
    return longs, shorts
