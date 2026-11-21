#!/usr/bin/env python3
"""ELLE sanal işlem (gerçek para YOK). Güncel fiyattan sanal alım/satım yapar.
  python3 manual.py buy  SI=F 100      # 100 $'lık gümüş al
  python3 manual.py sell SI=F all      # hepsini sat   (veya: sell SI=F 50  -> 50 $'lık sat)
  python3 manual.py status             # pozisyonlar, kâr/zarar
  python3 manual.py reset              # hesabı 1000 $'a sıfırla
"""
import json, os, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); PATH = os.path.join(HERE, "state", "manual.json")
COST_BPS = 5.0   # her işlemde spread+komisyon varsayımı (%0,05)
NAMES = {"SI=F": "Gümüş", "GC=F": "Altın", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "CL=F": "Petrol", "SPY": "S&P 500", "QQQ": "Nasdaq"}

def price(sym):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d"
    r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
    return float(r["chart"]["result"][0]["meta"]["regularMarketPrice"])

def load():
    if os.path.exists(PATH): return json.load(open(PATH))
    return {"cash": 1000.0, "start": 1000.0, "pos": {}, "log": []}

def save(s):
    os.makedirs(os.path.dirname(PATH), exist_ok=True); json.dump(s, open(PATH, "w"), indent=1, ensure_ascii=False)

def now(): return time.strftime("%F %T UTC", time.gmtime())

def status(s):
    tot = s["cash"]; print(f"\nSANAL HESAP (gerçek para yok)   nakit: {s['cash']:,.2f} $")
    for sym, p in s["pos"].items():
        px = price(sym); val = p["qty"] * px; tot += val; pnl = val - p["cost"]
        print(f"  {NAMES.get(sym, sym):9s} {sym:8s} {p['qty']:.4f} adet  alış {p['avg']:.3f}  şimdi {px:.3f}  değer {val:,.2f} $  kâr/zarar {pnl:+.2f} $ ({pnl/p['cost']*100:+.2f}%)")
    print(f"  TOPLAM: {tot:,.2f} $  (başlangıç {s['start']:,.0f} $, {tot-s['start']:+,.2f} $)")
    if s["log"]: print("  son işlemler:", *[f"\n    {l}" for l in s["log"][-5:]])

def main():
    a = sys.argv[1:]; s = load()
    if not a or a[0] == "status": return status(s)
    if a[0] == "reset": save({"cash": 1000.0, "start": 1000.0, "pos": {}, "log": []}); return print("Hesap 1000 $ ile sıfırlandı.")
    side, sym = a[0], a[1].upper() if len(a) > 1 else None; amt = a[2] if len(a) > 2 else None
    px = price(sym); k = COST_BPS / 1e4
    if side == "buy":
        usd = float(amt)
        if usd > s["cash"]: sys.exit(f"Yetersiz nakit: {s['cash']:.2f} $")
        fill = px * (1 + k); qty = usd / fill; p = s["pos"].setdefault(sym, {"qty": 0.0, "cost": 0.0, "avg": 0.0})
        p["qty"] += qty; p["cost"] += usd; p["avg"] = p["cost"] / p["qty"]; s["cash"] -= usd
        s["log"].append(f"{now()} AL  {sym} {usd:.2f}$ @ {fill:.3f}"); print(f"AL: {usd:.2f} $ → {qty:.4f} adet {sym} @ {fill:.3f} (sanal)")
    elif side == "sell":
        p = s["pos"].get(sym)
        if not p: sys.exit(f"{sym} pozisyonun yok.")
        qty = p["qty"] if amt in (None, "all") else min(p["qty"], float(amt) / px)
        fill = px * (1 - k); proceeds = qty * fill; cost = p["avg"] * qty; pnl = proceeds - cost
        p["qty"] -= qty; p["cost"] -= cost; s["cash"] += proceeds
        if p["qty"] < 1e-9: del s["pos"][sym]
        s["log"].append(f"{now()} SAT {sym} {proceeds:.2f}$ @ {fill:.3f} kâr/zarar {pnl:+.2f}$"); print(f"SAT: {qty:.4f} adet {sym} @ {fill:.3f} → {proceeds:.2f} $ (kâr/zarar {pnl:+.2f} $, sanal)")
    else: sys.exit(__doc__)
    save(s); status(s)

if __name__ == "__main__": main()
