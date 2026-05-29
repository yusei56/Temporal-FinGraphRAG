"""ToG-style fact sentence and chunk stitching for financial temporal QA."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .evidence_table import build_evidence_table
from .temporal_facts import compact_text
from .temporal_graph_retrieval import (
    build_coverage,
    row_matches_company,
    row_matches_time,
    select_coverage_first_rows,
)


@dataclass(frozen=True)
class FactSentence:
    sentence_id: str
    row_id: str
    company_name: str
    period: str
    period_type: str
    metric_text: str
    raw_value: str
    unit: str
    source_chunk_id: str
    source_card_id: str
    fact_sentence: str
    fact_path: List[str]
    supporting_excerpt: str
    relevance_score: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["relevance_score"] = round(float(self.relevance_score), 4)
        return payload


def build_fact_sentence_package(
    *,
    query: str,
    evidence_cards: Sequence[Mapping[str, Any]],
    metadata: Any,
    evidence_table: Optional[Mapping[str, Any]] = None,
    max_sentences: int = 16,
    excerpt_chars: int = 260,
) -> Dict[str, Any]:
    """Create compact fact paths, then stitch each path back to a source excerpt.

    The shape intentionally mirrors ToG-style reasoning: a fact path is the
    structured path the model should reason over, while the excerpt is the
    source text used to verify the path.
    """

    table = evidence_table or build_evidence_table(
        query,
        evidence_cards,
        max_rows=max(max_sentences * 2, max_sentences),
    )
    candidate_rows = [dict(row) for row in table.get("rows", [])]
    filtered_rows = [
        row for row in candidate_rows
        if row_matches_company(row, metadata) and row_matches_time(row, metadata)
    ]
    if not filtered_rows:
        filtered_rows = candidate_rows
    filtered_rows.sort(key=lambda row: float(row.get("relevance_score") or 0.0), reverse=True)
    selected_rows = select_coverage_first_rows(filtered_rows, metadata, top_k=max_sentences)

    cards_by_id = {str(card.get("card_id", "")): card for card in evidence_cards}
    cards_by_chunk = {str(card.get("chunk_id", "")): card for card in evidence_cards}
    sentences = [
        build_fact_sentence(
            row=row,
            index=index,
            cards_by_id=cards_by_id,
            cards_by_chunk=cards_by_chunk,
            excerpt_chars=excerpt_chars,
        ).to_dict()
        for index, row in enumerate(selected_rows, start=1)
    ]

    return {
        "method": "ToG-style fact sentence plus source-chunk stitching",
        "description": (
            "Use fact_path/fact_sentence as the reasoning skeleton and "
            "supporting_excerpt/source_chunk_id as provenance."
        ),
        "coverage": build_coverage(selected_rows, metadata),
        "sentences": sentences,
    }


def build_fact_sentence(
    *,
    row: Mapping[str, Any],
    index: int,
    cards_by_id: Mapping[str, Mapping[str, Any]],
    cards_by_chunk: Mapping[str, Mapping[str, Any]],
    excerpt_chars: int,
) -> FactSentence:
    company = compact_text(row.get("company_name", ""))
    period = compact_text(row.get("period", "")) or compact_period(row)
    period_type = compact_text(row.get("period_type", ""))
    metric = compact_text(row.get("metric_text", "")) or "financial metric"
    raw_value = compact_text(row.get("raw_value", ""))
    unit = compact_text(row.get("unit", ""))
    chunk_id = compact_text(row.get("source_chunk_id", ""))
    card_id = compact_text(row.get("source_card_id", ""))
    source_card = cards_by_id.get(card_id) or cards_by_chunk.get(chunk_id) or {}
    excerpt = compact_text(row.get("evidence_text", "")) or compact_text(source_card.get("excerpt", ""))
    if len(excerpt) > excerpt_chars:
        excerpt = excerpt[: max(0, excerpt_chars - 16)].rstrip() + " ...[truncated]"

    fact_sentence = (
        f"{company} | {period} | {metric} = {raw_value} "
        f"| unit={unit or 'unknown'} | period_type={period_type or 'unknown'} "
        f"| source={chunk_id}"
    )
    fact_path = [
        f"company:{company}",
        f"period:{period}",
        f"metric:{metric}",
        f"value:{raw_value}",
        f"source_chunk:{chunk_id}",
    ]
    return FactSentence(
        sentence_id=f"F{index}",
        row_id=compact_text(row.get("row_id", "")),
        company_name=company,
        period=period,
        period_type=period_type,
        metric_text=metric,
        raw_value=raw_value,
        unit=unit,
        source_chunk_id=chunk_id,
        source_card_id=card_id,
        fact_sentence=fact_sentence,
        fact_path=fact_path,
        supporting_excerpt=excerpt,
        relevance_score=float(row.get("relevance_score") or 0.0),
    )


def compact_period(row: Mapping[str, Any]) -> str:
    year = compact_text(row.get("year", ""))
    quarter = compact_text(row.get("quarter", ""))
    if year and quarter:
        return f"{year}-{quarter}"
    return year or quarter
