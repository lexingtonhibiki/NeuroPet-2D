"""SkeletonSpec 骨架数据结构(冻结点 F1)+ 微弹性滤波(纯 Python,无依赖)。

F1 冻结内容(决策记录 §5.0,任何 agent 不得单方面变更):
  JSON 顶层:schema_version / species / units / body_len_px / scale /
  segments(体段椭圆)/ legs×6{attach, coxa, femur, tibia, tarsus, 静息角,
  DOF 限位, 弹簧}/ antennae / wings / cerci。
  数值 = 权威比例经 D2 §1.2 投影校准后的冻结值(species PARAMS 为单一事实源,
  本模块只做结构化与序列化,不自造数值)。

设计要点:
- 可序列化:to_dict()/to_json()/from_json() 往返一致,供训练/渲染/调试读取;
- BL 单位制铺路:units="px" 时全部长度为 px;`scaled(k)` 返回等比缩放副本
  (长度×k,半径×k,角度/限位/弹簧不变)——尺寸缩放 0.5~2× 的骨架级入口,
  渲染层禁止自行硬缩放像素(SA 诊断 §3.2);
- 微弹性(D2 §2.4):每关节一阶弹簧-阻尼足够——负载下沉 2~6°、τ=30~80ms、
  仅压缩不对称。落点:
    * 关节下沉 → MicroElastic.knee_sag(每腿一通道,支撑相沉、摆动相回);
    * 腹部偏航滞后 / 头部摆动 → MicroElastic.lag(一阶跟踪,τ 独立);
  全部为标量滤波,每帧开销可忽略(<0.05ms,提案 C §3.6 预算)。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from neuropet.core.mathutil import clamp, wrap_angle

SCHEMA_VERSION = 2
# v1 仍可读(ADR-0026:v1→v2 为加法式扩展,缺省键回填安全默认);
# v2 新增腿条目键:trochanter/claw/diam/tarsomeres/ik(基节投影与膝弓向标定)。
_SUPPORTED_SCHEMA = (1, 2)

# ---- 微弹性默认参数(D2 §2.4:下沉 2~6°、τ=30~80ms;GaitProfile 可覆盖) ----
SAG_TAU_S = 0.06          # 一阶时间常数(s):60ms,落在 30~80ms 区间
SAG_MAX_DEG = 4.0         # 满载下沉角(度):区间中值
SAG_LEVER = 0.35          # 下沉角 → 膝点横移的换算臂(与 IK 膝拱同臂)


class SkeletonSpec:
    """骨架规格:species PARAMS 的结构化视图 + JSON 序列化 + 等比缩放。

    用途:
      * 渲染(Agent-3):读取体段/腿节段长/静息角/翅铰,替代散落的美术常数;
      * 训练(Agent-2):GaitProfile 训练仿真器读取节段长与 DOF 限位;
      * 缩放(Agent-5):scaled(k) 给出 0.5/0.75/1.5/2× 的骨架副本。
    """

    def __init__(self, spec: dict) -> None:
        if spec.get("schema_version") not in _SUPPORTED_SCHEMA:
            raise ValueError(f"骨架 spec 版本不受支持: {spec.get('schema_version')}")
        self.spec = spec

    # ---------------- 构造 ----------------
    @classmethod
    def from_params(cls, species: str, params: dict) -> "SkeletonSpec":
        """由 species PARAMS(权威数值的单一事实源)构建骨架 spec。

        腿条目:attach/home/coxa/l1(股)/l2(胫)/tarsus/side/kind/group +
        kin 派生(静息角/yaw 包络);DOF 限位取自 LegKinematics 同源包络。
        """
        from neuropet.body.kinematics import COXA_YAW_ENV, KNEE_RANGE_V2, \
            CTR_RANGE, FEMUR_RANGE, TIBIA_RANGE, LegKinematics

        legs_out = []
        # AG5/v3:scale_params 会把 p["scale"]=k 注入但只缩放旧长度键;len3d
        # (v3 真 3D 节段长)由这里注入 _scale_k 补齐缩放(与 LegKinematics
        # 的解析约定一致,见 body/kinematics.py 模块头"缩放"节;k=1 恒等)。
        try:
            k_in = clamp(float(params.get("scale", 1.0)), 0.05, 8.0)
        except (TypeError, ValueError):
            k_in = 1.0
        for i, leg in enumerate(params["legs"]):
            kin = LegKinematics(dict(leg, _scale_k=k_in) if "len3d" in leg else leg)
            rear_env, front_env = COXA_YAW_ENV.get(kin.kind, (50.0, 50.0))
            if "yaw_env" in leg:
                rear_env, front_env = float(leg["yaw_env"][0]), float(leg["yaw_env"][1])
            # v2 链(ADR-0026):DOF 限位按规格 §6.4(FTi 40~165°、CTr −10~40°);
            # v1 腿保持原 FEMUR/TIBIA 包络(果蝇零变化)。
            pitch_range = CTR_RANGE if kin.v2 else FEMUR_RANGE
            knee_range = KNEE_RANGE_V2 if kin.v2 else TIBIA_RANGE
            entry = {
                "id": f"{'L' if kin.side < 0 else 'R'}{kin.kind}",
                "kind": kin.kind, "side": int(kin.side),
                "group": int(leg.get("group", i % 2)),
                "attach": [round(kin.attach[0], 3), round(kin.attach[1], 3)],
                "home": [round(kin.home[0], 3), round(kin.home[1], 3)],
                "coxa": round(kin.coxa, 3),              # 基节(D2 §1.5/1.6)
                "femur": round(kin.l1, 3),               # 股节(含转节,D2 §1.1)
                "tibia": round(kin.l2, 3),               # 胫节
                "tarsus": round(kin.tarsus, 3),          # 跗节(渲染外伸段口径)
                "rest_yaw_deg": round(math.degrees(kin.yaw0), 2),  # 静息足向角
                "reach": round(kin.reach, 3),
                "dof": {                                  # DOF 限位(度,D2 §2.2)
                    "thc_yaw": [round(-front_env, 1), round(rear_env, 1)],
                    "femur_pitch": list(pitch_range),
                    "knee": list(knee_range)},
                "spring": {                               # 微弹性(D2 §2.4)
                    "tau_ms": SAG_TAU_S * 1000.0,
                    "sink_deg": SAG_MAX_DEG},
            }
            if kin.v2:
                # ---- v2 加法式扩展(ADR-0026):老消费方忽略未知键 ----
                entry["trochanter"] = round(kin.trochanter, 3)   # 转节(3D)
                entry["claw"] = round(kin.claw, 3)               # 前跗节(双爪)
                # diam/trochanter/claw 均长度量纲(ADR-0026):由 core/app.
                # scale_params 在参数层 ×k(本函数读到的即已缩放值)——在参数层
                # 单点缩放才能保证 reach 等派生长度(cTerr+claw 参与推导)两条
                # 缩放路径 from_params∘scale_params ≡ from_params∘scaled 等价
                entry["diam"] = [round(v, 3) for v in (kin.diam or ())]
                entry["tarsomeres"] = [round(v, 4) for v in (kin.tarsomeres or ())]
                entry["ik"] = {"base_k": round(kin.base_k, 4),
                               "base_off_deg": round(math.degrees(kin.base_off), 2),
                               "bend_w": round(kin.bend_w, 4)}
            if "len3d" in leg:
                # ---- v3 真 3D 链扩展(腿部 3D 波;规格 §3.1/§4.2;老消费方
                #      忽略未知键)。len3d 为 1x 归一后 px(经 _scale_k);
                #      rest3d/rom3d 角度与 tarsus_seg 比例均尺度不变。 ----
                entry["len3d"] = {"coxa": round(kin.coxa3d, 3),
                                  "femur": round(kin.f3d, 3),
                                  "tibia": round(kin.t3d, 3),
                                  "tarsus": round(kin.tarsus3d, 3)}
                entry["rest3d"] = {rk: round(rv, 2)
                                   for rk, rv in kin.rest3d.items()}
                entry["rom3d"] = {"yaw": [round(kin.rom3d["yaw"][0], 2),
                                          round(kin.rom3d["yaw"][1], 2)],
                                  "femur_pitch": [round(kin.rom3d["femur_pitch"][0], 2),
                                                  round(kin.rom3d["femur_pitch"][1], 2)],
                                  "knee": [round(kin.rom3d["knee"][0], 2),
                                           round(kin.rom3d["knee"][1], 2)],
                                  "tita": [round(kin.rom3d["tita"][0], 2),
                                           round(kin.rom3d["tita"][1], 2)]}
                entry["tarsus_seg"] = [round(v, 4) for v in kin.tarsus_seg]
                # v3 腿的 DOF 限位 = 生效中的 rom3d(替代 v1 回退包络)
                entry["dof"] = {"thc_yaw": [round(-kin.rom3d["yaw"][1], 1),
                                            round(kin.rom3d["yaw"][0], 1)],
                                "femur_pitch": list(kin.rom3d["femur_pitch"]),
                                "knee": list(kin.rom3d["knee"])}
            legs_out.append(entry)

        segs = []
        for j, (x, y, rx, ry) in enumerate(params["segments"]):
            segs.append({"id": ("head", "thorax", "abdomen")[j if j < 3 else 0],
                         "x": float(x), "y": float(y), "rx": float(rx), "ry": float(ry)})

        spec: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "species": species,
            "units": "px",                      # BL 制见 docs/接口冻结_GaitProfile.md §4
            "body_len_px": float(params.get("body_len", 50.0)),
            # AG5:档位标记 —— scale_params 会把 p["scale"]=k 注入;此前恒为 1.0,
            # 与 scaled(k)(置为 k)不等价,只能靠 app._build_body 事后回写。
            # 这里改为尊重 PARAMS(缺省仍 1.0,k=1 行为逐位不变)。
            "scale": float(params.get("scale", 1.0)),
            "segments": segs,
            "legs": legs_out,
            "antennae": {
                "base": [float(v) for v in params.get("antenna_base", (30, 4))],
                "len": float(params.get("antenna_len", 40)),
                "segments": int(params.get("antenna_segments", 9))},
        }
        # 翅(物种差异:蟑螂覆翅滑翔翼 / 果蝇静息翅;翅端为 D1 冻结值域)
        # AG5:hinge/tip_x 优先取 PARAMS(缩放管线注入并已 ×k),缺省沿用 D1 冻结
        # 值 —— k=1 时与历史行为逐位一致,k≠1 时才能随档位缩放(否则翅铰/翅端
        # 会钉死在 1x 位置,与 scaled(k) 不等价,见 tests/test_scaling.py)。
        if float(params.get("wing_span", 0)) > 0:
            hinge_x = float(params.get("wing_hinge_x", 14.0))
            tip_x = float(params.get("wing_tip_x",
                                     -56.0 if species == "cockroach" else -25.0))
            spec["wings"] = {"hinge": [hinge_x, 0.0], "tip_x": tip_x,
                             "span": float(params["wing_span"]),
                             "fold": "tegmen" if params.get("glide") else "rest_flat"}
        # 尾须(蟑螂;权威 §1:(−49,±4.4) 长 10.4px)
        if species == "cockroach" or "cerci_anchor" in params:
            cax, cay = params.get("cerci_anchor", (-49.0, 4.4))
            spec["cerci"] = {"anchor": [float(cax), float(cay)],
                             "len": float(params.get("cerci_len", 10.4))}
        return cls(spec)

    @classmethod
    def from_json(cls, path: str | Path) -> "SkeletonSpec":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict:
        return json.loads(json.dumps(self.spec))   # 深拷贝且 JSON 安全

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.spec, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    # ---------------- 查询 ----------------
    @property
    def body_len(self) -> float:
        return float(self.spec["body_len_px"])

    def scaled(self, k: float) -> "SkeletonSpec":
        """等比缩放副本(尺寸缩放 0.5/0.75/1/1.5/2× 的骨架级入口)。

        长度/半径×k;角度、DOF 限位、弹簧(τ/下沉角)、组不变——步态频率
        与姿态角与尺度无关(D2 §3.5:θ 与 s 无关,零重训)。
        """
        k = clamp(float(k), 0.25, 4.0)
        import copy
        s = copy.deepcopy(self.spec)
        s["scale"] = round(float(self.spec.get("scale", 1.0)) * k, 4)
        s["body_len_px"] = self.body_len * k
        for seg in s["segments"]:
            seg["x"] *= k; seg["y"] *= k; seg["rx"] *= k; seg["ry"] *= k
        for leg in s["legs"]:
            for key in ("attach", "home"):
                leg[key] = [v * k for v in leg[key]]
            for key in ("coxa", "femur", "tibia", "tarsus", "reach",
                        "trochanter", "claw"):
                if key in leg:
                    leg[key] = round(leg[key] * k, 4)
            if "diam" in leg:   # 直径×k(ADR-0026:直径是长度量纲)
                leg["diam"] = [round(v * k, 4) for v in leg["diam"]]
            if "len3d" in leg:  # v3:3D 节段长×k;rest3d/rom3d/tarsus_seg 不变
                leg["len3d"] = {lk: round(lv * k, 4)
                                for lk, lv in leg["len3d"].items()}
            # tarsomeres 为占比、ik 为无量纲标定系数、dof/角度:不随尺度变化
        if "antennae" in s:
            s["antennae"]["base"] = [v * k for v in s["antennae"]["base"]]
            s["antennae"]["len"] *= k
        if "wings" in s:
            s["wings"]["span"] *= k
            s["wings"]["tip_x"] *= k
            s["wings"]["hinge"] = [v * k for v in s["wings"]["hinge"]]
        if "cerci" in s:
            s["cerci"]["anchor"] = [v * k for v in s["cerci"]["anchor"]]
            s["cerci"]["len"] *= k
        return SkeletonSpec(s)


class _Channel:
    """单微弹性通道:一阶弹簧-阻尼(临界阻尼),dq/dt=(target−q)/τ。"""
    __slots__ = ("value", "tau")

    def __init__(self, tau: float) -> None:
        self.value = 0.0
        self.tau = max(0.008, float(tau))

    def update(self, dt: float, target: float) -> float:
        self.value += (target - self.value) * min(1.0, max(0.0, dt) / self.tau)
        return self.value


class MicroElastic:
    """微弹性滤波器组(D2 §2.4,每关节一阶弹簧-阻尼足够)。

    通道:
      * knee_sag[i](六腿):支撑相负载下沉(2~6° 等效的膝点横移 px),
        摆动相回零——"有重量感"的最小实现;
      * abdomen_lag(腹部偏航滞后):heading 的一阶跟踪,转向时腹末甩尾
        (差值钳 ±6°,照片:腹末随转向甩尾);
    参数(sag_tau_s/sag_max_deg)可被 GaitProfile 覆盖(D2 §3.2-9 训练维)。
    """

    def __init__(self, n_legs: int = 6, sag_tau_s: float = SAG_TAU_S,
                 sag_max_deg: float = SAG_MAX_DEG) -> None:
        self.sag_tau_s = sag_tau_s
        self.sag_max_deg = sag_max_deg
        self._sag = [_Channel(sag_tau_s) for _ in range(n_legs)]
        self._ab_heading: float | None = None   # 腹部航向(一阶滞后状态)
        self._ab_lag_tau = 0.12                 # 腹部滞后 τ≈120ms(欠阻尼小摆观感)

    def set_profile(self, sag_tau_s: float, sag_max_deg: float) -> None:
        """GaitProfile 注入(D2 §3.2-9:微弹性 (k_soft, τ) 为训练维)。"""
        self.sag_tau_s = clamp(float(sag_tau_s), 0.02, 0.12)
        self.sag_max_deg = clamp(float(sag_max_deg), 1.0, 8.0)
        for ch in self._sag:
            ch.tau = self.sag_tau_s

    def knee_sag(self, i: int, dt: float, stance: bool, load_norm: float = 1.0,
                 gain: float = 1.0) -> float:
        """第 i 腿的膝下沉量(px)。stance=支撑相;load_norm=负载归一;
        gain=姿态增益(警戒/惊觉姿态压腿更实)。下沉角 → 膝点横移按
        sag_px = sin(sink°)·LEVER·l_name,以名义节段 30px 归一(幅度 ~1.5px)。"""
        target = math.sin(math.radians(self.sag_max_deg)) * SAG_LEVER * 30.0 \
            * clamp(load_norm, 0.0, 1.5) * clamp(gain, 0.0, 2.0) if stance else 0.0
        return self._sag[i].update(dt, target)

    def abdomen_lag(self, dt: float, heading: float, max_deg: float = 6.0) -> float:
        """腹部偏航滞后(度):腹部航向对 heading 的一阶跟踪差值,钳 ±max_deg。

        返回 (dyaw_deg, ab_heading)——渲染层可再对腹部层独立旋转(Agent-3)。
        """
        if self._ab_heading is None:
            self._ab_heading = heading
        self._ab_heading += wrap_angle(heading - self._ab_heading) \
            * min(1.0, max(0.0, dt) / self._ab_lag_tau)
        dyaw = math.degrees(wrap_angle(heading - self._ab_heading))
        return clamp(dyaw, -max_deg, max_deg)

    def reset(self, heading: float = 0.0) -> None:
        """状态清零(瞬移/落地的连续性由调用方决定是否调用)。"""
        self._ab_heading = heading
        for ch in self._sag:
            ch.value = 0.0
