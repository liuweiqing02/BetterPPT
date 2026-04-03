from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppException

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 45
_DEFAULT_MAX_RETRIES = 2
_BACKOFF_SECONDS = (1.0, 2.0)


@dataclass(slots=True)
class LLMChatCompletionResult:
    content: str
    usage: dict[str, Any]
    raw: dict[str, Any]


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip('/')


def _build_chat_completions_url() -> str:
    settings = get_settings()
    if not settings.llm_api_base:
        raise AppException(status_code=500, code=9001, message='LLM API base is not configured')
    return f"{_normalize_base_url(settings.llm_api_base)}/chat/completions"


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json',
    }


def _parse_json_body(body: bytes | None) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode('utf-8', errors='replace'))
        return parsed if isinstance(parsed, dict) else {'data': parsed}
    except Exception:
        return {'raw_text': body.decode('utf-8', errors='replace')}


def _extract_error_message(payload: dict[str, Any], fallback: str) -> str:
    error_obj = payload.get('error')
    if isinstance(error_obj, dict):
        for key in ('message', 'details', 'detail', 'msg'):
            value = error_obj.get(key)
            if value:
                return str(value)
    for key in ('message', 'detail'):
        value = payload.get(key)
        if value:
            return str(value)
    return fallback


def _extract_content(payload: dict[str, Any]) -> str:
    choices = payload.get('choices')
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get('message')
            if isinstance(message, dict):
                content = message.get('content')
                if content is not None:
                    return str(content)
            content = first_choice.get('text')
            if content is not None:
                return str(content)
    return ''


def _extract_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get('usage')
    if isinstance(usage, dict):
        return usage
    return {}


def _should_retry_http_error(error: urllib.error.HTTPError) -> bool:
    return error.code == 429 or 500 <= error.code <= 599


def _raise_http_error(error: urllib.error.HTTPError) -> None:
    body = error.read() if hasattr(error, 'read') else b''
    payload = _parse_json_body(body)
    message = _extract_error_message(payload, f'LLM request failed with HTTP {error.code}')
    raise AppException(
        status_code=error.code if error.code >= 400 else 502,
        code=9002 if error.code == 429 or 500 <= error.code <= 599 else 9001,
        message=message,
        data={
            'http_status': error.code,
            'response': payload,
        },
    ) from error


def call_chat_completions(
    *,
    model: str | None = None,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    max_retries: int | None = None,
) -> LLMChatCompletionResult:
    settings = get_settings()
    api_key = (settings.llm_api_key or '').strip()
    if not api_key:
        raise AppException(status_code=500, code=9001, message='LLM API key is not configured')
    effective_timeout = timeout_seconds if timeout_seconds is not None else int(settings.llm_request_timeout_seconds or _DEFAULT_TIMEOUT_SECONDS)
    effective_timeout = max(3, effective_timeout)
    effective_retries = max_retries if max_retries is not None else int(settings.llm_request_max_retries or _DEFAULT_MAX_RETRIES)
    effective_retries = max(0, effective_retries)

    request_payload: dict[str, Any] = {
        'model': model or settings.llm_model,
        'messages': messages,
    }
    if temperature is not None:
        request_payload['temperature'] = temperature
    if max_tokens is not None:
        request_payload['max_tokens'] = max_tokens

    url = _build_chat_completions_url()
    headers = _build_headers(api_key)
    body = json.dumps(request_payload, ensure_ascii=False).encode('utf-8')

    last_error: Exception | None = None
    for attempt in range(effective_retries + 1):
        try:
            request = urllib.request.Request(url=url, data=body, headers=headers, method='POST')
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                raw_bytes = response.read()
                payload = _parse_json_body(raw_bytes)
                content = _extract_content(payload)
                usage = _extract_usage(payload)
                return LLMChatCompletionResult(content=content, usage=usage, raw=payload)
        except urllib.error.HTTPError as exc:
            if _should_retry_http_error(exc) and attempt < effective_retries:
                delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning('LLM request retryable HTTP error %s on attempt %s, sleeping %.1fs', exc.code, attempt + 1, delay)
                time.sleep(delay)
                last_error = exc
                continue
            _raise_http_error(exc)
        except urllib.error.URLError as exc:
            if attempt < effective_retries:
                delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning('LLM request network error on attempt %s, sleeping %.1fs: %s', attempt + 1, delay, exc)
                time.sleep(delay)
                last_error = exc
                continue
            last_error = exc
            break
        except TimeoutError as exc:
            if attempt < effective_retries:
                delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning('LLM request timeout on attempt %s, sleeping %.1fs: %s', attempt + 1, delay, exc)
                time.sleep(delay)
                last_error = exc
                continue
            last_error = exc
            break

    raise AppException(
        status_code=502,
        code=9002,
        message='LLM request failed after retries',
        data={'detail': str(last_error) if last_error else 'unknown error'},
    )


__all__ = ['LLMChatCompletionResult', 'call_chat_completions']
