"""agent/ 검증. API 키 없이 전부 돌아간다.

    python -m unittest discover tests -v

LLM 호출은 대본(FakeClient)으로 대체한다. 검증 대상은 모델의 답이 아니라
**그 답을 어떻게 받아 처리하는가**다. 특히 지어낸 근거를 걸러내는지를 본다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from agent import evidence, orchestrator, prompts, skills, toolbox  # noqa: E402


class FakeClient:
    """대본대로 응답하는 클라이언트. 남으면 마지막 응답을 반복한다."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def messages(self, system, messages, tools=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        self.usage["requests"] += 1
        return self.script.pop(0) if self.script else _text("끝")


def _text(t, stop="end_turn"):
    return {"content": [{"type": "text", "text": t}], "stop_reason": stop}


def _use(name, args, uid="tu_1"):
    return {
        "content": [{"type": "tool_use", "id": uid, "name": name, "input": args}],
        "stop_reason": "tool_use",
    }


class TestSkillParsing(unittest.TestCase):
    def test_all_skills_have_seven_sections(self):
        for procedure in tools.PROCEDURES:
            with self.subTest(procedure=procedure):
                s = skills.skill_for(procedure)
                for n in skills.REQUIRED_SECTIONS:
                    self.assertTrue(s.section(n).strip(), f"{s.name} {n}절이 비었다")

    def test_rejection_section_is_not_empty(self):
        """기각 조건이 없는 Skill 은 쓰지 않는다는 것이 이 프로젝트의 전제다."""
        for procedure in tools.PROCEDURES:
            with self.subTest(procedure=procedure):
                self.assertGreater(len(skills.skill_for(procedure).section(5)), 100)

    def test_skill_text_reaches_the_prompt(self):
        """프롬프트에 Skill 본문이 실제로 들어가는지. 복사본이 아니라 파일이 원본이어야 한다."""
        s = skills.skill_for("cutoff")
        system = prompts.system_for("cutoff", s)
        self.assertIn(s.section(5)[:60], system)


class TestToolSchemas(unittest.TestCase):
    def test_schemas_are_wellformed(self):
        for s in toolbox.SCHEMAS:
            with self.subTest(tool=s["name"]):
                self.assertTrue(s["description"])
                sch = s["input_schema"]
                self.assertEqual(sch["type"], "object")
                for req in sch.get("required", []):
                    self.assertIn(req, sch["properties"])
                json.dumps(s)  # API 로 보낼 수 있어야 한다

    def test_finding_requires_rejection_checks(self):
        """기각 조건 검토를 필수로 두지 않으면 에이전트는 그 단계를 건너뛴다."""
        f = next(s for s in toolbox.SCHEMAS if s["name"] == "emit_finding")
        self.assertIn("rejection_checks", f["input_schema"]["required"])
        self.assertIn("evidence", f["input_schema"]["required"])


class TestEvidenceVerification(unittest.TestCase):
    """근거를 지어내면 걸러야 한다. 이 프로젝트에서 가장 중요한 장치다."""

    def test_real_citation_verifies(self):
        gl = tools.loader.load("gl_journal")[0]
        c = evidence.verify_citation(
            {"dataset": "gl_journal", "field": "voucher_no", "value": gl["voucher_no"]}
        )
        self.assertTrue(c["verified"])
        self.assertEqual(c["grade"], evidence.GRADE_B)  # 원문 인용 없음

    def test_fabricated_voucher_is_rejected(self):
        c = evidence.verify_citation(
            {"dataset": "gl_journal", "field": "voucher_no", "value": "JV209912-9999"}
        )
        self.assertFalse(c["verified"])

    def test_fabricated_quote_is_downgraded(self):
        """행은 실재하지만 적요를 지어낸 경우. 이쪽이 더 위험하다."""
        gl = tools.loader.load("gl_journal")[0]
        c = evidence.verify_citation(
            {
                "dataset": "gl_journal",
                "field": "voucher_no",
                "value": gl["voucher_no"],
                "quote": "존재하지 않는 적요 문자열",
            }
        )
        self.assertTrue(c["verified"])
        self.assertEqual(c["grade"], evidence.GRADE_B)
        self.assertIn("인용문이 그 행에 없다", c["note"])

    def test_verbatim_quote_gets_grade_a(self):
        gl = tools.loader.load("gl_journal")[0]
        c = evidence.verify_citation(
            {
                "dataset": "gl_journal",
                "field": "voucher_no",
                "value": gl["voucher_no"],
                "quote": gl["description"],
            }
        )
        self.assertEqual(c["grade"], evidence.GRADE_A)

    def test_finding_without_evidence_is_downgraded(self):
        f = evidence.verify_finding({"finding": "매출 과대계상", "evidence": []})
        self.assertEqual(f["status"], "미확인 가설")

    def test_one_bad_citation_downgrades_the_whole_finding(self):
        gl = tools.loader.load("gl_journal")[0]
        f = evidence.verify_finding(
            {
                "finding": "테스트",
                "evidence": [
                    {"dataset": "gl_journal", "field": "voucher_no", "value": gl["voucher_no"]},
                    {"dataset": "gl_journal", "field": "voucher_no", "value": "JV209912-9999"},
                ],
            }
        )
        self.assertEqual(f["status"], "미확인 가설")
        self.assertIn("JV209912-9999", f["downgrade_reason"])

    def test_feedback_tells_the_agent_what_failed(self):
        """조용히 격하하면 에이전트는 고칠 기회가 없다."""
        f = evidence.verify_finding(
            {
                "finding": "테스트",
                "evidence": [
                    {"dataset": "gl_journal", "field": "voucher_no", "value": "JV209912-9999"}
                ],
            }
        )
        msg = evidence.feedback_for(f)
        self.assertIn("JV209912-9999", msg)
        self.assertIn("미확인 가설", msg)


class TestToolbox(unittest.TestCase):
    def test_run_procedure_returns_calculation(self):
        box = toolbox.Toolbox("costing")
        out, err = box.dispatch("run_procedure", {"procedure": "costing"})
        self.assertFalse(err)
        data = json.loads(out)
        self.assertEqual(data["procedure"], "cost-driver-alignment")

    def test_run_procedure_result_is_reused(self):
        """같은 계산을 두 번 하지 않는다. 두 번 하면 결과가 갈릴 여지가 생긴다."""
        shared = {}
        box = toolbox.Toolbox("costing", precomputed=shared)
        box.dispatch("run_procedure", {"procedure": "costing"})
        self.assertIn("costing", shared)

    def test_lookup_records_filters(self):
        box = toolbox.Toolbox("window")
        out, err = box.dispatch(
            "lookup_records", {"dataset": "gl_journal", "where": {"account_code": "310"}}
        )
        self.assertFalse(err)
        data = json.loads(out)
        self.assertGreater(data["matched"], 0)
        for r in data["rows"]:
            self.assertEqual(r["account_code"], "310")

    def test_unknown_column_is_reported_not_crashed(self):
        box = toolbox.Toolbox("window")
        out, _ = box.dispatch(
            "lookup_records", {"dataset": "gl_journal", "where": {"없는컬럼": "x"}}
        )
        self.assertIn("없는 컬럼", out)

    def test_tool_error_is_returned_to_the_model(self):
        """도구 오류로 대화를 끊지 않는다. 모델이 고쳐 다시 부를 수 있어야 한다."""
        box = toolbox.Toolbox("window")
        out, err = box.dispatch("lookup_records", {"dataset": "없는파일"})
        self.assertIn("모르는 데이터셋", out)
        out, err = box.dispatch("아무도구", {})
        self.assertTrue(err)


class TestOrchestration(unittest.TestCase):
    def test_loop_runs_tools_then_stops(self):
        inv = tools.loader.load("sales_invoices")[0]
        client = FakeClient(
            [
                _use("run_procedure", {"procedure": "cutoff"}),
                _use("lookup_records", {"dataset": "sales_invoices", "limit": 1}, "tu_2"),
                _use(
                    "emit_finding",
                    {
                        "finding": "기간귀속 오류",
                        "risk_grade": "High",
                        "impact_krw": 1_000_000,
                        "evidence": [
                            {
                                "dataset": "sales_invoices",
                                "field": "invoice_no",
                                "value": inv["invoice_no"],
                            }
                        ],
                        "rejection_checks": ["계절성 확인함"],
                        "follow_up": ["인도완료 증빙 대조"],
                        "questions_for_management": ["인도조건 확인"],
                    },
                    "tu_3",
                ),
                _use(
                    "emit_rejection",
                    {"candidate": "12월 매출 급증", "rejection_condition": "계절성", "basis": "2.59배"},
                    "tu_4",
                ),
                _text("조서 서술."),
            ]
        )
        run = orchestrator.run_procedure(client, "cutoff")
        self.assertEqual(run["summary"]["findings"], 1)
        self.assertEqual(run["summary"]["rejections"], 1)
        self.assertEqual(run["stopped"], "완료")
        self.assertEqual(run["narrative"], "조서 서술.")

    def test_turn_limit_is_recorded_not_silent(self):
        """상한에 걸려 잘린 결과를 완료한 것처럼 내놓으면 안 된다."""
        client = FakeClient([_use("run_procedure", {"procedure": "cutoff"})] * 10)
        run = orchestrator.run_procedure(client, "cutoff", max_turns=3)
        self.assertIn("턴 상한", run["stopped"])

    def test_workpaper_separates_hypotheses(self):
        runs = {
            "cutoff": {
                "procedure": "cutoff",
                "skill_title": "t",
                "findings": [
                    {"status": "발견사항", "risk_grade": "Medium", "finding": "a"},
                    {"status": "미확인 가설", "risk_grade": "High", "finding": "b"},
                ],
                "rejections": [],
                "narrative": "",
                "tool_calls": [],
                "turns": 1,
                "stopped": "완료",
                "elapsed_sec": 0.1,
            }
        }
        wp = orchestrator.workpaper(runs)
        self.assertEqual(len(wp["findings"]), 1)
        self.assertEqual(len(wp["unverified_hypotheses"]), 1)

    def test_coherence_receives_prior_judgments(self):
        """6번 절차는 앞선 절차의 계산이 아니라 판단을 받아야 한다."""
        runs = {
            "cutoff": {
                "skill_title": "기간귀속",
                "findings": [{"status": "발견사항", "risk_grade": "High", "finding": "조기인식 9건"}],
                "rejections": [{"candidate": "12월 급증", "rejection_condition": "계절성"}],
            }
        }
        digest = prompts.prior_findings_digest(runs)
        self.assertIn("조기인식 9건", digest)
        self.assertIn("기각", digest)


class TestPromptBoundary(unittest.TestCase):
    """프롬프트가 경계를 실제로 말하고 있는지. 문서에만 있으면 지켜지지 않는다."""

    def test_system_prompt_forbids_arithmetic(self):
        system = prompts.system_for("costing", skills.skill_for("costing"))
        self.assertIn("직접 계산하지 않는다", system)
        self.assertIn("run_procedure", system)

    def test_system_prompt_states_evidence_rule(self):
        system = prompts.system_for("cutoff", skills.skill_for("cutoff"))
        self.assertIn("미확인 가설", system)


if __name__ == "__main__":
    unittest.main(verbosity=2)
