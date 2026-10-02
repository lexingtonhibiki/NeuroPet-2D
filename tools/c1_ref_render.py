"""r25 C1 参考渲染冻结 / 复算工具 —— 判据 #5 ② 的「历史基线」。

背景(架构 §7 判据 #5 + §10 裁决 1 可实施性补丁):C1 把旋转桶构建从
「SS 域旋转 → reduce」改为「reduce → 1x 域旋转」,与 git HEAD 的位等价
数学上必然破裂。新判据改为**度量外观**:

  ① 同路径自洽:`off` 态与**当前**基准逐字节相同
  ② 跨路径外观容差:与**本工具冻结的历史基线**比
     alpha_mean_abs ≤ 1.5/255、alpha_gt16_ratio ≤ 3.5%、锐度不降
  ③ `off` 必须与 chibi 态不同

历史基线必须在**改 C1 代码之前**冻结,否则事后不可复现。本工具即那份
可复跑的生成脚本 + 复算器。

用法(只依赖 stdlib + Pillow,**不得引入第三方运行时依赖**):
    python tools/c1_ref_render.py gen       # 冻结当前(改前)渲染输出 → tests/refs/c1_baseline/
    python tools/c1_ref_render.py verify    # 12 场景 vs 冻结基线(历史对照,见「判据」节)
    python tools/c1_ref_render.py verify --full-circle   # ★权威外观判据:全圈 360 桶逐桶
    python tools/c1_ref_render.py full-circle            # 同 --full-circle(单独入口)
    python tools/c1_ref_render.py sweep     # 滤波器旋钮:速度×质量对照表(3 滤波 × 全圈)
    python tools/c1_ref_render.py compare   # 生成「前/后对照图」PNG(入库,供视觉终判)
    python tools/c1_ref_render.py worst     # 最坏角前/后/差分三联图(4× 最近邻)→ worst_angles/

------------------------------------------------------------------ 口径(冻结)--
场景 = 1x 精灵(两物种 × off/chibi-on × 12 个 heading,closed 变体)+ 整帧
       (render_pose;蝇 5 态 + 蟑解剖链 off/on)。渲染顺序即清单顺序,不得改:
       180° 桶由对面桶 transpose 派生(命中路径的一部分),顺序会影响「谁被
       派生、谁被构建」。

**全圈**(full-circle / worst / sweep)口径:对 **1° 量化的全部 360 个桶**逐桶
复算**同一度量、同一区域**,基线取「SS 域路径」(`_ROT_SS_DOMAIN=True` = C1 前
语义;它与 REF_COMMIT(b2ca945)的逐位等价由 `tests/test_chibi_render.py` 的回退
路径判据锁定 → **无需为 360 个角冻结 PNG,也无需重冻结基线**)。1x 侧按
`_ROT_FILTER` 旋钮(默认 = 本节「滤波器选定」的 BICUBIC)渲染;滤波器不在桶键里,
故每次切换前必须 `_clear_rot_only()`(与 `_ROT_SS_DOMAIN` 同一条陷阱)。

度量区域 = 「图像自身」:精灵场景取 **1x 精灵画布**(`_rot_geo` 的裁切框,
   即架构 §1.4 微基准所用的那块;`get_torso` 的 (2*half)² 画布只是居中容器,
   把它算进去会被透明边距稀释);整帧场景取整帧画布。

alpha_mean_abs   = mean(|A_new - A_base|)/255            区域口径
alpha_gt16_ratio = #{|ΔA| > 16} / 区域像素数              区域口径
alpha_iou        = |m_new ∩ m_base| / |m_new ∪ m_base|   区域口径
                   m = {α ≥ 128} 的硬掩膜(α 掩膜 IoU)
edge_width_px    = band / contour                        过渡带宽度(px)
                   band    = #{0 < a < 255}
                   contour = #{a ≥ 128 且 4-邻域存在 a < 128}(50% 等值线长度)
contour_ratio    = Σcontour_new / Σcontour_base          族内**总量比**(长度可加)
alpha_mass_ratio = Σα_new / Σα_base                      族内**总量比**(质量可加)
    —— 长度/质量是可加量,故按族内总量比读(边带比沿用既有的逐场景比均值口径)。
    contour_ratio 仍照算(历史对照读数列),但**已不再是判据**(见下)。

---------------------------------------------------------- 判据(r25 收口修订)--
原判据(判据 #5 ②,主控裁决 2 版):12 个 30° 倍数 heading 的**族内池均**过
   α mean|Δ| ≤1.5/255、>16 ≤3.5%、边带宽比 ≤1.60 + 轮廓长比 ∈[0.98,1.02] +
   α 量比 ∈[0.96,1.005]。
为什么改(黑盒验收 r25-test-C1「可疑判据 1」,360 桶逐桶实测):**30° 倍数恰好是
   最好的一档**(0/90/180/270 位等价 → 比值 1.000),旧口径**数学上采不到**最坏角:
   fly_off 全圈最高 1.920@45°(池均 1.638 > 1.60)、α 均超 1.5/255 的桶 324/360、
   >16 超 3.5% 的桶 32/360、最坏绝对带宽 1.32→2.54px;蟑螂两族则 0 桶超标
   (问题精确地是苍蝇)。旧口径偏乐观不是「实测噪声」而是**抽样偏差**。
新判据(★权威,`verify --full-circle` / `full-circle` 判 rc):**全 360 桶逐桶**
   都必须过 —— 语义是「每一个桶都必须过」,**不是**族内池均:
     ① α mean|Δ| ≤ 1.5/255          (阈值**不动**)
     ② >16 占比  ≤ 3.5%              (阈值**不动**)
     ③ α 掩膜 IoU ≥ 0.96             (替换「轮廓长比」判据;**主控裁决 4 定的下界**,
                                      原 0.98 在蝇上不可达 → 见「IoU 下限」节)
     ④ α 量比 ∈ [0.96, 1.005]        (阈值**不动**)
     ⑤ 边带宽比 ≤ MAX_EDGE_WIDTH_RATIO(**按所选滤波器全圈分布重定**,见「滤波器选定」)
   12 场景 vs 冻结基线的 `verify`(无 flag)保留为**历史对照**并同样改判 IoU;
   两处阈值同源(本文件常量),不存在两套标准。
为什么 IoU 替换轮廓长比:轮廓长比是 50% 等值线的**长度比**,对**平移完全
   不敏感** —— 黑盒负对照(把基线平移 1px):边带比/轮廓长比/α 量比**全绿**,只有
   ①② 咬红。即它不约束「形状没被改坏」,命名还误导读者以为位置被锁住。
   α≥128 掩膜 IoU 对几何偏移敏感(同一负对照下咬红),才是有鉴别力的替换
   (这是**换判据**而非放宽:同族数据下 IoU 比长比严格更强)。

------------------------------------------------------------ 滤波器选定(P0) ----
1x 域的 resample 是旋钮(`NEUROPET_ROT_FILTER` / 模块全局 `_ROT_FILTER`):
BILINEAR(便宜)→ BICUBIC(锐)。**LANCZOS 在这一层不存在** —— PIL 的 rotate→transform
只接受 NEAREST/BILINEAR/BICUBIC,传 LANCZOS 实测抛
`ValueError: Image.Resampling.LANCZOS (1) cannot be used`(Pillow 11.3.0),
故搜索空间 = 两档。选择规则(主控硬性,`sweep` 子命令即按此表判):
**从便宜到贵依次试,取第一个满足「全圈 360 桶**全部** α mean|Δ| ≤1.5/255 且
>16 ≤3.5%」的滤波器**;实测(2026-09-22,`filter_sweep.json`):

| 滤波器 | 冷桶 median/p95 ms(min-of-3) | 全圈 α均 max | >16 max | IoU(α≥128) min | 边带 max | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| BILINEAR | 蟑 1.134/2.007 蝇 0.264/0.512 | **1.776/255**(fly_off) | **3.60%** | 0.9591 | 1.920 | **否决**:fly_off α 324/360 桶超 1.5/255、>16 32/360 桶超 |
| BICUBIC | 蟑 2.696/3.810 蝇 0.455/0.979 | 1.047/255 | 2.12% | 0.9721 | 1.757 | **选定**:α 判据 0 桶越界(比 SS 域旧路径 11.7/15.6ms 仍快 4.3×) |

选定后 ⑤ 的边带宽比上限按该滤波器全圈分布重定:`MAX_EDGE_WIDTH_RATIO = 1.85`
(= 观测 max 1.757 + 5%)。⚠ 不许为了让判据变绿放宽 ①/②(1.5/255、3.5%)、
判据 #6(帧预算 caps)、#9(内存 215/230MB)。

**性能目标随档位修订**(架构 §15.3):原目标「桶构建 median ≤1.5 / p95 ≤2.5ms」的
   前提是 **BILINEAR**,而 BILINEAR 已被外观判据**否决**(fly_off α 324/360、
   >16 32/360 桶越界)→ 目标按所选档位(BICUBIC)修订为 **median ≤3.0 /
   p95 ≤4.5ms**(实测 2.696/3.810 + ~10% 余量)。这是**随档位修订**,不是
   「达不到就改目标」。SS 域旧路径 11.749/15.588ms 仍是对照(慢 4.3×)。

---------------------------------------------------------- IoU 下限(裁决 4) --
原判据:③ α 掩膜 IoU ≥ **0.98**(§12 新增,替换轮廓长比)。全圈实测(BICUBIC):
   **fly 达不到** —— fly_off **88/360** 桶、fly_on **16/360** 桶 <0.98
   (min **0.9721**@12°),蟑螂两族全过(0.9939/0.9942)。执行按纪律**保留红断言、未改
   阈值**并上报,主控裁决(架构 §15.2)= **选项 A**。
新判据:**③ α 掩膜 IoU ≥ 0.96**(逐桶;①/②/④/⑤ 一字未动)。
为什么(**不是放宽,是原判据本身有量纲问题**):
  · **IoU 是尺度相关的**:蝇精灵 `50×50`,周长/面积比远大于蟑 `124×124`,
    同样的 ~0.5px 边界差在蝇身上扣的 IoU 多得多 —— 蟑同配置全过(0.9939)即佐证;
  · 几何核对**排除真缺陷**:最坏桶 α≥128 掩膜**面积比 0.9974**、**质心差 0.079px**
    (BILINEAR 同角 0.9871/0.08px),差异只在 ~0.5px 的抗锯齿/边界归属;
  · **负向对照(鉴别力仍在)**:1px 平移 → IoU 蝇 **0.8421**/蟑 0.9566(对平移强
    敏感 ✓);高斯模糊 r=1.2 → 蝇 0.9744(对模糊弱敏感,但模糊已由 ①② 咬死:
    α均 7.80/255、>16 14.32%);
  · **0.96 落在「可接受的亚像素噪声」(实测 ≥0.972)与「真实几何偏移」(≤0.842)
    之间的空当里**,鉴别力不丢。
选项 (B)「苍蝇回 SS 域」(IoU 恒 1.0)被**否决**:它换来的只是主控这条**校准错了的
   判据** —— SS 路径同样 `rotate(SS)→reduce` **回 1x**,**桶字节完全相同**
   (蝇 9.8KB/桶、蟑 60.1KB),回退还省掉 1x 母图缓存;代价是纯增 CPU(蝇冷桶
   0.455→1.543ms)。主控原话的「桶体积 ×9」不成立于本实现(×9 的是 88KB 的
   **瞬态**旋转画布)。

---------------------------------------------------------------- 阈值修订 ----
「锐度不降」(判据 #5 ② 第三条)原为**定性措辞、架构从未给数值容差**。主控裁决 2
(架构 §12,2026-09-22,含主控独立视觉终判:精灵层肉眼不可辨、diff 除轮廓抗锯齿带
外全黑)把它定为 `MAX_EDGE_WIDTH_RATIO = 1.60`(原 1.02 =「不增」,不可达),并**新增
两条原条完全没覆盖的守恒判据**作为更严补偿(宽度是 band/contour 的**比**,对整体
缩放/密度漂移不敏感,故不构成补偿):
    轮廓位置守恒: 50% α 等值线长比 ∈ [0.98, 1.02]
    α 质量守恒:   α 总量比        ∈ [0.96, 1.005]
本机实测(族内聚合):边带比(逐场景比均值)蟑 off 1.340× / 蝇 off 1.416× /
整帧 1.081×(主控「8 非轴角」子集口径为 1.454× / 1.524×,1.60 亦覆盖)、
轮廓比 1.0000/0.9962/0.9979、α 量比 0.9952/0.9825/0.9977。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "tests" / "refs" / "c1_baseline"

HEADINGS = tuple(float(h) for h in range(0, 360, 30))     # 12 个 heading
SPR_HALF = 120                                            # 与 test_chibi_render 同口径
SPECIES = (("roach", "species.cockroach", "AmericanCockroach"),
           ("fly", "species.fruitfly", "FruitFly"))

# 权威外观判据阈值(r25 C1 收口:口径 = **全圈 360 桶逐桶**;见 docstring「判据」节)
MAX_ALPHA_MEAN_ABS = 1.5 / 255.0        # 不动(原 12 样本池均 → 现逐桶)
MAX_ALPHA_GT16 = 0.035                  # 不动(原 12 样本池均 → 现逐桶)
# α 掩膜 IoU 下界(替换「轮廓长比」判据)。**主控裁决 4 = 架构 §15.2:0.98 → 0.96** ——
# 0.98 在蝇上不可达(BICUBIC 全圈 fly_off 88/360、fly_on 16/360 桶,min 0.9721@12°),
# 根因是 **IoU 尺度相关**(蝇 50² vs 蟑 124²,同样的 ~0.5px 边界差扣得更多);
# 几何核对排除真缺陷(面积比 0.9974、质心差 0.079px)。0.96 仍高于「蟑 1px 平移」的
# 0.9566,低于实测最坏 0.9721 —— 落在噪声与真实几何偏移之间的空当。详见模块 docstring
# 「IoU 下限」节。**此常量是唯一真源**(测试经 ref.MIN_ALPHA_IOU 引用,不得另写一份)。
MIN_ALPHA_IOU = 0.96
MIN_ALPHA_MASS_RATIO = 0.96             # 不动(逐桶)
MAX_ALPHA_MASS_RATIO = 1.005            # 不动(逐桶)
MIN_CONTOUR_RATIO = 0.98                # 历史读数列:轮廓长比(平移不敏感,已非判据)
MAX_CONTOUR_RATIO = 1.02
# 边带宽比上限:按**所选滤波器**(BICUBIC)的全圈 360 桶分布重定 = 观测 max 1.757
# (fly_off@45°) + 5% → 1.85。不再是池均推出来的 1.60 —— 1.60 只在 12 样本抽样集内
# 成立(BILINEAR 全圈最坏 1.920@45°、BICUBIC 1.757@45°;实测读数见
# worst_angles/filter_sweep.json 与 full_circle.json)。
MAX_EDGE_WIDTH_RATIO = 1.85
# 滤波器旋钮的搜索序(便宜 → 贵);真源 = neuropet/render/torso_art.py 的 _ROT_RESAMPLE
# ⚠ 原计划的第三档 LANCZOS 在 API 层不存在:PIL 的 rotate→transform 只接受
#   NEAREST/BILINEAR/BICUBIC(传 LANCZOS 抛 ValueError,Pillow 11.3.0 实测),
#   故搜索空间 = 两档。若两档都不满足全圈判据 → 按纪律停下回传主控。
FILTERS = ("BILINEAR", "BICUBIC")


# --------------------------------------------------------------- 场景渲染 ----
def _traits(name: str) -> dict:
    mod = importlib.import_module("neuropet.species."
                                  + ("cockroach" if name == "roach" else "fruitfly"))
    return getattr(mod, "AmericanCockroach" if name == "roach" else "FruitFly")(
    ).render_traits()


def _test_chibi_module():
    """复用 test_chibi_render 的姿态构造器(单一真源,避免姿势漂移)。"""
    p = ROOT / "tests" / "test_chibi_render.py"
    spec = importlib.util.spec_from_file_location("_c1_ref_chibi", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_scenes() -> "dict[str, object]":
    """按冻结顺序渲染全部场景 → {scene_name: RGBA Image}。每次从干净缓存开始。

    固定随机种子:整帧场景的姿态链里有 `random.uniform`(body/base.py:130 触角
    摆动绝对时间等),不设种子则跨进程不可复现 —— 参考产物必须是可复算的。"""
    from neuropet.render import torso_art
    from neuropet.render.renderer import render_pose
    random.seed(0xC1)                                     # 见 docstring
    torso_art.invalidate()                                # 干净缓存 → 顺序确定
    out = {}
    for tag, sid, _cls in SPECIES:
        tr = _traits(tag)
        for mode, t in (("off", tr), ("on", dict(tr, chibi=True))):
            for hd in HEADINGS:
                name = f"spr_{tag}_{mode}_h{int(hd):03d}"
                out[name] = torso_art.get_torso(sid, SPR_HALF, hd, 1.0, t)
    ch = _test_chibi_module()
    tr_r, tr_f = _traits("roach"), _traits("fly")
    frames = [(f"frame_fly_{k}", p, tr_f) for k, p in ch.build_fly_poses()]
    frames.append(("frame_roach_off", ch.build_roach_pose(), tr_r))
    frames.append(("frame_roach_on", ch.build_roach_pose(),
                   dict(tr_r, chibi=True)))
    for name, pose, t in frames:
        out[name] = render_pose(pose, t)
    return out


# ------------------------------------------------------------------- 度量 ----
# 掩膜查表(0/255),供 `point` 一次性映射(C 速度);真源口径见模块 docstring
_GE128 = [255 if v >= 128 else 0 for v in range(256)]   # 50% 等值线内的「实心」掩膜
_GE1 = [0] + [255] * 255                                # α>0 的「墨迹」支撑掩膜


def alpha_metrics(a, b) -> dict:
    """(基线 a, 当前 b) → alpha 差异度量。全画布 + 墨迹并集两个口径。

    `alpha_iou` = α≥128 硬掩膜(50% 等值线内)的交并比,`iou_ink` = α>0 支撑
    掩膜的交并比(判据 ③ = 前者,替换「轮廓长比」—— 后者对平移不敏感,见模块
    docstring「判据」节)。本函数是**权威慢口径**(逐像素 Python 循环),
    `diff_metrics` 的 C 版必须与它逐位相同(由 `_selftest_metric_equiv` 断言)。"""
    if a.size != b.size:
        raise AssertionError(f"尺寸不一致 {a.size} vs {b.size}")
    pa, pb = a.getchannel("A").load(), b.getchannel("A").load()
    w, h = a.size
    n = w * h
    tot = 0
    ink = 0
    gt16 = 0
    inter = union = 0
    inter_i = union_i = 0
    for y in range(h):
        for x in range(w):
            va, vb = pa[x, y], pb[x, y]
            d = va - vb
            if d < 0:
                d = -d
            tot += d
            if va > 0 or vb > 0:
                ink += 1
                if d > 16:
                    gt16 += 1
            ma, mb = va >= 128, vb >= 128
            if ma and mb:
                inter += 1
            if ma or mb:
                union += 1
            ia, ib = va > 0, vb > 0
            if ia and ib:
                inter_i += 1
            if ia or ib:
                union_i += 1
    return {"alpha_mean_abs": tot / n / 255.0,
            "alpha_gt16_ratio": gt16 / n,
            "alpha_gt16_ratio_ink": (gt16 / ink) if ink else 0.0,
            "alpha_iou": (inter / union) if union else 1.0,
            "iou_ink": (inter_i / union_i) if union_i else 1.0,
            "alpha_max_abs": max(abs(pa[x, y] - pb[x, y])
                                 for y in range(h) for x in range(w))}


def edge_metrics(img) -> dict:
    """过渡带宽度 = band / contour(见模块 docstring 口径)。"""
    a = img.getchannel("A").load()
    w, h = img.size
    band = 0
    contour = 0
    for y in range(h):
        for x in range(w):
            v = a[x, y]
            if 0 < v < 255:
                band += 1
            if v >= 128 and ((x == 0 or a[x - 1, y] < 128)
                             or (x == w - 1 or a[x + 1, y] < 128)
                             or (y == 0 or a[x, y - 1] < 128)
                             or (y == h - 1 or a[x, y + 1] < 128)):
                contour += 1
    return {"band": band, "contour": contour,
            "edge_width_px": (band / contour) if contour else 0.0}


def sprite_box_for(tag: str, traits: dict):
    """(物种标签, traits) → 1x 精灵画布在 (2*half)² 容器里的居中框。"""
    from neuropet.render import torso_art
    m = torso_art._master_state(tag, torso_art._thash(traits), traits)
    _box, s1x = torso_art._rot_geo(m, "closed")
    off = (SPR_HALF * 2 - s1x) // 2
    return (off, off, off + s1x, off + s1x)


def sprite_box(name: str):
    """精灵场景名 → 1x 精灵画布在 (2*half)² 容器里的居中框;整帧返回 None。"""
    if not name.startswith("spr_"):
        return None
    tr = _traits("roach" if "_roach_" in name else "fly")
    if "_on_" in name:
        tr = dict(tr, chibi=True)
    return sprite_box_for("roach" if "_roach_" in name else "fly", tr)


def region(name: str, img):
    """度量区域(见模块 docstring)。"""
    box = sprite_box(name)
    return img.crop(box) if box else img


def sha(img) -> str:
    return hashlib.sha256(img.tobytes()).hexdigest()[:16]


# ---------------------------------------------------------------- 子命令 ----
def _git_rev() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True
                              ).stdout.strip()
    except Exception:                                     # noqa: BLE001
        return "?"


def cmd_gen() -> int:
    scenes = render_scenes()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "what": "r25 C1 参考渲染历史基线(C1 落地前的渲染路径输出)",
        "generated_at_git": _git_rev(),
        "note": "重新生成即破坏判据 #5 ② 的意义;仅在主控裁决后才可重冻结",
        "metric_defs": {
            "region": "sprite scenes: the 1x sprite canvas (_rot_geo crop box); "
                      "frame scenes: whole frame",
            "alpha_mean_abs": "mean(|A_new-A_base|)/255 over the region",
            "alpha_gt16_ratio": "#{|dA|>16} / region pixels",
            "edge_width_px": "#{0<a<255} / #{a>=128 and has 4-neighbour a<128}",
            "contour_ratio": "sum(contour_new) / sum(contour_base) per family",
            "alpha_mass_ratio": "sum(alpha_new) / sum(alpha_base) per family",
        },
        "thresholds": {"alpha_mean_abs": MAX_ALPHA_MEAN_ABS,
                       "alpha_gt16_ratio": MAX_ALPHA_GT16,
                       "edge_width_ratio": MAX_EDGE_WIDTH_RATIO,
                       "contour_ratio": [MIN_CONTOUR_RATIO, MAX_CONTOUR_RATIO],
                       "alpha_mass_ratio": [MIN_ALPHA_MASS_RATIO,
                                            MAX_ALPHA_MASS_RATIO]},
        "thresholds_note": "edge_width_ratio 由 1.02 标定为 1.60 并新增两条守恒判据"
                           "(主控裁决 2,架构 §12);场景 PNG 本身仍是 C1 落地前的冻结产物",
        "scenes": {},
    }
    for name, img in scenes.items():
        img.save(OUT_DIR / f"{name}.png", optimize=True)
        manifest["scenes"][name] = {"size": list(img.size), "sha256": sha(img)}
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    total = sum((OUT_DIR / f"{n}.png").stat().st_size for n in scenes)
    print(f"[gen] 冻结 {len(scenes)} 个场景 → {OUT_DIR.relative_to(ROOT)}"
          f"(PNG 合计 {total / 1024:.0f} KB,清单 manifest.json)")
    return 0


def _load_base(name: str):
    from PIL import Image
    with Image.open(OUT_DIR / f"{name}.png") as im:
        return im.convert("RGBA")


def alpha_mass(img) -> int:
    """区域 α 总量(可加量;直方图加权,避免逐像素 Python 循环)。"""
    hist = img.getchannel("A").histogram()
    return sum(i * c for i, c in enumerate(hist))


def measure(scenes: "dict[str, object]", names) -> "list[dict]":
    """对一组场景逐场景度量(基线 PNG vs 当前渲染),返回明细。"""
    rows = []
    for name in names:
        base = region(name, _load_base(name))
        new = region(name, scenes[name])
        am = alpha_metrics(base, new)
        eb, en = edge_metrics(base), edge_metrics(new)
        mb, mn = alpha_mass(base), alpha_mass(new)
        rows.append({"name": name, **am,
                     "edge_base": eb["edge_width_px"],
                     "edge_new": en["edge_width_px"],
                     "edge_ratio": (en["edge_width_px"]
                                    / max(1e-9, eb["edge_width_px"])),
                     "contour_base": eb["contour"], "contour_new": en["contour"],
                     "mass_base": mb, "mass_new": mn})
    return rows


def pool(rows: "list[dict]") -> dict:
    """参考集聚合(与架构 §1.4 公布数字同口径)。"""
    n = len(rows)
    cb = sum(r["contour_base"] for r in rows)
    mb = sum(r["mass_base"] for r in rows)
    return {"n": n,
            "alpha_mean_abs": sum(r["alpha_mean_abs"] for r in rows) / n,
            "alpha_gt16_ratio": sum(r["alpha_gt16_ratio"] for r in rows) / n,
            "alpha_mean_abs_max": max(r["alpha_mean_abs"] for r in rows),
            "alpha_gt16_ratio_max": max(r["alpha_gt16_ratio"] for r in rows),
            "iou_min": min(r["alpha_iou"] for r in rows),
            "iou_mean": sum(r["alpha_iou"] for r in rows) / n,
            "edge_base": sum(r["edge_base"] for r in rows) / n,
            "edge_new": sum(r["edge_new"] for r in rows) / n,
            "edge_ratio": sum(r["edge_ratio"] for r in rows) / n,
            "edge_ratio_max": max(r["edge_ratio"] for r in rows),
            "contour_base": cb, "contour_new": sum(r["contour_new"] for r in rows),
            "contour_ratio": sum(r["contour_new"] for r in rows) / max(1, cb),
            "alpha_mass_ratio": (sum(r["mass_new"] for r in rows)
                                 / max(1, mb)),
            "alpha_mass_ratio_min": min(r["mass_new"] / max(1, r["mass_base"])
                                        for r in rows),
            "alpha_max_abs": max(r["alpha_max_abs"] for r in rows)}


def families(manifest: dict) -> "dict[str, list[str]]":
    names = list(manifest["scenes"])
    return {f"spr_{sp}_{m}_h*": [n for n in names
                                 if n.startswith(f"spr_{sp}_{m}_")]
            for sp in ("roach", "fly") for m in ("off", "on")} | {
        "frame_*": [n for n in names if n.startswith("frame_")]}


def cmd_verify(verbose: bool = True) -> int:
    from PIL import Image                    # noqa: F401  (PIL 置前,报错更早)
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    scenes = render_scenes()
    print(f"[verify] 基线 git={manifest['generated_at_git']} "
          f"场景 {len(manifest['scenes'])} 个")
    print(f"{'scene':<26} {'mean|dA|':>9} {'>16 占比':>9} "
          f"{'边带(基线→今)':>17} {'锐度比':>7}")
    bad = []
    for fam, names in families(manifest).items():
        rows = measure(scenes, names)
        p = pool(rows)
        for r in rows:
            if verbose:
                print(f"{r['name']:<26} {r['alpha_mean_abs'] * 255:9.3f} "
                      f"{r['alpha_gt16_ratio'] * 100:8.2f}% "
                      f"{r['edge_base']:7.3f}→{r['edge_new']:<8.3f} "
                      f"{r['edge_ratio']:7.3f}")
        ok = (p["alpha_mean_abs_max"] <= MAX_ALPHA_MEAN_ABS
              and p["alpha_gt16_ratio_max"] <= MAX_ALPHA_GT16
              and p["edge_ratio_max"] <= MAX_EDGE_WIDTH_RATIO
              and p["iou_min"] >= MIN_ALPHA_IOU
              and p["alpha_mass_ratio_min"] >= MIN_ALPHA_MASS_RATIO)
        if not ok:
            bad.append(fam)
        print(f"[参考集] {fam:<20} n={p['n']} | mean|dA| 池均 "
              f"{p['alpha_mean_abs'] * 255:.3f}/max {p['alpha_mean_abs_max'] * 255:.3f}"
              f"/255(≤{MAX_ALPHA_MEAN_ABS * 255:.1f}) | >16 池均 "
              f"{p['alpha_gt16_ratio'] * 100:.2f}/max "
              f"{p['alpha_gt16_ratio_max'] * 100:.2f}%(≤{MAX_ALPHA_GT16 * 100:.1f})"
              f" | 边带 {p['edge_base']:.3f}→{p['edge_new']:.3f} 比池均 "
              f"{p['edge_ratio']:.3f}/max {p['edge_ratio_max']:.3f}"
              f"(≤{MAX_EDGE_WIDTH_RATIO}) | IoU min {p['iou_min']:.4f}"
              f"(≥{MIN_ALPHA_IOU}) | α量比 min {p['alpha_mass_ratio_min']:.4f}"
              f"(≥{MIN_ALPHA_MASS_RATIO}) | 轮廓比(读数列) "
              f"{p['contour_ratio']:.4f} | max|dA| {p['alpha_max_abs']} → "
              f"{'达标' if ok else '** 超标 **'}")
    print(f"[verify] 超标族:{bad if bad else '无'}")
    return 1 if bad else 0


def _triplet(base, new, zoom: int):
    """[基线 | 当前 | |ΔA| 热力图] 并排,背景白;zoom>1 用 NEAREST 放大看边缘。"""
    from PIL import Image
    heat = Image.new("RGB", base.size, (0, 0, 0))
    hp = heat.load()
    pa, pb = base.getchannel("A").load(), new.getchannel("A").load()
    for yy in range(base.height):
        for xx in range(base.width):
            dd = pb[xx, yy] - pa[xx, yy]
            if dd > 8:
                hp[xx, yy] = (255, 60, 60)          # 当前更不透明
            elif dd < -8:
                hp[xx, yy] = (70, 90, 255)          # 当前更透明
    out = []
    for im in (base, new, heat):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode == "RGBA":
            bg.paste(im, (0, 0), im)
        else:
            bg.paste(im, (0, 0))
        out.append(bg.resize((im.width * zoom, im.height * zoom),
                             Image.NEAREST) if zoom > 1 else bg)
    return out


def cmd_compare() -> int:
    """前/后对照图(入库,供 §10 裁决 1 的视觉终判)。

    两张:
      before_after_sprites.png —— 精灵整体(4 heading × off/on × 2 物种)
      before_after_edges.png   —— 轮廓局部 3× 放大(看边缘过渡带)
    左=历史基线(C1 前) 中=当前(C1 后) 右=|ΔA|>8 热力图(红=变不透明/蓝=变透明)
    """
    from PIL import Image, ImageDraw
    scenes = render_scenes()
    spr = [f"spr_{sp}_{m}_h{h:03d}" for sp in ("roach", "fly")
           for m in ("off", "on") for h in (0, 30, 120, 210)]
    frames = ["frame_roach_off", "frame_roach_on",
              "frame_fly_fly_rest", "frame_fly_fly_air"]
    for group, names, zoom in (("sprites", spr, 1), ("edges", spr, 3),
                               ("frames", frames, 1)):
        cells = {}
        for n in names:
            b, w = region(n, _load_base(n)), region(n, scenes[n])
            if group == "edges":                     # 取含轮廓的居中窗口
                if b.width > 48:
                    box = ((b.width - 44) // 2, (b.height - 44) // 2,
                           (b.width + 44) // 2, (b.height + 44) // 2)
                    b, w = b.crop(box), w.crop(box)
            cells[n] = _triplet(b, w, zoom)
        cw = max(c[0].width for c in cells.values()) + 12
        ch = max(c[0].height for c in cells.values()) + 30
        sheet = Image.new("RGB", (cw * 3 + 8, ch * len(names) + 34),
                          (246, 244, 240))
        d = ImageDraw.Draw(sheet)
        d.text((10, 8), f"r25 C1 前后对照 [{group}] 左=历史基线(C1 前) "
                        f"中=当前(C1 后) 右=|dA|>8 热力图"
                        + (f"  局部 {zoom}× 放大" if zoom > 1 else ""),
               fill=(40, 40, 40))
        for row, name in enumerate(names):
            y = 34 + row * ch
            for col, im in enumerate(cells[name]):
                sheet.paste(im, (4 + col * cw, y + 20))
            d.text((6, y + 4), f"{name}  {im.size[0] // zoom}x"
                               f"{im.size[1] // zoom}", fill=(30, 30, 30))
        p = OUT_DIR / f"before_after_{group}.png"
        sheet.save(p, optimize=True)
        print(f"[compare] {p.relative_to(ROOT)}({sheet.size[0]}x{sheet.size[1]}, "
              f"{p.stat().st_size / 1024:.0f} KB)")
    return 0


# ------------------------------------------- 全圈取证(★权威外观判据的测量面) ----
WORST_DIR = OUT_DIR / "worst_angles"
CIRCLE_STEP = 1.0            # 逐桶粒度(与生产 _angle_step=1 一致)
_SS_ENV_WARNED = [False]     # env 置真时的告警只打一次


def _pct(xs: "list[float]", q: float) -> float:
    """线性插值分位(与 numpy 默认口径一致;不为此引入第三方依赖)。"""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


_FONT_CACHE: dict = {}


def _font(size: int = 13):
    """图注字体 → (font, cjk_ok)。

    PIL 默认字体(FreeType 版 Aileron)**没有中文字形也没有箭头/乘号**,直接画
    中文会出豆腐块、`→` 会被吞掉(实测)。故优先加载系统中文字体;取不到时
    回退默认字体,并由调用方(`_L`)改用 ASCII 文案。"""
    if size not in _FONT_CACHE:
        from PIL import ImageFont
        got = None
        for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                  "C:/Windows/Fonts/simsun.ttc"):
            if Path(p).exists():
                try:
                    got = (ImageFont.truetype(p, size), True)
                    break
                except Exception:                      # noqa: BLE001
                    continue
        _FONT_CACHE[size] = got or (ImageFont.load_default(), False)
    return _FONT_CACHE[size]


def _L(zh: str, en: str) -> str:
    """中文文案(无中文字体时回退 ASCII,避免图注变豆腐块)。"""
    return zh if _font()[1] else en


def _clear_rot_only() -> None:
    """只清旋转桶(保留 master)。两条理由:① 全圈取证要逐桶**冷构建**,不能让
    LRU 淘汰/角量化降档参与;② 翻转 `_ROT_SS_DOMAIN` 前后**必须**清桶 —— 桶键
    (sid, th, variant, 桶) 不含域标志,否则取到旧域的桶(见 torso_art 的
    「中途翻转不隔离缓存」)。"""
    from neuropet.render import torso_art
    with torso_art._cache_lock:
        torso_art._rot.clear()
        torso_art._rot_bytes["roach"] = 0
        torso_art._rot_bytes["fly"] = 0
    torso_art._angle_step = 1


def cold_render(tag: str, traits: dict, heading: float, ss_domain: bool,
                filt: "str | None" = None):
    """单域**冷**渲染一个桶(渲染前清桶 → 强制走贵路径,不吃缓存/派生)。

    清桶是硬要求:`_ROT_SS_DOMAIN` 与 `_ROT_FILTER` **都不在桶键里**,不清就是
    串味(见 torso_art 的同名警告)。返回的图是 `get_torso` 新贴的画布,与缓存
    对象无关,故清桶不会影响已返回的图。"""
    from neuropet.render import torso_art
    sid = "species." + ("cockroach" if tag == "roach" else "fruitfly")
    prev_d, prev_f = torso_art._rot_ss_domain(), torso_art._ROT_FILTER
    if prev_d and not _SS_ENV_WARNED[0]:
        _SS_ENV_WARNED[0] = True
        print("[全圈] 注意:NEUROPET_ROT_SS 已置真,本模式强制两域对照(忽略该 env)")
    try:
        _clear_rot_only()
        torso_art._ROT_SS_DOMAIN = ss_domain
        if filt:
            torso_art._ROT_FILTER = filt
        return torso_art.get_torso(sid, SPR_HALF, heading, 1.0, traits)
    finally:
        torso_art._ROT_SS_DOMAIN = prev_d
        torso_art._ROT_FILTER = prev_f
        _clear_rot_only()                     # 余桶归零:不留串味状态给调用方


def domain_pair(tag: str, traits: dict, heading: float,
                filt: "str | None" = None, base=None):
    """(基线图, 当前图) @heading:基线 = SS 域路径(= C1 前语义;其与冻结基线的
    逐位等价由 tests/test_chibi_render.py 的回退路径判据锁定),当前 = 1x 域路径
    (`filt` 指定滤波器,None = 模块当前 `_ROT_FILTER`)。

    `base` 给定时复用该 SS 基线(多滤波器对照共用一张基线 → 省 2/3 渲染)。"""
    if base is None:
        base = cold_render(tag, traits, heading, True)
    return base, cold_render(tag, traits, heading, False, filt)


def _shift_mask(m, dx: int, dy: int):
    """0/255 掩膜平移:(x,y) 处取 m[x+dx, y+dy],越界填 0(供 C 级邻域运算)。"""
    from PIL import Image
    w, h = m.size
    out = Image.new("L", (w, h), 0)
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = max(0, -dx), max(0, -dy)
    out.paste(m.crop((x1, y1, w - x0, h - y0)), (x0, y0))
    return out


def _contour_count(a) -> int:
    """50% α 等值线长度,与 `edge_metrics` 的逐像素口径**逐位相同**。

    冻结口径条件 = `α≥128 且 4-邻域**存在** α<128`(注意是「存在」),故本实现取
    「4 邻域**全部** ≥128」的补:contour = m ∧ ¬(∧ 四个平移掩膜)。
    画布最外圈像素在冻结口径里**无条件**满足(「x==0 ⇒ 条件真」)—— 平移掩膜
    越界填 0 恰好复现该语义(越界 ≡ 那条邻域 <128 ≡ 存在邻域 <128)。"""
    from PIL import ImageChops
    m = a.point(_GE128)
    nb_all = ImageChops.darker(
        ImageChops.darker(_shift_mask(m, 1, 0), _shift_mask(m, -1, 0)),
        ImageChops.darker(_shift_mask(m, 0, 1), _shift_mask(m, 0, -1)))
    return ImageChops.darker(m, ImageChops.invert(nb_all)).histogram()[255]


def diff_metrics(base, new) -> dict:
    """一次算齐 `alpha_metrics` + `edge_metrics` + α 总量 + IoU(全走 PIL C 算子)。

    公式与冻结函数逐项等同(由 `_selftest_metric_equiv` 在实图上**逐位**断言),
    但全圈要跑 4 族 × 360 桶 × 滤波器数 × 2 图的 Python 像素循环开销不可接受 ——
    本实现全部走 difference/point/histogram/lighter/darker(C 级)。
    ⚠ 浮点算式必须与冻结函数**逐字相同**(`tot / n / 255.0`、`band / contour`),
    否则自查会咬。"""
    from PIL import ImageChops
    if base.size != new.size:
        raise AssertionError(f"尺寸不一致 {base.size} vs {new.size}")
    w, h = base.size
    n = w * h
    aa, ab = base.getchannel("A"), new.getchannel("A")
    hd = ImageChops.difference(aa, ab).histogram()
    tot = sum(i * c for i, c in enumerate(hd))
    gt16 = sum(hd[17:])                       # #{|ΔA| > 16}
    amax = max((i for i, c in enumerate(hd) if c), default=0)
    ha, hb = aa.histogram(), ab.histogram()
    mass_b = sum(i * c for i, c in enumerate(ha))
    mass_n = sum(i * c for i, c in enumerate(hb))
    band_b, band_n = sum(ha[1:255]), sum(hb[1:255])
    cont_b, cont_n = _contour_count(aa), _contour_count(ab)
    ma, mb = aa.point(_GE128), ab.point(_GE128)
    inter = ImageChops.darker(ma, mb).histogram()[255]
    union = ImageChops.lighter(ma, mb).histogram()[255]
    ia, ib = aa.point(_GE1), ab.point(_GE1)
    inter_i = ImageChops.darker(ia, ib).histogram()[255]
    union_i = ImageChops.lighter(ia, ib).histogram()[255]
    eb = band_b / cont_b if cont_b else 0.0
    en = band_n / cont_n if cont_n else 0.0
    return {"alpha_mean_abs": tot / n / 255.0,
            "alpha_gt16_ratio": gt16 / n,
            "alpha_max_abs": amax,
            "alpha_iou": (inter / union) if union else 1.0,
            "iou_ink": (inter_i / union_i) if union_i else 1.0,
            "band_base": band_b, "band_new": band_n,
            "contour_base": cont_b, "contour_new": cont_n,
            "edge_base": eb, "edge_new": en,
            "edge_ratio": en / max(1e-9, eb),
            "mass_base": mass_b, "mass_new": mass_n}


def _selftest_metric_equiv(base, new) -> None:
    """口径自查:`diff_metrics` 与冻结度量函数在同一张实图上**逐位**一致。"""
    dm = diff_metrics(base, new)
    am, eb, en = alpha_metrics(base, new), edge_metrics(base), edge_metrics(new)
    chk = [("alpha_mean_abs", dm["alpha_mean_abs"], am["alpha_mean_abs"]),
           ("alpha_gt16_ratio", dm["alpha_gt16_ratio"], am["alpha_gt16_ratio"]),
           ("alpha_iou", dm["alpha_iou"], am["alpha_iou"]),
           ("iou_ink", dm["iou_ink"], am["iou_ink"]),
           ("alpha_max_abs", float(dm["alpha_max_abs"]),
            float(am["alpha_max_abs"])),
           ("edge_base", dm["edge_base"], eb["edge_width_px"]),
           ("edge_new", dm["edge_new"], en["edge_width_px"]),
           ("mass_base", float(dm["mass_base"]), float(alpha_mass(base))),
           ("mass_new", float(dm["mass_new"]), float(alpha_mass(new)))]
    bad = [k for k, x, y in chk if x != y]
    if bad:
        raise AssertionError(f"全圈度量口径与冻结口径不一致:{bad}")


def summarize(rows: "list[dict]") -> dict:
    """全圈(360 桶)分布:min/p50/p90/p99/max + **逐桶越界计数** + 最坏角。

    自 r25 C1 收口起,全圈**就是**权威判据的测量面(逐桶判,阈值见模块常量),
    故这里的 `n_*_over` 即失败桶数;分布分位数仍是主读数列(供主控复算)。"""
    r = [x["edge_ratio"] for x in rows]
    am = [x["alpha_mean_abs"] for x in rows]
    g = [x["alpha_gt16_ratio"] for x in rows]
    io = [x["alpha_iou"] for x in rows]
    ioi = [x["iou_ink"] for x in rows]
    cr = [x["contour_new"] / max(1, x["contour_base"]) for x in rows]
    mr = [x["mass_new"] / max(1, x["mass_base"]) for x in rows]
    imax = max(range(len(rows)), key=lambda i: r[i])
    half = rows[sorted(range(len(r)), key=lambda i: r[i])[len(r) // 2]]
    return {"n": len(rows),
            "iou_min": min(io), "iou_p01": _pct(io, .01), "iou_p50": _pct(io, .5),
            "iou_argmin": rows[io.index(min(io))]["angle"],
            "n_iou_under": sum(1 for x in io if x < MIN_ALPHA_IOU),
            "iou_ink_min": min(ioi), "iou_ink_argmin":
                rows[ioi.index(min(ioi))]["angle"],
            "n_iou_ink_under": sum(1 for x in ioi if x < MIN_ALPHA_IOU),
            "edge_min": min(r), "edge_p50": _pct(r, .5), "edge_p90": _pct(r, .9),
            "edge_p99": _pct(r, .99), "edge_max": r[imax],
            "edge_argmax": rows[imax]["angle"],
            "edge_pool_mean": sum(r) / len(r),
            "band_base_at_max": rows[imax]["edge_base"],
            "band_new_at_max": rows[imax]["edge_new"],
            "band_base_at_p50": half["edge_base"], "band_new_at_p50": half["edge_new"],
            "n_over": sum(1 for x in r if x > MAX_EDGE_WIDTH_RATIO),
            "n_over_150": sum(1 for x in r if x > 1.50),
            "alpha_mean_max": max(am),
            "alpha_argmax": rows[max(range(len(am)), key=lambda i: am[i])]["angle"],
            "n_alpha_over": sum(1 for x in am if x > MAX_ALPHA_MEAN_ABS),
            "gt16_max": max(g),
            "gt16_argmax": rows[max(range(len(g)), key=lambda i: g[i])]["angle"],
            "n_gt16_over": sum(1 for x in g if x > MAX_ALPHA_GT16),
            "contour_min": min(cr), "contour_max": max(cr),
            "contour_argmin": rows[cr.index(min(cr))]["angle"],
            "contour_pool": (sum(x["contour_new"] for x in rows)
                             / max(1, sum(x["contour_base"] for x in rows))),
            "n_contour_out": sum(1 for x in cr
                                 if not MIN_CONTOUR_RATIO <= x <= MAX_CONTOUR_RATIO),
            "mass_min": min(mr), "mass_max": max(mr),
            "mass_pool": (sum(x["mass_new"] for x in rows)
                          / max(1, sum(x["mass_base"] for x in rows))),
            "n_mass_out": sum(1 for x in mr
                              if not MIN_ALPHA_MASS_RATIO <= x
                              <= MAX_ALPHA_MASS_RATIO),
            "atlas_12": [rows[int(i * 30)]["edge_ratio"] for i in range(12)],
            "worst": sorted(((rows[i]["angle"], r[i], rows[i]["edge_base"],
                              rows[i]["edge_new"]) for i in range(len(rows))),
                            key=lambda z: -z[1])[:8]}


def circle_rows(filters: "tuple[str, ...]" = FILTERS,
                progress: bool = True) -> "dict[str, dict[str, list[dict]]]":
    """全 360 桶逐桶度量 → {滤波器: {族: rows(按角度升序)}};row = 度量 + `angle`。

    SS 基线每桶只渲一次、各滤波器共用(否则多花 2/3 的渲染时间)。逐桶**冷构建**
    (不用 180° 派生、不吃 LRU),1° 量化 —— 即生产 `_angle_step=1` 的粒度。"""
    out = {}
    for tag in ("roach", "fly"):
        tr_off = _traits(tag)
        for mode, t in (("off", tr_off), ("on", dict(tr_off, chibi=True))):
            box = sprite_box_for(tag, t)
            fam = f"{tag}_{mode}"
            for f in filters:
                out.setdefault(f, {})[fam] = []
            for b in range(360):
                ang = b * CIRCLE_STEP
                base = cold_render(tag, t, ang, True)          # 每桶一次,各滤波共用
                for f in filters:
                    new = cold_render(tag, t, ang, False, f)
                    bb, nn = base.crop(box), new.crop(box)
                    m = diff_metrics(bb, nn)
                    m["angle"] = ang
                    out[f][fam].append(m)
                    if b == 0:                       # 实图口径自查(每族一次)
                        _selftest_metric_equiv(bb, nn)
            if progress:
                for f in filters:
                    s = summarize(out[f][fam])
                    bad = _violations(out[f][fam])
                    print(f"[全圈] {f:<8} {fam:<10} 完成:α均 max "
                          f"{s['alpha_mean_max'] * 255:.3f}/255,>16 max "
                          f"{s['gt16_max'] * 100:.2f}%,IoU min {s['iou_min']:.4f},"
                          f"边带 max {s['edge_max']:.3f}@{s['edge_argmax']:.0f}° "
                          f"| 逐桶越界 {'无' if not bad else bad}")
    return out


def _violations(rows: "list[dict]") -> str:
    """**逐桶**判据(★权威)的越界摘要:每一个桶都必须过;返回 '' = 全过。"""
    parts = []
    n = len(rows)
    c = sum(1 for m in rows if m["alpha_mean_abs"] > MAX_ALPHA_MEAN_ABS)
    if c:
        parts.append(f"α 1.5/255: {c}/{n}")
    c = sum(1 for m in rows if m["alpha_gt16_ratio"] > MAX_ALPHA_GT16)
    if c:
        parts.append(f">16 3.5%: {c}/{n}")
    c = sum(1 for m in rows if m["alpha_iou"] < MIN_ALPHA_IOU)
    if c:
        parts.append(f"IoU {MIN_ALPHA_IOU}: {c}/{n}")
    c = sum(1 for m in rows
            if m["mass_new"] / max(1, m["mass_base"]) < MIN_ALPHA_MASS_RATIO)
    if c:
        parts.append(f"α 量比 0.96: {c}/{n}")
    c = sum(1 for m in rows if m["edge_ratio"] > MAX_EDGE_WIDTH_RATIO)
    if c:
        parts.append(f"边带比 {MAX_EDGE_WIDTH_RATIO}: {c}/{n}")
    return "、".join(parts)


def print_circle(fam: str, s: dict, filt: str = "", rows=None) -> None:
    """打印一族全圈分布 + **逐桶判据结论**(★权威;`rows` 给定时判,否则只读分布)。"""
    tag = f"[全圈]{filt:>9} " if filt else "[全圈] "
    print(f"{tag}{fam:<10} n={s['n']}(1° 量化,逐桶冷构建;基线=SS 域=C1 前)")
    print(f"       边带比 min {s['edge_min']:.3f} / p50 {s['edge_p50']:.3f} / "
          f"p90 {s['edge_p90']:.3f} / p99 {s['edge_p99']:.3f} / max "
          f"{s['edge_max']:.3f}@{s['edge_argmax']:.0f}°  "
          f"(池均 {s['edge_pool_mean']:.3f},超 {MAX_EDGE_WIDTH_RATIO} 桶 "
          f"{s['n_over']}/{s['n']})")
    print(f"       绝对带宽 @max {s['band_base_at_max']:.2f}→"
          f"{s['band_new_at_max']:.2f}px,@p50 {s['band_base_at_p50']:.2f}→"
          f"{s['band_new_at_p50']:.2f}px")
    print(f"       α均差 max {s['alpha_mean_max'] * 255:.3f}/255"
          f"@{s['alpha_argmax']:.0f}°(逐桶超 1.5/255:{s['n_alpha_over']} 桶) | "
          f">16 占比 max {s['gt16_max'] * 100:.2f}%@{s['gt16_argmax']:.0f}°"
          f"(逐桶超 3.5%:{s['n_gt16_over']} 桶)")
    print(f"       α 掩膜 IoU(α≥128) min {s['iou_min']:.4f}@{s['iou_argmin']:.0f}°"
          f"(<{MIN_ALPHA_IOU} 桶 {s['n_iou_under']})  | 墨迹掩膜 IoU(α>0,读数列,"
          f"非判据) min "
          f"{s['iou_ink_min']:.4f}@{s['iou_ink_argmin']:.0f}°"
          f"(<{MIN_ALPHA_IOU} 桶 {s['n_iou_ink_under']})")
    print(f"       α量比 min {s['mass_min']:.4f}~{s['mass_max']:.4f}"
          f"(越界 {s['n_mass_out']} 桶;池均 {s['mass_pool']:.4f})")
    print(f"       轮廓长比 {s['contour_min']:.4f}~{s['contour_max']:.4f}"
          f"(读数列,已非判据;min@{s['contour_argmin']:.0f}°)")
    print(f"       12 样本(30° 倍数)边带比池均 = {sum(s['atlas_12']) / 12:.3f}"
          f"(= 旧判据口径);全圈 max 与其差 "
          f"{s['edge_max'] - sum(s['atlas_12']) / 12:+.3f}")
    print("       最坏角 top5:" + " | ".join(
        f"{a:.0f}° {rr:.3f}({b0:.2f}→{b1:.2f}px)" for a, rr, b0, b1 in s["worst"][:5]))
    if rows is not None:
        bad = _violations(rows)
        print(f"       → 逐桶判据:{'全部 360 桶通过' if not bad else '** 越界 ** ' + bad}")


def _dump_circle(rows_by_fam: "dict[str, list[dict]]", stats: dict,
                 filt: str = "") -> Path:
    """落盘全圈读数(可复算基础):分布 + 逐桶度量。"""
    WORST_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"what": "r25 C1 全 360 桶分布(基线 = SS 域路径 = C1 前语义)",
               "filter": filt or "?",
               "note": "★权威外观判据的测量面:逐桶判 α mean|Δ| ≤1.5/255、>16 ≤3.5%、"
                       f"IoU ≥{MIN_ALPHA_IOU}(裁决 4:0.98→0.96)、α 量比 ≥0.96、"
                       "边带宽比 ≤MAX_EDGE_WIDTH_RATIO;"
                       "阈值真源 = tools/c1_ref_render.py 常量。逐桶冷构建,1° 量化。",
               "step_deg": CIRCLE_STEP,
               "families": {}}
    for fam, s in stats.items():
        d = {k: v for k, v in s.items() if k != "worst"}
        d["worst"] = [{"angle": a, "edge_ratio": round(rr, 4),
                       "band_base_px": round(b0, 3), "band_new_px": round(b1, 3)}
                      for a, rr, b0, b1 in s["worst"]]
        d["per_bucket"] = {
            "alpha_mean_abs_max": round(max(x["alpha_mean_abs"] for x in
                                            rows_by_fam[fam]) * 255, 3),
            "alpha_gt16_ratio_max": round(max(x["alpha_gt16_ratio"] for x in
                                              rows_by_fam[fam]) * 100, 3),
            "iou_min": round(min(x["alpha_iou"] for x in rows_by_fam[fam]), 5),
            "edge_ratio_max": round(max(x["edge_ratio"] for x in
                                        rows_by_fam[fam]), 4),
            "violations": _violations(rows_by_fam[fam]) or "无",
        }
        d["edge_ratio_by_angle"] = [round(x["edge_ratio"], 4)
                                    for x in rows_by_fam[fam]]
        payload["families"][fam] = d
    p = WORST_DIR / "full_circle.json"
    p.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[全圈] 读数 → {p.relative_to(ROOT)}")
    return p


def _circle_report(rows_by_fam=None, filt: "str | None" = None) -> tuple:
    """全圈单滤波器:**逐桶判据**(★权威)判 rc 的依据,同时打印分布。

    `filt=None` → 用生产默认(`torso_art._ROT_FILTER`,= sweep 选定的那个),
    **不是**写死 BILINEAR —— 否则「verify --full-circle」会悄悄判错滤波器。"""
    from neuropet.render import torso_art
    filt = filt or torso_art._ROT_FILTER
    if rows_by_fam is None:
        rows_by_fam = circle_rows((filt,))[filt]
    stats = {fam: summarize(rs) for fam, rs in rows_by_fam.items()}
    bad = {fam: _violations(rs) for fam, rs in rows_by_fam.items()}
    bad = {k: v for k, v in bad.items() if v}
    for fam, s in stats.items():
        print_circle(fam, s, filt, rows_by_fam[fam])
    _dump_circle(rows_by_fam, stats, filt)
    print(f"[全圈] 滤波器 {filt or '?'} 逐桶判据:"
          f"{'** 全部 4 族 × 360 桶通过 **' if not bad else '** 越界 ** ' + str(bad)}")
    return rows_by_fam, stats


def _ink_crop(base, new, margin: int = 6):
    """并集 α>0 的包围盒 + margin(裁到画布内):4× 放大时精灵占满画面。"""
    from PIL import ImageChops
    bb = ImageChops.lighter(base.getchannel("A"), new.getchannel("A")).getbbox()
    if bb is None:
        return base, new
    w, h = base.size
    x0, y0 = max(0, bb[0] - margin), max(0, bb[1] - margin)
    x1, y1 = min(w, bb[2] + margin), min(h, bb[3] + margin)
    return base.crop((x0, y0, x1, y1)), new.crop((x0, y0, x1, y1))


def _sheet(blocks, path: Path, title: str) -> None:
    """blocks = [(说明行, [图1, 图2, 图3]), …] → 竖排一张 PNG(注:`_font`)。"""
    from PIL import Image, ImageDraw
    font = _font()[0]
    cw = max(im.width for _l, ims in blocks for im in ims) + 10
    ch = max(im.height for _l, ims in blocks for im in ims) + 30
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))      # 仅用于量文字宽度
    tw = max([probe.textlength(title, font=font)]
             + [probe.textlength(l, font=font) for l, _ in blocks])
    sheet = Image.new("RGB", (max(cw * 3 + 6, int(tw) + 20),
                              ch * len(blocks) + 34), (246, 244, 240))
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), title, fill=(40, 40, 40), font=font)
    for i, (label, ims) in enumerate(blocks):
        y = 30 + i * ch
        d.text((8, y + 3), label, fill=(30, 30, 30), font=font)
        for c, im in enumerate(ims):
            sheet.paste(im, (3 + c * cw, y + 24))
    sheet.save(path, optimize=True)
    print(f"[worst] {path.relative_to(ROOT)}({sheet.size[0]}x{sheet.size[1]}, "
          f"{path.stat().st_size / 1024:.0f} KB)")


def _worst_label(fam: str, ang: int, m: dict) -> str:
    """一行图注:角度 + 实测数字(带宽绝对值与比值是判断「糊到什么程度」的主读)"""
    mass = m["mass_new"] / max(1, m["mass_base"])
    if _font()[1]:
        return (f"{fam} {ang:>3d}°  带宽 {m['edge_base']:.2f}→"
                f"{m['edge_new']:.2f}px  比 {m['edge_ratio']:.3f}×"
                f"(判据上限 {MAX_EDGE_WIDTH_RATIO})  |dA|均 "
                f"{m['alpha_mean_abs'] * 255:.2f}/255  >16 "
                f"{m['alpha_gt16_ratio'] * 100:.2f}%  IoU {m['alpha_iou']:.4f}  "
                f"α量比 {mass:.4f}")
    return (f"{fam} {ang:>3d}deg  band {m['edge_base']:.2f}->"
            f"{m['edge_new']:.2f}px  ratio {m['edge_ratio']:.3f}x(max "
            f"{MAX_EDGE_WIDTH_RATIO})  mean|dA| {m['alpha_mean_abs'] * 255:.2f}/255"
            f"  >16 {m['alpha_gt16_ratio'] * 100:.2f}%  IoU {m['alpha_iou']:.4f}  "
            f"mass {mass:.4f}")


def cmd_worst(zoom: int = 4, rows_max: int = 6, filt: "str | None" = None) -> int:
    """最坏角「前 / 后 / 差分」三联图(4× 最近邻放大,标注实测数字)。

    角选集 = 各族全圈边带比最大值角 ∪ IoU 最小角 ∪ {45°, 2°}(黑盒点名的抽样集
    外最坏角),按边带比降序取前 `rows_max` 行;所有图注数字由 `diff_metrics` 实测
    (区域/口径与冻结口径逐位一致,见 `_selftest_metric_equiv`)。

    产物名带滤波器后缀(`..._<filter>.png`):未带后缀的那套是最初 BILINEAR 轮的
    留档(主控已过目的那一版),本命令只写**当前/所选滤波器**的一套。"""
    from neuropet.render import torso_art
    filt = (filt or torso_art._ROT_FILTER)
    rows_by_fam, stats = _circle_report(filt=filt)
    fam_rows = {(f, round(r["angle"])): r
                for f, rs in rows_by_fam.items() for r in rs}
    picks = []
    for fam, s in stats.items():
        for ang in (float(s["edge_argmax"]), float(s["iou_argmin"]), 45.0, 2.0):
            if (fam, round(ang)) not in picks:
                picks.append((fam, round(ang)))
    picks.sort(key=lambda fa: -fam_rows[fa]["edge_ratio"])
    picks = picks[:rows_max]
    # 每族的 **IoU 最小角**(判据 1d 的最坏点)不因 rows_max 截断而漏掉 —— 视觉终判
    # 必须看到「新判据最坏的那个角」,不能只看边带比最大的角。
    for fam, s in stats.items():
        k = (fam, round(float(s["iou_argmin"])))
        if k not in picks:
            picks.append(k)
    picks.sort(key=lambda fa: -fam_rows[fa]["edge_ratio"])
    blocks, singles = [], []
    for fam, ang in picks:
        tag, mode = fam.split("_")
        tr = _traits(tag)
        if mode == "on":
            tr = dict(tr, chibi=True)
        box = sprite_box_for(tag, tr)
        base, new = domain_pair(tag, tr, float(ang), filt)
        m = fam_rows[(fam, ang)]
        label = _worst_label(fam, ang, m)
        t = _triplet(*_ink_crop(base.crop(box), new.crop(box)), zoom)
        blocks.append((label, t))
        singles.append((fam, ang, label, t))
    print(f"[worst] 滤波器 {filt};所选角(边带比降序):"
          + " | ".join(f"{f}@{a}° {fam_rows[(f, a)]['edge_ratio']:.3f}"
                       for f, a in picks))
    cols = _L(f"左=前(SS 域=C1 前) 中=后(1x 域={filt}) 右=|dA|>8 热力图"
              "(红=当前更不透明/蓝=更透明)",
              f"left=before(SS domain, pre-C1)  mid=after(1x domain, {filt})  "
              "right=|dA|>8 heat (red=more opaque / blue=more transparent)")
    sfx = filt.lower()
    for fam, ang, label, t in singles:
        _sheet([(label, t)], WORST_DIR / f"triplet_{fam}_h{ang:03d}_{sfx}.png",
               f"r25 C1 {_L('最坏角三联 4× 最近邻放大', 'worst angle triplet 4x nearest')}"
               f"  [{filt}]  {cols}")
    _sheet(blocks, WORST_DIR / f"worst_triplets_{sfx}.png",
           f"r25 C1 {_L('最坏角前/后差分对照(4× 最近邻;区域=1x 精灵画布,数字实测)',
                        'worst-angle before/after diff (4x nearest; region=1x sprite canvas)')}"
           f"  [{filt}]  {cols}")
    return 0


def cmd_full_circle(filt: "str | None" = None) -> int:
    """全圈 360 桶**逐桶判据**(★权威;落盘 worst_angles/full_circle.json)。非零退出 = 越界。"""
    from neuropet.render import torso_art
    filt = (filt or torso_art._ROT_FILTER)
    rows_by_fam, _stats = _circle_report(filt=filt)
    bad = {fam: _violations(rs) for fam, rs in rows_by_fam.items()}
    return 1 if any(bad.values()) else 0


# -------------------------------------------------------- 滤波器旋钮 sweep ----
def cold_build_times(tag: str, half: int, filt: str,
                     n: int = 60) -> "tuple[float, float]":
    """单桶**冷构建**耗时 (median, p95) ms:母图预热后连测 n 个不同角度。

    口径与 `tests/test_chibi_render.py::test_bucket_build_perf_guard` 一致
    (含 get_torso 包装;master/1x 母图烘焙不计入)。"""
    from neuropet.render import torso_art
    sid = "species." + ("cockroach" if tag == "roach" else "fruitfly")
    tr = _traits(tag)
    prev = torso_art._ROT_FILTER
    try:
        torso_art.invalidate()
        torso_art._ROT_FILTER = filt
        torso_art.get_torso(sid, half, 0.0, 1.0, tr)      # 预热:master + 1x 母图
        xs = []
        for i in range(1, n + 1):
            t0 = time.perf_counter()
            torso_art.get_torso(sid, half, float(i), 1.0, tr)
            xs.append((time.perf_counter() - t0) * 1000.0)
    finally:
        torso_art._ROT_FILTER = prev
        torso_art.invalidate()
    xs.sort()
    return xs[len(xs) // 2], xs[min(len(xs) - 1, int(len(xs) * 0.95))]


def cmd_sweep(reps: int = 3, n: int = 60) -> int:
    """滤波器旋钮的**速度 × 质量对照表** + 按硬性规则选定滤波器。

    ① 速度:冷桶构建 median/p95(蟑/蝇分开,min-of-`reps`);
    ② 质量:BILINEAR/BICUBIC/LANCZOS 各自的**全圈 4 族 × 360 桶**逐桶度量;
    选定规则(主控硬性,不得另设计):从便宜到贵依次试,取第一个满足
    「全圈 360 桶**全部** α mean|Δ| ≤1.5/255 且 >16 ≤3.5%」的;选定后边带宽比
    上限按该滤波器全圈 max + 小幅余量重定(打印建议值 → 写进文件常量)。
    落盘 `worst_angles/filter_sweep.json`。"""
    speeds = {}
    for filt in FILTERS:
        per = {}
        for tag in ("roach", "fly"):
            med, p95 = [], []
            for _ in range(reps):
                m, p = cold_build_times(tag, SPR_HALF, filt, n)
                med.append(m)
                p95.append(p)
            per[tag] = [min(med), min(p95)]
        speeds[filt] = per
    print(f"[sweep] ① 冷桶构建 median/p95 ms(min-of-{reps},n={n} 角,含 get_torso):")
    for filt in FILTERS:
        r, f = speeds[filt]["roach"], speeds[filt]["fly"]
        print(f"        {filt:<9} 蟑 {r[0]:.3f}/{r[1]:.3f}  蝇 {f[0]:.3f}/{f[1]:.3f}")
    print("        (对照 SS 域旧路径:~11.7/15.6 蟑、~1.5/2.7 蝇)"
          "  测试护栏 = median ≤4ms)")
    print("[sweep] ② 全圈逐桶度量(4 族 × 360 桶 × 3 滤波器,基线 = SS 域 = C1 前)...")
    circles = circle_rows(FILTERS)
    table = {"filters": {}, "order": list(FILTERS),
             "what": "r25 C1 滤波器旋钮的速度 × 质量对照表",
             "rule": "从便宜到贵依次试,取第一个「全圈 360 桶全部 α mean|Δ| ≤1.5/255 "
                     "且 >16 ≤3.5%」的滤波器(主控硬性规则)",
             "baseline": "SS 域路径 = C1 前语义(= REF_COMMIT b2ca945 逐位等价)"}
    chosen = None
    for filt in FILTERS:
        rows_by_fam = circles[filt]
        stats = {fam: summarize(rs) for fam, rs in rows_by_fam.items()}
        viol = {fam: _violations(rs) for fam, rs in rows_by_fam.items()}
        print(f"       [{filt}]")
        for fam, s in stats.items():
            print(f"        {fam:<10} α均 max {s['alpha_mean_max'] * 255:6.3f}/255  "
                  f">16 max {s['gt16_max'] * 100:6.2f}%  "
                  f"IoU min {s['iou_min']:.4f}  "
                  f"边带 max {s['edge_max']:.3f}@{s['edge_argmax']:.0f}°  "
                  f"α量 min {s['mass_min']:.4f}  | 越界 "
                  f"{viol[fam] or '无'}")
        rec = max(s["edge_max"] for s in stats.values())
        # 选定规则只看 ①/②(α 容差),不看 IoU/边带比 —— 后两者按选定结果重定/复核
        alpha_ok = (sum(s["n_alpha_over"] for s in stats.values()) == 0
                    and sum(s["n_gt16_over"] for s in stats.values()) == 0)
        table["filters"][filt] = {
            "speed_ms": {k: {"median": v[0], "p95": v[1]} for k, v in
                         speeds[filt].items()},
            "families": {fam: {k: v for k, v in s.items() if k != "worst"}
                         for fam, s in stats.items()},
            "violations": viol,
            "edge_ratio_max": rec,
            "alpha_criteria_ok": bool(alpha_ok),
        }
        if chosen is None and alpha_ok:
            chosen = filt
    # 建议的边带比上限 = 所选滤波器全圈 max + 小幅余量(向上取整到 2 位)
    import math as _math
    rec_cap = _math.ceil((table["filters"][chosen]["edge_ratio_max"] * 1.05
                          if chosen else 0.0) * 100) / 100.0
    table["chosen"] = chosen
    table["edge_ratio_cap_recommended"] = rec_cap
    if chosen is None:
        print("[sweep] ** 三种滤波器都不满足全圈 α 判据 → 按纪律停下回传主控 **"
              "(不得按物种分治、不得放宽阈值)")
        rc = 2
    else:
        print(f"[sweep] 选定滤波器 = ** {chosen} **(从便宜到贵第一个全圈达标的);"
              f"边带比上限建议 = {rec_cap}(全圈 max "
              f"{table['filters'][chosen]['edge_ratio_max']:.3f} + 5%)")
        rc = 0
    WORST_DIR.mkdir(parents=True, exist_ok=True)
    p = WORST_DIR / "filter_sweep.json"
    p.write_text(json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[sweep] 对照表 → {p.relative_to(ROOT)}")
    return rc


def _flag_int(rest, name: str, default: int) -> int:
    return int(rest[rest.index(name) + 1]) if name in rest else default


def _flag_str(rest, name: str, default=None):
    return rest[rest.index(name) + 1] if name in rest else default


def main() -> int:
    argv = sys.argv[1:]
    mode = argv[0] if argv and not argv[0].startswith("-") else "verify"
    rest = argv[1:] if (argv and not argv[0].startswith("-")) else argv
    if mode == "gen":
        return cmd_gen()
    if mode == "verify":
        rc = cmd_verify()
        if "--full-circle" in rest:
            print()
            filt = _flag_str(rest, "--filter", None)
            rows_by_fam, _stats = _circle_report(filt=filt)
            bad = {fam: _violations(rs) for fam, rs in rows_by_fam.items()}
            if any(bad.values()):
                rc = 1
        return rc
    if mode in ("full-circle", "fullcircle"):
        return cmd_full_circle(_flag_str(rest, "--filter", None))
    if mode == "compare":
        return cmd_compare()
    if mode == "sweep":
        return cmd_sweep(reps=_flag_int(rest, "--reps", 3),
                         n=_flag_int(rest, "--n", 60))
    if mode == "worst":
        return cmd_worst(zoom=_flag_int(rest, "--zoom", 4),
                         rows_max=_flag_int(rest, "--rows", 6),
                         filt=_flag_str(rest, "--filter", None))
    print(f"未知子命令 {mode!r};可用:gen / verify [--full-circle [--filter F]] / "
          f"full-circle [--filter F] / sweep / compare / worst [--filter F]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
