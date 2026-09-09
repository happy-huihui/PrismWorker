from __future__ import annotations

import hashlib

""" 
计算整个技能包的 SHA256 指纹（package_digest）。
    - 用途：技能扫描器判断“技能包有没有变”。
    - 同一技能包放到任何路径，算出的指纹都必须一致，才能跨机器比对
        - 所以不能直接对目录或zip 做哈希，而是从快照各文件的元数据 kind/path/size/sha256 汇总计算。
"""


def compute_package_digest(snapshot: dict) -> str:
    """计算技能包的整体 SHA256 指纹。

    snapshot = {
        "files": [
            {"kind": "file", "path": "SKILL.md", "size": 1200, "sha256": "a1b2c3..."},
            {"kind": "file", "path": "references/template.pptx", "size": 4096, "sha256": "x9y8z7..."},
            {"kind": "dir", "path": "references", "size": 0, "sha256": ""}
        ]
    }

    示例：同一技能的两次扫描 digest 相同 → 未变化；不同 → 需更新。
    """

    records = []
    for ent in snapshot.get("files", []):
        kind = str(ent.get("kind", ""))
        path = str(ent.get("path", ""))
        size = str(ent.get("size", 0))
        content_digest = str(ent.get("sha256", ""))
        records.append(f"{kind}\x00{path}\x00{size}\x00{content_digest}")

    h = hashlib.sha256()
    for record in sorted(records):
        h.update(len(record).to_bytes(8, "big"))
        h.update(record.encode("utf-8"))

    return f"sha256:{h.hexdigest()}"
