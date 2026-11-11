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

if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
