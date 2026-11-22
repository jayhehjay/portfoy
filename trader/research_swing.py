#!/usr/bin/env python3
"""Swing (günlük, 4 analiz) varyantlarını varlıklar ve iki yarı dönemde karşılaştırır. Parametre optimize edilmez: önceden seçilmiş az sayıda varyant."""
import time
from data import fetch
from strategy import Swing, Donchian
from engine import Config, run, summarize

ASSETS = {"BTC-USD": 10, "ETH-USD": 12, "GC=F": 3, "SI=F": 5, "SPY": 2, "QQQ": 2}
VARIANTS = [
    ("A  4/4 onay, destek çıkışı", Swing(4, 20, 10)),
    ("B  3/4 onay, destek çıkışı", Swing(3, 20, 10)),
    ("C  3/4 onay, skor çıkışı",   Swing(3, 20, 10, exit_mode="score")),
    ("D  4/4 onay, 55g direnç/20g destek", Swing(4, 55, 20)),
    ("ref Donchian 55/20 (tek analiz)", Donchian(55, 20, 3.0, False)),
]
print(f"{'varlık':8s} {'varyant':36s} {'işlem/yıl':>9s} {'win%':>5s} {'NET':>8s} {'1.yarı':>8s} {'2.yarı':>8s} {'maxDD':>6s} {'al-tut':>8s}")
for sym, bps in ASSETS.items():
    bars = fetch(sym, "1d", "10y"); time.sleep(1)
    yrs = (bars[-1][0] - bars[0][0]) / (365.25 * 86400); h = len(bars) // 2
    cfg = Config(start=1000, cost_bps=bps, risk_pct=1.0, max_lev=1.0, kill_dd=0.99)
    for name, st in VARIANTS:
        a, cv = run(bars, st, cfg); s = summarize(bars, a, cv, cfg, 252)
        a1, c1 = run(bars[:h], st, cfg); s1 = summarize(bars[:h], a1, c1, cfg, 252)
        a2, c2 = run(bars[h:], st, cfg); s2 = summarize(bars[h:], a2, c2, cfg, 252)
        ok = "✓" if s1["ret"] > 0 and s2["ret"] > 0 else " "
        print(f"{sym:8s} {name:36s} {s['n']/yrs:9.1f} {s['win']*100:4.0f}% {s['ret']*100:7.0f}% {s1['ret']*100:7.0f}% {s2['ret']*100:7.0f}% {s['maxdd']*100:5.0f}% {s['bh']*100:7.0f}% {ok}")
