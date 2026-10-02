"""关键姿态库:骨架级目标姿态表 + 运行时 crossfade(纯 Python,零依赖)。

骨架重构波 M2 落地(决策记录 §5.1 Agent-1 R3;提案 C §3.1 ③):
- 姿态 = 一组**骨架级目标参数**(触角摆频/摆幅/基角、腹部滞后 τ、膝下沉
  增益、头偏移),不再是 body/base.py 里的 if-elif 硬编码(A1 病灶);
- 行为切换 → 目标姿态切换 → crossfade(一阶趋近,τ≈0.15s)混合,无跳变;
- **相位锚定静息位**:行走姿态的足端目标由 gait.py 的落点锚给出(支撑相
  收在权威静息足向 54/127/161°)——加长的腿改回权威值后步态仍不超阀
  (结构性消除"为迁就 IK 阀门加长腿"的历史包袱,本库不背该债)。

姿态表(保守取值=历史行为等价;新物种填表即用):
  rest    静息:触角慢扫,腹部滞后 τ=120ms
  alert   警戒(逃逸后 2~5s):触角高频全鞭波动+梢颤,腹滞后收硬,膝下沉压实
  walk    行走:触角行进慢扫(频率随速),其余同静息
  freeze  惊觉僵住:触角中频(仍动——文献:僵住期触角不冻),腿压实
  air     飞行收腿:足端向体轴收拢(迁移自 base._legs_pose 硬编码)
"""
from __future__ import annotations

from typing import Any

from neuropet.core.mathutil import clamp, lerp

CROSSFADE_TAU = 0.15     # 姿态切换一阶混合时间常数(s)

# ---- 姿态关键帧表(具名常量;语义见模块 docstring) ----
POSES: dict[str, dict[str, float]] = {
    "rest":   {"antenna_hz": 0.7, "antenna_amp": 0.30, "antenna_alert": 0.0,
               "ab_lag_tau": 0.12, "sag_gain": 1.0, "leg_tuck": 0.0, "leg_drag": 0.0},
    "alert":  {"antenna_hz": 3.0, "antenna_amp": 0.45, "antenna_alert": 1.0,
               "ab_lag_tau": 0.06, "sag_gain": 1.6, "leg_tuck": 0.0, "leg_drag": 0.0},
    "walk":   {"antenna_hz": 0.9, "antenna_amp": 0.30, "antenna_alert": 0.0,
               "ab_lag_tau": 0.12, "sag_gain": 1.0, "leg_tuck": 0.0, "leg_drag": 0.0},
    "freeze": {"antenna_hz": 1.5, "antenna_amp": 0.35, "antenna_alert": 0.0,
               "ab_lag_tau": 0.12, "sag_gain": 1.8, "leg_tuck": 0.0, "leg_drag": 0.0},
    "air":    {"antenna_hz": 1.2, "antenna_amp": 0.25, "antenna_alert": 0.0,
               "ab_lag_tau": 0.20, "sag_gain": 0.0, "leg_tuck": 0.55, "leg_drag": 3.0},
}


class PoseLibrary:
    """姿态库:当前姿态 → 目标姿态的一阶 crossfade。`sample()` 返回混合值。

    用法(body 持有一个实例):
      lib.set_target("alert")     # 行为切换时调用(重复设置无副作用)
      lib.update(dt)              # 每帧推进混合
      p = lib.sample()            # dict:键同 POSES 值
    """

    def __init__(self, poses: dict[str, dict[str, float]] | None = None) -> None:
        self._poses = poses if poses is not None else POSES
        self._cur: dict[str, float] = dict(self._poses["rest"])
        self._target = "rest"
        self._tau = CROSSFADE_TAU

    @property
    def target(self) -> str:
        return self._target

    def set_target(self, name: str) -> None:
        """切换目标姿态(未知姿态名回退 rest,不抛错)。"""
        if name in self._poses:
            self._target = name

    def update(self, dt: float) -> None:
        """一阶趋近目标(帧率无关;全键同步混合,无键间跳序)。"""
        tgt = self._poses[self._target]
        k = min(1.0, max(0.0, dt) / self._tau)
        for key, val in tgt.items():
            cur = self._cur.get(key, val)
            self._cur[key] = lerp(cur, val, k)

    def sample(self) -> dict[str, float]:
        return dict(self._cur)

    def get(self, key: str, default: float = 0.0) -> float:
        return float(self._cur.get(key, default))

    # ---------------- 姿态选择(行为/状态 → 姿态名) ----------------
    @staticmethod
    def pick(activity: Any, mode: Any, flying: bool, alert: bool,
             frozen: bool, speed_norm: float) -> str:
        """行为/状态 → 目标姿态名(选择逻辑集中于此,base 只调用)。

        speed_norm = speed/cruise(0~1+):行走姿态按速度归一提前量切换。
        """
        if flying:
            return "air"
        if frozen:
            return "freeze"
        if alert:
            return "alert"
        if activity is not None and getattr(activity, "name", "") in ("EAT", "GROOM"):
            return "rest"          # 进食/梳理:体姿静息,专属小动作在 body 层
        if speed_norm > 0.12:
            return "walk"
        return "rest"

    @staticmethod
    def valid_gait_speed_gate() -> float:
        """行走姿态切换的速度门(归一);供测试与诊断。"""
        return 0.12

    def clamp_gain(self, name: str, value: float) -> float:
        """姿态键值安全读取(未来表驱动扩展的护栏)。"""
        base = self._poses.get(name, {})
        return clamp(value, 0.0, float(base.get("sag_gain", 2.0)))
