"""Deterministic pseudo-question expansion for financial temporal retrieval."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .temporal_facts import (
    company_name_tokens,
    compact_text,
    important_query_terms,
    metadata_values,
    normalize_metadata_text,
)


@dataclass(frozen=True)
class PseudoQuestion:
    question: str
    purpose: str
    company_name: str
    year: str
    quarter: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def generate_pseudo_questions(
    query: str,
    metadata: Any,
    *,
    max_questions: int = 8,
) -> List[Dict[str, str]]:
    """Generate HopRAG-style supplementary queries without spending LLM tokens."""

    companies = [str(company) for company in metadata_values(metadata, "matched_companies")]
    years = [str(year) for year in metadata_values(metadata, "requested_years")]
    year_quarters = [
        (str(year), str(quarter))
        for year, quarter in metadata_values(metadata, "requested_year_quarters")
    ]
    metric_phrase = infer_metric_phrase(query, companies)
    comparison = infer_comparison_phrase(query)

    pseudo_questions: List[PseudoQuestion] = []
    if companies and year_quarters:
        for company in companies:
            for year, quarter in year_quarters:
                pseudo_questions.append(
                    PseudoQuestion(
                        question=(
                            f"What did {company} report about {metric_phrase} "
                            f"in {year}-{quarter}?"
                        ),
                        purpose="company_period_metric_recall",
                        company_name=company,
                        year=year,
                        quarter=quarter,
                    )
                )
        if comparison:
            company_phrase = " and ".join(companies)
            period_phrase = ", ".join(f"{year}-{quarter}" for year, quarter in year_quarters)
            pseudo_questions.append(
                PseudoQuestion(
                    question=(
                        f"Which of {company_phrase} has the {comparison} "
                        f"{metric_phrase} across {period_phrase}?"
                    ),
                    purpose="comparison_bridge_recall",
                    company_name=company_phrase,
                    year="",
                    quarter="",
                )
            )
    elif companies and years:
        for company in companies:
            for year in years:
                pseudo_questions.append(
                    PseudoQuestion(
                        question=(
                            f"What did {company} report about {metric_phrase} "
                            f"for fiscal year {year}?"
                        ),
                        purpose="company_year_metric_recall",
                        company_name=company,
                        year=year,
                        quarter="",
                    )
                )
        if comparison:
            company_phrase = " and ".join(companies)
            pseudo_questions.append(
                PseudoQuestion(
                    question=(
                        f"Compare {company_phrase} on {metric_phrase} "
                        f"for {' and '.join(years)} and identify the {comparison} value."
                    ),
                    purpose="comparison_bridge_recall",
                    company_name=company_phrase,
                    year=",".join(years),
                    quarter="",
                )
            )
    elif companies:
        for company in companies:
            pseudo_questions.append(
                PseudoQuestion(
                    question=f"What did {company} report about {metric_phrase}?",
                    purpose="company_metric_recall",
                    company_name=company,
                    year="",
                    quarter="",
                )
            )

    return dedupe_pseudo_questions(pseudo_questions, max_questions=max_questions)


def infer_metric_phrase(query: str, companies: List[str]) -> str:
    terms = important_query_terms(query) - company_name_tokens(companies)
    time_or_comparison_terms = {
        "highest",
        "lowest",
        "largest",
        "smallest",
        "maximum",
        "minimum",
        "after",
        "before",
        "between",
        "from",
        "through",
        "compare",
        "versus",
    }
    metric_terms = [
        term for term in sorted(terms)
        if term not in time_or_comparison_terms and not term.startswith("q")
    ]
    if metric_terms:
        return compact_text(" ".join(metric_terms[:8]))
    return "the requested financial metric"


def infer_comparison_phrase(query: str) -> str:
    normalized = set(normalize_metadata_text(query).split())
    if normalized & {"lowest", "smallest", "minimum", "min", "least", "lower"}:
        return "lowest"
    if normalized & {"highest", "largest", "maximum", "max", "most", "higher"}:
        return "highest"
    if normalized & {"compare", "versus", "vs", "greater", "less"}:
        return "relevant"
    return ""


def dedupe_pseudo_questions(
    questions: List[PseudoQuestion],
    *,
    max_questions: int,
) -> List[Dict[str, str]]:
    seen: set[str] = set()
    result: List[Dict[str, str]] = []
    for item in questions:
        key = normalize_metadata_text(item.question)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item.to_dict())
        if len(result) >= max_questions:
            break
    return result
