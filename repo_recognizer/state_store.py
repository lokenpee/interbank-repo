"""Pure-mechanical state store.

This module must not infer business meaning. It only:
- creates/retrieves per-conversation state
- stabilizes trade ids returned by the Extract LLM
- merges Extract/Judge outputs by id
- records field-level provenance and mechanical diffs
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .models import (
    ConversationState,
    ExtractResult,
    JudgeResult,
    Message,
    StateChange,
    TradeChange,
    TradeState,
    clean_text,
    row_ref,
)


class StateStore:
    """In-memory store of per-conversation state."""

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}

    def get_state(self, con_id: str) -> ConversationState:
        if con_id not in self._states:
            self._states[con_id] = ConversationState(con_id=con_id)
        return self._states[con_id]

    def normalize_extract_ids(
        self,
        state: ConversationState,
        extract_result: ExtractResult,
    ) -> ExtractResult:
        """Return a copy of ExtractResult with stable ids assigned.

        The Extract LLM may return empty ids for new trades. Before Judge LLM sees
        those trades, ids must be stable; otherwise verdicts cannot be merged.
        """
        normalized = deepcopy(extract_result)
        seen_ids: set[str] = set()
        id_map: dict[str, str] = {}

        for trade in normalized.trades:
            raw_id = clean_text(trade.id)
            requested_id = id_map.get(raw_id, raw_id)
            if requested_id and state.get_trade_by_id(requested_id) and requested_id not in seen_ids:
                trade.id = requested_id
            else:
                trade.id = state.allocate_id()
            if raw_id:
                id_map[raw_id] = trade.id
            seen_ids.add(trade.id)

        # Keep changes aligned with normalized trade ids. This is mechanical id
        # bookkeeping, not a business decision.
        trade_ids = [trade.id for trade in normalized.trades]
        for idx, change in enumerate(normalized.changes):
            raw_change_id = clean_text(change.trade_id)
            if raw_change_id in id_map:
                change.trade_id = id_map[raw_change_id]
            elif not raw_change_id and idx < len(trade_ids):
                change.trade_id = trade_ids[idx]

        return normalized

    def ensure_active_trades_for_judge(
        self,
        state: ConversationState,
        extract_result: ExtractResult,
    ) -> ExtractResult:
        """Return ExtractResult plus unchanged active trades for Judge context.

        Extract LLM should output the full active book, but if it omits an
        unchanged existing trade, Judge would otherwise lose that trade from its
        per-row verdict surface. This is a mechanical state carry-forward.
        """
        expanded = deepcopy(extract_result)
        seen_ids = {clean_text(trade.id) for trade in expanded.trades if clean_text(trade.id)}
        deleted_ids = {
            clean_text(change.trade_id)
            for change in expanded.changes
            if clean_text(change.type) == "delete"
        }

        for trade in state.active_trades():
            if trade.id not in seen_ids and trade.id not in deleted_ids:
                expanded.trades.append(deepcopy(trade))
                expanded.changes.append(
                    TradeChange(
                        type="noop",
                        trade_id=trade.id,
                        reason="系统机械补齐已有活跃交易，供 Judge 做全量状态判断",
                    )
                )
        return expanded

    def merge_extract_result(
        self,
        state: ConversationState,
        extract_result: ExtractResult,
        message: Message,
    ) -> list[StateChange]:
        changes: list[StateChange] = []

        if extract_result.counterparty:
            before = state.counterparty
            after = clean_text(extract_result.counterparty)
            if after and before != after:
                state.counterparty = after
                changes.append(StateChange("", "counterparty", before, after, row_ref(message)))

        for source_trade in extract_result.trades:
            trade_id = clean_text(source_trade.id)
            if not trade_id:
                trade_id = state.allocate_id()
                source_trade.id = trade_id

            target = state.get_trade_by_id(trade_id)
            if target is None:
                target = TradeState(id=trade_id)
                state.trades.append(target)
                changes.append(StateChange(trade_id, "_created", "", "true", row_ref(message)))

            changes.extend(self._apply_trade_fields(target, source_trade, message))

        for change in extract_result.changes:
            if change.type == "delete":
                trade_id = clean_text(change.trade_id)
                if trade_id:
                    archived = self.archive_trade(state, trade_id, message, reason=change.reason)
                    if archived:
                        changes.append(StateChange(trade_id, "_archived", "false", "true", row_ref(message)))

        return changes

    def merge_judge_result(
        self,
        state: ConversationState,
        judge_result: JudgeResult,
        message: Message,
    ) -> list[StateChange]:
        changes: list[StateChange] = []
        for verdict in judge_result.trades:
            trade_id = clean_text(verdict.get("id", ""))
            if not trade_id:
                continue
            trade = state.get_trade_by_id(trade_id)
            if trade is None:
                continue

            new_status = clean_text(verdict.get("status", ""))
            if new_status in {"negotiating", "confirmed", "rejected", "cancelled", "detail_pending"}:
                changes.extend(self._set_field(trade, "status", new_status, message))

            new_intent = clean_text(verdict.get("intent", ""))
            if new_intent:
                changes.extend(self._set_field(trade, "intent", new_intent, message))

            confidence = verdict.get("confidence", None)
            if isinstance(confidence, (int, float)):
                before = trade.confidence
                after = float(confidence)
                if before != after:
                    trade.confidence = after
                    changes.append(StateChange(trade.id, "confidence", str(before), str(after), row_ref(message)))

            reason = clean_text(verdict.get("reason", ""))
            if reason:
                self._append_sources(trade, "status", [reason])

        return changes

    def archive_trade(
        self,
        state: ConversationState,
        trade_id: str,
        message: Message,
        reason: str = "",
    ) -> bool:
        for idx, trade in enumerate(state.trades):
            if trade.id == trade_id:
                archived = state.trades.pop(idx)
                archived.status = "cancelled" if archived.status != "rejected" else archived.status
                if reason:
                    archived.evidence.append(reason)
                archived.evidence.append(row_ref(message))
                state.archived_trades.append(archived)
                return True
        return False

    def finalize_message(self, state: ConversationState, message: Message) -> None:
        state.messages.append(message)

    def _apply_trade_fields(
        self,
        target: TradeState,
        source: TradeState,
        message: Message,
    ) -> list[StateChange]:
        changes: list[StateChange] = []
        for field in ["account", "amount", "term", "price", "direction"]:
            value = clean_text(getattr(source, field, ""))
            if value:
                changes.extend(self._set_field(target, field, value, message))

        evidence = [clean_text(item) for item in source.evidence if clean_text(item)]
        if evidence:
            target.evidence.extend(evidence)
            target.evidence = target.evidence[-8:]
            self._append_sources(target, "evidence", evidence)
        return changes

    def _set_field(
        self,
        trade: TradeState,
        field: str,
        value: str,
        message: Message,
    ) -> list[StateChange]:
        before = clean_text(getattr(trade, field, ""))
        after = clean_text(value)
        if not after or before == after:
            return []
        setattr(trade, field, after)
        source = row_ref(message)
        self._append_sources(trade, field, [source])
        return [StateChange(trade.id, field, before, after, source)]

    @staticmethod
    def _append_sources(trade: TradeState, field: str, sources: Iterable[str]) -> None:
        bucket = trade.field_sources.setdefault(field, [])
        for source in sources:
            source = clean_text(source)
            if source:
                bucket.append(source)
        trade.field_sources[field] = bucket[-8:]
