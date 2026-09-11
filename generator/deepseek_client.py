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
    """Model usage for one run, counted at the HTTP request boundary.

    Three distinct numbers, deliberately not conflated:

    - ``logical_calls``: how many times ``chat_json`` was entered.
    - ``requests``: how many HTTP requests were actually issued, including
      retries and the JSON-mode fallback request.
    - ``failed_requests``: how many of those requests errored.

    Token totals only cover responses that were actually received; a request
    that failed before responding has no usage to attribute.
    """

    logical_calls: int = 0
    requests: int = 0
    failed_requests: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_sec: float = 0.0
    request_latency_sec: float = 0.0
    _by_stage: dict[str, dict[str, float]] = field(default_factory=dict)

    def begin_call(self, stage: str) -> None:
        self.logical_calls += 1
        entry = self._stage(stage)
        entry["logical_calls"] += 1

    def _stage(self, stage: str) -> dict[str, float]:
        return self._by_stage.setdefault(
            stage,
            {
                "logical_calls": 0,
                "requests": 0,
                "failed_requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "request_latency_sec": 0.0,
            },
        )

    def record(
        self,
        stage: str,
        usage: Any,
        request_latency_sec: float,
        retries: int = 0,
        failed: bool = False,
    ) -> None:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
        completion = int(getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.requests += 1
        self.request_latency_sec += request_latency_sec
        self.latency_sec += request_latency_sec
        self.retries += retries
        if failed:
            self.failed_requests += 1
        entry = self._stage(stage)
        entry["requests"] += 1
        entry["prompt_tokens"] += prompt
        entry["completion_tokens"] += completion
        entry["request_latency_sec"] += request_latency_sec
        if failed:
            entry["failed_requests"] += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_calls": self.logical_calls,
            "requests": self.requests,
            "failed_requests": self.failed_requests,
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "request_latency_sec": round(self.request_latency_sec, 2),
            "by_stage": {
                stage: {
                    "logical_calls": int(v["logical_calls"]),
                    "requests": int(v["requests"]),
                    "failed_requests": int(v["failed_requests"]),
                    "prompt_tokens": int(v["prompt_tokens"]),
                    "completion_tokens": int(v["completion_tokens"]),
                    "request_latency_sec": round(v["request_latency_sec"], 2),
                }
                for stage, v in sorted(self._by_stage.items())
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


class JsonModeUnsupported(RuntimeError):
    """The provider rejected response_format; retry the same request without it."""


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
    if isinstance(exc, (ModelOutputError, JsonModeUnsupported)):
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
        self.request_timeout = float(deepseek_cfg.get("timeout_sec", 120))
        self.client = OpenAI(
            api_key=api_key,
            base_url=deepseek_cfg.get("base_url", "https://api.deepseek.com/v1"),
            max_retries=0,
            timeout=self.request_timeout,
        )
        self.model_topics = deepseek_cfg.get("model_topics", "deepseek-flash")
        self.model_writer = deepseek_cfg.get("model_writer", "deepseek-flash")
        self.max_retries = int(deepseek_cfg.get("max_retries", 1))
        self.temperature = float(deepseek_cfg.get("temperature", 0.2))
        self.force_json_object = bool(deepseek_cfg.get("force_json_object", True))
        # Set once the API rejects response_format so we stop asking for it. It
        # must only change here or on a real rejection, never once per call.
        self._json_object_disabled = not self.force_json_object
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
            raise RunDeadlineExceeded(
                "model time budget exhausted for this run; aborting instead of retrying"
            )

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

    def _request_timeout(self) -> float:
        """The configured timeout, shortened to whatever budget is left."""
        remaining = self.deadline_remaining()
        if remaining is None:
            return self.request_timeout
        return max(0.001, min(self.request_timeout, remaining))

    def _create_completion(self, model: str, system_prompt: str, user_prompt: str):
        self._check_deadline()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "timeout": self._request_timeout(),
        }
        if not self._json_object_disabled:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "response_format" not in kwargs or not _json_object_unsupported(exc):
                raise
            # Remember the rejection and let the single retry loop re-issue the
            # request, so the fallback is budgeted, counted and backoff-ed like
            # any other attempt instead of slipping in an uncounted extra call.
            logger.warning("response_format json_object rejected: %s", exc)
            self._json_object_disabled = True
            raise JsonModeUnsupported(str(exc)) from exc

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

        self.usage.begin_call(stage)
        for attempt in range(attempts):
            used_attempts = attempt + 1
            attempt_started = time.monotonic()
            response: Any = None
            try:
                # The request is issued here, so a failure below means a real
                # request was made and consumed budget.
                request_sent = True
                response = self._create_completion(model, system_prompt, current_prompt)
                content = response.choices[0].message.content or ""
                try:
                    payload = self._extract_json(content)
                except ValueError as exc:
                    # Retryable: the model produced something unusable.
                    raise ModelOutputError(str(exc)) from exc
                if validator is not None:
                    payload = validator(payload)
                self.usage.record(
                    stage,
                    getattr(response, "usage", None),
                    time.monotonic() - attempt_started,
                    retries=attempt,
                )
                self.last_used_attempts = used_attempts
                return payload
            except RunDeadlineExceeded:
                # Budget expiry is a decision, not a failure to retry, and must
                # reach the caller as itself.
                raise
            except ResponseShapeError as exc:
                last_error = exc
                last_error_kind = "schema"
                self.usage.record(
                    stage,
                    getattr(response, "usage", None),
                    time.monotonic() - attempt_started,
                    retries=attempt,
                    failed=True,
                )
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
                if isinstance(exc, (ModelOutputError, JsonModeUnsupported)):
                    last_error_kind = "model output"
                else:
                    last_error_kind = "transport"
                # Recorded here for transport/model-output failures; the schema
                # branch above records its own so an attempt is never counted twice.
                self.usage.record(
                    stage,
                    getattr(response, "usage", None),
                    time.monotonic() - attempt_started,
                    retries=attempt,
                    failed=True,
                )
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

        self.last_used_attempts = used_attempts
        raise RuntimeError(
            f"DeepSeek request failed after {used_attempts} attempt(s) "
            f"({last_error_kind} error): {last_error}"
        )
