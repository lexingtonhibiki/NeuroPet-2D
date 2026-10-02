"""记忆系统:联想层 AssociationMemory + 情景层 EpisodicMemory。

从 brain/base.py(ReflexBrain 占位)抽出并增强,规格见 docs/学习与记忆系统设计.md:
- 情景层:显著度随时间指数衰减(遗忘曲线),容量按智能等级 20/40/80/140/220,
  超容先淘汰低显著度,显著度过低即遗忘;
- 联想层:key 规范(见该文档 §3):``context:<topic>`` / ``human:<action>`` /
  ``cursor:<gesture>`` / ``odor:<kind>`` / ``social:<pet_id>``;
  valence∈[-1,1] 为情境效价(>0 趋近,<0 回避),weight 为置信度(越大越难覆盖),
  Hebbian 式更新 ``valence += Δv·lr·(1+0.3·level)·(1-weight)``。

仅用标准库;save()/load() 为纯 JSON 兼容结构,供 data/profiles/<pet_id>/memory.json 持久化。
"""
from __future__ import annotations

import math
import time

from neuropet.core.contracts import Association, MemoryRecord

#: 情景记忆容量(按智能等级 1-5)
LEVEL_CAP = {1: 20, 2: 40, 3: 80, 4: 140, 5: 220}
#: 联想容量 = 情景容量 × 2(单条更小,key 更多)
ASSOC_CAP_SCALE = 2
#: 情景显著度衰减时间常数(s):约 5 天衰减至 1/e
SALIENCE_TAU = 5 * 24 * 3600.0
#: 情景显著度低于该值即遗忘
SALIENCE_FLOOR = 0.05
#: 联想效价向 0 回落的时间常数(s):约 30 天(慢遗忘)
ASSOC_DECAY_TAU = 30 * 24 * 3600.0


def level_gain(level: int) -> float:
    """联想学习率随智能等级放大(等级 3 → ×1.9)。"""
    return 1.0 + 0.3 * max(1, min(5, int(level)))


def _cap(level: int) -> int:
    return LEVEL_CAP.get(max(1, min(5, int(level))), 80)


class EpisodicMemory:
    """情景层:带时间线与显著度的记忆条目,超容淘汰低显著度。"""

    def __init__(self, level: int = 3) -> None:
        self.level = max(1, min(5, int(level)))
        self.items: list[MemoryRecord] = []

    def remember(self, summary: str, salience: float = 0.5,
                 kind: str = "episodic", context: dict | None = None,
                 ts: float | None = None) -> None:
        self.items.append(MemoryRecord(ts=ts if ts is not None else time.time(),
                                       kind=kind, summary=summary,
                                       salience=max(0.0, min(1.0, salience)),
                                       context=context or {}))
        self.trim()

    def tick(self, dt: float) -> None:
        """遗忘曲线:显著度指数衰减,低于下限即淘汰。"""
        if not self.items or dt <= 0:
            return
        k = math.exp(-dt / SALIENCE_TAU)
        alive = []
        for m in self.items:
            m.salience *= k
            if m.salience >= SALIENCE_FLOOR:
                alive.append(m)
        self.items = alive

    def set_level(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))
        self.trim()

    def trim(self) -> None:
        cap = _cap(self.level)
        if len(self.items) > cap:
            # 超容先淘汰低显著度(稳定排序保持时间线)
            self.items.sort(key=lambda m: m.salience, reverse=True)
            del self.items[cap:]
            self.items.sort(key=lambda m: m.ts)

    def __len__(self) -> int:
        return len(self.items)

    def digest(self, limit: int = 50) -> list[str]:
        out = []
        for m in self.items[-limit:]:
            t = time.strftime("%m-%d %H:%M", time.localtime(m.ts))
            out.append(f"[{t}] {m.summary}(显著度 {m.salience:.2f})")
        return out

    def save(self) -> list[dict]:
        return [{"ts": m.ts, "kind": m.kind, "summary": m.summary,
                 "salience": m.salience} for m in self.items]

    def load(self, data: list) -> None:
        for e in data or []:
            try:
                self.items.append(MemoryRecord(ts=float(e["ts"]),
                                               kind=str(e.get("kind", "episodic")),
                                               summary=str(e.get("summary", "")),
                                               salience=float(e.get("salience", 0.5))))
            except (KeyError, TypeError, ValueError):
                continue  # 脏数据跳过,不让坏档拖垮载入
        self.trim()


class AssociationMemory:
    """联想层:情境→效价映射,事件共现学习,支持先验覆写(story 机制)。"""

    def __init__(self, level: int = 3) -> None:
        self.level = max(1, min(5, int(level)))
        self.items: dict[str, Association] = {}

    # ---- 学习 ----
    def learn(self, key: str, valence_delta: float, lr: float) -> Association:
        """valence += Δv·lr·(1+0.3·level)·(1-weight);weight 缓慢增强。"""
        a = self.items.get(key)
        if a is None:
            a = self.items[key] = Association(key=key)
        a.valence = max(-1.0, min(1.0, a.valence + valence_delta * lr
                                  * level_gain(self.level) * (1.0 - a.weight)))
        a.weight = min(1.0, a.weight + 0.02)
        a.count += 1
        self.trim()
        return a

    # ---- 查询 ----
    def get(self, key: str) -> Association | None:
        return self.items.get(key)

    def valence(self, key: str, default: float = 0.0) -> float:
        a = self.items.get(key)
        return a.valence if a else default

    def tick(self, dt: float) -> None:
        """效价极慢回落(遗忘),防止单次强事件永久定型。"""
        if not self.items or dt <= 0:
            return
        k = math.exp(-dt / ASSOC_DECAY_TAU)
        for a in self.items.values():
            a.valence *= k

    def set_level(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))
        self.trim()

    def trim(self) -> None:
        cap = _cap(self.level) * ASSOC_CAP_SCALE
        if len(self.items) > cap:
            keep = sorted(self.items.values(), key=lambda a: a.weight, reverse=True)[:cap]
            self.items = {a.key: a for a in keep}

    def digest(self, limit: int = 50) -> list[str]:
        return [f"[联想] {a.key}: 效价{a.valence:+.2f} 置信{a.weight:.2f} (n={a.count})"
                for a in list(self.items.values())[-limit:]]

    def save(self) -> dict:
        return {k: {"valence": a.valence, "weight": a.weight, "count": a.count}
                for k, a in self.items.items()}

    def load(self, data: dict) -> None:
        for k, v in (data or {}).items():
            try:
                self.items[k] = Association(key=k, valence=float(v.get("valence", 0.0)),
                                            weight=float(v.get("weight", 0.1)),
                                            count=int(v.get("count", 0)))
            except (TypeError, ValueError, AttributeError):
                continue
        self.trim()
