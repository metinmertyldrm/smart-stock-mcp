"""Kabul koşucusu: taslaktaki örnek komutları gerçek yığına karşı tekrarlı çalıştırır.

Ölçtüğü iki şey var:
  * başarı oranı — komut kaç denemede baştan sona çalıştı,
  * varyans      — aynı komut için model kaç farklı plan üretti.

Kullanım (llm-host klasöründen, venv aktifken):
    python acceptance_runner.py                # salt okunur senaryolar, 3 tekrar
    python acceptance_runner.py --runs 5
    python acceptance_runner.py --include-writes   # taslak/sipariş senaryoları da
    python acceptance_runner.py --only draft_then_confirm

Ön koşul: kabul ortamı ve Ollama (11434) çalışıyor olmalı; uvicorn'a gerek yok.
Kabul ortamı en kolay docker compose ile kalkar:
    docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance
    $env:STOCK_SERVICE_URL = "http://localhost:8082"
Adres STOCK_SERVICE_URL ile ayarlanır; MCP sunucuları bu değişkeni miras alır.

DİKKAT: --include-writes gerçek taslak ve sipariş kaydı oluşturur, stok verisini
değiştirir. Demo veritabanında çalıştır.
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# --------------------------------------------------------------------------
# Senaryolar — taslaktaki "Örnek doğal dil komutları" bölümünden birebir alındı
# --------------------------------------------------------------------------

SCENARIOS = [
    {
        "id": "plan_out_of_stock",
        "kaynak": "Taslak örnek komut 1",
        "turns": ["Stokta olmayan ürünleri bul ve en ekonomik satın alma planını hazırla."],
        "expect": {
            "goals": ["PLAN", "REASON"],
            "tools_required": ["create_procurement_plan"],
            "tools_forbidden": ["place_order", "create_purchase_draft"],
        },
    },
    {
        "id": "category_and_rating_filter",
        "kaynak": "Taslak örnek komut 2",
        "writes": True,
        "turns": ["Elektronik kategorisindeki kritik ürünleri yalnızca puanı 4.5 "
                  "üzerindeki satıcılardan satın al."],
        "expect": {
            "goals": ["DRAFT"],
            "tools_required": ["create_purchase_draft"],
            "tools_forbidden": ["place_order"],
            "arguments_contain": {"min_rating": 4.5},
        },
    },
    {
        "id": "total_budget_limit",
        "kaynak": "Taslak örnek komut 3",
        "turns": ["Toplam bütçe 50.000 TL'yi geçmeyecek şekilde eksik ürünleri tamamla."],
        "expect": {
            "goals": ["PLAN", "REASON"],
            "tools_required": ["create_procurement_plan"],
            "arguments_contain": {"max_total_budget": 50000},
            # Bütçeyi birim fiyat tavanı sanmak sessiz yanlış cevap üretir.
            "arguments_forbidden": {"max_unit_price": 50000},
        },
    },
    {
        "id": "max_delivery_days",
        "kaynak": "Taslak örnek komut 4",
        "turns": ["Kritik ürünler için plan hazırla, teslimat süresi üç günden uzun "
                  "olan teklifleri kullanma."],
        "expect": {
            "goals": ["PLAN", "REASON"],
            "tools_required": ["create_procurement_plan"],
            "arguments_contain": {"max_delivery_days": 3},
        },
    },
    {
        "id": "pending_orders_receive",
        "kaynak": "Taslak örnek komut 5",
        "writes": True,
        # Teslim alma stogu artirir; taslaktaki onay kurali geregi tek turda
        # yapilamaz. Birinci tur yalnizca listeler, ikinci tur onaydan sonra
        # stoga alir. Degerlendirme son turu olctugu icin beklenti receive_orders.
        "turns": ["Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle.",
                  "Onaylıyorum."],
        "expect": {
            "goals": ["RECEIVE"],
            "tools_required": ["receive_orders"],
            # Tekil arac onay kapisini atlamanin klasik yolu: listeyle ayni turda cagrilir.
            "tools_forbidden": ["receive_order"],
        },
    },
    {
        "id": "pending_orders_listing_only",
        "kaynak": "Taslak örnek komut 5 — onay kapisi",
        # Ilk tur stok DEGISTIRMEMELI; bu yuzden writes=False, salt okunur kosumda da calisir.
        "turns": ["Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle."],
        "expect": {
            "goals": ["RECEIVE"],
            "tools_required": ["list_incoming_orders"],
            "tools_forbidden": ["receive_orders", "receive_order"],
        },
    },
    {
        "id": "balanced_objective",
        "kaynak": "Taslak örnek komut 6",
        "turns": ["Kritik ürünler için plan hazırla; en düşük fiyat yerine fiyat, "
                  "teslimat süresi ve satıcı puanını birlikte değerlendir."],
        "expect": {
            "goals": ["PLAN", "REASON"],
            "tools_required": ["create_procurement_plan"],
            "arguments_contain": {"objective": "BALANCED"},
        },
    },
    {
        "id": "compare_cheapest_fastest",
        "kaynak": "Web arayüzü hazır komutu",
        "turns": ["En ucuz ve en hızlı planı karşılaştır."],
        "expect": {"goals": ["REASON"], "tools_required": []},
    },
    {
        "id": "draft_then_confirm",
        "kaynak": "Taslak işlem zinciri, adım 6-8",
        "writes": True,
        "turns": ["Stokta azalan ürünler için en ucuz tekliflerden taslak sipariş oluştur.",
                  "Onaylıyorum."],
        "expect": {
            "goals": ["ORDER"],
            "tools_required": ["place_order", "create_incoming_orders"],
        },
    },
]


# --------------------------------------------------------------------------
# Değerlendirme — saf fonksiyonlar, testten çağrılabilir
# --------------------------------------------------------------------------

def collect_arguments(trace):
    """İşlem izindeki tüm argümanları düz bir sözlükte toplar (iç içe dahil)."""
    collected = {}

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(item, (dict, list)):
                    collected.setdefault(key, item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for step in trace or []:
        walk(step.get("arguments"))
    return collected


def evaluate(scenario, response):
    """Bir çalıştırmanın beklentileri karşılayıp karşılamadığını döndürür."""
    expect = scenario.get("expect", {})
    trace = response.get("trace") or []
    goal = (response.get("plan") or {}).get("goal", "")
    tools = [step.get("tool") for step in trace]
    statuses = [step.get("status") for step in trace]

    problems = []

    # Sifir adimli planlar patladiginda izde "failed" olmaz; butunsel bayrak sart.
    succeeded = response.get("succeeded")
    if succeeded is False:
        problems.append("istek başarısız tamamlandı")
    elif succeeded is None and "tamamlanamadı" in (response.get("finalAnswer") or ""):
        problems.append("istek başarısız tamamlandı (cevaptan anlaşıldı)")

    if "failed" in statuses:
        index = statuses.index("failed")
        detail = (trace[index].get("resultSummary") or "").strip()
        problems.append(f"adım başarısız: {tools[index]}"
                        + (f" -> {detail[:160]}" if detail else ""))

    goals = expect.get("goals")
    if goals and goal not in goals:
        problems.append(f"goal {goal!r}, beklenen {goals}")

    for tool in expect.get("tools_required", []):
        if tool not in tools:
            problems.append(f"eksik tool: {tool}")

    for tool in expect.get("tools_forbidden", []):
        if tool in tools:
            problems.append(f"yasak tool çağrıldı: {tool}")

    arguments = collect_arguments(trace)
    for key, expected in (expect.get("arguments_contain") or {}).items():
        if key not in arguments:
            problems.append(f"argüman verilmemiş: {key}")
        elif arguments[key] != expected:
            problems.append(f"{key}={arguments[key]!r}, beklenen {expected!r}")

    # Kullanıcının kısıtını yanlış bir parametreye kaydırmak, sessiz yanlış cevap üretir.
    for key, wrong in (expect.get("arguments_forbidden") or {}).items():
        if arguments.get(key) == wrong:
            problems.append(f"kısıt yanlış parametreye yazılmış: {key}={wrong!r}")

    return {
        "ok": not problems,
        "goal": goal,
        "tools": tools,
        "signature": f"{goal}: {' → '.join(tools) if tools else '(tool yok)'}",
        "problems": problems,
    }


def summarize(scenario, runs):
    """Bir senaryonun tekrarlarını başarı oranı ve plan çeşitliliğine indirger."""
    signatures = Counter(run["signature"] for run in runs)
    durations = [run["duration"] for run in runs]
    return {
        "id": scenario["id"],
        "kaynak": scenario.get("kaynak", ""),
        "known_gap": scenario.get("known_gap"),
        "runs": len(runs),
        "passed": sum(1 for run in runs if run["ok"]),
        "distinct_plans": len(signatures),
        "signatures": signatures.most_common(),
        "median_duration": round(statistics.median(durations), 1) if durations else 0.0,
        "problems": sorted({problem for run in runs for problem in run["problems"]}),
    }


# --------------------------------------------------------------------------
# Koşum
# --------------------------------------------------------------------------

def preflight():
    """Senaryolara baslamadan once bagimliliklari dogrular.

    Ollama veya backend kapaliyken kosmak, senaryo basarisizligi gibi gorunen
    ama aslinda ortam sorunu olan sonuclar uretiyor. Onceden ve net soylemek daha iyi.
    """
    import requests

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    ollama_root = ollama_url.split("/api/")[0]
    stock_url = os.getenv("STOCK_SERVICE_URL", "http://localhost:8081")

    checks = [
        ("Ollama", f"{ollama_root}/api/tags", "ollama serve"),
        ("Spring Boot backend", f"{stock_url}/api/products",
         "cd stock-service && java -jar target/stock-service-0.0.1-SNAPSHOT.jar"),
    ]

    problems = []
    for name, url, hint in checks:
        try:
            # (baglanti, okuma): baglanti hizli olmali, okuma yavas olabilir
            # cunku Ollama model yuklerken istekleri bekletir.
            response = requests.get(url, timeout=(5, 20))
            response.raise_for_status()
            print(f"  [OK ] {name}: {url}")
        except requests.exceptions.ReadTimeout:
            # Baglanti kuruldu, servis ayakta; sadece mesgul (model yukleniyor olabilir).
            print(f"  [YAVAS] {name}: {url} — ayakta ama yanit vermedi, devam ediliyor")
        except Exception as exc:
            print(f"  [YOK] {name}: {url} ({type(exc).__name__})")
            problems.append(f"{name} erisilemiyor ({url}). Baslatmak icin: {hint}")

    return problems


# --------------------------------------------------------------------------
# Baslangic durumu — olcumun karsilastirilabilir olmasinin sarti
# --------------------------------------------------------------------------

BASELINE_HINT = (
    "Kabul kosumu temiz bir veritabani ister. Projede bunun icin hazir bir\n"
    "    docker compose profili var (ayri Postgres + ayri port, demo verisine dokunmaz):\n"
    "      docker compose --profile acceptance up -d postgres-acceptance stock-service-acceptance\n"
    "      $env:STOCK_SERVICE_URL = \"http://localhost:8082\"   # PowerShell\n"
    "      export STOCK_SERVICE_URL=http://localhost:8082        # bash\n"
    "    Servis her acilista semayi bastan kurar (ddl-auto: create), sonra\n"
    "    data.sql + acceptance-data.sql ile tohumlar.\n"
    "    Docker yoksa elle: kabul veritabanini olustur ve\n"
    "    java -jar target/stock-service-0.0.1-SNAPSHOT.jar --spring.profiles.active=acceptance\n"
    "    (bu durumda servis 8081'i kullanir, demo ornegini once durdur)."
)

# Hangi arac hangi veriye muhtac. Senaryo listesinden turetiyoruz ki
# --only ile tek senaryo kosarken alakasiz on kosul dayatilmasin.
TOOL_REQUIREMENTS = {
    "create_procurement_plan": "replenishment",
    "create_purchase_draft": "replenishment",
    "place_order": "replenishment",
    "receive_orders": "receivable_order",
    "receive_order": "receivable_order",
    "list_incoming_orders": "pending_order",
}

REQUIREMENT_MESSAGES = {
    "replenishment": (
        "Siparis edilmesi gereken urun yok (/api/products/replenishment bos). "
        "Plan/taslak/siparis senaryolari bu veri olmadan anlamli olcmez."
    ),
    "pending_order": (
        "Bekleyen siparis yok (/api/orders/pending bos). "
        "Teslim alma senaryolarinin listeleme turu bos doner."
    ),
    "receivable_order": (
        "Teslim alinmaya hazir siparis yok (/api/orders/pending?readyOnly=true bos). "
        "Teslimat tarihi gelmemis siparisi stoga almak zaten yasak."
    ),
}


def required_preconditions(scenarios):
    """Secilen senaryolarin ihtiyac duydugu veri on kosullarini toplar."""
    needed = set()
    for scenario in scenarios:
        for tool in scenario.get("expect", {}).get("tools_required", []):
            requirement = TOOL_REQUIREMENTS.get(tool)
            if requirement:
                needed.add(requirement)
    # Teslim alma zinciri once listeler; hazir siparis varsa bekleyen de vardir.
    if "receivable_order" in needed:
        needed.discard("pending_order")
    return needed


def baseline_problems(baseline, scenarios):
    """Saf fonksiyon: olculen duruma bakip eksik on kosullari dondurur."""
    problems = []
    for requirement in sorted(required_preconditions(scenarios)):
        if not baseline.get(requirement):
            problems.append(REQUIREMENT_MESSAGES[requirement])
    return problems


def fetch_baseline(stock_url):
    """Backend'den senaryolarin dayandigi sayilari ceker."""
    import requests

    def count(path, params=None):
        try:
            response = requests.get(f"{stock_url}{path}", params=params, timeout=(5, 20))
            response.raise_for_status()
            payload = response.json()
            return len(payload) if isinstance(payload, list) else 0
        except Exception:
            # Erisim sorunlarini preflight zaten raporluyor; burada 0 sayiyoruz.
            return 0

    return {
        "replenishment": count("/api/products/replenishment"),
        "low_stock": count("/api/products/low-stock"),
        "pending_order": count("/api/orders/pending"),
        "receivable_order": count("/api/orders/pending", {"readyOnly": "true"}),
    }


class RecordingLLM:
    """LLMService'i sarar; istek başına harcanan token sayısını kaydeder."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return self.inner.generate(messages)


async def run_scenarios(scenarios, runs, verbose=False):
    from app import MARKETPLACE_SERVER_PATH, STOCK_SERVER_PATH
    from llm import LLMService
    from mcp_client import MCPClient
    from web_api import AgentApplication, ConversationStore

    client = MCPClient({"stock-server": STOCK_SERVER_PATH,
                        "marketplace-server": MARKETPLACE_SERVER_PATH})
    await client.connect()

    llm = RecordingLLM(LLMService())
    store = ConversationStore(os.path.join(tempfile.mkdtemp(), "acceptance.db"))
    agent = AgentApplication(client, llm, store)

    results = []
    try:
        for scenario in scenarios:
            scenario_runs = []
            for index in range(runs):
                conversation_id = f"acc-{scenario['id']}-{index}-{uuid.uuid4().hex[:6]}"
                started = time.perf_counter()
                response = None
                error = None
                try:
                    for turn in scenario["turns"]:
                        response = await agent.chat(conversation_id, turn, "acceptance")
                except Exception as exc:           # noqa: BLE001 - koşum devam etmeli
                    error = f"{type(exc).__name__}: {exc}"
                duration = time.perf_counter() - started

                if error is not None:
                    outcome = {"ok": False, "goal": "-", "tools": [],
                               "signature": f"HATA: {error[:60]}", "problems": [error]}
                else:
                    outcome = evaluate(scenario, response)
                outcome["duration"] = duration
                outcome["answer"] = (response or {}).get("finalAnswer", "")[:200]
                scenario_runs.append(outcome)

                mark = "OK " if outcome["ok"] else "BAD"
                print(f"  [{mark}] {scenario['id']} #{index + 1}  "
                      f"{duration:5.1f} sn  {outcome['signature'][:80]}")
                if verbose and outcome["problems"]:
                    for problem in outcome["problems"]:
                        print(f"        - {problem}")

            results.append({"scenario": scenario, "runs": scenario_runs,
                            "summary": summarize(scenario, scenario_runs)})
    finally:
        store.close()
        await client.close()

    return results, llm.calls


def print_report(results, llm_calls):
    print()
    print("=" * 78)
    print("KABUL RAPORU")
    print("=" * 78)
    print(f"{'senaryo':<30}{'başarı':>8}{'plan çeşidi':>13}{'ortanca süre':>14}")
    print("-" * 78)

    total_runs = total_passed = 0
    for entry in results:
        summary = entry["summary"]
        total_runs += summary["runs"]
        total_passed += summary["passed"]
        flag = " *" if summary["known_gap"] else ""
        print(f"{summary['id'][:28] + flag:<30}"
              f"{summary['passed']}/{summary['runs']:<6}"
              f"{summary['distinct_plans']:>13}"
              f"{summary['median_duration']:>13} sn")

    print("-" * 78)
    oran = (100 * total_passed / total_runs) if total_runs else 0
    print(f"TOPLAM: {total_passed}/{total_runs} (%{oran:.0f}) · {llm_calls} LLM çağrısı")

    gaps = [entry["summary"] for entry in results if entry["summary"]["known_gap"]]
    if gaps:
        print("\n* Bilinen eksikler (başarısızlık beklenen senaryolar):")
        for summary in gaps:
            print(f"  - {summary['id']}: {summary['known_gap']}")

    print("\nSorun görülen senaryolar:")
    clean = True
    for entry in results:
        summary = entry["summary"]
        if summary["passed"] == summary["runs"]:
            continue
        clean = False
        print(f"\n  {summary['id']}  ({summary['kaynak']})")
        for problem in summary["problems"]:
            print(f"    · {problem}")
        if summary["distinct_plans"] > 1:
            print(f"    · {summary['distinct_plans']} farklı plan üretildi:")
            for signature, count in summary["signatures"]:
                print(f"        {count}x  {signature}")
    if clean:
        print("  (yok)")


def main():
    parser = argparse.ArgumentParser(description="Smart Stock kabul koşucusu")
    parser.add_argument("--runs", type=int, default=3, help="senaryo başına tekrar (varsayılan 3)")
    parser.add_argument("--include-writes", action="store_true",
                        help="taslak/sipariş oluşturan senaryoları da çalıştır (veriyi değiştirir)")
    parser.add_argument("--only", action="append", metavar="SENARYO_ID",
                        help="yalnızca bu senaryoyu çalıştır (birden fazla kez verilebilir)")
    parser.add_argument("--allow-dirty-state", action="store_true",
                        help="veri ön koşulları tutmasa da koş (sonuçlar karşılaştırılabilir olmaz)")
    parser.add_argument("--verbose", action="store_true", help="her koşuda sorunları yazdır")
    parser.add_argument("--json", help="ayrıntılı sonucu bu dosyaya yaz")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {s["id"] for s in SCENARIOS}
        if unknown:
            # Sessizce atlamak, kosmadigi senaryoyu kostu sanmaya yol acar.
            print(f"Bilinmeyen senaryo: {', '.join(sorted(unknown))}")
            print(f"Gecerli id'ler: {', '.join(s['id'] for s in SCENARIOS)}")
            return 2
        scenarios = [s for s in SCENARIOS if s["id"] in wanted]
    elif not args.include_writes:
        scenarios = [s for s in scenarios if not s.get("writes")]

    print("Ön kontrol:")
    problems = preflight()
    if problems:
        print("\nKoşu başlatılmadı, önce şunları düzelt:")
        for problem in problems:
            print(f"  · {problem}")
        return 2

    # Kirli veriyle kosmak, kodla ilgisi olmayan basarisizliklar uretir ve
    # iki kosumun karsilastirilmasini imkansiz kilar. Olcum once durumu soylesin.
    stock_url = os.getenv("STOCK_SERVICE_URL", "http://localhost:8081")
    baseline = fetch_baseline(stock_url)
    print(f"  [VERI] siparis gereken urun: {baseline['replenishment']} · "
          f"kritik stok: {baseline['low_stock']} · bekleyen siparis: {baseline['pending_order']} "
          f"(teslime hazir: {baseline['receivable_order']})")

    state_problems = baseline_problems(baseline, scenarios)
    if state_problems:
        if args.allow_dirty_state:
            print("\n  [UYARI] Baslangic durumu eksik, --allow-dirty-state ile devam ediliyor.")
            print("  Bu kosumun sonuclari onceki kosumlarla karsilastirilamaz.")
            for problem in state_problems:
                print(f"    · {problem}")
        else:
            print("\nKosu baslatilmadi — baslangic durumu senaryolari olcmeye uygun degil:")
            for problem in state_problems:
                print(f"  · {problem}")
            print()
            print(BASELINE_HINT)
            print()
            print("  Yine de kosmak istiyorsan: --allow-dirty-state")
            return 2
    print()

    print(f"{len(scenarios)} senaryo × {args.runs} tekrar")
    if not args.include_writes and not args.only:
        print("(yazma senaryoları atlandı; dahil etmek için --include-writes)")
    print()

    results, llm_calls = asyncio.run(run_scenarios(scenarios, args.runs, args.verbose))
    print_report(results, llm_calls)

    if args.json:
        payload = {
            "olcum_zamani": datetime.now().isoformat(timespec="seconds"),
            "runs": args.runs,
            "sonuclar": [{"summary": entry["summary"],
                          "runs": entry["runs"]} for entry in results],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"\nAyrıntılı sonuç yazıldı: {args.json}")

    failed = sum(s["summary"]["runs"] - s["summary"]["passed"] for s in results
                 if not s["summary"]["known_gap"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
