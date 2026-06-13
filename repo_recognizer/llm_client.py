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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class LLMError(RuntimeError):
    """Raised when the LLM call fails (network, auth, bad response)."""
    pass


class LLMClient:
    """OpenAI-compatible chat completion client."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        system_prompt: str = "",
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("REPO_LLM_API_KEY") or "sk-0498658d5f4d49098f7d1153c7b09652"
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("REPO_LLM_BASE_URL") or "https://api.deepseek.com/v1"
        ).rstrip("/")
        self.model = model or os.getenv("REPO_LLM_MODEL") or "deepseek-chat"
        self.system_prompt = system_prompt
        self.timeout = timeout

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
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            parsed = json.loads(raw)
            return parsed["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Invalid LLM response structure: {raw[:1000]}") from exc

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
    ) -> "LLMClient":
        """Factory: create an LLMClient with a system prompt loaded from a .md file."""
        prompt = cls.load_prompt(prompt_filename)
        return cls(api_key=api_key, base_url=base_url, model=model, system_prompt=prompt, timeout=timeout)
