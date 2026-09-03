# Kurulum Rehberi

Projeyi ilk kez çalıştıracak biri için sıralı yol. İki seçenek var; Docker olan tercih
edilmelidir. Ayrıntılı işletim bilgisi ve İngilizce referans için kök dizindeki
`README.md` dosyasına bakın.

## Gereksinimler

| Bileşen | Sürüm | Ne için |
|---|---|---|
| Docker Desktop | Güncel | Önerilen kurulum yolu |
| Ollama | Güncel | Yerel dil modeli çıkarımı |
| Java (JDK) | 21 | Elle kurulumda backend |
| Python | 3.11+ | Elle kurulumda MCP ve host |
| Node.js | 20+ | Elle kurulumda arayüz |
| PostgreSQL | 17 | Elle kurulumda veritabanı |
| Disk | ~10 GB | Model (~5 GB) + imajlar |

Model dosyası büyüktür ve yavaş bağlantıda indirilmesi uzun sürer. İlk kurulumu zaman
ayırabileceğiniz bir aralıkta yapın.

---

## Yol A — Docker ile (önerilen)

```bash
git clone https://github.com/metinmertyldrm/smart-stock-mcp.git
cd smart-stock-mcp
cp .env.example .env          # PowerShell: copy .env.example .env
docker compose up -d
```

İlk çalıştırmada imajlar derlenir ve model indirilir; bu adım uzun sürebilir.
Servisler sağlık kontrolüne göre sıralanır — arayüz, backend ve model hazır olmadan
açılmaz.

```bash
docker compose ps             # hepsi "healthy" olmali
```

Arayüz: <http://localhost:5173>

> **Kısayol.** Ollama'yı makinenize zaten kurduysanız Compose'daki `ollama` servisini
> başlatmayın; aksi hâlde model ikinci kez indirilir. `OLLAMA_URL` değişkenini kendi
> örneğinize yöneltin.

---

## Yol B — Elle kurulum

Geliştirme yaparken katmanları ayrı çalıştırmak günlükleri okumayı ve hızlı yeniden
başlatmayı kolaylaştırır. **Dört ayrı terminal** gerekir; her komut terminali meşgul eder.

### 1. Veritabanı

PostgreSQL'i kurun, `smart_stock` veritabanını oluşturun, `DB_USERNAME` ve
`DB_PASSWORD` değişkenlerini tanımlayın. Alternatif olarak yalnızca veritabanını
konteynerden alın:

```bash
docker compose up -d postgres
```

### 2. Ollama ve model

```bash
ollama serve                  # ayri bir terminalde
ollama pull qwen3:8b
```

Ollama Windows'ta uygulama olarak kurulduysa arka planda zaten çalışır; `ollama serve`
gerekmez.

### 3. Backend — port 8081

```bash
cd stock-service
mvn clean package
java -jar target/stock-service-0.0.1-SNAPSHOT.jar
```

`Tomcat started on port 8081` satırını görene kadar bekleyin.

### 4. LLM host — port 8000

```bash
python -m venv .venv
source .venv/bin/activate            # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r llm-host/requirements.txt
pip install -r stock-mcp/requirements.txt
pip install -r marketplace-mcp/requirements.txt
cd llm-host
uvicorn secure_api:app --port 8000
```

Giriş noktası `web_api` değil **`secure_api`**'dir; güvenlik katmanı bunu sarar.
Geliştirmede `LLM_AUTH_MODE` varsayılanı `anonymous` olduğu için giriş yapmanız gerekmez.

### 5. Arayüz — port 5173

```bash
cd web-ui
npm install
npm run dev
```

---

## Doğrulama

```bash
curl http://localhost:8081/api/products     # backend veri donuyor mu
curl http://localhost:8000/api/health       # host ayakta mi
python -m unittest discover -s llm-host -p "test_*.py"
```

Ardından arayüzden şu komutu deneyin:

> "Stokta olmayan ürünleri bul ve en ekonomik satın alma planını hazırla."

Cevapla birlikte karar günlüğünde iki adım görünmelidir: `list_out_of_stock` ve
`create_procurement_plan`. İlk istek, modelin belleğe yüklenmesi nedeniyle
sonrakilerden yavaştır.

---

## Sık karşılaşılan sorunlar

### Ön kontrol "Ollama erisilemiyor" diyor

Ollama çalışmıyordur. Ayrı bir terminalde `ollama serve` çalıştırın, ardından
`ollama list` ile modelin yüklü olduğunu doğrulayın. Bu komut da bağlantı hatası
veriyorsa servis hiç ayakta değildir.

### Arayüzde beklenenden farklı veri görünüyor

Büyük ihtimalle `STOCK_SERVICE_URL` kabul ortamını (8082) gösteriyordur. Demo için bu
değişken **boş olmalıdır**:

```powershell
echo $env:STOCK_SERVICE_URL      # bos cikmali
Remove-Item Env:\STOCK_SERVICE_URL
```

### `createdb`: role "…" does not exist

`createdb`, PostgreSQL rolü olarak işletim sistemi kullanıcı adını dener. Kullanıcıyı
açıkça verin:

```powershell
createdb -U $env:DB_USERNAME smart_stock
psql -U $env:DB_USERNAME -c "CREATE DATABASE smart_stock"
```

### Backend başlıyor ama beklenmedik veritabanına bağlanıyor

`DB_URL` ortam değişkeni profil varsayılanını ezer. Kabul profilini elle çalıştırırken
bu istenen davranıştır; demo çalıştırırken değildir. `echo $env:DB_URL` ile kontrol edin.

### Docker derlemesi çok yavaş

`stock-service` imajı çok aşamalı Maven derlemesiyle kurulur ve ilk seferde Maven
imajı ile bağımlılıkları indirir. Yavaş bağlantıda uzun sürer, sonrası önbellekten
gelir. Aceleniz varsa yalnızca veritabanını konteynerden alıp derlenmiş `.jar`
dosyasını yerelden çalıştırabilirsiniz.

### İstekler çok uzun sürüyor

Grafik işlemcisi olmayan makinede normaldir; ölçülen aralık 15–320 saniyedir.
`OLLAMA_CONNECT_TIMEOUT` ve `OLLAMA_READ_TIMEOUT` ile sınırlar yükseltilebilir.

### Arayüzde "İstek tamamlanamadı." çıkıyor, karar günlüğü boş

Bu ileti `web-ui/src/api/client.ts` içindeki genel yedek metindir; HTTP yanıtı
başarısız olduğunda gösterilir. Karar günlüğünün boş kalması, yanıt gövdesinin
arayüze hiç ulaşmadığını gösterir. Arka uç hata dönseydi FastAPI `{"detail": …}`
JSON'u dönerdi ve arayüz o açıklamayı gösterirdi; genel iletinin görünmesi gövdenin
JSON olmadığı, yani hatanın ağ geçidinden geldiği anlamına gelir.

Nedeni nginx'in 60 saniyelik `proxy_read_timeout` varsayılanıdır. `llm-host`
Ollama'yı `OLLAMA_READ_TIMEOUT` (varsayılan 300s) kadar beklerken, ağ geçidi 60.
saniyede bağlantıyı kesip 504 döner. En uzun zincir olan "en ucuz ve en hızlı planı
karşılaştır" isteği bu sınırı ilk aşan istektir; belirlenimci hızlı yoldan geçen
listeleme komutları etkilenmez.

Ağ geçidi sınırı model sınırının **üzerinde** tutulmalıdır. `web-ui/nginx.conf` ve
`web-ui/nginx.prod.conf` dosyalarındaki `location /llm/` bloğunda:

```nginx
proxy_connect_timeout 10s;
proxy_send_timeout 30s;
proxy_read_timeout 330s;
```

Doğrulama: `docker compose logs --tail 40 web-ui` çıktısında `/llm/api/chat`
isteğinin 504 ile bitmesi ve hatanın yaklaşık 60. saniyede görünmesi.

### Çıkış / hesap değiştirme düğmesi yok

Geliştirme varsayılanı `LLM_AUTH_MODE=anonymous`'tur: giriş yoktur, bu yüzden
yan menüde "Çıkış yap" düğmesi ve başlıkta rol rozeti çizilmez. Rol de atanmaz.

Hesap değiştirmek, rolleri ve yetki kurallarını görmek için `.env` dosyasına:

```
LLM_AUTH_MODE=local
LLM_BOOTSTRAP_ADMIN_USERNAME=<kullanici adi>
LLM_BOOTSTRAP_ADMIN_PASSWORD=<parola>
```

Ardından `docker compose up -d llm-host`. İlk yönetici hesabı yalnızca kimlik
veritabanı boşken bir kez oluşturulur; daha sonra Kullanıcı Yönetimi ekranından
başka roller açılır. `.env` depoya girmez (`.gitignore` içinde).

Bu kipte RBAC devreye girer: taslağı OPERATOR oluşturur, onayı MANAGER veya ADMIN
verir. Onay düğmesini görmüyorsanız rolünüz yetersizdir — hata değil, tasarım.

### Panel boş, "kimlik servisine ulaşılamadı" ya da 502

Arka uç konteynerleri yeniden kurulduktan sonra görülürse sebep nginx'in adres
önbelleğidir (bkz. `teknoloji-kararlari.md`). Hızlı çözüm ağ geçidini yeniden
başlatmaktır:

```powershell
docker compose restart web-ui
```

Kalıcı çözüm uygulanmıştır: `nginx.conf` ve `nginx.prod.conf` artık adresi istek
anında çözüyor. Yine de görülürse `docker compose logs web-ui` çıktısında
`Host is unreachable` satırını arayın.

### Taslak sayfasında onay düğmesi görünmüyor

Geliştirme varsayılanı `LLM_AUTH_MODE=anonymous`'tur: kullanıcı girişi kapalıdır,
bu yüzden yan menüde "Çıkış yap" düğmesi ve başlıkta rol rozeti çizilmez.

Bu kipte sunucu rol atamaz. Arayüz taslak düğmelerini role bakarak çizdiği için
onay, ret ve silme hiç görünmüyordu; oysa arka uç izin verir (`rbac.py` rolsüz
isteği kısıtsız sayar, `secure_api.py` onay yetkisini yalnızca `local` kipte
denetler). Bu giderildi — `draftPermissions.ts` artık kimlik kipini de alıyor.

Eski bir sürümdeysen onayı sohbetten verebilirsin: taslak oluştuktan sonra aynı
sohbete `onaylıyorum` yaz. Rol tabanlı erişimi görmek istiyorsan `LLM_AUTH_MODE=local`
ile çalıştır; ilk yönetici hesabı `LLM_BOOTSTRAP_ADMIN_USERNAME` ve
`LLM_BOOTSTRAP_ADMIN_PASSWORD` ile bir kez oluşturulur.

### Testler geçici dosya silme hatası veriyor

Bu hata giderilmiştir. Yeniden görülürse `with sqlite3.connect(...)` deyiminin bir
yerde geri gelmiş olması muhtemeldir; `test_store_connections.py` içindeki AST
denetimi ihlalin dosya ve satırını raporlar.

---

## Ölçüm çalıştırma

Kabul koşumu **temiz bir veritabanı ister**. Kirli veriyle koşmak kodla ilgisi olmayan
başarısızlıklar üretir ve iki koşumun karşılaştırılmasını imkânsız kılar; koşucu bunu
kendisi engeller.

```powershell
docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance
$env:STOCK_SERVICE_URL = "http://localhost:8082"
cd llm-host
python acceptance_runner.py --runs 3 --include-writes
```

Ön kontrolde şu üç satırı görmelisiniz:

```
  [OK ] Ollama: http://localhost:11434/api/tags
  [OK ] Spring Boot backend: http://localhost:8082/api/products
  [VERI] siparis gereken urun: 2 · kritik stok: 3 · bekleyen siparis: 1 (teslime hazir: 1)
```

Tek senaryo için `--only <senaryo_id>` kullanılabilir ve birden fazla kez verilebilir.
Ollama gerektirmeyen hızlı denetim için:

```bash
python llm-host/golden_eval.py
```
