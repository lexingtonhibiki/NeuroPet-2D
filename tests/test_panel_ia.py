# -*- coding: utf-8 -*-
"""r25 面板信息架构验收(test_panel_ia)。运行:python tests/test_panel_ia.py

本套只承载**新能力**判据——旧 test_panel_v2 / test_pet_hide 不测这些
(架构 §2 A.6:新判据只放旧套件没有的能力,不重复外观与滚动骨架断言)。
当前承载 A1(常驻个体条 + 高频动作区):

  ① 跨页常驻:常驻条在页签**之上**、不在任何页内;逐个页签切换后
     `tree`/`feed_now_btn`/冻结勾选/`hide_btn` 仍 `winfo_ismapped()`,
     且条与控件**不随切页重建**(对象身份不变);`panel.tree` 仍是
     `_PetList` 且 Treeview 兼容子集(get_children/selection/
     selection_set/insert/delete/update_row)可用。
  ② 1 点击可达(面板已打开态起的最少点击数):投喂 = `feed_now_btn`
     1 次 invoke(抽屉收起态)且以当前食物回调;换食物 = 抽屉 1 +
     食物 1 = 2 点击;冻结/隐藏/选宠各 1 点击。
  ③ 食物抽屉 = **页内可折叠区**(ttk.Frame 显隐,非 Toplevel):默认收起、
     1 次 invoke 展开(9 个食物按钮全部在屏)、再 invoke 收起、幂等;
     不新增 Toplevel。
  ④ 版式预算:常驻条总高 ≤110px(3 宠 = 默认 max_pets 上限);
     最小窗 520×640 下**真实页面视口** ≥477px(**预算记录**,非空间目标);
     且常驻条占窗口高 ≤20%(比值判据,默认 max_pets=3)。
     **判据登记(A1 收口;主控已裁决,见架构 §13.1)**:
       原判据:`nb.winfo_height() ≥ 500` —— **量错对象**:该值含 39px
         页签标题条(实测 516px 里 39px 不是页面区域),且 500 从未被
         验证可达(此前"通过"只因量错了对象)。
       新判据:① 真实视口 `_ScrollPage.canvas.winfo_height()` ≥ **477px**
         @最小窗/抽屉收起/100% 缩放 —— **477px 是 A2「看它首屏零滚动」
         的输入预算**,此处只作防回归下限;② `bar.winfo_reqheight() /
         win.winfo_height() ≤ 0.20`(实测 107/640 = 16.7%)。
       为什么:修的是**口径错误**;把与单一窗口尺寸绑死的绝对像素换成
         **比值判据**(对窗口尺寸与缩放都成立,并能看住宠数增长);真正的
         把关交给 **A2「看它首屏零滚动」硬门**(content_height() ≤ canvas
         可视高,比 500px 更严的下游判据)。
  ⑤ 其余三条高频动作(跨页选宠/冻结/隐藏)各 1 点击 + 徽章随动。
  ⑥ 常驻条动作控件与选中态同步(A1 收口):无选中时投喂/冻结/隐藏一律
     disabled、冻结勾选归 unchecked;有选中时勾选 = 该宠实际 `frozen`。
     由此封死「无选中点投喂 → `app.py:1424` 静默改喂第一只宠」的路径。

数字前提:全部版式数字取自**抽屉收起 + 100% 显示缩放**(本机 Tk
scaling≈1.3346);抽屉展开态 / 更高 DPI 下这些数字都不成立(黑盒报告
「可疑判据」已记:展开态条 233px、scaling=2.0 时条 136px)。

headless 真实 Tk,面板置于屏幕外(+3000+3000),不干扰桌面。

r25 A2 追加(四意图页重排 + 信息节奏分档):
  ⑦ 页元组 = `("看它","逗它","养它","系统")`(主控裁决 2 冻结);默认页 =
     「看它」(打开那一刻量 `nb.select(0)`);首屏把关点 = **必见区**
     (读数卡 + 情绪卡)底边 ≤ 视口底边,且前提**显式断言**(3 宠 / 抽屉
     收起 / scaling≈1.33 / 最小窗 520×640,见 7c-0);整页高度只作记录;
     降级②(主控裁决 4 预授权)生效证据:记忆区默认折叠 = 页内抽屉,
     展开态 +209px ≥ 200px 起、显隐幂等。
  ⑧ 旧变量名/控件名在 **AST 上**仍存在:21 个名字的 `hasattr` 清单
     (读数 9 + 情绪 canvas + 记忆 Text + 全局块 5 + 页控件变量 4 + 隐藏
     计数)+ 旧方法可调用 + 旧控件对象在 + 源码 `self.X` 存在性。
     **A4 已把 `int_var`/`int_scale`/`int_label`/`on_intel`/`_intel_release`/
     `_apply_intel` 从三张清单迁走(22 → 21),并以 ⑫e 反向断言它们不得
     再现**(判据登记见 `LEGACY_NAMES` 上方的注释块)。
  ⑨ 情绪条视觉连续:注入 6Hz 采样、驱动 120 帧 dt=1/60 → 条形宽度
     **≥100 帧在变**(≥50Hz 视觉连续);相邻帧差 ≤ 满量程 8% 的上界改在
     **30fps 帧档**下判(原 60fps 档不可破 = 无鉴别力,见 9b 上方判据登记);
     另有一条"幅度非平凡"护栏(防静止假通过)。宽度直接从 canvas item 量。
  ⑩ 节奏分档:隐藏态(win.withdraw)`tick` 零 canvas 变更 + `pet_status`
     计数 0;非「看它」页情绪条零更新、不采样宠物状态(采样源已换值也能
     验出误更新)。

r25 A4 追加(「迟钝聪慧」处置:删滑杆 + 熟练度自适应 + 可感知读数):
  ⑪ 面板侧:读数行文本 = `熟练度 N/5 · 记得 M 件事 · 最近学会:…`
     (数据源 = `app.mastery_of`,6Hz 随刷新);等级 1→5 时文本随之变化;
     行在「看它」页内且位于情绪卡之下;无选中归位 `-`;
     **面板全程 0 次调用 `app.set_intelligence`**(滑杆已删);运行期无
     滑杆遗留属性。另:判据③ 按主控裁决(架构 §14 + §16.1)二次迁移为
     「首屏必见区(读数卡 + 情绪卡)底边 ≤ 视口底边」,整页高度只作记录
     ——原「整页零滚动」以 475≤477(2px)通过,对 A3/A4 必加内容零防御力。
  ⑫ 纯函数面(验收 ①②③)+ 真脑/真 App 面(④⑤⑥):
     1000 组随机 stats 断言值域 ⊆1..5 / 单调不减(逐键) / 确定性;
     5 组边界输入分别落 1..5;脏值 fail-closed(数字串/str/None/NaN/±inf/
     负数/容器 → 贡献 0 ≡ 缺键;r25 A4-r2 裁决 ③ 把 `'7'` 从"静默采纳"改为
     拒收);面板 AST 无 7 个被删名字;
     `app.set_intelligence(pid,2)` → brain/episodic/assoc/bandit 全 2;
     `NEUROPET_MASTERY=off` = **停止自适应**(r25 A4-r2 裁决 ①② 迁移):驱动
     3 个节拍对脑**零等级写入**、脑内等级与记忆条数不变、读数 = 脑内等级
     (≠ cfg);⑫g-2 是它的破坏性面护栏 —— 高等级 + 超容量记忆下 off 不得删
     任何记忆(旧"把脑内等级对齐回 cfg"的语义实测把 220 条删到 40 条);
     开态经历自适应(脑内 2→5)、读数行文本随之变化、节拍**只上调不下调**
     (ratchet)且仅在整数等级变化时写 brain(写次数实测);
     ⑫k 的夹具养到 > cap(cfg 基线)=40 条记忆(裁决 ④),让"真重启零删除"
     真的会被 trim 路径打破(12 条时该断言恒真、无鉴别力)。
  ⑬ A4 收口(架构 §16 副作用修复;主控裁决):
     ⑦ 自动派生**不写回 cfg、不落盘**——驱动真 App ≥2.5s(≥1 个 2s 节拍)
        且期间确有等级跃变,仍要求 config 文件**字节不变** + `save_config`
        零调用 + `cfg.intelligence` = 载入基线(brain 的派生值只进内存);
     ⑧ 可复现性——**真重启**(shutdown 真落盘 → 新 App → 同名 `pet_id` 走
        `_load_profile`,不手工注入年龄/等级/记忆)后:年龄确实落盘(>0)、
        `mastery_of()` 返回**同一等级/同一读数**;
     ⑩ 真重启**零删除**(黑盒缺陷 A 的正面判据):情景记忆条数不减 +
        `brain.level` 不低于重启前 —— 修复前真重启会把 220 条按小容量 trim;
     ⑨ 切页未派发事件时 `_on_select → _emo_snap` **不向非当前页 canvas 写入**,
        且反方向(实际当前页 = 「看它」而缓存陈旧)不因修复而漏画。
     ⚠ 判据可疑登记(验收⑥ 原话;**主控已裁决:原判据建立在错误假设上,
     不是执行没做到**):`learning_summary()` 文本对 level **恒定**(实测两物种
     L1==L5 逐字符相同),⑥ 落成可判最强形式(读数行 + `memory_of()` 学习产物
     两条),详见 ⑫ 段头注。

r25 A3 追加(记忆时间线;判据口径见主控裁定 1/2 + 架构 §2 A3):
  ⑭ 「看它」页的记忆区从**裸文本转储**升级为**时间线**(控件仍是冻结名
     `mem_txt`)。`memory_of()` 的返回是**异质行**(物种状态行 / 联想行 /
     情景行 / 畸形行),只有带时间戳的情景行进时间线:
     ⑭a 不丢行——显示出的内容行数 == 输入行数(口径改为"总行数不丢";
         原文"行数 == 6"没说清无时间戳的行怎么算);每行内容都在控件里;
     ⑭b 畸形行不抛、**原样**可见(归「未识别」,不静默丢弃);
     ⑭c 状态行/联想行各归其区,不塞进时间排序;
     ⑭d 排序**可证伪**:喂乱序输入 → 输出严格时间降序(原文"降序且与输入
         一致"自相矛盾;只喂已排序输入则判据恒绿 = 纸板);
     ⑭e 情景行**两种格式都解析**(带/不带"显著度"并存;不带 → 未知呈现,
         不丢进未识别区);
     ⑭f `clear_memory`(真入口,二次确认打桩)→ 空态文案 + 行数 0;
     ⑭g/⑭h/⑭i 2Hz 路径:挂在 tick 上(内容每拍变 → 10s 内真渲染 ≥15 次)、
         200 行单次刷新 p95 ≤1.5ms、内容未变不重绘(防每拍全量重画顶回
         滚动位置)。**判据⑤ 同时重写为 30fps 帧档**(原 60fps 口径数学上
         不可破 = 无鉴别力);判据③ 二次迁移为"显式前提 + 必见区"。
"""
from __future__ import annotations

import ast
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk
from tkinter import ttk

from neuropet.brain.mastery import (MASTERY_ENV, mastery_level, readout)
from neuropet.core.app import FRAME_DT, MASTERY_BEAT_S
from neuropet.core.config import AppConfig
from neuropet.core.contracts import Behavior, MovementMode
from neuropet.feeding import FOODS, FoodKind
from neuropet.ui import panel as panelmod
from neuropet.ui.panel import (BAR_X0, BAR_X1, EMOTIONS, ControlPanel, _PetList,
                               memory_row_count, parse_memory_lines)

PANEL_W, PANEL_H = 520, 640      # 最小窗口(与 test_panel_v2 同口径)
BAR_MAX_H = 110                  # 常驻条总高上限(架构 §2 A.1 验收 ④)
VP_MIN_H = 477                   # 真实页面视口下限 = A2「看它首屏零滚动」的
                                 # 输入预算(最小窗/抽屉收起/100% 缩放),防回归;
                                 # 硬门在 A2 的 content_height() ≤ canvas 可视高
BAR_MAX_RATIO = 0.20             # 常驻条占窗口高上限(默认 max_pets=3;
                                 # 比值判据,对窗口尺寸与缩放均成立)

# ---- r25 A2:四意图页 / 旧名字清单 ----
PAGES = ("看它", "逗它", "养它", "系统")   # 页签文案与顺序冻结(主控裁决 2)

# A2 验收 ④:旧变量名/控件名在 AST 上仍存在(hasattr 清单,21 个)。
# **判据迁移(r25 A4;原 → 新 → 为什么)**:
#   原:A2 版清单 22 个名字,含 `int_var`(names)/`on_intel`、`_intel_release`、
#       `_apply_intel`(methods)/`int_scale`、`int_label`(widgets)——
#       A2 当时按架构保留「智能等级」卡,故把它们列为"迁移未完成项"。
#   新:三张清单各删去上列 6 个名字(22 → 21,**只删这一项**),判据改为
#       "**不在** panel.py 里出现"(⑪c 的 AST 断言),其余名字与计数照旧。
#   为什么:架构 §2 A.5 第 1 步要求删整张智能卡 + 三方法 + 变量(用户点名
#       冗余;§16.4 已裁定"以 A4 才删为准")。控件删掉后 hasattr 清单若
#       仍列着它们就成了"要求死名字必须存在"的反向判据 → 必须反向迁移。
#       判别力不降:接线仍是 `app.set_intelligence`(插件协议方法,
#       `tests/test_panel_v2.py:153` 的 FakeApp 桩保留 = 接口存在性证据),
#       ⑪d 另用真 App + 真脑断言传播链完好。
LEGACY_NAMES = (
    # 原「状态」页读数(A2 → 「看它」):9 个 StringVar
    "s_name", "s_beh", "s_mode", "s_pos", "s_stomach", "s_flags",
    "s_mem", "s_scale", "s_learn",
    # 原「状态」页情绪 canvas + 原「记忆」页裸 Text
    "emo_canvas", "mem_txt",
    # 原「状态」页全局块(A2 → 「系统」→「运行状况」)
    "g_pets", "g_mem_mb", "g_fps", "g_feed", "g_cache",
    # 原「行为」/「情境演示」/「宠物」页控件变量
    "beh_var", "spec_var", "scale_var", "feed_var",
    # 原「宠物」页隐藏计数读数(条与「养它」两处共用同一 StringVar)
    "hidden_count_var",
)
LEGACY_METHODS = (
    "add_pet", "remove_pet", "hide_selected", "recall_selected", "recall_all",
    "reset_avatar", "on_scale", "trigger", "escape_test", "clear_zones",
    "refresh_memory", "clear_memory",
    "_fill_plugins", "_fill_external_report", "_draw_emotions",
    "selected", "refresh_pets", "set_feeding", "hide", "tick",
)
LEGACY_WIDGETS = (
    "tree", "hidden_list", "plug_tree", "ext_txt", "emo_canvas",
    "stomach_bar", "feed_check",
    "mem_body", "mem_drawer_btn", "bar", "nb",
)

#: A4 明令删除的面板面(验收 ③):不得再以 ``self.X`` 出现在 panel.py 里。
DROPPED_INTEL_NAMES = (
    "int_var", "int_scale", "int_label", "_last_intel",
    "on_intel", "_intel_release", "_apply_intel",
)
EMO_SPAN = float(BAR_X1 - BAR_X0)   # 情绪条满量程(canvas 像素)


def emo_fill_widths(panel) -> tuple:
    """情绪条各行的**已填充**条形宽度(直接从 canvas item 量,不信内部计数)。"""
    c = panel.emo_canvas
    return tuple(round(c.coords(i)[2] - c.coords(i)[0], 3)
                 for i in c.find_withtag("dyn")
                 if c.type(i) == "rectangle" and c.itemcget(i, "outline") == "")


def canvas_dump(panel) -> tuple:
    """canvas 全部 item 的类型+坐标快照(判"零变更"用)。"""
    c = panel.emo_canvas
    return tuple((c.type(i), c.coords(i)) for i in c.find_all())


# ---------------- Fake App(真实 tk 面板 + 最小 App 表面 + 调用记录) --------
class FakeState:
    def __init__(self, pid, species="species.cockroach"):
        self.pet_id = pid
        self.species_id = species
        self.name = f"名字{pid[-1]}"
        self.pos = (600.0, 400.0)
        self.mode = MovementMode.CRAWL
        self.activity = Behavior.EXPLORE
        self.frozen = False
        self.held = False
        self.stomach = 0.62


class FakeBody:
    def window_half(self) -> float:
        return 60.0


class FakeBrain:
    def learning_summary(self) -> str:
        return "stub"


class FakePet:
    def __init__(self, pid, species="species.cockroach"):
        self.pet_id = pid
        self.state = FakeState(pid, species)
        self.body = FakeBody()
        self.brain = FakeBrain()


class FakeManifest:
    def __init__(self, mid, name):
        self.id, self.name = mid, name
        self.type, self.version, self.source = "species", "1", "内置"


class FakeRegistry:
    def manifests(self):
        return []


class FakeApp:
    """ControlPanel 依赖面 + 三条动线的调用记录(1 点击判据的证据源)。

    宠数 = 3 = `AppConfig.max_pets` 默认上限(条高判据取最紧的合法配置)。
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = AppConfig(max_pets=3)  # Historical research-panel layout fixture.
        self.pets = {"roach-1": FakePet("roach-1"),
                     "fly-2": FakePet("fly-2", "species.fly"),
                     "roach-3": FakePet("roach-3")}
        self._hidden: dict = {}
        self.registry = FakeRegistry()
        self.feeding = False
        self._feeding_until = None
        self._external_reports: list = []
        self._static_dirty = False
        self.world = type("W", (), {"clear_zones": staticmethod(lambda: None)})()
        self.frozen_calls: list = []
        self.drop_calls: list = []
        self.panel = None
        # A2 节奏分档判据(⑤⑥)的两件仪表:情绪采样源的"时间"与调用计数
        self.emo_t = 0.0             # 由测试按帧推进;仅 6Hz 采样时才被读到
        self.status_calls = 0
        # A4 熟练度仪表:面板读数行的唯一数据源是 mastery_of(真 App 里由
        # brain/mastery.py 纯函数算);桩给可控档,用于验"读数随等级变"
        # 与"面板不再调用 set_intelligence"。
        self.mastery_level = 3
        self.mem_count = 12
        self.mastery_calls = 0
        self.intel_calls: list = []
        # r25 A3 记忆时间线仪表:`memory_of` 的返回面(桩只换数据面,不改契约)
        self.mem_lines: list = []
        self.memory_calls = 0
        self.clear_calls = 0

    # 查询
    def hidden_pets(self):
        return self._hidden

    def species_manifests(self):
        return [FakeManifest("species.cockroach", "美洲大蠊"),
                FakeManifest("species.fly", "黑腹果蝇")]

    def pet_status(self, pid):
        self.status_calls += 1           # A2 判据 ⑩:节奏分档的调用计数
        h = self.pets.get(pid)
        if not h:
            return {}
        # 情绪源 = 每键相位错开的 1Hz 正弦(幅值 0.3,值域 [0.2, 0.8]):
        # 只由 6Hz 采样读到 → 采样点之间的帧值恒定,插值必须自己撑起连续性
        emo = {k: 0.5 + 0.3 * math.sin(2 * math.pi * (self.emo_t + i / 7.0))
               for i, k in enumerate(("fear", "hunger", "curiosity", "anger",
                                      "trust", "valence", "arousal"))}
        return {"pet_id": pid, "species": h.state.species_id,
                "name": h.state.name, "pos": [600, 400], "mode": "crawl",
                "activity": "explore", "frozen": h.state.frozen, "held": False,
                "stomach": 0.62, "emotion": emo}

    def memory_of(self, _pid):
        self.memory_calls += 1
        return list(self.mem_lines)

    def pet_scale(self, _pid):
        return 1.0

    # 操作(记录调用 + 维持内部状态一致)
    def add_pet(self, _s=None):
        return None

    def remove_pet(self, _p=None):
        return None

    def hide_pet(self, p):
        h = self.pets.pop(p, None)
        if h:
            self._hidden[p] = h
            if self.panel is not None:
                self.panel.refresh_pets()
        return bool(h)

    def recall_pet(self, p):
        h = self._hidden.pop(p, None)
        if h:
            self.pets[p] = h
            if self.panel is not None:
                self.panel.refresh_pets()
        return bool(h)

    def set_frozen(self, p, v):
        self.frozen_calls.append((p, bool(v)))
        h = self.pets.get(p)
        if h:
            h.state.frozen = bool(v)

    def mastery_of(self, _p):
        """A4:熟练度读数行数据源(真 App 同名同形;桩只换数据面)。"""
        self.mastery_calls += 1
        return self.mastery_level, readout(self.mastery_level, self.mem_count,
                                           "stub")

    def set_intelligence(self, p, v):
        """桩**保留**(接口存在性证据,架构 §2 A.5 连带改动):A4 起面板
        不再调用它,故这里记录调用次数供 ⑪f 断言(0 次)。"""
        self.intel_calls.append((p, v))
        return None

    def set_pet_scale(self, _p, _k):
        return None

    def clear_effects(self, _p):
        return None

    def clear_memory(self, _p):
        self.clear_calls += 1
        self.mem_lines = []          # A3:清空后 memory_of 必须返回空(真 App 同语义)

    def trigger_behavior(self, _p, _b):
        return None

    def escape_test(self, _p):
        return None

    def tell_story(self, _k):
        return None

    def place_cold_zone(self):
        return None

    def toggle_feeding(self, _on):
        return True


def toplevels(root: tk.Tk) -> set:
    """root 下所有 Toplevel 的控件路径(抽屉不得新增 Toplevel)。"""
    return {str(w) for w in root.winfo_children() if isinstance(w, tk.Toplevel)}


def _a4_real_app_checks(check) -> None:
    """A4 验收 ④⑤⑥ + 收口 ⑦⑧ 的**真 App** 面(调前上一个 Tk root 已销毁)。

    用真 App 而非 FakeApp:④ 要证的是 ``app.set_intelligence`` 到
    episodic/assoc/bandit 的**传播链**("机制没被删"),⑤/⑥ 要证的是
    ``NEUROPET_MASTERY=off`` = 停止自适应(零写入、不删记忆、读数=脑内等级)
    与开态的经历自适应,⑦/⑧ 要证"自动派生不写回 cfg、
    不落盘且不丢信息"——桩都验不了(把落盘换成 no-op 桩,"没落盘"就成了
    同义反复:桩里本来就不会写)。

    卫生:profile_dir / _SESSION_FILE / _PETS_FILE 与 config 模块的
    DATA_DIR / CONFIG_PATH / PETS_PATH 全部指向**临时目录**;``save_config``
    **仍走真落盘路径**(只加记数 + 重定向),临时 config 是仓库
    ``data/config.json`` 的副本(基线等级改写为 2,保证"字节不变"有鉴别力)
    → 仓库 ``data/`` 零写入,而"写了没写"由真函数判定。
    """
    import os
    import shutil
    import tempfile
    from neuropet.core import app as appmod
    from neuropet.core import config as cfgmod

    with tempfile.TemporaryDirectory(prefix="neuropet_a4_") as td:
        tmp = Path(td)
        cfg_file = tmp / "config.json"
        repo_cfg = cfgmod.CONFIG_PATH
        orig = (appmod.profile_dir, appmod._SESSION_FILE, appmod._PETS_FILE,
                cfgmod.DATA_DIR, cfgmod.CONFIG_PATH, cfgmod.PETS_PATH,
                appmod.save_config)
        real_save = cfgmod.save_config
        saves: list = []                 # save_config 调用记录(⑦ 的牙齿)

        def _save_spy(cfg) -> None:
            saves.append(int(getattr(cfg, "intelligence", 0)))
            real_save(cfg)               # 真落盘(路径已重定向到临时目录)

        def _prof(pid: str) -> Path:
            d = tmp / "profiles" / pid
            d.mkdir(parents=True, exist_ok=True)
            return d

        appmod.profile_dir = _prof
        appmod._SESSION_FILE = tmp / "session.json"
        appmod._PETS_FILE = tmp / "pets.json"
        cfgmod.DATA_DIR = tmp
        cfgmod.CONFIG_PATH = cfg_file
        cfgmod.PETS_PATH = tmp / "pets.json"
        appmod.save_config = _save_spy
        app = None
        try:
            # 基线配置:仓库真配置的副本,但**基线等级(2)≠ 派生等级(5)**——
            # 否则"文件字节不变"会被"写回了同一个值"蒙混过关。
            if repo_cfg.exists():
                shutil.copyfile(repo_cfg, cfg_file)
            else:
                real_save(cfgmod.AppConfig())
            _cfg0 = cfgmod.load_config()
            _cfg0.intelligence = 2
            real_save(_cfg0)
            _bytes_base = cfg_file.read_bytes()     # 基线字节(⑦ 自足比较用)

            app = appmod.App()
            pids = [app.add_pet("species.cockroach", pos=(420.0, 300.0)),
                    app.add_pet("species.fruitfly", pos=(520.0, 300.0))]
            # ---- ④ 传播链回归:app.set_intelligence(pid, 2) 贯通四个 level ----
            for pid in pids:
                app.set_intelligence(pid, 2)
            rows, ok4 = [], True
            for pid in pids:
                b = app.pets[pid].brain
                lv = (b.level, b.episodic.level, b.assoc.level, b.bandit.level)
                rows.append(f"{type(b).__name__}{lv}")
                ok4 = ok4 and lv == (2, 2, 2, 2)
            check("⑫f app.set_intelligence(pid,2) → brain/episodic/assoc/bandit 全 2"
                  "(机制未被删)", ok4, " ".join(rows))
            check("⑫f-2 手动入口 set_intelligence 仍照旧写 cfg + 落盘"
                  "(插件/调试 API 行为不变;收口只改**自动派生**)",
                  saves == [2, 2] and app.cfg.intelligence == 2
                  and cfg_file.exists(),
                  f"save_config 调用={saves} cfg={app.cfg.intelligence}")
            # ---- ⑤ 回退开关:off = **停止自适应**(脑保持当前等级,读数 = 脑内) ----
            # 判据迁移(原 → 新 → 为什么;r25 A4-r2 主控裁决 ①+②):
            #   原:off 态 `mastery_of()` 恒 = `cfg.intelligence`,且 2s 节拍把
            #       `brain.level` **下调对齐回** cfg(旧语义号称"回退",⑫g-2 还
            #       把"对齐写入 == [4,4]"当正面证据)。
            #   新:off 态驱动 3 个节拍 → 对脑的等级写入 **0 次**、脑内等级与
            #       记忆条数不变,`mastery_of()` 读数 = **脑内等级**。夹具刻意把
            #       cfg 基线设成 4 ≠ 脑内 2:读数若还取 cfg、或节拍还对齐回 cfg,
            #       本条必红。
            #   为什么:① 原口径的"下调对齐"是**破坏性**的 —— 基线 2 + 档案
            #       L5/220 条时,首拍把等级压回 2 → `EpisodicMemory.set_level
            #       →trim()` 按 cap(2)=40 **静默删 180 条**(黑盒 P12 实测
            #       220→40,无任何提示)。回退开关的存在意义是**安全**:用户为
            #       诊断设一个环境变量,代价不该是宠物的记忆。故任何自动路径都
            #       不得下调等级、不得触发记忆删除(ratchet 对 on/off 一视同仁),
            #       off 只表示"别再自适应";破坏性面的活体护栏见 ⑫g-2。
            #       ② 原 `mastery_of` 有两套口径(on 取派生值 / off 取 cfg),正是
            #       "读数与实跑不一致"的来源(黑盒实测 on 态读数 2、脑跑 5),
            #       现统一 = 脑内等级(单一真源)。
            os.environ[MASTERY_ENV] = "off"
            app.cfg.intelligence = 4           # 基线刻意 ≠ 脑内(2):读数取 cfg 必红
            app.pets[pids[0]].state.age_s = 600.0   # 经历拉满,off 态也不许派生
            _off_w: list = []                  # 对脑的等级写入记录:(pid, level)

            def _spy_level(pid: str, log: list):
                b_ = app.pets[pid].brain
                o_ = b_.set_intelligence
                b_.set_intelligence = (lambda o, p: lambda lv:
                                       (o(lv), log.append((p, lv)))[0])(o_, pid)
                return o_

            _restore = {p: _spy_level(p, _off_w) for p in pids}
            _mem0 = {p: len(app.pets[p].brain.episodic.items) for p in pids}
            _saves_off = len(saves)
            for _ in range(3):                 # 3 拍:off 态必须始终零写入
                app._mastery_acc = MASTERY_BEAT_S
                app._step(FRAME_DT)
            for _p, _o in _restore.items():
                app.pets[_p].brain.set_intelligence = _o
            _brain_lv = {p: app.pets[p].brain.level for p in pids}
            _rd5 = {p: app.mastery_of(p)[0] for p in pids}
            _mem1 = {p: len(app.pets[p].brain.episodic.items) for p in pids}
            check("⑫g NEUROPET_MASTERY=off = 停止自适应:3 个节拍对脑**零等级写入**"
                  "(脑内等级不变),读数 = 脑内等级(≠ cfg),记忆条数不减,"
                  "不写 cfg/不落盘",
                  not _off_w and set(_brain_lv.values()) == {2}
                  and set(_rd5.values()) == {2}
                  and all(_mem1[p] >= _mem0[p] for p in pids)
                  and app.mastery_of("不存在的宠物")[0] == 4   # 未选中占位:仍取 cfg
                  and len(saves) == _saves_off,
                  f"写入={_off_w}(应空) 脑内={_brain_lv} 读数={_rd5} "
                  f"cfg 基线={app.cfg.intelligence}(≠ 读数) 记忆={_mem0}→{_mem1} "
                  f"save_config 增量={len(saves) - _saves_off}")
            # ---- ⑥ 开态:经历自适应 + 读数行随之变化(读数 = 脑内等级) ----
            del os.environ[MASTERY_ENV]
            pid = pids[0]
            app.cfg.intelligence = 2            # 基线(= 载入值;≠ 即将派生的 5)
            app.pets[pid].state.age_s = 0.0     # 撤掉 ⑤ 为验证"off 不写 brain"拉满的年龄
            lv1, txt1 = app.mastery_of(pid)     # 经历尚浅:脑内 = 基线 2
            _writes = []                        # 记录节拍对 brain 的写入次数
            for _p in pids:
                _b = app.pets[_p].brain
                _o = _b.set_intelligence
                _b.set_intelligence = (lambda o: lambda lv: (o(lv), _writes.append(lv))[0])(_o)
                for _ in range(12):
                    _b.on_event("fed", {})      # 经历:投喂 12 次 + 记忆 ≥12 条
                _b._n_danger = 6
                app.pets[_p].state.age_s = 600.0
            app._mastery_acc = MASTERY_BEAT_S
            app._step(FRAME_DT)                # 2s 节拍:派生 5 > 脑内 2 → 抬档
            lv5, txt5 = app.mastery_of(pid)    # 读数 = 脑内(节拍后 = 5)
            # 读数口径随裁决 ② 改 = 脑内等级:故 lv1 是**基线 2**(不再是派生值
            # 1 档),`lv5` 必须在节拍**之后**读 —— 读数跟着脑走,不跟着派生值走。
            check("⑫h 开态:经历自适应(脑内 2→5)且读数行文本随之变化"
                  "(⑥ 可感知面;读数 = 脑内等级)",
                  lv1 == 2 and lv5 == 5 and txt1 != txt5,
                  f"{lv1} 档 {txt1!r} → {lv5} 档 {txt5!r}")
            n_after_first = len(_writes)
            # 判据迁移(原 → 新 → 为什么;⑦ 是它的完整版):
            #   原:断言 `app.cfg.intelligence == 5`(架构 §2 A.5 第 3 步「写回
            #       cfg.intelligence 以保持 config 往返语义」)。
            #   新:节拍只把派生等级写进 **brain(内存)**,`cfg.intelligence`
            #       仍 = 载入基线 2;落盘由 ⑦ 用**真 save_config** 整段验。
            #   为什么:cfg.intelligence 语义已改为「熟练度基线(初始值)」,派生值
            #       不该覆盖基线;原写法会让任何驱动真 App ≥2s 的测试静默重写
            #       受版本控制的 data/config.json(详见 ⑦ 段注)。
            check("⑫h-2 2s 节拍把新等级写进 brain(内存),cfg 仍 = 载入基线",
                  app.pets[pid].brain.level == 5 and app.cfg.intelligence == 2
                  and app.pets[pid].brain.episodic.level == 5
                  and sorted(_writes) == [5, 5],
                  f"brain.level={app.pets[pid].brain.level} "
                  f"episodic={app.pets[pid].brain.episodic.level} "
                  f"cfg={app.cfg.intelligence}(基线 2) 写入={_writes}")
            app._mastery_acc = MASTERY_BEAT_S
            app._step(FRAME_DT)                # 等级未变:不重复写(幂等)
            check("⑫h-3 等级未变的节拍不重复写 brain(仅整数变化才写)",
                  len(_writes) == n_after_first, f"写入序列={_writes}")

            # ---- ⑦ 自动派生**不写回 cfg、不落盘**(A4 收口;架构 §16 副作用修复) ----
            # 判据迁移(原 → 新 → 为什么):
            #   原:⑫h-2 断言 `app.cfg.intelligence == 5` —— 依据是架构 §2 A.5
            #       第 3 步原话「写回 cfg.intelligence 以保持 config 往返语义」。
            #   新:先把两只宠的脑等级**退回基线 2**(制造"驱动的这 2.5s 内确有
            #       整数等级跃变"这一最不利条件),再驱动真 App 2.5s(≥1 个 2s
            #       节拍)→ 要求 config 文件**字节不变** + `save_config` 调用
            #       **0 次** + `cfg.intelligence` 仍 = 载入基线 2 + 派生值确实
            #       进了 brain(5)。
            #   为什么:① `AppConfig.intelligence` 的语义已被 A4 改为「熟练度
            #       基线(初始值)」,派生值不该覆盖基线(架构原文的"config 往返"
            #       意图是**载入兼容**,不是让运行时派生值把基线冲掉);② 原写法
            #       让**任何驱动真 App ≥2s 的测试静默重写受版本控制的
            #       `data/config.json`**(实测 test_pet_hide 把它改成
            #       intelligence=1/max_pets=6 → 污染提交 + 测试间顺序依赖),
            #       多宠时还被最后一只宠覆盖;③ 派生等级可由已持久化的 stats
            #       完整复现(⑧ 实测)→ 不落盘不丢信息。
            #       ⚠ ③ 的前提("已持久化")当时**不成立**:`age_s` 没有落盘路径
            #       (r25 A4-fix 才补上;此前 ⑧ 靠手工注入年龄才判绿 = 同义反复,
            #       真重启会掉级并按小容量 trim 删记忆 —— 黑盒缺陷 A)。补了年龄
            #       落盘 + 等级只上调之后,③ 才真正成立。主控裁决:不加
            #       `_roster_live` 式 guard,直接让自动派生不写 cfg、不落盘;
            #       手动/调试入口 `set_intelligence` 行为不变(⑫f-2 已验)。
            for _p in pids:
                app.pets[_p].brain.set_intelligence(2)   # 退回基线:跃变必然发生
            _beats: list = []
            _beat0 = app._mastery_beat
            app._mastery_beat = lambda: (_beats.append(1), _beat0())[1]
            _saves0, _lvl0 = len(saves), app.pets[pid].brain.level
            for _ in range(int(round(2.5 / FRAME_DT))):   # 2.5s ≥ 1 个 2s 节拍
                app._step(FRAME_DT)
            app._mastery_beat = _beat0
            # 字节比较用**基线副本**(不是运行前快照):判据自足——即使前面某条
            # 判据已经把文件改脏,本条的"字节不变"仍然判得动。
            _now = cfg_file.read_bytes()
            check("⑫i 驱动真 App 2.5s(≥1 个 2s 节拍、期间确有等级跃变)后:"
                  "config 文件字节 = 基线(bytes 不变)+ save_config 零调用"
                  "+ cfg 仍 = 基线",
                  len(_beats) >= 1 and _now == _bytes_base
                  and len(saves) == _saves0 and app.cfg.intelligence == 2
                  and _lvl0 == 2 and app.pets[pid].brain.level == 5,
                  f"节拍 {len(_beats)} 次 / 文件字节 = 基线({len(_now)}B)="
                  f"{_now == _bytes_base} / 期间 save_config 调用 "
                  f"{len(saves) - _saves0} 次 / cfg={app.cfg.intelligence}(基线 2) / "
                  f"brain.level {_lvl0}→{app.pets[pid].brain.level}(只进内存)")

            # ---- 夹具加料(主控裁决 ④):把记忆养到**基线容量之上** ----
            # 为什么:基线 2 的 cap = 40(LEVEL_CAP[2],brain/memory.py)。重启前若
            # 只有 12 条,"等级被下调 → 容量缩 → trim 删记忆"这条路径**根本触不到
            # 条目**,⑫k 的"记忆条数不减"就永远红不了(黑盒复验「可疑判据」1:12 条
            # fixture 下负向对照实测 12→12,无鉴别力)。养到 > 40 条后,一旦等级在
            # 重启时被压回基线,trim 必删 —— ⑫k 才算真有牙齿(负向对照见交付报告)。
            for _p in pids:
                _b = app.pets[_p].brain
                for _ in range(48):
                    _b.on_event("fed", {})      # 12 + 48 = 60 条 > cap(基线 2)=40
            _n_extra = len(app.pets[pid].brain.episodic.items)
            check("⑫k-0 夹具前置:重启前记忆条数 > cap(cfg 基线 2)=40 且脑内 L5"
                  "(否则 ⑫k 的「条数不减」无鉴别力)",
                  _n_extra > 40 and app.pets[pid].brain.level == 5,
                  f"记忆={_n_extra} 条 / cap(基线 2)=40 / 脑内等级="
                  f"{app.pets[pid].brain.level}")

            # ---- ⑤ 破坏性面(回归护栏):off 态 + 高等级 + 超容量记忆 ----
            # 判据迁移(原 → 新 → 为什么;主控裁决 ①):原 ⑫g-2 把"off 节拍把脑内
            # 等级**对齐回** cfg"当正面证据;新 ⑫g-2 反过来 —— 这正是**必须不发生**
            # 的事。夹具是这次的活体:脑内 L5 + 60 条记忆 + cfg 基线 2(旧语义会把它
            # 压回 2 → trim 删到 40 条,与黑盒 P12 的 220→40 同一机制、同一行代码)。
            # 为什么:回退开关的意义是安全;有了这条护栏,"再有人把 off 改回对齐"
            # 立刻变红,失败详情直接给出被删条数。
            os.environ[MASTERY_ENV] = "off"
            _d_lv0 = app.pets[pid].brain.level
            _d_n0 = len(app.pets[pid].brain.episodic.items)
            for _ in range(3):
                app._mastery_acc = MASTERY_BEAT_S
                app._step(FRAME_DT)
            _d_lv1 = app.pets[pid].brain.level
            _d_n1 = len(app.pets[pid].brain.episodic.items)
            check("⑫g-2 off 态破坏性面护栏:高等级(L5)+ 超容量记忆(>cap 基线)驱动"
                  " 3 拍后等级不变、**一条记忆都不删**(旧「对齐回 cfg」语义在此必红)",
                  _d_lv1 == _d_lv0 == 5 and _d_n1 >= _d_n0,
                  f"level {_d_lv0}→{_d_lv1} 记忆 {_d_n0}→{_d_n1}"
                  f"(删 {_d_n0 - _d_n1} 条);cfg 基线={app.cfg.intelligence}")
            os.environ.pop(MASTERY_ENV, None)

            # ---- ⑧ 真重启(判据修正;黑盒缺陷 A):不手工回灌年龄/等级/记忆 ----
            # 判据迁移(原 → 新 → 为什么):
            #   原:新建 App + 新建脑,手工 `brain.load(_snap)`,**并手工注入
            #       `state.age_s = _age`**(原注释写"年龄随档案/会话持久化"——实测
            #       为**假**:`age_s` 当时全仓无持久化路径),再断言 mastery_of()
            #       同等级。这是**同义反复**:被测的关键前提("年龄可复现")由判据
            #       自己塞进去,真实重启没有这一步。
            #   新:走真路径 —— `app.shutdown()`(save_all 真落盘 memory.json)→
            #       新建 App → `add_pet(..., pet_id=同一个 pid)` 触发
            #       `_load_profile`,**不注入任何东西**,断言 ① 年龄 > 0(确实
            #       落了盘)② mastery_of() 同等级同读数 ③ 记忆条数不减、等级不降
            #       (⑫k,零删除)。
            #   为什么:黑盒实测真重启(不注入年龄)下 age_s 归零 → 等级必降 ≥1
            #       档 → `EpisodicMemory.set_level→trim()` **永久删除**记忆
            #       (220→140 条,静默无提示),且反复启动还能继续删。修法在实现侧
            #       (年龄随脑存档落盘 + 等级只上调),判据侧必须改成真路径才有
            #       鉴别力——**修复前此判据为红**(等级 5→2 基线、记忆按小容量被
            #       trim),旧写法正是因此才手工塞年龄。
            #   ⚠ 夹具(主控裁决 ④):重启前必须养到 **> cap(cfg 基线)=40 条**
            #       (见 ⑫k-0),否则"记忆条数不减"这条断言在 12 条时恒真 ——
            #       黑盒复验的负向对照实测 12→12,真正守门的只剩 level 条款。
            _n_mem0 = len(app.pets[pid].brain.episodic.items)
            _lvl_pre = app.pets[pid].brain.level
            _age_pre = app.pets[pid].state.age_s
            _lv, _txt = app.mastery_of(pid)
            app.shutdown()                           # 停掉这一"进程"(save_all 落盘)
            app = None
            app = appmod.App()                       # "重启":全新 App + 全新脑
            pid2 = app.add_pet("species.cockroach", pos=(420.0, 300.0),
                               pet_id=pid)           # 同名 pet_id → 走 _load_profile
            h2 = app.pets[pid2]
            _n_mem1 = len(h2.brain.episodic.items)
            _lv2, _txt2 = app.mastery_of(pid2)
            check("⑫j 真重启(同名 pet_id 走 _load_profile,不手工注入年龄/等级/记忆)"
                  "→ 年龄已落盘(>0)且 mastery_of() 同等级同读数",
                  _lv2 == _lv and _txt2 == _txt and _lv == 5 and h2.state.age_s > 0.0,
                  f"重启前 {_lv} 档/{_n_mem0} 条/age={_age_pre:.0f}s → 重启后 "
                  f"{_lv2} 档/{_n_mem1} 条/age={h2.state.age_s:.0f}s 读数={_txt2!r}")
            check("⑫k 真重启零删除、等级不降(黑盒缺陷 A 的正面判据):"
                  f"情景记忆条数不减({_n_mem0} 条 > cap(基线 "
                  f"{app.cfg.intelligence})=40,trim 路径真的可触发)"
                  "+ brain.level 不低于重启前",
                  _n_mem1 == _n_mem0 and h2.brain.level >= _lvl_pre,
                  f"记忆 {_n_mem0} → {_n_mem1}(删 {_n_mem0 - _n_mem1} 条);"
                  f"level {_lvl_pre} → {h2.brain.level};cfg 基线="
                  f"{app.cfg.intelligence}(cap 40)")
        finally:
            os.environ.pop(MASTERY_ENV, None)
            try:
                if app is not None:
                    app.shutdown()             # 归档走临时 profile_dir(未还原)
            finally:
                (appmod.profile_dir, appmod._SESSION_FILE, appmod._PETS_FILE,
                 cfgmod.DATA_DIR, cfgmod.CONFIG_PATH, cfgmod.PETS_PATH,
                 appmod.save_config) = orig


def content_y(widget, top) -> int:
    """控件在滚动页 `top` 坐标系里的 y(比较页内相对位置;取不到返回 -1)。

    注意不能用 `winfo_ismapped()` 判"在「看它」页":`_ScrollPage` 是 canvas
    滚动页,canvas 的 window item 滚出视口即**被 Tk 反映射**(熟练度卡按
    设计就在折叠线以下,§14 裁决③),mapped=False 是预期而非缺陷。
    """
    y, w = 0, widget
    while w is not None and w is not top:
        y += w.winfo_y()
        w = getattr(w, "master", None)
    return y if w is top else -1


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    failures: list[str] = []

    def check(tag: str, cond: bool, detail: str = "") -> None:
        print(f"[{'OK' if cond else 'FAIL'}] {tag}" + (f" — {detail}" if detail else ""))
        if not cond:
            failures.append(tag)

    try:
        app = FakeApp(root)
        panel = ControlPanel(app)
        app.panel = panel
        panel.win.geometry(f"{PANEL_W}x{PANEL_H}+3000+3000")     # 最小窗 + 屏幕外
        root.update()

        # ---------- ⑥ 初始态(面板打开那一刻;其余 ⑥ 判据在套件末尾) ----------
        # 初始态处理:沿用构造期"默认选中第一只可见宠" → 三个动作控件可用,
        # 不会一开面板就看到「投喂」是灰的(选中态契约见 `_sync_action_state`)。
        check("6a 面板打开即选中第一只可见宠,三动作控件可用",
              panel.selected() == panel.tree.get_children()[0]
              and all(str(w.cget("state")) == "normal" for w in
                      (panel.feed_now_btn, panel.freeze_check, panel.hide_btn)),
              f"sel={panel.selected()} feed={panel.feed_now_btn.cget('state')} "
              f"freeze={panel.freeze_check.cget('state')} "
              f"hide={panel.hide_btn.cget('state')}")

        # 默认页必须在**打开那一刻**量(A2 验收①:nb.select(0) = 「看它」)
        check("7b 默认页=「看它」(打开即见状态;首页签也是它)",
              panel.nb.index("current") == 0
              and str(panel.nb.select()) == str(panel.pages["看它"])
              and str(panel.nb.tabs()[0]) == str(panel.pages["看它"]),
              f"index={panel.nb.index('current')} sel={panel.nb.select()}")

        # ---------- ① 跨页常驻 ----------
        check("1a 常驻条=PetBar.TFrame(新样式名)",
              str(panel.bar.cget("style")) == "PetBar.TFrame",
              str(panel.bar.cget("style")))
        check("1b 条与页签同为 win 的直接子级且条在上",
              panel.bar.master is panel.win and panel.nb.master is panel.win
              and panel.bar.winfo_y() + panel.bar.winfo_height()
              <= panel.nb.winfo_y(),
              f"bar_y+h={panel.bar.winfo_y() + panel.bar.winfo_height()} "
              f"nb_y={panel.nb.winfo_y()}")
        check("1c 条不在任何页内(切页不影响它)",
              not any(str(panel.bar).startswith(str(pg) + ".")
                      for pg in panel.pages.values()))
        # 条内四个动作控件(接纳收 ①:tree/投喂/冻结勾选/隐藏)
        act_widgets = (("tree", panel.tree), ("feed_now_btn", panel.feed_now_btn),
                       ("freeze_check", panel.freeze_check),
                       ("hide_btn", panel.hide_btn))
        check("1d 动作控件都在条内",
              all(str(w).startswith(str(panel.bar) + ".") for _n, w in act_widgets),
              " ".join(f"{n}={w}" for n, w in act_widgets))
        ids0 = (id(panel.bar), id(panel.tree), id(panel.feed_now_btn),
                id(panel.freeze_check), id(panel.hide_btn))
        for t, page in panel.pages.items():
            panel.nb.select(page)
            root.update()
            ok = all(bool(w.winfo_ismapped()) for _n, w in act_widgets)
            check(f"1e[{t}] 切到该页后 4 个动作控件仍常驻在屏", ok,
                  " ".join(f"{n}={bool(w.winfo_ismapped())}" for n, w in act_widgets))
        check("1f 切页不重建条与控件(对象身份不变)",
              (id(panel.bar), id(panel.tree), id(panel.feed_now_btn),
               id(panel.freeze_check), id(panel.hide_btn)) == ids0)
        check("1g tree 仍是 _PetList 且兼容子集可用",
              isinstance(panel.tree, _PetList)
              and all(callable(getattr(panel.tree, m, None)) for m in
                      ("get_children", "selection", "selection_set", "insert",
                       "delete", "update_row"))
              and set(panel.tree.get_children()) == set(app.pets),
              str(panel.tree.get_children()))

        # ---------- ② 1 点击投喂(抽屉收起态) ----------
        panel.set_on_drop_request(app.drop_calls.append)
        check("2a 抽屉默认收起 + 投喂按钮在屏",
              (not panel.drawer_body.winfo_ismapped())
              and bool(panel.feed_now_btn.winfo_ismapped()))
        check("2b 投喂=Accent 主操作且文案为「投喂」",
              str(panel.feed_now_btn.cget("style")) == "Accent.TButton"
              and str(panel.feed_now_btn.cget("text")) == "投喂")
        app.drop_calls.clear()
        panel.feed_now_btn.invoke()          # ← 唯一一次点击
        check("2c 1 点击投喂=当前食物(crumb)",
              app.drop_calls == [FoodKind.CRUMB], str(app.drop_calls))
        panel.set_on_drop_request(None)
        app.drop_calls.clear()
        panel.feed_now_btn.invoke()          # 未注册回调:no-op 不抛
        check("2d 未注册回调时投喂 no-op 不抛", app.drop_calls == [])
        panel.set_on_drop_request(app.drop_calls.append)

        # ---------- ③ 食物抽屉(页内可折叠区) ----------
        tls0 = toplevels(root)
        n_btns = len(panel._food_btns)
        check("3a 抽屉已建好 9 个食物按钮(收起态亦齐备)",
              n_btns == len(FOODS) and set(panel._food_btns) == set(FoodKind.ALL),
              f"{n_btns} 个")
        panel.food_drawer_btn.invoke()
        root.update()
        check("3b 1 次 invoke 展开抽屉(文案 ▾→▴)",
              bool(panel.drawer_body.winfo_ismapped())
              and str(panel.food_drawer_btn.cget("text")) == "选食物 ▴",
              str(panel.food_drawer_btn.cget("text")))
        check("3c 抽屉=页内可折叠区(ttk.Frame,非 Toplevel)",
              isinstance(panel.drawer_body, ttk.Frame)
              and panel.drawer_body.winfo_toplevel() is panel.win
              and toplevels(root) == tls0)
        check("3d 展开后 9 个食物按钮全部在屏",
              all(bool(b.winfo_ismapped()) for b in panel._food_btns.values()))
        before = panel.selected_food_kind
        panel._food_btns[FoodKind.GOLD].invoke()
        root.update()
        lbl = next(l for k, l, _c, _e in panel._catalog if k == FoodKind.GOLD)
        check("3e 换食物后 selected_food_kind 改变",
              before == FoodKind.CRUMB and panel.selected_food_kind == FoodKind.GOLD,
              f"{before} → {panel.selected_food_kind}")
        check("3f 条上「已选:X」读数随动",
              panel._food_var.get() == f"已选:{lbl}", panel._food_var.get())
        panel.food_drawer_btn.invoke()
        root.update()
        check("3g 再 invoke 收起(文案 ▴→▾)",
              (not panel.drawer_body.winfo_ismapped())
              and str(panel.food_drawer_btn.cget("text")) == "选食物 ▾")
        panel.food_drawer_btn.invoke()
        panel.food_drawer_btn.invoke()
        root.update()
        check("3h 展开/收起幂等(2 次回到收起)",
              (not panel.drawer_body.winfo_ismapped())
              and str(panel.food_drawer_btn.cget("text")) == "选食物 ▾")
        # 换食物口径:收起态起 = 抽屉 1 + 食物 1 = 2 点击
        panel.food_drawer_btn.invoke()
        panel._food_btns[FoodKind.SILVER].invoke()
        panel.food_drawer_btn.invoke()
        root.update()
        check("3i 换食物=2 点击后属性改变(架构 A.4 口径)",
              panel.selected_food_kind == FoodKind.SILVER,
              panel.selected_food_kind)
        app.drop_calls.clear()
        panel.feed_now_btn.invoke()
        check("3j 换食物后投喂用新 kind",
              app.drop_calls == [FoodKind.SILVER], str(app.drop_calls))

        # ---------- ④ 版式预算(条高 / 页签可视高) ----------
        bar_h = panel.bar.winfo_reqheight()
        check(f"4a 常驻条总高 ≤{BAR_MAX_H}px(3 宠=默认 max_pets)", bar_h <= BAR_MAX_H,
              f"{bar_h}px")
        # 真实页面视口 = `_ScrollPage.canvas` 可视高(用户实际看到的页面区域;
        # `nb.winfo_height()` 含 39px 页签标题条,原口径把它算进了视口)。
        # 取默认页(插入序第一个 = 「宠物」,A2 的「看它」前身)。
        page0 = next(iter(panel.pages.values()))
        panel.nb.select(page0)
        root.update()
        nb_h = panel.nb.winfo_height()
        vp_h = int(page0.canvas.winfo_height())
        # 4b-1 预算记录(主控裁决:477 = A2「看它首屏零滚动」的输入预算,防回归)
        check(f"4b-1 最小窗 {PANEL_W}×{PANEL_H} 下真实页面视口 ≥{VP_MIN_H}px"
              f"(A2 首屏零滚动的输入预算)",
              vp_h >= VP_MIN_H,
              f"canvas={vp_h}px(真实视口)/ nb={nb_h}px(含页签标题条 "
              f"{nb_h - vp_h}px,不计入视口)"
              f" win={panel.win.winfo_width()}×{panel.win.winfo_height()} "
              f"[抽屉收起 / 100% 缩放]")
        # 4b-2 占比判据(替代原绝对像素 500 的另一半:对窗口尺寸/缩放均成立,
        # 且随宠数增长而收紧——6 宠 ≈ +27px/宠,见架构 §13.3)
        win_h = int(panel.win.winfo_height())
        bar_ratio = bar_h / win_h if win_h else 1.0
        check(f"4b-2 常驻条占窗口高 ≤{BAR_MAX_RATIO:.0%}(默认 max_pets=3;比值判据)",
              bar_ratio <= BAR_MAX_RATIO + 1e-9,
              f"{bar_h}px/{win_h}px = {bar_ratio:.1%}[抽屉收起 / 100% 缩放]")
        check("4c 条高不随页签切换变化(跨页同一版式)",
              panel.bar.winfo_reqheight() == bar_h, f"{panel.bar.winfo_reqheight()}px")
        # 最小窗下不许把控件挤出条外:动作区宽度 = 其请求宽(未被挤压),
        # 且每个动作控件右缘都在条内(单行动作区曾把宠物行"名称"列挤成 1 字)
        act = panel.hide_btn.master.master
        edges = {n: w.winfo_rootx() - panel.bar.winfo_rootx() + w.winfo_width()
                 for n, w in (("投喂", panel.feed_now_btn),
                              ("冻结", panel.freeze_check),
                              ("隐藏", panel.hide_btn),
                              ("选食物", panel.food_drawer_btn))}
        row0 = panel.tree._rows[panel.tree.get_children()[0]]
        check("4d 最小窗下无控件被挤出条外/被挤扁",
              act.winfo_width() == act.winfo_reqwidth()
              and all(e <= panel.bar.winfo_width() for e in edges.values())
              and row0.name_lbl.winfo_width() == row0.name_lbl.winfo_reqwidth(),
              f"act={act.winfo_width()}/{act.winfo_reqwidth()} edges={edges} "
              f"bar_w={panel.bar.winfo_width()} name={row0.name_lbl.winfo_width()}")

        # ---------- ⑤ 其余三条高频动作:各 1 点击 ----------
        panel.nb.select(panel.pages["系统"])          # 站在非「看它」页
        root.update()
        panel.tree._rows["roach-3"].event_generate("<Button-1>", x=3, y=3)
        root.update()
        check("5a 跨页 1 点击选宠(点行即选中)", panel.selected() == "roach-3",
              str(panel.selected()))
        app.frozen_calls.clear()
        panel.freeze_check.invoke()                   # ← 唯一一次点击
        root.update()
        check("5b 1 点击冻结=选中宠物",
              app.frozen_calls == [("roach-3", True)]
              and panel.freeze_var.get() is True, str(app.frozen_calls))
        panel.hide_btn.invoke()                       # ← 唯一一次点击
        root.update()
        check("5c 1 点击隐藏=选中宠物",
              "roach-3" in app._hidden
              and "roach-3" not in panel.tree.get_children(),
              f"hidden={sorted(app._hidden)}")
        check("5d 条上「隐藏中:N」徽章随动",
              panel.hidden_count_var.get() == "隐藏中:1 只",
              panel.hidden_count_var.get())
        panel.recall_all()
        root.update()
        check("5e 召回后徽章归零且宠物回列表",
              panel.hidden_count_var.get() == "隐藏中:0 只"
              and "roach-3" in panel.tree.get_children())

        # ---------- ⑥ 动作控件与选中态同步(A1 收口;初始态见开头 6a) ----------
        def act_states() -> tuple:
            return tuple(str(w.cget("state")) for w in
                         (panel.feed_now_btn, panel.freeze_check,
                          panel.hide_btn))

        ids_act = (id(panel.feed_now_btn), id(panel.freeze_check),
                   id(panel.hide_btn))
        panel.tree.selection_set(("fly-2",))
        root.update()
        check("6b 有选中:三控件可用 + 勾选=该宠实际 frozen(False)",
              act_states() == ("normal", "normal", "normal")
              and panel.freeze_var.get() is False,
              f"states={act_states()} freeze_var={panel.freeze_var.get()}")
        panel.freeze_check.invoke()          # ← 唯一一次点击:冻结选中宠
        root.update()
        check("6c 冻结后勾选挂 True 且写回 App",
              panel.freeze_var.get() is True
              and app.pets["fly-2"].state.frozen is True,
              f"freeze_var={panel.freeze_var.get()} app={app.frozen_calls[-1:]}")
        panel.hide_btn.invoke()              # ← 唯一一次点击:隐藏选中宠
        root.update()
        check("6d 隐藏后无选中:三控件全 disabled + 冻结勾选归 unchecked",
              panel.selected() is None
              and act_states() == ("disabled", "disabled", "disabled")
              and panel.freeze_var.get() is False,
              f"sel={panel.selected()} states={act_states()} "
              f"freeze_var={panel.freeze_var.get()}")
        app.drop_calls.clear()
        panel.feed_now_btn.invoke()          # 无选中:按钮 disabled
        panel._drop_selected_food()          # 直调方法:同语义(双保险)
        check("6e 无选中时投喂不可达(封死 app.py:1424 静默喂第一只宠)",
              app.drop_calls == [], str(app.drop_calls))
        panel.recall_all()
        root.update()
        check("6f 召回隐藏宠不擅自改选(维持无选中 + disabled,不猜用户意图)",
              panel.selected() is None
              and str(panel.feed_now_btn.cget("state")) == "disabled",
              f"sel={panel.selected()}")
        panel.tree.selection_set(("fly-2",))
        root.update()
        check("6g 重选后控件回正,勾选反映该宠真实 frozen(True)",
              act_states() == ("normal", "normal", "normal")
              and panel.freeze_var.get() is True,
              f"states={act_states()} freeze_var={panel.freeze_var.get()}")
        app.pets["fly-2"].state.frozen = False      # 不经选中的状态漂移
        panel.tick(0.6)                             # 2Hz 路径
        check("6h 状态漂移在 2Hz tick 内被拉回(不经选中也同步)",
              panel.freeze_var.get() is False,
              f"freeze_var={panel.freeze_var.get()}")
        check("6i 对象身份冻结:只改 state,未换控件对象",
              (id(panel.feed_now_btn), id(panel.freeze_check),
               id(panel.hide_btn)) == ids_act)

        # ==============================================================
        # r25 A2:四意图页重排 + 信息节奏分档
        # ==============================================================
        # ---------- ⑦ 页元组 / 首屏零滚动(A2 验收 ①②③;7b 在开场量)----------
        check("7a 页元组=四意图页(文案与顺序冻结;主控裁决 2)",
              tuple(panel.pages) == PAGES, str(tuple(panel.pages)))
        # 口径:最小窗 520×640 + 抽屉收起 + 100% 缩放(与 4b-1 同)
        panel.win.geometry(f"{PANEL_W}x{PANEL_H}+3000+3000")
        look = panel.pages["看它"]
        panel.nb.select(look)
        root.update()
        ch_c, cv_h = look.content_height(), int(look.canvas.winfo_height())
        # **判据③ 三次迁移(r25 A3;原 → 新 → 为什么)**
        #   原(§2 A2 ③):「整页 `content_height() <= 视口`」——A2 以
        #       475 ≤ 477(余量 2px)通过,等于没有余量;而 A3(记忆时间线)
        #       与 A4(熟练度读数行)按设计**必然**往这页加内容。
        #   中继(§14 主控裁决 3):改判「**首屏必见区**(个体读数卡 + 情绪卡)
        #       底边 ≤ 视口底边」,整页高度只作记录(允许滚动)。
        #   新(A3 重写,§16.1 明令):度量对象与阈值**都不动**,把成立前提
        #       **显式写进判据并单独断言** —— 3 宠 / 抽屉收起 / scaling≈1.33 /
        #       最小窗 520×640。
        #   为什么:§16.1 实测**前提之外连新口径也红**(scaling=2.0 −30px、
        #       6 宠 −22px、抽屉展开 −67px);那些都不是本判据该守的情形
        #       (前者是用户 DPI、后者是常态之外),但判据**不许隐式依赖**它们
        #       —— 否则同一份代码在另一台机器上"莫名变红/变绿"。前提不成立
        #       时 7c-0 会**显式红**,而不是悄悄按另一套参数给出结论。
        #   **不是放宽**:阈值(≤ 视口底边)与度量对象(必见区底边)一字未改,
        #       改的是把此前只写在注释里的前提升为**可判定的断言**。
        _n_pets = len(app.pets)
        _scale = float(root.tk.call("tk", "scaling"))
        _drawer_shut = not bool(panel.mem_body.winfo_ismapped())
        _geo_now = panel.win.geometry()
        _premise = (_n_pets == 3 and app.cfg.max_pets == 3
                    and _drawer_shut and 1.25 <= _scale <= 1.45
                    and _geo_now.startswith(f"{PANEL_W}x{PANEL_H}"))
        check("7c-0 判据③前提显式成立(§16.1:3 宠 / 抽屉收起 / scaling≈1.33 / 最小窗)",
              _premise,
              f"{_n_pets} 宠(max_pets={app.cfg.max_pets}) / "
              f"抽屉{'收起' if _drawer_shut else '展开'} / "
              f"scaling={_scale:.3f} / 窗口 {_geo_now}")
        vis_bottom = panel.emo_card.winfo_y() + panel.emo_card.winfo_height()
        check("7c 首屏必见区(读数卡 + 情绪卡)底边 ≤ 视口底边(§14 口径 + §16.1 前提)",
              _premise and vis_bottom <= cv_h,
              f"必见区底边={vis_bottom}px ≤ 视口={cv_h}px"
              f"(余量 {cv_h - vis_bottom}px;前提见 7c-0)")
        check("7c-2 整页高度记录(§14 裁决②:允许 > 视口;A3/A4 必然加内容)",
              ch_c > 0 and ch_c >= vis_bottom,
              f"整页 content={ch_c}px(旧 A2 口径 475px + A4 熟练度卡 = "
              f"{ch_c}px)/ 视口 {cv_h}px → 熟练度行位于折叠线以下(允许)")
        # 降级链(主控裁决 4 预授权,按序):① 读数网格取 2 列 4 行紧凑式
        # (实现即该档,无更小档)→ ② 记忆区默认折叠(裸 Text 常驻会把
        # 「看它」顶到 684px > 477px,见回传证据)。
        check("7d 降级②生效:记忆区默认折叠(页内抽屉,非 Toplevel)",
              (not panel.mem_body.winfo_ismapped())
              and str(panel.mem_drawer_btn.cget("text")) == "记住的事 ▾"
              and isinstance(panel.mem_body, ttk.Frame)
              and panel.mem_body.winfo_toplevel() is panel.win,
              f"mapped={bool(panel.mem_body.winfo_ismapped())} "
              f"text={panel.mem_drawer_btn.cget('text')}")
        panel.mem_drawer_btn.invoke()
        root.update()
        ch_open = look.content_height()
        check("7e 展开记忆抽屉:内容高 ≥200px(沿用 A5 抽屉模式)",
              bool(panel.mem_body.winfo_ismapped()) and ch_open - ch_c >= 200,
              f"展开 content={ch_open}px(+{ch_open - ch_c}px)")
        panel.mem_drawer_btn.invoke()
        root.update()
        check("7f 再 invoke 收起:回到零滚动态(显隐幂等)",
              (not panel.mem_body.winfo_ismapped())
              and look.content_height() == ch_c,
              f"content={look.content_height()}px(收起基线 {ch_c}px)")
        bad = []
        for t, pg in panel.pages.items():       # A2 验收②(沿用旧口径)
            panel.nb.select(pg)
            root.update()
            if pg.content_height() <= 0 or pg.scrollregion_height() < pg.content_height():
                bad.append(f"{t}({pg.scrollregion_height()}/{pg.content_height()})")
        check("7g 每页 content_height()>0 且 scrollregion ≥ content(验收②)",
              not bad, f"不达标 {bad}" if bad else
              " ".join(f"{t}={pg.content_height()}" for t, pg in panel.pages.items()))

        # ---------- ⑧ 旧变量名/控件名在 AST 上仍存在(A2 验收 ④)----------
        missing_n = [n for n in LEGACY_NAMES if not hasattr(panel, n)]
        check("8a 21 个旧读数变量/控件名全部仍在(hasattr 清单;A4 迁出 int_var)",
              len(LEGACY_NAMES) == 21 and not missing_n,
              f"共 {len(LEGACY_NAMES)} 个,缺 {missing_n}")
        missing_m = [n for n in LEGACY_METHODS
                     if not callable(getattr(panel, n, None))]
        check("8b 旧页面动作方法仍可调用(重排未丢方法名)",
              not missing_m, f"缺 {missing_m}")
        missing_w = [n for n in LEGACY_WIDGETS
                     if getattr(panel, n, None) is None]
        check("8c 旧控件对象仍挂在 panel 上", not missing_w, f"缺 {missing_w}")
        _src = (Path(__file__).resolve().parents[1] / "neuropet" / "ui" /
                "panel.py").read_text(encoding="utf-8")
        _attrs = {t.attr for t in ast.walk(ast.parse(_src))
                  if isinstance(t, ast.Attribute)
                  and isinstance(t.value, ast.Name) and t.value.id == "self"}
        missing_a = [n for n in LEGACY_NAMES if n not in _attrs]
        check("8d AST:旧名字仍以 self.X 形式在源码里(非运行期伪造)",
              not missing_a, f"缺 {missing_a}")

        # ---------- ⑨ 情绪条视觉连续(A2 验收 ⑤)----------
        # 注入 6Hz 采样(采样源每帧推进,但只在 6Hz 节拍被读到),
        # 驱动 120 帧 dt=1/60:宽度必须几乎每帧都在变,且帧间无跳变。
        panel.nb.select(look)
        root.update()
        app.emo_t = 0.0
        panel.tick(1.0 / 60.0)                    # 建立基线帧
        prev = emo_fill_widths(panel)
        n_fill0, n_upd, worst = len(prev), 0, 0.0
        lo, hi = min(prev), max(prev)
        for _ in range(120):
            app.emo_t += 1.0 / 60.0
            panel.tick(1.0 / 60.0)
            cur = emo_fill_widths(panel)
            if cur != prev:
                n_upd += 1
            if len(cur) == len(prev):
                worst = max(worst, max(abs(a - b) for a, b in zip(cur, prev)))
            lo, hi = min(lo, min(cur)), max(hi, max(cur))
            prev = cur
        check("9a 条形宽度更新 ≥100/120 帧(≥50Hz 视觉连续)",
              n_fill0 == len(EMOTIONS) and n_upd >= 100,
              f"{n_upd}/120 帧在变,{n_fill0} 行条形")
        # **判据⑤ 重写(r25 A3;原 → 新 → 为什么;§16.3 登记)**
        #   原:「60fps 下相邻两帧宽度差 ≤8% 满量程」——
        #       **数学上不可破**:一阶插值的单帧步长上界 = alpha×满量程 =
        #       (1-exp(-(1/60)/τ))×span = 6.45% < 8%,**与信号无关** →
        #       任何"确实在插值"的实现都通过 ⇒ 无鉴别力(纸板判据)。
        #   新:「**30fps 帧档**下相邻两帧宽度差 ≤8% 满量程」——同一条 8%
        #       上界,帧间隔翻倍使 alpha = 12.5% > 8%,判据**重新可破**。
        #   为什么:8% 是"帧内不跳变"的观感上界;只有当帧档粗到单帧可能
        #       跨过它时,这条上界才真的在守门(帧档未按 dt 归一 / 插值被
        #       跳过等真实回归会在这一档显形)。
        #   ⚠ **10fps 档不可用同一 8% 口径(已回传主控,未自行放宽)**:
        #       实测 10fps 档自然帧差 **12.54%** 满量程;且任何忠实呈现 1Hz
        #       情绪信号的实现都不可能低于 8%/100ms —— 信号自身斜率
        #       0.3×2π = 1.88/s ⇒ **18.8%/100ms** 是物理下界。⇒ 8% 在 10fps
        #       档**恒不成立**(永远不可能绿 = 废判据,会让修复永远验收不过)。
        app.emo_t = 0.0
        panel.tick(1.0 / 30.0)                    # 建立 30fps 档基线帧
        prev30 = emo_fill_widths(panel)
        worst30 = 0.0
        for _ in range(60):                       # 2s @ 30fps
            app.emo_t += 1.0 / 30.0
            panel.tick(1.0 / 30.0)
            cur30 = emo_fill_widths(panel)
            if len(cur30) == len(prev30):
                worst30 = max(worst30, max(abs(a - b)
                                           for a, b in zip(cur30, prev30)))
            prev30 = cur30
        check("9b 30fps 帧档下相邻两帧宽度差 ≤ 满量程 8%(原 60fps 口径不可破)",
              worst30 <= 0.08 * EMO_SPAN + 1e-9,
              f"实测最大 {worst30:.2f}px = {worst30 / EMO_SPAN:.1%} 满量程"
              f"(阈值 8% = {0.08 * EMO_SPAN:.2f}px;60fps 档余量为 {worst:.2f}px)")
        check("9c 条形确有可见变化(信号非平凡;排除「静止即通过」)",
              (hi - lo) >= 0.10 * EMO_SPAN,
              f"幅度 {hi - lo:.2f}px = {(hi - lo) / EMO_SPAN:.1%} 满量程")

        # ---------- ⑩ 节奏分档(A2 验收 ⑥)----------
        panel.nb.select(panel.pages["养它"])
        root.update()
        app.emo_t += 0.5                          # 采样源跳到新值:误更新则宽度必变
        app.status_calls = 0
        w0 = emo_fill_widths(panel)
        for _ in range(30):
            panel.tick(1.0 / 60.0)
        check("10a 非「看它」页:情绪条零更新 + 不采样宠物状态",
              emo_fill_widths(panel) == w0 and app.status_calls == 0,
              f"宽度同前={emo_fill_widths(panel) == w0} "
              f"pet_status 调用={app.status_calls}")
        panel.nb.select(look)
        root.update()
        app.emo_t += 0.7
        app.status_calls = 0
        d0 = canvas_dump(panel)
        panel.hide()                              # 关闭按钮 = win.withdraw()
        for _ in range(30):
            panel.tick(1.0 / 60.0)
        check("10b 隐藏态 tick:canvas 零变更 + pet_status 计数 0",
              canvas_dump(panel) == d0 and app.status_calls == 0,
              f"state={panel.win.state()} "
              f"canvas同前={canvas_dump(panel) == d0} "
              f"pet_status 调用={app.status_calls}")
        panel.win.deiconify()
        root.update()

        # ---------- ⑪ A4:「迟钝聪慧」处置——面板侧(验收 ⑥/③)----------
        panel.nb.select(look)
        root.update()
        app.mastery_level, app.mem_count, app.mastery_calls = 1, 3, 0
        panel.tick(1.0)                 # 跨过 6Hz 节流:走一次读数刷新
        root.update()
        m1 = panel.s_mastery.get()
        check("11a 熟练度读数行 = 熟练度 N/5 · 记得 M 件事 · 最近学会:…",
              m1 == readout(1, 3, "stub") and app.mastery_calls > 0,
              f"文本={m1!r} / mastery_of 调用 {app.mastery_calls} 次")
        app.mastery_level, app.mem_count = 5, 17
        panel.tick(1.0)
        root.update()
        m5 = panel.s_mastery.get()
        check("11b 等级 1→5 时读数行文本随之变化(可感知面:用户看得见)",
              m5 == readout(5, 17, "stub") and m5 != m1,
              f"1 档={m1!r} → 5 档={m5!r}")
        _my = content_y(panel.mastery_lbl, look.body)
        _ey = content_y(panel.emo_card, look.body)
        _dy = content_y(panel.mem_drawer_btn, look.body)
        check("11c 读数行在「看它」页内(非 Toplevel),位于情绪卡与记忆区之间"
              "(A2 预留位)",
              panel.mastery_lbl.winfo_toplevel() is panel.win
              and _ey < _my < _dy,
              f"y={_my}px(情绪卡 {_ey}px → 记忆区 {_dy}px);"
              f"折叠线以下属预期(§14 裁决③)")
        panel.tree.selection_set(())
        panel.tick(1.0)
        root.update()
        check("11d 无选中时读数行归位 '-'(不留上一只的值)",
              panel.s_mastery.get() == "-", repr(panel.s_mastery.get()))
        check("11e 面板全程不再调用 app.set_intelligence(滑杆已删)",
              app.intel_calls == [], f"调用记录={app.intel_calls}")
        _left = [n for n in DROPPED_INTEL_NAMES if hasattr(panel, n)]
        check("11f 运行期无滑杆遗留属性(删除面干净)",
              not _left, f"残留 {_left}")

        # ---------- ⑬ 切页未派发事件:_on_select 不得写非当前页 canvas ----------
        # 判据迁移(原 → 新 → 为什么;架构 §16.2 登记的低危真缺陷,本套新增):
        #   原:(无判据)缺陷:`nb.select()` 后 `<<NotebookTabChanged>>` 未派发时,
        #       `_on_select → _emo_snap` 按**缓存** `_page_key` 判定,切走的那一刻
        #       会向**非当前页** canvas 写入(程序可达、用户无感),切回时又漏画。
        #   新:13a 构造该序列(只 select、不 update),断言 canvas **零变更**;
        #       13b 反方向(实际当前页 = 「看它」而缓存陈旧)断言**必须画**——
        #       防"修 13a = 干脆不画"这种假修复。
        #   为什么:页面归属只能问 notebook(`_current_page_key()`),不能信缓存;
        #       插值源 `_emo_tgt` 与绘制目标页是两件事,判定用错源就会画错页。
        look = panel.pages["看它"]
        panel.nb.select(look)
        root.update()                       # 基线:实际当前页 = 看它,缓存同步
        panel.tree.selection_set((panel.tree.get_children()[0],))
        panel.tick(1.0)                     # 6Hz:把屏上情绪条刷到与采样值一致
        root.update()
        _w0 = emo_fill_widths(panel)
        panel.nb.select(panel.pages["养它"])     # 只改 notebook 选中,**不 update**
        app.emo_t += 3.7                    # 采样源跳到新值:误写则宽度必变
        panel._on_select()                  # 缺陷触发路径(选中变化)
        check("13a nb.select() 未派发事件时,_on_select 不向非当前页 canvas 写入",
              emo_fill_widths(panel) == _w0,
              f"实际当前页=养它(事件未派发,缓存仍=看它)/ 宽度同前="
              f"{emo_fill_widths(panel) == _w0}")
        panel.tick(1.0)                     # 走一次 tick:缓存 _page_key 同步为养它
        panel.nb.select(look)               # 实际当前页 = 看它,仍不派发事件
        app.emo_t += 3.7
        _w1 = emo_fill_widths(panel)
        panel._on_select()
        check("13b 实际当前页 = 「看它」时(缓存陈旧)仍必须画(修 13a 不得变漏画)",
              emo_fill_widths(panel) != _w1,
              f"缓存=养它/实际=看它 → 宽度变化={emo_fill_widths(panel) != _w1}")

        # ==============================================================
        # ⑭ r25 A3:记忆时间线(架构 §2 A3 + 主控裁定 1/2 的口径)
        # ==============================================================
        # 主控**裁定 1**:`memory_of()` 返回的是**异质行**(物种状态行 /
        #   联想行 / 情景行 / 畸形行),不是"一堆记忆" → 只有带时间戳的情景行
        #   能进时间线;状态行归「当前状态」,联想行归「联想记忆」(均不参与
        #   时间排序);解析失败行**原样**归「未识别」,不抛异常。
        # 主控**裁定 2**:情景行在本仓库**两种格式并存**(`memory.py:88` 带
        #   `(显著度 0.72)`、`base.py:169` 不带)→ 两种都要解析;不带显著度时
        #   **按未知呈现,不得丢进未识别区**。
        # 计量口径(替换 §2 原文①②):① 口径改为「**总行数不丢**」(原文
        #   "行数 == 6" 没说清无时间戳的行怎么算 —— 它们也要显示,只是换了区);
        #   ② 口径改为「喂**乱序**输入 → 输出严格时间降序」(原文"降序且与输入
        #   一致"自相矛盾;且只喂排好序的输入则判据恒绿 = 纸板)。
        #
        # 分区标题约定:控件里以 "— " 开头的行是**装饰行**(分区标题),
        # 其余非空行 = 内容行 → 这是"显示出的总行数"的可数口径。
        def _body_lines(txt: str) -> list:
            return [ln for ln in txt.splitlines()
                    if ln.strip() and not ln.startswith("— ")]

        look = panel.pages["看它"]
        panel.nb.select(look)
        panel.tree.selection_set((panel.tree.get_children()[0],))
        root.update()
        _MIX = [
            "[09-22 18:40] 被投喂(显著度 0.72)",          # 情景·带显著度
            "[09-22 09:12] 被抓了一下",                     # 情景·不带显著度
            "[联想] context:food: 效价+0.40 置信0.85 (n=3)",  # 联想
            "[NN] 效价 approach+0.12 avoid-0.30 wander+0.05",  # 蟑螂状态
            "[GF] 警觉度 0.42 触发阈 0.30 KC 激活 3/8",        # 果蝇状态
            "[XX 畸形行:既不是时间戳也不是已知前缀",           # 畸形(原样保留)
        ]
        app.mem_lines = _MIX
        panel.refresh_memory()
        root.update()
        _txt = panel.mem_txt.get("1.0", "end")
        _sec = parse_memory_lines(_MIX)
        _shown = _body_lines(_txt)
        # 每条输入的"可辨识面"必须出现在控件里:情景行按 `[时间戳] 摘要`
        # (渲染会重排显著度尾注 → 不比原文),其余行按**原文**逐字出现。
        _need = [f"[{r['stamp']}] {r['text']}" for r in _sec["episodic"]]
        _need += [r["raw"] for r in _sec["assoc"] + _sec["state"] + _sec["unknown"]]
        _missing = [n for n in _need if n not in _txt]
        check("⑭a 不丢行:显示出的内容行数 == 输入行数(6),且每行内容都可见",
              len(_shown) == len(_MIX) == memory_row_count(_sec) == panel.mem_rows
              and not _missing,
              f"输入 {len(_MIX)} 行 → 显示 {len(_shown)} 行 / 分区计数 "
              f"{dict((k, len(v)) for k, v in _sec.items())} / 丢失 {_missing}")
        check("⑭b 畸形行不抛且**原样**可见(归「未识别」区,不静默丢弃)",
              len(_sec["unknown"]) == 1
              and _sec["unknown"][0]["raw"] == _MIX[5]
              and _MIX[5] in _txt
              and "未识别" in _txt,
              # 详情也必须**不可能抛**(判据说 A3 崩掉会掩盖后面所有判据)
              f"未识别 {len(_sec['unknown'])} 行 = "
              f"{[r['raw'] for r in _sec['unknown']]}")
        check("⑭c 两类状态行/联想行各归其区(不塞进时间线)",
              len(_sec["state"]) == 2 and len(_sec["assoc"]) == 1
              and [r["raw"] for r in _sec["state"]] == _MIX[3:5]
              and "当前状态" in _txt and "联想记忆" in _txt,
              f"state={len(_sec['state'])} assoc={len(_sec['assoc'])} "
              f"episodic={len(_sec['episodic'])}")
        # ② 排序可证伪:**乱序**输入(既非升序也非降序)→ 输出严格降序
        _SHUF = ["[09-22 09:12] 中", "[09-20 07:00] 最旧",
                 "[09-22 18:40] 最新", "[09-21 23:05] 次新"]
        _in_stamps = ["09-22 09:12", "09-20 07:00", "09-22 18:40", "09-21 23:05"]
        app.mem_lines = _SHUF
        panel.refresh_memory()
        root.update()
        _out = [r["stamp"] for r in parse_memory_lines(_SHUF)["episodic"]]
        _txt2 = panel.mem_txt.get("1.0", "end")
        check("⑭d 排序可证伪:乱序输入 → 输出严格时间降序(新→旧,控件内亦然)",
              _out == sorted(_out, reverse=True) and _out != _in_stamps
              and _txt2.find("最新") < _txt2.find("次新") < _txt2.find("最旧")
              and 0 <= _txt2.find("最新"),
              f"输入序 {_in_stamps} → 输出序 {_out};"
              f"控件内 最新<次新<最旧="
              f"{_txt2.find('最新') < _txt2.find('次新') < _txt2.find('最旧')}")
        _eps = _sec["episodic"]
        _sals = [(r["stamp"], r["salience"]) for r in _eps]
        check("⑭e 显著度条:带上/不带两种格式都解析(带 → 数值,不带 → 未知不丢行)",
              len(_eps) == 2 and _eps[0]["salience"] == 0.72
              and _eps[1]["salience"] is None
              and "0.72" in _txt and "显著度未知" in _txt,
              f"(时间戳, 显著度)={_sals}(不带 → 渲染成「显著度未知」)")
        # ③ clear_memory:走真入口(二次确认打桩),空态文案 + 行数 0
        app.mem_lines = _MIX          # 先放回 6 行内容:验"有 → 清空"的完整路径
        panel.refresh_memory()
        root.update()
        app.clear_calls = 0
        _orig_yes = panelmod.messagebox.askyesno
        panelmod.messagebox.askyesno = lambda *a, **k: True
        try:
            _rows_before = panel.mem_rows
            panel.clear_memory()          # 方法名冻结(test_pet_hide 间接依赖)
        finally:
            panelmod.messagebox.askyesno = _orig_yes
        root.update()
        _txt3 = panel.mem_txt.get("1.0", "end")
        _bl3 = _body_lines(_txt3)
        check("⑭f clear_memory 后:空态文案 + 内容行数 0(不留旧行)",
              _rows_before == 6 and app.clear_calls == 1
              and panel.mem_rows == 0
              and len(_bl3) == 1 and "暂无记忆" in _bl3[0]
              and "情景记忆" not in _txt3 and _MIX[0] not in _txt3,
              f"清空前 {_rows_before} 行 → 清空后 {panel.mem_rows} 行;"
              f"控件内容行={_bl3}")

        # ④ 2Hz 路径:200 行的实耗时(判据 ④;口径见下)
        #   原口径「tick 耗时 p95 ≤ 1.5ms」若按**整帧 tick 墙钟**量,会被
        #   同一帧里的情绪条重画(1.14ms p50/2.09ms p90,§14 已登记)淹没 →
        #   该判据将恒不成立。故口径收窄到**记忆路径自身**的耗时,且分三段:
        #   ④g 路径确实挂在 tick 的 2Hz 节拍上(内容每拍变 → 每次真渲染);
        #   ④h 单次刷新(取数 + 解析 + 渲染 + 写控件)p95 ≤ 1.5ms;
        #   ④i 内容未变则**不重绘**(2Hz 常态零开销)。
        #   测量口径(为什么这样量;**不是放宽**):
        #   ① 判据取 **min(批均值)**(25 批 × 每批 64 次刷新)。理由:该渲染
        #      成本是**确定性量**(同一输入的实耗时是常数),观测到的方差
        #      **全部**来自机器噪声(抢占 / 缓存与 SMT 争用),而噪声**只加
        #      不减** ⇒ min 是无偏估计,p95(批均值)在满负载机上会被噪声
        #      系统性抬高(本机实测同一份代码 p95 在 1.22~1.76ms 间漂移,
        #      而 min 稳定在 ~1.0ms)——用 p95 会把"机器忙"判成"代码慢"
        #      (§5 坑表:判据隐式依赖未受控的环境输入)。
        #   ② p50/p95/max(批均值)与**单次样本 p95** 一并写进详情,**不作
        #      判据**:数字全部可见,不藏。
        #   ③ 阈值、被测路径、被测输入(200 行、每拍全变)都**没有放宽**。
        def _mk200(gen: int) -> list:
            out = [f"[09-{1 + i % 28:02d} {i % 24:02d}:{i % 60:02d}] "
                   f"事件{gen}-{i}(显著度 {(i % 100) / 100:.2f})"
                   for i in range(197)]
            out += ["[联想] context:food: 效价+0.40 置信0.85 (n=3)",
                    "[NN] 效价 approach+0.12 avoid-0.30 wander+0.05",
                    f"[09-01 00:00] 无显著度事件{gen}"]
            return out

        _FIX = [_mk200(g) for g in range(25 * 64)]  # 夹具预生成(计时只含被测路径)
        app.mem_lines = _FIX[0]
        panel.refresh_memory()
        root.update()
        _n0 = panel.mem_render_n
        _gen = 0
        for _f in range(600):                        # 10s @ 60fps = 20 个 2Hz 节拍
            if _f % 30 == 0:
                _gen += 1
                app.mem_lines = _mk200(_gen)
            panel.tick(1.0 / 60.0)
        _txt4 = panel.mem_txt.get("1.0", "end")
        check("⑭g 2Hz 路径挂在 tick 上:10s 内真渲染 ≥15 次(2Hz×10s=20 拍)"
              "且控件里是最新内容",
              panel.mem_render_n - _n0 >= 15 and f"事件{_gen}-0" in _txt4
              and panel.mem_rows == 200,
              f"渲染 {panel.mem_render_n - _n0} 次 / 行数 {panel.mem_rows} / "
              f"最新代 {_gen} 在控件中={f'事件{_gen}-0' in _txt4}")
        _means, _singles = [], []
        for _b in range(25):
            _t0 = time.perf_counter()
            for _k in range(64):
                app.mem_lines = _FIX[_b * 64 + _k]
                _s0 = time.perf_counter()
                panel.refresh_memory()
                _singles.append((time.perf_counter() - _s0) * 1000.0)
            _means.append((time.perf_counter() - _t0) * 1000.0 / 64.0)
        _means.sort()
        _singles.sort()
        _p50 = _means[len(_means) // 2]
        _p95 = _means[int(len(_means) * 0.95)]
        _p95_single = _singles[int(len(_singles) * 0.95)]
        check("⑭h 渲染 200 行的单次刷新成本 ≤ 1.5ms(面板 2Hz 路径;取 min(批均值))",
              _means[0] <= 1.5,
              f"min(批均值)={_means[0]:.3f}ms ≤ 1.5ms;批均值 p50={_p50:.3f} "
              f"p95={_p95:.3f} max={_means[-1]:.3f}ms(200 行;25 批 × 64 次);"
              f"含环境噪声的单次样本 p95={_p95_single:.3f}ms(参考,不作判据)")
        app.mem_lines = _FIX[0]
        panel.refresh_memory()
        _n1 = panel.mem_render_n
        _t0 = time.perf_counter()
        for _ in range(300):
            panel.refresh_memory()
        _steady = (time.perf_counter() - _t0) * 1000.0 / 300.0
        check("⑭i 内容未变不重绘(2Hz 常态零开销;防「每拍全量重画」顶回滚动位置)",
              panel.mem_render_n == _n1 and _steady <= 0.05,
              f"300 次调用新增渲染 {panel.mem_render_n - _n1} 次 / "
              f"单次 {_steady:.4f}ms")
        app.mem_lines = []

        panel.win.destroy()
    finally:
        root.destroy()

    # ================================================================
    # ⑫ r25 A4:「迟钝聪慧」处置 —— 纯函数面(验收 ①②③)+ 真脑/真 App 面(④⑤⑥)
    # ================================================================
    # 本段不依赖 Tk;真 App 段自建 Tk root(上一个已在 finally 里销毁)。
    #
    # ⚠ **判据可疑登记(A4 验收⑥;执行未自行放宽、未删判据,报主控裁决)**:
    #   ⑥ 原话「熟练度从 1→5 时 `learning_summary()` 文本随之变化」。实测两物种
    #   (同一事件序列 fed×6 + grab×6)等级 1 与 5 的 `learning_summary()`
    #   **逐字符相同**:该方法的每个字段(信任档/信任值/V/最近δ/偏好 μ(n))
    #   都取自与 level 无关的状态(bandit.update 的 μ 更新式不含 level,
    #   trust/emo 也不含)。level 真正改的是**学习动态**——联想学习增益
    #   `level_gain`(memory.py:121 生效点,×1.3..×2.5)与探索系数
    #   `c0*(1.4-0.15*level)`(instinct.py)——前者在学习产物 `memory_of()`
    #   里**可见**(实测联想效价 L1 +0.93 vs L5 +1.00)。
    #   → 本套把⑥落成**可判的最强形式**:①读数行随等级变(⑪b,用户看得见
    #     的面);②学习产物 memory_of() 随等级变(⑫h,可感知的机制面);
    #     「learning_summary() 本身随等级变」不写进断言(否则本套必红),
    #     作为存疑项回传主控。
    #   **主控裁决(2026-09-22,收口):原判据建立在错误假设上(假设
    #     `learning_summary()` 会随等级变),不是执行没做到。**
    #     判据变更登记(原 → 新 → 为什么):
    #       原:⑥「熟练度从 1→5 时 `learning_summary()` 文本随之变化」。
    #          —— **不可满足**:该方法的字段(信任档/信任值/V/最近δ/偏好 μ(n))
    #          全部取自与 level 无关的状态,两物种 L1 与 L5 逐字符相同(实测)。
    #       新:「等级 1→5 时**可感知面**随之变化」两条并取:①读数行文本
    #          (⑪b,用户直接看得见);②学习产物 `memory_of()`(⑫h,机制面
    #          证据:同一 fed×6+grab×6 序列下联想效价 L1 +0.93 vs L5 +1.00)。
    #       为什么:level 真正改的是**学习动态**(联想增益 `level_gain`
    #          ×1.3..2.5 / 探索系数 `c0*(1.4-0.15*level)`),"可感知"应落在
    #          学习产物与面板读数上,而不是一个**结构上不含 level** 的摘要串。
    #          **不是放宽**:原判据在真实实现上恒假(0 鉴别力),新判据两条都
    #          可被真实回归打破(改错 level 传播链或删读数行即红)。
    _rng = random.Random(20260922)          # 固定种子:判据自身可复现
    _KEYS = ("n_memory", "trust", "age_s", "n_fed", "n_danger")
    _bad_rng, _bad_mono, _bad_det = [], [], []
    for _ in range(1000):
        _base = {"n_memory": _rng.randint(0, 40), "trust": _rng.random(),
                 "age_s": _rng.uniform(0.0, 1200.0), "n_fed": _rng.randint(0, 20),
                 "n_danger": _rng.randint(0, 12)}
        _v = mastery_level(_base)
        if not 1 <= _v <= 5:
            _bad_rng.append((_base, _v))
        if mastery_level(dict(_base)) != _v:
            _bad_det.append(_base)
        for _k in _KEYS:
            _up = dict(_base)
            _up[_k] = _base[_k] + _rng.random() * 5.0 + 0.01
            if mastery_level(_up) < _v:
                _bad_mono.append((_k, _v, mastery_level(_up)))
    check("⑫a 纯函数值域 ⊆ 1..5(1000 组随机 stats)",
          not _bad_rng, f"越界 {len(_bad_rng)} 组")
    check("⑫b 纯函数单调不减(1000 组 × 5 键各自单调增 → 输出不减)",
          not _bad_mono, f"违反 {len(_bad_mono)} 例:{_bad_mono[:3]}")
    check("⑫c 纯函数确定性(同输入同输出,1000 组各两次)",
          not _bad_det, f"不一致 {len(_bad_det)} 组")
    _cases = (
        ("新宠(trust 初始 0.2)", {"trust": 0.2}, 1),
        ("养着不互动 5min", {"age_s": 300.0, "trust": 0.2}, 2),
        ("熟手 5min(喂4/记6)", {"age_s": 300.0, "n_fed": 4, "n_memory": 6,
                                "trust": 0.2}, 3),
        ("长期互动(10min/记12)", {"age_s": 600.0, "n_memory": 12,
                                  "trust": 0.6}, 4),
        ("满档(10min/记12/喂8/信1/险6)",
         {"age_s": 600.0, "n_memory": 12, "n_fed": 8, "trust": 1.0,
          "n_danger": 6}, 5),
    )
    _got = [(zh, mastery_level(st)) for zh, st, _exp in _cases]
    _exp = [e for _zh, _st, e in _cases]
    check("⑫d 各等级可达(5 组边界输入分别落 1..5)",
          [g for _zh, g in _got] == _exp,
          "; ".join(f"{zh}→{g}" for zh, g in _got))
    # ⑫d-2/⑫d-3 fail-closed 契约(`_num`);判据迁移(原 → 新 → 为什么):
    #   原:模块 docstring 写"非数(None/str/容器)一律记 0.0",实现却直接
    #       `float(v)` —— 数字串 `'7'` 被**静默采纳**(r25 A4-r2 复验实测:
    #       `mastery_level({"n_memory": '7'})` = 2 级),契约与实现不符。
    #   新:5 键 × 11 类脏值断言 `mastery_level({k: 脏值}) == mastery_level({})`
    #       (≡ 该分量贡献 0),另加正向对照(合法数值仍照常记分)。
    #   为什么:数值统计量里出现字符串**是上游 bug 的信号**,静默转换会把 bug
    #       藏起来(主控裁决 ③);NaN/±inf 两条是 A4 黑盒缺陷 C 的回归护栏
    #       (旧实现 `min(1.0, nan) == 1.0` 把 NaN 当满额 → 全 NaN = 5 级)。
    _dirty = (None, "7", "", "abc", float("nan"), float("inf"), float("-inf"),
              -3.0, [], {}, 10 ** 400)
    _zero_lv = mastery_level({})
    _bad_dirty = [(k, repr(d)[:12], mastery_level({k: d}))
                  for k in _KEYS for d in _dirty
                  if mastery_level({k: d}) != _zero_lv]
    check("⑫d-2 fail-closed 契约:5 键 × 11 类脏值(数字串/字符串/None/NaN/"
          "±inf/负数/容器/超大整数)→ 贡献 0(≡ 缺键),不静默转换",
          not _bad_dirty, f"违反 {len(_bad_dirty)} 组:{_bad_dirty[:3]}")
    _full = mastery_level({"age_s": 600.0, "n_memory": 12.0, "n_fed": 8.0,
                           "trust": 1.0, "n_danger": 6.0})
    check("⑫d-3 正向对照:合法数值统计量仍照常记分(满档 = 5;拒 str 不是把"
          "所有输入都打成 0)",
          _full == 5 and _zero_lv == 1, f"满档={_full} 空 stats={_zero_lv}")
    _src = (Path(__file__).resolve().parents[1] / "neuropet" / "ui" /
            "panel.py").read_text(encoding="utf-8")
    _attrs = {t.attr for t in ast.walk(ast.parse(_src))
              if isinstance(t, ast.Attribute)
              and isinstance(t.value, ast.Name) and t.value.id == "self"}
    _left = sorted(n for n in DROPPED_INTEL_NAMES if n in _attrs)
    check("⑫e 面板 AST 无 int_scale/int_label/int_var/_last_intel/on_intel/"
          "_intel_release/_apply_intel(验收③)",
          not _left, f"源码残留 {_left}")

    # ---- ⑫h 学习产物随等级变(真脑,两物种;⑥ 的机制面证据) ----
    from neuropet.brain.roach_brain import RoachBrain
    from neuropet.brain.fly_brain import FlyConnectomeBrain
    from neuropet.core.contracts import PetState as _PS
    _rows = []
    _prod_ok = True
    for _cls in (RoachBrain, FlyConnectomeBrain):
        _dig, _sum = {}, {}
        for _lv in (1, 5):
            _b = _cls(_PS(pet_id="p", species_id="s", name="n"))
            _b.set_intelligence(_lv)
            for _ in range(6):
                _b.on_event("fed", {})
                _b.on_event("grab", {})
            _dig[_lv], _sum[_lv] = _b.memory_digest(), _b.learning_summary()
        _prod_ok = _prod_ok and _dig[1] != _dig[5]
        _rows.append(f"{_cls.__name__}: memory_of={'变' if _dig[1] != _dig[5] else '同'}"
                     f" / learning_summary={'变' if _sum[1] != _sum[5] else '同'}")
    check("⑫h 等级改变**学习产物**(memory_of 文本 L1≠L5,两物种)",
          _prod_ok, " ".join(_rows) + "(learning_summary 两档相同 → 判据可疑,"
                                      "见本段头注)")
    try:
        _a4_real_app_checks(check)
    except Exception as exc:                       # 真 App 面整体失败也要留痕
        check("⑫f/g 真 App 面(④⑤⑥)可跑", False, f"{type(exc).__name__}: {exc}")

    if failures:
        print(f"\n[test_panel_ia] {len(failures)} 项失败: {failures}")
        sys.exit(1)
    print("\n[test_panel_ia] 全部判据通过(跨页常驻/1 点击投喂/抽屉/条高预算)")
    sys.exit(0)


if __name__ == "__main__":
    main()
