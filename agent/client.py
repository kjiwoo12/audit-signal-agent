"""Anthropic Messages API 클라이언트.

SDK 대신 urllib 로 직접 호출한다. 이 프로젝트는 표준 라이브러리만 쓴다.

API 키는 헤더에 넣는 것 외에 어디에도 쓰지 않는다. 로그·전사기록·예외 메시지
어디에도 남기지 않는다. 예외를 `from None` 으로 끊는 것도 같은 이유다 —
예외 사슬에 요청 객체가 붙으면 헤더가 트레이스백에 찍힌다.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# 최신 세대. 더 무거운 판단이 필요하면 --model claude-opus-5.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8192

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class ApiKeyMissing(RuntimeError):
    pass


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Anthropic API {status}: {body[:800]}")


def load_api_key(explicit: str | None = None) -> str:
    """환경변수 → .env 순으로 찾는다. .env 는 .gitignore 에 있다."""
    if explicit:
        return explicit.strip()

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and key.strip():
        return key.strip()

    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == "ANTHROPIC_API_KEY":
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value

    raise ApiKeyMissing(
        "ANTHROPIC_API_KEY 가 없다. 환경변수에 넣거나 프로젝트 루트 .env 에 적을 것.\n"
        '  Windows : setx ANTHROPIC_API_KEY "..."   (새 터미널부터 적용)\n'
        "  bash    : export ANTHROPIC_API_KEY=...\n"
        "  .env    : ANTHROPIC_API_KEY=...\n\n"
        ".env 는 .gitignore 에 있다. 키를 코드나 커밋에 적지 말 것.\n"
        "키 없이 무엇을 보내는지만 확인하려면: python -m agent.run --dry-run"
    )


class Client:
    """Messages API 호출 한 겹. 재시도와 토큰 집계만 얹는다."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = 300,
        max_retries: int = 4,
    ):
        self._key = load_api_key(api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def messages(self, system: str, messages: list, tools: list | None = None) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_err = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(API_URL, data=data, method="POST")
            req.add_header("content-type", "application/json")
            req.add_header("anthropic-version", API_VERSION)
            req.add_header("x-api-key", self._key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status = e.code
                text = e.read().decode("utf-8", errors="replace")
                if status in RETRY_STATUS and attempt < self.max_retries:
                    self._sleep(attempt, e.headers.get("retry-after"))
                    continue
                raise ApiError(status, text) from None
            except urllib.error.URLError as e:
                last_err = e
                if attempt < self.max_retries:
                    self._sleep(attempt, None)
                    continue
                raise RuntimeError(f"API 연결 실패: {e.reason}") from None
            else:
                self._count(body)
                return body

        raise RuntimeError(f"재시도 소진: {last_err}")

    def _sleep(self, attempt: int, retry_after) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 60))
                return
            except (TypeError, ValueError):
                pass
        time.sleep(2**attempt)

    def _count(self, body: dict) -> None:
        u = body.get("usage") or {}
        self.usage["requests"] += 1
        self.usage["input_tokens"] += u.get("input_tokens", 0)
        self.usage["output_tokens"] += u.get("output_tokens", 0)
