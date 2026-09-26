//+------------------------------------------------------------------+
//|                                              GoldSignalsAI_EA.mq5 |
//|   MetaTrader 5 implementation of "Lux Algo Signals & Overlays      |
//|   [6.3]" (pinescriptbot.pine) for XAUUSD.                         |
//|                                                                  |
//|   The signal is the pine script's SuperTrend AI k-means engine,    |
//|   ported exactly (see Include/GoldSignalsAI/SuperTrendAI.mqh).     |
//|   The pine script only draws signals, so the trade management      |
//|   below is an addition, not a port:                               |
//|                                                                  |
//|     entry   at the open of the bar AFTER the signal bar            |
//|     stop    entry -/+ InpSLAtrMult * ATR(signal bar)               |
//|     trail   the engine's own adaptive line `ts`, never loosening   |
//|     exit    optional, when the ported Take-Profit-Warning fires    |
//|     reverse on the opposite signal                                |
//|                                                                  |
//|   RESEARCH / BACKTEST BUILD. It refuses to run on a real account.  |
//+------------------------------------------------------------------+
#property copyright "Port of pinescriptbot.pine"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include "..\\..\\Include\\GoldSignalsAI\\SuperTrendAI.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\ReversalExit.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\Diagnostics.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\LiveGuards.mqh"

//--- Signal engine (pine "BASIC SETTINGS")
input group             "=== SIGNAL ENGINE ==="
input double            InpSensitivity       = 10.0;   // Signal Sensitivity (pine line 30)
input int               InpAtrLength         = 10;     // Signal Tuner / ATR length (pine line 31)
input int               InpMinSignalStrength = 1;      // Min AI signal strength 1-4 (1 = keep all)

//--- Chop / fakeout filter (pine "FAKEOUT / CHOP FILTER")
//--- Re-selected on 2.8 years (2023-12 to 2026-09), NOT on the 7.5 months the
//--- first version used. The chop filter helps at ANY strength here - off/Light/
//--- Medium/Strong all score PF 1.60-1.74 - so Light is chosen as the least
//--- aggressive setting that captures the effect. The indicator keeps the pine
//--- defaults so it can still be compared against TradingView. See README.md.
input group             "=== FAKEOUT / CHOP FILTER ==="
input bool              InpFilterFakeouts    = true;   // Filter Fakeouts / Chop (pine line 55)
input ENUM_STAI_STRENGTH InpFakeoutStrength  = STAI_STRONG; // Filter Strength
input int               InpCustomMaxChop     = 60;     // Custom: Max Chop Level
input double            InpCustomMinQuality  = 0.0;    // Custom: Min Signal Quality
input double            InpCustomBodyOverride= 0.0;    // Custom: Strong Candle Override

//--- Trend regime filter (NOT part of the pine script, but derived from it)
//--- The script computes ema50/ema200 (lines 396-397) purely to decorate its
//--- labels with "+" (line 1582) and never filters on them. Using EMA200 as an
//--- actual gate - longs only above it, shorts only below - was the single best
//--- improvement found: profit factor 1.63 -> 2.29, drawdown 9.3% -> 6.6%, and it
//--- improved 5 of 6 walk-forward folds. The counter-trend half of the signals
//--- came out at PF 0.95, i.e. no edge at all, which is why this works.
input group             "=== TREND REGIME FILTER ==="
input bool              InpUseTrendFilter    = true;   // Only trade with the EMA trend
input int               InpTrendEmaLength    = 200;    // Trend EMA length

//--- Higher-timeframe agreement: a SECOND copy of the same engine running on a
//--- slower chart, with entries allowed only when it points the same way.
//--- DEFAULTS OFF after re-testing on 2.8 years. On the 7.5-month sample this
//--- looked like the best risk-adjusted filter (PF 3.04); on the full sample it
//--- cuts the sample to 62 trades and only 5 of 8 folds are profitable, while
//--- costing more than it adds. Kept switchable, but the evidence no longer
//--- supports it. See README.md.
input group             "=== HIGHER-TIMEFRAME AGREEMENT ==="
input bool              InpUseHtfFilter      = false;  // Require a slower chart to agree
input ENUM_TIMEFRAMES   InpHtfPeriod         = PERIOD_H4; // Higher timeframe
input int               InpHtfWarmupBars     = 1500;   // HTF bars used to warm that engine

//--- News blackout. Uses MT5's built-in economic calendar.
//--- "No open positions 30 min before and after news" is stronger than "no new
//--- entries": inside the window an existing position is CLOSED, not just left
//--- alone. Consequence to be aware of - a good trend running into a release is
//--- cut, and the engine only re-enters on its next flip, so the rest of that
//--- move is given up. That is the cost of the guarantee.
//--- The calendar needs a connected terminal. If it returns nothing the EA warns
//--- once and keeps trading; set InpNewsRequireCalendar to halt instead.
input group             "=== NEWS BLACKOUT ==="
input bool              InpUseNewsFilter     = true;   // Flatten and stand aside around news
input int               InpNewsMinutesBefore = 30;     // Minutes before a release
input int               InpNewsMinutesAfter  = 30;     // Minutes after a release
input ENUM_CALENDAR_EVENT_IMPORTANCE InpNewsImportance = CALENDAR_IMPORTANCE_HIGH; // Minimum importance
input string            InpNewsCurrencies    = "USD";  // Comma-separated; "" = all
input bool              InpNewsRequireCalendar = false;// Stop trading if the calendar is unavailable

//--- Structural protection, found by measuring trade anatomy rather than by
//--- tuning. Both act independently of the adaptive line:
//---
//---   Breakeven ratchet - the adaptive line sits factor*ATR from price, so a
//---   freshly flipped trade is unprotected for its first bars. Measured: 49%
//---   of trades that reached +1R still ended as FULL losses, handing back 59%
//---   of the average peak. Pulling the stop to breakeven at +1R cut drawdown
//---   from 19.0% to 14.8% without capping any winner.
//---
//---   WaveTrend entry agreement - only 46% of trades were in profit at bar 0,
//---   i.e. many entries chase an already-extended move. Requiring the
//---   WaveTrend oscillator (already ported, parity-verified) to agree with the
//---   signal direction lifted the profit factor from 1.406 to 1.580.
//---
//--- Together these turned the 11-year H1 walk-forward from -1,679 to +4,691.
input group             "=== STRUCTURAL PROTECTION ==="
input double            InpBreakevenAtR      = 1.0;    // Move stop to breakeven at +n R (0 = off)
input double            InpBreakevenOffsetR  = 0.05;   // Breakeven offset in R, covers costs
input bool              InpUseWaveTrendEntry = true;   // Require WaveTrend to agree on entry

//--- Trade management (NOT part of the pine script)
input group             "=== TRADE MANAGEMENT ==="
input bool              InpUseAtrStop        = true;   // Use initial ATR stop loss
input double            InpSLAtrMult          = 2.5;   // Initial SL = n * ATR
input double            InpTPAtrMult          = 0.0;   // Fixed TP = n * ATR (0 = off)
input bool              InpUseTsTrail        = true;   // Trail the stop along the adaptive line
//--- Defaults OFF: as a mechanical exit the ported Take-Profit Warning cuts the
//--- average win from 339 to 179 and roughly halves net profit, because it fires
//--- on reversal hints long before the trend is finished. Useful as a chart prompt,
//--- harmful as an order. Turn it on only to reproduce that behaviour.
input bool              InpUseReversalExit   = false;  // Exit on the Take-Profit-Warning reversal
input ENUM_REX_SENSITIVITY InpReversalSens   = REX_NORMAL; // Reversal sensitivity
input bool              InpReverseOnOpposite = true;   // Reverse on the opposite signal

//--- Risk
input group             "=== RISK ==="
input bool              InpUseRiskPercent    = true;   // Size by % of balance (else fixed lot)
//--- 0.50%: over 2.8 years and 142 simulated FTMO starts this is the only level
//--- with ZERO breaches (worst drawdown 6.6%, bootstrap 95th percentile ~8.5%).
//--- 0.75% fails 3 of 87, 1.0% fails 8 of 98, 1.5% fails 19 of 113. Median time
//--- to a +10% target at this risk is 35 trading days.
input double            InpRiskPercent       = 0.50;   // Risk per trade, % of balance
input double            InpFixedLot          = 0.10;   // Fixed lot
input double            InpMaxSpreadPoints   = 500;    // Skip entries above this spread (0 = off)

//--- Live trading and account protection.
//--- The strategy is allowed to be wrong; it is not allowed to breach a prop
//--- limit while being wrong. These halt BEFORE the limit, with a buffer,
//--- because FTMO measures on equity including floating P&L and a gap can
//--- carry price past a stop. A halt survives an EA restart - clearing it is
//--- deliberate, via InpResetGuards.
input group             "=== LIVE TRADING AND ACCOUNT LIMITS ==="
input bool              InpAllowLiveTrading  = false;  // REQUIRED to trade a real account
input double            InpMaxDailyLossPct   = 4.0;    // Halt for the day at this % loss (FTMO 5%)
input double            InpMaxTotalLossPct   = 8.0;    // Halt for good at this % loss (FTMO 10%)
input int               InpMaxConsecFailures = 5;      // Halt after N consecutive order failures
input bool              InpResetGuards       = false;  // Set true ONCE to clear a stored halt

//--- Plumbing
input group             "=== PLUMBING ==="
input int               InpWarmupBars        = 3000;   // History bars used to warm the engine
input long              InpMagic             = 63063001;// Magic number
input int               InpSlippagePoints    = 30;     // Max deviation
input bool              InpDumpDiagnostics   = false;  // Write the per-bar parity CSV
input string            InpDumpFile          = "GoldSignalsAI_diag.csv"; // CSV name

//--- globals
CTrade          trade;
CPositionInfo   pos;
CSuperTrendAI   engine;
CReversalExit   reversal;
CDiagnostics    diag;
CPmEma          trendEma;
CLiveGuards     guards;
double          g_entry_risk  = 0.0;   // 1R in price units for the open position
double          g_entry_price = 0.0;
bool            g_be_done     = false; // breakeven already applied to this trade
double          g_trend_value = 0.0;   // EMA of close, NaN until warm
CSuperTrendAI   htfEngine;             // the same engine on InpHtfPeriod
datetime        g_htf_last_fed = 0;
bool            g_htf_warmed   = false;

//--- news blackout state
datetime        g_news_times[];        // release times that passed the filters
datetime        g_news_refreshed = 0;
bool            g_news_warned    = false;
bool            g_in_news        = false;
datetime        g_news_until     = 0;  // when the current blackout ends

datetime        g_last_fed   = 0;      // open time of the last bar fed to the engine
bool            g_warmed     = false;
double          g_point      = 0.0;
double          g_tick_size  = 0.0;
double          g_tick_value = 0.0;
double          g_stops_level= 0.0;
int             g_digits     = 2;

bool Preflight();

//+------------------------------------------------------------------+
int OnInit()
{
   //--- A real account requires an explicit opt-in. Defaulting this to false is
   //--- what stops an accidental attach from trading live money.
   bool is_real = ((ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE)
                   == ACCOUNT_TRADE_MODE_REAL);
   if(is_real && !InpAllowLiveTrading)
   {
      Print("GoldSignalsAI: REAL account detected and InpAllowLiveTrading is false. "
            "Set it true deliberately to trade live. Aborting.");
      return(INIT_FAILED);
   }
   if(is_real)
      Print("GoldSignalsAI: LIVE TRADING ENABLED on a real account.");

   //--- gold only, as requested
   string sym = _Symbol;
   StringToUpper(sym);
   if(StringFind(sym, "XAU") < 0 && StringFind(sym, "GOLD") < 0)
   {
      PrintFormat("GoldSignalsAI: %s is not a gold symbol. This EA only trades gold.", _Symbol);
      return(INIT_FAILED);
   }

   g_point       = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_tick_size   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   g_tick_value  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   g_digits      = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_stops_level = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * g_point;
   if(g_tick_size <= 0.0)
      g_tick_size = g_point;

   //--- Sizing sanity check. Some servers (MetaQuotes-Demo among them) report a
   //--- SYMBOL_TRADE_TICK_VALUE that disagrees with their own contract maths, and
   //--- a wrong value here scales every position. Surface it loudly at startup.
   {
      double ref = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      if(ref > 0.0)
      {
         double per_lot_real = LossPerLot(1, ref, ref - 1.0);
         double per_lot_tick = (g_tick_size > 0.0) ? 1.0 / g_tick_size * g_tick_value : 0.0;
         PrintFormat("GoldSignalsAI sizing: 1.00 lot over a 1.00 move = %.2f %s "
                     "(tick_value would say %.2f, contract_size says %.2f)",
                     per_lot_real, AccountInfoString(ACCOUNT_CURRENCY), per_lot_tick,
                     SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE));
         if(per_lot_tick > 0.0 && MathAbs(per_lot_tick - per_lot_real) / per_lot_real > 0.01)
            PrintFormat("GoldSignalsAI: WARNING - this server's tick_value is "
                        "inconsistent with its contract size. Using OrderCalcProfit.");
      }
   }

   SStaiParams p;
   StaiDefaults(p);
   p.sensitivity          = InpSensitivity;
   p.atr_length           = InpAtrLength;
   p.filter_fakeouts      = InpFilterFakeouts;
   p.fakeout_strength     = InpFakeoutStrength;
   p.custom_max_chop      = InpCustomMaxChop;
   p.custom_min_quality   = InpCustomMinQuality;
   p.custom_body_override = InpCustomBodyOverride;
   p.min_signal_strength  = InpMinSignalStrength;
   if(!engine.Init(p))
      return(INIT_PARAMETERS_INCORRECT);

   reversal.Init(InpReversalSens);
   if(InpUseHtfFilter)
   {
      //--- enum values are not ordered by duration, so compare seconds
      if(PeriodSeconds(InpHtfPeriod) <= PeriodSeconds(PERIOD_CURRENT))
      {
         PrintFormat("GoldSignalsAI: HTF period %s is not slower than the chart (%s).",
                     EnumToString(InpHtfPeriod),
                     EnumToString((ENUM_TIMEFRAMES)Period()));
         return(INIT_PARAMETERS_INCORRECT);
      }
      //--- identical signal settings; only the timeframe differs
      if(!htfEngine.Init(p))
         return(INIT_PARAMETERS_INCORRECT);
   }
   trendEma.Init(InpTrendEmaLength);
   g_trend_value = PmNaN();

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePoints);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetAsyncMode(false);

   guards.Init(InpMagic, InpMaxDailyLossPct, InpMaxTotalLossPct, InpMaxConsecFailures);
   if(InpResetGuards)
   {
      guards.Reset();
      guards.Init(InpMagic, InpMaxDailyLossPct, InpMaxTotalLossPct, InpMaxConsecFailures);
   }

   if(!Preflight())
      return(INIT_FAILED);

   if(InpDumpDiagnostics)
      diag.Open(InpDumpFile);

   PrintFormat("GoldSignalsAI init: %s %s, %d factors (sensitivity %.1f, ATR %d), warmup %d bars",
               _Symbol, EnumToString(PERIOD_CURRENT), engine.FactorCount(),
               InpSensitivity, InpAtrLength, InpWarmupBars);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   diag.Close();
}

//+------------------------------------------------------------------+
//| Feed the engine every closed bar it has not seen yet, newest last. |
//+------------------------------------------------------------------+
void FeedNewBars()
{
   datetime last_closed = iTime(_Symbol, PERIOD_CURRENT, 1);
   if(last_closed <= 0 || last_closed == g_last_fed)
      return;

   //--- one-off warm-up: everything up to, but excluding, the newest closed bar
   if(!g_warmed)
   {
      MqlRates rates[];
      int want = InpWarmupBars + 1;
      int got  = CopyRates(_Symbol, PERIOD_CURRENT, 1, want, rates);
      if(got <= 1)
         return;                       // not enough history yet, try again next tick
      for(int i = 0; i < got - 1; i++) // oldest .. second-newest
      {
         engine.OnBar(rates[i].open, rates[i].high, rates[i].low, rates[i].close);
         g_trend_value = trendEma.Update(rates[i].close);
         if(InpUseReversalExit || InpUseWaveTrendEntry || diag.Enabled())
            reversal.OnBar(rates[i].high, rates[i].low, rates[i].close);
         //--- The engine is recursive, so parity is only meaningful if the Python
         //--- reference sees the SAME first bar. Warm-up bars are therefore dumped
         //--- as well, giving research/parity_check.py the whole series.
         if(diag.Enabled())
            diag.Write(rates[i].time, rates[i].open, rates[i].high, rates[i].low,
                       rates[i].close, engine, reversal, true);
      }
      g_last_fed = rates[got - 2].time;
      g_warmed   = true;
      PrintFormat("GoldSignalsAI warm-up: %d bars fed, state ready at %s",
                  got - 1, TimeToString(g_last_fed));
   }

   //--- then every closed bar newer than the last one fed
   int shift = 1;
   while(shift < Bars(_Symbol, PERIOD_CURRENT) && iTime(_Symbol, PERIOD_CURRENT, shift) > g_last_fed)
      shift++;
   for(int s = shift - 1; s >= 1; s--)
   {
      datetime t = iTime(_Symbol, PERIOD_CURRENT, s);
      if(t <= g_last_fed)
         continue;
      double o = iOpen(_Symbol, PERIOD_CURRENT, s);
      double h = iHigh(_Symbol, PERIOD_CURRENT, s);
      double l = iLow(_Symbol, PERIOD_CURRENT, s);
      double c = iClose(_Symbol, PERIOD_CURRENT, s);

      engine.OnBar(o, h, l, c);
      g_trend_value = trendEma.Update(c);
      if(InpUseReversalExit || InpUseWaveTrendEntry || diag.Enabled())
         reversal.OnBar(h, l, c);
      g_last_fed = t;

      if(diag.Enabled())
         diag.Write(t, o, h, l, c, engine, reversal,
                    InpUseReversalExit || diag.Enabled());

      //--- act on the bar that just closed, at the price the next bar opens on
      ManageAndTrade();
   }
}

//+------------------------------------------------------------------+
//| Rebuild the list of upcoming releases from MT5's economic calendar.|
//| Refreshed every 15 minutes rather than every tick - the calendar is |
//| a relatively expensive call and release times do not move.          |
//+------------------------------------------------------------------+
void RefreshNewsTimes(const datetime now)
{
   ArrayResize(g_news_times, 0);

   MqlCalendarValue values[];
   int total = CalendarValueHistory(values, now - 86400, now + 86400);
   if(total <= 0)
   {
      if(!g_news_warned)
      {
         PrintFormat("GoldSignalsAI: economic calendar returned no events (error %d). "
                     "%s", GetLastError(),
                     InpNewsRequireCalendar
                     ? "InpNewsRequireCalendar is set, so trading is halted."
                     : "Trading continues WITHOUT the news filter - set "
                       "InpNewsRequireCalendar to halt instead.");
         g_news_warned = true;
      }
      return;
   }
   g_news_warned = false;

   string filter = "," + InpNewsCurrencies + ",";
   for(int i = 0; i < total; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev))
         continue;
      if((int)ev.importance < (int)InpNewsImportance)
         continue;
      //--- the currency lives on the country record, not on the event
      if(InpNewsCurrencies != "")
      {
         MqlCalendarCountry country;
         if(!CalendarCountryById(ev.country_id, country))
            continue;
         if(StringFind(filter, "," + country.currency + ",") < 0)
            continue;
      }

      int k = ArraySize(g_news_times);
      ArrayResize(g_news_times, k + 1);
      g_news_times[k] = values[i].time;
   }
}

//+------------------------------------------------------------------+
//| Are we inside a blackout window right now?                        |
//+------------------------------------------------------------------+
void UpdateNewsState()
{
   g_in_news = false;
   g_news_until = 0;
   if(!InpUseNewsFilter)
      return;

   datetime now = TimeCurrent();
   if(now - g_news_refreshed >= 900 || g_news_refreshed == 0)
   {
      RefreshNewsTimes(now);
      g_news_refreshed = now;
   }

   //--- no calendar data: either stand down completely or carry on unfiltered
   if(ArraySize(g_news_times) == 0 && g_news_warned)
   {
      if(InpNewsRequireCalendar)
      {
         g_in_news = true;
         g_news_until = now + 900;
      }
      return;
   }

   for(int i = 0; i < ArraySize(g_news_times); i++)
   {
      datetime from = g_news_times[i] - InpNewsMinutesBefore * 60;
      datetime to   = g_news_times[i] + InpNewsMinutesAfter * 60;
      if(now >= from && now <= to)
      {
         g_in_news = true;
         if(to > g_news_until)
            g_news_until = to;
      }
   }
}

//+------------------------------------------------------------------+
//| Advance the higher-timeframe engine over any HTF bars that have    |
//| closed since last time. Only shift >= 1 is ever read, so a bar on  |
//| the base chart can never see an unfinished HTF bar - the same       |
//| no-lookahead rule research/experiments2.py applies with its         |
//| searchsorted on HTF close times.                                   |
//+------------------------------------------------------------------+
void FeedHtf()
{
   if(!InpUseHtfFilter)
      return;

   datetime last_closed = iTime(_Symbol, InpHtfPeriod, 1);
   if(last_closed <= 0 || last_closed == g_htf_last_fed)
      return;

   if(!g_htf_warmed)
   {
      MqlRates r[];
      int got = CopyRates(_Symbol, InpHtfPeriod, 1, InpHtfWarmupBars + 1, r);
      if(got <= 1)
         return;                       // not enough HTF history yet
      for(int i = 0; i < got - 1; i++)
         htfEngine.OnBar(r[i].open, r[i].high, r[i].low, r[i].close);
      g_htf_last_fed = r[got - 2].time;
      g_htf_warmed = true;
      PrintFormat("GoldSignalsAI HTF warm-up: %d %s bars, ready at %s",
                  got - 1, EnumToString(InpHtfPeriod), TimeToString(g_htf_last_fed));
   }

   int shift = 1;
   while(shift < Bars(_Symbol, InpHtfPeriod)
         && iTime(_Symbol, InpHtfPeriod, shift) > g_htf_last_fed)
      shift++;
   for(int s2 = shift - 1; s2 >= 1; s2--)
   {
      datetime tt = iTime(_Symbol, InpHtfPeriod, s2);
      if(tt <= g_htf_last_fed)
         continue;
      htfEngine.OnBar(iOpen(_Symbol, InpHtfPeriod, s2), iHigh(_Symbol, InpHtfPeriod, s2),
                      iLow(_Symbol, InpHtfPeriod, s2), iClose(_Symbol, InpHtfPeriod, s2));
      g_htf_last_fed = tt;
   }
}

//+------------------------------------------------------------------+
//| Higher-timeframe gate. Abstains until that engine is warm rather   |
//| than blocking, so a cold start does not silently stop trading.     |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| WaveTrend entry gate: refuse to enter against momentum.           |
//| Abstains until the oscillator is warm rather than blocking.       |
//+------------------------------------------------------------------+
bool WaveTrendAllows(const int direction)
{
   if(!InpUseWaveTrendEntry)
      return(true);
   double osc = reversal.SeriesNormal();
   if(!MathIsValidNumber(osc))
      return(true);
   return(direction > 0 ? (osc > 0.0) : (osc < 0.0));
}

//+------------------------------------------------------------------+
bool HtfAllows(const int direction)
{
   if(!InpUseHtfFilter || !g_htf_warmed)
      return(true);
   return(direction > 0 ? (htfEngine.Os() == 1) : (htfEngine.Os() == 0));
}

//+------------------------------------------------------------------+
//| Position size from the risk budget and the stop distance.         |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| What one lot loses over `stop_distance`, in account currency.      |
//|                                                                   |
//| Do NOT trust SYMBOL_TRADE_TICK_VALUE for this. MetaQuotes-Demo     |
//| reports 0.1 for XAUUSD while its own OrderCalcProfit says a 1.00   |
//| move on 1 lot is worth 100.00 - the tick_value route would have    |
//| sized positions 10x too large. OrderCalcProfit is the broker's own |
//| contract maths and is authoritative; the others are fallbacks, and |
//| the larger (i.e. smaller position) wins if they ever disagree.     |
//+------------------------------------------------------------------+
double LossPerLot(const int direction, const double entry_price, const double stop_price)
{
   double profit = 0.0;
   if(OrderCalcProfit(direction > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,
                      _Symbol, 1.0, entry_price, stop_price, profit)
      && MathAbs(profit) > 0.0)
      return(MathAbs(profit));

   double distance = MathAbs(entry_price - stop_price);
   double by_contract = distance * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double by_tick = (g_tick_size > 0.0 && g_tick_value > 0.0)
                    ? distance / g_tick_size * g_tick_value : 0.0;
   return(MathMax(by_contract, by_tick));
}

//+------------------------------------------------------------------+
double CalcLots(const double stop_distance, const int direction,
                const double entry_price, const double stop_price)
{
   double vmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0)
      vstep = 0.01;

   double lots = InpFixedLot;
   if(InpUseRiskPercent && stop_distance > 0.0)
   {
      double risk_cash    = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
      double loss_per_lot = LossPerLot(direction, entry_price, stop_price);
      if(loss_per_lot > 0.0)
         lots = risk_cash / loss_per_lot;
   }
   lots = MathFloor(lots / vstep + 1e-9) * vstep;
   lots = MathMax(vmin, MathMin(vmax, lots));
   return(NormalizeDouble(lots, 2));
}

//+------------------------------------------------------------------+
bool HaveOurPosition()
{
   if(!pos.SelectByMagic(_Symbol, InpMagic))
      return(false);
   return(pos.PositionType() == POSITION_TYPE_BUY || pos.PositionType() == POSITION_TYPE_SELL);
}

int OurDirection()
{
   if(!HaveOurPosition())
      return(0);
   return(pos.PositionType() == POSITION_TYPE_BUY ? 1 : -1);
}

//+------------------------------------------------------------------+
//| Keep a stop on the correct side of price and outside stops level.  |
//+------------------------------------------------------------------+
double SanitizeStop(const int direction, double sl)
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double margin = MathMax(g_stops_level, g_point);
   if(direction > 0)
      sl = MathMin(sl, bid - margin);
   else
      sl = MathMax(sl, ask + margin);
   return(NormalizeDouble(sl, g_digits));
}

//+------------------------------------------------------------------+
void OpenPosition(const int direction)
{
   double a = engine.Atr();
   if(!MathIsValidNumber(a) || a <= 0.0)
      return;

   if(InpMaxSpreadPoints > 0)
   {
      double spread_points = (SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                              - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / g_point;
      if(spread_points > InpMaxSpreadPoints)
      {
         PrintFormat("skip entry: spread %.0f > %.0f points", spread_points, InpMaxSpreadPoints);
         return;
      }
   }

   double stop_dist = InpSLAtrMult * a;
   double price = (direction > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                  : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = 0.0, tp = 0.0;
   if(InpUseAtrStop)
      sl = SanitizeStop(direction, (direction > 0) ? price - stop_dist : price + stop_dist);
   if(InpTPAtrMult > 0.0)
   {
      double td = InpTPAtrMult * a;
      tp = NormalizeDouble((direction > 0) ? price + td : price - td, g_digits);
   }

   //--- size against the real stop level, whatever it ended up being
   double risk_stop = (sl > 0.0) ? sl
                      : ((direction > 0) ? price - stop_dist : price + stop_dist);
   double lots = CalcLots(stop_dist, direction, price, risk_stop);
   bool ok = (direction > 0) ? trade.Buy(lots, _Symbol, 0.0, sl, tp, "STAI buy")
                             : trade.Sell(lots, _Symbol, 0.0, sl, tp, "STAI sell");
   guards.RecordOrderResult(ok);
   if(ok)
   {
      //--- anchor 1R for the breakeven ratchet
      g_entry_risk  = stop_dist;
      g_entry_price = price;
      g_be_done     = false;
   }
   if(!ok)
      PrintFormat("order failed: retcode %d (%s)", trade.ResultRetcode(),
                  trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
void ClosePosition(const string why)
{
   if(!HaveOurPosition())
      return;
   bool closed = trade.PositionClose(pos.Ticket());
   guards.RecordOrderResult(closed);
   if(!closed)
      PrintFormat("close (%s) failed: retcode %d", why, trade.ResultRetcode());
}

//+------------------------------------------------------------------+
//| Trail the stop along the engine's adaptive line, never loosening.  |
//+------------------------------------------------------------------+
void TrailStop()
{
   if(!InpUseTsTrail || !HaveOurPosition())
      return;
   double ts = engine.Ts();
   if(!MathIsValidNumber(ts))
      return;

   int    dir = OurDirection();
   double cur = pos.StopLoss();
   double want = ts;

   //--- Breakeven ratchet. Applied BEFORE the adaptive line and kept even if
   //--- the line is further away, because early on the line protects nothing.
   if(InpBreakevenAtR > 0.0 && !g_be_done && g_entry_risk > 0.0)
   {
      double px = (dir > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                            : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double open_r = (px - pos.PriceOpen()) * dir / g_entry_risk;
      if(open_r >= InpBreakevenAtR)
      {
         double be = pos.PriceOpen() + dir * InpBreakevenOffsetR * g_entry_risk;
         bool better = (cur <= 0.0) || (dir > 0 ? be > cur : be < cur);
         if(better && (dir > 0 ? be < px : be > px))
         {
            double bes = SanitizeStop(dir, be);
            if(trade.PositionModify(pos.Ticket(), bes, pos.TakeProfit()))
            {
               PrintFormat("breakeven ratchet: +%.2fR reached, stop -> %.2f",
                           open_r, bes);
               g_be_done = true;
               cur = bes;
            }
         }
      }
   }

   //--- only ever tighten
   if(dir > 0)
   {
      if(ts >= SymbolInfoDouble(_Symbol, SYMBOL_BID))
         return;
      if(cur > 0.0 && want <= cur)
         return;
   }
   else
   {
      if(ts <= SymbolInfoDouble(_Symbol, SYMBOL_ASK))
         return;
      if(cur > 0.0 && want >= cur)
         return;
   }

   want = SanitizeStop(dir, want);
   if(cur > 0.0 && MathAbs(want - cur) < g_point)
      return;
   if(!trade.PositionModify(pos.Ticket(), want, pos.TakeProfit()))
   {
      uint rc = trade.ResultRetcode();
      if(rc != TRADE_RETCODE_INVALID_STOPS && rc != TRADE_RETCODE_NO_CHANGES)
         PrintFormat("trail modify failed: retcode %d", rc);
   }
}

//+------------------------------------------------------------------+
//| Decide and act on the bar that has just closed. Runs once per bar. |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Trend regime gate. Vetoes entries only - an open position is still |
//| managed and trailed normally, exactly as in research/backtest.py.  |
//| Abstains while the EMA is still warming rather than blocking.      |
//+------------------------------------------------------------------+
bool TrendAllows(const int direction)
{
   if(!InpUseTrendFilter || PmIsNa(g_trend_value))
      return(true);
   double c = iClose(_Symbol, PERIOD_CURRENT, 1);   // the bar just closed
   return(direction > 0 ? (c > g_trend_value) : (c < g_trend_value));
}

//+------------------------------------------------------------------+
void ManageAndTrade()
{
   int dir = OurDirection();

   //--- 1) a signal wins over everything else
   if(engine.Buy() && !g_in_news && !guards.Halted() && TrendAllows(1)
      && HtfAllows(1) && WaveTrendAllows(1))
   {
      if(dir < 0)
      {
         if(!InpReverseOnOpposite)
            return;
         ClosePosition("reverse");
         dir = 0;
      }
      if(dir == 0)
         OpenPosition(1);
      return;
   }
   if(engine.Sell() && !g_in_news && !guards.Halted() && TrendAllows(-1)
      && HtfAllows(-1) && WaveTrendAllows(-1))
   {
      if(dir > 0)
      {
         if(!InpReverseOnOpposite)
            return;
         ClosePosition("reverse");
         dir = 0;
      }
      if(dir == 0)
         OpenPosition(-1);
      return;
   }

   //--- 2) reversal exit
   if(dir != 0 && InpUseReversalExit)
   {
      if((dir > 0 && reversal.BearishExhaustion())
         || (dir < 0 && reversal.BullishExhaustion()))
      {
         ClosePosition("reversal");
         return;
      }
   }

   //--- 3) otherwise trail
   TrailStop();
}

//+------------------------------------------------------------------+
void OnTick()
{
   //--- Account limits come first. A halt flattens immediately and blocks all
   //--- further entries; the bar feed below still runs so engine state stays
   //--- correct and the EA can resume cleanly the next day.
   if(guards.Check() && HaveOurPosition())
   {
      PrintFormat("Account limit hit (%s) - flattening.", guards.Reason());
      ClosePosition("risk halt");
   }

   UpdateNewsState();

   //--- Flatten on any tick, not just on a new bar: with M15 bars, waiting for
   //--- the close could leave a position open up to 15 minutes into the window.
   if(g_in_news && HaveOurPosition())
   {
      static datetime last_news_close = 0;
      if(TimeCurrent() - last_news_close > 30)      // do not spam retries
      {
         PrintFormat("News blackout until %s - closing position",
                     TimeToString(g_news_until));
         ClosePosition("news blackout");
         last_news_close = TimeCurrent();
      }
   }

   //--- The engine is recursive, so it must be fed EVERY bar regardless of the
   //--- blackout. Only the trading decisions are gated, never the bar feed.
   FeedHtf();        // must lead, so a decision sees the latest closed HTF bar
   FeedNewBars();
}
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Startup self-check.                                               |
//|                                                                  |
//| This exists because a forward test was skipped. Its job is to     |
//| catch, at attach time, the class of problem a forward test would  |
//| have caught days later - and it already has: MetaQuotes-Demo      |
//| reports a tick_value for XAUUSD that disagrees with its own       |
//| contract maths, which would have sized every position 10x too     |
//| large.                                                            |
//|                                                                  |
//| The decisive test is the last one: price a REAL trade at the      |
//| current price with a realistic stop, and refuse to run unless the |
//| money actually at risk matches InpRiskPercent. Anything that      |
//| corrupts sizing - wrong tick value, wrong contract size, an       |
//| unexpected lot step - fails that check regardless of its cause.   |
//+------------------------------------------------------------------+
bool Preflight()
{
   bool fatal = false;
   Print("---------------- GoldSignalsAI preflight ----------------");

   //--- 1. is trading actually possible
   bool algo_terminal = (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
   bool algo_program  = (bool)MQLInfoInteger(MQL_TRADE_ALLOWED);
   bool algo_account  = (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
   bool algo_allowed  = (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED);
   PrintFormat("  algo trading    terminal=%s expert=%s account=%s trading=%s",
               algo_terminal ? "on" : "OFF", algo_program ? "on" : "OFF",
               algo_account ? "on" : "OFF", algo_allowed ? "on" : "OFF");
   if(!MQLInfoInteger(MQL_TESTER) && (!algo_terminal || !algo_program))
   {
      Print("  -> FAIL: algo trading is disabled; the EA cannot place orders.");
      fatal = true;
   }

   //--- 2. symbol is fully tradable
   ENUM_SYMBOL_TRADE_MODE tmode =
      (ENUM_SYMBOL_TRADE_MODE)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   PrintFormat("  symbol          %s  trade_mode=%s  digits=%d  point=%g  contract=%g",
               _Symbol, EnumToString(tmode), g_digits, g_point,
               SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE));
   if(tmode != SYMBOL_TRADE_MODE_FULL)
   {
      PrintFormat("  -> FAIL: %s is not open for full trading.", _Symbol);
      fatal = true;
   }

   //--- 3. enough history for the engine warm-up
   int bars = Bars(_Symbol, PERIOD_CURRENT);
   PrintFormat("  history         %d bars on %s (need %d for warm-up)",
               bars, EnumToString((ENUM_TIMEFRAMES)Period()), InpWarmupBars);
   if(bars < InpWarmupBars)
      Print("  -> WARNING: short history; the engine will warm up on fewer bars "
            "than intended and its state will differ from the backtest.");

   //--- 4. spread right now against the entry guard
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread_pts = (g_point > 0.0) ? (ask - bid) / g_point : 0.0;
   PrintFormat("  spread          %.0f points (%.2f %s)  limit %.0f",
               spread_pts, ask - bid, SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT),
               InpMaxSpreadPoints);

   //--- 5. a realistic ATR, so the sizing test below uses a real stop distance
   double atr = 0.0;
   MqlRates r[];
   int got = CopyRates(_Symbol, PERIOD_CURRENT, 1, 100, r);
   if(got > 2)
   {
      double sum = 0.0;
      for(int i = 1; i < got; i++)
      {
         double tr = MathMax(r[i].high - r[i].low,
                     MathMax(MathAbs(r[i].high - r[i - 1].close),
                             MathAbs(r[i].low - r[i - 1].close)));
         sum += tr;
      }
      atr = sum / (got - 1);
   }
   if(atr <= 0.0)
   {
      Print("  -> FAIL: cannot measure ATR; no usable price history.");
      return(false);
   }

   //--- 6. THE decisive check: price a real trade and verify the money at risk
   double stop_dist = InpSLAtrMult * atr;
   double entry     = ask;
   double stop      = entry - stop_dist;
   double lots      = CalcLots(stop_dist, 1, entry, stop);
   double per_lot   = LossPerLot(1, entry, stop);
   double risk_cash = lots * per_lot;
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double intended  = balance * InpRiskPercent / 100.0;

   PrintFormat("  sizing          ATR %.2f -> stop %.2f wide; %.2f lots; "
               "risk %.2f vs intended %.2f",
               atr, stop_dist, lots, risk_cash, intended);

   double vmin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vstep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0)
      vstep = 0.01;

   //--- 6a. The tick_value-class detector: two INDEPENDENT routes to "what does
   //--- one lot lose over this distance". OrderCalcProfit is the broker's own
   //--- contract maths; stop_dist * contract_size is arithmetic. For a symbol
   //--- quoted in the account currency they must agree. Comparing CalcLots
   //--- against LossPerLot instead would be circular - both use the same source.
   double by_contract = stop_dist * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(per_lot > 0.0 && by_contract > 0.0)
   {
      double disagreement = MathAbs(per_lot - by_contract) / by_contract;
      PrintFormat("  contract check  OrderCalcProfit %.2f vs contract maths %.2f "
                  "(%.1f%% apart)", per_lot, by_contract, disagreement * 100.0);
      if(disagreement > 0.02
         && StringFind(SymbolInfoString(_Symbol, SYMBOL_CURRENCY_PROFIT),
                       AccountInfoString(ACCOUNT_CURRENCY)) >= 0)
      {
         Print("  -> FAIL: this broker's contract figures disagree with each "
               "other. Sizing cannot be trusted; refusing to trade.");
         fatal = true;
      }
   }

   //--- 6b. Money actually at risk. Rounding the lot DOWN to the broker's step
   //--- makes the position smaller, which is safe and expected - only over-risk
   //--- is a fault.
   if(InpUseRiskPercent && intended > 0.0)
   {
      double pct_of_balance = risk_cash / balance * 100.0;
      if(lots <= vmin + 1e-9 && risk_cash > intended * 1.05)
         PrintFormat("  -> WARNING: at the %.2f minimum lot this risks %.2f "
                     "(%.2f%% of balance) against the %.2f%% intended. The "
                     "account is too small for this stop distance.",
                     vmin, risk_cash, pct_of_balance, InpRiskPercent);
      else if(risk_cash > intended * 1.05)
      {
         PrintFormat("  -> FAIL: would risk %.2f (%.2f%%) against an intended "
                     "%.2f (%.2f%%). Refusing to trade.",
                     risk_cash, pct_of_balance, intended, InpRiskPercent);
         fatal = true;
      }
      else
      {
         double shortfall = (intended - risk_cash) / intended * 100.0;
         double one_step  = vstep * per_lot;
         PrintFormat("  risk check      %.2f of %.2f intended (%.1f%% under, "
                     "one lot step = %.2f) - rounding down, OK",
                     risk_cash, intended, shortfall, one_step);
         //--- more than a full lot step under means something other than
         //--- rounding is shrinking the position
         if(risk_cash + one_step < intended * 0.99 && lots > vmin + 1e-9)
         {
            Print("  -> FAIL: position is smaller than lot-step rounding can "
                  "explain; sizing logic disagrees with the broker.");
            fatal = true;
         }
      }
   }

   //--- 7. margin, and the stop against the broker's minimum distance
   double margin = 0.0;
   if(OrderCalcMargin(ORDER_TYPE_BUY, _Symbol, lots, entry, margin))
   {
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      PrintFormat("  margin          %.2f for %.2f lots (%.1f%% of equity), leverage 1:%d",
                  margin, lots, (equity > 0.0) ? margin / equity * 100.0 : 0.0,
                  (int)AccountInfoInteger(ACCOUNT_LEVERAGE));
      if(equity > 0.0 && margin > equity * 0.5)
      {
         Print("  -> FAIL: one position would consume over half the account's margin.");
         fatal = true;
      }
   }
   if(g_stops_level > 0.0 && stop_dist <= g_stops_level)
   {
      PrintFormat("  -> FAIL: stop %.2f is inside the broker's minimum distance %.2f.",
                  stop_dist, g_stops_level);
      fatal = true;
   }

   //--- 8. news filter needs a calendar
   if(InpUseNewsFilter)
   {
      MqlCalendarValue vals[];
      int n = CalendarValueHistory(vals, TimeCurrent() - 86400, TimeCurrent() + 86400);
      PrintFormat("  news calendar   %d events in +/-24h%s", n,
                  (n <= 0) ? "  (unavailable)" : "");
      if(n <= 0 && InpNewsRequireCalendar)
      {
         Print("  -> FAIL: InpNewsRequireCalendar is set but the calendar is empty.");
         fatal = true;
      }
      else if(n <= 0)
         Print("  -> WARNING: news blackout requested but no calendar data; "
               "the EA will trade WITHOUT it.");
   }

   //--- 9. account limits in force
   PrintFormat("  account limits  daily %.1f%% / total %.1f%% from anchor %.2f%s",
               InpMaxDailyLossPct, InpMaxTotalLossPct, guards.Anchor(),
               guards.Halted() ? "   [CURRENTLY HALTED]" : "");

   Print(fatal ? "---------------- PREFLIGHT FAILED ----------------"
               : "---------------- preflight OK ----------------");
   return(!fatal);
}
