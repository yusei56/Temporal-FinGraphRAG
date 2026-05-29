"""WP3 / P2: value_kind-aware graph fact pack + cross-period comparison cards.

The retriever (temporal_graph_search) puts the right *chunks* in context, but the
limit=100 bootstrap showed the remaining answer-quality lever is *downstream* of
retrieval — in how facts are organised for the LLM. This module builds that
organisation directly from the typed, bounded, ``value_kind``-tagged ``FinFact``
nodes instead of re-extracting numbers from messy chunk text:

  * ``fact_table``            — level-preferred rows (bps/delta noise dropped for
                                point-in-time questions), deduped, period-sorted.
  * ``cross_period_comparison`` — for multi-period / trend / comparison queries,
                                the same metric pivoted across periods (the TG-RAG
                                "reason over the time series" value, not just
                                "filter to a period").

It only fires when a company *and* an in-domain metric are detected, so it never
pollutes out-of-domain questions (the 6 extracted metrics don't cover every ECT
question). Numbers come from the graph, so they inherit P1's sanity bounds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .metric_extractors import detect_qualifier, detect_query_metrics
from .temporal_facts import metadata_values
from .temporal_graph_search import _DELTA_INTENT

FACT_PACK_QUERY = """
MATCH (co:FinCompany)-[:REPORTED]->(f:FinFact)-[:FROM_CHUNK]->(ch:FinChunk)
WHERE co.name IN $companies AND f.metric_key IN $metrics
  AND ($periods = [] OR (f.period_year + '-' + f.period_quarter) IN $periods)
  AND ($years   = [] OR f.period_year IN $years)
RETURN co.name AS company, f.metric_key AS metric, f.metric_display AS metric_display,
       f.qualifier AS qualifier, f.value AS value, f.raw_value AS raw_value, f.unit AS unit,
       f.value_kind AS value_kind, f.period_year AS year, f.period_quarter AS quarter,
       f.period_type AS period_type, ch.chunk_id AS chunk_id
LIMIT 2000
"""

_QORDER = {"q1": 1, "q2": 2, "q3": 3, "q4": 4, "": 0}


def _period_key(year: str, quarter: str) -> tuple:
    try:
        y = int(year)
    except (TypeError, ValueError):
        y = 0
    return (y, _QORDER.get(str(quarter).lower(), 0))


def _period_str(year: str, quarter: str) -> str:
    if year and quarter:
        return f"{year}-{quarter}"
    return year or quarter


def build_graph_fact_pack(
    driver,
    query: str,
    metadata: Any,
    *,
    max_rows: int = 24,
    allowed_chunk_ids: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a value_kind-aware fact pack, or None when not applicable.

    If ``allowed_chunk_ids`` is given, the pack is restricted to facts whose
    source chunk is in that set (the evidence actually shown to the LLM), so it
    reorganises visible evidence rather than introducing un-cited numbers.
    """
    companies = [str(c) for c in metadata_values(metadata, "matched_companies")]
    metrics = detect_query_metrics(query)
    if not companies or not metrics:
        return None  # out-of-domain or unscoped: skip rather than add noise

    periods = [
        f"{y}-{q}".lower() for y, q in metadata_values(metadata, "requested_year_quarters")
    ]
    years = [str(y) for y in metadata_values(metadata, "requested_years")]
    wants_delta = bool(_DELTA_INTENT.search(query or ""))
    q_qualifier = detect_qualifier(query)

    records, _, _ = driver.execute_query(
        FACT_PACK_QUERY,
        parameters_={"companies": companies, "metrics": metrics, "periods": periods, "years": years},
    )
    rows = [dict(r) for r in records]
    if allowed_chunk_ids is not None:
        allowed = set(allowed_chunk_ids)
        rows = [r for r in rows if r.get("chunk_id") in allowed]
    if not rows:
        return None

    # Prefer levels for point-in-time questions; keep deltas only when asked.
    kept = [r for r in rows if (r.get("value_kind") != "delta") or wants_delta]
    if not kept:
        kept = rows

    # Dedup exact (company, period, metric, qualifier, raw_value); period-sort.
    seen = set()
    table: List[Dict[str, Any]] = []
    for r in sorted(kept, key=lambda r: (r["company"], _period_key(r["year"], r["quarter"]), r["metric"], r["qualifier"])):
        key = (r["company"], r["year"], r["quarter"], r["metric"], r["qualifier"], r["raw_value"])
        if key in seen:
            continue
        seen.add(key)
        table.append(
            {
                "company": r["company"],
                "period": _period_str(r["year"], r["quarter"]),
                "metric": r["metric"],
                "qualifier": r["qualifier"],
                "value": r["value"],
                "raw_value": r["raw_value"],
                "unit": r["unit"],
                "value_kind": r["value_kind"],
                "period_type": r["period_type"],
                "source_chunk": r["chunk_id"],
            }
        )
    table = table[:max_rows]

    pack: Dict[str, Any] = {
        "description": (
            "Authoritative numeric facts pulled from the temporal knowledge graph "
            "(typed, range-checked, value_kind-tagged). Prefer these over numbers "
            "re-read from chunk text. value_kind=level is a point value; delta is a change."
        ),
        "metrics_detected": metrics,
        "value_kind_preference": "delta" if wants_delta else "level",
        "qualifier_hint": q_qualifier or "unspecified",
        "fact_table": table,
    }

    # Cross-period series for multi-period / trend / comparison questions.
    multi_period = len({(r["year"], r["quarter"]) for r in kept}) > 1 or len({r["year"] for r in kept}) > 1
    if multi_period:
        series: Dict[tuple, Dict[str, Any]] = {}
        for r in kept:
            # A cross-period *series* is only meaningful for levels; pivoting a
            # bps delta across periods is meaningless.
            if r.get("value_kind") == "delta":
                continue
            k = (r["company"], r["metric"], r["qualifier"])
            s = series.setdefault(k, {"company": r["company"], "metric": r["metric"],
                                      "qualifier": r["qualifier"], "_pts": {}})
            p = _period_str(r["year"], r["quarter"])
            s["_pts"].setdefault(p, set()).add(r["raw_value"])
        comparison = []
        for s in series.values():
            pts = sorted(s["_pts"].items(), key=lambda kv: _period_key(*(kv[0].split("-") + [""])[:2]))
            if len(pts) < 2:
                continue
            comparison.append(
                {
                    "company": s["company"],
                    "metric": s["metric"],
                    "qualifier": s["qualifier"],
                    "series": [{"period": p, "values": sorted(v)} for p, v in pts],
                }
            )
        if comparison:
            pack["cross_period_comparison"] = comparison

    return pack
