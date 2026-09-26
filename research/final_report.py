"""Headline results for the configurations worth quoting.

Runs the SHIPPED config (from shipped.py, which is checked against the EA's
compiled defaults) alongside the pine defaults and a couple of variants, and
repeats each with entries pushed one bar later as a lookahead probe: a genuine
edge degrades gradually, a lookahead bug collapses.
"""

from __future__ import annotations

from dataclasses import replace

import backtest
import dataset
import engine
import shipped

COSTS = shipped.costs()

CONFIGS = [
    (
        "pine defaults (filter off, reversal exit on)",
        engine.EngineParams(),
        backtest.TradeParams(risk_pct=shipped.RISK_PCT),
    ),
    (
        "SHIPPED: " + shipped.describe(),
        shipped.engine_params(),
        shipped.trade_params(),
    ),
    (
        "shipped but no trend filter",
        shipped.engine_params(),
        shipped.trade_params(trend_ema_length=0),
    ),
    (
        "shipped but tight stop (1.5xATR)",
        shipped.engine_params(),
        shipped.trade_params(sl_atr_mult=1.5),
    ),
    (
        "raw signal: no chop filter, no trend filter, stops only",
        engine.EngineParams(),
        shipped.trade_params(trend_ema_length=0),
    ),
]


def main() -> None:
    for tf in (shipped.TIMEFRAME,):
        o, h, l, c, t = dataset.from_npy(shipped.SYMBOL, tf)
        print("=" * 78)
        print(f"{shipped.SYMBOL} {tf}   {dataset.describe(t)}")
        print(f"costs: FTMO terms, spread {COSTS.spread_price} USD/oz, "
              f"{shipped.RISK_PCT}% risk per trade")
        print("=" * 78)

        cache: dict = {}
        for label, ep, tp in CONFIGS:
            key = (ep.sensitivity, ep.atr_length, ep.max_data)
            core = cache.get(key)
            if core is None:
                core = engine.run_core(o, h, l, c, ep.sensitivity, ep.atr_length, ep.max_data)
                cache[key] = core
            eng = engine.apply_signals(core, ep)
            _, stats, _ = backtest.run(o, h, l, c, t, eng=eng, trade=tp, costs=COSTS)

            # lookahead probe on the same configuration
            eng2 = engine.apply_signals(core, ep)
            _, delayed, _ = backtest.run(
                o, h, l, c, t, eng=eng2,
                trade=replace(tp, entry_delay_bars=1), costs=COSTS,
            )

            print(f"\n--- {label}")
            print(backtest.format_stats(stats))
            print(f"  +1 bar delay    net {delayed.get('net_profit', 0):>11,.0f}  "
                  f"PF {delayed.get('profit_factor', 0):.3f}   "
                  f"(gradual decay = no lookahead)")
        print()


if __name__ == "__main__":
    main()
