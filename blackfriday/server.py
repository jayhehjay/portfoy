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
        qs = urllib.parse.parse_qs(p.query)
        g = lambda k, d=None: (qs.get(k) or [d])[0]
        try:
            if p.path == "/api/search":
                q = (g("q") or "").strip()
                if len(q) < 2:
                    return _json(self, {"ok": False, "error": "en az 2 karakter"})
                res = T.search_all(q, strict=g("strict", "1") != "0", accessories=g("accessories", "0") == "1",
                                   force=g("force", "0") == "1")
                return _json(self, res)
            if p.path == "/api/catalog":
                return _json(self, {"ok": True, "catalog": load_catalog()})
            if p.path == "/api/stores":
                return _json(self, {"ok": True, "stores": public_state()["stores"]})
            if p.path == "/api/state":
                return _json(self, public_state())
            if p.path == "/api/history":
                st = T.load_state()
                hid = g("id")
                q = g("q")
                if hid:
                    return _json(self, {"ok": True, "history": st["history"].get(hid)})
                if q:
                    must, exc = T.query_tokens(q)
                    out = {k: v for k, v in st["history"].items()
                           if T.matches_query(v.get("title", ""), must, exc, strict=False)}
                    return _json(self, {"ok": True, "history": out})
                return _json(self, {"ok": True, "history": st["history"]})
            if p.path.startswith("/api/"):
                return _json(self, {"ok": False, "error": "bilinmeyen uç"}, 404)
        except Exception as e:
            return _json(self, {"ok": False, "error": str(e)}, 500)
        if p.path in ("/", ""):
            self.path = "/index.html"
        if p.path in ("/state.json", "/stores.json"):
            return _json(self, {"ok": False, "error": "erişim yok"}, 403)
        return super().do_GET()

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        try:
            body = _body(self)
            if p.path == "/api/watch":
                q = (body.get("q") or "").strip()
                if len(q) < 2:
                    return _json(self, {"ok": False, "error": "arama terimi gerekli"})
                st = T.load_state()
                if len(st["watch"]) >= 60 and not any(w["q"].lower() == q.lower() for w in st["watch"]):
                    return _json(self, {"ok": False, "error": "en fazla 60 takip"})
                mp = body.get("maxPrice")
                mp = float(mp) if mp not in (None, "", 0, "0") else None
                for w in st["watch"]:
                    if w["q"].lower() == q.lower():
                        w["maxPrice"] = mp; w["strict"] = bool(body.get("strict", True))
                        T.save_state(st)
                        return _json(self, {"ok": True, "updated": True, "watch": st["watch"]})
                st["watch"].append({"q": q, "added": int(time.time()), "maxPrice": mp,
                                    "strict": bool(body.get("strict", True))})
                T.save_state(st)
                return _json(self, {"ok": True, "watch": st["watch"]})
            if p.path == "/api/url":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    return _json(self, {"ok": False, "error": "geçerli bir ürün linki girin"})
                st = T.load_state()
                if any(u["url"] == url for u in st["urls"]):
                    return _json(self, {"ok": True, "exists": True, "urls": st["urls"]})
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
                return _json(self, {"ok": True, "product": prod, "urls": st["urls"]})
            if p.path == "/api/run":
                if not _run_lock.acquire(blocking=False):
                    return _json(self, {"ok": False, "error": "tarama zaten sürüyor"})
                try:
                    s = T.run_once(force_notify=bool(body.get("notify", True)))
                finally:
                    _run_lock.release()
                return _json(self, {"ok": True, "summary": s})
            if p.path == "/api/notify/test":
                r = T.notify("Black Friday Tracker", "✅ Test bildirimi — kanal çalışıyor.")
                return _json(self, {"ok": any(x.get("ok") for x in r), "results": r})
            return _json(self, {"ok": False, "error": "bilinmeyen uç"}, 404)
        except Exception as e:
            return _json(self, {"ok": False, "error": str(e)}, 500)

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path)
        try:
            body = _body(self)
            st = T.load_state()
            if p.path == "/api/watch":
                q = (body.get("q") or "").strip().lower()
                st["watch"] = [w for w in st["watch"] if w["q"].lower() != q]
                T.save_state(st)
                return _json(self, {"ok": True, "watch": st["watch"]})
            if p.path == "/api/url":
                url = (body.get("url") or "").strip()
                st["urls"] = [u for u in st["urls"] if u["url"] != url]
                T.save_state(st)
                return _json(self, {"ok": True, "urls": st["urls"]})
            return _json(self, {"ok": False, "error": "bilinmeyen uç"}, 404)
        except Exception as e:
            return _json(self, {"ok": False, "error": str(e)}, 500)

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def main():
    if os.environ.get("BF_NO_SCHEDULER") != "1":
        threading.Thread(target=T.scheduler_loop, daemon=True, name="bf-scheduler").start()
    httpd = Server(("0.0.0.0", PORT), Handler)
    ch = T.channels_configured()
    print("Black Friday Tracker  →  http://localhost:%d" % PORT)
    print("  mağaza: %d  |  tarama aralığı: %g saat  |  Telegram: %s  |  ntfy: %s" % (
        len(T.active_stores()), T.INTERVAL_HOURS, "açık" if ch["telegram"] else "kapalı", "açık" if ch["ntfy"] else "kapalı"))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
