from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, NotRequired, TypedDict, cast

from langchain.agents import AgentState
from langchain_core.messages import (
    AnyMessage,
    BaseMessageChunk,
    RemoveMessage,
    convert_to_messages,
    message_chunk_to_message,
)
from langgraph.channels import DeltaChannel
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from harness.agents.goal_state import GoalState
from harness.config.database_config import DEFAULT_CHECKPOINT_SNAPSHOT_FREQUENCY
from harness.subagents.status_contract import SUBAGENT_STATUS_VALUES


"""线程会话状态

    职责：定义会话状态的全部字段与合并规则，是读写上下文的统一字典
        - 状态蓝图：ThreadState / DeltaThreadState / SandboxState / ThreadDataState
        - 字段 reducer：artifacts / todos / prints / delegations / skill_context / viewed_images 等
        - 消息合并：merge_message_writes（Delta 模式核心）
        - 字段一个不删，完整保留 DeerFlow 全部字段

    对外暴露：
        - ThreadState / DeltaThreadState   状态蓝图（LangGraph AgentState 子类）
        - 各字段 merge_*                   字段合并语义（追加 / 覆盖 / 按 id 合并 / 清空）
        - merge_message_writes             消息写入的合并入口
        - THREAD_STATE_REDUCER_FIELDS      需要 reducer 的字段清单
"""


class SandboxState(TypedDict):
    """记录当前会话绑定的沙箱 ID。

    个人项目 + Agent 本地跑，通常只有一个沙箱实例；这里记下达的沙箱 ID，
    工具据此知道去哪个环境执行命令。sandbox_id 可空（尚未初始化）。
    """
    sandbox_id: NotRequired[str | None]

def merge_sandbox(
    existing: SandboxState | None,
    new: SandboxState | None,
) -> SandboxState | None:
    """`sandbox` 字段的 reducer：只接受幂等写入。

    多个沙箱工具可能在同一图步里并发地写 sandbox（共享同一个状态键），
    LangGraph 需要显式 reducer 来仲裁合并结果。规则：
      - new 为 None（本轮没写）→ 保留 existing
      - existing 为 None（首次写）→ 直接采用 new
      - 两者 sandbox_id 相同 → 幂等写入，返回 existing
      - 两者 sandbox_id 不同且旧沙箱仍在当前进程存活（并发隔离出问题）→
        宁可报错也不要偷偷二选一（fail-closed）
      - 两者 sandbox_id 不同但旧沙箱已不在当前进程（后端重启后容器重建、
        或容器被回收）→ 旧状态已失效，允许迁移到新沙箱（fail-open for stale）
    """
    # 1.new 为空 → 本轮没写，保留现有
    if new is None:
        return existing
    # 2.existing 为空 → 首次写入，直接采用
    if existing is None:
        return new

    existing_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    # 分支一：id 相同 → 幂等写入，保留现有（避免无谓地换对象）
    if existing_id == new_id:
        return existing
    # 分支二：旧状态还没记 id → 视为空槽，直接采用新值
    if existing_id is None:
        return new

    # 分支三：旧沙箱若已不在当前进程沙箱管理器（后端重启 / 容器被回收），
    # 说明这份状态已失效，允许迁移到新沙箱；查询失败则保守按「仍存活」处理。
    try:
        from harness.sandbox.lifecycle import get_sandbox_manager

        if get_sandbox_manager().get(existing_id) is None:
            return new
    except Exception:  # noqa: BLE001 —— 查询失败保守 fail-closed
        pass
    # 分支四：旧沙箱仍存活却要换成另一个 → 并发隔离出了问题，宁可报错也不偷偷二选一
    raise ValueError(
        f"Conflicting sandbox state updates: {existing_id!r} != {new_id!r}"
    )

SandboxStateField = Annotated[NotRequired[SandboxState | None], merge_sandbox]


class ThreadDataState(TypedDict):
    """会话文件存放地的三个关键路径。

    即 paths.py 里 sandbox_work_dir / sandbox_uploads_dir / sandbox_outputs_dir
    的状态化承载：运行时、present_file 等工具通过这些字段知道去哪读写文件。
    这个字段没有 reducer——路径是一次性由初始化写入的普通数据，无需合并仲裁。
    """
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """一张“已被模型看过”的图片的轻量元数据。

    图片字节很大，塞进每个 checkpoint 又慢又占空间，所以只往状态里记元数据，
    真正的字节在模型需要时按需从磁盘读（key 是图片路径）。
    """
    mime_type: str
    size: int
    actual_path: str

def merge_viewed_images(
    existing: dict[str, ViewedImageData] | None,
    new: dict[str, ViewedImageData] | None,
) -> dict[str, ViewedImageData]:
    """`viewed_images` 字典的 reducer：合并各轮新增的已看图片。

    规则：
      - existing/new 为 None → 回退到空
      - new 是空 dict {} → 清空整个池（供中间件“看完后清理”的特殊约定）
      - 否则 → dict 合并，同 key（图片路径）用新的覆盖新的
    """
    # 1.existing 为空 → 以 new 为底（None 归一成空字典）
    if existing is None:
        return new or {}
    # 2.new 为空 → 本轮没新增，保留现有
    if new is None:
        return existing
    # 3.new 是空 dict → 约定为「清空整池」（供中间件看完后清理用）
    if len(new) == 0:
        return {}
    # 4.按图片路径合并，同一路径用新元数据覆盖
    return {**existing, **new}




def merge_artifacts(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """`artifacts` 字段的 reducer：把多轮新增的成品合并、去重、保序。

    artifacts 是 “Agent 生成并提交给用户的最终成品清单”（路径字符串列表）。
    dict.fromkeys 既去重又保留首次出现的顺序
    （结果：新元素追加、重复元素只留最前一次）。
    """
    # 1.existing 为空 → 直接用 new（None 归一成空列表）
    if existing is None:
        return new or []
    # 2.new 为空 → 本轮没新增成品，保留现有
    if new is None:
        return existing
    # 3.拼接后去重：dict.fromkeys 既去重又保留首次出现的顺序
    return list(dict.fromkeys(existing + new))




def merge_archived_messages(
    existing: list[AnyMessage] | None,
    new: list[AnyMessage] | None,
) -> list[AnyMessage]:
    """`archived_messages` 字段的 reducer：压缩归档只增不减、追加保序。

    摘要中间件（summarization）压缩早期消息时，会把被移除的消息原文
    归档到这里——模型上下文（messages）保持精简，但对话展示历史
    （history reader）可以从本字段完整取回，二者从此解耦。
    """
    # 1.existing 为空 → 复制一份 new（不共享调用方列表，避免被后续原地修改污染）
    if existing is None:
        return list(new) if new else []
    # 2.new 为空 → 本轮没归档，保留现有
    if new is None:
        return existing
    # 3.追加归档：只增不减、保序（归档是历史回看的唯一来源，不能丢）
    return existing + list(new)


def merge_prints(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """`prints` 字段的 reducer：把各中间件产出的进度消息追加保存。

    prints 是“思考链数据”的载体：中间件把人类可读的状态消息
    （工具开始/结束、摘要完成、记忆已保存…）逐条 append 进去，
    运行层用 astream(stream_mode="values") 每次迭代取出增量推给前端。
    规则：纯追加、保序、不去重（流水日志语义，重复说明重复发生）。
    """
    # 1.existing 为空 → 用 new（None 归一成空列表）
    if existing is None:
        return new or []
    # 2.new 为空 → 本轮没有新进度，保留现有
    if new is None:
        return existing
    # 3.纯追加、保序、不去重（流水日志语义：重复即说明事情重复发生了）
    return existing + new




def merge_todos(existing: list | None, new: list | None) -> list | None:
    """`todos` 字段的 reducer：保留最近一次非空更新。

    todos 是规划/计划模式下的任务列表。关键区别：
      - new 为 None（节点本轮没碰 todos）→ 保留 existing
      - new 是一个列表（哪怕空）→ 明确更新，整体替换 existing
    这是“全量替换”语义，不是追加。
    """
    # 1.new 为 None → 本轮没碰 todos，保留旧清单
    if new is None:
        return existing
    # 2.new 是列表（哪怕是空列表）→ 明确更新，整体替换（不是追加）
    return new




class PromotedTools(TypedDict):
    """本次会话“提升”出来给 LLM 用的一批工具。

    catalog_hash 是工具目录的哈希，用于跟当前目录对账：
    一旦目录变过，旧的提升名单就失效，需要整体更换。
    """
    catalog_hash: str
    names: list[str]


def merge_promoted(
    existing: PromotedTools | None,
    new: PromotedTools | None,
) -> PromotedTools | None:
    """`promoted` 字段的 reducer：按目录哈希作用域合并提升的工具。

    规则：
      - new 为空 → 保留 existing（本轮没碰提升）
      - catalog_hash 变了 → 整体替换（旧目录下的名字失效，防止持久化的
        裸名在当前目录下指向另一个工具，目录漂移防护）
      - catalog_hash 相同 → 合并 names，去重保序
    """
    # 1.new 为空 → 本轮没碰提升名单，保留现有
    if not new:
        return existing
    # 2.首次提升、或工具目录哈希已变 → 旧名单整体作废，按新目录重建
    #   （目录漂移防护：旧目录下的裸名在当前目录可能指向另一个工具）
    if existing is None or existing.get("catalog_hash") != new["catalog_hash"]:
        return {
            "catalog_hash": new["catalog_hash"],
            "names": list(dict.fromkeys(new["names"])),
        }
    # 3.目录未变 → 合并 names，去重且保留首次出现顺序
    return {
        "catalog_hash": existing["catalog_hash"],
        "names": list(dict.fromkeys(existing["names"] + new["names"])),
    }



def merge_goal(
    existing: GoalState | None,
    new: GoalState | None,
) -> GoalState | None:
    """`goal` 字段的 reducer：保留已存在，采用显式更新。

    goal 是这次会话的终极目标 + 推进账本（见 goal_state.py）。
    通常由系统/用户一次性设置，后续整体覆盖，不需要合并。
    """
    # 1.new 为 None → 本轮没碰目标，保留现有
    if new is None:
        return existing
    # 2.有显式更新 → 整体覆盖（目标不做字段级合并）
    return new



# 子代理的「终止态」集合：一旦进入终止态就不允许被非终止态覆盖（防状态回退）
TERMINAL_STATUSES: frozenset[str] = frozenset(SUBAGENT_STATUS_VALUES)

# 委托账本最多保留多少条（长会话只留最近记录，防止无限增长）
_DELEGATION_LEDGER_MAX_ENTRIES = 50


class DelegationEntry(TypedDict):
    """一条子代理委托的账本记录。

    status 用 SUBAGENT_STATUS_VALUES；stop_reason 表示被护栏（token/turn/loop
    上限）提前终止时的原因，status 仍是 completed/failed，stop_reason 是附加信号。
    """
    id: str
    run_id: NotRequired[str]
    description: str
    subagent_type: str
    status: str
    result_brief: NotRequired[str]
    result_sha256: NotRequired[str]
    result_ref: NotRequired[str]
    stop_reason: NotRequired[str]
    created_at: str


def merge_delegations(
    existing: list[DelegationEntry] | None,
    new: list[DelegationEntry] | None,
) -> list[DelegationEntry]:
    """`delegations` 委托账本的追加/更新 reducer。

    规则：
      - new 为空 → 保留现有账本
      - 追加条目：同 id 用最新版替换，但保留首次出现顺序
      - 终止态保护：已进入终止态的条目不能被非终止态覆盖（防回退）
      - 复用信息：已有条目带 created_at/run_id 时，新版本补上缺失的
      - 上限裁剪：超 _DELEGATION_LEDGER_MAX_ENTRIES 时只保留最新的
    """
    # 1.new 为空 → 本轮没记委托，保留现有账本
    if not new:
        return existing or []

    # 2.按 id 归并：先铺旧账本再铺新条目，同 id 后者生效，但保留首次出现顺序
    by_id: dict[str, DelegationEntry] = {}
    order: list[str] = []
    for entry in [*(existing or []), *new]:
        entry_id = entry["id"]
        previous = by_id.get(entry_id)
        # 2.1 终止态保护：已终止的条目不能被非终止态覆盖（防状态回退）
        if (
            previous is not None
            and previous["status"] in TERMINAL_STATUSES
            and entry["status"] not in TERMINAL_STATUSES
        ):
            continue
        if entry_id not in by_id:
            order.append(entry_id)
        else:
            # 2.2 新条目补上旧条目已有的 created_at / run_id：保留「首次创建」信息
            if previous.get("created_at"):
                entry = {**entry, "created_at": previous["created_at"]}
                if previous.get("run_id") and not entry.get("run_id"):
                    entry["run_id"] = previous["run_id"]
        by_id[entry_id] = entry

    # 3.按首次出现顺序还原成列表
    merged = [by_id[entry_id] for entry_id in order]
    # 4.上限裁剪：只保留最新的 N 条，防长会话把账本撑到无限大
    if len(merged) > _DELEGATION_LEDGER_MAX_ENTRIES:
        merged = merged[-_DELEGATION_LEDGER_MAX_ENTRIES:]
    return merged



# 技能缓存池最多保留多少条（只留最近读过的技能元数据）
_SKILL_CONTEXT_MAX_ENTRIES = 8
# 技能描述写入状态前的截断长度（防长描述把 checkpoint 撑大）
_SKILL_DESCRIPTION_MAX_CHARS = 500


class SkillEntry(TypedDict):
    """一个已读/已加载技能的简要记录。

    只存元数据，技能正文按需从磁盘读；避免每轮重复加载技能内容。
    """
    name: str
    path: str
    description: str
    loaded_at: int


def _normalize_skill_entry(entry: Mapping[str, object]) -> SkillEntry:
    """把任意传入的技能词典规范成标准 SkillEntry。

    description 去掉多余空白并截断到 _SKILL_DESCRIPTION_MAX_CHARS；
    loaded_at 非 int 时兜底为 0。防止脏数据写坏 skill_context。
    """
    # 1.取原始字段（name/path 强制转字符串；path 缺键会 KeyError，说明调用方写坏了数据）
    description = entry.get("description")
    loaded_at = entry.get("loaded_at")
    # 2.按 SkillEntry 形状重建：description 压成单行并截断，loaded_at 非 int 一律兜底 0
    return {
        "name": str(entry.get("name") or ""),
        "path": str(entry["path"]),
        "description": (
            " ".join(description.split())[:_SKILL_DESCRIPTION_MAX_CHARS]
            if isinstance(description, str)
            else ""
        ),
        "loaded_at": loaded_at if isinstance(loaded_at, int) else 0,
    }


def merge_skill_context(
    existing: list[SkillEntry] | None,
    new: list[SkillEntry] | None,
) -> list[SkillEntry]:
    """`skill_context` 技能缓存池的 LRU 式管理 reducer。

    规则：
      - 先把旧的归一化（去掉历史脏 key）
      - new 为空 → 返回归一化后的旧条目
      - 按 path 去重；同一技能再次读到 → 刷新“最近使用”顺序并替换描述
      - 上限 _SKILL_CONTEXT_MAX_ENTRIES：只保留最近读的几条
      - loaded_at 只是观察值（消息索引在压缩后会重置，不能当唯一依据）
    """
    # 1.旧条目先归一化，顺手清掉历史遗留的脏 key（防止脏数据继续传播）
    normalized_existing = [_normalize_skill_entry(entry) for entry in existing or []]
    # 2.new 为空 → 只返回归一化后的旧池
    if not new:
        return normalized_existing

    # 3.旧池按 path 建索引，并记住首次出现顺序
    by_path: dict[str, SkillEntry] = {}
    order: list[str] = []
    for entry in normalized_existing:
        path = entry["path"]
        if path not in by_path:
            order.append(path)
        by_path[path] = entry

    # 4.新读到的技能：已在池中就挪到队尾（刷新「最近使用」），再写入最新元数据
    for entry in (_normalize_skill_entry(entry) for entry in new):
        path = entry["path"]
        if path in by_path:
            order.remove(path)
        order.append(path)
        by_path[path] = entry

    # 5.按顺序还原，并裁到上限（只保留最近读过的几条）
    merged = [by_path[path] for path in order]
    if len(merged) > _SKILL_CONTEXT_MAX_ENTRIES:
        merged = merged[-_SKILL_CONTEXT_MAX_ENTRIES:]
    return merged



class ThreadState(AgentState):
    # 会话绑定的沙箱（reducer 保证并发写只认「幂等」或「旧沙箱已失效」两种情况）
    sandbox: SandboxStateField
    # 工作区 / 上传目录 / 产出目录三个宿主路径（初始化一次性写入，无需 reducer）
    thread_data: NotRequired[ThreadDataState | None]
    # 会话标题（TitleMiddleware 生成后写入）
    title: NotRequired[str | None]
    # 已提交给用户的成品清单（跨轮累加、去重保序）
    artifacts: Annotated[list[str], merge_artifacts]
    # 任务清单（整体替换语义，不是追加）
    todos: Annotated[list | None, merge_todos]
    # 模型最近一次产出/更新任务计划的墙钟时刻（time.time()）。
    # 用途：run 收尾判断「本轮模型有没有碰过 todo」——没碰过就把 todos 清空
    # （用户语义：新问题没有计划就不该挂着上一轮的旧清单）。
    # plain 字段即 last-value-wins，无需 reducer。
    todos_touched_at: NotRequired[float]
    # 会话长期目标 + 推进账本（见 goal_state.py）
    goal: Annotated[GoalState | None, merge_goal]
    # 本轮上传的文件（UploadsMiddleware 写入）
    uploaded_files: NotRequired[list[dict] | None]
    # 已看过的图片元数据（图片字节按需从磁盘读，不塞进 checkpoint）
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]
    # 本会话「提升」给模型用的工具名单（按工具目录哈希作用域，防目录漂移）
    promoted: Annotated[PromotedTools | None, merge_promoted]
    # 子代理委托账本（谁派了什么、结果如何）
    delegations: Annotated[list[DelegationEntry], merge_delegations]
    # 已读技能缓存（只存元数据，避免每轮重复加载技能正文）
    skill_context: Annotated[list[SkillEntry], merge_skill_context]
    # 摘要文本（压缩后写入，供展示与续接）
    summary_text: NotRequired[str | None]
    # 被压缩归档的原始消息（模型上下文精简，但对话历史仍可完整回看）
    archived_messages: Annotated[list[AnyMessage], merge_archived_messages]
    # 思考链进度消息（各中间件追加，运行层按增量推给前端）
    prints: Annotated[list[str], merge_prints]




def _normalize_messages(value: Any) -> list[AnyMessage]:
    """把任意消息输入统一转成 list[AnyMessage]，并补齐缺失的 message id。

    - value 可以是单条消息或消息列表，统一包成 list
    - 分片消息（BaseMessageChunk）会物化为完整消息
    - id 缺失的补齐 uuid，保证每条都有稳定 id（覆盖/删除依赖它）
    """
    # 1.单条消息也包成列表，后面统一按列表处理
    values = value if isinstance(value, list) else [value]
    # 2.分片（BaseMessageChunk）物化成完整消息：写入状态前必须是完整消息
    messages = [
        message_chunk_to_message(cast(BaseMessageChunk, message))
        for message in convert_to_messages(values)
    ]
    # 3.补齐缺失 id：覆盖与删除都靠 id 定位，不能为空
    for message in messages:
        if message.id is None:
            message.id = str(uuid.uuid4())
    return messages


def _index_messages(
    messages: list[AnyMessage | None],
) -> tuple[dict[str, int], dict[str, list[int]]]:
    """为消息列表建两套索引，供覆盖/删除 O(1) 定位。

    - latest_position:  message_id → 最后一次出现的位置
    - positions_by_id:  message_id → 所有出现位置列表
    返回后调用方可据此替换或置空被删的消息槽位。
    """
    # 1.一次遍历同时记「最后位置」与「全部位置」两套索引
    latest_position: dict[str, int] = {}
    positions_by_id: dict[str, list[int]] = {}
    for position, message in enumerate(messages):
        # 2.已置空的槽位（之前被删过）跳过
        if message is None:
            continue
        message_id = cast(str, message.id)
        latest_position[message_id] = position
        positions_by_id.setdefault(message_id, []).append(position)
    return latest_position, positions_by_id


def _raise_null_write(has_messages: bool) -> None:
    """抛出“必须同时给 left 和 right”的 null 写错误。

    对应 add_messages(left, None) 的语义：非空时只用 left，空时只用 right，
    这里把两种误用统一抛成明确的错误，方便定位。
    """
    # 有 left 说明是 writes[0] 为空，反之是 state 为空 —— 报错时点名是哪一侧，便于定位
    received = "left" if has_messages else "right"
    raise ValueError(
        f"Must specify non-null arguments for both 'left' and 'right'. Only received: '{received}'."
    )


def merge_message_writes(state: list[AnyMessage], writes: Sequence[Any]) -> list[AnyMessage]:
    """DeltaChannel 的写入折叠：用 add_messages 语义线性把增量叠回全量。

    LangGraph 内置的 _messages_delta_reducer 虽也是线性的，但没保留公共
    add_messages 的全部强制转换 / ID / 删除 / REMOVE_ALL_MESSAGES 行为，
    故这里手工复刻以保证语义一致。

    - writes 为空 → 原样返回已折叠状态
    - 新消息追加、同 id 消息原地覆盖
    - RemoveMessage 删除对应 id；REMOVE_ALL_MESSAGES 清空全部后重来
    - 删除不存在的 id 抛错（add_messages 语义）
    """
    # 1.没有写入 → 原样返回当前折叠状态（DeltaChannel 会频繁空写）
    if not writes:
        return list(state)
    # 2.首条写入为 None → 非法 null 写（add_messages 语义），直接报错
    if writes[0] is None:
        _raise_null_write(bool(state))

    # 3.把当前状态物化成消息列表并建索引，后续覆盖/删除都能 O(1) 定位
    messages: list[AnyMessage | None] = _normalize_messages(state)
    latest_position, positions_by_id = _index_messages(messages)

    # 4.逐条写入折叠（保持 add_messages 的线性语义）
    for write in writes:
        if write is None:
            _raise_null_write(bool(latest_position))
        normalized_write = _normalize_messages(write)
        remove_all_idx = None
        for position, message in enumerate(normalized_write):
            if isinstance(message, RemoveMessage) and message.id == REMOVE_ALL_MESSAGES:
                remove_all_idx = position

        # 4.1 出现 REMOVE_ALL_MESSAGES → 整表清空，只保留它之后的新消息
        if remove_all_idx is not None:
            messages = list(normalized_write[remove_all_idx + 1:])
            latest_position, positions_by_id = _index_messages(messages)
            continue

        # 4.2 常规写入：按 id 命中则「覆盖或标记删除」，未命中则追加
        ids_to_remove: set[str] = set()
        for message in normalized_write:
            message_id = cast(str, message.id)
            existing_position = latest_position.get(message_id)
            if existing_position is not None:
                # 4.2.1 同 id 已存在：RemoveMessage 记为待删，普通消息原地覆盖
                if isinstance(message, RemoveMessage):
                    ids_to_remove.add(message_id)
                else:
                    ids_to_remove.discard(message_id)
                    messages[existing_position] = message
                continue

            # 4.2.2 同 id 不存在却要删 → 按 add_messages 语义报错（删不存在的 id 是调用方 bug）
            if isinstance(message, RemoveMessage):
                raise ValueError(
                    f"Attempting to delete a message with an ID that doesn't exist ('{message_id}')"
                )
            position = len(messages)
            messages.append(message)
            latest_position[message_id] = position
            positions_by_id[message_id] = [position]

        # 4.3 统一处理本轮标记的删除：槽位置空 + 清掉两套索引
        for message_id in ids_to_remove:
            for position in positions_by_id.pop(message_id):
                messages[position] = None
            del latest_position[message_id]

    # 5.过滤掉被删的空槽位，返回折叠后的全量消息
    return [message for message in messages if message is not None]


class DeltaThreadState(ThreadState):
    """delta 检查点用的会话状态：把 messages 换成 DeltaChannel。

    其它 12 个字段全部继承自 ThreadState，只覆盖 messages——
    不再存全量，而是只存增量、每 DEFAULT_CHECKPOINT_SNAPSHOT_FREQUENCY 步
    折叠一次完整快照。需要省存储时用本类作为 LangGraph 的 state schema。
    """
    # 只存增量、每 N 步折叠一次全量快照：牺牲一点重放成本换存储体积
    messages: Annotated[
        list[AnyMessage],
        DeltaChannel(
            merge_message_writes,
            snapshot_frequency=DEFAULT_CHECKPOINT_SNAPSHOT_FREQUENCY,
        ),
    ]



# 所有带 reducer（即需要 LangGraph 合并仲裁）的字段名；外部据此判断某字段是否走 reducer
THREAD_STATE_REDUCER_FIELDS = frozenset(
    {
        "messages",
        "sandbox",
        "artifacts",
        "todos",
        "goal",
        "viewed_images",
        "promoted",
        "delegations",
        "skill_context",
        "archived_messages",
        "prints",
    }
)