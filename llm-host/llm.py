import json
import os
import unicodedata

import requests


FAST_INFO_BLOCKERS = (
    "plan",
    "taslak",
    "siparis",
    "satin al",
    "teklif",
    "fiyat",
    "karsilastir",
    "kac tane",
    "ne kadar",
    "almaliyim",
    "onay",
    "teslim",
    "stoga al",
    "purchase",
    "draft",
    "order",
    "compare",
    "cheapest",
    "fastest",
)
OUT_OF_STOCK_TERMS = (
    "stokta olmayan",
    "stok yok",
    "tukenen",
    "tukenmis",
    "out of stock",
)
LOW_STOCK_TERMS = (
    "kritik stok",
    "kritik urun",
    "azalan stok",
    "azalan urun",
    "dusuk stok",
    "low stock",
    "minimum stok",
)


def _normalize_for_route(text):
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return normalized.replace("ı", "i")


def _fast_read_only_tool(user_message):
    """Return a safe single read tool for unambiguous stock-state retrievals."""
    normalized = _normalize_for_route(user_message)
    if any(term in normalized for term in FAST_INFO_BLOCKERS):
        return None
    if any(term in normalized for term in OUT_OF_STOCK_TERMS):
        return "list_out_of_stock"
    if any(term in normalized for term in LOW_STOCK_TERMS):
        return "list_low_stock"
    return None


def prepare_inference_messages(messages):
    """Shrink only clearly read-only execution-planner requests before inference.

    Host-side plan validation and permission gates remain authoritative. Complex,
    write-like, procurement and reasoning requests keep the full planner prompt.

    The compact messages are retained for diagnostics/tests, while LLMService.generate
    can safely bypass Ollama entirely when `tool` is returned because the only valid
    execution plan is deterministic and still passes through host validation/execution.
    """
    if not messages:
        return messages, None

    system = next((item for item in messages if item.get("role") == "system"), None)
    if not system or "Smart Stock & Procurement execution planner." not in system.get("content", ""):
        return messages, None

    user = next((item for item in reversed(messages) if item.get("role") == "user"), None)
    if not user:
        return messages, None

    tool = _fast_read_only_tool(user.get("content", ""))
    if not tool:
        return messages, None

    compact_system = f"""Smart Stock read-only execution planner.
This request was preclassified as a simple stock-state lookup.
Return EXACTLY one JSON object and no Markdown/prose:
{{"type":"execution_plan","goal":"INFO","steps":[{{"id":"step_1","tool":"{tool}","arguments":{{}}}}]}}
Rules:
- Use exactly `{tool}` and no other tool.
- Goal must be INFO and arguments must be an empty object.
- Never emit write tools, procurement plans, drafts, orders, receive actions, `params`, or `final_response`.
- Do not invent data; this response only selects the read tool. Actual data comes from MCP execution.
"""
    return [
        {"role": "system", "content": compact_system},
        {"role": "user", "content": user.get("content", "")},
    ], tool


def _fast_execution_plan(tool):
    """Build the only permitted plan for a preclassified single-tool INFO lookup."""
    return json.dumps(
        {
            "type": "execution_plan",
            "goal": "INFO",
            "steps": [
                {
                    "id": "step_1",
                    "tool": tool,
                    "arguments": {},
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


class LLMService:
    def __init__(self):
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
        # Ollama varsayilan num_ctx degeri sistem promptumuzdan kucuk olabilir.
        # Asildiginda prompt BASTAN kesilir; ilk kesilen bolum AVAILABLE TOOLS olur.
        self.num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
        self.num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "1024"))
        # Keep network limits configurable, but do not hide prompt/model performance
        # problems behind ever-growing defaults. Slower environments can override them.
        self.connect_timeout = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "20"))
        self.read_timeout = float(os.getenv("OLLAMA_READ_TIMEOUT", "300"))
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("OLLAMA_CONNECT_TIMEOUT and OLLAMA_READ_TIMEOUT must be positive")

    def generate(self, messages):
        messages, fast_tool = prepare_inference_messages(messages)
        if fast_tool:
            print(f"[LLM] fast read-only planner bypass: {fast_tool} (Ollama skipped)")
            return _fast_execution_plan(fast_tool)

        prompt_parts = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            prompt_parts.append(f"{role}: {content}")

        prompt = "\n".join(prompt_parts)
        if not prompt.endswith("\nassistant:"):
            prompt += "\nassistant:"

        print(f"Prompt karakter sayısı: {len(prompt)}")
        print(f"Prompt token sayısı (tahmini): {len(prompt) // 4}")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx
            }
        }

        try:
            response = requests.post(
                self.url,
                json=payload,
                timeout=(self.connect_timeout, self.read_timeout)
            )
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama zaman aşımı: connect={self.connect_timeout}s, read={self.read_timeout}s"
            ) from exc

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(
                f"LLM API hatası: {response.status_code} - {response.text}"
            ) from exc

        data = response.json()

        prompt_tokens = data.get("prompt_eval_count")
        if prompt_tokens is not None:
            print(
                f"[LLM] prompt {prompt_tokens} token / num_ctx {self.num_ctx} | "
                f"cikti {data.get('eval_count')} token / num_predict {self.num_predict}"
            )
            if prompt_tokens >= self.num_ctx:
                print(
                    "[LLM] UYARI: prompt baglam penceresini doldurdu. "
                    "Prompt bastan kesilmis olabilir (once AVAILABLE TOOLS gider)."
                )

        if "response" not in data:
            raise RuntimeError(f"Ollama cevabında 'response' alanı yok: {data}")
        return data["response"]
