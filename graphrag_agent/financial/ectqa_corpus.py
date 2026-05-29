"""ECT-QA corpus loading and chunking.

Extracted from ``scripts/ectqa_eval.py`` so the same dataset loader is shared by
the evaluation harness *and* the financial graph builder (WP2) instead of being
duplicated. This module is intentionally free of Neo4j / sklearn dependencies:
it only downloads ECT-QA transcripts and splits them into period-tagged chunks.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import re

import requests

from .temporal_facts import stable_id

HF_REPO = "austinmyc/ECT-QA"
HF_BASE = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main"
HF_TREE = f"https://huggingface.co/api/datasets/{HF_REPO}/tree/main?recursive=1"


@dataclass(frozen=True)
class EctDocument:
    filename: str
    split: str
    company_name: str
    stock_code: str
    sector: str
    year: str
    quarter: str
    raw_content: str


@dataclass(frozen=True)
class EctChunk:
    chunk_id: str
    filename: str
    split: str
    company_name: str
    stock_code: str
    sector: str
    year: str
    quarter: str
    text: str


class EctQaDataManager:
    def __init__(self, data_dir: Path, *, download: bool = True) -> None:
        self.data_dir = data_dir
        self.download = download
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _local_path(self, remote_path: str) -> Path:
        return self.data_dir / remote_path

    def _download_file(self, remote_path: str) -> Path:
        local_path = self._local_path(remote_path)
        if local_path.exists():
            return local_path
        if not self.download:
            raise FileNotFoundError(
                f"Missing {local_path}; rerun with --download or place the ECT-QA file locally."
            )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"{HF_BASE}/{remote_path}"
        last_error: Optional[Exception] = None
        for attempt in range(1, 6):
            try:
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                local_path.write_bytes(response.content)
                return local_path
            except requests.RequestException as exc:
                last_error = exc
                if attempt == 5:
                    break
                sleep_seconds = min(2**attempt, 20)
                print(
                    f"Download failed for {remote_path} "
                    f"(attempt {attempt}/5): {exc}. Retrying in {sleep_seconds}s..."
                )
                time.sleep(sleep_seconds)
        raise RuntimeError(f"Failed to download {remote_path} from {url}: {last_error}") from last_error

    def _tree(self) -> List[Dict[str, Any]]:
        cache_path = self.data_dir / "_hf_tree.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        if not self.download:
            raise FileNotFoundError(
                f"Missing {cache_path}; rerun with --download to fetch the ECT-QA file list."
            )
        response = requests.get(HF_TREE, timeout=60)
        response.raise_for_status()
        payload = response.json()
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def load_questions(self, scenario: str) -> List[Dict[str, Any]]:
        if scenario in {"base", "updated"}:
            remote_path = "questions/local_questions_old.json"
        elif scenario == "new":
            remote_path = "questions/local_questions_new.json"
        else:
            raise ValueError(f"Unsupported scenario: {scenario}")
        path = self._download_file(remote_path)
        questions = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(questions, list):
            raise ValueError(f"Expected list in {path}")
        return questions

    def corpus_file_paths(self, scenario: str) -> List[str]:
        allowed_prefixes = {
            "base": ("data/old/",),
            "updated": ("data/old/", "data/new/"),
            "new": ("data/old/", "data/new/"),
        }[scenario]
        paths = [
            item["path"]
            for item in self._tree()
            if item.get("type") == "file"
            and item.get("path", "").endswith(".json")
            and item.get("path", "").startswith(allowed_prefixes)
        ]
        return sorted(paths)

    def load_document_by_remote_path(self, remote_path: str) -> EctDocument:
        path = self._download_file(remote_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        split = "new" if remote_path.startswith("data/new/") else "old"
        return EctDocument(
            filename=Path(remote_path).name,
            split=split,
            company_name=str(payload.get("company_name", "")),
            stock_code=str(payload.get("stock_code", "")),
            sector=str(payload.get("sector", "")),
            year=str(payload.get("year", "")),
            quarter=str(payload.get("quarter", "")),
            raw_content=str(payload.get("raw_content", "")),
        )

    def load_corpus(
        self,
        scenario: str,
        questions: Sequence[Mapping[str, Any]],
        *,
        corpus_scope: str,
        distractor_files: int = 0,
    ) -> List[EctDocument]:
        all_paths = self.corpus_file_paths(scenario)
        if corpus_scope == "full":
            selected_paths = all_paths
        elif corpus_scope == "evidence":
            evidence_filenames = {
                ev.get("ect_filename")
                for question in questions
                for ev in question.get("evidence_list", [])
                if isinstance(ev, Mapping) and ev.get("ect_filename")
            }
            selected_paths = [
                path for path in all_paths if Path(path).name in evidence_filenames
            ]
            if distractor_files > 0:
                selected = set(selected_paths)
                for path in all_paths:
                    if path not in selected:
                        selected_paths.append(path)
                        if len(selected_paths) >= len(selected) + distractor_files:
                            break
            selected_paths = sorted(set(selected_paths))
        else:
            raise ValueError(f"Unsupported corpus scope: {corpus_scope}")

        if not selected_paths:
            raise ValueError("No corpus documents selected. Check scenario/filter/limit.")
        return [self.load_document_by_remote_path(path) for path in selected_paths]


def chunk_document(document: EctDocument, *, max_chars: int, overlap: int) -> List[EctChunk]:
    text = re.sub(r"\n{3,}", "\n\n", document.raw_content).strip()
    chunks: List[EctChunk] = []
    start = 0
    chunk_index = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunk_text = text[start:end].strip()
        if chunk_text:
            raw_id = f"{document.filename}:{chunk_index}:{chunk_text[:80]}"
            chunks.append(
                EctChunk(
                    chunk_id=f"ect_{stable_id(raw_id)}",
                    filename=document.filename,
                    split=document.split,
                    company_name=document.company_name,
                    stock_code=document.stock_code,
                    sector=document.sector,
                    year=document.year,
                    quarter=document.quarter,
                    text=chunk_text,
                )
            )
            chunk_index += 1
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks
