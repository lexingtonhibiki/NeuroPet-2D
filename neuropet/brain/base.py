"""大脑占位实现 ReflexBrain:规则+随机的通用脑。
实现 agent 2 将替换为:果蝇连接组简化网络 / 蟑螂神经网络,以及
本能库(数百条情境-反应)、情绪、联想记忆、智能分级等。
"""
from __future__ import annotations

import random
import time

from neuropet.core.contracts import (Association, Behavior, BehaviorCommand,
                                     EmotionState, MemoryRecord, Stimulus,
                                     StimulusKind)
from neuropet.core.interfaces import IBrain
from neuropet.core.world import WorldView

DANGER = {StimulusKind.WIND, StimulusKind.SHADOW, StimulusKind.VIBRATION,
          StimulusKind.CONTACT}


class ReflexBrain(IBrain):
    brain_id = "reflex"

    def __init__(self, state, flyer: bool = False) -> None:
        self.state = state
        self.flyer = flyer
        self.emotion_ = EmotionState()
        self.target: tuple[float, float] | None = None
        self._wander_until = 0.0
        self._injected: list[Stimulus] = []
        self._cmd_queue: list[BehaviorCommand] = []
        self._last_stimuli: list[Stimulus] = []
        self.level = 3
        self.episodes: list[MemoryRecord] = []
        self.assoc: dict[str, Association] = {}

    # ---- 感知 ----
    def observe(self, world: WorldView, stimuli: list[Stimulus], dt: float) -> None:
        self._last_stimuli = stimuli
        for s in self._injected:
            stimuli.append(s)
        self._injected.clear()
        e = self.emotion_
        # 情绪动力学(占位)
        danger = max((s.intensity for s in stimuli if s.kind in DANGER), default=0.0)
        odor = max((s.intensity for s in stimuli if s.kind == StimulusKind.ODOR_FOOD),
                   default=0.0)
        e.fear = max(e.fear * (1 - 1.2 * dt), danger)
        e.curiosity = min(1.0, e.curiosity + 0.02 * dt)
        e.hunger = min(1.0, e.hunger + 0.004 * dt)
        e.arousal = max(e.arousal * (1 - 0.8 * dt), danger, odor * 0.5)
        e.valence += ((odor * 0.5 - danger * 0.8) - e.valence) * 0.5 * dt
        # 联想:危险刺激与光标绑定学习
        if danger > 0.5:
            self._learn("cursor:rush", -0.6, 0.05)

    def decide(self, world: WorldView) -> BehaviorCommand:
        e = self.emotion_
        if self._cmd_queue:
            return self._cmd_queue.pop(0)
        # 危险反射:逃逸
        danger = [s for s in self._last_stimuli if s.kind in DANGER and
                  s.intensity > 0.35 + 0.1 * (3 - min(3, self.level))]
        if danger:
            s = max(danger, key=lambda s: s.intensity)
            # 反向逃逸 + 少量随机偏转
            if s.direction:
                fx, fy = -s.direction[0], -s.direction[1]
            else:
                fx, fy = 1.0, 0.0
            px, py = self.state.pos
            t = world.clamp_to_screen((px + fx * 420, py + fy * 420))
            self._remember("危险!快速逼近的目标触发逃逸", salience=0.8)
            return BehaviorCommand(Behavior.ESCAPE, target=t, intensity=min(1.0, s.intensity),
                                   priority=90, reason="危险刺激逃逸")
        # 饥饿觅食
        if e.hunger > 0.45:
            f = world.nearest_food(self.state.pos, max_r=900)
            if f:
                if world.pets and dist(self.state.pos[0], self.state.pos[1], *f.pos) < 60:
                    return BehaviorCommand(Behavior.EAT, priority=40, reason="进食")
                return BehaviorCommand(Behavior.SEEK_FOOD, target=f.pos, priority=40,
                                       reason="饥饿觅食")
        # 飞行种:周期性起降(占位)
        if self.flyer and random.random() < 0.002:
            if self.state.mode.value == "crawl":
                return BehaviorCommand(Behavior.TAKEOFF, priority=20, reason="想飞了")
            if self.state.mode.value == "fly" and random.random() < 0.1:
                return BehaviorCommand(Behavior.LAND, priority=20, reason="降落")
        # 漫游
        if self.target is None or time.perf_counter() > self._wander_until:
            m = 140
            self.target = world.clamp_to_screen(
                (random.uniform(m, world.screen[0] - m), random.uniform(m, world.screen[1] - m)))
            self._wander_until = time.perf_counter() + random.uniform(4, 9)
            return BehaviorCommand(Behavior.EXPLORE, target=self.target, priority=5,
                                   reason="探索")
        return BehaviorCommand(Behavior.EXPLORE, target=self.target, priority=5, reason="探索中")

    # ---- 事件 ----
    def on_event(self, name: str, data: dict) -> None:
        e = self.emotion_
        if name == "grab":
            e.fear = min(1.0, e.fear + 0.55)
            e.trust = max(0.0, e.trust - 0.12)
            e.arousal = 1.0
            self._learn("human:grab", -0.5, 0.08)
            self._remember("被抓了起来,很害怕", salience=0.9)
        elif name == "released":
            self._remember("被放开,趁机逃跑!", salience=0.7)
            px, py = self.state.pos
            self._cmd_queue.append(BehaviorCommand(
                Behavior.ESCAPE, target=(px + random.uniform(-500, 500),
                                         py + random.uniform(-500, 500)),
                intensity=1.0, priority=95, reason="脱手逃逸"))
        elif name == "fed":
            e.hunger = max(0.0, e.hunger - 0.3)
            e.trust = min(1.0, e.trust + 0.06)
            e.valence = min(1.0, e.valence + 0.3)
            self._learn("human:feed", +0.7, 0.06)
            self._remember("吃到了主人投喂的食物,很满足", salience=0.6)
        elif name == "hungry":
            e.hunger = min(1.0, e.hunger + 0.1)
        elif name == "freeze_on":
            self._remember("被冻结了(魔法?)", salience=0.3)
        elif name == "external_stimulus":
            kind = data.get("kind")
            try:
                k = StimulusKind(kind)
            except ValueError:
                return
            pos = tuple(data.get("source_pos", self.state.pos))
            self._injected.append(Stimulus(k, source="system", pos=pos,
                                           intensity=float(data.get("intensity", 0.8))))
        elif name == "story":
            # 意识层联想:先验注入(如"雪很美")改变对情境的效价
            about = data.get("about", "")
            valence = float(data.get("valence", 0))
            self._learn(f"context:{about}", valence, 0.15)
            self._remember(f"听到了一个故事:{data.get('text', '')}", salience=0.5)

    def inject_command(self, cmd: BehaviorCommand) -> None:
        self._cmd_queue.append(cmd)

    # ---- 学习/记忆 ----
    def _learn(self, key: str, valence_delta: float, lr: float) -> None:
        a = self.assoc.setdefault(key, Association(key=key))
        a.valence = max(-1.0, min(1.0, a.valence + valence_delta * lr * (1 + self.level * 0.3)))
        a.weight = min(1.0, a.weight + 0.02)
        a.count += 1

    def _remember(self, summary: str, salience: float = 0.5) -> None:
        cap = {1: 20, 2: 40, 3: 80, 4: 140, 5: 220}.get(self.level, 80)
        self.episodes.append(MemoryRecord(ts=time.time(), kind="episodic",
                                          summary=summary, salience=salience))
        if len(self.episodes) > cap:
            self.episodes.sort(key=lambda m: m.salience, reverse=True)
            del self.episodes[cap:]

    # ---- 智能等级/持久化 ----
    def set_intelligence(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))

    def emotion(self) -> EmotionState:
        return self.emotion_

    def memory_digest(self, limit: int = 50) -> list[str]:
        lines = [f"[联想] {a.key}: 效价{a.valence:+.2f} 置信{a.weight:.2f} (n={a.count})"
                 for a in self.assoc.values()]
        lines += [f"[{time.strftime('%m-%d %H:%M', time.localtime(m.ts))}] {m.summary}"
                  for m in self.episodes[-limit:]]
        return lines

    def save(self) -> dict:
        return {
            "episodes": [{"ts": m.ts, "summary": m.summary, "salience": m.salience}
                         for m in self.episodes],
            "assoc": {k: {"valence": a.valence, "weight": a.weight, "count": a.count}
                      for k, a in self.assoc.items()},
        }

    def load(self, data: dict) -> None:
        from neuropet.core.contracts import MemoryRecord
        for e in data.get("episodes", []):
            self.episodes.append(MemoryRecord(ts=e["ts"], kind="episodic",
                                              summary=e["summary"],
                                              salience=e.get("salience", 0.5)))
        for k, v in data.get("assoc", {}).items():
            self.assoc[k] = Association(key=k, valence=v.get("valence", 0),
                                        weight=v.get("weight", 0.1), count=v.get("count", 0))

    def clear_memory(self) -> None:
        self.episodes.clear()
        self.assoc.clear()
