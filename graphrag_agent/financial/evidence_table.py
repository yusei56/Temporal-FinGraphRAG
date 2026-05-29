"""Evidence-table construction for financial temporal reasoning."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .metric_extractors import (
    CURRENCY_UNITS,
    METRIC_SPECS_BY_KEY,
    PERCENT_UNITS,
    detect_query_metrics,
)
from .temporal_facts import compact_text, important_query_terms, normalize_metadata_text


@dataclass(frozen=True)
class EvidenceTableRow:
    row_id: str
    company_name: str
    stock_code: str
    year: str
    quarter: str
    period: str
    period_type: str
    metric_text: str
    raw_value: str
    value: Optional[float]
    unit: str
    source_chunk_id: str
    source_card_id: str
    evidence_text: str
    relevance_score: float

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["relevance_score"] = round(float(self.relevance_score), 4)
        return payload


COMPARISON_MAX_WORDS = {
    "highest",
    "largest",
    "maximum",
    "max",
    "most",
    "greater",
    "higher",
}

COMPARISON_MIN_WORDS = {
    "lowest",
    "smallest",
    "minimum",
    "min",
    "least",
    "lower",
    "less",
}


def comparison_direction(query: str) -> str:
    tokens = set(normalize_metadata_text(query).split())
    if tokens & COMPARISON_MIN_WORDS:
        return "min"
    if tokens & COMPARISON_MAX_WORDS:
        return "max"
    return ""


def asks_for_quarter(query: str) -> bool:
    normalized = normalize_metadata_text(query)
    return bool(re.search(r"\bq[1-4]\b", normalized)) or "quarter" in normalized


def asks_for_year(query: str) -> bool:
    normalized = normalize_metadata_text(query)
    return "year" in normalized or bool(re.search(r"\b20\d{2}\b", normalized))


def period_score(query: str, period_type: str, quarter: str) -> float:
    period_type = period_type or ""
    score = 0.0
    if "guidance" in period_type:
        score -= 4.0
    if asks_for_quarter(query):
        if period_type in {"quarter", "quarter_or_3m", "quarter_or_fiscal_year"}:
            score += 2.0
        if "fiscal_year" in period_type or "year_to_date" in period_type:
            score -= 1.5
    if asks_for_year(query):
        if "fiscal_year" in period_type or str(quarter).lower() == "q4":
            score += 1.5
        if "year_to_date" in period_type:
            score -= 0.5
    return score


def unit_class(unit: str) -> str:
    """Map a normalized unit to its metric unit-class, or '' if unknown."""
    if unit in CURRENCY_UNITS:
        return "currency"
    if unit in PERCENT_UNITS:
        return "percent"
    return ""


def expected_unit_classes(metric_keys: Sequence[str]) -> set[str]:
    """Unit classes a query's metrics expect (per_share is satisfied by currency)."""
    classes: set[str] = set()
    for key in metric_keys:
        spec = METRIC_SPECS_BY_KEY.get(key)
        if not spec:
            continue
        classes.add("currency" if spec.unit_class == "per_share" else spec.unit_class)
    return classes


def metric_match_score(query: str, fact: Mapping[str, Any], fact_text: str) -> float:
    """Reward facts about the metric the query asked for; penalise off-metric facts.

    Replaces the previous hardcoded EPS/margin/revenue unit bumps with general
    metric-key matching driven by the named-metric extractor's patterns, so the
    signal extends automatically as new metrics are added.
    """
    query_metrics = detect_query_metrics(query)
    if not query_metrics:
        return 0.0
    fact_metrics = set(detect_query_metrics(fact_text))
    expected = expected_unit_classes(query_metrics)
    fact_class = unit_class(str(fact.get("unit", "")))

    score = 0.0
    if set(query_metrics) & fact_metrics:
        score += 5.0  # fact text is about the asked-for metric
    elif fact_metrics:
        score -= 3.0  # fact is clearly about a different named metric
    if fact_class and expected:
        score += 4.0 if fact_class in expected else -3.0
    return score


def fact_relevance_score(query: str, fact: Mapping[str, Any]) -> float:
    terms = important_query_terms(query)
    text = compact_text(
        " ".join(
            [
                str(fact.get("metric_text", "")),
                str(fact.get("evidence_text", "")),
                str(fact.get("raw_value", "")),
            ]
        )
    )
    normalized = set(normalize_metadata_text(text).split())
    overlap = len(terms & normalized)
    unit = str(fact.get("unit", ""))
    unit_bonus = 1.0 if unit in (CURRENCY_UNITS | PERCENT_UNITS) else 0.0
    numeric_bonus = 1.0 if fact.get("value") is not None else 0.0
    return (
        overlap * 4.0
        + unit_bonus
        + numeric_bonus
        + metric_match_score(query, fact, text)
        + period_score(
            query,
            str(fact.get("period_type", "")),
            str(fact.get("period_quarter", "")),
        )
    )


def build_evidence_table(
    query: str,
    evidence_cards: Sequence[Mapping[str, Any]],
    *,
    max_rows: int = 24,
) -> Dict[str, Any]:
    rows: List[EvidenceTableRow] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for card in evidence_cards:
        for fact in card.get("facts") or []:
            score = fact_relevance_score(query, fact)
            if score <= 0:
                continue
            key = (
                str(fact.get("source_chunk_id", card.get("chunk_id", ""))),
                str(fact.get("metric_text", "")),
                str(fact.get("raw_value", "")),
                str(fact.get("period_year", card.get("year", ""))),
                str(fact.get("period_quarter", card.get("quarter", ""))),
            )
            if key in seen:
                continue
            seen.add(key)
            year = str(fact.get("period_year", card.get("year", "")))
            quarter = str(fact.get("period_quarter", card.get("quarter", "")))
            rows.append(
                EvidenceTableRow(
                    row_id=f"R{len(rows) + 1}",
                    company_name=str(fact.get("entity_name", card.get("company_name", ""))),
                    stock_code=str(fact.get("stock_code", card.get("stock_code", ""))),
                    year=year,
                    quarter=quarter,
                    period=f"{year}-{quarter}" if year and quarter else year or quarter,
                    period_type=str(fact.get("period_type", "")),
                    metric_text=str(fact.get("metric_text", "")),
                    raw_value=str(fact.get("raw_value", "")),
                    value=fact.get("value"),
                    unit=str(fact.get("unit", "")),
                    source_chunk_id=str(fact.get("source_chunk_id", card.get("chunk_id", ""))),
                    source_card_id=str(card.get("card_id", "")),
                    evidence_text=compact_text(str(fact.get("evidence_text", "")))[:320],
                    relevance_score=score,
                )
            )

    direction = comparison_direction(query)
    if direction in {"max", "min"}:
        rows.sort(
            key=lambda row: (
                -row.relevance_score,
                0 if row.value is not None else 1,
                -(row.value or 0.0) if direction == "max" else (row.value or 0.0),
            )
        )
    else:
        rows.sort(key=lambda row: -row.relevance_score)

    selected = rows[:max_rows]
    return {
        "comparison_direction": direction,
        "rows": [row.to_dict() for row in selected],
        "num_rows": len(selected),
        "num_candidate_rows": len(rows),
        "period_guidance": period_guidance(query),
    }


def period_guidance(query: str) -> str:
    if asks_for_quarter(query):
        return "Question asks for quarter-level evidence; prefer quarter/3-month rows over fiscal-year or year-to-date rows."
    if asks_for_year(query):
        return "Question asks for year-level evidence; prefer full-year rows, q4 rows, or explicit 'for the year' evidence."
    return "Use the period_type column to avoid mixing quarterly, year-to-date, and full-year values."
