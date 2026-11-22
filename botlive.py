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
            "pnl": total - s["start"], "positions": pos, "log": s["log"][-30:][::-1], "auto": _auto(), "symbols": NAMES, "bot": auto_state()}

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


# ====================== OTOMATİK CANLI BOT (sanal) ======================
# Kural ("düşükten al, ortalamaya dönünce sat"), 5 dakikalık mumlar, dakikada bir karar:
#   AL : fiyat alt Bollinger bandının (SMA20 - 2*sapma) altında VE RSI(14) < 35
#   SAT: fiyat SMA20'ye dönünce (hedef)  |  giriş fiyatından %1,5 düşünce (stop)  |  3 saat dolunca (zaman stopu)
AUTO_SYMS = ["BTC-USD", "ETH-USD"]
AUTO_USD = 100.0          # her işlem tutarı
AUTO_MAX_POS = 2
AUTO_STOP = 0.015
AUTO_RSI = 35
AUTO_GAP = 0.0025
SERIES_N = 288            # grafikte son 24 saat (5 dk'lık 288 mum)
AUTO_MAX_HOLD = 3 * 3600
AUTO_COOLDOWN = 15 * 60
AUTO_FILE = os.path.join(DIR, "bot_auto.json")
_eval = {}                # son değerlendirme (sayfada "bot şu an ne düşünüyor")
_last_tick = [0]

def _ind(closes, n=20, rn=14):
    if len(closes) < max(n, rn + 1) + 1:
        return None
    w = closes[-n:]; m = sum(w) / n; sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
    g = l = 0.0
    for i in range(len(closes) - rn, len(closes)):
        d = closes[i] - closes[i - 1]; g += max(d, 0); l += max(-d, 0)
    rsi = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return {"c": closes[-1], "sma": m, "lo": m - 2 * sd, "hi": m + 2 * sd, "rsi": rsi}

def _series(closes, ts, n=20, rn=14):
    """Grafik için son SERIES_N mumun bant ve RSI serisi."""
    out = {"t": [], "c": [], "sma": [], "lo": [], "hi": [], "rsi": []}
    for i in range(max(len(closes) - SERIES_N, max(n, rn + 1)), len(closes)):
        w = closes[i - n + 1:i + 1]; m = sum(w) / n; sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        g = l = 0.0
        for j in range(i - rn + 1, i + 1):
            d = closes[j] - closes[j - 1]; g += max(d, 0); l += max(-d, 0)
        rsi = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
        out["t"].append(ts[i]); out["c"].append(round(closes[i], 4)); out["sma"].append(round(m, 4))
        out["lo"].append(round(m - 2 * sd, 4)); out["hi"].append(round(m + 2 * sd, 4)); out["rsi"].append(round(rsi, 1))
    return out

def _aload():
    if os.path.exists(AUTO_FILE):
        try:
            return json.load(open(AUTO_FILE))
        except Exception:
            pass
    return {"cash": START_CASH, "start": START_CASH, "pos": {}, "log": [], "marks": [], "cool": {}, "eq": []}

def _asave(s):
    tmp = AUTO_FILE + ".tmp"; json.dump(s, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, AUTO_FILE)

def auto_tick():
    """Her çağrıda her sembol için karar verir. Dakikada bir çağrılır."""
    now = int(time.time()); k = COST_BPS / 1e4
    with _lock:
        s = _aload()
        for sym in AUTO_SYMS:
            try:
                ch = chart(sym, "5m", "5d", ttl=20)
            except Exception:
                continue
            closes = [p[1] for p in ch["points"]]
            if closes: closes[-1] = ch["price"]
            ind = _ind(closes)
            if not ind:
                continue
            nm = NAMES.get(sym, sym); px = ch["price"]; p = s["pos"].get(sym)
            status = "bekliyor"
            if p:
                pnl_pct = px / p["avg"] - 1; held = now - p["opened"]
                why = None
                if px >= ind["sma"] and pnl_pct > 0.0006:
                    why = "hedef: fiyat ortalamaya döndü"
                elif pnl_pct <= -AUTO_STOP:
                    why = "stop: %1,5 düştü"
                elif held >= AUTO_MAX_HOLD:
                    why = "zaman doldu (3 saat)"
                if why:
                    fill = px * (1 - k); proceeds = p["qty"] * fill; pnl = proceeds - p["cost"]
                    s["cash"] += proceeds; del s["pos"][sym]; s["cool"][sym] = now
                    s["log"].append({"t": now, "kind": "sell", "txt": "SAT %s @ %.2f → %+.2f $ (%s)" % (nm, fill, pnl, why)})
                    s["marks"].append({"t": now, "sym": sym, "side": "sell", "px": fill})
                    status = "sattı"
                else:
                    status = "pozisyonda (%+.2f%%)" % (pnl_pct * 100)
            else:
                signal = ind["c"] < ind["lo"] and ind["rsi"] < AUTO_RSI and (ind["sma"] / ind["c"] - 1) >= AUTO_GAP   # ortalamaya dönüş payı maliyeti aşmalı
                cool = now - s["cool"].get(sym, 0) < AUTO_COOLDOWN
                if signal and not cool and s["cash"] >= AUTO_USD and len(s["pos"]) < AUTO_MAX_POS:
                    fill = px * (1 + k); qty = AUTO_USD / fill
                    s["pos"][sym] = {"qty": qty, "cost": AUTO_USD, "avg": fill, "opened": now}; s["cash"] -= AUTO_USD
                    s["log"].append({"t": now, "kind": "buy", "txt": "AL %s %.0f $ @ %.2f — fiyat alt banda indi (%.2f < %.2f), RSI %.0f" % (nm, AUTO_USD, fill, ind["c"], ind["lo"], ind["rsi"])})
                    s["marks"].append({"t": now, "sym": sym, "side": "buy", "px": fill})
                    status = "aldı"
                elif signal and cool:
                    status = "sinyal var, ama bekleme süresinde"
                elif ind["c"] < ind["lo"]:
                    status = "alt bantta, RSI henüz yüksek (%.0f)" % ind["rsi"]
            ts = [pt[0] for pt in ch["points"]]
            _eval[sym] = {"name": nm, "price": px, "sma": ind["sma"], "lo": ind["lo"], "hi": ind["hi"], "rsi": ind["rsi"], "status": status, "t": now,
                          "gap": ind["sma"] / ind["c"] - 1, "series": _series(closes, ts),
                          "th": {"rsi": AUTO_RSI, "gap": AUTO_GAP, "stop": AUTO_STOP, "hold": AUTO_MAX_HOLD}}
        total = s["cash"] + sum(p["qty"] * (_eval.get(sy, {}).get("price") or p["avg"]) for sy, p in s["pos"].items())
        if not s["eq"] or now - s["eq"][-1][0] >= 300:
            s["eq"].append([now, round(total, 2)])
        s["log"] = s["log"][-200:]; s["marks"] = s["marks"][-200:]; s["eq"] = s["eq"][-600:]
        _asave(s)
    _last_tick[0] = now

def _closed_trades(s):
    """Alış ve satış işaretlerini eşleştirip tamamlanmış işlemlerin listesini çıkarır (yeniden eskiye)."""
    openb, out = {}, []
    sells = {(l["t"]): l for l in s["log"] if l["kind"] == "sell"}
    for m in sorted(s["marks"], key=lambda x: x["t"]):
        if m["side"] == "buy":
            openb[m["sym"]] = m
        elif m["sym"] in openb:
            bm = openb.pop(m["sym"]); lg = None
            for l in s["log"]:
                if l["kind"] == "sell" and l["t"] == m["t"] and NAMES.get(m["sym"], m["sym"]) in l["txt"]:
                    lg = l; break
            pnl, why = None, ""
            if lg:
                mt = re.search(r"→ ([+-]?[\d.]+) \$ \((.*)\)\s*$", lg["txt"])
                if mt: pnl, why = float(mt.group(1)), mt.group(2)
            pct = (m["px"] / bm["px"] - 1) * 100
            out.append({"sym": m["sym"], "name": NAMES.get(m["sym"], m["sym"]), "t_in": bm["t"], "px_in": bm["px"], "t_out": m["t"], "px_out": m["px"],
                        "pnl": pnl if pnl is not None else AUTO_USD * pct / 100, "pnl_pct": pct, "why": why, "mins": round((m["t"] - bm["t"]) / 60)})
    return out[::-1]

def auto_state():
    with _lock:
        s = _aload()
    pos, total = [], s["cash"]
    for sym, p in s["pos"].items():
        px = _eval.get(sym, {}).get("price") or p["avg"]; val = p["qty"] * px; total += val
        pos.append({"symbol": sym, "name": NAMES.get(sym, sym), "avg": p["avg"], "price": px, "value": val, "qty": p["qty"],
                    "pnl": val - p["cost"], "pnl_pct": (val / p["cost"] - 1) * 100, "opened": p["opened"],
                    "stop_px": p["avg"] * (1 - AUTO_STOP), "target_px": _eval.get(sym, {}).get("sma"),
                    "time_left": max(0, AUTO_MAX_HOLD - (int(time.time()) - p["opened"]))})
    trades = [l for l in s["log"] if l["kind"] == "sell"]
    return {"cash": s["cash"], "start": s["start"], "total": total, "pnl": total - s["start"], "positions": pos,
            "log": s["log"][-40:][::-1], "marks": s["marks"], "eval": _eval, "last_tick": _last_tick[0],
            "closed": _closed_trades(s), "trades": len(trades), "wins": sum(1 for l in trades if "+" in l["txt"].split("→")[1][:3]),
            "rule": "Alt Bollinger bandı + RSI<35 + yeterli düşüş (%0,25+) → AL 100 $; ortalamaya dönünce SAT; %1,5 stop; 3 sa zaman stopu"}

def auto_reset():
    with _lock:
        _asave({"cash": START_CASH, "start": START_CASH, "pos": {}, "log": [], "marks": [], "cool": {}, "eq": []})
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

    def live():           # dakikada bir canlı karar
        time.sleep(10)
        while True:
            try:
                auto_tick()
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=live, daemon=True).start()
