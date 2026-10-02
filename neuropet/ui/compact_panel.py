"""Small, direct controls for the 2D desktop pets. Optional views are created on demand.

界面文案全部走 `neuropet.core.i18n`(zh-CN / en)。切换语言时 `apply_language()`
在**同一个窗口内就地刷新**控件:窗口、托盘线程、宠物 id、选中项、编号、暂停与
隐藏状态、打开的可选窗口及其关联宠物都不受影响。
"""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox, ttk

from neuropet.core.config import save_config
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

    def _optional_window(self, key, title, pid_attr=None, pid=None):
        """取(或建)可选窗口。同一只宠重复打开只置顶不重建;换了宠物才重建。
        返回窗口;已存在并置顶时返回 None。"""
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
        width, height = round(350*self._scale), round(430*self._scale)
        win.geometry(f"{width}x{height}+{self.win.winfo_rootx()+20}+{self.win.winfo_rooty()+40}")
        win.minsize(width, round(300*self._scale))
        win.transient(self.win)
        self.extra_windows[key] = win
        win.bind("<Configure>", lambda event: self.app._refresh_panel_rect())
        win.bind("<Escape>", lambda event: win.destroy())
        return win

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

    def open_settings(self):
        pid = self.selected()
        win = self._optional_window("settings", t("settings.title"),
                                    pid_attr="_settings_pid", pid=pid)
        if win is None:
            return
        self._build_settings(win)

    def _build_settings(self, win):
        """设置窗口内容。语言入口永远在最上方,两种语言下都易于找到。"""
        pid = getattr(self, "_settings_pid", None)
        frame = getattr(self, "_settings_frame", None)
        if frame is not None and frame.winfo_exists():
            frame.destroy()
        win.title(t("settings.title"))
        frame = ttk.Frame(win, padding=16, style="Pet2D.TFrame")
        frame.pack(fill="both", expand=True)
        self._settings_frame = frame
        ttk.Label(frame, text=t("settings.language"), style="Pet2D.TLabel").pack(anchor="w")
        language = tk.StringVar(win, _language_label(get_lang()))
        language_box = ttk.Combobox(frame, textvariable=language, values=_language_labels(),
                                    state="readonly", style="Pet2D.TCombobox")
        language_box.pack(fill="x", pady=(7, 15))
        def choose_language(event=None):
            index = language_box.current()
            if not 0 <= index < len(LANGUAGES):
                return
            code = LANGUAGES[index][0]
            if code == get_lang():
                return
            # 下一个空闲周期再切换:本次事件正由将被重建的下拉框发出。
            self.win.after_idle(self.app.set_language, code)
        language_box.bind("<<ComboboxSelected>>", choose_language)
        label = t("settings.size_selected", name=self._display_name(pid)) if pid in self.app.pets \
            else t("settings.size_none")
        ttk.Label(frame, text=label, style="Pet2D.TLabel", wraplength=300).pack(anchor="w")
        sizes = (0.5, 0.75, 1.0, 1.5, 2.0)
        size = tk.StringVar(win, f"{self.app.pet_scale(pid):g}×" if pid in self.app.pets else "1×")
        box = ttk.Combobox(frame, textvariable=size, values=[f"{k:g}×" for k in sizes], state="readonly", style="Pet2D.TCombobox")
        box.pack(fill="x", pady=(7, 15))
        if pid not in self.app.pets:
            box.configure(state="disabled")
        box.bind("<<ComboboxSelected>>", lambda event: self.app.set_pet_scale(pid, float(size.get().rstrip("×"))))
        startup = tk.BooleanVar(win, self.app.cfg.panel_visible)
        def save_startup():
            self.app.cfg.panel_visible = startup.get()
            save_config(self.app.cfg)
        ttk.Checkbutton(frame, text=t("settings.startup"), variable=startup, command=save_startup, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        def toggle_click_feed():
            self.app.toggle_feeding(self.feeding_var.get())
        ttk.Checkbutton(frame, text=t("settings.click_feed"), variable=self.feeding_var, command=toggle_click_feed, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Label(frame, text=t("settings.hint"), style="Pet2D.Muted.TLabel", wraplength=300).pack(anchor="w", pady=(15, 14))
        ttk.Button(frame, text=t("settings.quit"), command=self.app.shutdown, style="Pet2D.TButton").pack(fill="x")

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
        self._settings_frame = self._memory_frame = None
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
