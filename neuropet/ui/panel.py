"""NeuroPet 控制面板:常驻个体条 + 四意图页导航 + 滚动骨架。

r25 A1:页签**之上**新增常驻个体条 `PetBar`(在 `self.win` 内,切页不重建
→ 选宠/投喂/冻结/隐藏在任意页 1 点击可达):
  宠物行列表(每只一行:名称(pid)/物种/行为·模式·饱食 + "i" 按钮,悬停弹
            深底浅字 tooltip:情绪四维+能量+当前行为+一条引导文案,
            2.5s 自动关,复用同一 Toplevel)
  动作区    [投喂](Accent 主操作) [冻结 ☐] [隐藏] 隐藏中:N
  抽屉行    [选食物 ▾/▴] 已选:X  ← 展开 = **页内可折叠区**(ttk.Frame 显隐,
            非 Toplevel):原食物选择器(每种食物色块按钮,悬停显示效果描述,
            选中态高亮;暴露 selected_food_kind 属性与 on_drop_request
            回调注册点,供 App 接桌面投喂),默认收起

r25 A2:六页工具表单 → **四意图页**(页签文案与顺序冻结,架构 §10 裁决 2;
每页统一包成 Canvas+ttk.Scrollbar 滚动容器 `_ScrollPage`,内容高度超窗
即可滚,最小窗口 520×640 下零截断):
  看它(默认页) 选中个体读数网格(名称/行为/模式/位置/饱食/状态标记/
            记忆条数/体型档位 + 学习状态行)+ 饱食进度条 → 七维情绪横条
            → 「记住的事」折叠抽屉(记忆时间线:情景/联想/状态/未识别四分区,
              情景按时间戳降序 + 显著度条;解析失败行原样显示)
  逗它      立即触发 explore/groom/seek_food/eat/escape/rest + 逃逸测试
            → 联想学习演示三按钮 → 各行为含义 / 怎么玩说明
            (两段说明由 A5 改为按需展开,本轮仍常驻)
  养它      增删 → 隐藏与召回 → 智能等级(A4 将删) → 体型缩放 → 恢复原形
            → 危险操作(清除记忆,二次确认)
  系统      项目简介 → 已注册插件清单 → 外部插件加载报告 → 投喂模式
            → 运行状况(原状态页全局块 + 内存护栏只读行)

信息节奏分档(架构 §2 A.2):常驻个体条与「看它」页读数 6Hz;情绪条**每帧**
更新(仅「看它」页),用一阶插值把 6Hz 采样平滑到 ≥50Hz 视觉连续(插值系数
按 dt 归一,帧率切档不跳变);其余页 2Hz;面板隐藏时 `tick` 早退(全停)。

第十轮语义变更(用户裁决):
- 引导气泡不再画在宠物头顶(舞台气泡层退役);引导文案迁入面板,
  由 "i" 图标悬停 tooltip 呈现(文案源 neuropet/ui/bubbles.py);
- 投喂不再随机抽签:用户先在食物选择器选食物再投喂
  (效果表 neuropet/feeding.py FOODS)。

约定与边界:
- 对 App 的控制一律走公开 API;唯一例外:「清除演示区域」直接调
  app.world.clear_zones() 并置 app._static_dirty = True(维持原状)。
- App 以 ControlPanel(app) 构造,并调用 refresh_pets()/set_feeding(bool)/
  hide()/tick(dt)——这四个方法名与构造签名保持稳定。
- 界面全中文,字体 Microsoft YaHei UI;无宠物时所有操作安全降级为提示。
"""
from __future__ import annotations

import math
import re
import time

import tkinter as tk
from tkinter import messagebox, ttk

from ..core import instr as _I     # r25 U0:tick 可见/隐藏计数(默认关;零成本)

from neuropet.core.windowing import working_set_mb
from neuropet.feeding import food_catalog
from neuropet.ui import panel_theme as T
from neuropet.ui.bubbles import tooltip_text

# 字体与颜色统一来自设计系统(panel_theme);名字保留给既有调用点。
FONT = T.FONT
FONT_BOLD = T.FONT_BOLD
FONT_SMALL = T.FONT_SMALL
HINT_FG = T.INK_SOFT
HINT_FAINT = T.INK_FAINT
CARD_BG = T.CARD
PAPER_BG = T.PAPER

BEHAVIORS = ["explore", "groom", "seek_food", "eat", "escape", "rest"]

# AG5:体型五档(用户需求 0.5/0.75/1/1.5/2;即改即生效,per-pet 持久化)
SCALE_LABELS = ("0.5×", "0.75×", "1×", "1.5×", "2×")
SCALE_VALUES = (0.5, 0.75, 1.0, 1.5, 2.0)

# 行为含义(与 brain/body 实际实现对齐:面板触发 = 注入优先级 50 的命令)
BEHAVIOR_HELP = (
    "explore    探索:随机选目标点漫游,是空闲时的默认行为\n"
    "groom      理毛:原地停下清洁身体(自我维持行为)\n"
    "seek_food  觅食:朝气味可及范围内最近的食物移动\n"
    "eat        进食:停在食物处进食,饱食度上升\n"
    "escape     逃逸:以冲刺速度远离刺激源方向\n"
    "rest       休息:原地静止,唤醒度逐渐回落\n"
    "\n"
    "「立即触发」向选中宠物的大脑注入一条优先级 50 的命令;\n"
    "若随后出现更强的真实刺激(如危险逼近),反射仍会正常接管。\n"
    "「逃逸测试」在宠物一侧注入强度 0.95 的气流刺激,\n"
    "用于观察 感知→情绪→逃逸 反射链路。"
)

# 展示用中文映射(未知枚举值回退原文)
BEHAVIOR_ZH = {"idle": "待机", "explore": "探索", "groom": "理毛",
               "seek_food": "觅食", "eat": "进食", "escape": "逃逸",
               "rest": "休息", "turn": "转向", "takeoff": "起飞",
               "fly_wander": "飞行", "land": "降落"}
MODE_ZH = {"crawl": "爬行", "fly": "飞行", "swim": "游泳"}

# 七维情绪:(键, 中文名, 条形颜色)。七色经去饱和与同明度校准,交由
# 青绿强调色之外的**语义色域**承载——情绪条是数据可视,不是装饰。
EMOTIONS = [
    ("fear", "恐惧", "#b5544e"),
    ("hunger", "饥饿", "#c98a35"),
    ("curiosity", "好奇", "#4a86b8"),
    ("anger", "愤怒", "#95413a"),
    ("trust", "信任", "#4f9169"),
    ("valence", "效价", "#8469a8"),
    ("arousal", "唤醒", "#c2703c"),
]

# 情绪条几何与 tooltip 底(tooltip 深底 = 主文字色,与设计系统同源)
BAR_X0, BAR_X1 = T.BAR_X0, T.BAR_X1   # 情绪条形图:条形轨道左右边界(canvas 像素)
BAR_H = T.BAR_H                        # 条形高度
ROW_H = T.ROW_H                        # 每行行距
TRACK = T.TRACK                        # 条轨道填充
EMO_TRACK_LINE = "#cfd6dd"             # 条轨道描边

# ---- 第十轮:tooltip / 滚动骨架 / 食物选择器常量 ----
TIP_BG = T.INK                 # tooltip 深底
TIP_FG = "#eef2f6"             # tooltip 浅字
TIP_AUTO_CLOSE_MS = 2500       # tooltip 自动关闭(ms;骨架清单裁决值)
WHEEL_STEP = 120               # Windows 滚轮一格的 delta 基数
FOOD_COLS = 3                  # 食物选择器每行按钮数

# ---- r25 A2:信息节奏分档 + 「看它」页抽屉 ----
FAST_S = 1.0 / 6.0             # 常驻个体条 / 「看它」读数:6Hz 节流
SLOW_S = 0.5                   # 其余页:「系统」页慢变读数 2Hz 节流
EMO_TAU = 0.25                 # 情绪条一阶插值时间常数(s):
                               #   每帧步长 ≈ (dt/τ)×满量程 = 6.5% @60fps
                               #   < 验收 ⑤ 的"相邻两帧 ≤ 满量程 8%"(无跳变);
                               #   按 dt 归一 → 帧档切到 30fps 也不跳。
MEM_TEXT_H = 11                # 「记住的事」抽屉正文行数(展开态 ≈200px 起)

# ---------------- r25 A3:记忆时间线(解析 + 渲染;纯函数,只用 stdlib `re`) ----
# `app.memory_of(pid)` 返回的是**异质行**,不是"一堆记忆"(本仓库实测四类):
#   `[NN] …` / `[GF] …` 物种状态行(无时间戳,本就不是记忆)
#   `[联想] …`           语义联想行(无时间戳,累计统计量)
#   `[MM-DD HH:MM] …`    情景行(唯一带时间戳 → 唯一能进时间线)
#   (其它)               解析失败 → 原样显示,不抛异常
# 上游契约(`memory_of`/`memory_digest`)本轮**只解析、不改**。
_RE_EPISODE = re.compile(r"^\[(\d{2})-(\d{2}) (\d{2}):(\d{2})\]\s*")
_RE_ASSOC = re.compile(r"^\[联想\]")
_RE_STATE = re.compile(r"^\[(?:NN|GF)\]")


def _split_salience(body: str) -> tuple[str, float | None]:
    """情景行尾注"显著度" → ``(摘要, 显著度|None)``。

    本仓库**两种情景行格式并存** —— `memory.py:88` 带 ``(显著度 0.72)``、
    `base.py:169` 不带 → 两种都算情景行,无显著度时返回 `None`
    (渲染成"显著度未知",**不得丢进未识别区**,也**不当 0**)。

    尾注判定没用 `re` 是**纯性能**原因(判据 ④:200 行 × 2Hz 的单次刷新
    ≤1.5ms,尾注正则在解析里占大头);语义守卫保留 —— 尾部括号里必须
    出现"显著度"且最后一段能转 float,否则**原文整段保留**(不静默截断)。
    """
    if not body.endswith(")"):
        return body, None
    i = body.rfind("(")
    if i < 0:
        return body, None
    tok = body[i + 1:-1]
    if "显著度" not in tok:
        return body, None
    k = tok.rfind(" ")
    if k < 0:
        return body, None
    try:
        return body[:i].strip(), max(0.0, min(1.0, float(tok[k + 1:])))
    except ValueError:              # 数值坏 → 当未知,但正文原样保留
        return body, None


_MEM_BAR_W = 10                # 显著度条(文本模式)格数
#: 分区(渲染顺序 = 情景在首:它才是"时间线"主体)
_MEM_SECTIONS = (("episodic", "情景记忆 · 新 → 旧"),
                 ("assoc", "联想记忆(累计统计,不参与时间排序)"),
                 ("state", "当前状态(不是记忆)"),
                 ("unknown", "未识别(解析失败,原样显示)"))
#: 预生成显著度条与数值文本(判据 ④:200 行 2Hz 重绘 ≤1.5ms → 省掉每行的
#: 字符串乘法与浮点格式化,查表即可)。
_MEM_BAR_TOK = tuple("[" + "#" * i + "-" * (_MEM_BAR_W - i) + "]"
                     for i in range(_MEM_BAR_W + 1))
_MEM_SAL_TOK = tuple(f"{i / 100:.2f}" for i in range(101))
_MEM_SAL_NONE = "[" + "-" * _MEM_BAR_W + "] 显著度未知"


def parse_memory_lines(lines) -> dict:
    """`memory_of()` 异质行 → 四类分区(A3;只解析,不改上游契约)。

    返回 ``{"episodic": [...], "assoc": [...], "state": [...], "unknown": [...]}``,
    每项 = ``{"raw", "text", "stamp", "salience"}``。要点:

    - **情景行两种格式都收**(带/不带"显著度"),无显著度时 `salience=None`;
    - **情景行按时间戳降序**(新→旧):输入序是 `episodes[-limit:]`(旧→新),
      排序必须显式做,不能靠输入序。时间戳只有 MM-DD HH:MM(上游格式决定,
      无年份)→ 本实现按 `%m-%d %H:%M` 字典序排序,**跨年会把旧记录排到新记录之前**
      (12-31 排在 01-01 前)。⚠ 这**不是「面板层无解」**(主控复核更正了执行方的说法):
      面板层**可解** —— 对每个 MM-DD 取「不晚于现在」的最近一次出现即得年份
      (`time.localtime()` 推断;情景记忆约 5 天衰减到 1/e、容量 ≤220 ⇒ 不会有
      一年以上的条目,单年回推足够)。本轮不做的**真实理由**:要给它写判据,
      就得先给面板开一道**时钟输入缝**(否则判据依赖真实的「今天」),
      为一个只在跨年前后几天显形的边角开缝不划算 ⇒ **登记为已知缺陷 + 具名修法**
      (`docs/handoff/r25-test-A3-r1.md`);
    - **任何一行解析失败 → 原样进 `unknown`**,不抛异常(优雅降级);
    - 行数守恒:``sum(len(v) for v in 结果.values()) == len(输入)``。
    """
    ep: list = []
    as_: list = []
    st: list = []
    un: list = []
    for raw in lines or []:
        try:
            s = str(raw).strip()
            m = _RE_EPISODE.match(s)
            if m:
                body, sal = _split_salience(s[m.end():].strip())
                ep.append({"raw": s, "text": body,
                           "stamp": s[1:12], "salience": sal})
            elif _RE_ASSOC.match(s):
                as_.append({"raw": s, "text": s, "stamp": "", "salience": None})
            elif _RE_STATE.match(s):
                st.append({"raw": s, "text": s, "stamp": "", "salience": None})
            else:
                un.append({"raw": s, "text": s, "stamp": "", "salience": None})
        except Exception:                   # 极端坏输入也不拖垮整批
            un.append({"raw": str(raw), "text": str(raw),
                       "stamp": "", "salience": None})
    ep.sort(key=lambda r: r["stamp"], reverse=True)
    return {"episodic": ep, "assoc": as_, "state": st, "unknown": un}


def memory_row_count(sections: dict) -> int:
    """分区里**内容行**总数(不含分区标题 = 装饰行)。"""
    return sum(len(v) for v in sections.values())


def render_memory_timeline(sections: dict) -> list[tuple[str, str]]:
    """分区 → ``[(tag, 文本块)]``(每块一次 `Text.insert`,200 行也只几次 Tk 调用)。

    分区标题由 `_MEM_SECTIONS` 提供(即每行的"类型标签"——逐行重复标签会与
    标题重复);情景行 = ``[MM-DD HH:MM] 摘要  [####------] 0.72``。
    空分区不渲染(不出现空标题)。
    """
    blocks: list[tuple[str, str]] = []
    for key, title in _MEM_SECTIONS:
        rows = sections.get(key) or []
        if not rows:
            continue
        blocks.append(("head", f"— {title} · {len(rows)} 条 —"))
        if key == "episodic":
            body = "\n".join(
                "[%s] %s   %s" % (
                    r["stamp"], r["text"],
                    _MEM_SAL_NONE if r["salience"] is None else
                    "%s %s" % (_MEM_BAR_TOK[int(round(r["salience"] * _MEM_BAR_W))],
                               _MEM_SAL_TOK[int(round(r["salience"] * 100))]))
                for r in rows)
        else:
            body = "\n".join(r["text"] if r["text"] else "(空行)" for r in rows)
        blocks.append(("un" if key == "unknown" else key[:2], body))
    return blocks


def _memory_mb() -> float:
    """当前进程工作集(MB),取自 windowing.working_set_mb()。

    坑:working_set_mb() 会在**共享单例** ctypes.windll.psapi / windll.kernel32
    上设置进程级全局的函数签名:
      - psapi.GetProcessMemoryInfo.argtypes = [HANDLE, POINTER(PMC_EX), DWORD]
      - kernel32.GetCurrentProcess.restype  = HANDLE(c_void_p)
    这会波及同进程内其它直接调用这两个 API 的代码——例如
    scratch/_launch_check.py 的本地 ws_mb() 用简化 PMC 结构且未设 restype,
    会分别报 TypeError(结构不匹配)或 OverflowError(伪句柄 -1 被
    当作超大无符号整数)。因此这里先保存旧签名,调用后立即还原,
    对任何调用方都无可见差异。
    """
    import ctypes
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    restype_prev = getattr(k32.GetCurrentProcess, "restype", None)
    argtypes_prev = getattr(psapi.GetProcessMemoryInfo, "argtypes", None)
    try:
        return working_set_mb()
    finally:
        try:
            k32.GetCurrentProcess.restype = restype_prev
            psapi.GetProcessMemoryInfo.argtypes = argtypes_prev
        except Exception:
            pass


class _Tooltip:
    """深底浅字 tooltip:复用同一 Toplevel(不泄漏),2.5s 自动关闭。

    show(text, x, y) 幂等:首次懒创建无边框 Toplevel,此后只换文案与
    位置;hide() 后 Toplevel 保留(withdraw)供下次复用。每次 show 重置
    自动关闭计时器(用户在多个 "i" 间移动时不被上一个计时器误杀)。
    """

    def __init__(self, master) -> None:
        self._master = master
        self._tw: tk.Toplevel | None = None
        self._lbl: ttk.Label | None = None
        self._job: str | None = None

    def show(self, text: str, x: int, y: int) -> None:
        if self._tw is None:
            self._tw = tk.Toplevel(self._master)
            self._tw.overrideredirect(True)          # 无边框气泡
            self._tw.attributes("-topmost", True)
            self._tw.withdraw()
            frm = ttk.Frame(self._tw, style="Tip.TFrame", padding=(10, 7))
            frm.pack(fill="both", expand=True)
            self._lbl = ttk.Label(frm, style="Tip.TLabel", justify="left",
                                  text=text, wraplength=320)
            self._lbl.pack()
        else:
            assert self._lbl is not None
            self._lbl.configure(text=text)
        self._tw.geometry(f"+{int(x)}+{int(y)}")
        self._tw.deiconify()
        self._tw.lift()
        self._arm_auto_close()

    def hide(self) -> None:
        if self._job is not None:
            try:
                self._tw.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._tw is not None:
            try:
                self._tw.withdraw()
            except Exception:
                pass

    def _arm_auto_close(self) -> None:
        if self._job is not None and self._tw is not None:
            try:
                self._tw.after_cancel(self._job)
            except Exception:
                pass
        self._job = self._tw.after(TIP_AUTO_CLOSE_MS, self.hide)


class _ScrollPage(ttk.Frame):
    """Notebook 页统一滚动骨架:Canvas + 右侧 ttk.Scrollbar + 内部 body。

    - scrollbar 绑定 canvas.yview;scrollregion 随 body 实际高度同步
      (body <Configure>);body 宽度跟随 canvas 视口宽(不留横向白边);
    - Windows 滚轮 <MouseWheel>:delta 正值=向上滚 → yview_scroll(-n)。
      经 bind_all 挂接(覆盖页内所有子控件),但按事件 widget 的控件
      路径前缀判定归属,只滚光标所在页,不抢舞台等其它画布的滚轮。
    """

    def __init__(self, master, **kw) -> None:
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0,
                                background=PAPER_BG)
        self.sb = ttk.Scrollbar(self, orient="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=8)
        self._win_id = self.canvas.create_window((0, 0), window=self.body,
                                                 anchor="nw")
        self.body.bind("<Configure>", lambda _e: self._sync_scrollregion())
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    # ---------------- 内部 ----------------
    def _sync_scrollregion(self) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._win_id, width=event.width)
        self._sync_scrollregion()

    def _on_wheel(self, event) -> None:
        try:
            w = str(event.widget)
        except Exception:
            return
        page = str(self)
        if not (w.startswith(page + ".") or w in (page, str(self.canvas))):
            return                       # 光标不在本页:不抢滚轮
        delta = int(getattr(event, "delta", 0) or 0)
        step = delta // WHEEL_STEP or (1 if delta > 0 else -1)
        self.canvas.yview_scroll(-step, "units")   # delta>0=向上滚

    # ---------------- 对外 ----------------
    def content_height(self) -> int:
        """body 内容要求高度(px;headless 几何断言用)。"""
        return int(self.body.winfo_reqheight())

    def scrollregion_height(self) -> int:
        bbox = self.canvas.bbox("all")
        return int(bbox[3] - bbox[1]) if bbox else 0

    def scroll_to_end(self) -> None:
        self.canvas.yview_moveto(1.0)


class _PetRow(ttk.Frame):
    """宠物列表单行:选中指示 + 名称(编号) + 物种 + 状态摘要 + "i" 按钮。

    整行可点=选中;"i" 按钮 Enter/Leave 弹出面板 tooltip
    (情绪四维+能量+当前行为+一条引导文案,2.5s 自动关)。

    r25 A1:本行住在常驻个体条(**CARD 白底**)上,故行内控件全部配同底
    样式(PetBar/Card/CardHint/Chip);用默认 TLabel/TFrame(PAPER 底)
    会在白条上露出灰方补丁。
    """

    def __init__(self, master, panel: "ControlPanel", pid: str,
                 values: tuple) -> None:
        super().__init__(master, padding=(2, 3), style="PetBar.TFrame")
        self.pid = pid
        self._panel = panel
        _pid, name, species, beh, mode = values
        self.mark = ttk.Label(self, text="◦", width=2, foreground=HINT_FAINT,
                              background=CARD_BG)
        self.name_lbl = ttk.Label(self, text=f"{name}({pid})",
                                  style="Card.TLabel")
        self.species_lbl = ttk.Label(self, text=species, width=9,
                                     style="CardHint.TLabel")
        self.sum_var = tk.StringVar(value=f"{beh}·{mode}")
        sum_lbl = ttk.Label(self, textvariable=self.sum_var,
                            style="CardHint.TLabel")
        self.i_btn = ttk.Button(self, text="i", width=2, style="Chip.TButton")
        self.mark.grid(row=0, column=0)
        self.name_lbl.grid(row=0, column=1, sticky="w", padx=(0, 4))
        self.species_lbl.grid(row=0, column=2, sticky="w", padx=(0, 4))
        sum_lbl.grid(row=0, column=3, sticky="w", padx=(0, 6))
        self.i_btn.grid(row=0, column=4, sticky="e")
        self.columnconfigure(1, weight=1)
        # 整行点击选中(标签与行框都吃 <Button-1>)
        for w in (self, self.mark, self.name_lbl, self.species_lbl, sum_lbl):
            w.bind("<Button-1>", self._pick)
        # "i" 悬停 → 面板 tooltip(Enter/Leave 成对,复用同一 Toplevel)
        self.i_btn.bind("<Enter>",
                        lambda _e: self._panel._show_pet_tip(pid, self.i_btn))
        self.i_btn.bind("<Leave>", lambda _e: self._panel._hide_pet_tip())

    def _pick(self, _event=None) -> None:
        self._panel.tree.selection_set((self.pid,))

    def set_selected(self, on: bool) -> None:
        self.mark.configure(text="▶" if on else "◦")

    def update_summary(self, name: str, summary: str) -> None:
        self.name_lbl.configure(text=f"{name}({self.pid})")
        self.sum_var.set(summary)


class _PetList(ttk.Frame):
    """宠物列表(每只宠物一行)。

    duck-type 旧 ttk.Treeview 的子集(get_children/selection/
    selection_set/delete/insert):panel 既有逻辑与 tests/test_pet_hide
    的面板接线断言依赖这些名字,保持调用面不变。
    """

    def __init__(self, master, panel: "ControlPanel", **kw) -> None:
        super().__init__(master, **kw)
        self._panel = panel
        self._rows: dict[str, _PetRow] = {}
        self._sel: str | None = None

    # ---------------- Treeview 兼容子集 ----------------
    def insert(self, _parent, _index, iid, values) -> None:
        old = self._rows.pop(iid, None)
        if old is not None:
            old.destroy()
        row = _PetRow(self, self._panel, iid, values)
        row.pack(fill="x")
        self._rows[iid] = row
        if self._sel == iid:
            row.set_selected(True)

    def delete(self, *iids) -> None:
        for pid in iids:
            row = self._rows.pop(pid, None)
            if row is not None:
                row.destroy()
            if self._sel == pid:
                self._sel = None

    def get_children(self, _item=None) -> tuple[str, ...]:
        return tuple(self._rows)

    def selection(self) -> tuple[str, ...]:
        return (self._sel,) if self._sel is not None else ()

    def selection_set(self, iids) -> None:
        pid = iids[0] if iids else None
        if pid is not None and pid not in self._rows:
            return
        self._sel = pid
        for p, row in self._rows.items():
            row.set_selected(p == pid)
        self._panel._on_select()

    # ---------------- 扩展(2Hz 摘要刷新) ----------------
    def update_row(self, pid: str, name: str, summary: str) -> None:
        row = self._rows.get(pid)
        if row is not None:
            row.update_summary(name, summary)


class ControlPanel:
    """控制面板。对外仅暴露 App 依赖的四个方法:refresh_pets/set_feeding/hide/tick。"""

    def __init__(self, app) -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("NeuroPet 控制面板")
        self.win.attributes("-topmost", True)
        self.win.geometry("600x768+60+80")
        self.win.minsize(520, 640)   # 最小尺寸下各页滚动骨架保证零截断
        self.win.configure(background=T.PAPER)

        # ---- 设计系统装配(panel_theme):调色令牌 → ttk 样式 + 选项库 ----
        # 投喂复选框的常规/告急两态(Feed / FeedUrgent.TCheckbutton)是面板
        # 按剩余秒数切换的**冻结样式名**,在 panel_theme 里一并定义。
        T.apply_theme(ttk.Style(self.win))
        T.apply_option_db(self.win)

        # ---- 运行时状态 ----
        self._stat_acc = 1.0          # 6Hz 节流累加器(个体条 + 「看它」读数)
        self._slow_acc = 1.0          # 2Hz 节流累加器(其余页慢变读数)
        self._feed_txt_acc = 1.0      # 投喂倒计时节流累加器(0.25s,首帧立即)
        self._fps_n = 0               # FPS 估算:帧计数 / 累计 dt
        self._fps_t = 0.0
        self._fps = 0.0
        # 情绪条插值状态(A2):_emo_tgt = 6Hz 采样目标,_emo_cur = 每帧插值现值
        self._emo_tgt: dict[str, float] = {k: 0.0 for k, _zh, _c in EMOTIONS}
        self._emo_cur: dict[str, float] = dict(self._emo_tgt)
        self._page_key = ""           # 当前页键(切页判定:插值/刷新的开关)
        self._mem_open = False        # 「记住的事」抽屉初始收起(零滚动预算)
        # 记忆时间线状态(r25 A3):签名 = 上次渲染的输入,未变则不重绘
        self._mem_sig: tuple | None = None
        self.mem_rows = 0             # 上次渲染的**内容行数**(不含分区标题)
        self.mem_refresh_ms = 0.0     # 上次刷新实耗时(ms,含解析;判据 ④ 读数)
        self.mem_render_n = 0         # 真渲染次数(判据 ④:证路径活着)

        # ---- 第十轮:面板 tooltip(复用单例) / 页容器 / 食物选择器 ----
        self._tips = _Tooltip(self.win)
        self.pages: dict[str, _ScrollPage] = {}      # 页标题 → 滚动容器
        self.on_drop_request = None                  # 注册点:callable(kind)->None
        self._food_kind: str = ""                    # selected_food_kind 真源
        self._food_btns: dict[str, ttk.Button] = {}
        self._food_imgs: dict[str, tuple[tk.PhotoImage, tk.PhotoImage]] = {}
        self._catalog: tuple = food_catalog()        # (kind,label,color,effect)

        # ---- 构建界面 ----
        # 版式骨架(r25 A1):常驻个体条在页签**之上**、win 之内;页签容器
        # 占剩余空间。条先建 → 其内的 tree/冻结勾选/投喂按钮在任何页可达。
        self._drawer_open = False                    # 食物抽屉初始收起
        self._build_pet_bar()
        tk.Frame(self.win, height=1, background=T.LINE).pack(fill="x", padx=6)
        self.nb = ttk.Notebook(self.win)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 4))
        # 四意图页:插入序 = 页签顺序 = `panel.pages` 键序(测试面)
        self._build_look_page()
        self._build_play_page()
        self._build_care_page()
        self._build_system_page()
        self.nb.select(0)             # 默认页 = 「看它」(打开即见状态)

        self.refresh_pets()
        # 初始默认选中第一只宠物,便于直接观察状态
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set((kids[0],))
        # 切页只影响「进入侧刷新」(插值/读数的开关在 tick 内按当前页判定,
        # 不依赖本事件是否已派发 → 未 update 的切页也不会误画)
        self._page_key = next(iter(self.pages))
        self.nb.bind("<<NotebookTabChanged>>", self._on_page_changed)
        self._emo_snap()              # 首屏情绪条立即出数(不等 6Hz 采样)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)   # 关闭=隐藏,不退出程序

    # ================================================================
    # 版式骨架:卡片 / 分区标题 / 说明 / 读数
    # ================================================================
    # 设计约定:面板靠"留白 + 一条分隔线"分层级,不用 ttk 的浮雕边框
    # (Labelframe raised 边框是 9x 时代的观感)。每张卡片 = 一个主题,
    # 标题是卡片内第一行的粗体标签,上方一条 1px 线。

    def _card(self, parent, title: str = "") -> tuple[ttk.Frame, ttk.Frame]:
        """一张卡片:返回 (卡片外框, 内容区)。标题为空则不画标题行。

        卡片 = 白底 + 1px 分隔线;标题用次级字色 + 粗体,靠字号与颜色
        建立层级,不加边框、不加背景块。
        """
        outer = ttk.Frame(parent, style="Page.TFrame")
        outer.pack(fill="x", pady=(0, 10))
        if title:
            head = ttk.Frame(outer, style="Page.TFrame")
            head.pack(fill="x", pady=(0, 3))
            ttk.Label(head, text=title, style="Section.TLabel").pack(side="left")
            ttk.Frame(outer, height=1, style="Card.TFrame").pack(fill="x")
        body = ttk.Frame(outer, style="Card.TFrame", padding=(10, 8))
        body.pack(fill="x")
        return outer, body

    @staticmethod
    def _hint(parent, text: str, card: bool = False, wrap: int = 470) -> ttk.Label:
        """卡片内/外的说明文字(次级字色 + 小字号 + 自动换行)。"""
        lbl = ttk.Label(parent, text=text,
                        style="CardHint.TLabel" if card else "Hint.TLabel",
                        wraplength=wrap, justify="left")
        return lbl

    @staticmethod
    def _reading(parent, var: tk.StringVar, card: bool = True,
                 width: int = 0) -> ttk.Label:
        """仪表读数标签(数值大一号;供状态页的量值列)。"""
        lbl = ttk.Label(parent, textvariable=var, style="Reading.TLabel")
        if width:
            lbl.configure(width=width)
        return lbl

    # ================================================================
    # 常驻个体条 PetBar(r25 A1)
    # ================================================================
    def _build_pet_bar(self) -> None:
        """常驻个体条:宠物行列表 + 三个高频动作 + 食物抽屉触发器。

        位置在页签之上、`self.win` 之内(`pack(fill="x")`,不随页签切换),
        因此选宠/投喂/冻结/隐藏在**任何页**都是 1 点击可达。条内三段:
          (a) `self.tree`(_PetList,与旧宠物页**同一对象**,测试面不变)
          (b) 动作区:`feed_now_btn`(Accent 主操作,= 现有
              `_drop_selected_food`)/ `freeze_check` / `hide_btn` /
              `hidden_count_var`
          (c) 抽屉触发器 `food_drawer_btn`(「选食物 ▾/▴」)+ 已选食物读数
        食物抽屉是**页内可折叠区**(`drawer_body` 的 pack_forget/pack),
        不用 Toplevel:避免多窗管理与测试面膨胀;默认收起。
        """
        self.bar = ttk.Frame(self.win, style="PetBar.TFrame", padding=(6, 4))
        self.bar.pack(fill="x", padx=6, pady=(6, 0))

        row = ttk.Frame(self.bar, style="PetBar.TFrame")
        row.pack(fill="x")

        # ---- (b)+(c) 动作区:右侧三行 ----
        # 三行而非一行是**宽度约束**逼出来的:520px 最小窗下 522-6*2 的内容宽
        # 分给"宠物行(实测需 288px:名称+物种+行为·模式·饱食+i)"与单行动作区
        # (实测需 275px)会差 ~80px → Tk 把有 weight 的名称列挤成一个字。
        # 拆三行后动作区 ≈163px,宠物行拿满 325px(渲染验证:名称完整不截)。
        act = ttk.Frame(row, style="PetBar.TFrame")
        act.pack(side="right", anchor="n")
        # 行1:主操作(投喂)+ 冻结(最高频的两个写入动作)
        act_r1 = ttk.Frame(act, style="PetBar.TFrame")
        act_r1.pack(fill="x")
        self.feed_now_btn = ttk.Button(act_r1, text="投喂", width=8,
                                       style="Accent.TButton",
                                       command=self._drop_selected_food)
        self.feed_now_btn.pack(side="left")
        self.freeze_var = tk.BooleanVar(value=False)
        self.freeze_check = ttk.Checkbutton(
            act_r1, text="冻结", variable=self.freeze_var,
            style="PetBar.TCheckbutton", command=self.toggle_freeze)
        self.freeze_check.pack(side="left", padx=(6, 0))
        # 行2:隐藏 + 「隐藏中:N」徽章(隐藏列表本体留在「隐藏与召回」区)
        act_r2 = ttk.Frame(act, style="PetBar.TFrame")
        act_r2.pack(fill="x", pady=(4, 0))
        self.hide_btn = ttk.Button(act_r2, text="隐藏", width=6,
                                   command=self.hide_selected)
        self.hide_btn.pack(side="left")
        self.hidden_count_var = tk.StringVar(value="隐藏中:0 只")
        ttk.Label(act_r2, textvariable=self.hidden_count_var,
                  style="PetBar.TLabel").pack(side="left", padx=(8, 0))
        # 行3:「选食物 ▾/▴」抽屉触发器 + 当前已选食物(读数,原选择器底部那行)
        act_r3 = ttk.Frame(act, style="PetBar.TFrame")
        act_r3.pack(fill="x", pady=(4, 0))
        self.food_drawer_btn = ttk.Button(act_r3, text="选食物 ▾",
                                          style="Drawer.TButton",
                                          command=self._toggle_food_drawer)
        self.food_drawer_btn.pack(side="left")
        self._food_var = tk.StringVar(value="")
        ttk.Label(act_r3, textvariable=self._food_var,
                  style="CardHint.TLabel").pack(side="left", padx=(8, 0))

        # ---- (a) 宠物行列表(与旧宠物页同一 _PetList 对象)+ 行选择说明 ----
        col = ttk.Frame(row, style="PetBar.TFrame")
        col.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.tree = _PetList(col, panel=self)
        self.tree.pack(fill="both", expand=True)
        ttk.Label(col, text="点一行选中;悬停 i 看它的情绪与引导",
                  style="CardHint.TLabel").pack(anchor="w", pady=(2, 0))

        # ---- 食物抽屉(默认收起;内容 = 原食物选择器,契约零改动) ----
        self.drawer_body = ttk.Frame(self.bar, style="PetBar.TFrame")
        self._build_food_selector(self.drawer_body)

    def _toggle_food_drawer(self) -> None:
        """「选食物 ▾/▴」:页内可折叠区显隐(非 Toplevel),默认收起。"""
        if self._drawer_open:
            self.drawer_body.pack_forget()
        else:
            self.drawer_body.pack(fill="x", pady=(6, 0))
        self._drawer_open = not self._drawer_open
        self.food_drawer_btn.configure(
            text="选食物 ▴" if self._drawer_open else "选食物 ▾")

    # ================================================================
    # 「养它」页(原「宠物」页的低频写入项:A.3 归属表 2/3/5/6 + 危险操作)
    # ================================================================
    def _build_care_page(self) -> None:
        page = _ScrollPage(self.nb)
        self.pages["养它"] = page
        self.nb.add(page, text=" 养它 ")
        f = page.body
        # 注:宠物列表 / 冻结 / 单次投喂 / 选食物 已升格进常驻个体条
        # (见 _build_pet_bar),本页只留"改变它是什么"的低频写入项。

        # -- 添加 / 移除 --
        _box_a, b = self._card(f, "增删")
        row = ttk.Frame(b, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="物种", style="CardHint.TLabel").pack(side="left")
        self.spec_var = tk.StringVar()
        self.spec_box = ttk.Combobox(row, textvariable=self.spec_var, width=16,
                                     state="readonly")
        self.spec_box.pack(side="left", padx=(6, 8))
        ttk.Button(row, text="添加", width=6, command=self.add_pet).pack(side="left")
        ttk.Button(row, text="移除选中", command=self.remove_pet).pack(side="left", padx=6)

        # -- 隐藏 / 召回(§6:隐藏整包保留记忆,召回原样恢复) --
        _box_h, box_h = self._card(f, "隐藏与召回")
        hrow = ttk.Frame(box_h, style="Card.TFrame")
        hrow.pack(fill="x")
        ttk.Button(hrow, text="隐藏选中宠物", command=self.hide_selected).pack(side="left")
        ttk.Button(hrow, text="召回选中", command=self.recall_selected).pack(
            side="left", padx=(6, 0))
        ttk.Button(hrow, text="全部召回", command=self.recall_all).pack(
            side="left", padx=(6, 0))
        # hidden_count_var 由常驻个体条创建(这里是同一 StringVar 的第二个
        # 读数位:条上给"现在有几只藏着",本卡给"召回"动作上下文)
        ttk.Label(hrow, textvariable=self.hidden_count_var,
                  style="CardHint.TLabel").pack(side="left", padx=(8, 0))
        self.hidden_list = tk.Listbox(box_h, height=3, font=FONT,
                                      exportselection=False, relief="flat",
                                      background=CARD_BG, foreground=T.INK,
                                      highlightthickness=1,
                                      highlightbackground=T.LINE,
                                      highlightcolor=T.ACCENT,
                                      selectbackground=T.ACCENT_TINT,
                                      selectforeground=T.ACCENT_INK,
                                      activestyle="none")
        self.hidden_list.pack(fill="x", pady=(6, 5))
        self._hint(box_h, "隐藏期间宠物暂离屏幕:不模拟、不渲染、不占宠物名额;"
                          "情绪、记忆与学会的本领都原样保留,召回后无缝继续。",
                   card=True).pack(anchor="w")

        # -- 智能等级滑杆已删(r25 A4)--
        # 原「智能等级」1..5 滑杆作用于记忆容量 / 学习增益 / 探索-温度三类
        # 长期统计量,玩家在看得见的时间尺度上无法归因(架构 §1.3),用户判为
        # 冗余。等级改由经历自适应派生(brain/mastery.py + app._mastery_beat),
        # 读数落在「看它」页的熟练度行;插件协议方法 app.set_intelligence 与
        # tests 里的 FakeApp 桩保留(接口存在性证据)。

        # -- 体型缩放(AG5:五档下拉,对选中宠物即改即生效) --
        _box_s, box_s = self._card(f, "体型缩放")
        srow = ttk.Frame(box_s, style="Card.TFrame")
        srow.pack(fill="x")
        ttk.Label(srow, text="档位", style="CardHint.TLabel").pack(side="left")
        self.scale_var = tk.StringVar(value="1×")
        self.scale_box = ttk.Combobox(srow, textvariable=self.scale_var,
                                      values=SCALE_LABELS, width=6, state="readonly")
        self.scale_box.pack(side="left", padx=(6, 10))
        self.scale_box.bind("<<ComboboxSelected>>", self.on_scale)
        self.s_scale_now = ttk.Label(srow, text="当前 -", style="Reading.TLabel")
        self.s_scale_now.pack(side="left")
        self._hint(box_s, "腿长/体节/触角整体等比缩放,速度按体长换算(行为节奏不变);"
                          "档位越大内存占用越高。",
                   card=True).pack(anchor="w", pady=(4, 0))

        # -- 恢复原形:清除变身效果(体型档与记忆不动) --
        _box_r, box_r = self._card(f, "恢复原形")
        ttk.Button(box_r, text="恢复原形", command=self.reset_avatar).pack(anchor="w")
        self._hint(box_r, "清除变身效果(Q 版/虹色/限时增益);体型档与记忆不动。",
                   card=True).pack(anchor="w", pady=(4, 0))

        # -- 危险操作(A.3:不可逆操作隔离在页底 + 二次确认) --
        _box_d, box_d = self._card(f, "危险操作")
        drow = ttk.Frame(box_d, style="Card.TFrame")
        drow.pack(fill="x")
        ttk.Button(drow, text="清除选中宠物记忆", style="Danger.TButton",
                   command=self.clear_memory).pack(side="left")
        self._hint(drow, "清除是唯一会删除记忆的操作").pack(side="left", padx=(8, 0))

        self._hint(f, "提示:增删/隐藏/行为等操作均针对常驻条中选中的宠物。"
                  ).pack(anchor="w", pady=(0, 2))

    # ---------------- 食物选择器(第十轮,缺陷 #6) ----------------
    def _build_food_selector(self, f) -> None:
        """食物选择器:每种食物一个色块按钮 + 悬停效果 tooltip,选中态高亮。

        暴露 selected_food_kind 属性与 on_drop_request 注册点
        (callable(kind: str) -> None),供 App 接到桌面投喂;点「投喂」
        以当前选中 kind 回调。原「投喂模式」勾选(全局钩子流)语义不变。

        r25 A1:整个选择器连同 `_catalog/_food_btns/FOOD_COLS/`样式名与
        回调**原样搬进常驻条的食物抽屉**(`drawer_body`);原底部那行
        「投喂」主按钮与「已选:X」读数移到抽屉**外**的条上——投喂必须
        在抽屉收起态也 1 点击可达(它们不参与选择器本体)。
        """
        self._hint(f, "先选一种食物(悬停看效果),再点「投喂」。",
                   card=True).pack(anchor="w", pady=(6, 5))
        grid = ttk.Frame(f, style="Card.TFrame")
        grid.pack(fill="x")
        for i, (kind, label, color, effect) in enumerate(self._catalog):
            r, c = divmod(i, FOOD_COLS)
            img_n, img_s = self._chip(color, False), self._chip(color, True)
            self._food_imgs[kind] = (img_n, img_s)
            btn = ttk.Button(grid, text=label, image=img_n, compound="left",
                             style="Food.TButton",
                             command=lambda k=kind: self._select_food(k))
            btn.grid(row=r, column=c, sticky="w", padx=(0, 6), pady=2)
            tip_txt = f"{label}:{effect}"
            btn.bind("<Enter>", lambda e, t=tip_txt, w=btn:
                     self._tips.show(t, w.winfo_rootx(),
                                     w.winfo_rooty() + w.winfo_height() + 4))
            btn.bind("<Leave>", lambda _e: self._tips.hide())
            self._food_btns[kind] = btn
        self._select_food(self._catalog[0][0])   # 默认 = FOODS 第一种

    @staticmethod
    def _chip(color: str, selected: bool) -> tk.PhotoImage:
        """食物色块(14×14 PhotoImage):选中态换深色描边。调用方持引用防 GC。"""
        img = tk.PhotoImage(width=14, height=14)
        border = "#1b1b1b" if selected else "#c9ccd1"
        for a, b, c, d in ((0, 0, 14, 2), (0, 12, 14, 14),
                           (0, 0, 2, 14), (12, 0, 14, 14)):
            img.put(border, to=(a, b, c, d))
        img.put(color, to=(2, 2, 12, 12))
        return img

    def _select_food(self, kind: str) -> None:
        """选中一种食物:高亮按钮(描边色块+加粗+pressed 态)并更新属性。"""
        if kind not in self._food_btns:
            return
        self._food_kind = str(kind)
        for k, btn in self._food_btns.items():
            sel = (k == kind)
            btn.configure(image=self._food_imgs[k][1 if sel else 0],
                          style="FoodSel.TButton" if sel else "Food.TButton")
            try:
                btn.state(["pressed"] if sel else ["!pressed"])
            except tk.TclError:
                pass
        label = next(l for k, l, _c, _e in self._catalog if k == kind)
        self._food_var.set(f"已选:{label}")

    @property
    def selected_food_kind(self) -> str:
        """当前选中的食物 kind(默认 = FOODS 第一种;供 App 桌面投喂读)。"""
        return self._food_kind

    @selected_food_kind.setter
    def selected_food_kind(self, kind: str) -> None:
        self._select_food(str(kind))

    def set_on_drop_request(self, callback) -> None:
        """注册投喂回调:签名 callable(kind: str) -> None;传 None 注销。"""
        self.on_drop_request = callback

    def _drop_selected_food(self) -> None:
        """「投喂」按钮:已注册 on_drop_request 则以选中 kind 回调。

        无选中时**直接返回**(r25 A1 收口):该路径会落进
        `app._on_panel_drop` 的无选中兜底(`app.py:1424`,静默改喂第一只宠,
        界面无任何指示)。App 侧不改,只从面板侧封住;按钮态另由
        `_sync_action_state()` 置 disabled 承担同一语义(双保险)。
        """
        if not self.selected():
            return
        cb = self.on_drop_request
        if callable(cb):
            cb(self._food_kind)

    # ---------------- 面板 tooltip(缺陷 #2:引导迁入面板) ----------------
    def _show_pet_tip(self, pid: str, widget) -> None:
        """悬停宠物行 "i":弹情绪四维+能量+当前行为+引导文案 tooltip。"""
        try:
            st = dict(self.app.pet_status(pid) or {})
        except Exception:
            st = {}
        if not st:                            # pet_status 缺失时兜底组装
            h = self.app.pets.get(pid) if hasattr(self.app, "pets") else None
            if h is None:
                return
            s = h.state
            st = {"name": getattr(s, "name", "") or "-",
                  "activity": getattr(getattr(s, "activity", None),
                                      "value", ""),
                  "stomach": getattr(s, "stomach", 0.0)}
        text = tooltip_text(st)
        self._tips.show(text,
                        widget.winfo_rootx() + widget.winfo_width() + 6,
                        widget.winfo_rooty() - 4)

    def _hide_pet_tip(self) -> None:
        self._tips.hide()

    # ================================================================
    # 「逗它」页(原「行为」页 + 原「情境演示」页:A.3 归属表 8/10/9/11)
    # ================================================================
    def _build_play_page(self) -> None:
        page = _ScrollPage(self.nb)
        self.pages["逗它"] = page
        self.nb.add(page, text=" 逗它 ")
        f = page.body

        _box, box = self._card(f, "立即触发")
        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(anchor="w")
        self.beh_var = tk.StringVar(value=BEHAVIORS[0])
        ttk.Combobox(row, textvariable=self.beh_var, values=BEHAVIORS,
                     width=12, state="readonly").pack(side="left")
        ttk.Button(row, text="触发", style="Accent.TButton",
                   command=self.trigger).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="逃逸测试", command=self.escape_test).pack(side="left", padx=8)
        self.beh_hint = self._hint(
            box, "未选中宠物时操作无效,请先在顶部常驻条点选一只。", card=True)
        self.beh_hint.pack(anchor="w", pady=(5, 0))

        # -- 联想学习演示(原「情境演示」页;A.3 #10) --
        _box2, box2 = self._card(f, "联想学习演示")
        row2 = ttk.Frame(box2, style="Card.TFrame")
        row2.pack(anchor="w", pady=(0, 4))
        ttk.Button(row2, text="① 讲述:雪景很美",
                   command=lambda: self.app.tell_story("snow_beautiful")).pack(side="left")
        ttk.Button(row2, text="② 在光标处放置寒冷区域",
                   command=lambda: self.app.place_cold_zone()).pack(side="left", padx=8)
        ttk.Button(box2, text="③ 清除演示区域(不移除食物)",
                   command=self.clear_zones).pack(anchor="w")

        _box2, box2 = self._card(f, "各行为含义")
        txt = tk.Text(box2, height=13, wrap="word", relief="flat",
                      background=CARD_BG, foreground=T.INK, font=FONT,
                      highlightthickness=0, padx=2, pady=2)
        txt.insert("1.0", BEHAVIOR_HELP)
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

        # -- 怎么玩(A.3 #11;A5 将改为「怎么玩 ▾」按需展开) --
        _guide, guide = self._card(f, "怎么玩")
        txt = tk.Text(guide, height=12, wrap="word", relief="flat",
                      background=CARD_BG, foreground=T.INK, font=FONT,
                      highlightthickness=0, padx=2, pady=2)
        txt.insert("1.0", (
            "先讲故事,再放冷区——宠物会主动趋近寒冷:\n"
            "① 点「讲述:雪景很美」:给所有宠物注入『寒冷=美好』的\n"
            "    正效价联想(语义先验覆写本能里的负先验);\n"
            "② 点「在光标处放置寒冷区域」:以当前光标为圆心生成\n"
            "    半径约 220px 的冷区(屏幕上显示为蓝色虚线圆 ❄);\n"
            "③ 观察:即使不饿,宠物也会因『期待』进入冷区停留。\n"
            "\n"
            "对照实验:重启后(或清除记忆后)不放故事直接放冷区,\n"
            "寒冷的出厂先验为负(僵硬/回避),宠物会绕开冷区。\n"
            "多次讲述同一个故事会增强联想置信度;「清除演示区域」\n"
            "只移除冷区,食物与宠物记忆不受影响。"))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)

    # ================================================================
    # 「看它」页(默认页;原「状态」页选中个体部分 + 原「记忆」页)
    # ================================================================
    def _build_look_page(self) -> None:
        page = _ScrollPage(self.nb)
        self.pages["看它"] = page
        self.nb.add(page, text=" 看它 ")
        f = page.body

        # -- 选中宠物的基础信息(A.3 #12:首屏第一块) --
        self.s_name = tk.StringVar(value="(未选择宠物)")
        self.s_beh = tk.StringVar(value="-")
        self.s_mode = tk.StringVar(value="-")
        self.s_pos = tk.StringVar(value="-")
        self.s_stomach = tk.StringVar(value="-")
        self.s_flags = tk.StringVar(value="-")
        self.s_mem = tk.StringVar(value="-")
        self.s_scale = tk.StringVar(value="-")
        self.s_learn = tk.StringVar(value="-")   # 在线学习状态(各学习器/最近偏好)
        # 读数网格:左列标签(次级字色)+ 右列数值(Reading 样式,大一档)。
        # 数值放大是这个面板唯一"排版上的奢">——读者是来看数的。
        _box0, box0 = self._card(f, "选中宠物")
        grid = ttk.Frame(box0, style="Card.TFrame")
        grid.pack(fill="x")
        for i, (label, var) in enumerate((
                ("名称", self.s_name), ("当前行为", self.s_beh),
                ("运动模式", self.s_mode), ("位置", self.s_pos),
                ("饱食度", self.s_stomach), ("状态标记", self.s_flags),
                ("记忆条数", self.s_mem), ("体型档位", self.s_scale))):
            r, c = divmod(i, 2)
            ttk.Label(grid, text=label, style="CardHint.TLabel").grid(
                row=r, column=c * 2, sticky="w", padx=(0, 8), pady=2)
            self._reading(grid, var).grid(
                row=r, column=c * 2 + 1, sticky="w", padx=(0, 20), pady=2)
        # 学习状态(在线自学习)一行较长:单独横跨整行,复用同一网格布局
        ttk.Label(grid, text="学习状态", style="CardHint.TLabel").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Label(grid, textvariable=self.s_learn,
                  style="CardHint.TLabel").grid(
            row=4, column=1, columnspan=3, sticky="w", pady=2)
        self.stomach_bar = ttk.Progressbar(box0, maximum=1.0, length=200)
        self.stomach_bar.pack(anchor="w", pady=(6, 0))

        # -- 七维情绪横条(canvas 绘制) --
        _box_e, box_e = self._card(f, "七维情绪")
        self.emo_card = _box_e     # 首屏必见区下界(§14/§16.1 判据的测量锚点)
        self.emo_canvas = tk.Canvas(box_e, width=386,
                                    height=len(EMOTIONS) * ROW_H + 8,
                                    highlightthickness=0, background=CARD_BG)
        self.emo_canvas.pack(anchor="w")
        # 静态元素:左侧中文标签 + 效价行的中性刻度线
        for i, (_key, zh, _color) in enumerate(EMOTIONS):
            y = 6 + i * ROW_H + BAR_H // 2
            self.emo_canvas.create_text(6, y, text=zh, anchor="w", font=FONT_SMALL,
                                        fill=T.INK_SOFT)
        mid_x = (BAR_X0 + BAR_X1) / 2
        vy = 6 + 5 * ROW_H   # 效价(第 6 行)的 y 顶部
        self.emo_canvas.create_line(mid_x, vy - 2, mid_x, vy + BAR_H + 2,
                                    fill=EMO_TRACK_LINE)

        # -- 熟练度(r25 A4:删掉「智能等级」滑杆后的**可感知**读数) --
        # 位置:情绪卡与记忆区之间(A2 已按架构预留此位)。等级不再是用户
        # 拧的滑杆,而是从经历统计里长出来(app.mastery_of 唯一数据源);
        # 显示区在折叠线以下属预期(架构 §14:熟练度读数行允许在折叠线下)。
        _box_mastery, box_mastery = self._card(f, "熟练度")
        self.s_mastery = tk.StringVar(value="-")
        self.mastery_lbl = ttk.Label(box_mastery, textvariable=self.s_mastery,
                                     style="Reading.TLabel")
        self.mastery_lbl.pack(anchor="w")
        self._hint(box_mastery, "它是学出来的,不是拧出来的:活得久、被投喂、"
                                "记得多、信任高,等级自己往上走。",
                   card=True).pack(anchor="w", pady=(4, 0))
        _mtip = ("它学得多快、记得多牢:等级 = 年龄 + 投喂次数 + 记忆条数 + "
                 "信任 + 经历。想让它更熟练,多陪它一会。")
        self.mastery_lbl.bind("<Enter>", lambda e, t=_mtip, w=self.mastery_lbl:
                              self._tips.show(t, w.winfo_rootx(),
                                              w.winfo_rooty() + w.winfo_height() + 4))
        self.mastery_lbl.bind("<Leave>", lambda _e: self._tips.hide())

        # -- 记住的事(原「记忆」页裸 Text;A3 = 记忆时间线) --
        self._build_memory_timeline(f)

    def _build_memory_timeline(self, f) -> None:
        """「记住的事」抽屉 = **记忆时间线**(A3;取代裸文本转储)。

        控件仍是 `self.mem_txt`(冻结名:`test_panel_ia` 的 LEGACY_NAMES
        hasattr 清单 + AST 判据),改的是**渲染方式**:分区 + 时间降序 +
        显著度条(见模块级 `parse_memory_lines`/`render_memory_timeline`)。

        默认折叠 = A2 验收 ③(首屏必见区)的降级②:正文 424px 高放不进
        477px 预算,故沿用 A5 的抽屉显隐模式(展开态 ≈200px 起)。
        """
        mem_outer = ttk.Frame(f, style="Page.TFrame")
        mem_outer.pack(fill="x", pady=(0, 10))
        mrow = ttk.Frame(mem_outer, style="Page.TFrame")
        mrow.pack(fill="x")
        self.mem_drawer_btn = ttk.Button(mrow, text="记住的事 ▾",
                                         style="Drawer.TButton",
                                         command=self._toggle_memory_drawer)
        self.mem_drawer_btn.pack(side="left")
        ttk.Button(mrow, text="刷新显示",
                   command=self.refresh_memory).pack(side="left", padx=(6, 0))
        self.mem_body = ttk.Frame(mem_outer, style="Page.TFrame")
        frame = ttk.Frame(self.mem_body)
        frame.pack(fill="both", expand=True)
        self.mem_txt = tk.Text(frame, wrap="word", relief="flat",
                               background=CARD_BG, foreground=T.INK, font=FONT,
                               height=MEM_TEXT_H,
                               highlightthickness=1,
                               highlightbackground=T.LINE,
                               highlightcolor=T.ACCENT, padx=8, pady=6)
        sb = ttk.Scrollbar(frame, command=self.mem_txt.yview)
        self.mem_txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.mem_txt.pack(side="left", fill="both", expand=True)
        # 分区着色(tag 名 = 分区键前两字母;`head` = 分区标题,属装饰行)
        self.mem_txt.tag_config("head", foreground=T.INK_SOFT, font=FONT_BOLD)
        self.mem_txt.tag_config("ep", foreground=T.INK)
        self.mem_txt.tag_config("as", foreground=T.ACCENT_INK)
        self.mem_txt.tag_config("st", foreground=T.INK_FAINT)
        self.mem_txt.tag_config("un", foreground=T.ALERT)
        self.mem_txt.configure(state="disabled")
        self.refresh_memory()

    def _toggle_memory_drawer(self) -> None:
        """「记住的事 ▾/▴」:页内可折叠区显隐(非 Toplevel),默认收起。"""
        if self._mem_open:
            self.mem_body.pack_forget()
        else:
            self.mem_body.pack(fill="x", pady=(6, 0))
            self.refresh_memory()        # 展开即出最新内容
        self._mem_open = not self._mem_open
        self.mem_drawer_btn.configure(
            text="记住的事 ▴" if self._mem_open else "记住的事 ▾")

    # ---------------- 情绪条:6Hz 采样 + 每帧插值(A.2 信息节奏分档) ----
    def _emo_snap(self) -> None:
        """把插值现值对齐到最新采样值并立即重画(切页进入 / 换选中个体)。

        换个体/切页都**不做**动画过渡:那是"跳到另一组数",不是同一组数的
        连续变化——插值只用于把 6Hz 采样抹平成 ≥50Hz 的视觉连续。
        """
        self._emo_cur = dict(self._emo_tgt)
        # 按**查 notebook 得到的当前页**判定,不用缓存 `_page_key`(r25 A4 收口,
        # 架构 §16.2):`nb.select()` 后若 `<<NotebookTabChanged>>` 尚未派发,
        # 缓存就是陈旧的 —— 切走的那一刻会在 `_on_select` 里向**非当前页**
        # canvas 写入(程序可达、用户无感),切回时又会漏画。
        if self._current_page_key() == "看它":
            self._draw_emotions(self._emo_cur)

    def _emo_step(self, dt: float, key: str) -> None:
        """每帧:一阶插值 + 重画(仅「看它」页;其余页/隐藏态不动 canvas)。

        alpha = 1 - exp(-dt/τ) 按 dt 归一:帧档从 60fps 掉到 30fps 时,
        每帧步长只按实际时长放大,不会出现"切档跳变"(架构 §2 A.2)。
        """
        if key != "看它" or dt <= 0.0:
            return
        a = 1.0 - math.exp(-dt / EMO_TAU)
        cur = self._emo_cur
        for k, tgt in self._emo_tgt.items():
            cur[k] = cur.get(k, 0.0) + (tgt - cur.get(k, 0.0)) * a
        self._draw_emotions(cur)

    def _draw_emotions(self, emo: dict[str, float]) -> None:
        """重绘七维情绪条形(动态项统一 tag='dyn',先删后画)。"""
        c = self.emo_canvas
        c.delete("dyn")
        for i, (key, _zh, color) in enumerate(EMOTIONS):
            v = float(emo.get(key, 0.0))
            # valence ∈ [-1,1] 映射到 [0,1] 显示(中线=中性)
            shown = (v + 1.0) / 2.0 if key == "valence" else max(0.0, min(1.0, v))
            y = 6 + i * ROW_H
            c.create_rectangle(BAR_X0, y, BAR_X1, y + BAR_H,
                               outline=EMO_TRACK_LINE, fill=TRACK, tags="dyn")
            w = (BAR_X1 - BAR_X0) * shown
            if w > 1:
                c.create_rectangle(BAR_X0, y, BAR_X0 + w, y + BAR_H,
                                   outline="", fill=color, tags="dyn")
            c.create_text(BAR_X1 + 8, y + BAR_H // 2, anchor="w", font=FONT_SMALL,
                          text=f"{v:+.2f}", fill=T.INK_SOFT, tags="dyn")

    def refresh_memory(self) -> None:
        """把选中宠物的记忆摘要渲染成**记忆时间线**(r25 A3;方法名冻结)。

        口径(架构 §2 A3 + 主控裁定 1/2):

        - 情景行(两种格式都收)→ 按时间戳**降序**排的时间线主体;
        - 状态行(`[NN]`/`[GF]`)/ 联想行 → 各自归区(不参与时间排序);
        - 解析失败行 → **原样显示**在"未识别"区,不抛异常;
        - 内容未变(签名相同)→ **不重绘**:保住用户的滚动位置/选区,
          也把 2Hz 路径的常态成本降到一次元组比较。

        计时走 `self.mem_refresh_ms`(本次刷新的实耗时,ms;判据 ④ 的读数)
        与 `instr.observe`(默认关,零成本)。取数失败 → 空态降级,不抛到 tick。
        """
        pid = self.selected()
        lines: list[str] = []
        if pid:
            try:
                lines = list(self.app.memory_of(pid) or [])
            except Exception:
                lines = []           # 取数失败 → 空态(不把异常抛进 tick)
        sig = (pid, tuple(lines))
        if sig == self._mem_sig:
            return                   # 内容未变:不重绘(滚动位置不跳)
        try:
            if not self.mem_txt.winfo_exists():
                return
        except Exception:
            return                   # 面板已销毁(测试/退出路径):静默降级
        self._mem_sig = sig
        # 计时口径 = **本方法这次真跑的全部工作**(解析 + 渲染 + 写控件):
        # tick 的 2Hz 路径上,内容变化的那一拍就是这个成本(判据 ④)。
        t0 = time.perf_counter()
        sec = parse_memory_lines(lines)
        self.mem_rows = memory_row_count(sec)
        self.mem_txt.configure(state="normal")
        self.mem_txt.delete("1.0", "end")
        if not pid:
            self.mem_txt.insert("1.0", "(未选择宠物:请在顶部常驻条点选一只)")
        elif not lines:
            self.mem_txt.insert("1.0", "(暂无记忆——投喂、讲故事、被抓几次,"
                                       "记忆就会积累起来)")
        else:
            for tag, block in render_memory_timeline(sec):
                self.mem_txt.insert("end", block + "\n", tag)
        self.mem_txt.configure(state="disabled")
        self.mem_refresh_ms = (time.perf_counter() - t0) * 1000.0
        self.mem_render_n += 1
        _I.observe("panel_mem_refresh_ms", self.mem_refresh_ms)

    def clear_memory(self) -> None:
        """清除选中宠物记忆:唯一删除记忆的入口,必须二次确认。"""
        pid = self.selected()
        if not pid:
            return
        if not messagebox.askyesno(
                "清除记忆",
                f"确定清除 {pid} 的全部记忆吗?\n"
                f"情景记忆与语义联想都会删除,宠物恢复出厂本能。\n"
                f"注意:仅此操作会删除记忆;正常退出或移除宠物都会自动保存记忆。",
                parent=self.win):
            return
        self.app.clear_memory(pid)
        self.refresh_memory()
        self._sync_rows()

    # ================================================================
    # 「系统」页(原「关于」页 + 原「状态」页全局块:A.3 16/17/18/14)
    # ================================================================
    def _build_system_page(self) -> None:
        page = _ScrollPage(self.nb)
        self.pages["系统"] = page
        self.nb.add(page, text=" 系统 ")
        f = page.body

        _box, box = self._card(f, "NeuroPet")
        ttk.Label(box, text="神经拟真桌宠", style="Reading.TLabel").pack(anchor="w")
        self._hint(box, "感知→情绪→记忆→决策→运动 分层的桌面昆虫宠物。\n"
                        "版本 0.1(核心插件 API v1)。一切皆插件:物种/大脑/感知器/"
                        "面板/API 五类插件统一注册,新物种不改核心。\n"
                        "技术栈:Python 3.13 + tkinter + Pillow(零第三方运行时依赖)。",
                   card=True).pack(anchor="w", pady=(4, 0))

        _box1, box1 = self._card(f, "已注册插件")
        self.plug_tree = ttk.Treeview(box1,
                                      columns=("id", "name", "type", "version", "source"),
                                      show="headings", height=7, selectmode="browse")
        for cid, txt2, w, anc in (("id", "插件 ID", 150, "w"),
                                  ("name", "名称", 76, "w"),
                                  ("type", "类型", 60, "center"),
                                  ("version", "版本", 44, "center"),
                                  ("source", "来源", 60, "center")):
            self.plug_tree.heading(cid, text=txt2)
            self.plug_tree.column(cid, width=w, anchor=anc, stretch=True)
        self.plug_tree.pack(fill="both", expand=True)
        self._fill_plugins()

        _box2, box2 = self._card(f, "外部插件(plugins/)加载报告")
        self.ext_txt = tk.Text(box2, height=4, wrap="word", relief="flat",
                               background=CARD_BG, foreground=T.INK,
                               font=FONT_SMALL, highlightthickness=1,
                               highlightbackground=T.LINE,
                               highlightcolor=T.ACCENT, padx=6, pady=4)
        self.ext_txt.pack(fill="x")
        self._fill_external_report()

        # -- 投喂模式:全局钩子流(屏幕任意处左键投放食物) --
        # 归页判据(A.3):K1**全局**(整程序一个开关,非选中个体)× K2 低
        # × K3 写 → 「低频 ∧ 全局 → 系统」。单次投喂/食物选择在常驻条上。
        _box_fd, box_fd = self._card(f, "投喂模式")
        self.feed_var = tk.BooleanVar(value=False)
        self.feed_check = ttk.Checkbutton(box_fd, text="投喂模式(已关闭)",
                                          style="Feed.TCheckbutton",
                                          variable=self.feed_var,
                                          command=self.toggle_feeding)
        self.feed_check.pack(anchor="w")
        self.feed_hint = self._hint(
            box_fd, "开启后在屏幕任意处点击左键投放食物;食物有气味,"
                    "宠物饥饿(饱食度 < 45%)时会循味而来。", card=True)
        self.feed_hint.pack(anchor="w", pady=(2, 0))

        # -- 运行状况(原「状态」页全局块,字段不增不减 + 护栏只读行) --
        _box_g, box_g = self._card(f, "运行状况")
        self.g_pets = tk.StringVar(value="-")
        self.g_mem_mb = tk.StringVar(value="-")
        self.g_fps = tk.StringVar(value="-")
        self.g_feed = tk.StringVar(value="关")
        self.g_cache = tk.StringVar(value="-")
        self.g_guard = tk.StringVar(value="-")
        for i, (label, var) in enumerate((
                ("宠物数量", self.g_pets), ("内存工作集", self.g_mem_mb),
                ("帧率估算", self.g_fps), ("投喂模式", self.g_feed),
                ("躯干缓存", self.g_cache), ("内存护栏", self.g_guard))):
            r, c = divmod(i, 2)
            ttk.Label(box_g, text=label, style="CardHint.TLabel").grid(
                row=r, column=c * 2, sticky="w", padx=(0, 8), pady=2)
            self._reading(box_g, var).grid(
                row=r, column=c * 2 + 1, sticky="w", padx=(0, 20), pady=2)

    def _fill_plugins(self) -> None:
        """遍历注册表展示全部已注册插件(内置 + 加载成功的外部)。"""
        self.plug_tree.delete(*self.plug_tree.get_children())
        for m in self.app.registry.manifests():
            self.plug_tree.insert("", "end", values=(
                m.id, m.name, m.type, m.version, m.source))

    def _fill_external_report(self) -> None:
        """外部插件扫描报告:含加载失败的原因(来自 app._external_reports)。"""
        self.ext_txt.configure(state="normal")
        self.ext_txt.delete("1.0", "end")
        reports = getattr(self.app, "_external_reports", [])
        if not reports:
            self.ext_txt.insert("1.0", "(plugins/ 目录为空,没有外部插件)")
        for pid, err in reports:
            self.ext_txt.insert("end", f"[失败] {pid}: {err}\n" if err
                                else f"[已加载] {pid}\n")
        self.ext_txt.configure(state="disabled")

    # ================================================================
    # 选择与列表维护
    # ================================================================
    def selected(self) -> str | None:
        """当前选中宠物的 pet_id;无选中/宠物已不存在时返回 None(空列表保护)。"""
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0] if sel[0] in self.app.pets else None

    def refresh_pets(self) -> None:
        """App 在添加/移除/隐藏/召回宠物后调用:重建列表,保留原选中项。"""
        prev = self.selected()
        self.tree.delete(*self.tree.get_children())
        for pid, h in self.app.pets.items():
            self.tree.insert("", "end", iid=pid, values=(
                pid, h.state.name or "-", self._species_zh(h.state.species_id),
                self._beh_zh(h.state.activity.value),
                self._mode_zh(h.state.mode.value)))
        if prev and prev in self.app.pets:
            self.tree.selection_set((prev,))
        # §6:隐藏中的宠物列表 + 数量展示(隐藏宠不进上方列表,不可选中)
        hidden = self.app.hidden_pets()
        self._hidden_order = list(hidden)       # 行号 → pet_id(召回用)
        self.hidden_list.delete(0, "end")
        for pid, h in hidden.items():
            self.hidden_list.insert("end", f"{pid}({h.state.name or '-'})")
        self.hidden_count_var.set(f"隐藏中:{len(hidden)} 只")
        # 物种下拉:随注册的物种包更新
        specs = [m.id for m in self.app.species_manifests()]
        self.spec_box["values"] = specs
        if specs and self.spec_var.get() not in specs:
            self.spec_var.set(specs[0])
        self._sync_scale_ui()   # AG5:体型下拉随选中宠物实际档位同步
        self._sync_action_state()   # A1 收口:选中被隐藏/移除清空时动作控件随之失效

    def _species_zh(self, species_id: str) -> str:
        """物种展示名:优先物种插件 manifest.name,回退 id 尾段。"""
        try:
            for m in self.app.species_manifests():
                if m.id == species_id:
                    return str(m.name)
        except Exception:
            pass
        return str(species_id).split(".")[-1]

    def _sync_rows(self) -> None:
        """2Hz 轻量刷新:只更新已有行的名称/状态摘要(不重建、不动选中)。"""
        for pid in self.tree.get_children():
            h = self.app.pets.get(pid)
            if not h:
                continue
            try:
                st_pct = f"{float(getattr(h.state, 'stomach', 0.0)):.0%}"
            except Exception:
                st_pct = "-"
            summary = (f"{self._beh_zh(h.state.activity.value)}"
                       f"·{self._mode_zh(h.state.mode.value)}·饱食{st_pct}")
            self.tree.update_row(pid, h.state.name or "-", summary)

    @staticmethod
    def _beh_zh(v: str) -> str:
        return f"{BEHAVIOR_ZH.get(v, v)}"

    @staticmethod
    def _mode_zh(v: str) -> str:
        return MODE_ZH.get(v, v)

    def _on_select(self) -> None:
        """选中变化:动作控件/体型档随宠物实际状态同步;立即刷新读数与记忆。

        情绪条按**换个体 = 跳到另一组数**(非同一组数的连续变化)处理:
        采样后直接 snap,不走插值动画(见 `_emo_snap`)。
        """
        self._sync_action_state()
        self._sync_scale_ui()
        self._refresh_readings()
        self._refresh_globals()
        self.refresh_memory()
        self._emo_snap()

    def _sync_action_state(self) -> None:
        """常驻条三个动作控件同步到**当前选中**(r25 A1 收口,黑盒报告问题 1)。

        契约:**无选中时不得处于误导态**——投喂/冻结/隐藏一律 `disabled`,
        冻结勾选归 `unchecked`;有选中时勾选 = 该宠实际 `state.frozen`。
        否则「无选中却显示冻结中」会被读成全局状态,且「投喂」会命中
        `app._on_panel_drop` 的无选中兜底(`app.py:1424`,静默改喂第一只宠)。

        三条调用路径:选中变化(`_on_select`)、列表重建(`refresh_pets` 末尾,
        覆盖"选中宠被隐藏/移除"这一旧缺陷路径)、2Hz `tick`(覆盖不经选中的
        状态漂移)。只读 App 状态、只写控件态:`BooleanVar.set` 不触发
        command,不会反向写回 App(`app.set_frozen` 仍只由用户点击
        `toggle_freeze` 触发)。
        """
        pid = self.selected()
        h = self.app.pets.get(pid) if pid else None
        st = "normal" if h else "disabled"
        for w in (self.feed_now_btn, self.freeze_check, self.hide_btn):
            if str(w.cget("state")) != st:
                w.configure(state=st)
        self.freeze_var.set(bool(h.state.frozen) if h else False)

    # ---------------- 体型缩放(AG5) ----------------
    def _sync_scale_ui(self) -> None:
        """把下拉框与「当前」标签同步到选中宠物的实际档位(不触发 command)。"""
        pid = self.selected()
        h = self.app.pets.get(pid) if pid else None
        if not h:
            self.scale_var.set("1×")
            self.s_scale_now.configure(text="当前 -")
            return
        k = float(self.app.pet_scale(pid))
        label = min(zip(SCALE_VALUES, SCALE_LABELS),
                    key=lambda t: abs(t[0] - k))[1]
        self.scale_var.set(label)
        half = h.body.window_half()
        self.s_scale_now.configure(text=f"当前 {k:g}×(画布 {half * 2}px)")

    def on_scale(self, _event=None) -> None:
        """档位下拉:对选中宠物应用新档(app.set_pet_scale 内部吸附+重建+持久化)。"""
        pid = self.selected()
        if not pid:
            self._sync_scale_ui()
            return
        label = self.scale_var.get()
        k = SCALE_VALUES[SCALE_LABELS.index(label)] if label in SCALE_LABELS else 1.0
        try:
            self.app.set_pet_scale(pid, k)
        except Exception:
            pass                      # 宠物已被移除等竞态:界面下次选中时校正
        self._sync_scale_ui()

    # ================================================================
    # 操作(宠物页)
    # ================================================================
    def add_pet(self) -> None:
        try:
            self.app.add_pet(self.spec_var.get() or "species.cockroach")
        except Exception as exc:   # 例如已达 max_pets 上限
            messagebox.showwarning("添加失败", str(exc), parent=self.win)

    def remove_pet(self) -> None:
        pid = self.selected()
        if pid:
            self.app.remove_pet(pid)

    # ---------------- 隐藏 / 召回(§6) ----------------
    def hide_selected(self) -> None:
        """隐藏选中宠物(记忆整包保留,可随时在下方列表召回)。"""
        pid = self.selected()
        if pid:
            self.app.hide_pet(pid)      # 内部回调 refresh_pets 同步两列表

    def recall_selected(self) -> None:
        """召回隐藏列表中选中的宠物。"""
        sel = self.hidden_list.curselection()
        if sel:
            pids = getattr(self, "_hidden_order", [])
            if sel[0] < len(pids):
                self.app.recall_pet(pids[sel[0]])

    def recall_all(self) -> None:
        """召回全部隐藏宠物(幂等:已召回/不存在的 id 自动跳过)。"""
        for pid in list(self.app.hidden_pets()):
            self.app.recall_pet(pid)

    def reset_avatar(self) -> None:
        '''恢复原形:清除变身效果(Q版/虹色/限时增益),体型档与记忆不动。'''
        pid = self.selected()
        if pid:
            self.app.clear_effects(pid)   # 内部 _save_profile 落盘 avatar.json
            self.refresh_pets()

    def toggle_freeze(self) -> None:
        pid = self.selected()
        if not pid:
            self.freeze_var.set(False)
            return
        self.app.set_frozen(pid, self.freeze_var.get())

    def toggle_feeding(self) -> None:
        ok = self.app.toggle_feeding(self.feed_var.get())
        if not ok:      # 钩子不可用等原因,App 已回调 set_feeding(False)
            self.feed_var.set(False)
            self.feed_hint.configure(
                text="投喂模式开启失败:低级鼠标钩子不可用(详见控制台日志)。")
        else:
            self.feed_hint.configure(
                text="开启后在屏幕任意处点击左键投放食物;食物有气味,"
                     "宠物饥饿(饱食度 < 45%)时会循味而来。")

    # r25 A4:on_intel / _intel_release / _apply_intel 已随「智能等级」滑杆删除
    # (面板不再调用 app.set_intelligence;该方法是插件协议接口,保留在 app 侧)。

    # ================================================================
    # 操作(行为页 / 情境演示页)
    # ================================================================
    def trigger(self) -> None:
        pid = self.selected()
        if pid:
            self.app.trigger_behavior(pid, self.beh_var.get())

    def escape_test(self) -> None:
        pid = self.selected()
        if pid:
            self.app.escape_test(pid)

    def clear_zones(self) -> None:
        """清除全部演示区域(冷区)。

        App 目前没有公开的 clear_zones 方法;按项目规范允许面板在此
        直接访问内部:app.world.clear_zones() + app._static_dirty=True
        (_static_dirty 会让下一渲染帧重绘静态层,屏幕上的冷区圆圈消失)。
        """
        if hasattr(self.app, "clear_zones"):     # 未来 App 提供公开方法时优先使用
            self.app.clear_zones()
        else:
            self.app.world.clear_zones()
            self.app._static_dirty = True

    # ================================================================
    # App 回调的四个接口
    # ================================================================
    def set_feeding(self, on: bool) -> None:
        """App 在钩子开启失败等情况下回调,同步勾选框。"""
        self.feed_var.set(bool(on))

    def _update_feeding_label(self) -> None:
        """投喂复选框实时倒计时(由 tick 以 0.25s 节流调用)。

        - 开启且限时:显示"投喂模式(剩 Ns,面板点击不受影响)",剩 <5s 变红;
        - 开启且不限时(app._feeding_until 为 None):显示"不限时";
        - 关闭:显示"投喂模式(已关闭)"。
        变红经样式切换(FeedUrgent.TCheckbutton),任何主题下兜底只刷文字。
        """
        app = self.app
        if not app.feeding:
            text, style = "投喂模式(已关闭)", "Feed.TCheckbutton"
        else:
            until = getattr(app, "_feeding_until", None)
            if until is None:                 # ttl<=0 配置为不限时
                text, style = "投喂模式(不限时,面板点击不受影响)", "Feed.TCheckbutton"
            else:
                remaining = until - time.perf_counter()
                secs = max(1, math.ceil(remaining))   # 不足 1s 也显示 1,避免"剩 0s"
                style = ("FeedUrgent.TCheckbutton" if remaining < 5.0
                         else "Feed.TCheckbutton")
                text = f"投喂模式(剩 {secs}s,面板点击不受影响)"
        try:
            self.feed_check.configure(text=text, style=style)
        except Exception:                     # 主题异常时至少保证倒计时文字可刷新
            self.feed_check.configure(text=text)

    def hide(self) -> None:
        """关闭按钮 = 隐藏窗口(程序继续在悬浮窗运行)。"""
        self.win.withdraw()

    # ---------------- 切页 ----------------
    def _current_page_key(self) -> str:
        """当前页键(查 notebook 而非缓存 → 未 update 的切页也不会误判)。"""
        try:
            cur = str(self.nb.select())
        except Exception:
            return self._page_key
        for k, p in self.pages.items():
            if str(p) == cur:
                return k
        return self._page_key

    def _page_tick(self) -> str:
        """每帧取当前页键,并在**切页当帧**做进入侧刷新(幂等)。

        进入侧刷新不依赖 `<<NotebookTabChanged>>` 是否已派发(测试里
        `nb.select()` 后未必 `update()`),事件回调与 tick 走同一函数。
        """
        key = self._current_page_key()
        if key != self._page_key:
            self._page_key = key
            if key == "看它":       # 进入:立即出数 + 情绪条对齐(再开始插值)
                self._refresh_readings()
                self._emo_snap()
            elif key == "系统":     # 进入:慢变读数不等 2Hz 节拍
                self._refresh_globals()
        return key

    def _on_page_changed(self, _event=None) -> None:
        """`<<NotebookTabChanged>>` → 立即做进入侧刷新(不等下一帧)。"""
        self._page_tick()

    def tick(self, dt: float) -> None:
        """App 主循环每帧调用。信息节奏分档(架构 §2 A.2):

        - 情绪条:**每帧**一阶插值 + 重画(仅当前页 == 「看它」);
        - 常驻个体条 + 「看它」页读数:**6Hz**(比旧 2Hz 快,观感是"活的");
        - 其余页 2Hz(「系统」页全局块;「看它」页的记忆时间线刷新);
        - 面板隐藏(`withdrawn`):**早退**,零工作量(不得删)。
        """
        self._fps_n += 1
        self._fps_t += dt
        _I.bump("panel_tick_calls")
        try:
            state = self.win.state()
        except Exception:
            state = "normal"
        if state == "withdrawn":     # 面板隐藏时不做任何刷新,省 CPU
            _I.bump("panel_tick_hidden")     # r25 U0:早退次数(隐藏态工作量=0)
            return
        _I.bump("panel_tick_work")
        key = self._page_tick()
        self._emo_step(dt, key)      # 情绪条:每帧(仅「看它」页)
        # 投喂倒计时:独立 0.25s 节流(比 2Hz 状态区更细,秒数跳动感自然)
        self._feed_txt_acc += dt
        if self._feed_txt_acc >= 0.25:
            self._feed_txt_acc = 0.0
            self._update_feeding_label()
        # 6Hz:常驻个体条 + 「看它」页读数(采样进 _emo_tgt,不画 canvas)
        self._stat_acc += dt
        if self._stat_acc >= FAST_S:
            self._stat_acc = 0.0
            self._fps = self._fps_n / self._fps_t if self._fps_t > 0 else 0.0
            self._fps_n, self._fps_t = 0, 0
            self._sync_rows()
            self._sync_action_state()   # A1 收口:勾选/按钮态随选中宠真实状态回正
            if key == "看它":
                self._refresh_readings()
        # 2Hz:其余页的慢变读数(「系统」页全局块 + 「看它」页记忆时间线)
        self._slow_acc += dt
        if self._slow_acc >= SLOW_S:
            self._slow_acc = 0.0
            if key == "系统":
                self._refresh_globals()
            elif key == "看它":
                self.refresh_memory()   # A3:2Hz 记忆时间线(内容未变即空转)

    # ================================================================
    # 「看它」页读数(6Hz)/「系统」页运行状况(2Hz)
    # ================================================================
    def _refresh_readings(self) -> None:
        """选中宠物的读数 + 情绪**采样**(写入 `_emo_tgt`;**不画 canvas**)。

        不画是关键:canvas 只在当前页 == 「看它」时才被改(验收 ⑥),
        采样与绘制分离后,非「看它」页的任何路径都不会动情绪条。
        """
        pid = self.selected()
        if not pid:
            self.s_name.set("(未选择宠物)")
            for v in (self.s_beh, self.s_mode, self.s_pos,
                      self.s_stomach, self.s_flags, self.s_mem, self.s_scale):
                v.set("-")
            self.s_learn.set("-")
            self.s_mastery.set("-")
            self.stomach_bar.configure(value=0.0)
            self._emo_tgt = {k: 0.0 for k, _zh, _c in EMOTIONS}
        else:
            st = self.app.pet_status(pid)
            if st:   # 宠物可能在取状态瞬间被移除,空 dict 保护
                em = st.get("emotion", {})
                self.s_name.set(f"{st.get('name', '-')}({pid})")
                self.s_beh.set(self._beh_zh(st.get("activity", "")))
                self.s_mode.set(self._mode_zh(st.get("mode", "")))
                pos = st.get("pos", (0, 0))
                self.s_pos.set(f"x={pos[0]}, y={pos[1]}")
                self.s_stomach.set(f"{st.get('stomach', 0):.0%}")
                self.stomach_bar.configure(value=float(st.get("stomach", 0)))
                flags = []
                if st.get("frozen"):
                    flags.append("冻结")
                if st.get("held"):
                    flags.append("被抓着")
                self.s_flags.set("、".join(flags) if flags else "正常")
                # 记忆条数:摘要条数 = 语义联想 + 情景记录(memory_digest 口径)
                try:
                    n_mem = len(self.app.memory_of(pid))
                except Exception:
                    n_mem = 0
                self.s_mem.set(f"{n_mem} 条(联想+情景)")
                # 体型档位(AG5):k + 当前渲染画布尺寸
                try:
                    half = self.app.pets[pid].body.window_half()
                    self.s_scale.set(f"{self.app.pet_scale(pid):g}×(画布 {half * 2}px)")
                except Exception:
                    self.s_scale.set("-")
                # 在线学习状态("它学会了…"):各学习器可读数值 + 最近学会的偏好
                try:
                    self.s_learn.set(self.app.pets[pid].brain.learning_summary())
                except Exception:
                    self.s_learn.set("-")
                # 熟练度(r25 A4):等级 + 一行中文读数。等级由 app 按经历
                # 自适应(app.mastery_of),面板只读不写(滑杆已删)。
                try:
                    _lv, mtxt = self.app.mastery_of(pid)
                    self.s_mastery.set(mtxt)
                except Exception:
                    self.s_mastery.set("-")
                # 情绪:6Hz 采样进插值目标(绘制由 _emo_step 按页承担)
                self._emo_tgt = {k: float(em.get(k, 0.0))
                                 for k, _zh, _c in EMOTIONS}

    def _refresh_globals(self) -> None:
        """「系统」页运行状况(2Hz):全局块 + 内存护栏只读行。"""
        self.g_pets.set(f"{len(self.app.pets)} / {self.app.cfg.max_pets} 只")
        try:
            mb = _memory_mb()
            self.g_mem_mb.set(f"{mb:.1f} MB" if mb >= 0 else "不可用")
        except Exception:
            self.g_mem_mb.set("不可用")
        self.g_fps.set(f"{self._fps:.0f} fps" if self._fps > 0 else "测量中…")
        self.g_feed.set("开(左键点击投放食物)" if self.app.feeding else "关")
        # 躯干缓存占用(AG5 性能观察:L1 1x 旋转桶 + L2 缩放成品桶)
        try:
            from neuropet.render import torso, torso_art
            l1 = sum(torso_art._rot_bytes.values())
            l2 = torso.l2_stats()["l2_bytes"]
            self.g_cache.set(f"L1 {l1 / 1048576:.1f} + L2 {l2 / 1048576:.1f} MB")
            # 内存护栏只读行(ok/trim/purge + 当前角度量化):只读 app 私有态,
            # 不改 app.py(mem_guard_next 的状态机在 app._mem_state)
            state = max(0, min(2, int(getattr(self.app, "_mem_state", 0))))
            step = int(getattr(torso_art, "_angle_step", 1))
            self.g_guard.set(f"{('ok', 'trim', 'purge')[state]} · 角度量化 {step}°")
        except Exception:
            self.g_cache.set("-")
            self.g_guard.set("-")
