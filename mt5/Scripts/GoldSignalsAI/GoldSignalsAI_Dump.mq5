//+------------------------------------------------------------------+
//|                                            GoldSignalsAI_Dump.mq5 |
//|   Runs the ported engine over cached chart history and writes the  |
//|   per-bar diagnostics CSV - bars included, so the file is both the |
//|   parity reference AND the price data for research/.               |
//|                                                                  |
//|   Why a script instead of the Strategy Tester: the tester needs a   |
//|   live trade-server session and refuses to start a local agent      |
//|   without one. A script runs on a chart, and a chart opens happily  |
//|   from cached history with the terminal offline - so this works on  |
//|   an unauthorised terminal, which the tester does not.              |
//|                                                                  |
//|   Meant to be launched from a [StartUp] config; see                |
//|   tools\run_dump.ps1. It closes the terminal when done unless told  |
//|   otherwise.                                                      |
//+------------------------------------------------------------------+
#property copyright "Port of pinescriptbot.pine"
#property version   "1.00"
#property strict
// NO script_show_inputs on purpose: it opens a modal input dialog and would hang
// an unattended /config run forever. Parameters come from a .set preset instead
// (tools\run_dump.ps1 writes one into MQL5\Presets).

#include "..\\..\\Include\\GoldSignalsAI\\SuperTrendAI.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\ReversalExit.mqh"
#include "..\\..\\Include\\GoldSignalsAI\\Diagnostics.mqh"

input int               InpBars              = 200000; // Bars to dump (0 = all available)
input double            InpSensitivity       = 5.0;    // Signal Sensitivity (pine line 30)
input int               InpAtrLength         = 10;     // Signal Tuner / ATR length (pine line 31)
input int               InpMinSignalStrength = 1;      // Min AI signal strength 1-4
input bool              InpFilterFakeouts    = false;  // Filter Fakeouts / Chop (pine line 55)
input ENUM_STAI_STRENGTH InpFakeoutStrength  = STAI_MEDIUM; // Filter Strength
input int               InpCustomMaxChop     = 60;     // Custom: Max Chop Level
input double            InpCustomMinQuality  = 0.0;    // Custom: Min Signal Quality
input double            InpCustomBodyOverride= 0.0;    // Custom: Strong Candle Override
input ENUM_REX_SENSITIVITY InpReversalSens   = REX_NORMAL; // Reversal sensitivity
input string            InpDumpFile          = "GoldSignalsAI_diag.csv"; // Output CSV
input bool              InpCloseTerminal     = true;   // Close the terminal when finished

//+------------------------------------------------------------------+
void OnStart()
{
   //--- Wait for the cached history to be mapped into the chart. Offline this is
   //--- quick, but the first access to a symbol can still take a moment.
   int bars = 0;
   for(int attempt = 0; attempt < 50; attempt++)
   {
      bars = Bars(_Symbol, PERIOD_CURRENT);
      if(bars > 1000)
         break;
      Sleep(200);
   }
   PrintFormat("GoldSignalsAI_Dump: %s %s, %d bars available",
               _Symbol, EnumToString((ENUM_TIMEFRAMES)Period()), bars);
   if(bars < 300)
   {
      Print("GoldSignalsAI_Dump: not enough cached history to be useful. Aborting.");
      if(InpCloseTerminal)
         TerminalClose(2);
      return;
   }

   int want = (InpBars <= 0) ? bars : MathMin(InpBars, bars);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);          // oldest first
   int got = CopyRates(_Symbol, PERIOD_CURRENT, 0, want, rates);
   if(got <= 0)
   {
      PrintFormat("GoldSignalsAI_Dump: CopyRates failed (error %d)", GetLastError());
      if(InpCloseTerminal)
         TerminalClose(3);
      return;
   }

   //--- The last element is the still-forming bar; drop it so every dumped bar is
   //--- closed, which is the only thing the engine is ever fed.
   int last = got - 1;
   if(last > 0 && rates[last].time == iTime(_Symbol, PERIOD_CURRENT, 0))
      last--;

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

   CSuperTrendAI engine;
   CReversalExit reversal;
   CDiagnostics  diag;

   if(!engine.Init(p))
   {
      Print("GoldSignalsAI_Dump: engine init failed");
      if(InpCloseTerminal)
         TerminalClose(4);
      return;
   }
   reversal.Init(InpReversalSens);

   if(!diag.Open(InpDumpFile))
   {
      if(InpCloseTerminal)
         TerminalClose(5);
      return;
   }

   int buys = 0, sells = 0;
   for(int i = 0; i <= last; i++)
   {
      engine.OnBar(rates[i].open, rates[i].high, rates[i].low, rates[i].close);
      reversal.OnBar(rates[i].high, rates[i].low, rates[i].close);
      diag.Write(rates[i].time, rates[i].open, rates[i].high, rates[i].low,
                 rates[i].close, engine, reversal, true);
      if(engine.Buy())
         buys++;
      if(engine.Sell())
         sells++;
   }
   diag.Close();

   PrintFormat("GoldSignalsAI_Dump: %d closed bars, %s -> %s, %d buys / %d sells, %d factors",
               last + 1, TimeToString(rates[0].time), TimeToString(rates[last].time),
               buys, sells, engine.FactorCount());
   PrintFormat("GoldSignalsAI_Dump: wrote MQL5\\Files\\%s", InpDumpFile);

   if(InpCloseTerminal)
      TerminalClose(0);
}
//+------------------------------------------------------------------+
