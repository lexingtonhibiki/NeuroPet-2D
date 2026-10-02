"""Q 版(chibi)+ 虹色渲染半场验收(E3/C1;调研 §二/§三 实施记录)。

判据(≥6,全绿退出码 0;运行 python tests/test_chibi_render.py):
  1. off 位等价 + 外观容差:①无重采样角度(0/90/180/270°)上 1x 域化 ≡ SS 域
     路径、且回退路径(NEUROPET_ROT_SS=1)与**固定参照提交** REF_COMMIT
     (b2ca945,C1 前最后提交;**不是** git HEAD,见 load_head docstring)逐字节
     相同;②跨路径
     外观容差(**r25 C1 收口:权威口径 = 全 360 桶逐桶**,见判据 1c/1d):
     alpha mean|Δ| ≤1.5/255、>16 ≤3.5%、α 掩膜 IoU ≥0.96、α 量比 ∈[0.96,1.005]、
     边带宽比 ≤1.85(**按所选滤波器 BICUBIC 的全圈分布重定**,原 1.60 是池均口径);
     阈值真源 = tools/c1_ref_render.py 常量,与测试内引用一致;③off 与
     chibi 态不同 —— 原判据/新判据/为什么见 test_off_bitident、
     test_off_appearance_vs_baseline(12 场景历史对照)与
     test_off_appearance_fullcircle(★权威)/ test_iou_floor_fullcircle
     的 docstring(r25 判据 #5 迁移 + 收口,架构 §7/§12)
  2. on 头部放大带:蟑螂头前伸长度 ×≥3.0、前伸域宽 ×∈[1.15,1.7](目标 ×1.3,
     头宽/体宽 off 1:3.1 → on ≈1:2.4,向 1:1.5 目标带方向)
  3. on 腿节段比例带:支撑相中间关节 ×0.60 精确且爪尖保位(钉足不滑);
     摆动相整链 ×0.60(stride 视觉 ×0.60,gait/poses 零改动)
  4. 触须:节点 9→7、弧长 ×0.45(带 ±1e-6)
  5. hue 相位循环:虚拟时钟周期内命中 0,1,2,0;三相位精灵两两不同;
     phase0(带键)与基线(无键)逐位一致(LUT 零旋转);色板 hue 旋转
     保留眼/高光键(_HUE_KEEP)
  6. traits 透传:base_traits 保留 chibi/hue_phase(进 _thash → L1/L2/master
     桶键);chibi on/off 各自独立 master(互不污染);PetHandle 默认双关
  7. 预算达标:chibi master 冷烘焙 ≤400ms/物种;chibi 整帧 P50/P95 过
     蟑螂 ≤7/9ms、果蝇 ≤3/4ms 门槛;全合成增量(on−off)**配对中位** ≤1ms
     (r25 裁决 12:估计量由上一轮的 `min(n)−min(o)` 改为 `sorted(n[i]−o[i])[10]`
     —— 前者取两臂**各自全局最小**、丢了 o/n 交替采样自带的相邻配对,散布大
     且套件里出现 1.29ms 越阈;原判据 → 新判据 → 为什么见 test_budget docstring)
  8. `invalidate` 真清(r25 修复**既存缺陷**):物种清/全清均无异常、字节计数
     归零、清后重建位等价、`variants_1x` 母图缓存换新对象(原缺陷:缺
     `global _rot_bytes` → 物种分支必抛 UnboundLocalError 被吞成半清,全清
     分支静默不归零;见 test_invalidate_species)
  9. 旋转桶构建**回归护栏**(⚠ 非性能目标):冷桶 **每角 3 遍取最快 → 全圈中位**
     ≤6ms(性能目标 median ≤3.0/p95 ≤4.5ms 见架构 §15.3 —— **随所选滤波器
     BICUBIC 修订**,原 ≤1.5/≤2.5 的前提 BILINEAR 已被外观判据否决;护栏只拦
     「退化回 ~11.7ms SS 域旧路径」,故阈值放宽到不抖的量级)。r25 账本行 22
     把统计量由「单遍 median」换成「每角 min-of-3 再取全圈中位」:阈值与覆盖
     不变,但 median 把「被别人抢走的 CPU」算进成本(实测闲时漂 45% vs 新口径 2%)
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DT = 1 / 60
BAKE_BUDGET_MS = 400.0
# test_budget 本轮的两个统计量读数(配对中位 = 判据;min(n)−min(o) = 参照读数列)。
# 由 main() 附在**验收末行**上:全套件跑手(run_all_tests.py)只保留子进程的最后一行
# —— 读数不外露的话,套件上下文里只看得见「红/绿」,看不见**为什么**、也看不见余量
# (r25 裁决 12 的教训:越阈那次只有失败正文能读到数字)。只作观察,不参与判决。
_BUDGET_READINGS: dict = {}
# ⑨ 旋转桶构建**回归护栏**——**这是回归护栏,不是性能目标**。
# 性能目标(架构 §15.3,**随所选滤波器 BICUBIC 修订**,原 ≤1.5/≤2.5 的前提 BILINEAR
# 已被外观判据否决):median ≤3.0ms / p95 ≤4.5ms。BICUBIC 实测 median 2.96–3.39ms
# (n=60 冷桶),若拿 4.0 当阈值只剩 15–26% 余量 → 会抖成**假红**;故取 6.0。
# 它拦的是「退化回 SS 域旧路径」(median 11.749 / p95 15.588ms),6.0 仍绰绰有余。
ROT_BUILD_MEDIAN_GUARD_MS = 6.0


# ---------------- 固定参照模块(回退态逐位一致的基准;C1 前最后提交) ----------------
# 参照锚 = **不可变提交** b2ca945(C1 落地前的最后提交),绝不是 git HEAD:
# 锚 HEAD 时,提交 C1 的瞬间 HEAD 就变成 1x 新版,而判据比的是 SS 回退路径 →
# 断言当场翻红,且语义退化为「新代码 vs 新代码」。锚固定 sha 后,本判据的结论
# 与 HEAD 位置无关(架构 §12 教训:b2ca945 的 U0 相对导入曾让 HEAD 锚判据进入
# ERROR 态;凡比对 git HEAD 的判据,绿灯必须在提交之后重采)。
REF_COMMIT = "b2ca945bdd731953b5489174cf90a6e02e47d494"


def load_head(name: str, tmp_name: str, ref: str = REF_COMMIT):
    """加载**固定参照提交** `ref` 的渲染模块(位等价基准)。

    本判据锁的语义是「回退路径(`_ROT_SS_DOMAIN=True`)仍**逐字节复现 C1 之前
    的实现**」,而不是「与当前 HEAD 一致」。`ref` 是常量 sha,函数体只查
    `git show <ref>:...`,全路径不出现 HEAD 记号 —— HEAD 前移/回退都不改变本
    判据的结论,提交 C1 不会再自爆(结构性解耦)。

    函数名 `load_head` 沿用历史名(曾取 git HEAD);语义以 ref=REF_COMMIT 为准。

    r25 修复(保留):以 `neuropet.render.<tmp_name>` 全名注册进 sys.modules ——
    参照版 torso_art.py 含相对导入(`from ..core import instr as _I`,b2ca945 U0
    插桩引入),按独立顶层模块加载会 ImportError("attempted relative import with
    no known parent package")。即本判据自 b2ca945 起一直是 ERROR 态(不是通过态)。"""
    src = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{ref}:neuropet/render/{name}.py"],
        capture_output=True, text=True, check=True).stdout
    tmp = ROOT / "scratch" / tmp_name
    tmp.write_text(src, encoding="utf-8")
    mod_name = f"neuropet.render.{tmp_name[:-3]}"
    spec = importlib.util.spec_from_file_location(mod_name, tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ref_tool():
    """载入 C1 参考渲染工具(判据 1b 的历史基线与度量口径真源)。"""
    spec = importlib.util.spec_from_file_location(
        "_c1_ref_render", ROOT / "tools" / "c1_ref_render.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_fly_poses():
    from neuropet.core.world import WorldModel
    from neuropet.core.contracts import PetState, Behavior, BehaviorCommand
    from neuropet.body.base import GenericInsectBody
    from neuropet.species.fruitfly import PARAMS as FLY
    out = []
    w = WorldModel(1920, 1080)
    st = PetState(pet_id="fx", species_id="fly", pos=(960.0, 540.0))
    st.heading = 0.7
    body = GenericInsectBody(st, dict(FLY))
    view = w.snapshot("fx", 0.0)
    body.apply(BehaviorCommand(Behavior.IDLE, priority=1), view, DT)
    out.append(("fly_rest", body.pose()))
    cmd = BehaviorCommand(Behavior.EXPLORE, target=(3000.0, 540.0), intensity=1.0)
    for k in (30, 90, 151):
        for _ in range(30):
            body.apply(cmd, view, DT)
        out.append((f"fly_walk{k}", body.pose()))
    body._flight.start_takeoff()
    for _ in range(120):
        body.apply(BehaviorCommand(Behavior.FLY_WANDER, intensity=0.8), view, DT)
    out.append(("fly_air", body.pose()))
    return out


def build_roach_pose():
    """蟑螂解剖链(7 点)姿态:含 1 条摆动腿 + 9 节触须(确定性手搓)。"""
    segs = [(44.0, 0.0, 0.0, 15.0, 14.0), (14.0, 0.0, 0.0, 24.0, 25.0),
            (-30.0, 0.0, 0.0, 33.0, 21.0)]
    legs = []
    for k, (ax, ay) in enumerate(((26.0, -14.0), (4.0, -20.0), (-8.0, -17.0),
                                  (26.0, 14.0), (4.0, 20.0), (-8.0, 17.0))):
        ux, uy = (0.6 if k % 2 else 0.8), (1.0 if ay < 0 else -1.0)
        d = math.hypot(ux, uy)
        ux, uy = ux / d, uy / d
        chain = [(ax, ay)]
        r = 52.0
        for f in (0.16, 0.42, 0.70, 0.88, 0.97):
            chain.append((ax + ux * r * f + 0.2 * k, ay + uy * r * f))
        chain.append((ax + ux * r, ay + uy * r))
        legs.append({"points": chain, "lift": 0.05 if k == 1 else 0.0,
                     "swing": k == 1, "angles": (0.0, 0.0, 0.0)})
    ants = []
    for side in (-1.0, 1.0):
        pts = [(52.0, side * 6.0)]
        for j in range(1, 9):
            pts.append((52.0 + 15.0 * j * math.cos(0.35 * side),
                        side * (6.0 + 7.0 * j * math.sin(0.35))))
        ants.append(pts)
    return {"half": 120, "altitude": 0.0, "segments": segs, "legs": legs,
            "antennae": ants,
            "wings": {"active": False, "phase": 0.0, "span": 70.0, "fold": 1.0}}


# ---------------- 像素量测(1x 精灵:1px=1 局部 px,原点=画布中心) ----------------
def col_span(img, col: int) -> int:
    a = img.getchannel("A").load()
    return sum(1 for y in range(img.size[1]) if a[col, y] > 0)


def content_front(img) -> int:
    bb = img.getchannel("A").getbbox()
    return bb[2] - 1 if bb else 0


def max_span_in(img, col0: int, col1: int) -> int:
    return max((col_span(img, c) for c in range(col0, col1 + 1)), default=0)


def body_max_width(img) -> int:
    a = img.getchannel("A").load()
    return max(sum(1 for y in range(img.size[1]) if a[x, y] > 0)
               for x in range(img.size[0]))


def arclen(ps):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(ps, ps[1:]))


# ---------------- 判据 ----------------
def _scene_bytes(fn, names):
    return {n: fn(n).tobytes() for n in names}


def test_off_bitident() -> None:
    """判据 1（r25 判据 #5 ① 迁移）：同路径自洽的位等价。

    原判据：`off` 态整帧/精灵与 git HEAD **逐字节**相同（锁的是实现路径）。
    新判据：① 默认路径在**无重采样角度**(0°/90°/180°/270°，PIL 走 copy/
            transpose 快路径)上与回退路径逐字节相同 —— 新路径与 C1 前语义
            在数学等价处**仍逐位**；
            ② 回退路径（`_ROT_SS_DOMAIN=True` = C1 前语义）与**固定参照提交**
            REF_COMMIT（b2ca945，C1 前最后提交；**不是** git HEAD）在全部 off
            场景上**逐字节**相同（原判据保留在回退路径上）—— 语义：回退路径
            仍逐字节复现 C1 之前的实现；
            ③ 默认路径冷/热缓存两次渲染逐字节相同（同路径自洽）。
    为什么：C1 把桶构建从 SS 域旋转改为 1x 域旋转，「vs HEAD 逐字节」在默认
            路径上数学必然破裂（§7 #5）。口径改为度量**外观**（判据 1b）并把
            位等价收窄到回退路径与无重采样角度，二者都是**不放松**的替换。
            参照锚由 git HEAD 改为固定提交 REF_COMMIT：锚 HEAD 是自爆结构 ——
            提交本单元时 HEAD 即变 1x 新版，断言当场翻红且语义退化成「新 vs
            新」（§12 教训）；锚固定 sha 后本判据结论与 HEAD 位置无关。
    """
    from neuropet.render import torso_art
    from neuropet.render.renderer import render_pose
    from neuropet.species.cockroach import AmericanCockroach
    from neuropet.species.fruitfly import FruitFly
    ref_r = load_head("renderer", "_chibi_renderer_head.py")
    ref_ta = load_head("torso_art", "_chibi_torsoart_head.py")
    tr_r = AmericanCockroach().render_traits()
    tr_f = FruitFly().render_traits()
    poses = build_fly_poses()
    pose_r = build_roach_pose()
    sids = (("species.cockroach", tr_r), ("species.fruitfly", tr_f))

    # ---- ② 回退路径 ≡ 固定参照提交 REF_COMMIT（原判据 1 逐字保留在回退路径上）----
    torso_art._ROT_SS_DOMAIN = True
    try:
        torso_art.invalidate()
        n = 0
        for tag, pose in poses:
            assert ref_r.render_pose(pose, tr_f).tobytes() == \
                render_pose(pose, tr_f).tobytes(), \
                f"[回退路径] {tag}: 整帧与参照 {REF_COMMIT[:7]} 不一致"
            n += 1
        assert ref_r.render_pose(pose_r, tr_r).tobytes() == \
            render_pose(pose_r, tr_r).tobytes(), \
            f"[回退路径] 蟑螂解剖链整帧与参照 {REF_COMMIT[:7]} 不一致"
        n += 1
        ref_sprites = {}
        for sid, tr in sids:
            for hd in (0.0, 37.0, 180.0):
                ref_sprites[(sid, hd)] = ref_ta.get_torso(
                    sid, 120, hd, 1.0, tr).tobytes()
            for hd in (0.0, 37.0, 180.0):
                assert torso_art.get_torso(sid, 120, hd, 1.0, tr).tobytes() == \
                    ref_sprites[(sid, hd)], \
                    f"[回退路径] {sid}@{hd}: 精灵与参照 {REF_COMMIT[:7]} 不一致"
                n += 1
        print(f"    [回退路径 NEUROPET_ROT_SS=1] {n} 组场景与参照 "
              f"{REF_COMMIT[:7]} IDENTICAL")
    finally:
        torso_art._ROT_SS_DOMAIN = False

    # ---- ① 默认路径 vs 回退路径：无重采样角度仍逐字节相同 ----
    for sid, tr in sids:
        for hd in (0.0, 90.0, 180.0, 270.0):
            torso_art.invalidate()
            torso_art._ROT_SS_DOMAIN = True
            a = torso_art.get_torso(sid, 120, hd, 1.0, tr).tobytes()
            torso_art.invalidate()
            torso_art._ROT_SS_DOMAIN = False
            b = torso_art.get_torso(sid, 120, hd, 1.0, tr).tobytes()
            assert a == b, f"{sid}@{hd}°: 无重采样角度应逐字节相同(1x 域化不等价)"
    print("    ① 无重采样角度 0/90/180/270°：1x 域化 ≡ SS 域路径(逐字节)")

    # ---- ③ 默认路径冷/热缓存自洽 ----
    torso_art.invalidate()
    hot = render_pose(pose_r, tr_r).tobytes()
    assert render_pose(pose_r, tr_r).tobytes() == hot, "同路径两次渲染不一致"
    print("    ③ 默认路径冷/热缓存两次渲染 IDENTICAL")


def test_off_appearance_vs_baseline() -> None:
    """判据 1b（12 场景 vs **冻结历史基线**；历史对照，非权威口径）。

    历史基线 = `tests/refs/c1_baseline/`（C1 落地**之前**冻结的参考渲染，
    生成/复算脚本 `tools/c1_ref_render.py`；口径见其模块 docstring）。
    注：`tests/refs/c1_baseline/manifest.json` 是**冻结产物**，
    `manifest.thresholds` 记录的是冻结当时（主控裁决之前）的旧值（如
    edge_width_ratio 1.02），仅供历史对照、不得据此判绿；**权威阈值以
    `tools/c1_ref_render.py` 的常量（及本测试内引用）为准**。

    ---- 原判据 → 新判据 → 为什么（本项目对判据变更的硬性纪律）----
    原判据：① alpha mean|Δ| ≤ 1.5/255；② >16 差异像素占比 ≤ 3.5%；
            ③「边缘过渡带宽度**不减**（锐度不降）」。
    新判据（r25 主控裁决 2，详见架构 §12）：① ② **不动**；③ 标定
            `MAX_EDGE_WIDTH_RATIO`；④ **新增** 轮廓长比 ∈[0.98,1.02]；
            ⑤ **新增** α 量比 ∈[0.96,1.005]；⑥ off 必须与 chibi 态不同。
    为什么：原 ③ 在架构里只定性、无任何数值容差（「1.0×」是愿望不是标准）。
            主控独立视觉终判（直接看 before_after_sprites/edges.png）：
            精灵层肉眼不可辨，diff 除轮廓抗锯齿带外全黑。故按纪律改口径 +
            新增更严判据补偿（宽度是 band/contour 的比，对整体缩放不敏感）。
    r25 C1 **收口**（本函数随之改读法，见判据 1c/1d）：12 场景是**抽样集**，
            恰好是最优的一档（30° 倍数；黑盒实测 fly_off 全圈 α均 max 1.776/255
            而 12 样本池均仅 1.113/255）→ 本函数降为**历史对照**（仍按
            **逐场景**读，不再池均：max 而非 mean），权威口径移到
            `test_off_appearance_fullcircle`（全 360 桶逐桶）；
            ④ 轮廓长比 → **α 掩膜 IoU ≥0.96**（长比对平移不敏感，黑盒 1px 平移
            负对照下五条判据里只有 ①② 咬红；IoU 在同负对照下蝇 0.8421/蟑 0.9566。
            长比仍作为读数列打印，但**不再是判据**）。**下界 0.98 → 0.96 = 主控
            裁决 4（架构 §15.2）**：0.98 在蝇上不可达，原判据有量纲问题 —— 原/新/
            为什么见 `test_iou_floor_fullcircle` 的 docstring。
    """
    ref = _ref_tool()
    manifest = json.loads(
        (ROOT / "tests" / "refs" / "c1_baseline" / "manifest.json")
        .read_text(encoding="utf-8"))
    scenes = ref.render_scenes()
    failed = []
    for fam, names in ref.families(manifest).items():
        rows = ref.measure(scenes, names)
        p = ref.pool(rows)
        print(f"    [基线对比] {fam:<18} mean|ΔA| 池均 "
              f"{p['alpha_mean_abs'] * 255:.3f}/max "
              f"{p['alpha_mean_abs_max'] * 255:.3f}/255"
              f"(≤{ref.MAX_ALPHA_MEAN_ABS * 255:.1f})"
              f"  >16 池均 {p['alpha_gt16_ratio'] * 100:.2f}/max "
              f"{p['alpha_gt16_ratio_max'] * 100:.2f}%"
              f"(≤{ref.MAX_ALPHA_GT16 * 100:.1f})"
              f"  边带 {p['edge_base']:.3f}→{p['edge_new']:.3f}px"
              f" 比 max {p['edge_ratio_max']:.3f}(≤{ref.MAX_EDGE_WIDTH_RATIO})"
              f"  IoU min {p['iou_min']:.4f}(≥{ref.MIN_ALPHA_IOU})"
              f"  α量比 min {p['alpha_mass_ratio_min']:.4f}"
              f"(≥{ref.MIN_ALPHA_MASS_RATIO})"
              f"  长比(读数列) {p['contour_ratio']:.4f}"
              f"  max|ΔA| {p['alpha_max_abs']}")
        checks = (
            (f"alpha mean|Δ| {p['alpha_mean_abs_max'] * 255:.3f}/255 > "
             f"{ref.MAX_ALPHA_MEAN_ABS * 255:.1f}/255(判据 #5 ② 第一条,逐场景)",
             p["alpha_mean_abs_max"] <= ref.MAX_ALPHA_MEAN_ABS),
            (f">16 差异 {p['alpha_gt16_ratio_max'] * 100:.2f}% > "
             f"{ref.MAX_ALPHA_GT16 * 100:.1f}%(判据 #5 ② 第二条,逐场景)",
             p["alpha_gt16_ratio_max"] <= ref.MAX_ALPHA_GT16),
            (f"边带比 {p['edge_ratio_max']:.3f} > {ref.MAX_EDGE_WIDTH_RATIO}"
             f"(判据 #5 ② 第三条)",
             p["edge_ratio_max"] <= ref.MAX_EDGE_WIDTH_RATIO),
            (f"α 掩膜 IoU {p['iou_min']:.4f} < {ref.MIN_ALPHA_IOU}"
             f"(判据 #5 ② 第四条:替换轮廓长比,逐场景)",
             p["iou_min"] >= ref.MIN_ALPHA_IOU),
            (f"α 量比 {p['alpha_mass_ratio_min']:.4f} 越界 "
             f"[{ref.MIN_ALPHA_MASS_RATIO},{ref.MAX_ALPHA_MASS_RATIO}](α 质量守恒)",
             ref.MIN_ALPHA_MASS_RATIO <= p["alpha_mass_ratio_min"]
             <= ref.MAX_ALPHA_MASS_RATIO),
        )
        failed += [f"{fam}: {msg}" for msg, ok_one in checks if not ok_one]
    # ③ off 必须与 chibi 态不同（保留判据）
    for sp in ("roach", "fly"):
        for hd in (0, 30, 120):
            a = scenes[f"spr_{sp}_off_h{hd:03d}"].tobytes()
            b = scenes[f"spr_{sp}_on_h{hd:03d}"].tobytes()
            assert a != b, f"{sp}@{hd}: off 应与 chibi 态不同"
    print("    ⑥ off 态与 chibi 态逐场景不同:保留")
    assert not failed, \
        ("12 场景 vs 冻结基线不达标(见本函数 docstring;权威口径见判据 1c/1d):\n  "
         + "\n  ".join(failed)
         + "\n实测见上方 [基线对比] 行与 `python tools/c1_ref_render.py verify`;"
         "撤该单元的路径 = 设 NEUROPET_ROT_SS=1(回退路径位等价仍由判据 1a 锁定)。")


_FC_CACHE: list = []          # [ref, filt, rows] 懒缓存(1c/1d 共用一次全圈测量)


def _full_circle():
    """全圈 360 桶逐桶测量(懒缓存;两族 1c/1d 共用,只跑一次 ≈20s)。"""
    if not _FC_CACHE:
        ref = _ref_tool()
        from neuropet.render import torso_art
        filt = torso_art._ROT_FILTER
        assert filt in ref.FILTERS, \
            (f"1x 域滤波器 {filt!r} 不在搜索集 {ref.FILTERS} 里"
             f"(旋钮默认值必须是 sweep 按硬性规则选定的那个)")
        _FC_CACHE[:] = [ref, filt, ref.circle_rows((filt,), progress=False)[filt]]
    return _FC_CACHE


def test_off_appearance_fullcircle() -> None:
    """判据 1c（★**权威**外观判据，r25 C1 收口）：全 360 桶**逐桶**判。

    ---- 原判据 → 新判据 → 为什么 ----
    原判据（判据 #5 ②，主控裁决 2 版）：**12 个 30° 倍数 heading 的族内池均**
        过 α mean|Δ| ≤1.5/255、>16 ≤3.5%、边带宽比 ≤1.60、轮廓长比 ∈[0.98,1.02]、
        α 量比 ∈[0.96,1.005]。
    新判据（架构 §7 #5 二次迁移 + 主控本轮 1️⃣/3️⃣/5️⃣）：**1° 量化的全部 360 个桶
        逐桶**都必须过 —— ① α mean|Δ| ≤1.5/255、② >16 ≤3.5%（**阈值一字未动**）、
        ③ α 掩膜 IoU ≥0.96（替换轮廓长比，见 1b 的为什么；下界随主控裁决 4
        由 0.98 改标 0.96，原/新/为什么见 `test_iou_floor_fullcircle`）、
        ④ α 量比 ∈[0.96,1.005]、⑤ 边带宽比 ≤`MAX_EDGE_WIDTH_RATIO`。
    为什么（1️⃣：旧口径**数学上采不到**最坏角，是抽样偏差而非噪声）：
        30° 倍数恰好是**最好的一档**（0/90/180/270 位等价 → 比 1.000），旧池均
        与全圈的实测差（黑盒 r25-test-C1「可疑判据 1」+ 本轮 sweep，同口径）：

        | 族 | 12 样本池均(旧) | 全圈 max | >1.60 桶 | α均>1.5/255 桶 | >16>3.5% 桶 |
        | --- | --- | --- | --- | --- | --- |
        | roach_off | 1.340 | 1.682@2° | 136/360 | 0 | 0 |
        | roach_on | 1.362 | 1.690@2° | 156/360 | 0 | 0 |
        | fly_off | 1.416 | **1.920@45°** | **224/360** | **324/360** | **32/360** |
        | fly_on | 1.341 | 1.683@45° | 64/360 | **176/360** | 0 |

        （上表为旧配置 BILINEAR 的实测；**新配置 BICUBIC** 见下方运行输出：
        α均 max 1.047/255、>16 max 2.12%、边带 max 1.757、α量比 min 0.9868 ——
        本判据 ①/②/④/⑤ 全部 0 桶越界。）**不是放宽，反而更严**：
        ①/② 的数值阈值未动，但读数从「12 个样本的池均」变成「360 个桶逐桶
        都必须过」；③ 用对平移敏感的 IoU 换掉对平移不敏感的轮廓长比；
        ⑤ 的上限虽由 1.60 → 1.85（主控 5️⃣ 明令按所选滤波器的**全圈分布**
        重定：观测 max 1.757 + 5%），但覆盖从 12 样本变成 360 桶，且被否决的
        BILINEAR（全圈 max 1.920）**仍然过不了这条新上限** —— 鉴别力在。
    """
    ref, filt, rows = _full_circle()
    print(f"    [全圈] 滤波器 {filt};基线 = SS 域路径(= C1 前语义,与 REF_COMMIT "
          f"{REF_COMMIT[:7]} 逐位等价);4 族 × 360 桶逐桶冷构建")
    failed = []
    for fam, rs in rows.items():
        s = ref.summarize(rs)
        print(f"    [全圈] {fam:<10} α均 max {s['alpha_mean_max'] * 255:6.3f}/255"
              f"  >16 max {s['gt16_max'] * 100:5.2f}%"
              f"  边带 max {s['edge_max']:.3f}@{s['edge_argmax']:.0f}°"
              f"  α量 min {s['mass_min']:.4f}  IoU min {s['iou_min']:.4f}")
        for m in rs:
            if m["alpha_mean_abs"] > ref.MAX_ALPHA_MEAN_ABS:
                failed.append(f"{fam}@{m['angle']:.0f}°: α均 "
                              f"{m['alpha_mean_abs'] * 255:.3f}/255 > 1.5")
            if m["alpha_gt16_ratio"] > ref.MAX_ALPHA_GT16:
                failed.append(f"{fam}@{m['angle']:.0f}°: >16 "
                              f"{m['alpha_gt16_ratio'] * 100:.2f}% > 3.5%")
            if m["edge_ratio"] > ref.MAX_EDGE_WIDTH_RATIO:
                failed.append(f"{fam}@{m['angle']:.0f}°: 边带比 "
                              f"{m['edge_ratio']:.3f} > {ref.MAX_EDGE_WIDTH_RATIO}")
            if m["mass_new"] / max(1, m["mass_base"]) < ref.MIN_ALPHA_MASS_RATIO:
                failed.append(f"{fam}@{m['angle']:.0f}°: α量比 "
                              f"{m['mass_new'] / max(1, m['mass_base']):.4f} < 0.96")
    assert not failed, \
        ("★权威外观判据(全 360 桶逐桶)不达标:\n  " + "\n  ".join(failed[:12])
         + (f"\n  …共 {len(failed)} 桶(全部明细见 "
            f"`python tools/c1_ref_render.py verify --full-circle`)"
            if len(failed) > 12 else ""))


def test_iou_floor_fullcircle() -> None:
    """判据 1d：α 掩膜 IoU ≥0.96（逐桶；替换轮廓长比的**鉴别力**判据）。

    ---- 原判据 → 新判据 → 为什么（本项目对判据变更的硬性纪律）----
    原判据（判据 1d 首版，主控本轮 3️⃣，架构 §12）：α 掩膜 IoU ≥ **0.98**，全 360 桶逐桶。
    新判据（**主控裁决 4 = 架构 §15.2，选项 A**）：α 掩膜 IoU ≥ **0.96**，口径不变（逐桶）。
      本测试读 `ref.MIN_ALPHA_IOU`（真源在 `tools/c1_ref_render.py`，**单一常量**，
      两文件不各写一份）；①/②/④/⑤ 与边带比上限 1.85 **一字未动**。
    为什么（**不是放宽，是原判据本身有量纲问题**）：
      1. **IoU 是尺度相关的** —— 蝇精灵 50×50，周长/面积比远大于蟑 124×124，
         同样的 ~0.5px 边界差在蝇身上扣的 IoU 多得多。实测（BICUBIC）：
         roach_off 0.9939 / roach_on 0.9942（**两族全过**）；
         **fly_off min 0.9721@12°：88/360 桶 <0.98**、**fly_on min 0.9764@30°：
         16/360 桶**。同一配置下「蟑全过、蝇大面积不过」正是尺度效应，不是蝇的缺陷。
      2. **几何核对排除真缺陷**：最坏桶 α≥128 掩膜**面积比 0.9974**、**质心差
         0.079px**（BILINEAR 同角 0.9871/0.08px）—— 差异只在 ~0.5px 的抗锯齿/
         边界归属上，不是位移或形变（1a 的无重采样角位等价亦佐证）。
      3. **负向对照（鉴别力仍在）**：1px 平移 → IoU(α≥128) 蝇 **0.8421** / 蟑
         0.9566（对平移强敏感 ✓，这正是 3️⃣ 要的鉴别力）；高斯模糊 r=1.2 → 蝇
         0.9744（对模糊弱敏感，但**模糊已由 ①② 咬死**：该负对照 α均 7.80/255、
         >16 14.32%，远超 1.5/255 与 3.5%）。
      4. **0.96 落在空当里**：「可接受的亚像素噪声」（实测 ≥0.972）与「真实几何
         偏移」（≤0.842）之间；它仍**高于**「蟑 1px 平移」的 0.9566，鉴别力不丢。
    被否决的选项 (B)「苍蝇回 SS 域」（IoU 恒 1.0）：**桶字节不变**（SS 路径同样
      `rotate(SS)→reduce` 回 1x：蝇 9.8KB/桶、蟑 60.1KB），仅 +1.09ms/冷桶 CPU
      （蝇 0.455→1.543ms）；主控原文的「桶体积 ×9」不成立于本实现（×9 的是 88KB 的
      **瞬态**旋转画布），故 (B) 换来的只是主控这条**校准错了的判据**、代价是纯增 CPU。
    """
    ref, filt, rows = _full_circle()
    failed = []
    for fam, rs in rows.items():
        bad = [m for m in rs if m["alpha_iou"] < ref.MIN_ALPHA_IOU]
        lo = min(rs, key=lambda m: m["alpha_iou"])
        print(f"    [IoU] {fam:<10} min {lo['alpha_iou']:.4f}@{lo['angle']:.0f}°"
              f"(阈值 {ref.MIN_ALPHA_IOU})  低于阈值 {len(bad)}/{len(rs)} 桶")
        failed += [f"{fam}@{m['angle']:.0f}° IoU {m['alpha_iou']:.4f}"
                   for m in bad]
    assert not failed, \
        (f"α 掩膜 IoU < {ref.MIN_ALPHA_IOU}(判据 1d,{filt},全 360 桶逐桶,"
         f"下界 = 主控裁决 4/架构 §15.2):共 {len(failed)} 桶越界 —— "
         f"见本函数 docstring 的 原判据 → 新判据 → 为什么:\n  "
         + "\n  ".join(failed[:12]))


def test_roach_head_band() -> None:
    """判据 2:on 蟑螂头放大带(前伸长度 ×≥3;前伸域宽 ×∈[1.15,1.7])。"""
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    half, c = 120, 120
    off = torso_art.get_torso("species.cockroach", half, 0.0, 1.0, tr)
    on = torso_art.get_torso("species.cockroach", half, 0.0, 1.0, dict(tr, chibi=True))
    pro_front = c + 57                       # 背板前缘(头前伸域起点)
    fr_off, fr_on = content_front(off), content_front(on)
    len_ratio = (fr_on - pro_front) / max(1, fr_off - pro_front)
    w_off = max_span_in(off, pro_front + 2, fr_off)
    w_on = max_span_in(on, pro_front + 2, fr_on)
    w_ratio = w_on / max(1, w_off)
    bw = body_max_width(off)
    print(f"    蟑头前伸 {fr_off - pro_front}→{fr_on - pro_front}px(×{len_ratio:.2f})"
          f" 域宽 ×{w_ratio:.2f} 头宽/体宽 off 1:{bw / max(1, w_off):.1f}"
          f" on 1:{bw / max(1, w_on):.1f}")
    assert len_ratio >= 3.0, f"头前伸长度 ×{len_ratio:.2f} < 3.0"
    assert 1.15 <= w_ratio <= 1.7, f"头前伸域宽 ×{w_ratio:.2f} 不在 [1.15,1.7]"
    assert fr_on > fr_off, "on 头前缘应超出 off(大头前伸)"


def test_fly_head_band() -> None:
    """判据 2b:on 蝇头 ×1.6 带(复眼行头高比 ∈[1.4,1.8])。"""
    from neuropet.render import torso_art
    from neuropet.species.fruitfly import FruitFly
    tr = FruitFly().render_traits()
    off = torso_art.get_torso("species.fruitfly", 120, 0.0, 1.0, tr)
    on = torso_art.get_torso("species.fruitfly", 120, 0.0, 1.0, dict(tr, chibi=True))
    h_off, h_on = col_span(off, 134), col_span(on, 134)
    r = h_on / max(1, h_off)
    print(f"    蝇头高 {h_off}→{h_on}px(×{r:.2f})")
    assert 1.4 <= r <= 1.8, f"蝇头 ×{r:.2f} 不在 [1.4,1.8]"


def test_leg_and_antenna_ratios() -> None:
    """判据 3+4:腿节段 ×0.60(支撑爪尖保位/摆动整链)、触须 9→7 ×0.45。"""
    from neuropet.render.renderer import (chibi_leg_points, chibi_antenna,
                                          CHIBI_LEG_LEN, CHIBI_ANT_LEN,
                                          CHIBI_ANT_NODES)
    chain = [(0.0, 0.0), (6.0, 2.0), (18.0, 10.0), (28.0, 22.0),
             (34.0, 30.0), (38.0, 36.0), (42.0, 40.0)]
    stc = chibi_leg_points(chain, swing=False)
    for j in range(1, len(chain) - 1):
        dx = stc[j][0] - stc[0][0]
        dy = stc[j][1] - stc[0][1]
        ox = chain[j][0] - chain[0][0]
        oy = chain[j][1] - chain[0][1]
        assert abs(dx - ox * CHIBI_LEG_LEN) < 1e-9 and \
            abs(dy - oy * CHIBI_LEG_LEN) < 1e-9, f"支撑关节 {j} 未按 ×0.60 压缩"
    assert stc[-1] == chain[-1], "支撑相爪尖必须保位(钉足不滑)"
    swc = chibi_leg_points(chain, swing=True)
    r = arclen([swc[0], swc[-1]]) / arclen([chain[0], chain[-1]])
    assert abs(r - CHIBI_LEG_LEN) < 1e-6, f"摆动整链 ×{r:.3f} ≠ 0.60"
    ant = [(52.0, 6.0)] + [(52.0 + 15.0 * j * math.cos(0.35),
                            6.0 + 7.0 * j * math.sin(0.35)) for j in range(1, 9)]
    antc = chibi_antenna(ant)
    assert len(ant) == 9 and len(antc) == CHIBI_ANT_NODES, "触须节点应 9→7"
    ar = arclen(antc) / arclen(ant)
    assert abs(ar - CHIBI_ANT_LEN) < 1e-6, f"触须弧长 ×{ar:.3f} ≠ 0.45"
    print(f"    腿:支撑 ×0.600 精确+爪尖保位;摆动( stride )×{r:.3f};"
          f"触须 9→7 ×{ar:.3f}")


def test_hue_phase_cycle() -> None:
    """判据 5:hue 相位循环 0,1,2,0;相位桶两两不同;phase0≡基线;保留键不转。"""
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    span = torso_art.IRIDES_PERIOD_S / torso_art.IRIDES_PHASES
    seq = [torso_art.hue_phase_now(t) for t in
           (0.0, span + 0.1, 2 * span + 0.1, torso_art.IRIDES_PERIOD_S + 0.1)]
    assert seq == [0, 1, 2, 0], f"相位循环 {seq} ≠ [0,1,2,0]"
    imgs = [torso_art.get_torso("species.cockroach", 120, 0.0, 1.0,
                                dict(tr, hue_phase=p)) for p in range(3)]
    base = torso_art.get_torso("species.cockroach", 120, 0.0, 1.0, tr)
    assert imgs[0].tobytes() == base.tobytes(), "phase0(零旋转)应与基线逐位一致"
    assert imgs[0].tobytes() != imgs[1].tobytes() \
        and imgs[1].tobytes() != imgs[2].tobytes() \
        and imgs[0].tobytes() != imgs[2].tobytes(), "三相位精灵应两两不同"
    # 保留键(眼/高光)不参与 hue 旋转;其余键被旋转
    pal0 = torso_art._pal_for("roach", dict(tr, hue_phase=0))
    pal1 = torso_art._pal_for("roach", dict(tr, hue_phase=1))
    for k in torso_art._HUE_KEEP:
        if k not in pal0:
            continue                    # 该物种色板无此键(如蟑螂无 torso_eye)
        assert torso_art._rgb(pal0[k]) == torso_art._rgb(pal1[k]), \
            f"保留键 {k} 被 hue 旋转(防死鱼眼约束)"
    moved = sum(1 for k in pal0 if k not in torso_art._HUE_KEEP
                and k != "_chibi"
                and torso_art._rgb(pal0[k]) != torso_art._rgb(pal1[k]))
    assert moved >= 8, f"仅 {moved} 个色板键发生 hue 旋转,应 ≥8"
    print(f"    hue 循环 {seq};相位两两不同;保留键 {torso_art._HUE_KEEP};"
          f"旋转键 {moved} 个")


def test_traits_passthrough() -> None:
    """判据 6:chibi/hue_phase 经 base_traits/_thash 透传进桶键;独立 master。"""
    from neuropet.render import torso, torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    scaled = dict(tr, scale=1.5, body_len_base=115.0, body_len=172.5,
                  chibi=True, hue_phase=2)
    base = torso.base_traits(scaled)
    assert base.get("chibi") is True and base.get("hue_phase") == 2, \
        "base_traits 必须保留 chibi/hue_phase(L1 桶键透传)"
    assert "scale" not in base and "body_len_base" not in base
    off_traits = dict(tr, scale=1.0, body_len_base=115.0, body_len=115.0)
    assert "chibi" not in torso.base_traits(off_traits), \
        "关闭态 traits 不得出现 chibi 键(基线逐键一致)"
    h_off = torso_art._thash(torso.base_traits(off_traits))
    h_on = torso_art._thash(base)
    assert h_off != h_on, "chibi on/off 必须落在不同 master 桶(互不污染)"
    assert h_off == torso_art._thash(torso.base_traits(
        dict(off_traits, scale=2.0, body_len=230.0))), \
        "五档共享同一份 L1 桶(键与 k 无关)不得被破坏"
    # 物种基线 traits 不含新键(关闭态零注入的源头)
    assert "chibi" not in tr and "hue_phase" not in tr
    # get_torso 双桶独立可用(on 精灵可取且非空)
    img = torso_art.get_torso("species.cockroach", 120, 0.0, 1.0, base)
    assert img.size == (240, 240) and \
        img.getchannel("A").getextrema()[1] > 0, "chibi master 精灵应可渲染"
    print(f"    traits 透传:base 保留 chibi/hue_phase;桶键 off={h_off} on={h_on}")


def test_invalidate_species() -> None:
    """判据 8(r25 修复**既存缺陷**,非 C1 引入):`invalidate` 必须**真清**。

    原缺陷:`torso_art.invalidate` 只声明 `global _angle_step`,缺 `_rot_bytes`
    → ① 全清分支的 `_rot_bytes = {...}` 落成**局部名赋值**,全局字节计数永不归零
    (静默);② 物种分支的 `_rot_bytes[sid] = ...` 读局部名 → **必抛**
    `UnboundLocalError`,被 `torso.py:245` 的 `except: pass` 吞成**半清**
    (master/桶已删,计数与 `_angle_step` 未复位)。C1 新增的 `variants_1x`
    (1x 母图)是 master 状态字典的成员,随 `_masters` 条目一起丢弃。

    判据(**不是「不抛就算过」**):清后 ① 该物种在 `_masters`/`_rot` 无条目、
    `_rot_bytes[sid] == 0`;② 重建与首次构建**逐位**等价;③ 重建后的
    `variants_1x` 是**新对象**且仍有内容 —— 证明母图缓存真被丢弃而非续用。
    """
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    sid = "species.cockroach"
    tr = AmericanCockroach().render_traits()
    torso_art.invalidate()
    key = ("roach", torso_art._thash(tr))
    first = torso_art.get_torso(sid, 120, 0.0, 1.0, tr)
    torso_art.get_torso(sid, 120, 37.0, 1.0, tr)
    v1x_before = torso_art._masters[key].get("variants_1x")
    assert torso_art._rot_bytes["roach"] > 0 and v1x_before, \
        "前置条件:roach 应已有桶与 1x 母图缓存"
    torso_art.invalidate(sid)                    # 缺陷点:原为必抛 + 半清
    assert not [k for k in torso_art._masters if k[0] == "roach"], \
        "invalidate(sid) 后该物种 master 未清"
    assert not [k for k in torso_art._rot if k[0] == "roach"], \
        "invalidate(sid) 后该物种旋转桶未清"
    assert torso_art._rot_bytes["roach"] == 0, \
        "invalidate(sid) 后字节计数未归零(原缺陷:UnboundLocalError 半清)"
    again = torso_art.get_torso(sid, 120, 0.0, 1.0, tr)
    assert again.tobytes() == first.tobytes(), "清后重建与首次构建不位等价"
    v1x_after = torso_art._masters[key].get("variants_1x")
    assert v1x_after and "closed" in v1x_after and v1x_after is not v1x_before, \
        "variants_1x 未随 master 丢弃(续用了旧 1x 母图缓存)"
    torso_art.invalidate()                       # 全清分支(原缺陷在此是静默的)
    assert torso_art._rot_bytes == {"roach": 0, "fly": 0}, \
        "invalidate() 全清后字节计数未复位(原缺陷:局部名赋值)"
    assert torso_art._angle_step == 1 and not torso_art._masters \
        and not torso_art._rot, "invalidate() 全清后角量化/master/桶未复位"
    print("    invalidate:物种清/全清均无异常,计数归零、重建位等价、"
          "1x 母图缓存换新对象")


def test_bucket_build_perf_guard() -> None:
    """判据 9:旋转桶构建的**回归护栏**(⚠ **这是回归护栏,不是性能目标**)。

    性能目标(架构 §15.3,**随所选滤波器修订**):原 median ≤1.5ms / p95 ≤2.5ms
    的前提是 **BILINEAR**,而 BILINEAR 已被外观判据**否决**(fly_off α 324/360、
    >16 32/360 桶越界)→ 新目标 **median ≤3.0ms / p95 ≤4.5ms**(BICUBIC 实测
    median 2.696 / p95 3.810ms,含 get_torso 包装冷桶;SS 域旧路径 11.749 /
    15.588ms)。这是**随档位修订**,不是「达不到就改目标」。

    护栏阈值 `ROT_BUILD_MEDIAN_GUARD_MS = 6.0`(原 4.0):BICUBIC 实测中位数
    2.96–3.39ms,4.0 只剩 15–26% 余量 → 会抖成**假红**;6.0 仍能拦住「退化回
    SS 路径」(11.749ms)。本机计时噪声 ±5ms,目标值当阈值用必抖(同族的估计量
    校准见 test_budget 判据 7:判据必须选**散布 << 阈值**的估计量)。同轮打印的
    p95/max 仅供参照,不作断言。

    r25 账本行 22(**原判据 → 新判据 → 为什么**,阈值 6.0 未动):
    - 原判据:单遍 60 个冷桶的 `median ≤6.0ms`。
    - 新判据:**每个角取 3 遍冷建里最快的一次,再对全圈 60 个角取中位 ≤6.0ms**。
    - 为什么:同一棵未改动的树、同一分钟内,单遍 median 实测漂 2.26~4.10ms
      (极差/中位 45%)——它把「被别人的进程抢走的 CPU」算进了「构造成本」;
      新统计量实测 2.11~2.16ms(2%)。满载(CPU 100%)下 4.21 vs 3.75ms。
      覆盖与阈值都不变:仍是对**全圈**取中位(拦影响 ≥半数角的系统性退化),
      6.0 仍拦得住 SS 域旧路径(该路径 median/min 都 ~11.7ms 起)。
      ⚠ 角内仍必须是**冷**桶:每遍前 `invalidate()`(与判据自身起手一致),
      不能改成「同角连测 3 次」——那第 2、3 次是缓存命中,测的不是构建。
    """
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    sid = "species.cockroach"
    # r25 账本行 22(口径改正,**阈值未动**):每个角取 3 遍里最快的一次,再对全圈取中位。
    # 原判据 = 单遍 60 个冷桶的 median ≤6.0。实测(本机非独占,13 个他人 python):
    #   单遍 median 在同一棵树、同一分钟内漂到 2.26~4.10ms(极差/中位 45%);
    #   换成「每角 min-of-3 → 全圈中位」后 2.11~2.16ms(极差/中位 2%);
    #   满载(8 个忙循环,CPU 100%)下:前者 3.71~4.21ms,后者 3.57~3.75ms。
    # 即:median 统计量把「被别人抢走的 CPU」算进了成本,min 统计量测的是本征成本。
    # 覆盖不变:仍对**全圈** 60 个角取中位(能拦影响 ≥半数角的系统性退化);
    # 阈值不变:6.0ms 仍拦得住「退化回 SS 域旧路径」(该路径 median/min 都 ~11.7ms 起)。
    # 代价:3 遍(每遍 invalidate + 预热烘焙)≈ +0.15s。
    passes = []
    for _ in range(3):
        torso_art.invalidate()
        torso_art.get_torso(sid, 120, 0.0, 1.0, tr)      # 预热:母图烘焙不计入
        row = []
        for i in range(1, 61):                           # 60 个不同角度 = 冷桶
            t0 = time.perf_counter()
            torso_art.get_torso(sid, 120, float(i), 1.0, tr)
            row.append((time.perf_counter() - t0) * 1000.0)
        passes.append(row)                               # 角内仍是**冷**桶:遍间已 invalidate
    xs = sorted(min(passes[k][i] for k in range(3)) for i in range(60))
    med, p95 = xs[len(xs) // 2], xs[int(len(xs) * 0.95)]
    assert med <= ROT_BUILD_MEDIAN_GUARD_MS, \
        (f"旋转桶冷构建(每角 min-of-3)全圈中位 {med:.2f}ms > 回归护栏 "
         f"{ROT_BUILD_MEDIAN_GUARD_MS}ms(疑退化回 SS 域旧路径 ~11.7ms;"
         f"性能目标 median ≤3.0ms 见架构 §15.3 —— 护栏不是目标达标线)")
    print(f"    [rot-build] roach 冷桶 n={len(xs)}×3 遍取每角最快:中位 {med:.2f}ms"
          f"(护栏 ≤{ROT_BUILD_MEDIAN_GUARD_MS},目标 ≤3.0)"
          f" p95 {p95:.2f}ms(目标 ≤4.5) max {xs[-1]:.2f}ms")


def test_budget() -> None:
    """判据 7:chibi 冷烘焙 ≤400ms/物种;chibi 整帧过 P50/P95 门槛(与
    test_render_budget 同口径:render_pose=OPT-7 包装);全合成增量(on−off)
    **配对中位** ≤1ms —— chibi 绘制层近零成本守卫。

    ⚠ 本判据是**判据,不是软判据**(r25 裁决 12④):它 `assert` 且会让 rc≠0,
    旧 docstring 自称「软判据」与事实矛盾(工作流 §6「同一份状态不许两个说法」),
    措辞已删。

    ---- 全合成增量的 原判据 → 新判据 → 为什么(阈值 1.0ms 一字未动)----
    - 原判据 v1:`sorted(n)[10] − sorted(o)[10]`(两臂**各排各的** 10 分位之差)。
      实测噪声带 ±1.9ms > 阈值 1.0ms ⇒ 闲时 1/8 轮假红(+2.44ms)。
    - 原判据 v2(账本行 22 首改):`min(n) − min(o)`(两臂各自最快样本之差),
      自称「配对统计量」。**它没有真的用上配对** —— 取的是两臂**各自的全局
      最小**,o/n 的相邻性被丢掉。实测 6 次 standalone 0.06~0.86ms(散布 ±80%,
      对 1.0ms 只剩 14% 余量)、**套件上下文 1.29ms 越阈**(架构 §30.1/30.2:
      判决由抽样运气决定 = 掷硬币)。
    - 新判据:v3 `sorted(n[i] − o[i] for i in range(20))[10] <= 1.0`(**配对中位**),
      阈值 1.0ms 未动。
    - 为什么:代码本来就是 `o[i]` 紧接 `n[i]` 的**交替**采样 —— 相邻两测共享
      同一瞬间的机器状态(负载/频率/缓存),机器漂移在**逐对差值**里抵掉;
      再对 20 对取中位 ⇒ 抗尾样本。v2 只把「全局最小」相减,等于在**两段不同
      时间**采的极值相减,配对信息全丢(架构 §30.3)。实测(本轮 15 次 standalone
      直采 + 15 次整文件重跑 + 3 次套件,见 docs/handoff/r25-chibi-delta-r1.md):
      配对中位安静段 0.03~0.79ms;整文件重跑里**负载期出现过 1 次越阈(1.58ms,
      同轮 min-min=−0.01)**,3 次套件全绿(但余量 21~36%,非裁决预期的 ≥43%——
      残留与盲区见 r1 报告 §5,含「偶发成本看不见」)。
    - 什么情况下会**红**:chibi 层真做逐帧工 —— 该成本出现在 n 臂的**每个**
      样本里(含中位那一对),逐对差值整体抬升 ⇒ 中位越阈。负向对照(在
      renderer 的 chibi 分支注入 8000 次定点迭代)实测红,见 r1 报告 §3。
    - 什么情况下会**绿**:两臂逐帧本征成本之差的中位 ≤1.0ms(闲时实测 0.03~0.79ms)。
    - ⚠ 已知盲区(登记不蒸发):配对中位守的是「**稳定的**逐帧成本」。若 chibi 层
      的成本**只偶发出现**(如每 N 帧一次的重活)而不足以抬高中位,本判据**看不见
      它** —— 这类劣化需另有判据(当前**无覆盖**,r1 报告 §5 明列)。
    """
    from neuropet.render import torso_art
    from neuropet.render.renderer import render_pose, _render_pose_full
    from neuropet.species.cockroach import AmericanCockroach
    from neuropet.species.fruitfly import FruitFly
    for sid, tr, half in (("species.cockroach",
                           AmericanCockroach().render_traits(), 120),
                          ("species.fruitfly",
                           FruitFly().render_traits(), 78)):
        # 唯一标记键 → 唯一 _thash → 强制冷 master(未被 _pal_for 消费,像素同)
        marker = dict(tr, chibi=True, budget_probe=time.perf_counter())
        t0 = time.perf_counter()
        img = torso_art.get_torso(sid, half, 0.0, 1.0, marker)
        ms = (time.perf_counter() - t0) * 1000.0
        assert img.size == (half * 2, half * 2) and \
            img.getchannel("A").getextrema()[1] > 0
        assert ms <= BAKE_BUDGET_MS, \
            f"{sid} chibi 冷烘焙 {ms:.1f}ms > {BAKE_BUDGET_MS:.0f}ms"
        print(f"    [bake] {sid} chibi 冷烘焙 {ms:.1f}ms(预算 {BAKE_BUDGET_MS:.0f})")
    caps = {"roach": (7.0, 9.0), "fly": (3.0, 4.0)}
    for tag, pose, tr, cap in (
            ("roach", build_roach_pose(),
             AmericanCockroach().render_traits(), caps["roach"]),
            ("fly", build_fly_poses()[0][1],
             FruitFly().render_traits(), caps["fly"])):
        tr_on = dict(tr, chibi=True)
        render_pose(pose, tr_on)                         # 预热(OPT-7 签名)
        xs = []
        for _ in range(40):
            t0 = time.perf_counter()
            img = render_pose(pose, tr_on)
            xs.append((time.perf_counter() - t0) * 1000.0)
        assert img.getchannel("A").getextrema()[1] > 0
        xs.sort()
        p50, p95 = xs[len(xs) // 2], xs[int(len(xs) * 0.95)]
        assert p50 <= cap[0] and p95 <= cap[1], \
            f"{tag} chibi 整帧 P50={p50:.2f}/P95={p95:.2f} 超 {cap}"
        # 判据:全合成路径 chibi 增量 ≤1ms(绘制层系数近零成本)。
        _render_pose_full(pose, tr)
        _render_pose_full(pose, tr_on)
        o = []
        n = []
        for _ in range(20):
            t0 = time.perf_counter()
            _render_pose_full(pose, tr)
            o.append((time.perf_counter() - t0) * 1000.0)
            t0 = time.perf_counter()
            _render_pose_full(pose, tr_on)
            n.append((time.perf_counter() - t0) * 1000.0)
        # r25 裁决 12(**原判据 → 新判据 → 为什么**,阈值 1.0ms 未动):
        #   原判据 v1 = sorted(n)[10] - sorted(o)[10]:两臂各排各的,噪声带 ±1.9ms
        #     > 阈值 ⇒ 闲时 1/8 假红(+2.44ms)。
        #   原判据 v2 = min(n) - min(o):**没真用上配对** —— 取两臂各自的全局最小,
        #     o/n 的相邻性被丢掉;6 次 standalone 0.06~0.86ms(±80%)、套件里 1.29ms
        #     越阈(架构 §30.2/30.3)。
        #   新判据 v3 = 逐对差值的中位。o[i] 紧接 n[i] 采样,相邻两测共享同一瞬间
        #     的机器状态(负载/频率/缓存)⇒ 漂移在**逐对差值**里抵掉;再取 20 对的
        #     中位 ⇒ 抗尾样本。本轮实测(15 次直采 + 15 次整文件 + 3 次套件):安静段
        #     0.03~0.79ms,负载期 1 次越阈(1.58)—— 残留/盲区见
        #     docs/handoff/r25-chibi-delta-r1.md §5。
        #   chibi 层若真做逐帧工 ⇒ 成本在 n 臂**每个**样本里(含中位那一对)⇒ 仍会红
        #     (负向对照实测红)。
        diffs = sorted(n[i] - o[i] for i in range(20))
        delta = diffs[10]
        delta_minmin = min(n) - min(o)          # 参照读数列(v2 估计量;不作断言)
        _BUDGET_READINGS[tag] = (delta, delta_minmin)
        assert delta <= 1.0, \
            (f"chibi 全合成增量 {delta:.2f}ms > 1.0ms(配对中位,绘制层应近零成本;"
             f"参照 min(n)-min(o)={delta_minmin:+.2f})")
        print(f"    [perf] {tag} chibi: OPT-7 P50={p50:.2f}/P95={p95:.2f}ms "
              f"(门槛 ≤{cap[0]}/{cap[1]});全合成增量(配对中位) {delta:+.2f}ms"
              f"(判据 ≤1.0;p10 {diffs[2]:+.2f} p90 {diffs[17]:+.2f}"
              f" 对照 min(n)-min(o) {delta_minmin:+.2f})")


def main() -> int:
    tests = [test_off_bitident, test_off_appearance_vs_baseline,
             test_off_appearance_fullcircle, test_iou_floor_fullcircle,
             test_roach_head_band, test_fly_head_band,
             test_leg_and_antenna_ratios, test_hue_phase_cycle,
             test_traits_passthrough, test_invalidate_species,
             test_bucket_build_perf_guard, test_budget]
    failed = []
    for t in tests:
        try:
            t()
            print(f"[ok] {t.__name__}")
        except AssertionError as exc:
            print(f"[FAIL] {t.__name__}: {exc}")
            failed.append(t.__name__)
        except Exception as exc:                     # noqa: BLE001
            print(f"[ERROR] {t.__name__}: {exc!r}")
            failed.append(t.__name__)
    verdict = f"chibi/虹色渲染验收:{len(tests) - len(failed)}/{len(tests)} 通过"
    if _BUDGET_READINGS:                    # 读数外露(见 _BUDGET_READINGS 注释)
        verdict += "  [全合成增量 配对中位(判据≤1.0)/对照 min(n)-min(o)] " + "  ".join(
            f"{t} {med:+.2f}/{mm:+.2f}" for t, (med, mm) in _BUDGET_READINGS.items())
    print(verdict)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
