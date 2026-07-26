"""scoring/ 검증.

채점기는 두 방향으로 틀릴 수 있다. 맞은 것을 못 알아보거나(만점 불가),
틀린 것을 맞았다고 하거나(후한 채점). 양쪽을 다 본다.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from scoring import score as scorer  # noqa: E402
from scoring.answer_key import LEVEL1_IDS, SIGNALS, SIGNALS_BY_ID  # noqa: E402
from scoring.selftest import perfect_submission  # noqa: E402
from tools.loader import as_int, load  # noqa: E402

MILLION = 1_000_000


class TestAnswerKeyTiesToDataset(unittest.TestCase):
    """정답지 수치가 실제 데이터와 맞는지. 어긋나면 채점 자체가 무의미하다."""

    def test_anchor_values_exist(self):
        """앵커가 실재하지 않으면 아무도 그 항목으로 점수를 받을 수 없다."""
        for s in SIGNALS:
            for a in s.anchors:
                rows = load(a["dataset"])
                present = {str(r.get(a["field"], "")).strip() for r in rows}
                missing = [v for v in a["values"] if v not in present]
                self.assertEqual(
                    missing, [], f"{s.id} 앵커가 {a['dataset']}.{a['field']} 에 없다: {missing}"
                )

    def test_a1_amount(self):
        r = tools.cutoff.analyze()
        self.assertEqual(r["cutoff_candidates"]["amount_krw"], 2_800 * MILLION)
        self.assertIn(2_800 * MILLION, SIGNALS_BY_ID["A1"].amounts)

    def test_a3_amount(self):
        total = sum(
            as_int(p["amount_krw"]) for p in load("purchase_orders") if p["remark"].strip()
        )
        self.assertEqual(total, 1_450 * MILLION)
        self.assertIn(total, SIGNALS_BY_ID["A3"].amounts)

    def test_b2_amount(self):
        r = tools.window.analyze()
        self.assertEqual(r["matched_pairs"][0]["open_amount_krw"], 5_000 * MILLION)

    def test_b3_amount(self):
        total = sum(
            as_int(g["debit_krw"])
            for g in load("gl_journal")
            if g["account_code"] == "115" and "한빛홀딩스" in g["description"]
        )
        self.assertEqual(total, 2_400 * MILLION)

    def test_c1_amount(self):
        r = tools.costing.analyze()
        pc = next(p for p in r["profitability"] if p["product_code"] == "P-C")
        self.assertEqual(pc["oh_delta_krw"], 7_200 * MILLION)


class TestHarnessCanAwardFullMarks(unittest.TestCase):
    """만점이 안 나오는 채점기로는 에이전트가 못 한 것인지
    채점기가 못 알아본 것인지 가릴 수 없다."""

    @classmethod
    def setUpClass(cls):
        cls.result = scorer.score(perfect_submission())

    def test_all_level1_detected(self):
        self.assertEqual(self.result["level1"]["detected"], len(LEVEL1_IDS))

    def test_all_quantified(self):
        self.assertEqual(
            self.result["level2"]["quantified"], self.result["level2"]["of_detected"]
        )

    def test_all_traps_avoided(self):
        for t in self.result["traps"]:
            self.assertEqual(t["outcome"], "회피", f"{t['id']} 회피 실패")

    def test_no_unmatched_or_downgraded(self):
        self.assertEqual(self.result["unmatched_findings"], [])
        self.assertEqual(self.result["unverified_hypotheses"], [])


class TestHarnessIsNotGenerous(unittest.TestCase):
    """후한 채점기는 없는 성적을 만들어낸다."""

    def test_empty_submission_scores_zero(self):
        r = scorer.score({})
        self.assertEqual(r["level1"]["detected"], 0)

    def test_keywords_without_anchor_do_not_count(self):
        """근거 없이 말만 맞은 발견은 탐지로 세지 않는다."""
        runs = {
            "cutoff": {
                "findings": [
                    {
                        "status": "발견사항",
                        "procedure": "cutoff",
                        "finding": "매출 기간귀속에 문제가 있어 보인다",
                        "impact_krw": 2_800 * MILLION,
                        "evidence": [],
                    }
                ],
                "rejections": [],
                "narrative": "",
            }
        }
        self.assertEqual(scorer.score(runs)["level1"]["detected"], 0)

    def test_fabricated_evidence_does_not_count(self):
        """검증에 실패한 인용은 앵커로 인정하지 않는다."""
        runs = {
            "cutoff": {
                "findings": [
                    {
                        "status": "발견사항",
                        "procedure": "cutoff",
                        "finding": "매출 기간귀속 조기인식",
                        "impact_krw": 2_800 * MILLION,
                        "evidence": [
                            {
                                "dataset": "sales_invoices",
                                "field": "invoice_no",
                                "value": "SI2024-00604",
                                "verified": False,
                            }
                        ],
                    }
                ],
                "rejections": [],
                "narrative": "",
            }
        }
        self.assertEqual(scorer.score(runs)["level1"]["detected"], 0)

    def test_downgraded_hypothesis_is_not_a_detection(self):
        runs = {
            "cutoff": {
                "findings": [
                    {
                        "status": "미확인 가설",
                        "procedure": "cutoff",
                        "finding": "매출 기간귀속 조기인식",
                        "impact_krw": 2_800 * MILLION,
                        "evidence": [
                            {
                                "dataset": "sales_invoices",
                                "field": "invoice_no",
                                "value": "SI2024-00604",
                                "verified": True,
                            }
                        ],
                    }
                ],
                "rejections": [],
                "narrative": "",
            }
        }
        r = scorer.score(runs)
        self.assertEqual(r["level1"]["detected"], 0)
        self.assertEqual(len(r["unverified_hypotheses"]), 1)

    def test_trap_reported_as_finding_is_counted_as_false_positive(self):
        runs = {
            "substance": {
                "findings": [
                    {
                        "status": "발견사항",
                        "procedure": "substance",
                        "finding": "정상 거래처의 반품 38건이 과다하다",
                        "evidence": [],
                    }
                ],
                "rejections": [],
                "narrative": "",
            }
        }
        traps = {t["id"]: t["outcome"] for t in scorer.score(runs)["traps"]}
        self.assertEqual(traps["T1"], "오탐")

    def test_level3_4_are_not_scored(self):
        """인과 판정을 규칙으로 하지 않는다는 것이 설계다. 점수 필드가 있으면 안 된다."""
        r = scorer.score(perfect_submission())["level3_4"]
        for key in ("level3", "level4"):
            self.assertNotIn("score", r[key])
            self.assertNotIn("passed", r[key])
        self.assertIn("사람이 읽고", r["warning"])


class TestAnswerKeyDoesNotLeak(unittest.TestCase):
    """정답지가 에이전트 쪽으로 새면 점수는 의미를 잃는다.
    의존 방향을 한쪽으로만 두고, 그 방향을 테스트로 고정한다."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_agent_and_tools_do_not_import_scoring(self):
        for pkg in ("agent", "tools"):
            d = os.path.join(self.ROOT, pkg)
            for fn in os.listdir(d):
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(d, fn), encoding="utf-8") as fh:
                    src = fh.read()
                self.assertNotIn("scoring", src, f"{pkg}/{fn} 이 scoring 을 참조한다")

    def test_agent_prompts_contain_no_answer_key_identifiers(self):
        """정답 항목의 전표번호가 프롬프트에 들어가면 에이전트는 찾은 게 아니라 받은 것이다."""
        from agent import prompts, skills

        leaky = ["SI2024-00604", "PO2024-00147", "2,800", "7,200", "68.9"]
        for procedure in tools.PROCEDURES:
            text = prompts.system_for(procedure, skills.skill_for(procedure))
            for token in leaky:
                self.assertNotIn(token, text, f"{procedure} 프롬프트에 정답 식별자 {token}")


class TestCoverageGapIsReported(unittest.TestCase):
    def test_b3_is_reported_as_uncovered(self):
        """어떤 Skill 도 다루지 않는 정답 항목이 있다는 사실을 감추지 않는다."""
        gaps = [g["id"] for g in scorer.score(perfect_submission())["coverage_gaps"]]
        self.assertIn("B3", gaps)


if __name__ == "__main__":
    unittest.main(verbosity=2)
