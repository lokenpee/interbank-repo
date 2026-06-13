from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CONFIRMED_INTENT = "交易成交"
NEGOTIATING_INTENT = "价格议价期限调整"
REJECTED_INTENT = "交易拒绝"
CANCELLED_INTENT = "取消"
DETAIL_INTENT = "补充明细"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = clean_text(value).lower()
    return text in {"true", "1", "yes", "y", "是"}


def row_ref(message: "Message") -> str:
    text = clean_text(message.context).replace("\n", " / ").replace("\t", " | ")
    if len(text) > 140:
        text = text[:140] + "..."
    return f"row {message.row_number} {message.sender}: {text}"


@dataclass
class Message:
    """A single chat message from Excel."""

    row_number: int
    con_id: str
    sender: str
    context: str
    send_time: str = ""
    tradername: str = ""
    interlocutor: str = ""
    is_start: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_self_sender(self) -> bool:
        return bool(self.tradername and self.sender == self.tradername)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "con_ID": self.con_id,
            "sender": self.sender,
            "context": self.context,
            "CHATSENDTIMEORI": self.send_time,
            "TRADERNAME": self.tradername,
            "INTERLOCUTOR": self.interlocutor,
            "is_start": self.is_start,
            "sender_role": "self" if self.is_self_sender else "counterparty",
        }


@dataclass
class TradeState:
    """A single trade tracked within a conversation.

    The stable key is internal id (T1, T2, ...). account may be empty when
    the trade is first discussed verbally and only filled later by details.
    """

    id: str = ""
    account: str = ""
    amount: str = ""
    term: str = ""
    price: str = ""
    direction: str = ""
    status: str = "negotiating"
    intent: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.8
    field_sources: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account": self.account,
            "amount": self.amount,
            "term": self.term,
            "price": self.price,
            "direction": self.direction,
            "status": self.status,
            "intent": self.intent,
            "evidence": self.evidence[-8:],
            "confidence": self.confidence,
            "field_sources": {key: values[-5:] for key, values in self.field_sources.items()},
        }

    def to_public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "account": self.account,
            "amount": self.amount,
            "term": self.term,
            "price": self.price,
            "status": self.status,
            "intent": self.intent,
        }


@dataclass
class ConversationState:
    """Per-conversation state maintained across messages."""

    con_id: str
    counterparty: str = ""
    tradername: str = ""
    trades: list[TradeState] = field(default_factory=list)
    archived_trades: list[TradeState] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    _next_trade_id: int = 1

    def get_trade_by_id(self, trade_id: str) -> TradeState | None:
        for trade in self.trades:
            if trade.id == trade_id:
                return trade
        return None

    def active_trades(self) -> list[TradeState]:
        return [trade for trade in self.trades if trade.status not in {"cancelled"}]

    def allocate_id(self) -> str:
        while True:
            trade_id = f"T{self._next_trade_id}"
            self._next_trade_id += 1
            if self.get_trade_by_id(trade_id) is None:
                return trade_id

    def to_prompt_state(self) -> dict[str, Any]:
        return {
            "con_ID": self.con_id,
            "counterparty": self.counterparty,
            "tradername": self.tradername,
            "trades": [trade.to_dict() for trade in self.trades],
            "archived_trades": [trade.to_dict() for trade in self.archived_trades],
        }

    def to_public_result(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "trades": [trade.to_public_dict() for trade in self.trades],
        }

    def to_full_result(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "state": {
                "trades": [trade.to_dict() for trade in self.trades],
                "archived_trades": [trade.to_dict() for trade in self.archived_trades],
            },
        }


def trade_index_for_prompt(state: ConversationState) -> dict[str, Any]:
    """Compact trade index to help LLMs reuse stable ids."""

    def compact(trade: TradeState) -> dict[str, Any]:
        return {
            "id": trade.id,
            "account": trade.account,
            "amount": trade.amount,
            "term": trade.term,
            "price": trade.price,
            "direction": trade.direction,
            "status": trade.status,
            "intent": trade.intent,
            "recent_evidence": trade.evidence[-3:],
        }

    return {
        "tracked_trades": [compact(trade) for trade in state.trades],
        "archived_trades": [compact(trade) for trade in state.archived_trades],
    }


@dataclass
class TradeChange:
    """What the Extract LLM thinks changed in the current message."""

    type: str
    trade_id: str
    reason: str


@dataclass
class StateChange:
    """Mechanical state diff emitted by StateStore."""

    trade_id: str
    field: str
    before: str
    after: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "trade_id": self.trade_id,
            "field": self.field,
            "from": self.before,
            "to": self.after,
            "source": self.source,
        }


@dataclass
class ExtractResult:
    """Output from the Extract LLM — trade book-keeper, not status judge."""

    counterparty: str = ""
    trades: list[TradeState] = field(default_factory=list)
    changes: list[TradeChange] = field(default_factory=list)
    linking_reason: str = ""
    status_signals: str = ""
    ambiguity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "trades": [trade.to_dict() for trade in self.trades],
            "changes": [
                {"type": change.type, "trade_id": change.trade_id, "reason": change.reason}
                for change in self.changes
            ],
            "linking_reason": self.linking_reason,
            "status_signals": self.status_signals,
            "ambiguity": self.ambiguity,
        }


@dataclass
class JudgeResult:
    """Output from the Judge LLM: status verdict per trade."""

    trades: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"trades": self.trades}


@dataclass
class VerifierResult:
    """Mechanical checks run after Judge, before Merge."""

    verdicts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"verdicts": self.verdicts}


@dataclass
class ProcessedRow:
    """Final output for one message row."""

    message: Message
    extract_result: dict[str, Any] = field(default_factory=dict)
    normalized_extract_result: dict[str, Any] = field(default_factory=dict)
    judge_result: dict[str, Any] = field(default_factory=dict)
    verifier_result: dict[str, Any] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    public_result: dict[str, Any] = field(default_factory=dict)
    state_changes: list[dict[str, str]] = field(default_factory=list)
    used_llm: bool = False
    llm_error: str = ""


# Deprecated classes kept only for compatibility with the old extractors.py.


@dataclass
class CandidateTrade:
    account: str = ""
    amount: str = ""
    term: str = ""
    price: str = ""
    direction: str = ""
    evidence: str = ""

    def is_empty(self) -> bool:
        return not any([self.account, self.amount, self.term, self.price, self.direction])


@dataclass
class Extraction:
    message_intent_guess: str = "未知"
    operation_guess: str = "noop"
    accounts: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    prices: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    trades: list[CandidateTrade] = field(default_factory=list)
    is_detail: bool = False
    has_bond_detail: bool = False
    reason: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "message_intent_guess": self.message_intent_guess,
            "operation_guess": self.operation_guess,
            "accounts": self.accounts,
            "amounts": self.amounts,
            "terms": self.terms,
            "prices": self.prices,
            "directions": self.directions,
            "candidate_trades": [trade.__dict__ for trade in self.trades],
            "is_detail": self.is_detail,
            "has_bond_detail": self.has_bond_detail,
            "reason": self.reason,
        }
