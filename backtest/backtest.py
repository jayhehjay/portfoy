#!/usr/bin/env python3
"""Tweet'teki tarzda (trailing-stop'lu, yüksek win-rate'li) altın scalper simülasyonu.
Konservatif: aynı mumda hem stop hem trail değerse ZARAR varsayılır. Sadece stdlib."""
import csv, sys, statistics as st
from datetime import datetime, timezone

LOT, OUNCE = 0.5, 100          # 0.5 lot, 1 lot = 100 oz
COMM = 7.0                     # $/lot (round-turn)
HARD_SL = 6.0                  # $ başlangıç stop mesafesi
ARM, TRAIL = 0.7, 0.5          # kâr >= ARM olunca trailing, en iyi fiyattan TRAIL geride
START = 5000.0

def ema(v, n):
    k, e, o = 2/(n+1), v[0], []
    for x in v: e = x*k + e*(1-k); o.append(e)
    return o

def run(rows, spread, hard_sl=HARD_SL):
    c = [r[4] for r in rows]; e20, e50 = ema(c, 20), ema(c, 50)
    trades, pos = [], None
    for i in range(60, len(rows)):
        _, o, h, l, cl = rows[i]
        if pos:
            d, ent, best = pos["d"], pos["ent"], pos["best"]
            # Look-ahead YOK: trailing seviyesi yalnızca ÖNCEKİ mumlara göre belirlenir,
            # bu mumun en iyi fiyatı bir sonraki mumda kullanılır. Stop önce kontrol edilir.
            if d == -1:   # sell
                hit_sl = h >= pos["sl"]
                armed = (ent - best) >= ARM
                lvl = best + TRAIL
                exitp = pos["sl"] if hit_sl else (max(lvl, o) if armed and h >= lvl else None)
                best = min(best, l)
            else:
                hit_sl = l <= pos["sl"]
                armed = (best - ent) >= ARM
                lvl = best - TRAIL
                exitp = pos["sl"] if hit_sl else (min(lvl, o) if armed and l <= lvl else None)
                best = max(best, h)
            pos["best"] = best
            if exitp is not None:
                gross = (exitp - ent) * d * LOT * OUNCE
                cost = spread * LOT * OUNCE + COMM * LOT
                trades.append((rows[i][0], gross - cost, hit_sl)); pos = None
        if not pos:
            long_ok = e20[i] > e50[i] and c[i-1] < e20[i-1] and cl > e20[i]
            short_ok = e20[i] < e50[i] and c[i-1] > e20[i-1] and cl < e20[i]
            if long_ok or short_ok:
                d = 1 if long_ok else -1
                pos = {"d": d, "ent": cl, "best": cl, "sl": cl - d*hard_sl}
    return trades

def report(name, t):
    if not t: print(name, "işlem yok"); return
    pn = [x[1] for x in t]; w = [x for x in pn if x > 0]; ls = [x for x in pn if x <= 0]
    eq, peak, dd = START, START, 0
    for x in pn: eq += x; peak = max(peak, eq); dd = max(dd, peak-eq)
    stops = sum(1 for x in t if x[2])
    pf = sum(w)/abs(sum(ls)) if ls and sum(ls) else float("inf")
    print(f"{name:28s} n={len(t):3d} win={len(w)/len(t)*100:5.1f}% stop={stops:2d} "
          f"ort.kazanç={st.mean(w) if w else 0:6.1f} ort.kayıp={st.mean(ls) if ls else 0:7.1f} "
          f"net={sum(pn):8.0f} PF={pf:4.2f} maxDD={dd:6.0f} ({dd/START*100:4.1f}%)")

if __name__ == "__main__":
    rows = [(int(r[0]), *map(float, r[1:])) for r in list(csv.reader(open(sys.argv[1])))[1:]]
    a, b = (datetime.fromtimestamp(rows[i][0], timezone.utc).date() for i in (0, -1))
    print(f"Veri: {a} -> {b}, {len(rows)} mum, hesap {START:.0f}$, {LOT} lot\n")
    for sp in (0.0, 0.3, 0.6): report(f"spread {sp:.1f}$", run(rows, sp))
    print()
    for sl in (3, 6, 12, 25): report(f"spread 0.3$, stop {sl}$", run(rows, 0.3, sl))
