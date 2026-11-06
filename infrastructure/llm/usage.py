"""模型用量的基础计费计算，不依赖具体应用。"""
from typing import Optional, Dict


def estimate_cost(pricing: Optional[Dict[str, float]],
                  input_tokens: int, output_tokens: int) -> Optional[float]:
    """
    按模型价格折算成本（元）。pricing 形如：
        {"input_per_mtok": 2.0, "output_per_mtok": 3.0}   # 元 / 百万 token
    未配置价格的模型（如本地 Ollama）返回 None（界面上显示"免费/未知"）。
    """
    if not pricing or (not input_tokens and not output_tokens):
        return None
    in_price = float(pricing.get("input_per_mtok", 0) or 0)
    out_price = float(pricing.get("output_per_mtok", 0) or 0)
    if in_price == 0 and out_price == 0:
        return 0.0
    return input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price


