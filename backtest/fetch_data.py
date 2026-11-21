#!/usr/bin/env python3
"""Yahoo Finance'ten altın vadeli (GC=F) mum verisi çeker -> CSV. Sadece stdlib."""
import urllib.request, json, csv, sys
interval, rng, out = (sys.argv + ["5m", "60d", "gold_5m.csv"][len(sys.argv)-1:])[1:4]
url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={interval}&range={rng}"
r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
d = r["chart"]["result"][0]; q = d["indicators"]["quote"][0]
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close"])
    n = 0
    for i, t in enumerate(d["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        w.writerow([t, q["open"][i], q["high"][i], q["low"][i], q["close"][i]]); n += 1
print(out, n, "bar")
