# 银行间债券质押式回购聊天交易识别

基于双 LLM 架构的银行间债券质押式回购 IM 聊天交易状态识别系统。

## 架构

```
Excel 输入 → Extract LLM → ID 稳定化 → Judge LLM → Verifier 校验 → StateStore 合并 → Excel 输出
```

| 阶段 | 职责 | 实现 |
|------|------|------|
| Extract LLM | 识别交易要素（account/amount/term/price/direction） | LLM，提示词 `prompts/extract_prompt.md` |
| ID 稳定化 | LLM 空 id → 分配 T1/T2，已有 id → 复用 | 纯代码 `state_store.py` |
| Judge LLM | 判定交易状态（confirmed/rejected/negotiating/detail_pending） | LLM，提示词 `prompts/judge_prompt.md` |
| Verifier | 检查 ID 不一致、price 污染、漏判等 6 类矛盾 | 纯代码 `verifier.py`，不调 LLM |
| StateStore | 按 ID 机械合并字段，记录 before→after 变更 | 纯代码 `state_store.py` |

### 与单 LLM 方案对比

| 维度 | 原 Dify 单 LLM | 当前架构 |
|------|---------------|---------|
| LLM 调用 | 1 次（识别+判断全包） | 2 次 + 1 层纯代码校验 |
| 职责 | 混杂 | Extract（要素）←→ Judge（状态）分离 |
| 交易标识 | account 作 key（为空时无法追踪） | T1/T2 内部 ID，account 可为空 |
| 跨消息状态 | 无（每次独立调用） | StateStore 按 con_ID 维护完整历史 |
| 错误恢复 | 无 | 超时/断连自动重试 3 次（指数退避） |
| 可审计性 | 只有最终 JSON | 每步中间输出 + 字段级 StateChange |
| 提示词 | 204 行规则 | Extract 167 行 + Judge 109 行，各含 8 个对照示例 |

## 快速开始

### 1. 配置

```bash
cp config.example.json config.json
# 编辑 config.json，填入 API key
```

```json
{
  "api_key": "sk-your-key-here",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat"
}
```

### 2. 运行

```bash
# 默认运行测试集
python run_repo_recognizer.py

# 指定输入输出
python run_repo_recognizer.py --input 交易下文_测试集.xlsx --output 输出.xlsx

# 完整参数
python run_repo_recognizer.py \
  --input 交易下文_测试集.xlsx \
  --output 输出.xlsx \
  --extract-model deepseek-chat \
  --judge-model deepseek-chat \
  --timeout 120 \
  --max-retries 3
```

### 3. 输入格式

Excel 须包含列：`con_ID`、`SENDER`、`CONTEXT`、`CHATSENDTIMEORI`、`TRADERNAME`、`INTERLOCUTOR`、`SENTTYPE`、`is_start`

### 4. 输出格式

原始列 + 9 个新增列：

| 列 | 内容 |
|----|------|
| 识别LLM输出json | Extract LLM 原始输出 |
| 识别LLM稳定ID输出json | ID 稳定化后 |
| 判断LLM输出json | Judge LLM 输出 |
| 校验器输出json | Verifier 矛盾检测 |
| llm_used | Y/N |
| llm_error | 错误信息 |
| 状态变更摘要json | before→after + 来源行号 |
| 最终状态json | ConversationState 完整快照 |
| 预期格式输出 | 简化版 |

## 项目结构

```
repo_recognizer/
├── engine.py          # 流程编排：Extract → Judge → Verify → Merge
├── extract_llm.py     # 识别 LLM 封装
├── judge_llm.py       # 判断 LLM 封装
├── verifier.py        # 纯代码矛盾校验器
├── state_store.py     # 纯机械状态管理（零业务推断）
├── llm_client.py      # 通用 OpenAI 兼容客户端（含重试）
├── models.py          # 数据模型
├── excel_io.py        # Excel 读写
├── cli.py             # 命令行入口
├── prompts/
│   ├── extract_prompt.md  # 识别 LLM 提示词
│   └── judge_prompt.md    # 判断 LLM 提示词
├── extractors.py      # DEPRECATED: 旧正则提取器（仅供参考）
└── prompt.py          # DEPRECATED: 旧提示词模板
add_sender_column.py   # 工具：批量添加 SENDER 列
config.example.json    # 配置文件模板
```

## 核心数据模型

### TradeState
```python
id: str           # T1, T2, ...
account: str      # 可为空
amount: str       # 如 "15410万"
term: str         # 如 "隔夜", "7D"
price: str        # 如 "1.41"
direction: str    # "正回购" | "逆回购"
status: str       # negotiating | confirmed | rejected | cancelled | detail_pending
intent: str       # 人类可读标签
confidence: float # 0-1
evidence: list    # 支撑证据
field_sources: dict # 字段出处溯源
```

### ConversationState
```python
con_id: str
counterparty: str
tradername: str
trades: list[TradeState]
archived_trades: list[TradeState]
messages: list[Message]
```

## 设计原则

- **程序不猜业务**：所有交易理解由两个 LLM 完成，程序只做保存、传上下文、校验 JSON、分配 ID
- **Extract 不下结论**：只识别要素，不判 confirmed/rejected。状态信号通过 `status_signals` 传递但不裁决
- **Judge 不改要素**：只判状态，不改 amount/price/term
- **Verifier 不调 LLM**：纯代码检查，6 类机械矛盾
- **StateStore 纯机械**：按 ID 覆盖字段，记录变更，零业务推断
