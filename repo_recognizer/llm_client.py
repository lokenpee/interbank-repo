"""
Generic OpenAI-compatible LLM client.

Supports:
- Chat completion with configurable system prompt
- JSON-structured output
- Prompt loading from .md files
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class LLMError(RuntimeError):
    """Raised when the LLM call fails (network, auth, bad response)."""
    pass


def _load_local_config(key: str) -> str:
    """Load a value from the local config file (repo root / config.json).

    This file is NOT tracked by git — see .gitignore.
    """
    try:
        config_path = Path(__file__).resolve().parent.parent / "config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            return str(config.get(key, ""))
    except Exception:
        pass
    return ""


class LLMClient:
    """OpenAI-compatible chat completion client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        system_prompt: str = "",
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("REPO_LLM_API_KEY") or _load_local_config("api_key")
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("REPO_LLM_BASE_URL") or _load_local_config("base_url") or "https://api.deepseek.com/v1"
        ).rstrip("/")
        self.model = model or os.getenv("REPO_LLM_MODEL") or _load_local_config("model") or "deepseek-chat"
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        user_content: str,
        system_prompt: str | None = None,
        temperature: float = 0,
    ) -> str:
        """Send a chat completion request and return the raw text response.

        Automatically retries on transient failures (timeout, connection error,
        HTTP 5xx) up to ``self.max_retries`` times with exponential backoff.
        Does NOT retry on HTTP 4xx (auth/bad request) or JSON parse errors.

        Args:
            user_content: The user message (JSON string for structured prompts).
            system_prompt: Override the instance-level system prompt.
            temperature: Model temperature (default 0 for deterministic output).

        Returns:
            The raw text content from the assistant response.

        Raises:
            LLMError: On network errors, auth failures, or malformed responses.
        """
        if not self.available:
            raise LLMError("LLM is disabled because no API key was found.")

        sys_prompt = system_prompt if system_prompt is not None else self.system_prompt

        messages: list[dict[str, str]] = []
        if sys_prompt:
            messages.append({"role": "system", "content": sys_prompt})
        messages.append({"role": "user", "content": user_content})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            try:
                request = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=data,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")

                parsed = json.loads(raw)
                return parsed["choices"][0]["message"]["content"]

            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # 5xx: transient server error — retry. 4xx: client error — don't.
                if 500 <= exc.code < 600 and attempt < self.max_retries:
                    last_error = f"HTTP {exc.code} (attempt {attempt+1}/{self.max_retries+1})"
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc

            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    last_error = f"{type(exc).__name__} (attempt {attempt+1}/{self.max_retries+1}): {exc}"
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"LLM request failed after {self.max_retries+1} attempts: {exc}") from exc

            except (KeyError, IndexError) as exc:
                raise LLMError(f"Invalid LLM response structure: {raw[:1000]}") from exc

            except json.JSONDecodeError as exc:
                raise LLMError(f"LLM returned invalid JSON: {raw[:1000]}") from exc

        # Should be unreachable, but just in case all retries exhausted
        raise LLMError(f"LLM request exhausted {self.max_retries+1} attempts. Last: {last_error}")

    def chat_json(
        self,
        user_content: str,
        system_prompt: str | None = None,
        temperature: float = 0,
    ) -> dict[str, Any]:
        """Send a chat completion request and parse the response as JSON.

        Returns:
            Parsed JSON dict.

        Raises:
            LLMError: On network errors, auth failures, or JSON parse failures.
        """
        text = self.chat(user_content, system_prompt=system_prompt, temperature=temperature)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM returned invalid JSON: {text[:1000]}") from exc

    # ---- convenience helpers ----

    @staticmethod
    def load_prompt(filename: str) -> str:
        """Load a prompt from a .md file relative to the prompts/ directory."""
        prompts_dir = Path(__file__).resolve().parent / "prompts"
        path = prompts_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8").strip()

    @classmethod
    def with_prompt(
        cls,
        prompt_filename: str,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> "LLMClient":
        """Factory: create an LLMClient with a system prompt loaded from a .md file."""
        prompt = cls.load_prompt(prompt_filename)
        return cls(api_key=api_key, base_url=base_url, model=model, system_prompt=prompt, timeout=timeout, max_retries=max_retries)
