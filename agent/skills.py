"""Skill 마크다운을 읽어 절 단위로 나눈다.

Skill 은 프롬프트의 원본이다. 프롬프트 문자열을 코드에 복사해 두면
skills/*.md 를 고쳐도 에이전트 동작이 안 바뀌는 상태가 되고, 그때부터
문서와 실제가 갈라진다. 그래서 파일을 읽어서 그대로 넣는다.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(ROOT, "skills")

SECTION_RE = re.compile(r"^##\s*(\d)\.\s*(.+?)\s*$", re.M)

# 모든 Skill 이 갖춰야 하는 절. 하나라도 없으면 절차로 쓰지 않는다.
REQUIRED_SECTIONS = (1, 2, 3, 4, 5, 6, 7)


class Skill:
    def __init__(self, name: str, meta: dict, sections: dict, text: str):
        self.name = name
        self.meta = meta
        self.sections = sections  # {번호: {"title":…, "body":…}}
        self.text = text

    @property
    def title(self) -> str:
        return self.meta.get("절차명", self.name)

    @property
    def purpose(self) -> str:
        return self.meta.get("목적", "")

    def section(self, n: int) -> str:
        s = self.sections.get(n)
        return "" if s is None else s["body"]

    def body(self) -> str:
        """프론트매터를 뺀 본문. 프롬프트에 그대로 들어간다."""
        return self.text

    def __repr__(self) -> str:
        return f"<Skill {self.name}>"


def _parse_frontmatter(raw: str):
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    head = raw[3:end]
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, raw[end + 4 :].lstrip("\n")


def load_skill(name: str) -> Skill:
    path = os.path.join(SKILL_DIR, name + ".md")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Skill 파일이 없다: {path}")
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()

    meta, text = _parse_frontmatter(raw)

    marks = list(SECTION_RE.finditer(text))
    sections = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections[int(m.group(1))] = {
            "title": m.group(2),
            "body": text[m.end() : end].strip(),
        }

    missing = [n for n in REQUIRED_SECTIONS if n not in sections]
    if missing:
        raise ValueError(f"{name}: 절이 빠졌다 {missing}. 7개 절을 모두 갖춰야 한다.")

    return Skill(name, meta, sections, text.strip())


def skill_for(procedure: str) -> Skill:
    from tools import SKILL_OF

    if procedure not in SKILL_OF:
        raise KeyError(f"모르는 절차: {procedure}")
    return load_skill(SKILL_OF[procedure])


def all_skills() -> dict:
    from tools import SKILL_OF

    return {p: load_skill(s) for p, s in SKILL_OF.items()}
