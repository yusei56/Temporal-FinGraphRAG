#!/usr/bin/env python3
"""Run an optional LLM-as-a-judge pass over existing ECT-QA eval JSON files.

This avoids rerunning the RAG agents. It reads rows produced by ectqa_eval.py,
adds an `llm_judge` object to each selected row, and recomputes aggregates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ectqa_eval import (  # noqa: E402
    EctQaDataManager,
    aggregate,
    judge_prediction_with_llm,
    make_llm_judge,
    normalize_answer,
    select_questions,
)


def row_hits(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for event in row.get("retrieval_events", []) or []:
        for hit in event.get("hits", []) or []:
            key = str(hit.get("chunk_id") or f"{hit.get('filename')}::{len(hits)}")
            if key in seen:
                continue
            seen.add(key)
            hits.append(dict(hit))
    return hits


def fallback_question(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("question_id", ""),
        "question": row.get("question", ""),
        "answer": row.get("gold_answer", ""),
        "question_type": row.get("question_type", ""),
        "reasoning_type": row.get("reasoning_type", ""),
        "num_hops": row.get("num_hops", 0),
        "evidence_list": [],
    }


def load_question_map(
    input_data: Mapping[str, Any],
    *,
    data_dir: Path,
    download: bool,
) -> Dict[str, Dict[str, Any]]:
    config = input_data.get("config", {})
    scenario = config.get("scenario", "new")
    answer_filter = config.get("answer_filter", "answerable")
    limit = config.get("limit")
    offset = config.get("offset", 0)
    try:
        manager = EctQaDataManager(data_dir, download=download)
        raw_questions = manager.load_questions(scenario)
        selected = select_questions(
            raw_questions,
            answer_filter=answer_filter,
            limit=limit,
            offset=offset,
        )
        return {str(question["id"]): question for question in selected}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not load gold evidence from ECT-QA cache: {exc}")
        return {}


def recompute_agent_aggregates(data: Dict[str, Any]) -> None:
    agents = sorted({str(row.get("agent", "")) for row in data.get("rows", []) if row.get("agent")})
    data["agents"] = {
        agent: aggregate([row for row in data["rows"] if row.get("agent") == agent])
        for agent in agents
    }
    data["overall"] = aggregate(data.get("rows", []))


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def selected_row_indices(data: Mapping[str, Any], args: argparse.Namespace) -> List[int]:
    agent_filter = {
        item.strip()
        for item in (args.agents or "").split(",")
        if item.strip()
    }
    indices: List[int] = []
    for index, row in enumerate(data.get("rows", [])):
        if agent_filter and row.get("agent") not in agent_filter:
            continue
        if args.only_rule_errors and row.get("answer_metrics", {}).get("bucket") in {
            "correct",
            "correct_refusal",
        }:
            continue
        if args.skip_existing and (row.get("llm_judge") or {}).get("enabled"):
            continue
        indices.append(index)
    if args.offset_rows:
        indices = indices[args.offset_rows :]
    if args.limit_rows is not None:
        indices = indices[: args.limit_rows]
    return indices


def run_judge(args: argparse.Namespace) -> Dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env")
    input_path = args.input_json.resolve()
    output_path = args.output_json.resolve()
    if output_path.exists() and not args.allow_overwrite:
        raise FileExistsError(
            f"Output JSON already exists and will not be overwritten: {output_path}. "
            "Choose a new --output-json path or pass --allow-overwrite intentionally."
        )
    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    question_map = load_question_map(
        data,
        data_dir=args.data_dir.resolve(),
        download=args.download,
    )
    judge_llm, judge_model = make_llm_judge(args)
    indices = selected_row_indices(data, args)

    data.setdefault("config", {})
    data["config"]["llm_judge_offline"] = {
        "enabled": True,
        "input_json": str(input_path),
        "judge_model": judge_model,
        "judge_profile": args.judge_profile,
        "judge_max_evidence": args.judge_max_evidence,
        "judge_evidence_chars": args.judge_evidence_chars,
        "judge_max_answer_chars": args.judge_max_answer_chars,
        "selected_rows": len(indices),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    started = time.perf_counter()
    for done, row_index in enumerate(indices, start=1):
        row = rows[row_index]
        question = question_map.get(str(row.get("question_id"))) or fallback_question(row)
        judge = judge_prediction_with_llm(
            llm=judge_llm,
            judge_model=judge_model,
            question=question,
            answer=str(row.get("answer", "")),
            hits=row_hits(row),
            args=args,
        )
        row["llm_judge"] = judge

        if not args.quiet:
            gold = normalize_answer(str(row.get("gold_answer", "")))
            target = "refusal" if gold == "unanswerable" else "correct"
            print(
                f"[{done}/{len(indices)}] row={row_index} "
                f"agent={row.get('agent')} rule={row.get('answer_metrics', {}).get('bucket')} "
                f"judge={judge.get('judge_label') or judge.get('label')} target={target} "
                f"err={bool(judge.get('error'))}"
            )

        if args.checkpoint_every and done % args.checkpoint_every == 0:
            recompute_agent_aggregates(data)
            write_json(output_path, data)

    data["config"]["llm_judge_offline"]["finished_at"] = datetime.now().isoformat(timespec="seconds")
    data["config"]["llm_judge_offline"]["runtime_seconds"] = time.perf_counter() - started
    recompute_agent_aggregates(data)
    write_json(output_path, data)
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add LLM judge metrics to an existing ECT-QA eval JSON.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "datasets" / "ect_qa")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--agents", default="")
    parser.add_argument("--limit-rows", type=int, default=None)
    parser.add_argument("--offset-rows", type=int, default=0)
    parser.add_argument("--only-rule-errors", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--skip-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--judge-profile", choices=["focused", "full"], default="focused")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=700)
    parser.add_argument("--judge-timeout", type=float, default=None)
    parser.add_argument("--judge-retries", type=int, default=1)
    parser.add_argument("--judge-max-evidence", type=int, default=5)
    parser.add_argument("--judge-evidence-chars", type=int, default=700)
    parser.add_argument("--judge-max-answer-chars", type=int, default=1800)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.output_json is None:
        args.output_json = args.input_json.with_name(f"{args.input_json.stem}_llm_judged.json")
    return args


def main() -> int:
    data = run_judge(parse_args())
    print("\n=== ECT-QA LLM Judge Summary ===")
    print(json.dumps({"agents": data["agents"], "overall": data["overall"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
