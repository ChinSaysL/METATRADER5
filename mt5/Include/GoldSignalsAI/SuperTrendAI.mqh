//+------------------------------------------------------------------+
//|                                                  SuperTrendAI.mqh |
//|   LuxAlgo SuperTrend AI (k-means clustering) - the signal engine   |
//|   of pinescriptbot.pine, plus the author's chop / fakeout filter.  |
//|                                                                  |
//|   Ports pine lines 1310-1491 and 1509-1606. Mirrors               |
//|   research/engine.py exactly; research/parity_check.py proves it.  |
//|                                                                  |
//|   Evaluation order is load-bearing and is preserved verbatim:      |
//|     * in the multi-SuperTrend loop `trend` uses the PREVIOUS bar's |
//|       upper/lower, and `perf` uses the PREVIOUS bar's `output`;    |
//|     * in the final SuperTrend at the bottom upper/lower are        |
//|       updated BEFORE `os` - the two blocks really are ordered      |
//|       differently in the source (lines 1392-1394 vs 1482-1484).    |
//|                                                                  |
//|   Deliberate deviation: pine wraps the clustering in              |
//|   `if last_bar_index - bar_index <= maxData` (2500), a TradingView |
//|   execution-budget guard that on a chart means only the last 2500  |
//|   bars ever produce a signal. There is no such limit here, so a    |
//|   long backtest produces signals throughout.                      |
//+------------------------------------------------------------------+
#property strict

#include "PineMath.mqh"

#define STAI_STEP            0.5
#define STAI_PERF_ALPHA      10.0
#define STAI_MAX_ITER        250
#define STAI_CLUSTER_BEST    2       // fromCluster = 'Best' -> index 2 (pine line 1461)
#define STAI_MAX_FACTORS     64
#define STAI_CHOP_LOOKBACK   10
#define STAI_CHOP_RANK_LEN   200

//--- Filter Strength, matching the pine dropdown (line 56)
enum ENUM_STAI_STRENGTH
{
   STAI_LIGHT,     // Light
   STAI_MEDIUM,    // Medium
   STAI_STRONG,    // Strong
   STAI_CUSTOM     // Custom
};

//+------------------------------------------------------------------+
//| Engine inputs. Defaults are the pine defaults.                    |
//+------------------------------------------------------------------+
struct SStaiParams
{
   double              sensitivity;           // pine line 30
   int                 atr_length;            // pine line 31
   bool                filter_fakeouts;       // pine line 55
   ENUM_STAI_STRENGTH  fakeout_strength;      // pine line 56
   int                 custom_max_chop;       // pine line 57
   double              custom_min_quality;    // pine line 58
   double              custom_body_override;  // pine line 59
   int                 min_signal_strength;   // EA only: 1 keeps every signal
};

void StaiDefaults(SStaiParams &p)
{
   p.sensitivity          = 5.0;
   p.atr_length           = 10;
   p.filter_fakeouts      = false;
   p.fakeout_strength     = STAI_MEDIUM;
   p.custom_max_chop      = 60;
   p.custom_min_quality   = 0.0;
   p.custom_body_override = 0.0;
   p.min_signal_strength  = 1;
}

//+------------------------------------------------------------------+
//| The engine. Feed it closed bars in chronological order.           |
//+------------------------------------------------------------------+
class CSuperTrendAI
{
private:
   SStaiParams       m_p;

   //--- factor grid
   double            m_factors[STAI_MAX_FACTORS];
   int               m_nf;

   //--- one SuperTrend per factor
   double            m_upper[STAI_MAX_FACTORS];
   double            m_lower[STAI_MAX_FACTORS];
   int               m_trend[STAI_MAX_FACTORS];
   double            m_perf[STAI_MAX_FACTORS];
   double            m_output[STAI_MAX_FACTORS];
   double            m_perf_w;

   //--- final SuperTrend
   double            m_f_upper;
   double            m_f_lower;
   int               m_os;
   int               m_os_prev;
   double            m_target_factor;
   double            m_perf_idx;
   double            m_ts;

   //--- helpers
   CPmRma            m_atr;
   CPmEma            m_den;
   double            m_prev_close;
   bool              m_started;
   long              m_bars;

   //--- chop filter
   CPmRing           m_high;
   CPmRing           m_low;
   CPmPercentRank    m_rank;
   double            m_overlap_ratio;
   double            m_overlap_rank;
   double            m_body_atr;

   //--- per-bar results
   double            m_atr_value;
   int               m_strength;
   bool              m_raw_buy;
   bool              m_raw_sell;
   bool              m_passes;
   bool              m_buy;
   bool              m_sell;

   //--- k-means scratch (avoids per-bar allocation)
   double            m_centroids[3];
   double            m_new_centroids[3];
   double            m_cl_perf[3][STAI_MAX_FACTORS];
   double            m_cl_fact[3][STAI_MAX_FACTORS];
   int               m_cl_count[3];

   void              BuildFactors();
   void              RunKMeans();
   void              Thresholds(double &max_overlap, double &min_quality, double &body_override);

public:
                     CSuperTrendAI() { StaiDefaults(m_p); }

   bool              Init(const SStaiParams &p);
   void              OnBar(const double o, const double h, const double l, const double c);

   //--- state
   int               Os()            const { return(m_os); }
   int               OsPrev()        const { return(m_os_prev); }
   double            Ts()            const { return(m_ts); }
   double            Atr()           const { return(m_atr_value); }
   double            TargetFactor()  const { return(m_target_factor); }
   double            PerfIdx()       const { return(m_perf_idx); }
   double            OverlapRatio()  const { return(m_overlap_ratio); }
   double            OverlapRank()   const { return(m_overlap_rank); }
   double            BodyAtr()       const { return(m_body_atr); }
   int               SignalStrength()const { return(m_strength); }
   int               FactorCount()   const { return(m_nf); }
   long              BarsProcessed() const { return(m_bars); }

   //--- signals for the bar just fed
   bool              RawBuy()        const { return(m_raw_buy); }
   bool              RawSell()       const { return(m_raw_sell); }
   bool              PassesFilter()  const { return(m_passes); }
   bool              Buy()           const { return(m_buy); }
   bool              Sell()          const { return(m_sell); }
   bool              BlockedBuy()    const { return(m_raw_buy && !m_buy); }
   bool              BlockedSell()   const { return(m_raw_sell && !m_sell); }
};

//+------------------------------------------------------------------+
//| pine lines 1314-1315, 1378-1379                                   |
//+------------------------------------------------------------------+
void CSuperTrendAI::BuildFactors()
{
   double min_mult = MathMax(m_p.sensitivity - 4.0, 1.0);
   double max_mult = MathMin(m_p.sensitivity, 26.0);
   int    count    = (int)((max_mult - min_mult) / STAI_STEP);   // Pine's for-to is inclusive
   m_nf = MathMin(count + 1, STAI_MAX_FACTORS);
   for(int i = 0; i < m_nf; i++)
      m_factors[i] = min_mult + i * STAI_STEP;
}

//+------------------------------------------------------------------+
bool CSuperTrendAI::Init(const SStaiParams &p)
{
   m_p = p;
   if(MathMax(m_p.sensitivity - 4.0, 1.0) > MathMin(m_p.sensitivity, 26.0))
   {
      Print("CSuperTrendAI: minimum factor is greater than maximum factor in the range");
      return(false);
   }
   BuildFactors();

   m_perf_w = 2.0 / (STAI_PERF_ALPHA + 1.0);
   for(int i = 0; i < m_nf; i++)
   {
      m_upper[i]  = 0.0;
      m_lower[i]  = 0.0;
      m_trend[i]  = 0;
      m_perf[i]   = 0.0;
      m_output[i] = PmNaN();
   }

   m_f_upper       = 0.0;
   m_f_lower       = 0.0;
   m_os            = 0;
   m_os_prev       = 0;
   m_target_factor = PmNaN();
   m_perf_idx      = PmNaN();
   m_ts            = PmNaN();

   m_atr.Init(m_p.atr_length);
   m_den.Init((int)STAI_PERF_ALPHA);
   m_prev_close = PmNaN();
   m_started    = false;
   m_bars       = 0;

   m_high.Init(STAI_CHOP_LOOKBACK + 2);
   m_low.Init(STAI_CHOP_LOOKBACK + 2);
   m_rank.Init(STAI_CHOP_RANK_LEN);
   m_overlap_ratio = PmNaN();
   m_overlap_rank  = PmNaN();
   m_body_atr      = 0.0;

   m_atr_value = PmNaN();
   m_strength  = 1;
   m_raw_buy   = false;
   m_raw_sell  = false;
   m_passes    = true;
   m_buy       = false;
   m_sell      = false;
   return(true);
}

//+------------------------------------------------------------------+
//| pine lines 1405-1473. An empty cluster yields a NaN centroid,      |
//| every distance to it is NaN, Pine's array.min ignores NaN, so it   |
//| stays empty for good - reproduced here by skipping NaN centroids.  |
//+------------------------------------------------------------------+
void CSuperTrendAI::RunKMeans()
{
   m_centroids[0] = PmPercentileLinear(m_perf, m_nf, 25);
   m_centroids[1] = PmPercentileLinear(m_perf, m_nf, 50);
   m_centroids[2] = PmPercentileLinear(m_perf, m_nf, 75);

   for(int iter = 0; iter <= STAI_MAX_ITER; iter++)   // Pine's `for _ = 0 to maxIter`
   {
      m_cl_count[0] = 0;
      m_cl_count[1] = 0;
      m_cl_count[2] = 0;

      for(int i = 0; i < m_nf; i++)
      {
         double value = m_perf[i];
         int    best  = -1;
         double bestd = 0.0;
         for(int ci = 0; ci < 3; ci++)
         {
            if(PmIsNa(m_centroids[ci]))
               continue;
            double d = MathAbs(value - m_centroids[ci]);
            if(best < 0 || d < bestd)
            {
               best  = ci;
               bestd = d;
            }
         }
         if(best < 0)
            continue;
         int k = m_cl_count[best];
         m_cl_perf[best][k] = value;
         m_cl_fact[best][k] = m_factors[i];
         m_cl_count[best]   = k + 1;
      }

      bool same = true;
      for(int ci = 0; ci < 3; ci++)
      {
         if(m_cl_count[ci] <= 0)
            m_new_centroids[ci] = PmNaN();
         else
         {
            double sum = 0.0;
            for(int k = 0; k < m_cl_count[ci]; k++)
               sum += m_cl_perf[ci][k];
            m_new_centroids[ci] = sum / m_cl_count[ci];
         }
         // NaN == NaN is false, so a collapsed centroid never lets it break.
         if(!(m_new_centroids[ci] == m_centroids[ci]))
            same = false;
      }
      if(same)
         break;
      for(int ci = 0; ci < 3; ci++)
         m_centroids[ci] = m_new_centroids[ci];
   }

   int best_n = m_cl_count[STAI_CLUSTER_BEST];
   if(best_n > 0)
   {
      double fsum = 0.0, psum = 0.0;
      for(int k = 0; k < best_n; k++)
      {
         fsum += m_cl_fact[STAI_CLUSTER_BEST][k];
         psum += m_cl_perf[STAI_CLUSTER_BEST][k];
      }
      m_target_factor = fsum / best_n;                    // nz(..., target_factor)
      double d = m_den.Value();
      if(!PmIsNa(d) && d != 0.0)
         m_perf_idx = MathMax(psum / best_n, 0.0) / d;
   }
   else
   {
      // Best cluster empty: target_factor keeps its previous value (nz), and
      // perf_idx becomes max(nz(na),0)/den = 0.
      double d = m_den.Value();
      if(!PmIsNa(d) && d != 0.0)
         m_perf_idx = 0.0;
   }
}

//+------------------------------------------------------------------+
//| pine lines 1537-1547. Medium and Strong share the same chop level; |
//| only the strong-candle override differs between them.             |
//+------------------------------------------------------------------+
void CSuperTrendAI::Thresholds(double &max_overlap, double &min_quality, double &body_override)
{
   if(m_p.fakeout_strength == STAI_CUSTOM)
   {
      max_overlap   = (double)m_p.custom_max_chop;
      min_quality   = m_p.custom_min_quality;
      body_override = (m_p.custom_body_override <= 0.0) ? 1e10 : m_p.custom_body_override;
      return;
   }
   max_overlap = (m_p.fakeout_strength == STAI_LIGHT) ? 85.0 : 80.0;
   min_quality = 0.3;
   if(m_p.fakeout_strength == STAI_LIGHT)
      body_override = 0.6;
   else if(m_p.fakeout_strength == STAI_MEDIUM)
      body_override = 0.7;
   else
      body_override = 1e10;                              // Strong: no override
}

//+------------------------------------------------------------------+
//| Advance the engine by one closed bar.                             |
//+------------------------------------------------------------------+
void CSuperTrendAI::OnBar(const double o, const double h, const double l, const double c)
{
   double hl2 = (h + l) / 2.0;

   if(!m_started)
   {
      // supertrend.new() defaults upper/lower to hl2 of the first bar (pine
      // lines 1360-1361, 1476-1477).
      for(int i = 0; i < m_nf; i++)
      {
         m_upper[i] = hl2;
         m_lower[i] = hl2;
      }
      m_f_upper = hl2;
      m_f_lower = hl2;
      m_started = true;
   }

   double a  = m_atr.Update(PmTrueRange(h, l, m_prev_close));
   double pc = m_prev_close;

   // perf_idx denominator: ta.ema(|close - close[1]|, 10). NaN on bar 0, which
   // Pine's ta.ema skips (pine line 1466).
   m_den.Update(PmIsNa(pc) ? PmNaN() : MathAbs(c - pc));

   //--- multi-SuperTrend + performance scores (pine lines 1386-1400)
   for(int i = 0; i < m_nf; i++)
   {
      double f  = m_factors[i];
      double up = hl2 + a * f;
      double dn = hl2 - a * f;

      if(c > m_upper[i])
         m_trend[i] = 1;
      else if(c < m_lower[i])
         m_trend[i] = 0;

      m_upper[i] = (pc < m_upper[i]) ? MathMin(up, m_upper[i]) : up;
      m_lower[i] = (pc > m_lower[i]) ? MathMax(dn, m_lower[i]) : dn;

      double diff = PmNz(PmSign(pc - m_output[i]), 0.0);
      double chg  = PmIsNa(c - pc) ? 0.0 : (c - pc);
      m_perf[i] += m_perf_w * (chg * diff - m_perf[i]);

      m_output[i] = (m_trend[i] == 1) ? m_lower[i] : m_upper[i];
   }

   //--- k-means over the performance scores
   RunKMeans();

   //--- final SuperTrend (pine lines 1476-1485; upper/lower BEFORE os)
   double up = hl2 + a * m_target_factor;
   double dn = hl2 - a * m_target_factor;
   m_f_upper = (pc < m_f_upper) ? MathMin(up, m_f_upper) : up;
   m_f_lower = (pc > m_f_lower) ? MathMax(dn, m_f_lower) : dn;

   m_os_prev = m_os;
   if(c > m_f_upper)
      m_os = 1;
   else if(c < m_f_lower)
      m_os = 0;
   m_ts = (m_os != 0) ? m_f_lower : m_f_upper;

   //--- chop / fakeout inputs (pine lines 1519-1532)
   m_high.Push(h);
   m_low.Push(l);
   if(m_high.Count() >= STAI_CHOP_LOOKBACK + 1)
   {
      double overlap_sum = 0.0;
      double range_sum   = 0.0;
      for(int j = 0; j < STAI_CHOP_LOOKBACK; j++)
      {
         double h0 = m_high.At(j),     l0 = m_low.At(j);
         double h1 = m_high.At(j + 1), l1 = m_low.At(j + 1);
         double ov = MathMin(h0, h1) - MathMax(l0, l1);
         overlap_sum += (ov > 0.0) ? ov : 0.0;
         range_sum   += h0 - l0;
      }
      m_overlap_ratio = (range_sum > 0.0) ? overlap_sum / range_sum : 0.0;
      m_overlap_rank  = m_rank.Update(m_overlap_ratio);
   }
   m_body_atr  = (a > 0.0) ? MathAbs(c - o) / a : 0.0;
   m_atr_value = a;

   //--- signal strength (pine line 1581)
   double pidx = PmNz(m_perf_idx, 0.0);
   int    s10  = (int)(pidx * 10.0);
   m_strength = (s10 < 2) ? 1 : (s10 < 4) ? 2 : (s10 < 5) ? 3 : 4;

   //--- filter + signals (pine lines 1550-1600)
   double max_overlap, min_quality, body_override;
   Thresholds(max_overlap, min_quality, body_override);

   bool chop_ok = PmIsNa(m_overlap_rank) || m_overlap_rank <= max_overlap;
   bool quality_ok = true;
   if(min_quality > 0.0)
      quality_ok = (PmNz(m_perf_idx, 0.0) >= min_quality) || (m_body_atr >= body_override);
   m_passes = (!m_p.filter_fakeouts) || (chop_ok && quality_ok);

   m_raw_buy  = (m_bars > 0) && (m_os > m_os_prev);
   m_raw_sell = (m_bars > 0) && (m_os < m_os_prev);
   bool strong_enough = (m_strength >= m_p.min_signal_strength);
   m_buy  = m_raw_buy  && m_passes && strong_enough;
   m_sell = m_raw_sell && m_passes && strong_enough;

   m_prev_close = c;
   m_bars++;
}
