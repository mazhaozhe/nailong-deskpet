"""deskpet.prompts.task_qa —— 任务内一问一答（只聊当前任务）

口子②：prompt 独立文件带版本号。回答锁定在当前任务上下文里，
不做通用百科、不做联网检索（FORBIDDEN_TOOLS 保持禁用 search_web）。
降级原则：模型不可用时本文件不被调用，走静态兜底文案。
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_SYSTEM = (
    "你是桌面桌宠，只回答用户关于「当前这一条任务」的问题（任务内一问一答）。\n"
    "规则：\n"
    "1. 只围绕提供的任务上下文（标题/截止时间/还差什么/状态）和用户提问回答，"
    "不聊任务之外的闲聊话题，不知道就直说不知道；\n"
    "2. answer 简洁口语化，不超过 200 字；\n"
    "3. suggestions 给 0~3 条可执行的下一步行动建议（结合「还差什么」）；\n"
    "4. 只输出符合给定 JSON schema 的结构化结果，禁止自由文本。"
)


def build_messages(todo_context: str, question: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",
         "content": f"当前任务：{todo_context}\n用户的提问：{question}"},
    ]
