"""Agent 流式执行、终端展示和会话修复。"""
from apps.agent.runtime.ui import RunUI, extract_answer
from infrastructure.observability import log_step


def _is_orphan_error(exc: BaseException) -> bool:
    """判断异常是否是"孤儿 tool_calls"导致的接口 400（延迟导入避免成环）。"""
    from apps.agent.runtime.history_repair import looks_like_orphan_error
    return looks_like_orphan_error(exc)


async def stream_run(agent, message: str, thread_id: str) -> None:
    """
    执行一轮对话：
    - 思考/工具过程实时进"过程框"（固定高度，旧行滚出框外）；
    - 完整过程逐行写入 data/trace.log；
    - 结束后，最终答案以面板打印在框的正下方，一眼可见；
    - 结构化日志照常写 data/agent.log。
    """
    config = {"configurable": {"thread_id": thread_id}}
    log_step("user_input", thread_id=thread_id, message=message[:300])
    # 历史修复：上一轮被 Ctrl+C / 异常中断可能留下"孤儿 tool_calls"
    # （assistant 声明了工具调用但没有 tool 回复），带着它请求模型会被
    # 接口 400 拒绝，且错误会自我延续。开跑前先修一遍（幂等）。
    from apps.agent.runtime.history_repair import repair_history
    await repair_history(agent, thread_id)

    ui = RunUI(thread_id, message)
    answer = ""
    try:
        async for mode, payload in agent.astream(
            {"messages": [("user", message)]},
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                # (message_chunk, metadata) 元组：增量 token
                chunk, _meta = payload
                # 工具回包本身由下面 updates 分支的 tool_result 呈现；
                # 不过滤会把同一份输出以"模型文本"身份再灌一次过程框。
                if getattr(chunk, "type", "") == "tool":
                    continue
                content = getattr(chunk, "content", None)
                if content:
                    ui.model_text(content)
                # 深度推理模型（如 deepseek-reasoner）的思考链
                reasoning = getattr(chunk, "reasoning_content", None)
                if reasoning:
                    ui.reasoning_text(reasoning)

            elif mode == "updates":
                # payload: {node_name: {"messages": [...]}} —— 完整消息在这里
                if not isinstance(payload, dict):
                    continue
                for _node, state in payload.items():
                    if not state or not isinstance(state, dict):
                        continue
                    for msg in state.get("messages", []):
                        msg_type = getattr(msg, "type", "")
                        name = getattr(msg, "name", None) or ""
                        if msg_type == "ai":
                            # 带 tool_calls 的是中间步骤 → 记录调用；
                            # 不带 tool_calls 的才是最终答案
                            for tc in (getattr(msg, "tool_calls", None) or []):
                                ui.tool_call(tc.get("name", "?"),
                                             tc.get("args", {}))
                            answer = extract_answer([msg]) or answer
                        elif msg_type == "tool":
                            status = getattr(msg, "status", "success")
                            ok = status == "success"
                            ui.tool_result(name, ok,
                                           str(getattr(msg, "content", "")))
                            log_step("tool_result", tool=name,
                                     status=status,
                                     result=str(getattr(msg, "content", "")))
        ui.finish(answer)
    except KeyboardInterrupt:
        # Ctrl+C 中断同样可能留下孤儿 tool_calls，修掉再退出，别把坏历史
        # 留给下一轮（下一轮开跑前虽然也会修，这里双保险）
        from apps.agent.runtime.history_repair import repair_history
        await repair_history(agent, thread_id)
        ui.fail("[中断] 用户终止了本轮任务。")
        log_step("interrupted", thread_id=thread_id)
    except Exception as exc:  # 兜底：任何未预期异常都反馈给用户而不是崩溃
        if _is_orphan_error(exc):
            # 极端边角：开跑前的修复没兜住，这里补修一次并引导重新输入
            from apps.agent.runtime.history_repair import repair_history
            await repair_history(agent, thread_id)
            ui.fail("[错误] 检测到会话历史记录异常，已自动修复，请重新输入。")
            log_step("history_repaired_after_error", thread_id=thread_id)
        else:
            ui.fail(f"[错误] Agent 运行异常: {type(exc).__name__}: {exc}\n"
                    f"      请查看日志 data/agent.log 了解详情。")
            log_step("error", error=f"{type(exc).__name__}: {exc}")
    finally:
        ui.close()
