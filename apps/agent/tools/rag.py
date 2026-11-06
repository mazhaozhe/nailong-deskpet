"""将基础检索能力适配为 Agent 工具。"""
from config.settings import settings
from infrastructure.retrieval.rag import CHROMA_AVAILABLE, retrieve


def build_rag_tools() -> list:
    """
    构造暴露给 Agent 的 rag_search 工具。
    未开启 / 依赖缺失时返回 []，并在日志说明原因。
    """
    if not settings.rag_enabled:
        return []
    if not CHROMA_AVAILABLE:
        print("[rag] RAG_ENABLED=true 但 chromadb 未安装，已跳过 RAG 工具。"
              "（安装：pip install -r requirements-rag.txt）")
        return []

    from langchain.tools import tool
    from pydantic import BaseModel, Field

    class RagSearchArgs(BaseModel):
        query: str = Field(..., description="检索关键词或问题")

    @tool(args_schema=RagSearchArgs)
    def rag_search(query: str) -> str:
        """检索本地知识库，返回与问题最相关的资料片段。回答资料相关问题时先用本工具。"""
        try:
            results = retrieve(query)
        except Exception as exc:
            raise RuntimeError(f"知识库检索失败: {exc}") from exc
        if not results:
            return (
                f"知识库中没有检索到与「{query}」相关的内容。"
                "如需基于资料回答，请先补充资料；不要编造内容。"
            )
        return "\n---\n".join(f"[片段 {i + 1}] {r}" for i, r in enumerate(results))

    return [rag_search]
