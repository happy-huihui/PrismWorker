from __future__ import annotations


"""上传文件管理

    职责：判定网关上传过程中的暂存文件（`.upload-<随机>.part`）
        - list_uploaded_files 扫描上传目录时用它排除半成品
        - 只保留本项目需要的一段判定逻辑

    对外暴露：
        - is_upload_staging_file / UPLOAD_STAGING_PREFIX / UPLOAD_STAGING_SUFFIX
"""

UPLOAD_STAGING_PREFIX = ".upload-"

UPLOAD_STAGING_SUFFIX = ".part"


def is_upload_staging_file(filename: str) -> bool:
    """判断一个文件名是否是"未完成的网关上传暂存文件"。

    暂存文件形如 `.upload-<随机串>.part`，以 UPLOAD_STAGING_PREFIX 开头、
    以 UPLOAD_STAGING_SUFFIX 结尾。

    1. 用 startswith 检查是否带暂存前缀
    2. 再用 endswith 检查是否带暂存后缀
    3. 两者都满足才判定是真暂存文件
    """
    # 前缀 + 后缀都命中才算暂存文件（只看一个容易误伤正式文件）
    return filename.startswith(UPLOAD_STAGING_PREFIX) and filename.endswith(UPLOAD_STAGING_SUFFIX)
