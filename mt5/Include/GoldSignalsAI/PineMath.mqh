//+------------------------------------------------------------------+
//|                                                     PineMath.mqh |
//|   Pine Script v5 primitives, streaming form, for GoldSignalsAI.   |
//|                                                                  |
//|   Mirrors research/pine_math.py bar for bar. The built-in MT5     |
//|   indicators (iATR, iMA) are deliberately NOT used: they seed     |
//|   their recursions differently from Pine and would silently       |
//|   break the parity check against the Python reference.            |
//|                                                                  |
//|   NaN is used exactly as Pine uses na - every comparison against  |
//|   it is false, which is what makes the "abstain rather than       |
//|   block" behaviour of the chop filter come out right.             |
//+------------------------------------------------------------------+
#property strict

//--- NaN handling -------------------------------------------------------------
double PmNaN() { return(MathSqrt(-1.0)); }
bool   PmIsNa(const double x) { return(!MathIsValidNumber(x)); }
double PmNz(const double x, const double fallback = 0.0) { return(PmIsNa(x) ? fallback : x); }

double PmSign(const double x)
{
   if(PmIsNa(x))
      return(PmNaN());
   if(x > 0.0) return(1.0);
   if(x < 0.0) return(-1.0);
   return(0.0);
}

//+------------------------------------------------------------------+
//| Fixed-capacity ring buffer of the most recent values.             |
//| At(0) is the current bar, At(1) the previous one, Pine-style.     |
//+------------------------------------------------------------------+
class CPmRing
{
private:
   double            m_buf[];
   int               m_cap;
   int               m_head;     // index of the most recent value
   int               m_count;

public:
                     CPmRing(): m_cap(0), m_head(-1), m_count(0) {}

   void Init(const int capacity)
   {
      m_cap = MathMax(1, capacity);
      ArrayResize(m_buf, m_cap);
      ArrayInitialize(m_buf, 0.0);
      m_head  = -1;
      m_count = 0;
   }

   void Push(const double v)
   {
      m_head = (m_head + 1) % m_cap;
      m_buf[m_head] = v;
      if(m_count < m_cap)
         m_count++;
   }

   //--- At(0) newest; returns NaN when that far back is not available yet
   double At(const int back) const
   {
      if(back < 0 || back >= m_count)
         return(MathSqrt(-1.0));
      int idx = m_head - back;
      while(idx < 0)
         idx += m_cap;
      return(m_buf[idx]);
   }

   int  Count() const { return(m_count); }
   int  Capacity() const { return(m_cap); }
   bool Full() const { return(m_count >= m_cap); }
};

//+------------------------------------------------------------------+
//| ta.sma - NaN until `length` real values have arrived.             |
//| Leading NaN input is skipped, as Pine's built-ins do.             |
//+------------------------------------------------------------------+
class CPmSma
{
private:
   CPmRing           m_ring;
   int               m_len;
   int               m_seen;
   double            m_sum;

public:
   void Init(const int length)
   {
      m_len  = MathMax(1, length);
      m_ring.Init(m_len);
      m_seen = 0;
      m_sum  = 0.0;
   }

   double Update(const double v)
   {
      if(PmIsNa(v))
         return(PmNaN());
      if(m_ring.Full())
         m_sum -= m_ring.At(m_len - 1);
      m_ring.Push(v);
      m_sum += v;
      m_seen++;
      if(m_seen < m_len)
         return(PmNaN());
      return(m_sum / m_len);
   }
};

//+------------------------------------------------------------------+
//| ta.rma - Wilder smoothing seeded with the SMA of the first        |
//| `length` values (alpha = 1/length).                               |
//+------------------------------------------------------------------+
class CPmRma
{
private:
   int               m_len;
   int               m_seen;
   double            m_acc;
   double            m_value;
   double            m_alpha;

public:
   void Init(const int length)
   {
      m_len   = MathMax(1, length);
      m_alpha = 1.0 / m_len;
      m_seen  = 0;
      m_acc   = 0.0;
      m_value = PmNaN();
   }

   double Update(const double v)
   {
      if(PmIsNa(v))
         return(m_value);   // leading na is skipped, not counted
      m_seen++;
      if(m_seen < m_len)
      {
         m_acc += v;
         m_value = PmNaN();
      }
      else if(m_seen == m_len)
      {
         m_acc += v;
         m_value = m_acc / m_len;
      }
      else
         m_value = m_alpha * v + (1.0 - m_alpha) * m_value;
      return(m_value);
   }

   double Value() const { return(m_value); }
};

//+------------------------------------------------------------------+
//| ta.ema - seeded with the SMA of the first `length` values         |
//| (alpha = 2/(length+1)).                                          |
//+------------------------------------------------------------------+
class CPmEma
{
private:
   int               m_len;
   int               m_seen;
   double            m_acc;
   double            m_value;
   double            m_alpha;

public:
   void Init(const int length)
   {
      m_len   = MathMax(1, length);
      m_alpha = 2.0 / (m_len + 1.0);
      m_seen  = 0;
      m_acc   = 0.0;
      m_value = PmNaN();
   }

   double Update(const double v)
   {
      if(PmIsNa(v))
         return(m_value);
      m_seen++;
      if(m_seen < m_len)
      {
         m_acc += v;
         m_value = PmNaN();
      }
      else if(m_seen == m_len)
      {
         m_acc += v;
         m_value = m_acc / m_len;
      }
      else
         m_value = m_alpha * v + (1.0 - m_alpha) * m_value;
      return(m_value);
   }

   double Value() const { return(m_value); }
};

//+------------------------------------------------------------------+
//| ta.percentrank(src, length) - the percentage of the PREVIOUS      |
//| `length` values that are <= the current one. NaN until that many  |
//| prior values exist, which is what makes the chop filter abstain   |
//| early in the series (pine line 1550).                             |
//+------------------------------------------------------------------+
class CPmPercentRank
{
private:
   CPmRing           m_ring;      // holds the previous `length` values
   int               m_len;

public:
   void Init(const int length)
   {
      m_len = MathMax(1, length);
      m_ring.Init(m_len);
   }

   double Update(const double v)
   {
      double result = PmNaN();
      if(m_ring.Full())
      {
         int count = 0;
         for(int i = 0; i < m_len; i++)
            if(m_ring.At(i) <= v)
               count++;
         result = (double)count / m_len * 100.0;
      }
      m_ring.Push(v);
      return(result);
   }
};

//+------------------------------------------------------------------+
//| array.percentile_linear_interpolation - sorts, then interpolates. |
//| Returns NaN for an empty input, as Pine does.                     |
//+------------------------------------------------------------------+
double PmPercentileLinear(const double &values[], const int count, const double percentile)
{
   if(count <= 0)
      return(PmNaN());
   double sorted[];
   ArrayResize(sorted, count);
   for(int i = 0; i < count; i++)
      sorted[i] = values[i];
   ArraySort(sorted);

   double idx = (count - 1) * percentile / 100.0;
   int    lo  = (int)MathFloor(idx);
   int    hi  = (int)MathCeil(idx);
   if(lo == hi)
      return(sorted[lo]);
   return(sorted[lo] + (idx - lo) * (sorted[hi] - sorted[lo]));
}

//+------------------------------------------------------------------+
//| ta.tr - max(h-l, |h-c1|, |l-c1|).                                |
//| On the very first bar there is no previous close and we use       |
//| high-low (ta.tr's handle_na=false behaviour). research/           |
//| pine_math.py documents why, and uses the identical convention.    |
//+------------------------------------------------------------------+
double PmTrueRange(const double high, const double low, const double prev_close)
{
   if(PmIsNa(prev_close))
      return(high - low);
   double a = high - low;
   double b = MathAbs(high - prev_close);
   double c = MathAbs(low - prev_close);
   return(MathMax(a, MathMax(b, c)));
}

//+------------------------------------------------------------------+
//| SuperSmoother, 2-pole Butterworth (pine lines 599-607).          |
//| Keeps the nz(ss[1], src[1]) fallbacks literally, so the output is |
//| NaN for the first two bars and then heals itself.                 |
//+------------------------------------------------------------------+
class CPmSuperSmoother
{
private:
   double            m_c1, m_c2, m_c3;
   double            m_out1, m_out2;   // previous outputs
   double            m_src1, m_src2;   // previous inputs
   bool              m_started;

public:
   void Init(const int length)
   {
      double len = MathMax(1, length);
      double a1  = MathExp(-MathSqrt(2.0) * M_PI / len);
      double b1  = 2.0 * a1 * MathCos(MathSqrt(2.0) * M_PI / len);
      m_c3 = -(a1 * a1);
      m_c2 = b1;
      m_c1 = 1.0 - m_c2 - m_c3;
      m_out1 = PmNaN();
      m_out2 = PmNaN();
      m_src1 = PmNaN();
      m_src2 = PmNaN();
      m_started = false;
   }

   double Update(const double src)
   {
      double p1 = PmIsNa(m_out1) ? m_src1 : m_out1;
      double p2 = PmIsNa(m_out2) ? m_src2 : m_out2;
      double out = m_c1 * src + m_c2 * p1 + m_c3 * p2;

      m_out2 = m_out1;
      m_out1 = out;
      m_src2 = m_src1;
      m_src1 = src;
      return(out);
   }
};

//+------------------------------------------------------------------+
//| pine tanh(): -1 + 2/(1+exp(-2x))  (line 1135)                    |
//+------------------------------------------------------------------+
double PmTanh(const double x) { return(-1.0 + 2.0 / (1.0 + MathExp(-2.0 * x))); }

//+------------------------------------------------------------------+
//| normalizeDeriv - 2-bar derivative over its own quadratic mean     |
//| (pine lines 1130-1133).                                          |
//+------------------------------------------------------------------+
class CPmNormalizeDeriv
{
private:
   CPmRing           m_src;       // needs src[2]
   CPmRing           m_sq;        // rolling window of squared derivatives
   int               m_len;
   double            m_run;
   int               m_seen;

public:
   void Init(const int quadratic_mean_length)
   {
      m_len = MathMax(1, quadratic_mean_length);
      m_src.Init(3);
      m_sq.Init(m_len);
      m_run  = 0.0;
      m_seen = 0;
   }

   double Update(const double src)
   {
      m_src.Push(src);
      double prev2 = m_src.At(2);
      double deriv = PmIsNa(prev2) ? 0.0 : (src - prev2);

      double sq = deriv * deriv;
      if(m_sq.Full())
         m_run -= m_sq.At(m_len - 1);
      m_sq.Push(sq);
      m_run += sq;
      m_seen++;

      if(m_seen < m_len)
         return(0.0);              // nz(na) -> 0, then Pine's x/0 -> na -> 0 here
      double qm = MathSqrt(m_run / m_len);
      if(qm <= 0.0)
         return(0.0);
      return(deriv / qm);
   }
};

//+------------------------------------------------------------------+
//| dualPoleFilter (pine lines 1138-1147).                           |
//+------------------------------------------------------------------+
class CPmDualPole
{
private:
   double            m_beta, m_gamma, m_delta;
   double            m_f1, m_f2;
   double            m_src1;
   bool              m_has_src;

public:
   void Init(const double lookback)
   {
      double omega = -99.0 * M_PI / (70.0 * lookback);
      double alpha = MathExp(omega);
      m_beta  = -(alpha * alpha);
      m_gamma = MathCos(omega) * 2.0 * alpha;
      m_delta = 1.0 - m_gamma - m_beta;
      m_f1 = 0.0;
      m_f2 = 0.0;
      m_src1 = 0.0;
      m_has_src = false;
   }

   double Update(const double src)
   {
      double prev = m_has_src ? m_src1 : src;      // nz(src[1], src)
      double sliding = 0.5 * (src + prev);
      double out = m_delta * sliding + m_gamma * m_f1 + m_beta * m_f2;
      m_f2 = m_f1;
      m_f1 = out;
      m_src1 = src;
      m_has_src = true;
      return(out);
   }
};

//+------------------------------------------------------------------+
//| Gaussian kernel regression (jdehorty), as imported by the pine    |
//| script: sum(src[i]*exp(-i^2/2h^2)) / sum(weights), i = 0..start+1 |
//+------------------------------------------------------------------+
class CPmGaussianKernel
{
private:
   CPmRing           m_ring;
   double            m_weights[];
   double            m_wsum;
   int               m_span;

public:
   void Init(const double lookback, const int start_at_bar)
   {
      m_span = start_at_bar + 1;
      ArrayResize(m_weights, m_span + 1);
      m_wsum = 0.0;
      for(int i = 0; i <= m_span; i++)
      {
         m_weights[i] = MathExp(-(i * i) / (2.0 * lookback * lookback));
         m_wsum += m_weights[i];
      }
      m_ring.Init(m_span + 1);
   }

   double Update(const double src)
   {
      m_ring.Push(src);
      double acc = 0.0;
      for(int k = 0; k <= m_span; k++)
      {
         double v = m_ring.At(k);
         if(PmIsNa(v))
            v = m_ring.At(m_ring.Count() - 1);   // clamp to the oldest known value
         acc += v * m_weights[k];
      }
      return(acc / m_wsum);
   }
};

//+------------------------------------------------------------------+
//| ta.crossover / ta.crossunder over two streamed series.           |
//+------------------------------------------------------------------+
class CPmCross
{
private:
   double            m_a1, m_b1;
   bool              m_has;
   bool              m_over, m_under;

public:
   void Init() { m_has = false; m_over = false; m_under = false; m_a1 = 0.0; m_b1 = 0.0; }

   void Update(const double a, const double b)
   {
      if(!m_has)
      {
         m_over = false;
         m_under = false;
      }
      else
      {
         m_over  = (a > b && m_a1 <= m_b1);
         m_under = (a < b && m_a1 >= m_b1);
      }
      m_a1 = a;
      m_b1 = b;
      m_has = true;
   }

   bool CrossedOver() const { return(m_over); }
   bool CrossedUnder() const { return(m_under); }
};
