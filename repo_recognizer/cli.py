from __future__ import annotations

import argparse
from pathlib import Path

from .engine import RecognizerEngine
from .excel_io import load_messages, write_results
from .extract_llm import ExtractLLM
from .judge_llm import JudgeLLM
from .llm_client import LLMClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recognize pledged repo chat states from Excel (dual-LLM).")
    parser.add_argument("--input", default="交易下文_测试集.xlsx", help="Input xlsx path.")
    parser.add_argument("--output", default="交易下文_本地识别输出.xlsx", help="Output xlsx path.")

    # Shared API config
    parser.add_argument("--api-key", default=None, help="OpenAI-compatible API key.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")

    # Model selection (can override per-LLM)
    parser.add_argument("--model", default=None, help="Default model for both LLMs.")
    parser.add_argument("--extract-model", default=None, help="Model for Extract LLM (defaults to --model).")
    parser.add_argument("--judge-model", default=None, help="Model for Judge LLM (defaults to --model).")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    headers, rows, messages = load_messages(input_path)

    extract_model = args.extract_model or args.model
    judge_model = args.judge_model or args.model

    extract_client = LLMClient.with_prompt(
        "extract_prompt.md",
        api_key=args.api_key,
        base_url=args.base_url,
        model=extract_model,
    )
    judge_client = LLMClient.with_prompt(
        "judge_prompt.md",
        api_key=args.api_key,
        base_url=args.base_url,
        model=judge_model,
    )

    extract_llm = ExtractLLM(extract_client)
    judge_llm = JudgeLLM(judge_client)

    if not extract_client.available:
        print("WARNING: No API key found. Extract LLM will be skipped.")
        print("  Set --api-key or OPENAI_API_KEY / REPO_LLM_API_KEY environment variable.")

    engine = RecognizerEngine(extract_llm=extract_llm, judge_llm=judge_llm)
    processed = [engine.process(message) for message in messages]
    write_results(output_path, headers, rows, processed)
    print(f"Wrote {len(processed)} rows to {output_path}")

    errors = [p for p in processed if p.llm_error]
    if errors:
        print(f"WARNING: {len(errors)} row(s) had LLM errors.")


if __name__ == "__main__":
    main()
