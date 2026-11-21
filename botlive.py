"""Canlı SANAL bot (gerçek para YOK). server.py tarafından kullanılır.
  /api/bot/state            -> hesap, pozisyonlar (canlı fiyatla), günlük, otomatik bot özeti
  /api/bot/chart?symbol=X   -> bugünün 5 dakikalık fiyat serisi + anlık fiyat
  POST /api/bot/order       -> {"side":"buy|sell","symbol":"SI=F","usd":100}
Sadece Python standart kütüphanesi."""
import json, os, re, sys, time, threading, urllib.request, glob

DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "bot_state.json")
UA = {"User-Agent": "Mozilla/5.0"}
COST_BPS = 5.0           # her işlemde spread+komisyon varsayımı (%0,05)
START_CASH = 1000.0
NAMES = {"SI=F": "Gümüş", "GC=F": "Altın", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
         "CL=F": "Petrol", "SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq (QQQ)"}
_lock = threading.Lock()
_cache = {}

def _check_sym(sym):
    if not isinstance(sym, str) or not re.fullmatch(r"[A-Za-z0-9=\-\.\^]{1,15}", sym):
        raise ValueError("geçersiz sembol")
    return sym.upper()

def chart(sym, interval="5m", rng="1d", ttl=5):
    sym = _check_sym(sym); key = (sym, interval, rng); now = time.time()
    c = _cache.get(key)
    if c and now - c[0] < ttl:
        return c[1]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={rng}"
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15))
    d = r["chart"]["result"][0]; meta = d["meta"]
    cl = d["indicators"]["quote"][0]["close"]
    pts = [[t, round(v, 4)] for t, v in zip(d.get("timestamp") or [], cl) if v is not None]
    out = {"symbol": sym, "name": NAMES.get(sym, sym), "price": float(meta["regularMarketPrice"]),
           "prev_close": meta.get("chartPreviousClose"), "price_time": meta.get("regularMarketTime"),
           "points": pts, "now": int(now)}
    _cache[key] = (now, out)
    return out

def _load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    old = os.path.join(DIR, "trader", "state", "manual.json")   # elle denemelerden devral
    if os.path.exists(old):
        try:
            m = json.load(open(old)); now = int(time.time())
            return {"cash": m["cash"], "start": m["start"], "pos": {k: dict(v, opened=now) for k, v in m["pos"].items()},
                    "log": [{"t": now, "txt": x} for x in m.get("log", [])]}
        except Exception:
            pass
    return {"cash": START_CASH, "start": START_CASH, "pos": {}, "log": []}

def _save(s):
    tmp = STATE + ".tmp"; json.dump(s, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, STATE)

def _auto():
    """trader/ klasöründeki otomatik trend botunun sanal hesap özeti (varsa)."""
    out = []
    for f in sorted(glob.glob(os.path.join(DIR, "trader", "state", "*-*.json"))):
        try:
            S = json.load(open(f)); sid = os.path.basename(f)[:-5]
            out.append({"id": sid, "equity": round(S["cash"], 2), "start": round(S.get("start", S["cash"]), 2),
                        "trades": len(S.get("trades", [])), "pos": bool(S.get("pos")),
                        "pending": (S["pending"][0] if S.get("pending") else None),
                        "halted": bool(S.get("halted")), "last_ts": S.get("last_ts")})
        except Exception:
            pass
    return out

def state():
    with _lock:
        s = _load()
    pos, total = [], s["cash"]
    for sym, p in s["pos"].items():
        try:
            ch = chart(sym); px = ch["price"]; ptime = ch["price_time"]
        except Exception:
            px, ptime = p["avg"], None
        val = p["qty"] * px; total += val
        pos.append({"symbol": sym, "name": NAMES.get(sym, sym), "qty": p["qty"], "avg": p["avg"], "price": px,
                    "value": val, "pnl": val - p["cost"], "pnl_pct": (val / p["cost"] - 1) * 100 if p["cost"] else 0,
                    "price_time": ptime, "opened": p.get("opened")})
    return {"ok": True, "now": int(time.time()), "cash": s["cash"], "start": s["start"], "total": total,
            "pnl": total - s["start"], "positions": pos, "log": s["log"][-30:][::-1], "auto": _auto(), "symbols": NAMES}

def order(side, sym, usd):
    sym = _check_sym(sym); k = COST_BPS / 1e4; now = int(time.time())
    px = chart(sym, ttl=2)["price"]
    with _lock:
        s = _load()
        if side == "buy":
            usd = float(usd)
            if not (0 < usd <= s["cash"] + 1e-9):
                return {"ok": False, "error": "Yetersiz nakit (%.2f $)" % s["cash"]}
            fill = px * (1 + k); qty = usd / fill
            p = s["pos"].setdefault(sym, {"qty": 0.0, "cost": 0.0, "avg": 0.0, "opened": now})
            p["qty"] += qty; p["cost"] += usd; p["avg"] = p["cost"] / p["qty"]; s["cash"] -= usd
            s["log"].append({"t": now, "txt": "AL %s %.2f $ @ %.3f" % (NAMES.get(sym, sym), usd, fill)})
        elif side == "sell":
            p = s["pos"].get(sym)
            if not p:
                return {"ok": False, "error": "%s pozisyonun yok" % NAMES.get(sym, sym)}
            qty = p["qty"]; fill = px * (1 - k); proceeds = qty * fill; pnl = proceeds - p["cost"]
            s["cash"] += proceeds; del s["pos"][sym]
            s["log"].append({"t": now, "txt": "SAT %s %.2f $ @ %.3f  (kâr/zarar %+.2f $)" % (NAMES.get(sym, sym), proceeds, fill, pnl)})
        else:
            return {"ok": False, "error": "side buy ya da sell olmalı"}
        _save(s)
    return {"ok": True}

def reset():
    with _lock:
        _save({"cash": START_CASH, "start": START_CASH, "pos": {}, "log": []})
    return {"ok": True}

def _auto_step():
    sys.path.insert(0, os.path.join(DIR, "trader"))
    import argparse, portfolio
    portfolio.paper(argparse.Namespace(config=None, once=True))

def start_auto():
    """Otomatik trend botunun günlük adımını 6 saatte bir çalıştırır (yeni kapanmış bar varsa işler)."""
    def loop():
        time.sleep(90)
        while True:
            try:
                _auto_step()
            except Exception:
                pass
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True).start()
