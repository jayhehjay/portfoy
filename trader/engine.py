"""Backtest ve paper trading AYNI step() fonksiyonunu kullanır."""
from dataclasses import dataclass, field
import math

@dataclass
class Config:
    start: float = 10000.0
    cost_bps: float = 3.0      # işlem başına (spread+slippage), her yön için
    risk_pct: float = 0.01     # stop'a düşersen hesabın %1'i gider
    max_lev: float = 1.0       # nominal pozisyon <= equity * max_lev
    kill_dd: float = 0.25      # zirveden %25 düşüşte bot DURUR (kill switch)

@dataclass
class Pos:
    d: int; qty: float; entry: float; stop: float; i: int; ts: int

@dataclass
class Account:
    cash: float
    peak: float = 0.0
    pos: Pos = None
    pending: tuple = None
    halted: bool = False
    trades: list = field(default_factory=list)
    def mark(self, price): return self.cash + (self.pos.d*self.pos.qty*(price-self.pos.entry) if self.pos else 0.0)

def _close(a, px, ts, why):
    p = a.pos; pnl = p.d*p.qty*(px-p.entry); a.cash += pnl
    a.trades.append(dict(t_in=p.ts, t_out=ts, d=p.d, qty=p.qty, entry=p.entry, exit=px, pnl=pnl, why=why, bars=None))
    a.pos = None

def step(a, bars, I, i, strat, cfg):
    ts, o, h, l, c = bars[i][:5]; k = cfg.cost_bps/1e4
    pend, a.pending = a.pending, None
    if pend and pend[0] == "exit" and a.pos: _close(a, o*(1-a.pos.d*k), ts, "signal")
    elif pend and pend[0] == "enter" and not a.pos and not a.halted:
        d, sl = pend[1], pend[2]; entry = o*(1+d*k)
        qty = min(cfg.risk_pct*a.cash/sl, cfg.max_lev*a.cash/entry)
        if qty > 0: a.pos = Pos(d, qty, entry, o - d*sl, i, ts)
    p = a.pos
    if p:   # stop: bar içinde, boşluk (gap) varsa açılıştan
        if p.d == 1 and l <= p.stop: _close(a, min(p.stop, o)*(1-k), ts, "stop")
        elif p.d == -1 and h >= p.stop: _close(a, max(p.stop, o)*(1+k), ts, "stop")
    if a.pos:
        if strat.exit_signal(I, i, a.pos.d, i - a.pos.i): a.pending = ("exit",)
    elif not a.halted:
        d = strat.entry_signal(I, i)
        if d:
            sl = strat.stop_dist(I, i)
            if sl > 0: a.pending = ("enter", d, sl)
    eq = a.mark(c); a.peak = max(a.peak, eq)
    if a.peak and (a.peak-eq)/a.peak > cfg.kill_dd:
        a.halted = True
        if a.pos: a.pending = ("exit",)
    return eq

def run(bars, strat, cfg=Config()):
    I = strat.prepare(bars); a = Account(cfg.start, peak=cfg.start); curve = []
    for i in range(len(bars)): curve.append(step(a, bars, I, i, strat, cfg))
    return a, curve

def metrics(bars, a, curve, ppy):
    n = len(curve)
    r = [curve[i]/curve[i-1]-1 for i in range(1, n) if curve[i-1] > 0]
    mu = sum(r)/len(r) if r else 0; sd = math.sqrt(sum((x-mu)**2 for x in r)/len(r)) if r else 0
    peak, dd = curve[0], 0.0
    for x in curve: peak = max(peak, x); dd = max(dd, (peak-x)/peak)
    yrs = max((bars[-1][0]-bars[0][0])/(365.25*86400), 1e-9)
    return dict(sharpe=(mu/sd*math.sqrt(ppy) if sd else 0), maxdd=dd, years=yrs)

def summarize(bars, a, curve, cfg, ppy):
    tr = a.trades; w = [t["pnl"] for t in tr if t["pnl"] > 0]; ls = [t["pnl"] for t in tr if t["pnl"] <= 0]
    m = metrics(bars, a, curve, ppy); tot = curve[-1]/cfg.start - 1
    bh = bars[-1][4]/bars[0][4] - 1
    return dict(n=len(tr), win=(len(w)/len(tr) if tr else 0), avg_w=(sum(w)/len(w) if w else 0), avg_l=(sum(ls)/len(ls) if ls else 0),
                pf=(sum(w)/abs(sum(ls)) if ls and sum(ls) else float("inf")), ret=tot, cagr=((1+tot)**(1/m["years"])-1 if tot > -1 else -1),
                maxdd=m["maxdd"], sharpe=m["sharpe"], bh=bh, stops=sum(1 for t in tr if t["why"] == "stop"), halted=a.halted)
