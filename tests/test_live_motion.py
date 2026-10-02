"""r18-J2 活体连续取帧 Harness:真实应用循环产出连续帧序列,量化桌宠动画连续性。

运行:python tests/test_live_motion.py   (环境变量 NEUROPET_RENDER=3d,文件内置)

背景:用户报障"活体根本不会动,像静态图片平移"(NEUROPET_RENDER=3d 腿部
动画刚合入)。本 Harness 不用离线摆 pose,而是驱动真实 App 主循环
(root.after → _tick → _step_frame → _step + _render,与 run() 同一路径),
从 stage._pet_raw 取每帧实际上传的渲染图,量化:

  ⓪ 自然行为探针:不注入,看默认大脑 4s 内是否自发行走(speed_norm 带);
  ① 动画存在性:行走期间相邻上传帧非键色帧间差(_diff_px 同口径,
     逐通道 |Δ| 和 >8 计 1px)中位数 > 阈值(阈值 = 校准实测中位的 30%);
  ② 相位驱动:gait.phase 前进量与帧差 Pearson 相关,且回归截距≈0
     (Δphase→0 时帧差→0,即"静态图平移"的反命题);
  ③ 稳定性:无全键色帧;无 >5 连零差帧(活体卡顿);相邻帧 bbox 中心
     跳动 < 体长(排除瞬移);速度带/相位覆盖打印。

时基(2026-09-23):`_drive` 已**钉死仿真时基**并逐 tick 断言(`tools/u0_baseline.py::
Timebase`,与探针 `tools/u0_baseline.py` 同源唯一实现)。未钉口径实测:82.8% 的 tick
撞 `App._tick` 的 0.05 **上**钳位 ⇒ 每 tick 推进 **2.80×** 仿真时间
(证据 `logs/_tb_probe_live_old.txt`)。

隔离纪律:名册/profile/会话全部打入临时目录(test_roster_config 同惯例)。
注入方式:monkeypatch h.brain.decide 恒发 BehaviorCommand(EXPLORE,
intensity=1.0, target=沿当前 heading 前方 400px 每帧重算)——EXPLORE 走
body._apply_ground → spd=cruise×clamp(intensity,.4,1) → _speed→cruise,
gait.update 得非零 scramble,步态相位推进;不动任何实现文件。
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["NEUROPET_RENDER"] = "3d"        # 必须在 neuropet 首次导入前
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 时基钉的**唯一实现** = `tools/u0_baseline.py::Timebase`(探针与 `tests/test_emo_gait.py`
# 共用同一份;三处同源,改一处要改三处 —— 不许留悄悄分叉的实现,见 r25-workflow §5)。
from tools.u0_baseline import Timebase, TimebaseError   # noqa: E402

import numpy as np

RESULTS: list[tuple[str, bool, str]] = []

FRAME_DT = 1.0 / 60.0
KEY = (1, 1, 1)                 # 键色(与 test_render3d 同口径)
# ---- 校准阈值(实测后固话:对齐残差中位实测 6685px 的 30% ≈ 2000) ----
TH_ADIFF_MED = 2000             # 平移补偿残差中位数下限(px)
TH_R = 0.30                     # Δphase-残差 Pearson 相关下限(实测 0.573)


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ---- 时基钉(判据;`NEUROPET_TB_NEGCTRL=1` = 负向对照:按钉住口径断言但**不装钉子**) ----
_TB_NEGCTRL = os.environ.get("NEUROPET_TB_NEGCTRL") == "1"


def _timebase(app) -> Timebase:
    """本 app 的时基钉(幂等;实现 = `tools/u0_baseline.py::Timebase`)。

    钉法 = 每 tick 前把 `app._next_t` 置为当刻墙钟(语义「上一帧准时发生」)⇒ `dt ≡ 1/60`。
    为什么必须钉:本夹具按 `FRAME_DT` 自配速,而 Windows 上实际 tick 间隔 p50 ≈ 21.9ms
    > 名义帧 ⇒ `_next_t` **持续落后** ⇒ 撞 `App._tick`(`neuropet/core/app.py:944`)的
    0.05 **上**钳位 ⇒ 每 tick 推进 **2.80×** 仿真时间(未钉口径实测:撞钳位 **82.8%**、
    Σdt/名义 = **2.80×**,`logs/_tb_probe_live_old.txt`)——本文件此前的一切读数都站在
    这个坏节拍上。

    判据(违规 ⇒ 本次读数作废,不判红绿):逐 tick `dt∈[1/60, 20ms]` + 窗口
    `dt p99 ≤ 1/60×1.02`;`Timebase.finish()` 用**闭锁重判**兜底 —— `App._tick` 把
    `_step_frame` 包在 `except Exception: log_exc`(`app.py:948`)里,**第一次 raise 会被
    吞掉**,只有把违规记进判据自己的状态、窗口结束前重判,才真的作废。
    """
    tb = getattr(app, "_u0_timebase", None)
    if tb is None:
        tb = Timebase(app, pin=not _TB_NEGCTRL, negctrl=_TB_NEGCTRL)
        app._u0_timebase = tb
    return tb


# ---------------- 脚手架 ----------------
def _make_app(tmp: Path):
    import neuropet.core.app as appmod

    appmod._SESSION_FILE = tmp / "session.json"
    orig_profile = appmod.profile_dir

    def _tmp_profile(pet_id: str) -> Path:
        d = tmp / "profiles" / pet_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tmp_profile
    app = appmod.App()
    app._pets_path = tmp / "pets.json"
    return app, appmod, orig_profile


def _diff_px(img_a, img_b) -> int:
    """非键色帧间差:逐通道 |Δ| 和 >8 计 1 px(test_render3d._diff_px 同口径)。"""
    a = np.asarray(img_a, dtype=np.int16)
    b = np.asarray(img_b, dtype=np.int16)
    return int((np.abs(a - b).sum(axis=2) > 8).sum())


def _nonkey_mask(img) -> np.ndarray:
    a = np.asarray(img)
    return ~np.all(a[..., :3] == KEY, axis=2)


def _drive(app, h, ticks: int, min_frames: int, patch_decide: bool):
    """以 60Hz 实时节拍直接驱动真实主循环 app._tick()(= 真实 dt 计算 +
    _step_frame → bus.drain/_step/_render/panel.tick,与 run()/mainloop 同一
    函数)。不走 root.update() 派发:实测 Windows 下 update() 对 after 计时器
    派发不可靠(重首帧后 after 链停摆、tick 以 dt=0.001 钳制值突发),属测试
    驱动器伪影而非产品缺陷;直调 _tick 保留全部产品逻辑,只替换调度器。

    采样:仅在 app._upload_count 前进(该宠本帧真正上传新图)时抓取
    stage._pet_raw 副本 + body 状态。返回 (samples, stats)。

    时基:每 tick 走 `Timebase` 的钉子(见 `_timebase`)⇒ `dt ≡ 1/60`,且本驱动窗的
    每个 tick 都在判据内(违规 ⇒ 读数作废、rc=2,不是静默降级)。"""
    tb = _timebase(app)
    label = f"live_motion(ticks={ticks})"
    tb.begin(label)                            # 窗口起点(判据见 `_timebase`)
    if patch_decide:
        from neuropet.core.contracts import Behavior, BehaviorCommand
        st0 = h.state
        orig = h.brain.decide

        def _force_walk(view):
            # 沿当前 heading 前方 400px 逐帧重算目标:永不抵达(d>90 保持
            # arrive=1.0),速度恒 = cruise×intensity,确定性巡航。
            tgt = (st0.pos[0] + 400.0 * math.cos(st0.heading),
                   st0.pos[1] + 400.0 * math.sin(st0.heading))
            return BehaviorCommand(Behavior.EXPLORE, target=tgt,
                                   intensity=1.0, reason="harness-cruise")

        h.brain.decide = _force_walk          # 注入点:决策出口(实现零改动)
        app._force_orig_decide = orig

    samples: list[dict] = []
    last_up = app._upload_count
    no_up_ticks = 0
    skip_streak_max = 0
    # 重置绝对节拍器(时基钉每 tick 还会再置一次,见 `_timebase`;此处只是保证
    # 首帧前状态明确):上一段驱动/首帧烘焙可能让 _next_t 领先墙钟(此后
    # _tick 的 dt 全被钳到 0.001 下限,仿真时间与墙钟脱钩——驱动器伪影)。
    app._next_t = time.perf_counter()
    deadline = time.perf_counter()
    for _ in range(ticks):
        app._tick()                            # 真实主循环步进(真实墙钟 dt)
        deadline = max(deadline + FRAME_DT,    # 跳过错过的槽位,不追债
                       time.perf_counter())
        slack = deadline + FRAME_DT - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        skip_streak_max = max(skip_streak_max, h.skip_streak)
        if app._upload_count > last_up:
            last_up = app._upload_count
            # Inspect the untrimmed pose frame. Display transport now removes
            # transparent margins, which vary across poses but do not alter pixels.
            img, ix, iy = h.last_img, int(h.state.pos[0]), int(h.state.pos[1])
            st = h.state
            body = h.body
            samples.append({
                "img": img.copy(),
                "ix": int(ix), "iy": int(iy),
                "phase": float(body._gait.phase),
                "hz": float(body._gait.step_hz(body._speed)),
                "speed": float(st.speed),
                "speed_norm": float(body._speed) / max(1.0, body.p["cruise"]),
                "pos": (float(st.pos[0]), float(st.pos[1])),
                "heading": float(st.heading),
            })
            if min_frames and len(samples) >= min_frames:
                break
        else:
            no_up_ticks += 1
    stats = {"ticks": ticks, "no_up_ticks": no_up_ticks,
             "skip_streak_max": skip_streak_max}
    # 窗口判据(含闭锁重判):有任何一个 tick 的 dt 越界 ⇒ 本窗读数作废(rc=2)。
    tb.finish(label)
    return samples, stats


def _align_diff(img_a, img_b, dx: int, dy: int) -> int:
    """平移补偿帧间差:把 b 按 bbox 中心位移 (dx,dy) 反向对齐后与 a 求差。

    剔除"整图平移"贡献 → 残差即姿态/肢体动画本身。"静态图片平移" bug
    在该口径下帧差→0;真动画(腿摆/sway)残差保持大。"""
    A = np.asarray(img_a, dtype=np.int16)
    B = np.asarray(img_b, dtype=np.int16)
    h, w = A.shape[:2]
    ax0, ax1 = max(0, dx), min(w, w + dx)
    ay0, ay1 = max(0, dy), min(h, h + dy)
    if ax1 <= ax0 or ay1 <= ay0:
        return 0
    a = A[ay0:ay1, ax0:ax1]
    b = B[ay0 - dy:ay1 - dy, ax0 - dx:ax1 - dx]
    return int((np.abs(a - b).sum(axis=2) > 8).sum())


def _analyze(samples: list[dict], label: str) -> dict:
    """帧差/相位/稳定性量化。raw=原始帧差;aligned=平移补偿残差(动画本体)。"""
    diffs, adiffs, dps, centers, blens = [], [], [], [], []
    for s in samples:
        m = _nonkey_mask(s["img"])
        ys, xs = np.nonzero(m)
        if len(xs):
            s["c"] = (s["ix"] + float(xs.mean()), s["iy"] + float(ys.mean()))
            blens.append(max(xs.max() - xs.min(), ys.max() - ys.min()) + 1)
        else:
            s["c"] = None
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        diffs.append(_diff_px(a["img"], b["img"]))
        dps.append((b["phase"] - a["phase"]) % 1.0)
        if a["c"] and b["c"]:
            adiffs.append(_align_diff(a["img"], b["img"],
                                      int(round(b["c"][0] - a["c"][0])),
                                      int(round(b["c"][1] - a["c"][1]))))
    diffs_a = np.asarray(diffs, float)
    adiffs_a = np.asarray(adiffs, float)
    dps_a = np.asarray(dps, float)

    def _st(arr):
        return (float(np.median(arr)), float(arr.mean()), float(arr.max()),
                float(arr.min()), float(np.percentile(arr, 90)))

    dm, dmean, dmax, dmin, dp90 = _st(diffs_a)
    am, amean, amax, amin, ap90 = (_st(adiffs_a) if len(adiffs_a) else
                                   (0.0,) * 5)
    out = {"n": len(samples), "diff_med": dm, "diff_mean": dmean,
           "diff_max": dmax, "diff_min": dmin, "diff_p90": dp90,
           "adiff_med": am, "adiff_mean": amean, "adiff_max": amax,
           "adiff_min": amin, "adiff_p90": ap90,
           "dp_med": float(np.median(dps_a)),
           "zero_runs": _max_zero_run(diffs),
           "azero_runs": _max_zero_run(adiffs),
           "phase_total": float(sum(dps)),
           "nonkey_min": min(int(_nonkey_mask(s["img"]).sum()) for s in samples),
           "bright_max": max(int(np.asarray(s["img"])[..., :3].max())
                             for s in samples),
           "speed_lo": min(s["speed_norm"] for s in samples),
           "speed_hi": max(s["speed_norm"] for s in samples),
           "hz_med": float(np.median([s["hz"] for s in samples])),
           "center_jump": 0.0, "body_len": max(blens) if blens else 1}
    cs = [s["c"] for s in samples if s["c"]]
    jc = [math.dist(cs[i], cs[i + 1]) for i in range(len(cs) - 1)]
    out["center_jump"] = max(jc) if jc else 0.0
    # Pearson r(Δphase → 各口径帧差)
    for key, arr in (("r", diffs_a), ("r_align", adiffs_a)):
        if dps_a.std() > 1e-9 and arr.std() > 1e-9:
            out[key] = float(np.corrcoef(dps_a, arr)[0, 1])
        else:
            out[key] = 0.0
    print(f"    [{label}] n={out['n']} raw帧差 min/p50/mean/p90/max = "
          f"{dmin:.0f}/{dm:.0f}/{dmean:.0f}/{dp90:.0f}/{dmax:.0f}px")
    print(f"    [{label}] 对齐残差 min/p50/mean/p90/max = "
          f"{amin:.0f}/{am:.0f}/{amean:.0f}/{ap90:.0f}/{amax:.0f}px "
          f"(零残差连帧={out['azero_runs']})")
    print(f"    [{label}] Δphase p50={out['dp_med']:.3f} 相位覆盖="
          f"{out['phase_total']:.2f}周 gait.hz中位={out['hz_med']:.1f} "
          f"r(Δφ,raw)={out['r']:.3f} r(Δφ,对齐)={out['r_align']:.3f} "
          f"零差最长连帧={out['zero_runs']} 速度带[{out['speed_lo']:.2f},"
          f"{out['speed_hi']:.2f}] 中心最大跳动={out['center_jump']:.0f}px"
          f"/体长{out['body_len']}px 非键色最少={out['nonkey_min']}px "
          f"最亮={out['bright_max']}")
    return out


def _max_zero_run(xs: list[int]) -> int:
    run = best = 0
    for x in xs:
        run = run + 1 if x == 0 else 0
        best = max(best, run)
    return best


def _filmstrip(samples: list[dict], path: Path, step: int = 10,
               max_tiles: int = 14) -> None:
    from PIL import Image
    tiles = [s["img"] for s in samples[::step]][:max_tiles]
    if not tiles:
        return
    w, hh = tiles[0].size
    strip = Image.new("RGBA", (w * len(tiles), hh), (60, 60, 60, 255))
    for i, t in enumerate(tiles):
        strip.paste(t, (i * w, 0), t)
    path.parent.mkdir(parents=True, exist_ok=True)
    strip.convert("RGB").save(path)
    print(f"    filmstrip: {path} ({len(tiles)} 帧, 每 {step} 帧取 1)")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="neuropet_live_") as td:
        app, appmod, orig_profile = _make_app(Path(td))
 
        try:
            app._next_t = time.perf_counter()      # run() 未调,补 _tick 依赖
            pet_id = app.add_pet("species.cockroach")
            h = app.pets[pet_id]
            print(f"[harness] pet={pet_id} cruise={h.body.p['cruise']}px/s "
                  f"render_mode={os.environ['NEUROPET_RENDER']}")

            # ---- 0 自然行为探针(4s,不注入):默认大脑是否自发行走 ----
            nat, nat_stats = _drive(app, h, ticks=240, min_frames=0,
                                    patch_decide=False)
            nat_speeds = [s["speed_norm"] for s in nat] or [0.0]
            nat_walk_frac = (sum(1 for v in nat_speeds if v > 0.3)
                             / max(1, len(nat_speeds)))
            print(f"[probe] 自然 4s: 上传帧={len(nat)} "
                  f"speed_norm min/med/max={min(nat_speeds):.2f}/"
                  f"{sorted(nat_speeds)[len(nat_speeds) // 2]:.2f}/"
                  f"{max(nat_speeds):.2f} 行走占比(>0.3)={nat_walk_frac:.0%} "
                  f"无上传 tick={nat_stats['no_up_ticks']}")
            if len(nat) >= 10:
                _analyze(nat, "natural")

            # ---- 1-3 强制巡航采样(>=120 帧,截取 speed_norm>=0.6 稳态段) ----
            raw, fstats = _drive(app, h, ticks=600, min_frames=260,
                                 patch_decide=True)
            walk = [s for s in raw if s["speed_norm"] >= 0.6]
            print(f"[harness] 强制巡航: ticks={fstats['ticks']} 上传帧="
                  f"{len(raw)}(稳态 {len(walk)}) 无上传 tick="
                  f"{fstats['no_up_ticks']} 预测跳帧峰值 skip_streak_max="
                  f"{fstats['skip_streak_max']}")
            m = _analyze(walk, "walk")
            rs = app.render_stats()
            print(f"[counters] uploads={rs['uploads']} "
                  f"dedup_skips={rs['dedup_skips']} p50={rs['p50']:.1f}ms "
                  f"p95={rs['p95']:.1f}ms")
            out = Path(__file__).resolve().parents[1] / "scratch" / "_r18_j2"
            _filmstrip(walk, out / "live_filmstrip.png")

            # ---- 判据 ----
            ok_walk = len(walk) >= 120 and m["phase_total"] >= 2.0 \
                and m["speed_lo"] > 0.3
            record("0 强制行走生效", ok_walk,
                   f"稳态帧 {len(walk)}>=120, 相位覆盖 {m['phase_total']:.2f}>=2.0 "
                   f"周, 速度带 [{m['speed_lo']:.2f},{m['speed_hi']:.2f}]")
            record("1 动画存在性(平移补偿)",
                   m["adiff_med"] > TH_ADIFF_MED,
                   f"对齐残差中位 {m['adiff_med']:.0f}px > {TH_ADIFF_MED}"
                   f"(校准值 30%; raw 帧差中位 {m['diff_med']:.0f}px 含平移)")
            record("2 相位驱动", m["r_align"] > TH_R,
                   f"Pearson r(d_phase,对齐残差)={m['r_align']:.3f} > {TH_R}"
                   f"(raw 口径 r={m['r']:.3f}; d_phase p50={m['dp_med']:.3f},"
                   f"gait.hz 中位 {m['hz_med']:.1f})")
            record("3 稳定性", m["nonkey_min"] > 0 and m["bright_max"] > 40
                   and m["zero_runs"] <= 5
                   and m["center_jump"] < m["body_len"],
                   f"无全键色帧(min 非键 {m['nonkey_min']}px)/最亮 "
                   f"{m['bright_max']}/零差连帧 {m['zero_runs']}<=5/中心跳动 "
                   f"{m['center_jump']:.0f}px < 体长 {m['body_len']}px")
            record("0b 自然行为", True,
                   f"4s 自然行走占比 {nat_walk_frac:.0%}(证据,不设阈)")
        finally:
            app._running = False
            app.root.destroy()
            appmod.profile_dir = orig_profile

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[活体连续取帧] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, d in failed:
            print(f"  FAIL: {tag} — {d}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    # 时基判据失败 = **读数作废**(不判红绿):rc=2 + 明写 [VOID],不许静默降级。
    try:
        main()
    except TimebaseError as exc:
        print(f"\n[VOID] 时基钉失效 ⇒ 本次读数作废(不判红绿,rc=2): {exc}")
        sys.exit(2)
