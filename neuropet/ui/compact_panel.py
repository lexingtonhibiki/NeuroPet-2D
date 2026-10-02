"""Small, direct controls for the 2D desktop pets. Optional views are created on demand."""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox, ttk

from neuropet.core.config import save_config
from neuropet.feeding import FoodKind

BACKGROUND = "#F7F9FA"
INK = "#25323A"
MUTED = "#62717A"
ACCENT = "#206B8B"
LINE = "#D8E1E6"
SPECIES = {"species.cockroach": "蟑螂", "species.fruitfly": "果蝇"}
ACTIVITY = {"idle": "待机", "explore": "探索", "groom": "清洁身体", "seek_food": "觅食",
            "eat": "进食", "escape": "逃跑", "rest": "休息", "turn": "转向",
            "takeoff": "起飞", "fly_wander": "飞行", "land": "降落", "frozen": "已暂停"}
FOOD = {"面包屑": FoodKind.CRUMB, "嫩叶": FoodKind.GREEN,
        "油脂球（蟑螂爱吃）": FoodKind.GREASE, "果醋滴（果蝇爱吃）": FoodKind.VINEGAR}


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
        self.count_var = tk.StringVar(self.win)
        self.status_var = tk.StringVar(self.win, "点击上方按钮，添加第一只宠物。")
        self.detail_var = tk.StringVar(self.win, "选择一只宠物，即可投喂、暂停或隐藏。")
        self.food_var = tk.StringVar(self.win, "面包屑")
        self.feeding_var = tk.BooleanVar(self.win, bool(app.feeding))
        self._configure_style()
        outer = ttk.Frame(self.win, padding=(16, 14), style="Pet2D.TFrame")
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="桌面昆虫", style="Pet2D.Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="慢慢靠近它，或放一点食物。", style="Pet2D.Muted.TLabel").pack(anchor="w", pady=(3, 12))
        add = ttk.Frame(outer, style="Pet2D.TFrame")
        add.pack(fill="x")
        add.columnconfigure((0, 1), weight=1)
        self.add_roach_btn = ttk.Button(add, text="＋ 添加蟑螂", style="Pet2D.Primary.TButton",
                                       command=lambda: self.add_pet("species.cockroach"))
        self.add_fly_btn = ttk.Button(add, text="＋ 添加果蝇", style="Pet2D.TButton",
                                     command=lambda: self.add_pet("species.fruitfly"))
        self.add_roach_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.add_fly_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Label(outer, textvariable=self.count_var, style="Pet2D.Muted.TLabel").pack(anchor="w", pady=(12, 6))
        listing = ttk.Frame(outer, height=140, style="Pet2D.TFrame")
        listing.pack(fill="both", expand=True)
        listing.pack_propagate(False)
        self.tree = ttk.Treeview(listing, columns=("name", "activity", "food"), show="headings",
                                 selectmode="browse", height=4, style="Pet2D.Treeview")
        for name, label, width in (("name", "宠物", 104), ("activity", "正在做什么", 119), ("food", "饱食", 54)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=width-20, anchor="w", stretch=name != "food")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("hidden", foreground=MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._refresh_selection())
        self.tree.bind("<Double-1>", lambda event: self.toggle_visibility() if self.selected() in self.app.hidden_pets() else None)
        ttk.Label(outer, textvariable=self.detail_var, style="Pet2D.Muted.TLabel", wraplength=326).pack(anchor="w", pady=(10, 7))
        self.food_box = ttk.Combobox(outer, textvariable=self.food_var, values=tuple(FOOD), state="readonly", style="Pet2D.TCombobox")
        self.food_box.pack(fill="x", pady=(0, 7))
        actions = ttk.Frame(outer, style="Pet2D.TFrame")
        actions.pack(fill="x")
        actions.columnconfigure((0, 1, 2), weight=1)
        self.feed_now_btn = ttk.Button(actions, text="喂它", command=self.feed_selected, style="Pet2D.Primary.TButton")
        self.freeze_btn = ttk.Button(actions, text="暂停", command=self.toggle_freeze, style="Pet2D.TButton")
        self.hide_btn = ttk.Button(actions, text="隐藏", command=self.toggle_visibility, style="Pet2D.TButton")
        self.remove_btn = ttk.Button(actions, text="移除", command=self.remove_selected, style="Pet2D.TButton")
        self.memory_btn = ttk.Button(actions, text="记住的事", command=self.open_memory, style="Pet2D.TButton")
        self.settings_btn = ttk.Button(actions, text="设置", command=self.open_settings, style="Pet2D.TButton")
        for i, button in enumerate((self.feed_now_btn, self.freeze_btn, self.hide_btn,
                                    self.remove_btn, self.memory_btn, self.settings_btn)):
            button.grid(row=i//3, column=i%3, sticky="ew", padx=2, pady=3)
        bulk = ttk.Frame(outer, style="Pet2D.TFrame")
        bulk.pack(fill="x", pady=(9, 0))
        self.pause_all_btn = ttk.Button(bulk, text="全部暂停", command=self.pause_all, style="Pet2D.Small.TButton")
        self.pause_all_btn.pack(side="left")
        self.recall_all_btn = ttk.Button(bulk, text="全部召回", command=self.recall_all, style="Pet2D.Small.TButton")
        self.recall_all_btn.pack(side="left", padx=7)
        ttk.Button(bulk, text="收起", command=self.hide, style="Pet2D.Small.TButton").pack(side="right")
        ttk.Label(outer, textvariable=self.status_var, style="Pet2D.Muted.TLabel", wraplength=326).pack(anchor="w", pady=(9, 0))
        self.win.bind("<Control-Key-1>", lambda event: self.add_pet("species.cockroach"))
        self.win.bind("<Control-Key-2>", lambda event: self.add_pet("species.fruitfly"))
        self.tree.bind("<space>", lambda event: self._space())
        self.win.bind("<Escape>", lambda event: self.hide())
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
        return FOOD.get(self.food_var.get(), FoodKind.CRUMB)

    def selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def set_on_drop_request(self, callback):
        self.on_drop_request = callback

    def _name(self, pid, handle):
        if pid not in self._names:
            species = SPECIES.get(handle.state.species_id, handle.state.name)
            self._serial[species] = self._serial.get(species, 0) + 1
            self._names[pid] = f"{species} {self._serial[species]}"
        return self._names[pid]

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
            activity = "隐藏中" if pid in hidden else ("已暂停" if state.frozen else ACTIVITY.get(state.activity.value, "活动中"))
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
        self.count_var.set(f"桌面 {len(self.app.pets)} / {self.app.cfg.max_pets} 只    隐藏 {len(hidden)} 只")
        full = len(self.app.pets) >= self.app.cfg.max_pets
        for button in (self.add_roach_btn, self.add_fly_btn):
            button.state(["disabled"] if full else ["!disabled"])
        self.pause_all_btn.state(["!disabled"] if self.app.pets else ["disabled"])
        all_paused = bool(self.app.pets) and all(h.state.frozen for h in self.app.pets.values())
        self.pause_all_btn.configure(text="全部继续" if all_paused else "全部暂停")
        self.recall_all_btn.state(["!disabled"] if hidden else ["disabled"])
        if full:
            self.status_var.set(f"桌面已有 {self.app.cfg.max_pets} 只；隐藏或移除后可继续添加。")
        elif not handles:
            self.status_var.set("点击上方按钮，添加第一只宠物。")
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
        self.hide_btn.configure(text="召回" if pid in hidden else "隐藏")
        self.freeze_btn.configure(text="继续" if available and handle.state.frozen else "暂停")
        if not available:
            self.detail_var.set("选择一只宠物，即可投喂、暂停或隐藏。")
            return
        status = self.app.pet_status(pid) if visible else handle.state.as_summary()
        fear = status.get("emotion", {}).get("fear", 0)
        mood = "有些警觉" if fear > .3 else "很放松"
        self.detail_var.set(f"{self._name(pid, handle)} · {'隐藏中，随时可召回' if not visible else mood}")

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
        self.status_var.set(f"已添加 {self._names[pid]}。按住虫体可拖动。")

    def feed_selected(self):
        if self.selected() in self.app.pets and self.on_drop_request:
            self.on_drop_request(self.selected_food_kind)
            self.status_var.set("已在它附近放下食物，饿了就会过去吃。")

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
        self.status_var.set("全部继续活动。" if paused else "全部已暂停。再次点击可继续。")

    def toggle_visibility(self):
        pid = self.selected()
        if pid in self.app.hidden_pets():
            if not self.app.recall_pet(pid):
                self.status_var.set("桌面名额已满，先隐藏另一只再召回。")
                return
        elif pid in self.app.pets:
            self.app.hide_pet(pid)
        self.refresh_pets()

    def recall_all(self):
        count = sum(bool(self.app.recall_pet(pid)) for pid in list(self.app.hidden_pets()))
        self.refresh_pets()
        remaining = len(self.app.hidden_pets())
        self.status_var.set(f"已召回 {count} 只。" + (f"另有 {remaining} 只等待桌面名额。" if remaining else ""))

    def remove_selected(self):
        pid = self.selected()
        if pid and messagebox.askyesno("移除宠物", "从桌面移除这只宠物？历史档案会保留。", parent=self.win):
            self.app.remove_pet(pid)
            self.refresh_pets()
            self.status_var.set("已移除宠物，历史档案已保留。")

    def _optional_window(self, key, title):
        previous = self.extra_windows.get(key)
        if previous is not None and previous.winfo_exists():
            previous.lift()
            previous.focus_force()
            return None
        win = tk.Toplevel(self.win)
        win.title(title)
        win.configure(background=BACKGROUND)
        win.attributes("-topmost", True)
        width, height = round(350*self._scale), round(400*self._scale)
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
        previous = self.extra_windows.get("memory")
        if previous is not None and previous.winfo_exists() and getattr(self, "_memory_pid", None) != pid:
            previous.destroy()
        self._memory_pid = pid
        win = self._optional_window("memory", f"{self._names[pid]} · 记住的事")
        if win is None:
            return
        frame = ttk.Frame(win, padding=14, style="Pet2D.TFrame")
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="最近的经历", style="Pet2D.TLabel").pack(anchor="w", pady=(0, 8))
        text = tk.Text(frame, height=8, font=("Microsoft YaHei UI", -round(14*self._scale)), wrap="word", relief="flat", background="white", foreground=INK, padx=9, pady=9)
        text.pack(fill="both", expand=True)
        def refresh():
            lines = [line for line in self.app.memory_of(pid) if not line.startswith(("[NN]", "[GF]"))]
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", "\n\n".join(lines) or "还没有留下经历。喂它、慢慢靠近它，再回来看看。")
            text.configure(state="disabled")
        def clear():
            if messagebox.askyesno("清除记忆", "永久清除这只宠物的记忆？此操作无法撤销。", parent=win):
                self.app.clear_memory(pid)
                refresh()
        controls = ttk.Frame(frame, style="Pet2D.TFrame")
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="刷新", command=refresh, style="Pet2D.TButton").pack(side="left")
        ttk.Button(controls, text="清除记忆…", command=clear, style="Pet2D.TButton").pack(side="right")
        refresh()

    def open_settings(self):
        pid = self.selected()
        previous = self.extra_windows.get("settings")
        if previous is not None and previous.winfo_exists() and getattr(self, "_settings_pid", None) != pid:
            previous.destroy()
        self._settings_pid = pid
        win = self._optional_window("settings", "NeuroPet · 设置")
        if win is None:
            return
        width, height = round(350*self._scale), round(360*self._scale)
        win.geometry(f"{width}x{height}+{self.win.winfo_rootx()+20}+{self.win.winfo_rooty()+40}")
        win.minsize(width, height)
        frame = ttk.Frame(win, padding=16, style="Pet2D.TFrame")
        frame.pack(fill="both", expand=True)
        label = f"{self._names[pid]} 的显示大小" if pid in self.app.pets else "宠物显示大小（请先选中桌面宠物）"
        ttk.Label(frame, text=label, style="Pet2D.TLabel", wraplength=300).pack(anchor="w")
        sizes = (0.5, 0.75, 1.0, 1.5, 2.0)
        pid = self.selected()
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
        ttk.Checkbutton(frame, text="启动时打开控制面板", variable=startup, command=save_startup, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        def toggle_click_feed():
            self.app.toggle_feeding(self.feeding_var.get())
        ttk.Checkbutton(frame, text="在桌面点击放食物（自动结束）", variable=self.feeding_var, command=toggle_click_feed, style="Pet2D.TCheckbutton").pack(anchor="w", pady=5)
        ttk.Label(frame, text="关闭面板会收起程序，宠物继续活动。\n从系统托盘可再次打开。", style="Pet2D.Muted.TLabel", wraplength=300).pack(anchor="w", pady=(15, 14))
        ttk.Button(frame, text="保存并退出程序", command=self.app.shutdown, style="Pet2D.TButton").pack(fill="x")

    def visible_windows(self):
        return [win for win in (self.win, *self.extra_windows.values())
                if win.winfo_exists() and win.winfo_viewable()]

    def set_feeding(self, on):
        self.feeding_var.set(bool(on))

    def hide(self):
        for win in self.extra_windows.values():
            if win.winfo_exists():
                win.destroy()
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
