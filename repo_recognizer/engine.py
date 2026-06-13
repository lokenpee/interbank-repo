"""
RecognizerEngine — orchestrates the dual-LLM pipeline.

Flow per message:
  1. Get/create ConversationState for this con_ID
  2. Extract LLM  → identify trades & changes
  3. Judge LLM   → determine trade statuses
  4. State merge  → mechanically apply results to state
  5. Finalize     → append message to history

No regex-based business logic. All trade understanding is done by the two LLMs.
"""

from __future__ import annotations

from typing import Any

from .extract_llm import ExtractLLM
from .judge_llm import JudgeLLM
from .llm_client import LLMError
from .models import Message, ProcessedRow
from .state_store import StateStore


class RecognizerEngine:
    """Orchestrates the Extract → Judge → Merge pipeline."""

    def __init__(
        self,
        extract_llm: ExtractLLM,
        judge_llm: JudgeLLM,
    ) -> None:
        self.extract_llm = extract_llm
        self.judge_llm = judge_llm
        self.store = StateStore()

    def process(self, message: Message) -> ProcessedRow:
        """Process a single message through the dual-LLM pipeline.

        Returns a ProcessedRow regardless of LLM errors — errors are captured
        in llm_error and results will be empty dicts.
        """
        state = self.store.get_state(message.con_id)

        # Update tradername from message if present
        if message.tradername:
            state.tradername = message.tradername

        used_llm = False
        llm_error = ""
        extract_result_dict: dict[str, Any] = {}
        judge_result_dict: dict[str, Any] = {}
        final_state_dict: dict[str, Any] = {}
        public_result_dict: dict[str, Any] = {}

        # ---- Step 1: Extract LLM ----
        extract_result = None
        if self.extract_llm.available:
            try:
                extract_result = self.extract_llm.recognize(message, state)
                used_llm = True
            except LLMError as exc:
                llm_error = f"Extract LLM: {exc}"
        else:
            llm_error = "Extract LLM not available (no API key)"

        if extract_result is not None:
            extract_result_dict = extract_result.to_dict()

            # ---- Step 2: Judge LLM ----
            judge_result = None
            if self.judge_llm.available:
                try:
                    extract_trades_for_judge = [
                        t.to_dict() for t in extract_result.trades
                    ]
                    judge_result = self.judge_llm.judge(
                        message, state, extract_trades_for_judge
                    )
                except LLMError as exc:
                    llm_error = _append_error(llm_error, f"Judge LLM: {exc}")
            else:
                llm_error = _append_error(llm_error, "Judge LLM not available (no API key)")

            if judge_result is not None:
                judge_result_dict = judge_result.to_dict()

            # ---- Step 3: Merge ----
            self.store.merge_extract_result(state, extract_result)
            if judge_result is not None:
                self.store.merge_judge_result(state, judge_result)

        # ---- Step 4: Finalize ----
        self.store.finalize_message(state, message)

        final_state_dict = state.to_full_result()
        public_result_dict = state.to_public_result()

        return ProcessedRow(
            message=message,
            extract_result=extract_result_dict,
            judge_result=judge_result_dict,
            final_state=final_state_dict,
            public_result=public_result_dict,
            used_llm=used_llm,
            llm_error=llm_error,
        )


def _append_error(existing: str, new: str) -> str:
    """Append an error message, joining with '; '."""
    if existing:
        return f"{existing}; {new}"
    return new
