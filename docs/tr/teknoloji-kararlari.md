# Teknoloji Kararları

Bu belge, `smart-stock-mcp` projesinde hangi teknolojinin **neden** seçildiğini ve
**nasıl** kullanıldığını anlatır. Ne kullanıldığı ve nasıl çalıştırıldığı için kök
dizindeki `README.md` dosyasına bakın; bu belge yalnızca gerekçeleri kaydeder.

Amaç, kararların sonucunu değil sebebini korumaktır: bir seçim ileride
değiştirilecekse, önce hangi kısıtı karşılamak için yapıldığının bilinmesi gerekir.

---

## Model Context Protocol

**Neden.** Modelin çağırabileceği yeteneklerin, onu çağıran koddan ayrı
tanımlanabilmesi için. Doğrudan fonksiyon çağrısı yazılsaydı her yeni yetenek host
kodunun değişmesini gerektirirdi. Protokolde araç kendi şemasını bildirir, host
yalnızca kataloğu okur.

**Nasıl.** İki ayrı sunucu: `stock-mcp` envanter işlemlerini, `marketplace-mcp`
teklif ve sipariş işlemlerini sunar. Toplam 22 araç. Taşıma stdio üzerinden JSON-RPC.

**Neden iki sunucu.** Tek sunucuda birleştirilebilirdi. Ayrı tutulması, envanter
tarafının pazar yeri tarafından bağımsız yetkilendirilmesini ve ayrı ölçeklenmesini
mümkün kılıyor.

> **Kritik kısıt:** Sunucunun standart çıktısı mesaj kanalının kendisidir. Oraya
> yazılan bir `print` protokolü bozar ve cevapları kaybettirir. Bu bir kez yaşandı;
> loglar artık stderr'e gidiyor ve `test_mcp_stdout.py` içindeki AST denetimi kaynak
> kodda düz `print` bulursa test paketi düşüyor.

---

## Yerel çıkarım — Ollama + qwen3:8b

**Neden.** Sistem kurum ağı içinde çalışacak; stok seviyeleri, tedarikçi fiyatları ve
sipariş kayıtları bu ağın dışına çıkmamalı. Bir bulut API'si bu kısıtı karşılamıyor.

**Nasıl.** `/api/generate`, `think:false`. `num_ctx` ve `num_predict` ortam
değişkenleriyle ayarlanabilir; model `OLLAMA_MODEL` ile değiştirilebilir.

**Bedeli.** Grafik işlemcisi olmayan makinede istek başına 15–320 saniye. Bu, arayüz
tarafında iptal edilebilir istek ve anında görünen mesaj gibi telafiler gerektirdi;
ayrıca salt okunur istekler için modeli hiç çağırmayan deterministik hızlı yollar
eklendi.

**Ölçülen.** Bağlam penceresi ölçülmeden bırakılmıştı ve prompt varsayılan pencereyi
aşıyordu. Pencere aşıldığında prompt **baştan** kesiliyor, yani önce araç açıklamaları
siliniyordu. Pencere açıkça ayarlandı; her istekte belirteç sayısı raporlanıyor.

---

## Plan üretimi — tek belge, iteratif araç çağrısı yerine

**Neden.** Taslaktaki komutların çoğu birden fazla aracın sırayla kullanılmasını
gerektiriyor. Adım adım ilerleyen bir döngüde her adım ayrı bir çıkarımdır; yerel
modelde bu, tek komutun dakikalarca sürmesi demektir. Daha önemlisi: planın bütünü
önceden görülemezse **çalıştırılmadan doğrulanamaz**.

**Nasıl.** Model bir JSON belgesi üretir (hedef + sıralı adımlar + argümanlar). Host
belgeyi ayrıştırır, hedefe göre şeklini doğrular, durum kapılarından geçirir ve ancak
sonra çalıştırır. Adımlar birbirini `$from` (önceki adımın sonucu) ve `$from_context`
(konuşma durumundaki değer) ile besler; `$transform` biçim dönüşümü yapar.

```json
{
  "type": "execution_plan",
  "goal": "PLAN",
  "steps": [
    { "id": "step_1", "tool": "list_low_stock", "arguments": {} },
    { "id": "step_2", "tool": "create_procurement_plan",
      "arguments": {
        "items": { "$from": "step_1.products",
                   "$transform": "low_stock_products_to_items" },
        "objective": "CHEAPEST",
        "filters": { "max_total_budget": 50000 }
      } }
  ]
}
```

**Onarım.** Plan ayrıştırılamaz ya da bir adım başarısız olursa hata, özgün istekle
birlikte modele geri verilir ve **bir kez** düzeltme denenir. Sınırsız deneme olmayan
veriyi var edemeyeceği için kullanıcıyı boşuna bekletirdi.

**Ölçülen.** Çıktı sınırı 256 belirteçken çok adımlı planlar JSON'un ortasında
kesiliyordu (`eval_count == num_predict`, ardından ayrıştırma hatası). Sınır 1024'e
çıkarıldı; başarılı planlar 72–124 belirteç kullanıyor, yani bu bir tavan, maliyet değil.

### Şekil hatasını düzelt, niyet hatasını düzeltme

`order_ids=1` değerini `[1]` yapmak bilgi kaybetmez — kullanıcının kastettiği tek
öğeli listedir. Buna karşılık yanlış araca yazılmış bir `category` filtresini sessizce
silmek kullanıcının verdiği kısıtı kaybettirir ve doğru görünen yanlış bir cevap
üretir. İkincisi onarım döngüsüne bırakılır. Bu ayrım `test_argument_coercion.py`
içinde iki testle sabitlenmiştir.

---

## Onay kapısı — promptta değil, kodda

**Neden.** Model kurala çoğu zaman uyar. Stok kaydını değiştiren ve sipariş veren bir
işlem için "çoğu zaman" yeterli bir güvence değildir.

**Nasıl.** `validate_plan_against_state`, onay bekleyen bir taslak yokken
`place_order` içeren planı, onaylanmış teslimat listesi yokken `receive_orders` içeren
planı **çalıştırmadan** reddeder. Promptdaki kural ikinci savunma hattıdır, tek hat değil.

İki akış da iki turludur:

- **Satın alma:** DRAFT → `pending_draft_id` → onay → ORDER
- **Teslim alma:** listeleme → `pending_receive_ids` → onay → `receive_orders`

Buna ek olarak backend, beklenen teslimat tarihi gelmemiş bir siparişin stoğa
alınmasını `DeliveryNotReadyException` ile reddeder.

---

## Backend — Spring Boot 3.3 / Java 21 / PostgreSQL / Flyway

**Neden.** İş kurallarının modelden ve protokolden bağımsız, tipli ve test edilebilir
bir yerde durması gerekiyordu. Stok hareketleri işlem bütünlüğü ister; bunun yeri
ilişkisel bir veritabanı ve işlem yöneten bir servistir.

**Nasıl.** Katmanlı yapı (controller / service / repository / entity), JPA,
`spring-boot-starter-validation`, üretimde Flyway ile sürümlenmiş şema göçleri.

**Kural.** İkmal miktarı `hedef − (mevcut + yolda)`. Yolda olan malın düşülmesi, aynı
eksiğin ikinci kez sipariş edilmesini engeller.

**Üç profil.** Varsayılan (`ddl-auto: update`, demo verisi) · `acceptance` (ayrı
veritabanı, her açılışta şema baştan) · `production` (Flyway, `ddl-auto: validate`,
seed kapalı).

> `DB_URL` ortam değişkeni profil varsayılanını ezer. Kabul profilini elle
> çalıştırırken bu istenen davranıştır; demo çalıştırırken değildir.

---

## TOPSIS ile teklif sıralaması

**Neden.** Fiyat, teslimat süresi ve satıcı puanı birbiriyle çelişir. Tek alana göre
sıralamak yanlış karar üretir: en ucuz teklif, teslimatı iki hafta süren ve puanı
düşük bir satıcıdan geliyorsa en iyi teklif değildir.

**Nasıl.** Karar matrisi normalleştirilir, ağırlıklar uygulanır, ideal ve negatif ideal
çözümler belirlenir, her teklif bu iki referansa uzaklığına göre puanlanır. Ağırlıklar:
**maliyet 0,4 · teslimat 0,3 · satıcı puanı 0,3**.

Dört hedef: `CHEAPEST`, `FASTEST`, `HIGHEST_RATED`, `BALANCED`. Kullanıcının bütçe,
asgari puan ve azami teslimat süresi kısıtları sıralamadan **önce** uygulanır.

**Aritmetik kodda.** İki plan karşılaştırılırken farklar kodda hesaplanır. Modelden
hesaplaması istendiğinde yanlış sonuçlar ürettiği gözlendi; muhakeme promptu artık
modelin aritmetik yapmasını yasaklar ve hazır değerleri kullanmasını ister.

---

## Kimlik, yetkilendirme ve oturum

**Neden.** Stok kaydını değiştiren ve sipariş veren bir sistem kimliği doğrulanmamış
erişime açık kalamaz. Harici bir kimlik sağlayıcı, kurum içi bir simülasyon için
gereksiz bağımlılık olurdu.

**Nasıl.** Parolalar scrypt ve kullanıcıya özel rastgele tuzla özetlenir; düz metin hiç
saklanmaz. Oturum kimlik bilgileri opak rastgele belirteçlerdir, veritabanında yalnızca
SHA-256 özetleri durur.

| Rol | Yetenekler |
|---|---|
| `VIEWER` | okuma |
| `OPERATOR` | okuma, taslak oluşturma |
| `MANAGER` | okuma, taslak, onaylama, reddetme |
| `ADMIN` | hepsi + taslak silme, kullanıcı yönetimi |

Rol **sunucu tarafında** oturumdan belirlenir; istemciden gelen role güvenilmez.

**İnce nokta.** Yetkilendirme araç çağrısından *önce* uygulanır ve araç kataloğu role
göre süzülür. Sipariş verme yetkisi olmayan bir kullanıcı o aracı hiç görmez; böylece
model reddedilecek bir işlemi planlamaya kalkışmaz.

Ayrıntı: `docs/security.md`, `docs/task10-identity-rbac.md`,
`docs/task11-session-auth-hardening.md`.

---

## Arayüz — React 18 / Vite / TanStack Query

**Neden.** Panelin verisi sunucuda yaşıyor ve sürekli değişiyor. TanStack Query, sunucu
durumunu önbellekleme, tazeleme ve geçersiz kılma işini elle yazılmış `useEffect`
zincirlerinden devralır.

**Nasıl.** Operasyon paneli (stok, ikmal, siparişler, taslaklar) ve "AI İşlem Merkezi"
sohbet ekranı. Sohbet cevabıyla birlikte hangi aracın hangi argümanlarla çağrıldığını
ve ne döndüğünü gösteren bir **karar günlüğü** sunulur.

**Neden karar günlüğü.** Kullanıcı sistemin neden o kararı verdiğini göremezse otomatik
satın alma önerisine güvenemez. Günlük bir hata ayıklama aracı değil, güven aracıdır.

---

## Ölçüm altyapısı

Üç katman, üç farklı soruyu üç farklı hızda cevaplar.

| Araç | Ollama gerekir mi | Süre | Ne ölçer |
|---|---|---|---|
| `unittest discover` | Hayır | ~8 sn | Birim ve entegrasyon testleri |
| `golden_eval.py` | Hayır | Saniyeler | Hızlı yol, plan ayrıştırma, durum kapıları |
| `acceptance_runner.py` | Evet | 45–60 dk | Taslak komutları gerçek yığına karşı |

**Neden üç katman.** Canlı kabul koşumu bir saat sürer, dolayısıyla her değişiklikten
sonra çalıştırılamaz. Altın değerlendirme aynı davranış sözleşmesinin model çağırmayan
bölümünü saniyeler içinde denetler ve CI'da çalışır.

**Neden izole veritabanı.** Aynı kod, önceki manuel testlerin doldurduğu bir
veritabanında %33, temiz kabul veritabanında %59 verdi. İlk sayı kodu değil veri
durumunu ölçüyordu. Koşucu artık ön koşulları doğrular ve başlangıç durumu uygun
değilse koşmayı reddeder (`--allow-dirty-state` ile zorlanabilir, o zaman sonuç
"karşılaştırılamaz" damgası alır). Güncel referans **%70**; koşumların tarihçesi ve
karşılaştırması `docs/tr/olcum-gunlugu.md` dosyasında.

**AST denetimleri.** Bir kalıp iki kez tekrarlandığında denetim yazılmıştır: MCP
sunucularının stdout'a yazmaması (`test_mcp_stdout.py`) ve SQLite bağlantılarının
kapatılması (`test_store_connections.py`). İkincisi gerçek bir hataydı —
`with sqlite3.connect(...)` işlemi yönetir ama bağlantıyı kapatmaz.

---

## Dağıtım — Docker Compose, üç topoloji

**Neden.** Dört servisi elle ayağa kaldırmak sıra bağımlılığı hatalarına yol açıyordu.
Bağımlılıklar artık sabit bekleme süreleriyle değil sağlık kontrolleriyle ifade ediliyor.

| Topoloji | Dosya | Özellik |
|---|---|---|
| Geliştirme | `docker-compose.yml` | Tüm yığın, model otomatik indirilir |
| Kabul | `--profile acceptance` | Ayrı Postgres (5433), ayrı port (8082) |
| Üretim | `docker-compose.prod.yml` | Digest'e sabit imajlar, salt okunur konteynerler, `cap_drop: ALL`, üç ayrı ağ, Flyway |

Üretimde zorunlu ortam değişkenleri `${VAR:?...}` ile işaretlidir; eksikse yığın hiç
başlamaz. Ayrıntı: `docs/production.md`, `docs/release.md`.

### Aynı kural iki katmanda: kopyalanmamalı, türetilmeli

Yetkilendirme hem arka uçta (`rbac.py`, `secure_api.py`) hem arayüzde
(`draftPermissions.ts`) biliniyor. Arka uç kararı verir; arayüz yalnızca kullanıcıyı
yapamayacağı bir işleme yöneltmemek için aynı kuralı önden uygular. İki taraf
ayrışırsa iki hata tipi doğar: arayüz izin verip arka ucun reddetmesi (rahatsız edici
ama güvenli) ya da **arka ucun izin verdiğini arayüzün gizlemesi** (işlev kaybı).

İkincisi 02.09.2026'da yaşandı: anonim kipte sunucu rol atamaz, arayüz ise düğmeleri
role bakarak çiziyordu, dolayısıyla geliştirme varsayılanında taslak hiç
onaylanamıyordu. Arayüz denetimi artık kimlik kipini de hesaba katıyor.

Ders: arayüzdeki denetim arka ucun kuralını *kopyalamamalı*, aynı girdilerden
türetmelidir — rol tek başına yeterli girdi değildi, kip de gerekliydi.

### Ağ geçidi zaman aşımı model zaman aşımından büyük olmalı

Tarayıcı `llm-host` ile doğrudan konuşmaz; istek `web-ui` konteynerindeki nginx
üzerinden geçer. Bu iki sınır birlikte ayarlanmak zorundadır: `llm-host` Ollama'yı
`OLLAMA_READ_TIMEOUT` (300s) kadar bekler, nginx'in `proxy_read_timeout`
varsayılanı ise 60s'dir. Sınır ağ geçidinde daha küçük kalırsa, arka uç hâlâ
çalışırken bağlantı kesilir ve kullanıcı 504 alır; üstelik nginx'in hata gövdesi
HTML olduğundan arayüz arka ucun açıklamasını değil genel yedek iletiyi gösterir.

Bu durum 02.09.2026'da gerçekleşti: `nginx.prod.conf` dosyasında `proxy_read_timeout
330s` tanımlıyken geliştirme yapılandırması `nginx.conf` atlanmıştı. Aynı kuralın iki
dosyadan yalnızca birinde bulunması, ortam yapılandırmalarının ayrı ayrı elle
bakımının hataya açık olduğunu göstermektedir. Değer, model sınırının üzerinde
(330s > 300s) tutulur.
