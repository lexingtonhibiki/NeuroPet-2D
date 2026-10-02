# -*- coding: utf-8 -*-
"""投喂-变身效果引擎(B4,纯新增模块,零接线依赖)。

施工图:docs/references/投喂变身_Q版成长调研.md §一(食物-效果映射表)、
§四(avatar.json 持久化结构)。设计边界(接线时零改动的前提):

- 本模块 **只产出效果指令**(EffectCommand 子类),**不 import 也不修改**
  core/app、brain、render 任何既有模块 —— 指令由接线方消费:
    ScaleChange(delta)      → app.set_pet_scale(h, 当前档+delta)(冻结管线,app.py:522)
    SpeedMultiplier(mult,s) → 限时 buff 后重建 body(仿 _build_body)
    ChibiToggle(on)         → 渲染层 chibi 重烘焙 + chibi_params(调研 §二)
    Iridescence(on)         → 色板 hue 相位(调研 §三)
    TrustBoost(amount)      → brain 侧情绪调制(fed 载荷扩字段,base.py:115)
    BiteBoost(mult)         → 该食物后续 bite ×mult(_try_eat 侧,app.py:840)
    EffectExpired(cat,why)  → 回收:重建 body/traits 恢复 1.0
- 时间基 = 墙钟 time.time()(与调研 §四 avatar.json "until" 字段同基);
  所有入口都收显式 now 参数,测试可注入虚拟时钟,天然确定性。
- 叠加规则(调研 §一):同类互斥(每类别最多 1 条,后者顶替,顶替时先发
  EffectExpired(reason="replaced")),异类共存;scale 是永久档位位移,
  引擎**不记账**(档位真源在 scale.json,由 set_pet_scale 自己持久化)。
- 持久化:serialize()/load() 与 avatar.json 结构对齐(调研 §四样例);
  clear_memory 只删 memory.json,avatar.json 由接线方决定读写时机,
  恢复原形走 clear()(清 sticky+timed,不动图鉴/统计,更不动档位)。
- import 零副作用:仅 stdlib,无模块级 IO / 线程 / 窗口。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

# 与 core/app.SCALE_CHOICES 对齐的只读复制(避免 import 重模块,保持零副作用);
# 引擎只用于档位越界钳制与档位计数,真源仍以 app 侧为准。
SCALE_CHOICES: tuple[float, ...] = (0.5, 0.75, 1.0, 1.5, 2.0)

AVATAR_VERSION = 1          # avatar.json 结构版本(向前兼容用)

# ---------------- 效果参数(调研 §一 映射表 v1 定值) ----------------
SPEED_MULT = 1.3            # 蓝糖珠:速度倍率
SPEED_DURATION_S = 120.0    # 蓝糖珠:限时(墙钟秒)
TRUST_BOOST = 0.08          # 绿叶:信任增量(即时)
TRUST_CLEAR_FEAR = True     # 绿叶:恐惧清零
GOLD_DELTA = +1             # 金屑:体型 +1 档(永久,钳在五档内)
SILVER_DELTA = -1           # 银尘:体型 −1 档(永久)
PREF_BITE_MULT = 1.5        # 偏好食物:bite ×1.5(蟑油/蝇醋,调研 §一)
NONPREF_ODOR_MULT = 0.7     # 非偏好:odor_strength ×0.7("没那么急")
PREF_WEIGHT_MULT = 3.0      # 抽取权重:本物种偏好食物 ×3(grease/vinegar→30% 档)
NONPREF_WEIGHT_MULT = 0.7   # 抽取权重:他物种专属食物 ×0.7

# 效果类别(同类互斥的键;序列化键与调研 §四 avatar.json 样例一致)
CAT_SPEED = "speed"
CAT_CHIBI = "chibi"
CAT_HUE = "hue"


class FoodKind:
    """食物 kind 常量表(core/contracts.FoodItem.kind 的取值域,纯字符串)。"""

    CRUMB = "crumb"          # 普通屑:现状语义不变(无效果)
    GREEN = "green"          # 绿叶:信任+0.08、恐惧清零(即时)
    BLUE = "blue"            # 蓝糖珠:速度×1.3,限时 120s(墙钟)
    GOLD = "gold"            # 金屑:体型 +1 档(永久)
    SILVER = "silver"        # 银尘:体型 −1 档(永久)
    PURPLE = "purple"        # 紫浆果:Q 版(sticky,到恢复)
    RAINBOW = "rainbow"      # 虹露:虹色(sticky)
    GREASE = "grease"        # 油脂球:蟑螂偏好(bite×1.5)
    VINEGAR = "vinegar"      # 果醋滴:果蝇偏好(bite×1.5)

    ALL: tuple[str, ...] = (CRUMB, GREEN, BLUE, GOLD, SILVER,
                            PURPLE, RAINBOW, GREASE, VINEGAR)


@dataclass(frozen=True)
class FoodSpec:
    """食物属性(调研 §一 属性模型;windowing 上色 / odor 修饰都读这里)。"""

    kind: str
    label: str                    # 图鉴/提示用中文名
    color: str                    # 绘制色(hex;windowing.py 按 kind 上色)
    size: float                   # 半径系数(0.6~1.5)
    nutrition: float              # 胃增量(替换 _try_eat 写死的 0.18)
    weight: float                 # 基线抽取权重(归一化前)
    effect_category: str | None   # None=无效果(crumb/instant 不占互斥槽)
    prefs: frozenset[str] = frozenset()   # 偏好该食物的物种 id(如 {"roach"})

    @property
    def has_effect(self) -> bool:
        return self.kind != FoodKind.CRUMB


# 基线权重表(归一化前;grease/vinegar 基线 10,偏好物种 ×3 → ≈30%,对应
# 调研 §一"30% 替换 crumb 权重"的意图;非偏好 ×0.7)。总和 116,仅作相对权重。
FOODS: dict[str, FoodSpec] = {
    f.kind: f for f in (
        FoodSpec(FoodKind.CRUMB,  "面包屑", "#d8c290", 1.0, 0.18, 40.0, None),
        FoodSpec(FoodKind.GREEN,  "嫩叶",   "#7ec850", 1.1, 0.25, 22.0, "instant"),
        FoodSpec(FoodKind.BLUE,   "蓝糖珠", "#58a6ff", 0.8, 0.15, 14.0, CAT_SPEED),
        FoodSpec(FoodKind.GOLD,   "金屑",   "#ffd24a", 1.3, 0.20,  6.0, "scale"),
        FoodSpec(FoodKind.SILVER, "银尘",   "#c9d1d9", 0.7, 0.15,  6.0, "scale"),
        FoodSpec(FoodKind.PURPLE, "紫浆果", "#b48cff", 1.0, 0.20,  5.0, CAT_CHIBI),
        FoodSpec(FoodKind.RAINBOW,"虹露",   "#ff9edb", 0.9, 0.20,  3.0, CAT_HUE),
        FoodSpec(FoodKind.GREASE, "油脂球", "#8a6d4b", 1.2, 0.28, 10.0, "instant",
                 prefs=frozenset({"roach"})),
        FoodSpec(FoodKind.VINEGAR,"果醋滴", "#d9a441", 0.9, 0.22, 10.0, "instant",
                 prefs=frozenset({"fly"})),
    )
}


# ================= 面板食物选择器查询接口(第十轮,只读) =================
# 效果中文描述(调研 §一 映射表的 UI 面向版本;数值与上方效果参数常量同源)。
# 选择器悬停 tooltip 直接展示,改文案不动引擎语义。
FOOD_EFFECT_ZH: dict[str, str] = {
    FoodKind.CRUMB:  "普通面包屑:无特殊效果,安全的主食(饱食 +18%)",
    FoodKind.GREEN:  "嫩叶:信任 +0.08,恐惧清零(即时)",
    FoodKind.BLUE:   f"蓝糖珠:速度 ×{SPEED_MULT:g},限时 {SPEED_DURATION_S:g}s",
    FoodKind.GOLD:   "金屑:体型永久 +1 档(钳在五档内)",
    FoodKind.SILVER: "银尘:体型永久 −1 档(钳在五档内)",
    FoodKind.PURPLE: "紫浆果:变身 Q 版造型(sticky,「恢复原形」解除)",
    FoodKind.RAINBOW: "虹露:变身虹彩光泽(sticky,「恢复原形」解除)",
    FoodKind.GREASE: "油脂球:蟑螂的偏好食物,进食速度 ×1.5;对果蝇只是气味更淡",
    FoodKind.VINEGAR: "果醋滴:果蝇的偏好食物,进食速度 ×1.5;对蟑螂只是气味更淡",
}


def food_catalog() -> tuple[tuple[str, str, str, str], ...]:
    """食物种类清单(选择器 UI 用):按 FoodKind.ALL 顺序返回
    (kind, 中文名, 色块颜色, 效果中文描述) 四元组;只读、零副作用。"""
    return tuple(
        (spec.kind, spec.label, spec.color,
         FOOD_EFFECT_ZH.get(spec.kind, "无特殊效果"))
        for spec in (FOODS[k] for k in FoodKind.ALL))


# ================= 效果指令(接线方消费的唯一协议) =================
@dataclass(frozen=True)
class EffectCommand:
    """效果指令基类:引擎不直接改 app/brain,接线方按类型分发。"""


@dataclass(frozen=True)
class ScaleChange(EffectCommand):
    """体型档位位移(永久):接线方 → set_pet_scale(h, snap(当前档+kΔ))。"""
    delta: int                    # +1 金屑 / -1 银尘(档位索引位移,五档钳制)


@dataclass(frozen=True)
class SpeedMultiplier(EffectCommand):
    """速度倍率(限时):接线方重建 body;到期由 EffectExpired("speed") 回收。"""
    mult: float
    duration_s: float             # 新生效时长;rebuild 场景=剩余时长


@dataclass(frozen=True)
class ChibiToggle(EffectCommand):
    """Q 版开关(sticky):渲染层重烘焙 + 参数半场(调研 §二)。"""
    on: bool


@dataclass(frozen=True)
class Iridescence(EffectCommand):
    """虹色开关(sticky):色板 hue 相位注入(调研 §三)。"""
    on: bool


@dataclass(frozen=True)
class TrustBoost(EffectCommand):
    """信任增量(即时):接线方经 fed 载荷/直调调制 brain 情绪。"""
    amount: float
    clear_fear: bool = False      # True=同时把 fear 清零(绿叶)


@dataclass(frozen=True)
class BiteBoost(EffectCommand):
    """进食速度(即时):本食物剩余量吃完前 bite ×mult(_try_eat 侧)。"""
    mult: float


@dataclass(frozen=True)
class EffectExpired(EffectCommand):
    """效果回收指令:接线方按 category 恢复默认(重建 body/traits)。"""
    category: str                 # speed / chibi / hue
    reason: str = "expired"       # expired 墙钟到期 / replaced 同类顶替 / cleared 恢复原形


@dataclass(frozen=True)
class UnlockNotice(EffectCommand):
    """图鉴首次解锁通知(apply 首吃某 kind 时产出;接线方 _remember("变身了!"))。"""
    kind: str


@dataclass
class ActiveEffect:
    """一条生效中的效果(类别互斥槽的占位者)。"""
    category: str
    payload: dict[str, Any]
    started: float                # 墙钟生效起点
    until: float | None = None    # None=sticky(无墙钟,只被顶替/清除)

    def expired(self, now: float) -> bool:
        return self.until is not None and now >= float(self.until)

    def remaining(self, now: float) -> float:
        if self.until is None:
            return float("inf")
        return max(0.0, float(self.until) - float(now))


# ================= 抽取与食性 =================
def _adjusted_weight(spec: FoodSpec, species_id: str | None) -> float:
    """按食性偏好调整抽取权重(调研 §一:只做偏好,不做惩罚)。"""
    if not spec.prefs or species_id is None:
        return spec.weight
    if species_id in spec.prefs:
        return spec.weight * PREF_WEIGHT_MULT
    return spec.weight * NONPREF_WEIGHT_MULT


def roll_food(rng: random.Random, species_id: str | None = None) -> FoodSpec:
    """按权重抽一种食物(传入同种子 rng 即确定性可测)。

    仅消耗 rng.random() 一次:同 (seed, species_id) 序列完全可复现。
    """
    specs = [FOODS[k] for k in FoodKind.ALL]
    weights = [_adjusted_weight(s, species_id) for s in specs]
    total = sum(weights)
    x = rng.random() * total
    acc = 0.0
    for spec, w in zip(specs, weights):
        acc += w
        if x < acc:
            return spec
    return specs[-1]


def food_modifiers(species_id: str | None, kind: str) -> dict[str, float]:
    """该物种吃该食物的即时修饰(接线方在 _try_eat/odor 侧读)。

    - 偏好食物(蟑油/蝇醋):bite_mult=1.5(进食更快)
    - 他物种的专属食物:odor_mult=0.7(气味弱,"没那么急",不做惩罚)
    - 其余食物:双 1.0
    """
    spec = FOODS.get(kind)
    if spec is None or not spec.prefs or species_id is None:
        return {"bite_mult": 1.0, "odor_mult": 1.0}
    if species_id in spec.prefs:
        return {"bite_mult": PREF_BITE_MULT, "odor_mult": 1.0}
    return {"bite_mult": 1.0, "odor_mult": NONPREF_ODOR_MULT}


def _on_commands(category: str, payload: Mapping[str, Any],
                 remaining_s: float | None = None) -> list[EffectCommand]:
    """由类别+载荷产出"生效"指令(rebuild 时 remaining_s=剩余时长)。"""
    if category == CAT_SPEED:
        dur = float(payload.get("duration_s", SPEED_DURATION_S))
        if remaining_s is not None:
            dur = min(dur, remaining_s)
        return [SpeedMultiplier(float(payload.get("mult", SPEED_MULT)), dur)]
    if category == CAT_CHIBI:
        return [ChibiToggle(bool(payload.get("on", True)))]
    if category == CAT_HUE:
        return [Iridescence(bool(payload.get("on", True)))]
    return []


# ================= 效果引擎 =================
class EffectEngine:
    """active effects 账户:timed(墙钟)/ sticky / instant 三类管理 + 图鉴记账。

    纯状态机,不碰窗口/总线/大脑;接线方每帧(或节流)调 tick(now),
    进食时调 apply(kind, now, species_id),启动时 load(avatar dict) 后
    用 rebuild_commands() 重建渲染/body 状态。
    """

    def __init__(self) -> None:
        self._effects: dict[str, ActiveEffect] = {}       # 键=类别(互斥锁)
        self._unlocked: dict[str, float] = {}             # kind → 首次解锁墙钟
        self._fed_total = 0
        self._by_kind: dict[str, int] = {}

    # ---------- 查询 ----------
    def active(self, category: str) -> ActiveEffect | None:
        return self._effects.get(category)

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted(self._effects))

    def is_unlocked(self, kind: str) -> bool:
        return kind in self._unlocked

    @property
    def unlocked(self) -> tuple[str, ...]:
        """已解锁 kind(按首次解锁顺序,图鉴渲染直接用)。"""
        return tuple(self._unlocked)

    def unlocked_at(self, kind: str) -> float | None:
        return self._unlocked.get(kind)

    @property
    def fed_total(self) -> int:
        return self._fed_total

    def fed_by_kind(self) -> dict[str, int]:
        return dict(self._by_kind)

    # ---------- 记账(图鉴 + 统计,apply 内部自动调用) ----------
    def note_fed(self, kind: str, now: float) -> bool:
        """记一次投喂;首次该 kind 返回 True(接线方借此发"解锁!"提醒)。"""
        now = float(now)
        self._fed_total += 1
        self._by_kind[kind] = self._by_kind.get(kind, 0) + 1
        if kind not in self._unlocked:
            self._unlocked[kind] = now
            return True
        return False

    # ---------- 效果入口 ----------
    def apply(self, kind: str, now: float | None = None,
              species_id: str | None = None) -> list[EffectCommand]:
        """吃下 kind:记账 + 落账效果 + 产出指令列表(接线方按序消费)。"""
        now = time.time() if now is None else float(now)
        spec = FOODS.get(kind)
        cmds: list[EffectCommand] = []
        if self.note_fed(kind, now):
            # 首次解锁通知排在最前(接线方 _remember("变身了!",salience=0.8) 用)
            cmds.append(UnlockNotice(kind))
        if spec is None or spec.effect_category is None:
            return cmds                       # crumb / 未知 kind:仅记账

        cat = spec.effect_category
        if cat == "scale":                    # 永久:引擎不记账(真源=scale.json)
            cmds.append(ScaleChange(GOLD_DELTA if kind == FoodKind.GOLD
                                    else SILVER_DELTA))
            return cmds
        if cat == "instant":                  # 即时:不占互斥槽
            if kind == FoodKind.GREEN:
                cmds.append(TrustBoost(TRUST_BOOST, clear_fear=TRUST_CLEAR_FEAR))
            elif kind in (FoodKind.GREASE, FoodKind.VINEGAR):
                if species_id is not None and species_id in spec.prefs:
                    cmds.append(BiteBoost(PREF_BITE_MULT))
                # 非偏好吃油/醋:只走 odor 修饰(food_modifiers),无指令
            return cmds

        # timed / sticky:同类互斥,顶替先回收
        old = self._effects.get(cat)
        if old is not None:
            cmds.append(EffectExpired(cat, reason="replaced"))
        if cat == CAT_SPEED:
            payload = {"mult": SPEED_MULT, "duration_s": SPEED_DURATION_S}
            eff = ActiveEffect(cat, payload, started=now,
                               until=now + SPEED_DURATION_S)
        elif cat == CAT_CHIBI:
            payload = {"on": True}
            eff = ActiveEffect(cat, payload, started=now, until=None)
        else:                                 # CAT_HUE
            payload = {"on": True}
            eff = ActiveEffect(cat, payload, started=now, until=None)
        self._effects[cat] = eff
        cmds.extend(_on_commands(cat, payload))
        return cmds

    def tick(self, now: float | None = None) -> list[EffectCommand]:
        """墙钟回收:到期 timed 效果产出 EffectExpired(reason="expired")。

        sticky 永不因 tick 到期;返回空列表=无事发生(每帧调用零成本)。
        """
        now = time.time() if now is None else float(now)
        cmds: list[EffectCommand] = []
        for cat in [c for c, e in self._effects.items() if e.expired(now)]:
            del self._effects[cat]
            cmds.append(EffectExpired(cat, reason="expired"))
        return cmds

    def clear(self) -> list[EffectCommand]:
        """恢复原形:清 sticky + timed(不动图鉴/统计/档位——档位按钮单独回 1×)。"""
        cmds: list[EffectCommand] = []
        if self._effects.pop(CAT_CHIBI, None) is not None:
            cmds.append(ChibiToggle(False))
        if self._effects.pop(CAT_HUE, None) is not None:
            cmds.append(Iridescence(False))
        if self._effects.pop(CAT_SPEED, None) is not None:
            cmds.append(EffectExpired(CAT_SPEED, reason="cleared"))
        return cmds

    def rebuild_commands(self, now: float | None = None) -> list[EffectCommand]:
        """启动接线:按当前账户重建渲染/body 状态(timed 带剩余时长)。"""
        now = time.time() if now is None else float(now)
        cmds: list[EffectCommand] = []
        for cat in self.categories():
            eff = self._effects[cat]
            cmds.extend(_on_commands(cat, eff.payload, eff.remaining(now)))
        return cmds

    # ---------- 持久化(与 avatar.json 结构对齐,调研 §四) ----------
    def serialize(self) -> dict[str, Any]:
        effects: dict[str, dict[str, Any]] = {}
        for cat, eff in self._effects.items():
            d = dict(eff.payload)
            if eff.until is not None:
                d["until"] = float(eff.until)
                d["started"] = float(eff.started)
            effects[cat] = d
        return {
            "version": AVATAR_VERSION,
            "effects": effects,
            "unlocked": list(self._unlocked),
            "unlocked_at": {k: float(t) for k, t in self._unlocked.items()},
            "stats": {"fed_total": self._fed_total,
                      "by_kind": dict(self._by_kind)},
        }

    def load(self, data: Mapping[str, Any], now: float | None = None) -> "EffectEngine":
        """从 avatar dict 恢复(覆盖式,返回 self 便于 `EffectEngine().load(d)`)。

        已过期的 timed 直接丢弃(重启即失效);只接受引擎认识的类别键,
        未知键忽略(向前兼容);损坏字段逐项兜底,绝不抛异常。
        """
        now = time.time() if now is None else float(now)
        self._effects.clear()
        self._unlocked.clear()
        self._fed_total = 0
        self._by_kind.clear()

        for kind, ts in (data.get("unlocked_at") or {}).items():
            try:
                self._unlocked[str(kind)] = float(ts)
            except (TypeError, ValueError):
                continue
        # unlocked 列表兜底(旧结构无 unlocked_at 时以 now 补时间)
        for kind in (data.get("unlocked") or []):
            self._unlocked.setdefault(str(kind), float(now))
        # 按 unlocked_at 时间排序恢复"首次解锁顺序"
        ordered = sorted(self._unlocked.items(), key=lambda kv: kv[1])
        self._unlocked.clear()
        self._unlocked.update(ordered)

        stats = data.get("stats") or {}
        try:
            self._fed_total = int(stats.get("fed_total", 0))
        except (TypeError, ValueError):
            self._fed_total = 0
        for kind, n in (stats.get("by_kind") or {}).items():
            try:
                self._by_kind[str(kind)] = int(n)
            except (TypeError, ValueError):
                continue

        for cat, payload in (data.get("effects") or {}).items():
            cat = str(cat)
            if cat not in (CAT_SPEED, CAT_CHIBI, CAT_HUE):
                continue                     # 未知类别:忽略,不炸
            payload = dict(payload or {})
            until = payload.pop("until", None)
            started = payload.pop("started", now)
            try:
                until = float(until) if until is not None else None
            except (TypeError, ValueError):
                until = None
            if until is not None and now >= until:
                continue                     # 重启后已过期即失效(调研 §四)
            self._effects[cat] = ActiveEffect(cat, payload,
                                              started=float(started), until=until)
        return self

    # 便利迭代(图鉴 UI:for kind in engine.unlocked)
    def __iter__(self) -> Iterator[str]:
        return iter(self._unlocked)

    def __len__(self) -> int:
        return len(self._unlocked)
