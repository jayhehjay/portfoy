#!/usr/bin/env python3
"""Çok varlıklı portföy: backtest ve paper trading (SANAL, gerçek emir yok).
  python3 portfolio.py backtest
  python3 portfolio.py paper --once        # günde bir çalıştırılır (GitHub Actions bunu yapar)
"""
import argparse, json, os, sys, time, math
from data import fetch
from strategy import REGISTRY
from engine import Config, Account, Pos, run, step

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")

def load_cfg(path=None):
    c = json.load(open(path or os.path.join(HERE, "config.json"))); tw = len(c["sleeves"])
    for s in c["sleeves"]:
        s["capital"] = c["capital"] / tw
        s["cfg"] = Config(start=s["capital"], cost_bps=s["cost_bps"], risk_pct=c["risk_pct"], max_lev=c["max_lev"], kill_dd=c["kill_dd"])
        s["strat"] = REGISTRY[s["strategy"]](**s["params"])
    return c

def day(ts): return time.strftime("%Y-%m-%d", time.gmtime(ts))

def closed_bars(sym):
    bars = fetch(sym, "1d", "10y", use_cache=False)
    today = day(time.time())
    return [b for b in bars if day(b[0]) < today]   # bugünün (henüz kapanmamış) barını kullanma

# ---------------------------------------------------------------- backtest
def backtest(a):
    c = load_cfg(a.config); curves = {}; allb = {}
    for s in c["sleeves"]:
        bars = fetch(s["symbol"], "1d", "10y"); time.sleep(1)
        ac, cv = run(bars, s["strat"], s["cfg"]); curves[s["id"]] = {day(b[0]): v for b, v in zip(bars, cv)}
        allb[s["id"]] = (len(ac.trades), sum(t["pnl"] for t in ac.trades), ac.halted)
    dates = sorted({d for cv in curves.values() for d in cv}); eq = []; last = {k: c["capital"]/len(c["sleeves"]) for k in curves}
    for d in dates:
        for k, cv in curves.items():
            if d in cv: last[k] = cv[d]
        eq.append(sum(last.values()))
    # BTC verisi en eskiyi başlatır; tüm sleeve'ler veri başladıktan sonra anlamlı: ortak başlangıç
    first = max(min(cv) for cv in curves.values()); i0 = dates.index(first); dates, eq = dates[i0:], eq[i0:]
    r = [eq[i]/eq[i-1]-1 for i in range(1, len(eq))]; mu = sum(r)/len(r); sd = math.sqrt(sum((x-mu)**2 for x in r)/len(r))
    peak, dd = eq[0], 0
    for x in eq: peak = max(peak, x); dd = max(dd, (peak-x)/peak)
    yrs = (len(eq))/365.25 if False else (time.mktime(time.strptime(dates[-1], "%Y-%m-%d")) - time.mktime(time.strptime(dates[0], "%Y-%m-%d")))/(365.25*86400)
    tot = eq[-1]/eq[0]-1
    print(f"\nPORTFÖY {dates[0]} → {dates[-1]} ({yrs:.1f} yıl), başlangıç {eq[0]:.0f}$ → {eq[-1]:.0f}$")
    print(f"toplam {tot*100:.1f}%  CAGR {((1+tot)**(1/yrs)-1)*100:.1f}%  maxDD {dd*100:.1f}%  Sharpe(~yıllık) {mu/sd*math.sqrt(365):.2f}")
    print("\nyıllık getiri:")
    ys = {}
    for d, e in zip(dates, eq): ys.setdefault(d[:4], [e, e])[1] = e
    prev = eq[0]
    for y, (s0, s1) in sorted(ys.items()): print(f"  {y}: {(s1/prev-1)*100:6.1f}%"); prev = s1
    print("\nsleeve: işlem sayısı, net PnL")
    for k, (n, p, h) in allb.items(): print(f"  {k:11s} {n:3d} işlem  {p:8.0f}$" + ("  [KILL]" if h else ""))

# ---------------------------------------------------------------- paper
def paper(a):
    c = load_cfg(a.config); os.makedirs(STATE_DIR, exist_ok=True); lines = []; summary = []
    for s in c["sleeves"]:
        path = os.path.join(STATE_DIR, s["id"] + ".json")
        S = json.load(open(path)) if os.path.exists(path) else dict(last_ts=0, cash=s["capital"], peak=s["capital"], pos=None, pending=None, halted=False, trades=[], start=s["capital"])
        try: bars = closed_bars(s["symbol"])
        except Exception as e: lines.append(f"! {s['id']}: veri alınamadı ({e})"); continue
        I = s["strat"].prepare(bars); ac = Account(S["cash"], S["peak"]); ac.pos = Pos(**S["pos"]) if S["pos"] else None
        ac.pending = tuple(S["pending"]) if S["pending"] else None; ac.halted = S["halted"]; ac.trades = S["trades"]
        new = [i for i, b in enumerate(bars) if b[0] > S["last_ts"]] if S["last_ts"] else [len(bars)-1]
        for i in new:
            n0 = len(ac.trades); pend0 = ac.pending; had = ac.pos is not None
            step(ac, bars, I, i, s["strat"], s["cfg"])
            if ac.pos and not had: lines.append(f"{day(bars[i][0])} {s['id']}: ALIM doldu (sanal) {ac.pos.qty:.4f} @ {ac.pos.entry:.2f}, stop {ac.pos.stop:.2f}")
            for t in ac.trades[n0:]: lines.append(f"{day(bars[i][0])} {s['id']}: KAPANDI {t['why']} pnl {t['pnl']:+.2f}$ ({t['entry']:.2f}→{t['exit']:.2f})")
            if ac.pending and ac.pending != pend0: lines.append(f"{day(bars[i][0])} {s['id']}: SİNYAL {ac.pending[0]} → sonraki açılışta")
        S.update(last_ts=bars[-1][0] if bars else 0, cash=ac.cash, peak=ac.peak, halted=ac.halted, trades=ac.trades,
                 pending=list(ac.pending) if ac.pending else None, pos=vars(ac.pos) if ac.pos else None)
        json.dump(S, open(path, "w"), indent=1)
        eq = ac.mark(bars[-1][4]); pnl = sum(t["pnl"] for t in ac.trades)
        summary.append(dict(id=s["id"], symbol=s["symbol"], equity=eq, start=S["start"], trades=len(ac.trades), realized=pnl, pos=S["pos"], pending=S["pending"], halted=ac.halted, last=day(bars[-1][0]), price=bars[-1][4]))
    # rapor
    tot = sum(x["equity"] for x in summary); st = sum(x["start"] for x in summary)
    md = [f"# Paper trading raporu (SANAL HESAP)\n", f"Son güncelleme: {time.strftime('%F %T', time.gmtime())} UTC\n",
          f"**Toplam equity: {tot:,.0f}$** (başlangıç {st:,.0f}$, {((tot/st-1)*100 if st else 0):+.2f}%)\n",
          "| sleeve | sembol | equity | getiri | işlem | pozisyon | bekleyen sinyal | son bar |", "|---|---|---|---|---|---|---|---|"]
    for x in summary:
        pos = f"{x['pos']['qty']:.4f} @ {x['pos']['entry']:.2f} (stop {x['pos']['stop']:.2f})" if x["pos"] else "-"
        pend = x["pending"][0] if x["pending"] else "-"
        md.append(f"| {x['id']} | {x['symbol']} | {x['equity']:,.0f} | {(x['equity']/x['start']-1)*100:+.2f}% | {x['trades']} | {pos} | {pend} | {x['last']} |" + (" KILL" if x["halted"] else ""))
    log = os.path.join(HERE, "journal.log")
    if lines:
        with open(log, "a") as f: f.write("\n".join(lines) + "\n")
    md += ["", "## Son olaylar", ""] + (["```"] + (open(log).read().splitlines()[-25:] if os.path.exists(log) else []) + ["```"])
    open(os.path.join(HERE, "REPORT.md"), "w").write("\n".join(md) + "\n"); print("\n".join(md[:6 + len(summary)])); print("\n".join(lines))

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("mode", choices=["backtest", "paper"]); p.add_argument("--config"); p.add_argument("--once", action="store_true")
    a = p.parse_args(); (backtest if a.mode == "backtest" else paper)(a)
