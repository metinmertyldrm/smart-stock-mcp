import os

import requests


class LLMService:
    def __init__(self):
        self.url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
        # Ollama varsayilan num_ctx degeri sistem promptumuzdan kucuk olabilir.
        # Asildiginda prompt BASTAN kesilir; ilk kesilen bolum AVAILABLE TOOLS olur.
        self.num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
        self.num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "1024"))
        # CPU-only local inference can exceed five minutes for the large planning prompt.
        # Keep both values configurable so slower machines can widen the window without
        # changing application code.
        self.connect_timeout = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "20"))
        self.read_timeout = float(os.getenv("OLLAMA_READ_TIMEOUT", "600"))
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("OLLAMA_CONNECT_TIMEOUT and OLLAMA_READ_TIMEOUT must be positive")

    def generate(self, messages):
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
