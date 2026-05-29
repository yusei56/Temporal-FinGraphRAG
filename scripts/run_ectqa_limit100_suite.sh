#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/home/bian/projects/graph-rag-agent"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
SUITE_LOG="$PROJECT_ROOT/docs/ectqa_eval_limit100_suite.log"

cd "$PROJECT_ROOT"

export PYTHONPATH="$PROJECT_ROOT"
export LANGCHAIN_TRACING_V2=false
export LANGSMITH_TRACING=false

mkdir -p "$PROJECT_ROOT/docs"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$SUITE_LOG"
}

summarize_json() {
  local output_json="$1"
  "$PYTHON" - "$output_json" <<'PY' | tee -a "$SUITE_LOG"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
print(f"summary_file={path}")
print(f"dataset={data.get('dataset')}")
print(f"overall={data.get('overall')}")
for agent, metrics in data.get("agents", {}).items():
    print(
        f"agent={agent} "
        f"correct_like={metrics.get('correct_like_rate')} "
        f"doc_recall_at_k={metrics.get('doc_recall_at_k')} "
        f"temporal_coverage_at_k={metrics.get('temporal_coverage_at_k')} "
        f"buckets={metrics.get('buckets')}"
    )
PY
}

run_eval() {
  local name="$1"
  local output_json="$2"
  local run_log="$3"
  shift 3

  log "START $name"
  log "output_json=$output_json"
  log "run_log=$run_log"

  set +e
  "$PYTHON" "$PROJECT_ROOT/scripts/ectqa_eval.py" "$@" --output-json "$output_json" --quiet > "$run_log" 2>&1
  local status=$?
  set -e

  if [[ "$status" -ne 0 ]]; then
    log "FAILED $name status=$status"
    log "See log: $run_log"
    exit "$status"
  fi

  log "FINISHED $name"
  summarize_json "$output_json"
}

log "ECT-QA limit=100 suite started"
log "project=$PROJECT_ROOT"

run_eval \
  "answerable_baseline_off_limit100" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100.json" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100.log" \
  --scenario new \
  --answer-filter answerable \
  --limit 100 \
  --corpus-scope full \
  --metadata-filter off \
  --no-refusal-guard \
  --retrieval-top-k 8 \
  --metric-top-k 8

run_eval \
  "answerable_metadata_boost_limit100" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100_metadata_boost.json" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_answerable_limit100_metadata_boost.log" \
  --scenario new \
  --answer-filter answerable \
  --limit 100 \
  --corpus-scope full \
  --metadata-filter boost \
  --no-refusal-guard \
  --retrieval-top-k 8 \
  --metric-top-k 8

run_eval \
  "unanswerable_metadata_boost_refusal_guard_limit100" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_unanswerable_limit100_refusal_guard.json" \
  "$PROJECT_ROOT/docs/ectqa_eval_full_unanswerable_limit100_refusal_guard.log" \
  --scenario new \
  --answer-filter unanswerable \
  --limit 100 \
  --corpus-scope full \
  --metadata-filter boost \
  --refusal-guard \
  --retrieval-top-k 8 \
  --metric-top-k 8

log "ECT-QA limit=100 suite completed"
