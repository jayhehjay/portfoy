#!/usr/bin/env python3
# Çevrimdışı testler: çıkarıcılar, eşleşme kuralları, durum/tarama/bildirim akışı ve HTTP API.
# Çalıştır:  python -m unittest discover -s blackfriday/tests -v
import os, sys, json, unittest, threading, http.server, socketserver, tempfile, urllib.request, functools
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["BF_STATE_FILE"] = os.path.join(tempfile.mkdtemp(), "state.json")
os.environ["BF_INTERVAL_HOURS"] = "6"
import tracker as T
FIX = os.path.join(HERE, "fixtures")

def read(n):
    with open(os.path.join(FIX, n), encoding="utf-8") as f:
        return f.read()

class Extractors(unittest.TestCase):
    store = {"name": "Test", "host": "shop.example.se"}
    def test_jsonld(self):
        items = T.extract_jsonld(read("jsonld.html"), self.store)
        self.assertEqual(len(items), 4)
        d = {i["title"]: i for i in items}
        self.assertEqual(d["Apple AirPods 4"]["price"], 1490)
        self.assertEqual(d["Apple AirPods 4"]["url"], "https://shop.example.se/product/airpods-4/123")
        self.assertEqual(d["Apple AirPods 4 med aktiv brusreducering"]["price"], 1990)  # lowPrice
        self.assertTrue(d["Apple AirPods 4"]["inStock"])
    def test_embedded_json(self):
        items = T.extract_embedded_json(read("nextdata.html"), self.store)
        d = {i["title"]: i for i in items}
        self.assertEqual(d["Apple AirPods 4 (2024)"]["price"], 1399)
        self.assertEqual(d["Apple AirPods 4 (2024)"]["oldPrice"], 1590)
        self.assertEqual(d["Apple AirPods 4 (2024)"]["url"], "https://shop.example.se/produkt/555-apple-airpods-4")
        self.assertEqual(d["Apple AirPods 4 (2024)"]["image"], "https://img/555.jpg")
    def test_html(self):
        items = T.extract_html(read("plain.html"), self.store)
        d = {i["title"]: i for i in items}
        self.assertEqual(d["Apple AirPods 4 – Vit"]["price"], 1349)
        self.assertEqual(d["Apple AirPods 4 – Vit"]["oldPrice"], 1590)
        self.assertEqual(d["Apple AirPods Pro 2 (USB‑C)"]["price"], 2495)
        self.assertEqual(d["Apple AirPods 4 med ANC"]["price"], 1799)
        self.assertNotIn("Kundservice och frågor", d)   # 0 kr -> fiyat değil
    def test_api_json(self):
        store = dict(self.store, product_url="https://shop.example.se/p/{id}")
        items = T.extract_api_json(json.loads(read("api.json")), store)
        d = {i["title"]: i for i in items}
        self.assertEqual(d["Apple AirPods 4"]["price"], 1390)
        self.assertEqual(d["Apple AirPods 4"]["oldPrice"], 1590)
        self.assertEqual(d["Apple AirPods 4"]["url"], "https://shop.example.se/p/364000")

class Matching(unittest.TestCase):
    def m(self, q, title, strict=True):
        must, exc = T.query_tokens(q)
        return T.matches_query(title, must, exc, strict)
    def test_specific_model(self):
        self.assertTrue(self.m("airpods 4", "Apple AirPods 4 med aktiv brusreducering"))
        self.assertFalse(self.m("airpods 4", "Apple AirPods Pro 2"))
        self.assertFalse(self.m("airpods 4", "Apple AirPods Max"))
        self.assertFalse(self.m("airpods 4", "Apple AirPods 2024 (3:e gen)"))
        self.assertFalse(self.m("iphone 17", "Apple iPhone 17 Pro Max"))
        self.assertTrue(self.m("iphone 17 pro max", "Apple iPhone 17 Pro Max 256GB"))
        self.assertFalse(self.m("galaxy s25", "Samsung Galaxy S25 Ultra"))
        self.assertTrue(self.m("ps5", "Sony PS5 Slim", strict=False))
    def test_generic(self):
        self.assertTrue(self.m("airpods", "Apple AirPods Pro 3"))
        self.assertTrue(self.m("airpods", "Apple AirPods Max"))
    def test_exclude(self):
        self.assertFalse(self.m("airpods 4 -anc", "Apple AirPods 4 ANC"))
    def test_accessory(self):
        self.assertTrue(T.is_accessory("Fodral till AirPods 4"))
        self.assertTrue(T.is_accessory("Rem till AirPods 4 – Svart"))
        self.assertTrue(T.is_accessory("Sony DualSense handkontroll"))
        self.assertFalse(T.is_accessory("Apple AirPods 4"))
    def test_price(self):
        for v, exp in [("12 990 kr", 12990), ("1 299:-", 1299), ("1299,00 SEK", 1299), (1299, 1299),
                       ("12.990,00 kr", 12990), ("Pris: 2 495 kr", 2495), ("0", None), ("abc", None)]:
            self.assertEqual(T.parse_price(v), exp, v)
        self.assertEqual(T.fmt_kr(12990), "12 990 kr")

class _Fix(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = self.path.split("?")[0]
        name = {"/jsonld": "jsonld.html", "/next": "nextdata.html", "/plain": "plain.html", "/api": "api.json",
                "/product": "jsonld.html"}.get(path)
        if not name:
            self.send_response(404); self.end_headers(); return
        data = read(name).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json" if name.endswith(".json") else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), _Fix); cls.srv.allow_reuse_address = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        port = cls.srv.server_address[1]
        base = "http://127.0.0.1:%d" % port
        cls.stores = {
            "s_ld":   {"name": "LD Shop", "host": "127.0.0.1:%d" % port, "search": base + "/jsonld?q={q}", "kind": "html"},
            "s_next": {"name": "Next Shop", "host": "127.0.0.1:%d" % port, "search": base + "/next?q={q}", "kind": "html"},
            "s_html": {"name": "Plain Shop", "host": "127.0.0.1:%d" % port, "search": base + "/plain?q={q}", "kind": "html"},
            "s_api":  {"name": "API Shop", "host": "127.0.0.1:%d" % port, "search": base + "/plain?q={q}",
                       "api": base + "/api?q={q}", "kind": "json", "product_url": base + "/p/{id}"},
            "s_dead": {"name": "Dead Shop", "host": "127.0.0.1:%d" % port, "search": base + "/nope?q={q}", "kind": "html"},
        }
        T.STORES.clear(); T.STORES.update(cls.stores)
        cls.base = base
        # web sunucusu
        import server as S
        cls.S = S
        cls.web = S.Server(("127.0.0.1", 0), S.Handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        cls.api = "http://127.0.0.1:%d" % cls.web.server_address[1]
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.web.shutdown()
    def call(self, path, method="GET", body=None):
        req = urllib.request.Request(self.api + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=10))
    def test_search_sorted_specific_no_accessories(self):
        res = T.search_all("airpods 4", force=True)
        titles = [(i["price"], i["storeName"], i["title"]) for i in res["items"]]
        prices = [t[0] for t in titles]
        self.assertEqual(prices, sorted(prices))
        self.assertTrue(all("pro" not in t[2].lower() and "max" not in t[2].lower() for t in titles), titles)
        self.assertFalse(any(T.is_accessory(t[2]) for t in titles), titles)
        self.assertEqual(res["cheapest"]["price"], 1349)   # Plain Shop 1 349 kr
        dead = next(s for s in res["stores"] if s["store"] == "s_dead")
        self.assertFalse(dead["ok"]); self.assertIn("HTTP 404", dead["error"])
        ld = next(s for s in res["stores"] if s["store"] == "s_ld")
        self.assertTrue(ld["blackFriday"])
        self.assertEqual(next(s for s in res["stores"] if s["store"] == "s_api")["count"], 2)
    def test_search_accessories_toggle_and_cache(self):
        a = T.search_all("airpods 4", accessories=True, force=True)
        self.assertTrue(any(i["accessory"] for i in a["items"]))
        b = T.search_all("airpods 4", accessories=True)
        self.assertTrue(b["cached"])
    def test_api_flow(self):
        r = self.call("/api/search?q=airpods%204&force=1")
        self.assertTrue(r["ok"]); self.assertGreater(len(r["items"]), 3)
        r = self.call("/api/watch", "POST", {"q": "airpods 4", "maxPrice": 1400})
        self.assertTrue(r["ok"]); self.assertEqual(len(r["watch"]), 1)
        r = self.call("/api/url", "POST", {"url": self.base + "/product"})
        self.assertTrue(r["ok"]); self.assertTrue(r["product"]["ok"]); self.assertEqual(r["product"]["price"], 1490)
        r = self.call("/api/run", "POST", {"notify": True})
        self.assertTrue(r["ok"])
        s = r["summary"]
        self.assertEqual(s["queries"][0]["cheapest"]["price"], 1349)
        self.assertEqual(s["sent"][0]["channel"], "none")       # kanal ayarlı değil -> günlükte hata
        st = self.call("/api/state")
        self.assertEqual(st["watch"][0]["best"]["price"], 1349)
        self.assertEqual(len(st["notifications"]), 1)
        txt = st["notifications"][0]["text"]
        self.assertIn("Hedef fiyat altında", txt); self.assertIn("1 349 kr", txt); self.assertIn("🆕", txt)
        h = self.call("/api/history?q=airpods%204")
        self.assertGreaterEqual(len(h["history"]), 4)
        # ikinci tarama: değişiklik yok, fiyat etiketleri düşüş içermemeli
        r2 = self.call("/api/run", "POST", {"notify": True})
        self.assertNotIn("düştü", self.call("/api/state")["notifications"][0]["text"])
        r = self.call("/api/watch", "DELETE", {"q": "airpods 4"}); self.assertEqual(r["watch"], [])
        r = self.call("/api/url", "DELETE", {"url": self.base + "/product"}); self.assertEqual(r["urls"], [])
        cat = self.call("/api/catalog"); self.assertGreater(sum(len(v) for v in cat["catalog"].values()), 50)
        code = urllib.request.urlopen(self.api + "/").status; self.assertEqual(code, 200)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.api + "/state.json")

if __name__ == "__main__":
    unittest.main()
