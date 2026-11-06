"""deskpet.tools_allowlist —— 收窄工具注册（白名单注册表骨架）

brief 第七节硬约束（v5 · P0-5 拍板 = 方案 A 隔离导入口径）：
    - 桌宠不构造 ReAct Agent、**不注册任何工具**；模型调用（阶段 3）走
      shared.models.ModelManager 取 chat model + with_structured_output()
    - gen_docx 复用方式改为：markdown→docx 逻辑抽成 deskpet/docx_gen.py
      纯函数（P0-5 选 A）。理由：from tools.documents import gen_docx 会先
      执行 tools/__init__.py → import tools.coding → run_shell 模块被拖进
      进程；且 gen_docx 是 @tool 装饰的 StructuredTool 不是普通函数。
      方案 A 下 tools 包根与 tools.coding 不进桌宠进程，
      tools.coding 属「从源头不出现」，不只是「导入但不可调用」
    - 场景专用 prompt，强制固定 JSON schema 输出 + 校验重试
    - 本文件不是「注册表白名单」：没有工具注册这一步，
      FORBIDDEN_TOOLS 只作配置层禁用清单与启动自检依据

现状（tools/__init__.py 当前行为，之所以不能走 get_agent()）：
    导入时无差别加载 coding/documents/search 全部工具，包含 run_shell、
    read_file、write_file、edit_file、list_dir；core.build_agent() 还会把它们
    硬编码进 tools，桌宠公网场景必须整体绕开。

本文件定义桌宠场景的禁用清单：
    导出 docx 走 deskpet/docx_gen.py 纯函数（方案 A，不经 tools 包）
    禁止：run_shell / read_file / write_file / edit_file / list_dir /
          gen_xlsx / gen_pptx（桌宠主线用不到）
          search 类（桌宠不做通用问答/联网搜索）

实现阶段（模型调用路径，阶段 3 生效）：桌宠**不构造 ReAct Agent、
不注册任何工具**，模型调用走
shared.models.ModelManager 的 chat model + with_structured_output()。
本白名单与 filter_tools_for_deskpet() 保留为配置层的禁用清单与自检依据；
若将来真引入 Agent/工具机制，必须在实际传给 create_agent 的集合上
调用 assert_no_forbidden()（「先全注册再筛」不足以证明安全）。
"""

from __future__ import annotations

from typing import Iterable


# ===========================================================================
# 桌宠场景允许注册的工具名（白名单）
# ===========================================================================
ALLOWED_DESKPET_TOOLS: set[str] = {
    "gen_docx",   # 导出 docx（方案 A：逻辑已抽到 deskpet/docx_gen.py 纯函数）
}

# 注：贴通知抽清单/一句话变待办/任务内一问一答/游戏大脑策略复盘，
# 走「场景专用 prompt + 固定 JSON schema + 校验重试」，是后端按场景编排的，
# 不是模型可自主触发的工具，所以不在工具白名单里。
#
# 【已定 · 方案 B：复用 agent 的模型层与纯函数护栏，不构造 ReAct Agent】
# 依据（读源码得到，不是凭印象）：
#   core.build_agent() 硬编码 tools = all_tools() + build_rag_tools() + extra_tools，
#   extra_tools 只能追加不能替换 → 走 get_agent() 必然把 run_shell 一起注册进来，
#   撞白名单红线；且 SYSTEM_PROMPT 要求自由文本、TodoList/Summarization/checkpointer
#   都面向通用任务对话，与「固定 JSON schema、单次调用」不对口。
# 落地：
#   1) 模型实例从 shared.models.ModelManager(config/models.json) + init_default()
#      取 current_model（LangChain chat model）。不调 api.init_runtime()——
#      它会构造 AgentRuntime → build_agent() → 注册全部工具。
#   2) 调用用 model.with_structured_output(Schema).ainvoke() 或 .ainvoke()
#      + Pydantic 校验重试；prompt 放 deskpet/prompts/，输出固定 schema。
#   3) 分级路由按 id 在启动时为 flash / v4-pro / ollama-local 各建实例，
#      不用 api.switch_model()（那条会重建 Agent）。
#   4) 导出 docx 用 deskpet/docx_gen.py 纯函数（P0-5 方案 A），
#      不 import tools.documents（会连带 tools.coding 进进程）。
# 护栏复用矩阵（别写成「全都现成」）：
#   直接复用：memory/cache.enable_llm_cache()（LangChain 全局缓存，
#     须在桌宠启动时显式调一次——原本只在 build_agent() 里被调用）、
#     token_tracker 的 estimate_tokens/estimate_cost/fmt_cost/SessionUsage、
#     output_guard 的 trim_degeneration/StreamGuard、observability.log_step
#   直连时改读：AIMessage.usage_metadata 取真实 token，套 estimate_cost(pricing,...)
#     → 每日成本硬闸改在 deskpet 侧落地：deskpet/cost.py 本地账本 +
#     deskpet/settings.py 单次/每日上限，超限自动降级并显示原因；
#     estimate_run/actual_usage 依赖 checkpointer 状态差分，只有
#     LangGraph Agent 能用，桌宠不用（方案 B 不构造 Agent）
#   公网三层限流 / 访问口令：主交付已是 Windows 单机 exe（每人一份本地库），
#     无公网暴露面，v6.1 已作废该口径，不在桌宠侧做任何补丁。
#     middleware/rate_limit.py 的 RateLimitMiddleware 只实现 awrap_tool_call
#     （限工具调用，不限模型请求），桌宠不调工具 → 与桌宠无关，不引用。


# ===========================================================================
# 明确禁用的工具（安全红线：无论单机还是公网部署都禁）
# ===========================================================================
FORBIDDEN_TOOLS: set[str] = {
    "run_shell",    # 任意 shell 执行——公网权限问题，绝禁
    "read_file",    # 任意文件读取——桌宠无需读用户磁盘
    "write_file",   # 任意文件写入——桌宠只通过 gen_docx 走受控导出
    "edit_file",    # 任意文件编辑——同上
    "list_dir",     # 目录浏览——无需
    "gen_xlsx",     # 桌宠主线不导 xlsx，不注册减少攻击面
    "gen_pptx",     # 同上
    "search_web",   # 桌宠不做通用问答/联网搜索——主线是记忆与待办，不做百科
}


def filter_tools_for_deskpet(all_tool_names: Iterable[str]) -> list[str]:
    """从全部已注册工具名里筛出桌宠场景允许的子集。

    方案 B 下桌宠不注册工具，本函数当前**不会被调用**，保留作配置层的
    禁用清单与自检依据。若将来引入 Agent/工具机制，用它筛完还要在
    「实际传给 create_agent 的集合」上再跑 assert_no_forbidden()。
    """
    return [n for n in all_tool_names
            if n in ALLOWED_DESKPET_TOOLS and n not in FORBIDDEN_TOOLS]


def assert_no_forbidden(registered: Iterable[str]) -> None:
    """启动自检：注册集合里不应出现任何禁用工具。答辩可讲的工程亮点。

    调用点一（已接）：deskpet/__init__.py 导入即检查 ALLOWED_DESKPET_TOOLS 自身。
    调用点二（仅当将来引入 Agent 才需要）：以实际传给 create_agent 的集合
    再调一次——「先全注册再筛」只查白名单不足以证明运行时安全。
    方案 B 下桌宠没有工具注册，故不存在第二个调用点。
    """
    leaked = set(registered) & FORBIDDEN_TOOLS
    if leaked:
        raise PermissionError(
            f"桌宠运行时检出到禁用工具被注册: {leaked}。"
            f"无论部署形态都必须收窄工具白名单，请检查 agent 组装逻辑。")
