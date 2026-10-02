"""躯干精灵库 —— 冻结接口(W1 渲染底盘 与 W2 形态资产 的唯一协作点)。

语义:返回"已完成 heading 旋转"的躯干 RGBA 图(PIL.Image),尺寸 (2*half, 2*half),
躯干中心对齐画布中心;渲染底盘直接贴到宠物画布中心(altitude 的整体抬升由底盘处理)。

- species_id:  "species.cockroach" | "species.fruitfly"(与 SpeciesPlugin manifest.id 一致)
- heading_deg: 朝向角(度,与 pose 的 heading 弧度一致的方向,0=朝右,顺时针为正)
- fold:        1.0=翅/翅盖收拢(地面),0.0=展开(飞行/滑翔);中间值=过渡帧
- traits:      species.render_traits() 的返回值(颜色/比例参数);内容变化由实现方经
               哈希感知并自动重建。

实现分派:优先加载 neuropet/render/torso_art.py(形态资产,高细节烘焙+变体+缓存);
不存在或抛错时回退到本文件的占位实现(简笔椭圆,仅供底盘开发,非交付形态)。
双方都不得修改对方的文件;接口本身由主 agent 冻结,变更需 ADR。

AG5 缩放扩展(F3 冻结点"旋转缓存键含 scale"的本侧落地;只增不改):
- traits 新增可选键 "scale"(k)与 "body_len_base"(基础体长,由 core/app.py 的
  _render_traits 统一供给)。k==1 时走原路径,行为逐位不变;
- k≠1 时:先取「k=1 基准精灵」(torso_art 的 L1 旋转桶按 base_traits 取哈希,
  五档缩放共享同一份 1x master/旋转桶 → L1 内存与 k 无关),再整体重采样到
  2*half(=base_half×k)见方 —— 烘焙分辨率语义 = 2×half(提案 A §5.1);
- L2 成品桶(提案 A §5.2 的降配实现版):重采样结果按 (物种, 基准哈希, fold 0.1
  档, 2° 朝向桶, k) 做 LRU 缓存、按字节预算淘汰 —— "torso 旋转缓存降档(2° 量化)
  + L1/L2 双桶策略"的 L2 半场;L1(torso_art 内部)由 app 侧护栏统一把量化降到
  2° 并下调预算。预算由 core/app.py 按当前最大档位经 set_l2_budget() 调整。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from PIL import Image

from ..core import instr as _I      # r25 U0:L2 桶命中/缺失插桩(默认关;零成本)

# ---------------- L2 成品桶(缩放精灵缓存,AG5) ----------------
# 条目 = 已重采样 + 已做 α 吸附的最终 (2*half)² RGBA。预算默认给小档
# (0.5/0.75 精灵更小,4MB 绰绰有余);k≥1.5 时由 app 护栏上调(蟑螂 10MB)。
_L2_DEFAULT_BUDGET = {"roach": 4 * 1024 * 1024, "fly": 1024 * 1024}
_L2_budget: dict = dict(_L2_DEFAULT_BUDGET)
_L2: "OrderedDict[tuple, tuple[Image.Image, int]]" = OrderedDict()
_L2_bytes = {"roach": 0, "fly": 0}
_L2_STEP_DEG = 2          # L2 朝向桶量化(°):k≥1.5 护栏口径,全局统一 2°
_L2_FOLD_STEP = 0.1       # fold 量化:过渡帧 ~0.2s 内按 0.1 档复用(观感无差)
_L2_lock = threading.RLock()


def _norm_sid(species_id: str) -> str:
    """species_id → "roach"|"fly"(本地实现,占位回退场景也不依赖 torso_art)。"""
    s = str(species_id).lower()
    if "roach" in s or "cockroach" in s:
        return "roach"
    if "fly" in s:
        return "fly"
    return "roach"          # 未知物种按蟑螂占位走(与 _placeholder 假设一致)


def set_l2_budget(roach: int | None = None, fly: int | None = None) -> None:
    """调整 L2 成品桶字节预算(core/app.py 内存护栏调用;None=不变)。"""
    with _L2_lock:
        if roach is not None:
            _L2_budget["roach"] = int(roach)
        if fly is not None:
            _L2_budget["fly"] = int(fly)
        _l2_evict()


def l2_stats() -> dict:
    """L2 成品桶占用统计(面板/探针/测试)。"""
    with _L2_lock:
        return {"l2_bytes": sum(_L2_bytes.values()),
                "l2_budget": sum(_L2_budget.values()),
                "l2_entries": len(_L2),
                "l2_step_deg": _L2_STEP_DEG}


def invalidate_l2(species_id: Optional[str] = None) -> None:
    """清空 L2 成品桶(内存护栏 >58MB 触发;species_id 缺省全清)。"""
    global _L2_bytes            # r25 单元 2:模块级记账必须同步归零(缺此行=局部名 ⇒ 虚高)
    with _L2_lock:
        if species_id is None:
            _L2.clear()
            _L2_bytes = {"roach": 0, "fly": 0}
            return
        sid = _norm_sid(species_id)
        for k in [k for k in _L2 if k[0] == sid]:
            _L2_bytes[k[0]] -= _L2.pop(k)[1]


def _l2_evict() -> None:
    """按物种预算淘汰最旧条目(锁内调用)。"""
    with _L2_lock:
        for sid in ("roach", "fly"):
            while _L2_bytes.get(sid, 0) > _L2_budget.get(sid, 0):
                victim = next((k for k in _L2 if k[0] == sid), None)
                if victim is None:
                    break
                _L2_bytes[sid] -= _L2.pop(victim)[1]


def base_traits(traits: dict) -> dict:
    """从 app 合成 traits 中剥离缩放键,得到「k=1 基准 traits」。

    供给 torso_art 的 L1 旋转桶(其哈希 = base_traits 的哈希)与 app 侧
    预热/跳帧查询 —— 保证五档缩放共享同一份 1x master 与旋转桶。约定:
    core/app.py 的 _render_traits 恒定供给 scale/body_len_base/body_len 三键。
    """
    if "scale" not in traits:
        return traits                      # 传统调用方(直接传 species traits)
    base = {k: v for k, v in traits.items() if k not in ("scale", "body_len_base")}
    if "body_len_base" in traits:
        base["body_len"] = traits["body_len_base"]
    return base


def _scale_of(traits: dict) -> float:
    try:
        k = float(traits.get("scale", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return k if 0.25 <= k <= 4.0 else 1.0


def _snap_alpha(spr: Image.Image) -> Image.Image:
    """躯干边缘 α 吸附(与 renderer._snap_torso_alpha 同式,在 L2 存储前做一次,
    渲染帧内只剩直方图检查零成本):α<48→0、α≥160→255,消灭重采样摩尔边在
    色键窗下的半透明脏色。"""
    a = spr.getchannel("A")
    hist = a.histogram()
    if not any(hist[1:48]) and not any(hist[160:255]):
        return spr
    out = spr.copy()
    out.putalpha(a.point(lambda v: 0 if v < 48 else (255 if v >= 160 else v)))
    return out


def _l2_get(sid: str, th: str, fold_q: int, bucket: int, kq: int,
            half: int, heading_deg: float, fold: float,
            base_traits_d: dict) -> Image.Image:
    """L2 成品桶查询/构建(未命中走「基准精灵 → 重采样 → α 吸附」)。"""
    key = (sid, th, fold_q, bucket, kq)
    with _L2_lock:
        entry = _L2.get(key)
        if entry is not None:
            _L2.move_to_end(key)
            _I.bump("l2_hit")                  # r25 U0:L2 成品桶命中
            return entry[0]
    # ---- 未命中:贵路径在锁外做(重采样 ~1-4ms,不阻塞并发查询) ----
    _I.bump("l2_miss")
    if threading.current_thread() is threading.main_thread():
        _I.bump("l2_miss_main")
    _I.seen("l2_keys", key)
    _t_l2 = _I.begin()
    base_half = max(4, int(round(half / (kq / 100.0))))
    base = _base_sprite(sid, base_half, heading_deg, fold, base_traits_d)
    size = half * 2
    if base.width != size:
        # k>1 放大用 BILINEAR(快、软化插值痕);k<1 缩小用 LANCZOS(保细节锐度)
        resample = Image.BILINEAR if kq > 100 else Image.LANCZOS
        base = base.resize((size, size), resample)
    spr = _snap_alpha(base)
    nbytes = spr.width * spr.height * 4
    _I.end("l2_build_ms", _t_l2)          # r25 U0:重采样 + α 吸附耗时
    with _L2_lock:
        old = _L2.get(key)
        if old is not None:                # 并发竞态:已有人写入同键
            _L2_bytes[sid] -= old[1]
            _L2[key] = (spr, nbytes)
            return spr
        _L2[key] = (spr, nbytes)
        _L2_bytes[sid] = _L2_bytes.get(sid, 0) + nbytes
        _l2_evict()
    return spr


def prewarm_scaled(species_id: str, half: int, heading_deg: float,
                   fold: float, traits: dict) -> bool:
    """预热一个 L2 成品桶(app 跟随式预热线程调用;与逐帧路径同一实现)。

    返回是否真正构建(已缓存时 False)。供缩放宠物转向时把前方 2° 桶的
    缩放精灵提前备好,未命中的重采样(~1-4ms)不进渲染帧。"""
    sid = _norm_sid(species_id)
    base = base_traits(traits)
    k = _scale_of(traits)
    if k == 1.0:
        return False
    half = max(4, int(half))
    bucket = int(round(heading_deg / _L2_STEP_DEG)) % (360 // _L2_STEP_DEG)
    fold_q = int(round(max(0.0, min(1.0, float(fold))) / _L2_FOLD_STEP))
    key = (sid, _thash(base), fold_q, bucket, int(round(k * 100)))
    with _L2_lock:
        if key in _L2:
            return False
    _l2_get(sid, key[1], fold_q, bucket, key[4], half, heading_deg, fold, base)
    return True


def _thash(traits: dict) -> str:
    """traits 内容指纹(与 torso_art._thash 同式的本地实现,只用于 L2 键)。"""
    import hashlib
    try:
        items = sorted((k, repr(v)) for k, v in traits.items())
    except Exception:
        items = [("traits", repr(traits))]
    return hashlib.md5(repr(items).encode("utf-8")).hexdigest()[:12]


def _base_sprite(species_id: str, half: int, heading_deg: float,
                 fold: float, traits: dict, slack_deg: float = 0.0) -> Image.Image:
    """k=1 基准精灵(torso_art 直通,缺席回退占位)——原 get_torso 主体。"""
    try:
        from neuropet.render import torso_art  # W2 的实现,缺席则占位
        return torso_art.get_torso(species_id, half, heading_deg, fold, traits,
                                   slack_deg)
    except Exception:
        return _placeholder(species_id, half, heading_deg, fold, traits)


def get_torso(species_id: str, half: int, heading_deg: float,
              fold: float, traits: dict, slack_deg: float = 0.0) -> Image.Image:
    """获取当前朝向的躯干精灵(旋转已合成)。

    k==1:原路径直通(逐位等价)。k≠1:L2 成品桶(见模块头注)。
    slack_deg(r24 转向连续性):快转时最近已缓存桶的回退半径,直通 k=1
    路径;L2 路径自带 10° 级粗桶,忽略。"""
    k = _scale_of(traits)
    half = max(4, int(half))
    if k == 1.0:
        return _base_sprite(species_id, half, heading_deg, fold,
                            base_traits(traits) if "scale" in traits else traits,
                            slack_deg)
    base = base_traits(traits)
    bucket = int(round(float(heading_deg) / _L2_STEP_DEG)) % (360 // _L2_STEP_DEG)
    fold = max(0.0, min(1.0, float(fold)))
    fold_q = int(round(fold / _L2_FOLD_STEP))
    return _l2_get(_norm_sid(species_id), _thash(base), fold_q, bucket,
                   int(round(k * 100)), half, float(heading_deg), fold, base)


def invalidate(species_id: Optional[str] = None) -> None:
    """外部(如窗口尺寸/traits 大改)请求清空缓存;实现方自行决定粒度。
    AG5:同时清 L2 成品桶(几何随 traits 变化,缓存全部失效)。"""
    try:
        from neuropet.render import torso_art
        torso_art.invalidate(species_id)
    except Exception:
        pass
    invalidate_l2(species_id)


# ---------------- 占位实现(底盘开发用,勿作交付形态) ----------------

def _placeholder(species_id: str, half: int, heading_deg: float,
                 fold: float, traits: dict) -> Image.Image:
    import math
    ss = 2
    W = half * 2 * ss
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    c = W // 2
    body = traits.get("body", "#5a3418")
    dark = traits.get("dark", "#3c2210")
    # 简笔三节(头/胸/腹)+中线,仅供占位
    segs = [(0.38, 0.30), (0.0, 0.42), (-0.42, 0.36)]
    for fx, rx in segs:
        r = int(rx * W * 0.5)
        d.ellipse([c + int(fx * W * 0.5) - r, c - int(r * 0.7),
                   c + int(fx * W * 0.5) + r, c + int(r * 0.7)],
                  fill=body, outline=dark)
    a = math.radians(heading_deg)
    img = img.rotate(-heading_deg, resample=Image.BILINEAR, center=(c, c))
    return img.resize((half * 2, half * 2), Image.LANCZOS)
