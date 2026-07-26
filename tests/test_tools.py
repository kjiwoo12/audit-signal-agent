"""tools/ 검증. 표준 라이브러리만 사용한다.

    python -m unittest discover tests -v

검증 대상은 세 가지다.
  (1) 산술이 맞는가 — 정합성(tie-out)
  (2) 경계가 지켜지는가 — 계산 도구가 판단을 내놓지 않는가
  (3) 회귀 — 한 번 고친 오류가 다시 들어오지 않는가
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import PARALLEL, PROCEDURES, coherence, costing, cutoff  # noqa: E402
from tools import divergence, substance, window  # noqa: E402
from tools.loader import (  # noqa: E402
    as_int,
    biz_days_between,
    load,
    shift_biz_days,
    split_exact,
)


class TestDatasetTieOut(unittest.TestCase):
    """도구를 신뢰하려면 원천 데이터부터 맞아야 한다."""

    def test_gl_balances(self):
        gl = load("gl_journal")
        debit = sum(as_int(r["debit_krw"]) for r in gl)
        credit = sum(as_int(r["credit_krw"]) for r in gl)
        self.assertEqual(debit, credit, "총계정원장 차변·대변 불일치")

    def test_sales_system_ties_to_gl(self):
        si = sum(as_int(r["amount_krw"]) for r in load("sales_invoices"))
        gl = sum(
            as_int(r["credit_krw"])
            for r in load("gl_journal")
            if r["account_code"] == "501"
        )
        self.assertEqual(si, gl, "판매시스템 매출과 GL 매출계정 불일치")

    def test_every_invoice_has_shipment(self):
        inv = {r["invoice_no"] for r in load("sales_invoices")}
        shp = {r["invoice_no"] for r in load("shipments")}
        self.assertEqual(inv - shp, set(), "출고 기록이 없는 송장이 있다")


class TestArithmetic(unittest.TestCase):
    def test_split_exact_preserves_total(self):
        for total, weights in [
            (15_000_000_000, [7560, 3920, 1080]),
            (1, [1, 1, 1]),
            (100, [1, 0, 0]),
            (999_999_999, [33, 33, 34]),
        ]:
            with self.subTest(total=total):
                out = split_exact(total, weights)
                self.assertEqual(sum(out), total)
                self.assertEqual(len(out), len(weights))

    def test_split_exact_rejects_zero_weights(self):
        with self.assertRaises(ValueError):
            split_exact(100, [0, 0])

    def test_business_days(self):
        # 2024-12-31 은 화요일, 2025-01-05 는 일요일
        self.assertEqual(shift_biz_days(date(2024, 12, 31), 1), date(2025, 1, 1))
        self.assertEqual(shift_biz_days(date(2024, 12, 27), -1), date(2024, 12, 26))
        # 금요일 → 다음 월요일은 1영업일
        self.assertEqual(biz_days_between(date(2024, 12, 27), date(2024, 12, 30)), 1)
        self.assertEqual(biz_days_between(date(2024, 12, 30), date(2024, 12, 27)), -1)
        self.assertEqual(biz_days_between(date(2024, 12, 27), date(2024, 12, 27)), 0)


class TestCosting(unittest.TestCase):
    """배부 합계가 1원이라도 어긋나면 그 조서는 쓸 수 없다."""

    @classmethod
    def setUpClass(cls):
        cls.r = costing.analyze()

    def test_reallocation_ties_out(self):
        oh_total = self.r["overhead"]["total_krw"]
        allocated = sum(
            sum(a["allocated_krw"] for a in act["allocation"].values())
            for act in self.r["activity_based_reallocation"]
        )
        self.assertEqual(allocated, oh_total)

    def test_total_gross_profit_unchanged(self):
        """배부기준을 바꿔도 총 매출총이익은 변하지 않아야 한다."""
        cur = sum(p["current_gp_krw"] for p in self.r["profitability"])
        abc = sum(p["abc_gp_krw"] for p in self.r["profitability"])
        self.assertEqual(cur, abc)
        self.assertEqual(self.r["financial_statement_impact"]["amount_krw"], 0)

    def test_current_basis_proportionality_verified(self):
        self.assertTrue(self.r["current_basis"]["proportionality_verified"])

    def test_oh_delta_sums_to_zero(self):
        self.assertEqual(sum(p["oh_delta_krw"] for p in self.r["profitability"]), 0)


class TestCutoff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = cutoff.analyze()

    def test_all_invoices_joined(self):
        c = self.r["coverage"]
        self.assertEqual(c["joined"], c["invoices"])
        self.assertEqual(c["unknown_incoterms"], [])

    def test_candidates_are_strictly_period_crossing(self):
        pe = date.fromisoformat(self.r["parameters"]["period_end"])
        for it in self.r["cutoff_candidates"]["items"]:
            self.assertLessEqual(date.fromisoformat(it["revenue_date"]), pe)
            self.assertGreater(date.fromisoformat(it["control_date"]), pe)

    def test_control_date_follows_incoterms(self):
        rule = self.r["parameters"]["control_date_rule"]
        for it in self.r["cutoff_candidates"]["items"]:
            self.assertEqual(it["control_basis"], rule[it["incoterms"]])

    def test_counter_facts_present(self):
        """오탐 함정을 기각하려면 반증자료가 계산 결과에 있어야 한다."""
        cf = self.r["counter_facts"]
        self.assertIn("ship_before_invoice", cf)
        self.assertIn("period_end_month_revenue", cf)


class TestSubstance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = substance.analyze()

    def test_related_party_match_is_not_promiscuous(self):
        """회귀: 느슨한 부분일치로 전 거래처가 특수관계자로 잡히던 오류."""
        matched = [
            c["customer_code"] for c in self.r["by_customer"] if c["related_parties"]
        ]
        self.assertEqual(matched, ["C801"], f"특수관계자 오매칭: {matched}")

    def test_return_rates_sum_consistently(self):
        total = sum(c["returned_krw"] for c in self.r["by_customer"])
        self.assertEqual(total, self.r["overall"]["returned_krw"])

    def test_return_reasons_are_verbatim(self):
        """반품 사유는 집계하지 않고 원문을 그대로 넘겨야 한다.
        '품질하자'와 '미판매'의 구별이 이 절차의 결정적 신호이기 때문이다."""
        reasons = set(self.r["return_reasons"])
        source = {r["reason"] for r in load("credit_notes")}
        self.assertEqual(reasons, source)


class TestWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = window.analyze()

    def test_matched_pairs_are_equal_and_opposite(self):
        for p in self.r["matched_pairs"]:
            self.assertEqual(p["open_amount_krw"], p["close_amount_krw"])
            self.assertLess(p["open_date"], p["close_date"])

    def test_mid_period_borrowings_excluded_from_pairs(self):
        """회귀: 이자 지급과 기중 차입이 반증자료에 섞여 계수를 부풀리던 오류."""
        for m in self.r["counter_facts"]["mid_period_borrowings"]:
            self.assertGreater(m["deposit_krw"], 0)
            self.assertNotIn("이자", m["description"])

    def test_no_profit_and_loss_effect(self):
        self.assertIn("손익 영향 없음", self.r["balance_sheet_effect"]["note"])


class TestDivergence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = divergence.analyze()

    def test_unavailable_is_declared_not_estimated(self):
        """없는 데이터는 추정하지 않고 없다고 말해야 한다."""
        self.assertTrue(self.r["unavailable"])
        for u in self.r["unavailable"]:
            self.assertIn("reason", u)

    def test_working_capital_attribution_signs(self):
        for w in self.r["working_capital_attribution"]["rows"]:
            expected = -w["delta_krw"] if w["side"] == "자산" else w["delta_krw"]
            self.assertEqual(w["cash_effect_krw"], expected)


class TestCoherence(unittest.TestCase):
    def test_no_rank_judgment_without_identified_investment(self):
        """식별된 투입액이 없으면 정합성 판단을 하지 않아야 한다.
        0원끼리 순위를 매기면 없는 불일치가 만들어진다."""
        r = coherence.analyze(costing_result=costing.analyze())
        for row in r["resource_allocation"]:
            if row["total_identified_investment_krw"] == 0:
                self.assertIsNone(row["investment_rank"])
                self.assertIsNone(row["rank_misaligned"])

    def test_capitalizable_items_cite_source(self):
        r = coherence.analyze()
        for it in r["capitalizable_expensed"]["items"]:
            self.assertTrue(it["po_no"])
            self.assertTrue(it["remark"])


class TestPythonLLMBoundary(unittest.TestCase):
    """이 프로젝트의 핵심 설계는 3절(계산)과 4·5절(판단)의 경계다.
    계산 도구가 판단 결과를 내놓기 시작하면 그 경계는 무너진다."""

    FORBIDDEN = ("발견사항", "위험등급", "risk_grade", "finding", "conclusion", "결론")

    @classmethod
    def setUpClass(cls):
        cls.results = {n: PROCEDURES[n].analyze() for n in PARALLEL}
        cls.results["coherence"] = coherence.analyze(
            costing_result=cls.results["costing"]
        )

    def _keys(self, obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from self._keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from self._keys(v)

    def test_no_judgment_keys(self):
        for name, res in self.results.items():
            for k in self._keys(res):
                for bad in self.FORBIDDEN:
                    self.assertNotIn(
                        bad,
                        str(k).lower() if bad.isascii() else str(k),
                        f"{name} 의 출력 키 '{k}' 가 판단을 담고 있다",
                    )

    def test_all_results_json_serializable(self):
        """에이전트에 넘기려면 직렬화되어야 한다."""
        for name, res in self.results.items():
            with self.subTest(procedure=name):
                json.dumps(res, ensure_ascii=False)

    def test_every_procedure_declares_its_skill(self):
        for name, res in self.results.items():
            self.assertIn("procedure", res)
            skill = os.path.join("skills", res["procedure"] + ".md")
            self.assertTrue(
                os.path.exists(
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), skill)
                ),
                f"{name} 이 선언한 Skill 파일이 없다: {skill}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
