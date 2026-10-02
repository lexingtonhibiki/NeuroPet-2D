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

## 剩余工作
- [x] 10 宠、面板、字节缓存、便携启动、依赖与许可、说明和已完成的视觉/资源证据。
- [x] 已公开发布 https://github.com/lexingtonhibiki/NeuroPet-2D ，默认分支 main；v0.1.0 Release 附 Windows x64 便携 ZIP。按用户指令不追加验证。
- [x] 最终打包完成，ZIP 21,065,352 字节，SHA256 BD279BCA0B114802484CD645F99BFA0077E0DBF68D5E0B266BDC728AAFC4B540。ZIP 使用 EXE/_internal/许可/说明文件白名单，不收集运行产生的数据或日志。

## 执行决定
- 用户“直接做”覆盖技能的重复设计/计划确认；本会话直接执行。
- 独立发行目录隔离研究仓库，采用当前工作区代码而非旧 HEAD，以保留已接受的未提交腿部修复。
- 图像缓存、App 生命周期和真实 Tk 控件为验证边界；临时存档隔离，真实用户存档不参与测试。
- 沿用已有行为/姿态契约，生产入口使用新 compact_panel；原面板保留为研究界面及原回归测试对象，默认不导入。
- 执行由当前代理完成；按 executing-plans 技能要求在结束时做一次独立子代理审查。
- 当前计划预计 6 个交付单元、约 35 个操作步骤；实测结果逐单元登记。
