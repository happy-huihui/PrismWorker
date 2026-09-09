from __future__ import annotations


"""
    上传管理 —— 判定"暂存文件"。

    网关侧在上传过程中会先生成一个带标记的临时文件（暂存文件），等上传完成
    再落盘成正式文件。list_uploaded_files 扫描上传目录时，必须能识别并排除这些
    半成品，避免把还没传完的文件当正式历史文件返回给 Agent。

    这里只保留最需要的一段判定逻辑（is_upload_staging_file + 两个前缀/后缀常量），
    DeerFlow 的 uploads/manager 还有几十个上传管理函数，本项目暂不需要，不展开。
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
    return filename.startswith(UPLOAD_STAGING_PREFIX) and filename.endswith(UPLOAD_STAGING_SUFFIX)