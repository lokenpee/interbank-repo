"""
Judge LLM — determines trade status from chat context.

Calls the Judge LLM with:
- Full conversation history + current message
- Extract LLM output (trades list)
- Current state

Returns a structured JudgeResult.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_client import LLMClient, LLMError
from .models import (
    ConversationState,
    JudgeResult,
    Message,
    clean_text,
    trade_index_for_prompt,
)


class JudgeLLM:
    """Wraps the Judge LLM for trade status determination."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    @property
    def available(self) -> bool:
        return self.client.available

    def judge(
        self,
        current_message: Message,
        state: ConversationState,
        normalized_extract_result: dict[str, Any],
    ) -> JudgeResult:
        """Run the Judge LLM and return a parsed JudgeResult.

        Args:
            current_message: The message being processed.
            state: Current conversation state (including history before this message).
            normalized_extract_result: Full normalized Extract LLM output
                (trades + linking_reason + status_signals + ambiguity).

        Raises:
            LLMError: On API or parse failures.
        """
        payload = self._build_payload(current_message, state, normalized_extract_result)
        user_content = json.dumps(payload, ensure_ascii=False, indent=2)

        raw = self.client.chat_json(user_content)

        return self._parse_result(raw)

    def _build_payload(
        self,
        current_message: Message,
        state: ConversationState,
        normalized_extract_result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "conversation_history": [
                msg.to_prompt_dict() for msg in state.messages
            ]
            + [current_message.to_prompt_dict()],
            "current_message": current_message.to_prompt_dict(),
            "normalized_extract_result": normalized_extract_result,
            "current_state": state.to_prompt_state(),
            "known_trade_index": trade_index_for_prompt(state),
        }

    @staticmethod
    def _parse_result(raw: dict[str, Any]) -> JudgeResult:
        result = JudgeResult()
        for item in raw.get("trades", []) or []:
            if not isinstance(item, dict):
                continue
            confidence_raw = item.get("confidence", 0.5)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.5
            result.trades.append({
                "id": clean_text(item.get("id", "")),
                "status": clean_text(item.get("status", "negotiating")),
                "intent": clean_text(item.get("intent", "")),
                "confidence": confidence,
                "reason": clean_text(item.get("reason", "")),
            })
        return result
