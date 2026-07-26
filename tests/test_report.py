"""조서 렌더러 검증.

렌더러는 조서의 내용을 만들지 않는다. 그래서 검증할 것은 "예쁘게 나오는가"가
아니라 다음 네 가지다.

1. **보태지 않는다.** 조서에 없는 판단·결론을 화면에 만들어내지 않는다.
   렌더러가 요약을 지어내면 화면과 채점 대상이 갈라진다.
2. **감추지 않는다.** 기각 0건, 서술 없음, 근거등급 B 를 공백으로 처리하지 않고
   그 사실을 문장으로 적는다. 조서에서 빈칸은 "검토 안 함"과 "해당 없음"을
   구별해 주지 못한다.
3. **혼자서 열린다.** 외부 스크립트·스타일·이미지를 참조하지 않는다. 첨부해서
   보내거나 5년 뒤에 열어도 같은 문서여야 한다.
4. **원천까지 닿는다.** 드릴다운의 마지막 층에 실제 CSV 행 값이 있어야 한다.
   여기서 끊기면 "신뢰성 있게 설명한다"는 말이 성립하지 않는다.
"""

from __future__ import annotations

import copy
import html as html_mod
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report  # noqa: E402
from agent import baseline, orchestrator  # noqa: E402

_CACHE: dict = {}


def esc(s: str) -> str:
    """렌더러와 같은 방식으로 이스케이프한다. 원문 그대로 찾으면 오탐이 난다."""
    return html_mod.escape(s, quote=True)


def workpaper() -> dict:
    """대조군 조서. 결정론적이므로 한 번 만들어 재사용한다."""
    if "wp" not in _CACHE:
        wp = orchestrator.workpaper(baseline.run_all())
        wp["engine"] = "규칙 기반 대조군 (LLM 미사용)"
        wp["thresholds"] = dict(baseline.THRESHOLDS)
        _CACHE["wp"] = wp
    return copy.deepcopy(_CACHE["wp"])


def html() -> str:
    if "html" not in _CACHE:
        _CACHE["html"] = report.render(workpaper())
    return _CACHE["html"]


class TestSelfContained(unittest.TestCase):
    """첨부해서 보낼 수 있어야 조서다."""

    def test_no_external_references(self):
        doc = html()
        for pattern in ("<script", "<link", "<img", "<iframe", "http://", "https://"):
            self.assertNotIn(pattern, doc, f"외부 참조 {pattern} 가 들어 있다")

    def test_is_a_complete_document(self):
        doc = html()
        self.assertTrue(doc.startswith("<!doctype html>"))
        self.assertIn("<style>", doc)
        self.assertIn('<meta charset=utf-8>', doc)
        self.assertIn("</html>", doc)

    def test_output_is_deterministic(self):
        """같은 조서를 넣으면 같은 바이트가 나와야 커밋해 둘 수 있다."""
        self.assertEqual(report.render(workpaper()), report.render(workpaper()))

    def test_escapes_injected_markup(self):
        """조서 본문은 데이터다. 태그로 해석되면 문서가 깨진다."""
        wp = workpaper()
        wp["findings"][0]["finding"] = '<script>alert(1)</script> & "인용" 검토'
        doc = report.render(wp)
        self.assertNotIn("<script>alert", doc)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", doc)
        self.assertIn("&amp;", doc)


class TestNothingIsAdded(unittest.TestCase):
    """렌더러는 조서에 없는 판단을 만들지 않는다."""

    def test_every_finding_text_appears(self):
        doc = html()
        for f in workpaper()["findings"]:
            self.assertIn(esc(f["finding"][:60]), doc)

    def test_every_finding_names_its_procedure_and_rule(self):
        """규칙 본문에 `<=` 가 있으므로 이스케이프된 형태로 들어 있어야 맞다."""
        doc = html()
        for f in workpaper()["findings"]:
            self.assertIn(f["procedure"], doc)
            self.assertIn(esc(f["rule"][:30]), doc)

    def test_impact_total_is_labelled_as_unadjusted(self):
        """절차 간 중복을 조정하지 않은 합계를 그냥 '합계'로 내놓으면 오해를 만든다."""
        doc = html()
        self.assertIn("단순 합계", doc)
        self.assertIn("중복", doc)

    def test_no_conclusion_is_invented_when_narrative_is_empty(self):
        """대조군의 서술은 빈 문자열이다. 렌더러가 대신 결론을 써 주면 안 된다."""
        wp = workpaper()
        self.assertEqual(set(v.strip() for v in wp["narratives"].values()), {""})
        doc = report.render(wp)
        self.assertIn("서술을 생성하지 않는다", doc)
        self.assertNotIn("<div class=narrative>", doc)


class TestNothingIsHidden(unittest.TestCase):
    """비어 있는 항목을 공백으로 처리하지 않는다."""

    def test_zero_rejections_is_explained_not_blank(self):
        wp = workpaper()
        self.assertEqual(wp["rejected_candidates"], [])
        doc = report.render(wp)
        self.assertIn("정상 판정을 기록하지 않았다", doc)

    def test_empty_rejection_checks_are_explained(self):
        doc = html()
        self.assertIn("기각 조건을 검토하지 않는다", doc)

    def test_evidence_grade_is_shown_for_every_finding(self):
        doc = html()
        grades = {f["evidence_grade"] for f in workpaper()["findings"]}
        for g in grades:
            self.assertIn(f"근거 {g}", doc)
        self.assertIn("이 조서를 읽는 방법", doc)

    def test_downgraded_findings_get_their_own_section(self):
        """미확인 가설이 발견사항과 같은 표에 섞이면 격하가 무의미해진다."""
        wp = workpaper()
        wp["unverified_hypotheses"] = [
            {
                "finding": "기말 단기차입 후 익년 즉시 상환",
                "downgrade_reason": "원천 데이터에 없는 행이다",
                "evidence": [
                    {
                        "dataset": "gl_journal",
                        "field": "voucher_no",
                        "value": "JV202412-9999",
                        "verified": False,
                        "grade": "C",
                        "row": {},
                    }
                ],
            }
        ]
        doc = report.render(wp)
        self.assertIn("미확인 가설 1건", doc)
        self.assertIn("원천 데이터에 없는 행이다", doc)
        self.assertIn("발견사항이 아니다", doc)
        # 요약표에는 들어가지 않는다
        summary = doc.split("발견사항 요약")[1].split("발견사항 상세")[0]
        self.assertNotIn("JV202412-9999", summary)


class TestDrilldownReachesSource(unittest.TestCase):
    """마지막 층에 원천 CSV 행이 있어야 한다."""

    def test_source_row_values_are_rendered(self):
        doc = html()
        checked = 0
        for f in workpaper()["findings"]:
            for ev in f["evidence"]:
                row = ev.get("row") or {}
                if not row:
                    continue
                self.assertIn(str(ev["value"]), doc)
                for k, v in list(row.items())[:3]:
                    self.assertIn(str(k), doc)
                    self.assertIn(str(v), doc)
                checked += 1
        self.assertGreater(checked, 0, "원천 행이 붙은 근거가 하나도 없다")

    def test_drilldown_is_collapsed_by_default(self):
        """펼쳐 놓으면 아무도 1층을 읽지 않는다."""
        doc = html()
        self.assertIn("<details>", doc)
        self.assertNotIn("<details open", doc)

    def test_one_details_block_per_finding(self):
        doc = html()
        self.assertEqual(doc.count("<details>"), len(workpaper()["findings"]))


class TestTitle(unittest.TestCase):
    def test_long_entity_is_split_into_name_and_detail(self):
        doc = html()
        title = re.search(r"<title>(.*?)</title>", doc).group(1)
        self.assertIn("감사조서", title)
        self.assertLess(len(title), 40, f"제목이 너무 길다: {title}")
        # 업종·보고기간은 버리지 않고 머리글로 옮긴다
        self.assertIn("FY2024", doc)


if __name__ == "__main__":
    unittest.main()
