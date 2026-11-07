"""apps.deskpet.backend.prompts —— 场景专用 prompt（独立文件，改语气不动代码）

brief 第七节：场景专用 prompt，强制固定 JSON schema 输出 + 校验重试，不要自由文本。
brief 第八节可替换准则：prompt 是独立文件，与形象/交互/能力三者互不引用。

口子②（v6.1 §4.11）：每个 prompt 文件带 PROMPT_VERSION 版本号；
将来检索结果作为 context 注入 user 消息即可，不改 schema 形状。
降级原则：模型不可用时这些 prompt 不被调用，走规则兜底/静态文案。

已实现（阶段 3）：
- notice_to_checklist.py  贴通知 → 抽要求清单（deepseek-flash，固定 JSON schema）
- voice_to_todo.py        一句话变待办（deepseek-flash 口语解析）
- task_qa.py              任务内一问一答（只聊当前任务，不做百科/检索）

已实现（会话 3）：
- free_chat.py            自由闲聊（人设锚点 + 记忆块 + 固定 JSON schema）。
  契约：单次 gateway.invoke()、工具表 0、不联网、不注册工具；
  完整 Agent（全工具 + 联网 + 审批）属会话 4，不在本文件放行。

每个 prompt 文件导出 build_messages() 函数，接收场景参数返回
[{"role": "system"/"user", "content": ...}]，供 LlmGateway.ainvoke 使用。
"""
