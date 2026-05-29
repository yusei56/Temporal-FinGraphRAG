"""Time-filtered retrieval primitives for financial temporal facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .evidence_table import build_evidence_table
from .temporal_facts import metadata_values, normalize_metadata_text


@dataclass(frozen=True)
class TemporalRetrievalResult:
    query: str
    rows: List[Dict[str, Any]]
    candidate_rows: int
    filtered_rows: int
    requested_companies: List[str]
    requested_years: List[str]
    requested_year_quarters: List[Tuple[str, str]]
    coverage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def row_matches_time(row: Mapping[str, Any], metadata: Any) -> bool:
    requested_yq = {
        (str(year), str(quarter))
        for year, quarter in metadata_values(metadata, "requested_year_quarters")
    }
    requested_years = {str(year) for year in metadata_values(metadata, "requested_years")}
    row_year = str(row.get("year", ""))
    row_quarter = str(row.get("quarter", ""))
    if requested_yq:
        return (row_year, row_quarter) in requested_yq
    if requested_years:
        return row_year in requested_years
    return True


def row_matches_company(row: Mapping[str, Any], metadata: Any) -> bool:
    companies = {str(company) for company in metadata_values(metadata, "matched_companies")}
    if not companies:
        return True
    return str(row.get("company_name", "")) in companies


def build_coverage(rows: Sequence[Mapping[str, Any]], metadata: Any) -> Dict[str, Any]:
    requested_companies = [str(company) for company in metadata_values(metadata, "matched_companies")]
    requested_yq = [
        (str(year), str(quarter))
        for year, quarter in metadata_values(metadata, "requested_year_quarters")
    ]
    requested_years = [str(year) for year in metadata_values(metadata, "requested_years")]

    covered_companies = {str(row.get("company_name", "")) for row in rows}
    covered_yq = {(str(row.get("year", "")), str(row.get("quarter", ""))) for row in rows}
    covered_years = {str(row.get("year", "")) for row in rows}
    return {
        "company_coverage": (
            len(set(requested_companies) & covered_companies) / len(set(requested_companies))
            if requested_companies
            else None
        ),
        "year_quarter_coverage": (
            len(set(requested_yq) & covered_yq) / len(set(requested_yq))
            if requested_yq
            else None
        ),
        "year_coverage": (
            len(set(requested_years) & covered_years) / len(set(requested_years))
            if requested_years
            else None
        ),
        "covered_companies": sorted(covered_companies),
        "covered_year_quarters": sorted(covered_yq),
        "covered_years": sorted(covered_years),
    }


class TemporalFilteredRetriever:
    """Apply explicit company/time constraints before graph expansion or PPR."""

    def retrieve(
        self,
        *,
        query: str,
        evidence_cards: Sequence[Mapping[str, Any]],
        metadata: Any,
        top_k: int = 24,
        use_ppr: bool = False,
        ppr_iterations: int = 20,
    ) -> TemporalRetrievalResult:
        table = build_evidence_table(query, evidence_cards, max_rows=max(top_k * 3, top_k))
        candidate_rows = list(table.get("rows", []))
        filtered_rows = [
            row for row in candidate_rows
            if row_matches_company(row, metadata) and row_matches_time(row, metadata)
        ]
        if use_ppr:
            ranked_rows = rerank_rows_with_ppr(filtered_rows, iterations=ppr_iterations)
        else:
            ranked_rows = [dict(row) for row in filtered_rows]
            ranked_rows.sort(key=lambda row: float(row.get("relevance_score") or 0.0), reverse=True)
        selected = select_coverage_first_rows(ranked_rows, metadata, top_k=top_k)
        return TemporalRetrievalResult(
            query=query,
            rows=selected,
            candidate_rows=len(candidate_rows),
            filtered_rows=len(filtered_rows),
            requested_companies=[str(company) for company in metadata_values(metadata, "matched_companies")],
            requested_years=[str(year) for year in metadata_values(metadata, "requested_years")],
            requested_year_quarters=[
                (str(year), str(quarter))
                for year, quarter in metadata_values(metadata, "requested_year_quarters")
            ],
            coverage=build_coverage(selected, metadata),
        )


def select_coverage_first_rows(
    rows: Sequence[Mapping[str, Any]],
    metadata: Any,
    *,
    top_k: int,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_best(predicate) -> None:
        candidates = [row for row in rows if predicate(row)]
        if not candidates:
            return
        best = max(candidates, key=lambda row: float(row.get("relevance_score") or 0.0))
        row_id = str(best.get("row_id", ""))
        if row_id and row_id not in selected_ids:
            selected_ids.add(row_id)
            selected.append(dict(best))

    for company in metadata_values(metadata, "matched_companies"):
        add_best(lambda row, company=company: str(row.get("company_name", "")) == str(company))

    requested_yq = list(metadata_values(metadata, "requested_year_quarters"))
    if requested_yq:
        for year, quarter in requested_yq:
            add_best(
                lambda row, year=year, quarter=quarter: (
                    str(row.get("year", "")) == str(year)
                    and str(row.get("quarter", "")) == str(quarter)
                )
            )
    else:
        for year in metadata_values(metadata, "requested_years"):
            add_best(lambda row, year=year: str(row.get("year", "")) == str(year))

    for row in rows:
        if len(selected) >= top_k:
            break
        row_id = str(row.get("row_id", ""))
        if row_id in selected_ids:
            continue
        selected_ids.add(row_id)
        selected.append(dict(row))
    return selected[:top_k]


def rerank_rows_with_ppr(
    rows: Sequence[Mapping[str, Any]],
    *,
    damping: float = 0.85,
    iterations: int = 20,
) -> List[Dict[str, Any]]:
    if not rows:
        return []
    row_list = [dict(row) for row in rows]
    seed_scores = [max(float(row.get("relevance_score") or 0.0), 0.0) for row in row_list]
    seed_total = sum(seed_scores) or 1.0
    personalization = [score / seed_total for score in seed_scores]
    rank = personalization[:]
    adjacency = build_row_adjacency(row_list)

    for _ in range(iterations):
        next_rank = [(1.0 - damping) * value for value in personalization]
        for source_index, neighbors in enumerate(adjacency):
            if not neighbors:
                continue
            weight_total = sum(weight for _target_index, weight in neighbors) or 1.0
            for target_index, weight in neighbors:
                next_rank[target_index] += damping * rank[source_index] * weight / weight_total
        rank = next_rank

    max_rank = max(rank) or 1.0
    for index, row in enumerate(row_list):
        ppr_score = rank[index] / max_rank
        row["ppr_score"] = round(ppr_score, 6)
        row["combined_score"] = round(
            float(row.get("relevance_score") or 0.0) + ppr_score,
            6,
        )
    row_list.sort(key=lambda row: float(row.get("combined_score") or 0.0), reverse=True)
    return row_list


def build_row_adjacency(rows: Sequence[Mapping[str, Any]]) -> List[List[Tuple[int, float]]]:
    adjacency: List[List[Tuple[int, float]]] = [[] for _ in rows]
    for source_index, source in enumerate(rows):
        for target_index, target in enumerate(rows):
            if source_index == target_index:
                continue
            weight = row_relation_weight(source, target)
            if weight > 0:
                adjacency[source_index].append((target_index, weight))
    return adjacency


def row_relation_weight(source: Mapping[str, Any], target: Mapping[str, Any]) -> float:
    weight = 0.0
    if source.get("company_name") and source.get("company_name") == target.get("company_name"):
        weight += 1.5
    if source.get("year") and source.get("year") == target.get("year"):
        weight += 0.8
    if source.get("quarter") and source.get("quarter") == target.get("quarter"):
        weight += 0.8
    if source.get("source_chunk_id") and source.get("source_chunk_id") == target.get("source_chunk_id"):
        weight += 1.0
    if source.get("unit") and source.get("unit") == target.get("unit"):
        weight += 0.3
    source_metric = set(normalize_metadata_text(str(source.get("metric_text", ""))).split())
    target_metric = set(normalize_metadata_text(str(target.get("metric_text", ""))).split())
    if source_metric and target_metric:
        overlap = len(source_metric & target_metric)
        union = len(source_metric | target_metric)
        weight += 3.0 * overlap / union
    return weight
