#!/usr/bin/env python3
"""Run the ECT-QA base/updated/new incremental evaluation protocol."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_SCRIPT = PROJECT_ROOT / "scripts" / "ectqa_eval.py"

PROTOCOL_RUNS = [
    {
        "name": "base_old_questions_old_corpus",
        "scenario": "base",
        "description": "Old questions over old corpus; measures original/base ability.",
    },
    {
        "name": "retention_old_questions_updated_corpus",
        "scenario": "updated",
        "description": "Old questions over old+new corpus; measures stability after corpus update.",
    },
    {
        "name": "new_questions_updated_corpus",
        "scenario": "new",
        "description": "New questions over old+new corpus; measures acquisition of new information.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ECT-QA incremental protocol.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--answer-filter", choices=["answerable", "unanswerable", "all"], default="answerable")
    parser.add_argument("--agents", default="TemporalEvidenceAgent")
    parser.add_argument("--metadata-filter", choices=["off", "boost", "strict"], default="boost")
    parser.add_argument("--retriever", choices=["tfidf", "graph"], default="tfidf")
    parser.add_argument("--graph-fact-pack", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--retrieval-top-k", type=int, default=8)
    parser.add_argument("--metric-top-k", type=int, default=8)
    parser.add_argument("--temporal-evidence-cards", type=int, default=8)
    parser.add_argument("--temporal-evidence-chars", type=int, default=700)
    parser.add_argument("--temporal-pseudo-questions", type=int, default=0)
    parser.add_argument("--corpus-scope", choices=["evidence", "full"], default="full")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "docs" / "incremental_runs")
    parser.add_argument("--summary-json", type=Path, default=PROJECT_ROOT / "docs" / "ectqa_incremental_summary.json")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def build_eval_command(args: argparse.Namespace, run: Mapping[str, str], output_json: Path) -> List[str]:
    command = [
        sys.executable,
        str(EVAL_SCRIPT),
        "--scenario",
        run["scenario"],
        "--answer-filter",
        args.answer_filter,
        "--limit",
        str(args.limit),
        "--offset",
        str(args.offset),
        "--agents",
        args.agents,
        "--corpus-scope",
        args.corpus_scope,
        "--metadata-filter",
        args.metadata_filter,
        "--retriever",
        args.retriever,
        "--retrieval-top-k",
        str(args.retrieval_top_k),
        "--metric-top-k",
        str(args.metric_top_k),
        "--temporal-evidence-cards",
        str(args.temporal_evidence_cards),
        "--temporal-evidence-chars",
        str(args.temporal_evidence_chars),
        "--temporal-pseudo-questions",
        str(args.temporal_pseudo_questions),
        "--output-json",
        str(output_json),
    ]
    command.append("--graph-fact-pack" if args.graph_fact_pack else "--no-graph-fact-pack")
    if args.quiet:
        command.append("--quiet")
    return command


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def agent_metric(result: Mapping[str, Any], agent: str, metric: str) -> Any:
    return ((result.get("agents") or {}).get(agent) or {}).get(metric)


def build_summary(args: argparse.Namespace, run_results: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    agents = [agent.strip() for agent in args.agents.split(",") if agent.strip()]
    per_agent: Dict[str, Any] = {}
    for agent in agents:
        base = run_results["base_old_questions_old_corpus"]
        retention = run_results["retention_old_questions_updated_corpus"]
        new = run_results["new_questions_updated_corpus"]
        base_rate = agent_metric(base, agent, "correct_like_rate")
        retention_rate = agent_metric(retention, agent, "correct_like_rate")
        new_rate = agent_metric(new, agent, "correct_like_rate")
        per_agent[agent] = {
            "base_correct_like_rate": base_rate,
            "retention_correct_like_rate": retention_rate,
            "retention_delta_vs_base": safe_delta(retention_rate, base_rate),
            "new_correct_like_rate": new_rate,
            "base_doc_recall_at_k": agent_metric(base, agent, "doc_recall_at_k"),
            "retention_doc_recall_at_k": agent_metric(retention, agent, "doc_recall_at_k"),
            "new_doc_recall_at_k": agent_metric(new, agent, "doc_recall_at_k"),
            "base_num_errors": agent_metric(base, agent, "num_errors"),
            "retention_num_errors": agent_metric(retention, agent, "num_errors"),
            "new_num_errors": agent_metric(new, agent, "num_errors"),
        }
    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": PROTOCOL_RUNS,
        "config": {
            "limit": args.limit,
            "offset": args.offset,
            "answer_filter": args.answer_filter,
            "agents": agents,
            "corpus_scope": args.corpus_scope,
            "metadata_filter": args.metadata_filter,
            "retriever": args.retriever,
            "graph_fact_pack": args.graph_fact_pack,
            "temporal_pseudo_questions": args.temporal_pseudo_questions,
        },
        "runs": {
            name: {
                "dataset": result.get("dataset", {}),
                "agents": result.get("agents", {}),
                "overall": result.get("overall", {}),
            }
            for name, result in run_results.items()
        },
        "per_agent": per_agent,
    }


def safe_delta(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    summary_json = args.summary_json.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    run_results: Dict[str, Dict[str, Any]] = {}
    for run in PROTOCOL_RUNS:
        output_json = output_dir / f"{run['name']}_limit{args.limit}.json"
        command = build_eval_command(args, run, output_json)
        if not args.quiet:
            print(f"\n[protocol] running {run['name']}: {' '.join(command)}")
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        run_results[run["name"]] = load_json(output_json)

    summary = build_summary(args, run_results)
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"per_agent": summary["per_agent"]}, ensure_ascii=False, indent=2))
    print(f"Saved incremental summary to: {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
