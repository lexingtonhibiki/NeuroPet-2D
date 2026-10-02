"""本能库:程序化生成的"情境→反应"出厂先验表 + schema 校验。

规格见 docs/学习与记忆系统设计.md §2:
- 条目 schema:{"id","species","stimulus":{kinds,gesture,min_intensity},
  "internal":{fear_gt,hunger_gt},"reaction":{behavior,gain,dir},"valence_prior"};
- 规模 ≥300 条:16 刺激原型 × 3 强度档 × 4 内部状态档 × 2 物种 = 384 条;
- 本库只提供"出厂先验";story 等叙事事件在运行时经联想层(AssociationMemory)
  覆写 cold 等负先验(效价转正 → 趋近冷区),覆写逻辑在各个大脑内实现;
- instinct_library.json 是 build_entries() 的落盘产物(可用 write_default 再生);
  加载时逐条 schema 校验,任何非法条目都视为库损坏并回退程序化生成。
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from neuropet.core.contracts import Behavior, StimulusKind

#: 支持的物种标识(与各大脑的 SPECIES 属性对应)
SPECIES_IDS = ("fly", "roach")
#: 合法反应方向
DIRS = ("toward", "away", "stay")

# 刺激原型:(id, kinds, gesture|None, 基准min_intensity, 基准效价先验,
#           反应行为, 基准增益, 方向, 中文说明)
ARCHETYPES = [
    ("cursor_rush_wind", ("wind",), "rush", 0.55, -0.45, "escape", 1.20, "away",
     "光标猛冲带起的风(果蝇风感极敏)"),
    ("wind_gust", ("wind",), None, 0.15, -0.05, "explore", 0.70, "away",
     "一般气流:警戒性小幅挪位,不升级为逃逸"),
    ("looming_shadow", ("shadow",), "approach_fast", 0.50, -0.90, "escape", 1.40, "away",
     "逼近阴影(LOOM 超选择威胁)"),
    ("shadow_pass", ("shadow",), "approach_slow", 0.18, -0.25, "explore", 0.80, "away",
     "缓慢单影掠过:监视但不逃"),
    ("vibration_tap", ("vibration",), None, 0.40, -0.80, "escape", 1.30, "away",
     "点按/拖拽震动(蟑螂尾须、果蝇琼氏器)"),
    ("contact_grab", ("contact",), None, 0.35, -0.90, "escape", 1.40, "away",
     "身体接触/被抓"),
    ("food_odor", ("odor_food",), None, 0.10, 0.55, "seek_food", 1.00, "toward",
     "食物气味(先天趋向)"),
    ("feed_context", ("odor_food",), "approach_slow", 0.10, 0.35, "seek_food", 0.90, "toward",
     "伴随缓慢靠近的食物气味(投喂语境,信任积累)"),
    ("mate_odor", ("odor_mate",), None, 0.12, 0.25, "explore", 0.80, "toward",
     "信息素(聚集/求偶,扩展位)"),
    ("cold_zone", ("cold",), None, 0.12, -0.60, "explore", 0.90, "away",
     "冷区:先天回避;联想层 positive 后可反转为趋近"),
    ("light_glare", ("light",), None, 0.22, -0.50, "explore", 1.00, "away",
     "强光(负趋光:蟑螂强,果蝇弱)"),
    ("cursor_idle_near", ("contact", "shadow"), "idle", 0.05, 0.05, "groom", 0.60, "stay",
     "光标静止停在附近:低威胁,可梳理/驻留"),
    ("cursor_orbit", ("wind", "shadow"), "orbit", 0.10, -0.10, "turn", 0.70, "away",
     "光标绕圈:不确定威胁,转身监视"),
    ("cursor_jab", ("vibration", "wind"), "jab", 0.45, -0.70, "escape", 1.25, "away",
     "戳刺:快而短的机械刺激"),
    ("retreat_release", ("wind",), "retreat", 0.10, 0.20, "explore", 0.80, "toward",
     "威胁退去:恢复探索"),
    ("story_hint", ("story",), None, 0.0, 0.0, "explore", 0.50, "toward",
     "叙事先验注入(注意与联想挂钩)"),
]

# 强度档:(名称, 先验乘子, min_intensity 乘子)
INTENSITY_BANDS = (("soft", 0.70, 0.70), ("mid", 1.15, 1.00), ("strong", 1.60, 1.30))

# 内部状态档:(名称, fear_gt, hunger_gt, 逃逸/回避增益乘子, 觅食增益乘子)
INTERNAL_BANDS = (
    ("baseline", 0.0, 0.0, 1.00, 1.00),
    ("hungry", 0.0, 0.55, 0.95, 1.30),
    ("scared", 0.50, 0.0, 1.25, 0.70),
    ("stressed", 0.50, 0.55, 1.35, 0.60),
)

# 物种差异:刺激增益/先验的放大倍数(果蝇风感/视觉强,蟑螂震动/嗅觉/负趋光强)
SPECIES_MOD = {
    "fly": {"wind": 1.20, "shadow": 1.10, "vibration": 0.80, "light": 0.60,
            "odor_food": 1.00, "cold": 1.00, "contact": 1.00, "odor_mate": 1.00,
            "story": 1.00},
    "roach": {"wind": 0.70, "shadow": 0.90, "vibration": 1.25, "light": 1.30,
              "odor_food": 1.15, "cold": 1.00, "contact": 1.00, "odor_mate": 0.90,
              "story": 1.00},
}

_VALID_KINDS = {k.value for k in StimulusKind}
_VALID_BEHAVIORS = {b.value for b in Behavior}


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def build_entries() -> list[dict]:
    """程序化展开本能库:原型 × 强度档 × 内部状态档 × 物种。"""
    out: list[dict] = []
    for sp in SPECIES_IDS:
        for (aid, kinds, gesture, base_min, prior, beh, gain, dr, _note) in ARCHETYPES:
            sp_mod = 1.0
            for k in kinds:
                sp_mod = max(sp_mod, SPECIES_MOD[sp].get(k, 1.0))
            for (bn, p_mul, i_mul) in INTENSITY_BANDS:
                min_int = round(_clamp(base_min * i_mul, 0.0, 1.0), 2)
                band_prior = round(_clamp(prior * p_mul, -1.0, 1.0), 3)
                for (sn, f_gt, h_gt, esc_mul, food_mul) in INTERNAL_BANDS:
                    mul = esc_mul if beh in ("escape", "turn") else (
                        food_mul if beh == "seek_food" else 1.0)
                    out.append({
                        "id": f"{sp}.{aid}.{bn}.{sn}",
                        "species": sp,
                        "stimulus": {"kinds": list(kinds), "gesture": gesture,
                                     "min_intensity": min_int},
                        "internal": {"fear_gt": f_gt, "hunger_gt": h_gt},
                        "reaction": {"behavior": beh,
                                     "gain": round(_clamp(gain * mul * sp_mod, 0.0, 2.0), 2),
                                     "dir": dr},
                        "valence_prior": band_prior,
                    })
    return out


def validate_entries(entries: list[dict]) -> list[str]:
    """逐条 schema 校验,返回错误列表(空 = 合法)。"""
    errs: list[str] = []
    seen: set[str] = set()
    if not isinstance(entries, list) or not entries:
        return ["条目表为空或不是列表"]
    for i, e in enumerate(entries):
        where = f"#{i}"
        try:
            if not isinstance(e, dict):
                errs.append(f"{where}: 非字典"); continue
            for req in ("id", "species", "stimulus", "internal", "reaction",
                        "valence_prior"):
                if req not in e:
                    errs.append(f"{where}: 缺字段 {req}")
            eid = str(e.get("id", ""))
            where = f"[{eid or i}]"
            if eid in seen:
                errs.append(f"{where}: id 重复")
            seen.add(eid)
            if e.get("species") not in SPECIES_IDS:
                errs.append(f"{where}: species 非法 {e.get('species')}")
            st = e.get("stimulus") or {}
            kinds = st.get("kinds")
            if not isinstance(kinds, list) or not kinds or \
                    any(k not in _VALID_KINDS for k in kinds):
                errs.append(f"{where}: stimulus.kinds 非法 {kinds}")
            mi = float(st.get("min_intensity", -1))
            if not (0.0 <= mi <= 1.0):
                errs.append(f"{where}: min_intensity 越界 {mi}")
            g = st.get("gesture")
            if g is not None and not isinstance(g, str):
                errs.append(f"{where}: gesture 非法")
            it = e.get("internal") or {}
            for kk in ("fear_gt", "hunger_gt"):
                v = float(it.get(kk, 0.0))
                if not (0.0 <= v <= 1.0):
                    errs.append(f"{where}: internal.{kk} 越界 {v}")
            rx = e.get("reaction") or {}
            if rx.get("behavior") not in _VALID_BEHAVIORS:
                errs.append(f"{where}: reaction.behavior 非法 {rx.get('behavior')}")
            gv = float(rx.get("gain", -1))
            if not (0.0 <= gv <= 2.0):
                errs.append(f"{where}: reaction.gain 越界 {gv}")
            if rx.get("dir") not in DIRS:
                errs.append(f"{where}: reaction.dir 非法 {rx.get('dir')}")
            vp = float(e.get("valence_prior", 9))
            if not (-1.0 <= vp <= 1.0):
                errs.append(f"{where}: valence_prior 越界 {vp}")
        except (TypeError, ValueError) as exc:
            errs.append(f"{where}: 结构异常 {exc}")
    return errs


class InstinctLibrary:
    """运行时本能库:编译为 (物种, 刺激种类) → 条目 的查找索引。"""

    _default: "InstinctLibrary | None" = None

    def __init__(self, entries: list[dict]) -> None:
        errs = validate_entries(entries)
        if errs:
            raise ValueError(f"本能库 schema 校验失败 {len(errs)} 条: {errs[:5]}")
        self.entries = entries
        self._index: dict[tuple[str, str], list[dict]] = {}
        for e in entries:
            for k in e["stimulus"]["kinds"]:
                self._index.setdefault((e["species"], k), []).append(e)

    # ---- 加载 ----
    @classmethod
    def library_path(cls) -> Path:
        return Path(__file__).resolve().parent / "instinct_library.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "InstinctLibrary":
        p = path or cls.library_path()
        try:
            data = json.loads(p.read_text("utf-8"))
            return cls(data)  # 校验失败会抛 ValueError
        except (OSError, ValueError, json.JSONDecodeError):
            return cls(build_entries())  # 回退程序化生成

    @classmethod
    def default(cls) -> "InstinctLibrary":
        if cls._default is None:
            cls._default = cls.load()
        return cls._default

    # ---- 查询 ----
    def query(self, species: str, kinds: set[str] | frozenset[str], *,
              fear: float = 0.0, hunger: float = 0.0,
              intensity: float = 0.0, gesture: str | None = None,
              limit: int = 8) -> list[dict]:
        """按当前刺激种类与内部状态取候选反应(按|先验|降序,含置信过滤)。"""
        cand: dict[str, dict] = {}
        for k in kinds:
            for e in self._index.get((species, k), ()):
                st, it, rx = e["stimulus"], e["internal"], e["reaction"]
                if intensity and intensity < st["min_intensity"]:
                    continue
                if gesture and st.get("gesture") and gesture != st["gesture"]:
                    continue
                if fear < it["fear_gt"] - 1e-9 or hunger < it["hunger_gt"] - 1e-9:
                    continue
                cand[e["id"]] = e
        out = sorted(cand.values(), key=lambda e: abs(e["valence_prior"]), reverse=True)
        return out[:limit]

    def valence_prior(self, species: str, kind: str) -> float:
        """某物种对某类刺激的出厂先验效价(取 baseline 档均值)。"""
        vals = [e["valence_prior"] for e in self._index.get((species, kind), ())
                if e["internal"]["fear_gt"] == 0.0 and e["internal"]["hunger_gt"] == 0.0]
        return sum(vals) / len(vals) if vals else 0.0

    def __len__(self) -> int:
        return len(self.entries)


def write_default(path: Path | None = None) -> int:
    """把程序化生成的库落盘为 instinct_library.json(维护/再生用)。"""
    p = path or InstinctLibrary.library_path()
    entries = build_entries()
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")
    return len(entries)


# ================= 多臂老虎机仲裁(操作式学习,路线 B) =================
# 审计 R2:本能库 query() 全局零调用、human:feed 联想写入后 decide() 不读 ——
# 两个死路在这里一起修:臂表 = "可被学习的行为"(尾须/逃逸等反射不进臂),
# μ 先验由本能库 valence_prior 播种(query() 首次进入决策链),联想层效价
# 在线覆写 μ(联想→操作式打通)。算法:UCB1 置信加分(Auer 2002)+
# softmax 温度读出;Beta 后验并行累计供 Thompson 采样(Chapelle & Li 2011)。
#: 可学习臂表(K=7;"做什么",不是"怎么动"——转向参数包不受影响)
BANDIT_ARMS = ("explore_wander", "seek_food", "approach_cursor", "groom",
               "rest", "near_cold", "near_small_target")
#: 臂中文名(面板显示)
BANDIT_ARM_ZH = {"explore_wander": "巡游探索", "seek_food": "觅食趋味",
                 "approach_cursor": "靠近主人", "groom": "理毛",
                 "rest": "驻留静息", "near_cold": "趋近冷区",
                 "near_small_target": "探究小目标"}
#: 臂 → (本能库 kinds, 反应行为):出厂 μ 先验 = 命中条目 valence_prior 均值
BANDIT_ARM_QUERY = {
    "explore_wander": (("wind",), "explore"),
    "seek_food": (("odor_food",), "seek_food"),
    "approach_cursor": (("contact", "shadow"), "explore"),
    "groom": (("contact", "shadow"), "groom"),
    "rest": ((), None),                       # 静息无本能条目:固定中性偏负
    "near_cold": (("cold",), "explore"),
    "near_small_target": (("odor_mate",), "explore"),
}
#: 臂 → 联想键:assoc.valence 在线播种 μ(0.7μ+0.3·(0.5+0.5v),调研 §4.3(5))
BANDIT_ARM_CTX = {
    "seek_food": "human:feed",
    "approach_cursor": "human:feed",
    "near_cold": "context:cold",
    "near_small_target": "human:feed",
}


class BanditArbiter:
    """K 臂老虎机仲裁器:μ 加 UCB 置信加分选臂,情绪调制探索率与逆温度。

    每决策点 O(K) 次 sqrt+add(≈1µs);奖励事件 O(1) 更新。全状态可 JSON
    序列化(并入 memory.json 的 "arms" 键)。奖励空间 [0,1]:1=好事(fed),
    -1=坏事(grab),update() 内部映射到 μ∈[0,1]。
    """

    __slots__ = ("mu", "n", "alpha", "beta", "t", "level", "c0", "beta0", "_rng")

    def __init__(self, level: int = 3, seed: int = 424242) -> None:
        self.mu = {a: 0.5 for a in BANDIT_ARMS}
        self.n = {a: 0 for a in BANDIT_ARMS}
        self.alpha = {a: 1.0 for a in BANDIT_ARMS}   # Beta(1+α,1+β) 后验
        self.beta = {a: 1.0 for a in BANDIT_ARMS}
        self.t = 0                                    # 决策点计数(UCB 的 ln t)
        self.level = max(1, min(5, int(level)))
        self.c0 = 0.2                                 # 置信系数基值(调研 §3.2)
        self.beta0 = 3.0                              # softmax 逆温度基值(1~6,文献量级)
        self._rng = random.Random(seed)

    # ---- 情绪/智能调制 ----
    def c(self, curiosity: float = 0.5, arousal: float = 0.3) -> float:
        """探索系数:好奇↑→更探索;唤醒↑→更收敛;智能高→探索少。"""
        return self.c0 * (1.4 - 0.15 * self.level) \
            * (1.0 + 0.5 * curiosity - 0.4 * arousal)

    def inv_beta(self, arousal: float = 0.3, curiosity: float = 0.5) -> float:
        """softmax 逆温度:唤醒↑/智能高 → 更果断;好奇↑ → 更试探。"""
        return self.beta0 * (0.6 + 0.2 * self.level) \
            * (1.0 + 0.5 * arousal - 0.3 * curiosity)

    def set_level(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))

    # ---- 选臂 ----
    def ucb(self, arm: str, curiosity: float = 0.5, arousal: float = 0.3) -> float:
        """UCB1 分值:μ̂ + c·√(2lnT/n)。

        未试过的臂(n=0)按"μ 先验 + n=1 满额置信加分"计:既保留出厂先验
        的次序(食物 > 巡游 > 冷区),又保证未试臂不会被永久埋没(t 增大时
        加分随之上涨,强制探索扫描)。"""
        bonus = self.c(curiosity, arousal) * math.sqrt(
            2.0 * math.log(max(self.t, 2)) / max(1, self.n.get(arm, 0)))
        return self.mu[arm] + bonus

    def select(self, curiosity: float = 0.5, arousal: float = 0.3) -> str:
        """纯老虎机选臂(合成环境测试用):argmax UCB。"""
        return max(BANDIT_ARMS, key=lambda a: self.ucb(a, curiosity, arousal))

    def trial(self, arm: str) -> None:
        """记一次决策点(t+1、n+1):由调用方按"臂切换或每 4s"节流。"""
        self.t += 1
        self.n[arm] = self.n.get(arm, 0) + 1

    def softmax_pick(self, scored: dict, inv_beta: float,
                     temperature: float = 2.0):
        """温度化 softmax 读出 P(a) ∝ exp(β·score/T):分差 >3T 近似确定,
        近平手才有随机性(保行为学验收可复现)。键为调用方的候选索引。"""
        if not scored:
            raise ValueError("softmax_pick 需要非空候选")
        items = list(scored.items())
        if len(items) == 1:
            return items[0][0]
        mx = max(v for _, v in items)
        beta_t = max(0.1, inv_beta) / temperature
        weights = [(math.exp(min(20.0, (v - mx) * beta_t)), k) for k, v in items]
        total = sum(w for w, _ in weights)
        pick = self._rng.random() * total
        acc = 0.0
        for w, k in weights:
            acc += w
            if pick <= acc:
                return k
        return items[-1][0]

    def thompson(self) -> str:
        """Thompson 采样(备选读出):a* = argmax sample Beta(1+α,1+β)。"""
        return max(BANDIT_ARMS,
                   key=lambda a: self._rng.betavariate(self.alpha[a], self.beta[a]))

    # ---- 学习 ----
    def update(self, arm: str, reward: float) -> None:
        """事件更新:μ ← μ + (R−μ)/max(2,n)(调研 §4.3(5));Beta 后验并行累计。"""
        if arm not in self.mu:
            return
        r = max(-1.0, min(1.0, float(reward)))
        n = self.n.get(arm, 0)
        self.mu[arm] = max(0.0, min(1.0, self.mu[arm] + (r - self.mu[arm]) / max(2, n)))
        self.n[arm] = n + 1
        if r >= 0.0:
            self.alpha[arm] += 0.5 + 0.5 * r
        else:
            self.beta[arm] += 0.5 - 0.5 * r

    # ---- 先验播种 ----
    def seed_from_instinct(self, lib: InstinctLibrary, species: str) -> None:
        """出厂 μ 先验:本能库 query() 命中条目的 valence_prior 均值 → [0,1]。"""
        for arm, (kinds, behavior) in BANDIT_ARM_QUERY.items():
            if not kinds:
                self.mu[arm] = 0.45            # 静息:中性偏负,不压过主动行为
                continue
            entries = lib.query(species, frozenset(kinds))
            vals = [e["valence_prior"] for e in entries
                    if e["reaction"]["behavior"] == behavior]
            if vals:
                self.mu[arm] = max(0.05, min(0.95, 0.5 + 0.5 * (sum(vals) / len(vals))))

    def seed_assoc(self, arm: str, valence: float) -> None:
        """联想播种:μ ← 0.7·μ + 0.3·(0.5+0.5·v) —— story/投喂联想立即改变倾向。"""
        if arm not in self.mu:
            return
        v = max(-1.0, min(1.0, float(valence)))
        self.mu[arm] = max(0.02, min(0.98, 0.7 * self.mu[arm] + 0.3 * (0.5 + 0.5 * v)))

    # ---- 查询/持久化 ----
    def best_arm(self) -> tuple:
        """当前最偏好的臂(按 μ,平手取 n 大者):面板"它学会了…"显示用。"""
        a = max(BANDIT_ARMS, key=lambda x: (self.mu[x], self.n[x]))
        return a, self.mu[a], self.n[a]

    def to_dict(self) -> dict:
        return {"t": self.t, "level": self.level, "mu": dict(self.mu),
                "n": dict(self.n), "alpha": dict(self.alpha), "beta": dict(self.beta)}

    def load(self, d: dict | None) -> None:
        d = d or {}
        try:
            self.t = int(d.get("t", 0))
            self.set_level(int(d.get("level", self.level)))
            for k in ("mu", "n", "alpha", "beta"):
                src = d.get(k, {})
                if isinstance(src, dict):
                    dst = getattr(self, k)
                    for arm in BANDIT_ARMS:
                        if arm in src:
                            dst[arm] = float(src[arm]) if k != "n" else int(src[arm])
        except (TypeError, ValueError):
            pass    # 脏档容错:保持缺省先验

    def reset(self) -> None:
        self.mu = {a: 0.5 for a in BANDIT_ARMS}
        self.n = {a: 0 for a in BANDIT_ARMS}
        self.alpha = {a: 1.0 for a in BANDIT_ARMS}
        self.beta = {a: 1.0 for a in BANDIT_ARMS}
        self.t = 0


if __name__ == "__main__":  # 手动再生:python -m neuropet.brain.instinct
    n = write_default()
    errs = validate_entries(build_entries())
    print(f"本能库已写出 {n} 条,schema 错误 {len(errs)} 个")
