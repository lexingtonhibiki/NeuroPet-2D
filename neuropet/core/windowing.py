"""悬浮舞台:单一全屏色键透明窗口(共享舞台方案,依据 scratch/原型测量报告.md)。

- 色键 #010101 的像素既透明又点击穿透(实测 WindowFromPoint 验证);
- 每只宠物一个 canvas image 项(只 create 一次)+ 每帧新建 PhotoImage 经
  itemconfig 换图(实测优于 tk copy 原地复用 ~7 倍,见 update_pet 说明);
- 食物/区域用矢量描边绘制,减少对下层窗口点击的误捕获;
- 定时器分辨率提升 timeBeginPeriod(1),否则 after(16) 会被钳到 ~30fps(实测)。
"""
from __future__ import annotations

import atexit
import time
import tkinter as tk

from neuropet.render.trail import TrailLayer

MAGIC = "#010101"  # 色键透明色:该颜色像素透明且点击穿透
_KEY_RGB = (1, 1, 1)  # 同上,数值形式(RGB 预合成底色,见 update_pet)

# ================= v0.2.0:视口节拍(黑框闪烁的修复面) =================
# 诚实口径:**本轮没有复现过黑框**,也没有做过任何屏幕录制或取帧比对。下面
# 写的是"改动内容"与"它针对的假设",不是已确认的根因;是否真的消除了闪烁,
# 需要真实桌面运行才能判定,本轮按用户要求未启动桌宠。
#
# 实际改了三处,全部围绕同一个假设:**色键窗在原生 resize/move 与画布重绘的
# 间隙里被涂上窗口类背景(纯黑),而键色是 #010101,所以"还没画到"的区域会闪
# 成可见黑块。** 若这个假设成立,能减少这类间隙的办法是:少改几何、改的时候
# 不上屏、改的时候不裁掉已经可见的像素。
#  ① 过渡原子化:旧码 geometry → update_idletasks() → 滚动。update_idletasks()
#     会跑完挂起的 idle 重绘,此刻窗口位置已是新的而画布原点还是旧的,这个
#     中间帧会被呈现。现改为在 Win32 ``WM_SETREDRAW`` 抑制下 geometry 与滚动
#     一次做完,finally 恢复后只呈现一帧。
#  ② 只扩不缩:扩边时新视口 ⊇ 旧视口 ∪ 内容 ⇒ 屏幕上已可见的像素只增不减,
#     不会因改几何而"消失一截再回来"。收边只在内容静止一段时间后发生,且那时
#     内容必然在视口内,同样连续。
#  ③ 节流:内容包围盒按 FIT_BOUNDS_INTERVAL_S 重算(5Hz),不是每帧。
#
# 滚动分数的换算**本轮未改**,并已对照 Tk 源码核对过:
# ``generic/tkCanvas.c`` 的 ``CANV_XVIEW``/``CANV_YVIEW`` 在 TK_SCROLL_MOVETO
# 分支是 ``newX = scrollX1 - inset + (int)(fraction * (scrollX2 - scrollX1) + .5)``,
# 分母是**整个 scrollregion 宽**(inset = 2×borderWidth,本画布 bd=0 即 0)。
# scrollregion 恒为 (0, 0, self.w, self.h),故 ``fraction = left / self.w``
# 才让可见左缘正好落在 left —— 与改动前一致,不要"优化"成减视口宽的分母。
FIT_BOUNDS_INTERVAL_S = 0.2   # 内容包围盒重算节流(5Hz;原来每帧都算 bbox)
FIT_EDGE = 12                  # 扩边判据的回差(px)
FIT_MARGIN_GROW = 96           # 运动中窗口在内容外的余量(沿用旧值)
FIT_MARGIN_SHRINK = 48         # 静止收边后的余量
FIT_SHRINK_EAGER_S = 2.0       # 内容稳定且视口明显过大 → 提前收边
FIT_SHRINK_QUIET_S = 6.0       # 内容稳定多久后无条件收边
FIT_OVERWIDE = 6.0             # 视口面积 > 需要面积 ×该值 ⇒ 判"过大"
WM_SETREDRAW = 0x000B          # Win32:开关窗口重绘
_RDW = 0x0001 | 0x0080       # RedrawWindow: INVALIDATE | ALLCHILDREN


def set_dpi_aware() -> None:
    """必须在创建任何 tkinter 窗口之前调用;坐标系统一为物理像素(实测 Δ=0)。"""
    import ctypes
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


_timer_boosted = False


def boost_timer_resolution() -> None:
    """把系统定时器分辨率提到 1ms。必须在进入主循环前调用。"""
    global _timer_boosted
    try:
        import ctypes
        ctypes.windll.winmm.timeBeginPeriod(1)
        _timer_boosted = True
        atexit.register(lambda: ctypes.windll.winmm.timeEndPeriod(1))
    except Exception:
        pass


def working_set_mb() -> float:
    """当前进程工作集(MB)。实现照搬 scratch/proto_overlay.py 已验证版本
    (坑:伪句柄需 restype=HANDLE;结构须用 PROCESS_MEMORY_COUNTERS_EX)。"""
    import ctypes
    import ctypes.wintypes as wt

    class PMC_EX(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    k32.GetCurrentProcess.restype = wt.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wt.HANDLE, ctypes.POINTER(PMC_EX), wt.DWORD]
    psapi.GetProcessMemoryInfo.restype = wt.BOOL
    pmc = PMC_EX()
    pmc.cb = ctypes.sizeof(pmc)
    h = k32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(pmc)):
        return -1.0
    return pmc.WorkingSetSize / 1048576


# ============================================================================
# PF 快赢①:显示签名与上传去重
# 依据 docs/references/性能优化发散_2026.md §A2-b/H1:OPT-7 签名(姿态+几何
# +状态键)+窗口坐标不变时,update_pet 的显示结果逐位不变 → 可整体跳过
# render_pose 重绘与 PhotoImage 重建/上传(空闲宠省 2.6~3.4ms/宠,实测口径)。
#
# 两条独立判据(各自充分,任一命中即可跳过;均只读,不改 renderer):
#  A) OPT-7 缓存对象同一:renderer._pose_signature(不含 alert/sprint/pitch/
#     bones,3~5 位舍入)命中且返回对象 == 上次实际上传的对象时,正常路径
#     render_pose 也必然返回同一对象(见 render_pose_hybrid),跳过重绘+上传
#     与执行整条路径的显示结果严格一致 —— 这是与现管线语义等价的"免费跳过";
#  B) 完整显示签名相等:本模块 display_signature 对完整 pose + 完整 traits +
#     超采样档 ss + 整数画布坐标做精确(浮点不舍入)规范化,逐项相等 ⇒
#     渲染输出逐位相等。覆盖 OPT-7 签名未包含的 alert/sprint/pitch/bones 等
#     键,在 OPT-7 条目被挤出(cap 8)时仍能跳过,方向只会多画、不会漏画。
# 注意:静息姿态触角按 0.7Hz 慢扫(body/poses.py,拟真特性),站立宠 pose 逐帧
# 变化 → 签名天然不命中(显示确实在变,必须重绘);命中场景是冻结/抓握停驻
# 等 pose 完全静止的宠(微弹性滤波器一阶收敛后逐位不变)。
# ============================================================================
def _canon(v):
    """递归规范化为可哈希元组(dict→按键排序的项元组;序列→元组;
    float 原样(值相等⇔位等,NaN 归一为哨兵);numpy 标量等转 float)。"""
    if isinstance(v, dict):
        return tuple(sorted((k, _canon(val)) for k, val in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_canon(x) for x in v)
    if isinstance(v, float):
        return "nan" if v != v else v
    if isinstance(v, (int, bool, str, bytes)) or v is None:
        return v
    try:
        return float(v)
    except Exception:
        return repr(v)


def display_signature(pose: dict, traits: dict, x: float, y: float,
                      ss: int = 0) -> tuple:
    """该宠画布显示结果的全部输入快照(可哈希;相等 ⇒ 渲染输出逐位不变)。

    坐标取 int:update_pet 只按 (int(x), int(y)) 定位画布项,浮点位置在
    同一像素内变化不改变显示,故按整型纳入签名(多省一次无效上传)。
    ss(超采样档)纳入:降级阶梯切档会改变抖动域,输出随之改变。
    (ADR-0034:遮挡维度随 hide 退役,签名回到 5 参。)"""
    return (_canon(pose), _canon(traits), int(ss), int(x), int(y))




class _RedrawGuard:
    """Win32 ``WM_SETREDRAW`` 抑制:批量改几何/滚动期间**不呈现中间态**。

    针对的假设:色键窗在原生 resize/move 擦除期间按窗口类背景涂黑,而键色是
    #010101,于是"尚未绘制"的黑与键色不同 ⇒ 可能闪出可见黑块。抑制重绘让这
    段过渡根本不上屏。**这是修复目标,本轮未复现也未验证该闪烁。**

    不改配色、不退回全屏画布,也不做每帧 hide/show 遮掩。

    失败安全:任一步异常都已在 ``__exit__`` 的 finally 语义下恢复(wParam=1
    + 一次 RedrawWindow),异常照旧向上抛由调用点兜住;非 Windows 或
    ``ctypes`` 不可用时整个类退化为空操作,行为与修复前完全一致。
    """

    def __init__(self, *tk_windows) -> None:
        self._wins = [w for w in tk_windows if w is not None]
        self._user32 = None
        self._hwnds: list[int] = []

    def __enter__(self):
        if not self._wins:
            return self
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
            self._hwnds = [int(w.winfo_id()) for w in self._wins]
            for hwnd in self._hwnds:
                self._user32.SendMessageW(hwnd, WM_SETREDRAW, 0, 0)
        except Exception:
            self._user32 = None
            self._hwnds = []
        return self

    def __exit__(self, *exc) -> bool:
        if self._user32 is None or not self._hwnds:
            return False
        try:
            for hwnd in self._hwnds:
                self._user32.SendMessageW(hwnd, WM_SETREDRAW, 1, 0)
                self._user32.RedrawWindow(hwnd, None, None, _RDW)
        except Exception:
            pass
        self._user32 = None
        self._hwnds = []
        return False        # 不吞异常


class OverlayStage:
    """全屏共享舞台:所有宠物/食物/区域画在同一透明窗口里。"""

    def __init__(self, root: tk.Tk, screen_w: int, screen_h: int) -> None:
        self.w, self.h = screen_w, screen_h
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.geometry("1x1+0+0")
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", MAGIC)
        self.win.configure(bg=MAGIC)
        self.canvas = tk.Canvas(self.win, width=1, height=1, bg=MAGIC,
                                scrollregion=(0, 0, screen_w, screen_h),
                                confine=False,
                                xscrollincrement=1, yscrollincrement=1,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._viewport = (0, 0, 1, 1)
        self._fit_checked = 0.0          # 内容包围盒上次重算时刻(节流)
        self._fit_content = None         # 上次重算到的内容矩形
        self._fit_quiet_since = 0.0      # 内容矩形最后一次变化的时刻
        self._pet_items: dict[str, int] = {}
        # 值=(常驻 tk.PhotoImage, 尺寸):尺寸不变时原地换数据,见 update_pet
        self._pet_photos: dict[str, tuple[tk.PhotoImage, tuple[int, int]]] = {}
        # ---- ADR-0032 宠物重叠合成(第十二轮用户反馈"宠物碰到宠物有正方形
        # 遮挡"):canvas 项是矩形不透明位图,两宠重叠时上层项的键色像素把
        # 下层宠挖成方块。现缓存每宠最新原始 RGBA,重叠组(≥2 且 bbox 相交)
        # 改为按成员顺序 α 合成为一张图、单 canvas 项渲染,成员单项挪出屏;
        # 分离后立即从缓存恢复单项(无闪烁)。_pet_raw=(RGBA, ix, iy)。
        self._pet_raw: dict[str, tuple] = {}
        self._pet_offsets: dict[str, tuple[int, int]] = {}
        self._batch = False
        self._image_dirty = set()
        self._merged_groups: dict[str, tuple[str, ...]] = {}
        self._merged_items: dict[str, int] = {}
        self._merged_photos: dict[str, tuple[tk.PhotoImage, tuple[int, int]]] = {}
        self._merged_sigs: dict[str, tuple] = {}   # 帧签名(成员图对象/坐标/遮挡)
        self._static_ids: list[int] = []
        self._handlers: dict[str, object] = {}
        # v0.2.0 高速拖尾:矢量线段装饰层(非图像,项数与长度双重有界)。
        # 挂在同一块画布上 ⇒ 自动参与 fit_viewport 的包围盒,复用现成的
        # 视口节拍,不新增任何 Win32 查询或位图内存。
        self.trail = TrailLayer(self.canvas)
        boost_timer_resolution()

    # ---- 宠物项 ----
    def bind_interaction(self, on_press, on_drag, on_release) -> None:
        self.canvas.bind("<ButtonPress-1>", on_press)
        self.canvas.bind("<B1-Motion>", on_drag)
        self.canvas.bind("<ButtonRelease-1>", on_release)

    def ensure_pet_item(self, pet_id: str) -> None:
        if pet_id not in self._pet_items:
            item = self.canvas.create_image(-9999, -9999, anchor="center")
            self._pet_items[pet_id] = item

    def update_pet(self, pet_id: str, x: float, y: float, pil_image) -> None:
        """一帧:换图 + 移动。pil_image 为该宠物局部渲染(RGBA,透明=色键)。

        【ADR-0032 重叠合成】先缓存原始帧再查重叠组:本宠与 ≥1 只他宠 bbox
        相交 → 成员单项挪出屏,由 _render_merged 按成员序 α 合成为单 canvas
        项(键色像素不再挖洞);无重叠 → 单项上传(原路径,见 _upload_single
        的性能注记)。分离瞬间由 reconciliation 从缓存立即恢复单项,无闪烁。"""
        self.ensure_pet_item(pet_id)
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        ix, iy = int(x), int(y)
        # Transparent margins take space in Tk and cause false overlap groups.
        # Crop them while preserving the exact screen coordinate of every pixel.
        box = pil_image.getbbox(alpha_only=True)
        if box is not None:
            old_w, old_h = pil_image.size
            pil_image = pil_image.crop(box)
            offset = (box[0] + pil_image.width//2 - old_w//2,
                      box[1] + pil_image.height//2 - old_h//2)
        else:
            offset = (0, 0)
        self._pet_offsets[pet_id] = offset
        ix, iy = ix + offset[0], iy + offset[1]
        self._pet_raw[pet_id] = (pil_image, ix, iy)
        if self._batch:
            self._image_dirty.add(pet_id)
            return
        groups = self._compute_overlap_groups()
        # ---- 组 reconciliation:成员单项全部挪出屏(合成项接管显示) ----
        for g in groups:
            for pid in g:
                if pid in self._pet_items:
                    self.canvas.coords(self._pet_items[pid], -9999, -9999)
        new_groups = {f"|".join(g): g for g in groups}
        for key, members in new_groups.items():
            self._render_merged(key, members)
        for key in list(self._merged_groups):
            if key not in new_groups:
                self._dispose_merged(key)
        # 离开组的宠物:从缓存立即恢复单项(其本帧未 update,不闪)
        old_members = {pid for g in self._merged_groups.values() for pid in g}
        new_members = {pid for g in groups for pid in g}
        for pid in old_members - new_members:
            raw = self._pet_raw.get(pid)
            if raw is not None and pid in self._pet_items:
                self._upload_single(pid, raw[1], raw[2], raw[0])
        if pet_id not in new_members:
            self._upload_single(pet_id, ix, iy, pil_image)
        self._merged_groups = new_groups

    # ---- 重叠组检测(≤max_pets 只,两两 bbox 相交 union-find,零分配热路径) ----
    def begin_frame(self):
        self._batch = True

    def end_frame(self):
        self._batch = False
        self.move_pets({}, _flush=True)
        self._image_dirty.clear()

    def move_pets(self, positions: dict, *, _flush=False) -> None:
        """Move cached images every simulation tick, independently of pose rate."""
        changed = False
        for pid, (x, y) in positions.items():
            dx, dy = self._pet_offsets.get(pid, (0, 0))
            x, y = int(x)+dx, int(y)+dy
            raw = self._pet_raw.get(pid)
            if raw is not None and raw[1:] != (int(x), int(y)):
                self._pet_raw[pid] = (raw[0], int(x), int(y))
                changed = True
        if self._batch or (not changed and not _flush):
            return
        groups = self._compute_overlap_groups()
        new_groups = {"|".join(g): g for g in groups}
        old_members = {pid for g in self._merged_groups.values() for pid in g}
        new_members = {pid for g in groups for pid in g}
        for key, members in new_groups.items():
            for pid in members:
                self.canvas.coords(self._pet_items[pid], -9999, -9999)
            self._render_merged(key, members)
        for key in list(self._merged_groups):
            if key not in new_groups:
                self._dispose_merged(key)
        for pid, raw in self._pet_raw.items():
            if pid in new_members:
                continue
            if pid in old_members or pid in self._image_dirty:
                self._upload_single(pid, raw[1], raw[2], raw[0])
            else:
                self.canvas.coords(self._pet_items[pid], raw[1], raw[2])
        self._merged_groups = new_groups

    def _content_bounds(self) -> tuple[int, int, int, int]:
        """画布上所有"与屏幕相交"的项的包围盒(屏幕坐标)。

        离屏项(重叠组合成的成员单项被挪到 -9999)必须过滤掉:它们会把包围盒
        拉成负数,与屏幕求交后得到空矩形,视口随之塌成 1×1。没有可见项时
        返回左上角 1×1 占位(窗口缩到最小,不留全屏黑底)。
        """
        boxes = []
        for item in self.canvas.find_all():
            box = self.canvas.bbox(item)
            if (box and box[2] > box[0] and box[3] > box[1]
                    and box[2] > 0 and box[3] > 0
                    and box[0] < self.w and box[1] < self.h):
                boxes.append(box)
        if not boxes:
            return (0, 0, 1, 1)
        return (max(0, min(b[0] for b in boxes)), max(0, min(b[1] for b in boxes)),
                min(self.w, max(b[2] for b in boxes)),
                min(self.h, max(b[3] for b in boxes)))

    def fit_viewport(self, force: bool = False) -> None:
        """屏幕坐标作画 + 窗口只包住可见内容(v0.2.0:只扩不缩 + 原子过渡)。

        节流:完整内容包围盒按 ``FIT_BOUNDS_INTERVAL_S`` 重算(5Hz)。缓存的
        宠物矩形越出视口时立即扩边,避免高速宠物等待下一次轮询才重新出现。
        扩边:任何内容越出视口 ``FIT_EDGE`` 立即扩,且新视口取
        **旧视口 ∪ 内容(+余量)** —— 屏幕上的像素集合只增不减,所以扩边本身
        连续,不会被"新露出来的空白"闪一下。
        收边:内容稳定 ``FIT_SHRINK_EAGER_S`` 且视口明显过大,或稳定
        ``FIT_SHRINK_QUIET_S`` 后才收(收边时内容必然在视口内,同样连续)。
        """
        now = time.perf_counter()
        left, top, right, bottom = self._viewport
        escaped = any(
            (x - image.width // 2 < left and left > 0)
            or (y - image.height // 2 < top and top > 0)
            or (x + (image.width + 1) // 2 > right and right < self.w)
            or (y + (image.height + 1) // 2 > bottom and bottom < self.h)
            for image, x, y in self._pet_raw.values())
        if not force and not escaped and now - self._fit_checked < FIT_BOUNDS_INTERVAL_S:
            return
        self._fit_checked = now
        content = self._content_bounds()
        if content != self._fit_content:
            self._fit_content = content
            self._fit_quiet_since = now
        left, top, right, bottom = self._viewport
        grew = escaped or (content[0] < left - FIT_EDGE or content[1] < top - FIT_EDGE
                           or content[2] > right + FIT_EDGE or content[3] > bottom + FIT_EDGE)
        if force or grew:
            margin = FIT_MARGIN_GROW
            new = (max(0, min(left, content[0] - margin)),
                   max(0, min(top, content[1] - margin)),
                   min(self.w, max(right, content[2] + margin)),
                   min(self.h, max(bottom, content[3] + margin)))
            self._apply_viewport(new)
            return
        quiet = now - self._fit_quiet_since
        if quiet < FIT_SHRINK_QUIET_S and not (
                quiet >= FIT_SHRINK_EAGER_S and self._overwide(content)):
            return
        if content == (0, 0, 1, 1):
            self._apply_viewport(content)          # 无内容:收到最小窗口
            return
        margin = FIT_MARGIN_SHRINK
        self._apply_viewport(
            (max(0, content[0] - margin), max(0, content[1] - margin),
             min(self.w, content[2] + margin), min(self.h, content[3] + margin)))

    def _overwide(self, content: tuple[int, int, int, int]) -> bool:
        """视口是否明显大于当前内容需要(面积比 > FIT_OVERWIDE)。"""
        left, top, right, bottom = self._viewport
        need = ((content[2] - content[0] + 2 * FIT_MARGIN_SHRINK)
                * (content[3] - content[1] + 2 * FIT_MARGIN_SHRINK))
        return (right - left) * (bottom - top) > FIT_OVERWIDE * max(1.0, need)

    def _apply_viewport(self, new: tuple[int, int, int, int]) -> None:
        """一次原子过渡:改几何 + 设画布原点,期间不上屏,finally 恢复重绘。

        先落实窗口分配再设原点。Canvas 禁用 confine,因此即使 Windows 的
        Configure 事件稍后才更新画布尺寸,也不会按旧宽度把世界原点夹回 0。

        滚动分数:**``left / self.w``**(未改动)。Tk ``generic/tkCanvas.c`` 的
        ``CANV_XVIEW``/``CANV_YVIEW`` 在 TK_SCROLL_MOVETO 分支为
        ``newX = scrollX1 - inset + (int)(fraction * (scrollX2 - scrollX1) + .5)``,
        分母是整个 scrollregion 宽、不是"scrollregion 宽 − 视口宽";本画布
        ``bd=0`` 故 inset=0,scrollregion 恒为 (0,0,self.w,self.h),于是
        ``fraction = left / self.w`` 让可见左缘正好落在 left。

        整个过程包在 ``_RedrawGuard`` 里 ⇒ 几何与滚动之间的中间帧不上屏,
        只呈现恢复后的一帧(这是本轮的**修复目标**,未经真机验证)。
        """
        if new == self._viewport:
            return
        self._viewport = new
        left, top, right, bottom = new
        width = max(1, right - left)
        height = max(1, bottom - top)
        with _RedrawGuard(self.win, self.canvas):
            self.win.geometry(f"{width}x{height}+{left}+{top}")
            self.win.update_idletasks()          # 落实分配(不上屏)
            self.canvas.xview_moveto(min(1.0, left / max(1, self.w)))
            self.canvas.yview_moveto(min(1.0, top / max(1, self.h)))

    def _compute_overlap_groups(self) -> list[tuple[str, ...]]:
        ids = list(self._pet_raw)
        n = len(ids)
        if n < 2:
            return []
        rects = []
        for pid in ids:
            img, ix, iy = self._pet_raw[pid]
            w, h = img.size
            rects.append((ix - w // 2, iy - h // 2, ix - w // 2 + w,
                          iy - h // 2 + h))
        parent = list(range(n))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in range(n):
            for j in range(i + 1, n):
                a, b = rects[i], rects[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
        clusters: dict[int, list[int]] = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(i)
        return [tuple(sorted(ids[i] for i in members))
                for members in clusters.values() if len(members) >= 2]

    # ---- 合成项:成员序 α 合成 → 键色/裁剪 → 单 canvas 项 ----
    def _render_merged(self, key: str, members: tuple[str, ...]) -> None:
        from PIL import Image, ImageTk
        raws = [self._pet_raw[pid] for pid in members]
        # 帧签名去重:成员图对象/坐标
        # 全部不变 ⇒ 合成输出逐位不变,跳过重合成与上传(重叠是稳态,
        # 每帧重建浪费;测试共享舞台的陈旧成员同样被签名正确去重)。
        sig = tuple((id(r[0]), r[1], r[2]) for r in raws)
        if self._merged_sigs.get(key) == sig and key in self._merged_items:
            return
        l = min(r[1] - r[0].size[0] // 2 for r in raws)
        t = min(r[2] - r[0].size[1] // 2 for r in raws)
        r_ = max(r[1] + (r[0].size[0] + 1) // 2 for r in raws)
        b_ = max(r[2] + (r[0].size[1] + 1) // 2 for r in raws)
        canvas_img = Image.new("RGBA", (r_ - l, b_ - t), (0, 0, 0, 0))
        for img, ix, iy in raws:                    # 成员序 = 稳定 z 序
            canvas_img.alpha_composite(img, (ix - img.size[0] // 2 - l,
                                             iy - img.size[1] // 2 - t))
        rgb = Image.new("RGB", canvas_img.size, _KEY_RGB)
        rgb.paste(canvas_img, (0, 0), canvas_img)
        photo = ImageTk.PhotoImage(rgb)
        cx, cy = (l + r_) // 2, (t + b_) // 2
        item = self._merged_items.get(key)
        if item is None:
            item = self.canvas.create_image(cx, cy, anchor="center")
            self._merged_items[key] = item
        # 时序同 update_pet:先切画布引用再释放旧 photo(refcount→0)
        self.canvas.itemconfig(item, image=photo)
        self.canvas.coords(item, cx, cy)
        self._merged_photos[key] = (photo, rgb.size)
        self._merged_sigs[key] = sig

    def _dispose_merged(self, key: str) -> None:
        item = self._merged_items.pop(key, None)
        if item is not None:
            self.canvas.delete(item)
        self._merged_photos.pop(key, None)
        self._merged_sigs.pop(key, None)

    # ---- 单项上传(update_pet 原路径;无重叠时的唯一显示通道) ----
    def _upload_single(self, pet_id: str, x: float, y: float,
                       pil_image) -> None:
        """单项上传(ADR-0032 前的 update_pet 原路径,性能注记随迁):

        PhotoImage 更新策略(D2 规格书技巧①的落地与实测结论):
        ImageTk.PhotoImage 无公开 paste;tkinter.PhotoImage.put 需逐像素填色串
        (~57k 次/帧)不可行;剩"常驻 photo + tk copy 原地换数据"与
        "每帧新建 PhotoImage + itemconfig"两条路,应用内实测(6s 真屏运行,
        1920×1080 色键分层窗,两宠物):
          常驻+copy:tk copy 段 P50 ≈ 19.0ms/次 —— 对已显示 photo 的数据变更
            会同步触发色键窗受损区重绘(独立基准窗口未映射测得 0.64ms,失真);
          每帧新建+itemconfig:整段 P50 ≈ 2.6ms/帧(itemconfig 仅切引用,
            脏区在 idle 合并;Photoshop 式原地复用反而慢一个数量级)。
        故采用"每帧新建 + itemconfig",保留:canvas 项只 create 一次、
        常驻引用防 GC(原型坑 #2)、尺寸记录(换档诊断用)。

        【PF 性能专项补充:RGB 预合成上传】Pillow 11.3 ImageTk.PhotoImage 对
        含混合 α 的 RGBA 图走 Tk 慢通道:蟑螂 240² 实测 P50 8.3ms/帧(蝇 156²
        0.36ms,同为 RGBA 但内容不同差异达 10×);转成"α 合成到键色 #010101 的
        不透明 RGB"后同样 240² 只要 0.20ms(42×)。显示语义严格等价:色键窗下
        RGBA photo 的显示结果 = 像素与窗口底色(键色)按 α 混合,与本地预合成
        到 (1,1,1) 上的结果一致;α=0 的像素两者都落在键色上 = 透明。
        (scratch/perf_probe.py --app 可复测;渲染器输出本身不变。)

        跳帧判据(ADR-0034:遮挡裁剪随 hide 退役,仅剩图/位两元):同
        pil_image 对象 + 同整型坐标不变 ⇒ 显示逐位不变,跳过重绘与上传。"""
        from PIL import Image, ImageTk
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        ix, iy = int(x), int(y)
        # α 预合成到键色底 → 不透明 RGB:Tk 上传走快通道(实测 42×),显示结果
        # 与"RGBA 直接贴色键窗"逐像素等价(色键窗显示即与键色底的 α 混合)。
        rgb = Image.new("RGB", pil_image.size, _KEY_RGB)
        rgb.paste(pil_image, (0, 0), pil_image)
        photo = ImageTk.PhotoImage(rgb)
        # AG5 内存专项(换档后 ImageTk 尺寸随 k 自动变化,此处只管释放时序):
        # 必须先 itemconfig 把画布项切到新 photo,**再**覆盖 _pet_photos 释放旧
        # photo 的引用。旧顺序(先存后切)会让旧 PhotoImage 在画布项仍引用它时
        # 被 tkinter.Image.__del__ 执行 "image delete" → 间歇性
        # TclError: image "pyimageN" doesn't exist(6s 长跑实测必现)。
        # 新顺序下旧 photo 在画布切换后立即释放(refcount→0,无 GC 等待),
        # PhotoImage 驻留恰好 1 帧/宠物,k=2 档 480² RGB ≈0.66MB 峰值可控。
        self.canvas.itemconfig(self._pet_items[pet_id], image=photo)
        self.canvas.coords(self._pet_items[pet_id], int(x), int(y))
        self._pet_photos[pet_id] = (photo, (pil_image.size[0], pil_image.size[1]))

    def remove_pet(self, pet_id: str) -> None:
        self.trail.remove(pet_id)
        if pet_id in self._pet_items:
            self.canvas.delete(self._pet_items[pet_id])
            del self._pet_items[pet_id]
        self._pet_photos.pop(pet_id, None)
        self._pet_offsets.pop(pet_id, None)
        self._image_dirty.discard(pet_id)
        was_grouped = any(pet_id in g for g in self._merged_groups.values())
        self._pet_raw.pop(pet_id, None)
        if was_grouped:
            # ADR-0032:成员被移除 → 组重算,余下成员立即从缓存恢复单项
            groups = self._compute_overlap_groups()
            new_groups = {"|".join(g): g for g in groups}
            for key, members in new_groups.items():
                self._render_merged(key, members)
            for key in list(self._merged_groups):
                if key not in new_groups:
                    self._dispose_merged(key)
            old_members = {pid for g in self._merged_groups.values()
                           for pid in g}
            new_members = {pid for g in groups for pid in g}
            for pid in old_members - new_members:
                raw = self._pet_raw.get(pid)
                if raw is not None and pid in self._pet_items:
                    self._upload_single(pid, raw[1], raw[2], raw[0])
            self._merged_groups = new_groups

    # ---- 静态物(食物/区域) ----
    def render_static(self, foods: dict, zones: dict) -> None:
        """仅在内容变化时调用(矢量绘制,开销可忽略)。区域用描边,避免大面积
        捕获本应穿透的下层点击。"""
        for iid in self._static_ids:
            self.canvas.delete(iid)
        self._static_ids.clear()
        for z in zones.values():
            if z.kind == "cold":
                x, y, r = z.pos[0], z.pos[1], z.radius
                self._static_ids.append(self.canvas.create_oval(
                    x - r, y - r, x + r, y + r, outline="#9dc3e6", width=2, dash=(6, 4)))
                self._static_ids.append(self.canvas.create_text(
                    x, y - r + 14, text="❄ 寒冷区域", fill="#9dc3e6",
                    font=("Microsoft YaHei UI", 9)))
        try:
            from neuropet.feeding import FOODS
        except Exception:
            FOODS = {}
        for f in foods.values():
            spec = FOODS.get(getattr(f, "kind", "crumb"))
            r = (3 + 4 * min(1.0, f.amount)) * (spec.size if spec is not None else 1.0)
            fill = spec.color if spec is not None else "#c8a165"
            x, y = f.pos
            self._static_ids.append(self.canvas.create_oval(
                x - r, y - r, x + r, y + r, fill=fill, outline="#8a6a3d"))
            self._static_ids.append(self.canvas.create_oval(
                x - r * 0.4, y - r * 0.6, x + r * 0.05, y - r * 0.05,
                fill="#e8cf9e", outline=""))

    def hit_pet(self, x: float, y: float, pets: dict) -> str | None:
        """按下点命中的宠物:距中心最近且在其渲染半径内。"""
        best, bd = None, 1e18
        for pid, h in pets.items():
            half = h.body.window_half()
            d = abs(x - h.state.pos[0]) + abs(y - h.state.pos[1])
            if abs(x - h.state.pos[0]) <= half and abs(y - h.state.pos[1]) <= half and d < bd:
                best, bd = pid, d
        return best

    def bubble_layer(self) -> tk.Canvas:
        """C2 气泡层挂载点:返回共享画布, BubbleLayer(ui/bubbles.py)在此
        建独立 canvas 项(tag="bubble"、state="disabled")。气泡项不进宠物
        精灵管线(不走 update_pet/签名去重),创建后 tag_raise 置顶、不吃
        点击(色键窗穿透语义保持);宠物项生命周期(_pet_items)不受影响。"""
        return self.canvas

    def destroy(self) -> None:
        self.trail.clear()
        self.win.destroy()
