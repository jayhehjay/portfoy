#!/usr/bin/env python3
"""Kullanım:
  python3 run.py backtest --symbol GC=F --interval 1d --range 10y
  python3 run.py paper    --symbol GC=F --interval 1h --range 60d      (sanal para, gerçek emir YOK)
"""
import argparse, itertools, json, os, sys, time
from data import fetch
from strategy import MeanReversion
from engine import Config, Account, run, step, summarize

PPY = {"1d": 252, "1h": 5750, "1wk": 52, "15m": 23000}

def fmt(name, s):
    return (f"{name:34s} işlem={s['n']:3d} win={s['win']*100:4.0f}% stop={s['stops']:3d} ort.+{s['avg_w']:7.0f}/ort.{s['avg_l']:7.0f} "
            f"PF={s['pf']:4.2f} getiri={s['ret']*100:6.1f}% CAGR={s['cagr']*100:5.1f}% maxDD={s['maxdd']*100:4.1f}% "
            f"Sharpe={s['sharpe']:4.2f} al-tut={s['bh']*100:6.1f}%" + (" [KILL]" if s['halted'] else ""))

def backtest(a):
    bars = fetch(a.symbol, a.interval, a.range); ppy = PPY[a.interval]; cfg = Config(cost_bps=a.cost_bps)
    cut = int(len(bars)*0.6); ins, oos = bars[:cut], bars[cut:]
    d = lambda b: time.strftime("%Y-%m-%d", time.gmtime(b[0]))
    print(f"\n=== {a.symbol} {a.interval} | {len(bars)} bar | eğitim {d(ins[0])}→{d(ins[-1])} | test {d(oos[0])}→{d(oos[-1])} | maliyet {a.cost_bps}bps/yön")
    for sh in (False, True):
        grid = itertools.product((10, 20, 40), (1.5, 2.0, 2.5), (25, 35), (2, 3, 5))
        best = None
        for n, k, r, sl in grid:
            st = MeanReversion(n=n, k=k, rsi_lo=r, sl_atr=sl, max_hold=a.max_hold, allow_short=sh)
            ac, cv = run(ins, st, cfg); s = summarize(ins, ac, cv, cfg, ppy)
            if s["n"] < 15: continue
            score = s["ret"] / (s["maxdd"] + 0.05)
            if best is None or score > best[0]: best = (score, st, s)
        tag = "long+short" if sh else "sadece long"
        if not best: print(f"[{tag}] eğitimde yeterli işlem yok"); continue
        st = best[1]; print(f"\n[{tag}] eğitimde seçilen: {st.params()}")
        print(fmt("  eğitim (seçim burada yapıldı)", best[2]))
        ac, cv = run(oos, st, cfg); print(fmt("  TEST (hiç görülmemiş veri)", summarize(oos, ac, cv, cfg, ppy)))
        dflt = MeanReversion(max_hold=a.max_hold, allow_short=sh); ac, cv = run(oos, dflt, cfg)
        print(fmt("  TEST varsayılan (ayarsız)", summarize(oos, ac, cv, cfg, ppy)))

def paper(a):
    """Sanal hesap. Aynı step() kodunu kullanır. Durum state_<sembol>.json dosyasında tutulur."""
    cfg = Config(cost_bps=a.cost_bps, start=a.capital); st = MeanReversion(max_hold=a.max_hold, allow_short=a.short)
    path = f"state_{a.symbol.replace('=','_')}.json"
    state = json.load(open(path)) if os.path.exists(path) else {"last_ts": 0, "cash": cfg.start, "peak": cfg.start, "pos": None, "pending": None, "halted": False, "trades": []}
    while True:
        bars = fetch(a.symbol, a.interval, a.range, use_cache=False)
        bars = bars[:-1]  # son bar henüz kapanmamış olabilir: kullanma
        I = st.prepare(bars); acct = Account(state["cash"], state["peak"])
        from engine import Pos
        acct.pos = Pos(**state["pos"]) if state["pos"] else None
        acct.pending = tuple(state["pending"]) if state["pending"] else None
        acct.halted = state["halted"]; acct.trades = state["trades"]
        new = [i for i, b in enumerate(bars) if b[0] > state["last_ts"]] if state["last_ts"] else [len(bars)-1]
        for i in new:
            before = len(acct.trades); eq = step(acct, bars, I, i, st, cfg)
            for t in acct.trades[before:]: print("KAPANDI:", t)
            if acct.pending: print(f"{time.strftime('%F %T', time.gmtime(bars[i][0]))} SİNYAL → {acct.pending} (sanal)")
        if new:
            state.update(last_ts=bars[-1][0], cash=acct.cash, peak=acct.peak, halted=acct.halted, trades=acct.trades,
                         pending=list(acct.pending) if acct.pending else None,
                         pos=vars(acct.pos) if acct.pos else None)
            json.dump(state, open(path, "w"), indent=1)
            print(f"equity≈{acct.mark(bars[-1][4]):.2f} pozisyon={'var' if acct.pos else 'yok'} halted={acct.halted}")
        if a.once: break
        time.sleep(a.poll)

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("mode", choices=["backtest", "paper"])
    p.add_argument("--symbol", default="GC=F"); p.add_argument("--interval", default="1d"); p.add_argument("--range", default="10y")
    p.add_argument("--cost-bps", type=float, default=3.0); p.add_argument("--max-hold", type=int, default=20)
    p.add_argument("--capital", type=float, default=10000); p.add_argument("--short", action="store_true")
    p.add_argument("--poll", type=int, default=300); p.add_argument("--once", action="store_true")
    a = p.parse_args(); (backtest if a.mode == "backtest" else paper)(a)
