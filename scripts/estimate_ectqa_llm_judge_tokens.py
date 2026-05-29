#!/usr/bin/env python3
"""Estimate token usage for offline ECT-QA LLM judge runs.

The script does not call an LLM. It reconstructs the current judge prompt for
rows in an existing eval JSON and estimates input plus configurable output
tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ectqa_eval import DEFAULT_LLM_JUDGE_MODEL, build_judge_prompt  # noqa: E402
from ectqa_llm_judge import fallback_question, load_question_map, row_hits  # noqa: E402


def get_token_counter(model: str):
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")

        return lambda text: len(encoding.encode(text)), "tiktoken"
    except Exception:
        return lambda text: max(1, len(text) // 4), "chars_div_4"


def percentile(values: Sequence[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))]


def selected_rows(data: Dict[str, Any], args: argparse.Namespace) -> List[Dict[str, Any]]:
    rows = list(data.get("rows", []))
    if args.agents:
        allowed = {item.strip() for item in args.agents.split(",") if item.strip()}
        rows = [row for row in rows if row.get("agent") in allowed]
    if args.offset_rows:
        rows = rows[args.offset_rows :]
    if args.limit_rows is not None:
        rows = rows[: args.limit_rows]
    return rows


def estimate(args: argparse.Namespace) -> Dict[str, Any]:
    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    question_map = load_question_map(
        data,
        data_dir=args.data_dir.resolve(),
        download=args.download,
    )
    token_count, tokenizer = get_token_counter(args.model)

    tokens: List[int] = []
    chars: List[int] = []
    rows = selected_rows(data, args)
    for row in rows:
        question = question_map.get(str(row.get("question_id"))) or fallback_question(row)
        prompt = build_judge_prompt(
            question=question,
            answer=str(row.get("answer", "")),
            hits=row_hits(row),
            args=args,
        )
        tokens.append(token_count(prompt))
        chars.append(len(prompt))

    input_total = sum(tokens)
    output_total = args.output_tokens_per_call * len(rows)
    total = input_total + output_total
    return {
        "input_json": str(args.input_json),
        "judge_profile": args.judge_profile,
        "tokenizer": tokenizer,
        "model_for_tokenizer": args.model,
        "rows": len(rows),
        "input_tokens": {
            "total": input_total,
            "avg": round(mean(tokens), 1) if tokens else 0,
            "median": round(median(tokens), 1) if tokens else 0,
            "p90": percentile(tokens, 0.9) if tokens else 0,
            "min": min(tokens) if tokens else 0,
            "max": max(tokens) if tokens else 0,
        },
        "prompt_chars": {
            "avg": round(mean(chars), 1) if chars else 0,
            "min": min(chars) if chars else 0,
            "max": max(chars) if chars else 0,
        },
        "assumed_output_tokens_per_call": args.output_tokens_per_call,
        "estimated_output_tokens": output_total,
        "estimated_total_tokens": total,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate ECT-QA LLM judge token usage.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "datasets" / "ect_qa")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--agents", default="")
    parser.add_argument("--limit-rows", type=int, default=100)
    parser.add_argument("--offset-rows", type=int, default=0)
    parser.add_argument("--judge-profile", choices=["focused", "full"], default="full")
    parser.add_argument("--judge-max-evidence", type=int, default=5)
    parser.add_argument("--judge-evidence-chars", type=int, default=700)
    parser.add_argument("--judge-max-answer-chars", type=int, default=1800)
    parser.add_argument("--output-tokens-per-call", type=int, default=250)
    parser.add_argument("--model", default=DEFAULT_LLM_JUDGE_MODEL)
    return parser.parse_args()


def main() -> int:
    print(json.dumps(estimate(parse_args()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
