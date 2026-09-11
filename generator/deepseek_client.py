from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


class ResponseShapeError(ValueError):
    """Raised by a response validator when valid JSON has the wrong shape.

    Kept separate from transport errors so the client can re-prompt with the
    validation error instead of blindly repeating the same request.
    """


def _json_object_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_object" in text


def _is_retryable(exc: Exception) -> bool:
    """Network/rate-limit errors are worth repeating; caller errors are not."""
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("timeout", "connection", "ratelimit", "internalserver", "apierror")):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in {408, 409, 429} or status >= 500
    return False


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
        self.model_topics = deepseek_cfg.get("model_topics", "deepseek-flash")
        self.model_writer = deepseek_cfg.get("model_writer", "deepseek-flash")
        self.max_retries = int(deepseek_cfg.get("max_retries", 1))
        self.temperature = float(deepseek_cfg.get("temperature", 0.2))
        self.force_json_object = bool(deepseek_cfg.get("force_json_object", True))
        # Set once the API rejects response_format so we stop asking for it.
        self._json_object_disabled = not self.force_json_object

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

    def _create_completion(self, model: str, system_prompt: str, user_prompt: str):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
        }
        if not self._json_object_disabled:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "response_format" not in kwargs or not _json_object_unsupported(exc):
                raise
            logger.warning("response_format json_object rejected; retrying without it: %s", exc)
            self._json_object_disabled = True
            kwargs.pop("response_format")
            return self.client.chat.completions.create(**kwargs)

    def _repair_prompt(self, user_prompt: str, error: Exception, attempt: int) -> str:
        return (
            f"{user_prompt}\n\n"
            f"---\n"
            f"你上一次的回复未通过格式校验（第 {attempt} 次）。\n"
            f"校验错误：{error}\n"
            f"请只返回修正后的合法 JSON，不要包含解释文字、Markdown 代码块或额外字段。"
        )

    def chat_json(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        validator: Callable[[Any], Any] | None = None,
    ) -> Any:
        """Call the model and return parsed JSON.

        ``validator`` receives the parsed payload and may raise
        ``ResponseShapeError`` to request a corrected response. Transport errors
        are retried; contract errors are re-prompted with the specific error.
        """
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        last_error_kind = "unknown"
        current_prompt = user_prompt
        repair_attempt = 0
        used_attempts = 0

        for attempt in range(attempts):
            used_attempts = attempt + 1
            try:
                response = self._create_completion(model, system_prompt, current_prompt)
                content = response.choices[0].message.content or ""
                payload = self._extract_json(content)
                if validator is not None:
                    return validator(payload)
                return payload
            except ResponseShapeError as exc:
                last_error = exc
                last_error_kind = "schema"
                repair_attempt += 1
                current_prompt = self._repair_prompt(user_prompt, exc, repair_attempt)
                logger.warning(
                    "Model response failed schema validation (attempt %d/%d): %s",
                    used_attempts,
                    attempts,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                last_error_kind = "transport"
                logger.warning(
                    "DeepSeek call failed (attempt %d/%d): %s",
                    used_attempts,
                    attempts,
                    exc,
                )
                if not _is_retryable(exc):
                    logger.warning("Non-retryable DeepSeek error; giving up early")
                    break

        raise RuntimeError(
            f"DeepSeek request failed after {used_attempts} attempt(s) "
            f"({last_error_kind} error): {last_error}"
        )
