"""deskpet.games.actions —— 白名单动作表（游戏按键注册表）

brief 第五节硬约束：
    - 模型只输出策略 JSON，不输出单步按键
    - 能不能做由白名单动作表决定
    - 动作层与 run_shell 是同一类公网权限问题——绝不注册任意按键/任意命令
    - 禁止逐帧调模型；实时执行层永远不进模型

本文件定义「动作」的统一抽象与白名单校验。每个游戏插件注册自己允许的动作集，
策略层产出的 GameStrategy 必须能映射到该游戏已注册的白名单动作，否则校验失败
回落默认规则。

动作分类（与 brief 四层对应，实时执行层用）：
    key       键盘按键（白名单集合，如 ArrowUp/Space，不含任意 keycode）
    click     点击坐标（仅限游戏画布内，不允许点页面其它元素）
    none      不动作（等待）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class GameAction:
    """一个白名单动作的定义。"""
    name: str                                 # 动作名（如 fire / move_left）
    kind: str                                 # key / click / none
    params_schema: dict = field(default_factory=dict)
    # key: {"key": "Space"}  click: {"x": int, "y": int}  none: {}
    description: str = ""


class ActionWhitelist:
    """某游戏的白名单动作集合 + 校验。

    策略层输出的策略必须能被 translate_to_actions() 映射成本表里的动作；
    映射不出 → 校验失败 → 回落默认规则。
    """

    def __init__(self, game_id: str):
        self.game_id = game_id
        self._actions: dict[str, GameAction] = {}

    def register(self, action: GameAction) -> None:
        assert_safe_action(action)   # 注册即校验：禁用/未知动作类型一律拦截
        if action.name in self._actions:
            raise ValueError(f"动作重复注册: {action.name}")
        self._actions[action.name] = action

    def allowed_names(self) -> list[str]:
        return list(self._actions.keys())

    def validate(self, action_name: str) -> bool:
        """策略层/执行层调用前校验：动作是否在白名单内。"""
        return action_name in self._actions

    def get(self, action_name: str) -> Optional[GameAction]:
        return self._actions.get(action_name)


# ===========================================================================
# 内置游戏的白名单动作表（实现阶段填具体按键；此处给骨架示例）
# ===========================================================================
# 示例：thunder-fighter（已存在 games/thunder-fighter.html）
THUNDER_FIGHTER_ACTIONS = ActionWhitelist("thunder-fighter")
# 实现阶段注册，例如：
# THUNDER_FIGHTER_ACTIONS.register(GameAction("move_left",  "key", {"key": "ArrowLeft"}))
# THUNDER_FIGHTER_ACTIONS.register(GameAction("move_right", "key", {"key": "ArrowRight"}))
# THUNDER_FIGHTER_ACTIONS.register(GameAction("fire",       "key", {"key": "Space"}))
# THUNDER_FIGHTER_ACTIONS.register(GameAction("wait",       "none", {}))


# ===========================================================================
# 全局禁用动作（任何游戏都不允许注册，安全红线）
# ===========================================================================
FORBIDDEN_ACTION_KINDS = {
    "shell",      # 任意 shell 命令（同 run_shell 公网权限问题）
    "file_write", # 任意文件写入
    "file_read",  # 任意文件读取
    "url_open",   # 任意打开网址（打开网址只能用户手势前端完成，brief 第四节）
    "system_key", # 系统级按键（Alt+F4/Ctrl+Alt+Del 等）
}


ALLOWED_ACTION_KINDS = {"key", "click", "none"}


def assert_safe_action(action: GameAction) -> None:
    """注册动作时调用：白名单语义，只允许 key/click/none。

    先按禁用集合给可读报错，再按允许集合兜底——未知/自造的 kind
    （如 magic、network）同样拒绝，不能靠「不在黑名单里」就放行。
    调用点在 ActionWhitelist.register()，注册阶段即拦截。
    """
    if action.kind in FORBIDDEN_ACTION_KINDS:
        raise PermissionError(
            f"禁止注册动作类型 `{action.kind}`（公网安全红线）。"
            f"游戏动作只允许 key/click/none，且 key 必须是游戏白名单按键。")
    if action.kind not in ALLOWED_ACTION_KINDS:
        raise PermissionError(
            f"未知动作类型 `{action.kind}`：只允许 {sorted(ALLOWED_ACTION_KINDS)}。"
            f"白名单动作表不接受「不在黑名单里」即放行。")
