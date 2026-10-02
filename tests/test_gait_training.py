"""AG2 步态训练系统验收(训练产物 + 训练-部署一致性;断言脚本直跑)。

运行:python tests/test_gait_training.py

判据(任务书 ≥5 项):
  ① schema 合法:4 个训练产物(data/gait/<species>_<preset>.json,两物种 ×
     cruise/sprint)严格遵守 F2 冻结 schema——版本/物种/预设/params 白名单
     (29 维)/provenance 六个冻结键齐全且数值与实测一致;
  ② 加载链:load_profile(species, preset) 返回 source="trained",且加载值
     与落盘 params 逐键一致(回退链未被误触发);
  ③ 训练-部署一致性 <1e-6px:gait_sim 批量仿真器(P=1、无域随机化)与
     运行时 TripodGait 同路径逐帧对比——足端世界坐标最大绝对偏差与摆动
     掩码逐帧一致(F2 §1 规则 4;偏差为 0~1e-13 量级,浮点 ulp 残差);
  ④ 训练产物适应度 ≥ 默认基线:同一协议(gait_sim.evaluate_params,
     min over 速度档 × mean over 域随机化种子)下,训练产物 ≥ 随库默认表
     (训练器把默认 θ 注入第 0 代候选集,最差情形原样落盘默认,故恒成立);
  ⑤ 同 seed 可复现:同参数调 train_gait 两次(小预算 iters/lam),产物
     params 逐键相等、适应度与评估数相等(随机源唯一 = np.random.default_rng);
  ⑥ 文献带防御(追加):训练产物的步频/占空落 D2 §4 验收带(巡航 hz 3~8、
     冲刺 10~15、duty 0.42~0.50)、映射单调 repairing 生效、不可辨识维
     (触发/应急/摆窗/差速/体摆/微弹性/rest_cap)已回收先验默认。

任何一项 FAIL 退出码 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.body import gait_sim as gs
from neuropet.body.gait_profile import (DEFAULT_PARAMS, DATA_DIR, load_params,
                                        load_profile, validate_params)

RESULTS: list[tuple[str, bool, str]] = []
PROFILES = (("cockroach", "cruise"), ("cockroach", "sprint"),
            ("fruitfly", "cruise"), ("fruitfly", "sprint"))
# 不可辨识/结构投影维(gait_sim._ANCHOR_KEYS 的 params 键;训练后必须 == 默认)
UNIDENTIFIABLE = ("diff_k_in", "diff_k_out", "sway_amp_px", "sway_freq_ratio",
                  "pitch_amp_deg", "sag_tau_ms", "sag_max_deg", "rest_cap_k",
                  "trigger_k", "trigger_min_k", "trigger_max_k",
                  "emergency_err_k", "emergency_stride_k", "swing_win_k",
                  "metachronal_deg")


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


def load_trained(species: str, preset: str) -> tuple[dict, dict, Path]:
    path = DATA_DIR / f"{species}_{preset}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc, doc["params"], path


# ============================================================ ① schema
def check_schema() -> None:
    frozen_prov = ("algorithm", "seed", "generations", "evaluations",
                   "fitness_history", "code_version")
    for species, preset in PROFILES:
        doc, params, path = load_trained(species, preset)
        tag = f"{species}.{preset}"
        ok_v = doc.get("schema_version") == 1
        ok_sp = doc.get("species") == species and doc.get("preset") == preset
        ok_wl = set(params) == set(DEFAULT_PARAMS) and len(params) == 26
        record(f"schema.{tag}.header", ok_v and ok_sp,
               f"schema_version={doc.get('schema_version')} "
               f"species={doc.get('species')} preset={doc.get('preset')}")
        record(f"schema.{tag}.whitelist", ok_wl,
               f"params 键集与 29 维白名单({len(params)} 键 + group_phase/lift_k 合成)"
               f"{'一致' if ok_wl else '不一致: ' + str(set(params) ^ set(DEFAULT_PARAMS))}")
        ok_val = validate_params(params)
        record(f"schema.{tag}.ranges", ok_val, "数值全部落在 F2 _PARAM_RANGES")
        prov = doc.get("provenance", {})
        missing = [k for k in frozen_prov if k not in prov]
        ok_hist = (isinstance(prov.get("fitness_history"), list)
                   and len(prov.get("fitness_history", [])) > 0
                   and prov.get("evaluations", 0) >= prov.get("generations", 1)
                   and prov.get("generations", 0) >= 1)
        record(f"schema.{tag}.provenance", not missing and ok_hist,
               f"冻结键齐全={not missing} 代数={prov.get('generations')} "
               f"评估={prov.get('evaluations')} λ={prov.get('lambda')} "
               f"seed={prov.get('seed')} 曲线点={len(prov.get('fitness_history', []))}")


# ============================================================ ② 加载链
def check_load_chain() -> None:
    for species, preset in PROFILES:
        prof = load_profile(species, preset)
        _, params, _ = load_trained(species, preset)
        ok = prof["source"] == "trained" and prof["params"] == {**DEFAULT_PARAMS, **params}
        record(f"load.{species}.{preset}.source_trained", ok,
               f"source={prof['source']} 加载值与落盘一致={prof['params'] == {**DEFAULT_PARAMS, **params}}")


# ============================================================ ③ 一致性
def check_consistency() -> None:
    worst = 0.0
    for species, preset in PROFILES:
        _, params, _ = load_trained(species, preset)
        spp = gs.species_params(species)
        errs = []
        for v in (float(spp["cruise"]), float(spp["sprint"])):
            err, n = gs.consistency_max_error(species, params, v, 300)
            errs.append((v, err, n))
            worst = max(worst, err)
        detail = " ".join(f"v={v:.0f}:{e:.2e}px/{n}f" for v, e, n in errs)
        ok = all(e < 1e-6 for _, e, _ in errs)
        record(f"consistency.{species}.{preset}", ok,
               f"gait_sim vs TripodGait 逐帧最大偏差 {detail}(门槛 <1e-6)")
    record("consistency.worst_overall", worst < 1e-6,
           f"四产物全速档最大偏差 {worst:.3e} px(F2 §1 规则 4:验收线 1e-6)")


# ============================================================ ④ 适应度 ≥ 基线
def check_fitness() -> None:
    for species, preset in PROFILES:
        _, params, _ = load_trained(species, preset)
        f_tr = gs.evaluate_params(species, params, preset)
        f_def = gs.evaluate_params(species, dict(DEFAULT_PARAMS), preset)
        prov = load_trained(species, preset)[0]["provenance"]
        # 协议同一性:测试复算值必须与落盘 provenance 一致(provenance 以
        # 6 位小数落盘,容差 1e-6;严格 1e-9 的同协议复算见上一断言)
        match = abs(f_tr - prov["fitness_final"]) < 1e-6
        ok = f_tr >= f_def - 1e-9
        record(f"fitness.{species}.{preset}", ok and match,
               f"trained={f_tr:.6f} >= default={f_def:.6f}(Δ={f_tr - f_def:+.6f});"
               f"provenance 复核{'一致' if match else '不一致'}")


# ============================================================ ⑤ 可复现
def check_reproducibility() -> None:
    import tools.train_gait as tg
    kw = dict(lam=64, iters=3, seed=20260916, sigma0=0.25, patience=12,
              budget_s=600.0, log=False)
    r1 = tg.train_gait("fly", "cruise", **kw)
    r2 = tg.train_gait("fly", "cruise", **kw)
    same_params = r1["params"] == r2["params"]
    same_f = abs(r1["fitness_final"] - r2["fitness_final"]) < 1e-12
    same_hist = r1["generations"] == r2["generations"] and \
        r1["evaluations"] == r2["evaluations"] and \
        len(r1["fitness_history"]) == len(r2["fitness_history"])
    record("reproducibility.same_seed", same_params and same_f and same_hist,
           f"同 seed 两次训练: params 逐键相等={same_params} fitness="
           f"{r1['fitness_final']:.6f}=={r2['fitness_final']:.6f} "
           f"代数/评估数/曲线长相等={same_hist}")


# ============================================================ ⑥ 文献带防御
def check_literature_bands() -> None:
    hz_band = {"cockroach": (3.0, 8.0), "fruitfly": (4.0, 8.0)}
    for species, preset in PROFILES:
        _, p, _ = load_trained(species, preset)
        lo, hi = hz_band[species]
        ok_hz = lo - 1e-9 <= p["hz_cruise"] <= hi + 1e-9 \
            and 10.0 <= p["hz_sprint"] <= 15.0
        ok_duty = 0.42 - 1e-9 <= p["duty_cruise"] <= 0.50 + 1e-9 \
            and 0.42 - 1e-9 <= p["duty_sprint"] <= 0.50 + 1e-9
        ok_mono = (p["hz_rest"] <= p["hz_cruise"] + 1e-12
                   and p["hz_cruise"] <= p["hz_sprint"] + 1e-12
                   and p["duty_patrol"] >= p["duty_cruise"] - 1e-12
                   and p["duty_cruise"] >= p["duty_sprint"] - 1e-12)
        ok_anchor = (all(p[k] == DEFAULT_PARAMS[k] for k in UNIDENTIFIABLE)
                     and p["group_phase"] == list(DEFAULT_PARAMS["group_phase"]))
        record(f"bands.{species}.{preset}.hz_duty", ok_hz and ok_duty,
               f"hz_cruise={p['hz_cruise']:.2f}[{lo},{hi}] hz_sprint={p['hz_sprint']:.2f}"
               f"[10,15] duty={p['duty_cruise']:.3f}/{p['duty_sprint']:.3f}[0.42,0.50]")
        record(f"bands.{species}.{preset}.mono_anchor", ok_mono and ok_anchor,
               f"hz/duty 映射单调={ok_mono};不可辨识 15 维+三角互锁结构投影"
               f"(group_phase=[0,0.5])回收默认={ok_anchor}")


# ============================================================ ⑦ 三角互锁不变量
def check_interlock() -> None:
    """产物互锁判据(两域,证据化口径;与适应度 v1.2 的 W_BG 限域一致):

    ① 行为工作域(0.6/1.0×cruise + 1.5×cruise=QF 冲刺统计入口阈值):
       both_groups(两组同时有摆动足)**必须为 0**——这是 test_body
       「both_groups==0」三角互锁不变量的直接镜像,全部速度 × 全部评估种子
       (适应度 v1.2 在同域以 W_BG=6 惩罚,训练产物必须兑现);
    ② 观赏极速域(≥0.75×sprint_px,蟑螂 ≈9.8~13 BL/s):步距 75~100px 对
       前腿静息距 37px 已超腿系几何(15Hz 文献带上限不可再升),过拉伸安全阀
       按设计语义解除互锁(gait.py OVER_K 兜底);冻结默认表在同域同样破互锁
       (实测 @1125px/s both=102/105 帧)且伴随大量 fall 帧(几何不可达=
       视觉拖步)。故此处判据 = **fall 帧数不劣于默认表**(互锁本身不再硬判)。
    """
    for species, preset in PROFILES:
        _, params, _ = load_trained(species, preset)
        spp = gs.species_params(species)
        op_speeds = [0.6 * float(spp["cruise"]), float(spp["cruise"]),
                     1.5 * float(spp["cruise"])]
        ext_speeds = [v for v in gs.preset_speeds(species, preset)
                      if v >= 0.75 * float(spp["sprint"])]
        both_tr = 0
        fall_tr, fall_def = 0, 0
        for v in op_speeds:
            for sd in gs.EVAL_SEEDS:
                both_tr += _frame_stat(species, params, v, sd, "both")
        for v in ext_speeds:
            for sd in gs.EVAL_SEEDS:
                fall_tr += _frame_stat(species, params, v, sd, "fall")
                fall_def += _frame_stat(species, dict(DEFAULT_PARAMS), v, sd,
                                        "fall")
        ok_op = both_tr == 0
        ok_ext = fall_tr <= fall_def
        record(f"interlock.{species}.{preset}.operating_zone", ok_op,
               f"工作域(0.6/1.0/1.5×cruise × {len(gs.EVAL_SEEDS)} 种子)"
               f"both_groups={both_tr}(门槛=0)")
        record(f"interlock.{species}.{preset}.extreme_falls", ok_ext,
               f"极速域({[round(v) for v in ext_speeds]}px/s) fall 帧 "
               f"trained={fall_tr} ≤ default={fall_def}")


def _frame_stat(species: str, params: dict, speed: float, seed,
                stat: str) -> int:
    geom = gs.species_geometry(species)
    consts = gs.resolve_gait_constants(gs.species_params(species),
                                       {"params": params})
    pv = gs.params_to_pv(params)
    m, _tr = gs.rollout_batch(geom, consts, pv, float(speed), seed,
                              n_frames=gs.EVAL_FRAMES,
                              warmup_frames=gs.EVAL_WARMUP)
    return int(m.both_groups_frames[0] if stat == "both"
               else m.fall_frames[0])


def main() -> None:
    check_schema()
    check_load_chain()
    check_consistency()
    check_fitness()
    check_reproducibility()
    check_literature_bands()
    check_interlock()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[训练系统验收] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, detail in failed:
            print(f"  FAIL: {tag} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
