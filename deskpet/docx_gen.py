"""deskpet.docx_gen —— docx 生成纯函数（P0-5 拍板：方案 A · 隔离导入）

导入链口径（写死，阶段 3 也不变）：
    桌宠进程不允许出现 tools.coding（run_shell 所在模块）。
    原复用路径 from tools.documents import gen_docx 有两条问题：
      1) import tools.documents 会先执行 tools/__init__.py，
         而 __init__ 里 import tools.coding —— run_shell 模块被拖进进程；
      2) gen_docx 被 @tool 装饰成 StructuredTool，不是普通函数。
    方案 A：把 markdown→docx 核心逻辑抽成 deskpet 内纯函数，
    零 langchain 工具机制、零 tools 包依赖，import 链干净。
    算法与 tools/documents.gen_docx 保持一致（同一段转换逻辑），
    上游若改排版规则需两边同步——这是方案 A 的已知代价，换取进程干净。

不用模型：纯 python-docx 确定性转换。
"""

from __future__ import annotations

import re
from pathlib import Path


def _add_runs(paragraph, text: str) -> None:
    """解析 **粗体** 语法：按 ** 切分，奇数段加粗。"""
    parts = text.split("**")
    for i, part in enumerate(parts):
        if not part:
            continue
        run = paragraph.add_run(part)
        run.bold = (i % 2 == 1)


def _bullet_style(indent: int) -> str:
    """嵌套列表样式：缩进 ≥2 空格 → 二级项目符号，否则一级。

    样式缺失时回退 List Bullet（python-docx 默认模板自带
    List Bullet 2，此回退只是保险）。
    """
    if indent >= 2:
        return "List Bullet 2"
    return "List Bullet"


def markdown_to_docx(markdown_path: str, output_path: str,
                     title: str = "") -> str:
    """Markdown 文件 → 排版好的 Word 文档。纯函数，确定性输出。

    返回结果描述字符串（与 tools/documents.gen_docx 同款格式）。
    """
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as exc:
        raise ImportError("缺少 python-docx，请执行: pip install python-docx") from exc

    src = Path(markdown_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"Markdown 源文件不存在: {src}")

    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)

    if title:
        doc.add_heading(title, level=0)

    # P1 修复 1：title 参数已渲染 level 0 标题，markdown 首个同文本的
    # 「# 标题」行跳过，不再双标题（导出模板首行固定是 # {title}）。
    _first_h1_done = False
    for raw_line in src.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # P1 修复 2：前导空格不算普通段落 —— 「  - 截止：…」这类嵌套
        # 列表项按缩进层级走 List Bullet / List Bullet 2。
        indent = len(line) - len(line.lstrip())
        body = line.lstrip()
        if line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            text = line[2:].strip()
            is_dup_title = (title and not _first_h1_done and text == title)
            _first_h1_done = True
            if is_dup_title:
                continue
            doc.add_heading(text, level=1)
        elif body.startswith(("- ", "* ")):
            _add_runs(doc.add_paragraph(style=_bullet_style(indent)),
                      body[2:].strip())
        elif re.match(r"^\d+[.)]\s+", body):
            _add_runs(doc.add_paragraph(style="List Number"),
                      re.sub(r"^\d+[.)]\s+", "", body).strip())
        elif line.strip() in ("---", "***"):
            continue  # 分隔线不处理
        else:
            _add_runs(doc.add_paragraph(), line.strip())

    out = Path(output_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc.save(str(out))
    except PermissionError as exc:
        raise PermissionError(
            f"无法写入 {out}，文件可能正被 Word/WPS 打开，请关闭后重试。"
        ) from exc
    return f"已生成 {out}（{len(doc.paragraphs)} 个段落）"
