from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


class DeepSeekClient:
    def __init__(self, config: dict[str, Any]):
        deepseek_cfg = config.get("deepseek", {})
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set")

        self.client = OpenAI(
            api_key=api_key,
            base_url=deepseek_cfg.get("base_url", "https://api.deepseek.com/v1"),
        )
        self.model_topics = deepseek_cfg.get("model_topics", "deepseek-v4-pro")
        self.model_writer = deepseek_cfg.get("model_writer", "deepseek-v4-pro")
        self.max_retries = int(deepseek_cfg.get("max_retries", 1))
        self.temperature = float(deepseek_cfg.get("temperature", 0.2))

    def _extract_json(self, content: str) -> Any:
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        match = JSON_BLOCK_RE.search(content)
        if match:
            return json.loads(match.group(1))

        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])

        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])

        raise ValueError("Model response does not contain valid JSON")

    def chat_json(self, model: str, system_prompt: str, user_prompt: str) -> Any:
        last_error: Exception | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                )
                content = response.choices[0].message.content or ""
                return self._extract_json(content)
            except Exception as exc:
                last_error = exc
                logger.warning("DeepSeek call failed (attempt %d/%d): %s", attempt + 1, attempts, exc)

        raise RuntimeError(f"DeepSeek request failed after {attempts} attempts: {last_error}")
