#!/usr/bin/env python3
"""Haftalık / aylık trend filtresi swing botunu iyileştiriyor mu?
Look-ahead yok: günlük bar için haftalık/aylık değerler, ÖNCEKİ tamamlanmış hafta/aydan alınır."""
import time
from data import fetch
from strategy import Swing, Donchian, sma_std, rsi
from engine import Config, run, summarize

def wkey(t): return time.strftime("%G-%V", time.gmtime(t))
def mkey(t): return time.strftime("%Y-%m", time.gmtime(t))

def agg(bars, keyfn):
    keys, out, cur, last = [], [], None, None
    for b in bars:
        k = keyfn(b[0])
        if k != last:
            if cur: out.append(cur)
            cur = [b[0], b[1], b[2], b[3], b[4]]; last = k
        else:
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]
    if cur: out.append(cur)
    return out

def align(bars, keyfn, ma_n):
    """Her günlük bar için, bir ÖNCEKİ tamamlanmış periyodun (kapanış>MA, RSI) değerleri."""
    per = agg(bars, keyfn); c = [p[4] for p in per]
    ma = sma_std(c, ma_n)[0]; r = rsi(c, 14)
    idx = {keyfn(p[0]): i for i, p in enumerate(per)}
    up, rs = [], []
    for b in bars:
        j = idx[keyfn(b[0])] - 1          # önceki tamamlanmış periyot
        if j < 0 or ma[j] is None: up.append(None); rs.append(None)
        else: up.append(c[j] > ma[j]); rs.append(r[j])
    return up, rs

class SwingHTF(Swing):
    def __init__(self, weekly=False, monthly=False, wrsi=False, **kw):
        super().__init__(**kw); self.weekly, self.monthly, self.wrsi = weekly, monthly, wrsi
    def prepare(self, bars):
        I = super().prepare(bars)
        I["wu"], I["wr"] = align(bars, wkey, 40); I["mu"], _ = align(bars, mkey, 10)
        return I
    def entry_signal(self, I, i):
        if not super().entry_signal(I, i): return 0
        if self.weekly and not I["wu"][i]: return 0
        if self.monthly and not I["mu"][i]: return 0
        if self.wrsi and not (I["wr"][i] is not None and I["wr"][i] > 50): return 0
        return 1

ASSETS = {"BTC-USD": 10, "ETH-USD": 12, "GC=F": 3, "SI=F": 5, "SPY": 2, "QQQ": 2}
VARS = [("günlük (şu anki bot)", SwingHTF()),
        ("+ haftalık trend (fiyat>40 hafta ort.)", SwingHTF(weekly=True)),
        ("+ haftalık trend + haftalık RSI>50", SwingHTF(weekly=True, wrsi=True)),
        ("+ haftalık + aylık (fiyat>10 ay ort.)", SwingHTF(weekly=True, monthly=True))]

print(f"{'varlık':8s} {'varyant':40s} {'işlem/yıl':>9s} {'win%':>5s} {'NET':>8s} {'1.yarı':>8s} {'2.yarı':>8s} {'maxDD':>6s}")
tot = {v[0]: [0, 0, 0] for v in VARS}
for sym, bps in ASSETS.items():
    bars = fetch(sym, "1d", "10y"); time.sleep(1)
    yrs = (bars[-1][0] - bars[0][0]) / (365.25 * 86400); h = len(bars) // 2
    cfg = Config(start=1000, cost_bps=bps, risk_pct=1.0, max_lev=1.0, kill_dd=0.99)
    for name, st in VARS:
        a, cv = run(bars, st, cfg); s = summarize(bars, a, cv, cfg, 252)
        a1, c1 = run(bars[:h], st, cfg); s1 = summarize(bars[:h], a1, c1, cfg, 252)
        a2, c2 = run(bars[h:], st, cfg); s2 = summarize(bars[h:], a2, c2, cfg, 252)
        ok = "✓" if s1["ret"] > 0 and s2["ret"] > 0 else " "
        tot[name][0] += (1 if s1["ret"] > 0 and s2["ret"] > 0 else 0); tot[name][1] += s["maxdd"]; tot[name][2] += s["ret"]
        print(f"{sym:8s} {name:40s} {s['n']/yrs:9.1f} {s['win']*100:4.0f}% {s['ret']*100:7.0f}% {s1['ret']*100:7.0f}% {s2['ret']*100:7.0f}% {s['maxdd']*100:5.0f}% {ok}")
print("\nÖZET (6 varlık): iki yarıda da kârlı sayısı | ortalama maxDD")
for name, (ok, dd, ret) in tot.items(): print(f"  {name:40s} {ok}/6 | maxDD ort. {dd/6*100:.0f}%")
