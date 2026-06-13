from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- intent constants (kept for reference / output labels) ----
CONFIRMED_INTENT = "交易成交"
NEGOTIATING_INTENT = "价格议价期限调整"
REJECTED_INTENT = "交易拒绝"
CANCELLED_INTENT = "取消"
DETAIL_INTENT = "补充明细"


# ---- utility functions ----
def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = clean_text(value).lower()
    return text in {"true", "1", "yes", "y", "是"}


# ---- data classes ----

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

    Uses an internal ``id`` (T1, T2, ...) as the stable key.
    ``account`` may be empty when the trade is discussed verbally
    before detailed accounts are sent.
    """
    id: str = ""                      # T1, T2, ...
    account: str = ""                 # may be empty
    amount: str = ""
    term: str = ""
    price: str = ""
    direction: str = ""               # 正回购 / 逆回购
    status: str = "negotiating"       # negotiating | confirmed | rejected | cancelled | detail_pending
    intent: str = ""                  # human-readable label
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.8

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
        }

    def to_public_dict(self) -> dict[str, str]:
        """Compact output for the '预期格式输出' column."""
        return {
            "account": self.account,
            "amount": self.amount,
            "term": self.term,
            "price": self.price,
            "intent": self.intent,
        }


@dataclass
class ConversationState:
    """Per-conversation state maintained across messages."""
    con_id: str
    counterparty: str = ""
    tradername: str = ""
    trades: list[TradeState] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    _next_trade_id: int = 1

    def get_trade_by_id(self, trade_id: str) -> TradeState | None:
        for t in self.trades:
            if t.id == trade_id:
                return t
        return None

    def active_trades(self) -> list[TradeState]:
        return [t for t in self.trades if t.status not in {"cancelled"}]

    def allocate_id(self) -> str:
        """Return the next available trade id (T1, T2, ...)."""
        tid = f"T{self._next_trade_id}"
        self._next_trade_id += 1
        return tid

    def to_prompt_state(self) -> dict[str, Any]:
        return {
            "con_ID": self.con_id,
            "counterparty": self.counterparty,
            "tradername": self.tradername,
            "trades": [t.to_dict() for t in self.trades],
        }

    def to_public_result(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "trades": [t.to_public_dict() for t in self.trades],
        }

    def to_full_result(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "state": {
                "trades": [t.to_dict() for t in self.trades],
            },
        }


@dataclass
class TradeChange:
    """Describes what the extract LLM thinks changed in the current message."""
    type: str      # "create" | "update" | "delete" | "noop"
    trade_id: str  # which trade is affected
    reason: str    # why


@dataclass
class ExtractResult:
    """Output from the Extract LLM."""
    counterparty: str = ""
    trades: list[TradeState] = field(default_factory=list)
    changes: list[TradeChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty,
            "trades": [t.to_dict() for t in self.trades],
            "changes": [{"type": c.type, "trade_id": c.trade_id, "reason": c.reason} for c in self.changes],
        }


@dataclass
class JudgeResult:
    """Output from the Judge LLM — status verdict per trade."""
    trades: list[dict[str, Any]] = field(default_factory=list)
    # Each dict: {"id": "T1", "status": "confirmed", "intent": "交易成交", "confidence": 0.95, "reason": "..."}

    def to_dict(self) -> dict[str, Any]:
        return {"trades": self.trades}


@dataclass
class ProcessedRow:
    """Final output for one message row."""
    message: Message
    extract_result: dict[str, Any] = field(default_factory=dict)
    judge_result: dict[str, Any] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    public_result: dict[str, Any] = field(default_factory=dict)
    used_llm: bool = False
    llm_error: str = ""


# ---- deprecated classes (kept for extractors.py reference) ----

@dataclass
class CandidateTrade:
    """DEPRECATED: Old regex-based candidate trade. Not used by new engine."""
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
    """DEPRECATED: Old regex-based extraction result. Not used by new engine."""
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
