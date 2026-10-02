# 目标
交付并公开发布低开销的 Windows 2D 蟑螂/果蝇桌宠，支持创建 10 只宠物，重做易用面板，保持已接受的运动。默认 1×双宠常驻工作集目标 60 MiB，10 宠独立报告。

## 已验证进度
- [x] 原始代码/演示按字节复制到独立发行目录；原仓库没有修改。Git 初始化为 codex/2d-release。
- [x] GitHub CLI 已登录 lexingtonhibiki；NeuroPet-2D 仓库名尚未占用。
- [x] 原运动 smoke：`python tests/smoke.py` → ALL OK，证据 logs/baseline-smoke.txt。
- [x] 缓存滞留 RED：`python tests/test_release_cache.py` 首图弱引用仍存活，退出 1，logs/cache-red.txt。
- [x] 原 2/10 宠真实 mainloop 各 20 秒对照：logs/baseline-2.json、baseline-10.json。双宠末工作集 142.6 MiB，图像缓存去重 80.72 MiB，其中 snap 54.318；10 宠 loop 40.32 Hz，sprite 8.07 次/宠/秒。
- [x] 容量 RED：旧配置迁移和真实创建 10 宠两项均失败于旧上限 3，logs/capacity-red.txt。
- [x] 容量 GREEN：`python tests/test_release_capacity.py` → 2 passed；10 宠、分散位置、同 id 重启和记忆保真通过。
- [x] 缓存 GREEN：首图弱引用释放；`test_body.py` 11/11、`test_turn_continuity.py` 4/4；相同 probe 双宠 20 秒末工作集 77.18 MiB、图像缓存 15.71 MiB、峰值 78.28（原峰值 279.49）。仍未达到工作集 60 目标。
- [x] 原投喂鼠标钩子 `test_hook_passthrough.py` → ALL OK。

## 进行中
- [x] 新面板真实控件测试 4 passed；使用与生产一致的单 Tk 解释器消除重复解释器初始化错误。
- [x] 面板裁切修复：380×620，固定操作区与可缩放滚动列表；真实截图所有按钮边界通过，记忆/设置截图已检查。
- [x] 显示运输：RGBA 透明边裁切保留原像素坐标；单宠位置移动复用 PhotoImage；重叠姿态每帧合成一次；窗口按内容范围分配。真实 Tk merge/split/viewport/batch 检查通过。
- [x] 预热线程持续读取当前宠物名册，新增/隐藏/移除宠不再依赖启动快照。多蟑螂场景限制后台阴影预热。
- [x] 全套第一轮 38/46，第二轮 44/46：补齐固定历史外观基线与训练工具，修正裁切后像素检查坐标，历史面板 fixture 显式维持其 3 宠情景。最后两项单跑均通过，待最终全跑。
- [x] 构建便携 EXE 成功；打包性能与默认启动烟测待执行。
- [x] 曾比较 >2 宠 2× 抗锯齿，改善很小，已撤回，保留已接受画质。
- [x] 47/47 全套通过（logs/full-suite-final-2.txt）；高负载时基 RED 为重复推进 0.0467/0.05s，GREEN 正确推进每帧 0.03s，原运动夹具保持明确的固定 60Hz。
- [x] 125/150% 缩放下六项真实面板检查通过；窗口与字体按可用屏幕高度适配。
- [x] 内存采样结构发现遗漏 PeakWorkingSetSize，旧护栏把峰值当当前值。32MiB VirtualAlloc/Free 复现 RED，结构修复 GREEN；工具 probe 的独立采样从一开始就是正确的。
- [x] 10 秒 / 15fps 的 10 宠自有像素连续动画，已查看中间帧；录制缓存不属于运行内存测量。
- [x] onedir 单进程便携候选构建、正常启动、托盘、渲染及同 id 重启检查通过。包附 MIT 和依赖许可、pystray 纯 Python 源码轮子，构建命令自动装配这些文件。
- [x] 已结束的便携候选双宠 300 秒：实际托盘/生产 App._tick，工作集 87.18 MiB，Private 61.60 MiB，峰值 97.00 MiB。60 MiB 目标仍未达成。
- [x] 独立审查发现帧时截止下固定游标 +4 会令一半宠物永久不重绘。RED 两项复现，改为最后实际服务位置后继续，GREEN 2/2，原 render_gate 5/5。
- [x] 用户明确要求停止审查测试；不再追加测试、审查或测量。CI 改为手动构建且不自动运行测试。仅收尾打包和授权的公开发布。
- [x] v0.1.1 中英双语实现（分支 codex/bilingual）：新增内置翻译字典 neuropet/core/i18n.py（zh-CN / en，无第三方依赖）；AppConfig 增加 language 字段，旧配置缺失时按系统语言判定（中文系统中文，其余英文，检测失败回退中文），宠物存档与旧配置原样保留。
- [x] 生产 compact_panel 全量文案本地化（按钮、列头、数量、物种、活动、食物、详情、设置项、空状态、确认与错误对话框、记忆窗口），新增设置窗口顶部「语言 / Language」入口，切换立即生效并保存，无需重启。
- [x] 语言切换为就地刷新：面板、已打开的设置/记忆窗口与系统托盘菜单同步更新，不重建窗口、不重启托盘线程；选中宠物、编号、已选食物（FoodKind 持久标识不变）、暂停与隐藏状态、打开窗口关联的宠物全部保留；用户自定义名字不翻译。
- [x] 托盘菜单与悬停标题同步刷新（菜单文案为 callable + pystray 公共 title setter），投喂勾选态行为不变。
- [x] 记忆正文保持历史原文，不重写也不机器翻译；该边界已写入中英文 README 与 v0.1.1 发布说明。旧研究面板 panel.py 未改动。
- [x] 文档：README.md 增加语言入口并说明语言切换与记忆原文边界；新增 README.en.md 覆盖下载、便携使用、10 宠、面板/托盘/快捷键、语言切换、存档位置与退出方式；docs/performance.md 改为中英对照单文件（60 MiB 为目标非结果、便携候选双宠 87.18 MiB / Private 61.60 MiB / 峰值约 97 MiB 口径不变，并写明双语改动未产生新测量）；新增 docs/release-notes-v0.1.1.md 双语发布说明；tools/collect_licenses.py 生成的便携 README.txt 改为中英双语快速入门并含语言切换说明，许可正文保持原文。
- [x] 本次未运行任何测试、审查代理、probe、smoke 或内存采样（用户明确禁止）；双语改动没有产生新的测量数据。

- [x] v0.2.0 连贯运动与桌面体验（分支 codex/motion-comfort，基线 8cc870b）：新增 `neuropet/core/desktop.py` 作为可用桌面唯一事实源（Win32 `MonitorFromPoint` + `GetMonitorInfo` 的 `rcWork`，非 Windows 回退整屏，2s 节流刷新），身体软墙/拖拽仲裁/面板落食/随机航点全部改走它；新增 `neuropet/core/autostart.py`（stdlib `winreg` 写 HKCU Run）与 `neuropet/render/trail.py`（Canvas 矢量拖尾）。
- [x] 黑框闪烁：`fit_viewport` 重写为「内容包围盒 5Hz 节流 + 运动中只扩不缩 + 静止收边」；滚动原点改用 `left / (scrollregion 宽 − 画布宽)` 反解（旧码少乘「画布宽/屏宽」因子，窗口位置与可见左缘恒不同步）；几何与滚动包在 `_RedrawGuard`（Win32 `WM_SETREDRAW` + `RedrawWindow`，finally 恢复）里一次做完，中间态不上屏。
- [x] 边缘瞬移：删掉 body 的整屏 80px 软墙与 world 的整屏 60px 钳位，统一为「可用桌面 ∩ margin_for(window_half)」（下限 80 保原活动范围）；撞墙改为逐轴连续截断 + 贴边滑行；区外不再单帧夹回，而是压速朝区内最近点转向；`_steer_toward` 增加朝边界的提前减速；取食半径随体型走，避免边缘食物够不到。
- [x] 全局爬行速度 0.5×~8×（默认 2×）：`body.set_speed_multiplier` 按 `_p_base` 一次算出 cruise/sprint/fly_speed/accel/escape_sprint_cap 与飞行逃逸上限，不累积乘法、不重建 body（保留 `_speed`、步态相位、钉足）；`ABS_SPEED_MAX=5200` 兜底 dt 抖动；不缩放全局 dt。配置新增 `crawl_speed` / `trail_enabled`。
- [x] 鼠标闭合风压：`perception/mouse.py` 新增 `closing_pressure` / `shear_pressure` / `wind_pressure` 三个纯函数（正投影平方归一 × 距离衰减开根），静止/远离为 0、擦身保留弱剪切；`body._apply_escape` 用同一函数驱动逃速（1× 时最高 ×1.6，风压 0 时逐位等于改动前）。头部无循环导入（函数内 import）。
- [x] 高速拖尾：阈值 `max(260, 3.5×window_half)` px/s，历史 deque ≤8 点、每宠 ≤4 段、≤240px，停下 0.15s 内收回，项池只增不减（3000 帧仍只创建 4 项），隐藏/移除/退出清理，`remove_pet` 联动；默认开启、设置可关。
- [x] 开机自启动：设置里独立复选框，勾选态只读注册表（`enabled()` 比对当前命令行，移动文件夹后自动显示未勾选），写失败回滚复选框 + 双语错误；`--autostart` 启动落托盘，手动启动仍按 `cfg.panel_visible`；仅自启动路径走命名互斥体单实例守卫。
- [x] 面板/设置窗口：新增速度下拉（7 档）、拖尾开关、开机自启动，文案全双语；内容改放进可滚动容器（`_scrollable`），高度 430→580，控件增多或高 DPI 都不溢出；语言刷新只重建控件，不写注册表、不重置速度。
- [x] 文档：README.md / README.en.md 增补爬行速度与鼠标反应、边界与开机自启动两节；新增 `docs/release-notes-v0.2.0.md` 双语发布说明；`tools/collect_licenses.py` 生成的便携 README.txt 增补速度/拖尾/自启动说明；新增 `tools/make_portable_zip.py`（白名单 + 固定排序的可复现打包脚本）。
- [x] 本轮**没有**运行测试、审查代理、probe、smoke、桌宠启动、性能采样或长跑（用户明确禁止）；只做了纯逻辑的静态核对（滚动换算、风压符号、倍率幂等、边界截断、拖尾项数上限，均无 GUI、无文件写入），**没有新的性能或视觉数据**。

- [x] v0.2.0 追加要求（docs/settings-live-save-addendum.md）：设置窗口改为全项实时保存（语言/大小/速度/拖尾/开机自启动/启动面板各自独立），去掉「保存并退出程序」，底部换「关闭设置」并注明改动自动保存；Esc、X、按钮都只销毁设置窗口，不动 app 与主面板，退出仍走托盘「退出」。
- [x] 设置窗口的 Tk 变量根因修复：`tkinter.Variable` 是 Python 对象，建构函数返回即被回收 → `__del__` → `unset` Tcl 变量 → Tk 的变量 trace 把 combobox 重置为默认（空），这就是"语言下拉不显示默认项"的真正原因。改为统一由 `self._settings_vars` 强引用持有，`_combo()` 额外显式 `var.set()` + `current(index)` 对齐现值；`_settings_release()` 在重建与关闭时释放。
- [x] 大小设置可用化：新增「当前宠物」下拉（可见宠在前，隐藏宠标注"已隐藏"），目标解析顺序 = 面板选中 → 上次设置里选的 → 第一只可见 → 第一只隐藏；开窗不再静默禁用，隐藏宠附「召回它」按钮（`app.recall_pet`）；设置里选的宠物同步 `tree.selection_set` 到主面板，两处一致；重建用 `after_idle` 延迟，不在自己的事件回调里销毁自己。
- [x] 投喂模式下的下拉点击：`_refresh_panel_rect` 的穿透区域扩到本进程全部可见 Tk 顶层窗口（`winfo children .`，主线程，排除透明舞台）；另加 `_own_ui_window_at()` 纯 Win32 同进程命中判断（`WindowFromPoint` + `GetWindowThreadProcessId`，钩子线程不碰 Tk，无缓存延迟）。`_panel_contains` 改为两层判据。
- [x] 实时保存的失败回退：`_guard()` 统一 try/except → `diag.log_exc` + 双语 `error.save` 提示 + 回退显示值；开机自启动失败另有 `error.autostart` 并回滚复选框到注册表真实状态。程序填值不触发写盘：`<<ComboboxSelected>>` 只由用户操作产生（已核对 Tk 8.6.15 `ttk::combobox::SelectEntry` 才会 generate），另加 `_settings_loading` 闸门。
- [x] 新增双语文案：`settings.pet_label` / `settings.pet_recall` / `settings.size_hidden` / `settings.close` / `settings.autosave` / `error.save`，并移除 `settings.quit`。
- [x] 追加项的静态核对：宠物目标解析/标签往返/过期 pid 回退在两种语言下用桩对象验证通过；下拉弹层确实是独立 Tk 顶层（`ttk::combobox::PopdownToplevel` → `toplevel $w`），会出现在 `winfo children .` 里；`<<ComboboxSelected>>` 的生成点已逐行确认。**未启动桌宠、未跑测试、未做真实点击验证。**

## 剩余工作
- [x] 10 宠、面板、字节缓存、便携启动、依赖与许可、说明和已完成的视觉/资源证据。
- [x] 已公开发布 https://github.com/lexingtonhibiki/NeuroPet-2D ，默认分支 main；v0.1.0 Release 附 Windows x64 便携 ZIP。按用户指令不追加验证。
- [x] 最终打包完成，ZIP 21,065,352 字节，SHA256 BD279BCA0B114802484CD645F99BFA0077E0DBF68D5E0B266BDC728AAFC4B540。ZIP 使用 EXE/_internal/许可/说明文件白名单，不收集运行产生的数据或日志。
- [x] v0.1.1 便携构建一次完成：`D:\DevTools\IDEs\Python\Python313\python.exe tools\build_release.py`（PyInstaller onedir + collect_licenses）成功，`dist/NeuroPet-2D/` 含 EXE、_internal、licenses、README.txt、LICENSE.txt，无 data/logs。ZIP `dist/NeuroPet-2D-v0.1.1-windows-x64.zip`，21,053,399 字节，1,023 条目，SHA256 16D59461FFF93D109EDE21370DBB7179D7A96E8427757BA5CC0D152D61665EB0。构建后只核对了静态产物（Analysis-00.toc 含 neuropet.core.i18n、包内 README.txt 双语内容、ZIP 白名单条目），未启动 EXE、未跑 probe/smoke。公开发布与 push 由 Codex 协调。

- v0.2.0 计划（docs/motion-comfort-plan.md）由用户授权直接执行：实现、双语文档、本地提交与一次便携构建由当前会话完成；不派审查代理、不跑测试、不 push、不发布 Release、不改本机自启动设置。
- 实现期发现的真实缺陷（非候选）：`xview_moveto` 的分数换算少乘「画布宽/屏宽」因子；闭合速度正投影方向写反（`c - p` 应为 `p - c`）；`escape_sprint_cap` 对不飞物种不在参数里，8× 时 `min(sprint,cap)` 会把倍率吃掉；`StateArbiter` 恒用默认留白钳位会与身体留白互相拉扯。三处都已按代码事实修正并留注释。
- 留白口径最终定为 `max(80, window_half × 0.7)`：下限保 v0.1.x 的活动范围，体型放大时按比例放开；取食半径随之按体型放宽，避免边缘食物不可达。

- [x] v0.2.0 便携构建一次完成：`D:/DevTools/IDEs/Python/Python313/python.exe tools/build_release.py`（PyInstaller onedir + collect_licenses）。首次尝试因上一轮遗留的 `dist/NeuroPet-2D/NeuroPet-2D.exe`（PID 21796，19:51 启动）锁住 `_internal` 而失败，结束该遗留进程后一次成功；`dist/NeuroPet-2D/` 只含 EXE、_internal、licenses、README.txt、LICENSE.txt，无 data/logs。
- [x] ZIP `dist/NeuroPet-2D-v0.2.0-windows-x64.zip`，21,061,829 字节，1,023 条目（与 v0.1.1 同数），SHA256 42E4B2B2A26BFBE73BD63841B9DCF9C1D12139699D74BCF5E5CFEDCEEB5DCEC9。白名单只含 EXE/_internal/许可/说明文件；`_internal/data/gait/*.json` 是**构建输入的步态参数表**（v0.1.0/v0.1.1 同样内含），不含 config.json / pets.json / session.json / profiles/ / logs/。打包脚本 `tools/make_portable_zip.py` 按顶层白名单枚举并固定排序。
- [x] 构建后只核对静态产物：Analysis-00.toc 含 `neuropet.core.desktop` / `neuropet.core.autostart` / `neuropet.render.trail` / `neuropet.core.i18n`，包内 README.txt 含新增的中英文速度/自启动说明，ZIP 顶层清单无 data/logs。**未启动 EXE、未跑 probe/smoke、未做视觉或交互验证。**

## 执行决定
- 用户“直接做”覆盖技能的重复设计/计划确认；本会话直接执行。
- 独立发行目录隔离研究仓库，采用当前工作区代码而非旧 HEAD，以保留已接受的未提交腿部修复。
- 图像缓存、App 生命周期和真实 Tk 控件为验证边界；临时存档隔离，真实用户存档不参与测试。
- 沿用已有行为/姿态契约，生产入口使用新 compact_panel；原面板保留为研究界面及原回归测试对象，默认不导入。
- 执行由当前代理完成；按 executing-plans 技能要求在结束时做一次独立子代理审查。
- 当前计划预计 6 个交付单元、约 35 个操作步骤；实测结果逐单元登记。
- v0.1.1 双语计划（docs/bilingual-plan.md）由用户授权直接执行：实现、文档、本地提交与一次便携构建由当前会话完成；不派审查代理、不跑测试、不 push、不发布 Release、不改系统配置。
