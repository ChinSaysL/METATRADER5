//+------------------------------------------------------------------+
//|                                                  ReversalExit.mqh |
//|   The script's Take-Profit Warning, used here as an exit rule.     |
//|                                                                  |
//|   Two mechanisms, both fully contained in pinescriptbot.pine:      |
//|     * Mean Reversion Channel (lines 499-719) - a SuperSmoother     |
//|       mean with bands at pi*1.0 and pi*2.415 mean-ranges, and the  |
//|       `condition` code saying how deep into the reversal zone      |
//|       price is;                                                   |
//|     * WaveTrend 3D (lines 999-1304) - three normalised-derivative  |
//|       oscillators plus a Gaussian-kernel trend check, producing    |
//|       "trigger waves".                                            |
//|                                                                  |
//|   The exhaustion switch is pine lines 1649-1657 MINUS the Smart    |
//|   Trail and Trend Catcher flips: those live in the closed-source   |
//|   ayvaliktrading/EyopsTelegram library and were deliberately not   |
//|   reconstructed, so this fires somewhat less often than on         |
//|   TradingView. That is a coverage difference, not a logic one.     |
//|                                                                  |
//|   Mirrors research/reversal_exit.py.                              |
//|                                                                  |
//|   Two behaviours that are faithful to the original, NOT bugs:      |
//|     * meanrange goes negative for roughly the first 50 bars of a   |
//|       series - the SuperSmoother has c1 ~ 0.004 against c2 ~ 1.91, |
//|       so Pine's nz(ss[1], src[1]) seeding overshoots before it     |
//|       settles. TradingView does the same. It sits far inside the    |
//|       EA's warm-up window.                                        |
//|     * `condition` is almost always +/-4 or +/-5; the outer zone at  |
//|       pi*2.415 ~ 7.6 mean-ranges is rarely touched, exactly as the  |
//|       script's own comment at lines 1624-1627 says. The exit is     |
//|       therefore carried mostly by the momentum zero-cross and the   |
//|       trigger waves.                                              |
//+------------------------------------------------------------------+
#property strict

#include "PineMath.mqh"

//--- Mean reversion channel inputs (pine lines 500-524)
#define REX_MRC_LENGTH       100
#define REX_MRC_INNER_MULT   1.0
#define REX_MRC_OUTER_MULT   2.415
#define REX_MRC_GRADSIZE     0.5

//--- WaveTrend 3D inputs (pine lines 1006-1024)
#define REX_OSC_LOOKBACK     20.0
#define REX_QMEAN_LENGTH     50
#define REX_F_LENGTH         0.75
#define REX_F_SMOOTHING      0.45
#define REX_N_LENGTH         1.00
#define REX_N_SMOOTHING      1.00
#define REX_S_LENGTH         1.75
#define REX_S_SMOOTHING      2.50

#define REX_DIV_THRESHOLD    30      // pine line 1027
#define REX_SIZE_PERCENT     40.0    // pine line 1028
#define REX_KERNEL_LOOKBACK  6.0     // kernels.gaussian(seriesEmphasis, 6, 6)
#define REX_KERNEL_START     6

//--- Warning Sensitivity, matching the pine dropdown (line 65)
enum ENUM_REX_SENSITIVITY
{
   REX_EARLY,    // Early
   REX_NORMAL,   // Normal
   REX_LATE      // Late
};

//+------------------------------------------------------------------+
class CReversalExit
{
private:
   ENUM_REX_SENSITIVITY m_sens;

   //--- mean reversion channel
   CPmSuperSmoother  m_mean;
   CPmSuperSmoother  m_range;
   double            m_prev_close;
   double            m_meanline;
   double            m_meanrange;
   double            m_upband1, m_loband1;
   double            m_upband2, m_loband2;
   int               m_condition;

   //--- WaveTrend 3D
   CPmNormalizeDeriv m_nderiv;
   CPmDualPole       m_fast, m_normal, m_slow;
   CPmGaussianKernel m_kernel;
   CPmCross          m_x_fast_normal;
   CPmCross          m_x_normal_zero;
   double            m_series_fast, m_series_normal, m_series_slow;
   double            m_yhat0;

   bool              m_bear_cross, m_bull_cross;
   bool              m_prev_bear_cross, m_prev_bull_cross;
   double            m_last_bear_value, m_prev_bear_value;
   double            m_last_bull_value, m_prev_bull_value;
   double            m_bs_bear, m_bs_bull;      // ta.barssince, NaN = never
   bool              m_bear_trigger, m_bull_trigger;
   bool              m_osc_bear, m_osc_bull;

   //--- results
   bool              m_bearish_exhaustion;
   bool              m_bullish_exhaustion;
   long              m_bars;

public:
   void              Init(const ENUM_REX_SENSITIVITY sensitivity);
   void              OnBar(const double h, const double l, const double c);

   bool              BearishExhaustion() const { return(m_bearish_exhaustion); }
   bool              BullishExhaustion() const { return(m_bullish_exhaustion); }
   int               Condition()         const { return(m_condition); }
   double            UpBand1()           const { return(m_upband1); }
   double            LoBand1()           const { return(m_loband1); }
   double            SeriesNormal()      const { return(m_series_normal); }
   double            SeriesSlow()        const { return(m_series_slow); }
};

//+------------------------------------------------------------------+
void CReversalExit::Init(const ENUM_REX_SENSITIVITY sensitivity)
{
   m_sens = sensitivity;

   m_mean.Init(REX_MRC_LENGTH);
   m_range.Init(REX_MRC_LENGTH);
   m_prev_close = PmNaN();
   m_meanline = PmNaN();
   m_meanrange = PmNaN();
   m_upband1 = PmNaN();
   m_loband1 = PmNaN();
   m_upband2 = PmNaN();
   m_loband2 = PmNaN();
   m_condition = 0;

   m_nderiv.Init(REX_QMEAN_LENGTH);
   m_fast.Init(REX_F_SMOOTHING * REX_OSC_LOOKBACK);
   m_normal.Init(REX_N_SMOOTHING * REX_OSC_LOOKBACK);
   m_slow.Init(REX_S_SMOOTHING * REX_OSC_LOOKBACK);
   m_kernel.Init(REX_KERNEL_LOOKBACK, REX_KERNEL_START);
   m_x_fast_normal.Init();
   m_x_normal_zero.Init();

   m_series_fast = 0.0;
   m_series_normal = 0.0;
   m_series_slow = 0.0;
   m_yhat0 = 0.0;

   m_bear_cross = false;
   m_bull_cross = false;
   m_prev_bear_cross = false;
   m_prev_bull_cross = false;
   m_last_bear_value = PmNaN();
   m_prev_bear_value = PmNaN();
   m_last_bull_value = PmNaN();
   m_prev_bull_value = PmNaN();
   m_bs_bear = PmNaN();
   m_bs_bull = PmNaN();
   m_bear_trigger = false;
   m_bull_trigger = false;
   m_osc_bear = false;
   m_osc_bull = false;

   m_bearish_exhaustion = false;
   m_bullish_exhaustion = false;
   m_bars = 0;
}

//+------------------------------------------------------------------+
void CReversalExit::OnBar(const double h, const double l, const double c)
{
   //=== Mean Reversion Channel (pine get_mrc, lines 657-719) =========
   double hlc3 = (h + l + c) / 3.0;
   m_meanline  = m_mean.Update(hlc3);
   m_meanrange = m_range.Update(PmTrueRange(h, l, m_prev_close));

   double mult  = M_PI * REX_MRC_INNER_MULT;
   double mult2 = M_PI * REX_MRC_OUTER_MULT;
   m_upband1 = m_meanline + m_meanrange * mult;
   m_loband1 = m_meanline - m_meanrange * mult;
   m_upband2 = m_meanline + m_meanrange * mult2;
   m_loband2 = m_meanline - m_meanrange * mult2;

   m_condition = 0;
   if(c > m_meanline)
   {
      double up2_1 = m_upband2 + m_meanrange * REX_MRC_GRADSIZE * 4.0;
      double up2_9 = m_upband2 + m_meanrange * REX_MRC_GRADSIZE * -4.0;
      if(h >= up2_9 && h < m_upband2)
         m_condition = 1;
      else if(h >= m_upband2 && h < up2_1)
         m_condition = 2;
      else if(h >= up2_1)
         m_condition = 3;
      else if(c <= m_meanline + m_meanrange)
         m_condition = 4;
      else
         m_condition = 5;
   }
   else if(c < m_meanline)
   {
      double lo2_1 = m_loband2 - m_meanrange * REX_MRC_GRADSIZE * 4.0;
      double lo2_9 = m_loband2 - m_meanrange * REX_MRC_GRADSIZE * -4.0;
      if(l <= lo2_9 && l > m_loband2)
         m_condition = -1;
      else if(l <= m_loband2 && l > lo2_1)
         m_condition = -2;
      else if(l <= lo2_1)
         m_condition = -3;
      // The bearish mirror of the bullish "near mean" test; the original library
      // had the sign wrong and the author already fixed it in-file (lines 708-714).
      else if(c >= m_meanline - m_meanrange)
         m_condition = -4;
      else
         m_condition = -5;
   }

   //=== WaveTrend 3D (pine lines 1149-1304) ==========================
   // getOscillator() computes normalizeDeriv once per source; all three speeds
   // share it and differ only in the dual-pole lookback.
   double hyper = PmTanh(m_nderiv.Update(c));
   m_series_fast   = REX_F_LENGTH * m_fast.Update(hyper);
   m_series_normal = REX_N_LENGTH * m_normal.Update(hyper);
   m_series_slow   = REX_S_LENGTH * m_slow.Update(hyper);

   // seriesEmphasis = seriesNormal (speedToEmphasize = 'Normal'), medium fit.
   m_yhat0 = m_kernel.Update(m_series_normal);
   bool bear_kernel_trend = (m_yhat0 < m_series_slow);
   bool bull_kernel_trend = (m_yhat0 > m_series_slow);

   m_x_fast_normal.Update(m_series_fast, m_series_normal);
   m_x_normal_zero.Update(m_series_normal, 0.0);

   m_bear_cross = m_x_fast_normal.CrossedUnder() && m_series_normal > 0.0;
   m_bull_cross = m_x_fast_normal.CrossedOver()  && m_series_normal < 0.0;
   m_osc_bear   = m_x_normal_zero.CrossedUnder();
   m_osc_bull   = m_x_normal_zero.CrossedOver();

   // ta.barssince(bearishCross[1]) - the PREVIOUS bar's cross flag.
   if(m_prev_bear_cross)
      m_bs_bear = 0.0;
   else if(!PmIsNa(m_bs_bear))
      m_bs_bear += 1.0;
   if(m_prev_bull_cross)
      m_bs_bull = 0.0;
   else if(!PmIsNa(m_bs_bull))
      m_bs_bull += 1.0;

   bool in_bear_div = (m_bs_bear < REX_DIV_THRESHOLD);   // NaN < x is false
   bool in_bull_div = (m_bs_bull < REX_DIV_THRESHOLD);

   // ta.valuewhen(cross, seriesNormal, 1) - the cross BEFORE the current one.
   if(m_bear_cross)
   {
      m_prev_bear_value = m_last_bear_value;
      m_last_bear_value = m_series_normal;
   }
   if(m_bull_cross)
   {
      m_prev_bull_value = m_last_bull_value;
      m_last_bull_value = m_series_normal;
   }

   double factor = REX_SIZE_PERCENT / 100.0;
   bool smaller_bear = m_bear_cross && (m_series_normal < m_prev_bear_value * factor);
   bool smaller_bull = m_bull_cross && (m_series_normal > m_prev_bull_value * factor);

   m_bear_trigger = smaller_bear && in_bear_div && bear_kernel_trend;
   m_bull_trigger = smaller_bull && in_bull_div && bull_kernel_trend;

   //=== Exhaustion switch (pine lines 1619-1657) =====================
   int depth = (m_sens == REX_EARLY) ? 1 : (m_sens == REX_NORMAL) ? 2 : 3;
   bool in_upper = (m_condition >= depth && m_condition <= 3);
   bool in_lower = (m_condition <= -depth && m_condition >= -3);
   bool touched_upper_inner = (h >= m_upband1);
   bool touched_lower_inner = (l <= m_loband1);

   if(m_sens == REX_EARLY)
   {
      m_bearish_exhaustion = touched_upper_inner || in_upper || m_bear_cross
                             || m_bear_trigger || m_osc_bear;
      m_bullish_exhaustion = touched_lower_inner || in_lower || m_bull_cross
                             || m_bull_trigger || m_osc_bull;
   }
   else if(m_sens == REX_LATE)
   {
      m_bearish_exhaustion = in_upper && m_bear_trigger;
      m_bullish_exhaustion = in_lower && m_bull_trigger;
   }
   else
   {
      m_bearish_exhaustion = in_upper || m_bear_trigger || m_osc_bear;
      m_bullish_exhaustion = in_lower || m_bull_trigger || m_osc_bull;
   }

   m_prev_bear_cross = m_bear_cross;
   m_prev_bull_cross = m_bull_cross;
   m_prev_close = c;
   m_bars++;
}
