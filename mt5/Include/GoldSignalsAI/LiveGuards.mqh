//+------------------------------------------------------------------+
//|                                                   LiveGuards.mqh |
//|   Account-level protection for live / funded trading.            |
//|                                                                  |
//|   The strategy is allowed to be wrong. What is NOT allowed is for |
//|   it to breach a prop-firm limit while being wrong. These guards  |
//|   halt trading BEFORE the limit, not at it:                      |
//|                                                                  |
//|     daily loss   default 4%   (FTMO limit 5%)                     |
//|     total loss   default 8%   (FTMO limit 10%)                    |
//|                                                                  |
//|   The buffer is deliberate. FTMO measures on EQUITY including     |
//|   floating P&L, and a gap can move price past a stop, so halting  |
//|   exactly at the limit would still breach it.                    |
//|                                                                  |
//|   State survives a restart via terminal GlobalVariables, keyed on |
//|   the magic number - otherwise reattaching the EA after a halt    |
//|   would silently clear it, which is the failure mode that makes   |
//|   this kind of guard useless in practice.                        |
//+------------------------------------------------------------------+
#property strict

class CLiveGuards
{
private:
   long     m_magic;
   double   m_max_daily_pct;
   double   m_max_total_pct;
   int      m_max_failures;

   double   m_anchor_balance;     // balance when the guard first ran
   double   m_day_start_equity;
   int      m_day;                // server day-of-year currently tracked
   bool     m_halted_today;
   bool     m_halted_total;
   int      m_consec_failures;
   string   m_reason;

   string GvName(const string suffix) const
   {
      return(StringFormat("GSAI_%I64d_%s", m_magic, suffix));
   }

   double GvGet(const string suffix, const double fallback)
   {
      string n = GvName(suffix);
      if(GlobalVariableCheck(n))
         return(GlobalVariableGet(n));
      GlobalVariableSet(n, fallback);
      return(fallback);
   }

   void GvSet(const string suffix, const double value)
   {
      GlobalVariableSet(GvName(suffix), value);
   }

   int ServerDay() const
   {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      return(dt.day_of_year + dt.year * 1000);
   }

public:
   void Init(const long magic, const double max_daily_pct,
             const double max_total_pct, const int max_failures)
   {
      m_magic          = magic;
      m_max_daily_pct  = max_daily_pct;
      m_max_total_pct  = max_total_pct;
      m_max_failures   = max_failures;
      m_consec_failures = 0;
      m_reason         = "";

      //--- the anchor is the balance the total-loss limit is measured from, and
      //--- it must NOT move when the EA restarts
      m_anchor_balance = GvGet("anchor", AccountInfoDouble(ACCOUNT_BALANCE));
      m_halted_total   = (GvGet("halted_total", 0.0) > 0.5);
      m_day            = (int)GvGet("day", 0.0);

      int today = ServerDay();
      if(m_day != today)
      {
         m_day = today;
         m_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
         GvSet("day", (double)m_day);
         GvSet("day_equity", m_day_start_equity);
         GvSet("halted_today", 0.0);
         m_halted_today = false;
      }
      else
      {
         m_day_start_equity = GvGet("day_equity", AccountInfoDouble(ACCOUNT_EQUITY));
         m_halted_today = (GvGet("halted_today", 0.0) > 0.5);
      }

      PrintFormat("LiveGuards: anchor balance %.2f, day-start equity %.2f, "
                  "limits daily %.1f%% / total %.1f%%%s",
                  m_anchor_balance, m_day_start_equity,
                  m_max_daily_pct, m_max_total_pct,
                  m_halted_total ? "  [HALTED: total loss]"
                  : (m_halted_today ? "  [HALTED: daily loss]" : ""));
   }

   //--- call every tick. Returns true when a NEW halt has just fired, so the
   //--- caller can flatten.
   bool Check()
   {
      int today = ServerDay();
      if(today != m_day)
      {
         m_day = today;
         m_day_start_equity = AccountInfoDouble(ACCOUNT_EQUITY);
         m_halted_today = false;
         GvSet("day", (double)m_day);
         GvSet("day_equity", m_day_start_equity);
         GvSet("halted_today", 0.0);
         PrintFormat("LiveGuards: new trading day, equity %.2f", m_day_start_equity);
      }

      if(m_halted_total || m_halted_today)
         return(false);                       // already halted, nothing new

      double equity = AccountInfoDouble(ACCOUNT_EQUITY);

      if(m_anchor_balance > 0.0)
      {
         double total_loss_pct = (m_anchor_balance - equity) / m_anchor_balance * 100.0;
         if(total_loss_pct >= m_max_total_pct)
         {
            m_halted_total = true;
            GvSet("halted_total", 1.0);
            m_reason = StringFormat("total loss %.2f%% of %.2f (limit %.1f%%)",
                                    total_loss_pct, m_anchor_balance, m_max_total_pct);
            PrintFormat("LiveGuards: HALT - %s. No further trading.", m_reason);
            return(true);
         }
      }

      if(m_day_start_equity > 0.0)
      {
         double daily_loss_pct = (m_day_start_equity - equity) / m_anchor_balance * 100.0;
         if(daily_loss_pct >= m_max_daily_pct)
         {
            m_halted_today = true;
            GvSet("halted_today", 1.0);
            m_reason = StringFormat("daily loss %.2f%% (limit %.1f%%)",
                                    daily_loss_pct, m_max_daily_pct);
            PrintFormat("LiveGuards: HALT for today - %s.", m_reason);
            return(true);
         }
      }
      return(false);
   }

   void RecordOrderResult(const bool ok)
   {
      if(ok)
      {
         m_consec_failures = 0;
         return;
      }
      m_consec_failures++;
      if(m_max_failures > 0 && m_consec_failures >= m_max_failures)
      {
         m_halted_total = true;
         GvSet("halted_total", 1.0);
         m_reason = StringFormat("%d consecutive order failures", m_consec_failures);
         PrintFormat("LiveGuards: HALT - %s. Something is wrong with execution, "
                     "not with the strategy. Investigate before resetting.", m_reason);
      }
   }

   bool   Halted()  const { return(m_halted_total || m_halted_today); }
   bool   HaltedPermanently() const { return(m_halted_total); }
   string Reason()  const { return(m_reason); }
   double Anchor()  const { return(m_anchor_balance); }

   //--- deliberately manual: clearing a halt should be a decision, not a restart
   void Reset()
   {
      GlobalVariableDel(GvName("halted_total"));
      GlobalVariableDel(GvName("halted_today"));
      GlobalVariableDel(GvName("anchor"));
      GlobalVariableDel(GvName("day"));
      GlobalVariableDel(GvName("day_equity"));
      m_halted_total = false;
      m_halted_today = false;
      Print("LiveGuards: state cleared.");
   }
};
