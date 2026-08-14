[index.html](https://github.com/user-attachments/files/31075930/index.html)
[server.py](https://github.com/user-attachments/files/31075932/server.py)#!/usr/bin/env python3
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

def _fin_day(v):
    v = _raw(v)
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(v)) if v else None
    except Exception:
        return None

def _rows(items, fields):
    """Mali tablo satırlarını sadeleştir: [{date, alan1, alan2...}]"""
    out = []
    for it in (items or []):
        row = {"date": _fin_day(it.get("endDate"))}
        for f in fields:
            row[f] = _raw(it.get(f))
        out.append(row)
    return out

def fetch_fundamentals(sym):
    """Temel analiz: gelir tablosu, bilanço, nakit akışı (yıllık+çeyreklik), kâr geçmişi, profil."""
    crumb = get_crumb()
    if not crumb:
        return {"ok": False, "error": "crumb yok"}
    mods = ("assetProfile,incomeStatementHistory,incomeStatementHistoryQuarterly,"
            "balanceSheetHistory,balanceSheetHistoryQuarterly,"
            "cashflowStatementHistory,cashflowStatementHistoryQuarterly,"
            "earningsHistory,earningsTrend,financialData,defaultKeyStatistics,summaryDetail,price")
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s"
           % (urllib.parse.quote(sym), mods, urllib.parse.quote(crumb)))
    try:
        r = json.load(_open(url))["quoteSummary"]["result"][0]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            c2 = get_crumb(force=True)
            if not c2:
                return {"ok": False, "error": "crumb yenilenemedi"}
            url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s"
                   % (urllib.parse.quote(sym), mods, urllib.parse.quote(c2)))
            r = json.load(_open(url))["quoteSummary"]["result"][0]
        else:
            raise

    ap = r.get("assetProfile", {}) or {}
    fd = r.get("financialData", {}) or {}
    ks = r.get("defaultKeyStatistics", {}) or {}
    sd = r.get("summaryDetail", {}) or {}
    pr = r.get("price", {}) or {}

    INC = ["totalRevenue", "grossProfit", "operatingIncome", "netIncome", "ebit"]
    BAL = ["totalAssets", "totalLiab", "totalStockholderEquity", "cash", "shortLongTermDebt", "longTermDebt"]
    CF = ["totalCashFromOperatingActivities", "capitalExpenditures", "netIncome",
          "dividendsPaid", "repurchaseOfStock"]

    inc_a = _rows((r.get("incomeStatementHistory") or {}).get("incomeStatementHistory"), INC)
    inc_q = _rows((r.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory"), INC)
    bal_a = _rows((r.get("balanceSheetHistory") or {}).get("balanceSheetStatements"), BAL)
    bal_q = _rows((r.get("balanceSheetHistoryQuarterly") or {}).get("balanceSheetStatements"), BAL)
    cf_a = _rows((r.get("cashflowStatementHistory") or {}).get("cashflowStatements"), CF)
    cf_q = _rows((r.get("cashflowStatementHistoryQuarterly") or {}).get("cashflowStatements"), CF)

    earn = []
    for h in ((r.get("earningsHistory") or {}).get("history") or []):
        est, act = _raw(h.get("epsEstimate")), _raw(h.get("epsActual"))
        earn.append({"quarter": _fin_day(h.get("quarter")), "estimate": est, "actual": act,
                     "surprisePct": _raw(h.get("surprisePercent"))})
    earn.sort(key=lambda x: x["quarter"] or "", reverse=True)

    growth = {}
    for tr in ((r.get("earningsTrend") or {}).get("trend") or []):
        p = tr.get("period")
        if p in ("0q", "+1q", "0y", "+1y"):
            growth[p] = {"growth": _raw(tr.get("growth")),
                         "epsEstimate": _raw((tr.get("earningsEstimate") or {}).get("avg")),
                         "revEstimate": _raw((tr.get("revenueEstimate") or {}).get("avg"))}

    return {"ok": True, "symbol": sym,
            "profile": {"name": pr.get("longName") or pr.get("shortName"),
                        "sector": ap.get("sector"), "industry": ap.get("industry"),
                        "employees": ap.get("fullTimeEmployees"), "country": ap.get("country"),
                        "website": ap.get("website"), "summary": ap.get("longBusinessSummary")},
            "currency": fd.get("financialCurrency") or pr.get("currency"),
            "tradeCurrency": pr.get("currency"),
            "income": {"annual": inc_a, "quarterly": inc_q},
            "balance": {"annual": bal_a, "quarterly": bal_q},
            "cashflow": {"annual": cf_a, "quarterly": cf_q},
            "earningsHistory": earn, "growth": growth,
            "ratios": {"trailingPE": _raw(sd.get("trailingPE")), "forwardPE": _raw(sd.get("forwardPE")),
                       "priceToBook": _raw(ks.get("priceToBook")),
                       "roe": _raw(fd.get("returnOnEquity")), "roa": _raw(fd.get("returnOnAssets")),
                       "profitMargin": _raw(fd.get("profitMargins")),
                       "grossMargin": _raw(fd.get("grossMargins")),
                       "operatingMargin": _raw(fd.get("operatingMargins")),
                       "revenueGrowth": _raw(fd.get("revenueGrowth")),
                       "earningsGrowth": _raw(fd.get("earningsGrowth")),
                       "debtToEquity": _raw(fd.get("debtToEquity")),
                       "currentRatio": _raw(fd.get("currentRatio")),
                       "quickRatio": _raw(fd.get("quickRatio")),
                       "freeCashflow": _raw(fd.get("freeCashflow")),
                       "ebitda": _raw(fd.get("ebitda")),
                       "totalDebt": _raw(fd.get("totalDebt")), "totalCash": _raw(fd.get("totalCash")),
                       "dividendYield": _raw(sd.get("dividendYield")),
                       "payoutRatio": _raw(sd.get("payoutRatio")),
                       "marketCap": _raw(pr.get("marketCap")) or _raw(sd.get("marketCap"))}}

_market_cache = {"t": 0, "data": None}

def market_direction():
    """M kriteri: genel piyasa yönü — S&P 500'ün 200 günlük ortalamaya göre durumu."""
    now = time.time()
    if _market_cache["data"] and now - _market_cache["t"] < 900:
        return _market_cache["data"]
    out = {"ok": False}
    try:
        d = json.load(_open("https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=1y"))
        res = d["chart"]["result"][0]
        cl = [c for c in (res["indicators"]["quote"][0]["close"] or []) if c is not None]
        if len(cl) >= 200:
            price, ma200, ma50 = cl[-1], sum(cl[-200:]) / 200.0, sum(cl[-50:]) / 50.0
            yr = (cl[-1] / cl[0] - 1) * 100 if cl[0] else None
            up = price > ma200
            out = {"ok": True, "price": price, "ma200": ma200, "ma50": ma50,
                   "uptrend": up, "abovePct": (price / ma200 - 1) * 100, "yearChange": yr}
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    _market_cache["data"] = out
    _market_cache["t"] = now
    return out

def _index_for(sym):
    """Hisseye uygun kıyas endeksi (L kriteri için)."""
    s = sym.upper()
    if s.endswith(".ST"):
        return "^OMX"
    if s.endswith(".OL"):
        return "^OSEAX"
    if s.endswith(".IS"):
        return "XU100.IS"
    if s.endswith(".CO"):
        return "^OMXC25"
    return "^GSPC"

def _year_change(sym):
    try:
        d = json.load(_open("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1y"
                            % urllib.parse.quote(sym)))
        res = d["chart"]["result"][0]
        cl = [c for c in (res["indicators"]["quote"][0]["close"] or []) if c is not None]
        vols = [v for v in (res["indicators"]["quote"][0].get("volume") or []) if v]
        if len(cl) < 20:
            return None
        hi, lo, last = max(cl), min(cl), cl[-1]
        return {"change": (last / cl[0] - 1) * 100 if cl[0] else None,
                "rangePos": ((last - lo) / (hi - lo) * 100) if hi > lo else None,
                "high": hi, "low": lo, "price": last,
                "volRecent": (sum(vols[-10:]) / 10.0) if len(vols) >= 10 else None,
                "volAvg": (sum(vols[-60:]) / 60.0) if len(vols) >= 60 else None}
    except Exception:
        return None

def fetch_canslim(sym):
    """CAN SLIM karnesi — O'Neil metodolojisinin 7 kriteri, şeffaf puanlama."""
    crumb = get_crumb()
    if not crumb:
        return {"ok": False, "error": "crumb yok"}
    mods = ("financialData,defaultKeyStatistics,incomeStatementHistory,earningsHistory,"
            "majorHoldersBreakdown,institutionOwnership,price")
    url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s"
           % (urllib.parse.quote(sym), mods, urllib.parse.quote(crumb)))
    try:
        r = json.load(_open(url))["quoteSummary"]["result"][0]
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            c2 = get_crumb(force=True)
            url = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/%s?modules=%s&crumb=%s"
                   % (urllib.parse.quote(sym), mods, urllib.parse.quote(c2)))
            r = json.load(_open(url))["quoteSummary"]["result"][0]
        else:
            raise

    fd = r.get("financialData", {}) or {}
    ks = r.get("defaultKeyStatistics", {}) or {}
    mh = r.get("majorHoldersBreakdown", {}) or {}
    io = r.get("institutionOwnership", {}) or {}
    pr = r.get("price", {}) or {}

    stock = _year_change(sym) or {}
    idx_sym = _index_for(sym)
    idx = _year_change(idx_sym) or {}
    mkt = market_direction()
    crit = []

    # C — Son çeyrek kâr büyümesi (yıllık bazda), hedef >= %25
    eg = _raw(fd.get("earningsGrowth"))
    c_pct = eg * 100 if eg is not None else None
    crit.append({"code": "C", "value": c_pct, "unit": "%",
                 "pass": (c_pct is not None and c_pct >= 25),
                 "partial": (c_pct is not None and 10 <= c_pct < 25),
                 "target": "≥ 25%"})

    # A — Yıllık kâr büyümesi + ROE >= %17
    inc = (r.get("incomeStatementHistory") or {}).get("incomeStatementHistory") or []
    nets = [_raw(x.get("netIncome")) for x in inc]
    nets = [n for n in nets if n is not None]
    a_cagr = None
    if len(nets) >= 2 and nets[-1] and nets[-1] > 0 and nets[0] > 0:
        yrs = len(nets) - 1
        a_cagr = ((nets[0] / nets[-1]) ** (1.0 / yrs) - 1) * 100
    roe = _raw(fd.get("returnOnEquity"))
    roe_pct = roe * 100 if roe is not None else None
    a_ok = (a_cagr is not None and a_cagr >= 25) and (roe_pct is not None and roe_pct >= 17)
    a_part = (a_cagr is not None and a_cagr >= 10) or (roe_pct is not None and roe_pct >= 17)
    crit.append({"code": "A", "value": a_cagr, "unit": "%", "extra": {"roe": roe_pct},
                 "pass": a_ok, "partial": (not a_ok and a_part), "target": "büyüme ≥25% + ROE ≥17%"})

    # N — 52 haftalık zirveye yakınlık
    rp = stock.get("rangePos")
    crit.append({"code": "N", "value": rp, "unit": "%",
                 "pass": (rp is not None and rp >= 85), "partial": (rp is not None and 70 <= rp < 85),
                 "target": "52h aralığında üst %15"})

    # S — Arz: hisse adedi (küçük daha iyi) + hacim artışı
    shares = _raw(ks.get("sharesOutstanding"))
    vr, va = stock.get("volRecent"), stock.get("volAvg")
    vol_ratio = (vr / va) if (vr and va) else None
    s_ok = (shares is not None and shares < 500e6) and (vol_ratio is None or vol_ratio >= 1.0)
    crit.append({"code": "S", "value": shares, "unit": "adet",
                 "extra": {"volRatio": vol_ratio},
                 "pass": s_ok,
                 "partial": (shares is not None and shares < 2e9),
                 "target": "az hisse + hacim artışı"})

    # L — Lider mi? Endekse göre 1 yıllık getiri farkı
    sc, ic = stock.get("change"), idx.get("change")
    rel = (sc - ic) if (sc is not None and ic is not None) else None
    crit.append({"code": "L", "value": rel, "unit": "%",
                 "extra": {"stock": sc, "index": ic, "indexSym": idx_sym},
                 "pass": (rel is not None and rel >= 10), "partial": (rel is not None and 0 <= rel < 10),
                 "target": "endeksten ≥10% iyi"})

    # I — Kurumsal sahiplik
    inst = _raw(mh.get("institutionsPercentHeld"))
    inst_pct = inst * 100 if inst is not None else None
    holders = io.get("ownershipList") or []
    rising = sum(1 for o in holders if (_raw(o.get("pctChange")) or 0) > 0)
    crit.append({"code": "I", "value": inst_pct, "unit": "%",
                 "extra": {"holders": len(holders), "rising": rising},
                 "pass": (inst_pct is not None and 15 <= inst_pct <= 90),
                 "partial": (inst_pct is not None and 5 <= inst_pct < 15),
                 "target": "kurumsal sahiplik %15+"})

    # M — Piyasa yönü (S&P 500 200 günlük ortalama üstünde mi)
    m_up = mkt.get("uptrend")
    crit.append({"code": "M", "value": mkt.get("abovePct"), "unit": "%",
                 "extra": {"index": "S&P 500", "yearChange": mkt.get("yearChange")},
                 "pass": bool(m_up), "partial": False,
                 "target": "piyasa 200g ortalama üstünde"})

    score = sum(2 if c["pass"] else (1 if c.get("partial") else 0) for c in crit)
    maxs = len(crit) * 2
    pct = score / maxs * 100 if maxs else 0
    grade = "A" if pct >= 80 else "B" if pct >= 65 else "C" if pct >= 50 else "D" if pct >= 35 else "E"
    return {"ok": True, "symbol": sym, "name": pr.get("longName") or pr.get("shortName"),
            "criteria": crit, "score": score, "max": maxs, "pct": pct, "grade": grade,
            "market": mkt}

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

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/api/watch":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                syms = [s for s in (body.get("symbols") or []) if isinstance(s, str)][:80]
                with _state_lock:
                    st = _load_state()
                    st["watch"] = syms
                    _save_state(st)
                return _json(self, {"ok": True, "count": len(syms)})
            except Exception as e:
                return _json(self, {"ok": False, "error": str(e)})
        return _json(self, {"ok": False, "error": "bilinmeyen uç"})

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path.startswith("/api/"):
            qs = urllib.parse.parse_qs(p.query)
            if p.path == "/api/tg/status":
                tok, ch = tg_config()
                return _json(self, {"ok": True, "configured": bool(tok and ch)})
            if p.path == "/api/tg/test":
                return _json(self, tg_send("✅ Portföy bildirimleri çalışıyor!\n\nBundan sonra bilanço ve temettü tarihlerinde, sonuçlar açıklandığında haber vereceğim."))
            if p.path == "/api/tg/check":
                return _json(self, check_and_notify())
            if p.path == "/api/ping":
                return _json(self, {"ok": True, "t": int(time.time())})
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
            if p.path == "/api/interpret":
                try:
                    rep = interpret_earnings(sym)
                    return _json(self, rep or {"ok": False, "error": "veri yok"})
                except Exception as e:
                    return _json(self, {"ok": False, "error": str(e)})
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
                if p.path == "/api/canslim":
                    return _json(self, fetch_canslim(sym))
                if p.path == "/api/fundamentals":
                    return _json(self, fetch_fundamentals(sym))
                if p.path == "/api/hist":
                    return _json(self, fetch_hist(sym, (qs.get("range") or ["6mo"])[0]))
            except Exception as e:
                return _json(self, {"ok": False, "symbol": sym, "error": str(e)})
            return _json(self, {"ok": False, "error": "bilinmeyen uç"})
        if p.path == "/":
            self.path = "/index.html"
        return super().do_GET()

# ================= TELEGRAM BİLDİRİM + SONUÇ YORUMLAMA =================
STATE_FILE = os.path.join(DIR, "notify_state.json")
_state_lock = threading.Lock()

def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"watch": [], "sent": {}, "lastEarnings": {}}

def _save_state(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass

def tg_config():
    return os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")

def tg_send(text):
    """Telegram'a mesaj gönder. Token ortam değişkeninde tutulur (kodda DEĞİL)."""
    token, chat = tg_config()
    if not token or not chat:
        return {"ok": False, "error": "TELEGRAM_TOKEN / TELEGRAM_CHAT_ID tanımlı değil"}
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token,
                                     data=data, headers=UA)
        r = json.load(urllib.request.urlopen(req, timeout=15))
        return {"ok": bool(r.get("ok")), "error": r.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _pct(a, b):
    """b'ye göre a'nın yüzde farkı."""
    try:
        if b in (None, 0) or a is None:
            return None
        return (a - b) / abs(b) * 100
    except Exception:
        return None

def interpret_earnings(sym):
    """Açıklanan bilanço sonucunu şeffaf, kural bazlı yorumla (tahmin değil, rakam karşılaştırması)."""
    f = fetch_fundamentals(sym)
    if not f.get("ok"):
        return None
    name = (f.get("profile") or {}).get("name") or sym
    cur = f.get("currency") or ""
    eh = f.get("earningsHistory") or []
    inc = (f.get("income") or {}).get("quarterly") or []
    r = f.get("ratios") or {}
    if not eh:
        return None
    last = eh[0]
    lines, verdict = [], []

    est, act = last.get("estimate"), last.get("actual")
    if est is not None and act is not None:
        diff = _pct(act, est)
        if diff is not None:
            if diff >= 2:
                lines.append("✅ EPS <b>%.2f</b> — beklenti %.2f, <b>%%%.1f üzerinde</b>" % (act, est, diff))
                verdict.append("beklentiyi aştı")
            elif diff <= -2:
                lines.append("❌ EPS <b>%.2f</b> — beklenti %.2f, <b>%%%.1f altında</b>" % (act, est, abs(diff)))
                verdict.append("beklentinin altında")
            else:
                lines.append("➖ EPS <b>%.2f</b> — beklentiyle uyumlu (%.2f)" % (act, est))
                verdict.append("beklentiye paralel")

    # Ciro: son çeyrek vs bir yıl önceki aynı çeyrek (4 çeyrek geriye)
    if len(inc) >= 4 and inc[0].get("totalRevenue") and inc[3].get("totalRevenue"):
        g = _pct(inc[0]["totalRevenue"], inc[3]["totalRevenue"])
        if g is not None:
            arrow = "📈" if g >= 0 else "📉"
            lines.append("%s Ciro <b>%s</b> — geçen yılın aynı çeyreğine göre <b>%%%.1f</b>"
                         % (arrow, _human(inc[0]["totalRevenue"], cur), g))
            if g >= 10:
                verdict.append("ciro güçlü büyüdü")
            elif g < 0:
                verdict.append("ciro daraldı")

    # Kâr marjı trendi
    if len(inc) >= 2:
        def marg(x):
            rev, net = x.get("totalRevenue"), x.get("netIncome")
            return (net / rev * 100) if (rev and net is not None) else None
        m0, m1 = marg(inc[0]), marg(inc[1])
        if m0 is not None and m1 is not None:
            d = m0 - m1
            sign = "yükseldi" if d > 0.5 else ("geriledi" if d < -0.5 else "yatay")
            lines.append("📊 Net kâr marjı <b>%%%.1f</b> — önceki çeyreğe göre %s" % (m0, sign))

    if r.get("debtEbitda") is None and r.get("totalDebt") is not None and r.get("ebitda"):
        pass
    if r.get("profitMargin") is not None:
        pass

    head = "📣 <b>%s</b> — çeyrek sonuçları açıklandı" % name
    if last.get("quarter"):
        head += "\n<i>Dönem: %s</i>" % last["quarter"]
    body = "\n".join("• " + x for x in lines) if lines else "• Ayrıntılı veri bulunamadı."
    tail = ""
    if verdict:
        tail = "\n\n<b>Özet:</b> " + ", ".join(verdict) + "."
    tail += "\n\n<i>Bilgi amaçlıdır; yatırım tavsiyesi değildir.</i>"
    return {"symbol": sym, "name": name, "quarter": last.get("quarter"),
            "text": head + "\n\n" + body + tail, "lines": lines, "verdict": verdict}

def _human(v, cur=""):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        s = "%.2fB" % (v / 1e9)
    elif a >= 1e6:
        s = "%.1fM" % (v / 1e6)
    elif a >= 1e3:
        s = "%.0fK" % (v / 1e3)
    else:
        s = "%.0f" % v
    return s + ((" " + cur) if cur else "")

def check_and_notify():
    """Takip listesindeki hisseler için: yaklaşan tarihleri ve yeni açıklanan sonuçları bildir."""
    token, chat = tg_config()
    if not token or not chat:
        return {"ok": False, "error": "telegram ayarlı değil"}
    with _state_lock:
        st = _load_state()
    watch = st.get("watch") or []
    sent = st.get("sent") or {}
    last_earn = st.get("lastEarnings") or {}
    today = time.strftime("%Y-%m-%d")
    n_sent = 0

    for sym in watch[:60]:
        try:
            f = fetch_fund(sym)
            if not f.get("ok"):
                continue
            # 1) Yaklaşan tarih uyarıları (bugün / 1 gün kala)
            for field, label in (("nextEarnings", "📊 Bilanço günü"),
                                 ("dividendDate", "💰 Temettü ödemesi"),
                                 ("exDividendDate", "✂️ Temettü son gün")):
                iso = f.get(field)
                if not iso:
                    continue
                try:
                    d = (time.mktime(time.strptime(iso, "%Y-%m-%d"))
                         - time.mktime(time.strptime(today, "%Y-%m-%d"))) / 86400
                except Exception:
                    continue
                if d in (0, 1):
                    key = "%s|%s|%s" % (sym, field, iso)
                    if sent.get(key):
                        continue
                    when = "bugün" if d == 0 else "yarın"
                    tg_send("%s — <b>%s</b>\n%s (%s)" % (label, sym, when, iso))
                    sent[key] = today
                    n_sent += 1

            # 2) Yeni açıklanan sonuç -> yorumla
            fu = fetch_fundamentals(sym)
            eh = (fu.get("earningsHistory") or []) if fu.get("ok") else []
            if eh and eh[0].get("actual") is not None:
                q = eh[0].get("quarter")
                if q and last_earn.get(sym) != q:
                    rep = interpret_earnings(sym)
                    if rep:
                        tg_send(rep["text"])
                        n_sent += 1
                    last_earn[sym] = q
        except Exception:
            continue

    with _state_lock:
        st["sent"] = sent
        st["lastEarnings"] = last_earn
        _save_state(st)
    return {"ok": True, "sent": n_sent, "watched": len(watch)}

def notify_loop():
    def loop():
        time.sleep(120)
        while True:
            try:
                check_and_notify()
            except Exception:
                pass
            time.sleep(6 * 60 * 60)      # 6 saatte bir kontrol
    threading.Thread(target=loop, daemon=True).start()

def keep_alive():
    """Render ücretsiz planda 15 dk hareketsizlikte uyur. Kendi adresimize düzenli
    istek atarak servisi uyanık tutuyoruz (aylık ~730 saat, 750 saat limitine sığar)."""
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return                       # yerelde çalışmaz, gerek de yok
    ping = url.rstrip("/") + "/api/ping"

    def loop():
        time.sleep(60)
        while True:
            try:
                urllib.request.urlopen(urllib.request.Request(ping, headers=UA), timeout=20).read()
            except Exception:
                pass
            time.sleep(11 * 60)      # 11 dakikada bir (15 dk limitin altında)

    th = threading.Thread(target=loop, daemon=True)
    th.start()

if __name__ == "__main__":
    # Bulut (Render vb.) PORT'u ortam değişkeninden verir; yerelde 8000.
    port = int(os.environ.get("PORT", PORT))
    host = os.environ.get("HOST", "0.0.0.0")
    socketserver.TCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=DIR)
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        keep_alive()
        notify_loop()
        print("Portföy sunucusu: port %d" % port)
        httpd.serve_forever()
