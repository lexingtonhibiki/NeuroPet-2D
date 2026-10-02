"""自发行为层验收:昼夜节律 / 自发理毛 / 主动趋近主人(互动性 B01/B02/B03)。

为什么必须有这个测试:R3 审计发现 GROOM 与 FROZEN 都是"已定义但脑永不发出"
的死功能。新增自发行为若不固化断言,下一轮又会退化成死代码。

直跑:`python tests/test_spontaneous.py`
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import neuropet.brain.spontaneous as SP
from neuropet.brain.fly_brain import FlyConnectomeBrain
from neuropet.brain.roach_brain import RoachBrain
from neuropet.core.contracts import Behavior, PetState, Stimulus, StimulusKind
from neuropet.core.world import WorldModel

FAILS: list[str] = []
DT = 1.0 / 60.0
SCREEN = (1920, 1080)


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


class _Cursor:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = x, y


class _FakeClock:
    """替换 spontaneous.datetime,固定到指定小时,便于断言昼夜节律。"""

    def __init__(self, hour: int) -> None:
        self._h = hour

    def now(self):
        class _N:
            hour = self._h
            minute = 0
        return _N()


def _brain_world(cls, tag: str):
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=tag, species_id=tag, pos=(960.0, 540.0))
    return cls(state=st), w, st


# ------------------------------------------------------------------ ① 昼夜
def test_circadian() -> None:
    print("[1] 昼夜节律(蟑螂夜行 / 果蝇晨昏性)")
    real = SP.datetime
    try:
        SP.datetime = _FakeClock(13)      # 正午
        noon_roach = SP.circadian_rest_bias(nocturnal=True)
        noon_fly = SP.circadian_rest_bias(nocturnal=False)
        SP.datetime = _FakeClock(2)       # 凌晨
        night_roach = SP.circadian_rest_bias(nocturnal=True)
        night_fly = SP.circadian_rest_bias(nocturnal=False)
        SP.datetime = _FakeClock(9)       # 上午
        morn_fly = SP.circadian_rest_bias(nocturnal=False)
    finally:
        SP.datetime = real
    check(noon_roach > 0.9, f"蟑螂正午静息倾向高({noon_roach:.2f})——昼伏夜出")
    check(night_roach < 0.2, f"蟑螂夜间静息倾向低({night_roach:.2f})——夜行")
    check(night_fly > 0.5, f"果蝇凌晨静息倾向高({night_fly:.2f})——夜间睡眠")
    check(morn_fly < 0.2, f"果蝇上午静息倾向低({morn_fly:.2f})——晨昏活跃")


# ------------------------------------------------------------------ ② 理毛
def test_grooming() -> None:
    print("[2] 自发理毛(B01):停歇期周期触发,持续 1~3s")
    sp = SP.Spontaneous(nocturnal=False)
    # 空闲推进到倒计时结束
    for _ in range(int(200.0 / DT)):
        sp.update(DT, 0.0, None)
        if sp.groom_cd <= 0.0:
            sp.groom_begin()
            break
    check(sp.wants_groom, "空闲足够久后进入理毛")
    dur = sp.groom_left
    check(1.0 <= dur <= 3.0, f"理毛时长 {dur:.2f}s 落在 1~3s")
    # 理毛期间不受短刺激打断,且到期自动结束
    n = 0
    while sp.wants_groom and n < int(10.0 / DT):
        sp.update(DT, 0.0, None)
        n += 1
    check(not sp.wants_groom, "理毛到期自动结束(不会卡死)")


# ------------------------------------------------------------------ ③ 找主人
def test_owner_approach() -> None:
    print("[3] 主动趋近主人(B02)")
    sp = SP.Spontaneous(nocturnal=False)
    # (a) 光标从未移动(无头仿真/终验场景)→ **绝不能**触发趋近
    cur = _Cursor(500.0, 500.0)
    for _ in range(int(60.0 / DT)):
        sp.update(DT, 0.0, cur)
    check(not sp.has_owner and not sp.wants_owner,
          "光标从未移动 → 不触发趋近(保护 QF 无刺激运动学指标)")
    # (b) 主人移动过 → 之后静止且无刺激 → 触发趋近
    for i in range(30):
        sp.update(DT, 0.0, _Cursor(500.0 + i * 5.0, 500.0))
    check(sp.has_owner, "光标移动过 → 认定存在主人")
    sp.update(DT, 0.8, cur)                 # 一次强刺激 → 空闲清零
    for _ in range(int(20.0 / DT)):
        sp.update(DT, 0.0, cur)
    check(sp.wants_owner,
          f"主人静止 {sp.since_cursor_s:.0f}s 且无刺激 {sp.idle_s:.0f}s → 趋近")
    # (c) 主人重新活动 → 立刻取消趋近(不是死状态)
    sp.update(DT, 0.0, _Cursor(900.0, 300.0))
    check(not sp.wants_owner, "主人重新移动 → 趋近立即取消")


# ------------------------------------------------------------------ ④ 接线
def test_wired_into_brains() -> None:
    print("[4] 接线:两个脑都真的会发出这些行为")

    def tick(brain, w, st, n, stimuli=None):
        out = []
        for i in range(n):
            brain.observe(w.snapshot(st.pet_id, i * DT), list(stimuli or []), DT)
            out.append(brain.decide(w.snapshot(st.pet_id, i * DT)))
        return out

    for cls, tag in ((FlyConnectomeBrain, "fly"), (RoachBrain, "roach")):
        b, w, st = _brain_world(cls, tag)
        check(hasattr(b, "spon"), f"{tag}:脑已挂载自发行为层")
        # 强制进入理毛 → 决策必须真的返回 GROOM
        b.spon.groom_left = 1.0
        b.spon.idle_s = 30.0
        cmds = tick(b, w, st, 5)
        check(any(c.behavior == Behavior.GROOM for c in cmds),
              f"{tag}:理毛状态真的由 decide 发出(GROOM)")
        # 强制"主人来过且现在安静" → 必须朝光标移动,且**不是**逃逸
        b.spon.groom_left = 0.0
        b.spon.has_owner = True
        b.spon.since_cursor_s = 30.0
        b.spon.idle_s = 30.0
        b.spon.rest_left = 0.0
        b.spon.rest_cd = 999.0
        cmds = tick(b, w, st, 5)
        check(all(c.behavior != Behavior.ESCAPE for c in cmds),
              f"{tag}:趋近主人的过程不触发逃逸(是靠近不是逃跑)")
        check(any(c.behavior in (Behavior.EXPLORE, Behavior.SEEK_FOOD)
                  and c.target is not None for c in cmds),
              f"{tag}:趋近带目标点(朝主人)")


# ------------------------------------------------------------------ ⑤ 优先级
def _survival_first(cls, tag: str, build) -> bool:
    """在自发态(理毛中 + 想找主人)下施加逼近威胁,看是否真的被抢占。"""
    b, w, st = _brain_world(cls, tag)
    b.spon.groom_left = 5.0          # 正在理毛
    b.spon.has_owner = True
    b.spon.since_cursor_s = 60.0
    b.spon.idle_s = 60.0
    for i in range(60):
        b.observe(w.snapshot(st.pet_id, i * DT), build(i, st), DT)
        cmd = b.decide(w.snapshot(st.pet_id, i * DT))
        if cmd.behavior in (Behavior.ESCAPE, Behavior.TAKEOFF):
            return True
    return False


def _loom_bundle(i: int, st: PetState) -> list[Stimulus]:
    """果蝇:一次真实的"快速逼近"——阴影 + 被推动的空气同时到达。

    必须用真实的多通道包而不是"单通道强阴影恒照":
    * perception/mouse.py 的判据是 WIND_R=300 且 speed>60 → 风,
      SHADOW_R=110 且 speed>700 → 阴影;即**任何够格产生阴影的逼近必然
      同时产生风**。真实逼近物体推空气,这是 GF 门控的"机械一致性"项。
    * 且阴影强度必须**持续上升**:S7 超选择检测的是扩张率(loom_rise),
      强度一旦饱和(rise=0)就只剩 0.3 的静态权重 —— 静止的大黑影不是威胁,
      这是 LPLC2 的正确生理语义。早前版本把强度 0.2s 内拉到 0.95 再恒照,
      超选择在下一拍即塌到 0.27,逃逸自然不触发(那是测试的错,不是脑的错)。
    """
    inten = min(0.95, 0.25 + i * 0.03)
    pos = (st.pos[0] + 40.0, st.pos[1])
    return [
        Stimulus(kind=StimulusKind.SHADOW, source="cursor", pos=pos,
                 intensity=inten),
        Stimulus(kind=StimulusKind.WIND, source="cursor", pos=pos,
                 intensity=min(0.9, 0.2 + i * 0.03)),
    ]


def test_survival_wins() -> None:
    print("[5] 自发行为绝不压过生存需求")
    check(_survival_first(FlyConnectomeBrain, "fly", _loom_bundle),
          "果蝇:理毛/趋近中遇逼近阴影(loom+风)仍逃逸(生存需求优先)")

    def _roach_wind(i: int, st: PetState) -> list[Stimulus]:
        # 蟑螂:尾须(机械感)是主通道 —— 逼近带来的强震动触发尾须反射
        return [Stimulus(kind=StimulusKind.VIBRATION, source="cursor",
                         pos=(st.pos[0] + 40.0, st.pos[1]), intensity=0.85)]

    check(_survival_first(RoachBrain, "roach", _roach_wind),
          "蟑螂:理毛/趋近中遇强震动仍尾须逃逸(反射排在自发之前)")


def main() -> None:
    print("=" * 74)
    print("自发行为层验收(昼夜节律 / 理毛 / 主动趋近)")
    print("=" * 74)
    test_circadian()
    test_grooming()
    test_owner_approach()
    test_wired_into_brains()
    test_survival_wins()
    print("\n" + "=" * 74)
    if FAILS:
        print(f"失败 {len(FAILS)} 项:")
        for f in FAILS:
            print(f"  x {f}")
    else:
        print("全部判据通过")
    print("=" * 74)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
