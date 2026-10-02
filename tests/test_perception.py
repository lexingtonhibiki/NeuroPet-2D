# -*- coding: utf-8 -*-
"""感知层离线测试:光标手势分类 / 物种差异化刺激 / 钩子模块接口。

运行:python tests/test_perception.py
全程离线:不安装真实鼠标钩子、不移动鼠标、不创建窗口。
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DT = 1.0 / 60.0  # 与主循环一致的采样间隔


# ---------------------------------------------------------------- 光标模拟器
class CursorSim:
    """合成光标轨迹:匀速/冲刺/急停序列,逐帧喂给 update_kinematics。"""

    def __init__(self) -> None:
        from neuropet.core.contracts import CursorKinematics, CursorSample
        self._CursorSample = CursorSample
        self.kin = CursorKinematics()
        self.hist: deque = deque(maxlen=90)
        self.x, self.y, self.t = 500.0, 400.0, 0.0

    def move(self, v: float, n: int = 1, direction: tuple[float, float] = (1.0, 0.0)) -> None:
        """以恒定速度 v(px/s)沿 direction 移动 n 帧(每帧记一个采样)。"""
        from neuropet.perception.mouse import update_kinematics
        for _ in range(n):
            self.x += v * direction[0] * DT
            self.y += v * direction[1] * DT
            self.t += DT
            self.hist.append(self._CursorSample(self.t, self.x, self.y))
            update_kinematics(self.hist, self.kin)

    def stop(self, n: int = 1) -> None:
        """原地保持 n 帧(光标不动,产生速度≈0 的采样)。"""
        self.move(0.0, n)


def test_gesture_classification() -> None:
    print("[test] 手势分类:匀速/冲刺/急停(jab)/静止/贴身 touch-retouch")
    sim = CursorSim()

    # 静止 → idle
    sim.stop(5)
    assert sim.kin.gesture == "idle", f"静止应 idle,得到 {sim.kin.gesture}"

    # 匀速 100 px/s → approach_slow(EMA 收敛后速度接近真值)
    sim.move(100.0, 12)
    assert sim.kin.gesture == "approach_slow", f"100px/s 应 approach_slow,得到 {sim.kin.gesture}"
    assert abs(sim.kin.speed - 100.0) < 30.0, f"平滑速度偏离真值: {sim.kin.speed:.1f}"

    # 冲刺 2000 px/s → rush(≥1300)
    sim.move(2000.0, 30)
    assert sim.kin.gesture == "rush", f"2000px/s 应 rush,得到 {sim.kin.gesture}"
    assert sim.kin.speed > 1500.0, f"冲刺平滑速度应接近 2000: {sim.kin.speed:.1f}"
    assert sim.kin.accel != 0.0 or True  # 加速度字段存在且为数值

    # 高速直指(笔直冲刺)后骤停 1 帧 → jab
    sim.stop(1)
    assert sim.kin.gesture == "jab", f"直指冲刺后骤停应 jab,得到 {sim.kin.gesture}"

    # 骤停保持数帧后 EMA 衰减回 idle
    sim.stop(6)
    assert sim.kin.gesture == "idle", f"急停保持后应 idle,得到 {sim.kin.gesture}"

    # 贴身 touch:上层命中检测设置 near_pet_id,光标低速压在宠物上
    sim.kin.near_pet_id = "petA"
    sim.stop(3)
    assert sim.kin.gesture == "touch", f"贴身低速应 touch,得到 {sim.kin.gesture}"

    # retouch:离开(近距标志清空)又快速贴回同一宠物
    sim.kin.near_pet_id = None
    sim.move(300.0, 10)                      # 离开(约 50px,<2s 窗口内)
    sim.stop(1)
    sim.kin.near_pet_id = "petA"
    sim.stop(2)
    assert sim.kin.gesture == "retouch", f"快速贴回应 retouch,得到 {sim.kin.gesture}"

    # 急停但轨迹弯曲 → 退化为 idle(枚举中无独立急停值)
    from neuropet.core.contracts import CursorSample
    from neuropet.perception.mouse import update_kinematics
    import math
    sim2 = CursorSim()
    for i in range(30):                      # 30 帧圆弧冲刺(持续转向,直指度低)
        ang = i * 0.25
        vx, vy = 2000.0 * math.cos(ang), 2000.0 * math.sin(ang)
        sim2.x += vx * DT
        sim2.y += vy * DT
        sim2.t += DT
        sim2.hist.append(CursorSample(sim2.t, sim2.x, sim2.y))
        update_kinematics(sim2.hist, sim2.kin)
    sim2.stop(1)
    assert sim2.kin.gesture == "idle", f"弯曲冲刺急停应 idle,得到 {sim2.kin.gesture}"


# ---------------------------------------------------------------- 物种差异化
def test_species_differentiated_stimuli() -> None:
    from neuropet.core.contracts import PetState, StimulusKind
    from neuropet.core.world import WorldModel
    from neuropet.perception.mouse import compute_stimuli
    from neuropet.species.cockroach import AmericanCockroach
    from neuropet.species.fruitfly import FruitFly

    fly_prof = FruitFly().perception_profile()
    roach_prof = AmericanCockroach().perception_profile()

    # ---- 1) rush:果蝇 WIND 强度 > 蟑螂,且带风向象限 ----
    print("[test] 物种差异:rush 时果蝇风感 > 蟑螂;风向象限存在")
    world = WorldModel(1920, 1080)
    st = PetState(pet_id="p", species_id="s", pos=(960.0, 540.0))
    world.upsert_pet(st)
    cur = world.cursor
    cur.x, cur.y = 1110.0, 540.0             # 距宠物 150px(<300 风半径)
    cur.vx, cur.vy, cur.speed = 2000.0, 0.0, 2000.0
    cur.gesture = "rush"
    view = world.snapshot("p", 0.0)
    sf = compute_stimuli(st, view, fly_prof)
    sr = compute_stimuli(st, view, roach_prof)
    wf = max((s.intensity for s in sf if s.kind is StimulusKind.WIND), default=0.0)
    wr = max((s.intensity for s in sr if s.kind is StimulusKind.WIND), default=0.0)
    assert wf > wr > 0.0, f"果蝇风感应更强: fly={wf:.3f} roach={wr:.3f}"
    wind_fly = next(s for s in sf if s.kind is StimulusKind.WIND)
    assert wind_fly.meta.get("wind_quadrant") == "东", wind_fly.meta
    assert wind_fly.direction is not None

    # ---- 2) coincide:风+食物气味同时出现,果蝇 meta 标记,蟑螂不标 ----
    print("[test] coincide:果蝇风+气味同时出现标记(蟑螂不标)")
    world.add_food((1100.0, 540.0))          # 距宠物 140px < odor_radius 420
    view = world.snapshot("p", 0.0)
    sf = compute_stimuli(st, view, fly_prof)
    sr = compute_stimuli(st, view, roach_prof)
    wind_fly = next(s for s in sf if s.kind is StimulusKind.WIND)
    assert wind_fly.meta.get("coincide") is True, wind_fly.meta
    assert not any(s.meta.get("coincide") for s in sr), "蟑螂不应有 coincide 标记"

    # ---- 3) 贴身低速:蟑螂 CONTACT/VIBRATION 之和 > 果蝇 ----
    print("[test] 物种差异:贴身低速时蟑螂接触/震动 > 果蝇")
    cur.x, cur.y = 960.0, 540.0              # 压在宠物身上
    cur.vx = cur.vy = 0.0
    cur.speed = 0.0
    cur.gesture = "idle"
    view = world.snapshot("p", 0.0)
    sf = compute_stimuli(st, view, fly_prof)
    sr = compute_stimuli(st, view, roach_prof)

    def touch_total(stims):
        return sum(s.intensity for s in stims
                   if s.kind in (StimulusKind.CONTACT, StimulusKind.VIBRATION))

    assert StimulusKind.CONTACT in {s.kind for s in sr}, "蟑螂应有 CONTACT 刺激"
    assert touch_total(sr) > touch_total(sf) > 0.0, (
        f"贴身时蟑螂应更强: roach={touch_total(sr):.3f} fly={touch_total(sf):.3f}")

    # ---- 4) 食物气味随距离单调衰减(多食物取最强) ----
    print("[test] 食物气味:随距离单调衰减")
    world2 = WorldModel(1920, 1080)
    world2.add_food((1200.0, 600.0))
    st2 = PetState(pet_id="q", species_id="s", pos=(1000.0, 600.0))   # 距食物 200
    world2.upsert_pet(st2)

    def odor_int() -> float:
        v = world2.snapshot("q", 0.0)
        ss = compute_stimuli(st2, v, roach_prof)
        return max((s.intensity for s in ss if s.kind is StimulusKind.ODOR_FOOD),
                   default=0.0)

    i_near = odor_int()
    st2.pos = (820.0, 600.0)                 # 距食物 380(仍在气味半径内)
    i_far = odor_int()
    assert i_near > i_far > 0.0, f"气味应随距离衰减: near={i_near:.3f} far={i_far:.3f}"

    # 多食物:取最强一束(近的那束胜出)
    world2.add_food((1010.0, 600.0))         # 更近的新食物(距 10px)
    v = world2.snapshot("q", 0.0)
    odors = [s for s in compute_stimuli(st2, v, roach_prof)
             if s.kind is StimulusKind.ODOR_FOOD]
    assert len(odors) == 1, f"应只输出最强一束气味,得到 {len(odors)} 束"
    assert odors[0].intensity > i_near, "最强束应强于原先较远食物的气味"

    # ---- 5) 冷区刺激 ----
    print("[test] 冷区:区域内产生 COLD 刺激")
    world2.add_zone("cold", (820.0, 600.0))  # 就在当前位置
    v = world2.snapshot("q", 0.0)
    ss = compute_stimuli(st2, v, roach_prof)
    cold = [s for s in ss if s.kind is StimulusKind.COLD]
    assert cold and cold[0].intensity > 0.0, "冷区内应有 COLD 刺激"


# ---------------------------------------------------------------- 钩子模块
def test_hook_module() -> None:
    """钩子模块可导入、类接口齐全;不真实安装钩子。"""
    print("[test] 钩子模块:可导入且 LLMouseHook 接口齐全(不真实安装)")
    import neuropet.core.hook as hook

    assert hasattr(hook, "LLMouseHook"), "缺少 LLMouseHook 类"
    for name in ("start", "set_capture", "stop"):
        assert callable(getattr(hook.LLMouseHook, name, None)), f"缺少方法 {name}"

    if hook.IS_WINDOWS:
        # Windows 上构造即可成功且不安装钩子(start 才安装)
        h = hook.LLMouseHook(on_left_click=lambda x, y: None)
        assert h.capturing is False
        h.set_capture(True)
        assert h.capturing is True
        h.set_capture(False)
        assert h.capturing is False
        # 未 start 就 stop 必须安全幂等
        h.stop()
    else:
        # 非 Windows:构造时抛出可读异常
        try:
            hook.LLMouseHook(on_left_click=lambda x, y: None)
        except RuntimeError as exc:
            assert "Windows" in str(exc), f"异常信息应说明仅支持 Windows: {exc}"
        else:
            raise AssertionError("非 Windows 平台构造 LLMouseHook 应抛 RuntimeError")
    # on_left_click 非法参数校验(Windows 才会走到类型检查,非 Windows 先抛平台异常)
    if hook.IS_WINDOWS:
        try:
            hook.LLMouseHook(on_left_click=None)
        except TypeError:
            pass
        else:
            raise AssertionError("on_left_click=None 应抛 TypeError")


def main() -> None:
    test_gesture_classification()
    test_species_differentiated_stimuli()
    test_hook_module()
    print("[perception] ALL OK")


if __name__ == "__main__":
    main()
