# -*- coding: utf-8 -*-
"""面板设计系统(r24 全面重做 UI):配色令牌 + 字体层级 + ttk 样式装配。

设计判读:这是一台"观察活体昆虫神经系统"的仪器面板。语言取标本/仪器一路
——冷中性底、单一强调色、数据前置、装饰克制。刻意避开当前 AI 生成界面的
默认外形(奶油底+衬线标题+陶土强调、近黑底+荧光青柠、等宽字体标数据、
满屏同圆角卡片、每段标题上方的全大写小标签),这些在 brief 未指定时是
默认套路而非选择。

    INK   主文字      PAPER 面板底     CARD  读出面/卡片
    LINE  分隔线      ACCENT 唯一强调色(器物青绿)

对比度:INK/PAPER ≈ 15:1,INK_SOFT/PAPER ≈ 7.5:1,INK_FAINT/PAPER ≈ 3.6:1
(仅用于单位与提示,不承载关键信息);ACCENT 上的文字用 ACCENT_INK。

用法:ControlPanel.__init__ 里 `apply_theme(ttk.Style(self.win))`。
本模块不引入任何新依赖,只做样式装配。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ------------------------------------------------------------------ 配色令牌
INK = "#14181f"          # 主文字(近黑)
INK_SOFT = "#4a5560"     # 次级文字/正文说明
INK_FAINT = "#8b95a1"    # 单位、轨道标注、禁用态
PAPER = "#f4f6f8"        # 面板底(冷浅灰)
CARD = "#ffffff"         # 读出面/卡片
LINE = "#dde3e9"         # 分隔线/边框
LINE_SOFT = "#eaeef2"    # 更浅的分隔(表格行线)
ACCENT = "#0f7274"       # 唯一强调色(器物青绿)
ACCENT_INK = "#0a5457"   # 强调色深版(选中文字/hover)
ACCENT_TINT = "#dfeeee"  # 强调色极浅底(选中行)
TRACK = "#e7ecf1"        # 进度/情绪条轨道
ALERT = "#c0392b"        # 告警红(投喂倒计时告急)
ALERT_TINT = "#fbeaea"   # 告警浅底

# ------------------------------------------------------------------ 字体层级
# 单一字族(microsoft 雅黑 UI);层级靠字号+字重,不引入第二字族,
# 也不用等宽字体标数据(那正是 AI 生成界面的常见痕迹之一)。
FAMILY = "Microsoft YaHei UI"
FONT_SECTION = (FAMILY, 9, "bold")    # 卡片标题
FONT = (FAMILY, 9)                    # 正文
FONT_BOLD = (FAMILY, 9, "bold")
FONT_SMALL = (FAMILY, 8)              # 辅助说明/单位
FONT_READING = (FAMILY, 10)           # 仪表读数(数值本身值得大一档)

# 情绪条几何(canvas 像素;状态页)
BAR_X0, BAR_X1 = 62, 322
BAR_H = 13
ROW_H = 26


def apply_theme(style: ttk.Style) -> None:
    """把设计系统装进 ttk.Style。幂等,可重复调用。

    先用 clam:这是唯一在所有状态(选中/按下/禁用)都完全遵从样式表、
    可逐项配色的内置主题;vista/xpnative 走原生 UxTheme 绘制,大多数
    颜色项被忽略,无法承载自定义视觉。
    """
    try:
        style.theme_use("clam")
    except tk.TclError:                     # 极端精简的 Tk 构建:保持现主题
        pass

    # ---- 基底:页面与文本 ----
    style.configure(".", background=PAPER, foreground=INK,
                    font=FONT, borderwidth=0, focuscolor=ACCENT)
    style.configure("TFrame", background=PAPER)
    style.configure("Page.TFrame", background=PAPER)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("Card.TLabel", background=CARD, foreground=INK)
    style.configure("Hint.TLabel", background=PAPER, foreground=INK_SOFT,
                    font=FONT_SMALL)
    style.configure("CardHint.TLabel", background=CARD, foreground=INK_SOFT,
                    font=FONT_SMALL)
    style.configure("Section.TLabel", background=PAPER, foreground=INK,
                    font=FONT_SECTION)
    style.configure("Reading.TLabel", background=PAPER, foreground=INK,
                    font=FONT_READING)
    style.configure("Unit.TLabel", background=PAPER, foreground=INK_FAINT,
                    font=FONT_SMALL)

    # ---- 卡片:用 Card.TFrame + 顶栏标题,不用 TLabelframe 的浮雕边框 ----
    style.configure("TLabelframe", background=PAPER, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, relief="solid",
                    borderwidth=1)
    style.configure("TLabelframe.Label", background=PAPER, foreground=INK_SOFT,
                    font=FONT_SECTION)

    # ---- 页签:激活页 = 白底 + 强调色文字与顶部压条 ----
    style.configure("TNotebook", background=PAPER, bordercolor=LINE,
                    lightcolor=PAPER, darkcolor=PAPER, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=PAPER, foreground=INK_FAINT,
                    padding=(15, 7), font=FONT, bordercolor=PAPER,
                    lightcolor=PAPER, darkcolor=PAPER)
    style.map("TNotebook.Tab",
              background=[("selected", CARD), ("active", LINE_SOFT)],
              foreground=[("selected", ACCENT_INK), ("active", INK)],
              # 选中页只让"顶/左"压条变强调色(clam 的 lightcolor),
              # 底/右与白底同色 → 观感上是一条落在激活页上的强调横线
              lightcolor=[("selected", ACCENT)],
              darkcolor=[("selected", CARD)],
              bordercolor=[("selected", CARD)])

    # ---- 按钮 ----
    style.configure("TButton", background=CARD, foreground=INK, font=FONT,
                    bordercolor=LINE, lightcolor=CARD, darkcolor=CARD,
                    relief="solid", borderwidth=1, padding=(9, 4),
                    focusthickness=0)
    style.map("TButton",
              background=[("pressed", ACCENT_TINT), ("active", LINE_SOFT),
                          ("disabled", PAPER)],
              foreground=[("disabled", INK_FAINT)],
              bordercolor=[("active", ACCENT)])
    # 主操作(投喂):唯一用实心强调色的控件——把"大胆"用在一处
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    font=FONT_BOLD, bordercolor=ACCENT, lightcolor=ACCENT,
                    darkcolor=ACCENT, padding=(12, 5))
    style.map("Accent.TButton",
              background=[("pressed", ACCENT_INK), ("active", ACCENT_INK),
                          ("disabled", LINE)],
              foreground=[("disabled", INK_FAINT)])
    # 危险操作(清除记忆)
    style.configure("Danger.TButton", background=CARD, foreground=ALERT,
                    font=FONT, bordercolor=LINE, lightcolor=CARD,
                    darkcolor=CARD, padding=(9, 4))
    style.map("Danger.TButton",
              background=[("pressed", ALERT_TINT), ("active", ALERT_TINT)],
              foreground=[("disabled", INK_FAINT)],
              bordercolor=[("active", ALERT)])
    style.configure("Info.TButton", background=PAPER, foreground=ACCENT_INK,
                    font=FONT_BOLD, bordercolor=LINE, lightcolor=PAPER,
                    darkcolor=PAPER, padding=(3, 0), width=2)
    style.map("Info.TButton",
              background=[("active", ACCENT_TINT)],
              bordercolor=[("active", ACCENT)])

    # ---- 勾选 ----
    style.configure("TCheckbutton", background=PAPER, foreground=INK, font=FONT,
                    focuscolor=PAPER, indicatorcolor=CARD)
    style.map("TCheckbutton",
              background=[("active", PAPER)],
              foreground=[("disabled", INK_FAINT)],
              indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_INK)])
    # 投喂:常规 / 告急两态(面板按剩余秒数切样式名;名字为冻结契约)
    style.configure("Feed.TCheckbutton", background=PAPER, foreground=INK,
                    font=FONT, focuscolor=PAPER, indicatorcolor=CARD)
    style.map("Feed.TCheckbutton",
              background=[("active", PAPER)],
              indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_INK)])
    style.configure("FeedUrgent.TCheckbutton", background=PAPER,
                    foreground=ALERT, font=FONT_BOLD, focuscolor=PAPER,
                    indicatorcolor=CARD)
    style.map("FeedUrgent.TCheckbutton",
              background=[("active", PAPER)],
              indicatorcolor=[("selected", ALERT)])

    # ---- 下拉框(含弹出列表:Listbox 不经 ttk 样式,须写选项库)----
    style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    foreground=INK, arrowcolor=INK_SOFT, bordercolor=LINE,
                    lightcolor=CARD, darkcolor=CARD, padding=(6, 3),
                    selectbackground=CARD, selectforeground=INK)
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD), ("disabled", PAPER)],
              foreground=[("disabled", INK_FAINT)],
              bordercolor=[("focus", ACCENT), ("hover", ACCENT)],
              arrowcolor=[("active", ACCENT_INK)])

    # ---- 滑杆 ----
    style.configure("Horizontal.TScale", background=PAPER, troughcolor=TRACK,
                    bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT,
                    sliderrelief="flat", sliderlength=18, gripcount=0)
    style.map("Horizontal.TScale",
              lightcolor=[("active", ACCENT_INK)],
              darkcolor=[("active", ACCENT_INK)])

    # ---- 进度条(饱食度)----
    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=TRACK, bordercolor=TRACK, lightcolor=ACCENT,
                    darkcolor=ACCENT, thickness=8)

    # ---- 表格(插件清单)----
    style.configure("Treeview", background=CARD, fieldbackground=CARD,
                    foreground=INK, font=FONT, rowheight=22, bordercolor=LINE,
                    lightcolor=CARD, darkcolor=CARD)
    style.map("Treeview",
              background=[("selected", ACCENT_TINT)],
              foreground=[("selected", INK)])
    style.configure("Treeview.Heading", background=PAPER, foreground=INK_SOFT,
                    font=FONT_SECTION, relief="flat", padding=(6, 4),
                    bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", LINE_SOFT)])

    # ---- 滚动条 ----
    style.configure("Vertical.TScrollbar", background=LINE_SOFT,
                    troughcolor=PAPER, bordercolor=PAPER, arrowcolor=INK_FAINT,
                    lightcolor=LINE_SOFT, darkcolor=LINE_SOFT, gripcount=0,
                    arrowsize=12)
    style.map("Vertical.TScrollbar",
              background=[("active", ACCENT), ("pressed", ACCENT_INK)],
              lightcolor=[("active", ACCENT), ("pressed", ACCENT_INK)],
              darkcolor=[("active", ACCENT), ("pressed", ACCENT_INK)])

    # ---- 食物选择器色块按钮 ----
    style.configure("Food.TButton", background=CARD, foreground=INK, font=FONT,
                    bordercolor=LINE, lightcolor=CARD, darkcolor=CARD,
                    padding=(6, 3))
    style.map("Food.TButton",
              background=[("pressed", ACCENT_TINT), ("active", LINE_SOFT)],
              bordercolor=[("active", ACCENT)])
    style.configure("FoodSel.TButton", background=ACCENT_TINT, foreground=ACCENT_INK,
                    font=FONT_BOLD, bordercolor=ACCENT, lightcolor=ACCENT_TINT,
                    darkcolor=ACCENT_TINT, padding=(6, 3))
    style.map("FoodSel.TButton",
              background=[("pressed", ACCENT_TINT), ("active", ACCENT_TINT)])

    # ---- 常驻个体条(r25 A1):白底仪表条 + 行内 i 芯片 + 抽屉触发器 ----
    # 条是「跟着选中个体走的活体仪表」面:用 CARD 与页面底 PAPER 拉开层级。
    # 条内的行/标签/勾选必须配同底样式,否则会在白底上露出 PAPER 色方补丁。
    style.configure("PetBar.TFrame", background=CARD)
    style.configure("PetBar.TLabel", background=CARD, foreground=INK,
                    font=FONT)
    style.configure("PetBar.TCheckbutton", background=CARD, foreground=INK,
                    font=FONT, focuscolor=CARD, indicatorcolor=CARD)
    style.map("PetBar.TCheckbutton",
              background=[("active", CARD)],
              indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT_INK)])
    # 行内 "i" 芯片(原 Info.TButton 是 PAPER 底,进条后需白底版)
    style.configure("Chip.TButton", background=CARD, foreground=ACCENT_INK,
                    font=FONT_BOLD, bordercolor=LINE, lightcolor=CARD,
                    darkcolor=CARD, padding=(3, 0), width=2)
    style.map("Chip.TButton",
              background=[("active", ACCENT_TINT)],
              bordercolor=[("active", ACCENT)])
    # 食物抽屉触发器(「选食物 ▾/▴」)
    style.configure("Drawer.TButton", background=CARD, foreground=ACCENT_INK,
                    font=FONT, bordercolor=LINE, lightcolor=CARD,
                    darkcolor=CARD, padding=(8, 2))
    style.map("Drawer.TButton",
              background=[("pressed", ACCENT_TINT), ("active", LINE_SOFT)],
              bordercolor=[("active", ACCENT)])

    # ---- tooltip(深底浅字,样式名冻结)----
    style.configure("Tip.TFrame", background=INK, relief="flat")
    style.configure("Tip.TLabel", background=INK, foreground="#eef2f6",
                    font=FONT_SMALL)
    style.configure("TipTitle.TLabel", background=INK, foreground="#ffffff",
                    font=FONT_BOLD)


def apply_option_db(root: tk.Misc) -> None:
    """写 Tk 选项库:ttk 样式表管不到的弹出列表与文本选择色。

    Combobox 的下拉列表是经典 Listbox(不经 ttk 样式),文本控件的选中
    底色由 selectBackground 控制——两处不设的话,重做后仍会露出系统默认
    的蓝白配色,是"半成品感"最常见的来源。
    """
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_TINT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_INK)
    root.option_add("*TCombobox*Listbox.font", FONT)
    root.option_add("*selectBackground", ACCENT_TINT)
    root.option_add("*selectForeground", ACCENT_INK)
