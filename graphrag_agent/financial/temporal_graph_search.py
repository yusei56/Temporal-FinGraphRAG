"""WP3: time-scoped TF-IDF + PPR-rerank retrieval over the Fin* temporal graph.

This is the financial counterpart to the entity-centric ``LocalSearch``. The
pipeline is:

    1. time + company filtering  (cheap Cypher WHERE over the Fin* graph; this
                                  alone removes cross-company / cross-period
                                  distractors that hurt global TF-IDF)
    2. PPR diffusion             (HippoRAG-style: seed metric/qualifier-matching
                                  FinFact nodes, spread relevance through the
                                  fact<->chunk graph so the chunk holding the
                                  asked metric rises)
    3. evidence rerank           (blend the in-scope TF-IDF signal for lexical
                                  coverage with the PPR signal for metric
                                  precision)

A pure-graph variant (candidates = only fact-bearing chunks) was measured first
and *hurt* recall, because chunks without one of the extracted metrics were
unreachable. Hence candidates are chunk-first (every in-scope chunk), and PPR is
a reranker rather than the candidate generator.

It returns chunk hits in the same shape as ``EctQaCorpus.search`` so it is a
drop-in retriever for the evaluation harness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import networkx as nx

import re

from .metric_extractors import detect_qualifier, detect_query_metrics
from .temporal_facts import metadata_values

# A query asking about a *change* ("how much did margin improve", "growth") —
# only then should delta facts be seeded as strongly as level facts.
_DELTA_INTENT = re.compile(
    r"\b(change|changed|increase[ds]?|decrease[ds]?|improve[ds]?|declin\w*|"
    r"grow\w*|grew|expansion|expand\w*|contract\w*|year[- ]?over[- ]?year|yoy|"
    r"how much .*(up|down)|basis points?)\b",
    re.IGNORECASE,
)

# Chunk-first: every in-scope chunk is a candidate; its facts ride along for PPR.
SCOPED_CHUNK_QUERY = """
MATCH (ch:FinChunk)-[:OF_COMPANY]->(co:FinCompany)
WHERE ($companies = [] OR co.name IN $companies)
  AND ($periods = [] OR (ch.year + '-' + ch.quarter) IN $periods)
  AND ($years = [] OR ch.year IN $years)
OPTIONAL MATCH (f:FinFact)-[:FROM_CHUNK]->(ch)
RETURN ch.chunk_id AS chunk_id, ch.text AS text, ch.filename AS filename, ch.split AS split,
       co.name AS company_name, co.stock_code AS stock_code, co.sector AS sector,
       ch.year AS year, ch.quarter AS quarter,
       collect({fact_id: f.fact_id, metric_key: f.metric_key,
                qualifier: f.qualifier, value: f.value, value_kind: f.value_kind}) AS facts
LIMIT $limit
"""


def _periods(metadata: Any) -> List[str]:
    return [
        f"{year}-{quarter}".lower()
        for year, quarter in metadata_values(metadata, "requested_year_quarters")
    ]


def _years(metadata: Any) -> List[str]:
    return [str(year) for year in metadata_values(metadata, "requested_years")]


def _companies(metadata: Any) -> List[str]:
    return [str(c) for c in metadata_values(metadata, "matched_companies")]


def _run(driver, companies, periods, years, limit) -> List[Dict[str, Any]]:
    records, _, _ = driver.execute_query(
        SCOPED_CHUNK_QUERY,
        parameters_={"companies": companies, "periods": periods, "years": years, "limit": limit},
    )
    rows: List[Dict[str, Any]] = []
    for rec in records:
        row = dict(rec)
        row["facts"] = [f for f in row.get("facts", []) if f and f.get("fact_id")]
        rows.append(row)
    return rows


def fetch_scoped_chunks(driver, metadata: Any, *, limit: int = 200) -> List[Dict[str, Any]]:
    """All in-scope chunks (+ their facts), with progressive filter relaxation.

    Tightest first (company + exact period), relaxing to company + year, then
    company-only, then period/year across companies. Returns [] when nothing
    scopes the query so the caller can fall back to global retrieval.
    """
    companies = _companies(metadata)
    periods = _periods(metadata)
    years = _years(metadata)

    attempts: List[Tuple[List[str], List[str], List[str]]] = []
    if companies and periods:
        attempts.append((companies, periods, []))
    if companies and years:
        attempts.append((companies, [], years))
    if companies:
        attempts.append((companies, [], []))
    if not companies and periods:
        attempts.append(([], periods, []))
    if not companies and years:
        attempts.append(([], [], years))

    for comp, per, yr in attempts:
        rows = _run(driver, comp, per, yr, limit)
        if rows:
            return rows
    return []


def ppr_chunk_scores(query: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """PPR over the fact<->chunk graph; returns chunk_id -> pagerank mass.

    Seeds personalization on FinFact nodes whose metric (and qualifier) match the
    query. Factless chunks are isolated nodes (teleport mass only) — that is fine
    because the caller blends this with the lexical signal.
    """
    detected = set(detect_query_metrics(query))
    q_qualifier = detect_qualifier(query)
    wants_delta = bool(_DELTA_INTENT.search(query or ""))

    graph = nx.Graph()
    chunk_ids: List[str] = []
    personalization: Dict[str, float] = {}
    any_metric_seed = False

    for row in rows:
        chunk_id = row["chunk_id"]
        chunk_ids.append(chunk_id)
        graph.add_node(chunk_id)
        for fact in row["facts"]:
            fact_id = fact["fact_id"]
            weight = 0.1
            if detected and fact.get("metric_key") in detected:
                # Prefer levels over deltas unless the query is about a change.
                is_delta = fact.get("value_kind") == "delta"
                weight += 3.0 if (not is_delta or wants_delta) else 1.5
                any_metric_seed = True
            if q_qualifier and fact.get("qualifier") == q_qualifier:
                weight += 1.0
            if fact.get("value") is not None:
                weight += 0.2
            graph.add_edge(fact_id, chunk_id, weight=1.0 + weight)
            personalization[fact_id] = max(personalization.get(fact_id, 0.0), weight)

    if graph.number_of_edges() == 0:
        return {chunk_id: 0.0 for chunk_id in chunk_ids}

    if not any_metric_seed:
        for fact_id in personalization:
            personalization[fact_id] = 1.0

    full_p = {node: 0.0 for node in graph.nodes}
    for fact_id, weight in personalization.items():
        if fact_id in full_p:
            full_p[fact_id] = weight
    if sum(full_p.values()) <= 0:
        full_p = {node: 1.0 for node in graph.nodes}

    ranks = nx.pagerank(graph, personalization=full_p, weight="weight")
    return {chunk_id: ranks.get(chunk_id, 0.0) for chunk_id in chunk_ids}


def _hits_from_scores(
    rows: Sequence[Dict[str, Any]], ordered: Sequence[Tuple[str, float]], top_k: int
) -> List[Dict[str, Any]]:
    meta = {row["chunk_id"]: row for row in rows}
    selected = ordered[:top_k]
    max_score = selected[0][1] if selected and selected[0][1] > 0 else 0.0
    hits: List[Dict[str, Any]] = []
    for rank, (chunk_id, score) in enumerate(selected, start=1):
        m = meta[chunk_id]
        hits.append(
            {
                "rank": rank,
                "score": (score / max_score) if max_score > 0 else 0.0,
                "raw_score": float(score),
                "adjusted_score": float(score),
                "chunk_id": chunk_id,
                "filename": m.get("filename", ""),
                "split": m.get("split", ""),
                "company_name": m.get("company_name", ""),
                "stock_code": m.get("stock_code", ""),
                "sector": m.get("sector", ""),
                "year": m.get("year", ""),
                "quarter": m.get("quarter", ""),
                "text": m.get("text", ""),
            }
        )
    return hits


def graph_retrieve(
    driver, query: str, metadata: Any, *, top_k: int = 8, limit: int = 200
) -> List[Dict[str, Any]]:
    """Pure-graph retrieval (PPR only); kept for inspection. Hybrid lives in the retriever."""
    rows = fetch_scoped_chunks(driver, metadata, limit=limit)
    if not rows:
        return []
    scores = ppr_chunk_scores(query, rows)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return _hits_from_scores(rows, ordered, top_k)


def _normalize(values: Dict[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    hi = max(values.values())
    if hi <= 0:
        return {k: 0.0 for k in values}
    return {k: v / hi for k, v in values.items()}


class FinancialGraphRetriever:
    """Drop-in retriever: time-scoped TF-IDF + PPR rerank over the Fin* graph.

    Wraps an ``EctQaCorpus`` for query analysis and the in-scope lexical signal,
    but restricts and reranks retrieval via the temporal graph. Falls back to the
    corpus's global TF-IDF search when the graph cannot scope the query.
    """

    def __init__(
        self, corpus: Any, driver, *, ppr_weight: float = 0.5, fallback: bool = True
    ) -> None:
        self._corpus = corpus
        self._driver = driver
        self._ppr_weight = ppr_weight
        self._fallback = fallback
        self._chunk_index: Optional[Dict[str, int]] = None

    def _chunk_id_to_index(self) -> Dict[str, int]:
        if self._chunk_index is None:
            self._chunk_index = {
                chunk.chunk_id: i for i, chunk in enumerate(self._corpus.chunks)
            }
        return self._chunk_index

    def _tfidf_scores(self, query: str, chunk_ids: Sequence[str]) -> Dict[str, float]:
        index = self._chunk_id_to_index()
        query_vector = self._corpus._vectorizer.transform([query])
        all_scores = (self._corpus._matrix @ query_vector.T).toarray().ravel()
        return {
            chunk_id: float(all_scores[index[chunk_id]])
            for chunk_id in chunk_ids
            if chunk_id in index
        }

    def search(
        self, query: str, *, top_k: int, metadata_filter: str = "off"
    ) -> List[Dict[str, Any]]:
        # Time-scoped TF-IDF + PPR rerank. A global TF-IDF safety net was tried to
        # recover the small doc_recall dip from mis-scoped queries, but it cost
        # ~0.06 evidence_text_recall (global lexical chunks displaced the
        # PPR-surfaced evidence chunks) for only +0.004 doc_recall, so it was
        # dropped: scoping precision is the win and is worth the dip.
        clean_query = query.strip()
        if not clean_query:
            return []
        metadata = self._corpus.analyze_query(clean_query)
        rows = fetch_scoped_chunks(self._driver, metadata)
        if not rows:
            if self._fallback:
                return self._corpus.search(
                    clean_query, top_k=top_k, metadata_filter=metadata_filter
                )
            return []

        chunk_ids = [row["chunk_id"] for row in rows]
        ppr = _normalize(ppr_chunk_scores(clean_query, rows))
        tfidf = _normalize(self._tfidf_scores(clean_query, chunk_ids))
        blended = {
            chunk_id: tfidf.get(chunk_id, 0.0) + self._ppr_weight * ppr.get(chunk_id, 0.0)
            for chunk_id in chunk_ids
        }
        ordered = sorted(blended.items(), key=lambda kv: kv[1], reverse=True)
        return _hits_from_scores(rows, ordered, top_k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._corpus, name)
