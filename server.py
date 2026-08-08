#!/usr/bin/env python3
# Portföy — yerel aracı sunucu (Yahoo Finance proxy).
# Yahoo tarayıcıdan direkt çağrılamıyor (CORS yok); bu sunucu araya girer.
#  /api/q     -> anlık fiyat, değişim, gün/52h aralığı  (chart)
#  /api/f     -> forward F/K, serbest nakit akışı, net borç/FAVÖK  (quoteSummary, crumb)
#  /api/news  -> şirkete özel son haberler  (RSS)
import http.server, socketserver, urllib.request, urllib.parse, urllib.error
import http.cookiejar, json, os, functools, re, threading, time

PORT = 8000
DIR = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_crumb = None
_lock = threading.Lock()

def _open(url):
    return _opener.open(urllib.request.Request(url, headers=UA), timeout=12)

def get_crumb(force=False):
    global _crumb
    with _lock:
        if _crumb and not force:
            return _crumb
        try:
            try:
                _open("https://fc.yahoo.com")
            except Exception:
                pass
            c = _open("https://query1.finance.yahoo.com/v1/test/getcrumb").read().decode("utf-8", "ignore")
            _crumb = c if (c and "<" not in c) else None
        except Exception:
            _crumb = None
        return _crumb

def _raw(x):
    return x.get("raw") if isinstance(x, dict) else x

def fetch_quote(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1d" % urllib.parse.quote(sym)
    m = json.load(_open(url))["chart"]["result"][0]["meta"]
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    change = (price - prev) if (price is not None and prev) else None
    pct = (change / prev * 100) if (change is not None and prev) else None
    return {"ok": True, "symbol": sym, "price": price, "prev": prev, "change": change, "pct": pct,
            "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
            "w52h": m.get("fiftyTwoWeekHigh"), "w52l": m.get("fiftyTwoWeekLow"),
            "currency": m.get("currency"), "exchange": m.get("exchangeName"),
            "name": m.get("longName"), "time": m.get("regularMarketTime")}

def _quotesummary(sym, crumb):
    mods = "defaultKeyStatistics,financialData,summaryDetail,price,calendarEvents"
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s"
           % (urllib.parse.quote(sym), mods, urllib.parse.quote(crumb)))
    return json.load(_open(url))["quoteSummary"]["result"][0]

def fetch_fund(sym):
    crumb = get_crumb()
    if not crumb:
        return {"ok": False, "error": "crumb yok"}
    try:
        r = _quotesummary(sym, crumb)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            crumb = get_crumb(force=True)
            if not crumb:
                return {"ok": False, "error": "crumb yenilenemedi"}
            r = _quotesummary(sym, crumb)
        else:
            raise
    fd = r.get("financialData", {}) or {}
    ks = r.get("defaultKeyStatistics", {}) or {}
    sd = r.get("summaryDetail", {}) or {}
    pr = r.get("price", {}) or {}
    ce = r.get("calendarEvents", {}) or {}

    def _day(v):
        v = _raw(v)
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(v)) if v else None
        except Exception:
            return None
    _ed = ((ce.get("earnings") or {}).get("earningsDate")) or []
    next_earn = _day(_ed[0]) if _ed else None
    next_div = _day(ce.get("dividendDate"))
    ex_div = _day(ce.get("exDividendDate")) or _day(sd.get("exDividendDate"))
    trade_ccy = pr.get("currency")
    fin_ccy = fd.get("financialCurrency") or pr.get("financialCurrency")
    def clean_pe(v):
        return v if (v is not None and 0 < v <= 150) else None
    tpe = clean_pe(_raw(sd.get("trailingPE")))
    fpe = clean_pe(_raw(sd.get("forwardPE")) or _raw(ks.get("forwardPE")))
    cross_ccy = bool(fin_ccy and trade_ccy and fin_ccy != trade_ccy)
    # Çapraz para birimi: forward EPS raporlama ccy'sinde -> forward F/K güvenilmez, gizle
    if cross_ccy:
        fpe = None
    fcf = _raw(fd.get("freeCashflow"))
    ebitda = _raw(fd.get("ebitda"))
    td = _raw(fd.get("totalDebt")); tc = _raw(fd.get("totalCash"))
    nd = (td - tc) if (td is not None and tc is not None) else None
    de = (nd / ebitda) if (nd is not None and ebitda) else None
    return {"ok": True, "forwardPE": fpe, "trailingPE": tpe, "fcf": fcf, "ebitda": ebitda,
            "crossCcy": cross_ccy, "finCcy": fin_ccy, "tradeCcy": trade_ccy,
            "netDebt": nd, "debtEbitda": de,
            "marketCap": _raw(pr.get("marketCap")) or _raw(sd.get("marketCap")),
            "dividendYield": _raw(sd.get("dividendYield")),
            "revenueGrowth": _raw(fd.get("revenueGrowth")),
            "earningsGrowth": _raw(fd.get("earningsGrowth")),
            "profitMargin": _raw(fd.get("profitMargins")),
            "grossMargin": _raw(fd.get("grossMargins")),
            "operatingMargin": _raw(fd.get("operatingMargins")),
            "roe": _raw(fd.get("returnOnEquity")),
            "recKey": fd.get("recommendationKey"),
            "recMean": _raw(fd.get("recommendationMean")),
            "targetMean": _raw(fd.get("targetMeanPrice")),
            "targetLow": _raw(fd.get("targetLowPrice")),
            "targetHigh": _raw(fd.get("targetHighPrice")),
            "numAnalysts": _raw(fd.get("numberOfAnalystOpinions")),
            "nextEarnings": next_earn, "dividendDate": next_div, "exDividendDate": ex_div,
            "currency": pr.get("currency")}

# ---- teknik göstergeler ----
def _sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None

def _ema_series(v, n):
    k = 2.0 / (n + 1); e = v[0]; out = [e]
    for x in v[1:]:
        e = x * k + e * (1 - k); out.append(e)
    return out

def _rsi(v, n=14):
    if len(v) < n + 1:
        return None
    gains = []; losses = []
    for i in range(1, len(v)):
        d = v[i] - v[i - 1]; gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n; al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n; al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def _macd(v):
    if len(v) < 35:
        return None
    e12 = _ema_series(v, 12); e26 = _ema_series(v, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = _ema_series(line, 9)
    return {"macd": line[-1], "signal": sig[-1], "hist": line[-1] - sig[-1]}

def fetch_tech(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1y" % urllib.parse.quote(sym)
    res = json.load(_open(url))["chart"]["result"][0]
    closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
    if len(closes) < 30:
        return {"ok": False, "error": "yetersiz veri"}
    price = closes[-1]
    hi = max(closes); lo = min(closes)
    pos = ((price - lo) / (hi - lo) * 100) if hi > lo else None
    n = len(closes)
    ref = {"w": closes[-6] if n >= 6 else closes[0],
           "m": closes[-22] if n >= 22 else closes[0],
           "y": closes[0]}
    return {"ok": True, "price": price, "rsi": _rsi(closes, 14), "macd": _macd(closes),
            "ma20": _sma(closes, 20), "ma50": _sma(closes, 50), "ma200": _sma(closes, 200),
            "yrHigh": hi, "yrLow": lo, "rangePos": pos, "ref": ref,
            "spark": [round(x, 3) for x in closes[-60:]]}

_spot_cache = {"t": 0, "data": None}
SPOT_MAP = {"XAUUSD": "XAU", "XAGUSD": "XAG"}

def fetch_spot(sym):
    """Spot altın/gümüş — Yahoo sadece vadeli veriyor, bu gerçek spot fiyat."""
    code = SPOT_MAP.get(sym.upper())
    if not code:
        return {"ok": False, "error": "desteklenmeyen spot sembol"}
    now = time.time()
    cache = _spot_cache["data"] or {}
    if cache.get(code) and now - _spot_cache["t"] < 60:
        return cache[code]
    try:
        req = urllib.request.Request("https://api.gold-api.com/price/%s" % code, headers=UA)
        d = json.load(urllib.request.urlopen(req, timeout=10))
        price = d.get("price")
        # gold-api önceki kapanışı vermiyor; günlük % değişimi vadeli sözleşmeden al (spot ile paralel hareket eder)
        prev = price
        try:
            fut = "GC=F" if code == "XAU" else "SI=F"
            fm = json.load(_open("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1d" % fut))
            fmeta = fm["chart"]["result"][0]["meta"]
            fp = fmeta.get("regularMarketPrice")
            fprev = fmeta.get("chartPreviousClose") or fmeta.get("previousClose")
            if fp and fprev:
                prev = price / (fp / fprev)      # aynı yüzde değişimi spot fiyata uygula
        except Exception:
            pass
        out = {"ok": True, "symbol": sym, "price": price, "prevClose": prev,
               "currency": "USD", "exchange": "SPOT",
               "name": ("Altın (Spot)" if code == "XAU" else "Gümüş (Spot)")}
        cache[code] = out
        _spot_cache["data"] = cache
        _spot_cache["t"] = now
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)}

def fetch_hist(sym, rng):
    rng = rng if rng in ("1mo", "3mo", "6mo", "1y", "2y", "5y") else "6mo"
    interval = "1d" if rng in ("1mo", "3mo", "6mo", "1y") else "1wk"
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s" % (urllib.parse.quote(sym), interval, rng)
    res = json.load(_open(url))["chart"]["result"][0]
    ts = res.get("timestamp") or []
    cl = res.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    pts = [{"t": ts[i], "c": round(cl[i], 4)} for i in range(min(len(ts), len(cl))) if cl[i] is not None]
    return {"ok": True, "points": pts, "currency": res.get("meta", {}).get("currency")}

_fx_cache = {"t": 0, "data": None}
def fetch_fx():
    now = time.time()
    if _fx_cache["data"] and now - _fx_cache["t"] < 300:
        return _fx_cache["data"]
    def one(sym):
        try:
            u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1d" % urllib.parse.quote(sym)
            return json.load(_open(u))["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception:
            return None
    out = {"ok": True,
           "sekPer": {"SEK": 1.0, "USD": one("SEK=X"), "NOK": one("NOKSEK=X"),
                      "EUR": one("EURSEK=X"), "DKK": one("DKKSEK=X"),
                      "GBP": one("GBPSEK=X"), "CAD": one("CADSEK=X")},
           "usdTry": one("USDTRY=X")}
    _fx_cache["data"] = out; _fx_cache["t"] = now
    return out

def _clean(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    s = re.sub(r"<.*?>", "", s)
    return s.strip()

def fetch_news(sym):
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % urllib.parse.quote(sym)
    xml = _open(url).read().decode("utf-8", "ignore")
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
        def ex(tag):
            mm = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
            return _clean(mm.group(1)) if mm else ""
        items.append({"title": ex("title"), "link": ex("link"), "date": ex("pubDate")})
    return {"ok": True, "items": items}

EX_COUNTRY = {
    "Stockholm": "🇸🇪", "Oslo": "🇳🇴", "Helsinki": "🇫🇮", "Copenhagen": "🇩🇰",
    "NYSE": "🇺🇸", "NasdaqGS": "🇺🇸", "NASDAQ": "🇺🇸", "NYSEArca": "🇺🇸", "NYSE American": "🇺🇸",
    "Toronto": "🇨🇦", "London": "🇬🇧", "Frankfurt": "🇩🇪", "XETRA": "🇩🇪", "Milan": "🇮🇹",
    "Amsterdam": "🇳🇱", "Paris": "🇫🇷", "Singapore": "🇸🇬",
}

def fetch_search(q):
    url = "https://query1.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=8&newsCount=0" % urllib.parse.quote(q)
    d = json.load(_open(url))
    out = []
    for x in d.get("quotes", []):
        if x.get("quoteType") != "EQUITY":
            continue
        exch = x.get("exchDisp") or ""
        out.append({"symbol": x.get("symbol"),
                    "name": x.get("shortname") or x.get("longname") or "",
                    "exchange": exch,
                    "country": EX_COUNTRY.get(exch, "🏳️")})
    return {"ok": True, "items": out}

def _json(handler, obj):
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path.startswith("/api/"):
            qs = urllib.parse.parse_qs(p.query)
            if p.path == "/api/fx":
                try:
                    return _json(self, fetch_fx())
                except Exception as e:
                    return _json(self, {"ok": False, "error": str(e)})
            if p.path == "/api/search":
                q = (qs.get("q") or [""])[0].strip()
                try:
                    return _json(self, fetch_search(q) if q else {"ok": True, "items": []})
                except Exception as e:
                    return _json(self, {"ok": False, "error": str(e)})
            sym = (qs.get("symbol") or [""])[0].strip()
            if not sym:
                return _json(self, {"ok": False, "error": "sembol yok"})
            try:
                if p.path == "/api/q":
                    if sym.upper() in SPOT_MAP:
                        s = fetch_spot(sym)
                        if s.get("ok"):
                            pr, pv = s["price"], s.get("prevClose") or s["price"]
                            ch = pr - pv
                            s.update({"prev": pv, "change": ch,
                                      "pct": (ch / pv * 100) if pv else 0,
                                      "high": None, "low": None, "w52h": None, "w52l": None,
                                      "time": int(time.time())})
                        return _json(self, s)
                    return _json(self, fetch_quote(sym))
                if p.path == "/api/f":
                    return _json(self, fetch_fund(sym))
                if p.path == "/api/news":
                    return _json(self, fetch_news(sym))
                if p.path == "/api/tech":
                    return _json(self, fetch_tech(sym))
                if p.path == "/api/hist":
                    return _json(self, fetch_hist(sym, (qs.get("range") or ["6mo"])[0]))
            except Exception as e:
                return _json(self, {"ok": False, "symbol": sym, "error": str(e)})
            return _json(self, {"ok": False, "error": "bilinmeyen uç"})
        if p.path == "/":
            self.path = "/index.html"
        return super().do_GET()

if __name__ == "__main__":
    # Bulut (Render vb.) PORT'u ortam değişkeninden verir; yerelde 8000.
    port = int(os.environ.get("PORT", PORT))
    host = os.environ.get("HOST", "0.0.0.0")
    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=DIR)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        print("Portföy sunucusu: port %d" % port)
        httpd.serve_forever()
