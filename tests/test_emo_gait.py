"""单元 B2 判据:情绪 → 步态映射(R5;可观察、无 UI)。运行:python tests/test_emo_gait.py

口径:
- **真实路径**:临时 App + 60Hz 实时节拍驱动 `app._tick()`(与 mainloop 同一
  函数;名册/profile/会话全部打进临时目录,不碰仓库 data/);
- 情绪**只写进脑**(唯一真源 `brain.emotion()`),经 app 每帧装配点进运动层;
  判据**不写 body/gait 的任何字段**——不制造被测路径本该提供的前置条件;
- **光标输入钉到屏外**(见 `_make_app`):真实鼠标靠近宠物会经鼠标感知通道
  抬高脑内恐惧(单帧可到 1.0)⇒ 那是**夹具未控制的输入**,不是被测行为;
  不钉住则判据结果取决于跑测时鼠标在哪(B2-d⑤ 曾因此偶发变红);
- 逐帧取 `gait.step_hz(body._speed)` / `body._speed` / `state.speed` →
  步幅 = mean v / mean hz、步频 = mean hz、停顿占比 = `speed < 5%×cruise`
  帧占比(与 U0/R5 同阈值);
- **仿真时基钉死**(2026-09-23,见 `_timebase`):`_drive` 每 tick 前把 `app._next_t`
  置为当刻墙钟(`tools/u0_baseline.py::Timebase`,唯一实现)⇒ `dt ≡ 1/60`,且本驱动窗
  每个 tick 都在判据内(违规 ⇒ 读数作废、rc=2)。未钉口径实测:撞 0.05 **上**钳位
  **83.1%**、每 tick 推进 **2.78×** 仿真时间(`logs/_tb_probe_emo_old.txt`)
  ⇒ 命名「1.0s」的窗实走 ~2.8 仿真秒 —— B2 的旧读数全部站在这个坏节拍上;
- 决策出口恒定 EXPLORE(intensity 1.0)⇒ 基础臂本身 0 停顿,故"恐惧→停顿"
  的增量**只可能来自情绪通道**(自然脑口径下脑自身的休息/行走切换会主导
  停顿占比、把通道掩掉;自然脑口径的前→后数字由 `tools/u0_baseline.py r5`
  单独给出:**钉死光标口径** 3 样本(`logs/u0_r5pin{1,2,3}.json`)——roach
  +28.67/+28.67/+26.67pp、fly +27.67/+31.00/+31.00pp。⚠ 早先登记在此的
  +27.7/+26.0pp 与架构 §22.4 那组**均系未钉光标口径,已废**(基线 §4.6①)。

判据:
  R5-① 饥饿 0.10→0.90:步幅变化 ≤ −15%(两物种各自成立)
  R5-② 饥饿 0.10→0.90:步频 |Δ| ≤ 5%
  R5-③ 恐惧 0→0.85:停顿占比 Δ ≥ +10pp(两物种各自成立)
  R5-④ 带内(硬约束):逐帧 hz ∈[3,8];巡航帧 duty∈[0.42,0.50]
        (映射只许在带内调制,不越 test_acceptance_final 带)
  B2-a 恐惧 0→0.85:触角下垂角(体轴系基→梢展开角)↑
  B2-b 唤醒 0→0.9:触角扫频 ↑(**端到端**:同一 App 同一宠强制 IDLE,只换
       `brain.emotion()` 报告的 arousal —— app 装配线 → body 通道 → 梢端
       横向过零数,三层全在判据内;原 body 级直调口径绕过 app 装配点,
       被黑盒掐线法判为覆盖缺口,已按主控裁决上移)
       ⚠ 口径(r25 收口):**两臂各 15 仿真秒窗 + 每臂前归零运动瞬态**——
       5s 窗 ±1 计数 = ±14% 噪声,却要分辨 20%(实测 3 红 3 绿);
       且姿态 crossfade 残影会被 `_ant_t·hz` 的相位口径放大(详见 `check_sweep`)
  B2-c 负向对照(**同 App 同宠**只翻通道开关 `NEUROPET_EMO_GAIT=off`):
       R5-①②③ **全部不达**(判据有鉴别力,不是空转),且强制行走臂逐位回到
       单元前值(roach 300.0/8.0/37.5、fly 90.0/8.0/11.25;`logs/_b2_pre_r5.json`)
  B2-d 无锁存(修复 1):阈上→阈下多次往返(含 `clear_memory` 跳变)后,
       `stride_scale` 逐位回 1.0、停顿窗关闭(`_emo_fear/_emo_vig_t` = 0),
       再切 off 的强制行走臂逐位回到单元前值(黑盒实测锁存 0.858002 → 1.0)
  B2-e 非逃逸限定(修复 2):逃逸 sprint 期 hz **不被**饥饿通道拉起
       (≤ 同相位 off 值;黑盒实测曾 11.000→13.303)
  B2-f 装配守卫(修复 3 + r2 复验问题 2):插件脑 `emotion()` 返回脏值(None/
       非数/NaN/±inf/越界)→ 零异常(不中断帧)、帧照常推进,干净情绪回来时
       通道重新生效;**面板 6Hz 读数路径在判据内**(真实 ControlPanel 接入
       主循环,选中宠 = 脏值宠 —— 原夹具注入非选中宠,该路径根本不在判据内)
  B2-g 有界保持(r2 复验问题 1):永久脏值驱动 ≥2s → 状态**回到中性**(不许
       无界陈旧);瞬时脏值(单帧)不打断通道(保持窗吸收抖动);永久脏值期间
       切 `off` → 强制行走臂**逐位** = 单元前值,且 off 期间装配点**零次**
       读 `emotion()`(`off` 判定先于守卫:回退开关不依赖上游数据是否健康)
  B2-h 异常调用面(r2 复验问题 3):`emotion()` **本身抛异常** → 零异常逃逸
       (`_tick` 的 except 出口)、帧照常推进、状态同样回到中性
  B2-i 单一真源的**收敛**(§23):面板读数(瞬时观测,脏值期不续期保持窗)与
       body 驱动值**可短暂分歧**,但必须**有界**(≤保持窗 0.25s + 帧量化)且
       **必然收敛**——健康期逐帧一致;脏值期分歧 ≤T 后收敛、其后零分歧;
       异常输入结束/情绪恢复后 ≤1 帧一致(且驱动值真回到注入值,非空转)
"""
from __future__ import annotations

import ast
import math
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 时基钉的**唯一实现** = `tools/u0_baseline.py::Timebase`(探针与 `tests/test_live_motion.py`
# 共用同一份;三处同源,改一处要改三处 —— 不许留悄悄分叉的实现,见 r25-workflow §5)。
from tools.u0_baseline import Timebase, TimebaseError   # noqa: E402

def _cursorpos_accesses(src: str) -> list[int]:
    """源码里 `*.GetCursorPos` 的**属性访问**行号。

    用 AST 不用 `str.count`:文本扫描会被注释/字符串里提一句「由 GetCursorPos 提供」
    误报成红 —— 判据因**非缺陷**变红同样会逼人删判据(纸板判据的反面)。
    计数器本身在 `_make_app` 里有自证断言。
    """
    return [
        n.lineno
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Attribute) and n.attr == "GetCursorPos"
    ]


# 光标「独门」:模块级扫一次(文件 I/O 不进夹具),`_make_app` 每次断言其**数量**。
_PKG_ROOT = Path(__file__).resolve().parents[1] / "neuropet"
_CURSOR_DOORS: list[str] = [
    f"{p.relative_to(_PKG_ROOT.parent).as_posix()}:{ln}"
    for p in sorted(_PKG_ROOT.rglob("*.py"))
    for ln in _cursorpos_accesses(p.read_text(encoding="utf-8"))
]

from neuropet.core.contracts import EmotionState as _EMO   # noqa: E402

FRAME_DT = 1.0 / 60.0
# B2-b 测窗(**仿真秒**,时基钉住后 = 名义秒)。5→15 的理由见 `check_sweep`:
# 静息扫频 0.7Hz 在 5s 窗里只有 7 个过零点,「均值相减后数符号变化」这一读数
# 在窗内含**非整数个周期**时对起始相位有 ±1 个计数的量化(纯数学实测 720 档相位:
# 7(97%)/8(3%));±1/7 = ±14% 的噪声底对上 1.2× 的 20% 门限只剩 4% 余量。
# 15s 窗 ⇒ 计数 ≈21(±1/21 = ±5%),与**历史校准**同量级(旧坏时基实走 15 仿真秒)。
SWEEP_SEC = 15.0
# B2-i 收敛上限(契约值,架构 §22.1/§23:0.25s = 15 帧 @60Hz)。**故意写字面量、
# 不读被测常量 `app.EMO_HOLD_MAX_S`**:判据与被测共用常量会让「把窗口改大」
# 一起通过 —— 负向对照实测(该常量 →1e9)证实:读常量则"有界"判据恒真,
# 只有末态conjunct 会红(理由错),写字面量则"有界"判据本身变红(理由对)。
HOLD_MAX_S = 0.25
SPECIES = {"roach": "species.cockroach", "fly": "species.fruitfly"}
# 单元 B2 前值(U0 r5 强制行走臂;logs/_b2_pre_r5.json):(speed, hz, stride)
PRE_WALK = {"roach": (300.0, 8.0, 37.5), "fly": (90.0, 8.0, 11.25)}
_SPEED_TOL = 1e-6          # 前值回归容差(前值本身逐位:300.0/90.0/8.0)
# 相位表:(tag, hunger, fear, 通道关闭)
PHASES = [("h_lo", 0.10, 0.0, False), ("h_hi", 0.90, 0.0, False),
          ("h_off", 0.90, 0.0, True), ("f_hi", 0.30, 0.85, False),
          ("f_off", 0.30, 0.85, True)]

_fails: list[str] = []
_n = 0
_arms: dict = {}           # (kind, tag) → 测量结果
_latch: dict = {}          # B2-d 锁存序列结果
_sweep: dict = {}          # B2-b 端到端扫频结果
_guard: dict = {}          # B2-f 装配守卫结果
_hold: dict = {}           # B2-g 有界保持 / B2-h 异常调用面结果
_conv: dict = {}           # B2-i 面板读数 ↔ body 驱动值收敛结果


def record(tag: str, ok: bool, detail: str) -> None:
    global _n
    _n += 1
    if not ok:
        _fails.append(tag)
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ---------------------------------------------------------------- 脚手架
def _make_app(tmp: Path):
    """临时目录隔离(名册/profile/会话);不调用 run(),不落盘仓库 data/。

    **光标输入钉到屏外**(B2-d⑤ 口径根因,`_cursor_getter` 是 app 的光标输入缝):
    真实鼠标扫过/停在宠物旁时,`perception/mouse.py` 的 CONTACT/VIBRATION/
    SHADOW/WIND 通道会写进脑内恐惧(`roach_brain.observe`:
    `_fear = max(衰减, clamp(danger))`,实测**单帧跳到 1.0**)⇒ 恐惧停顿窗被
    **本夹具未控制的输入**打开,判据结果取决于跑测时鼠标在哪 = 判据隐式依赖
    抽样环境(工作流 §5 坑表同族)。屏外光标的 `d < *_R` 门全关(实测 stimuli
    空集),判据的输入只剩夹具注入的情绪 ⇒ 断言仍是**精确** `== 0.0`,
    不是数值容差。"""
    import neuropet.core.app as appmod
    appmod._SESSION_FILE = tmp / "session.json"

    def _prof(pid: str) -> Path:
        d = tmp / "profiles" / pid
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _prof
    app = appmod.App()
    app._pets_path = tmp / "pets.json"
    # 存在性断言(**先于赋值**):上游若把这道缝改名(或 app.__init__ 不再建它),
    # 赋值会变成「给一个死属性赋值」而**静默失效** —— 判据便悄悄退回
    # 「取决于跑测时鼠标在哪」。负向对照:把 `core/app.py` 的
    # `self._cursor_getter = ...` 改名为 `_cursor_source` → 本条必红。
    assert "_cursor_getter" in vars(app), (
        "光标输入缝契约变了:App 未再建 _cursor_getter;夹具钉死已静默失效"
        "(B2-d⑤ 会退化为环境依赖,见 §23.1)")
    app._cursor_getter = lambda: (-4000.0, -4000.0)
    # 独门判据:`_cursor_getter` 只是**间接层**,若别处再直接 `GetCursorPos`,
    # 钉死会「看着还在、实际失效」——读数又退回「取决于鼠标在哪」。
    # 负向对照:在 `neuropet/` 任意处补一行 `user32.GetCursorPos(...)` → 本条必红。
    # (原先此处写的是 `assert app._cursor_getter() == (-4000.0,-4000.0)`:那是
    #  **同义反复**——刚赋的 lambda 必然返回其返回值,永远不可能红 = 纸板判据,
    #  主控复审时删去并换成这一条。)
    # 计数器自证(先证仪器再证被测):注释/字符串里的 `GetCursorPos` **不算** ——
    # 否则判据会被**非缺陷**触红,同样会逼人删判据。
    assert _cursorpos_accesses(
        "x = u.GetCursorPos()  # GetCursorPos 注释\ns = 'GetCursorPos'\n"
    ) == [1], "独门计数器把注释/字符串也算进去了 ⇒ 判据会因非缺陷变红"
    assert len(_CURSOR_DOORS) == 1, (
        f"光标读取不再是独门:neuropet/ 下 `*.GetCursorPos` 属性访问 {len(_CURSOR_DOORS)} 处"
        f" {_CURSOR_DOORS}(应为 1,只在 `App._make_cursor_getter`);"
        "夹具钉死 `_cursor_getter` 可能已形同虚设")
    return app


def _force_walk(h, intensity: float = 1.0):
    """注入点 = 决策出口(实现零改动):恒定 EXPLORE,intensity 定速。"""
    from neuropet.core.contracts import Behavior, BehaviorCommand
    st0 = h.state

    def _walk(_view):
        tgt = (st0.pos[0] + 400.0 * math.cos(st0.heading),
               st0.pos[1] + 400.0 * math.sin(st0.heading))
        return BehaviorCommand(Behavior.EXPLORE, target=tgt, intensity=intensity,
                               reason="emo-gait-harness")
    h.brain.decide = _walk


def _force_idle(h):
    """注入点 = 决策出口:恒定 IDLE(扫频臂要静止态,排除腿动作污染)。"""
    from neuropet.core.contracts import Behavior, BehaviorCommand

    def _idle(_view):
        return BehaviorCommand(Behavior.IDLE, priority=0, reason="emo-gait-sweep")
    h.brain.decide = _idle


def _stop_obs(app, h, sec: float, refill=None) -> float:
    """驱动 sec 秒,返回"停顿帧占比"(speed < 5%×cruise,与 R5-③ 同阈值)。

    refill 非空时逐帧回填情绪(与 `_phase` 同口径:脑内动力学不吞掉注入值)。"""
    cruise = max(1.0, float(h.body.p.get("cruise", 1.0)))
    n = [0, 0]

    def hook():
        if refill is not None:
            refill()
        n[1] += 1
        if h.state.speed < 0.05 * cruise:
            n[0] += 1
    _drive(app, sec, hook)
    return n[0] / max(1, n[1])


def _set_emo(b, kind: str, hunger: float, fear: float) -> None:
    """情绪唯一写入口 = 脑(U0 r5 同口径);body/gait 一律不碰。"""
    if kind == "roach":
        b._hunger = hunger
        b._fear = fear
    else:                                   # fly:emo[E_FEAR]/emo[E_HUNGER]
        b.emo[0] = fear
        b.emo[1] = hunger


def _teardown(app) -> None:
    try:
        app._running = False
        app.stage.destroy()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass


# ---- 时基钉(判据;`NEUROPET_TB_NEGCTRL=1` = 负向对照:按钉住口径断言但**不装钉子**) ----
_TB_NEGCTRL = os.environ.get("NEUROPET_TB_NEGCTRL") == "1"


def _timebase(app) -> Timebase:
    """本 app 的时基钉(幂等;实现 = `tools/u0_baseline.py::Timebase`,唯一一份)。

    钉法 = 每 tick 前把 `app._next_t` 置为当刻墙钟(语义「上一帧准时发生」)⇒ `dt ≡ 1/60`。
    为什么必须钉:本夹具按 `FRAME_DT` 自配速(墙钟间隔 p50 ≈ 21.9ms),而
    `App._tick`(`neuropet/core/app.py:944`)的 dt 上钳位 0.05 ⇒ `_next_t` **持续落后**
    ⇒ 每 tick 推进 **2.78×** 仿真时间(未钉口径实测:撞钳位 **83.1%**,
    `logs/_tb_probe_emo_old.txt`)——本文件的判据(停顿占比、扫频过零率、位移)
    此前全部按这个被放大的仿真时间量。

    判据(违规 ⇒ 本次读数作废,不判红绿):逐 tick `dt∈[1/60, 20ms]` + 窗口
    `dt p99 ≤ 1/60×1.02`。`App._tick` 把 `_step_frame` 包在
    `except Exception: log_exc`(`app.py:948`)里 ⇒ **第一次 raise 会被吞掉**,
    所以 `finish()` 用**闭锁重判**(违规记在判据自己的状态里)兜底,不靠异常传播。
    """
    tb = getattr(app, "_u0_timebase", None)
    if tb is None:
        tb = Timebase(app, pin=not _TB_NEGCTRL, negctrl=_TB_NEGCTRL)
        app._u0_timebase = tb
    return tb


def _drive(app, seconds: float, hook=None) -> int:
    """U0 同款 60Hz 实时节拍(直接调 app._tick,不走 mainloop;**时基钉死**)。"""
    tb = _timebase(app)
    label = f"emo_gait({seconds}s)"
    tb.begin(label)                        # 窗口起点(判据见 `_timebase`)
    app._next_t = time.perf_counter()
    deadline = app._next_t
    n = int(seconds / FRAME_DT)
    for _ in range(n):
        app._tick()
        deadline = max(deadline + FRAME_DT, time.perf_counter())
        slack = deadline + FRAME_DT - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        if hook is not None:
            hook()
    tb.finish(label)                       # 违规 ⇒ TimebaseError(闭锁重判:被吞也算)
    return n


def _ant_droop(body) -> float:
    """触角下垂角(rad):体轴系下 基→梢 方向与体轴 +x 的夹角(俯视投影)。

    取 +y 侧触角(antennae[1],基角为正);只读观测(`_antennae_pose` 纯读状态、
    身份映射 = 不做世界旋转),无副作用。"""
    pts = body._antennae_pose(lambda x, y: (x, y))[1]
    base, tip = pts[0], pts[-1]
    return math.atan2(tip[1] - base[1], tip[0] - base[0])


# ---------------------------------------------------------------- 测量
def _phase(app, pets: list[tuple], *, warm: float, sec: float, off: bool,
           dirty: bool = False) -> dict:
    """一段:注入 (kind, pid, hunger, fear) 后测 warm+测量窗;off=关通道。

    同一 App 内逐段只翻情绪/开关 → 负向对照与正向臂共享同一宠物、同一机器
    状态(比"另起一进程"更强的对照),且省去重复启动。dirty=True = 情绪源
    换成**永久脏值**(`emotion()` 恒 None):用于"脏值期间切 off"的对照。"""
    old = os.environ.get("NEUROPET_EMO_GAIT")
    restores: list = []
    os.environ["NEUROPET_EMO_GAIT"] = "off" if off else "on"
    acc = {pid: {"hz": [], "spd": [], "duty": [], "droop": [], "n_stop": 0,
                 "ars": []} for _, pid, _, _ in pets}

    def set_emo():
        for kind, pid, hunger, fear in pets:
            _set_emo(app.pets[pid].brain, kind, hunger, fear)

    try:
        set_emo()
        if dirty:
            for _kind, pid, _h, _f in pets:
                restores.append(_override_emotion(app.pets[pid].brain,
                                                  _dirty_forever))
        _drive(app, warm)
        for a in acc.values():              # 暖机不计入
            for k in ("hz", "spd", "duty", "droop", "ars"):
                a[k].clear()
            a["n_stop"] = 0

        def hook():
            set_emo()                       # 脑内动力学不吞掉注入值(每帧回填)
            for kind, pid, _, _ in pets:
                h = app.pets[pid]
                g = getattr(h.body, "_gait", None)
                if g is None:
                    continue
                a = acc[pid]
                v = h.body._speed
                a["hz"].append(float(g.step_hz(v)))
                a["duty"].append(float(g.duty(v)))
                a["spd"].append(float(h.state.speed))
                a["droop"].append(_ant_droop(h.body))
                # 诊断量(无断言)用 getattr 兜脏值源(None → 0.0):判据本体
                # (speed/hz/stride/stop)一律不依赖情绪源是否健康。
                a["ars"].append(float(getattr(h.brain.emotion(), "arousal", 0.0)))
                if h.state.speed < 0.05 * max(1.0, h.body.p.get("cruise", 1.0)):
                    a["n_stop"] += 1

        _drive(app, sec, hook)
    finally:
        for _r in restores:
            _r()
        if old is None:
            os.environ.pop("NEUROPET_EMO_GAIT", None)
        else:
            os.environ["NEUROPET_EMO_GAIT"] = old
    out = {}
    for kind, pid, _, _ in pets:
        a = acc[pid]
        h = app.pets[pid]
        cruise = float(h.body.p.get("cruise", 1.0))
        n = max(1, len(a["hz"]))
        hz_m, spd_m = sum(a["hz"]) / n, sum(a["spd"]) / n
        walk_duty = [d for d, s in zip(a["duty"], a["spd"]) if s >= 0.5 * cruise]
        out[kind] = {
            "hz": hz_m, "speed": spd_m,
            "stride": (spd_m / hz_m) if hz_m else float("nan"),
            "stop": a["n_stop"] / n,
            "hz_min": min(a["hz"]) if a["hz"] else 0.0,
            "hz_max": max(a["hz"]) if a["hz"] else 0.0,
            "duty_walk_med": (statistics.median(walk_duty) if walk_duty
                              else float("nan")),
            "droop_med": (statistics.median(a["droop"]) if a["droop"]
                          else float("nan")),
            "arousal": (min(a["ars"]), max(a["ars"])) if a["ars"] else (0.0, 0.0),
            "n": n, "off": off}
    return out


def _latch_seq(app, pid: str) -> None:
    """B2-d 无锁存判据夹具(修复 1;真实 app 装配线,同一新宠):

    情绪序列 = 阈上(0.75,步长被削)→ 0.90 → **跌回阈下 0.10** → 0.75 → 再跌
    0.10(跨阈上下多次)→ fear 0.95(停顿窗开)→ `brain.clear_memory()` 跳变
    (fear 真源归零)→ 通道 off 强制行走臂(与前值逐位比)。

    每一步直接读步态状态:`set_emotion_gait` 阈下早退不写状态时,`stride_scale`
    会锁存上次斜坡值(黑盒实测 0.858002)→ 本判据即该场景的固化。"""
    from neuropet.body.base import EMO_HUNGER_LO, EMO_HUNGER_MAX, emo_ramp
    h = _reset_pet(app, pid)
    _force_walk(h)
    out = {"expect_hi": 1.0 - EMO_HUNGER_MAX * emo_ramp(0.75, EMO_HUNGER_LO)}

    def hold(hunger: float, fear: float, sec: float) -> None:
        _drive(app, sec, lambda: _set_emo(h.brain, "roach", hunger, fear))

    hold(0.75, 0.0, 0.6)
    out["hi"] = float(h.body._gait.stride_scale)          # 阈上:应 <1
    hold(0.90, 0.0, 0.4)
    hold(0.10, 0.0, 0.6)
    out["lo"] = float(h.body._gait.stride_scale)          # 跌回阈下:必须逐位 1.0
    hold(0.75, 0.0, 0.4)
    out["hi2"] = float(h.body._gait.stride_scale)         # 再上阈:重新被削
    hold(0.10, 0.0, 0.4)
    out["lo2"] = float(h.body._gait.stride_scale)         # 再下阈:必须逐位 1.0
    out["stop_fear"] = _stop_obs(app, h, 1.0,
                                 lambda: _set_emo(h.brain, "roach", 0.30, 0.95))
    out["fear_state"] = (float(h.body._emo_fear), float(h.body._emo_vig_t))
    h.brain.clear_memory()                                # 真路径:脑子"忘掉一切"
    out["fear_after_clear"] = float(h.brain.emotion().fear)
    out["stop_after_clear"] = _stop_obs(app, h, 1.0)       # 不回填:让跳变生效
    out["state_after_clear"] = (float(h.body._gait.stride_scale),
                                float(h.body._emo_speed),
                                float(h.body._emo_fear),
                                float(h.body._emo_vig_t))
    out["off_arm"] = _phase(app, [("roach", pid, 0.10, 0.0)],
                            warm=0.3, sec=0.8, off=True)["roach"]
    _latch.update(out)


_DIRTY_EMO = [
    ("None", lambda: None),
    ("str", lambda: _EMO(fear="0.5", hunger=0.3, arousal=0.2)),
    ("NaN", lambda: _EMO(fear=float("nan"), hunger=0.3, arousal=0.2)),
    ("+inf", lambda: _EMO(fear=0.2, hunger=float("inf"), arousal=0.2)),
    ("越界", lambda: _EMO(fear=0.2, hunger=0.3, arousal=7.0)),
    ("bool", lambda: _EMO(fear=True, hunger=0.3, arousal=0.2)),
]


def _dirty_forever():
    """永久脏值源:emotion() 恒返回 None(插件脑契约失败最常见的形态)。"""
    return None


def _override_emotion(brain, src):
    """替换情绪源(实例属性遮蔽类方法);返回还原闭包(src=None 亦还原)。

    逐字保留"替换前是否已有实例级 emotion"——夹具串行复用时不会把前一段的
    假源当类方法留下。"""
    _MISSING = object()
    prev = brain.__dict__.get("emotion", _MISSING)
    if src is None:
        brain.__dict__.pop("emotion", None)
    else:
        brain.emotion = src

    def restore() -> None:
        if prev is _MISSING:
            brain.__dict__.pop("emotion", None)
        else:
            brain.emotion = prev
    return restore


def _guard_seq(app, pid: str) -> None:
    """B2-f 装配守卫判据夹具(修复 3 + r2 复验问题 2/3):脏情绪/抛异常 →
    零异常、帧照常推进。

    异常检测 = 掐 `neuropet.diag.log_exc`(`_tick` 的 except 内 import 该名,
    每帧异常都会经过它);帧推进检测 = 强制行走宠在脏情绪期间的位移。

    **面板路径**(r2 复验问题 2):接入真实 `ControlPanel`(主循环每帧
    `panel.tick`),并把**选中宠**设为脏值宠 —— 原夹具把脏值注入非选中宠,
    面板 6Hz 读数(`pet_status → brain.emotion()`)根本不在判据内(夹具红利)。
    计数包裹 `app.pet_status` 证明该路径确实被驱动。"""
    import neuropet.diag as diag
    from neuropet.ui.panel import ControlPanel
    h = _reset_pet(app, pid)
    _force_walk(h)
    panel = ControlPanel(app)
    app._panel = panel                 # 真实主循环接线(_step_frame 每帧 tick)
    panel.refresh_pets()
    panel.tree.selection_set((pid,))   # 选中宠 = 脏值宠(消除夹具红利)
    _guard["panel_sel"] = panel.selected() == pid
    calls = [0]
    orig_status = app.pet_status

    def _counted_status(pet_id):
        calls[0] += 1
        return orig_status(pet_id)
    app.pet_status = _counted_status
    errs: list = []
    orig = diag.log_exc
    diag.log_exc = lambda where: (errs.append(where), orig(where))
    try:
        for name, src in _DIRTY_EMO:
            _reset_pet(app, pid)       # 每段重给跑道:撞墙/枢转不混进位移判据
            _force_walk(app.pets[pid])
            h.brain.emotion = src
            p0 = tuple(h.state.pos)
            _drive(app, 0.30)          # > 6Hz 周期 ⇒ 面板读数至少刷一次
            _guard[f"dist_{name}"] = math.dist(tuple(h.state.pos), p0)
        _guard["status_calls"] = calls[0]
        # 抛异常源(r2 复验问题 3):守卫必须覆盖"emotion() 本身抛"
        def _boom():
            raise RuntimeError("emo-gait-harness:emotion() 抛异常")
        h.brain.emotion = _boom
        p0 = tuple(h.state.pos)
        _drive(app, 0.5)               # > 保持窗 ⇒ 状态应回中性
        _guard["exc_dist"] = math.dist(tuple(h.state.pos), p0)
        _guard["exc_state"] = (float(h.body._emo_fear),
                               float(h.body._gait.stride_scale))
        _guard["status_calls_exc"] = calls[0]
        h.brain.emotion = lambda: _EMO(fear=0.0, hunger=0.9, arousal=0.0)
        _drive(app, 0.4)
        _guard["re_engage"] = (float(h.body._emo_speed),
                               float(h.body._gait.stride_scale))
    finally:
        diag.log_exc = orig
        app.pet_status = orig_status
        h.brain.__dict__.pop("emotion", None)   # 撤掉假源:归还类方法(下一段夹具用真脑)
    _guard["errs"] = list(errs)


def _hold_seq(app, pid: str) -> None:
    """B2-g 有界保持与脏值期回退夹具(r2 复验问题 1)。

    序列(同一新宠,真实 app 装配线):
      ① 健康 fear 0.95(0.6s)→ 通道真开着(`_emo_fear`>0.5、行走被停顿窗削)
      ② 瞬时脏值(**单帧** None,其后回到健康 fear 0.95)→ 逐帧采 `_emo_fear`
         的最小值:保持窗吸收抖动 ⇒ 从不掉到 0(无保持 = 该帧直接中性 = 抖动)
      ③ 永 久 脏值(恒 None)驱动 2.2s(≥2s 裁决口径)⇒ 状态**回到中性**
         (修复前 = 无界陈旧:停在 `_emo_fear` 斜坡值、停顿窗无限期按 ~55% 占空开)
      ④ 脏值仍在 + 切 off ⇒ 强制行走臂**逐位** = 单元前值
         (修复前 off 根本没被调用:守卫先跳过装配)
      ⑤ off 判定先于守卫:off 期间装配点**不读** `emotion()`(计数假源直测)
         —— 回退开关的生效不许取决于上游数据是否健康"""
    h = _reset_pet(app, pid)
    _force_walk(h)
    restores: list = []
    refill_hi = lambda: _set_emo(h.brain, "roach", 0.30, 0.95)
    try:
        _drive(app, 0.6, refill_hi)                # ① 通道打开
        _hold["fear_open"] = (float(h.body._emo_fear),
                              float(h.body._gait.stride_scale))
        # ② 瞬时脏值:第 1 次调用返回 None,其后健康(通道应**不被打断**)
        n_calls = [0]

        def _dirty_once():
            n_calls[0] += 1
            if n_calls[0] == 1:
                return None
            return _EMO(fear=0.95, hunger=0.30, arousal=0.0)
        restores.append(_override_emotion(h.brain, _dirty_once))
        mins: list[float] = []
        _drive(app, 0.5, lambda: mins.append(float(h.body._emo_fear)))
        _hold["hold_min"] = min(mins) if mins else 0.0
        _drop, restores[:] = restores.pop(), []    # 归还真脑
        _drop()
        _drive(app, 0.3, refill_hi)                # 再开通道(③ 的陈旧基值)
        _hold["fear_before_dirty"] = float(h.body._emo_fear)
        restores.append(_override_emotion(h.brain, _dirty_forever))
        _drive(app, 2.2)                           # ③ 永久脏值 ≥2s
        _hold["after_dirty"] = (float(h.body._emo_fear),
                                float(h.body._emo_speed),
                                float(h.body._gait.stride_scale),
                                float(h.body._emo_vig_t))
        _hold["stop_dirty"] = _stop_obs(app, h, 1.0)
        # ⑤ off 优先级:先回到健康(通道开),再进脏值**保持窗内**切 off
        _drop, restores[:] = restores.pop(), []
        _drop()
        _drive(app, 0.4, refill_hi)
        # ⑤ off 判定先于守卫(裁决:`off` 时直接中性化、**不依赖情绪数据**):
        # 用一个会计数的情绪源直接测 —— off 期间装配点**一次都不该读**
        # `emotion()`(计数只会来自装配点:面板只读选中宠,这里是非选中宠)。
        # 去掉"off 先于守卫"该计数 > 0(且脏源会走保持分支)。
        h2 = app.pets["fly1"]
        n_src = [0]

        def _counting_src():
            n_src[0] += 1
            return _EMO(fear=0.95, hunger=0.30, arousal=0.0)
        _drop = _override_emotion(h2.brain, _counting_src)
        old_env = os.environ.get("NEUROPET_EMO_GAIT")
        os.environ["NEUROPET_EMO_GAIT"] = "off"
        try:
            _drive(app, 6 * FRAME_DT)              # 6 帧:off 稳态
        finally:
            if old_env is None:
                os.environ.pop("NEUROPET_EMO_GAIT", None)
            else:
                os.environ["NEUROPET_EMO_GAIT"] = old_env
            _drop()
        _hold["off_src_calls"] = n_src[0]
        _hold["off_src_state"] = (float(h2.body._emo_fear),
                                  float(h2.body._gait.stride_scale))
        # ④ 脏值 + off 的强制行走臂(逐位 = 单元前值)
        _hold["off_arm"] = _phase(app, [("roach", pid, 0.10, 0.0)],
                                  warm=0.3, sec=0.8, off=True, dirty=True)["roach"]
    finally:
        for _r in restores:
            _r()


def _conv_seq(app, pid: str) -> None:
    """B2-i 单一真源的**收敛**判据(§23 主控裁决):面板读数 vs body 驱动值。

    读数口径(实现方登记,主控已采纳):面板读数是**瞬时观测**,脏值时不沿用
    装配线的保持窗(否则 6Hz 读数每帧续期会把「有界」变回「无界」)⇒ 保持窗内
    两者**可短暂分歧**;但分歧必须**有界且必然收敛**。

    观测量:
      - **面板读数** = `app.pet_status(pid)["emotion"]`(面板 6Hz 通路调用的
        **同一入口**);本夹具逐帧采样(比 6Hz 更密),同时计数面板自己的调用;
      - **body 驱动值** = 该帧装配点实际交给 `set_emotion_gait` 的三元组
        (实例级**包裹观测**,不注入、不改值;app 用 `getattr(body, ...)` 取实例
        属性,包裹即生效);
      - 一致判据 = `emo_dict` 的 round(3) 对 `round(drive, 3)` —— **同一量化**,
        不是容差;方向只取 body 真正消费的三通道(fear/hunger/arousal)。

    段:① 健康 fear 0.95(前置:两者本来就同源,逐帧一致)→ ② 永久脏值 2.0s
    (分歧有界 + ≤T+2 帧收敛 + 其后零分歧)→ ③ 异常源 → 干净源回来
    (输入结束即收敛,且驱动值真的回到 0.95 = 非空转)。"""
    from neuropet.core.app import EMO_HOLD_MAX_S
    h = _reset_pet(app, pid)
    _force_walk(h)
    drives: list = []                     # 该帧 body 实际收到的三元组
    _MISSING = object()
    prev = h.body.__dict__.get("set_emotion_gait", _MISSING)
    orig = h.body.set_emotion_gait
    h.body.set_emotion_gait = lambda f, g, a: (drives.append((f, g, a)),
                                               orig(f, g, a))[1]
    reads: list = []                      # (t, em_dict, drive) 逐帧
    refill_hi = lambda: _set_emo(h.brain, "roach", 0.30, 0.95)   # noqa: E731
    restores: list = []
    calls = [0, 0]                        # [本夹具读数, pet_status 总调用]
    orig_status = app.pet_status

    def _counted_status(pet_id):
        calls[1] += 1
        return orig_status(pet_id)
    app.pet_status = _counted_status

    def hook():
        calls[0] += 1
        reads.append((time.perf_counter(), app.pet_status(pid)["emotion"],
                      drives[-1] if drives else None))

    def hook_hi():
        hook()                       # 先采样(与本帧装配值同源),再回填下一帧
        refill_hi()

    def disagree(em, dr) -> float:
        """面板 − 驱动值的最大绝对差(round(3) 同量化);无驱动值记 0。"""
        if dr is None:
            return 0.0
        return max(abs(em.get(k, 0.0) - round(v, 3)) for k, v in
                   zip(("fear", "hunger", "arousal"), dr))
    try:
        _drive(app, 0.4, refill_hi)                   # ① 健康(不入读)
        reads.clear()
        _drive(app, 0.4, hook_hi)                     # ① 健康窗:逐帧一致
        _conv["a_frames"] = len(reads)
        _conv["a_bad"] = sum(1 for _t, em, dr in reads if disagree(em, dr) > 0.0)
        _conv["a_drive"] = reads[-1][2] if reads else None
        t_last_ok = reads[-1][0] if reads else time.perf_counter()
        restores.append(_override_emotion(h.brain, _dirty_forever))
        reads.clear()
        _drive(app, 2.0, hook)                        # ② 永久脏值 ≥2s
        bad = [i for i, (_t, em, dr) in enumerate(reads) if disagree(em, dr) > 0.0]
        _conv["b_frames"] = len(reads)
        _conv["b_div"] = len(bad)
        _conv["b_peak"] = max((disagree(em, dr) for _t, em, dr in reads),
                              default=0.0)
        # 分歧时长 = 最后一个分歧帧 − 最后一次健康读数(保持窗锚点)
        _conv["b_conv_s"] = (reads[bad[-1]][0] - t_last_ok) if bad else 0.0
        # 尾段 = 最后一次分歧之后的帧(**长度本身入判据**:无界陈旧时该长度
        # 为 0 → "零分歧"会空洞地成立,必须同时要求尾段足够长)
        _conv["b_tail"] = (len(reads) - 1 - bad[-1]) if bad else len(reads)
        _conv["b_tail_div"] = sum(
            1 for _t, em, dr in reads[(bad[-1] + 1) if bad else 0:]
            if disagree(em, dr) > 0.0)
        _conv["b_tail_s"] = (reads[-1][0] - reads[bad[-1]][0]) if bad else 0.0
        _conv["b_drive_end"] = reads[-1][2] if reads else None
        _conv["b_panel_end"] = reads[-1][1] if reads else None
        # ③ 异常源 → 干净源回来(输入结束即收敛)
        _drop, restores[:] = restores.pop(), []
        _drop()

        def _boom():
            raise RuntimeError("emo-gait-harness:收敛判据异常源")
        restores.append(_override_emotion(h.brain, _boom))
        _drive(app, 0.4)                              # 异常段(不定判据)
        _drop, restores[:] = restores.pop(), []
        _drop()
        restores.append(_override_emotion(
            h.brain, lambda: _EMO(fear=0.95, hunger=0.30, arousal=0.0)))
        reads.clear()
        _drive(app, 0.4, hook)                        # ③ 恢复窗
        _conv["c_frames"] = len(reads)
        _conv["c_bad"] = sum(1 for _t, em, dr in reads if disagree(em, dr) > 0.0)
        _conv["c_drive"] = reads[-1][2] if reads else None
        _conv["c_panel"] = reads[-1][1] if reads else None
        _conv["hold_impl"] = EMO_HOLD_MAX_S      # 仅供报告对照(不入判据)
        _conv["panel_live"] = app._panel is not None
        _conv["panel_calls"] = calls[1] - calls[0]     # 面板 6Hz 通路自己读了几次
    finally:
        for _r in restores:
            _r()
        app.pet_status = orig_status
        if prev is _MISSING:              # 归还类方法(不留实例级遮蔽)
            h.body.__dict__.pop("set_emotion_gait", None)
        else:
            h.body.set_emotion_gait = prev


def _sweep_seq(app, pid: str) -> None:
    """B2-b 端到端扫频夹具:只替换情绪**源**,装配线全在测。

    同一 App 同一宠、强制 IDLE(静止态)→ 只改 `brain.emotion()` 报告的
    arousal(0.0 / 0.9),测梢端横向过零数 + body 侧通道值。

    两处口径本单元钉死(实测依据见 `check_sweep` 的「原判据→新判据」):
    ① **每臂前把运动瞬态归零**(`_pin_sweep_state`)——上一段夹具的「行走残影」
       (姿态 crossfade / 警戒计时 / 逃逸序列)会经 `_ant_t·Δhz` 的放大率
       摆动相位(实测:钉前 c_lo = 5/6/9,钉后 = 7/7/7,而 `hz_pose` 读数相同);
    ② **窗长 5 → `SWEEP_SEC`=15 仿真秒**(相位量化 ±1 计数的相对噪声 1/7 → 1/21)。"""
    h = _reset_pet(app, pid)
    _force_idle(h)
    _drive(app, 0.5)                                   # 静置(不入测)
    for tag, aro in (("lo", 0.0), ("hi", 0.9)):
        _pin_sweep_state(h)                            # 两臂起点一致(见上 ①)
        h.brain.emotion = (lambda a: lambda: _EMO(fear=0.0, hunger=0.3, arousal=a))(aro)
        ys: list[float] = []
        hz_seen: list[float] = []

        def hook():
            b = h.body
            ys.append(b._antennae_pose(lambda x, y: (x, y))[0][-1][1])
            hz_seen.append(b._poses.get("antenna_hz", 0.0))

        n = _drive(app, SWEEP_SEC, hook)
        m = sum(ys) / len(ys)
        s = [y - m for y in ys]
        _sweep[tag] = sum(1 for a, b in zip(s, s[1:]) if (a > 0.0) != (b > 0.0))
        _sweep[f"{tag}_body"] = float(h.body._emo_arousal)
        _sweep[f"{tag}_hz"] = (min(hz_seen), max(hz_seen))
        _sweep[f"{tag}_sim"] = _timebase(app).windows[-1]["sim_sec"]
        _sweep[f"{tag}_n"] = n
    _sweep["n"] = _sweep["lo_n"]


def _reset_pet(app, pid: str):
    """夹具重置(harness 选择,不是注入被测量量):起点/朝向/静止入态。

    新判据三段夹具复用同一只额外宠(`cfg.max_pets=3`,不越上限、不改 cfg);
    每段开始前归位到与相位臂同款的"静止 + 朝向 +x + 足跑道"初态。"""
    h = app.pets[pid]
    h.state.pos = (120.0, 300.0)
    h.state.heading = 0.0
    h.state.speed = 0.0
    h.body._speed = 0.0
    return h


def _pin_sweep_state(h) -> None:
    """B2-b 专用**运动瞬态归零**(夹具状态,不改产品;两臂各调一次)。

    为什么必须钉(本单元实测,不是推测):`_antennae_pose` 的摆相位是
    `sin(_ant_t · sweep_hz · 2π)` —— 相位 = `_ant_t × hz`,**不是 `∫hz dt`**;
    而 `_ant_t` 是**绝对**时间,跑过前面几段夹具后已累积 20~40 仿真秒
    (实测 ant_t0 = 20.3/21.4/24.4/27.3)。⇒ 窗内姿态只要还有一点点过渡
    (上一段「行走→静止」的姿态 crossfade 残影,实测 `hz_pose` 0.738→0.700),
    相位就被放大 `_ant_t·Δhz ≈ 1 个整周期`,过零计数随之 ±2~4
    (实测 baseline c_lo = 5/6/9,且 `alert_t`/`speed`/`a.m.` 全相同);
    而这段过渡**落在窗内还是窗外**取决于上一段夹具何时停止行走 + `body.pose()`
    的调用节拍(实测并非每帧都调,~44%)⇒ 判据红绿由**夹具历史**决定。

    ⚠ 这是**夹具构造的初态**(与 `_reset_pet` 归零 pos/heading/speed 同一性质;
    真实路径里这一步由 ~0.15s 的 crossfade 自然完成)——**不构造被测承诺**:
    唤醒→扫频这条通道仍由 app 装配线端到端驱动(见 `check_sweep` 的掐线对照)。"""
    b = h.body
    for attr in ("_poses", "_alert_t", "_esc"):
        assert attr in vars(b), (
            f"B2-b 运动瞬态钉子失效:body 不再有 {attr} ⇒ 该臂读数不可信"
            "(姿态库/警戒面契约变了,先修夹具再采信数字)")
    from neuropet.body.poses import POSES
    b._poses._cur = dict(POSES["rest"])       # 姿态 crossfade 归位(= 「早已收敛」)
    b._poses.set_target("rest")
    b._alert_t = 0.0                          # 警戒计时(逃逸残影 ⇒ alert 档 hz=3.0)
    b._esc = None                             # 逃逸序列(freeze 档 hz=1.5)


def _run_app(warm: float, sec: float) -> None:
    """一个 App 装两物种(roach/fly),逐相位跑 PHASES(见模块头)。

    相位臂之后追加四条新判据夹具(每次归位同一只额外宠,不动既有两宠的
    时序/位置):B2-d 锁存序列 → B2-f 守卫(接入真实面板)→ B2-g 有界保持
    → B2-b 端到端扫频。"""
    with tempfile.TemporaryDirectory() as td:
        app = _make_app(Path(td))
        pets = []
        for i, kind in enumerate(("roach", "fly")):
            # 起点/朝向固定(harness 选择,不是注入被测量量):朝向 +x 给足跑道,
            # 免"撞墙枢转"污染强制行走臂的巡航稳态(实测噪声源)。
            pid = app.add_pet(SPECIES[kind], pos=(120.0, 300.0 + 130.0 * i),
                              pet_id=f"{kind}1")
            app.pets[pid].state.heading = 0.0
            _force_walk(app.pets[pid])
            pets.append((kind, pid))
        for tag, hunger, fear, off in PHASES:
            got = _phase(app, [(k, p, hunger, fear) for k, p in pets],
                         warm=warm, sec=sec, off=off)
            for kind, a in got.items():
                _arms[(kind, tag)] = a
        x_pid = app.add_pet(SPECIES["roach"], pos=(120.0, 300.0), pet_id="x1")
        _latch_seq(app, x_pid)
        _guard_seq(app, x_pid)
        _hold_seq(app, x_pid)
        _conv_seq(app, x_pid)
        _sweep_seq(app, x_pid)
        _teardown(app)


# ---------------------------------------------------------------- 判据
def _report() -> dict:
    d = {}
    for kind in ("roach", "fly"):
        g = {t: _arms[(kind, t)] for t, _, _, _ in PHASES}
        def rel(a, b, key):
            return 100.0 * (a[key] / b[key] - 1.0)
        d[kind] = {
            "stride_pct": rel(g["h_hi"], g["h_lo"], "stride"),
            "hz_pct": rel(g["h_hi"], g["h_lo"], "hz"),
            "off_stride_pct": rel(g["h_off"], g["h_lo"], "stride"),
            "off_hz_pct": rel(g["h_off"], g["h_lo"], "hz"),
            "stop_pp": 100.0 * (g["f_hi"]["stop"] - g["h_lo"]["stop"]),
            "off_stop_pp": 100.0 * (g["f_off"]["stop"] - g["h_lo"]["stop"]),
            **{f"g_{t}": a for t, a in g.items()},
        }
        print(f"  臂 {kind}: " + " | ".join(
            f"{t}: v={a['speed']:.2f} hz={a['hz']:.3f} stride={a['stride']:.3f} "
            f"stop={a['stop']:.4f} duty={a['duty_walk_med']:.3f} "
            f"hz∈[{a['hz_min']:.2f},{a['hz_max']:.2f}] droop={a['droop_med']:.3f} "
            f"arousal∈[{a['arousal'][0]:.2f},{a['arousal'][1]:.2f}]"
            for t, _, _, _ in PHASES for a in (g[t],)))
        x = d[kind]
        print(f"  Δ{kind}: stride {x['stride_pct']:+.2f}% | hz {x['hz_pct']:+.2f}% "
              f"| stop {x['stop_pp']:+.2f}pp 〔off: stride {x['off_stride_pct']:+.2f}% "
              f"hz {x['off_hz_pct']:+.2f}% stop {x['off_stop_pp']:+.2f}pp〕")
    return d


def check_r5() -> dict:
    d = _report()
    for kind in ("roach", "fly"):
        x = d[kind]
        record(f"R5-①{kind} 饥饿→步幅 ≤−15%", x["stride_pct"] <= -15.0,
               f"stride {x['g_h_lo']['stride']:.3f}→{x['g_h_hi']['stride']:.3f} "
               f"({x['stride_pct']:+.2f}%)")
        record(f"R5-②{kind} 步频 |Δ| ≤5%", abs(x["hz_pct"]) <= 5.0,
               f"hz {x['g_h_lo']['hz']:.3f}→{x['g_h_hi']['hz']:.3f} "
               f"({x['hz_pct']:+.2f}%)")
        record(f"R5-③{kind} 恐惧→停顿 +≥10pp", x["stop_pp"] >= 10.0,
               f"stop_frac {x['g_h_lo']['stop']:.4f}→{x['g_f_hi']['stop']:.4f} "
               f"({x['stop_pp']:+.2f}pp)")
    return d


def check_band() -> None:
    """R5-④:映射只许在带内调制(不越 test_acceptance_final 的 hz/duty 带)。"""
    hz_lo, hz_hi, dut_lo, dut_hi = 3.0, 8.0, 0.42, 0.50
    bad = [f"{k}/{t} hz∈[{_arms[(k, t)]['hz_min']:.3f},{_arms[(k, t)]['hz_max']:.3f}]"
           for (k, t), a in _arms.items()
           if not (hz_lo - 1e-9 <= a["hz_min"] and a["hz_max"] <= hz_hi + 1e-9)]
    record(f"R5-④a 全臂({len(_arms)})逐帧 hz∈[3,8]", not bad,
           "、".join(bad) if bad else "逐帧 hz 均在带内(停顿帧 = hz_rest 3.0)")
    bad = [f"{k}/{t} duty={_arms[(k, t)]['duty_walk_med']:.3f}"
           for (k, t), a in _arms.items()
           if not (dut_lo - 1e-9 <= a["duty_walk_med"] <= dut_hi + 1e-9)]
    record("R5-④b 巡航帧 duty∈[0.42,0.50]", not bad, "、".join(bad) if bad else
           "、".join(f"{k}/{t} {a['duty_walk_med']:.3f}"
                     for (k, t), a in _arms.items()))


def check_antenna() -> None:
    """B2-a 恐惧→触角下垂角↑(R5 未覆盖项,单元目标里的可观察面)。"""
    for kind in ("roach", "fly"):
        lo, hi = _arms[(kind, "h_lo")]["droop_med"], _arms[(kind, "f_hi")]["droop_med"]
        record(f"B2-a{kind} 恐惧→触角下垂角↑", hi > lo + 0.02,
               f"droop {lo:.3f}→{hi:.3f} rad(fear 0→0.85)")


def check_sweep() -> None:
    """B2-b 唤醒→触角扫频↑(**端到端**:app 装配线在测,只替换情绪源)。

    原口径(body 级直调 `set_emotion_gait`)绕过 app 装配点 → 黑盒掐线法实测
    「app 把 arousal 传成 0.0 时 19/19 仍全绿」= 唤醒通道端到端无人守护。
    新口径把同一断言抬到承诺的那一层:情绪源 → `app._step_frame` 装配 →
    body 通道 → 触角梢端轨迹。掐 app 的 arousal 装配 → 本判据必红。

    ---- 原判据 → 新判据 → 为什么(r25 收口单元;workflow §3/§5) ----
    原:`c_lo >= 3 and c_hi >= 1.2*c_lo`,窗 **5 仿真秒**、夹具**不归零运动瞬态**。
      时基钉住后实测 9 次:c_lo = 5,6,8,11,13,13 ⇒ 3 红 3 绿(掷硬币),
      最险一次 8→10 = 1.25(**余量 4%**)。
    新:`c_lo >= 3 and c_hi >= 1.2*c_lo`(**表达式与比值一字未动**)+
      ① 每臂前 `_pin_sweep_state`(运动瞬态归零;见该函数 docstring);
      ② 窗 **15 仿真秒**(`SWEEP_SEC`)。
    为什么:两处**共同**致病,且先分清了「污染」还是「样本量」——
      ① **污染**:姿态 crossfade 残影(0.738→0.700)在 `_ant_t·hz` 这个
         **非积分**相位口径下被放大成 ~1 个整周期的相位摆动,而残影落在窗内
         还是窗外由**上一段夹具 + 渲染节拍**决定(实测:钉前 c_lo=5/6/9,
         钉后 3/3 = 7/7/7;`alert` 帧 0/300、`_speed`=0、amp 逐位相同 ⇒ 不是
         `_screen_mon`/不是 RNG 抽样:纯数学扫描 720 档起始相位,0.7Hz 在 5s 窗
         只有 7(97%)/8(3%),而 `_ant_t` 初值(未播种 `random.uniform(0,10)`)
         只搬相位不搬计数)。
      ② **样本量**:5s 窗只有 7 个过零点,±1 个计数 = ±14%,却要分辨 20% 的差;
         15s 窗计数 ≈21 ⇒ ±1/21 = ±5%,最坏比值 31/22 = 1.41(门限 1.2)。
      负向对照(当次就做,见 r25-b2b-close-r1.md):`NEUROPET_EMO_GAIT=off`
      与 `EMO_SWEEP_GAIN=0` 两个真违规都**变红**(c_hi≈c_lo ⇒ 比值 1.0)。"""
    hz_lo, hz_hi = _sweep["lo_hz"], _sweep["hi_hz"]
    rest_hz = hz_lo[0]
    record("B2-bⓞ 前置:两臂窗内姿态档位恒定(=静息档;运动瞬态钉子生效)",
           max(hz_lo[1] - hz_lo[0], hz_hi[1] - hz_hi[0]) < 1e-3,
           f"窗内 antenna_hz:lo {hz_lo[0]:.4f}~{hz_lo[1]:.4f}、"
           f"hi {hz_hi[0]:.4f}~{hz_hi[1]:.4f}(恒定 0.700 = 无 crossfade 残影;"
           f"漂移 ⇒ `_pin_sweep_state` 失效/宠物被扰动,本臂读数不可信)")
    c_lo, c_hi = _sweep["lo"], _sweep["hi"]
    ok = c_lo >= 3 and c_hi >= 1.2 * c_lo
    t_lo, t_hi = _sweep["lo_sim"], _sweep["hi_sim"]
    record("B2-b 唤醒→触角扫频↑(app 装配线)", ok,
           f"{SWEEP_SEC:.0f} 仿真秒(实测 {t_lo:.2f}/{t_hi:.2f}s)静止态梢端横向"
           f"过零数 {c_lo}→{c_hi} = {c_lo / t_lo:.2f}→{c_hi / t_hi:.2f} 次/仿真秒"
           f"(arousal 0.0→0.9;_n={_sweep['n']};高唤醒档 = {rest_hz:.2f}Hz×"
           f"(1+0.6×body 通道 {_sweep['hi_body']:.4f}) = "
           f"{rest_hz * (1.0 + 0.6 * _sweep['hi_body']):.2f}Hz)")
    record("B2-b' 唤醒通道真的到达 body(app 装配线)",
           _sweep["lo_body"] == 0.0 and _sweep["hi_body"] > 0.5,
           f"body._emo_arousal {_sweep['lo_body']:.4f}→{_sweep['hi_body']:.4f}")


def check_latch() -> None:
    """B2-d 无锁存(修复 1):跨阈往返 + `clear_memory` 跳变后状态仍只依赖当前情绪。

    原判据:无(黑盒只登记了缺陷,没做成判据)。新判据:阈下帧后
    `stride_scale` 必须逐位 1.0(不是锁存的 0.858002)、停顿窗必须关闭、
    序列之后切 off 的行走臂必须逐位回前值。为什么:通道状态若保留历史值,
    `off` 就不再等价于"改动前行为"(回退承诺只在从未越过阈值时成立),
    且同一份状态出现两个说法(brain 说 fear=0,body 还在按 fear=0.9 停顿)。"""
    x = _latch
    record("B2-d① 阈上步长确被削(前置条件成立)",
           0.80 < x["hi"] < 0.99 and abs(x["hi"] - x["expect_hi"]) < 2e-3,
           f"hunger 0.75 → stride_scale={x['hi']:.6f}(公式值 {x['expect_hi']:.6f};"
           f"黑盒锁存场景值 0.858002)")
    record("B2-d② 跌回阈下必须逐位回 1.0(无锁存)",
           x["lo"] == 1.0 and x["lo2"] == 1.0,
           f"hunger 0.75→0.10 后 stride_scale={x['lo']:.6f};二次往返={x['lo2']:.6f}"
           f"(δ={x['lo'] - x['expect_hi']:+.6f})")
    record("B2-d③ 再上阈重新被削(通道没被写死)",
           x["hi2"] < 0.99, f"再回 hunger 0.75 → stride_scale={x['hi2']:.6f}")
    record("B2-d④ 恐惧停顿窗确实开着(前置条件成立)",
           x["fear_state"][0] > 0.5 and x["stop_fear"] > 0.1,
           f"fear 0.95 → _emo_fear={x['fear_state'][0]:.4f} "
           f"停顿占比={x['stop_fear']:.3f}")
    record("B2-d⑤ clear_memory 跳变后通道关闭(恐惧不锁存)",
           x["fear_after_clear"] == 0.0 and x["state_after_clear"][2] == 0.0
           and x["stop_after_clear"] == 0.0,
           f"脑内 fear={x['fear_after_clear']:.3f} → body._emo_fear="
           f"{x['state_after_clear'][2]:.4f}、停顿占比={x['stop_after_clear']:.3f}"
           f"(黑盒缺陷:锁存 0.7407、停顿窗无限期继续)")
    record("B2-d⑥ 序列后切 off:逐位回单元前值(off 承诺)",
           all(abs(x["off_arm"][k] - v) <= tol for k, v, tol in
               (("speed", PRE_WALK["roach"][0], _SPEED_TOL),
                ("hz", PRE_WALK["roach"][1], _SPEED_TOL),
                ("stride", PRE_WALK["roach"][2], 1e-3))),
           f"off v={x['off_arm']['speed']:.4f}/hz={x['off_arm']['hz']:.4f}/"
           f"stride={x['off_arm']['stride']:.4f}(前值 "
           f"{PRE_WALK['roach'][0]}/{PRE_WALK['roach'][1]}/{PRE_WALK['roach'][2]})")


def check_guard() -> None:
    """B2-f 装配守卫(修复 3 + r2 复验问题 2/3):插件脑脏情绪/抛异常 → 不抛、不中断帧。

    原判据:无。新判据:脏情绪序列(6 种形态)期间 **0 次异常日志**(`_tick`
    的 except 出口)且行走照常推进;干净情绪回来后通道重新生效(守卫是
    逐轮跳过,不是把通道写死)。为什么:插件脑 `emotion()` 契约不保证,
    脏值曾每帧抛 TypeError 整帧中断(c9d7da1 同类事故)。
    **面板路径**(r2 复验问题 2):真实面板接入主循环且选中宠 = 脏值宠 ——
    原夹具注入非选中宠,`pet_status → emotion()` 这条 6Hz 通路不在判据内。"""
    x = _guard
    d = {n: x[f"dist_{n}"] for n, _ in _DIRTY_EMO}
    record("B2-f① 脏情绪零异常(不中断帧)", not x["errs"],
           f"6 种脏值 × 0.30s:{len(x['errs'])} 次异常日志"
           f"{'(' + '、'.join(sorted(set(x['errs']))) + ')' if x['errs'] else ''}")
    record("B2-f② 每种脏值下帧都照常推进(位移)", min(d.values()) > 20.0,
           "、".join(f"{n}={v:.0f}px" for n, v in d.items()) + "(中断帧则≈0)")
    record("B2-f③ 干净情绪回来通道重新生效", x["re_engage"][0] < 0.99,
           f"再接 hunger 0.9 → _emo_speed={x['re_engage'][0]:.4f} "
           f"stride_scale={x['re_engage'][1]:.4f}")
    record("B2-f④ 面板 6Hz 读数路径在判据内(选中宠 = 脏值宠)",
           x["panel_sel"] and x["status_calls"] > 0,
           f"panel.selected()==脏值宠={x['panel_sel']};脏值期 pet_status 调用 "
           f"{x['status_calls']} 次(0 = 面板通路不在判据内 = 夹具红利)")
    record("B2-h① emotion() 抛异常:零异常逃逸 + 帧照常推进",
           not x["errs"] and x["exc_dist"] > 20.0,
           f"RuntimeError 源驱动 0.5s:异常日志 {len(x['errs'])} 次、位移 "
           f"{x['exc_dist']:.0f}px、状态 _emo_fear={x['exc_state'][0]:.4f} "
           f"stride_scale={x['exc_state'][1]:.4f}(> 保持窗 ⇒ 回中性)")
    record("B2-h② 抛异常期间面板 6Hz 读数仍被驱动(调用面受保护)",
           x["status_calls_exc"] > x["status_calls"],
           f"pet_status 累计 {x['status_calls']}→{x['status_calls_exc']} 次")


def check_hold() -> None:
    """B2-g 有界保持 + 脏值期回退(r2 复验问题 1):脏值不得造成**无界陈旧**,
    且 `off` 的生效不许取决于情绪数据是否健康。

    修复前:守卫"跳过装配"= 不写状态,而 `set_emotion_gait` 是这 5 个字段的
    唯一写者 ⇒ 永久脏值期间 `_emo_fear` 停在斜坡值、停顿窗按 ~55% 占空**无限期**
    开着,且此时 `NEUROPET_EMO_GAIT=off` **也不生效**(根本没被调用)。"""
    x = _hold
    record("B2-g⓪ 前置:恐惧通道确实开着(否则下面的判据空转)",
           x["fear_open"][0] > 0.5,
           f"fear 0.95 → _emo_fear={x['fear_open'][0]:.4f} "
           f"stride_scale={x['fear_open'][1]:.4f}")
    a = x["after_dirty"]
    record("B2-g① 永久脏值 ≥2s → 状态回到中性(不无界陈旧)",
           a == (0.0, 1.0, 1.0, 0.0) and x["stop_dirty"] == 0.0,
           f"永久脏值(emotion() 恒 None)2.2s 后:_emo_fear={a[0]:.4f} "
           f"_emo_speed={a[1]:.4f} stride_scale={a[2]:.4f} vig_t={a[3]:.4f}、"
           f"停顿时段占比={x['stop_dirty']:.4f}"
           f"(修复前:{x['fear_before_dirty']:.4f} 无限期保留、停顿窗不关)")
    pv = PRE_WALK["roach"]
    p = x["off_arm"]
    ok = (abs(p["speed"] - pv[0]) <= _SPEED_TOL
          and abs(p["hz"] - pv[1]) <= _SPEED_TOL
          and abs(p["stride"] - pv[2]) <= 1e-3)
    record("B2-g② 永久脏值期间切 off:逐位 = 单元前值", ok,
           f"off v={p['speed']:.4f}/hz={p['hz']:.4f}/stride={p['stride']:.4f}"
           f"(前值 {pv[0]}/{pv[1]}/{pv[2]})")
    record("B2-g④ off 时直接中性化、不依赖情绪数据(off 判定先于守卫)",
           x["off_src_calls"] == 0
           and x["off_src_state"] == (0.0, 1.0),
           f"off 期间 6 帧:装配点读 `emotion()` **{x['off_src_calls']} 次**"
           f"(>0 = 先读上游再判开关 = 回退开关的生效取决于上游数据是否健康;"
           f"保持分支还会把脏值/异常带进来)、状态 _emo_fear="
           f"{x['off_src_state'][0]:.4f} stride_scale={x['off_src_state'][1]:.4f}")
    record("B2-g③ 瞬时脏值(单帧)不打断通道(保持窗吸收抖动)",
           x["hold_min"] > 0.5,
           f"1 帧 None 混在健康 fear 0.95 之间 → 逐帧 _emo_fear 最小值="
           f"{x['hold_min']:.4f}(0.0 = 该帧被中性化 = 抖动;保持窗应吸收)")


def _sprint_hz(hunger: float, off: bool, seed: int = 20260922) -> dict:
    """逃逸冲刺期步频(body 级;逃逸序列的时长/抖动是随机量 → 播种固定)。"""
    from neuropet.body.base import GenericInsectBody
    from neuropet.core.contracts import Behavior, BehaviorCommand, PetState
    from neuropet.core.world import WorldModel
    from neuropet.species.cockroach import PARAMS
    old = os.environ.get("NEUROPET_EMO_GAIT")
    os.environ["NEUROPET_EMO_GAIT"] = "off" if off else "on"
    try:
        st = PetState(pet_id="sprint", species_id="roach", pos=(500.0, 500.0))
        body = GenericInsectBody(st, dict(PARAMS))
        body.set_emotion_gait(0.0, hunger, 0.0)      # 情绪输入(app 装配同值)
        w = WorldModel(1920, 1080)
        cmd = BehaviorCommand(Behavior.ESCAPE, target=(1700.0, 500.0),
                              intensity=1.0)
        random.seed(seed)
        hz, ph = [], []
        for i in range(240):                         # 4s:freeze→turn→sprint→decel
            body.apply(cmd, w.snapshot("sprint", i * FRAME_DT), FRAME_DT)
            e = body._esc
            p = None if e is None else e["phase"]
            ph.append(p)
            if p == "sprint":
                hz.append(float(body._gait.step_hz(body._speed)))
    finally:
        if old is None:
            os.environ.pop("NEUROPET_EMO_GAIT", None)
        else:
            os.environ["NEUROPET_EMO_GAIT"] = old
    seq = [p for i, p in enumerate(ph) if i == 0 or ph[i - 1] != p]
    return {"hz_max": max(hz) if hz else 0.0, "n": len(hz), "phases": seq}


def check_conv() -> None:
    """B2-i 单一真源的收敛判据(§23):面板读数与 body 驱动值可短暂分歧,
    但必须**有界且必然收敛**(§20「单一真源」的准确语义 = 不许**持久**分歧)。"""
    x = _conv
    tol = HOLD_MAX_S + 2 * FRAME_DT        # 保持窗 + 帧量化(观测只在帧边界)
    a_dr = x["a_drive"] or (0.0, 0.0, 0.0)
    record("B2-i① 前置:健康期面板读数 ≡ body 驱动值(逐帧,同一真源)",
           x["a_frames"] > 20 and x["a_bad"] == 0 and a_dr[0] > 0.5,
           f"健康 fear 0.95 驱动 {x['a_frames']} 帧:分歧 {x['a_bad']} 帧、"
           f"驱动值 fear={a_dr[0]:.4f}(通道真开着,非空转)")
    record("B2-i② 脏值期分歧有界且必然收敛(≤保持窗+2 帧)",
           x["b_div"] > 0 and 0.0 < x["b_conv_s"] <= tol
           and x["b_drive_end"] == (0.0, 0.0, 0.0),
           f"永久脏值 2.0s:分歧 {x['b_div']}/{x['b_frames']} 帧、峰值 |面板−驱动| "
           f"={x['b_peak']:.3f}、分歧时长 {x['b_conv_s']:.3f}s(上限契约 "
           f"{HOLD_MAX_S:.2f}+2 帧={tol:.3f}s;实现常量 {x['hold_impl']:.3f})"
           f"⇒ 必然收敛、末帧驱动=({x['b_drive_end'][0]:.3f},"
           f"{x['b_drive_end'][1]:.3f})")
    record("B2-i③ 收敛后不长期分歧(尾段够长且零分歧,两者同为中性)",
           x["b_tail"] >= 20 and x["b_tail_div"] == 0
           and x["b_panel_end"]["fear"] == 0.0,
           f"分歧结束后尾段 {x['b_tail']} 帧 / {x['b_tail_s']:.2f}s 零分歧"
           f"(尾段长度入判据:无界陈旧时尾段=0,「零分歧」会空洞成立);"
           f"末帧面板 fear={x['b_panel_end']['fear']:.3f}"
           f"(面板 {x['panel_calls']} 次 6Hz 读数在窗内)")
    c_dr = x["c_drive"] or (0.0, 0.0, 0.0)
    record("B2-i④ 异常输入结束后收敛 ≤1 帧(且通道重新生效)",
           x["c_frames"] > 20 and x["c_bad"] == 0 and round(c_dr[0], 3) == 0.95,
           f"抛异常源 → 干净 fear 0.95 回来:{x['c_frames']} 帧零分歧、驱动值 "
           f"fear={c_dr[0]:.4f}(恢复即一致,非两端同为 0 的空转)")


def check_sprint() -> None:
    """B2-e 情绪映射限定在非逃逸分支(修复 2):sprint 期 hz 不被饥饿拉起。

    原判据:无(黑盒只登记缺陷)。新判据:同一 seed 下 `hunger=0.90` 的 sprint
    期 hz 上界 ≤ 同相位 `NEUROPET_EMO_GAIT=off` 值。为什么:`step_hz/duty`
    的 `v/stride_scale` 还原对**所有**调用方生效,而逃逸冲刺速度不乘
    `_emo_speed` → 冲刺期 hz 被拉起 11.0→13.3(逼近 14Hz 钳位),与
    `_apply_ground` 注释"逃逸冲刺不受此限"矛盾。"""
    off = _sprint_hz(0.10, off=True)
    on90 = _sprint_hz(0.90, off=False)
    on10 = _sprint_hz(0.10, off=False)
    ok = (off["n"] > 0 and on90["n"] > 0
          and on90["hz_max"] <= off["hz_max"] + 1e-9
          and on10["hz_max"] <= off["hz_max"] + 1e-9)
    record("B2-e sprint 期 hz 不被情绪映射拉起", ok,
           f"sprint 帧数 {on10['n']}/{on90['n']}/{off['n']},hz 上界 "
           f"hunger0.10={on10['hz_max']:.3f} / hunger0.90={on90['hz_max']:.3f} / "
           f"off={off['hz_max']:.3f}(相位序列={on90['phases']})")


def check_negative(d: dict) -> None:
    """B2-c 负向对照:关通道 → R5 ①②③ 全部不达 + 强制行走臂逐位回前值。"""
    for kind in ("roach", "fly"):
        x = d[kind]
        record(f"B2-c{kind} 关通道后步幅判据应不达",
               not (x["off_stride_pct"] <= -15.0),
               f"off stride {x['off_stride_pct']:+.2f}%(>−15% → 判据会红)")
        record(f"B2-c{kind} 关通道后步频判据(hz 未变,步幅也未变)",
               abs(x["off_hz_pct"]) <= 5.0 and abs(x["off_stride_pct"]) <= 5.0,
               f"off hz {x['off_hz_pct']:+.2f}% stride {x['off_stride_pct']:+.2f}%"
               "(两项都不动 = 通道确实惰性)")
        record(f"B2-c{kind} 关通道后停顿判据应不达", x["off_stop_pp"] < 10.0,
               f"off stop {x['off_stop_pp']:+.2f}pp(<+10pp → 判据会红)")
        pv = PRE_WALK[kind]
        a = x["g_h_off"]
        ok = (abs(a["speed"] - pv[0]) <= _SPEED_TOL
              and abs(a["hz"] - pv[1]) <= _SPEED_TOL
              and abs(a["stride"] - pv[2]) <= 1e-3)
        record(f"B2-c{kind} 关通道 = 单元前值(逐位)", ok,
               f"v={a['speed']:.4f}(前值 {pv[0]}) hz={a['hz']:.4f}(前值 {pv[1]}) "
               f"stride={a['stride']:.4f}(前值 {pv[2]})")


def main() -> None:
    t0 = time.perf_counter()
    _run_app(warm=0.5, sec=1.0)
    d = check_r5()
    check_band()
    check_antenna()
    check_sweep()
    check_latch()
    check_guard()
    check_hold()
    check_conv()
    check_sprint()
    check_negative(d)
    print(f"\n情绪→步态判据:{_n - len(_fails)}/{_n} 通过"
          f"({time.perf_counter() - t0:.0f}s)")
    if _fails:
        print("FAIL:" + "、".join(_fails))
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    # 时基判据失败 = **读数作废**(不判红绿):rc=2 + 明写 [VOID],不许静默降级。
    try:
        main()
    except TimebaseError as exc:
        print(f"\n[VOID] 时基钉失效 ⇒ 本次读数作废(不判红绿,rc=2): {exc}")
        sys.exit(2)
