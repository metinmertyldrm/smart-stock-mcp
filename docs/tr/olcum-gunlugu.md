# Ölçüm Günlüğü

Kabul koşumunun tarih sırasına göre kaydı. Her satır, hangi koşulda ne ölçüldüğünü
ve iki koşum arasında neyin değiştiğini gösterir.

Bir ölçüm sonucu, ancak elde edildiği koşullarla birlikte anlamlıdır: model,
veritabanının durumu ve kodun sürümü. Bu günlüğün varlık sebebi budur.

| Tarih | Koşul | Sonuç | Not |
|---|---|---|---|
| 20.08.2026 | Kirli veritabanı, `qwen3:8b` | 9/27 (%33) | **Geçersiz.** Önceki manuel testler stoğu doldurmuştu; düşen senaryoların çoğu kodla ilgili değildi |
| 27.08.2026 | Temiz kabul veritabanı, `qwen3:8b` | 16/27 (%59) | Tekrarlanabilir koşullardaki ilk gerçek ölçüm |
| 02.09.2026 | Temiz kabul veritabanı, `qwen3:8b` | **19/27 (%70)** | Kritik açıklar kapatıldıktan sonra |

Ölçüm, arayüz ve nginx ağ geçidi üzerinden değil, `acceptance_runner.py` içinden
plan hattı doğrudan çağrılarak alınır. Bu nedenle 02.09.2026'da bulunan ağ geçidi
zaman aşımı hatası (bkz. `teknoloji-kararlari.md`) yukarıdaki sayıları etkilememiştir;
düzeltmesi sonrasında ölçüm yeniden alınmasını gerektirmez.

Koşum komutu:

```powershell
docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance
$env:STOCK_SERVICE_URL = "http://localhost:8082"
cd llm-host
python acceptance_runner.py --runs 3 --include-writes
```

---

## 02.09.2026 — güncel referans

45 LLM çağrısı. Ollama yerel, grafik işlemcisi yok.

| Senaryo | Başarı | Plan çeşidi | Ortanca süre |
|---|---|---|---|
| Stokta olmayanlar için ekonomik plan | 3/3 | 1 | 1,3 sn |
| Kategori + satıcı puanı filtresi | 0/3 | 1 | 31,0 sn |
| Toplam bütçe sınırı | 3/3 | 1 | 32,0 sn |
| Azami teslimat süresi | 3/3 | 1 | 30,7 sn |
| Bekleyen siparişleri stoğa alma | 1/3 | 3 | 60,9 sn |
| Onay kapısı — yalnızca listeleme | 3/3 | 1 | 15,9 sn |
| Dengeli hedef | 3/3 | 1 | 32,0 sn |
| En ucuz ve en hızlı planı karşılaştır | 2/3 | 3 | 146,0 sn |
| Taslak → onay → sipariş | 1/3 | 2 | 161,3 sn |
| **Toplam** | **19/27 (%70)** | | |

### 27.08 ile karşılaştırma

| Senaryo | 27.08 | 02.09 | Sebep |
|---|---|---|---|
| Azami teslimat süresi | 2/3 | **3/3** | `filters` tip doğrulaması |
| En ucuz/en hızlı karşılaştırma | 0/3 | **2/3** | `num_predict` 256 → 1024 |
| Bekleyen siparişleri stoğa alma | 0/3 | 1/3 | `receive_orders` çift tanımı |
| Stokta olmayanlar için plan | 30,3 sn | **1,3 sn** | Deterministik hızlı yol |
| Kategori + puan filtresi | 0/3 | 0/3 | Hata değişti (aşağıda) |

### Güvenlik davranışları

Kritik olan her şey geçiyor:

- Onay kapısı 3/3 — model ilk turda stok değiştiren aracı çağırmıyor
- Uydurma teslimat tarihi backend'e ulaşmıyor (plan doğrulaması engelliyor)
- Yetkisiz yazma yok
- Başarısız senaryolar sessiz yanlış cevap değil, **açık ret** üretiyor

### Kalan başarısızlıkların sınıflandırması

Sekiz başarısızlığın hiçbiri mimari kusur değil.

| Sınıf | Senaryo | Belirti |
|---|---|---|
| Hedef sınıflandırma | Kategori + puan filtresi | Komut "…satın al" diyor, model `PLAN` üretiyor, `DRAFT` bekleniyor. Kategori süzgeci artık **çalışıyor**; bu, onun arkasında saklanan ayrı bir sorundu |
| Onarım turu tek hak | Taslak → onay → sipariş; stoğa alma | İlk plan reddediliyor, onarım planı da tutmuyor, istek `CLARIFY` ile bitiyor |
| Model JSON kalitesi | Üç ayrı koşum | `Expecting ',' delimiter`. Kesilme değil (çıktı 73–124 belirteç, sınır 1024), gerçek sözdizimi hatası |
| Zaman aşımı | Karşılaştırma #1 | Beş adımlı plan, onarım turunda 300 sn aşıldı, koşum 422 sn sürdü |

Plan çeşitliliği iki senaryoda 3'e çıktı (önce 1–2 idi). Prompt 3.100 → 3.400 belirtece
büyüdü; küçük modelde bunun bedeli var. İzlenmesi gereken gösterge budur.

---

## 27.08.2026 — ilk geçerli ölçüm

16/27 (%59), 46 LLM çağrısı. Düşen beş senaryonun tamamı aynı aileden geliyordu:
model argümanı yanlış yere ya da yanlış biçimde yazıyor, MCP şeması reddediyor.

| Modelin ürettiği | Şemanın verdiği hata |
|---|---|
| `list_low_stock(category=…)` | `'category' was unexpected` |
| `calculate_replenishment(filters=…)` | `'filters' was unexpected` |
| `receive_orders(order_ids=1)` | `1 is not of type 'array'` |
| `compare_offers(product_id=[2,5], quantity=[])` | `[] is not of type 'integer'` |
| `expected_delivery_date='2023-10-15'` | geçmiş tarih reddedildi |

Bu ölçümden sonra yapılanlar: `category` argümanı listeleme araçlarına eklendi,
eşleşmeyen kategori açıklama üretiyor, ORDER zinciri plan doğrulamasıyla korundu,
boş istege bağlı argümanlar temizleniyor, `num_predict` yükseltildi, tekil koleksiyon
değeri listeye sarılıyor.

---

## 20.08.2026 — geçersiz ölçüm

9/27 (%33). Bu sayı kodu değil veri durumunu ölçüyordu: önceki manuel testler bütün
siparişleri stoğa almıştı, kritik ürün kalmamıştı, dolayısıyla plan üreten senaryolar
"sipariş edilecek ürün yok" ile düşüyordu.

**Alınan ders.** Kabul koşucusu artık koşum öncesi veri ön koşullarını doğruluyor ve
başlangıç durumu senaryoları ölçmeye uygun değilse koşmayı reddediyor. Zorlamak için
`--allow-dirty-state` var; o zaman sonuç "karşılaştırılamaz" damgası alıyor.
