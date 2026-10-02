"""AG3 折翅终版 —— 像素级断言固化(python tests/test_wing_fold.py 直跑)。

判据全部落在**像素统计/几何量测**上,不用"看起来像":
  ① 色键合规          : get_torso 输出中间 α(1..254) 画布占比 <5%,内部孔洞 == 0
  ② 腹末 0 外露       : closed 烘焙路径不调用 _roach_abdomen;尾部区域腹末特征色 == 0
  ③ 翅端 U 形收口     : 翅尖极值 -56~-54.5px、极值列半宽 ≤3.5、收口深度 ≥0.8、无内凹
  ④ 琥珀后域与纵向渐变: 对**规格字面锚点**命中 ≥500px;内芯中位亮度单调↑且增益 ≥40
  ⑤ 平行细亮脉纹      : 4 条 k 线各 13 点,亮于**实测局部背景中位** ≥70% 的采样点
  ⑥ 尾须外露两小截    : 每侧 15~220px、可见长 2.0~6.5px
  ⑦ spread 外露横纹腹末: 横环带/节间亮带像素均 >0(展开变体必须保留)
  ⑧ fold 1↔0 无跳变   : 21 帧定画布扫描,相邻帧 MAD 平滑度 max(步/邻均) ≤2.5
  ⑨ 果蝇静息翅        : 翅对盖腹外露 ≤0.45px、翅尖超腹端 0.05~0.08BL、隔翅暗带 ≥3/4
  ⑩ 旋转桶(OPT-01/08): 居中裁切旋转与整幅旋转逐位等价;get_torso 输出与"整幅旋转 +
     旧贴图路径"逐位一致;180° 派生对共用同一精灵(真实字节只计一次);预算不被突破

判据编号与 docs/references/virtual_cockroach_移植规格.md §4 + 交接文档 §五 对应。
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import Counter, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops  # noqa: E402

import neuropet.render.torso_art as TA  # noqa: E402
from neuropet.render import torso as TORSO  # noqa: E402

HALF = 120                      # get_torso 画布半宽(应用内蟑螂档,与 test_render_budget 同口径)
FLY_HALF = 78
RM = TA._ROACH_R_MAX            # 母图半宽(母图坐标系;master() 的 local 标尺)
RF = TA._FLY_R_MAX
TRAITS = {
    "roach": {"species_id": "species.cockroach"},
    "fly": {"species_id": "species.fruitfly"},
}
SID = {"roach": "species.cockroach", "fly": "species.fruitfly"}
FAILS: list[str] = []


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


# ---------------------------------------------------------------- 小工具
def pal_of(sid: str) -> dict:
    return TA._pal_for(sid, TRAITS[sid])


def master(sid: str, variant: str = "closed") -> Image.Image:
    p = pal_of(sid)
    rim = TA._ROACH_RIM if sid == "roach" else TA._FLY_RIM
    if sid == "fly":
        return TA._bake_fly(p, rim)
    return (TA._bake_roach_closed(p, rim) if variant == "closed"
            else TA._bake_roach_spread(p, rim))


def scale_of(m: Image.Image, half: int) -> float:
    return m.width / float(2 * half)


def px_at(m: Image.Image, half: int, x: float, y: float):
    s = scale_of(m, half)
    c = m.width / 2.0
    return m.load()[int(round(c + x * s)), int(round(c + y * s))]


def lum(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def near(c, t, tol: int) -> bool:
    return (abs(c[0] - t[0]) <= tol and abs(c[1] - t[1]) <= tol
            and abs(c[2] - t[2]) <= tol)


def alpha_stats(im: Image.Image) -> tuple[int, int, float]:
    """(中间 α 像素数, 内部孔洞数, 中间 α 画布占比)。"""
    a = im.getchannel("A")
    hist = a.histogram()
    mid = sum(hist[1:255])
    total = im.size[0] * im.size[1]
    px = a.load()
    w, h = im.size
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):                                   # 从画布边界泛洪
        for y in (0, h - 1):
            if px[x, y] < 129 and not seen[y * w + x]:
                seen[y * w + x] = 1
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if px[x, y] < 129 and not seen[y * w + x]:
                seen[y * w + x] = 1
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h:
                i = ny * w + nx
                if not seen[i] and px[nx, ny] < 129:
                    seen[i] = 1
                    q.append((nx, ny))
    holes = sum(1 for i in range(w * h) if not seen[i] and px[i % w, i // w] < 129)
    return mid, holes, mid / float(total)


def cercus_color(pal: dict):
    return TA._mix(TA._rgb(pal.get("torso_leg_core", "#933c10")), (0, 0, 0), 0.15)


def wing_columns(m: Image.Image, half: int, pal: dict, x_lo: float = -35.0) -> list:
    """尾区每列的「翅」半宽:α>128 且**排除尾须色**的像素跨度,按 x 递增返回
    (cols[0] = 最靠后的一列 = 翅端)。尾须在 -49~-57、|y|≥4.4,翅的渐变底色要到
    x≥-30 才接近尾须色,故只对 x < x_lo 的列做排除 —— 不排除会把尾须读成翅端。"""
    s = scale_of(m, half)
    c = m.width / 2.0
    cc = cercus_color(pal)
    px = m.load()
    cols = []
    for x in range(m.width):
        xs_local = (x - c) / s
        ys = [y for y in range(m.height)
              if px[x, y][3] > 128
              and not (xs_local < x_lo and near(px[x, y], cc, 24))]
        if ys:
            cols.append((x, (max(ys) - min(ys) + 1) / 2.0 / s))
    return cols


# ---------------------------------------------------------------- ① 色键
def test_chroma_alpha() -> None:
    print("\n[① 色键 alpha 完整性]")
    for sid, fold in (("roach", 1.0), ("roach", 0.5), ("roach", 0.0),
                      ("fly", 1.0)):
        im = TORSO.get_torso(SID[sid], HALF if sid == "roach" else FLY_HALF,
                             -90.0, fold, TRAITS[sid])
        mid, holes, frac = alpha_stats(im)
        check(frac < 0.05, f"{sid} fold={fold}: 中间α画布占比 {frac:.2%} < 5%")
        check(holes == 0, f"{sid} fold={fold}: 内部孔洞 {holes} == 0 (中间α {mid}px)")


# ---------------------------------------------------------------- ② 腹末 0 外露
def test_no_abdomen_in_closed() -> None:
    print("\n[② closed 腹末 0 外露]")
    calls = Counter()
    orig = TA._roach_abdomen

    def spy(cv, pal, *a, **k):
        calls["n"] += 1
        return orig(cv, pal, *a, **k)
    TA._roach_abdomen = spy
    try:
        TA._bake_roach_closed(pal_of("roach"), TA._ROACH_RIM)
    finally:
        TA._roach_abdomen = orig
    check(calls["n"] == 0,
          f"closed 烘焙路径 _roach_abdomen 调用 {calls['n']} 次 == 0（几何保证，"
          f"非 z 序遮挡）")
    calls["n"] = 0
    TA._roach_abdomen = spy
    try:
        TA._bake_roach_spread(pal_of("roach"), TA._ROACH_RIM)
    finally:
        TA._roach_abdomen = orig
    check(calls["n"] > 0, f"spread 烘焙仍调用 _roach_abdomen {calls['n']} 次 > 0")

    m = master("roach")
    p = pal_of("roach")
    band = TA._rgb(p["torso_ab_band"])
    light = TA._rgb(p["torso_ab_light"])
    s = scale_of(m, RM)
    c = m.width / 2.0
    px = m.load()
    nb = nl = 0
    for x in range(int(c - 57.5 * s), int(c - 30.0 * s)):
        for y in range(m.height):
            q = px[x, y]
            if q[3] > 128:
                nb += near(q, band, 12)
                nl += near(q, light, 12)
    check(nb == 0 and nl == 0,
          f"尾部 x∈[-57.5,-30] 腹末特征色像素 横环带={nb} 节间亮带={nl} 均 == 0")


# ---------------------------------------------------------------- ③ U 形收口
def test_tail_u_shape() -> dict:
    print("\n[③ 翅端 U 形收口几何]")
    m = master("roach")
    pal = pal_of("roach")
    cols = wing_columns(m, RM, pal)
    s = scale_of(m, RM)
    c = m.width / 2.0
    tip_x = (cols[0][0] - c) / s
    face_hw = cols[0][1]
    flat = TA._TEG_PROF[-1][1]
    depth = 0.0
    for x, hw in cols:
        if hw >= flat:
            depth = (x - cols[0][0]) / s
            break
    mono = 0.0
    for (_, h0), (_, h1) in zip([c0 for c0 in cols if (c0[0] - c) / s <= -20.0],
                                [c0 for c0 in cols if (c0[0] - c) / s <= -20.0][1:]):
        mono = max(mono, h0 - h1)
    check(-56.0 <= tip_x <= -54.5, f"翅尖极值 x={tip_x:.2f} ∈ [-56,-54.5]px")
    check(1.5 <= face_hw <= 3.5,
          f"极值列半宽 {face_hw:.2f} ∈ [1.5,3.5](规格端部 2~3px)")
    check(depth >= 0.8, f"U 收口深度 {depth:.2f} ≥ 0.8px(平切=0)")
    check(mono <= 0.8, f"尾段向内最大回缩 {mono:.2f} ≤ 0.8px(无内凹缺口)")
    # U 形 vs「锥形收尖」的判别(AG3 FIX-5 新增):在 [收口起点, 翅尖] 的中点处,
    # 半宽应显著高于「收口起点半宽 → 端面半宽」的**线性**值 —— 四分之一椭圆在
    # 该点给出 0.866×(实测 7.3/8.9 = 0.82),线性锥只有 0.5×。旧实现正是线性锥。
    x_mid = (TA._TEG_X1 + tip_x) / 2.0
    near = min(cols, key=lambda cc: abs((cc[0] - c) / s - x_mid))
    hw_mid = near[1]
    check(hw_mid >= 0.75 * flat,
          f"收口中点半宽 {hw_mid:.2f} ≥ 0.75×收口起点半宽 {flat:.2f}"
          f"(椭圆=7.8/线性锥=5.2 —— 判 U 不是锥)")
    return {"tip_x": tip_x, "cols": cols}


# ---------------------------------------------------------------- ④⑤ 琥珀 + 脉纹
def test_amber_and_veins(geo: dict) -> None:
    print("\n[④ 琥珀后域 + 纵向渐变]")
    m = master("roach")
    s = scale_of(m, RM)
    c = m.width / 2.0
    px = m.load()
    anchors = ((43.0, (0x7a, 0x2f, 0x1d)), (-2.0, (0x6e, 0x34, 0x13)),
               (-40.0, (0xa0, 0x6a, 0x3a)), (-55.5, (0xd7, 0x9e, 0x57)))

    def spec_col(x):
        if x >= anchors[0][0]:
            return anchors[0][1]
        for i in range(3):
            x0, c0 = anchors[i]
            x1, c1 = anchors[i + 1]
            if x >= x1:
                return TA._mix(c0, c1, (x0 - x) / max(1e-3, x0 - x1))
        return anchors[3][1]

    want = {i: spec_col(-40.0 - 1.9 * i) for i in range(9)}
    n_amb = 0
    for x in range(int(c - 55.5 * s), int(c - 40.0 * s)):
        for y in range(m.height):
            q = px[x, y]
            if q[3] > 128 and any(near(q, t, 8) for t in want.values()):
                n_amb += 1
    check(n_amb >= 500, f"琥珀后域对规格锚点命中 {n_amb}px ≥ 500")

    ramp = []
    for x in (0.0, -12.0, -25.0, -38.0, -48.0, -53.0):
        hw = TA._teg_env_in(x)
        vals = sorted(lum(px_at(m, RM, x, (-0.8 + 1.6 * i / 24) * hw))
                      for i in range(25))
        ramp.append(round(vals[len(vals) // 2], 1))
    check(all(b >= a - 0.5 for a, b in zip(ramp, ramp[1:])),
          f"内芯中位亮度单调↑ {ramp}")
    check(ramp[-1] - ramp[0] >= 40.0,
          f"中段→尾端亮度增益 {ramp[-1] - ramp[0]:.1f} ≥ 40(权威 §2 最强特征)")

    print("\n[⑤ 平行细亮脉纹]")
    hits = 0
    lanes = []
    for k in TA._VEIN_K:
        yk, x_end = TA._vein_lane(k)
        lanes.append(yk)
        h = t = 0
        for side in (-1, 1):
            # 只取线段**内部**采样点(i=1..11):脉纹按 3 段折线绘制、端点是平头
            # 收笔,恰在端点取样时窗口可能落在收笔之外(实测端点样本 Δ=0 是采样
            # 假失,不是对比度不足)。
            for i in range(1, 12):
                f = i / 12.0
                x = TA._VEIN_X0 + (x_end - TA._VEIN_X0) * f
                # y 恒定 = 真平行于中缝(AG3 FIX-5:_vein_lane 是渲染/测试同源的车道,
                # 旧版按 y=k·env(x) 取样既与"平行"矛盾,也盖住了"读成扇骨"的缺陷)
                yl = side * yk
                hw = TA._teg_env_in(x)
                core = [lum(px_at(m, RM, x, side * j / 12.0 * hw * 0.9))
                        for j in range(1, 13)]
                bg = sorted(core)[len(core) // 2]
                peak = max(lum(px_at(m, RM, x, yl + d / s))
                           for d in range(-2, 3))
                t += 1
                h += 1 if peak - bg >= 10.0 else 0
        ok = h / t >= 0.7
        hits += ok
        print(f"    k={k:.2f} |y|={yk:5.2f}px 止于 x={x_end:6.2f}: "
              f"亮线采样 {h}/{t} ({'达标' if ok else '不足'})")
    check(hits == 4, f"4 条平行车道均亮于实测局部背景 ≥70% 采样点 → {hits}/4")
    gaps = [b - a for a, b in zip(lanes, lanes[1:])]
    check(all(2.0 <= g <= 3.0 for g in gaps),
          f"车道间隔 {[round(g, 2) for g in gaps]}px 全落在规格「间隔 2~3px」内")
    check(lanes == sorted(lanes) and len(set(lanes)) == 4,
          f"4 条车道 |y| 互异且递增(互不重叠)= {[round(v, 2) for v in lanes]}")


# ---------------------------------------------------------------- ⑥ 尾须
def test_cerci(geo: dict) -> None:
    print("\n[⑥ 尾须外露两小截]")
    m = master("roach")
    pal = pal_of("roach")
    s = scale_of(m, RM)
    c = m.width / 2.0
    cc = cercus_color(pal)
    px = m.load()
    # 翅端外露段窗口 x∈[-60,-51.5]、|y|≥2.5px(避开翅面中轴,只量露在翅外的两截)
    for side, name in ((-1, "上"), (1, "下")):
        xs = []
        for x in range(int(c - 60.0 * s), int(c - 51.5 * s)):
            for y in range(m.height):
                q = px[x, y]
                if q[3] >= 128 and near(q, cc, 24):
                    yy = (y - c) / s
                    if abs(yy) >= 2.5 and (yy < 0) == (side < 0):
                        xs.append(x)
        n = len(xs)
        ln = (max(xs) - min(xs)) / s if xs else 0.0
        check(15 <= n <= 220, f"{name}侧外露像素 {n} ∈ [15,220]")
        check(2.0 <= ln <= 6.5, f"{name}侧可见长 {ln:.2f}px ∈ [2.0,6.5]")


# ---------------------------------------------------------------- ⑦ spread
def test_spread_abdomen() -> None:
    print("\n[⑦ spread 保留外露横纹腹末]")
    m = master("roach", "spread")
    p = pal_of("roach")
    band = TA._rgb(p["torso_ab_band"])
    light = TA._rgb(p["torso_ab_light"])
    s = scale_of(m, RM)
    c = m.width / 2.0
    px = m.load()
    nb = nl = 0
    for x in range(int(c - 57.5 * s), int(c - 30.0 * s)):
        for y in range(m.height):
            q = px[x, y]
            if q[3] > 128:
                nb += near(q, band, 12)
                nl += near(q, light, 12)
    check(nb > 0 and nl > 0, f"spread 腹末 横环带={nb} 节间亮带={nl} 均 > 0")


# ---------------------------------------------------------------- ⑧ fold 无跳变
def test_fold_no_jump() -> None:
    print("\n[⑧ fold 1→0 交叉淡化无跳变]")
    n = 21
    ims = []
    for i in range(n):
        f = 1.0 - i / (n - 1)
        ims.append(TORSO.get_torso(SID["roach"], HALF, -90.0, f, TRAITS["roach"]))
    sizes = {im.size for im in ims}
    check(len(sizes) == 1, f"全程画布定尺寸 {sizes}")
    mads = []
    for a, b in zip(ims, ims[1:]):
        pa, pb = list(a.getdata()), list(b.getdata())
        d = [abs(pa[i][k] - pb[i][k]) for i in range(len(pa)) for k in range(4)]
        mads.append(sum(d) / len(d))
    steps = [mads[i] / max(1e-9, (mads[i - 1] + mads[i + 1]) / 2.0)
             for i in range(1, len(mads) - 1)]
    check(mads[0] >= 0.0005 and mads[-1] >= 0.0005,
          f"有真实过渡(首帧 MAD={mads[0]:.4f} 末帧 MAD={mads[-1]:.4f} ≥5e-4)")
    check(max(steps) <= 2.5,
          f"帧差平滑度 max(步/邻均)={max(steps):.2f} ≤ 2.5(无突增)")
    check(sum(mads) > 0.15, f"累计变化 {sum(mads):.3f} > 0.15(确实在淡化)")


# ---------------------------------------------------------------- ⑨ 果蝇静息翅
def test_fly_rest_wing() -> None:
    print("\n[⑨ 果蝇静息翅]")
    m = master("fly")
    hf = TA._FLY_R_MAX
    s = scale_of(m, hf)
    c = m.width / 2.0
    px = m.load()
    wing_c = TA._rgb(TA._FLY_PAL["torso_wing_rest"])
    ab_c = TA._rgb(TA._FLY_PAL["torso_abdomen"])
    band_c = TA._rgb(TA._FLY_PAL["torso_abdomen_band"])

    def rear_x(target, tol=14):
        found = None
        for x in range(m.width // 2, -1, -1):
            for y in range(m.height):
                p = px[x, y]
                if p[3] > 128 and near(p, target, tol):
                    found = x
                    break
        return (found - c) / s if found is not None else None

    wx, ax = rear_x(wing_c), rear_x(ab_c)
    check(wx is not None and ax is not None, "翅尖/腹端均可测")
    over = (ax - wx) / 30.0
    check(0.05 <= over <= 0.08,
          f"翅尖超腹端 {ax - wx:.2f}px = {over:.3f}BL ∈ [0.05,0.08]")
    worst = (0.0, 0.0, 0.0)
    for x in (-6.8, -10.6, -14.4, -18.2):
        # 该列 α>128 的**像素数**(= 该处全宽;翅与腹之间若有缝,跨度会大于像素数)
        xi = int(round(c + x * s))
        hist = m.getchannel("A").crop((xi, 0, xi + 1, m.height)).histogram()
        n = sum(hist[129:])
        if n:
            vis = n / 2.0 / s
            gap = TA._ab_env(x) - vis
            if gap > worst[0]:
                worst = (gap, x, vis)
    check(worst[0] <= 0.45,
          f"翅对盖腹: X={worst[1]} 腹半宽 {TA._ab_env(worst[1]):.2f} - 可见半宽 "
          f"{worst[2]:.2f} = 外露 {worst[0]:.2f}px ≤ 0.45")
    ok_b = 0
    for bx in (-6.8, -10.6, -14.4, -18.2):
        xi = int(c + bx * s)
        prof = [px[xi, y] for y in range(m.height) if px[xi, y][3] > 128]
        if not prof:
            continue
        lo = min(prof, key=lambda p: sum(p[:3]))
        if lo[0] < wing_c[0] - 15 and abs(lo[0] - band_c[0]) < 90:
            ok_b += 1
    check(ok_b >= 3, f"隔翅环带: 环带位可读暗带 {ok_b}/4 ≥ 3")


# ---------------------------------------------------------------- ⑩ 旋转桶
def test_rot_bucket_equivalence() -> None:
    print("\n[⑩ 旋转桶 OPT-01:等价性 / 真实字节 / 预算]")
    pal = pal_of("roach")
    worst = 0
    canon = 0
    size = HALF * 2
    for variant, bake in (("closed", TA._bake_roach_closed),
                          ("spread", TA._bake_roach_spread)):
        m = bake(pal, TA._ROACH_RIM)
        box, s1x = TA._rot_box(m)
        canon = max(canon, s1x)
        check(box[0] % TA.SS == 0 and (box[2] - box[0]) % (2 * TA.SS) == 0,
              f"{variant} 裁切框 {box} 与母图降采样栅格同余(起点%SS==0, 边%2SS==0)")
        for d in range(0, 360, 11):
            old = m.rotate(-d, resample=Image.BILINEAR, expand=False).reduce(TA.SS)
            new = TA._rot_pad(m.crop(box).rotate(
                -d, resample=Image.BILINEAR, expand=False).reduce(TA.SS), canon)
            oa = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            oa.paste(old, ((size - old.width) // 2, (size - old.height) // 2), old)
            ob = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ob.paste(new, ((size - new.width) // 2, (size - new.height) // 2), new)
            worst = max(worst, sum(1 for q in ImageChops.difference(oa, ob)
                                   .convert("RGBA").getdata() if any(q)))
    check(worst == 0, f"33 角度 × 2 变体:裁切旋转 vs 整幅旋转 最大不等像素 {worst} == 0")

    # 180° 派生对共用同一精灵对象 → 真实字节只计一次
    TORSO.invalidate()
    tr = TRAITS["roach"]
    for hd in (0.0, 180.0):
        TORSO.get_torso(SID["roach"], HALF, hd, 1.0, tr)
    keys = [k for k in TA._rot if k[0] == "roach"]
    objs = {id(TA._rot[k][0]) for k in keys}
    naive = sum(TA._rot[k][0].width * TA._rot[k][0].height * 4 for k in keys)
    real = TA._rot_bytes["roach"]
    check(len(objs) < len(keys) and real < naive,
          f"180° 派生对共用对象(条目 {len(keys)} / 对象 {len(objs)});"
          f"真实字节 {real} < 条目累加 {naive}")
    check(real <= TA._ROT_BUDGET["roach"],
          f"真实字节 {real} ≤ 预算 {TA._ROT_BUDGET['roach']}")

    # 大量角度扫过:真实字节与条目数都不得越界
    for d in range(0, 360, 2):
        TORSO.get_torso(SID["roach"], HALF, float(d), 1.0, tr)
    check(TA._rot_bytes["roach"] <= TA._ROT_BUDGET["roach"],
          f"360° 扫过后真实字节 {TA._rot_bytes['roach']} ≤ 预算"
          f" {TA._ROT_BUDGET['roach']}")
    check(len([k for k in TA._rot if k[0] == "roach"]) <= TA._ROT_ENTRY_CAP,
          f"条目数 {len([k for k in TA._rot if k[0] == 'roach'])}"
          f" ≤ 硬上限 {TA._ROT_ENTRY_CAP}")
    TORSO.invalidate()


def main() -> None:
    print("=" * 74)
    print("AG3 折翅终版 像素级断言")
    print("=" * 74)
    test_chroma_alpha()
    test_no_abdomen_in_closed()
    geo = test_tail_u_shape()
    test_amber_and_veins(geo)
    test_cerci(geo)
    test_spread_abdomen()
    test_fold_no_jump()
    test_fly_rest_wing()
    test_rot_bucket_equivalence()
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
