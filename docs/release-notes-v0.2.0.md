# NeuroPet 2D v0.2.0

## 中文

Windows x64 便携版，解压 ZIP 后双击 `NeuroPet-2D.exe`。整个文件夹可移动，保存 EXE 与 `_internal/` 在一起。

本轮主题是**连贯运动与桌面体验**：针对爬行时的闪烁黑框与边缘瞬移做处理，把鼠标逼近变成真正的风压，让爬行速度可调，并加入 Windows 开机自启动。**诚实口径：本轮没有复现过黑框，也没有做真机视觉验证**，下面写的是改动内容与它针对的假设，不是已确认的根因。

- **针对闪烁黑框的处理**（未经真机验证）：窗口几何与画布原点改为**一次原子过渡**——在 Win32 `WM_SETREDRAW` 抑制下改完几何和滚动再恢复，只呈现恢复后的完整一帧，目标是让"新几何 + 旧原点"这个中间帧不上屏（色键窗在原生 resize/move 擦除与重绘间隙里可能被涂上窗口类背景的纯黑，而键色是 `#010101`）；窗口在运动中**只扩不缩**，静止一段时间才收边，扩边时可见像素集合只增不减；内容包围盒按 5Hz 节流重算而不是每帧。滚动分数的换算**本轮未改动**，并已对照 Tk 源码 `generic/tkCanvas.c` 核对：`TK_SCROLL_MOVETO` 是 `newX = scrollX1 - inset + fraction × (scrollX2 - scrollX1)`，分母是整个 scrollregion 宽，故 `left / self.w` 才是让可见左缘落在 left 的正确值。
- **边界只有一套口径**：程序读取 Windows 当前显示器的真实工作区（任务栏在底部、顶部、左侧、右侧都适用，不再写死 40/48px），身体软墙、拖拽仲裁、面板落食、随机航点全部走它。靠近边缘提前减速，碰边沿边滑行保留切向位移，宠物被放到工作区之外时**按正常速度自己走回**，不会单帧夹回。
- **全局爬行速度**：设置里选 **0.5× / 1× / 2× / 3× / 4× / 6× / 8×**，默认 **2×**，即时生效并保存，重启恢复。巡航、逃跑上限与加速度一起缩放，临时加速 buff 与之相乘（按基准参数一次算出，不累积）；只改运动参数，**不缩放全局节拍**，饥饿、记忆、食物与飞行节奏不变。升降档保留位置、朝向、速度、钉足与步态相位。
- **鼠标逼近 = 风压**：风改用**闭合速度**（鼠标速度在"鼠标→宠物"方向上的正投影）平方归一后乘距离衰减。鼠标静止或远离时没有风，擦身只留一点弱扰动，直冲越快风压越强；逃跑速度随持续风压上浮（1× 时最高约 1.6 倍），鼠标停下后平滑回落。急停僵住、转身、冲刺、减速警戒的既有节奏与果蝇起飞反应保持不变。
- **高速拖尾**：跑得够快时脚下出现一条 1px 细线，越快越长（每宠 ≤4 段、≤240px，历史 ≤8 点），停下后约 0.15 秒内收回。全程只用 Canvas 矢量线项，10 宠最多约 40 项、**不产生任何位图**；默认开启，设置里可关闭。暂停、抓握、隐藏、移除与退出都会清理。
- **开机自启动**：设置里独立的「开机自启动 / Start with Windows」复选框，默认关闭，只写 `HKEY_CURRENT_USER\...\Run`（stdlib `winreg`，不需要管理员），只删改本程序那一个值。勾选状态**直接读注册表**，不是读配置：写入失败会回滚复选框并给双语错误，不会假装成功。开机启动时直接进入系统托盘（附 `--autostart`），手动双击仍按"启动时打开控制面板"那个开关；自启动路径带单实例守卫，避免登录时复制一份宠物窗口。**便携文件夹移动后需要重新勾选一次**以更新路径。
- **设置窗口全部改为实时保存**：语言、宠物大小、爬行速度、拖尾、开机自启动、启动时打开面板各自独立，改完立刻生效并写盘，去掉了「保存并退出程序」，底部换成「关闭设置」并注明"改动会自动保存"。Esc、右上角 X 和按钮都只关这一个窗口，程序与宠物照常运行；退出仍走系统托盘的「退出」。
- **大小设置现在真的能用**：设置里新增「当前宠物」下拉，切换即改那只宠的大小；开窗时优先关联面板当前选中的宠物，没有选中就自动取第一只可见宠物（不再静默禁用），隐藏宠物也会列出并标注"已隐藏"、配「召回它」按钮。设置里选的宠物同时同步为主面板的选中项，两处始终显示同一只。
- **开窗就显示现值**：语言、当前宠物、实际大小、当前速度在下拉框里明确显示。根因是 `tkinter.Variable` 是 Python 对象，函数返回即被回收，`__del__` 会 `unset` 掉 Tcl 变量，Tk 的变量 trace 随即把下拉框重置为默认（空）——现在所有设置窗口的 Tk 变量由面板强引用持有，随窗口存活。
- **投喂模式下下拉能点**：全局鼠标钩子的穿透区域从"主面板 + 已登记窗口"扩到**本进程全部可见 Tk 顶层窗口**，因此 ttk 下拉的弹出层（独立原生窗，不在窗口登记表里）也被放行；另加一层纯 Win32 的同进程命中判断（`WindowFromPoint` + `GetWindowThreadProcessId`，钩子线程不碰 Tk），刚弹出的下拉第一次点击就生效。透明色键舞台被显式排除，否则铺满全屏的它会让投喂模式整体失效。
- 面板与设置窗口新增文案全部中英双语；设置窗口内容改放进可滚动容器，控件变多或高 DPI 下都不溢出。语言切换只重建控件，不写注册表、不重置速度。

内存与性能口径不变：**60 MiB 是双宠优化目标，不是已实现结果**；现有便携候选双宠 300 秒工作集约 87.18 MiB、Private 61.60 MiB、峰值约 97 MiB，10 宠源码短测约 83 MiB（未加载托盘）。本轮改动沿用这些数据：**没有产生新的测量，也没有跑测试或启动桌宠做视觉验证**。见 [performance.md](performance.md)。

## English

Portable Windows x64 build: extract the ZIP and double-click `NeuroPet-2D.exe`. The whole folder is movable — keep the EXE and `_internal/` together.

This round is about **coherent motion and desktop comfort**: work on the flashing black box and the edge teleporting; an approaching mouse becomes real wind pressure; crawling speed is adjustable; and Windows can start the app for you.

- **Work on the flickering black box** (not verified on a real desktop): changing the window geometry and the canvas origin is now one **atomic transition** — geometry and scroll are applied under a Win32 `WM_SETREDRAW` suppression, painting is restored, and a single complete frame is shown. The goal is to keep the "new geometry + old origin" intermediate frame off the screen, since a colour-key window can be painted with the plain black window-class background during native resize/move and repaint gaps while the key colour is `#010101`. While moving, the window only **grows**; it shrinks after a quiet period, and growth never removes already-visible pixels. Content bounds are recomputed at 5Hz instead of every frame. The scroll fraction conversion is **unchanged this round** and was checked against Tk's `generic/tkCanvas.c`: `TK_SCROLL_MOVETO` computes `newX = scrollX1 - inset + fraction * (scrollX2 - scrollX1)`, so the denominator is the whole scrollregion width and `left / self.w` is the value that puts the visible left edge exactly at `left`.
- **One set of edges**: the app reads the real work area of the current display, so a taskbar at the bottom, top, left or right all work and no taskbar height is hard-coded. The locomotion wall, the drag arbiter, panel food drops and wander targets all share it. Pets slow down before an edge, slide along it keeping their tangential motion, and when one ends up outside the usable area it **walks back at its normal speed** instead of being snapped back in one frame.
- **Global crawling speed**: choose **0.5× / 1× / 2× / 3× / 4× / 6× / 8×** in Settings, **2×** by default, applied immediately, saved and restored on restart. Cruising speed, the escape ceiling and acceleration scale together, and temporary food boosts multiply with it (computed once from the base parameters, never compounded). Only motion parameters change — **the global tick is not scaled** — so hunger, memories, food and flight keep their own pace. Switching a step preserves position, heading, speed, planted feet and gait phase.
- **Approaching mouse = wind pressure**: wind now uses the **closing velocity** (the projection of the cursor's velocity on the cursor→pet direction), squared and normalized, times a distance falloff. A still or receding cursor produces no wind; a near miss leaves only a faint draught; rushing straight in raises the pressure. Escape speed scales with sustained pressure (up to about 1.6× at 1×) and eases back when the cursor stops. The existing freeze → turn → sprint → slow-and-alert rhythm and the fruit fly's takeoff response are unchanged.
- **Speed trails**: past a certain speed a 1px line appears behind a pet, longer the faster it runs (≤4 segments and ≤240px per pet, ≤8 history points), retracting within about 0.15 seconds after it stops. Everything is Canvas vector line items — at most about 40 items for ten pets and **no bitmaps at all**. On by default, switchable in Settings. Pausing, grabbing, hiding, removing and quitting all clear them.
- **Start with Windows**: an independent "Start with Windows" checkbox in Settings, off by default, writing only `HKEY_CURRENT_USER\...\Run` (stdlib `winreg`, no administrator rights) and touching only this program's own value. The checkbox **reads the registry directly** rather than the configuration: a failed write rolls the box back and shows a bilingual error instead of pretending it worked. A sign-in start goes straight to the system tray (with `--autostart`), while double-clicking the EXE still follows the separate "open the panel at startup" switch; the autostart path uses a single-instance guard so a sign-in never duplicates the pet windows. **Re-tick it after moving the portable folder** so the path updates.
- **Settings are now all saved live**: language, pet size, crawling speed, trails, start with Windows and "open the panel at startup" each apply and persist the moment you change them. "Save and quit" is gone; the button is **Close settings** and the bottom says changes are saved automatically. Esc, the window X and that button close only that window — the app and its pets keep running — and quitting stays in the tray's Quit item.
- **Size selection actually works now**: Settings gained a **current pet** dropdown, so changing the dropdown changes *that* pet's size. On open it prefers the pet selected in the main panel, falls back to the first visible pet instead of silently disabling the control, and lists hidden pets too — marked as hidden, with a **Recall it** button. Choosing a pet here also selects it in the main panel, so both places always agree.
- **Current values are visible on open**: language, current pet, actual size and current speed all show their real value in the dropdowns. The root cause of the blank language dropdown was that a `tkinter.Variable` is a Python object: once the builder function returned it was collected, `__del__` unset the Tcl variable, and Tk's variable trace reset the widget to its (empty) default. All settings-window variables are now held by the panel for the window's lifetime.
- **Dropdowns stay clickable in feeding mode**: the global mouse hook's pass-through area grew from "the panel plus registered windows" to **every visible Tk toplevel of this process**, so a ttk dropdown popdown (a separate native window that is not in the window registry) is let through too. A second, purely Win32 same-process hit test (`WindowFromPoint` + `GetWindowThreadProcessId`, no Tk from the hook thread) makes the very first click on a freshly opened dropdown work. The transparent colour-key stage is excluded explicitly — otherwise the full-screen overlay would make feeding mode pass everything through.
- All new panel and settings text is bilingual; the settings window content moved into a scrollable container so it never overflows with the extra controls or at high DPI. Switching language only rebuilds widgets — it writes no registry key and does not reset the speed.

Memory and performance reporting is unchanged: **60 MiB is the two-pet optimization target, not a measured result**. The existing portable candidate, two pets for 300 seconds, ended at about 87.18 MiB working set, 61.60 MiB Private Bytes and a peak of about 97 MiB; the ten-pet source short run was about 83 MiB without the tray. This round reuses those numbers: **no new measurements were produced, no tests were run and the desktop app was not launched for visual verification**. See [performance.md](performance.md).

---

v0.1.1 ZIP SHA256（供核对升级包 / to identify the previous package）：`16D59461FFF93D109EDE21370DBB7179D7A96E8427757BA5CC0D152D61665EB0`

本次 v0.2.0 便携包 / This v0.2.0 portable package：`dist/NeuroPet-2D-v0.2.0-windows-x64.zip`，21,061,829 字节，1,023 个条目（只含 `NeuroPet-2D.exe`、`_internal/`、`licenses/`、`README.txt`、`LICENSE.txt`）。

SHA256：`42E4B2B2A26BFBE73BD63841B9DCF9C1D12139699D74BCF5E5CFEDCEEB5DCEC9`

便携包附项目与依赖许可证、pystray 的 Python 源码轮子。原研究目录未修改，发行包不包含私人存档、日志或 `data/`。

The portable package includes the project and dependency licenses plus pystray's pure-Python source wheel. The original research directory is untouched, and the release package contains no private saves, logs or `data/` folder.