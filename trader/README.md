# Trader — sanal (paper) işlem botu

**Gerçek para / gerçek emir YOK.** Her şey simülasyondur.

| dosya | görev |
|---|---|
| `data.py` | Yahoo Finance'ten ücretsiz günlük/saatlik veri |
| `strategy.py` | Stratejiler: `donchian` (trend takibi), `pullback` (trendde dip alımı), `mr` (ortalamaya dönüş) |
| `engine.py` | Backtest **ve** paper trading'in ortak çekirdeği (sinyal kapanışta, emir sonraki açılışta; look-ahead yok) |
| `research.py` | Strateji × varlık araştırması; yalnızca iki yarı dönemde de kârlı olanlar "dayanıklı" sayılır |
| `portfolio.py` | Çok varlıklı portföy: `backtest` ve `paper --once` |
| `config.json` | Portföy: hangi sleeve, hangi varlık/strateji/parametre, risk ayarı |
| `REPORT.md`, `state/`, `journal.log` | Paper hesabın güncel durumu |

## Risk kuralları (config.json)
- İşlem başına hesabın %1,5'i riske edilir (ATR tabanlı stop).
- Pozisyon nominali ≤ sleeve sermayesi (kaldıraç yok).
- Zirveden %25 düşüşte sleeve durur (kill switch).

## Dürüst beklentiler (backtest, 2017–2026)
Portföy: ~%3,9 yıllık, en kötü düşüş ~%5,5, Sharpe ~0,95. Al-tut çok daha fazla kazandırdı; bu bot **düşük riskli, düşük getirili** bir sistemdir.
Geçmiş performans gelecek için garanti değildir. Gerçek paraya geçmeden önce en az 2–3 ay paper sonucu izlenmelidir.

## Çalıştırma
```
python3 portfolio.py backtest
python3 portfolio.py paper --once
```
Günlük otomasyon: `.github/workflows/paper-trader.yml` (main dalına alınınca çalışır).
