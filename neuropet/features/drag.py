"""DragFeature:拖拽力学权威(ADR-0031 F3;从 core/app.py 内联版迁出)。

职责(施工图根因 D/E,修复方向 F3):
- 抓握期(held):光标差分 → 平滑加速度 + **体轴速度**(F3 新增)→
  DragDynamics 连续积分 → 肢体被动响应;位置意向经 ctx.claim_pos 提交
  (优先级 POS_DRAG=90,StateArbiter 合成)——替代旧 _on_drag 直写 st.pos
  (位置权威战争的一方,现在走同一仲裁器);
- F3 驱动项:旧任务书只给 +m·a,匀速拖拽 a≈0 → 肢体提前回中("拖拽肢体
  表现不连续")。方程改正 I·φ''=−c·φ'−k·(φ−φ_rest)+m·a−d·v:匀速段肢体
  保持 ∝ −v 的稳定偏转(绳牵球心智模型),静止 v→0 自然回归。V_DRIVE
  语义见 physics/drag.py;
- 冻结期(frozen):仅合成位置意向(冻结拖拽,用户需求 #5;无肢体动力学,
  与旧直写行为一致);
- 非抓握期(post_move):动力学零输入续走(摆动平滑归零),收敛即回收。
"""
from __future__ import annotations

import math

from neuropet.core.kernel import POS_DRAG, FeatureContext, FeaturePlugin
from neuropet.physics.drag import species_dynamics

_DRAG_EMA = 0.45          # 加速度 EMA(连续性:滤 tk 事件抖动)
_DRAG_TIP_TAU = 0.06      # 触须梢端再滞后(鞭状相位延迟 60ms)


class DragFeature(FeaturePlugin):
    """拖拽动力学与位置意向(冻结接口 DragDynamics 的集成所有者)。"""

    feature_id = "drag"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._dyn: dict = {}     # pet_id -> DragDynamics
        self._track: dict = {}   # pet_id -> {prev,pv,pa,tip0,tip1}
        self._intent: dict = {}  # pet_id -> (x, y) 拖拽目标位置(tk 事件写入)
        self._ctx: FeatureContext | None = None

    # ---------------- 生命周期 ----------------
    def mount(self, ctx: FeatureContext) -> None:
        self._ctx = ctx
        ctx.every_frame("held_tick", 0, self._held_tick)
        ctx.every_frame("frozen_tick", 0, self._frozen_tick)
        ctx.every_frame("post_move", 0, self._decay)
        ctx.subscribe("system/pet_removed", self._on_pet_gone)
        ctx.subscribe("system/pet_hidden", self._on_pet_gone)
        ctx.subscribe("user/grab_release", self._on_released)   # ADR-0032 抛掷

    def _on_released(self, topic: str, data: dict) -> None:
        """松手抛掷(ADR-0032;市场对标 Ragdoll/Goose 派的"活的"触感):
        把拖拽末速度交给 body 惯性滑行(轻放 <120px/s 不抛;上限 1400)。
        强抛的外拖位移不计入自身运动 → body 观测自然触发翻面(抛出去仰面)。"""
        pid = data.get("pet_id", "")
        tr = self._track.get(pid)
        h = getattr(self.app, "pets", {}).get(pid)
        if tr is None or h is None:
            return
        vx, vy = tr["pv"]
        sp = math.hypot(vx, vy)
        if sp < 120.0:
            return
        k = min(1.0, 1400.0 / sp)
        fling = getattr(h.body, "set_fling", None)
        if fling is not None:
            fling(vx * k, vy * k)

    def unmount(self) -> None:
        self._dyn.clear()
        self._track.clear()
        self._intent.clear()
        self._ctx = None

    def _on_pet_gone(self, topic: str, data: dict) -> None:
        pid = data.get("pet_id", "")
        self._dyn.pop(pid, None)
        self._track.pop(pid, None)
        self._intent.pop(pid, None)
        h = getattr(self.app, "pets", {}).get(pid)
        if h is not None:
            h._drag_pose = None

    # ---------------- tk 事件入口(app 转发) ----------------
    def on_drag(self, pet_id: str, xy: tuple[float, float]) -> None:
        """抓握拖拽目标位置(已钳屏;app._on_drag 转发)。"""
        self._intent[pet_id] = (float(xy[0]), float(xy[1]))

    def clear_intent(self, pet_id: str) -> None:
        """按压/释放时清空(防陈旧意向跨抓取泄漏)。"""
        self._intent.pop(pet_id, None)

    # ---------------- sim 管道 ----------------
    def _held_tick(self, h, dt: float) -> None:
        """held 宠每帧:位置意向 → 差分 → 体轴 (a, v) → 连续积分(F3)。"""
        st = h.state
        pid = h.pet_id
        tr = self._track.get(pid)
        if tr is None:
            self._track[pid] = {"prev": tuple(st.pos), "pv": (0.0, 0.0),
                                "pa": (0.0, 0.0), "tip0": 0.0, "tip1": 0.0}
        else:
            px, py = self._intent.get(pid, tuple(st.pos))
            ox, oy = tr["prev"]
            tr["prev"] = (px, py)
            vx, vy = (px - ox) / dt, (py - oy) / dt
            ra = ((vx - tr["pv"][0]) / dt, (vy - tr["pv"][1]) / dt)
            tr["pv"] = (vx, vy)
            pa = tr["pa"]
            ax = pa[0] + _DRAG_EMA * (ra[0] - pa[0])
            ay = pa[1] + _DRAG_EMA * (ra[1] - pa[1])
            tr["pa"] = (ax, ay)
            ch, sh = math.cos(st.heading), math.sin(st.heading)
            axb = ch * ax + sh * ay                  # 世界→体轴(前=+x)
            ayb = -sh * ax + ch * ay
            vxb = ch * vx + sh * vy                  # F3:体轴速度
            vyb = -sh * vx + ch * vy
            dyn = self._dyn.get(pid)
            if dyn is None:
                dyn = species_dynamics(st.species_id)
                self._dyn[pid] = dyn
            h._drag_pose = dyn.step(dt, axb, ayb, vxb, vyb)
            # 触须梢端:基角的一阶再滞后(渲染端 ant_tip_deg)
            kt = min(1.0, dt / _DRAG_TIP_TAU)
            tr["tip0"] += (h._drag_pose.antenna_swing[0] - tr["tip0"]) * kt
            tr["tip1"] += (h._drag_pose.antenna_swing[1] - tr["tip1"]) * kt
        # 被拖拽姿态(ADR-0032):足端重锚定到当前体下静息位(标准俯视图态;
        # 旧版钉在抓取前世界坐标 = "动量锚点是拖拽前的位置"),拖拽动量/
        # 甩动翻面观测在此续走。
        carried = getattr(h.body, "carried_tick", None)
        if carried is not None:
            carried(dt)
        if pid in self._intent and self._ctx is not None:
            self._ctx.claim_pos(pid, self._intent[pid], "drag", POS_DRAG)

    def _frozen_tick(self, h, dt: float) -> None:
        """冻结拖拽(用户需求 #5):仅位置意向合成,无肢体动力学。"""
        pid = h.pet_id
        if pid in self._intent and self._ctx is not None:
            self._ctx.claim_pos(pid, self._intent[pid], "drag", POS_DRAG)

    def _decay(self, h, dt: float) -> None:
        """非 held 期:动力学以零输入续走(摆动平滑归零),收敛即回收。"""
        pid = h.pet_id
        dyn = self._dyn.get(pid)
        if dyn is None:
            return
        h._drag_pose = dyn.step(dt, 0.0, 0.0)
        dp = h._drag_pose
        if (dp.settle > 0.98
                and max(abs(v) for v in dp.leg_swings) < 0.5
                and max(abs(v) for v in dp.antenna_swing) < 0.5):
            self._dyn.pop(pid, None)
            self._track.pop(pid, None)
            h._drag_pose = None

    # ---------------- render 管道 ----------------
    def drag_overlay(self, h) -> dict | None:
        """DragPose → 渲染 drag 字典(活跃时非 None;kernel.drag_overlay 聚合)。"""
        pid = h.pet_id
        if pid not in self._dyn or h._drag_pose is None:
            return None
        dp = h._drag_pose
        tr = self._track.get(pid, {"tip0": 0.0, "tip1": 0.0})
        legs = [float(v) for v in dp.leg_swings]
        ab = [max(-20.0, min(20.0, float(v))) for v in dp.antenna_swing]
        at = [max(-30.0, min(30.0, float(tr["tip0"]) * 1.5)),
              max(-30.0, min(30.0, float(tr["tip1"]) * 1.5))]
        return {"legs": legs, "ant_deg": ab, "ant_tip_deg": at,
                "d": (0.0, 0.0), "ant": (0.0, 0.0), "ant_tip": (0.0, 0.0),
                "ant_gain": 1.0, "ant_tip_ratio": 1.0,
                "gain": [1.0, 1.0, 1.0], "axes": [(1.0, 0.0)] * 6}

    # 诊断/验收:只读视图
    def pose_of(self, h):
        return h._drag_pose

    def is_flinging(self, h) -> bool:
        """抛掷滑行中(HideFeature 据此推迟新建躲藏,ADR-0032)。"""
        return getattr(h.body, "_fling", None) is not None
