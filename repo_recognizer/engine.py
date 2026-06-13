"""Dual-LLM orchestration for repo chat recognition."""

from __future__ import annotations

from typing import Any

from .extract_llm import ExtractLLM
from .judge_llm import JudgeLLM
from .llm_client import LLMError
from .models import Message, ProcessedRow, StateChange
from .state_store import StateStore


class RecognizerEngine:
    """Orchestrates Extract -> ID normalize -> Judge -> Merge."""

    def __init__(self, extract_llm: ExtractLLM, judge_llm: JudgeLLM) -> None:
        self.extract_llm = extract_llm
        self.judge_llm = judge_llm
        self.store = StateStore()

    def process(self, message: Message) -> ProcessedRow:
        state = self.store.get_state(message.con_id)
        if message.tradername:
            state.tradername = message.tradername

        used_llm = False
        llm_error = ""
        extract_result_dict: dict[str, Any] = {}
        normalized_extract_result_dict: dict[str, Any] = {}
        judge_result_dict: dict[str, Any] = {}
        state_changes: list[StateChange] = []

        extract_result = None
        normalized_extract_result = None
        judge_result = None

        if self.extract_llm.available:
            try:
                extract_result = self.extract_llm.recognize(message, state)
                extract_result_dict = extract_result.to_dict()
                normalized_extract_result = self.store.normalize_extract_ids(state, extract_result)
                normalized_extract_result_dict = normalized_extract_result.to_dict()
                used_llm = True
            except LLMError as exc:
                llm_error = f"Extract LLM: {exc}"
        else:
            llm_error = "Extract LLM not available (no API key)"

        if normalized_extract_result is not None:
            if self.judge_llm.available:
                try:
                    judge_result = self.judge_llm.judge(
                        message,
                        state,
                        [trade.to_dict() for trade in normalized_extract_result.trades],
                    )
                    judge_result_dict = judge_result.to_dict()
                    used_llm = True
                except LLMError as exc:
                    llm_error = _append_error(llm_error, f"Judge LLM: {exc}")
            else:
                llm_error = _append_error(llm_error, "Judge LLM not available (no API key)")

            state_changes.extend(
                self.store.merge_extract_result(state, normalized_extract_result, message)
            )
            if judge_result is not None:
                state_changes.extend(self.store.merge_judge_result(state, judge_result, message))

        self.store.finalize_message(state, message)

        return ProcessedRow(
            message=message,
            extract_result=extract_result_dict,
            normalized_extract_result=normalized_extract_result_dict,
            judge_result=judge_result_dict,
            final_state=state.to_full_result(),
            public_result=state.to_public_result(),
            state_changes=[change.to_dict() for change in state_changes],
            used_llm=used_llm,
            llm_error=llm_error,
        )


def _append_error(existing: str, new: str) -> str:
    if existing:
        return f"{existing}; {new}"
    return new
