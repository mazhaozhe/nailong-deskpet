"""apps.deskpet.backend.games.registry —— 游戏插件注册表

brief 第五节：游戏做成可注册的插件，可持续加新游戏，不锁死现成文件；
主线只做页面内置小游戏，读 DOM/JS 结构化状态 + 白名单按键。

四者互不引用原则在这里落地：
    - 游戏 = 注册表里的插件（可换档、可整个关掉；关掉后桌宠仍按规则玩）
    - 游戏「大脑」也是注册表里的能力，与游戏本身解耦
    - 换皮/加游戏不动桌宠主线逻辑

每个游戏插件需实现 GamePlugin 接口；通过 register_game() 登记进全局表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from apps.deskpet.backend.games.actions import ActionWhitelist


class GamePlugin(Protocol):
    """游戏插件接口（实现阶段细化）。

    一个游戏插件要提供：
      - 元信息（id/名称/状态读取方式）
      - 白名单动作表（ActionWhitelist）
      - 默认规则策略（关掉大脑时的纯规则玩法，确定性可复现）
      - 状态快照读取（从 DOM/JS 读结构化状态供策略层用）
    """
    game_id: str
    name: str

    def action_whitelist(self) -> ActionWhitelist: ...
    def default_strategy(self) -> dict: ...
    def read_state_snapshot(self, dom_context: object) -> dict: ...
    def translate_strategy(self, strategy: dict) -> list[str]: ...
        # 把策略 JSON 翻译成白名单动作名序列（实时执行层用，不进模型）


@dataclass
class GameRegistration:
    game_id: str
    name: str
    plugin: object                 # GamePlugin 实例
    brain_levels_supported: tuple = (1, 2)  # 3 逐帧仅离线演示，线上契约已拒
    enabled: bool = True           # 可整体关掉；关掉后桌宠按规则玩
    notes: str = ""


# 全局游戏注册表（模块级，与 apps/agent/tools/registry.py 同思路）
GAME_REGISTRY: dict[str, GameRegistration] = {}


def register_game(reg: GameRegistration) -> None:
    if reg.game_id in GAME_REGISTRY:
        raise ValueError(f"游戏重复注册: {reg.game_id}")
    GAME_REGISTRY[reg.game_id] = reg


def get_game(game_id: str) -> Optional[GameRegistration]:
    return GAME_REGISTRY.get(game_id)


def list_games() -> list[GameRegistration]:
    return list(GAME_REGISTRY.values())


def disable_game(game_id: str) -> None:
    """整体关掉某游戏；关掉后桌宠仍按规则玩（brief：大脑不可用时游戏照常能玩）。"""
    reg = GAME_REGISTRY.get(game_id)
    if reg:
        reg.enabled = False


# 实现阶段在此注册内置游戏，例如：
#   from apps.deskpet.backend.games.builtin.thunder_fighter import ThunderFighterPlugin
#   register_game(GameRegistration("thunder-fighter", "雷电战机",
#                                  ThunderFighterPlugin(), brain_levels_supported=(1,2)))
