"""Deterministic, named financial-metric extractors.

The generic extractor in :mod:`temporal_facts` grabs *every* number in a chunk
and guesses its ``metric_text`` from query-term overlap. That is the root cause
of the numerical-reasoning weakness in the ECT-QA evaluation: the system never
knows *which* number is free cash flow, revenue, or EPS.

This module instead recognises well-known financial metrics *by name* and binds
each named mention to its supporting number and unit, producing typed
:class:`MetricFact` records. The same records are the input both for Neo4j fact
nodes (graph ingestion) and for metric-aware relevance scoring during search,
so detection is intentionally deterministic and dependency-free.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .temporal_facts import (
    FinancialNumber,
    compact_text,
    extract_financial_numbers,
    infer_period_type,
    normalize_metadata_text,
    stable_id,
)

# --- unit classes ---------------------------------------------------------

CURRENCY_UNITS = {"billion", "million", "thousand", "currency"}
PERCENT_UNITS = {"percent", "basis_points"}

# How far (in characters) a supporting number may sit from a metric mention.
NUMBER_WINDOW = 140


@dataclass(frozen=True)
class MetricSpec:
    """Definition of a recognisable financial metric."""

    key: str
    display: str
    unit_class: str  # "currency" | "percent" | "per_share"
    patterns: Tuple[re.Pattern, ...]

    def find_mentions(self, text: str) -> List[re.Match]:
        mentions: List[re.Match] = []
        for pattern in self.patterns:
            mentions.extend(pattern.finditer(text))
        return mentions


@dataclass(frozen=True)
class MetricFact:
    """A named metric bound to its supporting number and period."""

    fact_id: str
    metric_key: str
    metric_display: str
    qualifier: str  # "" | "gaap" | "non_gaap"
    value: Optional[float]
    raw_value: str
    unit: str
    entity_name: str
    stock_code: str
    period_year: str
    period_quarter: str
    period_type: str
    source_chunk_id: str
    source_filename: str
    metric_phrase: str
    evidence_text: str
    confidence: float
    value_kind: str = "level"  # "level" (the metric value) | "delta" (a change in it)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _p(*fragments: str) -> Tuple[re.Pattern, ...]:
    return tuple(re.compile(fragment, re.IGNORECASE) for fragment in fragments)


# Ordering matters: more specific phrases (cash+investments, fixed-maturity
# purchases) are listed before broad ones (revenue) so that overlapping text is
# attributed to the more precise metric first.
METRIC_SPECS: Tuple[MetricSpec, ...] = (
    MetricSpec(
        key="free_cash_flow",
        display="Free cash flow",
        unit_class="currency",
        patterns=_p(r"\bfree cash flow\b", r"\bFCF\b"),
    ),
    MetricSpec(
        key="cash_and_investments",
        display="Cash, cash equivalents and investments",
        unit_class="currency",
        patterns=_p(
            r"\bcash,?\s+cash equivalents,?\s+and\s+(?:short[- ]term\s+)?(?:marketable\s+)?investments\b",
            r"\bcash,?\s+cash equivalents,?\s+and\s+restricted cash\b",
            r"\bcash and (?:short[- ]term\s+)?investments\b",
            r"\bcash and cash equivalents\b",
        ),
    ),
    MetricSpec(
        key="fixed_maturity_purchases",
        display="Purchases of fixed-maturity securities",
        unit_class="currency",
        patterns=_p(
            r"\bpurchases? of fixed[- ]maturity (?:securities|investments)\b",
            r"\bfixed[- ]maturity securities? purchas\w*\b",
        ),
    ),
    MetricSpec(
        key="gross_margin",
        display="Gross margin",
        unit_class="percent",
        patterns=_p(r"\bgross (?:profit )?margins?\b"),
    ),
    MetricSpec(
        key="eps",
        display="Earnings per share",
        unit_class="per_share",
        patterns=_p(
            r"\bearnings per (?:diluted |basic )?share\b",
            r"\b(?:diluted |basic )?(?:net )?(?:income|earnings|loss) per (?:diluted |basic )?share\b",
            r"\bEPS\b",
        ),
    ),
    MetricSpec(
        key="revenue",
        display="Revenue",
        unit_class="currency",
        patterns=_p(
            r"\btotal (?:net )?revenues?\b",
            r"\bnet revenues?\b",
            r"\brevenues?\b",
            r"\bnet sales\b",
            r"\bsales\b",
        ),
    ),
)

METRIC_SPECS_BY_KEY: Dict[str, MetricSpec] = {spec.key: spec for spec in METRIC_SPECS}


# Only an unambiguous change cue *immediately* before the number marks a delta:
# "up 8%", "down 5%", "by 200". Verbs like "increased/improved" are excluded on
# purpose -- they frequently precede a level ("improved gross margin of 62.5%",
# "increased to 62.5%"), so flagging them demotes real levels.
_DELTA_BEFORE = re.compile(r"\b(up|down|by)\b[\s$]*$", re.IGNORECASE)


# Per-metric plausibility bounds on the *normalized* value (NOT one global range:
# a gross-margin %, an EPS $/share, and a revenue $-amount differ by orders of
# magnitude). Implausible binds (e.g. a 164.8% "gross margin" that is really a
# growth figure, or "$10.1B" mis-bound to EPS) are dropped at extraction so they
# never pollute the graph, the PPR seeds, or the answer.
_CURRENCY_MAGNITUDE_UNITS = {"billion", "million", "thousand", "currency"}


def is_plausible_value(metric_key: str, unit: str, value: Optional[float], value_kind: str) -> bool:
    if value is None:
        return True
    # Gross-margin *level* is a real percentage; a 164.8% level is a misbind.
    if metric_key == "gross_margin" and unit == "percent" and value_kind == "level":
        return -50.0 <= value <= 100.0
    # EPS is a per-share dollar figure; a magnitude-scaled value is a misbind.
    if metric_key == "eps" and unit in _CURRENCY_MAGNITUDE_UNITS:
        return -200.0 <= value <= 200.0
    # Large-currency metrics: reject impossible signs / absurd magnitudes.
    _CURRENCY_BOUNDS = {
        "revenue": (0.0, 5e12),
        "cash_and_investments": (0.0, 5e12),
        "free_cash_flow": (-5e12, 5e12),
        "fixed_maturity_purchases": (-5e12, 5e12),
    }
    if metric_key in _CURRENCY_BOUNDS and unit in _CURRENCY_MAGNITUDE_UNITS:
        lo, hi = _CURRENCY_BOUNDS[metric_key]
        return lo <= value <= hi
    return True


def classify_value_kind(unit: str, before_text: str) -> str:
    """Classify a bound number as a metric *level* or a *delta* (change).

    Conservative: basis points are deltas (margins are quoted in %, bps are
    changes), and a bare ``up``/``down``/``by`` right before the number marks a
    delta. Everything else is a level, to avoid demoting genuine level facts.
    """
    if unit == "basis_points":
        return "delta"
    if _DELTA_BEFORE.search(before_text.lower()):
        return "delta"
    return "level"


def detect_qualifier(window_text: str) -> str:
    """Classify a metric mention as GAAP / non-GAAP / unmarked from its context."""

    lowered = normalize_metadata_text(window_text)
    if re.search(r"\bnon gaap\b", lowered) or "adjusted" in lowered:
        return "non_gaap"
    if "gaap" in lowered:
        return "gaap"
    return ""


def _unit_matches(number: FinancialNumber, unit_class: str, window_text: str) -> bool:
    if unit_class == "currency":
        return number.unit in CURRENCY_UNITS
    if unit_class == "percent":
        return number.unit in PERCENT_UNITS
    if unit_class == "per_share":
        # EPS is a currency-per-share value: either an explicit $-amount or a
        # bare decimal that the surrounding text marks as "per share".
        if number.unit == "currency":
            return True
        if number.unit == "absolute" and "per share" in normalize_metadata_text(window_text):
            return number.value is not None and abs(number.value) < 1000
        return False
    return False


def _bind_number(
    mention: re.Match,
    numbers: Sequence[FinancialNumber],
    unit_class: str,
    text: str,
) -> Optional[FinancialNumber]:
    """Pick the closest unit-compatible number to a metric mention.

    Numbers appearing after the mention are preferred (English financial prose
    reads "revenue was $1.2 billion"); a closely preceding number is the
    fallback ("$1.2 billion in revenue").
    """

    after: List[Tuple[int, FinancialNumber]] = []
    before: List[Tuple[int, FinancialNumber]] = []
    for number in numbers:
        if number.start >= mention.end():
            distance = number.start - mention.end()
            bucket = after
        else:
            distance = mention.start() - number.end
            bucket = before
        if distance < 0 or distance > NUMBER_WINDOW:
            continue
        lo = min(mention.start(), number.start)
        hi = max(mention.end(), number.end)
        window_text = text[max(0, lo - 10): hi + 10]
        if not _unit_matches(number, unit_class, window_text):
            continue
        bucket.append((distance, number))
    if after:
        return min(after, key=lambda item: item[0])[1]
    if before:
        return min(before, key=lambda item: item[0])[1]
    return None


def extract_metric_facts(
    hit: Mapping[str, Any],
    *,
    metric_keys: Optional[Sequence[str]] = None,
    max_facts: int = 24,
) -> List[MetricFact]:
    """Extract named metric facts from a single retrieval hit / chunk.

    Args:
        hit: mapping with ``text``/``text_preview`` plus company/period metadata.
        metric_keys: restrict extraction to these metric keys (default: all).
        max_facts: cap on returned facts.
    """

    text = str(hit.get("text") or hit.get("text_preview") or "")
    if not text:
        return []
    numbers = extract_financial_numbers(text, limit=64)
    specs = (
        [METRIC_SPECS_BY_KEY[key] for key in metric_keys if key in METRIC_SPECS_BY_KEY]
        if metric_keys
        else list(METRIC_SPECS)
    )

    year = str(hit.get("year", ""))
    quarter = str(hit.get("quarter", ""))
    company = str(hit.get("company_name", ""))
    stock_code = str(hit.get("stock_code", ""))
    chunk_id = str(hit.get("chunk_id", ""))
    filename = str(hit.get("filename", ""))
    confidence = float(hit.get("score") or 0.0)

    facts: List[MetricFact] = []
    seen: set[Tuple[str, str, str, int]] = set()
    claimed_spans: List[Tuple[int, int]] = []  # number spans already used

    for spec in specs:
        for mention in spec.find_mentions(text):
            number = _bind_number(mention, numbers, spec.unit_class, text)
            if number is None:
                continue
            # Avoid double-counting one number under several broad metrics
            # (e.g. "net sales" matched by both revenue patterns).
            span = (number.start, number.end)
            if any(span == claimed for claimed in claimed_spans):
                # allow reuse only across genuinely different metric keys
                pass
            # Qualifier (GAAP / non-GAAP / adjusted) almost always precedes the
            # metric phrase, so look just before the mention to avoid bleeding
            # into a neighbouring sentence's qualifier.
            qualifier_window = text[max(0, mention.start() - 40): mention.end()]
            qualifier = detect_qualifier(qualifier_window) if spec.key in {"gross_margin", "eps"} else ""
            value_kind = classify_value_kind(number.unit, text[max(0, number.start - 28): number.start])
            if not is_plausible_value(spec.key, number.unit, number.value, value_kind):
                continue
            key = (spec.key, qualifier, number.raw, number.start)
            if key in seen:
                continue
            seen.add(key)
            claimed_spans.append(span)
            raw_id = "|".join(
                [chunk_id, spec.key, qualifier, number.raw, year, quarter, str(number.start)]
            )
            facts.append(
                MetricFact(
                    fact_id=f"mf_{stable_id(raw_id)}",
                    metric_key=spec.key,
                    metric_display=spec.display,
                    qualifier=qualifier,
                    value=number.value,
                    raw_value=number.raw,
                    unit=number.unit,
                    entity_name=company,
                    stock_code=stock_code,
                    period_year=year,
                    period_quarter=quarter,
                    period_type=infer_period_type(number.context, year, quarter),
                    source_chunk_id=chunk_id,
                    source_filename=filename,
                    metric_phrase=compact_text(mention.group(0)),
                    evidence_text=number.context,
                    confidence=confidence,
                    value_kind=value_kind,
                )
            )
            if len(facts) >= max_facts:
                return facts
    return facts


def detect_query_metrics(query: str) -> List[str]:
    """Return the metric keys a query is asking about, most specific first."""

    text = str(query or "")
    detected: List[str] = []
    for spec in METRIC_SPECS:
        if spec.find_mentions(text):
            detected.append(spec.key)
    # A query mentioning the specific cash/fixed-maturity metric also trivially
    # matches "cash"/"securities" inside revenue patterns; keep specific-first
    # order (already guaranteed by METRIC_SPECS ordering) and de-dupe.
    seen: set[str] = set()
    ordered: List[str] = []
    for key in detected:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered
