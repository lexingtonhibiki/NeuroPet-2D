# -*- coding: utf-8 -*-
"""感知实现:光标运动学(3 帧 EMA 平滑 + 手势分类)+ 通用刺激计算(按物种画像差异化)。

升级自占位实现(实现 agent 3),依据 scratch/proto_hook.py 已验证原型与
scratch/原型测量报告.md §4 的实测结论:

- update_kinematics:对速度/加速度做 3 帧加权 EMA 平滑(原型报告建议:
  程序化/真人轨迹在阈值附近会来回穿越,平滑后再分类),并按报告实测阈值
  分类手势:idle / approach_slow / approach_fast / rush / jab / retouch / touch。
  急停 jab = 高速"直指"(以轨迹笔直度近似)后速度骤降;急停但轨迹弯曲
  则退化为 idle(枚举中无独立"急停"值)。
- compute_stimuli:按物种感知画像差异化——
  * 果蝇:wind 通道在 gesture=rush/approach_fast 且闭合逼近时乘
    wind_gust_gain 增益,meta 带"风向象限"与闭合速度;当风与食物气味同时出现
    时在风刺激 meta["coincide"]=True(供大脑 GF 门控;门控逻辑在大脑不在感知层)。
  * 蟑螂:vibration/contact 主导(光标点按 jab / 贴身 / 被抓持)。
  * 风压(v0.2.0):**闭合速度**(鼠标速度向量在"鼠标→宠物"方向上的正投影)
    归一后与距离衰减相乘 → 直冲才起风,静止/远离为 0,擦身保留弱剪切扰动。
    同一纯函数 `closing_pressure` 也被身体逃逸驱动读取(不重复采样鼠标)。
  * 食物气味:多食物取"最强一束"+方向(浓度随距离单调衰减)。
  * 冷区刺激:区域内按距离衰减。
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

from neuropet.core.contracts import (CursorKinematics, CursorSample,
                                     Stimulus, StimulusKind)
from neuropet.core.mathutil import clamp, dist
from neuropet.core.world import WorldView

# ---------------- 手势分类阈值(scratch/原型测量报告.md §4 实测) ----------------
V_STILL = 30.0          # px/s,低于算静止
V_SLOW = 350.0          # px/s,低于算缓慢靠近,否则快速
V_FAST = 1300.0         # px/s,低于算快速冲近,≥1300 算冲刺(rush)
V_STOP = 80.0           # 骤停判定:当前帧原始速度跌破此值
V_PREV_STOP = 600.0     # 且上一帧平滑速度高于此值 → 急停/jab 候选
JAB_STRAIGHTNESS = 0.82  # 冲刺段"直指度"=|净位移|/路径长 阈值(直指近似)
JAB_MIN_PATH = 90.0     # 冲刺段最短路径(px),防止手抖误判为 jab
TOUCH_V = 40.0          # 贴身(touch)判定的速度上限 px/s
RETOUCH_WINDOW = 2.0    # 离开后又贴回同一宠物判 retouch 的时间窗(s)

# 3 帧加权 EMA:最近一帧 0.5、次新 0.3、更早 0.2(原型报告建议 3 帧 EMA)
EMA_N = 3
EMA_W = (0.5, 0.3, 0.2)

# ---------------- 刺激几何半径(px) ----------------
WIND_R = 300.0          # 风有效半径(光标快速移动扰动空气)
SHADOW_R = 110.0        # 阴影:光标快速逼近/覆盖
CONTACT_R = 46.0        # 贴身接触半径(约等于虫体半径)
VIB_R = 160.0           # 震动感知半径(光标在附近移动/拖拽)
JAB_R = 260.0           # 点按(jab)冲击可感半径

# 风向象限:按"气流被光标推向的方位"8 分位(屏幕 y 向下,北 = -y)
_WIND_QUADRANTS = ("东", "东北", "北", "西北", "西", "西南", "南", "东南")


# ---------------- 闭合风压(v0.2.0:鼠标越快朝宠物逼近 → 风压越强) ----------------
# 旧口径只看"鼠标总速度 × 距离衰减":横向擦过和直冲宠物给同一个强度,
# 且与逃离方向无关。v0.2.0 改用**闭合速度** = 鼠标速度向量在"鼠标 → 宠物"
# 方向上的正投影(>0 才算逼近),平方归一后与距离衰减相乘:
#   闭合速度 0(静止/远离) → 风压 0;擦身而过 → 只剩下面那一项弱剪切扰动;
#   闭合速度 CLOSE_REF → 风压到顶。平方是廉价的拟真近似(风压 ∝ 动压)。
CLOSE_REF = 1500.0      # px/s,闭合速度归一基准
SHEAR_GAIN = 0.35       # 擦身(横向高速但远离)的弱空气扰动系数
PRESSURE_GAIN = 2.6     # 风压 → 刺激强度的总增益(与旧 2.4 同量级)
# 距离衰减取平方根:线性衰减时"200px 外全速冲来"只剩 1/3 风压,手感的威胁
# 感太弱;开根后同一情形约 0.58,贴近时仍按 (1-d/R) 单调收敛到 0。
FALLOFF_EXP = 0.5

# ================================ 光标运动学 ================================

def _kin_state(kin: CursorKinematics) -> dict[str, Any]:
    """把 EMA/手势判定所需的中间状态挂在 kin 实例上。

    WorldModel.cursor 是跨帧存活的同一实例,故状态可随帧累积;
    tests/多世界各自持有独立 CursorKinematics,互不串扰。
    """
    st = getattr(kin, "_percept_state", None)
    if st is None:
        st = {
            "rv": deque(maxlen=EMA_N),      # 原始标量速度
            "ra": deque(maxlen=EMA_N),      # 原始标量加速度
            "rvx": deque(maxlen=EMA_N),     # 原始速度分量(风向用)
            "rvy": deque(maxlen=EMA_N),
            "last_raw_speed": 0.0,
            "prev_s": 0.0,                  # 上一帧平滑速度(骤停判定)
            "seg_nx": 0.0, "seg_ny": 0.0, "seg_path": 0.0,  # 冲刺段净位移/路径长
            "touch_pet": None,              # 最近一次贴身的宠物 id
            "leave_t": None,                # 离开贴身的时刻
            "had_touch": False,
        }
        try:
            kin._percept_state = st
        except AttributeError:              # 极端情况(被冻结对象)退化为无状态
            pass
    return st


def _ema(buf: deque) -> float:
    """加权 EMA:最近 0.5 / 次新 0.3 / 更早 0.2;不足 3 帧按已有权重归一。"""
    num = den = 0.0
    for w, v in zip(EMA_W, reversed(buf)):  # reversed: 最新在前取最大权重
        num += w * v
        den += w
    return num / den if den > 1e-9 else 0.0


def update_kinematics(history: deque[CursorSample], kin: CursorKinematics) -> None:
    """由最近采样窗口估计速度/加速度/手势。history 按时间追加(WorldModel 维护)。

    平滑与响应的折中:分档阈值用平滑值(抗抖动),骤停判定用"当前帧原始
    速度"(EMA 会拖尾,漏检急停)。
    """
    if not history:
        return
    s = history[-1]
    kin.x, kin.y = s.x, s.y
    st = _kin_state(kin)
    if len(history) < 2:
        return
    p = history[-2]
    dt = max(1e-3, s.t - p.t)
    raw_vx, raw_vy = (s.x - p.x) / dt, (s.y - p.y) / dt
    raw_speed = math.hypot(raw_vx, raw_vy)

    # ---- 3 帧 EMA 平滑(原型报告工程建议) ----
    st["rv"].append(raw_speed)
    st["ra"].append((raw_speed - st["last_raw_speed"]) / dt)
    st["rvx"].append(raw_vx)
    st["rvy"].append(raw_vy)
    st["last_raw_speed"] = raw_speed
    kin.vx, kin.vy = _ema(st["rvx"]), _ema(st["rvy"])
    prev_s = st["prev_s"]
    kin.speed = _ema(st["rv"])
    kin.accel = _ema(st["ra"])

    # ---- 冲刺段累计(用于 jab 的"直指度"统计) ----
    dx, dy = s.x - p.x, s.y - p.y
    if kin.speed >= V_SLOW:
        st["seg_nx"] += dx
        st["seg_ny"] += dy
        st["seg_path"] += math.hypot(dx, dy)

    # ---- 贴身离开时刻记录(retouch 判定用;near_pet_id 由上层命中检测设置) ----
    now_t = s.t
    if st["had_touch"] and kin.near_pet_id is None and st["leave_t"] is None:
        st["leave_t"] = now_t

    # ---- 手势分类 ----
    gesture: str
    if raw_speed < V_STOP and prev_s >= V_PREV_STOP:
        # 急停:上一帧仍高速、当前骤停。冲刺段笔直且足够长 → jab(直指后骤停)
        path = st["seg_path"]
        net = math.hypot(st["seg_nx"], st["seg_ny"])
        straight = (net / path) if path > 1e-6 else 0.0
        gesture = "jab" if (path >= JAB_MIN_PATH and straight >= JAB_STRAIGHTNESS) else "idle"
        st["seg_nx"] = st["seg_ny"] = st["seg_path"] = 0.0
    elif kin.near_pet_id and kin.speed < TOUCH_V:
        if (st["had_touch"] and st["touch_pet"] == kin.near_pet_id
                and st["leave_t"] is not None
                and now_t - st["leave_t"] <= RETOUCH_WINDOW):
            gesture = "retouch"             # 短暂离开又贴回同一宠物
            st["leave_t"] = None
        else:
            gesture = "touch"
        st["touch_pet"] = kin.near_pet_id
        st["had_touch"] = True
    elif kin.speed < V_STILL:
        gesture = "idle"
    elif kin.speed < V_SLOW:
        gesture = "approach_slow"
    elif kin.speed < V_FAST:
        gesture = "approach_fast"
    else:
        gesture = "rush"
    kin.gesture = gesture
    st["prev_s"] = kin.speed


def closing_pressure(cur, pos, radius: float = WIND_R) -> float:
    """闭合风压 0..1(纯函数;感知层与身体逃逸驱动共用同一口径)。

    - ``cur``:``CursorKinematics``(世界模型里每帧更新的同一实例);
    - ``pos``: 宠物世界坐标;
    - 返回 0 = 静止或正在远离;返回 1 = 以 CLOSE_REF 的闭合速度直冲而来。

    读的是**已经算好的**光标运动学,不新增任何鼠标采样(单一事实源仍是
    ``update_kinematics``)。鼠标位置缺失(未初始化)或与宠物重合时退化为 0。
    """
    try:
        cx, cy = float(cur.x), float(cur.y)
        vx, vy = float(cur.vx), float(cur.vy)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if not (cx == cx and cy == cy):       # NaN 位置
        return 0.0
    dx, dy = float(pos[0]) - cx, float(pos[1]) - cy
    d = math.hypot(dx, dy)
    if radius <= 0.0 or d >= radius or d < 1e-6:
        return 0.0
    ux, uy = dx / d, dy / d                # 光标 → 宠物 方向(别写成反的)
    closing = vx * ux + vy * uy             # 正投影:>0 = 朝宠物逼近
    if closing <= 0.0:
        return 0.0
    falloff = (1.0 - d / radius) ** FALLOFF_EXP
    norm = min(1.0, closing / CLOSE_REF)
    return clamp(norm * norm * falloff)


def shear_pressure(cur, pos, radius: float = WIND_R) -> float:
    """擦身而过的弱扰动 0..1:横向高速、正在远离 ⇒ 只有一点点空气扰动。"""
    try:
        cx, cy = float(cur.x), float(cur.y)
        vx, vy = float(cur.vx), float(cur.vy)
    except (AttributeError, TypeError, ValueError):
        return 0.0
    dx, dy = float(pos[0]) - cx, float(pos[1]) - cy
    d = math.hypot(dx, dy)
    speed = math.hypot(vx, vy)
    if radius <= 0.0 or d >= radius or d < 1e-6 or speed <= 0.0:
        return 0.0
    ux, uy = dx / d, dy / d                # 光标 → 宠物 方向
    if (vx * ux + vy * uy) > 0.0:          # 正在逼近 → 交给 closing_pressure
        return 0.0
    lateral = abs(vx * (-uy) + vy * ux)    # 垂直分量 = 擦身强度
    norm = min(1.0, lateral / CLOSE_REF)
    return clamp(norm * norm * (1.0 - d / radius) ** FALLOFF_EXP * SHEAR_GAIN)


def wind_pressure(cur, pos, radius: float = WIND_R) -> float:
    """总风压 = max(闭合逼近, 擦身剪切);两者都不看则 0。"""
    return max(closing_pressure(cur, pos, radius),
               shear_pressure(cur, pos, radius))


def _wind_quadrant(vx: float, vy: float) -> str:
    """风向象限:气流被光标推向的方位(屏幕 y 向下,北 = -y);静止时返回"无"。"""
    if abs(vx) < 1e-6 and abs(vy) < 1e-6:
        return "无"
    deg = math.degrees(math.atan2(-vy, vx)) % 360.0   # 数学角:东=0°,北=90°
    return _WIND_QUADRANTS[int((deg + 22.5) // 45.0) % 8]


# ================================ 刺激计算 ================================

def compute_stimuli(state, view: WorldView, profile: dict[str, float]) -> list[Stimulus]:
    """按物种感知画像计算刺激:光标(风/阴影/震动/接触) + 食物气味 + 冷区。

    profile 键(由 SpeciesPlugin.perception_profile 提供):
      wind / vibration / shadow / odor_food / cold —— 通道敏感度 0..1;
      wind_gust_gain  —— rush/approach_fast 时的风增益(果蝇调高、蟑螂调低);
      wind_odor_gate  —— >0 时风+食物气味同时出现打 meta["coincide"](果蝇 GF 门控提示)。
    """
    out: list[Stimulus] = []
    px, py = state.pos
    cur = view.cursor
    d = dist(px, py, cur.x, cur.y)

    wind_prof = float(profile.get("wind", 0.3))
    vib_prof = float(profile.get("vibration", 0.5))
    shadow_prof = float(profile.get("shadow", 0.5))
    odor_prof = float(profile.get("odor_food", 0.8))
    cold_prof = float(profile.get("cold", 0.5))
    gust_gain = float(profile.get("wind_gust_gain", 1.0))
    coincide_gate = float(profile.get("wind_odor_gate", 0.0))

    # ---- 食物气味:多食物取"最强一束"+方向(浓度随距离单调衰减) ----
    odor_stim: Stimulus | None = None
    best_int, best_food, best_df = 0.02, None, 0.0
    for f in view.foods.values():
        df = dist(px, py, *f.pos)
        if df >= f.odor_radius or f.odor_strength <= 0.02:
            continue
        inten = odor_prof * f.odor_strength * (1.0 - df / f.odor_radius)
        if inten > best_int:
            best_int, best_food, best_df = inten, f, df
    if best_food is not None:
        odor_stim = Stimulus(
            StimulusKind.ODOR_FOOD, source=f"food:{best_food.food_id}",
            pos=(float(best_food.pos[0]), float(best_food.pos[1])),
            intensity=min(1.0, best_int),
            direction=((best_food.pos[0] - px) / max(1e-3, best_df),
                       (best_food.pos[1] - py) / max(1e-3, best_df)),
            meta={"dist": round(best_df, 1), "amount": round(best_food.amount, 3),
                  "odor_strength": round(best_food.odor_strength, 3)})
        out.append(odor_stim)

    # ---- 风:闭合速度 × 距离衰减(静态/远离为 0,擦身保留弱剪切扰动) ----
    # v0.2.0:不再是"总速度 × 距离":直冲宠物才起风,横擦只留一点扰动。
    # 冲刺/快速冲近按物种 gust_gain 放大(果蝇既有起飞反应保持不变)。
    wind_stim: Stimulus | None = None
    gusting = cur.gesture in ("rush", "approach_fast")   # 果蝇风通道增益场景
    press = wind_pressure(cur, (px, py))
    if press > 0.0:
        base = press
        if gusting and closing_pressure(cur, (px, py)) > 0.0:
            base *= gust_gain
        inten = base * wind_prof * PRESSURE_GAIN
        if inten > 0.04:
            closing_v = ((cur.vx * (cur.x - px) + cur.vy * (cur.y - py))
                         / max(1e-3, d)) if d > 1e-6 else 0.0
            wind_stim = Stimulus(
                StimulusKind.WIND, source="cursor", pos=(cur.x, cur.y),
                intensity=min(1.0, inten),
                direction=((cur.x - px) / max(1e-3, d), (cur.y - py) / max(1e-3, d)),
                meta={"gesture": cur.gesture, "dist": round(d, 1),
                      "closing_v": round(closing_v, 1),
                      "pressure": round(press, 3),
                      "wind_quadrant": _wind_quadrant(cur.vx, cur.vy),
                      "wind_vec": [round(cur.vx, 1), round(cur.vy, 1)]})
            out.append(wind_stim)

    # ---- 阴影:大而快的目标逼近/覆盖(looming) ----
    # 强度须随逼近单调上升(扩张率),果蝇 LOOM 超选择检测依赖逐拍 rise>0
    if d < SHADOW_R and cur.speed > 700.0:
        closeness = 1.0 - d / SHADOW_R
        shadow_int = min(1.0, (0.25 + 0.75 * closeness)
                         * (0.5 + cur.speed / 3000.0) * shadow_prof * 1.6)
        out.append(Stimulus(
            StimulusKind.SHADOW, source="cursor", pos=(cur.x, cur.y),
            intensity=shadow_int,  # 契约:0..1
            meta={"gesture": cur.gesture, "dist": round(d, 1),
                  "loom_rise": round(shadow_int, 3)}))

    # ---- 接触/震动:蟑螂的主导通道(点按/拖拽/贴身) ----
    if getattr(state, "held", False):
        # 被抓持:身体接触主导(引擎层强制定格,触觉通道供情绪/记忆)
        out.append(Stimulus(
            StimulusKind.CONTACT, source="cursor", pos=(cur.x, cur.y),
            intensity=clamp(0.55 + 0.45 * vib_prof),
            meta={"held": True, "gesture": cur.gesture}))
    elif d < CONTACT_R:
        if cur.speed < TOUCH_V:
            # 贴身静止:压在身上 → 接触(蟑螂尾须/足部感受器主导)
            inten = (0.55 + 0.45 * (1.0 - d / CONTACT_R)) * (0.35 + 0.65 * vib_prof)
            out.append(Stimulus(
                StimulusKind.CONTACT, source="cursor", pos=(cur.x, cur.y),
                intensity=min(1.0, inten),
                meta={"gesture": "touch", "dist": round(d, 1)}))
        else:
            # 贴身拖动/高频抖动 → 震动
            inten = clamp(cur.speed / 1500.0) * (0.6 + 0.4 * (1.0 - d / CONTACT_R))
            inten *= vib_prof * 1.8
            out.append(Stimulus(
                StimulusKind.VIBRATION, source="cursor", pos=(cur.x, cur.y),
                intensity=min(1.0, inten),
                meta={"gesture": cur.gesture, "dist": round(d, 1)}))
    else:
        if cur.gesture == "jab" and d < JAB_R:
            # 点按:高速直指后的骤停在近处产生一次冲击震动
            out.append(Stimulus(
                StimulusKind.VIBRATION, source="cursor", pos=(cur.x, cur.y),
                intensity=0.85 * vib_prof * (1.0 - d / JAB_R),
                meta={"gesture": "jab", "dist": round(d, 1), "tap": True}))
        elif d < VIB_R and cur.speed >= 40.0:
            inten = (1.0 - d / VIB_R) * clamp(cur.speed / 900.0) * vib_prof * 1.6
            if inten > 0.04:
                out.append(Stimulus(
                    StimulusKind.VIBRATION, source="cursor", pos=(cur.x, cur.y),
                    intensity=min(1.0, inten),
                    meta={"gesture": cur.gesture, "dist": round(d, 1)}))

    # ---- 冷区:区域内随距离衰减 ----
    for z in view.zones.values():
        dz = dist(px, py, *z.pos)
        if dz < z.radius:
            out.append(Stimulus(
                StimulusKind.COLD, source=f"zone:{z.zone_id}",
                pos=(float(z.pos[0]), float(z.pos[1])),
                intensity=min(1.0, z.intensity * cold_prof * (1.0 - dz / z.radius)),
                direction=((z.pos[0] - px) / max(1e-3, dz),
                           (z.pos[1] - py) / max(1e-3, dz)),
                meta={"dist": round(dz, 1), "zone_kind": z.kind}))

    # ---- 风 + 食物气味同时出现 → 果蝇 GF 门控提示(门控逻辑在大脑) ----
    if coincide_gate > 0.0 and wind_stim is not None and odor_stim is not None:
        wind_stim.meta["coincide"] = True
    return out
