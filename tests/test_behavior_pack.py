"""行为参数包测试(两态 run-and-turn 转向 + OU 纹理 + thigmotaxis 贴墙;
无显示器、无第三方库)。

运行:python tests/test_behavior_pack.py
第二波"果蝇转圈"修复(决策记录 §4.3 / F4 冻结点 / D1 §3.2 VC 短弧配方)的
落地验收:
  ① OU 纹理:脑内 OU 状态自相关显著高于同幅白噪声、稳态 std 受 σ 驱动
     (退烧后 σ=1.1/1.0,稳态 std≈0.8;以 MICRO_RATE_* 限幅角速度驱动航向微摆);
     路径级航向增量仍平滑相关(弧线段 + 航点对准段),非白噪声折线;
  ② 探索路径不出屏、航点牵引有效(牵引开/关对比的真到点率);
  ③ 蟑螂贴墙:贴墙段转向偏置方向正确 + 沿边滑行(开/关对比),果蝇不做贴墙;
  ④ 参数表常量存在且注释含出处(deskbug / F4 / D1 行号);
  ⑤ 零漂移项(防御②):10s 无刺激直行航向漂移 <5°(B 案适应度疫苗的
     运行时等价物:直行巡航态不得自带系统性偏航);
  ⑥ 防转圈兜底:60s 无刺激 |ω| 均值 <40°/s、同号累计 <270°、巡航转弯半径
     中位数 ≥3BL、整路径绕圈检测无命中(质心距/路径长,含屏幕尺度守卫)。
既有对外行为不回归:test_brain.py 8 项等由各自套件保证。
"""
from __future__ import annotations

import inspect
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core.contracts import PetState
from neuropet.core.world import WorldModel
from neuropet.brain import fly_brain as fb
from neuropet.brain import roach_brain as rb
from neuropet.brain.fly_brain import FlyConnectomeBrain
from neuropet.brain.roach_brain import RoachBrain
from neuropet.species.cockroach import PARAMS as ROACH
from neuropet.species.fruitfly import PARAMS as FLY
from neuropet.body.base import GenericInsectBody

DT = 1.0 / 60.0
SCREEN = (1920, 1080)
W = 2.0 * math.pi
HALF_DIAG = math.hypot(*SCREEN) / 2.0


def make_scene(pet_id: str, pos=(960.0, 540.0), heading: float = 0.0):
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=pet_id, species_id="test", pos=pos, heading=heading)
    w.upsert_pet(st)
    return w, st, w.snapshot(pet_id, 0.0)


def lag1_autocorr(series: list[float]) -> float:
    """滞后 1 自相关系数(均值守恒的朴素估计)。"""
    n = len(series)
    m = sum(series) / n
    num = sum((series[i] - m) * (series[i + 1] - m) for i in range(n - 1))
    den = sum((s - m) ** 2 for s in series)
    return num / den if den > 0 else 0.0


def shuffled_like(series: list[float], seed: int) -> list[float]:
    """同幅白噪声对照:保持边际分布/方差、打乱时间结构(固定种子)。"""
    s = series[:]
    random.Random(seed).shuffle(s)
    return s


def ang_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % W - math.pi


# ① OU 纹理:脑内 OU 状态与实际航向增量的自相关均显著高于同幅白噪声
def test_ou_turn_autocorrelation() -> None:
    for cls, params, tag in ((RoachBrain, ROACH, "roach"),
                             (FlyConnectomeBrain, FLY, "fly")):
        # (a) 脑级:OU 角速度状态应强相关(理论 r1≈exp(-dt/τ)≈0.98)且被 σ 驱动。
        # 退烧后 σ=1.0/1.1、限幅 ±1.2:稳态分布被限幅截断,实测 std ≈
        # 0.6~0.65(理论未截断值 σ√(τ/2) 的 0.65~0.8 倍),故按比例容差断言。
        w, st, view = make_scene(f"ou_{tag}")
        brain = cls(st)
        ou = []
        for _ in range(3000):
            brain.observe(view, [], DT)
            ou.append(brain._ou)
        r1 = lag1_autocorr(ou)
        r1_shuf = lag1_autocorr(shuffled_like(ou, 7))
        std = _pstdev(ou)
        tau = rb.OU_TAU if tag == "roach" else fb.OU_TAU
        sigma = rb.OU_SIGMA if tag == "roach" else fb.OU_SIGMA
        rate_max = rb.OU_RATE_MAX if tag == "roach" else fb.OU_RATE_MAX
        th = sigma * math.sqrt(tau / 2.0)
        assert r1 > 0.8, f"{tag}: OU 状态 r1={r1:.3f} 应 >0.8(长程相关)"
        assert r1_shuf < 0.1, f"{tag}: 白噪声对照 r1={r1_shuf:.3f} 应 <0.1"
        assert 0.5 * th <= std <= 1.05 * th, \
            f"{tag}: OU 稳态标准差 {std:.2f} 应在 σ√(τ/2)={th:.2f} 的截断量级"
        assert max(abs(x) for x in ou) <= rate_max + 1e-9, \
            f"{tag}: OU 状态应被 ±{rate_max} 限幅"
        # (b) 路径级:脑在环 + 身体执行,航向增量序列的自相关(排除撞墙枢转帧)。
        # 两态转向下增量 = 弧线段/航点对准段(强相关)+ OU 微摆抖动(近独立),
        # 整体仍须显著高于白噪声对照(平滑弧线而非折线)。
        # r24 口径修订:**10 帧块聚合后**再算 r1。逐帧口径的旧阈 0.6 有相当
        # 部分由「瞬反撞墙后大脑长弧回舵」撑起——r24 撞墙改枢转后该后遗弧
        # 消失,果蝇地面探索以直行+OU 微摆为主,逐帧 r1 落到 0.1~0.4 的
        # 轨迹成分彩票;块聚合让弧线增量(ω≈0.5-0.85rad/s → 10 帧 80-140
        # mrad)相对 OU 噪声(σ≈7mrad/帧,块和 ≈22mrad)重占主导。校准:
        # 真实(5 种子)块 r1≈0.42,之字对照(交替符号)≈-0.01,白噪≈0。
        dth = _heading_increments(cls, params, 1200)
        blocks = [sum(dth[i:i + 10]) for i in range(0, len(dth) - 10, 10)]
        pr1 = lag1_autocorr(blocks)
        pr1_shuf = lag1_autocorr(shuffled_like(blocks, 7))
        assert pr1 > 0.25, f"{tag}: 块聚合航向增量 r1={pr1:.3f} 应 >0.25(平滑弧线而非折线)"
        assert pr1_shuf < 0.2, f"{tag}: 航向增量白噪声对照 r1={pr1_shuf:.3f} 应 <0.2"
        assert pr1 - pr1_shuf > 0.2, \
            f"{tag}: 转向自相关应显著高于白噪声({pr1:.2f} vs {pr1_shuf:.2f})"
        print(f"    [ou] {tag}: 状态 r1={r1:.2f}(shuf {r1_shuf:.2f}) σ状态={std:.2f}"
              f"/{th:.2f} 路径 dθ 块10 r1={pr1:.2f}(shuf {pr1_shuf:.2f})")


def _pstdev(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _heading_increments(cls, params, n: int) -> list[float]:
    """脑在环 + 身体执行的航向增量(排除撞墙反弹帧)。

    r24 口径:旧反射是 |dθ|>0.4 的单帧尖峰,幅值过滤即够;r24 改 900°/s
    枢转后同一事件摊成 ~12 帧 0.26rad/帧——恰好穿过幅值过滤,大块同号增量
    混进序列把 r1 从 ~0.7 拉到 0.41(果蝇)。改为按**枢转活跃标志**语义排除
    (外部几何事件,非自主转向),幅值过滤保留兜底。"""
    w, st, view = make_scene("hdg")
    brain = cls(st)
    brain.flyer = False            # 排除偶发起降对地面转向统计的干扰
    body = GenericInsectBody(st, dict(params))
    dths, last = [], st.heading
    for _ in range(n):
        brain.observe(view, [], DT)
        body.apply(brain.decide(view), view, DT)
        dh = ang_diff(st.heading, last)
        if abs(dh) <= 0.4 and body._pivot_t is None:
            dths.append(dh)
        last = st.heading
    return dths


# ② 探索路径不出屏;航点牵引有效(牵引开/关对比的真到点率,固定种子可复现)
def test_explore_bounds_and_waypoint_pull() -> None:
    # 真到点阈值:果蝇 75px(航点 renew 半径 70,真正抵达而非掠过);
    # 蟑螂 260px(其航点 renew 半径 240,须覆盖否则到点永不计数)
    arrive_px = {"roach": 260.0, "fly": 75.0}
    for cls, params, n, tag in ((RoachBrain, ROACH, 1500, "roach"),
                                (FlyConnectomeBrain, FLY, 2500, "fly")):
        eps_on = _wander_episodes(cls, params, n, tag=f"{tag}-on",
                                  arrive_px=arrive_px[tag])
        # 出屏断言:身体钳制 80px、目标钳制 60px(留浮点余量)
        for pos, tgt, wp in eps_on["trace"]:
            assert 70.0 <= pos[0] <= SCREEN[0] - 70.0 and \
                70.0 <= pos[1] <= SCREEN[1] - 70.0, \
                f"{tag}: 探索位置出屏 {pos}"
            if tgt is not None:
                assert 50.0 <= tgt[0] <= SCREEN[0] - 50.0 and \
                    50.0 <= tgt[1] <= SCREEN[1] - 50.0, \
                    f"{tag}: 探索目标出屏 {tgt}"
        # 航点牵引:真到点率应显著,且高于同种子牵引关闭对照
        rate_on = eps_on["arrivals"] / eps_on["episodes"]
        assert eps_on["episodes"] >= 3, f"{tag}: 巡视点数过少 {eps_on['episodes']}"
        assert rate_on >= 0.6, \
            f"{tag}: 航点到点率 {rate_on:.0%}({eps_on['arrivals']}/{eps_on['episodes']})应 ≥60%"
        try:
            rb.EXPLORE_WP_PULL, fb.EXPLORE_WP_PULL = 0.0, 0.0
            eps_off = _wander_episodes(cls, params, n, tag=f"{tag}-off",
                                       arrive_px=arrive_px[tag])
        finally:
            rb.EXPLORE_WP_PULL, fb.EXPLORE_WP_PULL = 3.0, 6.0
        rate_off = eps_off["arrivals"] / eps_off["episodes"]
        assert rate_on > rate_off, \
            f"{tag}: 牵引开({rate_on:.0%})应高于关({rate_off:.0%})"
        print(f"    [wp] {tag}: 真到点率 开={rate_on:.0%} 关={rate_off:.0%} "
              f"({eps_on['episodes']} 个航点/{n} 步)")


def _wander_episodes(cls, params, n: int, tag: str = "",
                     arrive_px: float = 250.0) -> dict:
    """自由探索 n 步:记录(位置,目标)轨迹与各航点存续期内的最近距离。"""
    w, st, view = make_scene(f"wp_{tag}")
    brain = cls(st)
    brain.flyer = False            # 排除偶发起降对地面航点统计的干扰
    body = GenericInsectBody(st, dict(params))
    episodes = 0
    arrivals = 0
    cur_wp = None
    min_d = 1e18
    trace = []
    for _ in range(n):
        brain.observe(view, [], DT)
        cmd = brain.decide(view)
        body.apply(cmd, view, DT)
        if brain._wp is not cur_wp:
            if cur_wp is not None:
                episodes += 1
                arrivals += 1 if min_d < arrive_px else 0
            cur_wp = brain._wp
            min_d = 1e18
        if cur_wp is not None:
            min_d = min(min_d, math.dist(st.pos, cur_wp))
        trace.append((st.pos, cmd.target, cur_wp))
    if cur_wp is not None:
        episodes += 1
        arrivals += 1 if min_d < arrive_px else 0
    return {"trace": trace, "episodes": episodes, "arrivals": arrivals}


# ③ 蟑螂贴墙:偏置方向正确(远墙吸引/带内切向/保持距离内倾);果蝇不做贴墙
def test_roach_thigmotaxis_wall_follow() -> None:
    steer = RoachBrain._wall_steer
    # 带内(右墙):与当前航向最近的切线方向强拉 —— 方向断言
    bias = steer(1820.0, 540.0, SCREEN, 0.0)          # 正对墙 → 平手取 +π/2(向下沿边)
    assert bias > 3.0, f"带内切向偏置应强且为正(取最近切向),得到 {bias:.2f}"
    bias = steer(1820.0, 540.0, SCREEN, -0.3)         # 略朝上 → 最近切向 -π/2(向上)
    assert bias < -3.0, f"航向 -0.3 应被拉向上方切向,得到 {bias:.2f}"
    # 远墙弱吸引:屏中最近墙为上墙,y-down 坐标下朝向上墙 = -π/2(方向修正断言)
    bias = steer(960.0, 540.0, SCREEN, 0.0)
    assert -1.5 < bias < 0.0, f"远墙吸引应朝上墙方向(负),得到 {bias:.2f}"
    # 保持距离:带内 200px 处切向目标向壁面内倾(正偏置推向墙),到 120px 归零
    bias = steer(1720.0, 540.0, SCREEN, 0.5 * math.pi)
    assert bias > 0.5, f"200px 处应有向壁面的内倾,得到 {bias:.2f}"
    bias = steer(1800.0, 540.0, SCREEN, 0.5 * math.pi)   # 恰为 WALL_HOLD_DIST
    assert abs(bias) < 1e-9, f"保持距离处应纯切向(偏置≈0),得到 {bias:.2f}"
    # 物种差异:果蝇不做贴墙(deskbug brain.py:877-878 飞行种偏置为 0)
    assert fb.WALL_FOLLOW is False and hasattr(RoachBrain, "_wall_steer") \
        and not hasattr(FlyConnectomeBrain, "_wall_steer")
    # 集成:从屏中向右墙走(航点钉在墙后制造"必到边缘"场景),贴墙段沿边滑行。
    # 切向份额按"最近墙的切线方向"度量(蟑螂可沿任一堵墙滑行,横穿墙段也算切向)
    on = _wall_run(True)
    off = _wall_run(False)
    assert on["bounces"] == 0, \
        f"thigmotaxis 开启不应撞墙反弹(硬反弹 {on['bounces']} 次)"
    assert on["inband"] > off["inband"], \
        f"贴墙时间应多于关闭:{on['inband']} vs {off['inband']}"
    assert on["tangent"] > off["tangent"], \
        f"带内切向航向份额应更高:{on['tangent']:.0%} vs {off['tangent']:.0%}"
    assert on["stretch"] >= 150 and on["stretch"] > off["stretch"], \
        (f"贴墙跟随应持续(on 最长连续带内 {on['stretch']} 帧 vs off "
         f"{off['stretch']} 帧,要求 on ≥150 帧且更长)")
    assert on["slide"] >= 250.0, \
        f"应沿边滑行 ≥250px(实际 {on['slide']:.0f}px)"
    print(f"    [wall] on: 带内 {on['inband']}/900 切向 {on['tangent']:.0%} "
          f"反弹 {on['bounces']} 滑行 {on['slide']:.0f}px "
          f"最长连续 {on['stretch']} 帧 | "
          f"off: {off['inband']} {off['tangent']:.0%} 反弹 {off['bounces']} "
          f"最长连续 {off['stretch']} 帧")


def _nearest_wall_tangential(pos, heading) -> bool:
    """航向是否与最近墙的切线一致(轴向 ±40° 内,不计方向):沿任意一堵墙
    滑行均计为切向(贴上墙向左/向右走等价,故按模 π 的轴向比较)。"""
    px, py = pos
    w, h = SCREEN
    walls = (px, py, w - px, h - py)              # 左/上/右/下
    wi = min(range(4), key=walls.__getitem__)
    tangents = (0.5 * math.pi, 0.0, 0.5 * math.pi, 0.0)[wi]
    d = abs(ang_diff(heading, tangents))
    return min(d, math.pi - d) <= math.radians(40.0)


def _wall_run(thigmo: bool, n: int = 900) -> dict:
    """蟑螂从屏中朝右墙走(航点钉在墙后):统计带内/切向/反弹/滑行。"""
    w, st, view = make_scene("wallrun", pos=(1500.0, 540.0), heading=0.0)
    brain = RoachBrain(st)
    brain.set_thigmotaxis(thigmo)
    brain._wp = (1900.0, 540.0)        # 航点钉在墙后:强制"向边缘走"场景
    brain._wp_until = 1e18
    body = GenericInsectBody(st, dict(ROACH))
    inband = tang = bounces = 0
    stretch = best_stretch = 0
    last_h, ys = st.heading, []
    for _ in range(n):
        brain.observe(view, [], DT)
        body.apply(brain.decide(view), view, DT)
        d_wall = SCREEN[0] - st.pos[0]
        if d_wall < rb.WALL_TRIGGER_DIST:
            inband += 1
            stretch += 1
            best_stretch = max(best_stretch, stretch)
            if _nearest_wall_tangential(st.pos, st.heading):
                tang += 1
        else:
            stretch = 0
        if abs(ang_diff(st.heading, last_h)) > 0.4:  # 单帧大转角 = 身体反弹
            bounces += 1
        last_h = st.heading
        ys.append(st.pos[1])
    return {"inband": inband, "tangent": tang / max(1, inband),
            "bounces": bounces, "slide": max(ys) - min(ys),
            "stretch": best_stretch}


# ④ 参数表常量存在、数值与出处注释齐备
def test_parameter_pack_constants_and_provenance() -> None:
    # 蟑螂:τ 保留 deskbug 原值;σ/限幅按 F4 冻结点退烧(2.2→1.0 / 3.0→1.2)
    for name in ("OU_TAU", "OU_SIGMA", "OU_RATE_MAX", "EXPLORE_WP_PULL",
                 "EXPLORE_LOOKAHEAD", "WALL_TRIGGER_DIST", "WALL_ATTRACT_GAIN",
                 "WALL_TANGENT_GAIN", "WALL_SPEED_FACTOR", "WALL_HOLD_DIST",
                 "WALL_HUG_TILT", "WALL_PULL_DAMP", "EXPLORE_SPEED_NOISE",
                 "RUN_DUR_S", "ARC_OMEGA", "ARC_DELTA", "TURN_ENTER", "TURN_EXIT",
                 "TURN_RADIUS_MIN_BL", "MICRO_RATE_MAX", "SAME_SIGN_ROT_MAX",
                 "WALL_FOLLOW_S"):
        assert hasattr(rb, name), f"roach_brain 缺少参数常量 {name}"
    assert rb.OU_TAU == 1.6 and rb.OU_SIGMA == 1.0 and rb.OU_RATE_MAX == 1.2, \
        "蟑螂 OU 参数应为 F4 退烧档(τ=1.6 保留 deskbug species.py:94;σ 2.2→1.0)"
    assert rb.TURN_RADIUS_MIN_BL >= 3.0, \
        "蟑螂最小弧线半径应 ≥3BL(速度自适应限幅的结构保证)"
    assert 0.0 < rb.EXPLORE_SPEED_NOISE <= 0.6, "探索速度噪声应有感知可辨的量级"
    # 果蝇:τ 更小、σ 更大(F4:σ 3.0→1.1、限幅 3.0→1.2),deskbug species.py:176 同源
    for name in ("OU_TAU", "OU_SIGMA", "OU_RATE_MAX", "EXPLORE_WP_PULL",
                 "EXPLORE_LOOKAHEAD", "EXPLORE_SPEED_NOISE", "WALL_FOLLOW",
                 "RUN_DUR_S", "ARC_OMEGA", "ARC_DELTA", "TURN_ENTER", "TURN_EXIT",
                 "TURN_RADIUS_MIN_BL", "MICRO_RATE_MAX", "SAME_SIGN_ROT_MAX"):
        assert hasattr(fb, name), f"fly_brain 缺少参数常量 {name}"
    assert fb.OU_TAU == 1.1 and fb.OU_SIGMA == 1.1 and fb.OU_RATE_MAX == 1.2
    assert fb.OU_TAU < rb.OU_TAU and fb.OU_SIGMA > rb.OU_SIGMA, \
        "果蝇探索应比蟑螂更急(κ 更大、σ 更大)"
    assert fb.TURN_RADIUS_MIN_BL >= 3.0, \
        "果蝇最小弧线半径应 ≥3BL(速度自适应限幅的结构保证)"
    assert fb.MICRO_RATE_MAX <= 0.02 and rb.MICRO_RATE_MAX <= 0.02, \
        "航向微摆角速度限幅应 ≤0.02rad/s(航点对准均值回拉下 10s 漂移 <5°)"
    # 出处注释:deskbug 行号与文献必须写在常量区(inspect 源码级检查)
    src_roach = inspect.getsource(rb)
    for token in ("deskbug brain.py:508-511", "species.py:94", "Maye 2007",
                  "brain.py:875-888", "species.py:105", "wall_margin"):
        assert token in src_roach, f"roach_brain 参数注释缺少出处:{token}"
    src_fly = inspect.getsource(fb)
    for token in ("deskbug brain.py:508-511", "species.py:176", "WALL_FOLLOW",
                  "877-878", "物种差异"):
        assert token in src_fly, f"fly_brain 参数注释缺少出处:{token}"
    print("    [param] 常量齐备;蟑螂 τ=1.6/σ=1.0,果蝇 τ=1.1/σ=1.1(F4 退烧档);"
          "出处注释完整")


# ⑤ 零漂移项(防御②):10s 无刺激直行,航向漂移 <5°
def test_straight_drift_no_stimulus() -> None:
    """直行巡航态(RUN)不得自带系统性偏航。

    白盒构造合法巡航场景:航点钉在正前方远处、RUN 态计时不到期(抑制转向
    事件/停顿),即"持续直行"工况;此时航向只应跟随限幅微摆角速度
    (±0.86°/s)并被航点对准项均值回拉,10s 净漂移 <5°。
    蟑螂需关闭 thigmotaxis:远墙吸引/贴墙跟随是主动转向行为,不属于"直行"
    漂移的度量范围(同逃逸转身豁免半径统计的口径)。场景用大世界(8000×3000):
    蟑螂 10s 直行 3000px,常规 1920×1080 屏内任何直线都会在 10s 内撞墙,
    撞墙后的回转是正确行为而非漂移,不应计入本项度量。
    """
    big_w, big_h = 8000, 3000
    for cls, params, tag, thigmo_off in ((RoachBrain, ROACH, "roach", True),
                                         (FlyConnectomeBrain, FLY, "fly", False)):
        w = WorldModel(big_w, big_h)
        pos = (big_w / 2.0, big_h / 2.0)
        st = PetState(pet_id=f"drift_{tag}", species_id="test", pos=pos)
        w.upsert_pet(st)
        view = w.snapshot(st.pet_id, 0.0)
        brain = cls(st)
        if thigmo_off:
            brain.set_thigmotaxis(False)
        h0 = st.heading
        brain._wp = (st.pos[0] + math.cos(h0) * 20000.0,
                     st.pos[1] + math.sin(h0) * 20000.0)
        brain._wp_until = 1e18
        brain._mode = "run"
        brain._mode_until = 1e18
        body = GenericInsectBody(st, dict(params))
        drift = 0.0
        max_frame = 0.0
        last = st.heading
        for _ in range(int(10.0 / DT)):
            brain.observe(view, [], DT)
            body.apply(brain.decide(view), view, DT)
            dh = ang_diff(st.heading, last)
            drift += dh
            max_frame = max(max_frame, abs(dh))
            last = st.heading
        assert brain._mode == "run", f"{tag}: 直行工况不应切出 RUN 态"
        assert abs(drift) < math.radians(5.0), \
            f"{tag}: 10s 无刺激直行航向漂移 {math.degrees(drift):.2f}° 应 <5°"
        assert max_frame < 0.05, \
            f"{tag}: 直行期单帧航向增量 {max_frame:.3f}rad 过大(存在隐性再瞄准)"
        print(f"    [drift] {tag}: 10s 直行航向漂移 {math.degrees(drift):+.2f}°"
              f"(<5°),单帧最大 {math.degrees(max_frame):.2f}°")


# ⑥ 防转圈兜底:60s 无刺激 |ω|、同号累计、转弯半径中位数、绕圈检测
def test_no_circling_60s() -> None:
    """用户投诉"莫名转圈"的回归断言(决策记录 §5.7 门槛的任务子集)。

    巡航转弯半径统计口径:|ω|∈(0.3, 2.5]rad/s 且速度 ≥35% 巡航的
    "非贴墙约束"帧。排除项(与逃逸转身同口径豁免):
    - |ω|>2.5rad/s 的单帧大转角 = 身体撞墙反射(物理反弹,非脑指令转向);
    - 蟑螂贴墙带内切向项施力帧(brain._wall_hold):壁面是物理约束,须
      ω ≥ v/d 防撞墙(其零反弹与贴墙行为由测试③单独验收);离墙穿越弧、
      远墙吸引弧与随机转向弧均为脑指令的限幅巡航转向,计入统计。
    绕圈判定 = 整路径质心距最大值/路径长 <0.15,且附屏幕尺度守卫
    (maxd < 0.35×半对角线):60s 路径长(蟑螂 18000px)远大于屏内任何路径
    可能的质心距,单看比值会把"正常满屏游走"误判为绕圈,守卫保证只命中
    "困在小圆盘内"的真绕圈。
    """
    for cls, params, tag, bl, cruise in ((RoachBrain, ROACH, "roach", 115.0, 300.0),
                                         (FlyConnectomeBrain, FLY, "fly", 30.0, 90.0)):
        w, st, view = make_scene(f"circle_{tag}")
        brain = cls(st)
        brain.flyer = False
        body = GenericInsectBody(st, dict(params))
        n = int(60.0 / DT)
        omegas, speeds, pts = [], [], []
        wall_hold, modes = [], []
        last = st.heading
        for _ in range(n):
            brain.observe(view, [], DT)
            cmd = brain.decide(view)
            body.apply(cmd, view, DT)
            om = ang_diff(st.heading, last) / DT
            omegas.append(om)
            speeds.append(st.speed)
            pts.append(st.pos)
            wall_hold.append(getattr(brain, "_wall_hold", False))
            modes.append(brain._mode)
            last = st.heading
        # |ω| 均值(§5.7:<40°/s)
        mean_abs = sum(abs(x) for x in omegas) / len(omegas)
        assert math.degrees(mean_abs) < 40.0, \
            f"{tag}: 60s |ω| 均值 {math.degrees(mean_abs):.1f}°/s 应 <40°/s"
        # 同号累计转角(连续同向旋转的积分,§5.7:<270°)
        best_rot = cur_rot = 0.0
        cur_sign = 0
        for om in omegas:
            s = 1 if om > 0.3 else (-1 if om < -0.3 else 0)
            if s == 0:
                cur_sign, cur_rot = 0, 0.0
            elif s == cur_sign:
                cur_rot += abs(om) * DT
            else:
                cur_sign, cur_rot = s, abs(om) * DT
            best_rot = max(best_rot, cur_rot)
        assert best_rot < math.radians(270.0), \
            f"{tag}: 连续同向累计 {math.degrees(best_rot):.0f}° 应 <270°"
        # 转弯事件半径分布(口径见 docstring):事件 = 平滑角速度同号连续段
        # (>0.3rad/s、累计转角 ≥20°),事件半径 = 弧长/转角(圆弧定义,对
        # 单帧角速度抖动稳健)。中位数 ≥3BL。
        k = max(1, int(0.25 / DT))
        sm = [sum(omegas[max(0, i - k):i + 1]) / (i + 1 - max(0, i - k))
              for i in range(n)]
        events = []
        cur_len = cur_rot = 0.0
        cur_sign = 0

        def _close_event(sign, ln, rot):
            if sign != 0 and rot >= 0.35 and ln > 1.0:
                events.append(ln / rot / bl)

        for i in range(n):
            om = omegas[i]
            s = 1 if sm[i] > 0.3 else (-1 if sm[i] < -0.3 else 0)
            if s != cur_sign:
                _close_event(cur_sign, cur_len, cur_rot)
                cur_sign, cur_len, cur_rot = s, 0.0, 0.0
            if s != 0 and modes[i] != "show" and not wall_hold[i]:
                cur_len += speeds[i] * DT
                cur_rot += abs(om) * DT
        _close_event(cur_sign, cur_len, cur_rot)
        assert len(events) >= 5, \
            f"{tag}: 转向事件过少({len(events)}),两态转向未生效"
        med = sorted(events)[len(events) // 2]
        assert med >= 3.0, \
            f"{tag}: 转弯半径中位数 {med:.2f}BL 应 ≥3BL(事件数 {len(events)})"
        # 绕圈检测(整路径):质心距最大值/路径长 <0.15 判绕圈(附屏幕尺度守卫)
        hit, maxd, plen = _circle_hit(pts)
        assert not hit, \
            (f"{tag}: 60s 路径命中绕圈检测(质心距峰值 {maxd:.0f}px / "
             f"路径长 {plen:.0f}px),疑似转圈")
        print(f"    [circle] {tag}: |ω|={math.degrees(mean_abs):.1f}°/s "
              f"同号累计 {math.degrees(best_rot):.0f}° 半径中位 {med:.1f}BL "
              f"(事件 {len(events)}) 绕圈比值 {maxd / plen:.3f} maxd={maxd:.0f}px")


def _circle_hit(pts: list[tuple[float, float]]) -> tuple[bool, float, float]:
    """整路径绕圈判定:返回(是否命中, 质心距峰值, 路径长)。"""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    maxd = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts)
    plen = sum(math.dist(pts[i], pts[i + 1]) for i in range(n - 1))
    hit = maxd < 0.15 * plen and maxd < 0.35 * HALF_DIAG
    return hit, maxd, plen


TESTS = [test_ou_turn_autocorrelation,
         test_explore_bounds_and_waypoint_pull,
         test_roach_thigmotaxis_wall_follow,
         test_parameter_pack_constants_and_provenance,
         test_straight_drift_no_stimulus,
         test_no_circling_60s]


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
        print(f"行为参数包测试:{len(TESTS) - failed}/{len(TESTS)} 通过")
        sys.exit(1)
    print(f"行为参数包测试:全部 {len(TESTS)} 项通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
