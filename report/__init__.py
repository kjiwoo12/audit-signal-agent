"""조서 JSON 을 사람이 읽는 문서로 렌더링한다.

계산도 판단도 하지 않는다. 이미 만들어진 조서를 어떻게 **보여줄지**만 정한다.
"""

from .render import render, write

__all__ = ["render", "write"]
