#!/usr/bin/env python3
# Black Friday Tracker — web arayüzü + API + 6 saatlik zamanlayıcı.
# Çalıştır:  python server.py          (http://localhost:8010)
#  /api/search?q=airpods+4[&strict=0][&accessories=1][&force=1]   canlı tarama (en ucuz -> en pahalı)
#  /api/catalog                      hazır ürün seçenekleri (products.json)
#  /api/stores                       mağaza listesi
#  /api/state                        takip listesi, son/sonraki tarama, bildirim günlüğü, kanallar
#  /api/history?id=...               bir ürünün fiyat geçmişi
#  /api/watch     POST {q, maxPrice?}  / DELETE {q}       arama takibi ekle/sil
#  /api/url       POST {url, maxPrice?} / DELETE {url}   ürün linki takibi ekle/sil
#  /api/run       POST                 takip listesini şimdi tara ve bildir
#  /api/notify/test POST               bildirim kanalını test et
import http.server, socketserver, urllib.parse, json, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker as T

PORT = int(os.environ.get("PORT") or 8010)
DIR = os.path.dirname(os.path.abspath(__file__))

def load_catalog():
    if "EMBED_CATALOG" in globals():          # tek dosyalık sürüm (BlackFridayTracker.py)
        return globals()["EMBED_CATALOG"]
    try:
        with open(os.path.join(DIR, "products.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _json(h, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store, max-age=0")
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(body)

def _body(h):
    n = int(h.headers.get("Content-Length") or 0)
    return json.loads(h.rfile.read(n).decode("utf-8") or "{}") if n else {}

def public_state():
    st = T.load_state()
    hist = st.get("history") or {}
    def summary(q):
        # Bu aramaya ait son tarama noktalarından en ucuzunu bul
        best, last_ts = None, 0
        for h in hist.values():
            pts = h.get("points") or []
            if not pts:
                continue
            must, exc = T.query_tokens(q)
            if not T.matches_query(h.get("title", ""), must, exc, strict=False):
                continue
            ts, price = pts[-1]
            last_ts = max(last_ts, ts)
            if best is None or price < best["price"]:
                best = {"price": price, "title": h.get("title"), "storeName": h.get("storeName"), "url": h.get("url"),
                        "low": min(p[1] for p in pts), "n": len(pts)}
        return best, last_ts
    watch = []
    for w in st["watch"]:
        b, ts = summary(w["q"])
        watch.append(dict(w, best=b, lastSeen=ts or None))
    return {"ok": True, "watch": watch, "urls": st["urls"], "lastRun": st.get("lastRun"),
            "nextRun": st.get("nextRun"), "intervalHours": T.INTERVAL_HOURS, "runs": st["runs"][:10],
            "notifications": st["notifications"][:15], "channels": T.channels_configured(),
            "notifyMode": (os.environ.get("BF_NOTIFY") or "always"), "historyCount": len(hist),
            "stores": [{"key": k, "name": v["name"], "host": v["host"], "aggregator": bool(v.get("aggregator"))}
                       for k, v in T.active_stores().items()]}

_run_lock = threading.Lock()

def _send_index(h):
    if "EMBED_INDEX" in globals():          # tek dosyalık sürüm
        body = globals()["EMBED_INDEX"].encode("utf-8")
    else:
        with open(os.path.join(DIR, "index.html"), "rb") as f:
            body = f.read()
    h.send_response(200)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store, max-age=0")
    h.end_headers()
    h.wfile.write(body)

def route(h, method, path, qs=None, body=None):
    """Tek giriş noktası. Portföy sunucusu da /blackfriday altında buraya yönlendirir.
    path: önek atılmış yol ("/api/search", "/" ...). Dönüş: True = yanıtlandı."""
    qs = qs or {}
    g = lambda k, d=None: (qs.get(k) or [d])[0]
    body = body or {}
    try:
        if method == "GET":
            if path in ("/", "", "/index.html"):
                _send_index(h); return True
            if path == "/api/search":
                q = (g("q") or "").strip()
                if len(q) < 2:
                    _json(h, {"ok": False, "error": "en az 2 karakter"}); return True
                _json(h, T.search_all(q, strict=g("strict", "1") != "0", accessories=g("accessories", "0") == "1",
                                      force=g("force", "0") == "1")); return True
            if path == "/api/catalog":
                _json(h, {"ok": True, "catalog": load_catalog()}); return True
            if path == "/api/stores":
                _json(h, {"ok": True, "stores": public_state()["stores"]}); return True
            if path == "/api/state":
                _json(h, public_state()); return True
            if path == "/api/history":
                st = T.load_state()
                hid, q = g("id"), g("q")
                if hid:
                    _json(h, {"ok": True, "history": st["history"].get(hid)}); return True
                if q:
                    must, exc = T.query_tokens(q)
                    out = {k: v for k, v in st["history"].items()
                           if T.matches_query(v.get("title", ""), must, exc, strict=False)}
                    _json(h, {"ok": True, "history": out}); return True
                _json(h, {"ok": True, "history": st["history"]}); return True
            if path.startswith("/api/"):
                _json(h, {"ok": False, "error": "bilinmeyen uç"}, 404); return True
            return False
        if method == "POST":
            if path == "/api/watch":
                q = (body.get("q") or "").strip()
                if len(q) < 2:
                    _json(h, {"ok": False, "error": "arama terimi gerekli"}); return True
                st = T.load_state()
                if len(st["watch"]) >= 60 and not any(w["q"].lower() == q.lower() for w in st["watch"]):
                    _json(h, {"ok": False, "error": "en fazla 60 takip"}); return True
                mp = body.get("maxPrice")
                mp = float(mp) if mp not in (None, "", 0, "0") else None
                for w in st["watch"]:
                    if w["q"].lower() == q.lower():
                        w["maxPrice"] = mp; w["strict"] = bool(body.get("strict", True))
                        T.save_state(st)
                        _json(h, {"ok": True, "updated": True, "watch": st["watch"]}); return True
                st["watch"].append({"q": q, "added": int(time.time()), "maxPrice": mp,
                                    "strict": bool(body.get("strict", True))})
                T.save_state(st)
                _json(h, {"ok": True, "watch": st["watch"]}); return True
            if path == "/api/url":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    _json(h, {"ok": False, "error": "geçerli bir ürün linki girin"}); return True
                st = T.load_state()
                if any(u["url"] == url for u in st["urls"]):
                    _json(h, {"ok": True, "exists": True, "urls": st["urls"]}); return True
                prod = T.fetch_product(url)
                mp = body.get("maxPrice")
                entry = {"url": url, "added": int(time.time()), "maxPrice": float(mp) if mp not in (None, "", 0, "0") else None,
                         "title": prod.get("title"), "store": prod.get("store"), "storeName": prod.get("storeName"),
                         "last": prod.get("price"), "lastTime": int(time.time()) if prod.get("ok") else None,
                         "error": None if prod.get("ok") else prod.get("error")}
                st["urls"].append(entry)
                if prod.get("ok"):
                    import hashlib
                    item = {"id": "url-" + hashlib.sha1(url.encode()).hexdigest()[:12], "title": prod["title"],
                            "store": prod.get("store") or "url", "storeName": prod.get("storeName"), "url": url,
                            "price": prod["price"]}
                    T._record(st, item, int(time.time()))
                T.save_state(st)
                _json(h, {"ok": True, "product": prod, "urls": st["urls"]}); return True
            if path == "/api/run":
                if not _run_lock.acquire(blocking=False):
                    _json(h, {"ok": False, "error": "tarama zaten sürüyor"}); return True
                try:
                    s = T.run_once(force_notify=bool(body.get("notify", True)))
                finally:
                    _run_lock.release()
                _json(h, {"ok": True, "summary": s}); return True
            if path == "/api/notify/test":
                r = T.notify("Black Friday Tracker", "✅ Test bildirimi — kanal çalışıyor.")
                _json(h, {"ok": any(x.get("ok") for x in r), "results": r}); return True
            _json(h, {"ok": False, "error": "bilinmeyen uç"}, 404); return True
        if method == "DELETE":
            st = T.load_state()
            if path == "/api/watch":
                q = (body.get("q") or "").strip().lower()
                st["watch"] = [w for w in st["watch"] if w["q"].lower() != q]
                T.save_state(st)
                _json(h, {"ok": True, "watch": st["watch"]}); return True
            if path == "/api/url":
                url = (body.get("url") or "").strip()
                st["urls"] = [u for u in st["urls"] if u["url"] != url]
                T.save_state(st)
                _json(h, {"ok": True, "urls": st["urls"]}); return True
            _json(h, {"ok": False, "error": "bilinmeyen uç"}, 404); return True
    except Exception as e:
        _json(h, {"ok": False, "error": str(e)}, 500); return True
    return False

def start_scheduler():
    """Arka plandaki 6 saatlik tarama döngüsünü başlat (Portföy sunucusu da çağırır)."""
    if os.environ.get("BF_NO_SCHEDULER") == "1":
        return None
    th = threading.Thread(target=T.scheduler_loop, daemon=True, name="bf-scheduler")
    th.start()
    return th

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/state.json", "/stores.json", "/watchlist.json"):
            return _json(self, {"ok": False, "error": "erişim yok"}, 403)
        if route(self, "GET", p.path, urllib.parse.parse_qs(p.query)):
            return
        return super().do_GET()

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        try:
            body = _body(self)
        except Exception as e:
            return _json(self, {"ok": False, "error": "geçersiz JSON: %s" % e}, 400)
        route(self, "POST", p.path, None, body)

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path)
        try:
            body = _body(self)
        except Exception as e:
            return _json(self, {"ok": False, "error": "geçersiz JSON: %s" % e}, 400)
        route(self, "DELETE", p.path, None, body)

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def main():
    start_scheduler()
    httpd = Server(("0.0.0.0", PORT), Handler)
    ch = T.channels_configured()
    print("Black Friday Tracker  →  http://localhost:%d" % PORT)
    print("  mağaza: %d  |  tarama aralığı: %g saat  |  Telegram: %s  |  ntfy: %s" % (
        len(T.active_stores()), T.INTERVAL_HOURS, "açık" if ch["telegram"] else "kapalı", "açık" if ch["ntfy"] else "kapalı"))
    if os.environ.get("BF_OPEN_BROWSER", "1") != "0" and "EMBED_INDEX" in globals():
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:%d" % PORT)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
