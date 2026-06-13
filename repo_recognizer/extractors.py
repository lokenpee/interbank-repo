from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .models import CandidateTrade, Extraction


ACCOUNT_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]+?(?:信托|资管|基金|期货|证券|圆融|锦鸿|青枫|财丰|信固臻|信智)[\u4e00-\u9fa5A-Za-z0-9（）()]*?号)"
    r"|([\u4e00-\u9fa5A-Za-z0-9（）()]{2,30}?号)"
)

AMOUNT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*(kw|KW|kW|w|W|万|万元|e|E|亿|个)(?![A-Za-z0-9])"
)
TERM_RANGE_PATTERN = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*[dD]")
TERM_PATTERN = re.compile(r"(隔夜|O/N|ON|on|o/n|[1-9]\d?\s*[dD]|1M|跨月)")
PRICE_DECIMAL_PATTERN = re.compile(r"(?<![A-Za-z0-9])([1-3]\.\d{1,3})%?(?![A-Za-z0-9])")
PRICE_BARE_PATTERN = re.compile(r"(?<![A-Za-z0-9.])([3-9]\d)(?![A-Za-z0-9.])")
BOND_CODE_PATTERN = re.compile(r"\b\d{6,9}(?:\.IB)?\b")


DETAIL_KEYWORDS = [
    "发汇享",
    "发汇盈",
    "发天添富",
    "发西部",
    "划款",
    "打款",
    "发户",
    "过目",
    "明细",
    "押券",
    "质押",
    "质押率",
    "折扣",
    "95z",
    "90%",
    "债券清单",
]

REJECT_KEYWORDS = [
    "到不了",
    "出完",
    "没了",
    "没有",
    "户暂时平",
    "户平",
    "改不了",
    "不行",
    "晚了",
    "晚来",
    "不出",
]

CANCEL_KEYWORDS = ["取消", "不要了", "撤了", "作废", "废了"]
MODIFY_KEYWORDS = ["加", "多借", "变成", "改成", "修改", "调整", "追加", "减少", "少", "确认为", "总共", "一共", "改为"]
CONFIRM_KEYWORDS = ["OK", "ok", "Ok", "okk", "好", "好滴", "好嘞", "可以", "来吧", "成交", "同意", "没问题", "你发", "都发"]
INQUIRY_KEYWORDS = ["什么价", "啥价格", "多少", "咋出", "怎么出", "出出吗", "有吗", "还有么", "可以么", "可以不", "能出", "能借"]
REQUEST_KEYWORDS = ["借", "融入", "收", "要", "需要", "求", "需求"]
QUESTION_CONFIRM_BLOCKERS = ["可以不", "可以么", "可以吗", "行不", "行吗", "能不能", "能否"]


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def split_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        line = line.strip()
        if line:
            lines.append(line)
    return lines


def split_cells(line: str) -> list[str]:
    if "\t" in line:
        return [cell.strip() for cell in line.split("\t") if cell.strip()]
    if "|" in line:
        return [cell.strip() for cell in line.split("|") if cell.strip()]
    return []


def decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f").rstrip("0").rstrip(".")


def normalize_amount(raw_number: str, unit: str) -> str:
    try:
        number = Decimal(raw_number.replace(",", ""))
    except InvalidOperation:
        return raw_number + unit
    unit_lower = unit.lower()
    if unit_lower == "kw":
        return f"{decimal_text(number * Decimal(1000))}万"
    if unit_lower in {"w", "万", "万元"}:
        return f"{decimal_text(number)}万"
    if unit_lower in {"e", "亿", "个"}:
        if number < 1:
            return f"{decimal_text(number * Decimal(10000))}万"
        return f"{decimal_text(number)}亿"
    return raw_number + unit


def normalize_table_amount(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if AMOUNT_PATTERN.search(text):
        return extract_amounts(text)[0]
    if re.fullmatch(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
        return f"{text.replace(',', '')}万"
    match = re.search(r"融入\s*(\d+(?:\.\d+)?)", text)
    if match:
        return f"{match.group(1)}万"
    return text


def normalize_term(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    range_match = TERM_RANGE_PATTERN.search(text)
    if range_match:
        return f"{range_match.group(1)}-{range_match.group(2)}D"
    upper = text.upper().replace(" ", "")
    if upper in {"ON", "O/N"} or "隔夜" in text:
        return "隔夜"
    day_match = re.search(r"([1-9]\d?)D", upper)
    if day_match:
        return f"{day_match.group(1)}D"
    if upper == "1M":
        return "1M"
    if "跨月" in text:
        return "跨月"
    return text


def extract_amounts(text: str) -> list[str]:
    amounts = []
    for match in AMOUNT_PATTERN.finditer(text):
        amounts.append(normalize_amount(match.group(1), match.group(2)))
    return unique_keep_order(amounts)


def clean_account(account: str) -> str:
    account = account.strip(" ，,。；;：:")
    account = re.sub(r"^(那个|这个|欸姐|老板|麻烦|请问|还有|今天|的)+\s*", "", account)
    account = account.strip(" ，,。；;：:")
    return account


def extract_terms(text: str) -> list[str]:
    terms = []
    for match in TERM_RANGE_PATTERN.finditer(text):
        terms.append(f"{match.group(1)}-{match.group(2)}D")
    for match in TERM_PATTERN.finditer(text):
        value = normalize_term(match.group(1))
        if value not in terms:
            terms.append(value)
    return unique_keep_order(terms)


def is_likely_price_context(text: str, token: str) -> bool:
    if "质押" in text or "押券" in text or "折" in text or "z" in text.lower():
        return False
    if f"{token}%" in text:
        return False
    price_words = ["价格", "价", "报价", "利率", "目前", "得", "到", "还是", "贵", "便宜"]
    stripped = re.sub(r"\s+", "", text)
    return stripped == token or any(word in text for word in price_words)


def extract_prices(text: str) -> list[str]:
    prices = []
    protected_spans = []
    for match in AMOUNT_PATTERN.finditer(text):
        protected_spans.append(match.span())
    for match in BOND_CODE_PATTERN.finditer(text):
        protected_spans.append(match.span())
    for match in PRICE_DECIMAL_PATTERN.finditer(text):
        start, end = match.span()
        if any(start >= a and end <= b for a, b in protected_spans):
            continue
        prices.append(match.group(1))
    for match in PRICE_BARE_PATTERN.finditer(text):
        start, end = match.span()
        if any(start >= a and end <= b for a, b in protected_spans):
            continue
        token = match.group(1)
        if is_likely_price_context(text, token):
            prices.append(token)
    return unique_keep_order(prices)


def extract_accounts(text: str) -> list[str]:
    accounts = []
    for match in ACCOUNT_PATTERN.finditer(text):
        account = match.group(1) or match.group(2) or ""
        account = clean_account(account)
        if not account:
            continue
        if account in {"这个户", "那个户"}:
            continue
        if "账号" in account or "账户" in account:
            continue
        accounts.append(account)
    return unique_keep_order(accounts)


def extract_direction(text: str) -> list[str]:
    directions = []
    if any(word in text for word in ["融入", "借", "收", "需要", "需求", "求"]):
        directions.append("正回购")
    if any(word in text for word in ["出", "给"]):
        directions.append("逆回购")
    return unique_keep_order(directions)


def extract_table_trades(text: str) -> list[CandidateTrade]:
    trades: list[CandidateTrade] = []
    for line in split_lines(text):
        if BOND_CODE_PATTERN.search(line):
            continue
        cells = split_cells(line)
        if len(cells) < 3:
            continue
        joined = " ".join(cells)
        if not any(word in joined for word in ["融入", "借", "出"]):
            continue
        account = clean_account(cells[0])
        direction = ""
        amount = ""
        term = ""
        price = ""
        for idx, cell in enumerate(cells):
            if "融入" in cell or "借" in cell:
                direction = "正回购"
                inline_amount = normalize_table_amount(cell.replace("融入", "").replace("借", ""))
                if inline_amount:
                    amount = inline_amount
                    if idx + 1 < len(cells):
                        term = normalize_term(cells[idx + 1])
                elif idx + 1 < len(cells):
                    amount = normalize_table_amount(cells[idx + 1])
                    if idx + 2 < len(cells):
                        term = normalize_term(cells[idx + 2])
                elif idx + 2 < len(cells):
                    term = normalize_term(cells[idx + 2])
            elif cell == "出" or "融出" in cell:
                direction = "逆回购"
                inline_amount = normalize_table_amount(cell.replace("融出", "").replace("出", ""))
                if inline_amount:
                    amount = inline_amount
                    if idx + 1 < len(cells):
                        term = normalize_term(cells[idx + 1])
                elif idx + 1 < len(cells):
                    amount = normalize_table_amount(cells[idx + 1])
                    if idx + 2 < len(cells):
                        term = normalize_term(cells[idx + 2])
                elif idx + 2 < len(cells):
                    term = normalize_term(cells[idx + 2])
        decimal_prices = []
        for cell in cells[1:]:
            decimal_prices.extend(PRICE_DECIMAL_PATTERN.findall(cell))
        if decimal_prices:
            price = decimal_prices[-1]
        if not amount:
            amounts = extract_amounts(joined)
            amount = amounts[0] if amounts else ""
        if not term:
            terms = extract_terms(joined)
            term = terms[0] if terms else ""
        if account and not BOND_CODE_PATTERN.search(account):
            trades.append(
                CandidateTrade(
                    account=account,
                    amount=amount,
                    term=term,
                    price=price,
                    direction=direction,
                    evidence=line,
                )
            )
    return trades


def extract_segment_trades(text: str) -> list[CandidateTrade]:
    accounts_with_pos: list[tuple[str, int, int]] = []
    for match in ACCOUNT_PATTERN.finditer(text):
        account = clean_account(match.group(1) or match.group(2) or "")
        if not account or account in {"这个户", "那个户"}:
            continue
        if "账号" in account or "账户" in account:
            continue
        accounts_with_pos.append((account, match.start(), match.end()))
    if not accounts_with_pos:
        return []

    full_terms = extract_terms(text)
    full_prices = extract_prices(text)
    full_directions = extract_direction(text)
    trades: list[CandidateTrade] = []
    for idx, (account, start, end) in enumerate(accounts_with_pos):
        next_start = accounts_with_pos[idx + 1][1] if idx + 1 < len(accounts_with_pos) else len(text)
        segment = text[start:next_start]
        amounts = extract_amounts(segment)
        terms = extract_terms(segment)
        prices = extract_prices(segment)
        directions = extract_direction(segment)
        trades.append(
            CandidateTrade(
                account=account,
                amount=amounts[0] if amounts else "",
                term=terms[0] if terms else (full_terms[0] if len(full_terms) == 1 else ""),
                price=prices[0] if prices else (full_prices[0] if len(full_prices) == 1 else ""),
                direction=directions[0] if directions else (full_directions[0] if len(full_directions) == 1 else ""),
                evidence=segment.strip(" +；;，,。"),
            )
        )
    return trades


def classify_intent(text: str, extraction: Extraction) -> tuple[str, str, str]:
    compact = re.sub(r"\s+", "", text)
    if any(word in text for word in CANCEL_KEYWORDS):
        return "取消", "cancel", "出现明确取消/撤销词"
    if any(word in text for word in REJECT_KEYWORDS) or any(word in text for word in ["没到", "不到", "还没到"]):
        return "拒绝", "reject", "出现拒绝或无量词"
    if any(word in text for word in DETAIL_KEYWORDS) or extraction.has_bond_detail:
        return "补充明细", "noop", "出现押券、资金路径或明细词"
    if any(word in text for word in MODIFY_KEYWORDS):
        return "修改交易", "update", "出现修改金额/期限/账户信号"
    if extraction.prices and not extraction.amounts:
        return "报价", "update", "识别到报价数字"
    if (
        any(word in compact for word in CONFIRM_KEYWORDS)
        and len(compact) <= 12
        and not any(word in compact for word in QUESTION_CONFIRM_BLOCKERS)
    ):
        return "成交确认", "confirm", "短回复包含确认词"
    if any(word in text for word in REQUEST_KEYWORDS) or extraction.trades or extraction.accounts or extraction.amounts:
        return "交易请求", "create", "识别到账户/金额/交易方向"
    if any(word in text for word in INQUIRY_KEYWORDS) or extraction.terms:
        return "询价", "create", "识别到询价或期限"
    return "无关", "noop", "未识别到核心交易要素"


def choose_final_amount(text: str, amounts: list[str]) -> list[str]:
    if not amounts:
        return []
    final_patterns = [
        r"(?:变成|改成|确认为|确认(?:为)?|总共|一共)\s*(\d+(?:\.\d+)?)\s*(kw|KW|kW|w|W|万|万元|e|E|亿|个)",
        r"融入\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in final_patterns:
        match = re.search(pattern, text)
        if match:
            unit = match.group(2) if len(match.groups()) >= 2 else "万"
            return [normalize_amount(match.group(1), unit)]
    return amounts


def has_final_amount_marker(text: str) -> bool:
    return bool(re.search(r"(?:变成|改成|确认为|确认(?:为)?|总共|一共|融入\s*\d)", text))


def extract_message(text: str) -> Extraction:
    extraction = Extraction()
    extraction.trades = extract_table_trades(text)
    extraction.accounts = unique_keep_order(
        [trade.account for trade in extraction.trades if trade.account] + extract_accounts(text)
    )
    extraction.amounts = extract_amounts(text)
    extraction.amounts = choose_final_amount(text, extraction.amounts)
    extraction.terms = extract_terms(text)
    extraction.prices = extract_prices(text)
    extraction.directions = extract_direction(text)
    extraction.has_bond_detail = bool(BOND_CODE_PATTERN.search(text) or "MTN" in text or "PPN" in text)
    extraction.is_detail = any(word in text for word in DETAIL_KEYWORDS) or extraction.has_bond_detail

    if not extraction.trades and extraction.accounts:
        extraction.trades = extract_segment_trades(text)
    if extraction.trades and extraction.amounts and has_final_amount_marker(text):
        for trade in extraction.trades:
            trade.amount = extraction.amounts[-1]

    intent, operation, reason = classify_intent(text, extraction)
    extraction.message_intent_guess = intent
    extraction.operation_guess = operation
    extraction.reason = reason
    return extraction
