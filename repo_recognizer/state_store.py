"""
Pure-mechanical state store — zero business logic.

Responsibilities:
- Create / retrieve ConversationState by con_ID
- Merge ExtractResult into state (allocate IDs, update fields)
- Merge JudgeResult into state (update status/intent/confidence)
- Serialize state to output dicts

No regex, no keyword matching, no domain heuristics.
"""

from __future__ import annotations

from .models import (
    ConversationState,
    ExtractResult,
    JudgeResult,
    Message,
    TradeState,
    clean_text,
)


class StateStore:
    """In-memory store of per-conversation state."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}

    def get_state(self, con_id: str) -> ConversationState:
        """Get or create the ConversationState for a given con_ID."""
        if con_id not in self._states:
            self._states[con_id] = ConversationState(con_id=con_id)
        return self._states[con_id]

    def merge_extract_result(
        self,
        state: ConversationState,
        extract_result: ExtractResult,
    ) -> None:
        """
        Mechanically merge the Extract LLM output into the conversation state.

        Rules (pure mechanics, no business inference):
        - If trade.id already exists in state → update its fields
        - If trade.id is new or empty → allocate a new Tn id
        - Respect changes[]: 'delete' entries remove the trade
        - Update counterparty if provided and non-empty
        """
        if extract_result.counterparty:
            state.counterparty = clean_text(extract_result.counterparty)

        # Build a set of ids the LLM wants to keep
        llm_trade_ids: set[str] = set()

        for llm_trade in extract_result.trades:
            tid = clean_text(llm_trade.id)

            # Try to match an existing trade by id
            existing = state.get_trade_by_id(tid) if tid else None

            if existing is not None:
                # Update existing trade fields (only overwrite with non-empty)
                self._apply_trade_fields(existing, llm_trade)
                llm_trade_ids.add(existing.id)
            else:
                # New trade — allocate id
                new_id = state.allocate_id()
                new_trade = TradeState(id=new_id)
                self._apply_trade_fields(new_trade, llm_trade)
                new_trade.id = new_id  # ensure id is not overwritten
                state.trades.append(new_trade)
                llm_trade_ids.add(new_id)

        # Process explicit deletions from changes[]
        for change in extract_result.changes:
            if change.type == "delete":
                tid = clean_text(change.trade_id)
                if tid:
                    state.trades = [t for t in state.trades if t.id != tid]
                    llm_trade_ids.discard(tid)

    @staticmethod
    def _apply_trade_fields(target: TradeState, source: TradeState) -> None:
        """Copy non-empty fields from source to target. Does NOT overwrite id."""
        for field in ["account", "amount", "term", "price", "direction"]:
            val = clean_text(getattr(source, field, ""))
            if val:
                setattr(target, field, val)
        # evidence: append new items
        if source.evidence:
            target.evidence.extend(source.evidence)
            target.evidence = target.evidence[-8:]  # keep last 8

    def merge_judge_result(
        self,
        state: ConversationState,
        judge_result: JudgeResult,
    ) -> None:
        """
        Mechanically merge the Judge LLM output into the conversation state.

        For each trade verdict in judge_result:
        - Match by id, update status / intent / confidence
        """
        for verdict in judge_result.trades:
            tid = clean_text(verdict.get("id", ""))
            if not tid:
                continue
            trade = state.get_trade_by_id(tid)
            if trade is None:
                continue  # skip verdicts for unknown ids

            new_status = clean_text(verdict.get("status", ""))
            if new_status and new_status in {
                "negotiating", "confirmed", "rejected", "cancelled", "detail_pending",
            }:
                trade.status = new_status

            new_intent = clean_text(verdict.get("intent", ""))
            if new_intent:
                trade.intent = new_intent

            confidence = verdict.get("confidence", None)
            if isinstance(confidence, (int, float)):
                trade.confidence = float(confidence)

    def finalize_message(self, state: ConversationState, message: Message) -> None:
        """Append the processed message to conversation history."""
        state.messages.append(message)
