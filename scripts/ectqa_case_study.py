#!/usr/bin/env python3
"""Generate a compact case-study report for failed ECT-QA examples."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]


MONEY_OR_NUMBER_PATTERN = re.compile(
    r"(?:\$|usd\s*)?-?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:billion|million|thousand|basis points?|bps|percent|%)?",
    flags=re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create ECT-QA failure case-study markdown.")
    parser.add_argument("--input-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--only-failures", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-cases", type=int, default=20)
    parser.add_argument("--max-answer-chars", type=int, default=700)
    parser.add_argument("--max-hit-text-chars", type=int, default=380)
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_failure(row: Mapping[str, Any]) -> bool:
    bucket = ((row.get("answer_metrics") or {}).get("bucket") or "").lower()
    return bucket not in {"correct", "correct_refusal"}


def classify_failure(row: Mapping[str, Any]) -> List[str]:
    metrics = row.get("retrieval_metrics") or {}
    bucket = ((row.get("answer_metrics") or {}).get("bucket") or "").lower()
    labels: List[str] = []
    if row.get("error"):
        labels.append("runtime_error")
    if "refusal" in bucket:
        labels.append("wrong_refusal_policy")
    if safe_float(metrics.get("doc_recall_at_k")) < 1.0:
        labels.append("missing_gold_document")
    if safe_float(metrics.get("all_support_recall_at_k")) < 1.0:
        labels.append("missing_full_support_set")
    if safe_float(metrics.get("temporal_coverage_at_k")) < 1.0:
        labels.append("temporal_coverage_gap")
    if safe_float(metrics.get("evidence_text_recall_at_k")) < 0.5:
        labels.append("evidence_span_gap")

    gold_numbers = extract_numbers(row.get("gold_answer", ""))
    answer_numbers = extract_numbers(row.get("answer", ""))
    if gold_numbers and not set(gold_numbers).issubset(set(answer_numbers)):
        labels.append("numeric_value_mismatch")

    if not labels:
        labels.append("synthesis_or_reasoning_error")
    elif (
        "missing_gold_document" not in labels
        and "missing_full_support_set" not in labels
        and "temporal_coverage_gap" not in labels
        and "numeric_value_mismatch" in labels
    ):
        labels.append("retrieval_ok_generation_bad")
    return labels


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_numbers(text: Any) -> List[str]:
    values = []
    for match in MONEY_OR_NUMBER_PATTERN.finditer(str(text or "")):
        value = normalize_number_token(match.group(0))
        if value:
            values.append(value)
    return values


def normalize_number_token(text: str) -> str:
    value = re.sub(r"\s+", " ", text.lower().replace(",", "")).strip()
    value = value.replace("usd ", "$")
    return value


def truncate(text: Any, max_chars: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 16)].rstrip() + " ...[truncated]"


def retrieved_hits(row: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    events = row.get("retrieval_events") or []
    hits: List[Mapping[str, Any]] = []
    for event in events:
        hits.extend(event.get("hits") or [])
    return hits


def unique_filenames(hits: Sequence[Mapping[str, Any]], *, limit: int = 10) -> List[str]:
    seen: set[str] = set()
    values: List[str] = []
    for hit in hits:
        filename = str(hit.get("filename", ""))
        if filename and filename not in seen:
            seen.add(filename)
            values.append(filename)
        if len(values) >= limit:
            break
    return values


def top_hit_summaries(row: Mapping[str, Any], *, max_hit_text_chars: int) -> List[str]:
    summaries = []
    for hit in retrieved_hits(row)[:3]:
        summaries.append(
            (
                f"- rank={hit.get('rank')} score={safe_float(hit.get('score')):.3f} "
                f"file={hit.get('filename')} chunk={hit.get('chunk_id')} "
                f"period={hit.get('year')}-{hit.get('quarter')}: "
                f"{truncate(hit.get('text_preview') or hit.get('text'), max_hit_text_chars)}"
            )
        )
    return summaries


def render_report(result: Mapping[str, Any], args: argparse.Namespace) -> str:
    rows = list(result.get("rows") or [])
    if args.only_failures:
        rows = [row for row in rows if is_failure(row)]
    rows = rows[: args.max_cases]
    label_counts = Counter(label for row in rows for label in classify_failure(row))
    bucket_counts = Counter((row.get("answer_metrics") or {}).get("bucket", "unknown") for row in rows)

    lines = [
        "# ECT-QA Failure Case Study",
        "",
        "## Source",
        "",
        f"- Input: `{args.input_json}`",
        f"- Examples analyzed: {len(rows)}",
        f"- Dataset config: `{json.dumps(result.get('config', {}), ensure_ascii=False)}`",
        "",
        "## Failure Buckets",
        "",
    ]
    for bucket, count in bucket_counts.most_common():
        lines.append(f"- `{bucket}`: {count}")
    lines.extend(["", "## Diagnostic Labels", ""])
    for label, count in label_counts.most_common():
        lines.append(f"- `{label}`: {count}")

    lines.extend(["", "## Cases", ""])
    for index, row in enumerate(rows, start=1):
        labels = classify_failure(row)
        retrieval = row.get("retrieval_metrics") or {}
        lines.extend(
            [
                f"### Case {index}: `{row.get('question_id')}`",
                "",
                f"- Labels: {', '.join(f'`{label}`' for label in labels)}",
                f"- Bucket: `{(row.get('answer_metrics') or {}).get('bucket')}`",
                f"- Reasoning type: `{row.get('reasoning_type')}`; question type: `{row.get('question_type')}`; hops: `{row.get('num_hops')}`",
                f"- Retrieval: docR={retrieval.get('doc_recall_at_k')}, evidenceTextR={retrieval.get('evidence_text_recall_at_k')}, allSupport={retrieval.get('all_support_recall_at_k')}, temporal={retrieval.get('temporal_coverage_at_k')}, citation={retrieval.get('citation_support_rate')}",
                f"- Gold files: `{', '.join(retrieval.get('gold_filenames') or [])}`",
                f"- Retrieved files: `{', '.join(unique_filenames(retrieved_hits(row)))}`",
                "",
                "**Question**",
                "",
                truncate(row.get("question"), 1000),
                "",
                "**Gold Answer**",
                "",
                truncate(row.get("gold_answer"), 1000),
                "",
                "**Model Answer**",
                "",
                truncate(row.get("answer"), args.max_answer_chars),
                "",
                "**Top Retrieved Hits**",
                "",
                *top_hit_summaries(row, max_hit_text_chars=args.max_hit_text_chars),
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation",
            "",
            "- `missing_gold_document` means retrieval did not retrieve all required source documents.",
            "- `missing_full_support_set` means at least one required evidence item is absent from top-k.",
            "- `evidence_span_gap` means the right document may be retrieved, but the exact supporting text was weakly covered.",
            "- `numeric_value_mismatch` means the model answer did not preserve all gold numeric values.",
            "- `retrieval_ok_generation_bad` means retrieval looked adequate, so the likely issue is fact extraction, ranking, or synthesis.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    args.input_json = args.input_json.resolve()
    args.output_md = args.output_md.resolve()
    result = load_json(args.input_json)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_report(result, args), encoding="utf-8")
    print(f"Saved case study to: {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
