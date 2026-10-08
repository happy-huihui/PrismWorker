
from __future__ import annotations
from pathlib import PurePath

"""评测夹具路径判断

    职责：判断路径是否落在 evals/fixtures 夹具目录下，供 analyzer 豁免检查
        - 夹具里的 SKILL.md 是测试输入，不是真嵌套，不该报 blocker

    对外暴露：
        - is_eval_fixture_path       是否位于夹具目录之下
        - is_eval_fixture_skill_md   是否夹具内的 SKILL.md
"""

_EVAL_FIXTURES = ("evals", "fixtures")


def is_eval_fixture_path(path: PurePath | str) -> bool:
    """判断给定相对路径是否位于评测夹具（evals/fixtures）目录之下。

    用于让 analyzer 判断某个文件是否属于测试夹具，从而豁免对其的部分违规检查。

    例如：
      "evals/fixtures/my-test/SKILL.md"  → True
      "references/guide.md"              → False
    """
    # 相邻两段恰好是 evals / fixtures 才算夹具目录
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
    # 既要位于夹具目录，文件名又必须是 SKILL.md
    return is_eval_fixture_path(path) and PurePath(path).name.lower() == "skill.md"
