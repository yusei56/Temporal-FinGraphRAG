"""TempoRAG-Fin package entrypoint.

The repository keeps the historical package name ``graphrag_agent`` for import
compatibility, but the active project surface is the financial temporal RAG
track under :mod:`graphrag_agent.financial`.
"""

__version__ = "0.1.0"

from graphrag_agent.financial import (
    EctChunk,
    EctDocument,
    EctQaDataManager,
    EvidenceTableRow,
    FinancialGraphRetriever,
    MetricFact,
    MetricSpec,
    build_evidence_table,
    build_graph_fact_pack,
    chunk_document,
    detect_query_metrics,
    extract_metric_facts,
    fetch_scoped_chunks,
    graph_retrieve,
    ppr_chunk_scores,
)

__all__ = [
    "__version__",
    "EctChunk",
    "EctDocument",
    "EctQaDataManager",
    "EvidenceTableRow",
    "FinancialGraphRetriever",
    "MetricFact",
    "MetricSpec",
    "build_evidence_table",
    "build_graph_fact_pack",
    "chunk_document",
    "detect_query_metrics",
    "extract_metric_facts",
    "fetch_scoped_chunks",
    "graph_retrieve",
    "ppr_chunk_scores",
]
