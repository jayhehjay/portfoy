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

TS_FIELDS = {
    "balance": [("TotalAssets", "totalAssets"),
                ("TotalLiabilitiesNetMinorityInterest", "totalLiab"),
                ("StockholdersEquity", "totalStockholderEquity"),
                ("CashAndCashEquivalents", "cash"),
                ("TotalDebt", "longTermDebt")],
    "cashflow": [("OperatingCashFlow", "totalCashFromOperatingActivities"),
                 ("CapitalExpenditure", "capitalExpenditures"),
                 ("FreeCashFlow", "freeCashFlow"),
                 ("NetIncome", "netIncome")],
    "income": [("TotalRevenue", "totalRevenue"),
               ("CostOfRevenue", "costOfRevenue"),
               ("GrossProfit", "grossProfit"),
               ("OperatingIncome", "operatingIncome"),
               ("EBITDA", "ebitda"),
               ("NetIncome", "netIncome"),
               ("DilutedEPS", "eps")],
}

def fetch_timeseries(sym, group, period="quarterly", limit=4):
    """Yahoo'nun yeni fundamentals-timeseries API'si.
    quoteSummary artık bilanço/nakit akışı vermiyor; bu uç veriyor."""
    fields = TS_FIELDS.get(group) or []
    if not fields:
        return []
    types = ",".join(period + f[0] for f in fields)
    url = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
           "?symbol=%s&type=%s&period1=1451606400&period2=%d&merge=false"
           % (urllib.parse.quote(sym), urllib.parse.quote(sym), types, int(time.time())))
    try:
        d = json.load(_open(url))
    except Exception:
        return []
    by_date = {}
    for res in (d.get("timeseries", {}) or {}).get("result", []) or []:
        tp = ((res.get("meta") or {}).get("type") or [None])[0]
        if not tp:
            continue
        out_key = None
        for src, dst in fields:
            if tp == period + src:
                out_key = dst
                break
        if not out_key:
            continue
        for pt in (res.get(tp) or []):
            if not pt:
                continue
            day = pt.get("asOfDate")
            val = (pt.get("reportedValue") or {}).get("raw")
            if day is None or val is None:
                continue
            by_date.setdefault(day, {"date": day})[out_key] = val
    rows = sorted(by_date.values(), key=lambda r: r["date"], reverse=True)
    return rows[:limit]

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

    # Gelir tablosu quoteSummary'de dolu geliyor; bilanço/nakit akışı ise BOŞ dönüyor
    # (Yahoo kaldırdı) -> onları yeni timeseries API'sinden çekiyoruz.
    # quoteSummary gelir tablosunda eksikler var -> doğrudan timeseries kullan
    inc_q = fetch_timeseries(sym, "income", "quarterly")
    inc_a = fetch_timeseries(sym, "income", "annual")
    if not inc_q:
        inc_q = _rows((r.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory"), INC)
    if not inc_a:
        inc_a = _rows((r.get("incomeStatementHistory") or {}).get("incomeStatementHistory"), INC)
    bal_q = fetch_timeseries(sym, "balance", "quarterly")
    bal_a = fetch_timeseries(sym, "balance", "annual")
    cf_q = fetch_timeseries(sym, "cashflow", "quarterly")
    cf_a = fetch_timeseries(sym, "cashflow", "annual")

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

def fetch_valuation(sym):
    """Değerleme: bugünkü F/K, PD/DD, FD/FAVÖK — kendi geçmişiyle kıyaslanır."""
    types = ",".join(["quarterlyPeRatio", "quarterlyPbRatio", "quarterlyEnterprisesValueEBITDARatio",
                      "annualPeRatio", "annualPbRatio", "annualEnterprisesValueEBITDARatio"])
    url = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/%s"
           "?symbol=%s&type=%s&period1=1420070400&period2=%d&merge=false"
           % (urllib.parse.quote(sym), urllib.parse.quote(sym), types, int(time.time())))
    series = {}
    try:
        d = json.load(_open(url))
        for res in (d.get("timeseries", {}) or {}).get("result", []) or []:
            tp = ((res.get("meta") or {}).get("type") or [None])[0]
            if not tp:
                continue
            vals = []
            for pt in (res.get(tp) or []):
                if not pt:
                    continue
                v = (pt.get("reportedValue") or {}).get("raw")
                if v is not None:
                    vals.append({"date": pt.get("asOfDate"), "v": v})
            if vals:
                series[tp] = sorted(vals, key=lambda x: x["date"])
    except Exception:
        pass

    def metric(name):
        hist = (series.get("annual" + name) or []) + (series.get("quarterly" + name) or [])
        hist = sorted({h["date"]: h for h in hist}.values(), key=lambda x: x["date"])
        if not hist:
            return None
        raw_vals = [h["v"] for h in hist if h["v"] and h["v"] > 0]
        if not raw_vals:
            return None
        cur = raw_vals[-1]
        # Kâr sıfıra yaklaşınca F/K uçuk değerler alır (ör. 800) ve ortalamayı bozar.
        # Aykırı değerleri ele: medyanın 3 katından büyükleri at, ORTALAMA yerine MEDYAN kullan.
        srt = sorted(raw_vals)
        med = srt[len(srt) // 2] if len(srt) % 2 else (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2.0
        vals = [v for v in raw_vals if v <= max(med * 3.0, 1e-9)] or raw_vals
        srt2 = sorted(vals)
        typical = srt2[len(srt2) // 2] if len(srt2) % 2 else (srt2[len(srt2) // 2 - 1] + srt2[len(srt2) // 2]) / 2.0
        return {"current": cur, "avg": typical, "min": min(vals), "max": max(vals),
                "n": len(vals), "outliers": len(raw_vals) - len(vals),
                "vsAvgPct": ((cur - typical) / typical * 100) if typical else None,
                "history": hist[-12:]}
    return {"ok": True, "symbol": sym,
            "pe": metric("PeRatio"), "pb": metric("PbRatio"),
            "evEbitda": metric("EnterprisesValueEBITDARatio")}

def fetch_dividends(sym):
    """Temettü geçmişi + güvenilirlik: kaç yıldır ödüyor, artıyor mu, dağıtım oranı."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1mo&range=10y&events=div"
           % urllib.parse.quote(sym))
    try:
        d = json.load(_open(url))
        res = d["chart"]["result"][0]
        ev = (res.get("events") or {}).get("dividends") or {}
        cur = (res.get("meta") or {}).get("currency")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    items = sorted(({"date": v.get("date"), "amount": v.get("amount")}
                    for v in ev.values() if v.get("amount")), key=lambda x: x["date"])
    if not items:
        return {"ok": True, "symbol": sym, "hasDividend": False, "currency": cur, "years": []}

    by_year = {}
    for it in items:
        y = time.strftime("%Y", time.gmtime(it["date"]))
        by_year[y] = by_year.get(y, 0) + it["amount"]
    years = [{"year": y, "total": round(by_year[y], 4)} for y in sorted(by_year)]
    # Bu yıl henüz tamamlanmadıysa büyüme kıyasını bozmasın
    this_year = time.strftime("%Y")
    full = [y for y in years if y["year"] != this_year]
    growth_streak, cuts = 0, 0
    for i in range(len(full) - 1, 0, -1):
        if full[i]["total"] > full[i - 1]["total"] * 1.001:
            growth_streak += 1
        else:
            break
    for i in range(1, len(full)):
        if full[i]["total"] < full[i - 1]["total"] * 0.999:
            cuts += 1
    return {"ok": True, "symbol": sym, "hasDividend": True, "currency": cur,
            "years": years[-10:], "payingYears": len(by_year),
            "growthStreak": growth_streak, "cuts": cuts,
            "lastAmount": items[-1]["amount"],
            "lastDate": time.strftime("%Y-%m-%d", time.gmtime(items[-1]["date"]))}

def build_scorecard(sym):
    """4 boyutlu karne: Değer · Büyüme · Kalite · Momentum (şeffaf kurallar)."""
    f = fetch_fund(sym)
    val = fetch_valuation(sym)
    tech = fetch_tech(sym)
    if not f.get("ok"):
        return {"ok": False, "error": "veri yok"}
    dims = []

    # DEĞER — F/K kendi ortalamasına göre + PD/DD
    pts, notes = 0, []
    pe = (val.get("pe") or {})
    if pe.get("vsAvgPct") is not None:
        v = pe["vsAvgPct"]
        if v <= -20: pts += 2; notes.append({"d": "pos", "k": "scCheapVsHist", "v": abs(round(v))})
        elif v <= 0: pts += 1; notes.append({"d": "pos", "k": "scBelowHist", "v": abs(round(v))})
        elif v >= 30: notes.append({"d": "neg", "k": "scExpensiveVsHist", "v": round(v)})
        else: pts += 1
    tpe = f.get("trailingPE")
    if tpe is not None:
        if tpe < 15: pts += 1; notes.append({"d": "pos", "k": "scLowPe", "v": round(tpe, 1)})
        elif tpe > 35: notes.append({"d": "neg", "k": "scHighPe", "v": round(tpe, 1)})
    dims.append({"key": "value", "score": min(pts, 3), "max": 3, "notes": notes})

    # BÜYÜME — ciro + kâr
    pts, notes = 0, []
    rg, eg = f.get("revenueGrowth"), f.get("earningsGrowth")
    if rg is not None:
        if rg >= 0.15: pts += 2; notes.append({"d": "pos", "k": "scRevStrong", "v": round(rg * 100)})
        elif rg >= 0.05: pts += 1; notes.append({"d": "pos", "k": "scRevOk", "v": round(rg * 100)})
        elif rg < 0: notes.append({"d": "neg", "k": "scRevDown", "v": round(rg * 100)})
    if eg is not None:
        if eg >= 0.15: pts += 1; notes.append({"d": "pos", "k": "scEarnStrong", "v": round(eg * 100)})
        elif eg < 0: notes.append({"d": "neg", "k": "scEarnDown", "v": round(eg * 100)})
    dims.append({"key": "growth", "score": min(pts, 3), "max": 3, "notes": notes})

    # KALİTE — marj, ROE, borç
    pts, notes = 0, []
    pm, roe, de = f.get("profitMargin"), f.get("roe"), f.get("debtEbitda")
    if pm is not None:
        if pm >= 0.15: pts += 1; notes.append({"d": "pos", "k": "scMarginGood", "v": round(pm * 100)})
        elif pm < 0: notes.append({"d": "neg", "k": "scLoss"})
    if roe is not None:
        if roe >= 0.15: pts += 1; notes.append({"d": "pos", "k": "scRoeGood", "v": round(roe * 100)})
    if de is not None:
        if de <= 1: pts += 1; notes.append({"d": "pos", "k": "scDebtLow"})
        elif de > 3: notes.append({"d": "neg", "k": "scDebtHigh", "v": round(de, 1)})
    dims.append({"key": "quality", "score": min(pts, 3), "max": 3, "notes": notes})

    # MOMENTUM — trend + RSI
    pts, notes = 0, []
    if tech.get("ok"):
        p, m50, m200 = tech.get("price"), tech.get("ma50"), tech.get("ma200")
        if p and m200 and p > m200: pts += 1; notes.append({"d": "pos", "k": "scAbove200"})
        if p and m50 and p > m50: pts += 1; notes.append({"d": "pos", "k": "scAbove50"})
        mac = tech.get("macd") or {}
        if mac.get("hist") is not None:
            if mac["hist"] > 0: pts += 1; notes.append({"d": "pos", "k": "scMacdUp"})
            else: notes.append({"d": "neg", "k": "scMacdDown"})
    dims.append({"key": "momentum", "score": min(pts, 3), "max": 3, "notes": notes})

    total = sum(x["score"] for x in dims)
    return {"ok": True, "symbol": sym, "dims": dims, "total": total, "max": 12,
            "pct": total / 12.0 * 100}

def fetch_deep(sym):
    out = {"ok": True, "symbol": sym}
    try: out["valuation"] = fetch_valuation(sym)
    except Exception as e: out["valuation"] = {"ok": False, "error": str(e)}
    try: out["dividends"] = fetch_dividends(sym)
    except Exception as e: out["dividends"] = {"ok": False, "error": str(e)}
    try: out["scorecard"] = build_scorecard(sym)
    except Exception as e: out["scorecard"] = {"ok": False, "error": str(e)}
    return out

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

# ================= TRADE RADARI: SEVIYE HARITASI (ATR + destek/direnc) =================
def fetch_levels(sym):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=3mo" % urllib.parse.quote(sym)
    res = json.load(_open(url))["chart"]["result"][0]
    q = res.get("indicators", {}).get("quote", [{}])[0]
    hi, lo, cl, vo = q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    bars = []
    for i in range(len(cl)):
        if cl[i] is None or hi[i] is None or lo[i] is None:
            continue
        bars.append({"h": hi[i], "l": lo[i], "c": cl[i], "v": (vo[i] if i < len(vo) else None) or 0})
    if len(bars) < 20:
        return {"ok": False, "error": "yetersiz veri"}
    price = bars[-1]["c"]
    # ATR14
    trs = []
    for i in range(1, len(bars)):
        pc = bars[i-1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc)))
    atr = sum(trs[-14:]) / min(14, len(trs))
    # pivot dip/tepeler (2 sag 2 sol)
    sups, ress = [], []
    for i in range(2, len(bars) - 2):
        w = bars[i-2:i+3]
        if bars[i]["l"] == min(x["l"] for x in w):
            sups.append(bars[i]["l"])
        if bars[i]["h"] == max(x["h"] for x in w):
            ress.append(bars[i]["h"])
    def near_dedup(vals):
        out = []
        for v in sorted(vals):
            if not out or abs(v - out[-1]) / max(out[-1], 1e-9) > 0.01:
                out.append(v)
            else:
                out[-1] = (out[-1] + v) / 2.0
        return out
    sups = [v for v in near_dedup(sups) if v < price][-3:]
    ress = [v for v in near_dedup(ress) if v > price][:3]
    avgv = sum(b["v"] for b in bars[-20:]) / 20.0
    lastv = bars[-1]["v"]
    rng20 = sum((b["h"] - b["l"]) / b["c"] * 100 for b in bars[-20:] if b["c"]) / 20.0
    return {"ok": True, "price": price, "atr": atr, "atrPct": atr / price * 100 if price else None,
            "supports": [round(x, 3) for x in reversed(sups)],
            "resistances": [round(x, 3) for x in ress],
            "avgVol": avgv, "lastVol": lastv,
            "volX": (lastv / avgv) if avgv else None,
            "dayRangePct": rng20,
            "currency": res.get("meta", {}).get("currency")}


# ================= PROFESYONEL TEKNIK ANALIZ (/api/ta) =================
def _sma_series(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    s = sum(v[:n]); out[n-1] = s / n
    for i in range(n, len(v)):
        s += v[i] - v[i-n]; out[i] = s / n
    return out

def _stdev(v, n, i):
    if i + 1 < n:
        return None
    w = v[i-n+1:i+1]; m = sum(w) / n
    return (sum((x - m) ** 2 for x in w) / n) ** 0.5

def _rsi_series(v, n=14):
    out = [None] * len(v)
    if len(v) < n + 1:
        return out
    g = [0.0] * len(v); l = [0.0] * len(v)
    for i in range(1, len(v)):
        ch = v[i] - v[i-1]
        g[i] = max(ch, 0.0); l[i] = max(-ch, 0.0)
    ag = sum(g[1:n+1]) / n; al = sum(l[1:n+1]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out

def _stoch(hi, lo, cl, n=14, d=3):
    k = [None] * len(cl)
    for i in range(n - 1, len(cl)):
        hh = max(hi[i-n+1:i+1]); ll = min(lo[i-n+1:i+1])
        k[i] = 50.0 if hh == ll else (cl[i] - ll) / (hh - ll) * 100
    ks = [x for x in k if x is not None]
    dv = _sma_series(ks, d) if len(ks) >= d else []
    return (k[-1] if k else None), (dv[-1] if dv else None)

def _cci(hi, lo, cl, n=20):
    tp = [(hi[i] + lo[i] + cl[i]) / 3.0 for i in range(len(cl))]
    if len(tp) < n:
        return None
    w = tp[-n:]; m = sum(w) / n
    md = sum(abs(x - m) for x in w) / n
    return None if md == 0 else (tp[-1] - m) / (0.015 * md)

def _williams(hi, lo, cl, n=14):
    if len(cl) < n:
        return None
    hh = max(hi[-n:]); ll = min(lo[-n:])
    return None if hh == ll else (hh - cl[-1]) / (hh - ll) * -100

def _atr_series(hi, lo, cl, n=14):
    tr = [None]
    for i in range(1, len(cl)):
        tr.append(max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1])))
    out = [None] * len(cl)
    vals = [x for x in tr if x is not None]
    if len(vals) < n:
        return out
    a = sum(vals[:n]) / n; out[n] = a
    for i in range(n + 1, len(cl)):
        a = (a * (n - 1) + tr[i]) / n; out[i] = a
    return out

def _adx(hi, lo, cl, n=14):
    if len(cl) < n * 2:
        return None
    pdm = []; ndm = []; tr = []
    for i in range(1, len(cl)):
        up = hi[i] - hi[i-1]; dw = lo[i-1] - lo[i]
        pdm.append(up if (up > dw and up > 0) else 0.0)
        ndm.append(dw if (dw > up and dw > 0) else 0.0)
        tr.append(max(hi[i] - lo[i], abs(hi[i] - cl[i-1]), abs(lo[i] - cl[i-1])))
    def smooth(x):
        s = sum(x[:n]); out = [s]
        for i in range(n, len(x)):
            s = s - s / n + x[i]; out.append(s)
        return out
    st, sp, sn = smooth(tr), smooth(pdm), smooth(ndm)
    dx = []
    for i in range(len(st)):
        if st[i] == 0:
            continue
        pdi = sp[i] / st[i] * 100; ndi = sn[i] / st[i] * 100
        if pdi + ndi:
            dx.append(abs(pdi - ndi) / (pdi + ndi) * 100)
    if len(dx) < n:
        return None
    return sum(dx[-n:]) / n

def _obv(cl, vo):
    o = 0.0; out = [0.0]
    for i in range(1, len(cl)):
        if cl[i] > cl[i-1]:
            o += vo[i]
        elif cl[i] < cl[i-1]:
            o -= vo[i]
        out.append(o)
    return out

def fetch_ta(sym, rng="6mo", iv="1d", bars=70):
    iv = iv if iv in ("1d", "1wk", "1mo") else "1d"
    try:
        bars = max(20, min(400, int(bars)))
    except Exception:
        bars = 70
    # gostergeler icin genis veri cek (200 periyotluk ortalama gerekli), gosterimde kirp
    span = {"1d": "5y", "1wk": "10y", "1mo": "max"}[iv]
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s"
           % (urllib.parse.quote(sym), iv, span))
    res = json.load(_open(url))["chart"]["result"][0]
    meta = res.get("meta", {}) or {}
    ts = res.get("timestamp") or []
    q = res.get("indicators", {}).get("quote", [{}])[0]
    O, H, L, C, V = (q.get("open") or [], q.get("high") or [], q.get("low") or [],
                     q.get("close") or [], q.get("volume") or [])
    T, o_, h_, l_, c_, v_ = [], [], [], [], [], []
    for i in range(min(len(ts), len(C))):
        if C[i] is None or H[i] is None or L[i] is None or O[i] is None:
            continue
        T.append(ts[i]); o_.append(O[i]); h_.append(H[i]); l_.append(L[i]); c_.append(C[i])
        v_.append((V[i] if i < len(V) else 0) or 0)
    if len(c_) < 60:
        return {"ok": False, "error": "yetersiz veri"}

    price = c_[-1]
    sma = {n: _sma_series(c_, n) for n in (10, 20, 30, 50, 100, 200)}
    e12 = _ema_series(c_, 12); e26 = _ema_series(c_, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    macd_sig = _ema_series(macd_line, 9)
    rsi_s = _rsi_series(c_, 14)
    atr_s = _atr_series(h_, l_, c_, 14)
    obv_s = _obv(c_, v_)
    bb_mid = sma[20]
    sd = _stdev(c_, 20, len(c_) - 1)
    bb_up = (bb_mid[-1] + 2 * sd) if (bb_mid[-1] is not None and sd is not None) else None
    bb_dn = (bb_mid[-1] - 2 * sd) if (bb_mid[-1] is not None and sd is not None) else None
    bb_w = ((bb_up - bb_dn) / bb_mid[-1] * 100) if (bb_up and bb_mid[-1]) else None
    k, dd = _stoch(h_, l_, c_)
    vol20 = _sma_series([float(x) for x in v_], 20)
    vol_x = (v_[-1] / vol20[-1]) if (vol20[-1]) else None
    obv_trend = None
    if len(obv_s) > 21:
        obv_trend = "up" if obv_s[-1] > obv_s[-21] else ("down" if obv_s[-1] < obv_s[-21] else "flat")

    # --- sinyal sayimi (TradingView mantigina benzer, notr dille) ---
    ma_sig = []
    for n in (10, 20, 30, 50, 100, 200):
        mv = sma[n][-1]
        if mv is None:
            continue
        ma_sig.append({"name": "MA%d" % n, "value": round(mv, 4),
                       "signal": "up" if price > mv else ("down" if price < mv else "neutral")})
    osc = []
    r = rsi_s[-1]
    if r is not None:
        osc.append({"name": "RSI(14)", "value": round(r, 1),
                    "signal": "down" if r > 70 else ("up" if r < 30 else "neutral")})
    if macd_line and macd_sig:
        hist = macd_line[-1] - macd_sig[-1]
        osc.append({"name": "MACD(12,26,9)", "value": round(hist, 4),
                    "signal": "up" if hist > 0 else ("down" if hist < 0 else "neutral")})
    if k is not None:
        osc.append({"name": "Stochastic %K", "value": round(k, 1),
                    "signal": "down" if k > 80 else ("up" if k < 20 else "neutral")})
    cci = _cci(h_, l_, c_)
    if cci is not None:
        osc.append({"name": "CCI(20)", "value": round(cci, 1),
                    "signal": "down" if cci > 100 else ("up" if cci < -100 else "neutral")})
    wr = _williams(h_, l_, c_)
    if wr is not None:
        osc.append({"name": "Williams %R", "value": round(wr, 1),
                    "signal": "down" if wr > -20 else ("up" if wr < -80 else "neutral")})
    if len(c_) > 11:
        mom = c_[-1] - c_[-11]
        osc.append({"name": "Momentum(10)", "value": round(mom, 4),
                    "signal": "up" if mom > 0 else ("down" if mom < 0 else "neutral")})
    adx = _adx(h_, l_, c_)

    def tally(rows):
        u = sum(1 for x in rows if x["signal"] == "up")
        dn = sum(1 for x in rows if x["signal"] == "down")
        nt = sum(1 for x in rows if x["signal"] == "neutral")
        return {"up": u, "down": dn, "neutral": nt, "total": len(rows)}
    ma_t, os_t = tally(ma_sig), tally(osc)
    tot_u = ma_t["up"] + os_t["up"]; tot_d = ma_t["down"] + os_t["down"]
    tot_n = ma_t["neutral"] + os_t["neutral"]; tot = tot_u + tot_d + tot_n
    ratio = ((tot_u - tot_d) / tot) if tot else 0
    if ratio >= 0.5:   summary = "strong_up"
    elif ratio >= 0.15: summary = "up"
    elif ratio <= -0.5: summary = "strong_down"
    elif ratio <= -0.15: summary = "down"
    else:               summary = "neutral"

    # --- grafik verisi (istenen bar sayisi kadar) ---
    s = max(0, len(c_) - bars)
    def cut(arr):
        return [None if x is None else round(x, 4) for x in arr[s:]]
    bars = [{"t": T[i], "o": round(o_[i], 4), "h": round(h_[i], 4),
             "l": round(l_[i], 4), "c": round(c_[i], 4), "v": v_[i]} for i in range(s, len(c_))]
    # fibonacci (gosterilen aralikta)
    seg_h = max(h_[s:]); seg_l = min(l_[s:])
    fib = {("%.3f" % lv): round(seg_h - (seg_h - seg_l) * lv, 4)
           for lv in (0.236, 0.382, 0.5, 0.618, 0.786)}

    # devam eden hafta/ay barinin hacmi Yahoo'da eksik gelir -> isaretle
    partial = False
    if iv in ("1wk", "1mo") and T:
        lt = time.gmtime(T[-1]); nw = time.gmtime()
        if iv == "1mo":
            partial = (lt.tm_year == nw.tm_year and lt.tm_mon == nw.tm_mon)
        else:
            partial = (time.time() - T[-1]) < 7 * 86400

    # donem getirileri (gunluk kapanislardan; haftalik/aylik gorunumde de dogru)
    def ret_over(bars_back):
        if len(c_) <= bars_back:
            return None
        old = c_[-1 - bars_back]
        return round((price - old) / old * 100, 2) if old else None
    per = {"1d": 1, "1wk": 5, "1mo": 21}[iv]
    rets = {"m1": ret_over(int(21 / per)), "m3": ret_over(int(63 / per)),
            "m6": ret_over(int(126 / per)), "y1": ret_over(int(252 / per))}

    return {"ok": True, "symbol": sym, "range": rng, "interval": iv, "partialLast": partial,
            "returns": rets,
            "currency": meta.get("currency"), "name": meta.get("longName") or meta.get("shortName"),
            "price": price, "bars": bars,
            "series": {"ma20": cut(sma[20]), "ma50": cut(sma[50]), "ma200": cut(sma[200]),
                       "rsi": cut(rsi_s), "macd": cut(macd_line), "signal": cut(macd_sig),
                       "volMa": cut(vol20)},
            "ma": ma_sig, "osc": osc, "maTally": ma_t, "oscTally": os_t,
            "summary": summary, "score": round(ratio, 3),
            "rsi": (round(r, 1) if r is not None else None),
            "adx": (round(adx, 1) if adx is not None else None),
            "atr": (round(atr_s[-1], 4) if atr_s[-1] is not None else None),
            "atrPct": (round(atr_s[-1] / price * 100, 2) if (atr_s[-1] and price) else None),
            "bb": {"upper": (round(bb_up, 4) if bb_up else None),
                   "mid": (round(bb_mid[-1], 4) if bb_mid[-1] else None),
                   "lower": (round(bb_dn, 4) if bb_dn else None),
                   "width": (round(bb_w, 2) if bb_w else None)},
            "volume": {"last": v_[-1], "avg20": (round(vol20[-1]) if vol20[-1] else None),
                       "x": (round(vol_x, 2) if vol_x else None), "obvTrend": obv_trend},
            "hi52": round(max(c_[-260:]), 4), "lo52": round(min(c_[-260:]), 4),
            "fib": fib}

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

def _pubts(s):
    try:
        import email.utils
        return int(email.utils.parsedate_to_datetime(s).timestamp())
    except Exception:
        return 0

def _rss_items(url, limit=8):
    try:
        xml = _open(url).read().decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S)[:limit]:
        def ex(tag):
            mm = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
            return _clean(mm.group(1)) if mm else ""
        ttl = ex("title")
        if not ttl:
            continue
        src = ""
        m2 = re.search(r"<source[^>]*>(.*?)</source>", block, re.S)
        if m2:
            src = _clean(m2.group(1))
        # Google News basliklari "Baslik - Kaynak" seklinde gelir; kaynagi baslikten ayikla
        if " - " in ttl:
            parts = ttl.rsplit(" - ", 1)
            if len(parts[1]) < 40:
                ttl = parts[0]
                if not src:
                    src = parts[1]
        pd = ex("pubDate")
        out.append({"title": ttl, "link": ex("link"), "date": pd,
                    "ts": _pubts(pd), "source": src})
    return out

def _yahoo_search_news(name, limit=6):
    try:
        u = ("https://query1.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=0&newsCount=%d"
             % (urllib.parse.quote(name), limit))
        dd = json.load(_open(u))
    except Exception:
        return []
    out = []
    for n in dd.get("news", []):
        ts = n.get("providerPublishTime") or 0
        out.append({"title": n.get("title") or "", "link": n.get("link") or "",
                    "date": "", "ts": int(ts), "source": n.get("publisher") or ""})
    return [x for x in out if x["title"]]

GNEWS_LOCALE = {
    "en": ("en-US", "US", "US:en"),
    "tr": ("tr", "TR", "TR:tr"),
    "sv": ("sv", "SE", "SE:sv"),
}

def fetch_news(sym, name=None, per=6, lang="en"):
    """Uc kaynak: Yahoo RSS (sembol) + Yahoo arama + Google News (isim).
    Nordic hisselerde Yahoo RSS haftalarca eski kalir; Google News gunluk getirir."""
    items = []
    # Ingilizce disi bir dil secildiyse ONCE o dilde ara; yeterliyse sadece onu goster
    if name and lang != "en":
        hl, gl, ceid = GNEWS_LOCALE.get(lang, GNEWS_LOCALE["en"])
        words0 = [w for w in re.split(r"[^A-Za-zÅÄÖåäö0-9]+", name) if len(w) >= 4]
        key0 = max(words0, key=len).lower() if words0 else name.lower()
        loc = [it for it in _rss_items(
            "https://news.google.com/rss/search?q=%s&hl=%s&gl=%s&ceid=%s"
            % (urllib.parse.quote('"' + name + '"'), hl, gl, urllib.parse.quote(ceid)), limit=14)
            if key0 in it["title"].lower()]
        if len(loc) >= 2:
            seen0, uq = set(), []
            for it in loc:
                k0 = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
                if k0 and k0 not in seen0:
                    seen0.add(k0); uq.append(it)
            uq.sort(key=lambda x: x.get("ts") or 0, reverse=True)
            for it in uq:
                it["lang"] = lang
            return {"ok": True, "items": uq[:per], "now": int(time.time()), "lang": lang}
    sym_items = _rss_items(
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US"
        % urllib.parse.quote(sym))
    if name:
        # Sembol akisi da alakasiz baslik getirebiliyor -> sirket adi gecmeyeni ele
        w1 = [w for w in re.split(r"[^A-Za-zÅÄÖåäö0-9]+", name) if len(w) >= 4]
        k1 = max(w1, key=len).lower() if w1 else name.lower()
        base_sym = re.split(r"[.\-]", sym)[0].lower()
        sym_items = [it for it in sym_items
                     if k1 in it["title"].lower() or base_sym in it["title"].lower()]
    items += sym_items
    if name:
        # Isim bazli aramalar (Yahoo search + Google News) alakasiz sonuc dondurebiliyor
        # -> basligin icinde sirket adi gecmeyenleri ele
        words = [w for w in re.split(r"[^A-Za-zÅÄÖåäö0-9]+", name) if len(w) >= 4]
        key = max(words, key=len).lower() if words else name.lower()
        cand = _yahoo_search_news(name) if lang == "en" else []
        hl, gl, ceid = GNEWS_LOCALE.get(lang, GNEWS_LOCALE["en"])
        q = '"' + name + '"' + (" stock" if lang == "en" else "")
        cand += _rss_items(
            "https://news.google.com/rss/search?q=%s&hl=%s&gl=%s&ceid=%s"
            % (urllib.parse.quote(q), hl, gl, urllib.parse.quote(ceid)), limit=12)
        items += [it for it in cand if key in it["title"].lower()]
    seen, uniq = set(), []
    for it in items:
        k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    uniq.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    for it in uniq:
        it["lang"] = "en"
        if lang != "en":
            it["fallback"] = True
    return {"ok": True, "items": uniq[:per], "now": int(time.time()),
            "lang": "en", "fallback": (lang != "en")}

POS_WORDS = [
    "beat","beats","surge","surges","soar","soars","jump","jumps","rally","rallies","record",
    "upgrade","upgrades","raises","raised","boost","boosts","strong","growth","profit","wins",
    "win","approval","approved","expands","expansion","buyback","dividend increase","outperform",
    "higher","gains","gain","top","tops","bullish","optimistic","recovery","deal","acquires",
]
NEG_WORDS = [
    "miss","misses","plunge","plunges","slump","slumps","falls","fall","drop","drops","sinks",
    "downgrade","downgrades","cuts","cut","warns","warning","weak","loss","losses","lawsuit",
    "probe","investigation","recall","delay","delays","halt","halts","layoff","layoffs","fine",
    "fraud","bearish","concern","concerns","slashes","lower","decline","declines","bankruptcy",
    "resign","resigns","strike","short seller",
]
TOPIC_WORDS = {
    "earnings": ["earnings","quarter","q1","q2","q3","q4","results","revenue","eps","guidance"],
    "analyst":  ["analyst","upgrade","downgrade","price target","rating","initiated"],
    "dividend": ["dividend","buyback","payout","distribution"],
    "mna":      ["acquire","acquisition","merger","stake","takeover","bid","deal"],
    "legal":    ["lawsuit","probe","investigation","court","fine","settlement","regulator"],
    "product":  ["launch","product","contract","order","partnership","approval","trial"],
}

def score_headline(title):
    tl = " " + title.lower() + " "
    pos = sum(1 for w in POS_WORDS if (" " + w + " ") in tl or (" " + w + ",") in tl)
    neg = sum(1 for w in NEG_WORDS if (" " + w + " ") in tl or (" " + w + ",") in tl)
    topics = [k for k, ws in TOPIC_WORDS.items() if any(w in tl for w in ws)]
    if pos > neg:
        tone = "pos"
    elif neg > pos:
        tone = "neg"
    else:
        tone = "neu"
    return {"tone": tone, "score": pos - neg, "topics": topics}

def fetch_newsfeed(syms, per=4, lang="en"):
    pairs = []
    for s in syms[:14]:
        if not s:
            continue
        if "~" in s:
            a1, b1 = s.split("~", 1)
            pairs.append((a1.strip(), b1.strip()))
        else:
            pairs.append((s.strip(), None))
    syms = [p[0] for p in pairs]
    names = dict(pairs)
    out = {}
    lock = threading.Lock()

    def one(s):
        try:
            r = fetch_news(s, names.get(s), per=per + 2, lang=lang)
            items = []
            for it in (r.get("items") or [])[:per]:
                sc = score_headline(it.get("title") or "")
                it = dict(it); it.update(sc); it["symbol"] = s
                items.append(it)
            with lock:
                out[s] = items
        except Exception:
            with lock:
                out[s] = []

    ths = [threading.Thread(target=one, args=(s,)) for s in syms]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=12)

    flat = []
    for s in syms:
        flat.extend(out.get(s) or [])
    pos = sum(1 for x in flat if x["tone"] == "pos")
    neg = sum(1 for x in flat if x["tone"] == "neg")
    topc = {}
    for x in flat:
        for tp in x["topics"]:
            topc[tp] = topc.get(tp, 0) + 1
    flat.sort(key=lambda x: x.get("ts") or 0, reverse=True)
    return {"ok": True, "items": flat, "count": len(flat), "now": int(time.time()),
            "pos": pos, "neg": neg, "neu": len(flat) - pos - neg,
            "topics": sorted(topc.items(), key=lambda kv: -kv[1])}

# ================= SEKTOR ROTASYONU (11 SPDR sektoru, cok periyotlu) =================
SECTOR_ETFS = [
    ("XLK", "tech",    "cyc"), ("XLF", "fin",     "cyc"), ("XLY", "cons",  "cyc"),
    ("XLI", "ind",     "cyc"), ("XLB", "mat",     "cyc"), ("XLE", "energy","cyc"),
    ("XLC", "comm",    "cyc"), ("XLV", "health",  "def"), ("XLP", "staple","def"),
    ("XLU", "util",    "def"), ("XLRE", "reit",   "def"),
]

def _sector_one(sym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=6mo"
           % urllib.parse.quote(sym))
    res = json.load(_open(url))["chart"]["result"][0]
    cl = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
          if c is not None]
    if len(cl) < 30:
        raise ValueError("yetersiz")
    p_now = cl[-1]
    def ret(n):
        return round((p_now - cl[-1 - n]) / cl[-1 - n] * 100, 2) if len(cl) > n else None
    ma50 = sum(cl[-50:]) / 50 if len(cl) >= 50 else None
    return {"symbol": sym, "price": round(p_now, 2),
            "d1": ret(1), "w1": ret(5), "m1": ret(21), "m3": ret(63),
            "aboveMa50": (p_now > ma50) if ma50 else None}

def fetch_sectors():
    out, lock = {}, threading.Lock()
    def go(sym):
        try:
            r = _sector_one(sym)
        except Exception:
            r = None
        with lock:
            out[sym] = r
    ths = [threading.Thread(target=go, args=(s[0],)) for s in SECTOR_ETFS]
    for t_ in ths:
        t_.start()
    for t_ in ths:
        t_.join(timeout=14)
    rows = []
    for sym, key, grp in SECTOR_ETFS:
        r = out.get(sym)
        if r:
            r["key"] = key; r["group"] = grp
            rows.append(r)
    def avg(grp, field):
        vals = [r[field] for r in rows if r["group"] == grp and r.get(field) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {"ok": True, "rows": rows,
            "cyc": {f: avg("cyc", f) for f in ("d1", "w1", "m1", "m3")},
            "def": {f: avg("def", f) for f in ("d1", "w1", "m1", "m3")},
            "breadth": {f: sum(1 for r in rows if (r.get(f) or 0) > 0) for f in ("d1", "w1", "m1")},
            "count": len(rows)}

# ================= PORTFOY vs ENDEKS KARSILASTIRMASI (/api/perf) =================
def _daily_closes(sym, rng="1y"):
    """gun -> kapanis sozlugu (YYYY-MM-DD)."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=%s"
           % (urllib.parse.quote(sym), rng))
    res = json.load(_open(url))["chart"]["result"][0]
    ts = res.get("timestamp") or []
    cl = res.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    cur = (res.get("meta") or {}).get("currency") or "SEK"
    out = {}
    last = None
    for i in range(min(len(ts), len(cl))):
        v = cl[i]
        if v is None:
            v = last
        if v is None:
            continue
        last = v
        out[time.strftime("%Y-%m-%d", time.gmtime(ts[i]))] = v
    return out, cur

def fetch_perf(holdings, rng="1y", bench=None):
    """holdings: [(sembol, adet), ...] -> gunluk SEK portfoy degeri, endekslerle normalize."""
    bench = bench or ["^GSPC", "^IXIC", "^OMX"]
    rng = rng if rng in ("1mo", "3mo", "6mo", "1y", "2y", "5y") else "1y"
    need_fx = set()
    series, lock = {}, threading.Lock()

    def grab(sym, key):
        try:
            s, cur = _daily_closes(sym, rng)
        except Exception:
            s, cur = {}, "SEK"
        with lock:
            series[key] = {"data": s, "ccy": cur}
            if cur and cur != "SEK":
                need_fx.add(cur)

    ths = [threading.Thread(target=grab, args=(s, s)) for s, q in holdings]
    ths += [threading.Thread(target=grab, args=(b, b)) for b in bench]
    for t_ in ths:
        t_.start()
    for t_ in ths:
        t_.join(timeout=20)

    # gerekli kurlarin gunluk gecmisi (dogru cevrim icin)
    fx = {}
    fxth = []
    def grabfx(c):
        try:
            s, _ = _daily_closes("%sSEK=X" % c, rng)
        except Exception:
            s = {}
        with lock:
            fx[c] = s
    for c in list(need_fx):
        th = threading.Thread(target=grabfx, args=(c,)); fxth.append(th); th.start()
    for th in fxth:
        th.join(timeout=15)

    def rate(ccy, day, fallback):
        if ccy == "SEK":
            return 1.0
        s = fx.get(ccy) or {}
        if day in s:
            return s[day]
        return fallback.get(ccy) or 1.0

    # son bilinen kurlar (bosluklar icin)
    lastfx = {}
    for c, s in fx.items():
        if s:
            lastfx[c] = s[sorted(s.keys())[-1]]

    # portfoyun islem gunleri: en cok veri iceren varligin gunleri
    hold_keys = [s for s, q in holdings if series.get(s, {}).get("data")]
    if not hold_keys:
        return {"ok": False, "error": "veri yok"}
    days = sorted(set().union(*[set(series[k]["data"].keys()) for k in hold_keys]))
    if len(days) < 5:
        return {"ok": False, "error": "yetersiz veri"}

    qty = {s: q for s, q in holdings}
    # Her varligi tum gunlere yay: eksik gunde son bilinen fiyat, basta ise ilk bilinen fiyat.
    # (Fonlar gunluk fiyat vermez; doldurmazsak toplam yapay olarak duser.)
    filled = {}
    for k in hold_keys:
        s = series[k]["data"]
        ks = sorted(s.keys())
        if not ks:
            continue
        first_v = s[ks[0]]
        col, last_v = [], None
        for day in days:
            if day in s:
                last_v = s[day]
            col.append(last_v if last_v is not None else first_v)
        filled[k] = col

    port = []
    for i, day in enumerate(days):
        tot = 0.0
        for k, col in filled.items():
            tot += col[i] * qty.get(k, 0) * rate(series[k]["ccy"], day, lastfx)
        port.append(tot)
    if len(port) < 5 or not port[0]:
        return {"ok": False, "error": "yetersiz veri"}

    def norm(vals, base):
        return [round((v - base) / base * 100, 2) for v in vals]

    lines = [{"key": "portfolio", "name": "portfolio",
              "points": norm(port, port[0]),
              "ret": round((port[-1] - port[0]) / port[0] * 100, 2)}]
    for b in bench:
        s = series.get(b, {}).get("data") or {}
        vals, last = [], None
        for day in days:
            if day in s:
                last = s[day]
            vals.append(last)
        if vals and vals[0]:
            vv = [v if v is not None else vals[0] for v in vals]
            lines.append({"key": b, "name": b, "points": norm(vv, vv[0]),
                          "ret": round((vv[-1] - vv[0]) / vv[0] * 100, 2)})
    # grafik icin seyrelt
    n = len(days)
    if n > 160:
        step = n / 160.0
        idx = sorted(set(int(i * step) for i in range(160)) | {n - 1})
        days = [days[i] for i in idx]
        for L in lines:
            L["points"] = [L["points"][i] for i in idx]
    return {"ok": True, "range": rng, "days": days, "lines": lines,
            "startValue": round(port[0]), "endValue": round(port[-1]),
            "count": len(hold_keys)}

EX_COUNTRY = {
    "Stockholm": "🇸🇪", "Oslo": "🇳🇴", "Helsinki": "🇫🇮", "Copenhagen": "🇩🇰",
    "NYSE": "🇺🇸", "NasdaqGS": "🇺🇸", "NASDAQ": "🇺🇸", "NYSEArca": "🇺🇸", "NYSE American": "🇺🇸",
    "Toronto": "🇨🇦", "London": "🇬🇧", "Frankfurt": "🇩🇪", "XETRA": "🇩🇪", "Milan": "🇮🇹",
    "Amsterdam": "🇳🇱", "Paris": "🇫🇷", "Singapore": "🇸🇬",
}

TYPE_LABEL = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "fund", "INDEX": "index"}

def fetch_search(q):
    """Hisse + ETF + fon + endeks arar (once genel, sonra fonlara ozel ikinci tur)."""
    def hit(url):
        try:
            return json.load(_open(url)).get("quotes", []) or []
        except Exception:
            return []
    base = "https://query1.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=%d&newsCount=0"
    quotes = hit(base % (urllib.parse.quote(q), 14))
    # Yahoo genel aramada fonlari geri plana atiyor -> fon/ETF icin ikinci tur
    if not any((x.get("quoteType") in ("MUTUALFUND", "ETF")) for x in quotes):
        quotes += hit((base % (urllib.parse.quote(q), 10)) + "&quotesQueryId=tss_match_phrase_query")
    out, seen = [], set()
    for x in quotes:
        qt = x.get("quoteType")
        if qt not in TYPE_LABEL:
            continue
        sym = x.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        exch = x.get("exchDisp") or ""
        out.append({"symbol": sym,
                    "name": x.get("shortname") or x.get("longname") or "",
                    "exchange": exch,
                    "type": TYPE_LABEL[qt],
                    "country": EX_COUNTRY.get(exch, "🏳️")})
    order = {"stock": 0, "etf": 1, "fund": 2, "index": 3}
    out.sort(key=lambda r: order.get(r["type"], 9))
    out = out[:12]
    # Nordic fonlarda Yahoo isim vermiyor (sadece 0P... kodu) -> chart meta'dan cek
    missing = [r for r in out if not r["name"] or r["name"] == r["symbol"]]
    if missing:
        lock = threading.Lock()
        def name_of(r):
            try:
                u = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1d"
                     % urllib.parse.quote(r["symbol"]))
                m = json.load(_open(u))["chart"]["result"][0]["meta"]
                nm = m.get("longName") or m.get("shortName")
                cur = m.get("currency")
                with lock:
                    if nm:
                        r["name"] = nm
                    if cur:
                        r["currency"] = cur
            except Exception:
                pass
        ths = [threading.Thread(target=name_of, args=(r,)) for r in missing[:8]]
        for t_ in ths:
            t_.start()
        for t_ in ths:
            t_.join(timeout=8)
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
            if p.path == "/api/newsfeed":
                try:
                    ss = [x.strip() for x in (qs.get("s") or [""])[0].split(",") if x.strip()]
                    lg = (qs.get("lang") or ["en"])[0]
                    return _json(self, fetch_newsfeed(ss, lang=lg))
                except Exception as e:
                    return _json(self, {"ok": False, "error": str(e)})
            if p.path == "/api/perf":
                try:
                    raw = (qs.get("h") or [""])[0]
                    hold = []
                    for part in raw.split(","):
                        if "~" not in part:
                            continue
                        sy, q = part.split("~", 1)
                        try:
                            qn = float(q)
                        except Exception:
                            continue
                        if sy.strip() and qn > 0:
                            hold.append((sy.strip(), qn))
                    if not hold:
                        return _json(self, {"ok": False, "error": "varlik yok"})
                    bch = [x for x in (qs.get("b") or ["^GSPC,^IXIC,^OMX"])[0].split(",") if x]
                    return _json(self, fetch_perf(hold[:60], (qs.get("range") or ["1y"])[0], bch))
                except Exception as e:
                    return _json(self, {"ok": False, "error": str(e)})
            if p.path == "/api/sectors":
                try:
                    return _json(self, fetch_sectors())
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
                    return _json(self, fetch_news(sym, (qs.get("name") or [""])[0] or None,
                                                  lang=(qs.get("lang") or ["en"])[0]))
                if p.path == "/api/tech":
                    return _json(self, fetch_tech(sym))
                if p.path == "/api/canslim":
                    return _json(self, fetch_canslim(sym))
                if p.path == "/api/deep":
                    return _json(self, fetch_deep(sym))
                if p.path == "/api/fundamentals":
                    return _json(self, fetch_fundamentals(sym))
                if p.path == "/api/ta":
                    return _json(self, fetch_ta(sym, (qs.get("range") or ["6mo"])[0],
                                                (qs.get("iv") or ["1d"])[0],
                                                (qs.get("bars") or ["70"])[0]))
                if p.path == "/api/levels":
                    return _json(self, fetch_levels(sym))
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
