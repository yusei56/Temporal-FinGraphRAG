#!/usr/bin/env python3
"""Run a small ablation matrix over TemporalEvidenceAgent settings."""

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

ABLATION_VARIANTS: Dict[str, Dict[str, Any]] = {
    "table_only": {
        "description": "Evidence table + compact evidence cards; fact sentences and pseudo questions disabled.",
        "temporal_fact_sentences": False,
        "temporal_pseudo_questions": 0,
    },
    "fact_sentences": {
        "description": "Adds ToG-style fact sentences stitched to chunk excerpts.",
        "temporal_fact_sentences": True,
        "temporal_pseudo_questions": 0,
    },
    "pseudoq_table": {
        "description": "Adds HopRAG-style pseudo-question recall without fact sentences.",
        "temporal_fact_sentences": False,
        "temporal_pseudo_questions": 8,
    },
    "fact_sentences_pseudoq": {
        "description": "Combines fact sentences with pseudo-question supplementary recall.",
        "temporal_fact_sentences": True,
        "temporal_pseudo_questions": 8,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ECT-QA ablation matrix.")
    parser.add_argument("--scenario", choices=["base", "updated", "new"], default="new")
    parser.add_argument("--answer-filter", choices=["answerable", "unanswerable", "all"], default="answerable")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--variants", default="table_only,fact_sentences,pseudoq_table,fact_sentences_pseudoq")
    parser.add_argument("--agents", default="TemporalEvidenceAgent")
    parser.add_argument("--metadata-filter", choices=["off", "boost", "strict"], default="boost")
    parser.add_argument("--retriever", choices=["tfidf", "graph"], default="tfidf")
    parser.add_argument("--graph-fact-pack", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--retrieval-top-k", type=int, default=8)
    parser.add_argument("--metric-top-k", type=int, default=8)
    parser.add_argument("--temporal-evidence-cards", type=int, default=8)
    parser.add_argument("--temporal-evidence-chars", type=int, default=700)
    parser.add_argument("--corpus-scope", choices=["evidence", "full"], default="full")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "docs" / "ablation_runs")
    parser.add_argument("--summary-json", type=Path, default=PROJECT_ROOT / "docs" / "ectqa_ablation_summary.json")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def selected_variants(args: argparse.Namespace) -> List[str]:
    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in names if name not in ABLATION_VARIANTS]
    if unknown:
        raise ValueError(f"Unknown ablation variants: {unknown}. Known: {sorted(ABLATION_VARIANTS)}")
    return names


def build_eval_command(
    args: argparse.Namespace,
    variant_name: str,
    variant: Mapping[str, Any],
    output_json: Path,
) -> List[str]:
    command = [
        sys.executable,
        str(EVAL_SCRIPT),
        "--scenario",
        args.scenario,
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
        str(variant["temporal_pseudo_questions"]),
        "--output-json",
        str(output_json),
    ]
    command.append(
        "--temporal-fact-sentences"
        if variant["temporal_fact_sentences"]
        else "--no-temporal-fact-sentences"
    )
    command.append("--graph-fact-pack" if args.graph_fact_pack else "--no-graph-fact-pack")
    if args.quiet:
        command.append("--quiet")
    return command


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_summary(
    args: argparse.Namespace,
    names: List[str],
    results: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    agents = [agent.strip() for agent in args.agents.split(",") if agent.strip()]
    baseline_name = names[0]
    per_variant: Dict[str, Any] = {}
    for name in names:
        result = results[name]
        per_variant[name] = {
            "description": ABLATION_VARIANTS[name]["description"],
            "settings": ABLATION_VARIANTS[name],
            "dataset": result.get("dataset", {}),
            "agents": result.get("agents", {}),
            "overall": result.get("overall", {}),
        }

    deltas: Dict[str, Any] = {}
    for agent in agents:
        baseline_metrics = ((results[baseline_name].get("agents") or {}).get(agent) or {})
        agent_deltas: Dict[str, Any] = {}
        for name in names:
            metrics = ((results[name].get("agents") or {}).get(agent) or {})
            agent_deltas[name] = {
                "correct_like_delta": safe_delta(
                    metrics.get("correct_like_rate"),
                    baseline_metrics.get("correct_like_rate"),
                ),
                "doc_recall_delta": safe_delta(
                    metrics.get("doc_recall_at_k"),
                    baseline_metrics.get("doc_recall_at_k"),
                ),
                "temporal_coverage_delta": safe_delta(
                    metrics.get("temporal_coverage_at_k"),
                    baseline_metrics.get("temporal_coverage_at_k"),
                ),
            }
        deltas[agent] = agent_deltas

    return {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "scenario": args.scenario,
            "answer_filter": args.answer_filter,
            "limit": args.limit,
            "offset": args.offset,
            "agents": agents,
            "corpus_scope": args.corpus_scope,
            "metadata_filter": args.metadata_filter,
            "retriever": args.retriever,
            "graph_fact_pack": args.graph_fact_pack,
            "baseline_variant": baseline_name,
        },
        "variant_order": names,
        "variants": per_variant,
        "deltas_vs_baseline": deltas,
    }


def safe_delta(left: Any, right: Any) -> Any:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def main() -> int:
    args = parse_args()
    names = selected_variants(args)
    output_dir = args.output_dir.resolve()
    summary_json = args.summary_json.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_json.parent.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Dict[str, Any]] = {}
    for name in names:
        variant = ABLATION_VARIANTS[name]
        output_json = output_dir / f"{args.scenario}_{name}_limit{args.limit}.json"
        command = build_eval_command(args, name, variant, output_json)
        if not args.quiet:
            print(f"\n[ablation] running {name}: {' '.join(command)}")
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        results[name] = load_json(output_json)

    summary = build_summary(args, names, results)
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"deltas_vs_baseline": summary["deltas_vs_baseline"]}, ensure_ascii=False, indent=2))
    print(f"Saved ablation summary to: {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
