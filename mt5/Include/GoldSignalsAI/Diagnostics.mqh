//+------------------------------------------------------------------+
//|                                                  Diagnostics.mqh |
//|   Per-bar CSV dump of the engine's internals, so                  |
//|   research/parity_check.py can prove the MQL5 port and the Python |
//|   reference agree bar for bar. Nothing here affects trading.      |
//|                                                                  |
//|   In the Strategy Tester the file lands in                        |
//|   <data folder>/Tester/<agent>/MQL5/Files/, on a live chart in     |
//|   <data folder>/MQL5/Files/.                                     |
//+------------------------------------------------------------------+
#property strict

#include "SuperTrendAI.mqh"
#include "ReversalExit.mqh"

class CDiagnostics
{
private:
   int               m_handle;
   bool              m_enabled;
   long              m_rows;

public:
                     CDiagnostics(): m_handle(INVALID_HANDLE), m_enabled(false), m_rows(0) {}

   bool Open(const string filename)
   {
      m_handle = FileOpen(filename, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(m_handle == INVALID_HANDLE)
      {
         PrintFormat("CDiagnostics: cannot open %s (error %d)", filename, GetLastError());
         return(false);
      }
      FileWrite(m_handle,
                "time", "open", "high", "low", "close",
                "atr", "target_factor", "perf_idx", "os", "ts",
                "overlap_ratio", "overlap_rank", "body_atr",
                "signal_strength", "raw_buy", "raw_sell", "passes_filter",
                "buy", "sell", "condition", "bear_exhaustion", "bull_exhaustion");
      m_enabled = true;
      return(true);
   }

   //--- NaN would be written as "nan"/"-nan(ind)" inconsistently; use an empty
   //--- field instead so the Python side reads it straight back as NaN.
   string Num(const double v, const int digits = 10)
   {
      if(!MathIsValidNumber(v))
         return("");
      return(DoubleToString(v, digits));
   }

   void Write(const datetime t,
              const double o, const double h, const double l, const double c,
              CSuperTrendAI &eng,
              CReversalExit &rex,
              const bool have_rex)
   {
      if(!m_enabled || m_handle == INVALID_HANDLE)
         return;
      FileWrite(m_handle,
                (string)(long)t,
                Num(o, 5), Num(h, 5), Num(l, 5), Num(c, 5),
                Num(eng.Atr()), Num(eng.TargetFactor()), Num(eng.PerfIdx()),
                (string)eng.Os(), Num(eng.Ts()),
                Num(eng.OverlapRatio()), Num(eng.OverlapRank()), Num(eng.BodyAtr()),
                (string)eng.SignalStrength(),
                (string)(int)eng.RawBuy(), (string)(int)eng.RawSell(),
                (string)(int)eng.PassesFilter(),
                (string)(int)eng.Buy(), (string)(int)eng.Sell(),
                have_rex ? (string)rex.Condition() : "",
                have_rex ? (string)(int)rex.BearishExhaustion() : "",
                have_rex ? (string)(int)rex.BullishExhaustion() : "");
      m_rows++;
   }

   void Close()
   {
      if(m_handle != INVALID_HANDLE)
      {
         FileFlush(m_handle);
         FileClose(m_handle);
         PrintFormat("CDiagnostics: wrote %d rows", (int)m_rows);
      }
      m_handle = INVALID_HANDLE;
      m_enabled = false;
   }

   bool Enabled() const { return(m_enabled); }
};
