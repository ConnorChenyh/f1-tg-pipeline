from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from analyzer.net import RetryPolicy, backoff_delay

logger = logging.getLogger(__name__)

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)


@dataclass
class TokenUsage:
    """Accumulated model usage for one run, including failed calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    failed_calls: int = 0
    retries: int = 0
    latency_sec: float = 0.0
    _by_stage: dict[str, dict[str, float]] = field(default_factory=dict)

    def record(
        self,
        stage: str,
        usage: Any,
        latency_sec: float,
        retries: int = 0,
        failed: bool = False,
    ) -> None:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        completion = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.latency_sec += latency_sec
        self.retries += retries
        if failed:
            self.failed_calls += 1
        else:
            self.calls += 1
        entry = self._by_stage.setdefault(stage, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_sec": 0.0})
        entry["calls"] += 1
        entry["prompt_tokens"] += prompt
        entry["completion_tokens"] += completion
        entry["latency_sec"] += latency_sec

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_sec": round(self.latency_sec, 2),
            "by_stage": {
                stage: {
                    "calls": int(values["calls"]),
                    "prompt_tokens": int(values["prompt_tokens"]),
                    "completion_tokens": int(values["completion_tokens"]),
                    "latency_sec": round(values["latency_sec"], 2),
                }
                for stage, values in sorted(self._by_stage.items())
            },
        }


class ResponseShapeError(ValueError):
    """Raised by a response validator when valid JSON has the wrong shape.

    Kept separate from transport errors so the client can re-prompt with the
    validation error instead of blindly repeating the same request.
    """


class RunDeadlineExceeded(RuntimeError):
    """The run's cumulative model-time budget is spent.

    A per-request timeout bounds one call; this bounds the whole run so that
    exhausted retries cannot keep a scheduled job hanging.
    """


class ModelOutputError(ValueError):
    """The model returned text that is not usable JSON.

    Distinct from ResponseShapeError: the contract was never reached, so there is
    nothing to repair against. It is still the model's output, so it is worth
    asking again rather than failing the run outright.
    """


def _json_object_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_object" in text


def _is_retryable(exc: Exception) -> bool:
    """Network/rate-limit errors are worth repeating; caller errors are not.

    Checked by class rather than by name: the SDK raises APIConnectionError for a
    broken connection, but a raised httpx transport error can also reach here,
    and name matching alone misclassified it as a permanent caller error.
    """
    if isinstance(exc, ModelOutputError):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is an openai dependency
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(
        exc, (httpx.TransportError, httpx.TimeoutException, httpx.RemoteProtocolError)
    ):
        return True
    try:
        import openai
    except ImportError:  # pragma: no cover
        openai = None  # type: ignore[assignment]
    if openai is not None:
        if isinstance(exc, getattr(openai, "APIConnectionError", ())):
            return True
        if isinstance(exc, getattr(openai, "APITimeoutError", ())):
            return True
        if isinstance(exc, getattr(openai, "RateLimitError", ())):
            return True
        if isinstance(exc, getattr(openai, "InternalServerError", ())):
            return True

    name = type(exc).__name__.lower()
    if "ratelimit" in name or "internalserver" in name or "timeout" in name:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in {408, 409, 429} or status >= 500
    # A plain ValueError here means the request itself was malformed.
    return False


class DeepSeekClient:
    def __init__(self, config: dict[str, Any]):
        deepseek_cfg = config.get("deepseek", {})
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is not set")

        # max_retries=0 is deliberate: the SDK would otherwise retry twice
        # underneath this client's own retry loop, turning one logical call into
        # up to six requests and hiding the real attempt count.
        self.client = OpenAI(
            api_key=api_key,
            base_url=deepseek_cfg.get("base_url", "https://api.deepseek.com/v1"),
            max_retries=0,
            timeout=float(deepseek_cfg.get("timeout_sec", 120)),
        )
        self.model_topics = deepseek_cfg.get("model_topics", "deepseek-flash")
        self.model_writer = deepseek_cfg.get("model_writer", "deepseek-flash")
        self.max_retries = int(deepseek_cfg.get("max_retries", 1))
        self.temperature = float(deepseek_cfg.get("temperature", 0.2))
        self.force_json_object = bool(deepseek_cfg.get("force_json_object", True))
        self.retry_policy = RetryPolicy(
            attempts=self.max_retries + 1,
            backoff_sec=float(deepseek_cfg.get("retry_backoff_sec", 1.0)),
            max_backoff_sec=float(deepseek_cfg.get("retry_max_backoff_sec", 30.0)),
        )
        self.usage = TokenUsage()
        self.last_used_attempts = 0
        max_total = float(deepseek_cfg.get("max_total_seconds", 900) or 0)
        self._deadline = time.monotonic() + max_total if max_total > 0 else None

    def deadline_remaining(self) -> float | None:
        """Seconds left in the run budget, or None when unlimited."""
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def _check_deadline(self) -> None:
        remaining = self.deadline_remaining()
        if remaining is not None and remaining <= 0:
            self.usage.record("deadline", None, 0.0, failed=True)
            raise RunDeadlineExceeded(
                "model time budget exhausted for this run; aborting instead of retrying"
            )
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
        stage: str = "unknown",
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

        self._check_deadline()
        started = time.monotonic()
        for attempt in range(attempts):
            used_attempts = attempt + 1
            try:
                response = self._create_completion(model, system_prompt, current_prompt)
                self.usage.record(
                    stage,
                    getattr(response, "usage", None),
                    time.monotonic() - started,
                    retries=attempt,
                )
                content = response.choices[0].message.content or ""
                try:
                    payload = self._extract_json(content)
                except ValueError as exc:
                    # Retryable: the model produced something unusable.
                    raise ModelOutputError(str(exc)) from exc
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
                # Distinguish "the model gave us junk" from "the call itself
                # failed", because only the former means the provider answered.
                last_error_kind = "model output" if isinstance(exc, ModelOutputError) else "transport"
                logger.warning(
                    "DeepSeek call failed (attempt %d/%d): %s",
                    used_attempts,
                    attempts,
                    exc,
                )
                if not _is_retryable(exc):
                    logger.warning("Non-retryable DeepSeek error; giving up early")
                    break
                if used_attempts < attempts:
                    delay = backoff_delay(
                        used_attempts,
                        self.retry_policy.backoff_sec,
                        self.retry_policy.max_backoff_sec,
                    )
                    remaining = self.deadline_remaining()
                    if remaining is not None and delay >= remaining:
                        logger.warning(
                            "Model time budget would be exceeded by the next retry (%.1fs left); giving up",
                            max(remaining, 0.0),
                        )
                        break
                    logger.warning("Retrying DeepSeek call in %.1fs", delay)
                    time.sleep(delay)

        self.usage.record(stage, None, time.monotonic() - started, retries=used_attempts - 1, failed=True)
        self.last_used_attempts = used_attempts
        raise RuntimeError(
            f"DeepSeek request failed after {used_attempts} attempt(s) "
            f"({last_error_kind} error): {last_error}"
        )
