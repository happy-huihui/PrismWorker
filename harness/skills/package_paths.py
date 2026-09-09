
from __future__ import annotations
from pathlib import PurePath

"""评测夹具（fixture）路径判断工具。

技能包可以携带 evals/ 评测目录，其中 evals/fixtures/ 存放测试数（非法的 SKILL.md、恶意脚本等）

analyzer 在检查包内是否存在嵌套 SKILL.md 这一 blocker 规则时，
必须豁免夹具目录里的 SKILL.md，否则会把正常技能误判为失败。
本模块就是给 analyzer 提供这条豁免判断的依据。
"""

_EVAL_FIXTURES = ("evals", "fixtures")


def is_eval_fixture_path(path: PurePath | str) -> bool:
    """判断给定相对路径是否位于评测夹具（evals/fixtures）目录之下。

    用于让 analyzer 判断某个文件是否属于测试夹具，从而豁免对其的部分违规检查。

    例如：
      "evals/fixtures/my-test/SKILL.md"  → True
      "references/guide.md"              → False
    """
    segments = tuple(PurePath(path).parts)
    return any(
        segments[i] == _EVAL_FIXTURES[0] and segments[i + 1] == _EVAL_FIXTURES[1]
        for i in range(len(segments) - 1)
    )


def is_eval_fixture_skill_md(path: PurePath | str) -> bool:
    """判断路径是否指向夹具目录内的某个 SKILL.md。

    analyzer 用它豁免“嵌套 SKILL.md”违规检查：夹具里的 SKILL.md
    只是测试输入，不是真正的技能文件，不应报 blocker。

    例如：
      "evals/fixtures/my-test/SKILL.md"  → True
      "SKILL.md"                          → False（根目录主文件，需正常检查）
      "references/sub/SKILL.md"           → False（真嵌套，需报错）
    """
    return is_eval_fixture_path(path) and PurePath(path).name.lower() == "skill.md"
