"""AG5 尺寸缩放五档验收(0.5 / 0.75 / 1 / 1.5 / 2)。

交接文档 §六 把 AG5 记为"中途被停",但磁盘实况是缩放管线已完整落地
(`core/app.py`:SCALE_CHOICES / snap_scale / scale_params / set_pet_scale /
_load_scale / _save_scale / NEUROPET_SCALE;`ui/panel.py` 下拉框已接线;
`rig.SkeletonSpec.scaled(k)` 已就绪)。**真正缺的一直是验证** —— `scale_params`
的 docstring 声称等价性契约

    from_params(species, scale_params(P, k)) ≡ from_params(species, P).scaled(k)

"由 tests/test_scaling.py 断言",而这个测试此前并不存在,契约从未被验证。
本文件就是那个断言,并额外守"等比缩放、行为节奏不变"这条真正的产品语义。

覆盖:
  ① snap_scale 吸附(含越界/离档);
  ② 等价性契约(逐档、逐几何量,记录残差);
  ③ 等比不变性(腿长/体长比、静息角、DOF 限位、节段数 与尺度无关);
  ④ BL 单位制速度恒定(v[px/s] 与 body_len 同 ×k → v[BL/s] 不变);
  ⑤ 五档都能构建合法身体并稳定步进(无 NaN / 无越界);
  ⑥ 档位持久化往返(data/profiles/<pet_id>/scale.json,落 tmp 目录);
  ⑦ NEUROPET_SCALE 覆盖通道;
  ⑧ k=1 路径退化一致(scale_params(P,1.0) 不改变任何几何量)。

直跑:`python tests/test_scaling.py`(也可被 pytest 收集)。
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import neuropet.core.app as appmod
from neuropet.core.app import SCALE_CHOICES, scale_params, snap_scale
from neuropet.core.contracts import Behavior, BehaviorCommand, PetState
from neuropet.core.world import WorldModel
from neuropet.body.base import GenericInsectBody
from neuropet.body.rig import SkeletonSpec
from neuropet.species.cockroach import PARAMS as ROACH
from neuropet.species.fruitfly import PARAMS as FLY

FAILS: list[str] = []
DT = 1.0 / 60.0
SCREEN = (1920, 1080)
LEVELS = (0.5, 0.75, 1.0, 1.5, 2.0)
# 等价性残差容差(px):两条路径的取整次序不同 ——
#   A: scale_params 先 round(v*k,4),再经 from_params 的 round(...,3)
#   B: from_params 先 round(...,3),再经 scaled(k) 的 round(...,4)
# 两次 round 的次序差异留下 ≤1e-3 px 级残差,属纯数值噪声,不影响几何语义。
EQ_TOL_PX = 0.02


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


# ------------------------------------------------------------------ 工具
def _flat(obj, prefix: str = "") -> dict:
    """把 spec 拍平成 {路径: 数值},便于逐量比对。"""
    out: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_flat(v, f"{prefix}[{i}]"))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix] = float(obj)
    return out


def _max_diff(a: dict, b: dict) -> tuple[float, str]:
    worst, where = 0.0, ""
    for key in set(a) & set(b):
        d = abs(a[key] - b[key])
        if d > worst:
            worst, where = d, key
    return worst, where


def _build(params: dict, tag: str):
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=tag, species_id=tag, pos=(960.0, 540.0))
    return w, GenericInsectBody(st, dict(params))


# ------------------------------------------------------------------ ① 吸附
def test_snap_scale() -> None:
    print("[1] snap_scale 吸附")
    check(tuple(float(c) for c in SCALE_CHOICES) == LEVELS,
          f"SCALE_CHOICES 为五档 {tuple(SCALE_CHOICES)}")
    cases = ((0.4, 0.5), (0.55, 0.5), (0.62, 0.5), (0.7, 0.75), (0.9, 1.0), (1.2, 1.0),
             (1.4, 1.5), (1.9, 2.0), (3.0, 2.0), (1.0, 1.0), (0.0, 0.5))
    bad = [(p, float(snap_scale(p)), e) for p, e in cases
           if abs(float(snap_scale(p)) - e) > 1e-9]
    check(not bad, f"越界/离档值吸附到最近档(反例 {bad})")
    check(abs(float(snap_scale(1.0)) - 1.0) < 1e-12, "k=1 精确命中")


# ------------------------------------------------------------------ ② 等价性
def test_equivalence() -> None:
    print("[2] 等价性契约 from_params(scale_params(P,k)) ≡ from_params(P).scaled(k)")
    for name, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        base = SkeletonSpec.from_params(name, params)
        for k in LEVELS:
            a = SkeletonSpec.from_params(name, scale_params(params, k))
            b = base.scaled(k)
            fa, fb = _flat(a.to_dict()), _flat(b.to_dict())
            missing = set(fa) ^ set(fb)
            if missing:
                check(False, f"{name} k={k}:两路径键集合不一致 {sorted(missing)[:6]}")
                continue
            worst, where = _max_diff(fa, fb)
            check(worst <= EQ_TOL_PX,
                  f"{name} k={k}:几何量最大残差 {worst:.4f}px(≤{EQ_TOL_PX})"
                  f"{' @' + where if worst > 0 else ''}")
        # 缩放倍率本身:scaled(k) 应把 body_len 精确 ×k
        k = 2.0
        got = SkeletonSpec.from_params(name, scale_params(params, k)).body_len
        want = base.body_len * k
        check(abs(got - want) < 0.01,
              f"{name}:k=2 体长 {got:.1f}px ≈ 基准×2 = {want:.1f}px")


# ------------------------------------------------------------------ ③ 等比不变性
def test_ratio_invariance() -> None:
    print("[3] 等比不变性(比例/角度/限位与尺度无关)")
    for name, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        base = SkeletonSpec.from_params(name, params)
        for k in LEVELS:
            sp = SkeletonSpec.from_params(name, scale_params(params, k))
            ok, worst = True, 0.0
            for lb, ls in zip(base.spec["legs"], sp.spec["legs"]):
                for key in ("femur", "tibia", "tarsus", "coxa", "reach"):
                    r_b = lb[key] / base.body_len
                    r_s = ls[key] / sp.body_len
                    worst = max(worst, abs(r_b - r_s))
                    if abs(r_b - r_s) > 5e-4:
                        ok = False
                if abs(lb["rest_yaw_deg"] - ls["rest_yaw_deg"]) > 1e-6:
                    ok = False
                if lb["dof"] != ls["dof"]:
                    ok = False
            check(ok, f"{name} k={k}:腿节段/体长比、静息角、DOF 限位不变"
                      f"(最大比例残差 {worst:.2e})")
            check(all(sb["rx"] / base.body_len - ss["rx"] / sp.body_len < 5e-4
                      for sb, ss in zip(base.spec["segments"], sp.spec["segments"])),
                  f"{name} k={k}:体节比例不变")
        # 触角节数是整数,不随尺度变
        check(all(SkeletonSpec.from_params(name, scale_params(params, k))
                  .spec["antennae"]["segments"]
                  == SkeletonSpec.from_params(name, params).spec["antennae"]["segments"]
                  for k in LEVELS),
              f"{name}:触角节数不随档位变(整数语义)")


# ------------------------------------------------------------------ ④ BL 速度恒定
def test_bl_speed_invariance() -> None:
    print("[4] BL 单位制:v[BL/s] 与档位无关(px/s 与 body_len 同 ×k)")
    for name, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        base_bl = params["cruise"] / params["body_len"]
        worst = 0.0
        for k in LEVELS:
            p = scale_params(params, k)
            worst = max(worst, abs(p["cruise"] / p["body_len"] - base_bl))
        check(worst < 1e-3,
              f"{name}:五档巡航 {base_bl:.3f}BL/s 恒定(最大偏差 {worst:.2e})")
        # 反过来:px/s 必须真的随档位走
        got = [scale_params(params, k)["cruise"] for k in LEVELS]
        ok = all(b > a for a, b in zip(got, got[1:]))
        check(ok, f"{name}:cruise[px/s] 随档位单调递增 {[round(g,1) for g in got]}")


# ------------------------------------------------------------------ ⑤ 五档可跑
def test_five_levels_build() -> None:
    print("[5] 五档构建合法身体并稳定步进 120 帧")
    for name, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        halves = []
        for k in LEVELS:
            w, body = _build(scale_params(params, k), f"{name}{k}")
            halves.append(body.window_half())
            bad = ""
            for i in range(120):
                body.apply(BehaviorCommand(Behavior.EXPLORE, priority=1),
                           w.snapshot(f"{name}{k}", i * DT), DT)
                pose = body.pose()
                if not _pose_finite(pose):
                    bad = f"第 {i} 帧 pose 含 NaN/Inf"
                    break
            check(not bad, f"{name} k={k}:120 帧无 NaN/越界{bad}")
        check(all(b >= a for a, b in zip(halves, halves[1:])),
              f"{name}:画布半宽随档单调不减 {halves}")


def _pose_finite(pose: dict) -> bool:
    def walk(o) -> bool:
        if isinstance(o, dict):
            return all(walk(v) for v in o.values())
        if isinstance(o, (list, tuple)):
            return all(walk(v) for v in o)
        if isinstance(o, (int, float)):
            return math.isfinite(float(o))
        return True
    return walk(pose)


# ------------------------------------------------------------------ ⑥ 持久化
def test_persistence_roundtrip() -> None:
    print("[6] 档位持久化往返(data/profiles/<pet_id>/scale.json,落 tmp)")

    class _H:
        def __init__(self, pid: str, k: float) -> None:
            self.pet_id, self.scale = pid, k

    orig = appmod.profile_dir
    with tempfile.TemporaryDirectory() as td:
        try:
            # 真实 profile_dir 的契约包含 mkdir(parents=True, exist_ok=True)
            def _tmp_profile(pid: str) -> Path:
                d = Path(td) / pid
                d.mkdir(parents=True, exist_ok=True)
                return d

            appmod.profile_dir = _tmp_profile
            ok = True
            for k in LEVELS:
                h = _H("pet.test", k)
                appmod.App._save_scale(None, h)
                back = appmod.App._load_scale(None, "pet.test")
                if abs(back - k) > 1e-9:
                    ok = False
            check(ok, f"五档写入→读回一致(经 snap_scale 吸附)")
            # 损坏/缺失档:回退 1.0
            d = Path(td) / "pet.bad"
            d.mkdir(parents=True, exist_ok=True)
            (d / "scale.json").write_text("{not json", encoding="utf-8")
            check(abs(appmod.App._load_scale(None, "pet.bad") - 1.0) < 1e-9,
                  "损坏 scale.json 回退 1.0")
            (d / "scale.json").write_text(json.dumps({"scale": 1.37}),
                                          encoding="utf-8")
            check(abs(appmod.App._load_scale(None, "pet.bad") - 1.5) < 1e-9,
                  "离档存档值读回时被吸附(1.37→1.5)")
        finally:
            appmod.profile_dir = orig


# ------------------------------------------------------------------ ⑦ 环境覆盖
def test_env_override() -> None:
    print("[7] NEUROPET_SCALE 启动覆盖通道")
    old = os.environ.get("NEUROPET_SCALE")
    try:
        ok = True
        for k in LEVELS:
            os.environ["NEUROPET_SCALE"] = str(k)
            if abs(float(appmod.App._startup_scale_override()) - k) > 1e-9:
                ok = False
        check(ok, "五档环境变量均被正确吸附")
        os.environ["NEUROPET_SCALE"] = "abc"
        check(appmod.App._startup_scale_override() is None,
              "非法值被忽略(返回 None 走 profile 默认)")
        os.environ.pop("NEUROPET_SCALE", None)
        check(appmod.App._startup_scale_override() is None,
              "未设置时返回 None")
    finally:
        if old is None:
            os.environ.pop("NEUROPET_SCALE", None)
        else:
            os.environ["NEUROPET_SCALE"] = old


# ------------------------------------------------------------------ ⑧ k=1 退化
def test_k1_identity() -> None:
    print("[8] k=1 路径退化一致")
    for name, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        a = SkeletonSpec.from_params(name, scale_params(params, 1.0))
        b = SkeletonSpec.from_params(name, params)
        fa, fb = _flat(a.to_dict()), _flat(b.to_dict())
        worst, where = _max_diff(fa, fb)
        check(worst < 1e-6,
              f"{name}:scale_params(P,1.0) 与原 P 等价(残差 {worst:.2e}"
              f"{' @' + where if worst else ''})")
        # 不得污染物种单例 PARAMS
        check(params.get("scale") is None and "wing_tip_x" not in params
              and "cerci_anchor" not in params,
              f"{name}:scale_params 未污染物种单例 PARAMS(深拷贝生效)")


def main() -> None:
    print("=" * 74)
    print("AG5 尺寸缩放五档验收")
    print("=" * 74)
    test_snap_scale()
    test_equivalence()
    test_ratio_invariance()
    test_bl_speed_invariance()
    test_five_levels_build()
    test_persistence_roundtrip()
    test_env_override()
    test_k1_identity()
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
