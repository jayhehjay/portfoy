#!/usr/bin/env python3
# Black Friday Tracker — TEK DOSYA sürümü. Bu dosyayı çalıştırın (çift tık ya da: python BlackFridayTracker.py),
# tarayıcı http://localhost:8010 adresinde kendiliğinden açılır. Ek paket gerekmez (Python 3.8+).
# Telegram bildirimi için çalıştırmadan önce TELEGRAM_TOKEN ve TELEGRAM_CHAT_ID ortam değişkenlerini verin
# ya da aşağıdaki iki satırı doldurun:
TELEGRAM_TOKEN = ""      # örn. "123456:ABC-DEF..."
TELEGRAM_CHAT_ID = ""    # örn. "987654321"
import os as _os
if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID: _os.environ.setdefault('TELEGRAM_TOKEN', TELEGRAM_TOKEN); _os.environ.setdefault('TELEGRAM_CHAT_ID', TELEGRAM_CHAT_ID)

#!/usr/bin/env python3
# Black Friday Tracker — İsveç mağazalarında fiyat takibi.
# Sadece Python standart kütüphanesi kullanılır (ek paket yok).
#
#  Ne yapar?
#   • Bir arama terimi ("AirPods Pro") için İsveç mağazalarını tarar (Elgiganten, NetOnNet,
#     Webhallen, Komplett, MediaMarkt, Inet, Power, Kjell, Clas Ohlson, CDON, Amazon.se, Proshop,
#     Dustin), bulunan ürünleri en ucuzdan en pahalıya sıralar.
#   • Takip listesine alınan aramaları her N saatte (varsayılan 6) yeniden tarar, fiyat geçmişini
#     saklar, düşüşleri ve en düşük fiyatları Telegram / ntfy.sh ile bildirir.
#   • Doğrudan ürün linki de takip edilebilir (ürün sayfasındaki schema.org verisinden fiyat okur).
#
#  Komut satırı:
#   python tracker.py --search "airpods pro"      tek seferlik arama, sonucu yazdır
#   python tracker.py --once                      takip listesini tara + bildir (cron / GitHub Actions)
#   python tracker.py --import-watchlist watchlist.json   takip listesini dosyadan yükle (Actions için)
#   python tracker.py --url <ürün linki>          tek ürün sayfasından fiyat oku
#   python tracker.py --test-notify               bildirim kanalını dene
#   python server.py                              web arayüzü + 6 saatlik zamanlayıcı
#
#  Ortam değişkenleri:
#   TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   Telegram bildirimi
#   BF_NTFY_TOPIC                      ntfy.sh konusu (telefona bildirim, bot kurulumu gerekmez)
#   BF_INTERVAL_HOURS                  tarama aralığı (varsayılan 6)
#   BF_NOTIFY                          "always" (her taramada özet) | "changes" (sadece düşüş/yeni dip)
#   BF_STATE_FILE                      durum dosyası yolu (varsayılan blackfriday/state.json)
#   BF_STORES                          sadece bu mağazalar, virgülle ("elgiganten,netonnet")
import urllib.request, urllib.parse, urllib.error, http.cookiejar
import json, os, re, sys, time, html, threading, gzip, zlib, io, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("BF_STATE_FILE") or os.path.join(DIR, "state.json")
INTERVAL_HOURS = float(os.environ.get("BF_INTERVAL_HOURS") or 6)
FETCH_TIMEOUT = 25
MAX_PER_STORE = 12          # mağaza başına en fazla sonuç
HISTORY_KEEP = 400          # ürün başına saklanan fiyat noktası

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}

# ============================ MAĞAZALAR ============================
# Her mağaza: ad, arama URL şablonu ({q} = terim), ana alan adı, isteğe bağlı JSON API.
# "kind": "html" -> sayfa indirilir, JSON-LD / gömülü JSON / HTML sezgisel çıkarıcılar sırayla denenir.
# "kind": "json" -> API çağrılır, dönen JSON'daki ürün nesneleri sezgisel olarak toplanır.
STORES = {
    "elgiganten": {"name": "Elgiganten", "host": "www.elgiganten.se",
                   "search": "https://www.elgiganten.se/search?SearchTerm={q}", "kind": "html"},
    "netonnet":   {"name": "NetOnNet", "host": "www.netonnet.se",
                   "search": "https://www.netonnet.se/Search?query={q}", "kind": "html"},
    "webhallen":  {"name": "Webhallen", "host": "www.webhallen.com",
                   "search": "https://www.webhallen.com/se/search?searchString={q}",
                   "api": "https://www.webhallen.com/api/productdiscovery/search?query={q}&page=1&touchpoint=DESKTOP&totalProductCountSet=false",
                   "kind": "json", "product_url": "https://www.webhallen.com/se/product/{id}"},
    "komplett":   {"name": "Komplett", "host": "www.komplett.se",
                   "search": "https://www.komplett.se/search?q={q}", "kind": "html"},
    "mediamarkt": {"name": "MediaMarkt", "host": "www.mediamarkt.se",
                   "search": "https://www.mediamarkt.se/sv/search.html?query={q}", "kind": "html"},
    "inet":       {"name": "Inet", "host": "www.inet.se",
                   "search": "https://www.inet.se/sok?q={q}", "kind": "html"},
    "power":      {"name": "Power", "host": "www.power.se",
                   "search": "https://www.power.se/search/?q={q}", "kind": "html"},
    "kjell":      {"name": "Kjell & Company", "host": "www.kjell.com",
                   "search": "https://www.kjell.com/se/sok?query={q}", "kind": "html"},
    "clasohlson": {"name": "Clas Ohlson", "host": "www.clasohlson.com",
                   "search": "https://www.clasohlson.com/se/search?text={q}", "kind": "html"},
    "cdon":       {"name": "CDON", "host": "cdon.se",
                   "search": "https://cdon.se/search?q={q}", "kind": "html"},
    "amazon":     {"name": "Amazon.se", "host": "www.amazon.se",
                   "search": "https://www.amazon.se/s?k={q}", "kind": "html"},
    "proshop":    {"name": "Proshop", "host": "www.proshop.se",
                   "search": "https://www.proshop.se/?s={q}", "kind": "html"},
    "dustin":     {"name": "Dustin Home", "host": "www.dustinhome.se",
                   "search": "https://www.dustinhome.se/search?q={q}", "kind": "html"},
    "elon":       {"name": "Elon", "host": "www.elon.se",
                   "search": "https://www.elon.se/sok?q={q}", "kind": "html"},
    "teknikmagasinet": {"name": "Teknikmagasinet", "host": "www.teknikmagasinet.se",
                   "search": "https://www.teknikmagasinet.se/sok?q={q}", "kind": "html"},
    "hifiklubben": {"name": "HiFi Klubben", "host": "www.hifiklubben.se",
                   "search": "https://www.hifiklubben.se/sok/?q={q}", "kind": "html"},
    "computersalg": {"name": "Computersalg", "host": "www.computersalg.se",
                   "search": "https://www.computersalg.se/l/0/{q}", "kind": "html"},
    "apple":      {"name": "Apple Store", "host": "www.apple.com",
                   "search": "https://www.apple.com/se/shop/search?search={q}", "kind": "html"},
    # Fiyat karşılaştırma siteleri: tek bir ürün için pazardaki en düşük fiyatı verir.
    "prisjakt":   {"name": "Prisjakt (en düşük)", "host": "www.prisjakt.nu",
                   "search": "https://www.prisjakt.nu/search?search={q}", "kind": "html", "aggregator": True},
    "pricerunner": {"name": "PriceRunner (en düşük)", "host": "www.pricerunner.se",
                   "search": "https://www.pricerunner.se/results?q={q}", "kind": "html", "aggregator": True},
}

# Kullanıcı kendi mağaza tanımlarını blackfriday/stores.json ile ekleyebilir / değiştirebilir.
def _load_custom_stores():
    p = os.path.join(DIR, "stores.json")
    try:
        with open(p, encoding="utf-8") as f:
            extra = json.load(f)
        for k, v in (extra or {}).items():
            if isinstance(v, dict) and v.get("search"):
                v.setdefault("kind", "html")
                v.setdefault("name", k.title())
                v.setdefault("host", urllib.parse.urlparse(v["search"]).netloc)
                STORES[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        print("stores.json okunamadı:", e, file=sys.stderr)

_load_custom_stores()

def active_stores():
    only = (os.environ.get("BF_STORES") or "").strip()
    if only:
        keys = [k.strip() for k in only.split(",") if k.strip() in STORES]
        return {k: STORES[k] for k in keys}
    return STORES

# ============================ HTTP ============================
_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))

def fetch(url, headers=None, timeout=FETCH_TIMEOUT):
    """URL'yi indir; gzip/deflate açılır, metin olarak döner. Hata -> urllib.error / OSError."""
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with _opener.open(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        elif "deflate" in enc:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS) if raw[:2] != b"\x78\x9c" else zlib.decompress(raw)
        ctype = r.headers.get("Content-Type") or ""
        m = re.search(r"charset=([\w\-]+)", ctype)
        cs = m.group(1) if m else "utf-8"
        try:
            return raw.decode(cs, "replace")
        except LookupError:
            return raw.decode("utf-8", "replace")

# ============================ FİYAT / METİN YARDIMCILARI ============================
_PRICE_RE = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[   .]\d{3})+|\d{2,7})(?:[,.](\d{1,2}))?\s*(?:kr|:-|SEK|kronor)(?![\w])",
    re.I)

def parse_price(v):
    """'12 990 kr', '1 299:-', '1299,00 SEK', 1299, '1299.5' -> float (SEK). Tanınmazsa None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if 0 < v < 10_000_000 else None
    if isinstance(v, dict):
        for k in ("value", "amount", "price", "current", "inclVat", "incVat", "gross"):
            if k in v:
                return parse_price(v[k])
        return None
    s = str(v).strip()
    if not s:
        return None
    m = _PRICE_RE.search(s)
    if m:
        whole = re.sub(r"[   .]", "", m.group(1))
        dec = m.group(2) or "0"
        try:
            return float(whole + "." + dec)
        except ValueError:
            return None
    s2 = s.replace(" ", "").replace(" ", "").replace(" ", "")
    s2 = re.sub(r"(?i)(kr|sek|:-|kronor)", "", s2)
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d{1,2})?", s2):      # 12.990,00
        s2 = s2.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", s2):                      # 1299,50
        s2 = s2.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", s2):                  # 12,990
        s2 = s2.replace(",", "")
    try:
        f = float(s2)
        return f if 0 < f < 10_000_000 else None
    except ValueError:
        return None

def clean_text(s):
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def norm_title(s):
    return re.sub(r"[^a-z0-9åäö]+", " ", (s or "").lower()).strip()

def query_tokens(q):
    """Arama terimini zorunlu ve dışlanan kelimelere ayır: 'airpods pro -fodral'."""
    must, exclude = [], []
    for t in re.split(r"\s+", (q or "").strip()):
        if not t:
            continue
        if t.startswith("-") and len(t) > 1:
            exclude.append(t[1:].lower())
        else:
            must.append(t.lower())
    return must, exclude

# Aksesuar sayılan kelimeler: başlıkta geçerse ürün "aksesuar" olarak işaretlenir (gizlenebilir).
ACCESSORY_WORDS = ("fodral", "skal", "case", "skydd", "hölje", "cover", "sleeve", "strap", "band",
                   "rem", "laddare", "kabel", "cable", "adapter", "hållare", "holder", "mount",
                   "skärmskydd", "glas", "clip", "nyckelring", "tillbehör", "ersättning", "ear tips",
                   "öronkuddar", "earpads", "silikon", "silicone", "stativ", "docka", "handkontroll",
                   "kontroll", "controller", "gamepad", "laddstation", "laddningsfodral", "väska", "bag",
                   "ryggsäck", "dekal", "sticker", "stand", "fäste", "garanti", "försäkring", "presentkort",
                   "abonnemang", "prenumeration", "reservdel", "batteri", "powerbank", "pennspets", "spetsar",
                   "tangentbordsfodral", "väggfäste", "tv bänk", "kabelhållare", "ersättningsband")

def is_accessory(title):
    t = " " + norm_title(title) + " "
    return any((" %s " % w) in t for w in ACCESSORY_WORDS)

# Varyant kelimeleri: "iPhone 17" aranırken "iPhone 17 Pro" gelmesin, "AirPods 4" aranırken "AirPods Pro 3" gelmesin.
VARIANT_WORDS = ("pro", "max", "plus", "ultra", "mini", "lite", "se", "fe", "air", "fold", "flip", "edge",
                 "slim", "digital", "elite", "premium", "xl", "neo", "nano", "fit")

def _tokens(s):
    return norm_title(s).split()

def _has_model_token(must):
    """Sorgu belirli bir modeli işaret ediyor mu? (rakam içeriyor ya da varyant kelimesi var)"""
    return any(re.search(r"\d", w) for w in must) or any(w in VARIANT_WORDS for w in must)

def matches_query(title, must, exclude, strict=True):
    """Başlık, sorgunun TÜM kelimelerini içermeli. Rakamlar / kısa kelimeler / varyant kelimeleri tam
    kelime olarak eşleşir ('4' -> '2024' ile eşleşmez, 'se' -> 'sennheiser' ile eşleşmez).
    strict: sorgu belirli bir model ise (rakam ya da varyant kelimesi içeriyorsa), başlıkta sorguda
    olmayan bir varyant kelimesi (Pro, Max, Ultra...) ya da farklı bir nesil numarası bulunamaz."""
    t = norm_title(title)
    if not t:
        return False
    toks = _tokens(t)
    tokset = set(toks)
    joined = t.replace(" ", "")
    for w in exclude:
        if w in t:
            return False
    qset = set()
    for w in must:
        w2 = re.sub(r"[^a-z0-9åäö]+", "", w)
        if not w2:
            continue
        qset.add(w2)
        exact = bool(re.fullmatch(r"\d+", w2)) or w2 in VARIANT_WORDS or len(w2) <= 2
        if exact:
            if w2 in tokset:
                continue
            # 'airpods4' / 'ps5' gibi yapışık yazım: sayı, bir kelimenin sonuna bitişik olabilir
            if re.fullmatch(r"\d+", w2) and any(tok.endswith(w2) and not tok[:-len(w2)].isdigit() and len(tok) > len(w2)
                                                   for tok in toks):
                continue
            return False
        if w2 not in joined:
            return False
    if strict and _has_model_token(must):
        for v in VARIANT_WORDS:
            if v in tokset and v not in qset:
                return False
        qnums = set(w for w in qset if re.fullmatch(r"\d+", w))
        if qnums:
            # Sorgudaki model adının hemen ardından gelen farklı bir 1-2 haneli sayı = başka nesil
            for i in range(1, len(toks)):
                prev, tok = toks[i - 1], toks[i]
                if re.fullmatch(r"\d{1,2}", tok) and tok not in qnums and prev in qset and not prev.isdigit():
                    return False
    return True

def abs_url(u, store):
    if not u:
        return None
    u = html.unescape(str(u)).strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://%s%s" % (store["host"], u)
    if not u.startswith("http"):
        return "https://%s/%s" % (store["host"], u.lstrip("/"))
    return u

# ============================ ÇIKARICILAR ============================
NAME_KEYS = ("name", "title", "productName", "displayName", "headline", "heading", "label")
PRICE_KEYS = ("price", "currentPrice", "salesPrice", "salePrice", "sellingPrice", "priceIncVat",
              "priceInclVat", "priceWithVat", "nowPrice", "offerPrice", "unitPrice", "displayPrice",
              "lowPrice", "finalPrice", "pris", "currentPriceInclVat", "priceValue", "amount")
OLD_PRICE_KEYS = ("regularPrice", "listPrice", "oldPrice", "originalPrice", "beforePrice",
                  "ordinaryPrice", "ordinariePris", "previousPrice", "wasPrice", "recommendedPrice",
                  "rrp", "msrp", "comparePrice", "strikePrice", "highPrice", "regularPriceInclVat")
URL_KEYS = ("url", "href", "link", "productUrl", "canonicalUrl", "slug", "path", "uri", "permalink")
IMG_KEYS = ("image", "imageUrl", "img", "thumbnail", "images", "primaryImage", "mainImage")
ID_KEYS = ("id", "productId", "sku", "articleNumber", "artNo", "itemId", "code")

def _first(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    for k in d:  # büyük/küçük harf farklı yazımlar
        if k.lower() in [x.lower() for x in keys] and d[k] not in (None, "", [], {}):
            return d[k]
    return None

def _img(v):
    if isinstance(v, list) and v:
        return _img(v[0])
    if isinstance(v, dict):
        return _img(_first(v, ("url", "src", "contentUrl", "large", "medium", "small")))
    return str(v) if isinstance(v, str) else None

def _price_from_dict(d):
    """Bir ürün sözlüğünden (güncel, eski) fiyat çifti çıkar."""
    cur = old = None
    for k in PRICE_KEYS:
        if k in d:
            v = d[k]
            if isinstance(v, dict):
                # {"current": 1299, "regular": 1499} / {"value": .., "oldValue": ..} gibi
                cur = parse_price(_first(v, ("current", "now", "value", "amount", "price", "inclVat",
                                             "incVat", "gross", "sales", "sale", "min")))
                old = parse_price(_first(v, ("regular", "old", "before", "original", "list", "previous",
                                             "ordinary", "was", "oldValue", "regularValue")))
            else:
                cur = parse_price(v)
            if cur:
                break
    if old is None:
        for k in OLD_PRICE_KEYS:
            if k in d:
                old = parse_price(d[k])
                if old:
                    break
    if old is not None and cur is not None and old <= cur:
        old = None
    return cur, old

def _offers_price(offers):
    """schema.org offers -> (fiyat, para birimi, stok)."""
    if isinstance(offers, list):
        best = None
        for o in offers:
            r = _offers_price(o)
            if r[0] is not None and (best is None or r[0] < best[0]):
                best = r
        return best or (None, None, None)
    if not isinstance(offers, dict):
        return (None, None, None)
    price = parse_price(_first(offers, ("price", "lowPrice", "lowprice")))
    if price is None and isinstance(offers.get("priceSpecification"), (dict, list)):
        ps = offers["priceSpecification"]
        ps = ps[0] if isinstance(ps, list) and ps else ps
        if isinstance(ps, dict):
            price = parse_price(ps.get("price"))
    cur = offers.get("priceCurrency") or offers.get("pricecurrency")
    avail = offers.get("availability")
    return (price, cur, avail)

def _walk(obj, fn, depth=0):
    if depth > 40:
        return
    if isinstance(obj, dict):
        fn(obj)
        for v in obj.values():
            _walk(v, fn, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, fn, depth + 1)

def extract_jsonld(page, store):
    """<script type=application/ld+json> içindeki Product / ItemList nesneleri."""
    out = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        txt = m.group(1).strip()
        try:
            data = json.loads(txt)
        except Exception:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", txt))
            except Exception:
                continue
        def on(d):
            t = d.get("@type")
            if isinstance(t, list):
                t = t[0] if t else None
            if t in ("Product", "IndividualProduct", "ProductModel", "ProductGroup"):
                price, cur, avail = _offers_price(d.get("offers"))
                if price is None:
                    return
                if cur and str(cur).upper() not in ("SEK", "KR"):
                    return
                name = clean_text(_first(d, ("name", "title")))
                if not name:
                    return
                url = d.get("url") or (d.get("offers") or {}).get("url") if isinstance(d.get("offers"), dict) else d.get("url")
                item = {"title": name, "price": price, "oldPrice": None,
                        "url": abs_url(url, store) if url else None,
                        "image": _img(d.get("image")), "src": "jsonld",
                        "inStock": None if avail is None else ("InStock" in str(avail))}
                out.append(item)
        _walk(data, on)
    return out

_JSON_BLOB_RES = [
    re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I),
    re.compile(r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>', re.S | re.I),
    re.compile(r'window\.__(?:INITIAL_STATE|PRELOADED_STATE|APOLLO_STATE|NUXT|INITIAL_DATA|STATE)__\s*=\s*(\{.*?\})\s*;?\s*</script>', re.S),
    re.compile(r'window\.__remixContext\s*=\s*(\{.*?\})\s*;?\s*</script>', re.S),
    re.compile(r'<script[^>]+data-component-name[^>]*data-props=["\'](.*?)["\']', re.S | re.I),
]

def extract_embedded_json(page, store):
    """Next.js / Nuxt / Redux benzeri gömülü JSON durumundan ürün benzeri nesneler."""
    blobs = []
    for rx in _JSON_BLOB_RES:
        for m in rx.finditer(page):
            txt = m.group(1).strip()
            if len(txt) < 50:
                continue
            txt2 = html.unescape(txt) if txt.startswith("{&quot;") else txt
            try:
                blobs.append(json.loads(txt2))
            except Exception:
                continue
    out = []
    def on(d):
        name = _first(d, NAME_KEYS)
        if not isinstance(name, str) or len(name) < 4 or len(name) > 200:
            return
        cur, old = _price_from_dict(d)
        if cur is None:
            return
        url = _first(d, URL_KEYS)
        if isinstance(url, dict):
            url = _first(url, ("url", "href", "path"))
        pid = _first(d, ID_KEYS)
        if not url and pid and store.get("product_url"):
            url = store["product_url"].format(id=urllib.parse.quote(str(pid)))
        out.append({"title": clean_text(name), "price": cur, "oldPrice": old,
                    "url": abs_url(url, store) if isinstance(url, str) else None,
                    "image": _img(_first(d, IMG_KEYS)), "src": "json", "inStock": None})
    for b in blobs:
        _walk(b, on)
    return out

_A_RE = re.compile(r'<a\b([^>]*?)href=["\']([^"\'#]+)["\']([^>]*)>(.*?)</a>', re.S | re.I)
_STRIKE_RE = re.compile(
    r'(?:<(?:s|del|strike)\b[^>]*>|class=["\'][^"\']*(?:old|before|regular|ordinarie|strike|was|previous|list-?price)[^"\']*["\'][^>]*>)\s*'
    r'(?:<[^>]+>\s*)*([^<]{1,40})', re.I)

def extract_html(page, store):
    """Son çare: ürün bağlantıları + yakınındaki 'kr' fiyat metni."""
    out, seen = [], set()
    for m in _A_RE.finditer(page):
        href = m.group(2)
        if href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        inner = m.group(4)
        title = clean_text(inner)
        if not title:
            title = clean_text(re.search(r'title=["\']([^"\']+)', m.group(1) + m.group(3)).group(1)) \
                if re.search(r'title=["\']([^"\']+)', m.group(1) + m.group(3)) else ""
        if len(title) < 8 or len(title) > 160:
            continue
        if title.lower() in seen:
            continue
        # Fiyatı bağlantının içinde ya da hemen sonrasındaki ~2500 karakterde ara
        window = inner + page[m.end(): m.end() + 2500]
        window = re.split(r"<a\b", window[len(inner):], maxsplit=1)[0] if len(window) > len(inner) else window
        window = inner + window
        prices = [parse_price(x.group(0)) for x in _PRICE_RE.finditer(clean_text(window))]
        prices = [p for p in prices if p]
        if not prices:
            continue
        old = None
        sm = _STRIKE_RE.search(window)
        if sm:
            old = parse_price(clean_text(sm.group(1)))
        cur = min(prices)
        if old is not None and old <= cur:
            old = None
        seen.add(title.lower())
        out.append({"title": title, "price": cur, "oldPrice": old, "url": abs_url(href, store),
                    "image": None, "src": "html", "inStock": None})
    return out

def extract_api_json(data, store):
    out = []
    def on(d):
        name = _first(d, NAME_KEYS)
        if not isinstance(name, str) or len(name) < 4:
            return
        cur, old = _price_from_dict(d)
        if cur is None:
            return
        url = _first(d, URL_KEYS)
        pid = _first(d, ID_KEYS)
        if not isinstance(url, str) and pid is not None and store.get("product_url"):
            url = store["product_url"].format(id=urllib.parse.quote(str(pid)))
        out.append({"title": clean_text(name), "price": cur, "oldPrice": old,
                    "url": abs_url(url, store) if isinstance(url, str) else None,
                    "image": _img(_first(d, IMG_KEYS)), "src": "api", "inStock": None})
    _walk(data, on)
    return out

def extract_all(page, store):
    """Sırayla dene: JSON-LD -> gömülü JSON -> HTML. İlk anlamlı sonuç kümesi döner (birleştirilir)."""
    items = extract_jsonld(page, store)
    if len(items) < 3:
        items += extract_embedded_json(page, store)
    if len(items) < 3:
        items += extract_html(page, store)
    return items

def page_has_black_friday(page):
    return bool(re.search(r"black\s*friday|black\s*week|black\s*month|cyber\s*monday|singles'?\s*day", page, re.I))

# ============================ ARAMA ============================
def _dedupe(items):
    best = {}
    for it in items:
        key = norm_title(it["title"])[:80]
        if not key:
            continue
        if key not in best or it["price"] < best[key]["price"]:
            if key in best:  # bilgi kaybetme: eski fiyat / url varsa taşı
                for k in ("oldPrice", "url", "image"):
                    it[k] = it.get(k) or best[key].get(k)
            best[key] = it
    return list(best.values())

def search_store(key, q, strict=True, accessories=False):
    store = STORES[key]
    t0 = time.time()
    res = {"store": key, "storeName": store["name"], "ok": False, "items": [], "error": None,
           "blackFriday": False, "ms": 0}
    try:
        qq = urllib.parse.quote_plus(q)
        page = None
        items = []
        if store.get("kind") == "json" and store.get("api"):
            try:
                txt = fetch(store["api"].format(q=qq), headers={"Accept": "application/json"})
                items = extract_api_json(json.loads(txt), store)
            except Exception:
                items = []
        if not items:
            page = fetch(store["search"].format(q=qq))
            items = extract_all(page, store)
            res["blackFriday"] = page_has_black_friday(page)
        must, exclude = query_tokens(q)
        items = [it for it in items if it.get("price") and matches_query(it["title"], must, exclude, strict)]
        items = _dedupe(items)
        for it in items:
            it["store"] = key
            it["storeName"] = store["name"]
            it["aggregator"] = bool(store.get("aggregator"))
            it["accessory"] = is_accessory(it["title"])
        if not accessories:
            items = [it for it in items if not it["accessory"]]
        for it in items:
            if it.get("oldPrice"):
                it["discountPct"] = round((1 - it["price"] / it["oldPrice"]) * 100, 1)
            else:
                it["discountPct"] = None
            it["id"] = hashlib.sha1(("%s|%s" % (key, norm_title(it["title"]))).encode()).hexdigest()[:12]
        items.sort(key=lambda x: x["price"])
        res["items"] = items[:MAX_PER_STORE]
        res["ok"] = True
        if not items:
            res["error"] = "sonuç yok"
    except urllib.error.HTTPError as e:
        res["error"] = "HTTP %s" % e.code
    except Exception as e:
        res["error"] = (str(e) or e.__class__.__name__)[:120]
    res["ms"] = int((time.time() - t0) * 1000)
    return res

_cache = {}
CACHE_TTL = 300   # aynı arama 5 dk içinde tekrarlanırsa mağazalar yeniden sorgulanmaz (force=1 ile aşılır)

def search_all(q, stores=None, workers=10, strict=True, accessories=False, force=False):
    """Tüm mağazaları paralel tara; birleşik listeyi en ucuzdan pahalıya sırala."""
    ck = (q.strip().lower(), strict, accessories, tuple(sorted((stores or active_stores()).keys())))
    now = time.time()
    if not force:
        hit = _cache.get(ck)
        if hit and now - hit["time"] < CACHE_TTL:
            return dict(hit, cached=True)
    keys = list((stores or active_stores()).keys())
    results = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(keys)))) as ex:
        futs = {ex.submit(search_store, k, q, strict, accessories): k for k in keys}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"store": futs[f], "storeName": STORES[futs[f]]["name"], "ok": False,
                                "items": [], "error": str(e)[:120], "blackFriday": False, "ms": 0})
    results.sort(key=lambda r: r["storeName"])
    items = [it for r in results for it in r["items"]]
    items.sort(key=lambda x: (x["price"], x["storeName"]))
    out = {"ok": True, "query": q, "time": int(time.time()), "items": items, "strict": strict,
           "accessories": accessories, "cached": False,
           "stores": [{k: v for k, v in r.items() if k != "items"} | {"count": len(r["items"])} for r in results],
           "cheapest": next((it for it in items if not it.get("aggregator")), items[0] if items else None)}
    _cache[ck] = out
    return out

# ============================ ÜRÜN LİNKİ ============================
def _store_for_url(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    for k, s in STORES.items():
        if host.endswith(s["host"].replace("www.", "")):
            return k, s
    return None, {"name": host, "host": host}

def fetch_product(url):
    """Tek bir ürün sayfasından ad + fiyat oku (JSON-LD öncelikli)."""
    key, store = _store_for_url(url)
    out = {"ok": False, "url": url, "store": key, "storeName": store["name"]}
    try:
        page = fetch(url)
        items = extract_jsonld(page, store)
        if not items:
            items = extract_embedded_json(page, store)
        # Başlık: <title> veya og:title ile en iyi eşleşen
        t = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I)
        title = clean_text(t.group(1)) if t else None
        if not title:
            t = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
            title = clean_text(t.group(1)) if t else None
        if not items and title:
            # og:price / itemprop=price
            m = re.search(r'<meta[^>]+(?:property|itemprop)=["\'](?:product:price:amount|og:price:amount|price)["\'][^>]+content=["\']([^"\']+)', page, re.I)
            if m and parse_price(m.group(1)):
                items = [{"title": title, "price": parse_price(m.group(1)), "oldPrice": None, "url": url,
                          "image": None, "src": "meta", "inStock": None}]
        if not items:
            out["error"] = "fiyat bulunamadı"
            return out
        # Ürün sayfasında genelde tek Product vardır; birden fazlaysa sayfa başlığına en çok benzeyeni seç
        if len(items) > 1 and title:
            tt = set(_tokens(title))
            items.sort(key=lambda x: -len(tt & set(_tokens(x["title"]))))
        it = items[0]
        out.update({"ok": True, "title": it["title"] or title, "price": it["price"], "oldPrice": it.get("oldPrice"),
                    "image": it.get("image"), "blackFriday": page_has_black_friday(page),
                    "inStock": it.get("inStock"), "src": it["src"]})
        return out
    except urllib.error.HTTPError as e:
        out["error"] = "HTTP %s" % e.code
    except Exception as e:
        out["error"] = (str(e) or e.__class__.__name__)[:120]
    return out

# ============================ DURUM (STATE) ============================
_state_lock = threading.RLock()

def _default_state():
    return {"watch": [],        # [{"q": "airpods pro", "added": ts, "maxPrice": None}]
            "urls": [],         # [{"url": ..., "title": ..., "store": ..., "added": ts}]
            "history": {},      # id -> {"title","store","storeName","url","points":[[ts,price],...]}
            "runs": [],         # [{"time", "queries", "sent"}]
            "notifications": [],# [{"time","text","channel","ok"}]
            "lastRun": None, "nextRun": None}

def load_state():
    with _state_lock:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = _default_state()
        for k, v in _default_state().items():
            st.setdefault(k, v)
        return st

def save_state(st):
    with _state_lock:
        tmp = STATE_FILE + ".tmp"
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)

def _record(st, item, ts):
    h = st["history"].setdefault(item["id"], {"title": item["title"], "store": item["store"],
                                              "storeName": item["storeName"], "url": item.get("url"),
                                              "points": []})
    h["title"] = item["title"]
    h["url"] = item.get("url") or h.get("url")
    pts = h["points"]
    prev = pts[-1][1] if pts else None
    low_before = min((p[1] for p in pts), default=None)
    pts.append([ts, item["price"]])
    if len(pts) > HISTORY_KEEP:
        del pts[: len(pts) - HISTORY_KEEP]
    return prev, low_before

# ============================ BİLDİRİM ============================
def tg_config():
    return os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")

def tg_send(text):
    token, chat = tg_config()
    if not token or not chat:
        return {"ok": False, "error": "TELEGRAM_TOKEN / TELEGRAM_CHAT_ID tanımlı değil"}
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=data, headers=UA)
        r = json.load(urllib.request.urlopen(req, timeout=15))
        return {"ok": bool(r.get("ok")), "error": r.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def ntfy_send(title, text):
    topic = os.environ.get("BF_NTFY_TOPIC")
    if not topic:
        return {"ok": False, "error": "BF_NTFY_TOPIC tanımlı değil"}
    try:
        plain = re.sub(r"<[^>]+>", "", text)
        req = urllib.request.Request("https://ntfy.sh/%s" % urllib.parse.quote(topic), data=plain.encode("utf-8"),
                                     headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "shopping_bags",
                                              "User-Agent": UA["User-Agent"]})
        urllib.request.urlopen(req, timeout=15).read()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def notify(title, text, st=None):
    """Tüm yapılandırılmış kanallara gönder; sonucu durum günlüğüne yaz."""
    results = []
    tok, chat = tg_config()
    if tok and chat:
        r = tg_send(text); r["channel"] = "telegram"; results.append(r)
    if os.environ.get("BF_NTFY_TOPIC"):
        r = ntfy_send(title, text); r["channel"] = "ntfy"; results.append(r)
    if not results:
        results.append({"ok": False, "channel": "none", "error": "bildirim kanalı ayarlı değil (TELEGRAM_* veya BF_NTFY_TOPIC)"})
    if st is not None:
        st["notifications"].insert(0, {"time": int(time.time()), "title": title, "text": text,
                                       "channels": results})
        del st["notifications"][30:]
    return results

def fmt_kr(v):
    if v is None:
        return "—"
    s = "%d" % round(v) if abs(v - round(v)) < 0.005 else "%.2f" % v
    whole, _, dec = s.partition(".")
    whole = re.sub(r"(?<=\d)(?=(\d{3})+$)", " ", whole)
    return (whole + ("," + dec if dec else "")) + " kr"

def channels_configured():
    tok, chat = tg_config()
    return {"telegram": bool(tok and chat), "ntfy": bool(os.environ.get("BF_NTFY_TOPIC"))}

# ============================ ZAMANLANMIŞ TARAMA ============================
def run_once(force_notify=False, stores=None):
    """Takip listesindeki tüm aramaları ve ürün linklerini tara, geçmişe yaz, bildir."""
    st = load_state()
    ts = int(time.time())
    mode = (os.environ.get("BF_NOTIFY") or "always").lower()
    summary = {"time": ts, "queries": [], "urls": [], "sent": []}
    msgs = []
    any_change = False

    for w in st["watch"]:
        q = w["q"]
        res = search_all(q, stores=stores, strict=w.get("strict", True), accessories=False, force=True)
        items = res["items"]
        lines, changes = [], []
        for it in items[:6]:
            prev, low_before = _record(st, it, ts)
            tag = ""
            if prev is not None and it["price"] < prev - 0.5:
                tag = " 🔻 %s düştü" % fmt_kr(prev - it["price"])
                changes.append(it)
                any_change = True
            if low_before is not None and it["price"] < low_before - 0.5:
                tag += " 🏷 yeni dip"
            elif prev is None:
                tag += " 🆕"
            disc = (" (−%d%%)" % round(it["discountPct"])) if it.get("discountPct") else ""
            link = ('<a href="%s">%s</a>' % (html.escape(it["url"], quote=True), html.escape(it["storeName"]))
                    if it.get("url") else html.escape(it["storeName"]))
            lines.append("• <b>%s</b>%s — %s — %s%s" % (fmt_kr(it["price"]), disc, link,
                                                        html.escape(it["title"][:70]), tag))
        for it in items[6:]:
            _record(st, it, ts)
        under = None
        if w.get("maxPrice") and items and items[0]["price"] <= float(w["maxPrice"]):
            under = items[0]
            any_change = True
        ok_stores = sum(1 for s in res["stores"] if s["ok"])
        bf = sum(1 for s in res["stores"] if s.get("blackFriday"))
        head = "🛍 <b>%s</b> — %d mağaza tarandı, %d sonuç" % (html.escape(q), ok_stores, len(items))
        if bf:
            head += " · %d mağazada Black Friday kampanyası" % bf
        if under:
            head += "\n🎯 <b>Hedef fiyat altında!</b> %s — %s" % (fmt_kr(under["price"]), html.escape(under["storeName"]))
        body = "\n".join(lines) if lines else "• Eşleşen ürün bulunamadı."
        msgs.append(head + "\n" + body)
        summary["queries"].append({"q": q, "count": len(items), "cheapest": items[0] if items else None,
                                   "changes": len(changes), "stores": res["stores"]})

    for u in st["urls"]:
        p = fetch_product(u["url"])
        if not p.get("ok"):
            summary["urls"].append({"url": u["url"], "ok": False, "error": p.get("error")})
            continue
        u["title"] = p["title"]; u["store"] = p.get("store"); u["storeName"] = p.get("storeName")
        u["last"] = p["price"]; u["lastTime"] = ts
        item = {"id": "url-" + hashlib.sha1(u["url"].encode()).hexdigest()[:12], "title": p["title"],
                "store": p.get("store") or "url", "storeName": p.get("storeName"), "url": u["url"], "price": p["price"]}
        prev, low_before = _record(st, item, ts)
        tag = ""
        if prev is not None and p["price"] < prev - 0.5:
            tag = " 🔻 %s düştü" % fmt_kr(prev - p["price"]); any_change = True
        elif prev is not None and p["price"] > prev + 0.5:
            tag = " 🔺 %s arttı" % fmt_kr(p["price"] - prev)
        if low_before is not None and p["price"] < low_before - 0.5:
            tag += " 🏷 yeni dip"
        if u.get("maxPrice") and p["price"] <= float(u["maxPrice"]):
            tag += " 🎯 hedef altında"; any_change = True
        msgs.append('🔗 <b>%s</b> — <a href="%s">%s</a> — %s%s' % (fmt_kr(p["price"]), html.escape(u["url"], quote=True),
                                                                html.escape(p.get("storeName") or ""), html.escape(p["title"][:70]), tag))
        summary["urls"].append({"url": u["url"], "ok": True, "price": p["price"], "title": p["title"]})

    st["lastRun"] = ts
    st["nextRun"] = ts + int(INTERVAL_HOURS * 3600)
    st["runs"].insert(0, {"time": ts, "queries": len(st["watch"]), "urls": len(st["urls"]), "changes": any_change})
    del st["runs"][50:]

    if msgs and (force_notify or mode == "always" or any_change):
        text = "🖤 <b>Black Friday Tracker</b> · %s\n\n%s\n\n<i>Sonraki tarama: %d saat sonra</i>" % (
            time.strftime("%d.%m %H:%M"), "\n\n".join(msgs), int(INTERVAL_HOURS))
        summary["sent"] = notify("Black Friday Tracker", text, st)
    save_state(st)
    return summary

def scheduler_loop(stop_event=None):
    """Arka plan döngüsü: ilk taramayı 1 dk sonra, sonra her INTERVAL_HOURS saatte bir yapar."""
    st = load_state()
    nxt = st.get("nextRun") or (time.time() + 60)
    if nxt < time.time():
        nxt = time.time() + 60
    while not (stop_event and stop_event.is_set()):
        wait = max(5, nxt - time.time())
        if stop_event:
            if stop_event.wait(min(wait, 60)):
                break
            if time.time() < nxt:
                continue
        else:
            time.sleep(min(wait, 60))
            if time.time() < nxt:
                continue
        try:
            run_once()
        except Exception as e:
            print("tarama hatası:", e, file=sys.stderr)
        nxt = time.time() + INTERVAL_HOURS * 3600
        st = load_state(); st["nextRun"] = int(nxt); save_state(st)

# ============================ KOMUT SATIRI ============================
def _main(argv):
    if "--search" in argv:
        q = argv[argv.index("--search") + 1]
        res = search_all(q)
        for s in res["stores"]:
            print("%-16s %s %s (%d ms)" % (s["storeName"], "✓" if s["ok"] else "✗", s.get("error") or "%d sonuç" % s["count"], s["ms"]))
        print()
        for it in res["items"]:
            disc = (" (−%d%%)" % round(it["discountPct"])) if it.get("discountPct") else ""
            print("%12s%-8s %-16s %s" % (fmt_kr(it["price"]), disc, it["storeName"], it["title"][:80]))
            if it.get("url"):
                print("%37s%s" % ("", it["url"]))
        return 0
    if "--url" in argv:
        print(json.dumps(fetch_product(argv[argv.index("--url") + 1]), ensure_ascii=False, indent=1))
        return 0
    if "--once" in argv:
        s = run_once(force_notify="--notify" in argv)
        print(json.dumps(s, ensure_ascii=False, indent=1, default=str))
        return 0
    if "--import-watchlist" in argv:
        # GitHub Actions / cron: watchlist.json içindeki aramaları durum dosyasına ekle (geçmişi korur)
        path = argv[argv.index("--import-watchlist") + 1]
        if not os.path.exists(path):
            print("watchlist yok, atlandı:", path); return 0
        with open(path, encoding="utf-8") as f:
            wl = json.load(f)
        st = load_state()
        want = {(w.get("q") or "").strip().lower(): w for w in wl.get("watch", []) if w.get("q")}
        st["watch"] = [dict(w, maxPrice=want[w["q"].lower()].get("maxPrice", w.get("maxPrice")))
                       for w in st["watch"] if w["q"].lower() in want]
        have = {w["q"].lower() for w in st["watch"]}
        for k, w in want.items():
            if k not in have:
                st["watch"].append({"q": w["q"].strip(), "added": int(time.time()), "maxPrice": w.get("maxPrice"),
                                    "strict": w.get("strict", True)})
        urls = [u for u in wl.get("urls", []) if (u.get("url") or "").startswith("http") and "..." not in u["url"]]
        keep = {u["url"] for u in urls}
        st["urls"] = [u for u in st["urls"] if u["url"] in keep]
        have = {u["url"] for u in st["urls"]}
        for u in urls:
            if u["url"] not in have:
                st["urls"].append({"url": u["url"], "added": int(time.time()), "maxPrice": u.get("maxPrice")})
        save_state(st)
        print("takip: %d arama, %d link" % (len(st["watch"]), len(st["urls"])))
        return 0
    if "--test-notify" in argv:
        print(notify("Black Friday Tracker", "✅ Test bildirimi — kanal çalışıyor."))
        return 0
    print(__doc__ or "kullanım: --search <terim> | --once [--notify] | --url <link> | --test-notify")
    return 1


import sys as _sys; _T = _sys.modules[__name__]
EMBED_INDEX = "<!doctype html>\n<html lang=\"tr\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Black Friday Tracker · İsveç</title>\n<style>\n:root{\n  --bg:#0c0d10;--panel:#15171c;--panel2:#1c1f26;--line:#262a33;--text:#e8e9ee;--muted:#8b91a0;\n  --accent:#ffb000;--accent2:#ff5c35;--good:#38d27a;--bad:#ff6b6b;--link:#7cc4ff;--chip:#23272f;\n  --radius:12px;--font:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Inter,sans-serif;\n}\n*{box-sizing:border-box}\nhtml,body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 var(--font)}\na{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}\nbutton{font:inherit;cursor:pointer;border:0;border-radius:9px;padding:9px 14px;background:var(--chip);color:var(--text)}\nbutton:hover{filter:brightness(1.15)}button:disabled{opacity:.5;cursor:default;filter:none}\nbutton.primary{background:var(--accent);color:#1a1200;font-weight:700}\nbutton.danger{background:transparent;color:var(--bad);padding:4px 8px}\ninput[type=text],input[type=number],input[type=url],select{font:inherit;background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:9px;padding:10px 12px;outline:0}\ninput:focus,select:focus{border-color:var(--accent)}\n.wrap{max-width:1180px;margin:0 auto;padding:16px}\nheader{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:6px 0 18px}\nheader h1{font-size:22px;margin:0;letter-spacing:-.2px}\nheader h1 span{color:var(--accent)}\n.status{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;font-size:12.5px;color:var(--muted)}\n.pill{background:var(--chip);border-radius:999px;padding:4px 10px}\n.pill.on{color:var(--good)}.pill.off{color:var(--muted)}\n.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin-bottom:16px}\n.card h2{margin:0 0 12px;font-size:16px}\n.searchrow{display:flex;gap:8px;flex-wrap:wrap}\n.searchrow input[type=text]{flex:1;min-width:220px;font-size:16px}\n.opts{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;font-size:13px;color:var(--muted);align-items:center}\n.opts label{display:flex;gap:6px;align-items:center;cursor:pointer}\n.cats{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}\n.cat{background:var(--chip);border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer;color:var(--muted)}\n.cat.active,.cat:hover{color:var(--text);background:#2c313c}\n.prods{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;max-height:112px;overflow:auto}\n.prod{border:1px solid var(--line);border-radius:8px;padding:5px 10px;font-size:13px;cursor:pointer;background:var(--panel2)}\n.prod:hover{border-color:var(--accent)}\ntable{width:100%;border-collapse:collapse;font-size:14px}\nth,td{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}\nth{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px}\ntd.price{font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}\ntd.rank{color:var(--muted);width:34px}\ntr.best td.price{color:var(--accent)}\n.badge{display:inline-block;font-size:11px;padding:2px 7px;border-radius:6px;margin-left:6px;vertical-align:middle;font-weight:600}\n.badge.bf{background:#2a1d00;color:var(--accent)}\n.badge.disc{background:#0f2a1a;color:var(--good)}\n.badge.agg{background:#1c2430;color:var(--link)}\n.badge.acc{background:#2a1a1a;color:var(--bad)}\n.badge.new{background:#23272f;color:var(--muted)}\n.store{color:var(--muted);font-size:13px;white-space:nowrap}\n.stores{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}\n.st{font-size:12px;padding:3px 9px;border-radius:999px;background:var(--chip);color:var(--muted)}\n.st.ok{color:var(--good)}.st.fail{color:var(--bad)}\n.muted{color:var(--muted)}.small{font-size:12.5px}\n.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}\n@media(max-width:860px){.grid{grid-template-columns:1fr}}\n.watch{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}\n.watch:last-child{border-bottom:0}\n.watch .q{font-weight:600;cursor:pointer}\n.watch .best{margin-left:auto;text-align:right;white-space:nowrap}\n.watch .best b{color:var(--accent)}\n.spark{display:block}\n.notif{padding:10px 0;border-bottom:1px solid var(--line);font-size:13px;white-space:pre-wrap}\n.notif:last-child{border-bottom:0}\n.notif .t{color:var(--muted);font-size:12px}\n.empty{color:var(--muted);padding:18px 0;text-align:center}\n.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--muted);border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}\n@keyframes sp{to{transform:rotate(360deg)}}\n.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#23272f;border:1px solid var(--line);padding:10px 16px;border-radius:10px;font-size:14px;opacity:0;transition:.25s;pointer-events:none}\n.toast.show{opacity:1}\n.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}\ndetails summary{cursor:pointer;color:var(--muted);font-size:13px}\n.prodtitle{max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n@media(max-width:700px){.prodtitle{max-width:200px;white-space:normal}th:nth-child(1),td.rank{display:none}}\n</style>\n</head>\n<body>\n<div class=\"wrap\">\n<header>\n  <h1>🖤 Black Friday <span>Tracker</span> <span class=\"muted small\" style=\"font-weight:400\">· İsveç mağazaları</span></h1>\n  <div class=\"status\" id=\"status\"></div>\n</header>\n\n<div class=\"card\">\n  <h2>Ürün ara</h2>\n  <div class=\"searchrow\">\n    <input type=\"text\" id=\"q\" list=\"suggest\" placeholder=\"Örn: airpods 4, iphone 17 pro, playstation 5 slim … (dışlamak için: -ord)\" autocomplete=\"off\">\n    <datalist id=\"suggest\"></datalist>\n    <button class=\"primary\" id=\"go\">Fiyatları getir</button>\n    <button id=\"refresh\" title=\"Önbelleği atla, tüm mağazaları yeniden sorgula\">↻ Güncelle</button>\n  </div>\n  <div class=\"opts\">\n    <label><input type=\"checkbox\" id=\"strict\" checked> Sadece bu model (Pro/Max/Ultra ve farklı nesiller gizlenir)</label>\n    <label><input type=\"checkbox\" id=\"acc\"> Aksesuarları da göster (fodral, kabel, tillbehör…)</label>\n    <span id=\"resinfo\"></span>\n  </div>\n  <div class=\"cats\" id=\"cats\"></div>\n  <div class=\"prods\" id=\"prods\"></div>\n</div>\n\n<div class=\"card\" id=\"rescard\" style=\"display:none\">\n  <div class=\"row\" style=\"justify-content:space-between\">\n    <h2 id=\"restitle\" style=\"margin:0\"></h2>\n    <div class=\"row\">\n      <input type=\"number\" id=\"target\" placeholder=\"hedef fiyat (kr)\" style=\"width:150px\">\n      <button class=\"primary\" id=\"watchbtn\">＋ Takibe al (her <span id=\"ivh\">6</span> saatte bir)</button>\n    </div>\n  </div>\n  <div class=\"stores\" id=\"storestat\"></div>\n  <div style=\"overflow:auto;margin-top:12px\">\n  <table>\n    <thead><tr><th>#</th><th>Fiyat</th><th>Mağaza</th><th>Ürün</th><th></th></tr></thead>\n    <tbody id=\"rows\"></tbody>\n  </table>\n  </div>\n</div>\n\n<div class=\"grid\">\n  <div class=\"card\">\n    <div class=\"row\" style=\"justify-content:space-between;margin-bottom:10px\">\n      <h2 style=\"margin:0\">Takip listesi</h2>\n      <button id=\"runnow\">▶ Şimdi tara ve bildir</button>\n    </div>\n    <div id=\"watchlist\"><div class=\"empty\">Henüz takip yok. Bir ürün arayıp “Takibe al”a basın.</div></div>\n    <details style=\"margin-top:12px\">\n      <summary>Ürün linki ile takip et (belirli bir mağazadaki tek ürün)</summary>\n      <div class=\"row\" style=\"margin-top:8px\">\n        <input type=\"url\" id=\"purl\" placeholder=\"https://www.elgiganten.se/product/…\" style=\"flex:1;min-width:200px\">\n        <input type=\"number\" id=\"ptarget\" placeholder=\"hedef (kr)\" style=\"width:120px\">\n        <button id=\"addurl\">Ekle</button>\n      </div>\n      <div id=\"urllist\"></div>\n    </details>\n  </div>\n  <div class=\"card\">\n    <div class=\"row\" style=\"justify-content:space-between;margin-bottom:10px\">\n      <h2 style=\"margin:0\">Bildirimler</h2>\n      <div class=\"row\"><button id=\"tgtest\" class=\"small\">Kanalı test et</button><button id=\"browsernotif\" class=\"small\">🔔 Tarayıcı bildirimi</button></div>\n    </div>\n    <div id=\"chinfo\" class=\"small muted\" style=\"margin-bottom:8px\"></div>\n    <div id=\"notifs\"><div class=\"empty\">Henüz bildirim gönderilmedi.</div></div>\n  </div>\n</div>\n\n<div class=\"card small muted\">\n  Fiyatlar mağazaların arama sayfalarından anlık okunur; sıralama en ucuzdan en pahalıya. “Prisjakt / PriceRunner” satırları pazardaki en düşük fiyatı gösterir.\n  Takipteki aramalar sunucu açık kaldığı sürece her <span id=\"ivh2\">6</span> saatte bir yeniden taranır; düşüşler 🔻, tüm zamanların en düşüğü 🏷 ile işaretlenir.\n</div>\n</div>\n<div class=\"toast\" id=\"toast\"></div>\n\n<script>\nconst $=s=>document.querySelector(s);\nconst fmt=v=>v==null?'—':(Math.round(v)===v?Math.round(v):v.toFixed(2)).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g,' ').replace('.',',')+' kr';\nconst ago=ts=>{if(!ts)return'—';const d=Date.now()/1000-ts;if(d<90)return'az önce';if(d<3600)return Math.round(d/60)+' dk önce';if(d<86400)return Math.round(d/3600)+' sa önce';return Math.round(d/86400)+' gün önce'};\nconst until=ts=>{if(!ts)return'—';const d=ts-Date.now()/1000;if(d<=0)return'şimdi';if(d<3600)return Math.round(d/60)+' dk sonra';return (d/3600).toFixed(1)+' sa sonra'};\nconst esc=s=>(s||'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\nlet toastT;function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('show'),2800)}\nconst BASE=location.pathname.endsWith('/')?location.pathname.slice(0,-1):location.pathname.replace(/\\/[^/]*$/,'');\nasync function api(path,method='GET',body){const r=await fetch(BASE+path,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}\n\nlet catalog={},state=null,lastQuery='';\n\nasync function loadCatalog(){\n  const r=await api('/api/catalog');catalog=r.catalog||{};\n  const dl=$('#suggest');dl.innerHTML='';\n  const cats=$('#cats');cats.innerHTML='';\n  Object.keys(catalog).forEach((c,i)=>{\n    const el=document.createElement('span');el.className='cat'+(i===0?' active':'');el.textContent=c;\n    el.onclick=()=>{document.querySelectorAll('.cat').forEach(x=>x.classList.remove('active'));el.classList.add('active');showProds(c)};\n    cats.appendChild(el);\n    catalog[c].forEach(p=>{const o=document.createElement('option');o.value=p.q;o.label=p.name;dl.appendChild(o)});\n  });\n  const first=Object.keys(catalog)[0];if(first)showProds(first);\n}\nfunction showProds(c){\n  const box=$('#prods');box.innerHTML='';\n  (catalog[c]||[]).forEach(p=>{const el=document.createElement('span');el.className='prod';el.textContent=p.name;el.title=p.q;\n    el.onclick=()=>{$('#q').value=p.q;search(false)};box.appendChild(el)});\n}\n\nasync function search(force){\n  const q=$('#q').value.trim();if(q.length<2){toast('En az 2 karakter girin');return}\n  lastQuery=q;\n  $('#go').disabled=true;$('#refresh').disabled=true;\n  $('#resinfo').innerHTML='<span class=\"spinner\"></span>'+esc(q)+' için mağazalar taranıyor…';\n  try{\n    const r=await api(`/api/search?q=${encodeURIComponent(q)}&strict=${$('#strict').checked?1:0}&accessories=${$('#acc').checked?1:0}&force=${force?1:0}`);\n    if(!r.ok){toast(r.error||'hata');$('#resinfo').textContent='';return}\n    render(r);\n  }catch(e){toast('Sunucuya ulaşılamadı');$('#resinfo').textContent=''}\n  finally{$('#go').disabled=false;$('#refresh').disabled=false}\n}\n\nfunction render(r){\n  $('#rescard').style.display='';\n  const ok=r.stores.filter(s=>s.ok).length,bf=r.stores.filter(s=>s.blackFriday).length;\n  $('#restitle').innerHTML=`“${esc(r.query)}” — ${r.items.length} sonuç <span class=\"muted small\">(${ok}/${r.stores.length} mağaza yanıt verdi${r.cached?', önbellek':''} · ${new Date(r.time*1000).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})})</span>`;\n  $('#resinfo').textContent=bf?`${bf} mağazada Black Friday kampanyası görülüyor`:'';\n  const ss=$('#storestat');ss.innerHTML='';\n  r.stores.forEach(s=>{const el=document.createElement('span');el.className='st '+(s.ok&&s.count?'ok':(s.ok?'':'fail'));\n    el.textContent=`${s.storeName}: ${s.ok?(s.count?s.count+' ürün':'eşleşme yok'):(s.error||'hata')}`;el.title=`${s.ms} ms`;ss.appendChild(el)});\n  const tb=$('#rows');tb.innerHTML='';\n  if(!r.items.length){tb.innerHTML='<tr><td colspan=\"5\" class=\"empty\">Eşleşen ürün bulunamadı. “Sadece bu model” seçeneğini kapatmayı ya da daha kısa bir terim denemeyi deneyin.</td></tr>';return}\n  let best=true;\n  r.items.forEach((it,i)=>{\n    const tr=document.createElement('tr');if(!it.aggregator&&best){tr.className='best';best=false}\n    const badges=[];\n    if(it.discountPct)badges.push(`<span class=\"badge disc\">−${Math.round(it.discountPct)}% <s class=\"muted\">${fmt(it.oldPrice)}</s></span>`);\n    if(it.aggregator)badges.push('<span class=\"badge agg\">en düşük pazar fiyatı</span>');\n    if(it.accessory)badges.push('<span class=\"badge acc\">aksesuar</span>');\n    const bfs=r.stores.find(s=>s.store===it.store);if(bfs&&bfs.blackFriday)badges.push('<span class=\"badge bf\">Black Friday</span>');\n    tr.innerHTML=`<td class=\"rank\">${i+1}</td><td class=\"price\">${fmt(it.price)}</td><td class=\"store\">${esc(it.storeName)}</td>\n      <td><div class=\"prodtitle\">${it.url?`<a href=\"${esc(it.url)}\" target=\"_blank\" rel=\"noopener\">${esc(it.title)}</a>`:esc(it.title)}${badges.join('')}</div></td>\n      <td>${it.url?`<a href=\"${esc(it.url)}\" target=\"_blank\" rel=\"noopener\" class=\"small\">mağazaya git ↗</a>`:''}</td>`;\n    tb.appendChild(tr);\n  });\n}\n\nfunction spark(points){\n  if(!points||points.length<2)return'';\n  const w=90,h=22,ps=points.map(p=>p[1]),mn=Math.min(...ps),mx=Math.max(...ps);\n  const xy=ps.map((p,i)=>[(i/(ps.length-1))*w,h-((mx===mn?.5:(p-mn)/(mx-mn))*(h-4))-2]);\n  return `<svg class=\"spark\" width=\"${w}\" height=\"${h}\"><polyline fill=\"none\" stroke=\"#ffb000\" stroke-width=\"1.5\" points=\"${xy.map(a=>a.join(',')).join(' ')}\"/></svg>`;\n}\n\nasync function loadState(){\n  state=await api('/api/state');\n  $('#ivh').textContent=state.intervalHours;$('#ivh2').textContent=state.intervalHours;\n  const ch=state.channels;\n  $('#status').innerHTML=`<span class=\"pill\">${state.stores.length} mağaza</span>\n    <span class=\"pill ${ch.telegram?'on':'off'}\">Telegram ${ch.telegram?'açık':'kapalı'}</span>\n    <span class=\"pill ${ch.ntfy?'on':'off'}\">ntfy ${ch.ntfy?'açık':'kapalı'}</span>\n    <span class=\"pill\">son tarama: ${ago(state.lastRun)}</span><span class=\"pill\">sonraki: ${until(state.nextRun)}</span>`;\n  $('#chinfo').innerHTML=(ch.telegram||ch.ntfy)?`Kanal: ${[ch.telegram?'Telegram':'',ch.ntfy?'ntfy.sh':''].filter(Boolean).join(' + ')} · mod: ${state.notifyMode==='changes'?'sadece düşüş / yeni dip':'her taramada özet'}`\n    :'Bildirim kanalı ayarlı değil. Sunucuyu <code>TELEGRAM_TOKEN</code> + <code>TELEGRAM_CHAT_ID</code> veya <code>BF_NTFY_TOPIC</code> ortam değişkenleriyle başlatın (bkz. README).';\n  // takip listesi\n  const wl=$('#watchlist');\n  if(!state.watch.length){wl.innerHTML='<div class=\"empty\">Henüz takip yok. Bir ürün arayıp “Takibe al”a basın.</div>'}\n  else{\n    wl.innerHTML='';\n    const hist=await api('/api/history');\n    for(const w of state.watch){\n      const el=document.createElement('div');el.className='watch';\n      // en ucuz ürünün geçmişi (sparkline için)\n      let pts=null;if(w.best){const m=Object.values(hist.history||{}).find(h=>h.title===w.best.title&&h.storeName===w.best.storeName);pts=m&&m.points}\n      el.innerHTML=`<span class=\"q\" title=\"Sonuçları göster\">${esc(w.q)}</span>${w.maxPrice?`<span class=\"badge new\">hedef ≤ ${fmt(w.maxPrice)}</span>`:''}${w.strict===false?'<span class=\"badge new\">geniş eşleşme</span>':''}\n        ${spark(pts)}\n        <span class=\"best small\">${w.best?`<b>${fmt(w.best.price)}</b> · ${esc(w.best.storeName)}<br><span class=\"muted\">${esc((w.best.title||'').slice(0,48))} · dip ${fmt(w.best.low)} · ${ago(w.lastSeen)}</span>`:'<span class=\"muted\">henüz taranmadı</span>'}</span>\n        <button class=\"danger\" title=\"Takibi kaldır\">✕</button>`;\n      el.querySelector('.q').onclick=()=>{$('#q').value=w.q;$('#strict').checked=w.strict!==false;search(false);window.scrollTo({top:0,behavior:'smooth'})};\n      el.querySelector('button').onclick=async()=>{await api('/api/watch','DELETE',{q:w.q});toast('Takip kaldırıldı');loadState()};\n      wl.appendChild(el);\n    }\n  }\n  const ul=$('#urllist');ul.innerHTML='';\n  state.urls.forEach(u=>{const el=document.createElement('div');el.className='watch';\n    el.innerHTML=`<a href=\"${esc(u.url)}\" target=\"_blank\" rel=\"noopener\" class=\"small\">${esc(u.title||u.url)}</a><span class=\"muted small\">${esc(u.storeName||'')}</span>\n      <span class=\"best small\">${u.last?`<b>${fmt(u.last)}</b> · ${ago(u.lastTime)}`:`<span class=\"muted\">${esc(u.error||'okunamadı')}</span>`}</span><button class=\"danger\">✕</button>`;\n    el.querySelector('button').onclick=async()=>{await api('/api/url','DELETE',{url:u.url});loadState()};ul.appendChild(el)});\n  const nl=$('#notifs');\n  if(!state.notifications.length)nl.innerHTML='<div class=\"empty\">Henüz bildirim gönderilmedi.</div>';\n  else{nl.innerHTML='';state.notifications.forEach(n=>{const el=document.createElement('div');el.className='notif';\n    const okc=(n.channels||[]).map(c=>`${c.channel}: ${c.ok?'✓':'✗ '+(c.error||'')}`).join(', ');\n    el.innerHTML=`<div class=\"t\">${new Date(n.time*1000).toLocaleString('tr-TR')} · ${esc(okc)}</div>${n.text.replace(/<a href=\"([^\"]+)\">/g,'<a href=\"$1\" target=\"_blank\" rel=\"noopener\">')}`;nl.appendChild(el)})}\n}\n\n$('#go').onclick=()=>search(false);\n$('#refresh').onclick=()=>search(true);\n$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')search(false)});\n$('#strict').onchange=()=>{if(lastQuery)search(false)};\n$('#acc').onchange=()=>{if(lastQuery)search(false)};\n$('#watchbtn').onclick=async()=>{\n  if(!lastQuery)return;const mp=$('#target').value;\n  const r=await api('/api/watch','POST',{q:lastQuery,maxPrice:mp?parseFloat(mp):null,strict:$('#strict').checked});\n  toast(r.ok?(r.updated?'Takip güncellendi':'Takibe alındı — her '+(state?state.intervalHours:6)+' saatte bir taranacak'):(r.error||'hata'));loadState();\n};\n$('#runnow').onclick=async()=>{const b=$('#runnow');b.disabled=true;b.innerHTML='<span class=\"spinner\"></span>taranıyor…';\n  try{const r=await api('/api/run','POST',{notify:true});if(!r.ok)toast(r.error||'hata');else{toast('Tarama bitti');browserNotify(r.summary)}}finally{b.disabled=false;b.textContent='▶ Şimdi tara ve bildir';loadState()}};\n$('#addurl').onclick=async()=>{const u=$('#purl').value.trim();if(!u)return;const b=$('#addurl');b.disabled=true;\n  const r=await api('/api/url','POST',{url:u,maxPrice:$('#ptarget').value||null});b.disabled=false;\n  toast(r.ok?(r.product&&r.product.ok?`Eklendi: ${fmt(r.product.price)}`:'Eklendi (fiyat şimdilik okunamadı)'):(r.error||'hata'));$('#purl').value='';loadState()};\n$('#tgtest').onclick=async()=>{const r=await api('/api/notify/test','POST',{});toast(r.ok?'Test bildirimi gönderildi':'Gönderilemedi: '+((r.results||[]).map(x=>x.error).filter(Boolean).join('; ')||'kanal yok'));loadState()};\n$('#browsernotif').onclick=async()=>{if(!('Notification' in window)){toast('Tarayıcı desteklemiyor');return}const p=await Notification.requestPermission();toast(p==='granted'?'Tarayıcı bildirimleri açık':'İzin verilmedi')};\nfunction browserNotify(s){\n  if(!('Notification' in window)||Notification.permission!=='granted'||!s)return;\n  (s.queries||[]).forEach(q=>{if(q.cheapest)new Notification(`${q.q}: ${fmt(q.cheapest.price)} · ${q.cheapest.storeName}`,{body:q.cheapest.title})});\n}\nloadCatalog();loadState();setInterval(loadState,60000);\n</script>\n</body>\n</html>\n"
EMBED_CATALOG = {"Apple": [{"name": "AirPods 4", "q": "airpods 4"}, {"name": "AirPods 4 (ANC)", "q": "airpods 4 anc"}, {"name": "AirPods Pro 3", "q": "airpods pro 3"}, {"name": "AirPods Pro 2", "q": "airpods pro 2"}, {"name": "AirPods Max", "q": "airpods max"}, {"name": "iPhone 17", "q": "iphone 17"}, {"name": "iPhone 17 Pro", "q": "iphone 17 pro"}, {"name": "iPhone 17 Pro Max", "q": "iphone 17 pro max"}, {"name": "iPhone Air", "q": "iphone air"}, {"name": "iPhone 16", "q": "iphone 16"}, {"name": "iPhone 16e", "q": "iphone 16e"}, {"name": "iPad (A16)", "q": "ipad a16"}, {"name": "iPad Air", "q": "ipad air"}, {"name": "iPad Pro", "q": "ipad pro"}, {"name": "iPad mini", "q": "ipad mini"}, {"name": "MacBook Air M4", "q": "macbook air m4"}, {"name": "MacBook Pro M5", "q": "macbook pro m5"}, {"name": "Mac mini M4", "q": "mac mini m4"}, {"name": "Apple Watch Series 11", "q": "apple watch series 11"}, {"name": "Apple Watch SE", "q": "apple watch se"}, {"name": "Apple Watch Ultra 3", "q": "apple watch ultra 3"}, {"name": "Apple TV 4K", "q": "apple tv 4k"}, {"name": "HomePod mini", "q": "homepod mini"}, {"name": "AirTag", "q": "airtag"}], "Samsung": [{"name": "Galaxy S25", "q": "galaxy s25"}, {"name": "Galaxy S25 Ultra", "q": "galaxy s25 ultra"}, {"name": "Galaxy S25 FE", "q": "galaxy s25 fe"}, {"name": "Galaxy Z Fold7", "q": "galaxy z fold7"}, {"name": "Galaxy Z Flip7", "q": "galaxy z flip7"}, {"name": "Galaxy A56", "q": "galaxy a56"}, {"name": "Galaxy Tab S10", "q": "galaxy tab s10"}, {"name": "Galaxy Watch 8", "q": "galaxy watch 8"}, {"name": "Galaxy Buds 3 Pro", "q": "galaxy buds 3 pro"}], "Kulaklık & Ses": [{"name": "Sony WH-1000XM6", "q": "sony wh-1000xm6"}, {"name": "Sony WH-1000XM5", "q": "sony wh-1000xm5"}, {"name": "Sony WF-1000XM5", "q": "sony wf-1000xm5"}, {"name": "Bose QuietComfort Ultra", "q": "bose quietcomfort ultra"}, {"name": "Sennheiser Momentum 4", "q": "sennheiser momentum 4"}, {"name": "JBL Tune 770NC", "q": "jbl tune 770nc"}, {"name": "Sonos Era 100", "q": "sonos era 100"}, {"name": "Sonos Arc Ultra", "q": "sonos arc ultra"}, {"name": "Marshall Emberton III", "q": "marshall emberton iii"}, {"name": "JBL Flip 7", "q": "jbl flip 7"}], "Oyun": [{"name": "PlayStation 5 Slim", "q": "playstation 5 slim"}, {"name": "PlayStation 5 Pro", "q": "playstation 5 pro"}, {"name": "PlayStation Portal", "q": "playstation portal"}, {"name": "Nintendo Switch 2", "q": "nintendo switch 2"}, {"name": "Xbox Series X", "q": "xbox series x"}, {"name": "Xbox Series S", "q": "xbox series s"}, {"name": "Steam Deck OLED", "q": "steam deck oled"}, {"name": "Meta Quest 3S", "q": "meta quest 3s"}, {"name": "Logitech G Pro X Superlight 2", "q": "logitech g pro x superlight 2"}], "TV": [{"name": "LG OLED C5 55\"", "q": "lg oled55c5"}, {"name": "LG OLED C5 65\"", "q": "lg oled65c5"}, {"name": "LG OLED G5 65\"", "q": "lg oled65g5"}, {"name": "Samsung S90F 65\"", "q": "samsung s90f 65"}, {"name": "Samsung QN90F 65\"", "q": "samsung qn90f 65"}, {"name": "Sony Bravia 8 II", "q": "sony bravia 8 ii"}, {"name": "Philips OLED 810 55\"", "q": "philips 55oled810"}, {"name": "Samsung The Frame 55\"", "q": "samsung the frame 55"}], "Bilgisayar": [{"name": "Lenovo Legion 5", "q": "lenovo legion 5"}, {"name": "ASUS ROG Zephyrus G14", "q": "rog zephyrus g14"}, {"name": "Dell XPS 13", "q": "dell xps 13"}, {"name": "Surface Laptop 7", "q": "surface laptop 7"}, {"name": "RTX 5070", "q": "rtx 5070"}, {"name": "RTX 5080", "q": "rtx 5080"}, {"name": "Samsung 990 Pro 2TB", "q": "samsung 990 pro 2tb"}, {"name": "LG UltraGear 27GS95QE", "q": "lg 27gs95qe"}, {"name": "Logitech MX Master 3S", "q": "logitech mx master 3s"}, {"name": "Kindle Paperwhite", "q": "kindle paperwhite"}], "Ev & Mutfak": [{"name": "Dyson V15 Detect", "q": "dyson v15 detect"}, {"name": "Dyson Airwrap", "q": "dyson airwrap"}, {"name": "Dyson Supersonic", "q": "dyson supersonic"}, {"name": "iRobot Roomba Combo j9+", "q": "roomba combo j9"}, {"name": "Roborock S8 MaxV Ultra", "q": "roborock s8 maxv ultra"}, {"name": "Ninja Foodi Max", "q": "ninja foodi max"}, {"name": "Philips Airfryer XXL", "q": "philips airfryer xxl"}, {"name": "Nespresso Vertuo Pop", "q": "nespresso vertuo pop"}, {"name": "De'Longhi Magnifica S", "q": "delonghi magnifica s"}, {"name": "KitchenAid Artisan", "q": "kitchenaid artisan"}, {"name": "Oral-B iO 9", "q": "oral-b io 9"}, {"name": "Philips Hue Starter Kit", "q": "philips hue starter kit"}], "Kamera & Spor": [{"name": "GoPro Hero 13", "q": "gopro hero 13"}, {"name": "DJI Osmo Pocket 3", "q": "dji osmo pocket 3"}, {"name": "DJI Mini 4 Pro", "q": "dji mini 4 pro"}, {"name": "Garmin Forerunner 265", "q": "garmin forerunner 265"}, {"name": "Garmin Fenix 8", "q": "garmin fenix 8"}, {"name": "Sony A7 IV", "q": "sony a7 iv"}, {"name": "Instax Mini 12", "q": "instax mini 12"}]}
import http.server, socketserver, urllib.parse, json, os, sys, threading, time


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
    st = _T.load_state()
    hist = st.get("history") or {}
    def summary(q):
        # Bu aramaya ait son tarama noktalarından en ucuzunu bul
        best, last_ts = None, 0
        for h in hist.values():
            pts = h.get("points") or []
            if not pts:
                continue
            must, exc = _T.query_tokens(q)
            if not _T.matches_query(h.get("title", ""), must, exc, strict=False):
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
            "nextRun": st.get("nextRun"), "intervalHours": _T.INTERVAL_HOURS, "runs": st["runs"][:10],
            "notifications": st["notifications"][:15], "channels": _T.channels_configured(),
            "notifyMode": (os.environ.get("BF_NOTIFY") or "always"), "historyCount": len(hist),
            "stores": [{"key": k, "name": v["name"], "host": v["host"], "aggregator": bool(v.get("aggregator"))}
                       for k, v in _T.active_stores().items()]}

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
                _json(h, _T.search_all(q, strict=g("strict", "1") != "0", accessories=g("accessories", "0") == "1",
                                      force=g("force", "0") == "1")); return True
            if path == "/api/catalog":
                _json(h, {"ok": True, "catalog": load_catalog()}); return True
            if path == "/api/stores":
                _json(h, {"ok": True, "stores": public_state()["stores"]}); return True
            if path == "/api/state":
                _json(h, public_state()); return True
            if path == "/api/history":
                st = _T.load_state()
                hid, q = g("id"), g("q")
                if hid:
                    _json(h, {"ok": True, "history": st["history"].get(hid)}); return True
                if q:
                    must, exc = _T.query_tokens(q)
                    out = {k: v for k, v in st["history"].items()
                           if _T.matches_query(v.get("title", ""), must, exc, strict=False)}
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
                st = _T.load_state()
                if len(st["watch"]) >= 60 and not any(w["q"].lower() == q.lower() for w in st["watch"]):
                    _json(h, {"ok": False, "error": "en fazla 60 takip"}); return True
                mp = body.get("maxPrice")
                mp = float(mp) if mp not in (None, "", 0, "0") else None
                for w in st["watch"]:
                    if w["q"].lower() == q.lower():
                        w["maxPrice"] = mp; w["strict"] = bool(body.get("strict", True))
                        _T.save_state(st)
                        _json(h, {"ok": True, "updated": True, "watch": st["watch"]}); return True
                st["watch"].append({"q": q, "added": int(time.time()), "maxPrice": mp,
                                    "strict": bool(body.get("strict", True))})
                _T.save_state(st)
                _json(h, {"ok": True, "watch": st["watch"]}); return True
            if path == "/api/url":
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    _json(h, {"ok": False, "error": "geçerli bir ürün linki girin"}); return True
                st = _T.load_state()
                if any(u["url"] == url for u in st["urls"]):
                    _json(h, {"ok": True, "exists": True, "urls": st["urls"]}); return True
                prod = _T.fetch_product(url)
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
                    _T._record(st, item, int(time.time()))
                _T.save_state(st)
                _json(h, {"ok": True, "product": prod, "urls": st["urls"]}); return True
            if path == "/api/run":
                if not _run_lock.acquire(blocking=False):
                    _json(h, {"ok": False, "error": "tarama zaten sürüyor"}); return True
                try:
                    s = _T.run_once(force_notify=bool(body.get("notify", True)))
                finally:
                    _run_lock.release()
                _json(h, {"ok": True, "summary": s}); return True
            if path == "/api/notify/test":
                r = _T.notify("Black Friday Tracker", "✅ Test bildirimi — kanal çalışıyor.")
                _json(h, {"ok": any(x.get("ok") for x in r), "results": r}); return True
            _json(h, {"ok": False, "error": "bilinmeyen uç"}, 404); return True
        if method == "DELETE":
            st = _T.load_state()
            if path == "/api/watch":
                q = (body.get("q") or "").strip().lower()
                st["watch"] = [w for w in st["watch"] if w["q"].lower() != q]
                _T.save_state(st)
                _json(h, {"ok": True, "watch": st["watch"]}); return True
            if path == "/api/url":
                url = (body.get("url") or "").strip()
                st["urls"] = [u for u in st["urls"] if u["url"] != url]
                _T.save_state(st)
                _json(h, {"ok": True, "urls": st["urls"]}); return True
            _json(h, {"ok": False, "error": "bilinmeyen uç"}, 404); return True
    except Exception as e:
        _json(h, {"ok": False, "error": str(e)}, 500); return True
    return False

def start_scheduler():
    """Arka plandaki 6 saatlik tarama döngüsünü başlat (Portföy sunucusu da çağırır)."""
    if os.environ.get("BF_NO_SCHEDULER") == "1":
        return None
    th = threading.Thread(target=_T.scheduler_loop, daemon=True, name="bf-scheduler")
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
    ch = _T.channels_configured()
    print("Black Friday Tracker  →  http://localhost:%d" % PORT)
    print("  mağaza: %d  |  tarama aralığı: %g saat  |  Telegram: %s  |  ntfy: %s" % (
        len(_T.active_stores()), _T.INTERVAL_HOURS, "açık" if ch["telegram"] else "kapalı", "açık" if ch["ntfy"] else "kapalı"))
    if os.environ.get("BF_OPEN_BROWSER", "1") != "0" and "EMBED_INDEX" in globals():
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:%d" % PORT)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
