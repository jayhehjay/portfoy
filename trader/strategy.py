"""'Düşükten al, yüksekten sat' = ortalamaya dönüş (mean reversion).
Karar sinyali bar KAPANIŞINDA üretilir, emir bir SONRAKİ barın açılışında dolar (look-ahead yok)."""
import math

def sma_std(c, n):
    s, s2, m, sd = 0.0, 0.0, [None]*len(c), [None]*len(c)
    for i, x in enumerate(c):
        s += x; s2 += x*x
        if i >= n: s -= c[i-n]; s2 -= c[i-n]**2
        if i >= n-1:
            m[i] = s/n; sd[i] = math.sqrt(max(s2/n - m[i]**2, 0.0))
    return m, sd

def rsi(c, n):
    out = [None]*len(c); g = l = 0.0
    for i in range(1, len(c)):
        d = c[i]-c[i-1]; up, dn = max(d, 0), max(-d, 0)
        if i <= n:
            g += up/n; l += dn/n
        else:
            g = (g*(n-1)+up)/n; l = (l*(n-1)+dn)/n
        if i >= n: out[i] = 100.0 if l == 0 else 100 - 100/(1+g/l)
    return out

def atr(bars, n):
    out = [None]*len(bars); a = 0.0
    for i in range(1, len(bars)):
        tr = max(bars[i][2]-bars[i][3], abs(bars[i][2]-bars[i-1][4]), abs(bars[i][3]-bars[i-1][4]))
        if i <= n: a += tr/n
        else: a = (a*(n-1)+tr)/n
        if i >= n: out[i] = a
    return out

class MeanReversion:
    """Long: kapanış alt bandın (SMA - k*std) altında VE RSI < eşik  -> al.
    Çıkış: kapanış SMA'ya dönünce (hedef), ATR-stop, veya max_hold bar sonra (zaman stopu).
    allow_short=True ise ters yön (üst band + RSI yüksek) de açılır."""
    def __init__(self, n=20, k=2.0, rsi_n=14, rsi_lo=35, sl_atr=3.0, max_hold=48, allow_short=False):
        self.n, self.k, self.rsi_n, self.rsi_lo = n, k, rsi_n, rsi_lo
        self.sl_atr, self.max_hold, self.allow_short = sl_atr, max_hold, allow_short
    def params(self): return dict(n=self.n, k=self.k, rsi_lo=self.rsi_lo, sl_atr=self.sl_atr, max_hold=self.max_hold, short=self.allow_short)
    def prepare(self, bars):
        c = [b[4] for b in bars]; m, sd = sma_std(c, self.n)
        return dict(c=c, m=m, sd=sd, r=rsi(c, self.rsi_n), a=atr(bars, 14))
    def entry_signal(self, I, i):
        if None in (I["m"][i], I["r"][i], I["a"][i]): return 0
        c, m, sd, r = I["c"][i], I["m"][i], I["sd"][i], I["r"][i]
        if c < m - self.k*sd and r < self.rsi_lo: return 1
        if self.allow_short and c > m + self.k*sd and r > 100 - self.rsi_lo: return -1
        return 0
    def stop_dist(self, I, i): return self.sl_atr * I["a"][i]
    def exit_signal(self, I, i, d, held):
        m = I["m"][i]
        if held >= self.max_hold: return True
        return (d == 1 and I["c"][i] >= m) or (d == -1 and I["c"][i] <= m)
