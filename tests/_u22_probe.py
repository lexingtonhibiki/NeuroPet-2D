# -*- coding: utf-8 -*-
"""账本行 22 取证探针:把「机器慢」与「代码错」分开。

背景:同一棵未改动的树连跑全套件,每次红一个不同套件(chibi_render /
online_learning),两者单跑各自全绿。怀疑是绝对墙钟阈值撞上未受控的
机器负载(工作流 §5 坑表第 7 条的套件级重演)——**这是假设,须取证**。

本探针在**同一次运行、同一时刻**同时测:
  * metric:被测的墙钟量(冷桶构建 / 整帧 / 脑链路)
  * ref   :两项**定长**参照工作量(跨轮可比的是比值,不是它本身)
      - ref_py : 纯 Python 定点循环(≈ 脑链路的负载特性)
      - ref_pil: 固定图 PIL rotate+resize(≈ 桶构建的负载特性)
判读:
  * 「机器慢」 ⇒ 绝对值漂,但 **metric/ref 比值稳定**
  * 「代码错」 ⇒ **比值本身漂**

还输出被测量的**分布**(min/median/p95),因为判据是拿分布里的某个
统计量去比阈值的——换个统计量就是换口径。

用法::

    python tests/_u22_probe.py rot     8
    python tests/_u22_probe.py frame   8
    python tests/_u22_probe.py synth  10     # chibi 全合成增量(软判据)的噪声带
    python tests/_u22_probe.py online  8     # 8 轮 → 可算 min-of-3 / min-of-7
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

REF_PY_ITERS = 400_000      # 定长:跨轮可比的是**比值**
REF_PIL_REPS = 24
PIL_SIDE = 312

# 计时源:判据用哪个时钟就是「量什么」。perf_counter = 墙钟(含被别人抢走的
# 时间);process_time = 本进程的 CPU 时间(别人抢不走,但**只在我们真的在
# 跑时**才计)。模式名后缀 cpu 切到后者。
CLOCK = time.perf_counter
CLOCK_NAME = "perf_counter(墙钟)"


def granularity() -> None:
    """process_time 在本机的时间粒度(Windows 上可能被调度器量化)。"""
    vals = []
    for _ in range(6):
        t0 = time.process_time()
        s = 1
        for _ in range(2000):
            s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        vals.append((time.process_time() - t0) * 1e6)
    uniq = sorted(set(round(v, 3) for v in vals))
    print(f"[gran] process_time 6 次小循环读数(µs):{[round(v, 1) for v in vals]} "
          f"唯一值={uniq[:6]} → {'量化可疑' if len(uniq) <= 2 else '粒度够'}", flush=True)


def ref_py() -> float:
    """纯 Python 定点迭代 → ms。与被测脑链路同族(解释器开销主导)。"""
    t0 = time.perf_counter()
    s = 12345
    for _ in range(REF_PY_ITERS):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
    dt = (time.perf_counter() - t0) * 1000.0
    assert s != 0                                     # 防被优化掉
    return dt


def ref_pil() -> float:
    """固定图 rotate+resize(BICUBIC)→ ms。与被测桶构建同族(native 重采样)。"""
    from PIL import Image
    img = Image.new("RGBA", (PIL_SIDE, PIL_SIDE), (120, 90, 60, 255))
    box = (40, 40, PIL_SIDE - 40, PIL_SIDE - 40)
    t0 = time.perf_counter()
    acc = 0
    for _ in range(REF_PIL_REPS):
        r = img.rotate(7.3, resample=Image.BICUBIC, expand=False)
        r = r.resize((256, 256), resample=Image.BICUBIC, box=box)
        acc += r.getpixel((128, 128))[0]
    dt = (time.perf_counter() - t0) * 1000.0
    assert acc >= 0
    return dt


def load_probe() -> str:
    """机器负载:CPU 占用 + python 进程数(本机非独占,历史见过 5+ 他人 python)。"""
    try:
        import psutil
    except ImportError:
        return "load=?"
    cpu = psutil.cpu_percent(interval=None)
    pys = sum(1 for p in psutil.process_iter(["name"])
              if (p.info.get("name") or "").lower().startswith("python"))
    return f"cpu={cpu:4.1f}% py={pys}"


# ----------------------------------------------------------------- 被测量
def m_rot() -> list[float]:
    """同 test_bucket_build_perf_guard:60 个不同角 = 冷桶 → 60 个样本。"""
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    sid = "species.cockroach"
    torso_art.invalidate()
    torso_art.get_torso(sid, 120, 0.0, 1.0, tr)          # 预热
    xs = []
    for i in range(1, 61):
        t0 = CLOCK()
        torso_art.get_torso(sid, 120, float(i), 1.0, tr)
        xs.append((CLOCK() - t0) * 1000.0)
    return xs


def m_frame() -> list[float]:
    """同 test_budget:roach 整帧 render_pose ×40 → 40 个样本。"""
    import test_chibi_render as T
    from neuropet.render.renderer import render_pose
    from neuropet.species.cockroach import AmericanCockroach
    pose = T.build_roach_pose()
    tr_on = dict(AmericanCockroach().render_traits(), chibi=True)
    render_pose(pose, tr_on)
    xs = []
    for _ in range(40):
        t0 = CLOCK()
        img = render_pose(pose, tr_on)
        xs.append((CLOCK() - t0) * 1000.0)
    assert img.getchannel("A").getextrema()[1] > 0
    return xs


def m_synth() -> float:
    """同 test_budget 的软判据:**一轮** delta = sorted(n)[10] - sorted(o)[10]。"""
    import test_chibi_render as T
    from neuropet.render.renderer import _render_pose_full
    from neuropet.species.cockroach import AmericanCockroach
    pose = T.build_roach_pose()
    tr = AmericanCockroach().render_traits()
    tr_on = dict(tr, chibi=True)
    _render_pose_full(pose, tr)
    _render_pose_full(pose, tr_on)
    o, n = [], []
    for _ in range(20):
        t0 = CLOCK()
        _render_pose_full(pose, tr)
        o.append((CLOCK() - t0) * 1000.0)
        t0 = CLOCK()
        _render_pose_full(pose, tr_on)
        n.append((CLOCK() - t0) * 1000.0)
    return sorted(n)[10] - sorted(o)[10]


def m_online() -> float:
    """同 test_frame_budget:**一轮** = 2000 帧均值(两只脑取最差)。"""
    import test_online_learning as T
    from neuropet.brain.fly_brain import FlyConnectomeBrain
    from neuropet.brain.roach_brain import RoachBrain
    dt = T.DT
    out = 0.0
    for cls in (FlyConnectomeBrain, RoachBrain):
        stim = [T.odor(0.7), T.wind(0.4)]
        n = 2000
        w, st, view = T.make_scene("u22" + cls.brain_id[-2:])
        b = cls(st)
        t0 = CLOCK()
        for i in range(n):
            b.observe(view, stim, dt)
            b.decide(view)
            if i % 200 == 50:
                b.on_event("fed", {})
            if i % 500 == 250:
                b.on_event("grab", {})
        out = max(out, (CLOCK() - t0) / n * 1e6)
    return out


METRICS = {
    "rot":    (m_rot,    "ref_pil", "护栏 median ≤6.0ms(目标 ≤3.0)"),
    "frame":  (m_frame,  "ref_pil", "roach P50≤7.0 / P95≤9.0ms"),
    "synth":  (m_synth,  "ref_pil", "全合成增量 ≤1.0ms(软判据)"),
    "online": (m_online, "ref_py",  "min-of-3 ≤400µs"),
}


# --------- 候选新口径:换统计量(不换阈值)的对照测量 ---------
def m_rot3() -> list[float]:
    """候选口径:每个角取 3 次**冷**构建的最小值(角仍逐个冷建),再对全圈取中位。

    为什么不是「同角连测 3 次」:第 2、3 次是**缓存命中**,测的不是构建。
    故每遍之间 `invalidate()`(与判据自身一样的冷起手),角内样本仍是冷桶。
    """
    from neuropet.render import torso_art
    from neuropet.species.cockroach import AmericanCockroach
    tr = AmericanCockroach().render_traits()
    sid = "species.cockroach"
    passes: list[list[float]] = []
    for _ in range(3):
        torso_art.invalidate()
        torso_art.get_torso(sid, 120, 0.0, 1.0, tr)      # 预热:母图烘焙不计入
        row = []
        for i in range(1, 61):
            t0 = CLOCK()
            torso_art.get_torso(sid, 120, float(i), 1.0, tr)
            row.append((CLOCK() - t0) * 1000.0)
        passes.append(row)
    return [min(passes[k][i] for k in range(3)) for i in range(60)]


def m_synth2() -> tuple[float, float, float]:
    """候选口径:配对统计量。现判据用 sorted(n)[10]-sorted(o)[10](两臂各排各的,
    丢掉了配对)→ 换成 ①逐对差的中位 ②两臂各自最小之差(本征成本之差)。"""
    import test_chibi_render as T
    from neuropet.render.renderer import _render_pose_full
    from neuropet.species.cockroach import AmericanCockroach
    pose = T.build_roach_pose()
    tr = AmericanCockroach().render_traits()
    tr_on = dict(tr, chibi=True)
    _render_pose_full(pose, tr)
    _render_pose_full(pose, tr_on)
    o, n = [], []
    for _ in range(20):
        t0 = CLOCK()
        _render_pose_full(pose, tr)
        o.append((CLOCK() - t0) * 1000.0)
        t0 = CLOCK()
        _render_pose_full(pose, tr_on)
        n.append((CLOCK() - t0) * 1000.0)
    cur = sorted(n)[10] - sorted(o)[10]
    pairs = sorted(n[i] - o[i] for i in range(20))
    med_paired = pairs[len(pairs) // 2]
    return cur, med_paired, min(n) - min(o)


def _fmt(x: object) -> str:
    if isinstance(x, list):
        return _line(x)
    if isinstance(x, tuple):
        return "cur=%7.3f med配对=%7.3f min差=%7.3f" % x
    return f"{x:8.3f}"


METRICS.update({
    "rot3":   (m_rot3,   "ref_pil", "候选:每角冷建 min-of-3 → 全圈中位(阈值仍 6.0)"),
    "synth2": (m_synth2, "ref_pil", "候选:配对统计量对照(阈值仍 1.0)"),
})


def _q(xs: list[float], f: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * f))]


def _line(xs: list[float]) -> str:
    s = sorted(xs)
    return (f"min={s[0]:.3f} med={s[len(s) // 2]:.3f} max={s[-1]:.3f} "
            f"p95={_q(xs, 0.95):.3f} 极差/中位={100.0 * (s[-1] - s[0]) / max(1e-9, s[len(s) // 2]):.0f}%")


def main() -> int:
    spec, reps = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8
    if "," in spec:                      # 一条白名单命令跑多种,省得串 shell
        rc = 0
        for m in spec.split(","):
            rc |= main_one(m, reps)
        return rc
    return main_one(spec, reps)


def main_one(mode: str, reps: int) -> int:
    global CLOCK, CLOCK_NAME
    cpu_mode = mode.endswith("cpu")
    CLOCK = time.process_time if cpu_mode else time.perf_counter
    CLOCK_NAME = "process_time(本进程CPU时间)" if cpu_mode else "perf_counter(墙钟)"
    fn, refname, thr = METRICS[mode[:-3] if cpu_mode else mode]
    import psutil
    psutil.cpu_percent(interval=None)
    time.sleep(0.5)
    print(f"== probe {mode} reps={reps} 计时源={CLOCK_NAME} 判据({thr}) ==", flush=True)
    if cpu_mode:
        granularity()
    print(f"[load] 开始 {load_probe()}", flush=True)
    raws: list[float] = []          # 每轮的代表值(rot/frame 取中位,online/synth 取该轮值)
    rats: list[float] = []
    for k in range(reps):
        r0 = ref_pil() if refname == "ref_pil" else ref_py()
        got = fn()
        r1 = ref_pil() if refname == "ref_pil" else ref_py()
        ref = (r0 + r1) / 2.0
        snap = None
        if isinstance(got, list):
            snap = got
            rep = sorted(got)[len(got) // 2]        # 与判据同口径的代表值
            extra = f"n={len(got)}"
        else:
            snap = None
            rep = got[0] if isinstance(got, tuple) else got
            extra = ""
        raws.append(rep)
        rats.append(rep / ref)
        if snap is not None:
            print(f"[{k}] {mode} {extra} {_line(snap)} | ref={ref:7.2f} | 比值={rep / ref:7.4f}",
                  flush=True)
        else:
            txt = _fmt(got) if isinstance(got, tuple) else f"rep={rep:8.3f}"
            print(f"[{k}] {mode} {txt}{extra} | ref={ref:7.2f} | "
                  f"比值={rep / ref:7.4f}", flush=True)
    print(f"[abs ] 每轮代表值 {_line(raws)}", flush=True)
    print(f"[ratio] 每轮比值   {_line(rats)}  ← 比值稳=机器慢;比值漂=代码错", flush=True)
    if mode == "online":
        for k in (3, 5, 8):
            if reps >= k:
                print(f"[min-of-{k}] {sorted(raws)[0] if k > len(raws) else sorted(raws)[:k]}"
                      f" → min={min(sorted(raws)[:k]):.1f}µs", flush=True)
    print(f"[load] 结束 {load_probe()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
