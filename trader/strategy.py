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


def _rolling_extreme(vals, n, fn):
    """Bar i için ÖNCEKİ n barın (i hariç) max/min değeri."""
    out = [None]*len(vals)
    for i in range(n, len(vals)): out[i] = fn(vals[i-n:i])
    return out

class Donchian:
    """Trend takibi: kapanış önceki N günün zirvesini aşınca al, M günlük dibin altına inince çık.
    Küçük kayıplar, nadir ama büyük kazançlar (win rate ~%35-45, ama kazançlar kayıplardan büyük)."""
    def __init__(self, entry_n=55, exit_n=20, sl_atr=3.0, allow_short=False):
        self.entry_n, self.exit_n, self.sl_atr, self.allow_short = entry_n, exit_n, sl_atr, allow_short
    def params(self): return dict(entry_n=self.entry_n, exit_n=self.exit_n, sl_atr=self.sl_atr, short=self.allow_short)
    def prepare(self, bars):
        h = [b[2] for b in bars]; l = [b[3] for b in bars]
        return dict(c=[b[4] for b in bars], hi=_rolling_extreme(h, self.entry_n, max), lo=_rolling_extreme(l, self.entry_n, min),
                    xhi=_rolling_extreme(h, self.exit_n, max), xlo=_rolling_extreme(l, self.exit_n, min), a=atr(bars, 14))
    def entry_signal(self, I, i):
        if None in (I["hi"][i], I["a"][i]): return 0
        if I["c"][i] > I["hi"][i]: return 1
        if self.allow_short and I["c"][i] < I["lo"][i]: return -1
        return 0
    def stop_dist(self, I, i): return self.sl_atr * I["a"][i]
    def exit_signal(self, I, i, d, held):
        return (d == 1 and I["c"][i] < I["xlo"][i]) or (d == -1 and I["c"][i] > I["xhi"][i])

class TrendPullback:
    """Yükseliş trendinde (kapanış > SMA200) kısa vadeli düşüşü al (RSI(2) çok düşük), toparlanınca sat."""
    def __init__(self, trend_n=200, rsi_n=2, rsi_lo=10, exit_n=5, sl_atr=3.0, max_hold=10):
        self.trend_n, self.rsi_n, self.rsi_lo, self.exit_n, self.sl_atr, self.max_hold = trend_n, rsi_n, rsi_lo, exit_n, sl_atr, max_hold
    def params(self): return dict(trend_n=self.trend_n, rsi_lo=self.rsi_lo, exit_n=self.exit_n, sl_atr=self.sl_atr, max_hold=self.max_hold)
    def prepare(self, bars):
        c = [b[4] for b in bars]
        return dict(c=c, t=sma_std(c, self.trend_n)[0], x=sma_std(c, self.exit_n)[0], r=rsi(c, self.rsi_n), a=atr(bars, 14))
    def entry_signal(self, I, i):
        if None in (I["t"][i], I["r"][i], I["a"][i]): return 0
        return 1 if I["c"][i] > I["t"][i] and I["r"][i] < self.rsi_lo else 0
    def stop_dist(self, I, i): return self.sl_atr * I["a"][i]
    def exit_signal(self, I, i, d, held):
        return held >= self.max_hold or I["c"][i] > I["x"][i]

REGISTRY = {"mr": MeanReversion, "donchian": Donchian, "pullback": TrendPullback}


class Swing:
    """Günlük swing: 4 teknik analiz birlikte oy verir.
      1) TREND      : kapanış > 200 günlük ortalama (ZORUNLU)
      2) DİRENÇ     : kapanış, önceki brk_n günün en yüksek kapanışını aşıyor (direnç kırılımı)
      3) MOMENTUM   : RSI(14) > rsi_min
      4) BOLLINGER  : kapanış > orta bant (20 günlük ortalama)
    Giriş: trend şart + toplam >= min_score  (4 üzerinden)
    Çıkış: kapanış 200 günlüğün altına inerse | destek (önceki sup_n günün en düşüğü) kırılırsa | ATR stop"""
    def __init__(self, min_score=3, brk_n=20, sup_n=10, rsi_min=50, trend_n=200, bb_n=20, sl_atr=3.0, exit_mode="support"):
        self.min_score, self.brk_n, self.sup_n, self.rsi_min = min_score, brk_n, sup_n, rsi_min
        self.trend_n, self.bb_n, self.sl_atr, self.exit_mode = trend_n, bb_n, sl_atr, exit_mode
    def params(self): return dict(min_score=self.min_score, brk_n=self.brk_n, sup_n=self.sup_n, rsi_min=self.rsi_min, exit_mode=self.exit_mode)
    def prepare(self, bars):
        c = [b[4] for b in bars]; sma_t = sma_std(c, self.trend_n)[0]; mid, sd = sma_std(c, self.bb_n)
        return dict(c=c, t=sma_t, mid=mid, sd=sd, r=rsi(c, 14), a=atr(bars, 14),
                    hh=_rolling_extreme(c, self.brk_n, max), ll=_rolling_extreme([b[3] for b in bars], self.sup_n, min))
    def score(self, I, i):
        if None in (I["t"][i], I["r"][i], I["hh"][i], I["mid"][i]): return None
        c = I["c"][i]
        return (c > I["t"][i], c > I["hh"][i], I["r"][i] > self.rsi_min, c > I["mid"][i])
    def entry_signal(self, I, i):
        s = self.score(I, i)
        if s is None or I["a"][i] is None: return 0
        return 1 if s[0] and sum(s) >= self.min_score else 0
    def stop_dist(self, I, i): return self.sl_atr * I["a"][i]
    def exit_signal(self, I, i, d, held):
        s = self.score(I, i)
        if s is None: return False
        if not s[0] or (I["ll"][i] is not None and I["c"][i] < I["ll"][i]): return True
        return self.exit_mode == "score" and sum(s) <= 1

REGISTRY["swing"] = Swing
