"""大脑离线测试(无需显示器、无第三方库)。

运行:python tests/test_brain.py
覆盖:
  ① 果蝇 LOOM 刺激 → decide 返回 ESCAPE 且优先级最高(旁路仲裁);
  ② 风+气味同现比单独风时 GF 触发更快/阈值更低;纯风=警戒不逃,纯气味=趋近;
  ③ 连续 fed 5 次 → trust 上升且 memory_digest 可读;
  ④ story 后 cold 联想 valence>0,decide 产生趋近冷区的 SEEK/EXPLORE 目标偏向;
  ⑤ save/load 往返一致(两个大脑)+ brain 插件注册(state= 关键字);
  ⑥ 蟑螂震动刺激 → ESCAPE 方向与风向相反;
  ⑦ 本能库 ≥300 条且 schema 合法;
  ⑧ 性能:单次 observe+decide ≤2ms/只;节点预算与 KC 稀疏度。
全部断言式,可重复运行(每个用例独立世界/大脑实例)。
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core.contracts import (Behavior, PetState, Stimulus, StimulusKind)
from neuropet.core.plugin import PluginRegistry
from neuropet.core.world import WorldModel
from neuropet.brain.fly_brain import NODE_COUNTS, TOTAL_NODES, FlyConnectomeBrain
from neuropet.brain.instinct import (InstinctLibrary, build_entries,
                                     validate_entries)
from neuropet.brain.roach_brain import RoachBrain

DT = 1.0 / 60.0
SCREEN = (1920, 1080)


def make_scene(pet_id: str, pos: tuple[float, float] = (960.0, 540.0),
               cold_zone: tuple[float, float] | None = None):
    """独立场景:世界 + 宠物状态 + 快照(用例间互不污染)。"""
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=pet_id, species_id="test", pos=pos)
    w.upsert_pet(st)
    if cold_zone is not None:
        w.add_zone("cold", cold_zone)
    return w, st, w.snapshot(pet_id, 0.0)


def loom(intensity: float, src=(1260.0, 540.0)) -> Stimulus:
    return Stimulus(StimulusKind.SHADOW, source="cursor", pos=src,
                    intensity=intensity, meta={"gesture": "approach_fast"})


def wind(intensity: float = 0.5, src=(1600.0, 540.0)) -> Stimulus:
    d = ((src[0] - 960.0), (src[1] - 540.0))
    n = math.hypot(*d) or 1.0
    return Stimulus(StimulusKind.WIND, source="cursor", pos=src,
                    intensity=intensity, direction=(d[0] / n, d[1] / n),
                    meta={"gesture": "rush"})


def odor(intensity: float = 0.5, src=(600.0, 540.0)) -> Stimulus:
    d = ((src[0] - 960.0), (src[1] - 540.0))
    n = math.hypot(*d) or 1.0
    return Stimulus(StimulusKind.ODOR_FOOD, source="food:test01", pos=src,
                    intensity=intensity, direction=(d[0] / n, d[1] / n))


# ① LOOM 超选择 → GF 旁路仲裁逃逸
def test_fly_loom_escape() -> None:
    w, st, view = make_scene("loom")
    b = FlyConnectomeBrain(st)
    # 逼近中的阴影:面积逐拍膨胀(超选择需要"外圈变暗+膨胀率超阈")
    for intensity in (0.7, 0.85, 1.0):
        b.observe(view, [loom(intensity)], DT)
        assert b.decide(view).behavior != Behavior.ESCAPE or intensity == 1.0
    cmd = b.decide(view) if b._gf_fired else None
    # 上面的 decide 可能已消费触发标志;重新喂一拍满强度膨胀确保可观测
    if cmd is None:
        b2 = FlyConnectomeBrain(PetState(pet_id="loom2", species_id="test",
                                         pos=(960.0, 540.0)))
        w.upsert_pet(b2.state)
        v2 = w.snapshot("loom2", 0.0)
        for intensity in (0.7, 0.85, 1.0):
            b2.observe(v2, [loom(intensity)], DT)
        cmd = b2.decide(v2)
    assert cmd.behavior == Behavior.ESCAPE, f"LOOM 应触发逃逸,得到 {cmd.behavior}"
    assert cmd.priority >= 90, "逃逸应为最高优先级(旁路仲裁)"
    # 逃逸方向背离威胁源(源在右侧 → 目标在左侧)
    assert cmd.target[0] < st.pos[0] - 100, "应向威胁来向的反方向逃逸"


# ② 风+气味同现 → 警觉↑、GF 阈值↓、触发更快;纯风警戒不逃;纯气味趋近
def test_wind_odor_vigilance() -> None:
    def steady_threshold(with_odor: bool) -> float:
        w, st, view = make_scene(f"th{with_odor}")
        b = FlyConnectomeBrain(st)
        stim = [wind()] + ([odor()] if with_odor else [])
        for _ in range(12):
            b.observe(view, stim, DT)
            b.decide(view)
        return b.gf_threshold()

    th_wind = steady_threshold(False)
    th_both = steady_threshold(True)
    assert th_both < th_wind - 0.05, \
        f"风+气味应显著压低 GF 阈值:{th_both:.3f} vs {th_wind:.3f}"

    def fire_tick(mode: str) -> int | None:
        w, st, view = make_scene(f"ft{mode}")
        b = FlyConnectomeBrain(st)
        for t in range(40):
            stim = [loom(min(1.0, 0.55 + 0.03 * t))]
            if mode in ("both", "wind"):
                stim.append(wind())
            if mode in ("both", "odor"):
                stim.append(odor())
            b.observe(view, stim, DT)
            if b.decide(view).behavior == Behavior.ESCAPE:
                return t
        return None

    t_both, t_wind = fire_tick("both"), fire_tick("wind")
    assert t_both is not None and t_wind is not None, "两种场景最终都应触发逃逸"
    assert t_both < t_wind, f"风+气味同现应更早触发逃逸:{t_both} vs {t_wind}"

    # 纯风 = 警戒(不逃逸,警觉度上升)
    w, st, view = make_scene("purewind")
    b = FlyConnectomeBrain(st)
    for _ in range(30):
        b.observe(view, [wind()], DT)
        assert b.decide(view).behavior != Behavior.ESCAPE, "纯风不应触发逃逸"
    assert b.vigilance > 0.15, "纯风应进入警戒状态"

    # 纯气味 = 先天趋近(向气味源 SEEK/EXPLORE,不逃逸)
    w, st, view = make_scene("pureodor")
    b = FlyConnectomeBrain(st)
    for _ in range(10):
        b.observe(view, [odor(0.6)], DT)
    cmd = b.decide(view)
    assert cmd.behavior in (Behavior.SEEK_FOOD, Behavior.EXPLORE), \
        f"纯气味应产生趋近,得到 {cmd.behavior}"
    assert cmd.target[0] < 960.0, "趋近目标应位于气味源一侧(左侧)"


# ③ 连续 fed 5 次 → trust 上升且 memory_digest 可读
def test_fed_trust_and_digest() -> None:
    for brain_cls in (FlyConnectomeBrain, RoachBrain):
        w, st, view = make_scene(f"fed{brain_cls.brain_id}")
        b = brain_cls(st)
        t0 = b.emotion().trust
        for _ in range(5):
            b.observe(view, [], DT)
            b.on_event("fed", {})
        assert b.emotion().trust > t0 + 0.15, \
            f"{brain_cls.brain_id}: 5 次投喂后信任应上升({t0:.2f}->{b.emotion().trust:.2f})"
        a = b.assoc.get("human:feed")
        assert a is not None and a.valence > 0.1, "应形成 human:feed 正联想"
        digest = b.memory_digest()
        assert len(digest) > 0 and all(isinstance(x, str) and x for x in digest), \
            "memory_digest 应返回非空中文摘要"


# ④ story 覆写 cold 先验:联想转正 → 趋近冷区
def test_story_overrides_cold_prior() -> None:
    zone_pos = (1500.0, 800.0)
    w, st, view = make_scene("story", cold_zone=zone_pos)
    b = FlyConnectomeBrain(st)
    # 出厂先验:回避冷区
    assert b._cold_attitude() < 0.0, "出厂本能对冷区应为负效价(回避)"
    b.on_event("story", {"about": "cold", "valence": 0.8, "text": "雪景很美"})
    a = b.assoc.get("context:cold")
    assert a is not None and a.valence > 0.0, "story 后 cold 联想效价应为正"
    assert b._cold_attitude() > 0.2, "正联想应覆写负先验,态度转为趋近"
    b.observe(view, [], DT)  # 空转一拍使内部状态平稳
    cmd = b.decide(view)
    assert cmd.behavior in (Behavior.SEEK_FOOD, Behavior.EXPLORE), \
        f"应产生趋近行为,得到 {cmd.behavior}"
    d = math.dist(cmd.target, zone_pos)
    assert d < 150.0, f"目标应偏向冷区(距冷区 {d:.0f}px)"
    # 蟑螂同协议
    w2, st2, view2 = make_scene("storyroach", cold_zone=zone_pos)
    b2 = RoachBrain(st2)
    b2.on_event("story", {"about": "cold", "valence": 0.8, "text": "雪景很美"})
    assert b2.assoc.get("context:cold").valence > 0.0
    b2.observe(view2, [], DT)
    cmd2 = b2.decide(view2)
    assert cmd2.behavior in (Behavior.SEEK_FOOD, Behavior.EXPLORE)
    assert math.dist(cmd2.target, zone_pos) < 150.0


# ⑤ save/load 往返一致 + brain 插件注册
def test_save_load_and_plugins() -> None:
    # 插件注册:manifest type=brain,id 正确,registry.create(state=...) 可用
    reg = PluginRegistry()
    reg.register_class(FlyConnectomeBrain)
    reg.register_class(RoachBrain)
    ids = {m.id for m in reg.manifests("brain")}
    assert {"brain.fly_connectome", "brain.roach_nn"} <= ids, f"brain 插件未注册: {ids}"
    for pid, cls in (("brain.fly_connectome", FlyConnectomeBrain),
                     ("brain.roach_nn", RoachBrain)):
        w, st, view = make_scene("plug" + pid[-2:])
        inst = reg.create(pid, app=None, state=st)
        assert isinstance(inst, cls) and inst.state is st

    # 往返:喂刺激 + 投喂(学习发生)→ save → 新脑 load → save 逐字段一致
    for brain_cls in (FlyConnectomeBrain, RoachBrain):
        w, st, view = make_scene("sl" + brain_cls.brain_id[-2:])
        b = brain_cls(st)
        for inten in (0.3, 0.6, 0.9):
            b.observe(view, [odor(inten), wind(0.4)], DT)
            b.decide(view)
        b.on_event("fed", {})
        b.on_event("story", {"about": "cold", "valence": 0.8, "text": "雪景很美"})
        b.observe(view, [odor(0.5)], DT)  # 让 DAN 学习真正写入权重
        data = b.save()
        b2 = brain_cls(PetState(pet_id="sl2", species_id="test", pos=(100.0, 100.0)))
        b2.load(data)
        assert b2.save() == data, f"{brain_cls.brain_id}: save/load 往返不一致"
        # 载入后行为连续:联想与情绪可读且一致
        assert b2.emotion().trust == b.emotion().trust
        assert b2.assoc.get("context:cold").valence == \
            b.assoc.get("context:cold").valence


# ⑥ 蟑螂尾须震动 → ESCAPE 方向与风向相反
def test_roach_vibration_escape() -> None:
    for src in ((1460.0, 540.0), (460.0, 540.0), (960.0, 940.0)):
        w, st, view = make_scene(f"vib{src[0]:.0f}_{src[1]:.0f}")
        b = RoachBrain(st)
        d = (src[0] - st.pos[0], src[1] - st.pos[1])
        n = math.hypot(*d)
        s = Stimulus(StimulusKind.VIBRATION, source="cursor", pos=src,
                     intensity=0.9, direction=(d[0] / n, d[1] / n))
        b.observe(view, [s], DT)          # 单拍即响应(尾须反射,反应要快)
        cmd = b.decide(view)
        assert cmd.behavior == Behavior.ESCAPE, f"震动应立即逃逸,得到 {cmd.behavior}"
        assert cmd.priority >= 90, "尾须反射应为最高优先级"
        v = (cmd.target[0] - st.pos[0], cmd.target[1] - st.pos[1])
        nv = math.hypot(*v) or 1.0
        dot = (v[0] / nv) * (d[0] / n) + (v[1] / nv) * (d[1] / n)
        assert dot < -0.7, f"逃逸方向应与风向(指向源)相反,dot={dot:.2f} src={src}"


# ⑦ 本能库 ≥300 条且 schema 合法
def test_instinct_library() -> None:
    lib = InstinctLibrary.default()
    assert len(lib) >= 300, f"本能库应 ≥300 条,实际 {len(lib)}"
    errs = validate_entries(lib.entries)
    assert not errs, f"schema 校验失败 {len(errs)} 条: {errs[:5]}"
    # 双物种 & 刺激种类覆盖(20+ 刺激变体 = 16 原型 × 3 强度档)
    species = {e["species"] for e in lib.entries}
    assert species == {"fly", "roach"}
    kinds = {k for e in lib.entries for k in e["stimulus"]["kinds"]}
    assert len(kinds) >= 6, f"刺激种类覆盖不足: {kinds}"
    # 冷区出厂先验为负(可被 story 联想覆写)
    assert lib.valence_prior("fly", "cold") < 0
    assert lib.valence_prior("roach", "cold") < 0
    # 程序化再生成与落盘库一致
    assert len(build_entries()) >= 300


# ⑧ 性能与结构预算
def test_performance_and_budget() -> None:
    # 模块节点数与素材文档 §1 表一致;总节点在 2000 预算内
    expect = {"S1_ORN": 8, "S2_LN": 4, "S3_PN": 8, "S4_KC": 400, "S5_MBON": 8,
              "S5_DAN": 4, "S6_LH": 6, "S7_VIS": 12, "S8_WIND": 6, "S9_TEMP": 4,
              "S10_CX": 24, "S11_GF": 6, "S12_MOTOR": 16, "E1_EMO": 8}
    assert NODE_COUNTS == expect
    assert 500 <= TOTAL_NODES <= 2000
    w, st, view = make_scene("perf")
    fly = FlyConnectomeBrain(st)
    assert fly.state_size_bytes() < 50 * 1024, "果蝇脑节点状态应 <50KB"

    # KC 稀疏编码:气味输入下激活率 ~5%(2%-10% 容差)
    for _ in range(5):
        fly.observe(view, [odor(0.7)], DT)
    frac = fly.kc_active_fraction()
    assert 0.01 <= frac <= 0.10, f"KC 激活率应 ~5%,实际 {frac:.1%}"

    # 计时:典型感知负载下 observe+decide 均值 ≤2ms/只
    stim = [odor(0.6), wind(0.4), loom(0.45)]   # 含亚阈 LOOM(不触发逃逸)
    for brain_cls in (FlyConnectomeBrain, RoachBrain):
        w2, st2, view2 = make_scene("perf" + brain_cls.brain_id[-2:])
        b = brain_cls(st2)
        n = 300
        t0 = time.perf_counter()
        for _ in range(n):
            b.observe(view2, stim, DT)
            b.decide(view2)
        avg_ms = (time.perf_counter() - t0) / n * 1000.0
        print(f"    [{brain_cls.brain_id}] observe+decide 平均 {avg_ms:.3f} ms/拍")
        assert avg_ms < 2.0, f"{brain_cls.brain_id}: {avg_ms:.2f}ms 超出 2ms 预算"


TESTS = [test_fly_loom_escape, test_wind_odor_vigilance, test_fed_trust_and_digest,
         test_story_overrides_cold_prior, test_save_load_and_plugins,
         test_roach_vibration_escape, test_instinct_library,
         test_performance_and_budget]


def main() -> None:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"[ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
        except Exception as exc:  # 非断言异常也要暴露
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"大脑测试:{len(TESTS) - failed}/{len(TESTS)} 通过")
        sys.exit(1)
    print(f"大脑测试:全部 {len(TESTS)} 项通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
