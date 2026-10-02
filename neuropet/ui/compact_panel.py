"""Small, direct controls for the 2D desktop pets. Optional views are created on demand.

界面文案全部走 `neuropet.core.i18n`(zh-CN / en)。切换语言时 `apply_language()`
在**同一个窗口内就地刷新**控件:窗口、托盘线程、宠物 id、选中项、编号、暂停与
隐藏状态、打开的可选窗口及其关联宠物都不受影响。
"""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox, ttk

from neuropet.core.config import save_config, CRAWL_SPEED_CHOICES
from neuropet.core.i18n import get_lang, t
from neuropet.feeding import FoodKind

BACKGROUND = "#F7F9FA"
INK = "#25323A"
MUTED = "#62717A"
ACCENT = "#206B8B"
LINE = "#D8E1E6"
# 物种 id / 活动值 / FoodKind 是**持久标识**,不随语言改变;这里只放 i18n key。
SPECIES = {"species.cockroach": "species.cockroach",
           "species.fruitfly": "species.fruitfly"}
ACTIVITY = {"idle": "activity.idle", "explore": "activity.explore",
            "groom": "activity.groom", "seek_food": "activity.seek_food",
            "eat": "activity.eat", "escape": "activity.escape",
            "rest": "activity.rest", "turn": "activity.turn",
            "takeoff": "activity.takeoff", "fly_wander": "activity.fly_wander",
            "land": "activity.land", "frozen": "activity.frozen"}
# 下拉框顺序即展示顺序;选中项按 FoodKind 记忆,切语言后仍是同一份食物。
FOOD = (FoodKind.CRUMB, FoodKind.GREEN, FoodKind.GREASE, FoodKind.VINEGAR)
FOOD_KEY = {FoodKind.CRUMB: "food.crumb", FoodKind.GREEN: "food.green",
            FoodKind.GREASE: "food.grease", FoodKind.VINEGAR: "food.vinegar"}
# 语言选项两种语言下都不翻译(语言自称),任何语言的用户都能找到自己那一项。
LANGUAGES = (("zh-CN", "settings.language_zh"), ("en", "settings.language_en"))


def _food_label(kind: FoodKind) -> str:
    return t(FOOD_KEY.get(kind, "food.crumb"))


def _language_label(code: str) -> str:
    for value, key in LANGUAGES:
        if value == code:
            return t(key)
    return t(LANGUAGES[0][1])


def _language_labels() -> tuple[str, ...]:
    return tuple(_language_label(code) for code, _ in LANGUAGES)


class ControlPanel:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("NeuroPet 2D")
        available = max(600, self.win.winfo_screenheight()-120)
        self._scale = min(max(1., self.win.winfo_fpixels("1i")/96), available/620)
        self._width = int(380*self._scale)
        self._height = min(int(620*self._scale), available)
        self.win.geometry(f"{self._width}x{self._height}+50+50")
        self.win.minsize(self._width, min(int(600*self._scale), self._height))
        self.win.configure(background=BACKGROUND)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.extra_windows = {}
        self.on_drop_request = None
        self._names = {}
        self._serial = {}
        self._elapsed = 0.0
        self.refresh_count = 0
        self._food_kind = FoodKind.CRUMB
        self.count_var = tk.StringVar(self.win)
        self.status_var = tk.StringVar(self.win, t("panel.empty_hint"))
        self.detail_var = tk.StringVar(self.win, t("panel.select_hint"))
        self.food_var = tk.StringVar(self.win, _food_label(self._food_kind))
        self.feeding_var = tk.BooleanVar(self.win, bool(app.feeding))
        self._settings_pid = None            # 设置窗口的作用宠物(按 id 保存)
        self._settings_wrap = None            # 设置窗口滚动容器(重建时销毁)
        self._settings_frame = None
        self._settings_vars: dict = {}       # 设置窗口全部 Tk 变量的强引用
        self._settings_loading = False        # 填值期间禁止任何写配置/注册表
        self._settings_size_row = None
        self._settings_recall_btn = None
        self._configure_style()
        outer = ttk.Frame(self.win, padding=(16, 14), style="Pet2D.TFrame")
        outer.pack(fill="both", expand=True)
        self.subtitle_label = ttk.Label(outer, text=t("panel.subtitle"), style="Pet2D.Title.TLabel")
        self.subtitle_label.pack(anchor="w")
        self.tagline_label = ttk.Label(outer, text=t("panel.tagline"), style="Pet2D.Muted.TLabel")
        self.tagline_label.pack(anchor="w", pady=(3, 12))
        add = ttk.Frame(outer, style="Pet2D.TFrame")
        add.pack(fill="x")
        add.columnconfigure((0, 1), weight=1)
        self.add_roach_btn = ttk.Button(add, text=t("panel.add_roach"), style="Pet2D.Primary.TButton",
                                       command=lambda: self.add_pet("species.cockroach"))
        self.add_fly_btn = ttk.Button(add, text=t("panel.add_fly"), style="Pet2D.TButton",
                                     command=lambda: self.add_pet("species.fruitfly"))
        self.add_roach_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.add_fly_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(outer, textvariable=self.count_var, style="Pet2D.Muted.TLabel").pack(anchor="w", pady=(12, 6))
        listing = ttk.Frame(outer, height=140, style="Pet2D.TFrame")
        listing.pack(fill="both", expand=True)
        listing.pack_propagate(False)
        self.tree = ttk.Treeview(listing, columns=("name", "activity", "food"), show="headings",
                                 selectmode="browse", height=4, style="Pet2D.Treeview")
        for name, width in (("name", 104), ("activity", 119), ("food", 54)):
            self.tree.column(name, width=width, minwidth=width-20, anchor="w", stretch=name != "food")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("hidden", foreground=MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._refresh_selection())
        self.tree.bind("<Double-1>", lambda event: self.toggle_visibility() if self.selected() in self.app.hidden_pets() else None)
        ttk.Label(outer, textvariable=self.detail_var, style="Pet2D.Muted.TLabel", wraplength=326).pack(anchor="w", pady=(10, 7))
        self.food_box = ttk.Combobox(outer, textvariable=self.food_var, values=self._food_labels(),
                                     state="readonly", style="Pet2D.TCombobox")
        self.food_box.pack(fill="x", pady=(0, 7))
        self.food_box.bind("<<ComboboxSelected>>", self._on_food_selected)
        actions = ttk.Frame(outer, style="Pet2D.TFrame")
        actions.pack(fill="x")
        actions.columnconfigure((0, 1, 2), weight=1)
        self.feed_now_btn = ttk.Button(actions, text=t("panel.feed"), command=self.feed_selected, style="Pet2D.Primary.TButton")
        self.freeze_btn = ttk.Button(actions, text=t("panel.freeze"), command=self.toggle_freeze, style="Pet2D.TButton")
        self.hide_btn = ttk.Button(actions, text=t("panel.hide"), command=self.toggle_visibility, style="Pet2D.TButton")
        self.remove_btn = ttk.Button(actions, text=t("panel.remove"), command=self.remove_selected, style="Pet2D.TButton")
        self.memory_btn = ttk.Button(actions, text=t("panel.memory"), command=self.open_memory, style="Pet2D.TButton")
        self.settings_btn = ttk.Button(actions, text=t("panel.settings"), command=self.open_settings, style="Pet2D.TButton")
        for i, button in enumerate((self.feed_now_btn, self.freeze_btn, self.hide_btn,
                                    self.remove_btn, self.memory_btn, self.settings_btn)):
            button.grid(row=i//3, column=i%3, sticky="ew", padx=2, pady=3)
        bulk = ttk.Frame(outer, style="Pet2D.TFrame")
        bulk.pack(fill="x", pady=(9, 0))
        self.pause_all_btn = ttk.Button(bulk, text=t("panel.pause_all"), command=self.pause_all, style="Pet2D.Small.TButton")
        self.pause_all_btn.pack(side="left")
        self.recall_all_btn = ttk.Button(bulk, text=t("panel.recall_all"), command=self.recall_all, style="Pet2D.Small.TButton")
        self.recall_all_btn.pack(side="left", padx=7)
        self.collapse_btn = ttk.Button(bulk, text=t("panel.collapse"), command=self.hide, style="Pet2D.Small.TButton")
        self.collapse_btn.pack(side="right")
        ttk.Label(outer, textvariable=self.status_var, style="Pet2D.Muted.TLabel", wraplength=326).pack(anchor="w", pady=(9, 0))
        self.win.bind("<Control-Key-1>", lambda event: self.add_pet("species.cockroach"))
        self.win.bind("<Control-Key-2>", lambda event: self.add_pet("species.fruitfly"))
        self.tree.bind("<space>", lambda event: self._space())
        self.win.bind("<Escape>", lambda event: self.hide())
        self._apply_headings()
        self.refresh_pets()

    def _food_labels(self) -> tuple[str, ...]:
        return tuple(_food_label(kind) for kind in FOOD)

    def _on_food_selected(self, event=None):
        index = self.food_box.current()
        if 0 <= index < len(FOOD):
            self._food_kind = FOOD[index]

    def _apply_headings(self):
        for name, key in (("name", "panel.col_name"), ("activity", "panel.col_activity"),
                          ("food", "panel.col_food")):
            self.tree.heading(name, text=t(key))

    def apply_language(self):
        """就地刷新全部可见文案。窗口不重建:选中项、编号、按钮状态、可选窗口
        与其关联宠物都保持原样(语言切换立即生效,无需重启)。"""
        if not self.win.winfo_exists():
            return
        self.subtitle_label.configure(text=t("panel.subtitle"))
        self.tagline_label.configure(text=t("panel.tagline"))
        for button, key in ((self.add_roach_btn, "panel.add_roach"),
                            (self.add_fly_btn, "panel.add_fly"),
                            (self.feed_now_btn, "panel.feed"),
                            (self.remove_btn, "panel.remove"),
                            (self.memory_btn, "panel.memory"),
                            (self.settings_btn, "panel.settings"),
                            (self.recall_all_btn, "panel.recall_all"),
                            (self.collapse_btn, "panel.collapse")):
            button.configure(text=t(key))
        self._apply_headings()
        self.food_box.configure(values=self._food_labels())
        self.food_var.set(_food_label(self._food_kind))
        for key, rebuild in (("settings", self._build_settings),
                             ("memory", self._build_memory)):
            win = self.extra_windows.get(key)
            if win is not None and win.winfo_exists():
                rebuild(win)
        self.refresh_pets()

    def _configure_style(self):
        style = ttk.Style(self.win)
        style.theme_use("clam")
        normal = ("Microsoft YaHei UI", -round(14*self._scale))
        small = ("Microsoft YaHei UI", -round(13*self._scale))
        style.configure("Pet2D.TFrame", background=BACKGROUND)
        style.configure("Pet2D.TLabel", background=BACKGROUND, foreground=INK, font=normal)
        style.configure("Pet2D.Title.TLabel", background=BACKGROUND, foreground=INK, font=("Microsoft YaHei UI", -round(26*self._scale), "bold"))
        style.configure("Pet2D.Muted.TLabel", background=BACKGROUND, foreground=MUTED, font=small)
        style.configure("Pet2D.TButton", font=normal, padding=(7, 7), background="white", foreground=INK, bordercolor=LINE)
        style.map("Pet2D.TButton", background=[("active", "#E8F0F4")], foreground=[("disabled", "#8A979F")])
        style.configure("Pet2D.Primary.TButton", font=normal, padding=(7, 7), background=ACCENT, foreground="white", borderwidth=0)
        style.map("Pet2D.Primary.TButton", background=[("disabled", "#DBE4E9"), ("active", "#155470")], foreground=[("disabled", "#8A979F")])
        style.configure("Pet2D.Small.TButton", font=small, padding=(6, 4))
        style.configure("Pet2D.Treeview", font=normal, rowheight=round(30*self._scale), background="white", fieldbackground="white", bordercolor=LINE)
        style.configure("Pet2D.Treeview.Heading", font=small, padding=(5, 5), background="#EDF2F5", foreground=MUTED)
        style.configure("Pet2D.TCombobox", font=normal)
        style.configure("Pet2D.TCheckbutton", font=normal, background=BACKGROUND)
        style.map("Pet2D.Treeview", background=[("selected", "#DCECF3")], foreground=[("selected", INK)])

    @property
    def selected_food_kind(self):
        return self._food_kind

    def selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def set_on_drop_request(self, callback):
        self.on_drop_request = callback

    def _name(self, pid, handle):
        if pid not in self._names:
            key = SPECIES.get(handle.state.species_id)
            species = t(key) if key else handle.state.name
            self._serial[species] = self._serial.get(species, 0) + 1
            self._names[pid] = f"{species} {self._serial[species]}"
        return self._names[pid]

    def _display_name(self, pid):
        """窗口标题用的名字:宠物已移除时退回已知的名字,不再 KeyError。"""
        handle = self.app.pets.get(pid) or self.app.hidden_pets().get(pid)
        if handle is not None:
            return self._name(pid, handle)
        return self._names.get(pid, t("panel.col_name"))

    def refresh_pets(self):
        if not self.win.winfo_exists():
            return
        self.refresh_count += 1
        selected = self.selected()
        hidden = self.app.hidden_pets()
        handles = {**self.app.pets, **hidden}
        for pid in self.tree.get_children():
            if pid not in handles:
                self.tree.delete(pid)
        for pid, handle in handles.items():
            state = handle.state
            if pid in hidden:
                activity = t("activity.hidden")
            else:
                activity = t(ACTIVITY.get(state.activity.value, "activity.busy")) \
                    if not state.frozen else t("activity.frozen")
            values = (self._name(pid, handle), activity, f"{state.stomach:.0%}")
            tags = ("hidden",) if pid in hidden else ()
            if self.tree.exists(pid):
                if self.tree.item(pid, "values") != values or self.tree.item(pid, "tags") != tags:
                    self.tree.item(pid, values=values, tags=tags)
            else:
                self.tree.insert("", "end", iid=pid, values=values, tags=tags)
        children = self.tree.get_children()
        if selected in handles:
            self.tree.selection_set(selected)
        elif children:
            self.tree.selection_set(children[0])
        self.count_var.set(t("panel.count", on=len(self.app.pets),
                             total=self.app.cfg.max_pets, hidden=len(hidden)))
        full = len(self.app.pets) >= self.app.cfg.max_pets
        for button in (self.add_roach_btn, self.add_fly_btn):
            button.state(["disabled"] if full else ["!disabled"])
        self.pause_all_btn.state(["!disabled"] if self.app.pets else ["disabled"])
        all_paused = bool(self.app.pets) and all(h.state.frozen for h in self.app.pets.values())
        self.pause_all_btn.configure(text=t("panel.resume_all") if all_paused else t("panel.pause_all"))
        self.recall_all_btn.state(["!disabled"] if hidden else ["disabled"])
        if full:
            self.status_var.set(t("panel.full_hint", total=self.app.cfg.max_pets))
        elif not handles:
            self.status_var.set(t("panel.empty_hint"))
        self._refresh_selection()

    def _refresh_selection(self):
        pid = self.selected()
        hidden = self.app.hidden_pets()
        handle = self.app.pets.get(pid) or hidden.get(pid)
        available = handle is not None
        visible = pid in self.app.pets
        for button in (self.feed_now_btn, self.freeze_btn):
            button.state(["!disabled"] if visible else ["disabled"])
        for button in (self.hide_btn, self.memory_btn, self.remove_btn):
            button.state(["!disabled"] if available else ["disabled"])
        self.hide_btn.configure(text=t("panel.recall") if pid in hidden else t("panel.hide"))
        self.freeze_btn.configure(text=t("panel.resume") if available and handle.state.frozen
                                  else t("panel.freeze"))
        if not available:
            self.detail_var.set(t("panel.select_hint"))
            return
        status = self.app.pet_status(pid) if visible else handle.state.as_summary()
        fear = status.get("emotion", {}).get("fear", 0)
        mood = t("panel.mood_wary") if fear > .3 else t("panel.mood_calm")
        self.detail_var.set(f"{self._name(pid, handle)} · "
                            + (t("panel.hidden_detail") if not visible else mood))

    def add_pet(self, species):
        try:
            pid = self.app.add_pet(species)
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        self.refresh_pets()
        self.tree.selection_set(pid)
        self.tree.see(pid)
        self._refresh_selection()
        self.status_var.set(t("panel.added", name=self._names[pid]))

    def feed_selected(self):
        if self.selected() in self.app.pets and self.on_drop_request:
            self.on_drop_request(self.selected_food_kind)
            self.status_var.set(t("panel.fed"))

    def toggle_freeze(self):
        pid = self.selected()
        if pid in self.app.pets:
            self.app.set_frozen(pid, not self.app.pets[pid].state.frozen)
            self.refresh_pets()

    def _space(self):
        self.toggle_freeze()
        return "break"

    def pause_all(self):
        paused = bool(self.app.pets) and all(h.state.frozen for h in self.app.pets.values())
        for pid in list(self.app.pets):
            self.app.set_frozen(pid, not paused)
        self.refresh_pets()
        self.status_var.set(t("panel.resumed_all") if paused else t("panel.paused_all"))

    def toggle_visibility(self):
        pid = self.selected()
        if pid in self.app.hidden_pets():
            if not self.app.recall_pet(pid):
                self.status_var.set(t("panel.no_slot"))
                return
        elif pid in self.app.pets:
            self.app.hide_pet(pid)
        self.refresh_pets()

    def recall_all(self):
        count = sum(bool(self.app.recall_pet(pid)) for pid in list(self.app.hidden_pets()))
        self.refresh_pets()
        remaining = len(self.app.hidden_pets())
        self.status_var.set(t("panel.recalled", count=count)
                            + (t("panel.recalled_rest", count=remaining) if remaining else ""))

    def remove_selected(self):
        pid = self.selected()
        if pid and messagebox.askyesno(t("panel.remove_title"), t("panel.remove_ask"), parent=self.win):
            self.app.remove_pet(pid)
            self.refresh_pets()
            self.status_var.set(t("panel.removed"))

    def _optional_window(self, key, title, pid_attr=None, pid=None, height=430):
        """取(或建)可选窗口。同一只宠重复打开只置顶不重建;换了宠物才重建。
        返回窗口;已存在并置顶时返回 None。``height`` 由内容多少决定(设置
        窗口控件更多,给得更高)。"""
        previous = self.extra_windows.get(key)
        if previous is not None and previous.winfo_exists() and pid_attr \
                and getattr(self, pid_attr, None) != pid:
            previous.destroy()
            self.extra_windows.pop(key, None)
            previous = None
        if previous is not None and previous.winfo_exists():
            if pid_attr:
                setattr(self, pid_attr, pid)
            previous.title(title)
            previous.lift()
            previous.focus_force()
            return None
        win = tk.Toplevel(self.win)
        win.title(title)
        win.configure(background=BACKGROUND)
        win.attributes("-topmost", True)
        width = round(350*self._scale)
        win.geometry(f"{width}x{round(height*self._scale)}"
                     f"+{self.win.winfo_rootx()+20}+{self.win.winfo_rooty()+40}")
        win.minsize(width, round(300*self._scale))
        win.transient(self.win)
        self.extra_windows[key] = win
        win.bind("<Configure>", lambda event: self.app._refresh_panel_rect())
        win.bind("<Escape>", lambda event: win.destroy())
        return win

    def _scrollable(self, win, padding=16):
        """可竖直滚动的内容容器(v0.2.0 设置窗口控件增多,高 DPI 下不溢出)。

        滚轮绑在外层 Frame 上:Tk 的事件会从命中的子控件向上冒泡,所以下拉框/
        列表框之外的区域滚轮都能用;列表框内部仍由它自己处理(符合直觉)。
        """
        wrap = tk.Frame(win, background=BACKGROUND)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, background=BACKGROUND, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas, padding=padding, style="Pet2D.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda event: canvas.itemconfigure(window_id, width=event.width))
        wrap.bind("<MouseWheel>", lambda event: canvas.yview_scroll(
            -1 if event.delta > 0 else 1, "units"))
        return wrap, inner

    def open_memory(self):
        pid = self.selected()
        if not pid:
            return
        win = self._optional_window("memory", t("memory.title", name=self._display_name(pid)),
                                    pid_attr="_memory_pid", pid=pid)
        if win is None:
            return
        self._build_memory(win)

    def _build_memory(self, win):
        """记忆窗口内容。语言切换时原地重建控件;记忆正文是历史原文,不翻译。"""
        pid = getattr(self, "_memory_pid", None)
        frame = getattr(self, "_memory_frame", None)
        if frame is not None and frame.winfo_exists():
            frame.destroy()
        win.title(t("memory.title", name=self._display_name(pid) if pid else ""))
        frame = ttk.Frame(win, padding=14, style="Pet2D.TFrame")
        frame.pack(fill="both", expand=True)
        self._memory_frame = frame
        ttk.Label(frame, text=t("memory.recent"), style="Pet2D.TLabel").pack(anchor="w", pady=(0, 8))
        text = tk.Text(frame, height=8, font=("Microsoft YaHei UI", -round(14*self._scale)), wrap="word", relief="flat", background="white", foreground=INK, padx=9, pady=9)
        text.pack(fill="both", expand=True)
        def refresh():
            lines = [line for line in (self.app.memory_of(pid) if pid else [])
                     if not line.startswith(("[NN]", "[GF]"))]
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", "\n\n".join(lines) or t("memory.empty"))
            text.configure(state="disabled")
        def clear():
            if messagebox.askyesno(t("memory.clear_title"), t("memory.clear_ask"), parent=win):
                self.app.clear_memory(pid)
                refresh()
        controls = ttk.Frame(frame, style="Pet2D.TFrame")
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text=t("memory.refresh"), command=refresh, style="Pet2D.TButton").pack(side="left")
        ttk.Button(controls, text=t("memory.clear"), command=clear, style="Pet2D.TButton").pack(side="right")
        refresh()

    def _settings_target_pid(self):
        """设置窗口的作用对象(按 ID 保存,不依赖过期的窗口局部变量)。

        顺序:面板当前选中 → 上次在设置里选的 → 第一只**可见**宠 → 第一只
        隐藏宠(并在下拉里标注"已隐藏")。绝不再"没有选中就静默禁用大小"。
        """
        for pid in (self.selected(), getattr(self, "_settings_pid", None)):
            if pid and (pid in self.app.pets or pid in self.app.hidden_pets()):
                return pid
        if self.app.pets:
            return next(iter(self.app.pets))
        if self.app.hidden_pets():
            return next(iter(self.app.hidden_pets()))
        return None

    def _settings_pet_labels(self) -> tuple[str, ...]:
        """当前宠物下拉的选项(可见宠在前;隐藏宠标注状态)。顺序稳定。"""
        hidden = self.app.hidden_pets()
        out = []
        for pid in self.app.pets:
            out.append(self._display_name(pid))
        for pid in hidden:
            out.append(f"{self._display_name(pid)} · {t('activity.hidden')}")
        return tuple(out)

    def _settings_pid_from_label(self, label: str):
        """下拉显示文字 → pet_id(文字不唯一时取第一个)。"""
        hidden = self.app.hidden_pets()
        suffix = f" · {t('activity.hidden')}"
        for pid in list(self.app.pets) + list(hidden):
            if self._display_name(pid) == label or \
                    self._display_name(pid) + suffix == label:
                return pid
        return None

    def _combo(self, win, frame, values, current: str):
        """只读下拉框 + 强引用变量:初始值与 values 对齐并显式 current()。

        ``tkinter.Variable`` 是 Python 对象,函数结束即被回收,``__del__`` 会
        ``unset`` 掉 Tcl 变量,Tk 的变量 trace 于是把下拉框重置为默认(空)——
        这就是"语言下拉不显示默认项"的根因。变量统一存在 ``self._settings_vars``
        里,随窗口存活。
        """
        values = tuple(values)
        var = tk.StringVar(win, current)
        box = ttk.Combobox(frame, textvariable=var, values=values,
                           state="readonly", style="Pet2D.TCombobox")
        index = list(values).index(current) if current in values else -1
        if index >= 0:
            var.set(values[index])
            box.current(index)
        self._settings_vars[id(box)] = (var, box)
        return var, box

    def _settings_release(self):
        """释放设置窗口的全部 Tk 变量引用(重建或关闭时调用)。"""
        self._settings_vars.clear()

    def _guard(self, win, action, revert=None):
        """设置回调的统一保护:失败落 diag + 双语提示 + 回退显示值。"""
        try:
            return action()
        except Exception:
            from neuropet.diag import log_exc
            log_exc("settings")
            try:
                messagebox.showerror(t("settings.title"), t("error.save"),
                                     parent=win)
            except Exception:
                pass
            if revert is not None:
                try:
                    revert()
                except Exception:
                    pass
            return None

    def open_settings(self):
        win = self._optional_window("settings", t("settings.title"),
                                    pid_attr="_settings_pid",
                                    pid=self._settings_target_pid(),
                                    height=600)
        if win is None:
            return
        self._build_settings(win)

    def _build_settings(self, win):
        """设置窗口内容(v0.2.0 追加要求:显式现值、可用的大小、实时保存)。

        规则:

        - 语言入口永远在最上方,两种语言下都易于找到;
        - 开窗就把当前语言 / 当前宠物 / 该宠物实际大小 / 当前速度**明确显示**
          出来,不留空(所有 Tk 变量由 ``self._settings_vars`` 强引用);
        - 每一项改动立刻生效并落盘,没有"保存"按钮;程序填值(开窗、切语言
          重建)**不会**触发任何回调(``<<ComboboxSelected>>`` 只由用户操作
          产生,且额外有 ``_loading`` 闸门);
        - 关闭窗口(Esc / X / 按钮)只销毁这一个窗口,不动 app、不隐藏主面板;
        - 内容放在可滚动容器里,控件变多或高 DPI 都不溢出。
        """
        self._settings_release()
        wrap = getattr(self, "_settings_wrap", None)
        if wrap is not None and wrap.winfo_exists():
            wrap.destroy()          # 语言刷新:整只滚动容器重建,不留旧控件
        win.title(t("settings.title"))
        wrap, frame = self._scrollable(win)
        self._settings_wrap = wrap
        self._settings_frame = frame
        self._settings_loading = True       # 填值期间的写入门闸
        try:
            self._build_settings_rows(win, frame)
        finally:
            self._settings_loading = False
        self.app._refresh_panel_rect()

    def _build_settings_rows(self, win, frame):
        loading = getattr(self, "_settings_loading", False)
        V = self._settings_vars
        sizes = (0.5, 0.75, 1.0, 1.5, 2.0)

        # ---- 语言 ----
        ttk.Label(frame, text=t("settings.language"), style="Pet2D.TLabel").pack(anchor="w")
        labels = _language_labels()
        code = get_lang()
        current = _language_label(code)
        language, language_box = self._combo(win, frame, labels, current)
        language_box.pack(fill="x", pady=(7, 15))

        def choose_language(event=None):
            if loading:
                return
            index = language_box.current()
            if not 0 <= index < len(LANGUAGES):
                return
            picked = LANGUAGES[index][0]
            if picked == get_lang():
                return
            # 下一个空闲周期再切换:本次事件正由将被重建的下拉框发出。
            self.win.after_idle(self.app.set_language, picked)
        language_box.bind("<<ComboboxSelected>>", choose_language)

        # ---- 当前宠物(大小下拉的作用对象;设置内可直接切换/召回) ----
        ttk.Label(frame, text=t("settings.pet_label"),
                  style="Pet2D.TLabel").pack(anchor="w")
        pid = self._settings_target_pid()
        pet_labels = self._settings_pet_labels()
        pet, pet_box = self._combo(
            win, frame, pet_labels, self._settings_pet_label_for(pid, pet_labels))
        pet_box.pack(fill="x", pady=(7, 4))
        pet_row = ttk.Frame(frame, style="Pet2D.TFrame")
        pet_row.pack(fill="x")
        recall_btn = ttk.Button(pet_row, text=t("settings.pet_recall"),
                                style="Pet2D.TButton", command=self._settings_recall)
        self._settings_recall_btn = recall_btn

        def choose_pet(event=None):
            if loading:
                return
            picked = self._settings_pid_from_label(pet.get())
            if not picked:
                return
            self._settings_pid = picked
            # 设置里选的宠物同步为主面板的选中项:两处显示同一个"当前宠物",
            # 之后重新打开设置也不会弹回另一只。
            if self.tree.exists(picked):
                self.tree.selection_set(picked)
                self.tree.focus(picked)
                self._refresh_selection()
            self._rebuild_settings_deferred(win)
        pet_box.bind("<<ComboboxSelected>>", choose_pet)

        # ---- 大小 ----
        size_row = ttk.Frame(frame, style="Pet2D.TFrame")
        size_row.pack(fill="x", pady=(14, 0))
        size_label = ttk.Label(size_row, style="Pet2D.TLabel", wraplength=290)
        size_label.pack(side="left", fill="x", expand=True)
        values = tuple(f"{k:g}×" for k in sizes)
        shown = f"{self.app.pet_scale(pid):g}×" if pid in self.app.pets else values[2]
        size, size_box = self._combo(win, size_row, values, shown)
        size_box.configure(width=7)
        size_box.pack(side="right")
        self._settings_size_row = (size_label, size_box)

        def choose_size(event=None):
            if loading:
                return
            target = self._settings_target_pid()
            if target not in self.app.pets:
                return
            try:
                value = float(size.get().rstrip("×"))
            except ValueError:
                return
            actual = self._guard(win, lambda: self.app.set_pet_scale(target, value))
            size.set(f"{actual if actual is not None else self.app.pet_scale(target):g}×")
        size_box.bind("<<ComboboxSelected>>", choose_size)
        self._settings_sync_pet_rows()

        # ---- 全局爬行速度(7 档,即时生效 + 落盘) ----
        ttk.Label(frame, text=t("settings.speed"), style="Pet2D.TLabel").pack(anchor="w", pady=(16, 0))
        speed, speed_box = self._combo(win, frame, tuple(f"{k:g}×" for k in CRAWL_SPEED_CHOICES),
                                       f"{self.app.crawl_speed():g}×")
        speed_box.pack(fill="x", pady=(7, 4))

        def choose_speed(event=None):
            if loading:
                return
            try:
                value = float(speed.get().rstrip("×"))
            except ValueError:
                return
            actual = self._guard(win, lambda: self.app.set_crawl_speed(value))
            speed.set(f"{actual if actual is not None else self.app.crawl_speed():g}×")
        speed_box.bind("<<ComboboxSelected>>", choose_speed)
        speed_now = ttk.Label(frame, text=t("settings.speed_now", mult=self.app.crawl_speed()),
                              style="Pet2D.Muted.TLabel")
        speed_now.pack(anchor="w")
        ttk.Label(frame, text=t("settings.speed_note"),
                  style="Pet2D.Muted.TLabel", wraplength=300).pack(anchor="w", pady=(2, 14))

        # ---- 开关类(每一项改动立即落盘) ----
        startup = tk.BooleanVar(win, bool(self.app.cfg.panel_visible))
        V["startup"] = startup

        def toggle_startup():
            if loading:
                return
            self.app.cfg.panel_visible = bool(startup.get())
            if not self._guard(win, lambda: save_config(self.app.cfg)):
                startup.set(bool(self.app.cfg.panel_visible))
        ttk.Checkbutton(frame, text=t("settings.startup"), variable=startup,
                        command=toggle_startup, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        trails = tk.BooleanVar(win, bool(self.app.trails()))
        V["trails"] = trails

        def toggle_trails():
            if loading:
                return
            trails.set(bool(self._guard(win, lambda: self.app.set_trails(trails.get()))))
        ttk.Checkbutton(frame, text=t("settings.trails"), variable=trails,
                        command=toggle_trails, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        autostart = tk.BooleanVar(win, bool(self.app.autostart_enabled()))
        V["autostart"] = autostart

        def toggle_autostart():
            if loading:
                return
            want = bool(autostart.get())
            ok = bool(self._guard(win, lambda: self.app.set_autostart(want)))
            if ok != want:
                # 写入失败:回滚复选框到注册表真实状态 + 双语错误。
                autostart.set(bool(self.app.autostart_enabled()))
                try:
                    messagebox.showerror(t("settings.autostart"),
                                         t("error.autostart"), parent=win)
                except Exception:
                    pass
        ttk.Checkbutton(frame, text=t("settings.autostart"), variable=autostart,
                        command=toggle_autostart,
                        style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Label(frame, text=t("settings.autostart_note"),
                  style="Pet2D.Muted.TLabel", wraplength=300).pack(anchor="w")

        def toggle_click_feed():
            if loading:
                return
            self.app.toggle_feeding(self.feeding_var.get())
        ttk.Checkbutton(frame, text=t("settings.click_feed"), variable=self.feeding_var,
                        command=toggle_click_feed,
                        style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Label(frame, text=t("settings.hint"), style="Pet2D.Muted.TLabel",
                  wraplength=300).pack(anchor="w", pady=(16, 10))
        ttk.Label(frame, text=t("settings.autosave"), style="Pet2D.Muted.TLabel",
                  wraplength=300).pack(anchor="w", pady=(0, 12))
        ttk.Button(frame, text=t("settings.close"),
                   command=lambda: win.destroy(),
                   style="Pet2D.TButton").pack(fill="x")

    def _settings_pet_label_for(self, pid, labels):
        """pid 在下拉里对应的显示文字(找不到就返回第一项,不留空)。"""
        if not labels:
            return ""
        hidden = self.app.hidden_pets()
        suffix = f" · {t('activity.hidden')}"
        for handle_pid in list(self.app.pets) + list(hidden):
            if handle_pid != pid:
                continue
            name = self._display_name(handle_pid)
            return name + (suffix if handle_pid in hidden else "")
        return labels[0]

    def _settings_sync_pet_rows(self):
        """按当前目标宠物刷新"大小"行(标签、可用性、召回按钮)。"""
        pid = self._settings_target_pid()
        row = getattr(self, "_settings_size_row", None)
        if row is None or not row[0].winfo_exists():
            return
        size_label, size_box = row
        visible = pid in self.app.pets
        hidden = pid is not None and pid in self.app.hidden_pets()
        if pid is None:
            size_label.configure(text=t("settings.size_none"))
            size_box.configure(state="disabled")
        elif visible:
            size_label.configure(text=t("settings.size_selected",
                                        name=self._display_name(pid)))
            size_box.configure(state="readonly")
        else:
            size_label.configure(text=t("settings.size_hidden",
                                        name=self._display_name(pid)))
            size_box.configure(state="disabled")
        button = getattr(self, "_settings_recall_btn", None)
        if button is not None and button.winfo_exists():
            if hidden:
                button.configure(text=t("settings.pet_recall"))
                button.configure(state="normal", command=self._settings_recall)
                button.pack(side="left", padx=(0, 8))
            else:
                button.pack_forget()

    def _rebuild_settings_deferred(self, win):
        """下一个空闲周期重建设置内容(不在控件自己的事件回调里销毁自己)。"""
        def run():
            if win.winfo_exists():
                self._build_settings(win)
        win.after_idle(run)

    def _settings_recall(self):
        """设置窗口内直接召回当前目标宠(不用回主面板找)。"""
        pid = self._settings_target_pid()
        if pid and self.app.recall_pet(pid):
            self._settings_pid = pid
        else:
            self._settings_pid = self._settings_target_pid()
        win = self.extra_windows.get("settings")
        if win is not None and win.winfo_exists():
            self._rebuild_settings_deferred(win)

    def visible_windows(self):
        return [win for win in (self.win, *self.extra_windows.values())
                if win.winfo_exists() and win.winfo_viewable()]

    def set_feeding(self, on):
        self.feeding_var.set(bool(on))

    def hide(self):
        for win in self.extra_windows.values():
            if win.winfo_exists():
                win.destroy()
        self.extra_windows.clear()
        self._settings_frame = self._memory_frame = self._settings_wrap = None
        self._settings_release()
        self._settings_size_row = self._settings_recall_btn = None
        if getattr(self.app, "_tray", None) is None:
            self.win.iconify()  # Taskbar recovery if the optional tray is unavailable.
        else:
            self.win.withdraw()

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.refresh_pets()

    def tick(self, dt):
        if not self.win.winfo_exists() or self.win.state() != "normal":
            return
        self._elapsed += dt
        if self._elapsed >= 0.5:
            self._elapsed = 0.0
            self.refresh_pets()
