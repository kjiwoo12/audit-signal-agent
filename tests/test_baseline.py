"""규칙 기반 대조군 검증.

대조군은 두 가지를 동시에 만족해야 쓸 수 있다.

1. **실제로 돌아간다.** LLM 없이, 네트워크 없이, 근거 검증을 통과한 조서를 낸다.
   여기서 실패하면 대조군이 아니라 그냥 안 되는 코드다.
2. **약점이 그대로 남아 있다.** 기각하지 않고 서술하지 않는다는 것이 설계다.
   나중에 누가 "점수가 낮으니 규칙을 보강하자"며 이 성질을 지우면 대조가 무너진다.
   그래서 없음을 테스트로 고정한다.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from agent import baseline  # noqa: E402
from scoring import score as scorer  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CACHE: dict = {}


def runs():
    """대조군은 결정론적이다. 한 번 돌려 재사용한다."""
    if "runs" not in _CACHE:
        _CACHE["runs"] = baseline.run_all()
    return _CACHE["runs"]


class TestBaselineRuns(unittest.TestCase):
    """LLM 없이 조서가 나오는지."""

    @classmethod
    def setUpClass(cls):
        cls.runs = runs()

    def test_every_procedure_ran(self):
        self.assertEqual(set(self.runs), set(tools.PARALLEL + [tools.FINAL]))

    def test_findings_exist(self):
        total = sum(len(r["findings"]) for r in self.runs.values())
        self.assertGreater(total, 0)

    def test_all_findings_pass_evidence_verification(self):
        """대조군도 같은 검증 관문을 통과해야 한다. 규칙이라고 면제되지 않는다."""
        for name, run in self.runs.items():
            for f in run["findings"]:
                self.assertEqual(
                    f["status"],
                    "발견사항",
                    f"{name}: 근거 대조 실패 — {f.get('downgrade_reason')}",
                )

    def test_judgment_path_touches_no_api(self):
        """대조군이 몰래 API 를 부르면 '키 없이 돌아간다'는 주장이 무너진다.
        판정 로직이 있는 baseline.py 자체가 클라이언트를 참조하지 않아야 한다.
        (CLI 출력만 run.py 를 재사용하며, 그 경로도 호출은 하지 않는다.)"""
        with open(os.path.join(ROOT, "agent", "baseline.py"), encoding="utf-8") as fh:
            src = fh.read()
        for token in ("client", "urllib", "api_key", "ANTHROPIC"):
            self.assertNotIn(token, src, f"baseline.py 가 {token} 을 참조한다")

    def test_every_finding_names_the_rule_that_fired(self):
        """왜 그 건이 걸렸는지 설명할 수 없는 판정은 조서에 쓸 수 없다."""
        for run in self.runs.values():
            for f in run["findings"]:
                self.assertTrue(f.get("rule"), f"규칙명이 없는 발견: {f['finding'][:40]}")


class TestBaselineWeaknessesArePreserved(unittest.TestCase):
    """규칙 기반의 한계가 지워지지 않았는지. 지워지면 LLM 과의 대조가 무의미해진다."""

    @classmethod
    def setUpClass(cls):
        cls.runs = runs()

    def test_no_rejections_recorded(self):
        """임계값 아래로 걸러진 것과 검토 후 기각한 것은 다르다.
        규칙은 후자를 하지 못하고, 조서에 아무 기록도 남기지 않는다."""
        for run in self.runs.values():
            self.assertEqual(run["rejections"], [])
            for f in run["findings"]:
                self.assertEqual(f["rejection_checks"], [])

    def test_no_narrative(self):
        """개별 항목 나열은 되지만 인과로 엮는 문장은 규칙으로 만들 수 없다."""
        for run in self.runs.values():
            self.assertEqual(run["narrative"], "")

    def test_no_trap_is_explicitly_rejected(self):
        """함정을 안 밟은 것과 알아보고 기각한 것은 다르다.
        대조군은 전자에 머문다 — 전부 '미언급' 이어야 한다."""
        result = scorer.score(self.runs)
        outcomes = {t["id"]: t["outcome"] for t in result["traps"]}
        self.assertNotIn("오탐", outcomes.values())
        self.assertNotIn("회피", outcomes.values())


class TestLevel34ProxyIsNotEvidenceOfReasoning(unittest.TestCase):
    """이 저장소에서 가장 중요한 음성 결과다.

    대조군의 서술은 완전히 비어 있다. 인과 논증이 한 줄도 없다. 그런데도 Level 3·4
    대리지표는 토큰군이 전부 출현했다고 표시한다 — 발견사항에 적힌 수치와 화살표
    때문이다.

    즉 대리지표는 인과 추론의 증거가 되지 못한다. Level 3·4 를 규칙으로 채점하지
    않기로 한 결정이 옳았다는 것을 실측으로 보여주는 자리이므로, 이 상태를
    테스트로 고정한다. 이 테스트가 깨진다면 대리지표에 점수를 붙일 수 있다는 뜻이
    아니라, 대조군이 서술을 하게 되었다는 뜻이다.
    """

    @classmethod
    def setUpClass(cls):
        cls.result = scorer.score(runs())

    def test_narrative_is_empty(self):
        for run in runs().values():
            self.assertEqual(run["narrative"], "")

    def test_proxy_lights_up_anyway(self):
        l34 = self.result["level3_4"]
        self.assertTrue(
            l34["level3"]["proxy"]["all_groups_present"]
            or l34["level4"]["proxy"]["all_groups_present"],
            "대리지표가 켜지지 않았다면 이 테스트가 보여주려는 현상이 사라진 것이다",
        )

    def test_proxy_carries_no_score(self):
        """켜졌다는 사실이 점수로 환산되지 않아야 한다."""
        for key in ("level3", "level4"):
            self.assertNotIn("score", self.result["level3_4"][key])
            self.assertNotIn("passed", self.result["level3_4"][key])


class TestBaselineScoreIsRecorded(unittest.TestCase):
    """README 가 적어 둔 대조군 성적이 실제 실행과 일치하는지.
    문서의 숫자와 코드의 숫자가 갈리면 문서를 믿을 수 없게 된다."""

    def test_level1_six_of_seven(self):
        r = scorer.score(runs())["level1"]
        self.assertEqual((r["detected"], r["total"]), (6, 7))

    def test_b3_is_the_miss(self):
        """놓친 것이 절차 공백(B3)인지 확인한다.
        에이전트의 실패와 절차 설계의 공백을 구별해야 한다."""
        rows = {r["id"]: r["detected"] for r in scorer.score(runs())["level1"]["rows"]}
        self.assertFalse(rows["B3"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
