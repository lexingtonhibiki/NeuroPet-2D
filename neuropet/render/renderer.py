"""渲染器:把 pose 画成超采样抗锯齿的 RGBA 图(签名 render_pose(pose, traits) 不变)。

本文件自渲染重制第二波起为"双管线底盘"(决策记录 §4/§5,F1~F4 冻结点):

  1) hybrid 底盘(render_pose_hybrid,获胜路线 P3 增强矢量 = 混合式):
     - 躯干:每帧向 torso.get_torso()(F2 冻结接口,W2 形态资产实现,缺席时
       占位)取"已完成 heading 旋转的躯干",贴画布中心 —— 静态形态细节
       (背板/中缝/眼/静息翅/高光/滑翔翅盖)全部归 torso 侧,底盘不再画
       闭合翅盖;蟑螂滑翔时翅影与躯干变体(fold)交接逻辑保留(§4.3 修正一);
     - 腿:世界钉足的 pose.legs(身体局部点链,按 len(points) 自适应双链:
       v1 4 点 髋/膝/踝/足,或腿链 3D 波解剖链 体壁/CTr/膝/踝/跗分节…/爪尖)
       逐帧矢量,
       按 D1 §2.2 deskbug 画法:**深色描边层(本体宽 +2px)先画全部 → 再画
       全部本体层**(锥形、股节膨粗、腿色提亮 #8a5c2c 系)→ 圆关节点 →
       跗节小爪(再延伸 ~0.20×股节长,≤6 逻辑 px,不破坏钉足观感)。
       膝弯方向 前/中=-side、后=+side 的扇形规矩由 body/kinematics 的 IK
       链点给足(身体侧契约),底盘只负责"粗、黑边、圆关节"的表现;
       三对腿宽度差异化(前 0.88 / 中 1.00 / 后 1.18);
       摆动相弃用 α168 半透明(色键窗把半透明压暗成脏色、步态中闪烁),
       改实色预混(swing 色 × 深 30%,决策 §4.2-5);
     - 基节窝盖片:躯干贴画后于各髋点压一枚小实色椭圆,压住腿-体接缝;
     - 触角:pose 折线每段细分 ×3 的多节锥形(1.5→0.5 逻辑 px 渐缩);
     - 飞行翅:12 点椭圆轮廓(展弦比 ~4.5:1,过 ≥3:1 门槛)+ 根浓梢淡 α(t)
       三段带 + 3 层错相残影(α158/80/35 × fold 协调),颜色淡奶油
       (232,224,192) 系,**禁纯黑前缘**(不画深色前缘硬线);
     - 软阴影:预烘焙解析径向渐变 L 精灵(α(d)=A·(1−d²),D1 方案 A 原式,
       一次构建)→ 每帧按 altitude 缩放/调 α:scale=1/(1+h/H0)(下限 0.40)、
       α=α0·(1−0.6(h/H0)^1.3)(H0=fly_altitude,下限 0.2α0),**贴正下方、
       零水平偏移**,转抖动位图缓存(按 (尺寸,α档) LRU)。

  2) legacy 旧矢量路径(_render_vector_legacy):重制前整版逐像素保留,
     NEUROPET_RENDER=legacy(回滚开关;F4 后默认管线为 hybrid)一键回滚。

【色键硬约束 —— 本波最重要】窗口为 -transparentcolor 色键窗(键色 #010101),
任何 10≤α<255 的像素显示时与键色混合成不透明脏色(S 诊断 §五):黑色半透明
阴影显示为纯黑实心、浅色半透明翅显示为深灰板。因此 hybrid 路径的**一切半透明
效果(阴影/飞行翅/摆动腿)不依赖 PIL alpha**,统一走"抖动二色"(screen-door,
D1 §3.4 方案 A):按目标 α 用 8×8 Bayer 有序抖动把"不透明深色像素"与"完全
透明(α=0,键色)像素"交错,密度∝α;阴影色取比纯黑亮的两档
(58,52,48)/(32,28,26)。阴影抖动在超采样域做,抖动格子与 SS 块对齐
(格=SS px),整帧 reduce(SS) 后恰为 1 终态像素/格,不会再混出半透明;
飞行翅在降采样后的终态域做抖动(1px 格)。唯一允许的半透明:腿/躯干抗锯齿
边缘(reduce 自然产生的 ~1px 摩尔边),效果等价于 1px 深色描边,与拟真路线
的"边缘暗化"一致,不构成大面积色键失真。

降级阶梯(自 P2 吸收,决策 §4.2-9):底盘段滚动 60 帧 P95>6.5ms 触发
  L0 全效 → L1 残影 3→2 → L2 残影→1 → L3 SS 3→2(抖动格随 SS 重建);
P50 连续 3 次检查 <3.2ms 回弹一级(滞后)。阶梯只度量底盘段(阴影+腿+触角+
reduce),躯干成本归 F2 接口侧(torso_art 自带缓存),不互相背锅。

性能实测见文件末尾【实测记录】注释(scratch/render_matrix.py 可复测)。
"""
from __future__ import annotations

import math
import os
import time
from collections import OrderedDict, deque

from PIL import Image, ImageChops, ImageDraw

# r24:ImageChops.subtract 结果的二值化 LUT(v>0 → 255)。subtract 把负数钳到
# 0,故 sub>0 ⇔ a>b,与旧 numpy ``where(a > t)`` 逐位等价,且纯 C 级。
_GT_LUT = [0] + [255] * 255

SS = 3          # legacy 路径超采样倍数(固定 3,保证与重制前逐像素一致)
_ELL_N = 20     # 椭圆多边形边数(性能:边数与绘制耗时近似线性,20 足够光滑)


# ---------------- 路径开关(F4 冻结点:拟真度验收通过,默认改 hybrid;
# NEUROPET_RENDER=legacy 一键回滚重制前管线) ----------------
def _render_mode() -> str:
    """NEUROPET_RENDER 环境变量:hybrid(默认)| legacy(回滚)。"""
    return os.environ.get("NEUROPET_RENDER", "hybrid").strip().lower()


def render_pose(pose: dict, traits: dict) -> Image.Image:
    """对外唯一入口(签名冻结)。按 NEUROPET_RENDER 分派 hybrid / legacy。"""
    mode = _render_mode()
    if mode in ("hybrid", "3d", "3d2", "new", "v2"):
        return render_pose_hybrid(pose, traits)
    return _render_vector_legacy(pose, traits)


def render_pose_legacy(pose: dict, traits: dict) -> Image.Image:
    """legacy 路径直通(脚本/对比图用,免环境变量切换)。"""
    return _render_vector_legacy(pose, traits)


# ============================================================================
# 共享颜色/几何工具(两条路径通用)
# ============================================================================
def _rgb(c, fallback=(120, 80, 40)) -> tuple[int, int, int]:
    if isinstance(c, (tuple, list)) and len(c) >= 3:
        return (int(c[0]), int(c[1]), int(c[2]))
    try:
        c = c.lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return fallback


def _mix(c1, c2, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float, ang: float,
                 n: int = _ELL_N) -> list[tuple[float, float]]:
    ch, sh = math.cos(ang), math.sin(ang)
    pts = []
    step = 6.2831853 / n
    a = 0.0
    for _ in range(n):
        ex, ey = math.cos(a) * rx, math.sin(a) * ry
        pts.append((cx + ex * ch - ey * sh, cy + ex * sh + ey * ch))
        a += step
    return pts


def _ellipse(draw, cx, cy, rx, ry, ang, fill) -> None:
    draw.polygon(_ellipse_pts(cx, cy, rx, ry, ang), fill=fill)


def _lerp2(a, b, t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


# ============================================================================
# 抖动二色引擎(screen-door,D1 §3.4 方案 A)
#
# 原理:对目标"颜色 + 目标不透明度 D(0..255)",产出一张 0/255 掩码:
#   像素不透明  ⇔  alpha_map[x,y] * D/255 ≥ Bayer8[x%8,y%8] 阈值
# 掩码=255 处以纯色 paste(落点 α=255),掩码=0 处画布保持 α=0(键色透明)。
# 密度∝目标 α,肉眼混成半透明,物理上只有"不透明/全透明"两态 —— 色键窗零
# 混合失真。比较用 ImageChops.subtract(下限截 0)>0 判定,全程 C 级算子,
# 无逐像素 Python 循环。
# ============================================================================
_BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42), (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38), (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41), (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37), (63, 31, 55, 23, 61, 29, 53, 21),
)

# R3-A2/M3 内存收紧(记前后值 48→24):阈值图/翅掩码小缓存桶;命中率由
# 分档键稳定性保证(尺寸×格数有限),转向场景由预热线程兜底重建。
_LRU_CAP = 24  # 小型缓存(阈值图/翅掩码)条目上限(内存看守)

# OPT-04:阴影缓存硬上限(内存看守)。补丁桶由 56 收紧(原 RGBA 4B/px →
# OPT-03 改 L 掩码 1B/px,单桶 ~4× 下降;上限同时收紧,避免转向扫过时补丁数
# 暴涨;app 的 _trim_render_caches 每 5s 再裁,被裁桶由预热线程按转向方向补回)。
# R3-A2/M1+M4 内存收紧(记前后值):阴影精灵桶 22→14(±110°→±65° 全热弧,
# 蟑 171KB/片 省 ~1.2MB;大幅转向的精灵重建 ~4.5ms/次由预热线程提前补桶吸收);
# 阴影补丁桶 32→24(与 app _trim_render_caches 的裁剪口径对齐,消除 5s 窗口
# 内的超高水位,~−0.2~0.4MB)。
_SHADOW_SPRITE_CAP = 14
_SHADOW_PATCH_CAP = 24


def _lru_get(cache: OrderedDict, key, build):
    """简单 LRU:命中移尾,未命中构建,超限淘汰最旧。"""
    v = cache.get(key)
    if v is not None:
        cache.move_to_end(key)
        return v
    v = build()
    cache[key] = v
    while len(cache) > _LRU_CAP:
        cache.popitem(last=False)
    return v


_thr_maps: OrderedDict = OrderedDict()   # 键 (qw, qh, cell) → L 阈值平铺图


def _threshold_map(qw: int, qh: int, cell: int) -> Image.Image:
    """Bayer 8×8 阈值平铺图(像素=(b+0.5)*255/64);cell=抖动格边长(px)。"""
    def build():
        tile = Image.new("L", (8, 8))
        tile.putdata([int((v + 0.5) * 255 / 64) for row in _BAYER8 for v in row])
        tile = tile.resize((8 * cell, 8 * cell), Image.NEAREST)  # 格子放大到 cell px
        m = Image.new("L", (qw, qh))
        step = 8 * cell
        for yy in range(0, qh, step):
            for xx in range(0, qw, step):
                m.paste(tile, (xx, yy))
        return m
    return _lru_get(_thr_maps, (qw, qh, cell), build)


def _gt_lut() -> list[int]:
    """v>0 → 255 的 L 模式 LUT(供 ImageChops.subtract 结果二值化)。"""
    return [0] + [255] * 255


def _dither_mask_full(alpha_map: Image.Image, cell: int) -> Image.Image:
    """alpha_map(已含密度标定,0..255)对满密度 Bayer → 0/255 掩码。

    r24:原 numpy ``where(a > t)`` 改为 PIL C 级 **ImageChops.subtract +
    256 项 LUT point**:subtract 把负数钳到 0,故 sub>0 ⇔ alpha_map>thr,
    再经 LUT([0]+[255]*255,即 bin)得同语义 0/255 掩码。C 级、无 Python
    回调,与 numpy 版逐位等价,且免去 numpy 导入(冷启动 -210ms)。"""
    qw, qh = alpha_map.size
    thr = _threshold_map(qw, qh, cell)
    return ImageChops.subtract(alpha_map, thr).point(_GT_LUT)


def _dither_mask(alpha_map: Image.Image, density: int, cell: int,
                 floor: int = 0) -> Image.Image:
    """先按 density 缩放 alpha_map 再抖动(density=目标不透明度 0..255)。

    r24:缩放 + 截断改为 **Image.point(LUT)**(C 级、非 Python 回调):
    LUT[a] = min(255, int(v * k)) 截断朝零;floor>0 时 v*k<floor 记 0
    (消灭阴影外沿孤立散点的"黑刺裙边",权威规格 §4)。逐像素与 numpy 版
    等价(截断/钳顶语义一致,且 floor 比较用同一原始 v*k 浮值)。"""
    k = 255.0 / max(1, density)
    lut = [0] * 256
    for v in range(256):
        f = v * k
        lut[v] = 0 if (floor > 0 and f < floor) else min(255, int(f))
    return _dither_mask_full(alpha_map.point(lut), cell)


def _paste_masked(canvas: Image.Image, color, x: float, y: float, mask: Image.Image) -> None:
    """带裁剪的纯色+掩码粘贴(box 越出画布时裁掩码,防 paste 越界)。"""
    mw, mh = mask.size
    W, H = canvas.size
    x0, y0 = int(x), int(y)
    cx0, cy0 = max(0, -x0), max(0, -y0)
    cx1, cy1 = min(mw, W - x0), min(mh, H - y0)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    if cx0 == 0 and cy0 == 0 and cx1 == mw and cy1 == mh:
        canvas.paste(color, (x0, y0), mask)
    else:
        canvas.paste(color, (x0 + cx0, y0 + cy0), mask.crop((cx0, cy0, cx1, cy1)))


# ============================================================================
# hybrid 底盘 —— 软阴影(预烘焙渐变精灵 + 每帧缩放/调α → 抖动位图 LRU)
# ============================================================================
# 影色两档:比纯黑亮(纯黑在浅色桌面过死,且与键色过近),暖黑棕
_SHADOW_C_LO = (58, 52, 48)   # 外围稀疏档(半影)
_SHADOW_C_HI = (32, 28, 26)   # 中心稠密档(本影)

_shadow_sprites: OrderedDict = OrderedDict()   # 键 (species_id, ss) → L 渐变精灵
_shadow_patches: OrderedDict = OrderedDict()   # 键 (w, h, dens, cell) → L 三档补丁


def _shadow_dens(alpha_q: int) -> int:
    """墨量语义 alpha(0..255;权威 §4 a0=52 即中心墨量 ≈20%)→ `_dither_mask`
    的 density 参数(AG3 残留收敛修正:「腹末棋盘噪点带」根因)。

    阴影曾把 0..255 的形状图按 density=d_lo 直传 `_dither_mask`,而该函数的
    归一是**放大**(k=255/density;此数学被 tests/test_render_budget::OPT-05
    锁定为色键路径既有语义,不可改)→ 形状值 ≥d_lo 的整个核心区变成 100%
    实心墨色(对比 ~60%,远超权威 §4「最深处与桌面亮度差 ≤20%」),其 50%
    密度中环即用户在躯干后部看到的棋盘噪点带。本换算 dens=65025/alpha 使
    `_dither_mask` 的输出密度 = 形状 × alpha/255²(中心恰为 alpha/255),
    与权威 §4「α=A·max(0,1−d)^1.6」的字面语义一致;`_dither_mask` 本身零改动。"""
    a = max(1.0, float(alpha_q))
    return max(8, int(round(65025.0 / a)))


def _shadow_sprite(species_id: str, bl: float, ss: int,
                   rx_k: float, ry_k: float, head_deg: float) -> Image.Image:
    """预烘焙软阴影精灵:解析径向渐变椭圆,权威规格 §4 修订式
    α(d) = max(0, 1−d)^1.6,d=归一化椭圆半径;精灵外延 1.5×(旧 2.2×)。
    椭圆长轴沿**身体朝向**(head_deg 按 10° 桶量化缓存)——俯视投影应贴合
    体轴足迹,旧版贴屏幕轴、虫身横过来时两侧被切出深带(黑裙边根源之一)。
    合成仍走可分离路线:先得 v=255·(1−d²) 的 行/列 add 合成,再用 256 级
    LUT(point)换算到 (1−d)^1.6,最后按朝向桶旋转 —— 全程 C 级算子。"""
    def build():
        rx, ry = rx_k * bl * ss, ry_k * bl * ss
        ext = 1.5 * max(rx, ry)
        w = h = max(8, int(ext * 2))
        cx = cy = w / 2.0
        row = Image.new("L", (w, 1))
        row.putdata([max(0, min(255, round(255.0 * (1.0 - ((x + 0.5 - cx) / rx) ** 2))))
                     for x in range(w)])
        col = Image.new("L", (1, h))
        col.putdata([max(0, min(255, round(255.0 * (1.0 - ((y + 0.5 - cy) / ry) ** 2))))
                     for y in range(h)])
        q = ImageChops.add(row.resize((w, h), Image.NEAREST),
                           col.resize((w, h), Image.NEAREST),
                           scale=1.0, offset=-255)
        # LUT:p=255·(1−d²) → 255·max(0,1−d)^1.6(半影带从 ~35px 压到 ~8px)
        lut = []
        for p in range(256):
            d = math.sqrt(max(0.0, 1.0 - p / 255.0))
            lut.append(round(255.0 * max(0.0, 1.0 - d) ** 1.6))
        spr = q.point(lut)
        hb = int(round(head_deg / 10.0)) * 10      # 朝向桶 10°(旋转缓存)
        if hb % 360:
            spr = spr.rotate(-hb, resample=Image.BILINEAR)   # 方向与 pose 一致
        return spr
    return _lru_get(_shadow_sprites, (species_id, ss, rx_k, ry_k,
                                      int(round(head_deg / 10.0)) * 10), build)


def _draw_shadow_hybrid(canvas: Image.Image, species_id: str, roach: bool,
                        alt: float, bl: float, ss: int, traits: dict,
                        head_deg: float = 0.0) -> None:
    """软阴影:贴正下方、随 altitude 缩小减淡、抖动二色合成(权威规格 §4)。
    精灵半轴 rx/ry 按 traits(shadow_rx/shadow_ry,×BL)参数化:
    蟑螂 0.40×0.21(=腿足印的 ~88%,影不大于足印)、果蝇 0.17×0.09(几乎不可见);
    中心墨量 a0:蟑螂 52(旧 96 过深)/ 果蝇 22(旧 78 → "刺裙");经
    `_shadow_dens` 换算为抖动密度后中心密度 = a0/255 ≈ 20%(AG3 修正:旧路径
    密度语义放大,核心 100% 实心墨 → 对比 ~60% 的"棋盘噪点带",见该函数注释)。
    本影档 = 深芯区域(形状值 ≥3/4 满幅)同密度换深墨 C_HI(两色分档,不再叠密度);
    低密度截断 <26 → 0(密度 <10% 直接不画,消灭孤立散点;权威 §4 字面。
    静息态可见外沿随之收进体剪影内 → 贴地感由体侧 1px 暗缘承担,同 §4;)
    方向性:gx=+0.02BL、gy=+0.06BL(光源左上,下缘略重,不靠加深)。
    scale(h)=1/(1+h/H0)(下限 0.40);α(h)=α0·(1−0.6(h/H0)^1.3)(下限 0.2α0)。
    尺寸/α 量化分档命中补丁缓存(LRU≤24,R3-A2 收紧,内存看守);补丁=已烘焙
    的两色抖动 RGBA(α∈{0,255}),每帧仅一次 paste,缓存命中后抖动图逐帧静止。
    【SS 块对齐】尺寸取 ss 整数倍、粘贴原点对齐 SS 网格:抖动格(cell=ss px)
    与 reduce(SS) 的盒式降采样格严格重合,降采样后 1 终态像素/格、不再混出
    半透明(未对齐时格跨块混色 → 半透明脏色,实测蟑螂飞行格半透明占比 5.4%)。"""
    alt = max(0.0, alt)
    h0 = float(traits.get("fly_altitude", 38 if roach else 55)) or 40.0
    a0 = float(traits.get("shadow_alpha", 52 if roach else 22))
    rx_k = float(traits.get("shadow_rx", 0.40 if roach else 0.17))
    ry_k = float(traits.get("shadow_ry", 0.21 if roach else 0.09))
    if roach:
        # 滑翔 v2 阴影重校(§3.2,仅蟑螂;果蝇路径逐位不变):
        # scale=1/(1+alt/60)、α=a0/(1+alt/40)。下限 26 = 抖动密度 10% 截断线
        # (floor)的最小可渲染墨量——低于它的 α 会被 floor 二次吞掉(整影消失),
        # 故钳到"最淡可渲染"档;alt=0 时 α=a0 与旧式逐位一致。
        scale = max(0.40, 1.0 / (1.0 + alt / 60.0))
        alpha = max(26.0, a0 / (1.0 + alt / 40.0))
    else:
        t = min(1.4, alt / h0)
        scale = max(0.40, 1.0 / (1.0 + alt / h0))
        alpha = max(0.20 * a0, a0 * (1.0 - 0.6 * (t ** 1.3)))
    spr = _shadow_sprite(species_id, bl, ss, rx_k, ry_k, head_deg)
    # 朝向桶 LRU(PF 调优:12→22;R3-A2/M1 收紧 22→14 ≈ ±65° 弧全热,
    # 省 ~1.2MB):10° 桶共 36 个,宠物摆动转向会反复跨桶,精灵重建
    # ~4.5ms/次由预热线程提前补桶吸收;内存上限 ~2.4MB(蟑螂 414² L 精灵
    # 171KB/片,果蝇 ~2KB/片)。
    while len(_shadow_sprites) > _SHADOW_SPRITE_CAP:
        _shadow_sprites.popitem(last=False)
    w0, h0p = spr.size
    step_w = max(ss, round(0.04 * w0 / ss) * ss)     # 档距 ~4% 且为 ss 倍数
    step_h = max(ss, round(0.04 * h0p / ss) * ss)
    w = min(w0, max(ss, int(round(w0 * scale / step_w)) * step_w))
    h = min(h0p, max(ss, int(round(h0p * scale / step_h)) * step_h))
    a_q = max(8, int(alpha) // 8 * 8)                # 墨量 8 步量化 → 补丁键稳定
    dens = _shadow_dens(a_q)                         # 墨量 → 抖动密度参数
    floor = 26                                       # 密度 <10% 直接画 0(权威 §4 字面)
    cell = ss   # 抖动格子与 SS 块对齐:reduce 后 1 终态像素/格
    hb = int(round(head_deg / 10.0)) * 10
    key = (w, h, dens, cell, hb)
    tier = _shadow_patches.get(key)
    if tier is None:
        # OPT-03:烘焙成单张 L 三档掩码(0=透/1=半影 C_LO/2=本影 C_HI),
        # 1B/px 替代原 RGBA(4B/px),省 ~4×;合成时按常数色 tint 分档 paste。
        amap = spr.resize((w, h), Image.BILINEAR)
        lo = _dither_mask(amap, dens, cell, floor=floor)
        if roach:   # 果蝇单档:低密度下两档深色叠加正是"脏裙"感(权威 §4)
            # 本影档 = 深芯(形状 ≥3/4 满幅)**同密度**换深墨 C_HI。旧实现按
            # α×1.25 提密度:hi 掩码逐点 ⊇ lo 掩码,C_LO 档被整体覆盖,
            # 「两档」实际从未同时可见,且叠加后核心超权威 ≤20% 对比门槛。
            core = amap.point([0 if v < 191 else 255 for v in range(256)])
            hi = _dither_mask(core, dens, cell)
            # tier = (hi>0 & lo>0)→2, lo>0→1, else 0(权威三档语义)。
            # r24:先把两掩码归一为 0/1(LUT),再 add(饱和加,0/1/2),逐位
            # 等价旧 numpy where 链,且全 C 级。
            tier = ImageChops.add(lo.point([0] + [1] * 255),
                                  hi.point([0] + [1] * 255))
        else:
            tier = lo.point([0] + [1] * 255)
        _shadow_patches[key] = tier
        # OPT-04:补丁桶上限由 56 收紧到 _SHADOW_PATCH_CAP(转向扫过时的冗余
        # 补丁数上限);app 的 _trim_render_caches 每 5s 再裁到 ~24 桶。
        while len(_shadow_patches) > _SHADOW_PATCH_CAP:
            _shadow_patches.popitem(last=False)
    # 地面投影点 = 身体正下方 + 下缘略重的方向偏移(不随 lift 抬升);对齐 SS 网格
    gx = canvas.size[0] * 0.5 + 0.02 * bl * ss
    gy = canvas.size[1] * 0.5 + 0.06 * bl * ss
    x0 = int(round((gx - w / 2) / ss)) * ss
    y0 = int(round((gy - h / 2) / ss)) * ss
    W, H = canvas.size
    cx0, cy0 = max(0, -x0), max(0, -y0)
    cx1, cy1 = min(w, W - x0), min(h, H - y0)
    if cx1 > cx0 and cy1 > cy0:
        # OPT-03:从 L 三档掩码按常数色 tint 合成(每帧 1~2 次 paste);
        # 画布越界时裁掩码并对齐裁区,防 paste 越界。画质与改前逐位等价
        # (lo 档覆盖半影+本影区、hi 档仅本影核心,与原先分两档 paste 一致)。
        region = tier if (cx0 == 0 and cy0 == 0 and cx1 == w and cy1 == h) \
            else tier.crop((cx0, cy0, cx1, cy1))
        # r24:numpy where 比较改 LUT point(纯 C 级):lo = (≥1)→255,
        # hi = (==2)→255;tier 取值仅 0/1/2,故 LUT 直查逐位等价。
        # r24 性能:mask 只依赖 region 像素(≤_SHADOW_PATCH_CAP 种内容;未裁时
        # region is tier 即缓存对象)→ 按 id(region) 缓存,稳态免掉每帧两次
        # 全幅 L→L point(蟑螂阴影 @SS=3 达 1242²,是阴影段大头)。
        ck = id(region)
        cm = _SHADOW_MASK_CACHE.get(ck)
        if cm is None or cm[0] is not region:
            cm = (region, region.point([0] + [255] * 255),
                  region.point([0, 0] + [255] * 254) if roach else None)
            if len(_SHADOW_MASK_CACHE) > 160:
                _SHADOW_MASK_CACHE.clear()
            _SHADOW_MASK_CACHE[ck] = cm
        canvas.paste(_SHADOW_C_LO, (x0 + cx0, y0 + cy0), cm[1])
        if roach and cm[2] is not None:
            canvas.paste(_SHADOW_C_HI, (x0 + cx0, y0 + cy0), cm[2])


# ============================================================================
# hybrid 底盘 —— 阴影预热(跟随线程用;与逐帧路径同一实现,缓存逐位等价)
# ============================================================================
def shadow_bucket_ready(species_id: str, traits: dict, heading_deg: float) -> bool:
    """地面软阴影(alt=0 组合)的精灵与补丁是否都已在缓存。只读不构建。

    注意 heading_deg 必须与渲染路径同源(原始度数,不取模):阴影精灵/补丁
    缓存键沿用 _draw_shadow_hybrid 的 10° 桶原值(不 %360)。"""
    roach = bool(traits.get("wing_cover"))
    ss = 2 if _hy_level >= 3 else 3
    rx_k = float(traits.get("shadow_rx", 0.40 if roach else 0.17))
    ry_k = float(traits.get("shadow_ry", 0.21 if roach else 0.09))
    bl = float(traits.get("body_len", 115 if roach else 30))
    hb = int(round(heading_deg / 10.0)) * 10
    spr = _shadow_sprites.get((species_id, ss, rx_k, ry_k, hb))
    if spr is None:
        return False
    w0, h0p = spr.size
    step_w = max(ss, round(0.04 * w0 / ss) * ss)
    step_h = max(ss, round(0.04 * h0p / ss) * ss)
    w = min(w0, max(ss, int(round(w0 / step_w)) * step_w))       # scale=1(贴地)
    h = min(h0p, max(ss, int(round(h0p / step_h)) * step_h))
    a0 = float(traits.get("shadow_alpha", 52 if roach else 22))
    dens = _shadow_dens(max(8, int(a0) // 8 * 8))    # 与 _draw_shadow_hybrid 同源
    return (w, h, dens, ss, hb) in _shadow_patches


def prewarm_shadow(species_id: str, traits: dict, heading_deg: float) -> None:
    """预热一个 10° 朝向桶的地面软阴影精灵+补丁(贴地 alt=0 组合)。

    供 App 跟随线程在宠物转向前方调用:阴影精灵重建 ~4.5ms 是应用段 P95
    的另一尖刺源。直接复用 _draw_shadow_hybrid(同一实现 → 缓存内容逐位
    等价),画在即弃画布上;已缓存时仅浪费一次画布分配(0.3ms/次)。"""
    roach = bool(traits.get("wing_cover"))
    if shadow_bucket_ready(species_id, traits, heading_deg):
        return
    bl = float(traits.get("body_len", 115 if roach else 30))
    ss = 2 if _hy_level >= 3 else 3
    half = 120 if roach else 78
    img = Image.new("RGBA", (half * 2 * ss, half * 2 * ss), (0, 0, 0, 0))
    _draw_shadow_hybrid(img, species_id, roach, 0.0, bl, ss, traits, heading_deg)


# ============================================================================
# hybrid 底盘 —— 腿(D1 §2.2 deskbug 画法:描边层→本体层、锥形、圆关节、跗节)
# ============================================================================
# 三段逻辑宽(px):股节(髋→膝)/ 胫节(膝→踝)/ 跗节+爪;三对差异化系数 pair。
# 蟑螂(权威 §5.2):(3.6,2.2,1.5,0.9),pair 前 0.80/中 0.95/后 1.15(后足最粗);
# 描边加宽 2.0→1.2(旧 2.0 深边吞掉 3.4px 股节的内芯=黑棒主因);腿色亮棕
# #933c10 系(旧 #8a5c2c 仍偏"蜘蛛")。
# 果蝇(权威 §5.2 专档):seg(0.9,0.8,0.7,0.5) 全部 ≤1 终态 px;flat=True:
# 跳过描边层/关节圆/股节膨粗,单色单线琥珀棕(足是浅琥珀细线,不是黑棒)。
# 解剖链(≥7 点)新增宽(腿链 3D 波,规格 §4.5):coxa_w 基节短粗
# (蟑 2.8/蝇 0.8,短节藏体缘)、tarsus_w=(w0,w1) 跗链渐细
# (蟑 (1.2,0.6)/蝇 (0.7,0.4),≤1.2px 线不加描边);serp=跗分节蛇形微弯
# (装饰,规格 §1.2 跗摆 ±8° ROM;蟑螂启用、果蝇 flat 跳过)。
_SPEC_LEG = {
    True:  {"seg": (3.6, 2.2, 1.5, 0.9), "pair": (0.80, 0.95, 1.15),
            "core": "#933c10", "edge": "#5a2e14", "swing": "#6b4a2a",
            "claw": 9.0, "spine": "#4a2a12", "spine_n": 3,
            "coxa_w": 2.8, "tarsus_w": (1.2, 0.6), "serp": True,
            # 基节缘板半长轴(前/中/后,逻辑 px;腿部3D规格 §7.2 缘板宽
            # 0.040/0.045/0.055 BL ×115;只被 ≥7 点解剖链的缘板绘制消费)
            "plate_w": (4.6, 5.2, 6.33)},
    False: {"seg": (0.9, 0.8, 0.7, 0.5), "pair": (0.9, 1.0, 1.1),
            "core": "#9c7443", "edge": "#9c7443", "swing": "#7a5a33",
            "claw": 2.0, "spine": None, "spine_n": 0, "flat": True,
            "coxa_w": 0.8, "tarsus_w": (0.7, 0.4), "serp": False},
}


# ============================================================================
# E3/C1 Q 版(chibi)渲染半场(调研 §二 参数表;全部绘制层系数,零 body/步态
# 改动,零重训)。chibi=False(traits 无 "chibi" 键)时以下全部不生效 ——
# 既有路径逐位一致。traits["chibi"] 由 core/app.py _render_traits 注入。
#   腿:节段视觉 ×0.60 + 直径 ×1.8(短圆)。钉足契约不改:支撑相爪尖保持
#       pose 原位(不滑步),中间关节向髋径向压缩 0.60 → 节段读感变短、
#       膝弯更"团";摆动相(swing)整链含爪尖一并 ×0.60 → 步幅视觉 ×0.60
#       (stride 视觉缩短走绘制层,poses/gait 参数零改动)。
#   触须:长 ×0.45、线宽 ×1.8、节 9→7(弧长均匀重采样,基/梢端保真)。
# ============================================================================
CHIBI_LEG_LEN = 0.60
CHIBI_LEG_DIAM = 1.8
CHIBI_ANT_LEN = 0.45
CHIBI_ANT_W = 1.8
CHIBI_ANT_NODES = 7
CHIBI_STRIDE = 0.60      # = CHIBI_LEG_LEN(摆动整链压缩系数;参数表 stride ×0.60)


def chibi_leg_points(points: list, swing: bool) -> list:
    """Q 版腿链重映射:中间关节向髋(pts[0])径向压缩 CHIBI_LEG_LEN;
    支撑相末点(爪尖=步态钉点)保位,摆动相末点一并压缩 CHIBI_STRIDE
    (步幅视觉缩短;<3 点链原样返回)。纯函数,探针/测试直测。"""
    pts = list(points)
    if len(pts) < 3:
        return pts
    hx, hy = pts[0]
    k = CHIBI_LEG_LEN

    def c(p):
        return (hx + (p[0] - hx) * k, hy + (p[1] - hy) * k)
    last = len(pts) - 1
    out = [pts[0]] + [c(p) for p in pts[1:last]]
    out.append(c(pts[last]) if swing else pts[last])
    return out


def chibi_antenna(points: list) -> list:
    """Q 版触须:整链向基端(pts[0])压缩 CHIBI_ANT_LEN,再弧长均匀重采样到
    CHIBI_ANT_NODES 节点(9→7;基端/梢端保真,渐缩宽度公式按新节点数取 t)。"""
    pts = list(points)
    if len(pts) < 2:
        return pts
    bx, by = pts[0]
    k = CHIBI_ANT_LEN
    pts = [(bx + (p[0] - bx) * k, by + (p[1] - by) * k) for p in pts]
    n = CHIBI_ANT_NODES
    if len(pts) <= n:
        return pts
    ds = [0.0]
    for a, b in zip(pts, pts[1:]):
        ds.append(ds[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = ds[-1] or 1.0
    out = [pts[0]]
    j = 0
    for m in range(1, n - 1):
        target = total * m / (n - 1)
        while j < len(ds) - 2 and ds[j + 1] < target:
            j += 1
        seg = ds[j + 1] - ds[j] or 1e-6
        t = (target - ds[j]) / seg
        out.append((pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
                    pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t))
    out.append(pts[-1])
    return out


def _stroke_chain(draw, segs: list, widen: float, col, s: float) -> None:
    """锥形折线:segs=[(pa, pb, wa, wb)](逻辑坐标),画(宽+widen)双层之一。
    每段拆 2 子线近似线性锥;调用方负责端点圆关节(pass 内统一画)。"""
    fill = col + (255,)
    for pa, pb, wa, wb in segs:
        mid = ((pa[0] + pb[0]) * 0.5, (pa[1] + pb[1]) * 0.5)
        draw.line([pa, mid], fill=fill, width=max(1, int((wa + widen) * s)))
        draw.line([mid, pb], fill=fill, width=max(1, int(((wa + wb) * 0.5 + widen) * s)))


def _draw_legs_hybrid(draw, rot, legs: list, traits: dict, roach: bool,
                      s: float, drag: dict | None = None) -> None:
    """六腿矢量(按 len(leg["points"]) 自适应双链 —— 腿链 3D 波,规格 §4.5):

    - 4 点链(v1:果蝇/手工 pose/NEUROPET_LEG3D=0 回退)=[髋,膝,踝,足]:
      行为与改前逐位一致(锥形三段+膨粗+刺+跗端爪外伸);
    - ≥7 点解剖链(体壁→CTr→膝→踝→跗分节…→爪尖;现 v2 7 点与规格 §1.2
      契约 8 点共用一套,跗链 = pts[3:] 逐对连线):
      * 基节 pts[0]→[1] 短粗(coxa_w,描边+本体两 pass,藏体缘);
      * 股 pts[1]→[2] 最粗 + 膨粗椭圆取**股中点**;
      * 胫 pts[2]→[3](刺沿膝→踝,_spines 逻辑平移索引;股节远端刺迁
        CTr→膝);
      * 跗链 pts[3]→…→爪尖 仅本体 pass 渐细(tarsus_w,≤1.2px 线不加描边)
        + 爪尖钉点(tip=pts[-1]=步态钉点,不再超程,规格 §2.0);
      * 关节圆点平移到 CTr/膝/踝;摆动相(lift>0.1)整链 swing 色。
      跗分节「蛇形微弯」(装饰,规格 §1.2 跗摆 ±8° ROM):跗链内关节点沿
      腿轴外侧交替偏 ~0.3px(方向按腿侧镜像,与刺同侧规矩;蟑螂启用、
      果蝇 flat 跳过;爪尖不偏 —— 钉点保真)。以关节点偏移(而非段中点
      偏移)表达同一蛇形观感,换取跗链每段 1 线 —— 守规格 §6-6
      「跗链 line 增量 ≤30/帧」(段中点偏移需每段 2 线,增量超界)。
    果蝇 flat:单色单线,无描边/关节圆/膨粗(权威 §5.2);解剖链合并
    4 stroke(coxa/股/胫锥形 + 跗整链 polyline,中跗点不单独成线),
    ≤8 line/腿(实为 7),保 3.5ms 预算。蟑螂仍「先画全部描边、再画全部
    内芯」(P3:防相邻腿描边切断内芯);摆动相用实色预混 swing 色
    (色键合规,决策 §4.2-5)。"""
    spec = _SPEC_LEG[roach]
    pair_f = spec["pair"]
    flat = bool(spec.get("flat"))
    # E3/C1 Q 版:直径 ×1.8 + 节段视觉 ×0.60(短圆);门控,关闭态 wd=dk=1.0
    chibi = bool(traits.get("chibi"))
    wd = CHIBI_LEG_DIAM if chibi else 1.0            # 直径系数
    core_c = _rgb(traits.get("leg_core", spec["core"]))
    edge_c = _rgb(traits.get("leg_edge", spec["edge"]))
    # 摆动相:swing 色压暗 30% 的实色预混(色键合规,决策 §4.2-5)
    swing_core = _mix(_rgb(traits.get("legs_swing", spec["swing"])), (0, 0, 0), 0.30)
    swing_edge = _mix(edge_c, (0, 0, 0), 0.15)
    spine_c = _rgb(spec["spine"]) + (255,) if spec.get("spine") else None
    claw_cap = float(spec.get("claw", 6.0))
    # 解剖链宽(规格 §4.5):基节短粗 / 跗链渐细;serp=蛇形微弯开关
    coxa_w = float(spec.get("coxa_w", spec["seg"][0])) * wd
    tar_w = spec.get("tarsus_w", (spec["seg"][2], spec["seg"][3]))
    tar_w = (tar_w[0] * wd, tar_w[1] * wd)
    serp = bool(spec.get("serp"))

    # prepared 条目:(segs, joints[(pt,w)], is_swing, bulge(pt,ang,w),
    #                刺锚点(hp,kp,ap,tw,fw,i), tarsus(chain,w0,w1)|None)
    prepared = []
    for i, leg in enumerate(legs):
        pts = [rot(p) for p in leg["points"]]   # SS 画布坐标(含 lift 抬升)
        if len(pts) < 4:
            continue
        if chibi:
            # Q 版:节段视觉 ×0.60(支撑爪尖保位/摆动整链压缩=步幅 ×0.60)
            pts = chibi_leg_points(pts, float(leg.get("lift", 0.0)) > 0.1)
        if drag is not None:
            # ---- 拖拽动量滞后(A4/r10,仅拖拽态;非拖拽路径逐位不变) ----
            # r10 拖拽力学 v2:drag["legs"] 提供每腿独立偏角(度,来自
            # physics/drag.DragDynamics 的关节 DOF 钳制连续解);缺省回退
            # 旧 A4 单向量投影:δ_i = clamp(G_i·(d·n̂_i), ±35°)。
            if "legs" in drag:
                delta = math.radians(max(-35.0, min(
                    35.0, float(drag["legs"][i % len(drag["legs"])]))))
            else:
                dvec = drag["d"]
                g = drag["gain"][i % 3] if len(legs) == 6 else 1.0
                ax_, ay_ = drag["axes"][i % len(drag["axes"])]
                delta = math.radians(max(-35.0, min(
                    35.0, g * (dvec[0] * -ay_ + dvec[1] * ax_))))
            if delta:
                c_, s_ = math.cos(delta), math.sin(delta)
                hx, hy = pts[0]
                pts = [pts[0]] + [(hx + (qx - hx) * c_ - (qy - hy) * s_,
                                   hy + (qx - hx) * s_ + (qy - hy) * c_)
                                  for qx, qy in pts[1:]]
        kf = pair_f[i % 3] if len(legs) == 6 else 1.0
        w = [v * kf * wd for v in spec["seg"]]                   # 段端宽(Q 版×1.8)
        is_swing = float(leg.get("lift", 0.0)) > 0.1
        if len(pts) >= 7:
            # ---- 解剖链(现 v2 7 点 / 规格 §1.2 契约 8 点;跗链=pts[3:]) ----
            attach, ctr, knee, ankle = pts[0], pts[1], pts[2], pts[3]
            chain = list(pts[3:])            # 踝→跗分节…→爪尖(=步态钉点)
            if serp and len(chain) >= 3:
                # 蛇形微弯(±8° ROM):内关节沿腿轴外侧交替偏 ~0.3px;
                # 0.3px/段长 ≈ 5~9°,方向按腿侧镜像(法向取体侧外侧,
                # 与胫刺同规矩);装饰在渲染层,不改 IK 锚点/钉点。
                lside = -1.0 if i < 3 else 1.0
                for j in range(1, len(chain) - 1):
                    dx = chain[j][0] - chain[j - 1][0]
                    dy = chain[j][1] - chain[j - 1][1]
                    dn = math.hypot(dx, dy) or 1.0
                    nx_, ny_ = -dy / dn, dx / dn
                    if ny_ * lside < 0:      # 法向取腿轴外侧的一支
                        nx_, ny_ = -nx_, -ny_
                    off = 0.3 * s * (1.0 if j % 2 else -1.0)   # 段间交替
                    chain[j] = (chain[j][0] + nx_ * off,
                                chain[j][1] + ny_ * off)
            segs = [(attach, ctr, coxa_w * kf, coxa_w * 0.86 * kf),
                    (ctr, knee, w[0], w[1]),
                    (knee, ankle, w[1], w[2])]
            bmx, bmy = (ctr[0] + knee[0]) * 0.5, (ctr[1] + knee[1]) * 0.5
            bang = math.atan2(knee[1] - ctr[1], knee[0] - ctr[0])
            prepared.append((segs,
                             ((ctr, coxa_w * kf), (knee, w[1]), (ankle, w[2])),
                             is_swing, (bmx, bmy, bang, w[0]),
                             (ctr, knee, ankle, w[1], w[0], i),
                             (chain, tar_w[0] * kf, tar_w[1] * kf)))
        else:
            # ---- v1 4 点链:以下与改前逐位一致 ----
            femur = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            # 跗端小爪:沿踝→足方向再延伸 ~0.06×股节(≤上限),足端不滑。
            # 解剖口径:蟑螂前跗节(双爪+中垫)实测 ≈0.3~0.5mm,对 8~11mm 股节
            # ≈4~6%(R2 复刻校准;旧值 0.20 是卡通大爪,把"足"预算吃掉一半,
            # 导致跗节参数被迫压到失真的小值)。解剖链的爪已入链,不走此外伸。
            claw = min(claw_cap * s, 0.06 * femur)
            dux, duy = pts[3][0] - pts[2][0], pts[3][1] - pts[2][1]
            dn = math.hypot(dux, duy) or 1.0
            tip = (pts[3][0] + dux / dn * claw, pts[3][1] + duy / dn * claw)
            segs = [(pts[0], pts[1], w[0], w[1]),
                    (pts[1], pts[2], w[1], w[2]),
                    (pts[2], tip, w[2], w[3])]
            bmx, bmy = (pts[0][0] + pts[1][0]) * 0.5, (pts[0][1] + pts[1][1]) * 0.5
            bang = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
            prepared.append((segs,
                             ((pts[0], w[0]), (pts[1], w[1]), (pts[2], w[2])),
                             is_swing, (bmx, bmy, bang, w[0]),
                             (pts[0], pts[1], pts[2], w[1], w[0], i),
                             None))
    if flat:
        # ---- 果蝇 flat 路线:单色单线,无描边/关节圆/膨粗(权威 §5.2) ----
        for segs, _joints, is_swing, _bulge, _sp, tars in prepared:
            col = swing_core if is_swing else core_c
            if tars is None:
                _stroke_chain(draw, segs, 0.0, col, s)
            else:
                # 解剖链合并 4 stroke:coxa/股/胫(锥形子线)+ 跗整链 polyline
                # (中跗点不单独成线;≤8 line/腿 → 48/帧,保 3.5ms 预算)
                _stroke_chain(draw, segs, 0.0, col, s)
                tchain, tw0, _tw1 = tars
                draw.line(tchain, fill=col + (255,), width=max(1, int(tw0 * s)))
        return
    # ---- pass 1:全部描边层(本体宽 +1.2px,深色,先画全部;旧 +2.0 吞内芯) ----
    for segs, joints, is_swing, _bulge, _sp, _tars in prepared:
        col = swing_edge if is_swing else edge_c
        _stroke_chain(draw, segs, 1.2, col, s)
        for p, w_end in joints:
            r = max(1.0, (w_end + 1.2) * 0.5 * s)
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=col + (255,))
    # ---- pass 2:全部本体层(锥形 + 股节膨粗 + 圆关节 + 跗链/爪尖) ----
    for segs, joints, is_swing, bulge, _sp, tars in prepared:
        col = swing_core if is_swing else core_c
        _stroke_chain(draw, segs, 0.0, col, s)
        for p, w_end in joints:
            r = max(1.0, w_end * 0.5 * s)
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=col + (255,))
        # 股节膨粗:股节中段沿方向的一枚横椭圆(鼓节观感,治"等宽细线";
        # 解剖链取**股中点** CTr→膝,规格 §4.5)
        bmx, bmy, bang, bw = bulge
        _ellipse(draw, bmx, bmy, bw * 0.78 * s, bw * 0.48 * s, bang, col + (255,))
        if tars is not None:
            # 跗链(含蛇形微弯):仅本体 pass 渐细(≤1.2px 线不加描边),
            # 每段 1 线;末点=爪尖=步态钉点(解剖链不再外伸超程,规格 §2.0),
            # 爪尖压一粒深色小圆点作"钉点"记
            tchain, tw0, tw1 = tars
            nseg = len(tchain) - 1
            fill = col + (255,)
            for k in range(nseg):
                wt = max(1, int((tw0 + (tw1 - tw0) * (k + 0.5) / nseg) * s))
                draw.line([tchain[k], tchain[k + 1]], fill=fill, width=wt)
            cr = max(1.0, 0.7 * s)
            cx_, cy_ = tchain[-1]
            draw.ellipse([cx_ - cr, cy_ - cr, cx_ + cr, cy_ + cr],
                         fill=_mix(col, (0, 0, 0), 0.30) + (255,))
    # ---- pass 3:胫节 + 股节远端刺(AG3 残留收敛重排:稀疏、有方向性、远端渐长)。
    # 旧画法:胫节全程 t∈{0.25,0.5,0.75} 双侧等距等长(3px)垂直刺 —— 视觉上
    # 读成"股/胫全程均匀密布的梯子横杠"。真实美洲大蠊(权威 §1:胫刺每侧
    # 4~6 枚、≈2px;照片·侧:股节刺列集中在**远端**):股节只留远端 2 枚短刺;
    # 胫节外侧 4 枚/内侧 3 枚(内外错相,消对称梯子感)、近端短远端长、向跗端
    # 前倾 ~35°(耙状方向感);刺自腿缘起笔,不再横穿腿轴成暗色断节。
    # 色取权威 §5.2 刺色 #4a2a12(旧 #33200e 过黑)。~66 line/帧,与旧同量级。
    if spine_c is not None:
        tilt = math.radians(35.0)
        ct, st_ = math.cos(tilt), math.sin(tilt)
        for _segs, _joints, is_swing, _bulge, (hp, kp, ap, tw, fw, i), _tars in prepared:
            col = _mix(spine_c, (0, 0, 0), 0.15) if is_swing else spine_c
            lside = -1.0 if i < 3 else 1.0       # 前 3 条=体轴左侧(screen y<0)

            def _spines(p_from, p_to, hw, ts, lens):
                dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
                n = math.hypot(dx, dy) or 1.0
                ux, uy = dx / n, dy / n          # 指向远端(胫节→踝 / 股节→膝)
                nx_, ny_ = -uy, ux               # 腿轴法向
                if ny_ * lside < 0:              # 取指向体侧外侧的一支
                    nx_, ny_ = -nx_, -ny_
                for sgn, ts_s, ln_s in ((1.0, ts[0], lens[0]),
                                        (-1.0, ts[1], lens[1])):
                    for k, t in enumerate(ts_s):
                        bx = p_from[0] + dx * t
                        by = p_from[1] + dy * t
                        ox, oy = nx_ * sgn, ny_ * sgn
                        # 刺方向 = 法向 cos35° + 远端轴向 sin35°(耙状前倾)
                        ddx = ox * ct + ux * st_
                        ddy = oy * ct + uy * st_
                        p0 = (bx + ox * (hw - 0.3) * s, by + oy * (hw - 0.3) * s)
                        draw.line([p0, (p0[0] + ddx * ln_s[k] * s,
                                        p0[1] + ddy * ln_s[k] * s)],
                                  fill=col, width=max(1, int(1.0 * s)))

            # 胫节(膝→踝):外侧 4 枚 / 内侧 3 枚错相,2.1→3.3px 远端渐长
            _spines(kp, ap, tw * 0.5,
                    ((0.28, 0.52, 0.73, 0.90), (0.40, 0.63, 0.82)),
                    ((2.1, 2.5, 2.9, 3.3), (2.3, 2.8, 3.2)))
            # 股节只留远端 2 枚短刺(4 点链 髋→膝 / 解剖链 CTr→膝,索引平移;
            # 真实刺列集中股节远端)
            _spines(hp, kp, fw * 0.5,
                    ((0.72, 0.90), (0.80, 0.95)),
                    ((2.0, 2.2), (2.0, 2.2)))


# ============================================================================
# hybrid 底盘 —— 触角(pose 折线细分 ×3 的多节锥形)
# ============================================================================
def _draw_antennae_hybrid(draw, rot, antennae: list, traits: dict, s: float,
                          roach: bool, drag: dict | None = None) -> None:
    """触角多节锥形:蟑螂 (1.3→0.4) 逻辑 px 渐缩(旧 1.5→0.5 偏粗)、色 #6b4a3a
    渐淡系(旧 #241407 近黑=脏);果蝇 ≤1 终态 px 细线。梢端圆点删除
    (真梢端极细,圆点读成"小锤")。
    drag(可选,仅拖拽态):触须鞭状滞后 —— 基端 δ_b=clamp(ant_gain·(ant·n̂),±20°)、
    梢端 δ_t=clamp(ant_gain·tip_ratio·(ant_tip·n̂),±30°),ant_tip 为 body 侧
    再滞后滤波状态(相位延迟 40~80ms);沿鞭自基向梢逐点插值旋转。"""
    col = _rgb(traits.get("antenna", "#6b4a3a" if roach else "#3a2812")) + (255,)
    w0, w1 = (1.3, 0.4) if roach else (0.9, 0.4)
    # E3/C1 Q 版:触须长 ×0.45、线宽 ×1.8、节 9→7(门控;关闭态逐位不变)
    chibi = bool(traits.get("chibi"))
    if chibi:
        w0, w1 = w0 * CHIBI_ANT_W, w1 * CHIBI_ANT_W
    for _ai, ant in enumerate(antennae):
        if len(ant) < 2:
            continue
        pts = [rot(p) for p in ant]
        if chibi:
            pts = chibi_antenna(pts)
        if drag is not None and len(pts) >= 2:
            bx, by = pts[0]
            tx, ty = pts[-1]
            dn = math.hypot(tx - bx, ty - by) or 1.0
            ax_, ay_ = (tx - bx) / dn, (ty - by) / dn
            if "ant_deg" in drag:   # r10:每须独立基端/梢端角(度)
                ad = drag["ant_deg"]; atd = drag["ant_tip_deg"]
                pb = max(-20.0, min(20.0, float(ad[_ai % len(ad)])))
                pq = max(-30.0, min(30.0, float(atd[_ai % len(atd)])))
            else:
                ag = float(drag["ant_gain"])
                tr = float(drag["ant_tip_ratio"])
                dav = drag["ant"]
                dtv = drag["ant_tip"]
                pb = max(-20.0, min(20.0, ag * (dav[0] * -ay_ + dav[1] * ax_)))
                pq = max(-30.0, min(30.0, ag * tr * (dtv[0] * -ay_ + dtv[1] * ax_)))
            if pb or pq:
                n = len(pts) - 1
                rpts = [pts[0]]
                for k in range(1, len(pts)):
                    ang = math.radians(pb + (pq - pb) * k / n)
                    c_, s_ = math.cos(ang), math.sin(ang)
                    qx, qy = pts[k]
                    rpts.append((bx + (qx - bx) * c_ - (qy - by) * s_,
                                 by + (qx - bx) * s_ + (qy - by) * c_))
                pts = rpts
        total = len(pts) - 1
        sub = 1 if total >= 6 else 3              # 9 节鞭状触须无需再细分 ×3
        # r24 注:曾试按线宽分档批量 draw_lines(单段 12.2µs→2.6µs),但对
        # width≥2 的相接线段,批量调用与逐段调用**非逐位等价**(w=2 实测
        # DIFF:连接端帽处理不同)→ 会影响触角视觉,故保留逐段绘制。
        for i in range(total):
            for k in range(sub):
                q0 = _lerp2(pts[i], pts[i + 1], k / sub)
                q1 = _lerp2(pts[i], pts[i + 1], (k + 1) / sub)
                t1 = (i + (k + 1) / sub) / total
                w = (w0 - (w0 - w1) * t1) * s        # 线性渐缩至梢端
                draw.line([q0, q1], fill=col, width=max(1, int(w)))


# ============================================================================
# hybrid 底盘 —— 飞行翅(终态域抖动二色:椭圆 + 根浓梢淡 + 3 层错相残影)
# ============================================================================
# 残影层:(相位滞后, 目标 α)。主影 α158(≥150,色键校准可读)+ 滞后 80/35;
# 降级阶梯 L1/L2 依次砍到 2 层 / 1 层。
_WING_GHOSTS_FULL = ((-1.70, 35), (-0.85, 80), (0.0, 158))
_WING_GHOSTS_L1 = ((-0.85, 80), (0.0, 158))
_WING_GHOSTS_L2 = ((0.0, 158),)
_WING_COLOR = (232, 224, 192)        # 淡奶油 (230,222,190) 系(禁纯黑前缘)


def _wing_poly(root, dirx, diry, L: float, ts) -> list[tuple[float, float]]:
    """12 点椭圆翅形:沿中轴 t∈[0,1] 采样,半宽 0.22·L·sin(tπ)^0.7(展弦比~4.5:1)。"""
    px, py = -diry, dirx
    plus, minus = [], []
    for t in ts:
        hw = 0.22 * L * (math.sin(t * math.pi) ** 0.7)
        cx = root[0] + dirx * L * t
        cy = root[1] + diry * L * t
        plus.append((cx + px * hw, cy + py * hw))
        minus.append((cx - px * hw, cy - py * hw))
    return plus + minus[::-1]


def _wing_poly_fan(root, dirx, diry, L: float, n: int = 12) -> list[tuple[float, float]]:
    """扇形膜翅轮廓(滑翔 v2 §3.3 后翅段):半宽 hw=0.30·L·t^0.8(根部收窄、
    外段展开成扇;同位半宽 ≥1.5× 前翅椭圆式,B13)。"""
    px, py = -diry, dirx
    plus, minus = [], []
    for i in range(n):
        t = i / (n - 1)
        hw = 0.30 * L * (t ** 0.8)
        cx = root[0] + dirx * L * t
        cy = root[1] + diry * L * t
        plus.append((cx + px * hw, cy + py * hw))
        minus.append((cx - px * hw, cy - py * hw))
    return plus + minus[::-1]


def _fore_hw_at(L: float, t: float) -> float:
    """前翅椭圆式同位半宽(探针 B13 判据用;与 _wing_poly 内式一致)。"""
    return 0.22 * L * (math.sin(t * math.pi) ** 0.7)


def _hind_hw_at(L: float, t: float) -> float:
    """后翅扇形同位半宽(探针 B13 判据用;与 _wing_poly_fan 内式一致)。"""
    return 0.30 * L * (t ** 0.8)


def _glide_clock() -> float:
    """滑翔横摆/倾侧相位时钟(单调真实时间;探针可模块级替换为虚拟时钟)。"""
    return time.perf_counter()


def glide_sway_offset(traits: dict, hd: float, t: float | None = None) -> tuple[float, float, float]:
    """滑翔横摆(观赏层,§3.2 sway 行):纯函数,返回 (dx, dy, roll_deg)。

    - 横摆:正弦 ±glide_sway_px(traits,缺省 6.5px ∈ ±3.5~9 带)@
      glide_sway_hz(缺省 0.4Hz ∈ 0.3~0.5 带),方向取体轴法向 (-sin hd, cos hd);
    - 视觉倾侧(bank):roll = glide_bank_k × d(sway)/dt,与可见横摆速度同号
      (B12 相关符号判据的渲染侧表达),钳 ±25°,由双段翅 sweep 差动消费。
    仅蟑螂滑翔态调用;果蝇路径不经过本函数(逐位不变)。"""
    amp = float(traits.get("glide_sway_px", 6.5))
    hz = float(traits.get("glide_sway_hz", 0.4))
    k = float(traits.get("glide_bank_k", 1.0))
    ph = 6.2831853 * hz * (_glide_clock() if t is None else t)
    sw = amp * math.sin(ph)
    dsw = amp * 6.2831853 * hz * math.cos(ph)
    roll = max(-25.0, min(25.0, k * dsw))
    return (-math.sin(hd) * sw, math.cos(hd) * sw, roll)


# 躯干 cos(pitch) 纵向缩放缓存(滑翔 v2 §3.2 身体投影行):键=(物种, half,
# 朝向 10° 桶, pitch 0.5° 桶, fold 0.05 桶, traits 指纹)。滑翔 v3 起 pitch
# 带 ±1.2°@2.2Hz 微摆 → 工作集 =5 个 pitch 桶 ×(蜿蜒换朝向桶时+旧桶残余),
# LRU 8 会逐帧抖动重烘 → 上调 12(≈2.9MB 上界,滑翔期才填满;地面态不进)。
_pitch_torso_cache: OrderedDict = OrderedDict()


def _pitch_scale_torso(torso: Image.Image, species_id: str, half: int,
                       hd_deg: float, pitch_deg: float, traits: dict) -> Image.Image:
    """躯干精灵沿体轴 cos(pitch) 纵向缩放(pitch=-9.5° → ×0.986,115→113.4px)。
    旋转-缩放-旋转 + α 吸附,结果按桶缓存;|pitch|<0.5° 时调用方直通不进入。"""
    f = math.cos(math.radians(pitch_deg))
    key = (species_id, half, int(round(hd_deg / 10.0)), round(pitch_deg * 2.0),
           _traits_fp(traits))
    img = _pitch_torso_cache.get(key)
    if img is not None:
        _pitch_torso_cache.move_to_end(key)
        return img
    a = torso.rotate(hd_deg, resample=Image.BILINEAR)      # 体轴对齐屏幕 x
    w, h = a.size
    a = a.resize((max(1, int(round(w * f))), h), Image.BILINEAR)
    img = _snap_torso_alpha(a.rotate(-hd_deg, resample=Image.BILINEAR))
    _pitch_torso_cache[key] = img
    while len(_pitch_torso_cache) > 12:
        _pitch_torso_cache.popitem(last=False)
    return img


# ============================================================================
# 翻面/腹面渲染半场(D2,ADR-0030;调研 §2 翻面+腹面视图,§2.2 分段动画)。
# (ADR-0034:翻面已整体退役;_roll_scale_torso/roll_scale_factor 保留——
# 滑翔 v3 横摆 banking 复用同一横向压缩原语。)



def roll_scale_factor(roll_deg: float, body_len: float, body_w: float) -> float:
    """横滚俯视投影横向压缩系数(纯函数,探针/测试直测):
    f = |cos(roll)| + (T/W)·|sin(roll)|,T = 0.07·body_len(调研 §2.4)。
    roll=0 → 1.0 精确(调用方对 roll≤0 直通,保 roll0 逐位);90° → T/W
    (蟑螂 ≈0.162,即表观宽度 ≈ 体厚 8px)。"""
    r = math.radians(max(0.0, min(180.0, float(roll_deg))))
    return abs(math.cos(r)) + (_ROLL_T_THICK * body_len / max(1e-6, body_w)) \
        * abs(math.sin(r))


_ROLL_STEP_DEG = 5.0            # 横滚压缩缓存桶(°):翻滚 ~600°/s → 每帧 ~10°
_ROLL_T_THICK = 0.07            # 体厚 T ≈ 0.07BL(调研 §2.4 W_top 公式)
_ROLL_BODY_W = {"roach": 49.8, "fly": 14.0}   # 体全宽(px;蟑=覆翅最宽 24.9×2)
_ROLL_TORSO_CAP = 12            # 横滚压缩精灵缓存条目上限(内存看守)
_roll_torso_cache: "OrderedDict[tuple, Image.Image]" = OrderedDict()


def _roll_scale_torso(torso: Image.Image, species_id: str, half: int,
                      hd_deg: float, roll_deg: float, bl: float,
                      traits: dict) -> Image.Image:
    """躯干精灵沿体轴横向压缩(横滚投影):旋转对齐体轴 → 宽 ×f → 旋回,
    与 _pitch_scale_torso 同构(旋转-缩放-旋转 + α 吸附);结果按 (物种, half,
    朝向 10° 桶, roll 5° 桶, traits 指纹) 缓存。roll≤0 由调用方直通不进入。"""
    f = roll_scale_factor(roll_deg, bl, _ROLL_BODY_W[
        "roach" if bool(traits.get("wing_cover")) else "fly"])
    key = (species_id, half, int(round(hd_deg / 10.0)),
           int(round(roll_deg / _ROLL_STEP_DEG)), _traits_fp(traits))
    img = _roll_torso_cache.get(key)
    if img is not None:
        _roll_torso_cache.move_to_end(key)
        return img
    # 旋转把体「长轴」对齐屏幕 x → 横向(体宽方向)= 屏幕 y:横滚压缩的是
    # 侧向跨度,故缩高度(与 _pitch_scale_torso 缩宽度(长轴)方向相反)。
    a = torso.rotate(hd_deg, resample=Image.BILINEAR)
    w, h = a.size
    ch = max(1, int(round(h * f)))
    a = a.resize((w, ch), Image.BILINEAR)
    # 纵向回补到原画布高(内容仍居中)再旋回:窄画布内旋转会削掉体轴两端
    # (45° 斜向时压缩端伸出画布),且回补后粘贴位置语义与原精灵一致(居中)。
    pad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pad.alpha_composite(a, (0, (h - ch) // 2))
    img = _snap_torso_alpha(pad.rotate(-hd_deg, resample=Image.BILINEAR))
    _roll_torso_cache[key] = img
    while len(_roll_torso_cache) > _ROLL_TORSO_CAP:
        _roll_torso_cache.popitem(last=False)
    return img


def _draw_wings_hybrid(img: Image.Image, thorax, wings: dict, roach: bool,
                       half: int, lift: float, traits: dict,
                       sway: tuple[float, float] = (0.0, 0.0),
                       roll_deg: float = 0.0) -> None:
    """飞行翅:终态分辨率域直接抖动合成(1px 格),叠在躯干之上。

    蟑螂滑翔 v2(§3.3 双段翅):革质前翅(tegmen)半展画**下层**(半长
    0.58×span、sweep ±8° 拍动减半、主影 α158)+ 扇膜后翅画**上层**(扇形
    hw=0.30L·t^0.8、长 1.10×span、单主影 α≤110;fold≥0.5 整段隐藏,B13);
    roll_deg 为视觉倾侧(bank),按 ±0.6×roll 差动进 sweep 读出倾侧拐弯。
    果蝇路径与改前逐位一致(单段椭圆 + 3 层错相残影)。
    fold 协调(§4.3 修正一):收拢(fold→1)时翅影淡出让位静息翅;
    蟑螂展开巡航(fold<0.5)翅面全效 a_scale=1。"""
    span = float(wings.get("span", 0) or 0)
    if not wings.get("active") or span <= 0.0:
        return
    fold = max(0.0, min(1.0, float(wings.get("fold", 0.0))))
    if roach:
        spread = max(0.0, min(1.0, 1.0 - fold))
        # v2:展开巡航(fold<0.5)双段翅全效;收拢过渡沿 spread 线性淡出,
        # 与 torso spread 变体交接(旧式 1-spread/0.7 在近收拢时反全效,已弃)
        a_scale = 1.0 if fold < 0.5 else max(0.0, min(1.0, spread / 0.5))
    else:
        a_scale = max(0.0, min(1.0, 1.0 - fold * 1.25))
    if a_scale <= 0.03:
        return
    ghosts = (_WING_GHOSTS_FULL, _WING_GHOSTS_L1, _WING_GHOSTS_L2,
              _WING_GHOSTS_L2)[_hy_level]
    phase = float(wings.get("phase", 0.0))
    hd = thorax[2]
    ch, sh = math.cos(hd), math.sin(hd)
    color = _rgb(traits.get("wing_blur_color", _WING_COLOR))
    # 翅根(P3 §3.2:胸节 (0.28rx, ±0.30ry)),身体局部 → 终态画布坐标(+lift+sway)
    rdx, rdy = thorax[3] * 0.28, thorax[4] * 0.30
    ts = [i / 11.0 for i in range(12)]
    show_hind = roach and fold < 0.5            # B13:fold≥0.5 后翅不可见
    sweep_amp = 8.0 if roach else 16.0          # v2 滑翔拍动幅度减半(§3.3)
    roll_rad = math.radians(roll_deg)
    for side in (-1, 1):
        rx_ = thorax[0] + rdx * ch - side * rdy * sh
        ry_ = thorax[1] + rdx * sh + side * rdy * ch
        root = (half + rx_ + sway[0], half + lift + ry_ + sway[1])
        polys = []                       # (多边形, 目标α) 按残影→主影排列
        xs: list[float] = []
        ys: list[float] = []
        if roach:
            # ---- 前翅(革质 tegmen)下层:半长 0.58×span 半展(§3.3) ----
            for ph_off, a_base in ghosts:
                d = int(a_base * a_scale)
                if d < 8:
                    continue
                flap = math.sin(phase + ph_off)
                sweep = math.radians(108.0 + sweep_amp * flap) \
                    + side * roll_rad * 0.6       # bank 差动(倾侧观感)
                L = span * 0.58 * (0.98 + 0.06 * flap)
                ang = hd + side * sweep
                poly = _wing_poly(root, math.cos(ang), math.sin(ang), L, ts)
                polys.append((poly, d))
                for p in poly:
                    xs.append(p[0])
                    ys.append(p[1])
        else:
            for ph_off, a_base in ghosts:
                d = int(a_base * a_scale)
                if d < 8:
                    continue
                flap = math.sin(phase + ph_off)
                sweep = math.radians(108.0 + sweep_amp * flap)   # 后掠中轴 ±16° 拍动
                L = span * 1.15 * (0.98 + 0.10 * flap)           # 拍动中翅长微变
                ang = hd + side * sweep
                poly = _wing_poly(root, math.cos(ang), math.sin(ang), L, ts)
                polys.append((poly, d))
                for p in poly:
                    xs.append(p[0])
                    ys.append(p[1])
        if not polys:
            continue
        x0, y0 = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1
        qw = int(math.ceil(max(xs)) - x0) + 1
        qh = int(math.ceil(max(ys)) - y0) + 1
        shape = Image.new("L", (qw, qh), 0)
        sd = ImageDraw.Draw(shape)
        main_poly, main_d = polys[-1]
        for poly, d in polys:
            sd.polygon([(p[0] - x0, p[1] - y0) for p in poly], fill=d)
        # 主影根浓梢淡 α(t) 三段带(覆写梢端):poly 索引 0..11 = +半宽侧
        # (t:0→1),12..23 = -半宽侧(t:1→0);t≥6/11 与 t≥9/11 的两侧子集
        # 拼成闭合"半程→翅尖"透镜形
        mid = [(p[0] - x0, p[1] - y0) for p in (main_poly[6:12] + main_poly[12:18])]
        if len(mid) >= 3:
            sd.polygon(mid, fill=main_d * 170 // 255)
        tip = [(p[0] - x0, p[1] - y0) for p in (main_poly[9:12] + main_poly[12:15])]
        if len(tip) >= 3:
            sd.polygon(tip, fill=main_d * 110 // 255)
        mask = _dither_mask_full(shape, 1)               # 终态域 1px 格
        _paste_masked(img, color, x0, y0, mask)
        if show_hind:
            # ---- 后翅(扇状膜质)上层:单主影 α≤110,长 1.10×span(§3.3) ----
            flap = math.sin(phase)
            sweep_h = math.radians(108.0 + sweep_amp * flap) \
                + side * roll_rad * 0.6
            Lh = span * 1.10
            ang = hd + side * sweep_h
            fan = _wing_poly_fan(root, math.cos(ang), math.sin(ang), Lh)
            fx0 = math.floor(min(p[0] for p in fan)) - 1
            fy0 = math.floor(min(p[1] for p in fan)) - 1
            fw = int(math.ceil(max(p[0] for p in fan)) - fx0) + 1
            fh = int(math.ceil(max(p[1] for p in fan)) - fy0) + 1
            fshape = Image.new("L", (fw, fh), 0)
            ImageDraw.Draw(fshape).polygon(
                [(p[0] - fx0, p[1] - fy0) for p in fan], fill=int(110 * a_scale))
            _paste_masked(img, color, fx0, fy0,
                          _dither_mask_full(fshape, 1))


# ============================================================================
# hybrid 底盘 —— 躯干接缝(F2 冻结接口 + 开发期垫片缓存)
# ============================================================================
try:
    from neuropet.render import torso_art as _torso_art   # W2 形态资产(可能缺席)
except Exception:
    _torso_art = None

_torso_cache: OrderedDict = OrderedDict()   # 开发垫片 LRU(键见 _get_torso)


def _traits_fp(traits: dict):
    """traits 内容指纹(颜色/比例键变化 → 缓存失效)。"""
    try:
        return tuple(sorted((k, repr(v)) for k, v in traits.items()))
    except Exception:
        return None


def _get_torso(species_id: str, half: int, heading_deg: float,
               fold: float, traits: dict, slack_deg: float = 0.0) -> Image.Image:
    """取躯干(F2:已旋转、(2half,2half)、中心对齐)。
    W2 torso_art 在位 → 直通(缓存封装在 torso 侧,决策 F2);
    缺席(占位实现无缓存、每帧 rotate+LANCZOS ~18ms)→ 底盘开发垫片:
    3° 量化 × fold 0.02 量化 × traits 指纹的有界 LRU(≤2.5MB/尺寸),
    仅开发期生效,W2 交付后自动旁路。slack_deg:快转时最近桶回退半径
    (r24 转向连续性,直通 W2 用;垫片路径自带 3° 量化,忽略)。"""
    if _torso_art is not None and hasattr(_torso_art, "get_torso"):
        from neuropet.render import torso
        return torso.get_torso(species_id, half, heading_deg, fold, traits,
                               slack_deg)
    key = (species_id, half,
           int(round(heading_deg / 3.0)) % 120,      # 3° 档,有界(≤120 槽)
           round(float(fold) / 0.02), _traits_fp(traits))
    img = _torso_cache.get(key)
    if img is not None:
        _torso_cache.move_to_end(key)
        return img
    from neuropet.render import torso
    img = torso.get_torso(species_id, half, heading_deg, fold, traits)
    _torso_cache[key] = _snap_torso_alpha(img)   # 垫片内预吸附,运行时零成本
    cap = max(6, int(2_500_000 / (4 * (2 * half) ** 2)))   # 按 bytes 上限
    while len(_torso_cache) > cap:
        _torso_cache.popitem(last=False)
    return img


# ============================================================================
# hybrid 底盘 —— 降级阶梯(自 P2 吸收;只度量底盘段,躯干成本归 F2 侧)
# ============================================================================
_hy_level = 0                       # 0 全效 / 1 残影2层 / 2 残影1层 / 3 SS=2
_hy_perf: deque = deque(maxlen=60)  # 底盘段滚动窗(帧)
_ladder_n = 0                       # 检查计数(每 30 帧评估一次)
_good_streak = 0
_warmup_n = 0                       # 预热计数:冷启动烘焙/缓存填充帧不计入评估


def _ladder_note(ms_chassis: float) -> None:
    """滚动 P95/P50 评估降级阶梯(P95>6.5 降档,连续 3 次检查 P50<3.2 回弹)。
    开关 NEUROPET_RENDER_LADDER=0 关闭(测量脚本用);预热期(前 120 帧,
    冷启动烘焙与缓存填充)不评估,防误降档。"""
    global _hy_level, _ladder_n, _good_streak, _warmup_n
    if os.environ.get("NEUROPET_RENDER_LADDER", "1") in ("0", "false", "off"):
        return
    _hy_perf.append(ms_chassis)
    _warmup_n += 1
    if _warmup_n < 120:
        return
    _ladder_n += 1
    if _ladder_n < 30 or len(_hy_perf) < 30:
        return
    _ladder_n = 0
    xs = sorted(_hy_perf)
    p50, p95 = xs[len(xs) // 2], xs[int(len(xs) * 0.95)]
    if p95 > 6.5 and _hy_level < 3:
        _hy_level += 1
        _good_streak = 0
        _invalidate_caches()
    elif p50 < 3.2:
        _good_streak += 1
        if _good_streak >= 3 and _hy_level > 0:   # 连续 3 次检查(≈90 帧)回弹一级
            _hy_level -= 1
            _good_streak = 0
            _invalidate_caches()
    else:
        _good_streak = 0


def _invalidate_caches() -> None:
    """SS 变化时清空与超采样域相关的缓存(抖动格/阴影精灵随 SS 重建)。"""
    _thr_maps.clear()
    _shadow_sprites.clear()
    _shadow_patches.clear()
    _OPT7_CACHE.clear()
    _SNAP_CACHE.clear()


# ============================================================================
# OPT-07:静态姿态整帧合成缓存
# 空闲/站立宠每帧的 pose 签名(朝向/相位/缩放/腿/触角/traits)不变 → 直接复用
# 上一合成精灵,单帧合成≈0(仅上传)。签名任一变化(转向/摆腿/缩放变)即失效,
# 退回全合成。缓存仅保留少量条目(每宠稳态 1 条),不破坏 windowing 的 item
# 复用语义(返回的仍是不可变合成结果,由调用方每帧新建 PhotoImage 上传)。
# ============================================================================
_OPT7_CACHE: "OrderedDict[tuple, Image.Image]" = OrderedDict()
# R3-A2/M2 内存收紧(记前后值 8→4):稳态每宠 1 条 + 过渡 2 条足够。
# r24 体验修订 4→12:实测连续行走 240 帧,冷缓存 p50=8.00/p90=17.8ms、
# 热缓存(命中)p50=3.77/p90=7.3ms——4 条在转向期被反复挤爆(实测条目数
# 常驻 2-4),而签名已量化到 1°/1/16px,一点缓存就能保住整段弧。12 条
# 240² RGBA ≈2.8MB,工作集 66MB/预算 215MB 下无压力,换 ~2× 帧耗时下降。
_OPT7_CAP = 12


def _legs_fp(legs) -> tuple:
    """腿位指纹。r24:量化到 1/16 px(0.0625)——原 0.001px 远超显示/SS 可分辨
    粒度(SS=3 下 1 终态像素 = 3 子像素),使签名在连续转向中**每帧必变**、
    OPT-07 永不命中;1/16px 在 240² 显示上不可辨,换取转向中整帧复用。"""
    out = []
    for leg in (legs or []):
        for p in leg.get("points") or []:
            out.append((round(float(p[0]) * 16.0), round(float(p[1]) * 16.0)))
        out.append(round(float(leg.get("lift", 0.0)) * 16.0))
    return tuple(out)


def _ant_fp(ant) -> tuple:
    out = []
    for a in (ant or []):
        for p in a:
            out.append((round(float(p[0]) * 16.0), round(float(p[1]) * 16.0)))
    return tuple(out)


def _pose_signature(pose: dict, traits: dict, ss: int) -> tuple:
    """姿态签名:决定整帧合成结果的全部输入。任一变化 → 失效重算。"""
    segs = pose.get("segments") or []
    hd = float(segs[0][2]) if segs else 0.0
    wings = pose.get("wings") or {}
    d = pose.get("drag")   # 拖拽滞后:仅拖拽态非 None(进签名防 OPT-07 缓存滞留)
    if d is None:
        drag_sig = None
    elif "legs" in d:   # r10 力学 v2 签名:每腿偏角+每须基角(0.5° 粒度)
        drag_sig = ("v2", tuple(round(float(v), 1) for v in d["legs"]),
                    tuple(round(float(v), 1) for v in d["ant_deg"]),
                    tuple(round(float(v), 1) for v in d["ant_tip_deg"]))
    else:
        drag_sig = (
            round(float(d["d"][0]), 2), round(float(d["d"][1]), 2),
            round(float(d["ant_tip"][0]), 2), round(float(d["ant_tip"][1]), 2))
    # 滑翔 v3 振幅相位进签名(roll 0.5°/bob 0.5px 粒度量化):相位变 → 签名变
    # → OPT-07 失效重算,滑翔不再冻结;无 glide 键 → None(键序前缀兼容)。
    g = pose.get("glide")
    glide_sig = None
    if isinstance(g, dict):
        try:
            roll_g = round(float(g.get("roll_deg", 0.0)) * 2.0)
        except (TypeError, ValueError):
            roll_g = 0
        try:
            bob_g = round(float(g.get("bob_px", 0.0)) * 2.0)
        except (TypeError, ValueError):
            bob_g = 0
        glide_sig = (roll_g, bob_g)
    return (int(pose.get("half", 120)),
            # r24:朝向按 1° 量化(躯干旋转桶粒度;hd 为弧度 → 先转度)。原
            # 1e-4 度精度使连续转向每帧都是新签名 → OPT-07 永不命中、整帧重
            # 合成(~12ms@SS=3)。腿/触角在转向中连续变化(已按 1/16px 量化),
            # 故 1° 内复用不会掩盖肢体动画——它们变化即失效。静止/慢速场景
            # 本就命中,行为与画面不变。
            round(math.degrees(hd)),
            round(float(pose.get("altitude", 0.0)), 4),
            round(float(pose.get("pitch", 0.0)), 2),   # 滑翔 v2:躯干 cos(pitch) 缓存键
            round(float(wings.get("phase", 0.0)), 5),
            round(float(wings.get("fold", 1.0)), 4),
            bool(wings.get("active")),
            round(float(wings.get("span", 0.0)), 3),
            ss,
            drag_sig,
            glide_sig,
            _legs_fp(pose.get("legs")),
            _ant_fp(pose.get("antennae")),
            _traits_fp(traits))


def render_stats_snapshot() -> dict:
    """底盘渲染统计(供面板/调试;滚动窗内 P50/P95)。"""
    xs = sorted(_hy_perf)
    n = len(xs)
    return {"n": n,
            "p50": xs[n // 2] if n else 0.0,
            "p95": xs[min(n - 1, int(n * 0.95))] if n else 0.0,
            "level": _hy_level}


# ============================================================================
# hybrid 底盘 —— 主流程
# ============================================================================
def _snap_torso_alpha(torso: Image.Image) -> Image.Image:
    """躯干边缘 α 吸附:α<48 → 0(去光晕尾巴)、α≥160 → 255(压实近实像素),
    中间档保留。旋转/LANCZOS 在躯干轮廓外圈产生 ~1-2px 半透明摩尔边,色键窗
    会把这些像素与键色混成脏色;吸附后轮廓要么实色要么透出桌面,等效 1px 深
    描边(与拟真路线"边缘暗化"一致),同时大幅降低全帧半透明像素占比。
    返回原图(干净)或吸附后的副本(有摩尔档时);不修改调用方缓存。

    r24:结果按**输入对象 id** 记忆。吸附是纯函数、输入来自 torso_art 的旋转桶
    (≤512 条、命中即同一对象)→ 稳态下每帧 O(1) 查表,省 0.36ms/帧(整幅 copy
    + putalpha)。dict 以 id() 为键并存强引用防对象被回收后 id 复用;条目上限
    与旋转桶同量级,超出即整表清空(重建成本远低于每帧重算)。"""
    key = id(torso)
    cached = _SNAP_CACHE.get(key)
    if cached is not None and cached[0] is torso:
        return cached[1]
    a = torso.getchannel("A")
    hist = a.histogram()
    if not any(hist[1:48]) and not any(hist[160:255]):
        out = torso
    else:
        out = torso.copy()
        out.putalpha(a.point([0 if v < 48 else (255 if v >= 160 else v)
                              for v in range(256)]))
    if len(_SNAP_CACHE) > 600:
        _SNAP_CACHE.clear()
    _SNAP_CACHE[key] = (torso, out)
    return out


# r24:α 吸附结果缓存(见 _snap_torso_alpha;键=输入对象 id,值为 (输入引用, 结果))
_SNAP_CACHE: dict[int, tuple[Image.Image, Image.Image]] = {}

# r24:阴影 mask 缓存(见 _draw_shadow_hybrid;键=id(region),值为
# (region 引用, lo_mask, hi_mask|None))。region 未裁时即 _shadow_patches 的
# tier 对象,裁切时是临时对象——用 id + 引用校验防回收后 id 复用。
_SHADOW_MASK_CACHE: dict[int, tuple] = {}




def render_pose_hybrid(pose: dict, traits: dict) -> Image.Image:
    """混合式底盘(对外入口):OPT-07 静态姿态整帧合成缓存包装。

    签名(朝向/相位/缩放/腿/触角/traits/SS)不变时复用上一合成精灵,空闲宠
    单帧合成≈0;任一变化即退回 _render_pose_full 全合成。返回不可变合成结果,
    调用方每帧新建 PhotoImage 上传,不影响 windowing 的 item 复用语义。"""
    ss = 2 if _hy_level >= 3 else 3
    sig = _pose_signature(pose, traits, ss)
    cached = _OPT7_CACHE.get(sig)
    if cached is not None:
        _OPT7_CACHE.move_to_end(sig)
        return cached
    out = _render_pose_full(pose, traits)
    _OPT7_CACHE[sig] = out
    while len(_OPT7_CACHE) > _OPT7_CAP:
        _OPT7_CACHE.popitem(last=False)
    return out


def _render_pose_full(pose: dict, traits: dict) -> Image.Image:
    """混合式底盘(全合成):阴影→腿→触角(SS 域)→ reduce → 躯干贴画 →
    盖片 → 飞行翅。OPT-07 的缓存包装在 render_pose_hybrid 内,这里是无缓存的
    真实合成路径(也供性能探针/测试直接调用以测真实单帧成本)。"""
    half = int(pose.get("half", 120))
    size = half * 2
    roach = bool(traits.get("wing_cover"))
    # 物种识别:traits.species_id(增量键,优先)→ wing_cover 回退
    species_id = str(traits.get("species_id") or
                     ("species.cockroach" if roach else "species.fruitfly"))
    bl = float(traits.get("body_len", 115 if roach else 30))
    ss = 2 if _hy_level >= 3 else 3                  # 降级阶梯 L3:SS 3→2
    W = size * ss
    s = float(ss)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = W * 0.5
    alt = float(pose.get("altitude", 0.0))
    lift = alt * 0.30                                # 身体抬升(逻辑 px;阴影留地面)

    def rot(p):
        """pose 点(身体局部、已按 heading 旋转)→ SS 画布坐标 + 高度抬升
        (+ 滑翔横摆偏移;非滑翔态偏移为 0,输出与改前逐位一致)。"""
        return (c + (p[0] + swx) * s, c + (lift + p[1] + swy) * s)

    segs = pose.get("segments") or []
    hd = float(segs[0][2]) if segs else 0.0
    drag = pose.get("drag")   # 拖拽动量滞后通道(A4;非拖拽态为 None → 逐位不变)

    # ---------- 0. 滑翔 v2 观赏纹理(§3.2/§3.3;仅蟑螂滑翔态) ----------
    # 横摆 sway:整除躯干/腿/翅随体轴法向正弦平移(±3.5~9px @0.3~0.5Hz,
    # 阴影留地面);视觉倾侧 roll 由 sway 速度同号导出(±25° 钳),双段翅
    # sweep 差动消费。非滑翔姿态全部零偏移 → 果蝇/地面逐位不变。
    wings_early = pose.get("wings") or {}
    swx = swy = roll_vis = 0.0
    glide_ctx = pose.get("glide") if roach else None   # 滑翔 v3 振幅通道(body 下发)
    if roach and bool(wings_early.get("active")) and alt > 2.0 \
            and float(wings_early.get("fold", 1.0)) < 0.5:
        swx, swy, roll_vis = glide_sway_offset(traits, hd)
    if glide_ctx is not None:
        # 滑翔 v3:振幅相位由 body 方程下发(连续正弦+淡入淡出),渲染只投影:
        # - bob 并入 lift 通道(与 altitude 同向,全幅 ±2.5px 可见;阴影吃 alt
        #   不吃 lift → 地面影子不随 bob 浮动);
        # - drift 沿体轴法向续接 sway 通道(滚右漂右,位置连续);
        # - roll_vis 以 pose 总横滚(bank+sway 摆)为准,翅 sweep 差动随相位。
        # 无 glide 键(果蝇/地面/未活跃)全零 → 与改前逐位一致。
        lift += float(glide_ctx.get("bob_px", 0.0))
        drift = float(glide_ctx.get("drift_px", 0.0))
        swx += -math.sin(hd) * drift
        swy += math.cos(hd) * drift
        roll_vis = float(glide_ctx.get("roll_deg", roll_vis))

    legs_in = pose.get("legs") or []
    ants_in = pose.get("antennae") or []

    # ---------- 1. 底盘段计时(阶梯只看这一段) ----------
    t0 = time.perf_counter()
    # ---------- 2. 软阴影(抖动二色,SS 域,贴正下方,椭圆随体轴旋转) ----------
    _draw_shadow_hybrid(img, species_id, roach, alt, bl, ss, traits,
                        math.degrees(hd))
    # ---------- 3. 腿(描边层→本体层,世界钉足;drag=拖拽滞后偏角) ----------
    _draw_legs_hybrid(draw, rot, legs_in, traits, roach, s, drag)
    # ---------- 4. 触角(多节锥形,身体下层;drag=鞭状滞后偏角) ----------
    _draw_antennae_hybrid(draw, rot, ants_in, traits, s,
                          roach, drag)
    # ---------- 5. 降采样(Image.reduce 盒式,~1ms 级) ----------
    out = img.reduce(ss)
    _ladder_note((time.perf_counter() - t0) * 1000.0)

    # ---------- 6. 躯干(F2 接口,已旋转;底盘只贴画,边缘 α 吸附) ----------
    wings = pose.get("wings") or {}
    fold = max(0.0, min(1.0, float(wings.get("fold", 1.0))))
    hd_deg = math.degrees(hd) % 360.0
    # r24:角速度 → 桶回退半径(0.05s 前瞻,上限 4°):快转时未命中精确桶
    # 先用最近已缓存桶,~7ms 的首遇烘焙交给跟随预热,不再拖垮帧节拍。
    torso = _snap_torso_alpha(
        _get_torso(species_id, half, hd_deg, fold, traits,
                   min(4.0, abs(float(pose.get("turn_deg_s", 0.0))) * 0.05)))
    # 滑翔 v2 身体投影(§3.2):躯干精灵沿体轴 cos(pitch) 纵向缩放
    # (pitch −9.5° → ×0.986);仅蟑螂且 |pitch|≥0.5°,缓存于 10° 朝向桶
    if roach and abs(float(pose.get("pitch", 0.0))) >= 0.5:
        torso = _pitch_scale_torso(torso, species_id, half, hd_deg,
                                   float(pose["pitch"]), traits)
    # 滑翔 v3:总横滚(±6° 摆+bank)复用翻面的 roll_scale_factor 纯函数
    # 做横向微压缩。量化 10° 步进(±21° → 仅 {0,10,20} 3 精灵/朝向桶,
    # _ROLL_TORSO_CAP=12 不抖动;量化沿 |roll| 单调穿越 5°/15° 边沿,
    # 宽度跳变 ~0.5px 亚像素)。<10° 直通零成本。
    if glide_ctx is not None:
        r_g = abs(float(glide_ctx.get("roll_deg", 0.0)))
        r_q = round(r_g * 0.1) * 10.0
        if r_q >= 10.0:
            torso = _roll_scale_torso(torso, species_id, half, hd_deg,
                                      r_q, bl, traits)
    out.paste(torso, (int(round(swx)), int(round(lift + swy))), torso)

    # ---------- 7. 基节窝盖片(压腿-体接缝,终态域小实色椭圆) ----------
    # 蟑螂三对髋点全落在覆翅面上 → 盖片色取覆翅根色调(旧取腿描边色,
    # 在翅面上读成两块深色方斑);果蝇髋点在胸下,色调随胸。
    spec = _SPEC_LEG[roach]
    if roach:
        plate_base = _rgb(traits.get("torso_body", "#6e3413"))
    else:
        plate_base = _rgb(traits.get("torso_thorax", "#c08a42"))
    plate_c = _mix(plate_base, (0, 0, 0), 0.18) + (255,)
    pd = ImageDraw.Draw(out)
    legs_all = pose.get("legs") or []
    # 解剖链(≥7 点,蟑螂)= 基节缘板取代圆盖片(腿部3D规格 §7.3-1):贴体缘
    # 横椭圆,长轴⊥wall→coxa_tip(体系固定,不随摆动角摇晃),色比股节深一档,
    # 画在躯干之上读作"体缘短粗节根";4 点链(果蝇/手工 pose)照旧圆盖片。
    if roach and any(len(leg["points"]) >= 7 for leg in legs_all):
        plate_w = spec.get("plate_w", (4.6, 5.2, 6.33))
        rim_c = _mix(_rgb(traits.get("leg_edge", spec["edge"])), (0, 0, 0),
                     0.22) + (255,)
        for i, leg in enumerate(legs_all):
            pts_ = leg["points"]
            if len(pts_) < 7:
                continue
            wall, ctp = pts_[0], pts_[1]
            pang = math.atan2(ctp[1] - wall[1], ctp[0] - wall[0]) + math.pi / 2
            fpx, fpy = half + wall[0] + swx, half + lift + wall[1] + swy
            pd.polygon(_ellipse_pts(fpx, fpy, plate_w[i % 3] * 0.5,
                                    plate_w[i % 3] * 0.31, pang), fill=rim_c)
    for i, leg in enumerate(legs_all):
        pts_ = leg["points"]
        if roach and len(pts_) >= 7:
            continue                          # 解剖链已画缘板,不再叠圆盖片
        p0 = pts_[0]
        kf = spec["pair"][i % 3] if len(legs_all) == 6 else 1.0
        r = max(1.0, spec["seg"][0] * kf * 0.45)
        fx, fy = half + p0[0] + swx, half + lift + p0[1] + swy
        pd.ellipse([fx - r, fy - r, fx + r, fy + r], fill=plate_c)

    # ---------- 8. 飞行翅(终态域抖动二色;蟑螂双段翅 + sway/bank) ----------
    thorax = segs[1] if len(segs) > 1 else (segs[0] if segs else None)
    if thorax is not None:
        _draw_wings_hybrid(out, thorax, wings, roach, half, lift, traits,
                           sway=(swx, swy), roll_deg=roll_vis)
    return out


# ============================================================================
# legacy 旧矢量路径(重制前版本逐像素保留;NEUROPET_RENDER=legacy 回滚用)
# ============================================================================
def _render_vector_legacy(pose: dict, traits: dict) -> Image.Image:
    """重制前渲染管线(3x 超采样全矢量):躯干分节渐变 + 蟑螂翅盖/背板 +
    果蝇纹饰 + 半透明飞行翅影 + 实心椭圆阴影。保留原实现供一键回滚与
    before/after 对比;色键窗下其半透明阴影/翅影存在已证的显示失真。"""
    half = int(pose.get("half", 120))
    size = half * 2
    W = size * SS
    s = float(SS)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = W * 0.5
    alt = float(pose.get("altitude", 0.0))
    lift = alt * 0.30 * s           # 身体随高度抬升(阴影留在地面)

    def rot(p):
        """pose 点(身体局部、已按 heading 旋转)→ 窗口超采样坐标 + 高度抬升。"""
        return (c + p[0] * s, c + lift + p[1] * s)

    def seg_pt(seg, dx: float, dy: float):
        """段局部偏移(dx 沿体轴向前,dy 指向该段右侧)→ 随段 heading 旋转后映射。"""
        ang = seg[2]
        ch, sh = math.cos(ang), math.sin(ang)
        return rot((seg[0] + dx * ch - dy * sh, seg[1] + dx * sh + dy * ch))

    # ---------- 1. 阴影:贴地小而实;离地大而淡 + 偏移 ----------
    # 权威规格 §5.2-4 同步:纯黑 α78 是回滚路径的"黑裙边" → 暖黑棕 α46、
    # 半径 ×0.85(收紧到足迹内;纯黑在浅色桌面过死且与键色过近)
    ground_r = half * 0.30 * s * 0.85
    if alt <= 1.0:
        _ellipse(draw, c, c + ground_r * 0.28, ground_r, ground_r * 0.42, 0.0,
                 fill=(30, 24, 20, 46))
    else:
        sh_alpha = max(20, 46 - int(alt * 0.32))
        grow = 1.0 + alt / 320.0
        off = alt * 0.32 * s
        _ellipse(draw, c + off, c + ground_r * 0.28 * grow + off * 0.55,
                 ground_r * grow, ground_r * 0.42 * grow, 0.0,
                 fill=(30, 24, 20, sh_alpha))

    # ---------- 2. 腿(画在身体下层):粗细渐变 + 关节点 + 小爪 ----------
    leg_c = _rgb(traits.get("legs", "#3c2210"))
    swing_c = _rgb(traits.get("legs_swing", "#6b4a2a"))
    joint_c = _mix(leg_c, (0, 0, 0), 0.35)
    for leg in pose["legs"]:
        pts = [rot(p) for p in leg["points"]]
        lft = float(leg.get("lift", 0.0))
        col = swing_c if lft > 0.1 else leg_c
        alpha = 168 if lft > 0.1 else 255          # 摆动相微透明
        fill = col + (alpha,)
        widths = (max(2, int(2.5 * s)), max(2, int(1.8 * s)), max(1, int(1.1 * s)))
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=fill, width=widths[min(i, 2)])
        # 关节圆点(膝、踝;解剖链 ≥7 点平移到 FTi/TiTa —— 腿链 3D 波容错,
        # 规格 §4.5;4 点链取值与改前逐位一致)
        for j in ((1, 2) if len(pts) < 7 else (2, 3)):
            r = widths[min(j, 2)] * 0.62
            draw.ellipse([pts[j][0] - r, pts[j][1] - r, pts[j][0] + r, pts[j][1] + r],
                         fill=fill)
        # 足端小爪:沿末段方向再伸出的一小段尖
        # (tip 基点=pts[-1]:4 点链即 pts[3] 逐位不变;解剖链爪尖不重复外伸)
        if len(pts) >= 4:
            dx, dy = pts[3][0] - pts[2][0], pts[3][1] - pts[2][1]
            n = math.hypot(dx, dy) or 1.0
            tip = (pts[-1][0] + dx / n * 1.6 * s, pts[-1][1] + dy / n * 1.6 * s)
            draw.line([pts[-1], tip], fill=fill, width=max(1, int(0.8 * s)))

    # ---------- 3. 触角(多节折线粗细渐变,画在身体下层) ----------
    ant_c = _rgb(traits.get("antenna", "#2a1808"))
    for ant in pose["antennae"]:
        pts = [rot(p) for p in ant]
        for i in range(len(pts) - 1):
            w = max(1, int((1.5 - 0.35 * i) * s))
            draw.line([pts[i], pts[i + 1]], fill=ant_c + (255,), width=w)
        tip = pts[-1]
        r = max(1, int(0.6 * s))
        draw.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r],
                     fill=ant_c + (255,))

    # ---------- 4. 身体分节:腹 → 胸 → 头(渐变 + 高光 + 描边) ----------
    body_c = _rgb(traits.get("body", "#5a3418"))
    hi_c = _rgb(traits.get("highlight", "#7a4c24"))
    dark_c = _rgb(traits.get("dark", "#33200e"))
    dorsal_c = _mix(body_c, hi_c, 0.72)            # 背部中央亮带(背腹色差)
    edge_c = _mix(dark_c, body_c, 0.25)
    roach = bool(traits.get("wing_cover"))
    head_shrink = 0.72 if roach else 1.0           # 蟑螂头小且后半被前胸背板遮盖
    segs = []
    for i, (x, y, ang, rx, ry) in enumerate(pose["segments"]):
        if i == 0 and roach:
            rx, ry = rx * head_shrink, ry * head_shrink * 0.92
        segs.append((x, y, ang, rx, ry))
    for i_rev, (x, y, heading, rx, ry) in enumerate(reversed(segs)):
        base = rot((x, y))
        rx, ry = rx * s, ry * s
        outline_pts = _ellipse_pts(base[0], base[1], rx, ry, heading)
        draw.polygon(outline_pts, fill=body_c + (255,))
        # 背部亮带(纵轴窄椭圆);蟑螂的胸/腹被前翅与前胸背板完全覆盖,跳过省时
        i_fwd = len(segs) - 1 - i_rev
        covered = roach and i_fwd in (1, len(segs) - 1) and len(segs) >= 3
        if not covered:
            draw.polygon(_ellipse_pts(base[0], base[1], rx * 0.88, ry * 0.52, heading),
                         fill=dorsal_c + (255,))
        # 描边
        draw.line(outline_pts + [outline_pts[0]], fill=edge_c + (255,),
                  width=max(1, int(0.9 * s)))
    # 高光(光源左上:头部一侧偏移的小亮斑;偏移随 heading 旋转。
    # 蟑螂的胸节高光被前胸背板覆盖,只点头部)
    hi_segs = segs[:1] if roach else segs[:2]
    for seg in reversed(hi_segs):
        hx, hy = seg_pt(seg, -seg[3] * 0.22, -seg[4] * 0.30)
        draw.polygon(_ellipse_pts(hx, hy, seg[3] * s * 0.34, seg[4] * s * 0.24,
                                  seg[2]), fill=_mix(hi_c, (255, 255, 250), 0.45) + (255,))

    head = segs[0]
    thorax = segs[1] if len(segs) > 1 else head
    abdomen = segs[-1]
    wings = pose.get("wings") or {}
    wing_active = bool(wings.get("active"))
    # fold:1=收拢,0=展开(body/flight.py;缺省时飞行视为展开,地面视为收拢)
    fold = max(0.0, min(1.0, float(wings.get("fold", 0.0 if wing_active else 1.0))))
    span_w = float(wings.get("span", 0) or 0)
    if roach:
        # 蟑螂翅盖展开度:飞行中 fold→0 时翅盖(tegmen)张开为半透明滑翔翼面
        spread = max(0.0, min(1.0, 1.0 - fold)) if (wing_active and span_w > 0) else 0.0
    else:
        spread = 0.0

    # ---------- 5a. 蟑螂:革质前翅(中缝 + 纵脉)+ 尾须 + 前胸背板 ----------
    if roach:
        _draw_roach_cover(img, draw, seg_pt, head, thorax, abdomen, traits,
                          s, hi_c, spread)
    # ---------- 5b. 果蝇:中胸盾片沟 + 腹部环带 + 静息折叠翅 ----------
    else:
        _draw_fly_markings(img, draw, seg_pt, thorax, abdomen, wings, s,
                           body_c, dark_c, wing_active, fold)

    # ---------- 6. 眼睛 + 高光(局部偏移随头节 heading 旋转) ----------
    eye_c = _rgb(traits.get("eyes", "#151008"))
    big_eye = not roach                              # 果蝇大复眼
    for side in (-1, 1):
        if big_eye:
            p = seg_pt(head, head[3] * 0.28, side * head[4] * 0.55)
            r = head[4] * s * 0.60
        else:
            p = seg_pt(head, head[3] * 0.50, side * head[4] * 0.52)
            r = head[4] * s * 0.30
        draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=eye_c + (255,))
        r2 = r * 0.30
        draw.ellipse([p[0] - r * 0.45 - r2, p[1] - r * 0.55 - r2,
                      p[0] - r * 0.45 + r2, p[1] - r * 0.55 + r2],
                     fill=(240, 244, 248, 255))

    # ---------- 7. 翅:飞行半透明翅影(独立层合成) ----------
    # 与展开翅面的互斥调制(fold 协调,防"翅盖 + 翅影"叠成穿帮):
    # - 蟑螂滑翔:翅盖展开成翼面后翅影让位(spread≥0.35 起淡出,0.7 后全无),
    #   起飞开翅瞬间(fold 1→0)先见振翅残影、再交接到固定滑翔翼面;
    # - 果蝇:降落收翅(fold→1)时翅影同步淡出、贴腹静息翅同步淡入。
    if wing_active and span_w > 0:
        alpha_scale = (max(0.0, min(1.0, 1.0 - spread / 0.70)) if roach
                       else max(0.0, min(1.0, 1.0 - fold * 1.25)))
        if alpha_scale > 0.03:
            img = _draw_wing_blur(img, seg_pt, thorax, wings, s, alpha_scale)
    # 降采样:Image.reduce(盒式,~1ms 级)而非 LANCZOS resize(原型实测 ~13ms,超预算)
    return img.reduce(SS)


# ---------------- 蟑螂背板(前翅 + 尾须 + 前胸背板) ----------------
# 滑翔翼面展开角上限:绕翅根铰点外张(每侧),fold 0(全展开)时达到;
# 铰点取每片翅自己的根部内角(近中缝),前缘保持贴胸、只有后段外张——
# 俯视呈"后掠三角滑翔翼",而不是整片横扫过背
_COVER_SPLAY_RAD = math.radians(26.0)


def _draw_roach_cover(img, draw, seg_pt, head, thorax, abdomen, traits,
                      s, hi_c, spread: float = 0.0) -> None:
    """美洲大蠊俯视背板:两片纵长革质前翅(中缝线 + 纵脉,翅盖达腹末)、
    一对尾须、横椭圆前胸背板(遮住头后半,具一对深色中斑)。
    全部几何在"胸节体轴坐标系"(x 向前)内定义,经 seg_pt 随 heading 旋转 ——
    修复旧版以 |Δx| 估翅长(heading 旋转后缩水)与背板偏移不旋转的错位。

    spread(0=闭合,1=全展开):飞行滑翔时翅盖绕翅根铰点外张并半透明化
    (fold 调制,见 render_pose 第 7 步)——两片翅张开成滑翔翼面,此刻
    翅影层已让位(alpha_scale→0),不会与振翅残影叠成穿帮。"""
    wc = _rgb(traits.get("wing_cover_color", "#6b431f"))
    wc_hi = _mix(wc, hi_c, 0.55)
    wc_dk = _mix(wc, (0, 0, 0), 0.38)
    cerci_c = _mix(_rgb(traits.get("legs", "#3c2210")), (0, 0, 0), 0.15)
    ang = thorax[2]
    ca, sa = math.cos(ang), math.sin(ang)
    # 展开时翅面半透明(滑翔翼面透出背部),须用 RGBA 混成绘制
    blend = ImageDraw.Draw(img, "RGBA") if spread > 0.01 else draw

    def to_body(px: float, py: float) -> tuple[float, float]:
        dx, dy = px - thorax[0], py - thorax[1]
        return (dx * ca + dy * sa, -dx * sa + dy * ca)

    abx, _ = to_body(abdomen[0], abdomen[1])
    x0 = -thorax[3] * 0.15                 # 前翅前缘(藏于前胸背板之下)
    x1 = abx - abdomen[3] * 0.97           # 翅盖近腹末(腹在体轴负向,故取减号)
    wm = max(thorax[4], abdomen[4]) * 0.93
    n = 12
    ts = [i / (n - 1) for i in range(n)]
    xs = [x0 + (x1 - x0) * t for t in ts]

    def wshape(t: float) -> float:
        """前翅外缘半宽:根部 0.34wm 起速升,0.30 处最宽,向后渐收至端圆。"""
        if t < 0.30:
            return wm * (0.34 + 0.66 * (t / 0.30))
        return wm * max(0.15, 1.0 - 0.90 * ((t - 0.30) / 0.70) ** 1.25)

    # 翅面透明度随展开度下降(闭合 255 → 全展开 ~145,滑翔翼面半透明;
    # 外缘描边保留较多不透明度,勾出翼面轮廓)
    panel_a = int(255 - 110 * spread)
    hi_a = int(255 - 165 * spread)
    vein_a = int(255 - 185 * spread)
    edge_a = int(255 - 105 * spread)

    # 两片前翅(左右各一,沿中缝 y=0 对合;展开时绕各自翅根内角外张)
    for side in (-1, 1):
        prof = [(xs[0], side * wm * 0.10)]
        prof += [(x, side * wshape(t)) for x, t in zip(xs[1:-1], ts[1:-1])]
        prof.append((x1, side * wm * 0.12))
        if spread > 0.01:
            splay_a = side * spread * _COVER_SPLAY_RAD
            csp, ssp = math.cos(splay_a), math.sin(splay_a)
            pvx, pvy = xs[0] + (x1 - xs[0]) * 0.06, side * wm * 0.10
            prof = [(pvx + (px - pvx) * csp - (py - pvy) * ssp,
                     pvy + (px - pvx) * ssp + (py - pvy) * csp)
                    for px, py in prof]
        poly = [seg_pt(thorax, px, py) for px, py in prof]
        blend.polygon(poly, fill=wc + (panel_a,))
        # 翅面中央亮带(革质反光;展开后压暗,让翼面读作整片半透明帆)
        if spread <= 0.4:
            inner = [seg_pt(thorax, px, py * 0.52) for px, py in prof]
            blend.polygon(inner, fill=wc_hi + (hi_a,))
        # 纵向翅脉(与中缝平行,随外缘收窄)
        vein_c = _mix(wc_dk, wc, 0.5) + (vein_a,)
        for f in (0.30, 0.58, 0.82):
            pts = [seg_pt(thorax, xs[k], side * wshape(ts[k]) * f)
                   for k in range(1, n - 1)]
            blend.line(pts, fill=vein_c, width=max(1, int(0.7 * s)))
        # 外缘描边
        blend.line(poly + [poly[0]], fill=wc_dk + (edge_a,),
                   width=max(1, int(0.8 * s)))
    # 中缝线(两翅对合线;展开后两翅分离,缝线消失)
    if spread <= 0.05:
        draw.line([seg_pt(thorax, x0, 0.0), seg_pt(thorax, x1, 0.0)],
                  fill=wc_dk + (255,), width=max(1, int(0.9 * s)))
    # 一对尾须(腹末两根向后的小须;腹末在体轴负向,故锚 -0.9rx)
    for side in (-1, 1):
        p0 = seg_pt(abdomen, -abdomen[3] * 0.90, side * abdomen[4] * 0.22)
        p1 = seg_pt(abdomen, -abdomen[3] * 0.90 - 5.5, side * abdomen[4] * 0.52)
        p2 = seg_pt(abdomen, -abdomen[3] * 0.90 - 10.5, side * abdomen[4] * 0.70)
        draw.line([p0, p1, p2], fill=cerci_c + (255,), width=max(1, int(0.9 * s)))
    # 前胸背板:横椭圆(宽>长),遮住头后半;美洲大蠊样式:浅色周缘 + 暗色鞍心
    # + 周缘一对微小黑斑(斑要小而淡,防止读成"假眼/脸")
    pcx = thorax[3] * 0.52
    prx, pry = thorax[3] * 0.84, min(thorax[4] * 1.08, wm * 1.14 + 2.0)
    pc = seg_pt(thorax, pcx, 0.0)
    draw.polygon(_ellipse_pts(pc[0], pc[1], prx * s, pry * s, ang),
                 fill=_mix(wc_hi, (190, 160, 115), 0.28) + (255,))
    draw.polygon(_ellipse_pts(pc[0], pc[1], prx * s * 0.86, pry * s * 0.80, ang),
                 fill=_mix(wc, (0, 0, 0), 0.28) + (255,))
    draw.polygon(_ellipse_pts(pc[0], pc[1], prx * s * 0.48, pry * s * 0.46, ang),
                 fill=_mix(wc, (0, 0, 0), 0.16) + (255,))
    spot_c = _mix(wc, (0, 0, 0), 0.45) + (255,)
    for side in (-1, 1):
        sp = seg_pt(thorax, pcx + prx * 0.34, side * pry * 0.58)
        draw.polygon(_ellipse_pts(sp[0], sp[1], prx * s * 0.12, pry * s * 0.08, ang),
                     fill=spot_c)
    draw.line(_ellipse_pts(pc[0], pc[1], prx * s, pry * s, ang) +
              [seg_pt(thorax, pcx + prx, 0.0)], fill=wc_dk + (255,),
              width=max(1, int(0.8 * s)))


# ---------------- 果蝇胸部/腹部纹饰与静息翅 ----------------
def _draw_fly_markings(img, draw, seg_pt, thorax, abdomen, wings, s,
                       body_c, dark_c, wing_active: bool = False,
                       fold: float = 1.0) -> None:
    # 中胸盾片(深色几丁质块)+ 盾片横沟
    th = seg_pt(thorax, 0.0, 0.0)
    draw.polygon(_ellipse_pts(th[0], th[1], thorax[3] * s * 0.92,
                              thorax[4] * s * 0.90, thorax[2]),
                 fill=_mix(body_c, dark_c, 0.42) + (255,))
    p0 = seg_pt(thorax, thorax[3] * 0.18, -thorax[4] * 0.82)
    p1 = seg_pt(thorax, thorax[3] * 0.18, thorax[4] * 0.82)
    draw.line([p0, p1], fill=_mix(dark_c, (0, 0, 0), 0.2) + (255,),
              width=max(1, int(0.7 * s)))
    draw.polygon(_ellipse_pts(th[0], th[1], thorax[3] * s * 0.5,
                              thorax[4] * s * 0.4, thorax[2]),
                 fill=_mix(body_c, (60, 50, 35), 0.55) + (255,))
    # 腹部环带(垂直于体轴的弧线;体轴方向经 abdomen[2] 旋转,原有逻辑正确)
    ab = seg_pt(abdomen, 0.0, 0.0)
    ch, sh = math.cos(abdomen[2]), math.sin(abdomen[2])
    band_c = _mix(dark_c, (0, 0, 0), 0.25) + (255,)
    for f in (-0.25, -0.52, -0.78):
        along = abdomen[3] * s * f
        half_w = abdomen[4] * s * math.sqrt(max(0.05, 1.0 - f * f)) * 0.94
        p0 = (ab[0] + ch * along - sh * half_w, ab[1] + sh * along + ch * half_w)
        p1 = (ab[0] + ch * along + sh * half_w, ab[1] + sh * along - ch * half_w)
        draw.line([p0, p1], fill=band_c, width=max(1, int(1.1 * s)))
    # 静息折叠翅(贴腹部纵轴的两片淡色翅瓣;偏移经 seg_pt 随 heading 旋转;
    # 混色绘制 α~150,避免旧版"白色绷带"式过实过宽)。
    # fold 调制:1=收拢(贴腹静息)→ 0=展开(飞行);降落收翅时随 fold
    # 同步淡入,与翅影淡出(第 7 步)衔接,触地无跳变。
    if fold > 0.05 and float(wings.get("span", 0) or 0) > 0:
        span = float(wings["span"])
        alpha = int(150 * fold * (0.35 if wing_active else 1.0))
        line_a = int(110 * fold * (0.35 if wing_active else 1.0))
        blend = ImageDraw.Draw(img, "RGBA")
        for side in (-1, 1):
            wp = seg_pt(abdomen, abdomen[3] * 0.05, side * 1.5)
            wa = abdomen[2] + side * 0.04
            pts = _ellipse_pts(wp[0], wp[1], span * 0.55 * s, 1.3 * s, wa)
            blend.polygon(pts, fill=(226, 230, 238, alpha))
            blend.line(pts + [pts[0]], fill=(150, 158, 172, line_a),
                       width=max(1, int(0.6 * s)))


# ---------------- 飞行翅影 ----------------
def _draw_wing_blur(img: Image.Image, seg_pt, thorax, wings, s: float,
                    alpha_scale: float = 1.0) -> Image.Image:
    """飞行翅影:12~24Hz 观赏相位驱动的半透明后掠扇形残影(双层错相模拟运动模糊)。
    alpha_scale:fold 协调系数(蟑螂翅盖展开/果蝇收翅时翅影淡出,防叠加穿帮)。"""
    wd = ImageDraw.Draw(img, "RGBA")               # 直接混色绘制,免整层 alpha_composite
    phase = float(wings.get("phase", 0.0))
    span = float(wings["span"]) * s
    hd = thorax[2]
    for ph, amult in ((phase - 0.85, 0.55), (phase, 1.0)):   # 滞后残影 + 主影
        flap = math.sin(ph)
        sweep = math.radians(108.0 + 16.0 * flap)      # 相对体轴的后掠中轴
        spread = math.radians(13.0 + 4.0 * flap)       # 扇面半张角
        L = span * 1.12 * (0.98 + 0.10 * flap)         # 拍动中翅长微变
        alpha = int(96 * amult * alpha_scale)
        edge_alpha = min(255, alpha + 70)
        for side in (-1, 1):
            root = seg_pt(thorax, -thorax[3] * 0.2, side * thorax[4] * 0.5)

            def P(a_off: float, r: float, root=root):
                a = hd + side * (sweep + a_off)
                return (root[0] + math.cos(a) * r, root[1] + math.sin(a) * r)
            quad = [root,
                    P(-spread * 0.4, L * 0.52),       # 前缘中点(前缘略外拱)
                    P(-spread, L),                    # 前缘翅尖
                    P(spread * 0.45, L * 0.93),       # 外缘中点
                    P(spread * 1.35, L * 0.62),       # 后缘中点
                    P(spread * 1.65, L * 0.16)]       # 后缘根
            wd.polygon(quad, fill=(218, 224, 232, alpha))
            # 前缘线(略深,增强"翅"的可读性)
            wd.line([quad[0], quad[1], quad[2]], fill=(120, 128, 144, edge_alpha),
                    width=max(1, int(0.9 * s)))
    return img


# ============================================================================
# 【实测记录】(Python 3.13.7 + Pillow 11.3.0 / Win32;scratch/render_matrix.py
# --compare 与本节同步;NEUROPET_RENDER_LADDER=0 关阶梯、缓存预热后最优轮)
#
# hybrid 整帧(躯干 = W2 torso_art 已就位,缓存封装在 torso 侧):
#   蟑螂 240px:stand P50=3.50/P95=6.03;walk 5.38/6.11;fly 5.94/6.63;
#              glide 5.38/6.56 —— 全部过门槛(P50≤7 / P95≤9)
#   果蝇 156px:stand 2.75/3.32;walk 2.57/3.43;fly 2.98/3.53;glide 2.64/3.09
#              —— 过门槛(P50≤3 / P95≤4;fly 档 P50 贴线)
# hybrid 底盘段(阴影+腿+触角+reduce+贴躯干+盖片+翅,不含 F2 躯干成本):
#   蟑螂 3.35~3.78ms / 果蝇 2.44~2.79ms。
#   【开发期注意】若 W2 torso_art 缺席(占位实现+底盘垫片):宠物持续转向时
#   3° 量化垫片频繁未命中,单帧 +9~18ms(占位实现每次全量 rotate+LANCZOS);
#   W2 交付后自动旁路,已实测恢复上述达标值。
# legacy(回滚基线,与重制前一致):蟑螂 stand P50=4.50 / 果蝇 2.17ms。
# 色键合规(hybrid):全格 α∈(10,245) 像素占比 0.8%~4.1%(门槛 <5%);
#   legacy 同口径 4.7%~11.4% 超标 —— 抖动二色改造的直接对照。
# 验收目标(决策 §5.3-1):蟑螂整帧 P50≤7ms/P95≤9ms;果蝇 P50≤3ms/P95≤4ms;
# 超预算先走降级阶梯(残影 3→2→1、SS 3→2),滚动 60 帧 P95>6.5ms 触发。
# ============================================================================
