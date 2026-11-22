#!/usr/bin/env python3
"""Kısa vadeli (dakikalık) mi, swing (günlük) mi? Aynı stratejileri farklı zaman dilimlerinde karşılaştırır.
Tam yatırımlı (kaldıraçsız), maliyet dahil, iki yarı dönem ayrı raporlanır."""
import sys, time
from data import fetch
from strategy import MeanReversion, Donchian, TrendPullback
from engine import Config, run, summarize

def resample(bars, secs):
    out, cur, key = [], None, None
    for t, o, h, l, c, v in bars:
        k = t // secs
        if k != key:
            if cur: out.append(tuple(cur))
            cur, key = [t, o, h, l, c, v], k
        else:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
    if cur: out.append(tuple(cur))
    return out

TF = {  # ad: (aralık, aralık-menzil, saniye(resample), yılda bar)
    "5dk":    ("5m", "60d", None, 105120),
    "15dk":   ("15m", "60d", None, 35040),
    "1saat":  ("1h", "2y", None, 8760),
    "4saat":  ("1h", "2y", 4 * 3600, 2190),
    "günlük": ("1d", "10y", None, 365),
}
# Aynı "düşükten al" kuralı her zaman diliminde (elde tutma süresi her seferinde ~ aynı oranda)
HOLD = {"5dk": 36, "15dk": 24, "1saat": 24, "4saat": 18, "günlük": 10}
SW = {  # swing stratejileri (zaman dilimine göre ölçekli)
    "4saat": [("trend kırılımı 40/15", Donchian(40, 15, 3.0, False))],
    "günlük": [("trend kırılımı 55/20", Donchian(55, 20, 3.0, False)), ("trend kırılımı 20/10", Donchian(20, 10, 3.0, False)),
               ("trendde dip alımı", TrendPullback(100, 2, 10, 5, 3.0, 10))],
}

def go(sym, bps):
    print(f"\n######## {sym}  (maliyet {bps} bps/yön)")
    print(f"{'zaman':7s} {'strateji':24s} {'işlem/yıl':>9s} {'kazanç%':>7s} {'ort.K/Z':>8s} {'brüt':>7s} {'NET':>7s} {'1.yarı':>7s} {'2.yarı':>7s} {'maxDD':>6s} {'al-tut':>7s}")
    for name, (iv, rng, rs, ppy) in TF.items():
        bars = fetch(sym, iv, rng); time.sleep(1)
        if rs: bars = resample(bars, rs)
        if len(bars) < 200: continue
        yrs = (bars[-1][0] - bars[0][0]) / (365.25 * 86400)
        h = len(bars) // 2
        cfgs = dict(net=Config(start=1000, cost_bps=bps, risk_pct=1.0, max_lev=1.0, kill_dd=0.99),
                    gross=Config(start=1000, cost_bps=0, risk_pct=1.0, max_lev=1.0, kill_dd=0.99))
        strats = [("düşükten al (şu anki bot)", MeanReversion(20, 2.0, 14, 35, 3.0, HOLD[name], False))] + SW.get(name, [])
        for sname, st in strats:
            a, cv = run(bars, st, cfgs["net"]); s = summarize(bars, a, cv, cfgs["net"], ppy)
            ag, cg = run(bars, st, cfgs["gross"]); sg = summarize(bars, ag, cg, cfgs["gross"], ppy)
            a1, c1 = run(bars[:h], st, cfgs["net"]); s1 = summarize(bars[:h], a1, c1, cfgs["net"], ppy)
            a2, c2 = run(bars[h:], st, cfgs["net"]); s2 = summarize(bars[h:], a2, c2, cfgs["net"], ppy)
            print(f"{name:7s} {sname:24s} {s['n']/yrs:9.0f} {s['win']*100:6.0f}% {((s['avg_w']*s['win']+s['avg_l']*(1-s['win']))/10):7.2f}% "
                  f"{sg['ret']*100:6.1f}% {s['ret']*100:6.1f}% {s1['ret']*100:6.1f}% {s2['ret']*100:6.1f}% {s['maxdd']*100:5.1f}% {s['bh']*100:6.0f}%   ({yrs:.1f} yıl)")

if __name__ == "__main__":
    go("BTC-USD", 10); go("ETH-USD", 12)
