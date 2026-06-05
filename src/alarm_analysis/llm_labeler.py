from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .models import LabelInput, LabelOutput
from .prompting import SYSTEM_PROMPT, build_user_prompt


class LlmLabeler:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8090",
        model: str = "Qwen3.6-27B-Q4_K_M.gguf",
        api_key: str | None = None,
        api_key_file: Path | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.getenv("LLAMA_API_KEY") or self._read_key(api_key_file)
        self.timeout = timeout

    @staticmethod
    def _read_key(path: Path | None) -> str | None:
        if not path:
            return None
        expanded = path.expanduser()
        if not expanded.exists():
            return None
        for line in expanded.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
        return None

    def label(self, label_input: LabelInput) -> list[LabelOutput]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = self.build_payload(
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(label_input),
        )
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = response.text[:1000]
                raise RuntimeError(f"llama-server request failed: {exc}; body={body}") from exc
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_label_outputs(content)

    @staticmethod
    def build_payload(*, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "top_p": 0.8,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }


def parse_label_outputs(content: str) -> list[LabelOutput]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("LLM output must be a JSON array")
    return [LabelOutput.model_validate(item) for item in raw]
