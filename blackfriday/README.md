# 🖤 Black Friday Tracker — İsveç mağazalarında fiyat takibi

Bir ürün yazarsınız (örn. **airpods 4**), İsveç'teki büyük elektronik mağazaları aynı anda taranır,
bulunan fiyatlar **en ucuzdan en pahalıya** sıralanır. Takibe aldığınız ürünler **her 6 saatte bir**
yeniden taranır; fiyat düşüşleri ve tüm zamanların en düşük fiyatı **Telegram** ya da **ntfy.sh**
ile telefonunuza bildirilir. Ek paket gerekmez: yalnızca Python standart kütüphanesi.

## Mağazalar

Elgiganten · NetOnNet · Webhallen · Komplett · MediaMarkt · Inet · Power · Kjell & Company ·
Clas Ohlson · CDON · Amazon.se · Proshop · Dustin Home · Elon · Teknikmagasinet · HiFi Klubben ·
Computersalg · Apple Store · **Prisjakt** ve **PriceRunner** (pazardaki en düşük fiyat)

Kendi mağazanızı eklemek için `blackfriday/stores.json` oluşturun:

```json
{"minbutik": {"name": "Min Butik", "search": "https://www.minbutik.se/sok?q={q}"}}
```

## Ürüne özel arama

* **Sadece bu model**: `airpods 4` yazınca yalnızca AirPods 4 gelir; AirPods Pro / Max ya da
  3. nesil gelmez. `iphone 17` yazınca Pro / Pro Max gizlenir. (`iphone 17 pro` yazarsanız Pro gelir.)
* **Aksesuarlar gizli**: fodral, skal, kabel, laddare, handkontroll, tillbehör… listeye girmez.
  İsterseniz "Aksesuarları da göster" kutusunu açın.
* **Dışlama**: `airpods 4 -anc` → başlığında "anc" geçenleri atar.
* **Hazır seçenekler**: 8 kategoride 89 popüler ürün (`products.json`) tek tıkla aranır; listeyi
  dilediğiniz gibi genişletin.
* **Ürün linki**: belirli bir mağazadaki tek bir ürün sayfasını da takibe alabilirsiniz.

## Çalıştırma

```bash
cd blackfriday
python server.py            # http://localhost:8010
```

Bildirim için sunucuyu ortam değişkenleriyle başlatın:

```bash
# Telegram: @BotFather'dan bot oluşturun, bota /start yazın, chat id'nizi @userinfobot'tan alın
TELEGRAM_TOKEN=123:abc TELEGRAM_CHAT_ID=987654 python server.py

# ya da ntfy.sh (bot kurulumu gerekmez: telefona ntfy uygulamasını kurup aynı konuya abone olun)
BF_NTFY_TOPIC=benim-gizli-konum-42 python server.py
```

| Değişken | Anlamı | Varsayılan |
|---|---|---|
| `BF_INTERVAL_HOURS` | Tarama aralığı (saat) | `6` |
| `BF_NOTIFY` | `always`: her taramada özet · `changes`: sadece düşüş / yeni dip / hedef altı | `always` |
| `BF_STORES` | Sadece bu mağazalar: `elgiganten,netonnet,webhallen` | hepsi |
| `BF_STATE_FILE` | Takip listesi + fiyat geçmişi dosyası | `blackfriday/state.json` |
| `PORT` | Web arayüzü portu | `8010` |

Komut satırından:

```bash
python tracker.py --search "airpods 4"      # tek seferlik arama, tabloyu yazdırır
python tracker.py --once                    # takip listesini tara + bildir
python tracker.py --url https://www.elgiganten.se/product/...   # tek ürün fiyatı
python tracker.py --test-notify             # bildirim kanalını dene
```

## 6 saatte bir otomatik tarama

1. **Sunucu açık kalıyorsa** (kendi bilgisayarınız, bir VPS, Raspberry Pi): `server.py` arka planda
   kendi zamanlayıcısını çalıştırır; ilk tarama 1 dakika sonra, sonrakiler her 6 saatte bir.
2. **GitHub Actions ile ücretsiz** (bilgisayar kapalıyken de çalışır):
   `.github/workflows/blackfriday.yml` her 6 saatte bir `tracker.py --once` çalıştırır.
   * Depo ayarlarında **Secrets**: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (veya `BF_NTFY_TOPIC`).
   * Takip listesini `blackfriday/watchlist.json` dosyasına yazın (`watchlist.example.json`'a bakın)
     ve depoya ekleyin. Fiyat geçmişi çalıştırmalar arasında Actions cache ile saklanır.
   * Actions sekmesinden **Run workflow** ile hemen deneyebilirsiniz.
3. **Render / benzeri** bir sunucuda `python blackfriday/server.py` ile yayınlayabilirsiniz; ücretsiz
   planlar boşta uyuduğu için 6 saatlik döngü orada güvenilir değildir, bildirim için 2. seçeneği kullanın.

## API

| Uç | Açıklama |
|---|---|
| `GET /api/search?q=airpods+4&strict=1&accessories=0&force=1` | Canlı tarama; `force=1` 5 dakikalık önbelleği atlar |
| `GET /api/catalog` · `GET /api/stores` · `GET /api/state` | Hazır ürünler · mağazalar · takip listesi, son/sonraki tarama, bildirim günlüğü |
| `GET /api/history?q=…` veya `?id=…` | Fiyat geçmişi |
| `POST/DELETE /api/watch` `{q, maxPrice, strict}` | Arama takibi |
| `POST/DELETE /api/url` `{url, maxPrice}` | Ürün linki takibi |
| `POST /api/run` | Şimdi tara ve bildir |
| `POST /api/notify/test` | Bildirim kanalını test et |

## Nasıl çalışır?

Her mağazanın arama sayfası indirilir ve ürünler sırasıyla şu kaynaklardan okunur:
schema.org **JSON-LD** (`Product` / `ItemList`) → sayfaya gömülü **JSON durumu** (`__NEXT_DATA__`,
`__INITIAL_STATE__` vb.) → **HTML** (ürün bağlantısı + yanındaki "1 299 kr" metni). Webhallen için
doğrudan JSON API kullanılır. Eski fiyat (ordinarie pris) bulunursa indirim yüzdesi hesaplanır;
sayfada "Black Friday / Black Week" geçiyorsa mağaza işaretlenir.

Mağazalar sitelerini değiştirebilir ya da bot koruması uygulayabilir; böyle bir mağaza listede
"hata" olarak görünür, diğerleri çalışmaya devam eder. `stores.json` ile arama adresini düzeltmek
çoğu zaman yeterlidir.

## Testler

```bash
python -m unittest discover -s blackfriday/tests -v
```

Testler internete çıkmaz; örnek mağaza sayfaları `tests/fixtures/` altındadır.
