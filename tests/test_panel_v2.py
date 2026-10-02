# -*- coding: utf-8 -*-
"""控制面板第十轮重构验收(test_panel_v2)。运行:python tests/test_panel_v2.py

对应骨架清单与验收门槛(headless 真实 tk,面板定位到屏幕外,不干扰桌面):
 1) 四意图页滚动容器(r25 A2:六页 → 看它/逗它/养它/系统):每页
    Canvas+ttk.Scrollbar 且 sb↔canvas.yview 互绑;
    scrollregion 高度 = 内容 reqheight;内容超窗时 scroll_to_end 后
    yview 到底(bottom>0.99),内容不超窗时无需滚动(bottom==1.0);
    窗口 minsize = 520×640。
 2) tooltip 内容 API(bubbles.tooltip_text):状态 dict → 非空文案,
    情绪四维(愉悦/恐惧/好奇/信任)数值都出现;空 dict 安全兜底;
    含能量/当前行为/一条引导文案。
 3) 食物选择器:selected_food_kind 默认 = FOODS 第一种;invoke 另一
    食物按钮后属性变化;注册 on_drop_request 后点「投喂」以选中 kind
    回调;9 种食物全部渲染且悬停文案(效果描述)齐备;选中态高亮
    (FoodSel 样式 + pressed 态)。
 4) tooltip 复用同一 Toplevel(不泄漏):连续两次 show 为同一对象,
    hide() 后 withdraw;自动关闭计时器(2500ms)已挂。
 5) 旧接线兼容:tree.get_children/selection、hidden_list、隐藏计数等
    (test_pet_hide 依赖面)在新列表控件上语义不变。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk
from tkinter import ttk

from neuropet.core.config import AppConfig
from neuropet.core.contracts import Behavior, MovementMode
from neuropet.feeding import FOODS, FoodKind, food_catalog
from neuropet.ui import bubbles as B
from neuropet.ui import panel_theme as PT
from neuropet.ui.panel import (FOOD_COLS, TIP_AUTO_CLOSE_MS, _ScrollPage,
                               ControlPanel)

PANEL_W, PANEL_H = 520, 640   # 骨架清单裁决的最小窗口尺寸


# ---------------- Fake App(真实 tk 面板 + 最小 App 表面) ----------------
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
    """ControlPanel 依赖的 App 表面最小桩(真实方法均为安全 no-op)。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = AppConfig()
        self.pets = {"roach-1": FakePet("roach-1"),
                     "fly-2": FakePet("fly-2", "species.fly")}
        self._hidden: dict = {}
        self.registry = FakeRegistry()
        self.feeding = False
        self._feeding_until = None
        self._external_reports: list = []
        self._static_dirty = False
        self.world = type("W", (), {"clear_zones": staticmethod(lambda: None)})()

    # 查询
    def hidden_pets(self):
        return self._hidden

    def species_manifests(self):
        return [FakeManifest("species.cockroach", "美洲大蠊"),
                FakeManifest("species.fly", "黑腹果蝇")]

    def pet_status(self, pid):
        h = self.pets.get(pid)
        if not h:
            return {}
        return {"pet_id": pid, "species": h.state.species_id,
                "name": h.state.name, "pos": [600, 400], "mode": "crawl",
                "activity": "explore", "frozen": False, "held": False,
                "stomach": 0.62,
                "emotion": {"fear": 0.25, "hunger": 0.4, "curiosity": 0.6,
                            "anger": 0.0, "trust": 0.8, "valence": -0.3,
                            "arousal": 0.2}}

    def memory_of(self, _pid):
        return []

    def pet_scale(self, _pid):
        return 1.0

    # 操作(安全 no-op)
    def add_pet(self, _s=None):
        return None

    def remove_pet(self, _p=None):
        return None

    def hide_pet(self, p):
        h = self.pets.pop(p, None)
        if h:
            self._hidden[p] = h
            if hasattr(self, "_panel"):
                self._panel.refresh_pets()

    def recall_pet(self, p):
        h = self._hidden.pop(p, None)
        if h:
            self.pets[p] = h
            if hasattr(self, "_panel"):
                self._panel.refresh_pets()

    def set_frozen(self, _p, _v):
        return None

    def set_intelligence(self, _p, _v):
        return None

    def set_pet_scale(self, _p, _k):
        return None

    def clear_effects(self, _p):
        return None

    def clear_memory(self, _p):
        return None

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


def mk_panel(root: tk.Tk):
    app = FakeApp(root)
    panel = ControlPanel(app)
    app._panel = panel
    panel.win.geometry(f"{PANEL_W + 40}x{PANEL_H + 60}+3000+3000")  # 屏幕外
    return app, panel


def select_page(root: tk.Tk, panel, page: _ScrollPage) -> None:
    """选中该页并跑两轮 idle:未映射的页没有几何,必须先 select 再量。"""
    panel.nb.select(page)
    root.update_idletasks()
    root.update()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    failures: list[str] = []

    def check(tag: str, cond: bool, detail: str = "") -> None:
        print(f"[{'OK' if cond else 'FAIL'}] {tag}" + (f" — {detail}" if detail else ""))
        if not cond:
            failures.append(tag)

    try:
        app, panel = mk_panel(root)

        # ---------- 1) 四意图页滚动容器 ----------
        # 判据登记(r25 A2,原 → 新 → 为什么):
        #   原:("宠物","行为","情境演示","状态","记忆","关于")
        #   新:("看它","逗它","养它","系统")
        #   为什么:本轮交付物**就是**信息架构(六页 → 四意图页,页签文案与顺序
        #   由主控裁决 2 冻结);判据未放松,只把口径换成本轮定义。其余 1b–1j
        #   的滚动骨架口径原样沿用(每页仍是 _ScrollPage)。
        titles = ("看它", "逗它", "养它", "系统")
        check("1a 页数与标题", tuple(panel.pages) == titles, str(tuple(panel.pages)))
        for t, page in panel.pages.items():
            check(f"1b[{t}] Canvas+Scrollbar 实例",
                  isinstance(page, _ScrollPage) and isinstance(page.canvas, tk.Canvas)
                  and isinstance(page.sb, ttk.Scrollbar))
            # 绑定证据:command 注册串尾缀(canvas.yview / sb.set)
            sb_cmd = str(page.sb.cget("command") or "")
            cv_cmd = str(page.canvas.cget("yscrollcommand") or "")
            check(f"1c[{t}] sb↔canvas.yview 互绑(注册串)",
                  sb_cmd.endswith("yview") and cv_cmd.endswith("set"),
                  f"{sb_cmd!r} / {cv_cmd!r}")
            select_page(root, panel, page)
            # 几何一致性:scrollregion 高度 = body 内容 reqheight
            check(f"1d[{t}] scrollregion=内容高",
                  page.scrollregion_height() >= page.content_height()
                  and page.content_height() > 0,
                  f"sr={page.scrollregion_height()} req={page.content_height()}")
            fit = page.content_height() <= page.canvas.winfo_height()
            check(f"1e[{t}] 内容{'超窗可滚' if not fit else '不超窗无需滚'}",
                  (not fit) or page.canvas.yview()[1] > 0.99,
                  f"yview={page.canvas.yview()} cv_h={page.canvas.winfo_height()}")
        check("1f minsize=520×640", tuple(panel.win.minsize()) == (PANEL_W, PANEL_H),
              str(panel.win.minsize()))

        # 内容超窗 → scroll_to_end 必达末端(「逗它」页塞入高填充件;
        # r25 A2:页名由「行为」换成本轮承接它的「逗它」,口径不变)
        page = panel.pages["逗它"]
        filler = tk.Frame(page.body, height=1400)
        filler.pack()
        select_page(root, panel, page)
        check("1g 超窗判定", page.content_height() > page.canvas.winfo_height(),
              f"req={page.content_height()} cv={page.canvas.winfo_height()}")
        page.scroll_to_end()
        root.update_idletasks()
        top, bot = page.canvas.yview()
        check("1h scroll_to_end 到底", bot > 0.99 and top > 0.0, f"yview=({top},{bot})")
        # 双向联动(功能性):拖 scrollbar 命令 → canvas 动;canvas 动 → sb 反馈
        sb_cb = page.sb.cget("command")
        root.tk.call(sb_cb, "moveto", 0.25)          # 模拟滚动条把视图送到 25%
        root.update_idletasks()
        check("1i scrollbar→canvas", abs(page.canvas.yview()[0] - 0.25) < 0.01,
              f"yview={page.canvas.yview()}")
        page.canvas.yview_moveto(0.5)                # canvas 变 → yscrollcommand 反馈 sb
        root.update_idletasks()
        check("1j canvas→scrollbar", abs(page.sb.get()[0] - 0.5) < 0.01,
              f"sb.get()={page.sb.get()}")
        page.scroll_to_end()
        root.update_idletasks()
        filler.destroy()
        select_page(root, panel, page)

        # ---------- 2) tooltip 内容 API ----------
        st = app.pet_status("roach-1")
        txt = B.tooltip_text(st, rng=random.Random(0))
        check("2a 非空多行", bool(txt) and "\n" in txt)
        emo = st["emotion"]
        for key, zh in B.TOOLTIP_EMOTIONS:
            want = f"{float(emo[key]):+.2f}"
            check(f"2b 四维数值出现[{zh}]", want in txt and zh in txt,
                  f"want {want}")
        check("2c 能量+行为", "62%" in txt and "探索" in txt)
        check("2d 含引导文案", any(g in txt for g in B.BUBBLE_TEXTS["guide"]))
        empty = B.tooltip_text({}, rng=random.Random(0))
        check("2e 空 dict 兜底非空", bool(empty) and "+0.00" in empty)
        # guide_text rng 确定性
        check("2f guide_text rng 确定性",
              B.guide_text(random.Random(3)) == B.guide_text(random.Random(3)))

        # ---------- 3) 食物选择器 ----------
        kinds = [k for k, *_ in panel._catalog]
        check("3a 清单=FOODS 全部 9 种", kinds == list(FoodKind.ALL) and
              set(kinds) == set(FOODS) and len(kinds) == 9, str(kinds))
        check("3b 默认=FOODS 第一种", panel.selected_food_kind == next(iter(FOODS))
              and panel.selected_food_kind == FoodKind.CRUMB)
        check("3c 食物按钮齐全", set(panel._food_btns) == set(kinds)
              and len(kinds) // FOOD_COLS + bool(len(kinds) % FOOD_COLS) >= 3)
        # 悬停效果文案(每个按钮的 tooltip 文本含效果描述)
        for k, label, _c, effect in panel._catalog:
            check(f"3d 效果描述[{label}]", bool(effect) and effect not in ("", "无特殊效果"))
        # 选中态高亮:另一食物被选中后样式/pressed 态切换
        btn_gold = panel._food_btns[FoodKind.GOLD]
        btn_gold.invoke()
        check("3e 切换后属性变化", panel.selected_food_kind == FoodKind.GOLD)
        check("3f 选中态高亮(GoldSel 样式+pressed)",
              str(btn_gold.cget("style")) == "FoodSel.TButton"
              and "pressed" in btn_gold.state())
        btn_crumb = panel._food_btns[FoodKind.CRUMB]
        check("3g 旧选中态回落", str(btn_crumb.cget("style")) == "Food.TButton"
              and "pressed" not in btn_crumb.state())
        # 属性 setter 亦可用
        panel.selected_food_kind = FoodKind.RAINBOW
        check("3h setter 生效", panel.selected_food_kind == FoodKind.RAINBOW)
        # on_drop_request 注册 → 点「投喂」以选中 kind 回调
        got: list[str] = []
        panel.set_on_drop_request(got.append)
        panel._food_btns[FoodKind.SILVER].invoke()
        check("3i 点击投喂回调", got == [], "未点投喂前不应触发")
        # 找到「投喂」按钮:invoke 它(不依赖私有方法名)
        feed_btn = panel.feed_now_btn
        feed_btn.invoke()
        check("3j 投喂以选中 kind 回调", got == [FoodKind.SILVER], str(got))
        check("3k 注册点属性同体", panel.on_drop_request is not None)
        panel.set_on_drop_request(None)

        # ---------- 4) tooltip 复用同一 Toplevel(不泄漏) ----------
        tip = panel._tips
        tip.show("第一条", 10, 10)
        tw1 = tip._tw
        check("4a 弹出且 2.5s 计时已挂", tw1 is not None
              and tw1.state() != "withdrawn" and tip._job is not None
              and TIP_AUTO_CLOSE_MS == 2500)
        tip.show("第二条", 20, 20)
        check("4b 复用同一 Toplevel", tip._tw is tw1)
        tip.hide()
        check("4c hide 即收起", tip._tw is tw1 and tw1.state() == "withdrawn")
        # 宠物行 "i" 悬停路径:内容来自 tooltip_text(不炸、复用)
        row = panel.tree._rows["roach-1"]
        row.i_btn.invoke()  # 点击不选中也不炸
        panel._show_pet_tip("roach-1", row.i_btn)
        check("4d i 悬停 tooltip 文案含四维",
              all(zh in tip._lbl.cget("text") for _k, zh in B.TOOLTIP_EMOTIONS))
        panel._hide_pet_tip()

        # ---------- 5) 旧接线兼容(test_pet_hide 依赖面) ----------
        check("5a 可见列表=tree.get_children",
              set(panel.tree.get_children()) == set(app.pets))
        check("5b 选中语义", panel.selected() in app.pets)
        app.hide_pet("fly-2")
        check("5c 隐藏后列表/计数", "fly-2" not in panel.tree.get_children()
              and panel.hidden_count_var.get() == "隐藏中:1 只"
              and any(x.startswith("fly-2") for x in
                      panel.hidden_list.get(0, "end")))
        panel.recall_all()
        check("5d 全部召回", "fly-2" in panel.tree.get_children()
              and panel.hidden_count_var.get() == "隐藏中:0 只")
        check("5e 行摘要刷新(2Hz 路径)", panel.tree._rows["roach-1"]
              .sum_var.get().startswith("探索"), panel.tree._rows["roach-1"].sum_var.get())

        # ---------- 6) r24 设计系统:主题已装配 + 冻结样式名齐备 ----------
        # 面板靠 panel_theme 的令牌装配外观;若主题退回 vista 或样式名被改名,
        # 外观会静默退化成系统默认(测试全绿却"变丑")——此处钉住。
        st = ttk.Style(panel.win)
        check("6a 启用 clam 主题(可样式化)", st.theme_use() == "clam",
              st.theme_use())
        check("6b 页签激活态=白底",
              str(st.lookup("TNotebook.Tab", "background", ("selected",))).lower()
              == PT.CARD.lower())
        check("6c 页签激活态强调压条(lightcolor=ACCENT)",
              str(st.lookup("TNotebook.Tab", "lightcolor", ("selected",))).lower()
              == PT.ACCENT.lower())
        check("6d 主操作按钮=实心强调色",
              str(st.lookup("Accent.TButton", "background")).lower()
              == PT.ACCENT.lower())
        check("6e 投喂告急态=告警红",
              str(st.lookup("FeedUrgent.TCheckbutton", "foreground")).lower()
              == PT.ALERT.lower())
        check("6f 正文底色=设计系统 PAPER",
              str(st.lookup("TFrame", "background")).lower() == PT.PAPER.lower())
        # 冻结样式名必须仍可解析(改名即静默退化)
        for name in ("Food.TButton", "FoodSel.TButton", "Info.TButton",
                     "Feed.TCheckbutton", "FeedUrgent.TCheckbutton",
                     "Tip.TFrame", "Tip.TLabel"):
            check(f"6g 冻结样式名存在[{name}]", bool(st.layout(name)))

        panel.win.destroy()
    finally:
        root.destroy()

    if failures:
        print(f"\n[test_panel_v2] {len(failures)} 项失败: {failures}")
        sys.exit(1)
    print("\n[test_panel_v2] 全部判据通过(滚动骨架/tooltip API/食物选择器/复用不泄漏/旧接线)")
    sys.exit(0)


if __name__ == "__main__":
    main()
