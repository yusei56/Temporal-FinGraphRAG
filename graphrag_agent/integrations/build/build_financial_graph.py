"""WP2: ingest ECT-QA financial temporal facts into Neo4j.

Loads the ECT-QA corpus, runs the deterministic named-metric extractors (WP1) on
every chunk, and writes a time-hierarchy graph:

    (:FinCompany)-[:REPORTED]->(:FinFact)-[:IN_PERIOD]->(:FinQuarter)-[:OF_YEAR]->(:FinYear)
    (:FinFact)-[:FROM_CHUNK]->(:FinChunk)-[:OF_COMPANY]->(:FinCompany)
    (:FinChunk)-[:IN_PERIOD]->(:FinQuarter)

All nodes are namespaced with a ``Fin`` prefix so this graph never collides with
the original GraphRAG ``:Chunk`` / ``:__Entity__`` data. Writes are idempotent
(MERGE on stable ids), so re-running updates in place.

Usage:
    PYTHONPATH=. .venv/bin/python graphrag_agent/integrations/build/build_financial_graph.py \
        --scenario new --corpus-scope full --wipe
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag_agent.config.neo4jdb import get_db_manager  # noqa: E402
from graphrag_agent.financial.ectqa_corpus import (  # noqa: E402
    EctChunk,
    EctQaDataManager,
    chunk_document,
)
from graphrag_agent.financial.metric_extractors import extract_metric_facts  # noqa: E402

FIN_LABELS = ["FinCompany", "FinFact", "FinChunk", "FinYear", "FinQuarter"]

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT fin_company_name IF NOT EXISTS FOR (c:FinCompany) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT fin_fact_id IF NOT EXISTS FOR (f:FinFact) REQUIRE f.fact_id IS UNIQUE",
    "CREATE CONSTRAINT fin_chunk_id IF NOT EXISTS FOR (c:FinChunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT fin_year IF NOT EXISTS FOR (y:FinYear) REQUIRE y.year IS UNIQUE",
    "CREATE CONSTRAINT fin_quarter IF NOT EXISTS FOR (q:FinQuarter) REQUIRE q.period IS UNIQUE",
    "CREATE INDEX fin_fact_metric IF NOT EXISTS FOR (f:FinFact) ON (f.metric_key)",
    "CREATE INDEX fin_fact_period IF NOT EXISTS FOR (f:FinFact) ON (f.period_year, f.period_quarter)",
    "CREATE INDEX fin_chunk_period IF NOT EXISTS FOR (c:FinChunk) ON (c.year, c.quarter)",
]

CHUNK_WRITE = """
UNWIND $chunks AS c
MERGE (co:FinCompany {name: c.company_name})
  ON CREATE SET co.stock_code = c.stock_code, co.sector = c.sector
MERGE (ch:FinChunk {chunk_id: c.chunk_id})
  SET ch.filename = c.filename, ch.split = c.split, ch.year = c.year,
      ch.quarter = c.quarter, ch.company_name = c.company_name, ch.text = c.text
MERGE (ch)-[:OF_COMPANY]->(co)
WITH c, ch
WHERE c.period <> ''
MERGE (q:FinQuarter {period: c.period})
  ON CREATE SET q.year = c.year, q.quarter = c.quarter
MERGE (ch)-[:IN_PERIOD]->(q)
WITH c, q
WHERE c.year <> ''
MERGE (y:FinYear {year: c.year})
MERGE (q)-[:OF_YEAR]->(y)
"""

FACT_WRITE = """
UNWIND $facts AS f
MATCH (co:FinCompany {name: f.entity_name})
MATCH (ch:FinChunk {chunk_id: f.source_chunk_id})
MERGE (mf:FinFact {fact_id: f.fact_id})
  SET mf.metric_key = f.metric_key, mf.metric_display = f.metric_display,
      mf.qualifier = f.qualifier, mf.value = f.value, mf.raw_value = f.raw_value,
      mf.unit = f.unit, mf.period_year = f.period_year, mf.period_quarter = f.period_quarter,
      mf.period_type = f.period_type, mf.evidence_text = f.evidence_text,
      mf.metric_phrase = f.metric_phrase, mf.confidence = f.confidence,
      mf.value_kind = f.value_kind
MERGE (co)-[:REPORTED]->(mf)
MERGE (mf)-[:FROM_CHUNK]->(ch)
WITH mf, f
WHERE f.period <> ''
MATCH (q:FinQuarter {period: f.period})
MERGE (mf)-[:IN_PERIOD]->(q)
"""


def period_of(year: str, quarter: str) -> str:
    year = (year or "").strip()
    quarter = (quarter or "").strip()
    if year and quarter:
        return f"{year}-{quarter}"
    return year or quarter


def chunk_row(chunk: EctChunk) -> Dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "filename": chunk.filename,
        "split": chunk.split,
        "company_name": chunk.company_name,
        "stock_code": chunk.stock_code,
        "sector": chunk.sector,
        "year": chunk.year,
        "quarter": chunk.quarter,
        "period": period_of(chunk.year, chunk.quarter),
        "text": chunk.text,
    }


def fact_rows(chunk: EctChunk, metric_keys: Optional[Sequence[str]]) -> List[Dict[str, Any]]:
    hit = {
        "text": chunk.text,
        "company_name": chunk.company_name,
        "stock_code": chunk.stock_code,
        "year": chunk.year,
        "quarter": chunk.quarter,
        "chunk_id": chunk.chunk_id,
        "filename": chunk.filename,
        "score": 0.0,
    }
    rows: List[Dict[str, Any]] = []
    for fact in extract_metric_facts(hit, metric_keys=metric_keys):
        row = fact.to_dict()
        row["period"] = period_of(fact.period_year, fact.period_quarter)
        rows.append(row)
    return rows


def ensure_schema(driver) -> None:
    for statement in SCHEMA_STATEMENTS:
        driver.execute_query(statement)


def wipe_financial_graph(driver) -> None:
    # The ECT-QA financial graph is small (a few thousand nodes), so a single
    # DETACH DELETE is fine and avoids CALL { ... } IN TRANSACTIONS (which needs
    # an auto-commit tx and Neo4j 5.23+ for the CALL (n) variable-scope form).
    driver.execute_query(
        "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) DETACH DELETE n",
        parameters_={"labels": FIN_LABELS},
    )


def write_batch(driver, chunks: List[Dict[str, Any]], facts: List[Dict[str, Any]]) -> None:
    if chunks:
        driver.execute_query(CHUNK_WRITE, parameters_={"chunks": chunks})
    if facts:
        driver.execute_query(FACT_WRITE, parameters_={"facts": facts})


def build(args: argparse.Namespace) -> int:
    manager = get_db_manager()
    driver = manager.get_driver()

    print(f"[schema] ensuring constraints + indexes ...")
    ensure_schema(driver)
    if args.wipe:
        print(f"[wipe] deleting existing Fin* nodes ...")
        wipe_financial_graph(driver)

    data = EctQaDataManager(args.data_dir, download=args.download)
    questions = data.load_questions(args.scenario)
    documents = data.load_corpus(args.scenario, questions, corpus_scope=args.corpus_scope)
    if args.limit_docs:
        documents = documents[: args.limit_docs]
    print(f"[corpus] scenario={args.scenario} scope={args.corpus_scope} docs={len(documents)}")

    metric_keys = (
        [k.strip() for k in args.metric_keys.split(",") if k.strip()]
        if args.metric_keys
        else None
    )

    chunk_buf: List[Dict[str, Any]] = []
    fact_buf: List[Dict[str, Any]] = []
    total_chunks = total_facts = 0

    def flush() -> None:
        nonlocal chunk_buf, fact_buf
        write_batch(driver, chunk_buf, fact_buf)
        chunk_buf, fact_buf = [], []

    for doc_index, document in enumerate(documents, start=1):
        chunks = chunk_document(document, max_chars=args.chunk_chars, overlap=args.chunk_overlap)
        for chunk in chunks:
            chunk_buf.append(chunk_row(chunk))
            rows = fact_rows(chunk, metric_keys)
            fact_buf.extend(rows)
            total_chunks += 1
            total_facts += len(rows)
        if len(chunk_buf) >= args.batch_size:
            flush()
        if doc_index % 50 == 0 or doc_index == len(documents):
            print(f"[ingest] docs {doc_index}/{len(documents)} chunks={total_chunks} facts={total_facts}")
    flush()

    print(f"[done] chunks={total_chunks} facts={total_facts}")
    summarize(driver)
    return 0


def summarize(driver) -> None:
    print("\n[summary] node counts:")
    for label in FIN_LABELS:
        records, _, _ = driver.execute_query(f"MATCH (n:{label}) RETURN count(n) AS c")
        print(f"  {label:12} {records[0]['c']}")
    print("[summary] facts per metric:")
    records, _, _ = driver.execute_query(
        "MATCH (f:FinFact) RETURN f.metric_key AS metric, count(*) AS c ORDER BY c DESC"
    )
    for rec in records:
        print(f"  {rec['metric']:26} {rec['c']}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest ECT-QA temporal facts into Neo4j (WP2).")
    parser.add_argument("--scenario", choices=["base", "updated", "new"], default="new")
    parser.add_argument("--corpus-scope", choices=["evidence", "full"], default="full")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "datasets" / "ect_qa")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--chunk-chars", type=int, default=1800)
    parser.add_argument("--chunk-overlap", type=int, default=250)
    parser.add_argument("--metric-keys", default="", help="comma-separated subset; default all")
    parser.add_argument("--limit-docs", type=int, default=0, help="cap documents (for smoke tests)")
    parser.add_argument("--batch-size", type=int, default=400, help="chunks per write batch")
    parser.add_argument("--wipe", action="store_true", help="delete existing Fin* nodes first")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    return build(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
