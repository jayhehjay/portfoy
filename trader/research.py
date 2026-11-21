#!/usr/bin/env python3
"""Önceden belirlenmiş strateji/parametre ızgarası × varlıklar. Parametre OPTİMİZE edilmez:
bir (strateji, varlık) ancak iki yarı dönemde de ve komşu parametrelerde de kârlıysa 'dayanıklı' sayılır."""
import time, sys
from data import fetch
from strategy import MeanReversion, Donchian, TrendPullback
from engine import Config, run, summarize

ASSETS = {  # sembol: işlem maliyeti (bps / yön)
    "GC=F": 3, "SI=F": 5, "CL=F": 5, "SPY": 2, "QQQ": 2, "TLT": 2, "BTC-USD": 10, "ETH-USD": 12, "EURUSD=X": 2, "HG=F": 5,
}
STRATS = {
    "donchian": [Donchian(e, x, 3.0, sh) for e in (40, 55, 80) for x in (15, 20) for sh in (False, True)][:0] +
                [Donchian(e, x, 3.0, False) for e in (40, 55, 80) for x in (15, 20)] +
                [Donchian(e, x, 3.0, True) for e in (40, 55, 80) for x in (15, 20)],
    "pullback": [TrendPullback(200, 2, r, 5, 3.0, 10) for r in (5, 10, 15)] + [TrendPullback(100, 2, 10, 5, 3.0, 10)],
    "mr": [MeanReversion(n, 2.0, 14, 35, 3.0, 20, False) for n in (20, 40)],
}

def halves(bars):
    m = len(bars)//2; return bars[:m], bars[m:]

def main():
    rows = []
    for sym, bps in ASSETS.items():
        try: bars = fetch(sym, "1d", "10y")
        except Exception as e: print("atlandı", sym, e); continue
        time.sleep(1); cfg = Config(cost_bps=bps); b1, b2 = halves(bars)
        bh = bars[-1][4]/bars[0][4]-1
        for sname, lst in STRATS.items():
            res = []
            for st in lst:
                s = {}
                for tag, bb in (("full", bars), ("h1", b1), ("h2", b2)):
                    a, cv = run(bb, st, cfg); s[tag] = summarize(bb, a, cv, cfg, 252)
                res.append((st, s))
            # dayanıklılık: parametrelerin kaçı iki yarıda da kârlı?
            def key(r): return r[0].params()["short"] if "short" in r[0].params() else False
            for short in (False, True) if sname == "donchian" else (False,):
                sub = [r for r in res if (r[0].params().get("short", False) == short)]
                if not sub: continue
                ok = sum(1 for _, s in sub if s["h1"]["ret"] > 0 and s["h2"]["ret"] > 0)
                avg = lambda t, k: sum(s[t][k] for _, s in sub)/len(sub)
                rows.append(dict(sym=sym, strat=sname + ("+short" if short else ""), ok=ok, n=len(sub), cagr=avg("full","cagr"),
                                 dd=avg("full","maxdd"), sharpe=avg("full","sharpe"), h1=avg("h1","ret"), h2=avg("h2","ret"),
                                 trades=avg("full","n"), pf=avg("full","pf"), bh=bh))
    rows.sort(key=lambda r: (r["ok"]/r["n"], r["sharpe"]), reverse=True)
    print(f"{'varlık':9s} {'strateji':16s} {'iki yarıda kârlı':>16s} {'CAGR':>6s} {'maxDD':>6s} {'Sharpe':>6s} {'1.yarı':>7s} {'2.yarı':>7s} {'işlem':>5s} {'PF':>5s} {'al-tut':>7s}")
    for r in rows:
        print(f"{r['sym']:9s} {r['strat']:16s} {r['ok']:>8d}/{r['n']:<7d} {r['cagr']*100:5.1f}% {r['dd']*100:5.1f}% {r['sharpe']:6.2f} {r['h1']*100:6.1f}% {r['h2']*100:6.1f}% {r['trades']:5.0f} {r['pf']:5.2f} {r['bh']*100:6.0f}%")

if __name__ == "__main__": main()
