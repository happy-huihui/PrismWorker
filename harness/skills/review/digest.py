from __future__ import annotations

import hashlib

"""技能包指纹

    职责：从快照各文件的 kind / path / size / sha256 汇总算出整包 SHA256 指纹
        - 同一技能包换路径后指纹必须一致，故不能直接哈希目录或 zip

    对外暴露：
        - compute_package_digest
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
        # 1.每个文件摊成一条记录；字段间用 \0 分隔，防拼接串味
        records.append(f"{kind}\x00{path}\x00{size}\x00{content_digest}")

    # 2.先排序再喂：保证指纹与文件枚举顺序无关
    h = hashlib.sha256()
    for record in sorted(records):
        # 3.写入长度前缀，避免不同记录拼接后撞出同字节串
        h.update(len(record).to_bytes(8, "big"))
        h.update(record.encode("utf-8"))

    # 4.带算法前缀返回，将来换算法可区分
    return f"sha256:{h.hexdigest()}"
