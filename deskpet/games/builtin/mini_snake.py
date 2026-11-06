"""deskpet.games.builtin.mini_snake —— 页面内置贪吃蛇（规则版）

阶段 2：纯规则/状态机，不接模型。
阶段 4：实时执行层确定性补齐——
    - plan_actions()：按状态快照（蛇头/食物/蛇身/网格）做确定性评分选方向，
      动作名一律来自本游戏 ActionWhitelist；seed 只用于食物位置，
      同 seed + 同状态两次调用返回完全一致的动作序列；
    - translate_strategy() 签名不变（插件接口兼容）：无 state 时仍返回
      原固定探索序列（向后兼容），有 state 时调 plan_actions；
    - 禁止逐帧调模型：本文件零模型调用，策略/复盘/解说都不进实时执行层。
"""

from __future__ import annotations

from collections import deque

from deskpet.games.actions import ActionWhitelist, GameAction
from deskpet.games.registry import GameRegistration, register_game


_ACTIONS = ActionWhitelist("mini-snake")
for name, key in [("up", "ArrowUp"), ("down", "ArrowDown"),
                  ("left", "ArrowLeft"), ("right", "ArrowRight"),
                  ("wait", None)]:
    if key:
        _ACTIONS.register(GameAction(name, "key", {"key": key}, name))
    else:
        _ACTIONS.register(GameAction(name, "none", {}, name))


def default_strategy() -> dict:
    """默认规则策略：朝食物方向走，避开墙。

    实时执行层调 translate_strategy 把它翻成白名单动作序列。
    不进模型，确定性。
    """
    return {"strategy_name": "greedy_food", "params": {"avoid_walls": True}}


# 无状态时的固定探索序列（阶段 2 行为，向后兼容保留）
_EXPLORE_SEQ = ["up", "right", "down", "left"]
# 候选方向的固定枚举顺序（同为最高分时取先者 → 确定性 tie-break）
_DIRECTIONS = (("up", 0, -1), ("down", 0, 1),
               ("left", -1, 0), ("right", 1, 0))
_DIR_DELTA = {name: (dx, dy) for name, dx, dy in _DIRECTIONS}


def _cell(obj) -> tuple[int, int] | None:
    """{"x": .., "y": ..} → (int, int)；形状不对返回 None（不猜不编）。"""
    if not isinstance(obj, dict) or "x" not in obj or "y" not in obj:
        return None
    try:
        return (int(obj["x"]), int(obj["y"]))
    except (TypeError, ValueError):
        return None


def _food_from_seed(seed: int | None, k: int,
                    occupied: set, w: int, h: int) -> tuple[int, int]:
    """由 seed 确定性派生第 k 颗食物位置（seed 只用于食物位置）。

    线性探测避开蛇身，探测序本身也由 seed 决定 → 完全可复现。
    网格填满时兜底 (0, 0)（只影响评分目标，不再生成新格子）。
    """
    s = int(seed) & 0x7FFFFFFF if seed is not None else 0
    for i in range(w * h):
        pos = ((s + k + i) % w, (s // max(w, 1) + k * 7 + i * 3) % h)
        if pos not in occupied:
            return pos
    return (0, 0)


def _aggression_of(strategy: dict) -> float:
    """把策略折算成抢分权重 0~1（只影响评分权重，不影响确定性）。"""
    params = strategy.get("params") if isinstance(strategy, dict) else None
    params = params if isinstance(params, dict) else {}
    name = strategy.get("strategy_name", "") if isinstance(strategy, dict) else ""
    if isinstance(params.get("aggression"), (int, float)):
        return min(1.0, max(0.0, float(params["aggression"])))
    if name == "aggressive":
        return 0.8
    if name == "defensive":
        return 0.2
    return 0.5  # balanced / greedy_food / 未知 → 均衡


def plan_actions(state: dict, strategy: dict,
                 seed: int | None = None) -> list[str]:
    """确定性实时执行层：按状态快照评分选方向（纯规则，不进模型）。

    输入：state = {"grid": {"w","h"}, "head": {"x","y"},
                  "body": [{"x","y"}, ...], "food": {"x","y"} | 缺失}
    规则：
        - 评分主项 = 曼哈顿距离取负（越近越好）；
        - defensive 权重加贴墙惩罚（贴边避险），aggressive 权重抢分；
        - 撞墙/撞身的方向直接出局；
        - seed 只用于食物位置（缺失时由 seed 派生，吃到后由 seed 派生下一颗）；
        - 候选顺序固定 + 严格大于比较 → 同 seed + 同状态必然同序列；
        - 动作名全部来自 ActionWhitelist（up/down/left/right/wait）。
    """
    grid = state.get("grid") if isinstance(state, dict) else None
    grid = grid if isinstance(grid, dict) else {}
    try:
        w, h = max(1, int(grid.get("w", 20))), max(1, int(grid.get("h", 20)))
    except (TypeError, ValueError):
        w, h = 20, 20
    head = _cell(state.get("head")) if isinstance(state, dict) else None
    if head is None:
        return list(_EXPLORE_SEQ)  # 无有效状态：固定探索序列（确定性）
    food = _cell(state.get("food")) if isinstance(state, dict) else None

    # 蛇身有序队列（头在左、尾在右），头单独追踪；body 里混着头也去重
    snake = deque(head)
    cells = set()
    for c in (state.get("body") or []) if isinstance(state, dict) else []:
        cc = _cell(c)
        if cc is not None and cc != head:
            snake.append(cc)
            cells.add(cc)

    eaten = 0
    if food is None:
        food = _food_from_seed(seed, eaten, cells | {head}, w, h)

    aggression = _aggression_of(strategy)
    actions: list[str] = []
    max_steps = w * h  # 蛇身满格即不可能再走，硬上限防死循环
    for _ in range(max_steps):
        tail = snake[-1] if len(snake) > 1 else None
        best_name, best_score = None, None
        for name, dx, dy in _DIRECTIONS:
            nh = (head[0] + dx, head[1] + dy)
            if not (0 <= nh[0] < w and 0 <= nh[1] < h):
                continue                      # 撞墙出局
            if nh in cells and not (nh == tail and nh != food):
                continue                      # 撞身出局（尾巴让位且不进食物除外）
            dist = abs(nh[0] - food[0]) + abs(nh[1] - food[1])
            score = -10.0 * dist
            margin = min(nh[0], nh[1], w - 1 - nh[0], h - 1 - nh[1])
            score += (1.0 - aggression) * 0.5 * margin  # 保守档贴边避险
            if best_score is None or score > best_score:
                best_name, best_score = name, score
        if best_name is None:
            break                             # 无路可走：到此为止（确定性）
        actions.append(best_name)
        dx, dy = _DIR_DELTA[best_name]
        head = (head[0] + dx, head[1] + dy)
        if head == food:
            eaten += 1                        # 吃到：蛇变长，seed 派生下一颗
            food = _food_from_seed(seed, eaten, cells | {head}, w, h)
        else:
            tail = snake.pop()                # 没吃到：尾巴让位
            cells.discard(tail)
        snake.appendleft(head)
        cells.add(head)
    return actions


def register() -> None:
    """幂等注册：已注册则跳过（同一进程内多个 TestClient/app 都会调）。"""
    from deskpet.games.registry import GAME_REGISTRY
    if "mini-snake" in GAME_REGISTRY:
        return
    register_game(GameRegistration(
        game_id="mini-snake",
        name="贪吃蛇（规则版）",
        plugin=_SnakePlugin(),
        brain_levels_supported=(1, 2),
        enabled=True,
        notes="阶段 4：策略/复盘/解说层齐备，实时执行层纯规则确定性"))


class _SnakePlugin:
    game_id = "mini-snake"
    name = "贪吃蛇（规则版）"

    def action_whitelist(self) -> ActionWhitelist:
        return _ACTIONS

    def default_strategy(self) -> dict:
        return default_strategy()

    def read_state_snapshot(self, dom_context) -> dict:
        # 前端传蛇头位置、食物位置、蛇身、网格大小
        return dom_context if isinstance(dom_context, dict) else {}

    def translate_strategy(self, strategy: dict) -> list[str]:
        """把策略翻成动作名序列（签名不变，插件接口兼容）。

        无 state（或 state 无效）→ 固定探索序列（阶段 2 行为，向后兼容）；
        有 state → 调 plan_actions 做确定性评分（seed 可选，只管食物位置）。
        策略只影响评分权重，绝不逐帧调模型。
        """
        state = strategy.get("state") if isinstance(strategy, dict) else None
        if isinstance(state, dict) and state.get("head") is not None:
            seed = strategy.get("seed")
            seed = seed if isinstance(seed, int) or seed is None else None
            return plan_actions(state, strategy, seed)
        return list(_EXPLORE_SEQ)
