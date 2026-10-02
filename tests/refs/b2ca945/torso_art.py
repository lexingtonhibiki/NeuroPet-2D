"""躯干形态资产(W2):两物种躯干离线烘焙 + 逐帧旋转缓存。

本模块是冻结接口 ``neuropet/render/torso.py`` 的被分派实现(形态资产层):
- 烘焙:每物种把「头 / 盾形前胸背板(含蝶形鞍斑)/ 纵长覆翅(中缝+纵脉+磨砂
  质感+体节暗示纹)/ 腹部 / 静息折翅 / 油光高光」以 SS=3 超采样一次性画成
  master RGBA 精灵(实测 <400ms/物种,见 scratch/torso_preview.py 输出)。
  变体:蟑螂 2 张(翅盖闭合 closed = 折翅终版:翅=体轮廓、腹末 0 外露 /
  滑翔展开 spread,fold 1↔0 交叉淡化过渡);
  果蝇 1 张(静息折翅烘焙进精灵;飞行态翅由底盘矢量画,精灵不变)。
- 每帧:按 heading_deg 从 master 旋转(BILINEAR,expand=False),角度 1° 量化
  + LRU 缓存(条目上限与内存预算双保险,超限降 2° 量化),盒式 reduce 回 1x,
  居中贴到 (2*half, 2*half) 画布返回。
- 缓存:master 键 = (species, traits 哈希);旋转缓存键 = (species, traits,
  变体, 角度桶)。invalidate(species_id) 清对应物种,invalidate() 全清。

色键合规(硬约束,见 scratch/proposals/S_现状诊断.md §四/§五):
色键透明窗(-transparentcolor #010101)下任何 10≤α<255 的像素显示时与键色
混合后截断 → 半透明高光/翅会变成脏斑或"深色木板"。因此:
1. 所有装饰(翅脉/斑纹/点刻/体节纹)一律用 ImageDraw(img, "RGBA") 混色绘制在
   不透明底上 —— 即「烘焙为不透明色」,精灵内部不允许残留 10≤α<255 像素;
2. 跨出体轮廓的元素(果蝇静息折翅)用预混色直接画成不透明像素;
3. 高光 GaussianBlur 的出廓光晕在烘焙末尾用「轮廓 alpha 回写」裁掉:alpha 通道
   恢复为画高光之前的硬轮廓;透明像素 RGB 统一替换为物种收边暗色,使 BILINEAR
   旋转插值不会把垃圾 RGB 洇进边缘(色键窗零脏斑)。

坐标约定:body 局部系,原点=身体中心(与 pose segments 同源,躯干精灵与底盘
腿/触角的接缝同用这一原点),+x 向前,+y 向右(屏幕系 y 向下)。master 画布为
以原点为中心的正方形(边 ≥ 2×轮廓最大半径,含滑翔展翼),expand=False 任意角
旋转永不裁切(deskbug 的 1.44L×0.90L 非正方形画布在 90° 朝向会裁头端,已规避)。

形态蓝图:P3_增强矢量路线.md §3.1/§3.2 + deskbug_渲染移植规格.md §1.2/§1.3/§5,
参数级微调处均在代码内注明「对照说明」。
"""
from __future__ import annotations

import colorsys
import hashlib
import math
import threading
import time
from collections import OrderedDict
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from ..core import instr as _I      # r25 U0:桶命中/缺失插桩(默认关;零成本)

SS = 3                       # 烘焙超采样倍率(与渲染管线一致)
_ROT_ENTRY_CAP = 512         # 旋转缓存硬上限(条目)
# 旋转缓存内存预算(PF 性能专项,OPT-01/OPT-08):按物种分账,**按真实字节**
# 记账(同一张精灵被「本桶 + 对面派生桶」共用时只计一次,旧版重复计入 → 实际
# 常驻只有预算的一半,白丢一半容量)。
#
# 实测(AG3,.workbuddy/ag3_rot_ab.txt):蟑螂 1° 桶的构建是 480² 全画布
# BILINEAR 旋转 + reduce ≈ 12.1ms,是应用渲染段 P50 的第一大热点。优化后
# (居中裁切旋转,见 _rot_box)降为 ≈ 7.0ms(-42%),单桶字节从 160²=100KB
# 降到 124²=60.4KB(-40%)。预算按 OPT-08 收紧到 4MB:真实常驻 ↓,同时可容纳
# 的桶数仍多于旧版(旧:6MB 计数 ≈3MB 真实、60 桶;新:4MB 真实、≈130 张精灵
# / ≈260 桶)。果蝇精灵小(52²≈10.6KB),1MB 足够覆盖整圈。
# r24 转向连续性修订:4MB(≈130 张精灵/≈260 桶)容不下整圈(360 桶 = 180 张
# 精灵 ≈ 10.9MB)→ 持续转圈 LRU 反复淘汰重建,快转窗实测桶缺失率 67%、被迫
# 渲染 18.8/46.2ms(p50/p95),显示节奏塌到 ~20fps(scratch/_r24_turn)。
# 提到 16MB:整圈 closed 全驻留 + spread 工作集,稳态转圈零重建;2D 常驻
# ~60MB,500MB 预算下无压力(内存护栏仍可压力降档)。果蝇 1MB 覆盖整圈。
_ROT_BUDGET = {"roach": int(16.0 * 1024 * 1024), "fly": 1024 * 1024}

# ---------------------------------------------------------------------------
# 颜色工具(本地实现:renderer.py 归 W1 所有,其内部函数可能随重写变化,不依赖)
# ---------------------------------------------------------------------------

def _rgb(c, fallback=(120, 80, 40)) -> tuple[int, int, int]:
    """'#rrggbb' / (r,g,b) → (r,g,b)。"""
    if isinstance(c, (tuple, list)) and len(c) >= 3:
        return (int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255)
    try:
        c = str(c).lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return fallback


def _mix(c1, c2, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _premix(fg, alpha: int, bg) -> tuple[int, int, int]:
    """把「fg 以 alpha 叠在 bg 上」的显示结果预混成不透明色(色键合规)。"""
    return _mix(bg, fg, max(0.0, min(1.0, alpha / 255.0)))


# ---------------------------------------------------------------------------
# 轮廓公式(deskbug sprite.py:404-437 原式,背下来级别)
# ---------------------------------------------------------------------------

def _egg_env(t: float, hw0: float, hw1: float, exp: float = 0.75) -> float:
    """蛋形半宽:t∈[0,1](0=前缘,1=端部),端点因子 0.35、中部鼓胀 ≈×1.14。"""
    bulge = math.sin(max(0.0, min(1.0, t)) * math.pi) ** exp
    return (hw0 + (hw1 - hw0) * t) * (0.35 + 0.65 * bulge * 1.21)


def _egg_path(x0: float, x1: float, hw0: float, hw1: float,
              n: int = 24, exp: float = 0.75) -> list[tuple[float, float]]:
    """卵圆闭合轮廓(局部坐标):前缘 x0 → 端部 x1,半宽 hw0 → hw1。
    覆翅/滑翔翼面/果蝇腹部均用此式(「中部鼓、两端收」的纵长卵圆)。"""
    pts = [(x0 + (x1 - x0) * (i / n), _egg_env(i / n, hw0, hw1, exp))
           for i in range(n + 1)]
    pts += [(x0 + (x1 - x0) * (i / n), -_egg_env(i / n, hw0, hw1, exp))
            for i in range(n, -1, -1)]
    return pts


def _shield_path(xf: float, xr: float, hwf: float, hwr: float,
                 n: int = 16, dip: float = 0.0) -> list[tuple[float, float]]:
    """盾形闭合轮廓(前胸背板):前缘 xf(可内凹 dip px)→ 后缘 xr,半宽 hwf → hwr。
    deskbug sin^0.6 原式;dip>0 时前缘中央向后凹(「前缘内凹」,框出露头缺口)。"""

    def hwe(t: float) -> float:
        bulge = math.sin(t * math.pi) ** 0.6
        return (hwf + (hwr - hwf) * t) * (0.55 + 0.45 * bulge)

    h0 = hwe(0.0)
    k = 6
    pts = [(xf - dip * math.sin(u * math.pi) ** 2, -h0 + 2 * h0 * u / k)
           for u in range(k + 1)]                       # 前缘(内凹弧,左→右)
    pts += [(xf + (xr - xf) * (i / n), hwe(i / n))
            for i in range(1, n + 1)]                   # 右侧 前→后
    m = 5
    h1 = hwe(1.0)
    rc = 0.14 * (xf - xr)
    pts += [(xr + rc * math.sin(math.pi * j / m), h1 * math.cos(math.pi * j / m))
            for j in range(1, m)]                       # 后端圆拱
    pts += [(xf + (xr - xf) * (i / n), -hwe(i / n))
            for i in range(n, -1, -1)]                  # 左侧 后→前
    return pts


def _splay(pts, pivot, ang: float):
    """绕 pivot 旋转点集(滑翔翼面外张用)。"""
    c, s = math.cos(ang), math.sin(ang)
    px, py = pivot
    return [(px + (x - px) * c - (y - py) * s,
             py + (x - px) * s + (y - py) * c) for x, y in pts]


def _lcg(seed: int):
    """确定性伪随机(点刻散布用,避免烘焙结果每次不同)。"""
    state = seed & 0x7FFFFFFF

    def nxt():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    return nxt


# ---------------------------------------------------------------------------
# 烘焙画布:局部坐标 → SS 域,统一混色绘制
# ---------------------------------------------------------------------------

class _Canvas:
    def __init__(self, half: int):
        w = h = 6 * half                     # 正方形画布,6*half 必被 SS=3 整除
        self.img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img, "RGBA")   # 关键:混色模式,装饰烤成不透明
        self.cx = w * 0.5
        self.cy = h * 0.5

    def P(self, pts):
        """局部坐标(x,y)或点集 → SS 画布坐标。"""
        if isinstance(pts[0], (int, float)):
            return (self.cx + pts[0] * SS, self.cy + pts[1] * SS)
        return [(self.cx + x * SS, self.cy + y * SS) for x, y in pts]


def _bake_highlight(cv: _Canvas, ellipses, blur_ss: float, base_a: Image.Image,
                    rim: tuple[int, int, int]) -> Image.Image:
    """油光高光层:模糊后混成进不透明底,再回写轮廓 alpha(裁掉出廓光晕)并
    把透明像素 RGB 换成收边暗色(旋转插值防脏斑)。"""
    for (ex0, ey0, ex1, ey1, col, a) in ellipses:
        shine = Image.new("RGBA", cv.img.size, (0, 0, 0, 0))
        ds = ImageDraw.Draw(shine)
        p0, p1 = cv.P((ex0, ey0)), cv.P((ex1, ey1))
        ds.ellipse([p0[0], p0[1], p1[0], p1[1]], fill=_rgb(col) + (int(a),))
        shine = shine.filter(ImageFilter.GaussianBlur(blur_ss))
        cv.img.alpha_composite(shine)
    a = base_a
    rgb = cv.img.convert("RGB")
    rgb = Image.composite(rgb, Image.new("RGB", rgb.size, rim), a)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


# ---------------------------------------------------------------------------
# 蟑螂(美洲大蠊)烘焙 —— 闭合覆翅 / 滑翔展开 两变体
# 局部坐标单位 = 1x px(body_len=115)。
# 【折翅终版规格】(决策记录 §4.3 吸收 D1 行 + virtual_cockroach 移植规格 §4 +
# 用户权威照片 image-ca7e 俯视翅盖尾):翅=体轮廓本身 —— closed 变体**不画
# 腹部**(腹末 0 外露,"腹末根本不画"比 z 排序更彻底,VC 原法);覆翅从胸后缘
# 一直延伸到 -54~-56px(U 形收口圆弧,极值 -56,体端 -57.5 内),只露尾尖与
# 一对尾须(尾须画在最底层,被翅压住只露两小截)。翅端 20~25% 为琥珀后域
# (#a06a3a→#d79e57 渐变)+ 平行细亮脉纹;中缝暗线贯通到收口;尾端 V 形交叠
# 缺刻两道。消除上一版"覆翅止于 -33 + 0.216BL 横纹腹末外露"的空间关系错误
# (用户差评:尾部露抖动斑块)。
# ---------------------------------------------------------------------------

# 覆翅轮廓包络锚点(权威 §1 照片量测直接编码:肩宽 18.9 @x=+34、
# 体最宽 24.9 @x=-16)。
# AG3 FIX-5(看图抓到的真实缺陷:尾端读成"尖楔"而非 VC 的圆润尾):
# 旧尾部锚点 -32→20.0 / -44→12.5 / -50→7.5 / -54→4.2 是一条**直线收尖**,
# 在 10x 放大下与 VC 权威原帧并排比对,尾区(t≥0.80)每侧比 VC 窄 2.3~4.1px,
# 整体读成"锥形尖头";形态学权威 §3-#6 也把"尾端呈单尖"判为剪影级缺陷。
# 新锚点按 VC 原帧实测包络(同归一化空间,见 .workbuddy/ag3_tail_fit.py)重采样,
# 尾区偏差压到 ≤1.9px,并把"收口"从最后 1.5px 的平切改为
# **半轴 3.15×7.6 的四分之一椭圆弧** —— 包络保持 ~9px 宽直到 -52.4 才向后圆润收口,
# 即规格 §4「包络后端改圆润 U 形收口(尾端半宽 ≈8.5 → 端部 2~3px)」的字面实现。
_TEG_X0, _TEG_X1 = 43.0, -52.4
_TEG_PROF = ((43.0, 15.0), (34.0, 18.9), (0.0, 23.0), (-16.0, 24.9),
             (-30.0, 21.6), (-36.0, 19.4), (-41.0, 17.2), (-46.0, 14.4),
             (-50.0, 11.6), (-52.4, 8.9))
# U 形收口:包络锚点止于 -52.4(半宽 8.9),端部再以「向后」四分之一椭圆弧圆润
# 收口 _TEG_TIP_EXT = 3.15,端面半宽压到 _TEG_END_HW —— 翅尖极值 = -52.4-3.15
# = -55.55(规格 §4「≈-55、体端 -57.5 内 2~4px」+ 门槛「翅端 -54~-56px」),
# 端部全宽 2.8px 落在「端部 2~3px」带内。旧实现用 +sin 使圆弧朝**前**凸 2px,
# 尾端变成平切带一个前凹缺口 —— 看图即判:不是 U 收口,是"齐头 + 咬掉一块"。
# 另注:_teg_env 用 smoothstep 插值,锚点处导数为 0,与椭圆弧起始切线(竖直)
# 天然相接,收口与包络之间无折角。
_TEG_TIP_EXT = 3.15       # U 形收口圆弧向后延伸量(翅尖极值 -55.55)
_TEG_END_HW = 1.4         # 收口端面半宽(端部全宽 2.8px)
_TEG_EDGE_W = 1.9         # 覆翅暗描边宽度(内芯域 = 包络 - 此值)
# 前胸背板盾形:短而宽(权威 §1:长 16.6 × 全宽 39.3,W/L≈2.4:1;前缘 x=+57、
# 后缘 x=+41、半宽前 16 后 19.5、前缘中央内凹 1.5)。旧值 (54,24,27,22) 又长又前。
_PRO = (57.0, 41.0, 16.0, 19.5, 1.5)
_ROACH_R_MAX = 80        # 画布半宽:腹末 -57.5、滑翔翼尖 r≈66.5,取 80 全角不裁

# 覆翅纵向渐变锚点(权威 §2:头端深 #7a2f1d → 中段 #6e3413 → 尾端金黄
# #a06a3a → 末端高光 #d79e57;头端深→尾端金黄是照片最强特征。折翅终版:
# 琥珀后域按 D1 定在 x ∈ [-40, -55.5] —— #a06a3a 起于 -40、#d79e57 满色于
# 收口,tip 锚点放在翅尖之外保证圆弧收口区满琥珀)
_TEG_GRAD = ((_TEG_X0, "torso_teg_head"), (-2.0, "torso_body"),
             (-40.0, "torso_teg_tail"), (-55.8, "torso_teg_tip"))
_TEG_GRAD_DEF = {"torso_teg_head": "#7a2f1d", "torso_body": "#6e3413",
                 "torso_teg_tail": "#a06a3a", "torso_teg_tip": "#d79e57"}


def _teg_grad_color(x: float, pal: dict) -> tuple[int, int, int]:
    """覆翅纵向渐变取色:x 落在相邻锚点之间时线性插值(烘焙为不透明色)。

    AG3 FIX-4 修正(看图抓到的真实缺陷):原判据把「x 是否前于翅根」写成
    `x <= pts[0][0]`,而锚点 x 是 **向后递减** 的(43 → -2 → -40 → -55.5),
    于是 x∈[-54, 43] 全部命中早退分支 → 整片翅面恒为头端暗色 #7a2f1d,
    权威 §2 的最强照片特征「头端深 → 尾端金黄」完全没有落地。同因,遍历分支
    的 `x <= x1` 也应写作 `x >= x1`(x 落在 [x1, x0] 段内)。"""
    pts = [(_TEG_GRAD[i][0], _rgb(pal.get(_TEG_GRAD[i][1],
                                          _TEG_GRAD_DEF[_TEG_GRAD[i][1]])))
           for i in range(4)]
    if x >= pts[0][0]:                            # 前于翅根 → 头端色
        return pts[0][1]
    for i in range(3):
        x0, c0 = pts[i]
        x1, c1 = pts[i + 1]
        if x >= x1:                               # x 落在 [x1, x0] 段
            t = (x0 - x) / max(1e-3, x0 - x1)     # x 向后递减
            return _mix(c0, c1, t)
    return pts[3][1]                              # 后于翅尖 → 末端金黄


def _teg_env(x: float) -> float:
    """覆翅半宽包络:量测锚点间 smoothstep 插值(轮廓级精度,见 _TEG_PROF),
    尾端 U 形收口段(_TEG_X1 → 翅尖)按同一四分之一椭圆解析延续。

    AG3 FIX-5(看图抓到的真实缺陷):旧版对 x < _TEG_X1 直接返回常数
    `_TEG_PROF[-1][1]`(=8.9),即包络在收口段**不收窄** —— 于是任何以
    `_teg_env` 定纵向位置的图层(平行脉纹、侧缘浅带)都会在收口区被"以为有
    8.9px 可用宽度",亮脉纹越过翅尖继续画到透明背景上,尾部出现悬空亮条
    (尾端极值因此被量到 -56.0、极值列半宽 6.5px 的假数据)。"""
    if x >= _TEG_PROF[0][0]:
        return _TEG_PROF[0][1]
    for (x0, y0), (x1, y1) in zip(_TEG_PROF, _TEG_PROF[1:]):
        if x >= x1:
            t = (x0 - x) / max(1e-6, x0 - x1)
            s = t * t * (3.0 - 2.0 * t)
            return y0 + (y1 - y0) * s
    if x >= _TEG_X1:
        return _TEG_PROF[-1][1]
    t = min(1.0, (_TEG_X1 - x) / _TEG_TIP_EXT)
    hw1, end = _TEG_PROF[-1][1], _TEG_END_HW
    return end + (hw1 - end) * math.sqrt(max(0.0, 1.0 - t * t))


def _teg_env_in(x: float) -> float:
    """内芯包络(留 ~1.9px 暗描边的内缩域)。"""
    return max(0.6, _teg_env(x) - _TEG_EDGE_W)


def _teg_cap_arc(m: int = 8) -> list[tuple[float, float]]:
    """U 形收口的「半宽剖面」采样(局部坐标,θ 0→90°):x 由 _TEG_X1 向后到翅尖,
    半宽由 _TEG_PROF[-1][1] 按**四分之一椭圆**收到 _TEG_END_HW。端点 j=0 为
    (x=_TEG_X1, hw=_TEG_PROF[-1][1]) 与包络无缝相接,j=m 为 (翅尖, _TEG_END_HW)
    —— 即端面钝头。轮廓层与琥珀后域层共用本函数,保证两层形状严格同源。"""
    hw1, end, ext = _TEG_PROF[-1][1], _TEG_END_HW, _TEG_TIP_EXT
    q = (math.pi / 2.0) / m
    return [(_TEG_X1 - ext * math.sin(q * j), end + (hw1 - end) * math.cos(q * j))
            for j in range(m + 1)]


def _teg_outline(x_from: Optional[float] = None) -> list[tuple[float, float]]:
    """覆翅闭合轮廓(局部坐标):按包络采样,x 从 x_from(缺省翅根)到翅端,
    端部以圆弧**向后**收口成圆润 U 形(折翅终版 §4:左右翅端交叠收口,端面为
    宽 2*_TEG_END_HW 的钝头;翅尖极值 _TEG_X1-_TEG_TIP_EXT = -55.55,落在权威
    门槛 -54~-56 内)。n=64(≈1.5px 弦长):n 太小时包络被弦切掉 —— 实测 n=30
    (3.2px 弦)在 x=-39.5 处读数比解析包络窄 1.2px,尾端轮廓整体瘦一圈。"""
    x0 = _TEG_X0 if x_from is None else x_from
    n = 64
    xs = [x0 + (_TEG_X1 - x0) * i / n for i in range(n + 1)]
    cap = _teg_cap_arc()
    pts = [(x, _teg_env(x)) for x in xs]
    pts += cap
    pts += [(x, -hw) for x, hw in reversed(cap)]
    pts += [(x, -_teg_env(x)) for x in reversed(xs)]
    return pts


# 琥珀后域「平行细亮脉纹」的车道定义(D1 §4:4 条/翅、1px、间隔 2~3px、
# **平行于中缝**)。车道 y 恒定 —— 由 _vein_lane 统一给出,渲染/QA/测试同源。
_VEIN_K = (0.22, 0.40, 0.58, 0.76)
_VEIN_X0 = -39.5                          # 琥珀后域边界 = 脉纹前端


def _vein_lane(k: float) -> tuple[float, float]:
    """一条平行脉纹车道:(|y|, 终止 x)。终止 x = 内芯半宽收到 < |y| 的位置,
    即脉纹随翅面收窄自然终止(越靠外的脉纹越早收束,真翅纵脉的读感)。"""
    yk = k * (_teg_env(_VEIN_X0) - _TEG_EDGE_W)
    x = _VEIN_X0
    while x > _TEG_X1 - _TEG_TIP_EXT and _teg_env_in(x - 0.25) >= yk:
        x -= 0.25
    return yk, x


def _roach_abdomen(cv: _Canvas, pal: dict, x0: float, x1: float,
                   hw0: float, hw1: float) -> None:
    """外露腹末:横纹体节卵圆(权威 §5.1-2,剪影级修正)。底色=节间亮带
    #6c4131,其上 5 道暗横环带 #3f2823(带宽 2.6→1.5 越后越窄),端部收窄。"""
    ab_light = _rgb(pal.get("torso_ab_light", "#6c4131"))
    ab_band = _rgb(pal.get("torso_ab_band", "#3f2823"))
    pts = _egg_path(x0, x1, hw0, hw1)
    cv.d.polygon(cv.P(pts), fill=ab_light + (255,))
    cv.d.line(cv.P(pts) + [cv.P(pts)[0]],
              fill=_mix(ab_band, (0, 0, 0), 0.30) + (255,),
              width=max(1, int(0.8 * SS)), joint="curve")
    span = x0 - x1
    for i, f in enumerate((0.15, 0.35, 0.55, 0.75, 0.93)):
        bx = x0 - span * f                          # 环带位置(越后越密越窄)
        hw = _egg_env(f, hw0, hw1) * 0.98           # 带长=该处弦长
        w = 2.6 - i * 0.28
        cv.d.line(cv.P([(bx, -hw), (bx, hw)]), fill=ab_band + (255,),
                  width=max(1, int(round(w * SS))))


def _roach_cerci(cv: _Canvas, pal: dict, stretch: float = 0.0) -> None:
    """尾须一对(权威 §1:基部 x=-49、长 ≈10px、径 ≈1.2px,向体轴后外侧
    15~25°,末端 1px 下弯)。stretch>0 时向后多伸(滑翔变体完整可见)。

    AG3 残留收敛(「尾须不清」):旧画法外露段只有 ~1.6px 的 1.2px 细线 +
    0.5px 端球,1x reduce 后只剩亚像素苍白残迹(尾部特写读不出尾须)。
    按权威照片(尾须末端超过体末 ~2px)把末端外移到 -57.9(体端 -57.5 外
    0.4px,外露段 ~3.2px)、远段加粗到 1.5px、端球加到 0.8px —— 外露段以
    全宽存活 reduce,读作两根短须;外撇角 ~24° 保持「向体轴后外侧」。"""
    cerci_c = _mix(_rgb(pal.get("torso_leg_core", "#933c10")), (0, 0, 0), 0.15)
    # 滑翔态(stretch>0)尾须 = 「悬浮短须」设计(对照权威照片 ref_top_rear:
    # 尾须离开腹末侧角、向后外张开)。约束全部来自发丝缝防控:任何不透明附肢
    # 与腹末缘/翼内缘近并行地贴近(间隙 <~1.5px)时,旋转插值+reduce 都会产生
    # 被不透明区包围的半透明孔洞(判据①抓到,扫描 24 朝向复现)。因此 spread
    # 几何整体落在腹末缘与翼内缘之间的开放区内,线-缘间隙沿程 ≥1.4px:
    #   翼内缘线 x_e(|y|) ≈ −53.9 − 1.23(|y|−6)(母图量测);
    #   尾须线与其平行(斜率同 −1.23 → 间隙沿程恒定),**墨缘到翼缘 4px**
    #   —— 实测 2.5px 时斜向 reduce 仍会产生封闭半透明袋(3×3 块跨 ink|隙|ink,
    #   对角相位下透明核心消失),4px(12 母px)才能在全部相位保住通路。
    # closed 变体(stretch=0)几何不变(判据⑥窗口不受影响)。
    _CP = (((-49.0, 4.4), (-53.0, 5.4), (-57.9, 7.8)),      # closed
           ((-58.0, 5.3), (-61.6, 8.2), (-65.0, 11.0)))     # spread(悬浮短须)
    for side in (-1, 1):
        pts = [cv.P((ax + (bx - ax) * stretch,
                     side * (ay + (by - ay) * stretch)))
               for (ax, ay), (bx, by) in zip(_CP[0], _CP[1])]
        p0, p1, p2 = pts
        cv.d.line([p0, p1], fill=cerci_c + (255,), width=max(1, int(2.2 * SS)))
        cv.d.line([p1, p2], fill=cerci_c + (255,), width=max(1, int(1.9 * SS)))
        r = 0.8 * SS
        cv.d.ellipse([p2[0] - r, p2[1] - r, p2[0] + r, p2[1] + r],
                     fill=cerci_c + (255,))


def _roach_common_layers(cv: _Canvas, pal: dict) -> None:
    """闭合变体(折翅终版)自底向上图层:
    尾须 → 覆翅(暗轮廓+U 收口 → 纵向渐变色带 → 琥珀后域 → 侧缘浅带 →
    中缝 → 纵脉 → 平行脉纹 → 尾端 V 缺刻 → 磨砂点刻)→ 头 → 前胸背板。
    【终版要点】(D1 §4 + 用户照片 image-ca7e):腹部**不画**(翅=体轮廓,
    腹末 0 外露,"腹末根本不画"是比 z 排序更彻底的空间表达,VC 原法);
    尾须画在最底层、被覆翅压住只露翅端外两小截 —— 只露尾尖与一对尾须。"""
    body_edge = _rgb(pal["torso_body_edge"])
    seam_c = _rgb(pal["torso_wing_seam"])
    vein_c = _rgb(pal["torso_wing_vein"])

    # 1) 尾须(最底层:根 x=-49 埋入覆翅之下,翅压后只露 -52 以外两小截;
    #    权威 §1:基部 x=-49、长 ≈10px,向体轴后外侧 ±15-25°,末端 1px 下弯)
    _roach_cerci(cv, pal)

    # 2) 覆翅暗轮廓满形填充(含 U 形收口,翅尖极值 -55.55;其后腹末 0 外露)
    cv.d.polygon(cv.P(_teg_outline()), fill=body_edge + (255,))

    # 3) 内芯域纵向渐变色带(32 条不透明色带近似渐变,色键窗零半透明;
    #    权威 §2「分 5–7 个纵向色带近似渐变」的加细版。AG3 残留收敛(W3/Q 遗留
    #    「色带近似 vs 照片连续光泽」):16 条时 1x 交付 ~2px 一条,色带阶梯
    #    在翅面上肉眼可辨;32 条后 ~1px 一条,1x 下已近似连续(照片的连续
    #    光泽无法用真 alpha 渐变实现 —— 色键窗硬约束,只能靠加密色带逼近)。
    #    色带铺到收口起点 _TEG_X1,圆弧收口区由第 4 步的琥珀尾域续接 ——
    #    取色同梯度,接缝无阶跃。烘焙期成本 +16 个 polygon,一次性 <1ms)
    n_strip = 32
    xa, xb = _TEG_X0 - 1.5, _TEG_X1
    for i in range(n_strip):
        s0 = xa + (xb - xa) * i / n_strip
        s1 = xa + (xb - xa) * (i + 1) / n_strip
        col = _teg_grad_color((s0 + s1) * 0.5, pal)
        e0, e1 = _teg_env_in(s0), _teg_env_in(s1)
        cv.d.polygon(cv.P([(s0, -e0), (s1, -e1), (s1, e1), (s0, e0)]),
                     fill=col + (255,))

    # 4) 琥珀后域收口段(AG3 FIX-5 重写:沿 U 形圆弧**逐棱台**填充,取色沿程
    #    连续。旧版用一个 flat 色的内缩轮廓整块填 —— x_from 与 _TEG_X1 相等时
    #    上/下缘采样退化成同一列(细月牙),收口整片留成暗描边色,尾端发黑;
    #    且 -52.4 处与最后一条色带之间有 t 0.60→0.85 的硬阶跃。逐棱台后
    #    收口区取色与第 3 步的色带同梯度、逐段续接,暗描边在收口外圈保留一圈细边)
    cap = _teg_cap_arc()
    for (cx0, ch0), (cx1, ch1) in zip(cap, cap[1:]):
        i0, i1 = max(0.3, ch0 - _TEG_EDGE_W), max(0.3, ch1 - _TEG_EDGE_W)
        col = _teg_grad_color((cx0 + cx1) * 0.5, pal)
        cv.d.polygon(cv.P([(cx0, -i0), (cx1, -i1), (cx1, i1), (cx0, i0)]),
                     fill=col + (255,))

    # 5) 侧缘浅条带 #b5764a(左右翅外缘各一条淡黄褐纵带,照片半宽 0.82~0.95 处,
    #    随包络自然收窄)。AG3 FIX-3:内缘按「内芯域」夹紧 —— 不夹紧时,翅端包络
    #    收窄到 6px 以下、而内芯只有 env-1.9,条带就整条落在暗描边里,把收口外圈
    #    涂成浅色(看图:尾端外缘"发白";规格要求收口外圈保留一圈暗边)。夹紧后
    #    宽处仍取 0.82~0.95、窄处自动退化为贴内芯的细线,暗边全程可见。
    #    AG3 FIX-5:采样沿程扩到 U 形收口圆弧上(照片中浅缘纵带沿翅全长直达收口)。
    marg = _rgb(pal.get("torso_teg_margin", "#b5764a"))
    cap = _teg_cap_arc()[1:]                       # j=0 与包络末点重合,跳过
    band_pts = [(x, _teg_env(x)) for x in
                [xa + (xb - xa) * i / 26 for i in range(27)]] + cap

    def _band(hw: float) -> tuple[float, float]:
        o = max(0.5, min(hw * 0.95, hw - _TEG_EDGE_W))
        return o, min(max(0.25, min(hw * 0.82, hw - _TEG_EDGE_W - 0.35)), o - 0.15)

    for side in (-1, 1):
        pts = [(x, side * _band(hw)[0]) for x, hw in band_pts]
        pts += [(x, side * _band(hw)[1]) for x, hw in reversed(band_pts)]
        cv.d.polygon(cv.P(pts), fill=marg + (255,))

    # 6) 纵翅中缝(第二识别特征:没有中缝=甲虫不是蟑螂;终版:+43 → 收口钝头
    #    内 0.25px 处,D1 §4「中缝暗线 +43 → -54 贯通」)
    cv.d.line(cv.P([(_TEG_X0 - 1.0, 0.0), (_TEG_X1 - _TEG_TIP_EXT + 0.25, 0.0)]),
              fill=seam_c + (255,), width=max(1, int(1.4 * SS)))

    # 7) 纵脉 3 条/翅(权威 §5.1-3:粗 1.2px、只画前 1/3 长并淡出;
    #    低波形含蓄暗条,不是裂缝。色键合规:ImageDraw(img,"RGBA") 在
    #    Pillow 11 下 α<255 是"整像素替换"(连 alpha 一起打穿成洞,旋转后
    #    即用户差评的闪烁斑块)—— 一律按局部底色预混成不透明色再画)
    for k in (-0.55, -0.32, -0.12, 0.12, 0.32, 0.55):
        pts = []
        for i in range(8):
            t = i / 7.0
            x = 33.0 + (-45.0) * t                  # 前段(33 → -12)
            env = _teg_env(x)
            y = k * env * (0.4 + 0.6 * math.sin(t * math.pi * 0.5))
            y += 0.4 * math.sin(2 * math.pi * 1.6 * t + k * 7.3)
            y = math.copysign(min(abs(y), env * 0.9), y)
            pts.append((x, y))
        # 淡出:两段各取中点处的局部梯度底色预混(80 → 32 递减观感)
        col_a = _premix(vein_c, 80, _teg_grad_color(pts[2][0], pal))
        col_b = _premix(vein_c, 32, _teg_grad_color(pts[6][0], pal))
        cv.d.line(cv.P(pts[:5]), fill=col_a + (255,), width=max(1, int(1.2 * SS)),
                  joint="curve")
        cv.d.line(cv.P(pts[4:]), fill=col_b + (255,), width=max(1, int(1.0 * SS)),
                  joint="curve")

    # 8) 琥珀后域平行脉纹(D1 §4:细亮线 4 条/翅,1px,间隔 2~3px;VC zoom_tail
    #    证据:琥珀区内 4~5 条**平行于中缝**的细亮线)。
    #    AG3 FIX-5(看图抓到的真实缺陷):旧实现取 y = k·(env(x)-描边),而 env 在
    #    尾端急剧收窄 → 8 条线全部朝翅尖**收敛**,10x 放大下读成一把"扇骨"而不是
    #    平行脉纹(与规格字面"平行"、与 VC 原帧都不符)。改为 **y 恒定** 的真平行线,
    #    车道由 _vein_lane 统一给出(渲染/QA/测试同源),间隔 2.8px 落在
    #    「间隔 2~3px」带内;每条线向后延伸到内芯半宽收窄到自身 |y| 处自然终止。
    #    亮色按各自区段的局部梯度预混(不透明直写),保持沿程 Δlum 恒定。
    #    对比度标定:以 VC 原帧琥珀域实测条纹的**局部**对比度为基准
    #    (峰值 − 上下各 2px 均值:Δ 中位 4.0 / 均值 5.6,峰间距 2.4~3.8px;
    #    .workbuddy/_vc_striation2.txt),本实现取预混 46 → 线-底 Δlum ≈ 14;
    #    1x 交付时 1px 线会被 reduce 栅格按相位 1:2 劈开,强行实测 13~17、
    #    弱行 6~9,故取"最强行 ≥10"为可读性判据。约 VC 的 2~3 倍 —— VC 是
    #    277px 体长的另一套资产(条纹间距 2.4~3.8px 在我们的 1x 上等效更细),
    #    我们 240px 精灵要保住"明确可读"就必须略高于它。旧版预混 70 时
    #    Δ≈33,10x 下读成"白色光束/扇骨"。
    for k in _VEIN_K:
        yk, x_end = _vein_lane(k)
        if x_end >= _VEIN_X0 - 1.0:
            continue                       # 该脉纹在琥珀域内已无处可放
        for side in (-1, 1):
            n_seg = 3
            for j in range(n_seg):
                ta_ = j / n_seg
                tb = (j + 1) / n_seg
                x0 = _VEIN_X0 + (x_end - _VEIN_X0) * ta_
                x1 = _VEIN_X0 + (x_end - _VEIN_X0) * tb
                lite = _premix((255, 234, 190), 46,
                               _teg_grad_color((x0 + x1) * 0.5, pal))
                cv.d.line(cv.P([(x0, side * yk), (x1, side * yk)]),
                          fill=lite + (255,), width=max(1, int(0.9 * SS)))

    # 9) 尾端 V 形交叠缺刻(D1 §4:两翅端沿中缝交叠,缝处一个小 V 形缺刻;
    #    随收口外移到新翅尖 -55.55:apex 落在体轴上收口钝头前 0.25px、两臂向前外
    #    张开,臂端仍在琥珀内芯域内(不压到暗描边))
    for side in (-1, 1):
        cv.d.line(cv.P([(_TEG_X1 - _TEG_TIP_EXT + 0.25, 0.0), (-53.2, side * 2.0)]),
                  fill=seam_c + (255,), width=max(1, int(1.0 * SS)))

    # 10) 磨砂点刻(革质微孔:小、淡、稀 —— 过大过密读成"泥点"。
    #     AG3 FIX-5:散布域上界收到琥珀后域起点 -40 —— VC 原帧的琥珀后域是**干净**
    #     的透光区(只有平行脉纹),点刻落进去会与脉纹交织成"雪花/交叉网",
    #     10x 放大下正是旧版尾端读感脏的主因。rnd 序列照旧全量消耗以保持确定性)
    spk_c = _mix(_teg_grad_color(-8.0, pal), (0, 0, 0), 0.30)
    rnd = _lcg(20260914)
    for _ in range(42):
        x = -48.0 + 86.0 * rnd()
        ymax = _teg_env(x) * 0.62
        y = (rnd() * 2 - 1) * ymax
        r = (0.8 + 0.9 * rnd()) * SS * 0.55
        if abs(y) < 1.5 or x < -40.0:
            continue
        px, py = cv.P((x, y))
        cv.d.ellipse([px - r, py - r, px + r, py + r],
                     fill=_premix(spk_c, 26, _teg_grad_color(x, pal)) + (255,))

    # 11) 头 与 前胸背板,两变体共用实现(最后画:盖住覆翅根的接缝)
    _roach_common_layers_head_pronotum(cv, pal)


def _bake_roach_closed(pal: dict, rim) -> Image.Image:
    cv = _Canvas(_ROACH_R_MAX)
    _roach_common_layers(cv, pal)
    base_a = cv.img.getchannel("A")
    # 油光高光:只留一道宽而柔的暖白晕(旧加窄亮芯,在翅脉上折成白色锯齿)
    return _bake_highlight(
        cv,
        [(-12.0, -17.0, 26.0, 3.0, pal["torso_highlight"], 26)],
        16.5, base_a, rim)


def _bake_roach_spread(pal: dict, rim) -> Image.Image:
    """滑翔展开变体:一对后掠滑翔翼面(外张角 11° ≤12°、翼展拉长),
    露出横纹腹末与尾须 —— 决策记录 §4.3 修正一(废除 X 形帆)。
    Q 验收修正:①翼面根前移到 x=+46(埋入背板后缘之下,消除翼根与背板
    之间 ~11px 的"断头"空隙);②腹部画在翼面**之后**(展开时左右翼面绕
    翅根外张、让开体轴,腹末横纹在两翼之间完整可读,不再被翼面盖住只露尖端)。"""
    cv = _Canvas(_ROACH_R_MAX)
    body = _rgb(pal["torso_body"])

    # 1) 两片滑翔翼面:卵圆翼面绕翅根外张 11°(翼尖 r≈67,画布 80 不裁);
    #    翼面根 x=+46 藏于背板下(背板最后画,盖住翼根接缝)
    wcore = _mix(body, (200, 153, 94), 0.30)      # 革质翼面略亮于覆翅内芯
    wedge = _mix(body, (0, 0, 0), 0.45)
    for side in (-1, 1):
        panel = _egg_path(46.0, -64.0, 20.0, 15.0)
        vein_pts = []
        for kf in (0.38, 0.68):
            line = []
            for i in range(8):
                t = i / 7.0
                x = 40.0 + (-86.0) * t
                env = _egg_env(t, 20.0, 15.0)
                line.append((x, kf * env * (0.4 + 0.6 * math.sin(t * math.pi * 0.5))))
            vein_pts.append(line)
        pivot = (32.0, side * 4.0)
        ang = -side * math.radians(11.0)          # +y 向下系:side=1 外张为负角
        panel = _splay(panel, pivot, ang)
        cv.d.polygon(cv.P(panel), fill=wedge + (255,))
        cv.d.polygon(cv.P(_splay(_egg_path(44.5, -62.0, 18.2, 13.6), pivot, ang)),
                     fill=wcore + (255,))
        for line in vein_pts:
            cv.d.line(cv.P(_splay(line, pivot, ang)),
                      fill=_premix(_mix(wcore, (0, 0, 0), 0.3), 60, wcore) + (255,),
                      width=max(1, int(0.9 * SS)), joint="curve")
        # 翼面纵向光带(预混不透明直写 —— α<255 会打穿 alpha 成洞)
        streak = [(x, side * -_egg_env((30.0 - x) / 94.0, 20.0, 15.0) * 0.45)
                  for x in (20.0, 0.0, -20.0, -40.0, -56.0)]
        cv.d.line(cv.P(_splay(streak, pivot, ang)),
                  fill=_premix(_mix(wcore, (232, 196, 138), 0.45), 70, wcore)
                  + (255,),
                  width=max(1, int(2.2 * SS)), joint="curve")

    # 2) 腹末横纹(画在翼面之上:外张后的翼面让开体轴,横纹腹末在两翼
    #    之间完整可读)+ 尾须(展开时完整可见、向后多伸一截)
    _roach_abdomen(cv, pal, -18.0, -57.0, 21.0, 2.0)
    _roach_cerci(cv, pal, stretch=1.0)

    # 3) 前胸背板 + 头(最后画:盖住翼根与头的接缝)
    _roach_common_layers_head_pronotum(cv, pal)

    base_a = cv.img.getchannel("A")
    return _bake_highlight(
        cv,
        [(-12.0, -22.0, 26.0, 2.0, pal["torso_highlight"], 26)],
        16.5, base_a, rim)


def _roach_common_layers_head_pronotum(cv: _Canvas, pal: dict) -> None:
    """头 + 前胸背板(闭合/滑翔两变体共用)。权威规格 §5.1-4:
    - 背板短宽盾形(0.144×0.342BL),暗桃花心木底 #3e1b17 + 窄琥珀缘
      #ad5214(1.5~2px)+ 前缘一对小暗肾斑 —— 删除旧「亮黄底 + 宽浅环 +
      中央大椭圆斑 + 五点蝶斑」的"猫头鹰脸"构图;
    - 头只露背板前缘一线(半宽 8),复眼 #1a0f08 极小、**无白色高光点**
      (俯视不可见 —— 白高光是"脸"误读的主因)。
    E3/C1 Q 版(pal["_chibi"],调研 §二):头长 ×4~5(露一线 → 露 ~18px,
    前伸至 +75,头:体读感 ≈1:1.5)、头宽 ×1.3、眼斑半径 ×3.2 + 白色高光点
    2px;背板不动(前缘 57,头自其前方让位伸出)。关闭态走原路径逐位一致。"""
    head = _rgb(pal.get("torso_head", "#4a2018"))
    if pal.get("_chibi"):
        hp0, hp1 = cv.P((_CHIBI_ROACH_HEAD_X0, -_CHIBI_ROACH_HEAD_HW)), \
            cv.P((_CHIBI_ROACH_HEAD_X1, _CHIBI_ROACH_HEAD_HW))
        ex, ey = _CHIBI_EYE_X, _CHIBI_EYE_Y
        er = 0.9 * _CHIBI_EYE_R_K * SS
    else:
        hp0, hp1 = cv.P((54.0, -8.0)), cv.P((61.0, 8.0))
        ex, ey = 56.5, 6.5
        er = 0.9 * SS
    cv.d.ellipse([hp0[0], hp0[1], hp1[0], hp1[1]], fill=head + (255,))
    for side in (-1, 1):
        ep = cv.P((ex, side * ey))
        cv.d.ellipse([ep[0] - er, ep[1] - er, ep[0] + er, ep[1] + er],
                     fill=_rgb(pal.get("eyes", "#1a0f08")) + (255,))
        if pal.get("_chibi"):
            # Q 版白色高光点 2px(眼斑放大后的辨识度/萌感锚点;基线无 ——
            # 俯视拟真口径,故只挂在 chibi 门控内)
            hr = 1.0 * SS
            gp = cv.P((ex - 0.8, side * (ey - 0.8)))
            cv.d.ellipse([gp[0] - hr, gp[1] - hr, gp[0] + hr, gp[1] + hr],
                         fill=(240, 244, 248, 255))
    pe = _rgb(pal["torso_pronotum_edge"])
    rim_c = _rgb(pal["torso_pronotum_rim"])
    pf = _rgb(pal["torso_pronotum"])
    xf, xr, hwf, hwr, dip = _PRO
    # 三层盾形:深描边(0.8px)→ 琥珀窄环(~1.2px,照片中是含蓄细边不是亮圈)
    # → 暗桃花心木底
    cv.d.polygon(cv.P(_shield_path(xf, xr, hwf, hwr, dip=dip)), fill=pe + (255,))
    cv.d.polygon(cv.P(_shield_path(xf - 0.8, xr + 0.8, hwf - 0.8, hwr - 0.8,
                                   dip=dip * 0.9)), fill=rim_c + (255,))
    cv.d.polygon(cv.P(_shield_path(xf - 2.0, xr + 2.0, hwf - 2.0, hwr - 2.0,
                                   dip=dip * 0.7)), fill=pf + (255,))
    # 前缘一对小肾斑(长轴垂直体轴 ~2×3.5px,贴近前缘 x≈+52、y=±4;#2a120c
    # —— 斑要小而贴前缘,大而居中会读成"眼睛/脸")
    mark = _rgb(pal["torso_pronotum_mark"])
    for side in (-1, 1):
        mp0, mp1 = cv.P((51.0, side * 4.0 - 1.75)), cv.P((53.0, side * 4.0 + 1.75))
        cv.d.ellipse([mp0[0], mp0[1], mp1[0], mp1[1]], fill=mark + (255,))


# ---------------------------------------------------------------------------
# 果蝇(黑腹果蝇)烘焙 —— 单变体(静息折翅烤进精灵)
# 局部坐标单位 = 1x px(body_len=30)。蓝图 P3 §3.2(deskbug _FLY ×1.25)。
# 对照说明: thorax 半宽由 P3 的 4.6×1.25≈5.8 加宽到 7.0 —— 覆盖 PARAMS 腿
# attach(1,±7) 的中足髋点,防腿根悬空;腹部改卵圆收尖(真端部尖,deskbug 的
# 「尖角三角形」实际完全被椭圆盖住,是死代码)。
# ---------------------------------------------------------------------------

_FLY_R_MAX = 26           # 画布半宽:腹尖/翅尖 r≈24,取 26
# 腹部卵圆:x0,x1,hw0,hw1(权威 §5.1-1:hw0 6.0→5.2、hw1 3.0,
# 腹收窄 ≤ 胸半宽 —— 果蝇全身最宽处是胸,不是腹)
_AB_EGG = (-2.0, -21.5, 5.2, 3.0)
# 静息翅多边形(权威 §5.1-2 + 形态学权威 §1「翅对总宽 ≈ 腹宽」):
# 外缘 = 0.75×腹峰宽 ≈ ±4.4(翅对盖满腹宽,只露 ≤0.4px 腹侧缘);
# 翅尖 -23.5 = 超出腹端 -21.5 约 2.0px = 0.067BL ∈ 权威 0.05~0.08BL 带。
_FLY_WING = ((5.8, 0.6), (-9.0, 4.4), (-20.5, 4.1), (-23.5, 0.6), (-9.0, 0.15))
_FLY_WING_TIP_X = -23.5   # 翅尖 x(隔翅环带/横脉刻按翅长百分比起算用)


def _ab_env(x: float, egg: Optional[tuple] = None) -> float:
    e = _AB_EGG if egg is None else egg
    t = max(0.0, min(1.0, (e[0] - x) / (e[0] - e[1])))
    return _egg_env(t, e[2], e[3])


def _bake_fly(pal: dict, rim) -> Image.Image:
    cv = _Canvas(_FLY_R_MAX)
    # E3/C1 Q 版系数(调研 §二):头 ×1.6 / 复眼 ×1.15 / 高光 ×1.6 /
    # 体节 rx ×1.12、ry ×1.05(圆润)。关闭态全 1.0 → 逐位原路径。
    chibi = bool(pal.get("_chibi"))
    hk = _CHIBI_FLY_HEAD_K if chibi else 1.0
    ek = _CHIBI_FLY_EYE_K if chibi else 1.0
    gk = _CHIBI_FLY_GLA_K if chibi else 1.0
    srx = _CHIBI_SEG_RX_K if chibi else 1.0
    sry = _CHIBI_SEG_RY_K if chibi else 1.0
    # 体节卵圆(chibi:前端 x0 不动、端部沿体轴 ×1.12 外推,径向 ×1.05)
    ab_egg = (_AB_EGG[0],
              _AB_EGG[0] + (_AB_EGG[1] - _AB_EGG[0]) * srx,
              _AB_EGG[2] * sry, _AB_EGG[3] * sry)
    ab_c = _rgb(pal["torso_abdomen"])
    band_c = _rgb(pal["torso_abdomen_band"])
    th_c = _rgb(pal["torso_thorax"])
    stripe_c = _rgb(pal["torso_thorax_stripe"])
    head_c = _rgb(pal["torso_head"])

    # 1) 腹部:卵圆 + 端部收尖(越后越尖),1px 暗色收边
    ab_pts = _egg_path(*ab_egg)
    cv.d.polygon(cv.P(ab_pts), fill=ab_c + (255,))
    cv.d.line(cv.P(ab_pts) + [cv.P(ab_pts)[0]],
              fill=_mix(ab_c, (0, 0, 0), 0.40) + (255,),
              width=max(1, int(0.9 * SS)), joint="curve")

    # 2) 4 道黑环带(越后越宽;半宽裁到翅面内 ±4.2 —— 权威 §2:
    #    "带永远隔膜出现,不存在裸露黑带",静息翅外缘 ±4.4 盖住全部环带)
    for i, bx in enumerate((-6.8, -10.6, -14.4, -18.2)):
        hw = min(_ab_env(bx, ab_egg) * 0.96, 4.2)
        cv.d.line(cv.P([(bx, -hw), (bx, hw)]), fill=band_c + (255,),
                  width=max(1, int(round((1.1 + i * 0.3) * SS))))

    # 3) 胸:金黄橙底(权威 §2:#c08a42 亮金黄棕)+ 5 条细纵纹(k=0,±0.35,
    #    ±0.6,≤0.8px)+ 2 排背中鬃刻点 —— 删除旧「粗黑中纵纹 1.3px + 暗盾片块」
    th_rx, th_ry = 8.0 * srx, 7.0 * sry
    th_pts = _ellipse_poly(3.5, 0.0, th_rx, th_ry)
    cv.d.polygon(cv.P(th_pts), fill=th_c + (255,))
    cv.d.line(cv.P(th_pts) + [cv.P(th_pts)[0]],
              fill=_mix(th_c, (0, 0, 0), 0.40) + (255,),
              width=max(1, int(0.8 * SS)), joint="curve")
    for k in (0.0, 0.35, -0.35, 0.6, -0.6):       # 5 条细纵纹(1 中 + 2 对)
        y0 = k * 6.4
        # 预混不透明(α<255 的 RGBA 画会整像素替换、打穿 alpha 成洞)
        sc = _premix(stripe_c, 200 if k == 0.0 else 160, th_c)
        cv.d.line(cv.P([(10.2, y0 * 0.45), (-3.8, y0)]),
                  fill=sc + (255,), width=max(1, int(0.8 * SS)))
    rnd = _lcg(20250914)
    for _ in range(14):                            # 背中鬃/肩鬃黑色小刻点(2 排)
        bx = 8.5 - 10.5 * rnd()
        by = (0.36 + 0.30 * rnd()) * (6.4 if rnd() > 0.5 else -6.4) * 0.9
        r = 0.45 * SS
        px, py = cv.P((bx, by))
        cv.d.ellipse([px - r, py - r, px + r, py + r],
                     fill=_premix(stripe_c, 220, th_c) + (255,))
    cv.d.polygon(cv.P([(-4.4, 0.0), (0.4, 2.6), (0.4, -2.6)]),
                 fill=_mix(th_c, (232, 196, 138), 0.30) + (255,))

    # 4) 头 + 砖红大复眼(占头部 ≈2/3、两眼合计占头宽 ~85%,最强识别特征)
    h_rx, h_ry = 4.6 * hk, 5.2 * hk
    cv.d.polygon(cv.P(_ellipse_poly(14.2, 0.0, h_rx, h_ry)), fill=head_c + (255,))
    cv.d.line(cv.P(_ellipse_poly(14.2, 0.0, h_rx, h_ry)) +
              [cv.P(_ellipse_poly(14.2, 0.0, h_rx, h_ry))[0]],
              fill=_mix(head_c, (0, 0, 0), 0.45) + (255,),
              width=max(1, int(0.9 * SS)), joint="curve")
    eye_c = _rgb(pal["torso_eye"])
    for side in (-1, 1):
        # 对角长椭圆:两眼合计占头宽 ~85%、中间留窄额缝(GT:眼不相接)
        ep = cv.P(_ellipse_poly(11.9, side * 2.6, 3.0 * ek, 1.8 * ek,
                                math.radians(151.0) * side))
        cv.d.polygon(ep, fill=eye_c + (255,))
        # 眼高光:预混暖橙、极小(白方块高光在放大时读成"贴纸",GT 无此特征)
        gx, gy = cv.P((13.2, side * 1.8))
        r = 0.45 * gk * SS
        gl = _rgb(pal["torso_eye_gl"])
        cv.d.ellipse([gx - r, gy - r, gx + r, gy + r],
                     fill=_premix(gl, 110, eye_c))

    # 5) 静息折翅:一对翅盖满腹宽、翅尖略超腹端 0.05~0.08BL(果蝇静息剪影
    #    决定性特征)。色键合规:torso_wing_rest (222,208,168) 本身就是
    #    「翅色叠在腹底上」的**最终预混观感**(权威 §2 采样口径),直接以
    #    不透明色画 —— 旧版再叠一次 _premix 会把乳白压暗成腹底近似色,
    #    翅面因此"消失"、读成裸腹环带(差评主因之一)。
    wing_op = _rgb(pal["torso_wing_rest"])
    wing_rim = _premix((105, 92, 58), 150, wing_op)   # 翅外缘细线(预混不透明)
    vein_c_f = _rgb(pal["torso_wing_vein"])
    costa_c = _premix(vein_c_f, 95, wing_op)          # 前缘脉(costa,深一档)
    tick_c = _premix(vein_c_f, 50, wing_op)           # 横脉刻(极淡)
    wing_len = _FLY_WING[0][0] - _FLY_WING_TIP_X      # 翅根→翅尖(横脉刻用)
    for side in (-1, 1):
        wpts = cv.P([(x, side * y) for (x, y) in _FLY_WING])
        cv.d.polygon(wpts, fill=wing_op + (255,))
        cv.d.line(wpts + [wpts[0]], fill=wing_rim + (255,),
                  width=max(1, int(0.6 * SS)), joint="curve")
        # 前缘脉(costa,沿翅外缘色深一档)+ 翅中横脉刻(翅长 40%/58% 处,
        # 极淡 —— 过深读成"裂缝";均预混不透明直写)
        cv.d.line(cv.P([(4.2, side * 0.9), (-9.0, side * 4.1), (-20.0, side * 3.8)]),
                  fill=costa_c + (255,), width=max(1, int(0.7 * SS)), joint="curve")
        for vt in (0.40, 0.58):
            vx = _FLY_WING[0][0] - wing_len * vt
            cv.d.line(cv.P([(vx, side * 0.3), (vx, side * 2.4)]),
                      fill=tick_c + (255,), width=max(1, int(0.6 * SS)))

    # 6) 透翅腹环带(权威 §2 决定性画法):画完翅后在翅多边形内重画环带,
    #    色 = mix(band, 翅面, ~0.6) 预混不透明、宽 ×0.85 —— 腹环带永远以
    #    "透过乳白膜变淡"的形式出现,不存在裸露黑带([GT1][GT2]);
    #    混合强度取 165:隔翅环带要在乳白翅面下仍一读即辨(Q 验收观察项,
    #    [GT1] 隔膜环带对比度较高)。
    band_w = _premix(band_c, 165, wing_op)
    for i, bx in enumerate((-6.8, -10.6, -14.4, -18.2)):
        hw = min(_ab_env(bx, ab_egg) * 0.96, 4.15)    # 只画到翅面内(翅外缘 ±4.4)
        cv.d.line(cv.P([(bx, -hw), (bx, hw)]), fill=band_w + (255,),
                  width=max(1, int(round((1.1 + i * 0.3) * 0.85 * SS))))

    base_a = cv.img.getchannel("A")
    # 高光只留胸部一小道(腹部白斑会把预混翅面推成"白条",GT 无此特征)
    return _bake_highlight(
        cv,
        [(0.5, -3.4, 7.4, 0.4, pal["torso_highlight"], 30)],
        4.5, base_a, rim)


def _ellipse_poly(cx: float, cy: float, rx: float, ry: float,
                  ang: float = 0.0, n: int = 20) -> list[tuple[float, float]]:
    ch, sh = math.cos(ang), math.sin(ang)
    pts = []
    for i in range(n):
        a = 6.2831853 * i / n
        ex, ey = math.cos(a) * rx, math.sin(a) * ry
        pts.append((cx + ex * ch - ey * sh, cy + ex * sh + ey * ch))
    return pts


# ---------------------------------------------------------------------------
# D2 腹面(翻面)变体(ADR-0030;调研 §2.3 特征清单,B6/B8 判据)。
# 门控:traits["ventral_view"]=True → _pal_for 塞 pal["_ventral"](与 chibi
# 同机制:哈希进 master 键 → 独立预烘焙,关闭态零注入逐位一致)。烘焙产物
# 直接占满 closed/spread 两变体槽(腹面不见翅盖,spread 懒烘焙不会触发),
# fold 交叉淡化blend的是同一对象 → 任意 fold 输出恒等腹面精灵。
#
# 特征清单落地(量化口径见调研 §2.3 表):
#   - 腹部腹板 7±1 节带(雌 7/雄 8~9 取 7):节间深色细缝线(带宽 ~6.8px +
#     缝 1.2px ≈ 权威「带宽 4~7px + 1px 亮缝」);
#   - 胸部 3 腹板(前/中/后胸),基节影两排、中足基节近中线(间隙 ≤0.06BL
#     =7px,实现取中线相触的 ~0 间隙,转节近中线相触【项目·已核】);
#   - 头/口器区:下口式腹面可见,下颚须短须 2 根示意(~0.11BL);
#   - 体色显著浅于背面(#b98d55 vs #6e3413,L 148 vs 66,ΔL≥+25% ✓);
#   - **无覆翅纵缝/纵脉/琥珀后域/背板肾斑/点刻**(全部背面专属层,不画);
#   - 尾须腹面观略宽张角。
# ---------------------------------------------------------------------------

_VENTRAL_SEAM_N = 7         # 腹部腹板节缝数(= 节带数口径:6~8 带判据 B8)
_VENTRAL_SEAM_X0 = 2.0      # 第一节缝 x(腹区 +2 → −46,每节距 8px)
_VENTRAL_SEAM_DX = 8.0
_VENTRAL_PLATE_GAP = 1.2    # 腹板多边形与节缝的留隙(→ 深缘可见宽 ~2.4px)
_VENTRAL_ABDOMEN_X0 = 5.0   # 腹域前界(胸域自此向前,由基节主导)


def _ventral_body_fill(cv: _Canvas, edge, base) -> None:
    """腹面体轮廓(与背面 _teg_outline 同源,翻滚过程剪影连续):暗缘描边 +
    内芯域浅琥珀填充(内缩 = 同 _TEG_EDGE_W,复用覆翅内芯口径)。"""
    cv.d.polygon(cv.P(_teg_outline()), fill=edge + (255,))
    xs = [_TEG_X0 - 0.5 + (_TEG_X1 - _TEG_X0 + 0.5) * i / 64 for i in range(65)]
    inner = [(x, max(0.6, _teg_env(x) - _TEG_EDGE_W)) for x in xs]
    cap = [(x, max(0.5, hw - _TEG_EDGE_W)) for x, hw in _teg_cap_arc()]
    pts = (inner + cap + [(x, -hw) for x, hw in reversed(cap)]
           + [(x, -hw) for x, hw in reversed(inner)])
    cv.d.polygon(cv.P(pts), fill=base + (255,))


def _ventral_abdomen_domain() -> list[tuple[float, float]]:
    """腹面腹域闭合多边形(x≤_VENTRAL_ABDOMEN_X0,沿内芯包络 + 尾端收口弧)。"""
    xs = [_VENTRAL_ABDOMEN_X0 - 0.5 + (_VENTRAL_ABDOMEN_X0 - _TEG_X1 + 0.5)
          * -i / 64 for i in range(65)]
    inner = [(x, max(0.6, _teg_env(x) - _TEG_EDGE_W)) for x in xs]
    cap = [(x, max(0.5, hw - _TEG_EDGE_W)) for x, hw in _teg_cap_arc()]
    return (inner + cap
            + [(x, -hw) for x, hw in reversed(cap)]
            + [(x, -hw) for x, hw in reversed(inner)])


def _bake_roach_ventral(pal: dict, rim) -> Image.Image:
    """蟑螂腹面躯干 v2(仰翻 180° 俯视投影;权威 = docs/references/腹面形态要点.md,
    照片基准 docs/references/refs/ventral/,iNaturalist CC)。

    与 v1(细缝线示意)的差异 —— 照片最强特征是「浅腹板主体 + 深节间缘」的
    **条带交替**,不是缝线:先以节间缘色填满腹域,再逐节叠浅色腹板多边形
    (节距 8px、板宽 ~5.6px),体轴亮度交替 ≥6 次可直接采样断言;腹端 2~3 节
    按 照片实测 #7d4f44 方向渐深(幅值 ≤0.32,保旧缝计数/亮度判据兼容);
    基节改杯状窝(暗缘环 + 偏置内芯),中足对近中线相触(转节相触【项目·已核】)。
    色键纪律:全部 ImageDraw(RGBA) 烤成不透明,零中间 alpha。"""
    base = _rgb(pal.get("torso_ventral", "#b98d55"))
    band = _rgb(pal.get("torso_ventral_band", "#8a5c34"))
    edge = _rgb(pal.get("torso_ventral_edge", "#6e4426"))
    cv = _Canvas(_ROACH_R_MAX)

    # 1) 体轮廓(浅琥珀腹面底,剪影与背面同源 → 翻滚过程轮廓连续)
    _ventral_body_fill(cv, edge, base)

    # 2) 腹域整体 = 节间缘色(条带的「深」半周期),x≤+5 全域填充
    cv.d.polygon(cv.P(_ventral_abdomen_domain()), fill=band + (255,))

    # 3) 7 块腹板多边形(「浅」半周期):每节 x∈[sx_{k+1}+gap, sx_k−gap],
    #    gap=1.2px → 深缘可见宽 ~2.4px(权威缝宽 ≈ 节距 1/8);板宽沿内芯
    #    包络收边 0.3px。腹端渐深:#7d4f44(照片实测)按节线性加重 ≤0.32。
    rear = _rgb("#7d4f44")
    for k in range(_VENTRAL_SEAM_N):
        sxk = _VENTRAL_SEAM_X0 - _VENTRAL_SEAM_DX * k
        xr = sxk - _VENTRAL_SEAM_DX + _VENTRAL_PLATE_GAP
        xf = sxk - _VENTRAL_PLATE_GAP
        if xf <= _TEG_X1:
            break
        t_rear = max(0.0, (k - 3.0) / 3.0) * 0.32         # 第 4 节起渐深
        plate = _mix(base, rear, t_rear)
        n = 14
        xs = [xf + (xr - xf) * i / n for i in range(n + 1)]
        pts = [(x, max(0.4, _teg_env_in(x) - 0.3)) for x in xs] \
            + [(x, -max(0.4, _teg_env_in(x) - 0.3)) for x in reversed(xs)]
        cv.d.polygon(cv.P(pts), fill=plate + (255,))

    # 4) 腹中线(淡暗线,照片 L 比腹板低 ~10%;不参与节缝计数)
    cv.d.line(cv.P([(8.0, 0.0), (-50.0, 0.0)]),
              fill=_mix(base, (0, 0, 0), 0.10) + (255,),
              width=max(1, int(0.7 * SS)))

    # 5) 胸部腹板缝 2 道(前/中胸;胸部腹板大部分被基节遮盖 → 只留淡缝)
    for i, sx in enumerate((31.0, 21.0)):
        hw = 0.92 * _teg_env(sx)
        x0 = sx + (1.6 if i == 1 else 0.0)     # 中胸缝微斜(避基节窝)
        cv.d.line(cv.P([(x0, -hw), (sx, hw)]), fill=_mix(band, base, 0.35) + (255,),
                  width=max(1, int(1.1 * SS)))

    # 6) 基节三对,杯状窝(要点 §5):暗缘环 + 向体后偏置的浅内芯 → 投影读
    #    作凹窝;中足对近中线相触(±3.2、ry3.9 → 内缘过中线 0.7px)。
    cox_rim = _mix(base, (0, 0, 0), 0.34)
    cox_in = _mix(base, (0, 0, 0), 0.18)
    for (cx_, cy_, rx_, ry_) in ((31.0, 10.8, 6.6, 4.4),
                                 (10.0, 3.2, 6.0, 3.9),
                                 (-13.5, 9.2, 7.2, 4.6)):
        for side in (-1, 1):
            p0 = cv.P((cx_ - rx_, side * cy_ - ry_))
            p1 = cv.P((cx_ + rx_, side * cy_ + ry_))
            q0 = cv.P((cx_ - rx_ + 1.6, side * cy_ - ry_ + 1.3))
            q1 = cv.P((cx_ + rx_ - 1.6, side * cy_ + ry_ - 1.3))
            cv.d.ellipse([p0[0], p0[1], p1[0], p1[1]], fill=cox_rim + (255,))
            cv.d.ellipse([q0[0], q0[1], q1[0], q1[1]], fill=cox_in + (255,))

    # 7) 头 + 口器区(下口式,腹面可见;头囊略大于背面露一线)+ 下颚须短须
    #    2 根示意(5 节显著、~0.10~0.15BL=11.5~17px,取 ~13px 三折线)
    hp0, hp1 = cv.P((40.0, -9.2)), cv.P((64.0, 9.2))     # 后端埋入体轮廓(颈接)
    hq0, hq1 = cv.P((41.4, -7.8)), cv.P((62.6, 7.8))
    cv.d.ellipse([hp0[0], hp0[1], hp1[0], hp1[1]], fill=edge + (255,))
    cv.d.ellipse([hq0[0], hq0[1], hq1[0], hq1[1]],
                 fill=_mix(base, (0, 0, 0), 0.06) + (255,))
    for side in (-1, 1):
        pts = [(56.0, side * 4.0), (62.0, side * 7.5), (66.5, side * 6.8),
               (69.5, side * 8.6)]
        cv.d.line(cv.P(pts), fill=band + (255,), width=max(1, int(1.1 * SS)),
                  joint="curve")

    # 8) 尾须(腹面观张角略宽 ±25°;自末节腹侧后方伸出)
    cerci_c = _mix(band, (0, 0, 0), 0.15)
    for side in (-1, 1):
        cv.d.line(cv.P([(-50.5, side * 4.8), (-58.0, side * 8.4)]),
                  fill=cerci_c + (255,), width=max(1, int(1.6 * SS)))

    base_a = cv.img.getchannel("A")
    # 高光弱一档(腹面哑光;大面积亮斑会侵蚀「腹板节带」可读性)
    return _bake_highlight(
        cv,
        [(-10.0, -12.0, 20.0, 2.0, pal.get("torso_highlight", (255, 240, 210)), 16)],
        12.0, base_a, rim)


def _bake_fly_ventral(pal: dict, rim) -> Image.Image:
    """果蝇腹面(仰翻 180°):腹板奶琥珀 + 7 节缝 + 浅胸 + 头(小眼)+
    下颚须短须;**不画静息折翅**(翅在背面,调研 §2.3「腹面不见翅盖」种推)、
    不画背纵纹/鬃刻点。"""
    base = _rgb(pal.get("torso_ventral", "#ecd19b"))
    band = _rgb(pal.get("torso_ventral_band", "#a8834e"))
    th_c = _rgb(pal.get("torso_ventral_thorax", "#dcb877"))
    edge = _rgb(pal.get("torso_ventral_edge", "#8a683a"))
    cv = _Canvas(_FLY_R_MAX)

    # 1) 腹部(腹板面):卵圆 + 暗缘 + 7 道节缝
    ab_pts = _egg_path(*_AB_EGG)
    cv.d.polygon(cv.P(ab_pts), fill=base + (255,))
    cv.d.line(cv.P(ab_pts) + [cv.P(ab_pts)[0]],
              fill=_mix(base, (0, 0, 0), 0.35) + (255,),
              width=max(1, int(0.8 * SS)), joint="curve")
    for k in range(_VENTRAL_SEAM_N):
        bx = -4.6 - 2.45 * k                          # −4.6 → −19.3
        hw = min(_ab_env(bx) * 0.94, 5.0)
        cv.d.line(cv.P([(bx, -hw), (bx, hw)]), fill=band + (255,),
                  width=max(1, int(0.8 * SS)))

    # 2) 胸(浅琥珀,无背纵纹/无鬃刻点)
    th_pts = _ellipse_poly(3.5, 0.0, 8.0, 7.0)
    cv.d.polygon(cv.P(th_pts), fill=th_c + (255,))
    cv.d.line(cv.P(th_pts) + [cv.P(th_pts)[0]],
              fill=_mix(th_c, (0, 0, 0), 0.32) + (255,),
              width=max(1, int(0.8 * SS)), joint="curve")

    # 3) 头(腹面复眼读感缩小)+ 下颚须短须 2 根示意
    h_pts = _ellipse_poly(14.2, 0.0, 4.6, 5.2)
    cv.d.polygon(cv.P(h_pts), fill=_mix(th_c, base, 0.5) + (255,))
    cv.d.line(cv.P(h_pts) + [cv.P(h_pts)[0]],
              fill=_mix(th_c, (0, 0, 0), 0.38) + (255,),
              width=max(1, int(0.8 * SS)), joint="curve")
    eye_c = _mix(_rgb(pal.get("torso_eye", "#a8301f")), (0, 0, 0), 0.25)
    for side in (-1, 1):
        ep = cv.P((13.4, side * 2.4))
        r = 0.8 * SS
        cv.d.ellipse([ep[0] - r, ep[1] - r, ep[0] + r, ep[1] + r],
                     fill=eye_c + (255,))
        cv.d.line(cv.P([(16.0, side * 1.6), (18.8, side * 2.9)]),
                  fill=band + (255,), width=max(1, int(0.7 * SS)))

    base_a = cv.img.getchannel("A")
    return _bake_highlight(cv, [], 0.0, base_a, rim)   # 腹面不高光(保亮度)


# ---------------------------------------------------------------------------
# 色板读取:优先 traits 的 torso_* 键(species render_traits 增补),
# 缺省回退 P3 §3 色板 —— 老调用方(如 tests/smoke.py 手搓 traits)也能直接用。
# ---------------------------------------------------------------------------

_ROACH_PAL = {
    # 权威规格 §2 照片取样色板(traits 缺席时的回退,tests/smoke 手搓 traits 也能烤对)
    "torso_body": "#6e3413", "torso_body_edge": "#3f1d08",
    "torso_wing_seam": "#47220b", "torso_wing_vein": "#331512",
    "torso_teg_head": "#7a2f1d", "torso_teg_tail": "#a06a3a",
    "torso_teg_tip": "#d79e57", "torso_teg_margin": "#b5764a",
    "torso_ab_band": "#3f2823", "torso_ab_light": "#6c4131",
    "torso_pronotum": "#3e1b17", "torso_pronotum_rim": "#ad5214",
    "torso_pronotum_edge": "#2a120c", "torso_pronotum_mark": "#2a120c",
    "torso_head": "#4a2018", "torso_leg_core": "#933c10",
    "eyes": "#1a0f08",
    # D2 腹面变体色板(调研 §2.3:腹面显著浅于背面,琥珀黄褐 #b98d55 底 /
    # #8a5c34 节带 / #6e4426 缘描边;ΔL≥+25% 验收 B6)【外推 hex,待用户腹面照校】
    "torso_ventral": "#b98d55", "torso_ventral_band": "#8a5c34",
    "torso_ventral_edge": "#6e4426",
    "torso_highlight": (255, 240, 210),
}
_FLY_PAL = {
    # 权威规格 §2([GT2] 高清照片取样,金黄橙一档)
    "torso_thorax": "#c08a42", "torso_thorax_stripe": "#7a4e1e",
    "torso_abdomen": "#d9b269", "torso_abdomen_band": "#2a1c10",
    "torso_head": "#a4793a", "torso_eye": "#a8301f",
    "torso_eye_gl": (212, 96, 63),
    "torso_wing_rest": (222, 208, 168), "torso_wing_vein": (150, 130, 84),
    # D2 果蝇腹面色板(腹面浅于背面;果蝇背面本就偏亮,腹板取奶琥珀一档)
    "torso_ventral": "#ecd19b", "torso_ventral_band": "#a8834e",
    "torso_ventral_thorax": "#dcb877", "torso_ventral_edge": "#8a683a",
    "torso_highlight": (255, 250, 220),
}
_ROACH_RIM = (63, 29, 8)      # 收边暗色(#3f1d08):透明区 RGB 回填,防旋转洇色
_FLY_RIM = (75, 52, 20)


# ---------------------------------------------------------------------------
# E3/C1 虹色(皮肤变彩色)—— HSL 色相旋转查表 + 预烘焙相位桶(调研 §三)
# 硬约束:traits 哈希即 master 键(_thash),连续相位会逐帧重烘焙(37.7ms)爆帧
# 预算。故相位量化为整数桶(hue_phase ∈ 0..N-1),只随 traits 进键 → 每个相位
# 一份预烘焙 master(复用现有缓存机制,零逐帧 HSL 全图变换);相位推进 ~6.7s/档
# (周期 20s),切换瞬间的一次性重烘焙由预热线程跟随(与转向首遇桶同一机制)。
# 色键合规:只改色相、全不透明预混、不引入任何 alpha(Bayer 抖动零影响);
# 高光/眼键保留原色防"死鱼眼"(调研 §三 _ellipse_poly 高光预混键条款)。
# ---------------------------------------------------------------------------
IRIDES_PHASES = 3            # 相位数(预烘焙桶数;调研 §三护栏超限时降 2)
IRIDES_PERIOD_S = 20.0       # 全循环周期(秒;~6.7s/相位慢速推进)
_HUE_TABLE = (0.0, 120.0, 240.0)          # 相位 → 色相步进(°,均匀铺满色轮)
_HUE_KEEP = ("torso_eye", "torso_eye_gl", "torso_highlight", "eyes")


def hue_phase_now(now: Optional[float] = None) -> int:
    """当前虹色相位桶(0..IRIDES_PHASES-1):墙钟量化,周期 IRIDES_PERIOD_S。

    core/app.py _render_traits 注入 traits["hue_phase"] 时调用;探针/测试可
    传 now 虚拟时钟做相位循环断言。"""
    t = float(time.time() if now is None else now)
    span = IRIDES_PERIOD_S / IRIDES_PHASES
    return int(t / span) % IRIDES_PHASES


def _hue_rotate(c, deg: float):
    """单色 hue 旋转(保 S/L;烘焙期一次性调用,不进逐帧路径)。"""
    r, g, b = _rgb(c)
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    h = (h + deg / 360.0) % 1.0
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return (int(r2 * 255 + 0.5), int(g2 * 255 + 0.5), int(b2 * 255 + 0.5))


# ---------------------------------------------------------------------------
# E3/C1 Q 版(chibi)烘焙系数(调研 §二 参数表;相对现 master 几何)。
# chibi 标志经 traits["chibi"] → _pal_for 塞进 pal["_chibi"](烘焙函数只吃
# pal/rim,签名不变;_SPREAD_PENDING 懒烘焙透传同一 pal)。所有新路径以
# pal.get("_chibi") 门控:关闭态 False → 走原路径逐位一致。
# 注:蟑螂体轮廓=覆翅量测锚点(_TEG_PROF),Q 版不缩放(体长/步频/互锁不变
# 的硬约束);体节 rx×1.12/ry×1.05 落在果蝇胸/腹卵圆(唯一体节椭圆烘焙)。
# ---------------------------------------------------------------------------
_CHIBI_ROACH_HEAD_X0 = 50.0     # 头椭圆后端(藏入背板下)
_CHIBI_ROACH_HEAD_X1 = 75.0     # 头前端(=背板前缘 57 外露 18px ≈ 原露一线 ×4.5)
_CHIBI_ROACH_HEAD_HW = 8.0 * 1.3    # 头半宽 ×1.3(头宽随背板让位)
_CHIBI_EYE_R_K = 3.2            # 眼斑半径 ×3.2
_CHIBI_EYE_X = 62.0             # 眼斑随大头前移(头前 1/3 处)
_CHIBI_EYE_Y = 7.2              # 眼距(眼斑放大后仍完整落在头轮廓内:7.2+2.88<10.4)
_CHIBI_FLY_HEAD_K = 1.6         # 蝇头椭圆 ×1.6(头:体≈1:1.2)
_CHIBI_FLY_EYE_K = 1.15         # 复眼 ×1.15
_CHIBI_FLY_GLA_K = 1.6          # 眼高光 ×1.6
_CHIBI_SEG_RX_K = 1.12          # 体节 rx ×1.12(圆润)
_CHIBI_SEG_RY_K = 1.05          # 体节 ry ×1.05


def _pal_for(sid: str, traits: dict) -> dict:
    base = dict(_ROACH_PAL if sid == "roach" else _FLY_PAL)
    for k in base:
        v = traits.get(k)
        if v is not None:
            base[k] = v
    # E3/C1:虹色相位(hue LUT 预烘焙)与 Q 版标志随 traits 进烘焙;关闭态
    # 两键缺席 → 本函数输出与原实现逐键一致(缓存键/像素零变化)。
    if traits.get("chibi"):
        base["_chibi"] = True
# (ADR-0034:腹面变体随翻面退役)
    # 后经 traits["ventral_view"] 传入 → 独立 _thash → 独立 master(腹面烘焙)。
    # 关闭态键缺席 → 输出与原实现逐键一致(缓存键/像素零变化)。
    if traits.get("ventral_view"):
        base["_ventral"] = True
    if "hue_phase" in traits:
        try:
            ph = int(traits["hue_phase"])
        except (TypeError, ValueError):
            ph = 0
        deg = _HUE_TABLE[ph % IRIDES_PHASES]
        if deg:
            for k in list(base):
                if k in _HUE_KEEP or k == "_chibi":
                    continue
                base[k] = _hue_rotate(base[k], deg)
    return base


# ---------------------------------------------------------------------------
# 缓存与公开接口
# ---------------------------------------------------------------------------

_masters: dict = {}                    # (sid, traits_hash) -> state dict
# 旋转缓存:键 (sid, th, variant, 桶) → (精灵, derived 标记)。
# derived=True 表示存的其实是「对面桶(b∓180°)的精灵」,取出时 transpose(ROTATE_180)
# 即为本桶精灵 —— 恒等式 rotate(m, θ+180°) ≡ rotate(m, θ).transpose(ROTATE_180)
# 逐位成立(纯像素重排,已实测断言),一个 480² 旋转产出两个桶,内存减半。
_rot: "OrderedDict[tuple, tuple]" = OrderedDict()
_rot_bytes = {"roach": 0, "fly": 0}    # 按物种分账的真实字节占用(共用对象只计一次)
_angle_step = 1                        # 角度量化(°);真实字节仍超预算时降为 2
_cache_lock = threading.RLock()        # prewarm 线程与主线程共用的缓存锁


def _norm_sid(species_id: str) -> str:
    s = str(species_id).lower()
    if "roach" in s or "cockroach" in s:
        return "roach"
    if "fly" in s:
        return "fly"
    raise ValueError(f"torso_art: 未知物种 {species_id!r}")


def _thash(traits: dict) -> str:
    try:
        items = sorted((k, repr(v)) for k, v in traits.items())
    except Exception:
        items = [("traits", repr(traits))]
    return hashlib.md5(repr(items).encode("utf-8")).hexdigest()[:12]


def _bake(sid: str, traits: dict) -> dict:
    t0 = time.perf_counter()
    pal = _pal_for(sid, traits)
    rim = _ROACH_RIM if sid == "roach" else _FLY_RIM
    if sid == "roach":
        # 腹面变体(D2):closed/spread 同槽同一张腹面精灵(腹面不见翅盖,
        # spread 懒烘焙永不触发;fold 交叉淡化 blend 同一对象 → 恒等腹面)。
        if pal.get("_ventral"):
            img = _bake_roach_ventral(pal, rim)
            variants = {"closed": img, "spread": img}
        else:
            # closed 立即烘焙;spread(滑翔)懒烘焙 —— 首次 fold<0.985 才需要,
            # 省一份 456² RGBA master(~0.8MB,启动内存门槛的实打实构成)
            variants = {"closed": _bake_roach_closed(pal, rim), "spread": None}
            _SPREAD_PENDING[(sid, _thash(traits))] = (pal, rim)
    else:
        img = _bake_fly_ventral(pal, rim) if pal.get("_ventral") \
            else _bake_fly(pal, rim)
        variants = {"closed": img}
    import gc
    gc.collect()
    return {"sid": sid, "variants": variants,
            "bake_ms": (time.perf_counter() - t0) * 1000.0}


_SPREAD_PENDING: dict = {}             # (sid, traits_hash) → (pal, rim) 懒烘焙登记


def _rot_box(master: Image.Image) -> tuple[tuple[int, int, int, int], int]:
    """母图 → (居中方形裁切框, 裁切后 1x 边长)。OPT-01「降低单桶生成拷贝开销」。

    rotate(expand=False) 的旋转中心 = 画布中心 ((w-1)/2,(h-1)/2),仿射采样只
    依赖「像素到旋转中心的相对坐标」。因此取一个与画布中心 **同心** 的方形
    子窗口去旋转,结果与「整幅旋转后取同一窗口」逐位相同(实测 9 角度 × 2 变体
    最大不等像素 = 0,见 .workbuddy/ag3_paste_probe.txt)。

    边长取 2*SS 的倍数有两个硬约束:
    - **与母图同余**:reduce(SS) 是 SS×SS 盒式平均,窗口起点必须 ≡0 (mod SS),
      否则降采样栅格相对整幅错位(实测 spread 用非对齐框 → 2462 像素不等);
    - **同心**:偶数边长使起点 = (w//2 - side//2),窗口中心恰为 (w-1)/2。
    内容半径用 alpha 逐像素实测,另加 1.5px 覆盖 BILINEAR 的 1px 采样邻域。"""
    a = master.getchannel("A")
    bb = a.getbbox()
    if bb is None:
        return (0, 0, master.width, master.height), master.width // SS
    cx = cy = (master.width - 1) / 2.0
    r2 = 0.0
    px = a.load()
    for x in range(bb[0], bb[2]):
        dx = x - cx
        dx2 = dx * dx
        for y in range(bb[1], bb[3]):
            if px[x, y] > 0:
                dy = y - cy
                d2 = dx2 + dy * dy
                if d2 > r2:
                    r2 = d2
    half = SS * int(math.ceil((math.sqrt(r2) + 1.5) / SS))     # 半边长(SS 的倍数)
    side = min(2 * half, master.width)
    x0 = master.width // 2 - side // 2                          # 同心:中心 (w-1)/2
    return (x0, x0, x0 + side, x0 + side), side // SS


def _rot_geo(st: dict, variant: str) -> tuple[tuple[int, int, int, int], int]:
    """(裁切框, 1x 边长):每变体按需算一次并缓存(spread 懒烘焙也适用)。"""
    g = st.setdefault("rot_geo", {})
    r = g.get(variant)
    if r is None:
        r = _rot_box(st["variants"][variant])
        g[variant] = r
    return r


def _rot_pad(spr: Image.Image, side: int) -> Image.Image:
    """把精灵居中补到统一边长。用 alpha_composite 而非 paste —— paste 以 RGBA
    自身作掩码会对 alpha 再乘一次(实测 α=39 → 6),边缘半透明像素被平方。"""
    if spr.width == side and spr.height == side:
        return spr
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.alpha_composite(spr, ((side - spr.width) // 2, (side - spr.height) // 2))
    return out


def _rot_canon(st: dict, sid: str, need: int) -> int:
    """统一「本状态各变体精灵的 1x 边长」,供 get_torso 的 Image.blend 使用
    (blend 要求同尺寸)。closed/spread 内容半径不同 → 裁切边长不同;首次用到
    更大变体时提升一次,并把已存条目 alpha_composite 重贴(逐位精确、不重建)。"""
    cur = st.get("rot_side")
    if cur is None:
        st["rot_side"] = need
        return need
    if need <= cur:
        return cur
    st["rot_side"] = need
    th = st.get("th")
    with _cache_lock:
        for k in [k for k in _rot if k[0] == sid and k[1] == th]:
            spr, derived = _rot[k]
            if spr.width != need:
                _rot[k] = (_rot_pad(spr, need), derived)
        _rot_bytes[sid] = _rot_recount(sid)
    return need


def _rot_recount(sid: str) -> int:
    """本物种**真实**占用字节:同一张精灵被多个桶共用(180° 派生对)只计一次。

    旧版按条目累加:派生条目与它的对面桶是同一个 PIL 对象却记两遍 → 预算只当
    一半用,容量白丢一半。"""
    seen: set = set()
    total = 0
    for k, (spr, _d) in _rot.items():
        if k[0] != sid or id(spr) in seen:
            continue
        seen.add(id(spr))
        total += spr.width * spr.height * 4
    return total


def _rot_get(st: dict, variant: str, heading_deg: float,
             slack_buckets: int = 0) -> Image.Image:
    """从 master 取「已旋转 + 已降采样」的 1x 小精灵(1° 量化 + LRU)。

    PF/AG3 性能专项:
    - **降低单桶生成拷贝开销**(OPT-01):未命中时只旋转「覆盖全部不透明像素的
      居中方形子窗口」(蟑螂 closed 480²→372²:12.1ms→7.0ms,-42%;单桶字节
      160²→124²,-40%),与整幅旋转逐位等价(见 _rot_box);
    - 未命中先查「对面桶(b∓180°)」:命中则 transpose(ROTATE_180) 推导
      (逐位等价,见 _rot 注释),省一次整幅旋转(蟑螂 ~7ms);
    - **r24 转向连续性:slack 回退**——对面桶也未命中且 slack_buckets>0 时,
      先用 ≤slack 的最近已缓存邻桶精灵(角度误差 ≤slack×step°,快转下不可
      感),精确桶留给跟随预热。首圈转向/护栏清空后的重建期,渲染成本由此
      封顶在命中级,不再把 _tick 拖超预算(活体实测见 scratch/_r24_turn);
    - 果蝇也进缓存(52² 条目 ~10.5KB;旧版每帧旋转 ~1.2ms);
    - 双检锁:重活(旋转)在锁外做,prewarm 线程的旋转不会卡主线程的缓存
      命中查询;写入/淘汰在锁内。"""
    global _angle_step
    if variant == "spread" and st["variants"].get("spread") is None:
        with _cache_lock:
            pal_rim = _SPREAD_PENDING.pop((st["sid"], st["th"]), None)
            if pal_rim is not None:
                st["variants"]["spread"] = _bake_roach_spread(*pal_rim)
            else:
                st["variants"]["spread"] = st["variants"]["closed"]
    step = _angle_step
    nb = 360 // step
    b = int(round(heading_deg / step)) % nb
    sid = st["sid"]
    key = (sid, st["th"], variant, b)
    with _cache_lock:
        entry = _rot.get(key)
        if entry is not None:
            _rot.move_to_end(key)
    if entry is not None:
        spr, derived = entry
        _I.bump("rot_hit")                     # r25 U0:精确桶命中
        return spr.transpose(Image.ROTATE_180) if derived else spr

    # ---- 未命中:先试 180° 对面桶 ----
    pkey = (sid, st["th"], variant, (b + nb // 2) % nb)
    with _cache_lock:
        pentry = _rot.get(pkey)
        if pentry is not None:
            _rot.move_to_end(pkey)
    if pentry is not None:
        pspr, pderived = pentry
        _I.bump("rot_180")                     # r25 U0:180° 派生命中(廉价)
        if pderived:
            # 对面桶存的又是「它的对面」= 本桶精灵:直接复用,转正存储
            _rot_store(key, pspr, False, sid)
            return pspr
        # 对面桶存的是 S(b+180°):本桶 = transpose(它),仍存对面精灵(标记派生)
        _rot_store(key, pspr, True, sid)
        return pspr.transpose(Image.ROTATE_180)

    # ---- r24:对面也未命中 → slack 内最近邻桶(纯命中级成本,不烘焙) ----
    if slack_buckets > 0:
        th = st["th"]
        with _cache_lock:
            for d in range(1, slack_buckets + 1):
                for bb in ((b - d) % nb, (b + d) % nb):
                    e = _rot.get((sid, th, variant, bb))
                    if e is not None:
                        _rot.move_to_end((sid, th, variant, bb))
                        nspr, nderived = e
                        _I.bump("rot_slack")       # r25 U0:slack 邻桶命中
                        return (nspr.transpose(Image.ROTATE_180)
                                if nderived else nspr)

    # ---- 全未命中:贵路径(蟑螂裁切旋转 ~7ms),锁外构建 ----
    # r25 U0:命中率/稀释记账 —— 首见=首圈预热;重见=曾构建过又被淘汰
    # (预算被同物种多 traits/缩放组稀释的机制性证据)。
    _first = _I.seen("rot_keys_built", key)
    _I.bump("rot_miss")
    _I.bump("rot_miss_first" if _first else "rot_miss_rebuild")
    _I.seen("rot_groups", (sid, st["th"], variant))
    if threading.current_thread() is threading.main_thread():
        _I.bump("rot_miss_main")               # 打进帧预算的那一类
    _t_build = _I.begin()
    box, s1x = _rot_geo(st, variant)
    canon = _rot_canon(st, sid, s1x)
    rot = (st["variants"][variant].crop(box)
           .rotate(-b * step, resample=Image.BILINEAR, expand=False))
    spr = _rot_pad(rot.reduce(SS), canon)
    _I.end("rot_build_ms", _t_build)
    _rot_store(key, spr, False, sid)
    return spr


def _rot_store(key, spr: Image.Image, derived: bool, sid: str) -> None:
    """写入旋转缓存 + 按物种**真实字节**预算淘汰(锁内调用)。"""
    global _angle_step, _rot_bytes
    with _cache_lock:
        _rot[key] = (spr, derived)
        _rot.move_to_end(key)
        _rot_bytes[sid] = _rot_recount(sid)
        _rot_evict(sid)
        if _rot_bytes["roach"] > _ROT_BUDGET["roach"] and _angle_step < 2:
            # 最后一道兜底:真实字节仍超预算(单张精灵即超限等极端情形)。
            # 正常情况下 _rot_evict 已把占用压到预算内,生产内存阶梯由
            # app.py 的 GUARD_ANGLE_STEP/GUARD_ROT_BUDGET 主导。
            _angle_step = 2
            _rot.clear()
            _rot_bytes = {"roach": 0, "fly": 0}
            _I.bump("rot_cache_cleared")       # r25 U0:兜底清空(全圈重建)


def _rot_evict(sid: str) -> None:
    """淘汰到预算/条目上限内,按 LRU 顺序**整组**丢弃。

    一组 = 共用同一 PIL 对象的全部桶(本桶 + 对面派生桶)。旧版按条目淘汰:
    派生条目的对面桶还持有同一张精灵 → 淘汰一个不释放任何字节,预算形同翻倍。"""
    budget = _ROT_BUDGET.get(sid, 1024 * 1024)
    while _rot_bytes[sid] > budget or _species_count(sid) > _ROT_ENTRY_CAP:
        victim = next((k for k in _rot if k[0] == sid), None)
        if victim is None:
            break
        img = _rot[victim][0]
        for k in [k for k, v in _rot.items() if k[0] == sid and v[0] is img]:
            del _rot[k]
        _I.bump("rot_evict_groups")            # r25 U0:LRU 整组淘汰(LRU 抖动证据)
        _rot_bytes[sid] = _rot_recount(sid)


def _species_count(sid: str) -> int:
    return sum(1 for k in _rot if k[0] == sid)


def _master_state(sid: str, th: str, traits: dict) -> dict:
    """取(或一次性烘焙)物种 master 状态;烘焙在锁外做(≤400ms,不能让
    prewarm 线程持锁卡住主线程首帧),插入时双检:后到者丢弃自己的副本。"""
    key = (sid, th)
    st = _masters.get(key)
    if st is not None:
        return st
    st = _bake(sid, traits)
    st["th"] = th
    with _cache_lock:
        return _masters.setdefault(key, st)


def get_torso(species_id: str, half: int, heading_deg: float,
              fold: float, traits: dict, slack_deg: float = 0.0) -> Image.Image:
    """冻结接口:获取当前朝向的躯干精灵(旋转已合成,尺寸 (2*half, 2*half))。

    fold:1.0=翅盖收拢(地面)0.0=展开(飞行/滑翔);中间值=两变体交叉淡化。
    果蝇忽略 fold(静息折翅烘焙在精灵内,飞行翅由底盘矢量叠加)。
    slack_deg(r24 转向连续性):快转时允许的「最近已缓存桶」回退半径(°)。
    未命中精确桶时先用 ≤slack 的邻桶精灵(高速下数度误差不可感),精确桶
    交给跟随预热;渲染成本因此封顶在命中级,不再被 ~7ms 桶烘焙拖超帧。"""
    sid = _norm_sid(species_id)
    half = max(4, int(half))
    th = _thash(traits)
    st = _master_state(sid, th, traits)
    hd = float(heading_deg)
    fold = max(0.0, min(1.0, float(fold)))
    slack_b = max(0, int(float(slack_deg) / _angle_step))
    if sid == "fly" or fold >= 0.985:
        spr = _rot_get(st, "closed", hd, slack_b)
    elif fold <= 0.015:
        spr = _rot_get(st, "spread", hd, slack_b)
    else:
        a = _rot_get(st, "closed", hd, slack_b)
        b = _rot_get(st, "spread", hd, slack_b)
        if a.size != b.size:
            # 首次用到 spread 时统一边长刚被提升,本帧的 closed 精灵可能是提升前
            # 取出的旧尺寸(内容半径不同 → 裁切边长不同)。补边后 blend 逐位等价。
            n = max(a.width, b.width)
            a, b = _rot_pad(a, n), _rot_pad(b, n)
        spr = Image.blend(a, b, 1.0 - fold)      # fold 1→0:闭合→展开 交叉淡化
    size = half * 2
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(spr, ((size - spr.width) // 2, (size - spr.height) // 2), spr)
    return out


def prewarm(species_id: str, traits: dict, heading_deg: float = 0.0,
            fold: float = 1.0, span_deg: float = 70.0,
            pace_s: float = 0.0) -> int:
    """预热旋转缓存(PF 性能专项):围绕 heading_deg ±span_deg 逐桶构建,
    与逐帧路径走同一 _rot_get 实现(逐位等价,只是提前付费)。

    供 App 启动时的后台线程调用(PIL 旋转释放 GIL,不阻塞渲染主线程):
    宠物起步/转弯会扫过一片 1° 桶,逐帧未命中在蟑螂上是 ~12ms/帧的重活;
    提前把起始朝向附近 ±24° 铺满后,转弯首圈即全程命中。
    fold 决定预热哪些变体(蟑螂滑翔态需 spread;果蝇恒 closed)。
    pace_s>0 时每个桶之间 sleep 该时长 —— 后台线程让出 CPU,把对渲染
    主线程的 GIL/内存带宽干扰压到最低(实测无节流时主线程帧耗时 ~2×)。
    返回本次真正新构建的桶数(含 180° 对称推导,不计为构建)。"""
    sid = _norm_sid(species_id)
    th = _thash(traits)
    st = _master_state(sid, th, traits)
    step = _angle_step
    nb = 360 // step
    b0 = int(round(heading_deg / step)) % nb
    if sid == "fly" or fold >= 0.985:
        variants = ("closed",)
    elif fold <= 0.015:
        variants = ("spread",)
    else:
        variants = ("closed", "spread")
    built = 0
    with _cache_lock:
        known = {k[3] for k in _rot if k[0] == sid and k[1] == th}
    for variant in variants:
        for d in range(0, int(span_deg / step) + 1):
            for b in ((b0 + d) % nb, (b0 - d) % nb):
                if d == 0 and b != b0:
                    continue
                if (b not in known and (b + nb // 2) % nb not in known):
                    built += 1
                _rot_get(st, variant, b * step)
                if pace_s > 0:
                    time.sleep(pace_s)
    return built


def first_missing_bucket(species_id: str, traits: dict, heading_deg: float,
                         fold: float = 1.0, sign: float = 1.0,
                         span: int = 36, parity: int = -1) -> Optional[float]:
    """从 heading_deg 沿 sign 方向向外扫描,返回最近的缺失桶角度(度)。

    供 App 的跟随式预热线程用:traits 哈希只算一次(MD5 ~15µs,扫描 74 个
    候选桶若每次都算会显著挤占渲染主线程的 GIL);parity≥0 时只返回
    round(桶) % 2 == parity 的候选(双线程奇偶分工,前沿推进速度翻倍)。
    全部已覆盖时返回 None。"""
    sid = _norm_sid(species_id)
    th = _thash(traits)
    step = _angle_step
    nb = 360 // step
    b0 = int(round(heading_deg / step)) % nb
    if sid == "fly" or fold >= 0.985:
        variants = ("closed",)
    elif fold <= 0.015:
        variants = ("spread",)
    else:
        variants = ("closed", "spread")
    with _cache_lock:
        if (sid, th) not in _masters:
            return float(b0 * step)          # master 还没烘焙:先把中心补上
        for k in range(0, span + 1):
            for b in ((b0 + int(sign) * k) % nb, (b0 - int(sign) * k) % nb):
                if parity >= 0 and b % 2 != parity % 2:
                    continue
                if all((sid, th, v, b) in _rot or
                       (sid, th, v, (b + nb // 2) % nb) in _rot
                       for v in variants):
                    continue
                return float(b * step)
    return None


def has_bucket(species_id: str, traits: dict, heading_deg: float,
               fold: float = 1.0) -> bool:
    """查询指定朝向的旋转精灵是否已缓存(含 180° 对面桶可推导的情况)。

    供 App 的"预测性跳帧"用:蟑螂首遇新 1° 桶是一次 ~12-13ms 的 480² 旋转,
    与其让该帧超预算 2 倍,不如让 App 本 tick 跳过该宠渲染(下一 tick 精灵
    已由预热线程备好)。只读缓存,不构建。"""
    sid = _norm_sid(species_id)
    th = _thash(traits)
    step = _angle_step
    nb = 360 // step
    b = int(round(heading_deg / step)) % nb
    if sid == "fly" or fold >= 0.985:
        variants = ("closed",)
    elif fold <= 0.015:
        variants = ("spread",)
    else:
        variants = ("closed", "spread")     # 交叉淡化帧:两变体都必须在
    with _cache_lock:
        if (sid, th) not in _masters:
            return False
        for variant in variants:
            if (sid, th, variant, b) in _rot:
                continue
            if (sid, th, variant, (b + nb // 2) % nb) in _rot:
                continue
            return False
    return True


def invalidate(species_id: Optional[str] = None) -> None:
    """清空烘焙/旋转缓存;species_id 缺省时全清。"""
    global _angle_step
    with _cache_lock:
        if species_id is None:
            _masters.clear()
            _rot.clear()
            _rot_bytes = {"roach": 0, "fly": 0}
            _angle_step = 1
            return
        sid = _norm_sid(species_id)
        for k in [k for k in _masters if k[0] == sid]:
            del _masters[k]
        for k in [k for k in _rot if k[0] == sid]:
            del _rot[k]
        _rot_bytes[sid] = _rot_recount(sid)
        if not _masters:
            _angle_step = 1
