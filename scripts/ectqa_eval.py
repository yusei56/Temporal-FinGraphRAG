#!/usr/bin/env python3
"""First-pass ECT-QA evaluator for graph-rag-agent.

The script focuses on local/specific ECT-QA questions and reports:
- answer quality: EM, token F1, ROUGE-L, correct/refusal/incorrect buckets
- retrieval quality: evidence document recall@k, evidence text recall@k, all-support recall@k
- coarse temporal quality: gold year/quarter coverage in retrieved evidence

It can run a fast evidence-only smoke evaluation or a full-corpus evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import string
import sys
import tempfile
import time
import types
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLM_JUDGE_MODEL = "gpt-4.1-mini"
TEMPORAL_AGENT_NAME = "TemporalEvidenceAgent"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag_agent.financial.temporal_facts import (  # noqa: E402
    build_targeted_company_query as build_financial_targeted_company_query,
    build_temporal_evidence_cards as build_financial_temporal_evidence_cards,
    important_query_terms,
    normalize_metadata_text,
    temporal_query_intent as financial_temporal_query_intent,
)
from graphrag_agent.financial.evidence_table import build_evidence_table  # noqa: E402
from graphrag_agent.financial.fact_stitching import build_fact_sentence_package  # noqa: E402
from graphrag_agent.financial.pseudo_questions import generate_pseudo_questions  # noqa: E402
from graphrag_agent.financial.ectqa_corpus import (  # noqa: E402
    EctChunk,
    EctDocument,
    EctQaDataManager,
    chunk_document,
)
from graphrag_agent.financial.temporal_graph_search import FinancialGraphRetriever  # noqa: E402


def normalize_answer(text: str) -> str:
    """HotpotQA-style normalization for exact match and token F1."""

    def remove_articles(value: str) -> str:
        return re.sub(r"\b(a|an|the)\b", " ", value)

    def white_space_fix(value: str) -> str:
        return " ".join(value.split())

    def remove_punc(value: str) -> str:
        exclude = set(string.punctuation)
        return "".join(ch for ch in value if ch not in exclude)

    return white_space_fix(remove_articles(remove_punc((text or "").lower())))


def token_f1(prediction: str, ground_truth: str) -> Tuple[float, float, float]:
    pred_tokens = normalize_answer(prediction).split()
    gold_tokens = normalize_answer(ground_truth).split()
    if not pred_tokens or not gold_tokens:
        return (0.0, 0.0, 0.0)
    common = Counter(pred_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return (0.0, 0.0, 0.0)
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    f1 = (2 * precision * recall) / (precision + recall)
    return (f1, precision, recall)


def exact_match(prediction: str, ground_truth: str) -> float:
    return float(normalize_answer(prediction) == normalize_answer(ground_truth))


def rouge_l(prediction: str, ground_truth: str) -> float:
    pred = normalize_answer(prediction).split()
    gold = normalize_answer(ground_truth).split()
    if not pred or not gold:
        return 0.0
    dp = [0] * (len(gold) + 1)
    for token in pred:
        prev = 0
        for idx, gold_token in enumerate(gold, start=1):
            saved = dp[idx]
            if token == gold_token:
                dp[idx] = prev + 1
            else:
                dp[idx] = max(dp[idx], dp[idx - 1])
            prev = saved
    lcs = dp[-1]
    precision = lcs / len(pred)
    recall = lcs / len(gold)
    if precision + recall == 0:
        return 0.0
    return (2 * precision * recall) / (precision + recall)


REFUSAL_PATTERNS = [
    "unanswerable",
    "cannot answer",
    "can't answer",
    "cannot determine",
    "can't determine",
    "not enough information",
    "insufficient evidence",
    "insufficient information",
    "evidence to answer",
    "no relevant information",
    "unable to determine",
    "i do not have",
    "i don't have",
    "不知道",
    "无法回答",
    "不能回答",
    "无法确定",
    "不能确定",
    "没有足够",
    "没有相关信息",
    "未找到相关",
    "没有找到相关",
]

FOCUSED_JUDGE_SCORE_KEYS = [
    "evidence_faithfulness",
    "temporal_alignment",
]

FULL_JUDGE_SCORE_KEYS = [
    "answer_correctness",
    "evidence_faithfulness",
    "temporal_alignment",
    "numerical_reasoning",
    "answer_completeness",
    "citation_validity",
    "refusal_quality",
]

JUDGE_SCORE_DESCRIPTIONS = {
    "answer_correctness": "0-1: semantic correctness against the gold answer.",
    "evidence_faithfulness": "0-1: whether claims are supported by retrieved/gold evidence.",
    "temporal_alignment": "0-1: whether company, year, quarter, and time window are aligned.",
    "numerical_reasoning": "0-1: whether numbers, comparisons, maxima/minima, and trend reasoning are correct.",
    "answer_completeness": "0-1: whether the answer covers all required evidence and reasoning steps.",
    "citation_validity": "0-1: whether cited chunks actually support the answer's claims.",
    "refusal_quality": "0-1: whether a refusal is appropriate, explicit, and well justified.",
}


def judge_score_keys(profile: str) -> List[str]:
    return FULL_JUDGE_SCORE_KEYS if profile == "full" else FOCUSED_JUDGE_SCORE_KEYS


def is_refusal(answer: str) -> bool:
    normalized = normalize_answer(answer)
    return any(pattern in normalized for pattern in REFUSAL_PATTERNS)


def classify_answer(prediction: str, gold: str, f1: float) -> str:
    if normalize_answer(gold) == "unanswerable":
        return "correct_refusal" if is_refusal(prediction) else "incorrect"
    normalized_prediction = normalize_answer(prediction)
    normalized_gold = normalize_answer(gold)
    if normalized_gold and normalized_gold in normalized_prediction:
        return "correct"
    if exact_match(prediction, gold) >= 1.0 or f1 >= 0.5:
        return "correct"
    if is_refusal(prediction):
        return "wrong_refusal"
    return "incorrect"


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def clean_query_input(query_input: Any) -> str:
    if isinstance(query_input, dict):
        return str(
            query_input.get("query")
            or query_input.get("input")
            or query_input.get("text")
            or ""
        )
    return str(query_input or "")


def normalize_snippet(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_answer(text))


def quarter_number(quarter: str) -> int:
    match = re.search(r"[qQ]([1-4])", quarter or "")
    return int(match.group(1)) if match else 0


def year_quarter_key(year: str, quarter: str) -> Tuple[int, int]:
    try:
        year_value = int(year)
    except (TypeError, ValueError):
        year_value = 0
    return (year_value, quarter_number(quarter))


def year_quarter_range(
    start: Tuple[str, str],
    end: Tuple[str, str],
    available: Sequence[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    start_key = year_quarter_key(*start)
    end_key = year_quarter_key(*end)
    if start_key > end_key:
        start_key, end_key = end_key, start_key
    return [
        yq for yq in available
        if start_key <= year_quarter_key(*yq) <= end_key
    ]


@dataclass(frozen=True)
class QueryMetadata:
    matched_companies: Tuple[str, ...]
    candidate_companies: Tuple[str, ...]
    unknown_company: bool
    requested_years: Tuple[str, ...]
    requested_year_quarters: Tuple[Tuple[str, str], ...]
    out_of_range_time: bool



class EctQaCorpus:
    def __init__(self, documents: Sequence[EctDocument], *, max_chars: int, overlap: int) -> None:
        chunks: List[EctChunk] = []
        for document in documents:
            chunks.extend(chunk_document(document, max_chars=max_chars, overlap=overlap))
        if not chunks:
            raise ValueError("Unable to build ECT-QA corpus: no chunks generated.")
        self.chunks = chunks
        self.available_years = tuple(sorted({chunk.year for chunk in chunks}))
        self.available_year_quarters = tuple(
            sorted({(chunk.year, chunk.quarter) for chunk in chunks}, key=lambda yq: year_quarter_key(*yq))
        )
        self.min_year = min(int(year) for year in self.available_years if year.isdigit())
        self.max_year = max(int(year) for year in self.available_years if year.isdigit())
        self._company_aliases: List[Tuple[str, str]] = []
        seen_aliases: set[Tuple[str, str]] = set()
        for chunk in chunks:
            aliases = {
                normalize_metadata_text(chunk.company_name),
                normalize_metadata_text(chunk.company_name).removeprefix("the "),
            }
            if len(chunk.stock_code) > 2:
                aliases.add(normalize_metadata_text(chunk.stock_code))
            for alias in aliases:
                if not alias:
                    continue
                pair = (alias, chunk.company_name)
                if pair not in seen_aliases:
                    seen_aliases.add(pair)
                    self._company_aliases.append(pair)
        self._company_aliases.sort(key=lambda item: len(item[0]), reverse=True)
        self._vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=200_000)
        self._matrix = self._vectorizer.fit_transform(chunk.text for chunk in chunks)

    def analyze_query(self, query: str) -> QueryMetadata:
        clean_query = query.replace(chr(0x2019), chr(39))
        normalized_query = f" {normalize_metadata_text(clean_query)} "

        matched_companies = []
        for alias, company_name in self._company_aliases:
            if f" {alias} " in normalized_query and company_name not in matched_companies:
                matched_companies.append(company_name)

        suffixes = (
            "Corporation|Corp\\.?|Incorporated|Inc\\.?|Holdings|Company|Co\\.?|"
            "Ltd\\.?|PLC|S\\.A\\.?|U\\.S\\.A\\.?"
        )
        candidate_companies = []
        company_pattern = re.compile(
            rf"\b([A-Z][A-Za-z&.'-]*(?:,?\s+[A-Z][A-Za-z&.'-]*){{0,5}},?\s+(?:{suffixes}))",
        )
        for match in company_pattern.finditer(clean_query):
            candidate = match.group(1).strip(" ,.")
            if candidate not in candidate_companies:
                candidate_companies.append(candidate)

        unknown_company = False
        if candidate_companies:
            known_candidate = False
            for candidate in candidate_companies:
                normalized_candidate = normalize_metadata_text(candidate)
                for alias, _company_name in self._company_aliases:
                    if normalized_candidate == alias or normalized_candidate in alias or alias in normalized_candidate:
                        known_candidate = True
                        break
                if known_candidate:
                    break
            unknown_company = not known_candidate and not matched_companies

        years = {match.group(0) for match in re.finditer(r"\b20\d{2}\b", clean_query)}
        requested_yq: set[Tuple[str, str]] = set()

        for match in re.finditer(r"\b(20\d{2})\s*[- ]?\s*[qQ]([1-4])\b", clean_query):
            requested_yq.add((match.group(1), f"q{match.group(2)}"))
        for match in re.finditer(r"\b[qQ]([1-4])\s*[- ]?\s*(20\d{2})\b", clean_query):
            requested_yq.add((match.group(2), f"q{match.group(1)}"))

        ordered_pairs = sorted(requested_yq, key=lambda yq: year_quarter_key(*yq))
        lower_query = clean_query.lower()
        if ("between" in lower_query or "from" in lower_query) and len(ordered_pairs) >= 2:
            for yq in year_quarter_range(ordered_pairs[0], ordered_pairs[-1], self.available_year_quarters):
                requested_yq.add(yq)

        out_of_range_time = False
        for year in years:
            year_int = int(year)
            if year_int < self.min_year or year_int > self.max_year:
                out_of_range_time = True

        after_pair_patterns = [
            r"after\s+(20\d{2})\s*[- ]?\s*[qQ]([1-4])",
            r"after\s+[qQ]([1-4])\s*[- ]?\s*(20\d{2})",
        ]
        for pattern in after_pair_patterns:
            for match in re.finditer(pattern, lower_query):
                if match.group(1).startswith("20"):
                    ref = (match.group(1), f"q{match.group(2)}")
                else:
                    ref = (match.group(2), f"q{match.group(1)}")
                requested_yq.discard(ref)
                later = [
                    yq for yq in self.available_year_quarters
                    if year_quarter_key(*yq) > year_quarter_key(*ref)
                ]
                requested_yq.update(later)
                if not later:
                    out_of_range_time = True

        for match in re.finditer(r"after\s+(20\d{2})(?!\s*[- ]?\s*[qQ])", lower_query):
            ref_year = int(match.group(1))
            years.discard(str(ref_year))
            later_years = [str(year) for year in range(ref_year + 1, self.max_year + 1)]
            years.update(later_years)
            if not later_years:
                out_of_range_time = True

        before_pair_patterns = [
            r"before\s+(20\d{2})\s*[- ]?\s*[qQ]([1-4])",
            r"before\s+[qQ]([1-4])\s*[- ]?\s*(20\d{2})",
        ]
        for pattern in before_pair_patterns:
            for match in re.finditer(pattern, lower_query):
                if match.group(1).startswith("20"):
                    ref = (match.group(1), f"q{match.group(2)}")
                else:
                    ref = (match.group(2), f"q{match.group(1)}")
                requested_yq.discard(ref)
                earlier = [
                    yq for yq in self.available_year_quarters
                    if year_quarter_key(*yq) < year_quarter_key(*ref)
                ]
                requested_yq.update(earlier)
                if not earlier:
                    out_of_range_time = True

        for match in re.finditer(r"before\s+(20\d{2})(?!\s*[- ]?\s*[qQ])", lower_query):
            ref_year = int(match.group(1))
            years.discard(str(ref_year))
            earlier_years = [str(year) for year in range(self.min_year, ref_year)]
            years.update(earlier_years)
            if not earlier_years:
                out_of_range_time = True

        for year, _quarter in requested_yq:
            year_int = int(year)
            if year_int < self.min_year or year_int > self.max_year:
                out_of_range_time = True

        return QueryMetadata(
            matched_companies=tuple(matched_companies),
            candidate_companies=tuple(candidate_companies),
            unknown_company=unknown_company,
            requested_years=tuple(sorted(years)),
            requested_year_quarters=tuple(sorted(requested_yq, key=lambda yq: year_quarter_key(*yq))),
            out_of_range_time=out_of_range_time,
        )

    def _metadata_multiplier(self, chunk: EctChunk, metadata: QueryMetadata, mode: str) -> float:
        if mode == "off":
            return 1.0

        multiplier = 1.0
        if metadata.matched_companies:
            if chunk.company_name in metadata.matched_companies:
                multiplier *= 3.0
            elif mode == "strict":
                return 0.0
            else:
                multiplier *= 0.25

        requested_year_quarters = set(metadata.requested_year_quarters)
        requested_years = set(metadata.requested_years)
        if requested_year_quarters:
            if (chunk.year, chunk.quarter) in requested_year_quarters:
                multiplier *= 2.5
            elif mode == "strict":
                return 0.0
            else:
                multiplier *= 0.45
        elif requested_years:
            if chunk.year in requested_years:
                multiplier *= 1.7
            elif mode == "strict":
                return 0.0
            else:
                multiplier *= 0.65

        return multiplier

    def refusal_reason(
        self,
        query: str,
        hits: Sequence[Mapping[str, Any]],
        *,
        min_raw_score: float,
    ) -> str:
        metadata = self.analyze_query(query)
        if metadata.out_of_range_time:
            return "requested_time_out_of_corpus"
        if metadata.unknown_company:
            return "company_not_in_corpus"
        if not hits:
            return "no_retrieval_hits"
        if metadata.matched_companies:
            company_hits = [
                hit for hit in hits[:5]
                if hit.get("company_name") in metadata.matched_companies
            ]
            if not company_hits:
                return "no_retrieved_company_match"
        requested_yq = set(metadata.requested_year_quarters)
        requested_years = set(metadata.requested_years)
        if requested_yq:
            time_hits = [
                hit for hit in hits[:8]
                if (str(hit.get("year")), str(hit.get("quarter"))) in requested_yq
            ]
            if not time_hits:
                return "no_retrieved_time_match"
        elif requested_years:
            year_hits = [hit for hit in hits[:8] if str(hit.get("year")) in requested_years]
            if not year_hits:
                return "no_retrieved_year_match"
        max_raw = max(float(hit.get("raw_score", 0.0)) for hit in hits) if hits else 0.0
        if max_raw < min_raw_score:
            return "low_retrieval_score"
        return ""

    def search(self, query: str, *, top_k: int, metadata_filter: str = "off") -> List[Dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        query_vector = self._vectorizer.transform([clean_query])
        scores = (self._matrix @ query_vector.T).toarray().ravel()
        if not np.any(scores):
            return []
        metadata = self.analyze_query(clean_query)
        adjusted_scores = scores.copy()
        if metadata_filter != "off":
            multipliers = np.array(
                [self._metadata_multiplier(chunk, metadata, metadata_filter) for chunk in self.chunks]
            )
            adjusted_scores = adjusted_scores * multipliers
            if not np.any(adjusted_scores):
                adjusted_scores = scores.copy()
        top_indices = np.argsort(adjusted_scores)[::-1][:top_k]
        max_score = float(adjusted_scores[top_indices[0]]) if top_indices.size else 0.0
        hits: List[Dict[str, Any]] = []
        for rank, idx in enumerate(top_indices, start=1):
            raw_score = float(scores[idx])
            score = float(adjusted_scores[idx])
            if score <= 0:
                continue
            chunk = self.chunks[int(idx)]
            hits.append(
                {
                    "rank": rank,
                    "score": score / max_score if max_score > 0 else 0.0,
                    "raw_score": raw_score,
                    "adjusted_score": score,
                    "chunk_id": chunk.chunk_id,
                    "filename": chunk.filename,
                    "split": chunk.split,
                    "company_name": chunk.company_name,
                    "stock_code": chunk.stock_code,
                    "sector": chunk.sector,
                    "year": chunk.year,
                    "quarter": chunk.quarter,
                    "text": chunk.text,
                }
            )
        return hits


class RetrievalRecorder:
    def __init__(self) -> None:
        self._events: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    def record(self, agent: str, question_id: str, tool_name: str, hits: List[Dict[str, Any]]) -> None:
        key = (agent, question_id)
        event = {
            "tool_name": tool_name,
            "hits": [
                {
                    "rank": hit["rank"],
                    "score": round(float(hit["score"]), 4),
                    "raw_score": round(float(hit.get("raw_score", 0.0)), 6),
                    "adjusted_score": round(float(hit.get("adjusted_score", 0.0)), 6),
                    "chunk_id": hit["chunk_id"],
                    "filename": hit["filename"],
                    "company_name": hit["company_name"],
                    "year": hit["year"],
                    "quarter": hit["quarter"],
                    "text": hit["text"],
                    "text_preview": hit["text"][:300],
                }
                for hit in hits
            ],
        }
        self._events.setdefault(key, []).append(event)

    def hits_for(self, agent: str, question_id: str) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        merged: List[Dict[str, Any]] = []
        for event in self._events.get((agent, question_id), []):
            for hit in event["hits"]:
                chunk_id = hit["chunk_id"]
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                merged.append(hit)
        return merged

    def events_for(self, agent: str, question_id: str) -> List[Dict[str, Any]]:
        return self._events.get((agent, question_id), [])


class EctQaSearchTool:
    def __init__(
        self,
        corpus: EctQaCorpus,
        recorder: RetrievalRecorder,
        *,
        agent_name: str,
        question_id_getter,
        top_k: int,
        metadata_filter: str,
        tool_name: str = "ectqa_retriever",
    ) -> None:
        self.corpus = corpus
        self.recorder = recorder
        self.agent_name = agent_name
        self.question_id_getter = question_id_getter
        self.top_k = top_k
        self.metadata_filter = metadata_filter
        self.tool_name = tool_name

    def extract_keywords(self, query: str) -> Dict[str, List[str]]:
        words = re.findall(r"\b[A-Za-z][A-Za-z0-9&.-]{2,}\b", query)
        return {"low_level": words[:8], "high_level": words[:4]}

    def structured_search(self, query_input: Any) -> Dict[str, Any]:
        """Return structured ECT-QA retrieval results for a query."""
        query = clean_query_input(query_input)
        hits = self.corpus.search(query, top_k=self.top_k, metadata_filter=self.metadata_filter)
        question_id = self.question_id_getter()
        if question_id:
            self.recorder.record(self.agent_name, question_id, self.tool_name, hits)
        retrieval_results = [
            {
                "result_id": hit["chunk_id"],
                "granularity": "Chunk",
                "evidence": hit["text"],
                "metadata": {
                    "source_id": hit["chunk_id"],
                    "source_type": "chunk",
                    "confidence": round(float(hit["score"]), 4),
                    "extra": {
                        "filename": hit["filename"],
                        "year": hit["year"],
                        "quarter": hit["quarter"],
                        "company_name": hit["company_name"],
                        "stock_code": hit["stock_code"],
                    },
                },
                "source": self.tool_name,
                "score": round(float(hit["score"]), 4),
                "created_at": datetime.utcnow().isoformat(),
            }
            for hit in hits
        ]
        return {
            "query": query,
            "answer": self._format_hits(hits),
            "final_answer": self._format_hits(hits),
            "retrieval_results": retrieval_results,
            "raw_context": hits,
        }

    def search(self, query_input: Any) -> str:
        """Search ECT-QA transcript chunks and return formatted evidence context."""
        return self.structured_search(query_input)["answer"]

    def _format_hits(self, hits: Sequence[Mapping[str, Any]]) -> str:
        if not hits:
            return "No relevant ECT-QA evidence found.\n\n{'data': {'Chunks':[] } }"
        blocks = []
        chunk_ids = []
        for hit in hits:
            chunk_ids.append(hit["chunk_id"])
            blocks.append(
                "\n".join(
                    [
                        f"Chunk ID: {hit['chunk_id']}",
                        f"Source: {hit['filename']} ({hit['company_name']}, {hit['year']} {hit['quarter']})",
                        f"Score: {float(hit['score']):.4f}",
                        str(hit["text"]),
                    ]
                )
            )
        references = ", ".join(f"'{chunk_id}'" for chunk_id in chunk_ids)
        return "\n\n---\n\n".join(blocks) + f"\n\n{{'data': {{'Chunks':[{references}] }} }}"

    def get_tool(self) -> BaseTool:
        outer = self

        class EctQaRetrievalTool(BaseTool):
            name: str = outer.tool_name
            description: str = "Search ECT-QA earnings call transcript chunks for financial temporal QA evidence."

            def _run(self_tool, query: Any = "", **kwargs: Any) -> str:
                payload = query if query else kwargs
                if kwargs and not isinstance(payload, dict):
                    payload = {"query": payload, **kwargs}
                return outer.search(payload)

            def _arun(self_tool, query: Any = "", **kwargs: Any):
                raise NotImplementedError("Async execution is not implemented.")

        return EctQaRetrievalTool()

    def get_global_tool(self) -> BaseTool:
        previous_name = self.tool_name
        self.tool_name = "global_retriever"
        try:
            return self.get_tool()
        finally:
            self.tool_name = previous_name

    def close(self) -> None:
        return None


def build_temporal_synthesis_prompt(
    *,
    query: str,
    metadata: QueryMetadata,
    evidence_cards: Sequence[Mapping[str, Any]],
    use_fact_sentences: bool = True,
    graph_fact_pack: Optional[Mapping[str, Any]] = None,
) -> str:
    query_terms = important_query_terms(query)
    compact_cards = [
        compact_evidence_card_for_prompt(card, query_terms)
        for card in evidence_cards
    ]
    evidence_table = build_evidence_table(query, evidence_cards, max_rows=24)
    fact_sentences = (
        build_fact_sentence_package(
            query=query,
            evidence_cards=evidence_cards,
            metadata=metadata,
            evidence_table=evidence_table,
            max_sentences=16,
            excerpt_chars=260,
        )
        if use_fact_sentences
        else {"enabled": False, "sentences": []}
    )
    payload = {
        "question": query,
        "query_metadata": {
            "matched_companies": list(metadata.matched_companies),
            "candidate_companies": list(metadata.candidate_companies),
            "requested_years": list(metadata.requested_years),
            "requested_year_quarters": list(metadata.requested_year_quarters),
            "out_of_range_time": metadata.out_of_range_time,
        },
        "query_intent": financial_temporal_query_intent(query),
        "fact_sentences": fact_sentences,
        "evidence_table": evidence_table,
        "evidence_cards": compact_cards,
    }
    if graph_fact_pack:
        payload["graph_fact_pack"] = graph_fact_pack
    citation_ids = [card.get("chunk_id", "") for card in evidence_cards if card.get("chunk_id")]
    graph_pack_instruction = (
        "1b. graph_fact_pack holds authoritative, range-checked numbers from the knowledge graph. "
        "Prefer its fact_table values over numbers re-read from chunk text; honour value_kind "
        "(level=point value, delta=change) per graph_fact_pack.value_kind_preference; for trend/"
        "comparison questions reason over graph_fact_pack.cross_period_comparison.\n"
        if graph_fact_pack
        else ""
    )
    fact_sentence_instruction = (
        "2. Use fact_sentences as the primary reasoning paths. Each fact path is stitched to a source chunk excerpt."
        if use_fact_sentences
        else "2. Use evidence_table as the primary reasoning workspace; fact_sentences are disabled for this ablation."
    )
    return f"""
You are a financial temporal RAG answer synthesizer.

Use only the evidence_cards below. They have already been pruned by company, time, query-term overlap, and retrieval score.
This follows a graph/temporal-RAG style workflow: activate relevant company/time evidence, prune weak paths, then reason over the remaining evidence.

Instructions:
1. Identify the target company, year, quarter, metric, and comparison requirement from the question.
{graph_pack_instruction}{fact_sentence_instruction}
3. Use evidence_table for numeric comparisons. Treat it as the calculation workspace.
4. Respect evidence_table.period_guidance: do not mix quarter, year-to-date, guidance, and full-year values unless the question asks for that scope.
5. If the question asks for a comparison, maximum, minimum, trend, or change, compare the numeric evidence explicitly before answering.
6. If the evidence cards do not contain enough support, refuse with "Insufficient evidence to answer reliably." Do not guess from outside knowledge.
7. Keep the final answer concise, but include the key supporting values and time periods when available.
8. Cite supporting chunk IDs from this allowed set only: {citation_ids}.
9. If the answer is a quarter, include the canonical form like "2023-q4" in the first sentence.
10. End with a final citation line exactly like: {{'data': {{'Chunks':['ect_xxx','ect_yyy'] }} }}

Evidence package:
{json.dumps(payload, ensure_ascii=False)}
""".strip()


def compact_evidence_card_for_prompt(
    card: Mapping[str, Any],
    query_terms: set[str],
) -> Dict[str, Any]:
    facts = list(card.get("facts") or [])

    def fact_score(fact: Mapping[str, Any]) -> Tuple[int, int]:
        text = " ".join(
            [
                str(fact.get("metric_text", "")),
                str(fact.get("evidence_text", "")),
                str(fact.get("raw_value", "")),
            ]
        )
        normalized = set(normalize_metadata_text(text).split())
        overlap = len(query_terms & normalized)
        unit = str(fact.get("unit", ""))
        value_bonus = 1 if unit in {"billion", "million", "thousand", "currency", "percent"} else 0
        period_bonus = 1 if "year" in str(fact.get("period_type", "")) else 0
        return (overlap * 4 + value_bonus + period_bonus, len(str(fact.get("evidence_text", ""))))

    relevant_facts = [
        fact for fact in facts
        if fact_score(fact)[0] > 0
    ]
    if not relevant_facts:
        relevant_facts = facts[:3]
    ranked_facts = sorted(relevant_facts, key=fact_score, reverse=True)[:5]
    key_facts = [
        {
            "metric_text": fact.get("metric_text", ""),
            "raw_value": fact.get("raw_value", ""),
            "value": fact.get("value"),
            "unit": fact.get("unit", ""),
            "period_type": fact.get("period_type", ""),
            "evidence_text": truncate_for_judge(fact.get("evidence_text", ""), 260),
        }
        for fact in ranked_facts
    ]
    return {
        "card_id": card.get("card_id", ""),
        "chunk_id": card.get("chunk_id", ""),
        "filename": card.get("filename", ""),
        "rank": card.get("rank"),
        "retrieval_score": card.get("retrieval_score"),
        "company_name": card.get("company_name", ""),
        "stock_code": card.get("stock_code", ""),
        "year": card.get("year", ""),
        "quarter": card.get("quarter", ""),
        "company_match": card.get("company_match"),
        "time_match": card.get("time_match"),
        "query_term_overlap": card.get("query_term_overlap", []),
        "key_facts": key_facts,
        "excerpt": card.get("excerpt", ""),
    }


class TemporalEvidenceAgent:
    """Experiment agent for temporal financial evidence synthesis on ECT-QA."""

    def __init__(
        self,
        *,
        corpus: EctQaCorpus,
        recorder: RetrievalRecorder,
        question_id_getter,
        top_k: int,
        metadata_filter: str,
        refusal_guard: bool,
        refusal_min_raw_score: float,
        evidence_cards: int,
        evidence_chars: int,
        pseudo_questions: int,
        use_fact_sentences: bool,
        use_graph_fact_pack: bool = False,
    ) -> None:
        from graphrag_agent.models.get_models import get_llm_model

        self.corpus = corpus
        self.recorder = recorder
        self.question_id_getter = question_id_getter
        self.top_k = top_k
        self.metadata_filter = metadata_filter
        self.refusal_guard = refusal_guard
        self.refusal_min_raw_score = refusal_min_raw_score
        self.evidence_cards = evidence_cards
        self.evidence_chars = evidence_chars
        self.pseudo_questions = pseudo_questions
        self.use_fact_sentences = use_fact_sentences
        # P2: graph-native value_kind-aware fact pack. Needs a real Neo4j driver,
        # which only the graph retriever exposes (FinancialGraphRetriever._driver).
        self.use_graph_fact_pack = use_graph_fact_pack
        self.graph_driver = getattr(corpus, "_driver", None)
        self.llm = get_llm_model()

    def _dedupe_hits(self, hit_groups: Sequence[Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for hits in hit_groups:
            for hit in hits:
                chunk_id = str(hit.get("chunk_id", ""))
                if not chunk_id or chunk_id in seen:
                    continue
                seen.add(chunk_id)
                merged.append(dict(hit))
        return merged

    def _coverage_order_hits(
        self,
        hits: Sequence[Mapping[str, Any]],
        metadata: QueryMetadata,
    ) -> List[Dict[str, Any]]:
        if len(metadata.matched_companies) > 1:
            buckets = {company: [] for company in metadata.matched_companies}
            other_hits: List[Mapping[str, Any]] = []
            for hit in hits:
                company = str(hit.get("company_name", ""))
                if company in buckets:
                    buckets[company].append(hit)
                else:
                    other_hits.append(hit)
            return self._round_robin_buckets(buckets, other_hits)

        if len(metadata.requested_year_quarters) > 1:
            yq_buckets = {yq: [] for yq in metadata.requested_year_quarters}
            other_hits = []
            for hit in hits:
                key = (str(hit.get("year", "")), str(hit.get("quarter", "")))
                if key in yq_buckets:
                    yq_buckets[key].append(hit)
                else:
                    other_hits.append(hit)
            return self._round_robin_buckets(yq_buckets, other_hits)

        if len(metadata.requested_years) > 1:
            year_buckets = {year: [] for year in metadata.requested_years}
            other_hits = []
            for hit in hits:
                year = str(hit.get("year", ""))
                if year in year_buckets:
                    year_buckets[year].append(hit)
                else:
                    other_hits.append(hit)
            return self._round_robin_buckets(year_buckets, other_hits)

        return [dict(hit, rank=index) for index, hit in enumerate(hits, start=1)]

    def _round_robin_buckets(
        self,
        buckets: Mapping[Any, Sequence[Mapping[str, Any]]],
        other_hits: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        ordered: List[Mapping[str, Any]] = []
        max_bucket_size = max((len(bucket) for bucket in buckets.values()), default=0)
        for depth in range(max_bucket_size):
            for bucket_key in buckets:
                bucket = buckets.get(bucket_key, [])
                if depth < len(bucket):
                    ordered.append(bucket[depth])
        ordered.extend(other_hits)
        return [dict(hit, rank=index) for index, hit in enumerate(ordered, start=1)]

    def _retrieve_hits(self, query: str, metadata: QueryMetadata) -> List[Dict[str, Any]]:
        hit_groups: List[List[Dict[str, Any]]] = []
        if len(metadata.matched_companies) > 1:
            per_company_k = max(4, min(self.top_k, 6))
            for company in metadata.matched_companies:
                targeted_query = build_financial_targeted_company_query(query, company, metadata)
                hit_groups.append(
                    self.corpus.search(
                        targeted_query,
                        top_k=per_company_k,
                        metadata_filter="strict",
                    )
                )

        if metadata.matched_companies and metadata.requested_year_quarters:
            for company in metadata.matched_companies:
                for year, quarter in metadata.requested_year_quarters:
                    targeted_query = build_financial_targeted_company_query(
                        query,
                        company,
                        metadata,
                        year=year,
                        quarter=quarter,
                    )
                    hit_groups.append(
                        self.corpus.search(
                            targeted_query,
                            top_k=4,
                            metadata_filter="strict",
                        )
                    )

        if metadata.matched_companies and metadata.requested_years and not metadata.requested_year_quarters:
            for company in metadata.matched_companies:
                for year in metadata.requested_years:
                    targeted_query = build_financial_targeted_company_query(
                        query,
                        company,
                        metadata,
                        year=year,
                        quarter="q4",
                    )
                    hit_groups.append(
                        self.corpus.search(
                            targeted_query,
                            top_k=3,
                            metadata_filter="strict",
                        )
                    )

        if self.pseudo_questions > 0:
            for pseudo in generate_pseudo_questions(
                query,
                metadata,
                max_questions=self.pseudo_questions,
            ):
                hit_groups.append(
                    self.corpus.search(
                        pseudo["question"],
                        top_k=max(2, min(self.top_k, 4)),
                        metadata_filter="boost",
                    )
                )

        hit_groups.append(
            self.corpus.search(query, top_k=self.top_k, metadata_filter=self.metadata_filter)
        )
        return self._coverage_order_hits(self._dedupe_hits(hit_groups), metadata)

    def ask(self, query: str, thread_id: str = "default", recursion_limit: int = 10) -> str:
        del thread_id, recursion_limit
        metadata = self.corpus.analyze_query(query)
        hits = self._retrieve_hits(query, metadata)
        question_id = self.question_id_getter()
        if question_id:
            self.recorder.record(TEMPORAL_AGENT_NAME, question_id, "temporal_evidence_retriever", hits)

        refusal_reason = self.corpus.refusal_reason(
            query,
            hits,
            min_raw_score=self.refusal_min_raw_score,
        )
        if self.refusal_guard and refusal_reason:
            return (
                "Insufficient evidence to answer reliably. "
                f"Refusal reason: {refusal_reason}."
            )
        if not hits:
            return "Insufficient evidence to answer reliably. Refusal reason: no_retrieval_hits."

        evidence_cards = build_financial_temporal_evidence_cards(
            query,
            hits,
            metadata,
            max_cards=self.evidence_cards,
            excerpt_chars=self.evidence_chars,
        )
        if not evidence_cards:
            return "Insufficient evidence to answer reliably. Refusal reason: no_temporal_evidence_cards."

        graph_fact_pack = None
        if self.use_graph_fact_pack and self.graph_driver is not None:
            from graphrag_agent.financial.temporal_graph_facts import build_graph_fact_pack

            allowed_chunk_ids = [
                c.get("chunk_id", "") for c in evidence_cards if c.get("chunk_id")
            ]
            graph_fact_pack = build_graph_fact_pack(
                self.graph_driver, query, metadata, allowed_chunk_ids=allowed_chunk_ids
            )

        prompt = build_temporal_synthesis_prompt(
            query=query,
            metadata=metadata,
            evidence_cards=evidence_cards,
            use_fact_sentences=self.use_fact_sentences,
            graph_fact_pack=graph_fact_pack,
        )
        response = self.llm.invoke(prompt)
        return response.content if hasattr(response, "content") else str(response)

    def close(self) -> None:
        return None


def install_dummy_neo4j() -> None:
    """Avoid import-time Neo4j connections while using injected ECT-QA tools."""

    class _DummyGraph:
        def refresh_schema(self) -> None:
            return None

        def query(self, _query: str, _params=None):
            return []

    class _DummySession:
        def close(self) -> None:
            return None

    class _DummyDriver:
        def execute_query(self, *args, **kwargs):
            return []

        def session(self):
            return _DummySession()

        def close(self) -> None:
            return None

    class _DummyDBManager:
        def __init__(self):
            self.driver = _DummyDriver()
            self.graph = _DummyGraph()

        def get_driver(self):
            return self.driver

        def get_graph(self):
            return self.graph

        def execute_query(self, _cypher: str, params=None):
            return []

        def get_session(self):
            return _DummySession()

        def release_session(self, _session) -> None:
            return None

        def close(self) -> None:
            return None

    stub = types.ModuleType("graphrag_agent.config.neo4jdb")
    stub.DBConnectionManager = _DummyDBManager
    stub.db_manager = _DummyDBManager()
    stub.get_db_manager = lambda: stub.db_manager
    sys.modules["graphrag_agent.config.neo4jdb"] = stub


@contextmanager
def isolated_working_directory(enabled: bool):
    if not enabled:
        yield None
        return
    original_cwd = Path.cwd()
    tmpdir = Path(tempfile.mkdtemp(prefix="ectqa-eval-cold-cache-"))
    try:
        os.chdir(tmpdir)
        yield tmpdir
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


def patch_agent_tools(
    corpus: EctQaCorpus,
    recorder: RetrievalRecorder,
    *,
    agent_name: str,
    question_id_getter,
    top_k: int,
    metadata_filter: str,
):
    import graphrag_agent.agents.naive_rag_agent as naive_mod
    import graphrag_agent.agents.graph_agent as graph_mod
    import graphrag_agent.agents.hybrid_agent as hybrid_mod

    def make_tool(tool_name: str = "ectqa_retriever"):
        return EctQaSearchTool(
            corpus,
            recorder,
            agent_name=agent_name,
            question_id_getter=question_id_getter,
            top_k=top_k,
            metadata_filter=metadata_filter,
            tool_name=tool_name,
        )

    naive_mod.NaiveSearchTool = lambda: make_tool("naive_retriever")
    graph_mod.LocalSearchTool = lambda: make_tool("lc_search_tool")
    graph_mod.GlobalSearchTool = lambda: make_tool("global_retriever")
    hybrid_mod.HybridSearchTool = lambda: make_tool("hybrid_retriever")


def make_agent(
    agent_name: str,
    *,
    force_tool_retrieval: bool,
    corpus: Optional[EctQaCorpus] = None,
    recorder: Optional[RetrievalRecorder] = None,
    question_id_getter=None,
    top_k: int = 8,
    metadata_filter: str = "off",
    refusal_guard: bool = False,
    refusal_min_raw_score: float = 0.02,
    temporal_evidence_cards: int = 8,
    temporal_evidence_chars: int = 700,
    temporal_pseudo_questions: int = 0,
    temporal_fact_sentences: bool = True,
    temporal_graph_fact_pack: bool = False,
):
    if agent_name == TEMPORAL_AGENT_NAME:
        if corpus is None or recorder is None or question_id_getter is None:
            raise ValueError(f"{TEMPORAL_AGENT_NAME} requires corpus, recorder, and question_id_getter.")
        return TemporalEvidenceAgent(
            corpus=corpus,
            recorder=recorder,
            question_id_getter=question_id_getter,
            top_k=top_k,
            metadata_filter=metadata_filter,
            refusal_guard=refusal_guard,
            refusal_min_raw_score=refusal_min_raw_score,
            evidence_cards=temporal_evidence_cards,
            evidence_chars=temporal_evidence_chars,
            pseudo_questions=temporal_pseudo_questions,
            use_fact_sentences=temporal_fact_sentences,
            use_graph_fact_pack=temporal_graph_fact_pack,
        )

    from graphrag_agent.agents.naive_rag_agent import NaiveRagAgent
    from graphrag_agent.agents.graph_agent import GraphAgent
    from graphrag_agent.agents.hybrid_agent import HybridAgent

    if agent_name == "NaiveRagAgent":
        agent = NaiveRagAgent(force_tool_retrieval=force_tool_retrieval)
        prepare_agent_for_eval(agent)
        return agent
    if agent_name == "GraphAgent":
        agent = GraphAgent(force_tool_retrieval=force_tool_retrieval)
        prepare_agent_for_eval(agent)
        return agent
    if agent_name == "HybridAgent":
        agent = HybridAgent()
        agent.force_tool_retrieval = force_tool_retrieval
        prepare_agent_for_eval(agent)
        return agent
    raise ValueError(f"Unknown agent: {agent_name}")


def lightweight_keywords(query: str) -> Dict[str, List[str]]:
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9&.-]{2,}\b", query or "")
    return {"low_level": words[:8], "high_level": words[:4]}


def prepare_agent_for_eval(agent: Any) -> None:
    """Disable answer-cache shortcuts that can contaminate cold-cache evaluation."""
    agent._extract_keywords = lightweight_keywords
    agent.check_fast_cache = lambda query, thread_id="default": None


def select_questions(
    questions: Sequence[Dict[str, Any]],
    *,
    answer_filter: str,
    limit: Optional[int],
    offset: int,
) -> List[Dict[str, Any]]:
    filtered = []
    for question in questions:
        is_answerable = normalize_answer(str(question.get("answer", ""))) != "unanswerable"
        if answer_filter == "answerable" and not is_answerable:
            continue
        if answer_filter == "unanswerable" and is_answerable:
            continue
        filtered.append(dict(question))
    if offset:
        filtered = filtered[offset:]
    if limit is not None:
        filtered = filtered[:limit]
    for idx, question in enumerate(filtered):
        raw_id = f"{question.get('question','')}::{question.get('answer','')}::{idx + offset}"
        question["id"] = question.get("id") or stable_id(raw_id)
    return filtered


def evidence_metrics(
    question: Mapping[str, Any],
    hits: Sequence[Mapping[str, Any]],
    *,
    top_k: int,
) -> Dict[str, Any]:
    gold = [
        ev for ev in question.get("evidence_list", [])
        if isinstance(ev, Mapping) and ev.get("ect_filename")
    ]
    if not gold:
        return {
            "gold_evidence_count": 0,
            "doc_recall_at_k": None,
            "evidence_text_recall_at_k": None,
            "all_support_recall_at_k": None,
            "temporal_coverage_at_k": None,
            "gold_filenames": [],
            "retrieved_filenames": [hit.get("filename") for hit in hits[:top_k]],
        }

    top_hits = list(hits[:top_k])
    retrieved_filenames = {hit.get("filename") for hit in top_hits}
    gold_filenames = [ev.get("ect_filename") for ev in gold]
    unique_gold_filenames = set(gold_filenames)
    doc_hits = unique_gold_filenames & retrieved_filenames

    normalized_hit_text = "\n".join(
        normalize_snippet(str(hit.get("text", hit.get("text_preview", ""))))
        for hit in top_hits
    )
    text_hits = 0
    for ev in gold:
        snippet = normalize_snippet(str(ev.get("evidence", "")))
        if snippet and snippet in normalized_hit_text:
            text_hits += 1

    gold_times = {(str(ev.get("year", "")), str(ev.get("quarter", ""))) for ev in gold}
    retrieved_times = {(str(hit.get("year", "")), str(hit.get("quarter", ""))) for hit in top_hits}
    temporal_hits = gold_times & retrieved_times

    return {
        "gold_evidence_count": len(gold),
        "doc_recall_at_k": len(doc_hits) / len(unique_gold_filenames) if unique_gold_filenames else None,
        "evidence_text_recall_at_k": text_hits / len(gold) if gold else None,
        "all_support_recall_at_k": float(unique_gold_filenames.issubset(retrieved_filenames)),
        "temporal_coverage_at_k": len(temporal_hits) / len(gold_times) if gold_times else None,
        "gold_filenames": sorted(unique_gold_filenames),
        "retrieved_filenames": [hit.get("filename") for hit in top_hits],
    }


def citation_support_rate(
    answer: str,
    evidence: Mapping[str, Any],
    hits: Sequence[Mapping[str, Any]],
) -> Optional[float]:
    gold_filenames = set(evidence.get("gold_filenames") or [])
    if not gold_filenames:
        return None
    cited_chunk_ids = set(re.findall(r"ect_[0-9a-f]{12}", answer or ""))
    if not cited_chunk_ids:
        return 0.0
    chunk_to_file = {str(hit.get("chunk_id")): hit.get("filename") for hit in hits}
    matched = [
        chunk_id
        for chunk_id in cited_chunk_ids
        if chunk_to_file.get(chunk_id) in gold_filenames
    ]
    return len(matched) / len(cited_chunk_ids)


def truncate_for_judge(text: Any, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20].rstrip() + "\n...[truncated]"


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("LLM judge did not return a JSON object")


def normalize_judge_label(label: Any) -> str:
    value = normalize_metadata_text(str(label or ""))
    if value in {"correct", "right", "true"}:
        return "correct"
    if "refusal" in value or "refuse" in value or "cannot" in value or "insufficient" in value:
        return "refusal"
    return "incorrect"


def clamp_judge_score(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, score))


def build_judge_context(
    question: Mapping[str, Any],
    hits: Sequence[Mapping[str, Any]],
    *,
    max_evidence: int,
    evidence_chars: int,
) -> Dict[str, Any]:
    gold_evidence = []
    for evidence in question.get("evidence_list", []):
        if not isinstance(evidence, Mapping):
            continue
        gold_evidence.append(
            {
                "company_name": evidence.get("company_name", ""),
                "stock_code": evidence.get("stock_code", ""),
                "year": evidence.get("year", ""),
                "quarter": evidence.get("quarter", ""),
                "ect_filename": evidence.get("ect_filename", ""),
                "evidence": truncate_for_judge(evidence.get("evidence", ""), evidence_chars),
            }
        )

    retrieved_evidence = []
    for rank, hit in enumerate(hits[:max_evidence], start=1):
        retrieved_evidence.append(
            {
                "rank": rank,
                "chunk_id": hit.get("chunk_id", ""),
                "filename": hit.get("filename", ""),
                "company_name": hit.get("company_name", ""),
                "stock_code": hit.get("stock_code", ""),
                "year": hit.get("year", ""),
                "quarter": hit.get("quarter", ""),
                "score": hit.get("score"),
                "text": truncate_for_judge(
                    hit.get("text") or hit.get("text_preview", ""),
                    evidence_chars,
                ),
            }
        )

    return {
        "gold_evidence": gold_evidence[:max_evidence],
        "retrieved_evidence": retrieved_evidence,
    }


def make_llm_judge(args: argparse.Namespace) -> Tuple[Any, str]:
    from langchain_openai import ChatOpenAI
    from graphrag_agent.config.settings import OPENAI_LLM_CONFIG

    config = {k: v for k, v in OPENAI_LLM_CONFIG.items() if v is not None and v != ""}
    model = (
        args.judge_model
        or os.getenv("LLM_JUDGE_MODEL")
        or DEFAULT_LLM_JUDGE_MODEL
        or config.get("model")
    )
    if not model:
        raise ValueError(
            "LLM judge requires --judge-model, LLM_JUDGE_MODEL, OPENAI_LLM_MODEL, "
            f"or the default {DEFAULT_LLM_JUDGE_MODEL}."
        )
    config["model"] = model
    config["temperature"] = args.judge_temperature
    config["max_tokens"] = args.judge_max_tokens
    if args.judge_timeout is not None:
        config["timeout"] = args.judge_timeout
    return ChatOpenAI(**config), str(model)


def build_judge_schema(profile: str) -> str:
    schema: Dict[str, Any] = {
        "judge_label": "correct|refusal|incorrect",
    }
    for key in judge_score_keys(profile):
        schema[key] = 0.0
    schema["rationale"] = "one concise sentence"
    return json.dumps(schema, ensure_ascii=False, indent=2)


def build_judge_dimension_guidance(profile: str) -> str:
    lines = []
    for key in judge_score_keys(profile):
        lines.append(f"- {key}: {JUDGE_SCORE_DESCRIPTIONS[key]}")
    return "\n".join(lines)


def build_judge_prompt(
    *,
    question: Mapping[str, Any],
    answer: str,
    hits: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> str:
    context = build_judge_context(
        question,
        hits,
        max_evidence=args.judge_max_evidence,
        evidence_chars=args.judge_evidence_chars,
    )
    prompt_payload = {
        "question": question.get("question", ""),
        "gold_answer": question.get("answer", ""),
        "question_type": question.get("question_type", ""),
        "reasoning_type": question.get("reasoning_type", ""),
        "num_hops": question.get("num_hops", 0),
        "gold_evidence": context["gold_evidence"],
        "retrieved_evidence": context["retrieved_evidence"],
        "model_answer": truncate_for_judge(answer, args.judge_max_answer_chars),
    }
    schema_text = build_judge_schema(args.judge_profile)
    dimension_guidance = build_judge_dimension_guidance(args.judge_profile)
    prompt = f"""
You are an impartial evaluator for a financial temporal RAG system.

Judge the model answer against the gold answer and the provided evidence.
Use these labels:
- "correct": the answer semantically resolves the question and matches the gold answer.
- "refusal": the answer explicitly refuses or says the evidence is insufficient.
- "incorrect": the answer is wrong, unsupported, irrelevant, or gives a substantive answer when the gold answer is unanswerable.

Important rules:
- For gold_answer == "unanswerable", label a well-justified refusal as "refusal"; label any substantive unsupported answer as "incorrect".
- For answerable questions, label an unnecessary refusal as "refusal", not "correct".
- Pay special attention to company, year, quarter, numeric comparisons, and whether the answer is grounded in retrieved evidence.
- Return JSON only. Do not include markdown.

Score dimensions:
{dimension_guidance}

Return this schema:
{schema_text}

Evaluation item:
{json.dumps(prompt_payload, ensure_ascii=False)}
""".strip()

    return prompt


def judge_prediction_with_llm(
    *,
    llm: Any,
    judge_model: str,
    question: Mapping[str, Any],
    answer: str,
    hits: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    prompt = build_judge_prompt(
        question=question,
        answer=answer,
        hits=hits,
        args=args,
    )
    started = time.perf_counter()
    raw_response = ""
    last_error = ""
    for attempt in range(args.judge_retries + 1):
        try:
            response = llm.invoke(prompt)
            raw_response = response.content if hasattr(response, "content") else str(response)
            parsed = extract_json_object(raw_response)
            label = normalize_judge_label(parsed.get("judge_label") or parsed.get("label"))
            return {
                "enabled": True,
                "model": judge_model,
                "profile": args.judge_profile,
                "judge_label": label,
                "label": label,
                **{
                    key: clamp_judge_score(parsed.get(key))
                    for key in judge_score_keys(args.judge_profile)
                },
                "rationale": truncate_for_judge(parsed.get("rationale", ""), 500),
                "raw_response": truncate_for_judge(raw_response, 1200),
                "latency_seconds": time.perf_counter() - started,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < args.judge_retries:
                time.sleep(1 + attempt)

    return {
        "enabled": True,
        "model": judge_model,
        "profile": args.judge_profile,
        "judge_label": "judge_error",
        "label": "judge_error",
        **{key: None for key in judge_score_keys(args.judge_profile)},
        "rationale": "",
        "raw_response": truncate_for_judge(raw_response, 1200),
        "latency_seconds": time.perf_counter() - started,
        "error": last_error,
    }


def evaluate_prediction(question: Mapping[str, Any], answer: str) -> Dict[str, Any]:
    gold = str(question.get("answer", ""))
    em = exact_match(answer, gold)
    f1, precision, recall = token_f1(answer, gold)
    rouge = rouge_l(answer, gold)
    bucket = classify_answer(answer, gold, f1)
    return {
        "em": em,
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "rouge_l": rouge,
        "bucket": bucket,
    }


def aggregate(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    answer_keys = ["em", "f1", "precision", "recall", "rouge_l"]
    result: Dict[str, Any] = {}
    for key in answer_keys:
        result[key] = sum(float(row["answer_metrics"][key]) for row in rows) / len(rows)

    buckets = Counter(row["answer_metrics"]["bucket"] for row in rows)
    result["buckets"] = dict(buckets)
    result["correct_like_rate"] = (
        buckets.get("correct", 0) + buckets.get("correct_refusal", 0)
    ) / len(rows)

    retrieval_keys = [
        "doc_recall_at_k",
        "evidence_text_recall_at_k",
        "all_support_recall_at_k",
        "temporal_coverage_at_k",
        "citation_support_rate",
    ]
    for key in retrieval_keys:
        values = [
            row["retrieval_metrics"][key]
            for row in rows
            if row["retrieval_metrics"].get(key) is not None
        ]
        result[key] = sum(values) / len(values) if values else None

    judge_rows = [
        row for row in rows
        if (row.get("llm_judge") or {}).get("enabled")
    ]
    if judge_rows:
        labels = Counter(
            row["llm_judge"].get("judge_label")
            or row["llm_judge"].get("label", "unknown")
            for row in judge_rows
        )
        result["llm_judge"] = {
            "count": len(judge_rows),
            "labels": dict(labels),
            "correct_like_rate": sum(
                1
                for row in judge_rows
                if (
                    normalize_answer(str(row.get("gold_answer", ""))) == "unanswerable"
                    and (
                        row["llm_judge"].get("judge_label")
                        or row["llm_judge"].get("label")
                    ) == "refusal"
                )
                or (
                    normalize_answer(str(row.get("gold_answer", ""))) != "unanswerable"
                    and (
                        row["llm_judge"].get("judge_label")
                        or row["llm_judge"].get("label")
                    ) == "correct"
                )
            ) / len(judge_rows),
            "num_judge_errors": sum(
                1 for row in judge_rows if row["llm_judge"].get("error")
            ),
        }
        for key in FULL_JUDGE_SCORE_KEYS:
            values = [
                row["llm_judge"][key]
                for row in judge_rows
                if row["llm_judge"].get(key) is not None
            ]
            if values:
                result["llm_judge"][key] = sum(values) / len(values)
    result["num_examples"] = len(rows)
    result["num_errors"] = sum(1 for row in rows if row.get("error"))
    return result


def run_eval(args: argparse.Namespace) -> Dict[str, Any]:
    load_dotenv(PROJECT_ROOT / ".env")
    install_dummy_neo4j()

    data_manager = EctQaDataManager(args.data_dir, download=args.download)
    raw_questions = data_manager.load_questions(args.scenario)
    selected_questions = select_questions(
        raw_questions,
        answer_filter=args.answer_filter,
        limit=args.limit,
        offset=args.offset,
    )
    documents = data_manager.load_corpus(
        args.scenario,
        selected_questions,
        corpus_scope=args.corpus_scope,
        distractor_files=args.distractor_files,
    )
    corpus = EctQaCorpus(documents, max_chars=args.chunk_chars, overlap=args.chunk_overlap)
    if args.retriever == "graph":
        # Real Neo4j driver built from env: the agent-import stub installed above
        # replaces graphrag_agent.config.neo4jdb, so we cannot use get_db_manager.
        from neo4j import GraphDatabase

        graph_driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
        )
        corpus = FinancialGraphRetriever(corpus, graph_driver)
        print("[retriever] using Fin* graph (time-filter + PPR), TF-IDF fallback when unscoped")
    recorder = RetrievalRecorder()

    selected_agents = args.agents.split(",")
    current_question_id = {"value": ""}
    judge_llm = None
    judge_model = ""
    if args.llm_judge:
        judge_llm, judge_model = make_llm_judge(args)

    payload: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "scenario": args.scenario,
            "answer_filter": args.answer_filter,
            "limit": args.limit,
            "offset": args.offset,
            "agents": selected_agents,
            "retrieval_top_k": args.retrieval_top_k,
            "metric_top_k": args.metric_top_k,
            "corpus_scope": args.corpus_scope,
            "retriever": args.retriever,
            "graph_fact_pack": args.graph_fact_pack,
            "distractor_files": args.distractor_files,
            "chunk_chars": args.chunk_chars,
            "chunk_overlap": args.chunk_overlap,
            "cold_cache": args.cold_cache,
            "force_tool_retrieval": args.force_tool_retrieval,
            "metadata_filter": args.metadata_filter,
            "refusal_guard": args.refusal_guard,
            "refusal_min_raw_score": args.refusal_min_raw_score,
            "temporal_evidence_cards": args.temporal_evidence_cards,
            "temporal_evidence_chars": args.temporal_evidence_chars,
            "temporal_pseudo_questions": args.temporal_pseudo_questions,
            "temporal_fact_sentences": args.temporal_fact_sentences,
            "llm_judge": args.llm_judge,
            "judge_profile": args.judge_profile,
            "judge_model": (
                judge_model
                or args.judge_model
                or os.getenv("LLM_JUDGE_MODEL")
                or DEFAULT_LLM_JUDGE_MODEL
            ),
            "judge_max_evidence": args.judge_max_evidence,
            "judge_evidence_chars": args.judge_evidence_chars,
            "judge_max_answer_chars": args.judge_max_answer_chars,
        },
        "dataset": {
            "questions_loaded": len(raw_questions),
            "questions_selected": len(selected_questions),
            "documents_loaded": len(documents),
            "chunks_indexed": len(corpus.chunks),
        },
        "agents": {},
        "rows": [],
    }

    with isolated_working_directory(args.cold_cache) as tmp_cwd:
        if tmp_cwd is not None:
            payload["config"]["cold_cache_cwd"] = str(tmp_cwd)

        for agent_name in selected_agents:
            agent_name = agent_name.strip()
            if not agent_name:
                continue
            if agent_name != TEMPORAL_AGENT_NAME:
                patch_agent_tools(
                    corpus,
                    recorder,
                    agent_name=agent_name,
                    question_id_getter=lambda: current_question_id["value"],
                    top_k=args.retrieval_top_k,
                    metadata_filter=args.metadata_filter,
                )
            agent = make_agent(
                agent_name,
                force_tool_retrieval=args.force_tool_retrieval,
                corpus=corpus,
                recorder=recorder,
                question_id_getter=lambda: current_question_id["value"],
                top_k=args.retrieval_top_k,
                metadata_filter=args.metadata_filter,
                refusal_guard=args.refusal_guard,
                refusal_min_raw_score=args.refusal_min_raw_score,
                temporal_evidence_cards=args.temporal_evidence_cards,
                temporal_evidence_chars=args.temporal_evidence_chars,
                temporal_pseudo_questions=args.temporal_pseudo_questions,
                temporal_fact_sentences=args.temporal_fact_sentences,
                temporal_graph_fact_pack=args.graph_fact_pack,
            )
            agent_rows: List[Dict[str, Any]] = []
            try:
                for index, question in enumerate(selected_questions, start=1):
                    question_id = str(question["id"])
                    current_question_id["value"] = question_id
                    start = time.perf_counter()
                    error = ""
                    query_text = str(question["question"])
                    refusal_guard_reason = ""
                    preguarded = False
                    if args.refusal_guard:
                        probe_hits = corpus.search(
                            query_text,
                            top_k=args.retrieval_top_k,
                            metadata_filter=args.metadata_filter,
                        )
                        probe_reason = corpus.refusal_reason(
                            query_text,
                            probe_hits,
                            min_raw_score=args.refusal_min_raw_score,
                        )
                        if probe_reason in {"requested_time_out_of_corpus", "company_not_in_corpus"}:
                            recorder.record(agent_name, question_id, "refusal_guard_probe", probe_hits)
                            refusal_guard_reason = probe_reason
                            answer = (
                                "Insufficient evidence to answer reliably. "
                                f"Refusal reason: {refusal_guard_reason}."
                            )
                            preguarded = True

                    if not preguarded:
                        try:
                            answer = agent.ask(
                                query_text,
                                thread_id=f"ectqa_{args.scenario}_{agent_name}_{question_id}",
                                recursion_limit=args.recursion_limit,
                            )
                        except Exception as exc:  # noqa: BLE001
                            answer = ""
                            error = str(exc)
                    latency = time.perf_counter() - start
                    hits = recorder.hits_for(agent_name, question_id)
                    retrieval = evidence_metrics(question, hits, top_k=args.metric_top_k)
                    if args.refusal_guard and not refusal_guard_reason:
                        refusal_guard_reason = corpus.refusal_reason(
                            query_text,
                            hits,
                            min_raw_score=args.refusal_min_raw_score,
                        )
                        if refusal_guard_reason:
                            answer = (
                                "Insufficient evidence to answer reliably. "
                                f"Refusal reason: {refusal_guard_reason}."
                            )
                    answer_metrics = evaluate_prediction(question, answer)
                    citation_rate = citation_support_rate(answer, retrieval, hits)
                    if citation_rate is not None:
                        retrieval["citation_support_rate"] = citation_rate
                    llm_judge = None
                    if judge_llm is not None:
                        llm_judge = judge_prediction_with_llm(
                            llm=judge_llm,
                            judge_model=judge_model,
                            question=question,
                            answer=answer,
                            hits=hits,
                            args=args,
                        )
                    row = {
                        "agent": agent_name,
                        "index": index,
                        "question_id": question_id,
                        "question": question.get("question", ""),
                        "gold_answer": question.get("answer", ""),
                        "reasoning_type": question.get("reasoning_type", ""),
                        "question_type": question.get("question_type", ""),
                        "num_hops": question.get("num_hops", 0),
                        "answer": answer,
                        "answer_preview": answer[:600],
                        "answer_metrics": answer_metrics,
                        "retrieval_metrics": retrieval,
                        "retrieval_events": recorder.events_for(agent_name, question_id),
                        "llm_judge": llm_judge,
                        "refusal_guard_reason": refusal_guard_reason,
                        "latency_seconds": latency,
                        "error": error,
                    }
                    agent_rows.append(row)
                    payload["rows"].append(row)
                    if not args.quiet:
                        print(
                            f"[{agent_name}] {index}/{len(selected_questions)} "
                            f"bucket={answer_metrics['bucket']} "
                            f"f1={answer_metrics['f1']:.3f} "
                            f"docR@{args.metric_top_k}={retrieval.get('doc_recall_at_k')} "
                            f"judge={llm_judge.get('judge_label') if llm_judge else 'off'} "
                            f"latency={latency:.2f}s"
                        )
            finally:
                if hasattr(agent, "close"):
                    try:
                        agent.close()
                    except Exception:
                        pass
            payload["agents"][agent_name] = aggregate(agent_rows)

    payload["finished_at"] = datetime.now().isoformat(timespec="seconds")
    payload["overall"] = aggregate(payload["rows"])
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate TempoRAG-Fin on ECT-QA local questions.")
    parser.add_argument("--scenario", choices=["base", "updated", "new"], default="new")
    parser.add_argument("--answer-filter", choices=["answerable", "unanswerable", "all"], default="answerable")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--agents", default=TEMPORAL_AGENT_NAME)
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "datasets" / "ect_qa")
    parser.add_argument("--output-json", type=Path, default=PROJECT_ROOT / "docs" / "ectqa_eval_smoke.json")
    parser.add_argument("--download", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--corpus-scope", choices=["evidence", "full"], default="full")
    parser.add_argument("--distractor-files", type=int, default=0)
    parser.add_argument("--retrieval-top-k", type=int, default=8)
    parser.add_argument("--metric-top-k", type=int, default=8)
    parser.add_argument("--chunk-chars", type=int, default=1800)
    parser.add_argument("--chunk-overlap", type=int, default=250)
    parser.add_argument("--recursion-limit", type=int, default=10)
    parser.add_argument("--cold-cache", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force-tool-retrieval", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--metadata-filter", choices=["off", "boost", "strict"], default="off")
    parser.add_argument(
        "--retriever",
        choices=["tfidf", "graph"],
        default="tfidf",
        help="tfidf = in-script TF-IDF; graph = time-filter + PPR over the Fin* Neo4j graph (WP3)",
    )
    parser.add_argument(
        "--graph-fact-pack",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="P2: inject a value_kind-aware graph fact table + cross-period card into the prompt (needs --retriever graph)",
    )
    parser.add_argument("--refusal-guard", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--refusal-min-raw-score", type=float, default=0.02)
    parser.add_argument("--temporal-evidence-cards", type=int, default=8)
    parser.add_argument("--temporal-evidence-chars", type=int, default=700)
    parser.add_argument("--temporal-pseudo-questions", type=int, default=0)
    parser.add_argument(
        "--temporal-fact-sentences",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="ToG-style fact-sentence stitching; off by default — ablation showed it HURT quality",
    )
    parser.add_argument("--llm-judge", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--judge-profile", choices=["focused", "full"], default="focused")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=700)
    parser.add_argument("--judge-timeout", type=float, default=None)
    parser.add_argument("--judge-retries", type=int, default=1)
    parser.add_argument("--judge-max-evidence", type=int, default=5)
    parser.add_argument("--judge-evidence-chars", type=int, default=700)
    parser.add_argument("--judge-max-answer-chars", type=int, default=1800)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_json = args.output_json.resolve()
    args.data_dir = args.data_dir.resolve()
    result = run_eval(args)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== ECT-QA Eval Summary ===")
    print(json.dumps({"dataset": result["dataset"], "agents": result["agents"], "overall": result["overall"]}, ensure_ascii=False, indent=2))
    print(f"\nSaved result JSON to: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
