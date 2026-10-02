#!/usr/bin/env python3
"""AG2 离线步态训练 CLI(sep-CMA-ES 对角协方差,IPOP 式重启,纯 numpy)。

用法:
  python tools/train_gait.py --species roach --preset cruise
  python tools/train_gait.py --species fly --preset sprint [--iters N]
      [--seed S] [--lam N] [--sigma0 X] [--patience N] [--budget-s S] [--out P]

产物:data/gait/<species>_<preset>.json(命名遵 gait_profile.py 加载链
`<species>_<preset>.json`,species ∈ {cockroach, fruitfly};F2 冻结 schema,
provenance 如实记录 λ/代数/评估数/适应度曲线)。

== 算法(Hansen & Ostermeier 2001;Hansen 2016 tutorial;D2 §3.1/§3.4) ==
sep-CMA-ES:标准 CMA-ES 但协方差矩阵限定对角(秩-1 + 秩-μ 更新只作用于对角线),
采样/更新 O(D);D=29(训练向量 θ,GaitProfile params 白名单)。搜索域 [0,1]^29
(decode_theta 线性映射到文献带硬界:巡航 hz∈[3,8]、冲刺 hz∈[10,15]、
duty ∈[0.42,0.50] 等,D2 §4 验收线;越界分量被钳制=再采样语义的廉价替代)。
IPOP 式重启(D2 §3.4 口径):连续 --patience 代无改进 → σ 减半、C/路径复位、
均值回到历史最优,继续搜索(实现为"重启"而非严格 IPOP 的 λ 倍增——本问题
评估吞吐受限,λ 倍增会撞墙钟;如实记录,任务书允许"λ 太慢按 IPOP 减")。

== 适应度(gait_sim.fitness_from_metrics,D2 §3.3 七项 + 结构罚项) ==
F = w_v·min(v̄/v_target,1.2) + w_st·stab − w_sl·slip − w_j·jerk − w_h·height
    − w_p·rest_dev − w_f·fall − w_il·interlock − w_ms·multiswing
    − w_sd·stride_band − w_bg·both_groups(仅工作域≤1.5×cruise,v1.2)
    − w_anchor·anchor
多档防作弊(D2 §3.5):F = min over 速度档(0.6/1.0 × 档位速度)
                        × mean over 域随机化种子(无随机化 + 1 个随机种子)。

== 确定性 ==
随机源唯一:np.random.default_rng(--seed);域随机化种子、评估协议常数全部
固定(gait_sim.EVAL_*/TRAIN_SEEDS)。唯一非确定性来源是 --budget-s 墙钟熔断
(触发与否取决于机器负载)——复现实验请用 --iters/--lam 小参数(测试即如此),
勿依赖预算熔断的边界行为。

== 防退化保证 ==
历史最优候选集里**永远包含随库默认 θ**(第 0 代 0 号候选注入 + 落盘前与
默认表同协议对评,取优落盘):训练产物适应度 ≥ 默认基线,最差情形=原样落盘
默认参数(诚实记录,不虚构提升)。

== 落盘前投影(gait_sim.project_params) ==
① hz/duty 单调 repairing(运行时映射单调的结构前提);② 直线匀速仿真器中
不可辨识的 14 维(触发 3/应急 2/摆窗 1/差速 2/体摆 3/微弹性 2/rest_cap 1)
回收先验默认 —— 它们在真实行为层仍被消费,但本仿真器观测不到,任何"训练值"
都是漂移噪声(逐条理由见 gait_sim._ANCHOR_KEYS 注释);③ 三角互锁结构投影
(v1.2b group_phase=[0,0.5] + v1.3 duty 结点=0.5):工作域 both_groups=0 是
硬验收,其可行域是零测度结构切片,搜索经 project_pv 直接在可行面上进行。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from neuropet.body import gait_sim as gs
from neuropet.body.gait_profile import DEFAULT_PARAMS, save_profile

CODE_VERSION = "ag2-train-1.3"   # 1.3 = duty 结点互锁投影(project_pv);1.2 = W_BG=6 且限工作域;1.1 = +W_BG;1.0 = 初版
SPECIES_ALIASES = {"roach": "cockroach", "fly": "fruitfly",
                   "cockroach": "cockroach", "fruitfly": "fruitfly"}
PRESETS = ("cruise", "sprint")
MIN_SIGMA = 1e-3
EPS = 1e-9          # "有改进"的阈值(适应度量纲 ~O(1))


# ===================================================================
# sep-CMA-ES(对角协方差;Hansen 2016 tutorial §38 "sep-CMA-ES" 简化)
# ===================================================================
class SepCMAES:
    """对角 CMA-ES:秩-1/秩-μ 更新只作用于 C 的对角线,采样 O(D)。

    状态:均值 m(θ 空间 [0,1]^29)、步长 σ、对角协方差 C、演化路径 pc/ps。
    """

    def __init__(self, x0: np.ndarray, sigma0: float, lam: int,
                 rng: np.random.Generator) -> None:
        self.n = int(x0.size)
        self.m = x0.astype(np.float64).copy()
        self.sigma = float(sigma0)
        self.lam = int(lam)
        self.mu = max(1, self.lam // 2)
        raw = np.log(self.mu + 1.0) - np.log(np.arange(1, self.mu + 1, dtype=np.float64))
        self.w = raw / raw.sum()                      # 正权重归一
        self.mueff = 1.0 / float((self.w ** 2).sum())
        n = self.n
        self.cc = (4.0 + self.mueff / n) / (n + 4.0 + 2.0 * self.mueff / n)
        self.cs = (self.mueff + 2.0) / (n + self.mueff + 5.0)
        self.damps = (1.0 + 2.0 * max(0.0, math.sqrt((self.mueff - 1.0) / (n + 1.0)) - 1.0)
                      + self.cs)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1.0 - self.c1,
                       2.0 * (self.mueff - 2.0 + 1.0 / self.mueff)
                       / ((n + 2.0) ** 2 + self.mueff))
        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.diag_c = np.ones(n)
        self.chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))
        self.rng = rng
        self.t = 0

    def ask(self) -> np.ndarray:
        """采样 λ 个候选 (λ, D):x = m + σ·√diag(C)·z。"""
        z = self.rng.standard_normal((self.lam, self.n))
        return self.m + self.sigma * np.sqrt(self.diag_c) * z

    def tell(self, X: np.ndarray, fvals: np.ndarray) -> None:
        """以最小化 fvals 完成一代更新(tutorial 标准式;hsig 防路径爆炸)。"""
        self.t += 1
        order = np.argsort(fvals, kind="stable")
        y = (X[order[:self.mu]] - self.m) / self.sigma        # 相对旧均值
        yw = self.w @ y                                        # 加权均值方向
        self.m = self.m + self.sigma * yw
        # 共轭演化路径 ps(C^{-1/2} yw;对角 C 时=逐维除以 √C)
        invsq = 1.0 / np.sqrt(self.diag_c)
        self.ps = ((1.0 - self.cs) * self.ps
                   + math.sqrt(self.cs * (2.0 - self.cs) * self.mueff) * invsq * yw)
        hsig_exp = 2.0 * self.t
        hsig = (float(np.linalg.norm(self.ps))
                / math.sqrt(1.0 - (1.0 - self.cs) ** hsig_exp)
                / self.chi_n) < (1.4 + 2.0 / (self.n + 1.0))
        # 协方差演化路径 pc(hsig 假时退火,防快速适应后的坐标爆炸)
        self.pc = ((1.0 - self.cc) * self.pc
                   + math.sqrt(self.cc * (2.0 - self.cc) * self.mueff) * yw
                   * (1.0 if hsig else 0.0))
        # 对角 C 更新(秩-1 + 秩-μ;hsig 修正项同 tutorial 式 (56))
        self.diag_c = ((1.0 - self.c1 - self.cmu) * self.diag_c
                       + self.c1 * (self.pc ** 2
                                    + (1 - hsig) * self.cc * (2.0 - self.cc) * self.diag_c)
                       + self.cmu * (self.w @ (y * y)))
        # 步长自适应(路径长度累积)
        self.sigma *= math.exp((self.cs / self.damps)
                               * (float(np.linalg.norm(self.ps)) / self.chi_n - 1.0))
        # 数值防护(域宽 1.0,超界即退化)。σ 上限 0.5:截止域中点——θ 域
        # [0,1] 且 decode 硬截断,σ>0.5 后采样近似均匀噪声(选择失效,实测
        # σ 会中性漂移到 1.9+ 并把搜索拖成随机游走);下限防步长塌缩。
        self.diag_c = np.clip(self.diag_c, 1e-10, 1e4)
        self.sigma = min(max(self.sigma, MIN_SIGMA), 0.5)


# ===================================================================
# 训练主循环
# ===================================================================
def train_gait(species: str, preset: str, lam: int = 2048, iters: int = 60,
               seed: int = 20260916, sigma0: float = 0.25, patience: int = 12,
               budget_s: float = 300.0, log: bool = True) -> dict:
    """训练一个 (species, preset) 步态参数表,返回结果字典(含产物参数)。

    全程确定性(除非 --budget-s 熔断触发,见模块 docstring)。
    """
    sp_key = SPECIES_ALIASES.get(species)
    if sp_key is None or preset not in PRESETS:
        raise SystemExit(f"未知物种/预设: {species}/{preset}(支持 roach|fly × cruise|sprint)")
    rng = np.random.default_rng(seed)
    geom = gs.species_geometry(sp_key)
    consts = gs.resolve_gait_constants(gs.species_params(sp_key), None)
    speeds = gs.preset_speeds(sp_key, preset)
    train_seeds = list(gs.TRAIN_SEEDS)

    theta_def = gs.default_theta(sp_key)[0].copy()      # (29,) 随库默认(含带沿)
    x0 = np.clip(theta_def, 0.20, 0.80)                 # 均值离带沿(带沿默认点
    es = SepCMAES(x0, sigma0, lam, rng)                 # 由第 0 代 0 号候选兜底)

    best_f = -math.inf
    best_x = theta_def.copy()                            # 历史最优含默认 ⇒ 退化保证
    history: list[list[float]] = []
    evals = 0
    restarts = 0
    stall = 0
    t0 = time.perf_counter()
    budget_hit = False
    gens_run = 0
    for gen in range(iters):
        X = es.ask()
        if gen == 0:
            X[0] = theta_def                             # 默认 θ 参与第 0 代评估
        pv = gs.decode_theta(X, sp_key)
        pv = gs.project_pv(pv)          # 互锁结构投影:搜索在可行面上进行,
        # 落盘后测试复算(evaluate_params 于同一参数)与训练适应度逐位一致
        F, _diag = gs.evaluate_population(geom, consts, pv, speeds, train_seeds,
                                          n_frames=gs.EVAL_FRAMES,
                                          warmup_frames=gs.EVAL_WARMUP)
        evals += int(lam)
        gens_run += 1
        imax = int(np.argmax(F))
        if float(F[imax]) > best_f + EPS:
            best_f = float(F[imax])
            best_x = X[imax].copy()
            stall = 0
        else:
            stall += 1
        history.append([best_f, float(F.mean())])
        if log:
            print(f"  [{sp_key}/{preset}] gen {gen + 1:3d}/{iters} "
                  f"best={best_f:.5f} mean={float(F.mean()):.5f} "
                  f"sigma={es.sigma:.3f} stall={stall}", flush=True)
        es.tell(X, -F)
        if stall >= patience:                            # IPOP 式:σ 减半重启
            es = SepCMAES(best_x.copy(), max(es.sigma * 0.5, MIN_SIGMA), lam, rng)
            stall = 0
            restarts += 1
            if log:
                print(f"  [{sp_key}/{preset}] restart #{restarts} "
                      f"(sigma={es.sigma:.4f}, m<-best)", flush=True)
        if time.perf_counter() - t0 > budget_s:
            budget_hit = True
            if log:
                print(f"  [{sp_key}/{preset}] 墙钟预算 {budget_s:.0f}s 触发,"
                      f"第 {gens_run} 代收场(如实记录)", flush=True)
            break

    wall = time.perf_counter() - t0

    # ---- 落盘前:投影(单调 + 不可辨识维回收)后与默认表同协议对评,取优 ----
    raw_params = gs.theta_to_params(gs.decode_theta(best_x[None, :], sp_key))
    proj_params = gs.project_params(raw_params, sp_key)
    def_params = dict(DEFAULT_PARAMS)
    f_proj = gs.evaluate_params(sp_key, proj_params, preset)
    f_def = gs.evaluate_params(sp_key, def_params, preset)
    if f_proj >= f_def:
        ship_params, ship_f = proj_params, f_proj
        ship_src = "trained"
    else:                                                # 训练未超过基线 ⇒ 原样默认
        ship_params, ship_f = def_params, f_def
        ship_src = "default-fallback"

    # ---- 一致性自检(部署侧 <1e-6 硬线,不过就拒绝落盘) ----
    spp = gs.species_params(sp_key)
    cons_err = 0.0
    for v in (float(spp["cruise"]), float(spp["sprint"])):
        err, _n = gs.consistency_max_error(sp_key, ship_params, v, 300)
        cons_err = max(cons_err, err)
    if cons_err >= 1e-6:
        raise SystemExit(f"一致性自检失败 max_err={cons_err:.3e} px ≥ 1e-6,拒绝落盘")

    return {
        "species": sp_key, "preset": preset, "params": ship_params,
        "ship_source": ship_src,
        "fitness_final": ship_f, "fitness_baseline": f_def,
        "improvement": ship_f - f_def,
        "fitness_raw_best": best_f,                       # 投影前历史最优(参考)
        "generations": gens_run, "evaluations": evals, "restarts": restarts,
        "lambda": int(lam), "mu": int(lam // 2), "sigma0": float(sigma0),
        "patience": int(patience), "iters_requested": int(iters),
        "seed": int(seed), "budget_hit": bool(budget_hit), "wall_s": wall,
        "fitness_history": history, "speeds_px": speeds,
        "train_seeds": [s for s in gs.TRAIN_SEEDS], "eval_seeds": [s for s in gs.EVAL_SEEDS],
        "n_frames": gs.EVAL_FRAMES, "warmup_frames": gs.EVAL_WARMUP,
        "consistency_max_err_px": cons_err, "code_version": CODE_VERSION,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="离线步态训练(sep-CMA-ES,D2 §3)")
    ap.add_argument("--species", required=True, help="roach | fly(或全名)")
    ap.add_argument("--preset", required=True, help="cruise | sprint")
    ap.add_argument("--iters", type=int, default=60, help="最大代数(默认 60)")
    ap.add_argument("--seed", type=int, default=20260916, help="确定性种子")
    ap.add_argument("--lam", type=int, default=2048, help="种群规模(默认 2048)")
    ap.add_argument("--sigma0", type=float, default=0.25, help="初始步长([0,1] θ 空间)")
    ap.add_argument("--patience", type=int, default=12, help="重启无改进代数")
    ap.add_argument("--budget-s", type=float, default=300.0, help="墙钟熔断(s)")
    ap.add_argument("--out", default=None, help="落盘路径(默认 data/gait/<sp>_<preset>.json)")
    args = ap.parse_args()

    res = train_gait(args.species, args.preset, lam=args.lam, iters=args.iters,
                     seed=args.seed, sigma0=args.sigma0, patience=args.patience,
                     budget_s=args.budget_s)
    sp_key, preset = res["species"], res["preset"]
    out = Path(args.out) if args.out else \
        Path(__file__).resolve().parents[1] / "data" / "gait" / f"{sp_key}_{preset}.json"
    provenance = {
        "algorithm": "sep-CMA-ES(diag)+IPOP-style-sigma-halving-restart",
        "seed": res["seed"], "generations": res["generations"],
        "evaluations": res["evaluations"],
        "fitness_history": [[round(b, 6), round(m, 6)] for b, m in res["fitness_history"]],
        "code_version": res["code_version"],
        # ---- 以下为实施记录扩展字段(如实记录;F2 白名单外透传语义) ----
        "lambda": res["lambda"], "mu": res["mu"], "sigma0": res["sigma0"],
        "iters_requested": res["iters_requested"], "restarts": res["restarts"],
        "budget_hit": res["budget_hit"], "wall_s": round(res["wall_s"], 2),
        "fitness_final": round(res["fitness_final"], 6),
        "fitness_baseline_default": round(res["fitness_baseline"], 6),
        "improvement": round(res["improvement"], 6),
        "fitness_raw_best_unprojected": round(res["fitness_raw_best"], 6),
        "ship_source": res["ship_source"],
        "speeds_px": res["speeds_px"], "train_seeds": res["train_seeds"],
        "eval_seeds": res["eval_seeds"], "n_frames": res["n_frames"],
        "warmup_frames": res["warmup_frames"],
        "consistency_max_err_px": res["consistency_max_err_px"],
        "projection": "monotone-hz/duty + unidentifiable-dims->default (gait_sim.project_params)",
    }
    save_profile(res["params"], sp_key, preset, out, provenance)

    print(json.dumps({
        "saved": str(out), "species": sp_key, "preset": preset,
        "ship_source": res["ship_source"],
        "fitness_baseline": round(res["fitness_baseline"], 6),
        "fitness_final": round(res["fitness_final"], 6),
        "improvement": round(res["improvement"], 6),
        "generations": res["generations"], "evaluations": res["evaluations"],
        "restarts": res["restarts"], "wall_s": round(res["wall_s"], 1),
        "budget_hit": res["budget_hit"],
        "consistency_max_err_px": res["consistency_max_err_px"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
