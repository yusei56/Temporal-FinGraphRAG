"""Unit tests for the deterministic named-metric extractors (WP1)."""

import unittest

from graphrag_agent.financial.metric_extractors import (
    detect_query_metrics,
    extract_metric_facts,
)


def _hit(text, **meta):
    base = {
        "text": text,
        "company_name": "Acme Corp",
        "stock_code": "ACME",
        "year": "2023",
        "quarter": "Q2",
        "chunk_id": "c1",
        "filename": "acme_2023_q2.txt",
        "score": 0.9,
    }
    base.update(meta)
    return base


def _by_key(facts, key, qualifier=None):
    out = [f for f in facts if f.metric_key == key]
    if qualifier is not None:
        out = [f for f in out if f.qualifier == qualifier]
    return out


class FreeCashFlowTest(unittest.TestCase):
    def test_binds_currency_value(self):
        facts = extract_metric_facts(_hit("Free cash flow was $1.2 billion in the quarter."))
        fcf = _by_key(facts, "free_cash_flow")
        self.assertEqual(len(fcf), 1)
        self.assertEqual(fcf[0].value, 1_200_000_000)
        self.assertEqual(fcf[0].unit, "billion")

    def test_abbreviation(self):
        facts = extract_metric_facts(_hit("FCF reached $980 million."))
        self.assertTrue(_by_key(facts, "free_cash_flow"))


class GrossMarginTest(unittest.TestCase):
    def test_gaap_and_non_gaap_separated(self):
        text = "GAAP gross margin was 62.5%. Non-GAAP gross margin was 65.1%."
        facts = extract_metric_facts(_hit(text))
        gaap = _by_key(facts, "gross_margin", "gaap")
        non_gaap = _by_key(facts, "gross_margin", "non_gaap")
        self.assertEqual(len(gaap), 1, facts)
        self.assertEqual(len(non_gaap), 1, facts)
        self.assertEqual(gaap[0].value, 62.5)
        self.assertEqual(gaap[0].unit, "percent")
        self.assertEqual(non_gaap[0].value, 65.1)

    def test_rejects_currency_for_percent_metric(self):
        # A $-amount near "gross margin" must not bind to a percent metric.
        facts = extract_metric_facts(_hit("Gross margin dollars were $5.0 billion."))
        self.assertFalse(_by_key(facts, "gross_margin"))


class CashAndInvestmentsTest(unittest.TestCase):
    def test_full_phrase(self):
        facts = extract_metric_facts(
            _hit("Cash, cash equivalents and investments totaled $5.3 billion.")
        )
        cai = _by_key(facts, "cash_and_investments")
        self.assertTrue(cai)
        self.assertEqual(cai[0].value, 5_300_000_000)


class RevenueTest(unittest.TestCase):
    def test_binds_currency_not_percent(self):
        facts = extract_metric_facts(_hit("Total revenue was $10.1 billion, up 8%."))
        rev = _by_key(facts, "revenue")
        self.assertTrue(rev)
        # The bound value is the dollar amount, not the 8% growth figure.
        self.assertEqual(rev[0].value, 10_100_000_000)

    def test_single_fact_for_overlapping_patterns(self):
        # "Total revenue" matches several revenue patterns but the same number
        # must yield only one fact.
        facts = extract_metric_facts(_hit("Total revenue was $10.1 billion."))
        self.assertEqual(len(_by_key(facts, "revenue")), 1)


class EpsTest(unittest.TestCase):
    def test_dollar_per_share(self):
        facts = extract_metric_facts(_hit("Diluted earnings per share were $1.23."))
        eps = _by_key(facts, "eps")
        self.assertTrue(eps)
        self.assertEqual(eps[0].value, 1.23)

    def test_non_gaap_eps_qualifier(self):
        facts = extract_metric_facts(_hit("Non-GAAP EPS was $2.05."))
        self.assertTrue(_by_key(facts, "eps", "non_gaap"))


class FixedMaturityTest(unittest.TestCase):
    def test_purchases(self):
        facts = extract_metric_facts(
            _hit("Purchases of fixed-maturity securities were $2.4 billion.")
        )
        self.assertTrue(_by_key(facts, "fixed_maturity_purchases"))


class ValueKindTest(unittest.TestCase):
    def test_level_default(self):
        facts = extract_metric_facts(_hit("Gross margin was 62.5%."))
        gm = _by_key(facts, "gross_margin")
        self.assertTrue(gm)
        self.assertEqual(gm[0].value_kind, "level")

    def test_basis_points_is_delta(self):
        facts = extract_metric_facts(_hit("Gross margin improved 330 basis points."))
        gm = _by_key(facts, "gross_margin")
        self.assertTrue(gm)
        self.assertTrue(all(f.value_kind == "delta" for f in gm), gm)

    def test_change_word_is_delta(self):
        facts = extract_metric_facts(_hit("Gross margin was up 5% in the quarter."))
        # the 5% bound after "up" is a delta
        gm = [f for f in _by_key(facts, "gross_margin") if f.raw_value.startswith("5")]
        self.assertTrue(gm)
        self.assertEqual(gm[0].value_kind, "delta")

    def test_increased_to_is_level(self):
        facts = extract_metric_facts(_hit("Gross margin increased to 62.5%."))
        gm = _by_key(facts, "gross_margin")
        self.assertTrue(gm)
        self.assertEqual(gm[0].value_kind, "level")

    def test_revenue_level(self):
        facts = extract_metric_facts(_hit("Total revenue was $10.1 billion."))
        rev = _by_key(facts, "revenue")
        self.assertTrue(rev)
        self.assertEqual(rev[0].value_kind, "level")


class ValueSanityBoundsTest(unittest.TestCase):
    def test_rejects_impossible_gross_margin_level(self):
        # 164.8% is not a gross-margin level -> must be dropped.
        facts = extract_metric_facts(_hit("Gross margin was 164.8% this period."))
        gm_levels = [f for f in _by_key(facts, "gross_margin") if f.value_kind == "level"]
        self.assertEqual(gm_levels, [], gm_levels)

    def test_keeps_plausible_gross_margin_level(self):
        facts = extract_metric_facts(_hit("Gross margin was 62.5%."))
        self.assertTrue([f for f in _by_key(facts, "gross_margin") if f.value_kind == "level"])

    def test_keeps_large_basis_point_delta(self):
        # bps deltas are not level-bounded; 330 bps must survive.
        facts = extract_metric_facts(_hit("Gross margin improved 330 basis points."))
        self.assertTrue(_by_key(facts, "gross_margin"))

    def test_rejects_magnitude_scaled_eps(self):
        # "$10.1 billion" mis-bound to EPS is implausible per-share -> dropped.
        facts = extract_metric_facts(_hit("Diluted earnings per share ... revenue of $10.1 billion."))
        big = [f for f in _by_key(facts, "eps") if (f.value or 0) > 200]
        self.assertEqual(big, [], big)

    def test_keeps_normal_eps(self):
        facts = extract_metric_facts(_hit("Diluted earnings per share were $1.23."))
        self.assertTrue(_by_key(facts, "eps"))


class DetectQueryMetricsTest(unittest.TestCase):
    def test_detects_each(self):
        self.assertEqual(detect_query_metrics("What was free cash flow in 2023?"), ["free_cash_flow"])
        self.assertIn("gross_margin", detect_query_metrics("non-GAAP gross margin?"))
        self.assertIn("eps", detect_query_metrics("diluted earnings per share"))

    def test_empty_for_unrelated(self):
        self.assertEqual(detect_query_metrics("Who is the CEO?"), [])


class PeriodPropagationTest(unittest.TestCase):
    def test_period_metadata_carried(self):
        facts = extract_metric_facts(_hit("Free cash flow was $1.2 billion.", year="2024", quarter="Q1"))
        self.assertTrue(facts)
        self.assertEqual(facts[0].period_year, "2024")
        self.assertEqual(facts[0].period_quarter, "Q1")
        self.assertEqual(facts[0].entity_name, "Acme Corp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
