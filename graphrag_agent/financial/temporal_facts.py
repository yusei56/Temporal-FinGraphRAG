"""Structured financial temporal facts and evidence-card extraction.

This module is intentionally independent from the ECT-QA evaluator so the
same schema can be reused by graph ingestion, graph search, evaluation, and
product evidence-chain rendering.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


QUERY_STOPWORDS = {
    "about",
    "above",
    "after",
    "again",
    "against",
    "also",
    "among",
    "and",
    "answer",
    "any",
    "are",
    "before",
    "between",
    "both",
    "can",
    "company",
    "compare",
    "did",
    "does",
    "during",
    "each",
    "earnings",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "its",
    "more",
    "most",
    "much",
    "not",
    "quarter",
    "question",
    "than",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "through",
    "was",
    "were",
    "what",
    "when",
    "which",
    "with",
    "year",
}

FINANCIAL_NUMBER_PATTERN = re.compile(
    r"(?P<prefix>\$|usd\s*)?"
    r"(?P<sign>-)?"
    r"\b(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*(?P<unit>billion|million|thousand|basis points?|bps|percent|percentage points?|%))?",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class FinancialNumber:
    raw: str
    value: Optional[float]
    unit: str
    context: str
    start: int
    end: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinancialTemporalFact:
    fact_id: str
    entity_name: str
    stock_code: str
    metric_text: str
    value: Optional[float]
    raw_value: str
    unit: str
    period_year: str
    period_quarter: str
    period_type: str
    source_chunk_id: str
    source_filename: str
    evidence_text: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalEvidenceCard:
    card_id: str
    chunk_id: str
    filename: str
    rank: int
    retrieval_score: float
    company_name: str
    stock_code: str
    year: str
    quarter: str
    company_match: bool
    time_match: bool
    query_term_overlap: List[str]
    numbers: List[FinancialNumber]
    facts: List[FinancialTemporalFact]
    excerpt: str

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["numbers"] = [number.to_dict() for number in self.numbers]
        payload["facts"] = [fact.to_dict() for fact in self.facts]
        return payload


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def compact_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_metadata_text(text: str) -> str:
    value = (text or "").lower()
    value = value.replace("Ã¢â‚¬â„¢", "'").replace("â€™", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def important_query_terms(query: str) -> set[str]:
    terms = set()
    for token in normalize_metadata_text(query).split():
        if len(token) < 3 or token in QUERY_STOPWORDS:
            continue
        if token.isdigit():
            continue
        terms.add(token)
    return terms


def company_name_tokens(companies: Sequence[str]) -> set[str]:
    tokens: set[str] = set()
    for company in companies:
        tokens.update(normalize_metadata_text(company).split())
    return {token for token in tokens if len(token) >= 2}


def metadata_values(metadata: Any, field: str) -> Tuple[Any, ...]:
    value = getattr(metadata, field, ())
    if value is None:
        return ()
    return tuple(value)


def build_targeted_company_query(
    query: str,
    company: str,
    metadata: Any,
    *,
    year: Optional[str] = None,
    quarter: Optional[str] = None,
) -> str:
    matched_companies = metadata_values(metadata, "matched_companies")
    terms = important_query_terms(query) - company_name_tokens(matched_companies)
    time_terms: List[str] = []
    if year:
        time_terms.append(year)
        if quarter:
            time_terms.append(quarter)
    elif metadata_values(metadata, "requested_year_quarters"):
        for requested_year, requested_quarter in metadata_values(metadata, "requested_year_quarters"):
            time_terms.extend([str(requested_year), str(requested_quarter)])
    else:
        time_terms.extend(str(item) for item in metadata_values(metadata, "requested_years"))
    return compact_text(" ".join([company, *time_terms, *sorted(terms)]))


def normalize_financial_unit(unit: str, has_money_prefix: bool) -> str:
    value = (unit or "").lower().strip()
    if value in {"billion", "million", "thousand"}:
        return value
    if value in {"basis point", "basis points", "bps"}:
        return "basis_points"
    if value in {"percent", "%", "percentage point", "percentage points"}:
        return "percent"
    if has_money_prefix:
        return "currency"
    return "absolute"


def normalize_financial_value(raw_value: str, unit: str, sign: str) -> Optional[float]:
    try:
        value = float(raw_value.replace(",", ""))
    except ValueError:
        return None
    if sign == "-":
        value = -value
    if unit == "billion":
        return value * 1_000_000_000
    if unit == "million":
        return value * 1_000_000
    if unit == "thousand":
        return value * 1_000
    return value


def extract_financial_numbers(text: str, *, limit: int = 8) -> List[FinancialNumber]:
    numbers: List[FinancialNumber] = []
    for match in FINANCIAL_NUMBER_PATTERN.finditer(text or ""):
        raw_number = match.group("number") or ""
        raw_unit = match.group("unit") or ""
        prefix = match.group("prefix") or ""
        sign = match.group("sign") or ""
        if not prefix and not raw_unit and re.fullmatch(r"(19|20)\d{2}", raw_number):
            continue
        unit = normalize_financial_unit(raw_unit, bool(prefix))
        start = max(0, match.start() - 90)
        end = min(len(text), match.end() + 110)
        numbers.append(
            FinancialNumber(
                raw=compact_text(match.group(0)),
                value=normalize_financial_value(raw_number, unit, sign),
                unit=unit,
                context=compact_text(text[start:end]),
                start=match.start(),
                end=match.end(),
            )
        )
        if len(numbers) >= limit:
            break
    return numbers


def infer_period_type(text: str, year: str, quarter: str) -> str:
    lowered = (text or "").lower()
    if "guidance" in lowered:
        return "guidance"
    if "full year" in lowered or "for the year" in lowered or "during the year" in lowered:
        return "fiscal_year"
    if "first nine months" in lowered:
        return "year_to_date_9m"
    if "first half" in lowered or "six months" in lowered:
        return "year_to_date_6m"
    if "first three months" in lowered or "three months" in lowered:
        return "quarter_or_3m"
    if str(quarter).lower() == "q4":
        return "quarter_or_fiscal_year"
    return "quarter"


def infer_metric_text(context: str, query_terms: set[str]) -> str:
    normalized_words = normalize_metadata_text(context).split()
    overlap = [word for word in normalized_words if word in query_terms]
    if overlap:
        seen: set[str] = set()
        ordered = []
        for word in overlap:
            if word in seen:
                continue
            seen.add(word)
            ordered.append(word)
        return " ".join(ordered[:8])
    words = [word for word in normalized_words if word not in QUERY_STOPWORDS and not word.isdigit()]
    return " ".join(words[:8]) if words else "financial_metric"


def extract_financial_temporal_facts(
    hit: Mapping[str, Any],
    *,
    query_terms: Optional[set[str]] = None,
    max_numbers: int = 8,
) -> List[FinancialTemporalFact]:
    text = str(hit.get("text") or hit.get("text_preview") or "")
    terms = query_terms or set()
    numbers = extract_financial_numbers(text, limit=max_numbers)
    facts: List[FinancialTemporalFact] = []
    for index, number in enumerate(numbers, start=1):
        metric_text = infer_metric_text(number.context, terms)
        raw_id = "|".join(
            [
                str(hit.get("chunk_id", "")),
                str(index),
                metric_text,
                number.raw,
                str(hit.get("year", "")),
                str(hit.get("quarter", "")),
            ]
        )
        facts.append(
            FinancialTemporalFact(
                fact_id=f"ftf_{stable_id(raw_id)}",
                entity_name=str(hit.get("company_name", "")),
                stock_code=str(hit.get("stock_code", "")),
                metric_text=metric_text,
                value=number.value,
                raw_value=number.raw,
                unit=number.unit,
                period_year=str(hit.get("year", "")),
                period_quarter=str(hit.get("quarter", "")),
                period_type=infer_period_type(number.context, str(hit.get("year", "")), str(hit.get("quarter", ""))),
                source_chunk_id=str(hit.get("chunk_id", "")),
                source_filename=str(hit.get("filename", "")),
                evidence_text=number.context,
                confidence=float(hit.get("score") or 0.0),
            )
        )
    return facts


def temporal_fact_relevance(fact: FinancialTemporalFact, query_terms: set[str]) -> float:
    text = " ".join([fact.metric_text, fact.evidence_text, fact.raw_value])
    normalized = set(normalize_metadata_text(text).split())
    overlap = len(query_terms & normalized)
    value_bonus = 1.0 if fact.value is not None else 0.0
    unit_bonus = 1.0 if fact.unit in {"billion", "million", "thousand", "currency", "percent", "basis_points"} else 0.0
    if "eps" in query_terms or {"earnings", "share"} <= query_terms:
        if fact.unit == "currency":
            unit_bonus += 4.0
        elif fact.unit in {"percent", "basis_points", "billion", "million", "thousand"}:
            unit_bonus -= 3.0
    if "margin" in query_terms:
        if fact.unit in {"percent", "basis_points"}:
            unit_bonus += 3.0
        elif fact.unit in {"billion", "million", "thousand", "currency"}:
            unit_bonus -= 2.0
    return overlap * 2.0 + value_bonus + unit_bonus


def best_evidence_excerpt(text: str, query_terms: set[str], *, max_chars: int) -> str:
    cleaned = compact_text(text)
    if len(cleaned) <= max_chars:
        return cleaned

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or "")
        if sentence.strip()
    ]
    scored: List[Tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences):
        normalized = set(normalize_metadata_text(sentence).split())
        term_hits = len(query_terms & normalized)
        number_hits = len(list(FINANCIAL_NUMBER_PATTERN.finditer(sentence)))
        scored.append((term_hits * 3 + min(number_hits, 3), index, compact_text(sentence)))

    useful_indices = [
        index
        for score, index, _sentence in sorted(scored, key=lambda item: (-item[0], item[1]))[:3]
        if score > 0
    ]
    if not useful_indices:
        return cleaned[: max_chars - 20].rstrip() + " ...[truncated]"

    useful_set = set(useful_indices)
    selected = [
        sentence
        for _score, index, sentence in sorted(scored, key=lambda item: item[1])
        if index in useful_set
    ]
    excerpt = compact_text(" ".join(selected))
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[: max_chars - 20].rstrip() + " ...[truncated]"


def build_temporal_evidence_cards(
    query: str,
    hits: Sequence[Mapping[str, Any]],
    metadata: Any,
    *,
    max_cards: int,
    excerpt_chars: int,
) -> List[Dict[str, Any]]:
    query_terms = important_query_terms(query)
    requested_yq = set(metadata_values(metadata, "requested_year_quarters"))
    requested_years = set(str(year) for year in metadata_values(metadata, "requested_years"))
    matched_companies = set(str(company) for company in metadata_values(metadata, "matched_companies"))
    metric_query_terms = query_terms - company_name_tokens(tuple(matched_companies))
    scored_cards: List[Tuple[float, int, TemporalEvidenceCard]] = []

    for rank, hit in enumerate(hits, start=1):
        hit_text = str(hit.get("text") or hit.get("text_preview") or "")
        hit_terms = set(normalize_metadata_text(hit_text).split())
        overlap = sorted(query_terms & hit_terms)
        company_match = bool(matched_companies and hit.get("company_name") in matched_companies)
        time_match = False
        if requested_yq:
            time_match = (str(hit.get("year")), str(hit.get("quarter"))) in requested_yq
        elif requested_years:
            time_match = str(hit.get("year")) in requested_years

        numbers = extract_financial_numbers(hit_text, limit=16)
        facts = extract_financial_temporal_facts(hit, query_terms=metric_query_terms, max_numbers=16)
        best_fact_relevance = max(
            (temporal_fact_relevance(fact, metric_query_terms) for fact in facts),
            default=0.0,
        )
        annual_year_query = bool(requested_years and not requested_yq)
        retrieval_score = float(hit.get("score") or 0.0)
        priority = retrieval_score
        priority += 1.5 if company_match else 0.0
        priority += 1.5 if time_match else 0.0
        priority += min(len(overlap), 6) * 0.12
        priority += min(len(numbers), 4) * 0.08
        priority += min(best_fact_relevance, 12.0) * 0.18
        if annual_year_query:
            if str(hit.get("quarter", "")).lower() == "q4":
                priority += 0.9
            lowered_text = hit_text.lower()
            if "full year" in lowered_text or "for the year" in lowered_text or "during the year" in lowered_text:
                priority += 0.45
            if (
                "first nine months" in lowered_text
                or "first three months" in lowered_text
                or "first half" in lowered_text
            ):
                priority -= 0.35

        card = TemporalEvidenceCard(
            card_id=f"E{rank}",
            chunk_id=str(hit.get("chunk_id", "")),
            filename=str(hit.get("filename", "")),
            rank=int(hit.get("rank", rank) or rank),
            retrieval_score=round(retrieval_score, 4),
            company_name=str(hit.get("company_name", "")),
            stock_code=str(hit.get("stock_code", "")),
            year=str(hit.get("year", "")),
            quarter=str(hit.get("quarter", "")),
            company_match=company_match,
            time_match=time_match,
            query_term_overlap=overlap[:10],
            numbers=numbers,
            facts=facts,
            excerpt=best_evidence_excerpt(hit_text, query_terms, max_chars=excerpt_chars),
        )
        scored_cards.append((priority, rank, card))

    cards = select_coverage_first_cards(scored_cards, metadata, max_cards=max_cards)
    return [card.to_dict() for card in cards]


def select_coverage_first_cards(
    scored_cards: Sequence[Tuple[float, int, TemporalEvidenceCard]],
    metadata: Any,
    *,
    max_cards: int,
) -> List[TemporalEvidenceCard]:
    coverage_keys: List[Any] = []
    coverage_field = ""
    matched_companies = metadata_values(metadata, "matched_companies")
    requested_yq = metadata_values(metadata, "requested_year_quarters")
    requested_years = metadata_values(metadata, "requested_years")

    if len(matched_companies) > 1:
        coverage_field = "company"
        coverage_keys = list(matched_companies)
    elif len(requested_yq) > 1:
        coverage_field = "year_quarter"
        coverage_keys = list(requested_yq)
    elif len(requested_years) > 1:
        coverage_field = "year"
        coverage_keys = list(requested_years)

    if not coverage_keys:
        sorted_cards = sorted(scored_cards, key=lambda item: (-item[0], item[1]))
        return [card for _priority, _rank, card in sorted_cards[:max_cards]]

    buckets: Dict[Any, List[Tuple[float, int, TemporalEvidenceCard]]] = {
        key: [] for key in coverage_keys
    }
    for item in scored_cards:
        _priority, _rank, card = item
        if coverage_field == "company":
            key = card.company_name
        elif coverage_field == "year_quarter":
            key = (card.year, card.quarter)
        else:
            key = card.year
        if key in buckets:
            buckets[key].append(item)

    selected_items: List[Tuple[float, int, TemporalEvidenceCard]] = []
    selected_chunk_ids: set[str] = set()
    for key in coverage_keys:
        bucket = buckets.get(key, [])
        if not bucket:
            continue
        best = max(bucket, key=lambda item: (item[0], -item[1]))
        chunk_id = best[2].chunk_id
        if chunk_id and chunk_id not in selected_chunk_ids:
            selected_chunk_ids.add(chunk_id)
            selected_items.append(best)

    remaining = [
        item for item in scored_cards
        if item[2].chunk_id not in selected_chunk_ids
    ]
    remaining.sort(key=lambda item: (-item[0], item[1]))
    selected_items.extend(remaining[: max(0, max_cards - len(selected_items))])
    return [card for _priority, _rank, card in selected_items[:max_cards]]


def temporal_query_intent(query: str) -> Dict[str, bool]:
    normalized = normalize_metadata_text(query)
    comparison_words = {
        "highest",
        "lowest",
        "largest",
        "smallest",
        "maximum",
        "minimum",
        "increase",
        "decrease",
        "greater",
        "less",
        "higher",
        "lower",
        "compare",
        "versus",
        "vs",
    }
    trend_words = {"after", "before", "between", "from", "through", "trend", "change", "over"}
    tokens = set(normalized.split())
    return {
        "needs_comparison": bool(tokens & comparison_words),
        "needs_temporal_range": bool(tokens & trend_words),
        "mentions_year_or_quarter": bool(re.search(r"\b20\d{2}\b|\bq[1-4]\b", normalized)),
    }
