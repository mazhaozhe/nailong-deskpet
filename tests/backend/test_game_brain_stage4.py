"""tests/backend/test_game_brain_stage4.py —— 阶段 4 游戏大脑补齐验收（不接真实 API）

覆盖施工指令 §2-⑤ 的验收项：
  - start(brain_level=2) → strategy → review → memory 一局跑通；关态同样跑完；
  - 同 seed + 同状态两次操作序列完全一致；动作名全在白名单；
  - 单局模型调用 ≤3（策略/复盘/解说共用同一 session_id 计数，第 4 次回落）；
  - 非法策略 JSON 被拦并回落 balanced；
  - 解说在关态回落静态台词且 degraded=true；
  - 固定种子 + 关模型仍能玩完一局（纯规则实时层）。

假 Key / FakeModel 口径同 tests/backend/test_deskpet_llm.py，绝不发真实请求。
运行：python -m pytest tests/backend/test_game_brain_stage4.py -q
"""

from __future__ import annotations

import pytest

from tests.backend.test_deskpet_llm import (
    FakeModel,
    _client,
    _inject_key,
    _patch_model_layer,
    _primary_specs,
    _write_settings,
)

FAKE_KEY = "sk-fake-abcd1234"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立数据目录 + 清 KEY + gateway/计数器复位（每个测试干净开局）。"""
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from apps.deskpet.backend import llm, model_counter
    llm.gateway.reset()
    model_counter.reset()
    return tmp_path


# ---------------------------------------------------------------------------
# 1. 开局串策略：start(brain_level=2) → strategy → review → memory 跑通
# ---------------------------------------------------------------------------
def test_full_game_chain_with_model(env, monkeypatch):
    """假 Key 档：开局带初始策略 → 中局策略 → 复盘写入 GameMemory。"""
    from apps.deskpet.backend.models import GameReview, GameStrategy

    def _strategy():
        return GameStrategy(strategy_name="aggressive",
                            params={"aggression": 0.7}, reason="抢分优先")

    def _review():
        return GameReview(lost_at="后期贪快", next_change="中期收一次",
                          takeaway="这局输在后半段")

    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs(
        {"parsed_for": {"GameStrategy": _strategy(), "GameReview": _review()}}))
    c = _client()

    r = c.post("/api/game/start", json={
        "game_id": "mini-snake", "brain_level": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is False
    assert body["initial_strategy"]["strategy_name"] == "aggressive"
    sid = body["session_id"]

    r = c.post("/api/game/strategy", json={
        "session_id": sid, "state_snapshot": {"score": 10, "hp": 3}})
    assert r.status_code == 200 and r.json()["degraded"] is False
    assert r.json()["model_calls_this_game"] == 2  # 开局 + 中局共用计数

    r = c.post("/api/game/review", json={
        "session_id": sid,
        "final_state": {"result": "lose", "game_id": "mini-snake"},
        "strategy_history": ["aggressive"]})
    assert r.status_code == 200 and r.json()["degraded"] is False
    r = c.get("/api/game/mini-snake/memory")
    mem = r.json()["data"]
    assert mem and mem[-1]["lost_at"] == "后期贪快"


def test_full_game_chain_off_state_still_completes(env):
    """关态：整条链照常跑完（策略 None/回落 balanced、复盘静态兜底、
    memory 照写、解说静态台词），零调用、绝不 500。"""
    from apps.deskpet.backend import model_counter, settings
    settings.set_switch(settings.SWITCH_OFF)
    c = _client()

    r = c.post("/api/game/start", json={
        "game_id": "mini-snake", "brain_level": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["initial_strategy"] is None and body["degraded"] is True
    sid = body["session_id"]

    r = c.post("/api/game/strategy", json={
        "session_id": sid, "state_snapshot": {"score": 1}})
    assert r.status_code == 200 and r.json()["degraded"] is True
    assert r.json()["data"]["strategy_name"] == "balanced"

    r = c.post("/api/game/review", json={
        "session_id": sid,
        "final_state": {"result": "lose", "game_id": "mini-snake"},
        "strategy_history": ["balanced"]})
    assert r.status_code == 200 and r.json()["degraded"] is True

    r = c.post("/api/game/commentary", json={
        "session_id": sid, "state_snapshot": {"score": 1},
        "strategy_name": "balanced"})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert body["data"]["line"], "关态静态兜底台词不空白"

    r = c.get("/api/game/mini-snake/memory")
    assert r.status_code == 200 and r.json()["data"]
    assert model_counter.total() == 0  # 关态整局零调用


def test_start_brain_level_1_no_strategy_no_error(env):
    """brain_level=1 仅规则：不走模型，initial_strategy=None，不报错。"""
    c = _client()
    r = c.post("/api/game/start", json={
        "game_id": "mini-snake", "brain_level": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["initial_strategy"] is None and body["degraded"] is False
    from apps.deskpet.backend import model_counter
    assert model_counter.total() == 0


# ---------------------------------------------------------------------------
# 2. 单局 ≤3：策略/复盘/解说共用同一 session_id 计数
# ---------------------------------------------------------------------------
def test_session_cap_shared_across_strategy_review_commentary(env, monkeypatch):
    """2 次策略 + 1 次解说后计数到 3；第 4 次（复盘）回落兜底并报上限。"""
    from apps.deskpet.backend.models import GameCommentary, GameReview, GameStrategy

    def _strategy():
        return GameStrategy(strategy_name="aggressive",
                            params={"aggression": 0.7}, reason="抢分优先")

    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs(
        {"parsed_for": {
            "GameStrategy": _strategy(),
            "GameReview": GameReview(lost_at="x", next_change="y",
                                     takeaway="z"),
            "GameCommentary": GameCommentary(line="打得好！", mood="cheer"),
        }}))
    c = _client()
    sid = "sess-cap-4"

    r = c.post("/api/game/strategy", json={
        "session_id": sid, "state_snapshot": {"tick": 1}})
    assert r.status_code == 200 and r.json()["degraded"] is False
    assert r.json()["model_calls_this_game"] == 1
    r = c.post("/api/game/strategy", json={
        "session_id": sid, "state_snapshot": {"tick": 2}})
    assert r.json()["model_calls_this_game"] == 2
    r = c.post("/api/game/commentary", json={
        "session_id": sid, "state_snapshot": {"tick": 2},
        "strategy_name": "aggressive"})
    assert r.status_code == 200 and r.json()["degraded"] is False
    assert r.json()["data"]["mood"] == "cheer"

    # 第 4 次（复盘）→ 超限回落静态兜底，不 500
    r = c.post("/api/game/review", json={
        "session_id": sid,
        "final_state": {"result": "lose", "game_id": "mini-snake"},
        "strategy_history": ["aggressive"]})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True and "上限" in body["degrade_reason"]

    from apps.deskpet.backend import llm, model_counter
    assert llm.gateway.session_calls(sid) == 3
    assert model_counter.by_route()["game/strategy"] == 2
    assert model_counter.by_route()["game/commentary"] == 1


# ---------------------------------------------------------------------------
# 3. 非法策略 JSON 被拦回落 balanced（解说同口径）
# ---------------------------------------------------------------------------
def test_invalid_strategy_json_falls_back_balanced(env, monkeypatch):
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({"invalid": True}))
    c = _client()
    r = c.post("/api/game/strategy", json={
        "session_id": "sess-invalid-4", "state_snapshot": {"score": 10}})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert body["data"]["strategy_name"] == "balanced"
    assert "校验失败" in body["degrade_reason"]


def test_invalid_commentary_falls_back_static(env, monkeypatch):
    """解说输出校验不过 → 静态兜底台词 + degraded=true，绝不 500。"""
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({"invalid": True}))
    c = _client()
    r = c.post("/api/game/commentary", json={
        "session_id": "sess-invalid-c", "state_snapshot": {"score": 3}})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert body["data"]["line"], "静态兜底台词不空白"
    assert "校验失败" in body["degrade_reason"]


# ---------------------------------------------------------------------------
# 4. 实时执行层确定性：同 seed 同状态 → 同序列；动作全在白名单
# ---------------------------------------------------------------------------
_STATE = {
    "grid": {"w": 12, "h": 12},
    "head": {"x": 5, "y": 6},
    "body": [{"x": 4, "y": 6}, {"x": 3, "y": 6}, {"x": 2, "y": 6}],
}
_STRATEGY = {"strategy_name": "greedy_food", "params": {"avoid_walls": True}}


def test_plan_actions_deterministic_and_whitelisted():
    from apps.deskpet.backend.games.actions import ActionWhitelist
    from apps.deskpet.backend.games.builtin import mini_snake

    a1 = mini_snake.plan_actions(_STATE, _STRATEGY, seed=42)
    a2 = mini_snake.plan_actions(_STATE, _STRATEGY, seed=42)
    assert a1 == a2, "同 seed + 同状态两次调用必须完全一致"
    assert a1, "有状态时应产出动作序列"
    wl: ActionWhitelist = mini_snake._ACTIONS
    assert set(a1) <= set(wl.allowed_names()), f"动作越白名单：{set(a1)}"
    # 策略只影响权重，不影响确定性
    b1 = mini_snake.plan_actions(_STATE, {"strategy_name": "defensive"}, 42)
    b2 = mini_snake.plan_actions(_STATE, {"strategy_name": "defensive"}, 42)
    assert b1 == b2


def test_translate_strategy_backward_compatible_and_with_state():
    from apps.deskpet.backend.games.builtin.mini_snake import register
    from apps.deskpet.backend.games.registry import get_game
    register()
    plugin = get_game("mini-snake").plugin

    # 无 state：固定探索序列（阶段 2 行为，向后兼容）
    assert plugin.translate_strategy({"strategy_name": "greedy_food"}) == \
        ["up", "right", "down", "left"]
    # 有 state：等于 plan_actions 结果
    from apps.deskpet.backend.games.builtin import mini_snake
    payload = dict(_STRATEGY, state=_STATE, seed=7)
    assert plugin.translate_strategy(payload) == \
        mini_snake.plan_actions(_STATE, _STRATEGY, seed=7)


# ---------------------------------------------------------------------------
# 5. 固定种子 + 关模型仍能玩完一局（纯规则实时层）
# ---------------------------------------------------------------------------
def _play_full_game(seed: int, grid_w=10, grid_h=10, foods_needed=4,
                    max_frames=120):
    """纯规则玩一局：每帧用 plan_actions 的首步推进，吃到食物长身并
    换下一颗（固定食物序列），吃满 foods_needed 颗/死局/堵死即终局。
    返回 (帧动作序列, 终局)——关模型、纯规则、可复现。"""
    from apps.deskpet.backend.games.builtin import mini_snake
    foods = [(2, 2), (8, 3), (5, 8), (1, 7), (7, 7), (3, 5)]
    head = {"x": grid_w // 2, "y": grid_h // 2}
    body = [{"x": grid_w // 2, "y": grid_h // 2},
            {"x": grid_w // 2 - 1, "y": grid_h // 2}]
    delta = {"up": (0, -1), "down": (0, 1),
             "left": (-1, 0), "right": (1, 0)}
    moves, eaten, outcome = [], 0, "timeout"
    for _ in range(max_frames):
        fx, fy = foods[eaten % len(foods)]
        acts = mini_snake.plan_actions(
            {"grid": {"w": grid_w, "h": grid_h}, "head": head,
             "body": body, "food": {"x": fx, "y": fy}},
            _STRATEGY, seed=seed)
        if not acts:
            outcome = "stuck"
            break
        mv = acts[0]
        moves.append(mv)
        dx, dy = delta[mv]
        nx, ny = head["x"] + dx, head["y"] + dy
        if not (0 <= nx < grid_w and 0 <= ny < grid_h) or \
                {"x": nx, "y": ny} in body[:-1]:
            outcome = "dead"
            break
        body.insert(0, {"x": nx, "y": ny})
        head = {"x": nx, "y": ny}
        if (nx, ny) == (fx, fy):
            eaten += 1                      # 吃到：长身，换下一颗
            if eaten >= foods_needed:
                outcome = "win"
                break
        else:
            body.pop()                      # 没吃到：尾巴让位
    return moves, outcome


def test_fixed_seed_off_model_plays_full_game(env):
    """关模型（开关=关）+ 固定种子：纯规则实时层玩完一局且两次完全一致。"""
    from apps.deskpet.backend import model_counter, settings
    settings.set_switch(settings.SWITCH_OFF)
    moves1, outcome1 = _play_full_game(seed=2026)
    moves2, outcome2 = _play_full_game(seed=2026)
    assert outcome1 == "win", f"固定种子纯规则应吃满 4 颗终局（实得 {outcome1}）"
    assert moves1 == moves2 and outcome1 == outcome2, \
        "同 seed 纯规则两局必须完全一致"
    from apps.deskpet.backend.games.builtin.mini_snake import _ACTIONS
    assert set(moves1) <= set(_ACTIONS.allowed_names()), "动作越白名单"
    assert model_counter.total() == 0, "实时执行层零模型调用"
