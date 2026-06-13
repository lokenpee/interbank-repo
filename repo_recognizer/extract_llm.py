"""
Extract LLM — identifies trades and their fields from chat context.

Calls the Extract LLM with:
- Full conversation history
- Current saved trades state
- Current message

Returns a structured ExtractResult.
"""

from __future__ import annotations

import json
from typing import Any

from .llm_client import LLMClient, LLMError
from .models import (
    ConversationState,
    ExtractResult,
    Message,
    TradeChange,
    TradeState,
    clean_text,
)


class ExtractLLM:
    """Wraps the Extract LLM for trade element identification."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    @property
    def available(self) -> bool:
        return self.client.available

    def recognize(
        self,
        current_message: Message,
        state: ConversationState,
    ) -> ExtractResult:
        """Run the Extract LLM and return a parsed ExtractResult.

        Raises:
            LLMError: On API or parse failures.
        """
        payload = self._build_payload(current_message, state)
        user_content = json.dumps(payload, ensure_ascii=False, indent=2)

        raw = self.client.chat_json(user_content)

        return self._parse_result(raw)

    def _build_payload(
        self,
        current_message: Message,
        state: ConversationState,
    ) -> dict[str, Any]:
        return {
            "current_state": state.to_prompt_state(),
            "conversation_history": [
                msg.to_prompt_dict() for msg in state.messages
            ],
            "current_message": current_message.to_prompt_dict(),
        }

    @staticmethod
    def _parse_result(raw: dict[str, Any]) -> ExtractResult:
        result = ExtractResult()
        result.counterparty = clean_text(raw.get("counterparty", ""))

        for item in raw.get("trades", []) or []:
            if not isinstance(item, dict):
                continue
            trade = TradeState(
                id=clean_text(item.get("id", "")),
                account=clean_text(item.get("account", "")),
                amount=clean_text(item.get("amount", "")),
                term=clean_text(item.get("term", "")),
                price=clean_text(item.get("price", "")),
                direction=clean_text(item.get("direction", "")),
                evidence=list(item.get("evidence", []) or []),
            )
            result.trades.append(trade)

        for item in raw.get("changes", []) or []:
            if not isinstance(item, dict):
                continue
            result.changes.append(
                TradeChange(
                    type=clean_text(item.get("type", "noop")),
                    trade_id=clean_text(item.get("trade_id", "")),
                    reason=clean_text(item.get("reason", "")),
                )
            )

        return result
