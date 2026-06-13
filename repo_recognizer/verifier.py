"""
Verifier — lightweight mechanical contradiction checker.

Runs AFTER Judge, BEFORE StateStore merge.
Does NOT call any LLM. Pure code checks only.
"""

from __future__ import annotations

from .models import (
    ConversationState,
    JudgeResult,
    VerifierResult,
    clean_text,
)


def verify(
    state: ConversationState,
    normalized_extract_trades: list[dict],
    judge_result: JudgeResult,
) -> VerifierResult:
    """Mechanical checks on Judge output against Extract output and current state.

    Returns a VerifierResult with a list of issues found (empty = all clear).
    """
    verdicts: list[dict[str, str]] = []

    extract_ids = {clean_text(t.get("id", "")) for t in normalized_extract_trades}
    judge_ids = {clean_text(v.get("id", "")) for v in judge_result.trades}
    state_ids = {t.id for t in state.trades}
    archived_ids = {t.id for t in state.archived_trades}

    # 1. Judge missed a trade that Extract found
    missing = extract_ids - judge_ids
    for tid in sorted(missing):
        verdicts.append({
            "level": "warn",
            "check": "missing_verdict",
            "detail": f"Judge 未对 {tid} 输出 verdict",
        })

    # 2. Judge used an ID that doesn't exist in Extract output
    extra = judge_ids - extract_ids - state_ids
    for tid in sorted(extra):
        verdicts.append({
            "level": "error",
            "check": "unknown_id",
            "detail": f"Judge 使用了 Extract 未输出的 id: {tid}",
        })

    # 3. Judge used an archived ID
    archived_hits = judge_ids & archived_ids
    for tid in sorted(archived_hits):
        verdicts.append({
            "level": "warn",
            "check": "archived_id",
            "detail": f"Judge 对已归档交易 {tid} 输出了 verdict",
        })

    # 4. confirmed without evidence/reason
    for v in judge_result.trades:
        tid = clean_text(v.get("id", ""))
        status = clean_text(v.get("status", ""))
        reason = clean_text(v.get("reason", ""))
        if status == "confirmed" and len(reason) < 3:
            verdicts.append({
                "level": "warn",
                "check": "confirmed_no_reason",
                "detail": f"{tid} 判为 confirmed 但 reason 过短: '{reason}'",
            })

    # 5. price looks contaminated (95z, bond code, rating, haircut)
    for t in normalized_extract_trades:
        tid = clean_text(t.get("id", ""))
        price = clean_text(t.get("price", ""))
        if not price:
            continue
        contaminated = False
        for token in ["95z", "90%", "质押", "押", "MTN", "PPN", "AA+", "AAA", "建发", "发展"]:
            if token.lower() in price.lower():
                contaminated = True
                break
        # Bare numbers > 10 likely not a rate
        try:
            p = float(price)
            if p > 10 and p < 30:
                contaminated = True
        except ValueError:
            pass
        if contaminated:
            verdicts.append({
                "level": "warn",
                "check": "suspicious_price",
                "detail": f"{tid} price='{price}' 疑似质押折扣率/债券代码而非回购利率",
            })

    # 6. Extract has new trade but state already has a trade with same account
    # that wasn't reused
    for t in normalized_extract_trades:
        tid = clean_text(t.get("id", ""))
        account = clean_text(t.get("account", ""))
        if not account or not tid:
            continue
        for existing in state.trades:
            if existing.account == account and existing.id != tid:
                verdicts.append({
                    "level": "warn",
                    "check": "duplicate_account",
                    "detail": f"{tid} account='{account}' 与已有 {existing.id} 相同但未复用 id",
                })

    return VerifierResult(verdicts=verdicts)
