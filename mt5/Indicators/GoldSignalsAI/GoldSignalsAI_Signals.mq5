//+------------------------------------------------------------------+
//|                                        GoldSignalsAI_Signals.mq5 |
//|   Visual counterpart of GoldSignalsAI_EA: the same ported engine,  |
//|   drawn on the chart so its arrows can be compared against the     |
//|   TradingView original bar for bar.                               |
//|                                                                  |
//|   Signals are produced at bar close and never move afterwards.     |
//+------------------------------------------------------------------+
#property copyright "Port of pinescriptbot.pine"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4

//--- adaptive trailing stop
#property indicator_label1  "Adaptive Trail"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrDodgerBlue
#property indicator_style1  STYLE_SOLID
#property indicator_width1  1

#property indicator_label2  "Buy"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrTeal
#property indicator_width2  2

#property indicator_label3  "Sell"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrRed
#property indicator_width3  2

#property indicator_label4  "Filtered"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrDimGray
#property indicator_width4  1

#include "..\\..\\Include\\GoldSignalsAI\\SuperTrendAI.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\ReversalExit.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\Diagnostics.mqh"

input double            InpSensitivity       = 5.0;    // Signal Sensitivity (pine line 30)
input int               InpAtrLength         = 10;     // Signal Tuner / ATR length (pine line 31)
input int               InpMinSignalStrength = 1;      // Min AI signal strength 1-4
input bool              InpFilterFakeouts    = false;  // Filter Fakeouts / Chop (pine line 55)
input ENUM_STAI_STRENGTH InpFakeoutStrength  = STAI_MEDIUM; // Filter Strength
input int               InpCustomMaxChop     = 60;     // Custom: Max Chop Level
input double            InpCustomMinQuality  = 0.0;    // Custom: Min Signal Quality
input double            InpCustomBodyOverride= 0.0;    // Custom: Strong Candle Override
input bool              InpMarkFiltered      = false;  // Mark Filtered Signals (pine line 60)
input bool              InpShowTrail         = true;   // Draw the adaptive trailing stop
input bool              InpShowDashboard     = true;   // Print the live readings in the comment
input ENUM_REX_SENSITIVITY InpReversalSens   = REX_NORMAL; // Reversal sensitivity (dump only)
input string            InpDumpFile          = "";     // Parity CSV name ("" = off)
input bool              InpDumpCloseTerminal = false;  // Close the terminal after dumping

double BufTrail[];
double BufBuy[];
double BufSell[];
double BufFiltered[];

CSuperTrendAI engine;
CReversalExit reversal;
CDiagnostics  diag;
int           g_fed = 0;      // number of bars already fed to the engine
bool          g_dump_done = false;

//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, BufTrail,    INDICATOR_DATA);
   SetIndexBuffer(1, BufBuy,      INDICATOR_DATA);
   SetIndexBuffer(2, BufSell,     INDICATOR_DATA);
   SetIndexBuffer(3, BufFiltered, INDICATOR_DATA);

   PlotIndexSetInteger(1, PLOT_ARROW, 233);
   PlotIndexSetInteger(2, PLOT_ARROW, 234);
   PlotIndexSetInteger(3, PLOT_ARROW, 251);
   for(int i = 0; i < 4; i++)
      PlotIndexSetDouble(i, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   IndicatorSetString(INDICATOR_SHORTNAME,
                      StringFormat("GoldSignalsAI (%.1f / %d)", InpSensitivity, InpAtrLength));
   IndicatorSetInteger(INDICATOR_DIGITS, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
bool ResetEngine()
{
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
   g_fed = 0;
   reversal.Init(InpReversalSens);
   return(engine.Init(p));
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < 2)
      return(0);

   //--- The engine is recursive, so a history rebuild means starting over.
   if(prev_calculated <= 0 || g_fed > rates_total)
   {
      if(!ResetEngine())
         return(0);
      ArrayInitialize(BufTrail,    EMPTY_VALUE);
      ArrayInitialize(BufBuy,      EMPTY_VALUE);
      ArrayInitialize(BufSell,     EMPTY_VALUE);
      ArrayInitialize(BufFiltered, EMPTY_VALUE);
   }

   //--- Parity dump: an indicator recalculates over cached history the moment the
   //--- chart loads, with no ticks and no trade-server session, which is why this
   //--- lives here rather than in the EA. See tools\run_dump.ps1.
   bool dumping = (InpDumpFile != "" && !g_dump_done);
   if(dumping && !diag.Enabled() && !diag.Open(InpDumpFile))
      dumping = false;

   //--- Only CLOSED bars are fed, so nothing ever repaints.
   int last_closed = rates_total - 2;
   for(int i = g_fed; i <= last_closed; i++)
   {
      engine.OnBar(open[i], high[i], low[i], close[i]);
      if(dumping)
      {
         reversal.OnBar(high[i], low[i], close[i]);
         diag.Write(time[i], open[i], high[i], low[i], close[i], engine, reversal, true);
      }

      BufTrail[i]    = InpShowTrail && MathIsValidNumber(engine.Ts()) ? engine.Ts() : EMPTY_VALUE;
      BufBuy[i]      = engine.Buy()  ? low[i]  : EMPTY_VALUE;
      BufSell[i]     = engine.Sell() ? high[i] : EMPTY_VALUE;
      BufFiltered[i] = EMPTY_VALUE;
      if(InpMarkFiltered)
      {
         if(engine.BlockedBuy())
            BufFiltered[i] = low[i];
         else if(engine.BlockedSell())
            BufFiltered[i] = high[i];
      }
      g_fed = i + 1;
   }

   if(dumping)
   {
      diag.Close();
      g_dump_done = true;
      PrintFormat("GoldSignalsAI dump: %d closed bars of %s %s -> MQL5\\Files\\%s",
                  last_closed + 1, _Symbol,
                  EnumToString((ENUM_TIMEFRAMES)Period()), InpDumpFile);
      if(InpDumpCloseTerminal)
         TerminalClose(0);
   }

   //--- the forming bar carries no signal yet
   BufTrail[rates_total - 1]    = EMPTY_VALUE;
   BufBuy[rates_total - 1]      = EMPTY_VALUE;
   BufSell[rates_total - 1]     = EMPTY_VALUE;
   BufFiltered[rates_total - 1] = EMPTY_VALUE;

   if(InpShowDashboard)
   {
      string chop = MathIsValidNumber(engine.OverlapRank())
                    ? StringFormat("%.0f%%", engine.OverlapRank()) : "n/a";
      Comment(StringFormat(
                 "GoldSignalsAI  %s %s\n"
                 "trend            %s\n"
                 "adaptive factor  %.2f\n"
                 "signal quality   %.3f  (strength %d)\n"
                 "chop reading     %s  %s\n"
                 "candle body      %.2f ATR",
                 _Symbol, EnumToString((ENUM_TIMEFRAMES)Period()),
                 engine.Os() != 0 ? "UP" : "DOWN",
                 engine.TargetFactor(),
                 MathIsValidNumber(engine.PerfIdx()) ? engine.PerfIdx() : 0.0,
                 engine.SignalStrength(),
                 chop,
                 InpFilterFakeouts ? "(filter on)" : "(filter off)",
                 engine.BodyAtr()));
   }

   return(rates_total);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   diag.Close();
   Comment("");
}
//+------------------------------------------------------------------+
