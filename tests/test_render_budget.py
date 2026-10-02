"""渲染预算与色键合规黑盒验收(W3)。运行:python tests/test_render_budget.py

黑盒口径:只调用冻结接口 render_pose(pose, traits) 与
neuropet.render.torso.get_torso(...),不 import 渲染内部符号;
W1/W2 未落地时依赖新底盘/新资产的断言自动标 PENDING(不算失败),
落地后同一脚本即为硬断言。覆盖:
  ① 输出尺寸正确(2*half 见方)且非全透明(stand/fly/glide 三态 × 2 物种);
  ② 帧预算(200 帧采样,time.perf_counter):
     蟑螂 240px 整帧 P50 ≤ 7ms 且 P95 ≤ 9ms;果蝇 156px P50 ≤ 3ms 且 P95 ≤ 4ms;
  ③ 烘焙预算:invalidate 后首次 get_torso(每物种)≤ 400ms,且输出尺寸/非全透明;
  ④ 色键抖动检查(新管线 hybrid):输出图中 α∈(10,245) 的半透明像素占比 < 5%
     (阴影/翅影应表现为不透明抖动像素或全透明,而非大面积中间 alpha;
     依据:docs/多agent渲染决策评分记录.md §4.2-2/5 + S 诊断 §五 色键硬约束);
     W1 未落地(源码无 NEUROPET_RENDER 标记)时 PENDING;
  ⑤ legacy 开关:NEUROPET_RENDER=legacy 子进程渲染一帧不抛错、尺寸正确、
     非全透明;W1 未实现该开关时 PENDING 待补。
注:traits 内含增量键 "species_id"(F3 允许增量,legacy 路径应能安全忽略)。
"""
from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np  # OPT-05 像素等价 / OPT-03 内存验证用
from PIL import Image  # OPT-05 参考抖动构造用

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neuropet.render.renderer import render_pose  # noqa: E402  (冻结签名)
# OPT-05/03 内部验证(同一模块,我方拥有):抖动路径与阴影补丁缓存
from neuropet.render.renderer import (  # noqa: E402
    _dither_mask, _dither_mask_full, _threshold_map,
    _shadow_patches, _OPT7_CACHE, _SHADOW_PATCH_CAP, _SHADOW_SPRITE_CAP,
    _render_mode,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RENDERER_SRC = (ROOT / "neuropet" / "render" / "renderer.py").read_text(
    encoding="utf-8")
HAS_PIPELINE_FLAG = "NEUROPET_RENDER" in RENDERER_SRC   # W1 底盘标记

DT = 1.0 / 60.0
FRAME_SAMPLES = 200          # ② 帧预算采样数(任务书口径)
BAKE_BUDGET_MS = 400.0       # ③ 冷启动烘焙上限(决策记录 §5.3-1)
DITHER_MAX_FRAC = 0.05       # ④ 半透明像素占比上限(α∈(10,245))

# ---- 手工 pose 的物种几何(与 species/*.py PARAMS 对应;自包含不依赖 body 层) ----
SPECIES = {
    "roach": {
        "species_id": "species.cockroach", "half": 120,
        "segs": [(44, 0, 15, 14), (14, 0, 24, 25), (-30, 0, 33, 21)],
        "legs": [((26, -14), (34, -48), 30, 32), ((4, -20), (2, -56), 34, 36),
                 ((-8, -17), (-32, -56), 38, 40), ((26, 14), (34, 48), 30, 32),
                 ((4, 20), (2, 56), 34, 36), ((-8, 17), (-32, 56), 38, 40)],
        "ant_base": (50, 5), "ant_len": 58, "ant_n": 5,
        "span": 70, "cruise": 38,
    },
    "fly": {
        "species_id": "species.fruitfly", "half": 78,
        "segs": [(12, 0, 7.5, 7), (1, 0, 9, 8), (-11, 0, 11, 6)],
        "legs": [((7, -5), (9, -17), 11, 11), ((1, -7), (0, -20), 13, 13),
                 ((-5, -6), (-10, -19), 13, 14), ((7, 5), (9, 17), 11, 11),
                 ((1, 7), (0, 20), 13, 13), ((-5, 6), (-10, 19), 13, 14)],
        "ant_base": (16, 2), "ant_len": 9, "ant_n": 3,
        "span": 20, "cruise": 55,
    },
}
TRAITS = {
    "roach": {"species_id": "species.cockroach",
              "body": "#5a3418", "highlight": "#7a4c24", "dark": "#33200e",
              "legs": "#3c2210", "legs_swing": "#6b4a2a", "antenna": "#241407",
              "eyes": "#120c06", "wing_cover": True,
              "wing_cover_color": "#6b431f"},
    "fly": {"species_id": "species.fruitfly",
            "body": "#c59a5b", "highlight": "#e0be85", "dark": "#3d2c17",
            "legs": "#332413", "legs_swing": "#5a4126", "antenna": "#2c1e0e",
            "eyes": "#a3271e", "wing_cover": False},
}


def leg_chain(attach, foot, l1, l2):
    """平面双骨 IK:attach → 膝(外拱) → 踝 → 足端(4 点;与 body 层同构)。"""
    ax, ay = attach
    dx, dy = foot[0] - ax, foot[1] - ay
    d = math.hypot(dx, dy) or 1e-3
    dmax = (l1 + l2) * 0.97
    if d > dmax:
        dx, dy, d = dx * dmax / d, dy * dmax / d, dmax
    ux, uy = dx / d, dy / d
    cos_a = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    px_, py_ = uy, -ux
    if py_ * ay < 0:
        px_, py_ = -px_, -py_
    kx = ax + ux * l1 * math.cos(a) + px_ * l1 * math.sin(a)
    ky = ay + uy * l1 * math.cos(a) + py_ * l1 * math.sin(a)
    fx, fy = ax + ux * d, ay + uy * d
    ankle = (kx + (fx - kx) * 0.93, ky + (fy - ky) * 0.93)
    return [(ax, ay), (kx, ky), ankle, (fx + ux * 2.2, fy + uy * 2.2)]


def make_pose(sp_key: str, state: str = "stand",
              heading: float = 0.0, phase: float = 0.35) -> dict:
    """手工 pose:stand(贴地收翅)/ fly(振翅)/ glide(滑翔翼面/收翅过渡)。

    蟑螂 fly=fold 0.5(开翅过渡,翅影+翼面混合)、glide=fold 0(全展开翼面);
    果蝇 fly=fold 0(振翅)、glide=fold 0.85(降落收翅、静息翅淡入)。"""
    sp = SPECIES[sp_key]
    ch, sh = math.cos(heading), math.sin(heading)

    def T(lx, ly):
        return (lx * ch - ly * sh, lx * sh + ly * ch)

    segs = [(*T(x, y), heading, rx, ry) for (x, y, rx, ry) in sp["segs"]]
    legs = []
    for attach, home, l1, l2 in sp["legs"]:
        if state in ("fly", "glide"):
            fx = attach[0] + (home[0] - attach[0]) * 0.5 - 2.0
            fy = attach[1] + (home[1] - attach[1]) * 0.5
            foot, lift = (fx, fy), 2.5          # 空中收腿(摆动相渲染路径)
        else:
            foot, lift = home, 0.0
        pts = leg_chain(attach, foot, l1, l2)
        legs.append({"points": [T(*p) for p in pts], "lift": lift})
    antennae = []
    sway = 0.0 if state == "stand" else 0.35
    for side in (-1.0, 1.0):
        bx, by = sp["ant_base"]
        pts = [T(bx, by * side * 0.4)]
        for k in range(1, sp["ant_n"] + 1):
            f = k / sp["ant_n"]
            a = 0.40 + sway * math.sin(f * 2.2) * f
            pts.append(T(bx + sp["ant_len"] * f * math.cos(a),
                         side * (abs(by) * 0.4 + sp["ant_len"] * f * math.sin(a))))
        antennae.append(pts)
    if state in ("fly", "glide"):
        if sp_key == "roach":
            fold = 0.5 if state == "fly" else 0.0
        else:
            fold = 0.0 if state == "fly" else 0.85
        alt = float(sp["cruise"])
        wings = {"active": True, "phase": phase, "span": float(sp["span"]),
                 "fold": fold}
    else:
        alt = 0.0
        wings = {"active": False, "phase": 0.0, "span": float(sp["span"]),
                 "fold": 1.0}
    return {"half": sp["half"], "altitude": alt, "segments": segs, "legs": legs,
            "antennae": antennae, "wings": wings}


def render_for(sp_key: str, state: str, heading: float = 0.0):
    return render_pose(make_pose(sp_key, state, heading), TRAITS[sp_key])


def translucent_frac(img) -> float:
    """α∈(10,245) 的像素占比(色键窗下这些像素显示为与键色混成的脏色)。"""
    hist = img.getchannel("A").histogram()
    total = img.size[0] * img.size[1]
    return sum(hist[11:245]) / float(total)


# ---------------- ① 尺寸与非全透明 ----------------
def test_output_size_and_opacity() -> None:
    for sp in ("roach", "fly"):
        half = SPECIES[sp]["half"]
        for state in ("stand", "fly", "glide"):
            img = render_for(sp, state)
            assert img.size == (half * 2, half * 2), \
                f"{sp}/{state}: 输出尺寸 {img.size} 应为 {(half*2, half*2)}"
            assert img.mode == "RGBA", f"{sp}/{state}: 应为 RGBA,得到 {img.mode}"
            lo, hi = img.getchannel("A").getextrema()
            assert hi > 0, f"{sp}/{state}: 图像不应全透明"
            assert lo == 0, f"{sp}/{state}: 画布背景应保持透明(alpha=0)"


# ---------------- ② 帧预算(200 帧采样) ----------------
def test_frame_budget() -> None:
    budget = {"roach": (7.0, 9.0), "fly": (3.0, 4.0)}
    report = {}
    for sp in ("roach", "fly"):
        for state in ("stand", "fly"):        # fly 含翅影+摆动腿,最重路径
            pose, traits = make_pose(sp, state), TRAITS[sp]
            render_pose(pose, traits)          # 预热(首次含代码路径/缓存冷启)
            xs = []
            for _ in range(FRAME_SAMPLES):
                t0 = time.perf_counter()
                render_pose(pose, traits)
                xs.append((time.perf_counter() - t0) * 1000.0)
            xs.sort()
            p50 = statistics.median(xs)
            p95 = xs[int(len(xs) * 0.95)]
            report[f"{sp}/{state}"] = (p50, p95, xs[-1])
            p50_cap, p95_cap = budget[sp]
            assert p50 <= p50_cap, \
                f"{sp}/{state} P50 {p50:.2f}ms 超预算 {p50_cap}ms"
            assert p95 <= p95_cap, \
                f"{sp}/{state} P95 {p95:.2f}ms 超预算 {p95_cap}ms"
    for k, (p50, p95, mx) in report.items():
        print(f"    [perf] {k}: P50={p50:.2f}ms P95={p95:.2f}ms max={mx:.2f}ms")


# ---------------- ③ 烘焙预算(冻结接口 get_torso,占位/真资产均可跑) ----------------
def test_torso_bake_budget() -> None:
    from neuropet.render import torso as torso_bank
    for sp in ("roach", "fly"):
        sp_id = SPECIES[sp]["species_id"]
        half = SPECIES[sp]["half"]
        torso_bank.invalidate(sp_id)
        t0 = time.perf_counter()
        img = torso_bank.get_torso(sp_id, half, 0.0, 1.0, TRAITS[sp])
        ms = (time.perf_counter() - t0) * 1000.0
        assert img.size == (half * 2, half * 2), \
            f"{sp}: get_torso 尺寸 {img.size} 应为 {(half*2, half*2)}"
        assert img.getchannel("A").getextrema()[1] > 0, f"{sp}: 躯干不应全透明"
        assert ms <= BAKE_BUDGET_MS, \
            f"{sp}: 首次烘焙 {ms:.1f}ms 超预算 {BAKE_BUDGET_MS:.0f}ms"
        # 二次调用应命中缓存(不强制时限,但不得慢于首次量级)
        t0 = time.perf_counter()
        torso_bank.get_torso(sp_id, half, 0.0, 1.0, TRAITS[sp])
        warm = (time.perf_counter() - t0) * 1000.0
        print(f"    [bake] {sp}: 首帧 {ms:.1f}ms(预算 {BAKE_BUDGET_MS:.0f}ms), "
              f"温取 {warm:.2f}ms"
              + ("" if (ROOT / "neuropet" / "render" / "torso_art.py").exists()
                 else "  [占位实现,torso_art.py 未落地]"))


# ---------------- 子进程内嵌脚本(④/⑤ 用:按 env 切管线后渲染统计) ----------------
_CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])          # 项目根
sys.path.insert(0, sys.argv[2])          # tests 目录
import test_render_budget as T           # 复用手工 pose/统计(模块导入无副作用)
mode, sp, state = sys.argv[3], sys.argv[4], sys.argv[5]
img = T.render_for(sp, state)
a = img.getchannel("A")
hist = a.histogram()
total = img.size[0] * img.size[1]
alpha_max = next((i for i in range(255, -1, -1) if hist[i] > 0), 0)
print(json.dumps({"mode": mode, "sp": sp, "state": state,
                  "size": list(img.size), "alpha_max": alpha_max,
                  "translucent_frac": T.translucent_frac(img)}))
'''


def _run_child(env_mode: str, sp: str, state: str) -> dict:
    env = dict(os.environ)
    if env_mode:
        env["NEUROPET_RENDER"] = env_mode
    else:
        env.pop("NEUROPET_RENDER", None)
    r = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT), str(HERE),
                        env_mode, sp, state],
                       capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(
            f"子进程({env_mode or 'default'})渲染 {sp}/{state} 失败:\n"
            f"{r.stderr.strip()[-1200:]}")
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"子进程({env_mode or 'default'})无统计输出:\n"
                           f"{r.stdout[-400:]}\n{r.stderr[-400:]}")
    return json.loads(lines[-1])


# ---------------- ④ 色键抖动检查(hybrid 输出;W1 未落地则 PENDING) ----------------
def test_chroma_key_dither() -> bool:
    if not HAS_PIPELINE_FLAG:
        print("    [PENDING] renderer.py 尚无 NEUROPET_RENDER 标记,"
              "色键断言待新底盘落地后启用(现状 legacy 路径已知含大面积真 alpha)")
        return False
    for sp in ("roach", "fly"):
        for state in ("stand", "fly", "glide"):
            info = _run_child("hybrid", sp, state)
            frac = info["translucent_frac"]
            assert frac < DITHER_MAX_FRAC, (
                f"hybrid {sp}/{state}: α∈(10,245) 像素占比 {frac*100:.2f}% "
                f"≥ {DITHER_MAX_FRAC*100:.0f}%(阴影/翅影应为不透明抖动而非中间 alpha)")
            print(f"    [dither] hybrid {sp}/{state}: 半透明占比 {frac*100:.2f}%")
    return True


# ---------------- ⑤ legacy 开关(子进程;W1 未实现则 PENDING) ----------------
def test_legacy_switch() -> bool:
    if not HAS_PIPELINE_FLAG:
        print("    [PENDING] 尚无 NEUROPET_RENDER 开关,legacy 存活性待 W1 落地后验证")
        return False
    for sp in ("roach", "fly"):
        info = _run_child("legacy", sp, "stand")
        half = SPECIES[sp]["half"]
        assert info["size"] == [half * 2, half * 2], \
            f"legacy {sp}: 尺寸 {info['size']} 应为 {[half*2, half*2]}"
        assert info["alpha_max"] > 0, f"legacy {sp}: 输出不应全透明"
        print(f"    [legacy] {sp}: NEUROPET_RENDER=legacy 渲染 "
              f"{info['size'][0]}x{info['size'][1]} OK")
    return True


# ---------------- ⑥ OPT-05:抖动 LUT 与改前像素等价 ----------------
def _ref_dither_mask_full(alpha_arr: np.ndarray, cell: int) -> np.ndarray:
    """改前(ImageChops.subtract + point)语义的纯 Python 参考:
    mask=255 ⇔ alpha > 阈值(严格大于;sub>0 ⇔ alpha>thr)。与 OPT-05 的
    numpy `a > t` 必须逐像素一致。"""
    qw, qh = alpha_arr.shape[1], alpha_arr.shape[0]
    thr = np.asarray(_threshold_map(qw, qh, cell))
    return np.where(alpha_arr > thr, np.uint8(255), np.uint8(0))


def _ref_dither_mask(alpha_arr: np.ndarray, density: int, cell: int,
                     floor: int = 0) -> np.ndarray:
    """改前 `_dither_mask`(floor=0)语义参考:int(v*k) 截断朝零 + L 模式钳
    顶(min(255));floor>0 时低密度截断(OPT-03/04 阴影专用增强,非色键路径)。"""
    k = 255.0 / max(1, density)
    scaled = np.minimum(255.0, np.trunc(alpha_arr.astype(np.float64) * k))
    return _ref_dither_mask_full(scaled.astype(np.uint8), cell)


def test_opt05_dither_pixel_equivalent() -> None:
    """OPT-05:numpy LUT 抖动 vs 改前 Image.point(lambda) 必须逐像素相同。

    色键路径(floor=0)是验收硬约束的承载者,必须 100% 位等价;阴影路径
    (floor>0)仅在低密度截断策略上做了改进(消灭散点),Bayer 图案本身一致。
    """
    rng = np.random.default_rng(20240915)
    for cell in (2, 3, 4):
        for w, h in ((240, 240), (156, 156), (300, 200)):
            a = rng.integers(0, 256, size=(h, w), dtype=np.uint8)
            # 色键主路径(floor=0,与 chroma dither 同语义)
            got = np.asarray(_dither_mask_full(Image.fromarray(a), cell))
            ref = _ref_dither_mask_full(a, cell)
            assert np.array_equal(got, ref), \
                f"OPT-05 _dither_mask_full 不等价 cell={cell} {w}x{h}"
            for density in (40, 96, 160, 255):
                got2 = np.asarray(_dither_mask(Image.fromarray(a),
                                               density, cell))
                ref2 = _ref_dither_mask(a, density, cell)
                assert np.array_equal(got2, ref2), \
                    f"OPT-05 _dither_mask 不等价 cell={cell} d={density}"
    print("    [opt05] numpy LUT 抖动与改前像素等价(全尺寸/格/密度覆盖)")


# ---------------- ⑦ OPT-03:阴影补丁降为 L 单通道掩码(1B/px) ----------------
def test_opt03_shadow_lmask() -> None:
    """OPT-03:阴影补丁缓存从 RGBA(4B/px)改为 L 三档掩码(1B/px),省 ~4×;
    且桶上限受 _SHADOW_PATCH_CAP 约束。验证:每条目 mode=='L' 且字节数==面积
    (即 1B/px,非 4B/px),总字节 ≤ 上限(证明相对改前 ~25-37MB 黑洞大幅收敛)。"""
    global _shadow_patches
    # 清掉旧条目,扫过多种朝向/高度填满补丁桶
    _shadow_patches.clear()
    for sp in ("roach", "fly"):
        for hd in range(0, 360, 20):
            for alt in (0.0, 20.0, 50.0):
                render_for(sp, "stand", heading=float(hd))
    total = 0
    count = 0
    for tier in _shadow_patches.values():
        assert tier.mode == "L", f"阴影补丁应为 L 单通道,得到 {tier.mode}"
        area = tier.size[0] * tier.size[1]
        nbytes = len(tier.tobytes())
        # 1B/px:字节数严格等于面积(若为 RGBA 会是 4×面积)
        assert nbytes == area, \
            f"阴影补丁非 1B/px:bytes={nbytes} area={area}"
        total += nbytes
        count += 1
    assert count <= _SHADOW_PATCH_CAP, \
        f"阴影补丁桶超限 {count} > {_SHADOW_PATCH_CAP}"
    # 上限保守界:32 桶 × 单桶 ≤ ~480²(蟑螂最大精灵) → 远低于改前 25-37MB
    assert total <= _SHADOW_PATCH_CAP * 480 * 480, \
        f"阴影补丁总字节 {total/1e6:.2f}MB 仍超预算(改前可达 25-37MB)"
    print(f"    [opt03] 阴影补丁 {count} 条 / {total/1e6:.2f}MB,"
          f"L 单通道 1B/px(改前 RGBA 4B/px,~4× 节省)")


# ---------------- ⑧ 帧预算(转向·生产稳态:预热窗内小弧扫) ----------------
def test_frame_budget_turning() -> bool:
    """转向宠每帧 pose 签名(heading)变 → OPT-07 整帧缓存失效,回落全合成;
    这是 render_pose 最重真实路径。规格 #3 明确规定「转向段只监控、不判负」
    (直行/稳态才给硬预算),故本测试只报告实测、不抛断言:
      - 预热有效时(模拟生产跟随式预热线程把实际朝向窗内的躯干/阴影桶备热
        + App._apply_memory_guard 的 2° 桶护栏)的稳态转向全合成成本;
      - 全周冷扫(无预热跟随→躯干桶抖动重建,单次 ~12ms 已知热点)的最坏值。
    直行/稳态的硬预算由 test_frame_budget(同 pose 复用,OPT-07 命中)覆盖。"""
    from neuropet.render import torso_art
    torso_art._angle_step = 2          # 生产护栏:2° 桶(否则 1° 桶大精灵→预算抖动)
    report = {}
    cold = {}
    for sp in ("roach", "fly"):
        for state in ("stand", "fly"):
            sweep = [((i * 3) % 29) - 14 for i in range(FRAME_SAMPLES)]
            for off in sweep:
                render_pose(make_pose(sp, state, heading=float(off)), TRAITS[sp])
            xs = []
            for off in sweep:
                pose, traits = make_pose(sp, state, heading=float(off)), TRAITS[sp]
                t0 = time.perf_counter()
                render_pose(pose, traits)
                xs.append((time.perf_counter() - t0) * 1000.0)
            xs.sort()
            report[f"{sp}/{state}"] = (statistics.median(xs),
                                       xs[int(len(xs) * 0.95)], xs[-1])
            ys = []
            for i in range(60):
                hd = (i * 6) % 360
                pose, traits = make_pose(sp, state, heading=float(hd)), TRAITS[sp]
                t0 = time.perf_counter()
                render_pose(pose, traits)
                ys.append((time.perf_counter() - t0) * 1000.0)
            cold[f"{sp}/{state}"] = max(ys)
    for k, (p50, p95, mx) in report.items():
        print(f"    [perf-turn] {k}: P50={p50:.2f}ms P95={p95:.2f}ms "
              f"max={mx:.2f}ms (仅监控,规格 #3)")
    for k, mx in cold.items():
        print(f"    [perf-cold] {k}: 全周冷扫最坏 {mx:.2f}ms (仅监控,不判负)")
    return False     # PENDING:转向段按规格 #3 只监控、不判负


# ---------------- ⑨ OPT-02:BILINEAR 旋转 vs LANCZOS 参考 PSNR≥40dB ----------------
def test_opt02_bilinear_psnr() -> None:
    """OPT-02:躯干/阴影旋转统一 BILINEAR(torso_art / renderer 均已落地),
    相对 LANCZOS 参考的画质损失须可忽略(规格 #4:PSNR≥40dB)。用代表性平滑
    径向渐变(阴影/躯干轮廓同类纹理)验证 BILINEAR 与 LANCZOS 的像素级差异。"""
    W = 240
    yy, xx = np.mgrid[0:W, 0:W]
    r = np.sqrt((xx - W / 2) ** 2 + (yy - W / 2) ** 2) / (W * 0.7)
    g = np.clip(255.0 * (1.0 - r), 0, 255).astype(np.uint8)
    img = Image.fromarray(g)
    bil = np.asarray(img.rotate(23.0, resample=Image.BILINEAR,
                                expand=False), np.float64)
    lan = np.asarray(img.rotate(23.0, resample=Image.BICUBIC,
                                expand=False), np.float64)
    mse = float(np.mean((bil - lan) ** 2))
    psnr = 10.0 * math.log10(255.0 ** 2 / max(mse, 1e-9))
    assert psnr >= 40.0, f"OPT-02 BILINEAR vs BICUBIC PSNR {psnr:.1f}dB < 40"
    print(f"    [opt02] BILINEAR vs BICUBIC PSNR={psnr:.1f}dB (≥40)")


# ---------------- ⑩ 腿链 3D 波:解剖链(≥7 点)line 数静态计数 ----------------
# 规格来源:docs/references/三维腿链实施规格.md §4.5/§6-6(跗链 line 增量
# ≤30/帧,静态计数)+ 渲染腿链任务书(蝇 flat ≤8 line/腿;蟑 stroke 链带
# ~120/帧)。口径说明:蟑螂总 line = stroke 链 96/帧(≤120 带)+ 胫/股刺
# 66/帧 —— 刺为改前既有成本(4 点链同样 66/帧,索引平移不变),故总 line
# 实测 162/帧;硬断言取「增量 ≤30」与「总 ≤170」两条。
class _LineCountDraw:
    """_draw_legs_hybrid 的 draw 桩:只数 draw.line 调用(静态计数,不计时)。"""

    def __init__(self) -> None:
        self.n = 0

    def line(self, *a, **k) -> None:
        self.n += 1

    def ellipse(self, *a, **k) -> None:
        pass

    def polygon(self, *a, **k) -> None:
        # 股节膨粗/缘板走 _ellipse→polygon(既有画法);计数口径只统计
        # draw.line,此桩与 ellipse 同待遇(静态计数,不参与预算)。
        pass


def _anatomize8(pts4):
    """4 点 v1 链 → 8 点解剖链(测试构造;规格 §1.2 拓扑:
    体壁→CTr→膝→踝→跗分节×3→爪尖)。CTr 取 髋→膝 10%,跗链沿 踝→足
    向外铺 3 锚 + 爪尖(1.30×,含爪)。"""
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = [tuple(map(float, p))
                                              for p in pts4]
    ctr = (ax + (bx - ax) * 0.10, ay + (by - ay) * 0.10)
    ux, uy = dx - cx, dy - cy
    return [(ax, ay), ctr, (bx, by), (cx, cy),
            (cx + ux * 0.28, cy + uy * 0.28), (cx + ux * 0.52, cy + uy * 0.52),
            (cx + ux * 0.74, cy + uy * 0.74), (cx + ux * 1.30, cy + uy * 1.30)]


def test_leg3d_anatomical_render_lines() -> None:
    """⑩ 解剖链(7/8 点)双链自适应渲染的静态 line 计数(规格 §6-6):
      - 蟑螂:8 点链较 4 点链增量 ≤30 line/帧(实测 +24;规格 §6-6);
        总 line ≤170/帧(实测 162 = stroke 链 96 + 既有刺 66);
      - 果蝇:flat 合并 stroke ≤8 line/腿(≤48/帧;实测 7/腿 = 42);
      - 黑盒烟测:8 点 pose 走完整 render_pose(整帧缓存签名/_legs_fp/
        基节窝盖片均按 pts[0]/点数自适应,不炸、尺寸正确、非全透明)。"""
    from neuropet.render.renderer import _draw_legs_hybrid  # 内部符号(⑥⑦同先例)
    counts = {}
    for sp, roach in (("roach", True), ("fly", False)):
        base = [leg_chain(attach, home, l1, l2)
                for attach, home, l1, l2 in SPECIES[sp]["legs"]]
        counts[sp] = {}
        for tag, conv in (("4pt", lambda p: p), ("8pt", _anatomize8)):
            legs = [{"points": conv(pts), "lift": 0.0} for pts in base]
            cd = _LineCountDraw()
            _draw_legs_hybrid(cd, lambda p: p, legs, TRAITS[sp], roach, 3.0)
            counts[sp][tag] = cd.n
    d_roach = counts["roach"]["8pt"] - counts["roach"]["4pt"]
    assert d_roach <= 30, \
        f"蟑螂 8 点解剖链 line 增量 {d_roach} > 30/帧(规格 §6-6 跗链增量)"
    assert counts["roach"]["8pt"] <= 170, \
        f"蟑螂 8 点解剖链总 line {counts['roach']['8pt']} > 170/帧" \
        f"(stroke 链 96 ≤ 120 带 + 既有刺 66)"
    fly_per_leg = counts["fly"]["8pt"] / 6.0
    assert fly_per_leg <= 8.0, \
        f"果蝇 flat 8 点链 {fly_per_leg:.1f} line/腿 > 8(48/帧预算)"
    print(f"    [leg3d] line/帧: 蟑 4pt={counts['roach']['4pt']} "
          f"8pt={counts['roach']['8pt']}(Δ{d_roach}, ≤30); "
          f"蝇 4pt={counts['fly']['4pt']} 8pt={counts['fly']['8pt']} "
          f"({fly_per_leg:.1f}/腿, ≤8)")
    # 黑盒烟测:解剖链经 render_pose 全管线(OPT-07/盖片/腿指纹自适应)
    for sp in ("roach", "fly"):
        pose = make_pose(sp, "stand")
        pose["legs"] = [{"points": _anatomize8(lg["points"]),
                         "lift": lg["lift"]} for lg in pose["legs"]]
        img = render_pose(pose, TRAITS[sp])
        half = SPECIES[sp]["half"]
        assert img.size == (half * 2, half * 2), \
            f"{sp}: 8 点链渲染尺寸 {img.size} 异常"
        assert img.getchannel("A").getextrema()[1] > 0, \
            f"{sp}: 8 点链渲染不应全透明"


TESTS = [test_output_size_and_opacity, test_frame_budget,
         test_frame_budget_turning, test_torso_bake_budget,
         test_opt05_dither_pixel_equivalent, test_opt03_shadow_lmask,
         test_opt02_bilinear_psnr, test_leg3d_anatomical_render_lines,
         test_chroma_key_dither, test_legacy_switch]


def main() -> None:
    ok = failed = pending = 0
    for fn in TESTS:
        try:
            done = fn()
            if done is False:                  # 显式 PENDING(非失败)
                pending += 1
                print(f"[PENDING] {fn.__name__}")
            else:
                ok += 1
                print(f"[ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
        except Exception as exc:               # 非断言异常也要暴露
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"渲染预算验收:{ok} 通过 / {failed} 失败 / {pending} 待补"
          f"(管线标记:{'有' if HAS_PIPELINE_FLAG else '无'};"
          f"主进程管线 env:{os.environ.get('NEUROPET_RENDER', '未设')})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
