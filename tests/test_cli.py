"""명령줄 진입점 검증.

여기 있는 테스트는 전부 CI 가 잡은 버그에서 나왔다.

단위 테스트는 함수를 직접 부른다. 그래서 `main()` 안에서만 벌어지는 일 —
인자 파싱 — 은 테스트를 다 통과하고도 깨질 수 있었다. README 에 적어 둔
네 개의 명령 중 두 개가 Python 3.12 미만에서 실행조차 되지 않았고,
그 사실을 12칸짜리 CI 매트릭스를 켜고 나서야 알았다.

읽는 사람이 제일 먼저 치는 명령이 안 되는 것은 기능이 하나 빠진 것보다 나쁘다.
그래서 진입점을 테스트로 고정한다.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import baseline, run  # noqa: E402


@contextlib.contextmanager
def quiet():
    """진입점은 조서를 통째로 찍는다. 테스트 출력에 섞이지 않게 삼킨다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


class TestNoArguments(unittest.TestCase):
    """인자 없이 부를 수 있어야 한다.

    위치인자에 `nargs="*"` 와 `choices=` 를 함께 주면, 인자를 생략했을 때
    기본값인 빈 리스트가 choices 검사에 걸린다. argparse 는
    `invalid choice: []` 로 죽고 종료코드 2 를 낸다. 3.12 에서 고쳐진
    동작이라 최신 버전에서는 재현되지 않는다 — 그래서 더 늦게 발견된다.
    """

    def test_baseline_runs_with_no_arguments(self):
        with quiet() as out:
            self.assertEqual(baseline.main([]), 0)
        self.assertIn("규칙 기반 대조군", out.getvalue())

    def test_dry_run_needs_no_positional_arguments(self):
        with quiet() as out:
            self.assertEqual(run.main(["--dry-run"]), 0)
        self.assertIn("DRY RUN", out.getvalue())


class TestProcedureSelection(unittest.TestCase):
    """choices= 를 뗀 자리를 직접 검사가 대신한다.

    argparse 에 검사를 맡기지 않기로 한 이상, 검사가 실제로 있는지를
    테스트가 확인해야 한다. 없어도 아무도 모르는 종류의 코드다.
    """

    def test_named_procedure_runs_alone(self):
        with quiet() as out:
            self.assertEqual(baseline.main(["cutoff"]), 0)
        text = out.getvalue()
        self.assertIn("cutoff", text)
        self.assertNotIn("costing", text)

    def test_unknown_procedure_is_rejected(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(
            io.StringIO()
        ) as err:
            baseline.main(["bogus"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("bogus", err.getvalue())

    def test_rejection_message_lists_what_is_allowed(self):
        """틀렸다고만 하고 무엇이 맞는지 안 알려주는 오류는 절반만 오류다."""
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(
            io.StringIO()
        ) as err:
            baseline.main(["bogus"])
        for procedure in run.ALL:
            self.assertIn(procedure, err.getvalue())


if __name__ == "__main__":
    unittest.main()
