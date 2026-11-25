"""Günlük SWING botu (sanal hesap, gerçek para YOK).
4 teknik analiz birlikte oy verir: 200 günlük ortalama (trend, zorunlu), direnç kırılımı, RSI(14), Bollinger orta bandı.
Giriş: trend + en az 3/4 onay. Çıkış: 200 günlük altı | destek kırılımı | ATR stop.
Durum KAYDEDİLMEZ: hesap, geçmiş fiyatlardan her seferinde yeniden hesaplanır (sunucu yeniden başlasa da kaybolmaz).
Giriş/çıkış zamanı gelince Telegram bildirimi gönderir (varsa)."""
import json, os, sys, time, threading

DIR = os.path.dirname(os.path.abspath(__file__))
SYMS = [("BTC-USD", "Bitcoin", 10), ("ETH-USD", "Ethereum", 12), ("GC=F", "Altın", 3),
        ("SI=F", "Gümüş", 5), ("SPY", "S&P 500 (SPY)", 2), ("QQQ", "Nasdaq (QQQ)", 2)]
START = 1000.0
REPLAY = 420            # simülasyon penceresi (gün/bar)
SERIES_N = 200          # grafikte son 200 gün
NOTIFIED = os.path.join(DIR, "swing_notified.json")
NOTIFY = None           # server.py tg_send'i buraya bağlar
NOTIFY_READY = lambda: False
_lock = threading.Lock()
_cache = {"t": 0, "res": None}

def _mods():
    p = os.path.join(DIR, "trader")
    if p not in sys.path:
        sys.path.insert(0, p)
    import data, strategy, engine
    return data, strategy, engine

def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))

def _asset(sym, name, bps):
    data, strategy, engine = _mods()
    bars = data.fetch(sym, "1d", "3y", use_cache=False)
    today = _day(time.time())
    bars = [b for b in bars if _day(b[0]) < today]          # bugünün (kapanmamış) barını kullanma
    st = strategy.Swing(3, 20, 10)
    I = st.prepare(bars); n = len(bars)
    cap = START / len(SYMS)
    cfg = engine.Config(start=cap, cost_bps=bps, risk_pct=1.0, max_lev=1.0, kill_dd=0.99)
    a = engine.Account(cap, peak=cap); i0 = max(210, n - REPLAY); eq = []
    for i in range(i0, n):
        eq.append((bars[i][0], engine.step(a, bars, I, i, st, cfg)))
    i = n - 1; c = I["c"][i]; sc = st.score(I, i) or (False, False, False, False)
    m, sd = I["mid"][i], I["sd"][i]
    pos = a.pos; pend = a.pending[0] if a.pending else None
    reason = None
    if pend == "exit":
        reason = "200 günlük ortalamanın altına indi" if not sc[0] else "destek kırıldı (10 günlük dip altında kapanış)"
    ts_idx = {b[0]: k for k, b in enumerate(bars)}
    trades = []
    for t in a.trades:
        k = ts_idx.get(t["t_out"]); why = "stop çalıştı"
        if t["why"] != "stop" and k:
            sci = st.score(I, k - 1)
            why = "200 günlük ortalamanın altına indi" if (sci and not sci[0]) else "destek kırıldı"
        trades.append({"sym": sym, "name": name, "t_in": t["t_in"], "t_out": t["t_out"], "px_in": t["entry"], "px_out": t["exit"],
                       "pnl": t["pnl"], "pnl_pct": (t["exit"] / t["entry"] - 1) * 100, "days": round((t["t_out"] - t["t_in"]) / 86400, 1), "why": why})
    lo0 = max(n - SERIES_N, 0)
    def r(v, d=4): return None if v is None else round(v, d)
    series = {"t": [bars[k][0] for k in range(lo0, n)], "c": [r(I["c"][k]) for k in range(lo0, n)], "sma200": [r(I["t"][k]) for k in range(lo0, n)],
              "mid": [r(I["mid"][k]) for k in range(lo0, n)],
              "up": [r(I["mid"][k] + 2 * I["sd"][k]) if I["mid"][k] is not None else None for k in range(lo0, n)],
              "lo": [r(I["mid"][k] - 2 * I["sd"][k]) if I["mid"][k] is not None else None for k in range(lo0, n)],
              "rsi": [r(I["r"][k], 1) for k in range(lo0, n)]}
    return {"sym": sym, "name": name, "price": c, "bar_date": _day(bars[-1][0]), "bar_ts": bars[-1][0],
            "sma200": I["t"][i], "mid": m, "up": m + 2 * sd, "lo": m - 2 * sd, "rsi": I["r"][i], "resistance": I["hh"][i], "support": I["ll"][i], "atr": I["a"][i],
            "conds": {"trend": bool(sc[0]), "breakout": bool(sc[1]), "rsi": bool(sc[2]), "boll": bool(sc[3])}, "score": int(sum(sc)),
            "pending": pend, "reason": reason, "stop_dist": (st.stop_dist(I, i) if I["a"][i] else None),
            "pos": ({"entry": pos.entry, "stop": pos.stop, "qty": pos.qty, "t_in": pos.ts, "pnl": pos.qty * (c - pos.entry),
                     "pnl_pct": (c / pos.entry - 1) * 100, "value": pos.qty * c} if pos else None),
            "trades": trades, "series": series, "cap": cap, "equity": a.mark(c), "eq": eq}

def compute():
    assets, errs = [], []
    for sym, name, bps in SYMS:
        try:
            assets.append(_asset(sym, name, bps)); time.sleep(0.5)
        except Exception as e:
            errs.append("%s: %s" % (sym, e))
    # birleşik equity eğrisi
    dates = sorted({t for a in assets for t, _ in a["eq"]}); last = {a["sym"]: a["cap"] for a in assets}
    maps = {a["sym"]: dict(a["eq"]) for a in assets}; eq = []
    for t in dates:
        for s, mp in maps.items():
            if t in mp: last[s] = mp[t]
        eq.append([t, round(sum(last.values()), 2)])
    total = sum(a["equity"] for a in assets); start = sum(a["cap"] for a in assets)
    closed = sorted([t for a in assets for t in a["trades"]], key=lambda x: -x["t_out"])
    for a in assets:
        a.pop("eq"); a["trades"] = [t for t in a["trades"]]
    return {"ok": True, "now": int(time.time()), "start": start, "total": total, "pnl": total - start, "eq": eq[-300:], "assets": assets, "closed": closed,
            "errors": errs, "notify_ready": bool(NOTIFY_READY()),
            "rule": "Trend (200 günlük ortalama üstü) şart + direnç kırılımı / RSI>50 / Bollinger orta bandı üstü: en az 3/4 onay → AL. 200 günlük altı ya da destek kırılımı → SAT."}

def _load_sent():
    try:
        return json.load(open(NOTIFIED))
    except Exception:
        return {}

def _fmt(v, d=2):
    return ("{:,.%df}" % d).format(v).replace(",", "X").replace(".", ",").replace("X", ".") if v is not None else "—"

def _dp(v): return 2 if v >= 1000 else 3 if v >= 1 else 4

def _messages(res):
    out = []
    for a in res["assets"]:
        nm, px, d = a["name"], a["price"], _dp(a["price"])
        if a["pending"] == "enter":
            cs = a["conds"]
            lines = ["%s 200 günlük ortalamanın üstünde (%s)" % ("✅" if cs["trend"] else "❌", _fmt(a["sma200"], d)),
                     "%s Direnç kırılımı (20 günlük zirve %s)" % ("✅" if cs["breakout"] else "❌", _fmt(a["resistance"], d)),
                     "%s RSI(14) %s (eşik 50)" % ("✅" if cs["rsi"] else "❌", _fmt(a["rsi"], 0)),
                     "%s Bollinger orta bandının üstünde (%s)" % ("✅" if cs["boll"] else "❌", _fmt(a["mid"], d))]
            stop = px - a["stop_dist"] if a["stop_dist"] else None
            out.append(("%s:%s:enter" % (a["sym"], a["bar_date"]),
                        "🟢 <b>GİRİŞ ZAMANI: %s</b>\nGünlük kapanış %s $ (%d/4 analiz onayladı)\n%s\n\nÖnerilen stop: %s $\nÇıkış: 200 günlük altı ya da destek (%s) kırılırsa.\n<i>Sanal bot, yatırım tavsiyesi değil.</i>"
                        % (nm, _fmt(px, d), a["score"], "\n".join(lines), _fmt(stop, d), _fmt(a["support"], d))))
        if a["pending"] == "exit" and a["pos"]:
            out.append(("%s:%s:exit" % (a["sym"], a["bar_date"]),
                        "🔴 <b>ÇIKIŞ ZAMANI: %s</b>\nNeden: %s\nGünlük kapanış %s $, sanal pozisyonun kâr/zararı %+.1f%% (%+.2f $)\n<i>Sanal bot, yatırım tavsiyesi değil.</i>"
                        % (nm, a["reason"], _fmt(px, d), a["pos"]["pnl_pct"], a["pos"]["pnl"])))
        for t in a["trades"][-2:]:
            if t["why"].startswith("stop") and time.time() - t["t_out"] < 2 * 86400:
                out.append(("%s:%s:stop" % (a["sym"], _day(t["t_out"])),
                            "🛑 <b>STOP ÇALIŞTI: %s</b>\nSanal pozisyon %s $ civarında stopa değdi, kâr/zarar %+.1f%% (%+.2f $).\n<i>Sanal bot, yatırım tavsiyesi değil.</i>"
                            % (nm, _fmt(t["px_out"], d), t["pnl_pct"], t["pnl"])))
    return out

def notify_pending(res):
    """06:00–21:00 UTC arasında, henüz gönderilmemiş giriş/çıkış mesajlarını Telegram'a yollar."""
    if not NOTIFY or not NOTIFY_READY():
        return
    h = time.gmtime().tm_hour
    if not (6 <= h < 21):
        return
    sent = _load_sent()
    for key, msg in _messages(res):
        if key in sent:
            continue
        r = NOTIFY(msg)
        if r and r.get("ok"):
            sent[key] = int(time.time())
    try:
        json.dump(sent, open(NOTIFIED, "w"))
    except Exception:
        pass

def get(max_age=1500):
    with _lock:
        if _cache["res"] is None or time.time() - _cache["t"] > max_age:
            _cache["res"] = compute(); _cache["t"] = time.time()
        res = _cache["res"]
    res = dict(res); res["notify_ready"] = bool(NOTIFY_READY())
    # canlı fiyat (her istekte, 30 sn önbellekli) + veri gecikmesi
    try:
        import botlive
    except Exception:
        botlive = None
    yday = _day(time.time() - 86400)
    assets = []
    for a in res["assets"]:
        a = dict(a); a["live"] = None; a["live_time"] = None
        if botlive:
            try:
                ch = botlive.chart(a["sym"], "5m", "1d", ttl=30); a["live"] = ch["price"]; a["live_time"] = ch["price_time"]
            except Exception:
                pass
        a["data_lag"] = a["bar_date"] < yday and not (a["sym"] in ("GC=F", "SI=F", "SPY", "QQQ") and time.gmtime().tm_wday in (0, 5, 6))   # hafta sonu/pazartesi hariç
        assets.append(a)
    res["assets"] = assets
    return res

def start():
    def loop():
        time.sleep(40)
        while True:
            try:
                res = get(max_age=0)
                notify_pending(res)
            except Exception:
                pass
            time.sleep(30 * 60)
    threading.Thread(target=loop, daemon=True).start()
