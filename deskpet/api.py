"""deskpet.api —— 前后端接口（实现版）

阶段 3：接模型层（方案 B，收敛的 ReAct）。已实现：待办 CRUD、补报、
        导出 docx、成长、模拟，以及 notice/parse、todo/from-voice、
        task/qa、game/strategy、game/review（模型可用走 LlmGateway，
        不可用一律降级规则版/静态兜底，degraded=true + 明确原因）。

安全硬约束（brief 第七节 · v6.1 单机 exe 口径）：
    - 前端只调本文件路由，永不直连模型 API
    - 工具表收敛到 0 个：不构造 ReAct Agent、不注册任何工具，
      run_shell 从源头不进进程（也不 import tools.*/memory.rag）
    - 使用者自填 Key、safeStorage 加密存 userData（明文不进渲染进程/日志/exe 包）；
      三态开关（关/自动/开）全局生效，状态存用户数据目录 settings.json
    - 本地单次与每日成本上限（超限自动降级并显示原因）；
      不做公网三层限流 / 访问口令 / 团队 Key（旧公网口径已作废）
    - 服务不可用降级可运行（缓存 → ollama-local → 规则版/静态兜底）；
      托盘常驻 + 系统通知由 Electron 提供（阶段 5），补报按快照重算
"""

from __future__ import annotations

import re
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from deskpet import capabilities, clock, cost, settings, storage
from deskpet.deps import get_conn
from deskpet.growth import build_catchup
from deskpet.llm import gateway
from deskpet.models import (
    GameCommentary,
    GameReview,
    GameStrategy,
    NoticeParseResult,
    TaskQAAnswer,
    TodoStatus,
    VoiceParseResult,
)
from deskpet.prompts import (
    game_commentary as prompt_game_commentary,
    game_review as prompt_game_review,
    game_strategy as prompt_game_strategy,
    notice_to_checklist,
    task_qa as prompt_task_qa,
    voice_to_todo,
)
from deskpet.schemas import (
    CatchupResponse,
    ChecklistConfirmRequest,
    ChecklistConfirmResponse,
    ExportDocxRequest,
    ExportDocxResponse,
    GameCommentaryRequest,
    GameCommentaryResponse,
    GameMemoryResponse,
    GameReviewRequest,
    GameReviewResponse,
    GameStartRequest,
    GameStartResponse,
    GameStrategyRequest,
    GameStrategyResponse,
    GrowthResponse,
    ModelSwitchRequest,
    NoticeParseRequest,
    NoticeParseResponse,
    SimulateDaysRequest,
    SimulateDaysResponse,
    TaskQARequest,
    TodoCreate,
    TodoListResponse,
    TodoResponse,
    TodoUpdate,
    VoiceTodoRequest,
    VoiceTodoResponse,
)

router = APIRouter(prefix="/api", tags=["deskpet"])


def _uid(request: Request) -> str:
    """从 auth 中间件挂的 state 取 user_id；冒烟没挂中间件时兜底 anon。"""
    return getattr(request.state, "user_id", "anon-smoke")


# ===========================================================================
# 成长
# ===========================================================================
@router.get("/growth", response_model=GrowthResponse)
async def get_growth(request: Request,
                     conn=Depends(get_conn)) -> GrowthResponse:
    """认识天数/记忆条数/亲密度/形象阶段。不依赖模型。"""
    g = storage.get_growth(conn, _uid(request))
    return GrowthResponse(data=g)


@router.post("/growth/simulate", response_model=SimulateDaysResponse)
async def simulate_days(req: SimulateDaysRequest, request: Request,
                        conn=Depends(get_conn)) -> SimulateDaysResponse:
    """模拟第 N 天。只临时覆盖 clock.now() 返回值，不写脏真实数据。

    P0-3：模拟值按 user_id 存（clock._simulated 字典），A 模拟不影响 B；
    任何写库（create/update）走 clock.real_now() 绕开模拟；
    清除走 /growth/simulate/clear（前端页面加载时调一次 = 刷新回真实）。
    """
    uid_ = _uid(request)
    first_seen, _ = storage.get_or_create_user(conn, uid_)
    clock.set_simulated_now(req.target_day, first_seen, uid_)
    g = storage.get_growth(conn, uid_)
    return SimulateDaysResponse(
        data=g,
        note=f"模拟展示第 {req.target_day} 天，非真实数据；"
             f"清除后回真实天数（POST /growth/simulate/clear）")


@router.post("/growth/simulate/clear", response_model=GrowthResponse)
async def clear_simulation(request: Request,
                           conn=Depends(get_conn)) -> GrowthResponse:
    """显式清除本用户的模拟态，回到真实天数。

    前端在页面加载时调用一次（等效刷新回真实）；模拟按钮旁的
    「回到真实」也调它。SQLite 里 first_seen 不受任何影响。
    """
    uid_ = _uid(request)
    clock.clear_simulation(uid_)
    return GrowthResponse(data=storage.get_growth(conn, uid_))


# ===========================================================================
# 待办 CRUD
# ===========================================================================
@router.get("/todos", response_model=TodoListResponse)
async def list_todos(request: Request,
                     conn=Depends(get_conn)) -> TodoListResponse:
    return TodoListResponse(data=storage.list_todos(conn, _uid(request)))


@router.post("/todos", response_model=TodoResponse)
async def create_todo(req: TodoCreate, request: Request,
                      conn=Depends(get_conn)) -> TodoResponse:
    item = storage.create_todo(
        conn, _uid(request),
        title=req.title, deadline=req.deadline, gap=req.gap,
        source=req.source)
    return TodoResponse(data=item)


@router.patch("/todos/{todo_id}", response_model=TodoResponse)
async def update_todo(todo_id: str, req: TodoUpdate, request: Request,
                      conn=Depends(get_conn)) -> TodoResponse:
    fields = req.model_dump(exclude_unset=True)
    # status 字符串 → 枚举
    if "status" in fields and isinstance(fields["status"], str):
        fields["status"] = TodoStatus(fields["status"])
    item = storage.update_todo(conn, _uid(request), todo_id, **fields)
    if not item:
        raise HTTPException(404, "待办不存在")
    return TodoResponse(data=item)


@router.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str, request: Request,
                      conn=Depends(get_conn)) -> dict:
    if not storage.delete_todo(conn, _uid(request), todo_id):
        raise HTTPException(404, "待办不存在")
    return {"deleted": todo_id}


# ===========================================================================
# 回来补报
# ===========================================================================
@router.get("/catchup", response_model=CatchupResponse)
async def get_catchup(request: Request,
                      conn=Depends(get_conn)) -> CatchupResponse:
    """打开页面时的补报快照。当前时间 vs deadline 重算。"""
    uid_ = _uid(request)
    todos = storage.list_todos(conn, uid_)
    g = storage.get_growth(conn, uid_)
    snap = build_catchup(user_id=uid_, days_known=g.days_known, todos=todos)
    return CatchupResponse(data=snap)


# ===========================================================================
# 贴通知 → 抽清单（阶段 2 规则版，阶段 3 换模型，契约不变）
# ===========================================================================
# P0-2 / Step 0-1 拍板：deadline 只走前端单通道。
# parse 响应里的 deadline_hint 由前端存 window._deadlineHint（index.html），
# confirm 时显式回传 deadline_override —— 后端不再保留任何进程内 hint 兜底。
# 曾有模块级 _last_deadline_hint 字典：parse 返回 null 时不清空，上一条
# 通知的截止时间会被硬塞给不相关待办（陈旧 hint 污染 + 误报逾期），
# 且进程内存态在多进程/重启下行为不一致 —— 已整个移除。


@router.post("/notice/parse", response_model=NoticeParseResponse)
async def parse_notice(req: NoticeParseRequest,
                       request: Request) -> NoticeParseResponse:
    """贴通知 → 抽要求清单。阶段 3：模型抽取（方案 B 收敛版）。

    实现体换模型，路径、请求体、响应 schema 一律不动（v6.1 §4.1）：
      - 模型可用：LlmGateway + deepseek-flash 固定 JSON schema，
        use_v4_pro=True 走 deepseek-v4-pro（长通知复杂拆解手动档）；
      - 降级（开关=关 / 无 Key / 校验不过 / 超成本上限 / 网络失败）：
        回落 notice_rules 规则拆分，degraded=True + 明确 degrade_reason，
        前端不得渲染成报错。
    deadline 仍走前端单通道（Step 0-1 口径不变）。
    """
    from deskpet.notice_rules import parse_notice_by_rules
    out = await gateway.invoke(
        schema=NoticeParseResult,
        messages=notice_to_checklist.build_messages(
            req.notice_text, clock.to_iso(clock.real_now())),
        route="notice/parse", capability="parse_notice",
        prefer_manual=req.use_v4_pro)
    if out.data is not None:
        return NoticeParseResponse(data=out.data, degraded=False)
    return NoticeParseResponse(
        data=parse_notice_by_rules(req.notice_text),
        degraded=True, degrade_reason=out.degrade_reason)


@router.post("/notice/confirm", response_model=ChecklistConfirmResponse)
async def confirm_checklist(req: ChecklistConfirmRequest, request: Request,
                            conn=Depends(get_conn)) -> ChecklistConfirmResponse:
    """用户对照卡确认后，把勾选项落成 TodoItem。不用模型。

    deadline 唯一来源（Step 0-1）：req.deadline_override ——
    前端把 parse 抽出的 deadline_hint 存 window._deadlineHint 后显式回传。
    不传 override 一律落 null（无日期通知不串上一条的截止时间）。
    落库前统一走 clock.to_iso() 补时区（storage.create_todo 内完成），
    naive datetime 不会污染时区口径。
    """
    uid_ = _uid(request)
    deadline = req.deadline_override
    created = []
    for item in req.items:
        if item.matched:
            continue  # 已勾完成的不重复落
        todo = storage.create_todo(
            conn, uid_, title=item.text,
            deadline=deadline, gap=item.text,
            source="notice")
        created.append(todo)
    return ChecklistConfirmResponse(created=created)


# ===========================================================================
# 一句话变待办（阶段 3：模型口语解析 + 规则兜底，不留 500）
# ===========================================================================
@router.post("/todo/from-voice", response_model=VoiceTodoResponse)
async def todo_from_voice(req: VoiceTodoRequest,
                          request: Request) -> VoiceTodoResponse:
    """一句话 → 待办。模型可用走 deepseek-flash 口语解析（固定 schema）；
    不可用（关态/无 Key/校验不过）回落 notice_rules 规则兜底：
    本期不做语音识别，text 即转写文本或用户直接输入，degraded 如实标注。
    """
    from deskpet.notice_rules import parse_voice_by_rules
    out = await gateway.invoke(
        schema=VoiceParseResult,
        messages=voice_to_todo.build_messages(
            req.text, clock.to_iso(clock.real_now())),
        route="todo/from-voice", capability="from_voice")
    if out.data is not None:
        return VoiceTodoResponse(data=out.data, degraded=False)
    return VoiceTodoResponse(
        data=parse_voice_by_rules(req.text),
        degraded=True, degrade_reason=out.degrade_reason)


# ===========================================================================
# 任务内一问一答（阶段 3：只聊当前任务，降级给静态兜底不空白）
# ===========================================================================
@router.post("/task/qa")
async def task_qa(req: TaskQARequest, request: Request,
                  conn=Depends(get_conn)) -> dict:
    """任务内一问一答。锁定只聊当前任务（todo_id 上下文进 prompt）。

    响应 chunk 形态沿 TaskQAStreamChunk 契约（type/content/degraded）：
      - 模型可用：{"type": "done", "content": answer, "degraded": false}
      - 降级：{"type": "degrade", "content": 静态兜底文案, "degraded": true}
      - 任务不存在：{"type": "error", ...}，绝不 500、绝不空白。
    """
    todo = storage.get_todo(conn, _uid(request), req.todo_id)
    if todo is None:
        return {"type": "error", "content": "未找到关联任务，请核对任务 ID",
                "degraded": False}
    ctx = (f"「{todo.title}」（状态：{'已完成' if todo.status == TodoStatus.DONE else '进行中'}"
           f"；截止：{todo.deadline.strftime('%Y-%m-%d %H:%M') if todo.deadline else '未设置'}"
           f"；还差：{todo.gap or '未填写'}）")
    out = await gateway.invoke(
        schema=TaskQAAnswer,
        messages=prompt_task_qa.build_messages(ctx, req.question),
        route="task/qa", capability="task_qa")
    if out.data is not None:
        return {"type": "done", "content": out.data.answer,
                "suggestions": out.data.suggestions, "degraded": False}
    fallback = (f"模型暂不可用（{out.degrade_reason}）。"
                f"当前任务还差：{todo.gap or '未填写'}，"
                f"可先把「还差什么」补上，模型恢复后再细问。")
    return {"type": "degrade", "content": fallback, "degraded": True}


# ===========================================================================
# 导出 docx（不用模型 · P0-5 方案 A：deskpet 内纯函数，tools.coding 不进进程）
# ===========================================================================
# P0-1 / Step 0-2：导出目录统一落用户数据目录（deskpet/paths.py），
# 绝不放在 desktop/renderer/ 下 —— static 整个被 mount，
# /static/exports/{fname} 会 200，旁路下载路由的三道闩。
# 下载走 /exports/{filename} 白名单路由。
# 文件名白名单：只放行本模块自己生成的名字，从构造上杜绝 ../ 路径穿越
_EXPORT_NAME_RE = re.compile(r"^deskpet_export_[0-9a-f]{8}\.docx$")
_DOCX_MIME = ("application/vnd.openxmlformats-officedocument"
              ".wordprocessingml.document")
_EXPORT_TTL = timedelta(hours=1)  # expires_at 的真实语义：文件只活 1 小时


def _exports_dir() -> Path:
    """导出目录（动态取：测试经 DESKPET_DATA_DIR 注入 tmp_path）。"""
    from deskpet import paths
    return paths.get_exports_dir()


def _purge_export(path: Path) -> None:
    """删一个导出文件，不存在/占用都静默（清理路径，非主流程）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_expired_exports() -> int:
    """按过期时间删除导出文件（启动时调用；下载/导出时顺手兜底）。

    过期依据 = 文件 mtime + _EXPORT_TTL（文件名由本模块 uuid 生成，
    mtime 即生成时刻，与响应里的 expires_at 同源）。
    返回删除数。Step 0-2：expires_at 从此真清理，不再「假装过期」。
    """
    removed = 0
    d = _exports_dir()
    now = clock.real_now()
    for p in d.iterdir():
        if not _EXPORT_NAME_RE.fullmatch(p.name):
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=now.tzinfo)
        except OSError:
            continue
        if now - mtime > _EXPORT_TTL:
            _purge_export(p)
            removed += 1
    return removed


@router.post("/export/docx", response_model=ExportDocxResponse)
async def export_docx(req: ExportDocxRequest, request: Request,
                      conn=Depends(get_conn)) -> ExportDocxResponse:
    """一键导出 docx。deskpet.docx_gen.markdown_to_docx 纯函数直调。

    P0-5 方案 A：不从 tools.documents 导入（那会经 tools/__init__.py
    把 tools.coding/run_shell 拖进进程），docx 生成抽成 deskpet 内纯函数。
    导出文件落用户数据目录（Step 0-2），不随源码/exe 分发。
    """
    from deskpet.docx_gen import markdown_to_docx

    uid_ = _uid(request)
    todos = storage.list_todos(conn, uid_)
    selected = [t for t in todos if t.id in req.todo_ids]
    if not selected:
        raise HTTPException(404, "没有匹配的待办可导出")

    cleanup_expired_exports()  # 顺手兜底：不堆积过期导出物

    # 拼 markdown（固定模板填字段，确定性）
    lines = [f"# {req.title}", ""]
    for t in selected:
        status = "✓" if t.status == TodoStatus.DONE else "□"
        lines.append(f"- {status} **{t.title}**")
        if t.deadline:
            lines.append(f"  - 截止：{t.deadline.strftime('%Y-%m-%d %H:%M')}")
        if t.gap:
            lines.append(f"  - 还差：{t.gap}")
        lines.append("")
    md = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8")
    md.write("\n".join(lines))
    md.close()

    fname = f"deskpet_export_{uuid.uuid4().hex[:8]}.docx"
    out = _exports_dir() / fname
    markdown_to_docx(markdown_path=md.name, output_path=str(out),
                     title=req.title)
    Path(md.name).unlink(missing_ok=True)  # 临时 md 用完即删

    return ExportDocxResponse(
        download_url=f"/exports/{fname}",
        expires_at=clock.real_now() + _EXPORT_TTL)


# 下载路由：挂在 app 根路径（/exports/...，不带 /api 前缀）。
# 单独一个 router，app.py 一并 include；smoke/test 同样挂。
download_router = APIRouter(tags=["deskpet"])


@download_router.get("/exports/{filename}")
async def download_export(filename: str):
    """下载导出的 docx。

    P0-1 安装三道闩：
      1) 文件名白名单 fullmatch：deskpet_export_[0-9a-f]{8}.docx，
         含 / \\ .. 空格等任何其它字符直接 404 —— 路径穿越构造不出来；
      2) 只从导出目录读，且 resolve 后父目录必须仍是导出目录
         （双保险，防软链/符号链接绕过）；
      3) 绝不 mount 整个导出目录，逐文件 FileResponse。

    Step 0-2 第四道闩：expires_at 真生效 —— 过期文件返回 410 并删除，
    字段与响应 schema 原样保留（schemas.py:153 属阶段 1 契约，不许改）。
    """
    if not _EXPORT_NAME_RE.fullmatch(filename):
        raise HTTPException(404, "文件名不合法")
    base = _exports_dir()
    path = (base / filename).resolve()
    if path.parent != base.resolve() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    # 过期校验：mtime + 1h < 现在 → 410 + 删除（不假装还能下载）
    mtime = datetime.fromtimestamp(path.stat().st_mtime,
                                   tz=clock.real_now().tzinfo)
    if clock.real_now() - mtime > _EXPORT_TTL:
        _purge_export(path)
        raise HTTPException(410, "导出文件已过期，请重新导出")
    return FileResponse(str(path), media_type=_DOCX_MIME, filename=filename)


# ===========================================================================
# 游戏大脑
# ===========================================================================
@router.post("/game/start", response_model=GameStartResponse)
async def game_start(req: GameStartRequest, request: Request,
                     conn=Depends(get_conn)) -> GameStartResponse:
    """开局。阶段 4：brain_level=2 经 gateway 取初始策略（策略层开局，
    带历史复盘记忆体现「会进步」）；brain_level=1 仅规则。

    关态 / 无 Key / 超限 / 校验不过 → initial_strategy=None + degraded=true，
    照常开局绝不报错；不改 GameStartResponse 已有字段（阶段 1 契约）。
    brain_level 已在 GameStartRequest 契约层收紧为 1~2，传 3 直接 422。
    """
    from deskpet.games.registry import get_game
    reg = get_game(req.game_id)
    if not reg or not reg.enabled:
        raise HTTPException(404, f"游戏 {req.game_id} 不存在或已禁用")
    sess = str(uuid.uuid4())
    if req.brain_level < 2:
        return GameStartResponse(
            session_id=sess, game_id=req.game_id,
            brain_level=req.brain_level, initial_strategy=None)
    # 策略层开局：拿得到历史 GameMemory 就带一句（不编造，拿不到为空）
    mem = storage.list_game_memory(conn, _uid(request), req.game_id)
    memory_snippet = ""
    if mem:
        last = mem[-1]
        memory_snippet = (f"上次{last.result}：{last.lost_at}；"
                          f"下次改：{last.next_change}")
    out = await gateway.invoke(
        schema=GameStrategy,
        messages=prompt_game_strategy.build_messages(
            {"game_id": req.game_id, "phase": "opening"}, memory_snippet),
        route="game/strategy", capability="game_strategy",
        session_id=sess)
    if out.data is not None:
        return GameStartResponse(
            session_id=sess, game_id=req.game_id,
            brain_level=req.brain_level, initial_strategy=out.data)
    return GameStartResponse(
        session_id=sess, game_id=req.game_id,
        brain_level=req.brain_level, initial_strategy=None,
        degraded=True, degrade_reason=out.degrade_reason)


@router.post("/game/strategy", response_model=GameStrategyResponse)
async def game_strategy(req: GameStrategyRequest,
                        request: Request) -> GameStrategyResponse:
    """游戏策略层。硬约束（v6.1 §4.10）：

    - 模型只出策略 JSON（策略名白名单 + 参数 + 理由），不出单步按键，
      不进实时执行层；白名单动作表决定可行性；
    - 非法 JSON / 校验不过 → 回落默认规则策略（balanced），degraded=true；
    - 单局调用 ≤3（策略+复盘共用计数），超限回默认规则；
    - 模型不可用同样回默认规则，绝不 500。
    """
    out = await gateway.invoke(
        schema=GameStrategy,
        messages=prompt_game_strategy.build_messages(req.state_snapshot),
        route="game/strategy", capability="game_strategy",
        session_id=req.session_id)
    calls = gateway.session_calls(req.session_id)
    if out.data is not None:
        return GameStrategyResponse(
            data=out.data, degraded=out.degraded,
            degrade_reason=out.degrade_reason,
            model_calls_this_game=calls)
    return GameStrategyResponse(
        data=GameStrategy(
            strategy_name="balanced", params={},
            reason="默认规则策略（模型不可用/超限/校验不过的兜底）"),
        degraded=True, degrade_reason=out.degrade_reason,
        model_calls_this_game=calls)


@router.post("/game/review", response_model=GameReviewResponse)
async def game_review(req: GameReviewRequest, request: Request,
                      conn=Depends(get_conn)) -> GameReviewResponse:
    """每局结束一次的复盘。模型可用输出输在哪/下次怎么改/一句话结论；
    不可用回落静态兜底文案（页面不空白、有提示）。
    final_state 里带 game_id 时把复盘结论写入 GameMemory（下一局策略层
    可读它体现「会进步」）；拿不到就不写，绝不为了写库而编造。
    """
    out = await gateway.invoke(
        schema=GameReview,
        messages=prompt_game_review.build_messages(
            req.final_state, req.strategy_history),
        route="game/review", capability="game_review",
        session_id=req.session_id)
    if out.data is not None:
        review, degraded, reason = out.data, False, ""
    else:
        result = str(req.final_state.get("result", ""))
        review = GameReview(
            lost_at=f"本局已结束（{result}）",
            next_change="",
            takeaway=f"复盘模型暂不可用：{out.degrade_reason}")
        degraded, reason = True, out.degrade_reason
    game_id = req.final_state.get("game_id")
    if isinstance(game_id, str) and game_id:
        from deskpet.models import GameMemory
        storage.record_game_memory(conn, GameMemory(
            user_id=_uid(request), game_id=game_id,
            played_at=clock.real_now(),
            strategy_used=";".join(req.strategy_history[-3:]) or "default",
            result=str(req.final_state.get("result", "unknown")),
            lost_at=review.lost_at,
            next_change=review.next_change))
    return GameReviewResponse(
        data=review, degraded=degraded, degrade_reason=reason)


@router.post("/game/commentary", response_model=GameCommentaryResponse)
async def game_commentary(req: GameCommentaryRequest,
                          request: Request) -> GameCommentaryResponse:
    """游戏解说层（阶段 4）。硬约束：

    - 模型只写一句气泡台词 + 情绪标签，不碰操作、不出按键、
      不进实时执行层（执行层永远纯规则，禁止逐帧调模型）；
    - 与策略/复盘共用同一 session_id 计数，单局总调用 ≤3；
    - 关态 / 无 Key / 超限 / 校验失败 → 静态兜底台词 + degraded=true，
      绝不 500、不空白。
    """
    out = await gateway.invoke(
        schema=GameCommentary,
        messages=prompt_game_commentary.build_messages(
            req.state_snapshot, req.strategy_name),
        route="game/commentary", capability="game_commentary",
        session_id=req.session_id)
    if out.data is not None:
        return GameCommentaryResponse(data=out.data, degraded=False)
    return GameCommentaryResponse(
        data=GameCommentary(
            line="稳住节奏，一步一步来，我看着你呢～", mood="encourage"),
        degraded=True, degrade_reason=out.degrade_reason)


@router.get("/game/{game_id}/memory", response_model=GameMemoryResponse)
async def game_memory(game_id: str, request: Request,
                      conn=Depends(get_conn)) -> GameMemoryResponse:
    return GameMemoryResponse(
        data=storage.list_game_memory(conn, _uid(request), game_id))


# ===========================================================================
# 三态开关 + 能力注册表 + 成本配额 + Key 管理（阶段 3 本地接口；
# 阶段 5 设置界面经 IPC/本地接口调用，本阶段不做 UI）
# ===========================================================================
@router.get("/settings/model-switch")
async def get_model_switch() -> dict:
    """读三态开关（off 关 / auto 自动 / on 开，默认 auto）。"""
    return {"state": settings.get_switch()}


@router.post("/settings/model-switch")
async def set_model_switch(req: ModelSwitchRequest) -> dict:
    """写三态开关，全局立即生效（改完立刻响应，无需重启）。"""
    try:
        state = settings.set_switch(req.state)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    gateway.reset()  # 懒加载态重置：切「关」后立刻零 import、零调用
    return {"state": state, "changed": True}


@router.get("/capabilities")
async def list_capabilities() -> dict:
    """能力注册表 + 当前开关 + 成本配额（三态开关与配额查询走注册表）。"""
    caps = settings.get_cost_caps()
    return {
        "switch": settings.get_switch(),
        "capabilities": capabilities.describe(),
        "quota": {
            "today_cost_yuan": cost.today_cost_yuan(),
            "today_calls": cost.today_calls(),
            "per_call_cap_yuan": caps.get("per_call_yuan"),
            "daily_cap_yuan": caps.get("daily_yuan"),
        },
    }


@router.get("/key/status")
async def key_status() -> dict:
    """Key 脱敏状态：只回「已配置」+ 尾四位；明文绝不外泄。"""
    return gateway.key_status()


@router.delete("/key")
async def delete_key() -> dict:
    """删 Key：清进程内注入 + 重置懒加载态，立刻回规则版。

    safeStorage 加密份在 Electron 主进程 userData 里，阶段 5 由主进程
    同步删除（本阶段只提供本地接口约定）。
    """
    return gateway.delete_key()


@router.post("/key/check")
async def check_key() -> dict:
    """一键连通性检查：401 / 余额 / 网络分别返回，不吞异常。"""
    return await gateway.check_connectivity()


# ===========================================================================
# 调试：模型调用计数（硬闸门 2 的可读证据）
# ===========================================================================
@router.get("/debug/model-calls")
async def debug_model_calls() -> dict:
    """只读调试接口：关态 / 无 Key 时 total 必须为 0（v6.1 口径）。"""
    from deskpet import model_counter
    return {"total": model_counter.total(),
            "by_route": model_counter.by_route()}