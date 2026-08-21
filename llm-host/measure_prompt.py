"""Sistem promptunun boyutunu ve istege bagli olarak Ollama token sayimini olcer.

Kullanim (llm-host klasorunden):
    python measure_prompt.py --offline
    python measure_prompt.py

`--offline` modeli calistirmadan prompt karakter sayisini ve kaba token tahminini
verir. Normal mod Ollama'ya num_predict=1 ile gidip gercek prompt_eval_count degerini
okur.
"""
import argparse
import importlib.util
import json
import os
import sys
import types

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LLM_HOST_DIR = os.path.join(BASE_DIR, "llm-host")


def _ensure_mcp_stubs():
    """mcp SDK kurulu degilse tool tanimlarini okuyabilmek icin asgari sahte modul."""
    try:
        import mcp.server  # noqa: F401
        import mcp.types  # noqa: F401
        return
    except Exception:
        pass

    class Tool:
        def __init__(self, name, description, inputSchema):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class Server:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    types_mod = types.ModuleType("mcp.types")
    server_mod.Server = Server
    types_mod.Tool = Tool
    types_mod.TextContent = type("TextContent", (), {})
    sys.modules.setdefault("mcp", mcp_mod)
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.types"] = types_mod


def _load_tools(server_dir, module_alias):
    """Bir MCP sunucusunun tools.py dosyasindaki tool tanimlarini okur."""
    sys.modules.pop("services", None)
    sys.path.insert(0, server_dir)
    try:
        try:
            import services  # noqa: F401
        except Exception:
            stub = types.ModuleType("services")
            for cls_name in ("ProductService", "MarketplaceService"):
                setattr(stub, cls_name, type(cls_name, (), {"__init__": lambda self, *a, **k: None}))
            sys.modules["services"] = stub

        spec = importlib.util.spec_from_file_location(
            module_alias, os.path.join(server_dir, "tools.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        import asyncio
        return asyncio.run(module.list_tools())
    finally:
        sys.path.remove(server_dir)
        sys.modules.pop("services", None)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Measure Smart Stock planning prompt size")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="only build the prompt; do not call Ollama",
    )
    parser.add_argument(
        "--question",
        default="Kritik urunler icin en hizli satin alma planini hazirla.",
        help="sample user request appended to the system prompt",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    _ensure_mcp_stubs()
    sys.path.insert(0, LLM_HOST_DIR)

    from prompt import get_execution_plan_prompt
    from llm import LLMService

    tools = (
        _load_tools(os.path.join(BASE_DIR, "stock-mcp"), "stock_tools")
        + _load_tools(os.path.join(BASE_DIR, "marketplace-mcp"), "marketplace_tools")
    )

    system_prompt = get_execution_plan_prompt(tools)
    prompt = f"system: {system_prompt}\nuser: {args.question}\nassistant:"

    service = LLMService()

    print("=" * 58)
    print("PROMPT OLCUMU")
    print("=" * 58)
    print(f"Tool sayisi            : {len(tools)}")
    print(f"Sistem promptu         : {len(system_prompt)} karakter")
    print(f"Gonderilen tam prompt  : {len(prompt)} karakter")
    print(f"Kaba token tahmini     : ~{len(prompt) // 4}")
    print(f"Model                  : {service.model}")
    print(f"num_ctx (ayarli)       : {service.num_ctx}")
    print("-" * 58)

    if args.offline:
        pay = 100 * (len(prompt) // 4) / service.num_ctx
        print("MOD                    : OFFLINE (Ollama cagrilmadi)")
        print(f"Tahmini context doluluk: %{pay:.0f}")
        print("=" * 58)
        return 0

    payload = {
        "model": service.model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"num_predict": 1, "num_ctx": service.num_ctx},
    }

    import requests
    try:
        response = requests.post(service.url, json=payload, timeout=(10, 180))
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print("Ollama'ya ulasilamadi:", exc)
        print("\n'ollama serve' calisiyor mu, kontrol et.")
        return 1

    counted = data.get("prompt_eval_count")
    if counted is None:
        print("Ollama prompt_eval_count dondurmedi. Ham cevap alanlari:")
        print(" ", ", ".join(sorted(data.keys())))
        return 1

    print(f"OLLAMA'NIN SAYDIGI     : {counted} token")
    print("-" * 58)
    if counted >= service.num_ctx:
        print(f"SONUC: SIGMIYOR  ({counted} >= {service.num_ctx})")
        print("Prompt bastan kesiliyor; ilk kaybolan bolum AVAILABLE TOOLS.")
    else:
        pay = 100 * counted / service.num_ctx
        print(f"SONUC: SIGIYOR   ({counted} / {service.num_ctx}, %{pay:.0f} dolu)")
        if pay > 70:
            print("Not: sohbet gecmisi eklendikce bu oran yukselir, takipte kalalim.")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
