"""deskpet —— 桌宠 Web 应用（软件工程课程作业）

定位：「什么都记得，不无所不知」的桌面搭子，面向一个人赶 ddl 的学生。
主线：记住截止时间 → 打开时「回来补报还差什么」→ 贴通知出逐条交付清单
     → 一键导出 docx。杀手级 = 主动 + 还差什么。

最硬的结构原则（红线）：
    桌宠离开大模型必须是完整可运行的软件。角色/拖拽/气泡/昼夜、待办状态、
    回来补报、勾选、docx 导出、成长天数/记忆累积——全是确定性普通代码。
    智能体只是插在「理解」这一步的可插拔部件，只在三处刚需 AI：
    贴通知抽清单、任务内一问一答、一句话变待办。
    自检：删掉模型，业务流程仍在、只是变笨 = 接对；删完只剩空聊天框 = 不合格。

可替换准则（四者互不引用）：
    形象 = 资源（persona/）        换皮不动逻辑
    交互 = 状态机（interaction/）  加功能不动壳
    能力 = 注册表（games/、tools）  换档/整关不影响其它
    prompt = 独立文件（prompts/）  改语气不动代码

本包目前是骨架阶段：目录结构、数据模型、接口契约、白名单动作表、收窄工具注册、
存储与认证骨架。不接模型、不写 UI、不写实际读写实现——等逐部分确认后再填。

已拍板项：
    1. 存储选型：SQLite + WAL，理由是并发（JSON 并发写丢数据要自己造文件锁，
       SQLite 事务+WAL 天然支持读写不互锁）。见 storage.py。
    2. 用户隔离口径：访问口令管「能不能进门」+ 前端 localStorage UUID 管「你是谁」，
       两层分开。口令泄露换一个不影响数据归属；UUID 丢了数据找不回（demo 可接受）。
       见 auth.py。
    3. 模型调用路径 = 方案 B：复用 shared.models.ModelManager 的模型层与纯函数护栏，
       model.with_structured_output(Schema).ainvoke()，**不构造 ReAct Agent**；
       因此桌宠进程不注册任何工具，run_shell 从源头不出现。
       依据与护栏复用矩阵见 tools_allowlist.py 顶部说明；公网三层限流须在 Web 层自补。
"""

# 导入即执行的骨架级启动自检：白名单自身不得混入禁用工具。
# 方案 B 下桌宠不构造 Agent、不注册任何工具，这里检查的是配置层。
# 若将来真引入 Agent/工具机制，必须在实际传给 create_agent 的集合上再查一次
# （filter_tools_for_deskpet 是「先全注册再筛」，只查白名单不够）。
from deskpet.tools_allowlist import ALLOWED_DESKPET_TOOLS, assert_no_forbidden

assert_no_forbidden(ALLOWED_DESKPET_TOOLS)

