"""可选 RAG 基础设施：文档切分、Chroma 入库及相似度检索。"""

from pathlib import Path

from config.settings import settings
from infrastructure.observability import log_step

# ---------------------------------------------------------------------------
# 依赖探测：chroma 未安装时优雅降级
# ---------------------------------------------------------------------------
try:
    from langchain_chroma import Chroma
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False

_SPLITTER = None
_STORE = None


def _get_store() -> "Chroma":
    """懒加载向量库单例（使用 Chroma 默认 onnx 嵌入模型，无需 torch）。"""
    global _STORE, _SPLITTER
    if _STORE is None:
        persist_dir = settings.abs_path(settings.chroma_dir)
        _STORE = Chroma(persist_directory=str(persist_dir))
        _SPLITTER = RecursiveCharacterTextSplitter(
            chunk_size=500,      # 每块约 500 字
            chunk_overlap=50,    # 块间重叠 50 字，保留上下文连续性
        )
    return _STORE


def availability() -> str:
    """返回 RAG 可用性说明（给启动日志 / README 排障用）。"""
    if not settings.rag_enabled:
        return "未开启（.env 中 RAG_ENABLED=false）"
    if not CHROMA_AVAILABLE:
        return "已开启但依赖缺失（pip install -r requirements-rag.txt）"
    return "就绪"


def ingest(texts: list, metadatas: list | None = None) -> int:
    """文档入库：切分 → 向量化 → 写入 Chroma。返回切分后的块数。"""
    if not CHROMA_AVAILABLE:
        raise ImportError("RAG 依赖未安装: pip install -r requirements-rag.txt")
    store = _get_store()
    chunks = _SPLITTER.create_documents(
        [t if isinstance(t, str) else str(t) for t in texts],
        metadatas=metadatas,
    )
    store.add_documents(chunks)
    log_step("rag_ingest", docs=len(texts), chunks=len(chunks))
    return len(chunks)


def retrieve(query: str, k: int | None = None) -> list:
    """相似度检索：返回最相关的 k 个文本块。"""
    if not CHROMA_AVAILABLE:
        raise ImportError("RAG 依赖未安装: pip install -r requirements-rag.txt")
    store = _get_store()
    return [d.page_content for d in store.similarity_search(
        query, k=k or settings.rag_top_k
    )]


