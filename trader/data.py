"""Yahoo Finance veri çekici (stdlib). Bar = (ts, open, high, low, close, volume)."""
import urllib.request, urllib.error, json, csv, os, time

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

def fetch(symbol, interval="1d", rng="10y", use_cache=True, max_age=3600):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol.replace('=','_').replace('^','')}_{interval}_{rng}.csv")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return load(path)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}"
    for attempt in range(6):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5: raise
            time.sleep(5 * (attempt + 1))
    d = r["chart"]["result"][0]; q = d["indicators"]["quote"][0]
    bars = []
    for i, t in enumerate(d["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c): continue
        bars.append((t, o, h, l, c, q["volume"][i] or 0))
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ts", "o", "h", "l", "c", "v"]); w.writerows(bars)
    return bars

def load(path):
    with open(path) as f:
        return [tuple(float(x) if j else int(float(x)) for j, x in enumerate(r)) for r in list(csv.reader(f))[1:]]
