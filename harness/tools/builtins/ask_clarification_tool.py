from typing import Literal, Required, TypedDict
from langchain.tools import tool

""" 当 LLM 需要用户澄清时，调用此工具，触发 ClarificationMiddleware 执行 """

class ClarificationFormField(TypedDict, total=False):
    """ 澄清表单里的输入框定义：每个框叫什么名字、是什么类型、是不是必填 """
    name: Required[str]
    label: str
    type: Literal["text", "textarea", "number", "select", "multi_select", "checkbox", "date"]
    required: bool
    options: list[str]
    placeholder: str


@tool("ask_clarification", parse_docstring=True, return_direct=True)
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[str] | None = None,
    fields: list[ClarificationFormField] | None = None,
) -> str:
    """向用户提问，获取继续执行任务所需的关键信息。

    当你遇到以下“卡住”的情况时，使用此工具向用户求助：

    - 缺东西：用户没给全必要信息（比如文件路径、网址、具体要求）
    - 没说清：用户的要求有歧义，有好几种理解方式
    - 纠结选哪个：有好几种方案都能实现，需要用户决定用哪个
    - 要闯祸了：操作有风险（比如删文件、改线上配置），必须得到用户明确同意才能动手
    - 提建议：你有个想法，但需要用户拍板批准

    执行此工具会中断当前任务，把问题呈现给用户。请等待用户回复后再继续。

    什么时候该用：
    - 缺信息、有歧义、多选一、危险操作、需要用户点头

    提问的三种形式（选一种）：
    1. **纯文本提问**：直接写 question（用户打字回复）
    2. **给选项**：用 options（用户点击选择，适用于单选的场景）
    3. **一次性填表**：用 fields（适合一次要收集多个参数，比如填一张表单。强烈推荐这种方式，别一个问题接一个问题地问，用户体验很差）

    最佳实践（做事规矩）：
    - 一次只问一件事（填表也算“一次”）
    - 问题要具体、清晰，别让用户猜
    - 拿不准的时候别自作聪明，一定要问
    - 危险操作必须问，别手软
    - 如果某个技能（Skill）给你提供了现成的表单模板，直接原样传进去用，别自己乱改
    - 调用此工具后，程序会自动中断，等用户回复

    Args:
        question: 你要问的具体问题（必须写清楚）。
        clarification_type: 上述5种情况选一个（missing_info, ambiguous_requirement, approach_choice, risk_confirmation, suggestion）。
        context: 可选。补充说明为什么要问这个，帮用户理解背景。
        options: 可选。给用户的选项列表（比如 ["方案A", "方案B"]）。
        fields: 可选。表单字段定义（如果要填表就用这个）。每个字段包含：
            - name: 字段唯一标识（必填，别用 constructor、toString 这类 JS 保留词）
            - label: 显示给用户看的标签（默认就是 name）
            - type: 输入类型（text/textarea/number/select/multi_select/checkbox/date，默认 text）
            - required: 是否必填（默认 false）。注意：checkbox 默认是“否”，只有需要用户必须勾选同意时才设 required
            - options: 当 type 为 select/multi_select 时，必填选项列表
            - placeholder: 输入框里的灰色提示文字
        【注意】：表单最多 16 个字段，每个字段最多 24 个选项，名称/标签/选项/提示文字每项最多 200 个字符。超出限制会退化（降级）成纯文本提问。
    """
    return "Clarification request processed by middleware"