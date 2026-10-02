# NeuroPet 2D Implementation Plan

> 执行方式：executing-plans，由当前代理逐项执行；最后一次独立审查。用户已要求直接做，无重复确认。

**Goal:** 发布支持 10 宠、易用面板和有证据资源数据的 2D 桌宠。
**Architecture:** 现有 App/身体/大脑保持接口；新 compact_panel 只用公开控制 API；可重建图像缓存使用字节预算。
**Tech Stack:** Python 3.13、tkinter、Pillow、pystray、PyInstaller。
**Spec:** docs/design.md

## Global Constraints
- 保留已接受运动；不读写原研究目录存档；默认 max_pets=10；旧值迁移到至少 10。
- 默认双宠 60 MiB 为目标，10 宠独立实测；达标声明需要打包版证据。
- 公开仓库不导出研究 Git 历史和私人资料；用户已授权公开发布。

## Review Focus
- 旧配置 max_pets=3 升级后仍能创建 10 只。
- 空列表、达到容量、隐藏个体召回失败有清楚反馈。
- 列表刷新不改选中对象，10 宠布局不增长到屏幕之外。
- 面板隐藏后不持续刷新，按需窗口不遗留定时器和图像。
- 缓存淘汰释放 PIL 图像且不会改变画面；托盘失败仍有唤出方式。

### Task 1: baseline and reproduction
Files: tools/release_probe.py, tests/test_release_cache.py, PROGRESS.md.
- [ ] 用原渲染器构造 100 张不同 RGBA 图，弱引用首图，执行 alpha 吸附后删除本地引用；确认原缓存滞留。
- [ ] 临时数据目录启动真实 App，分别 2/10 宠测量工作集、Private Bytes 和帧时，默认不得触碰原存档。
- [ ] 保存 baseline JSON 与原运动回归结果。

### Task 2: ten pets
Files: neuropet/core/config.py, neuropet/core/app.py, tests/test_release_capacity.py.
- [ ] 先验证 `load_config()` 对 `{"app":{"max_pets":3}}` 的迁移，以及真实 App 连续创建 10 只。
- [ ] 默认容量改 10，旧配置迁移；保留 RuntimeError/recall 返回值接口。
- [ ] 分散新宠初始位置，检查名册重启数量及同一 pet_id 保真。

### Task 3: bounded image lifetime
Files: neuropet/render/image_cache.py, neuropet/render/renderer.py, neuropet/core/app.py, tests/test_release_cache.py.
- [ ] 以缓存弱引用复现作为 RED；添加按字节限额 LRU，支持 get/set/clear/popitem。
- [ ] alpha/shadow/whole-frame 缓存分别限额；get 刷新 LRU；超大单条不缓存；线程保护。
- [ ] 验证图像释放、实际去重字节统计、输出像素一致；再进行 App 对照，决定旋转桶/内存护栏预算。

### Task 4: compact panel
Files: neuropet/ui/compact_panel.py, neuropet/core/app.py, neuropet/ui/tray.py, tests/test_compact_panel.py.
- [ ] 真实 App+Tk 验证可点按的两个添加按钮，10 个列表行可见/可滚，当前选择刷新不丢。
- [ ] 首页使用 Treeview 固定高度与滚动条，单宠投喂/暂停/隐藏/召回；全部暂停/召回。
- [ ] 记忆/设置窗口首次打开才创建，销毁后再次打开可用；隐藏与最小化不刷新。
- [ ] App 的 selected/refresh_pets/set_feeding/hide/tick 回调保持；托盘与快捷键可唤出。
- [ ] 面板截图视觉检查，低分辨率及 125/150% 缩放核查。

### Task 5: portable distribution
Files: README.md, requirements.txt, requirements-dev.txt, start.bat, build_exe.bat, LICENSE, .github/workflows/windows.yml.
- [ ] 相对目录启动，打包只收运行依赖与 data/gait；不用本机绝对路径。
- [ ] 文档说明直接操作、10 宠、档案、实测口径；Windows CI 冒烟/发行测试。
- [ ] 打包成功后独立目录烟测，不污染研究存档。

### Task 6: measured release and review
Files: docs/performance.md, assets/demos, PROGRESS.md.
- [ ] 正式 2/10 宠资源测量、连续运动短片、真实面板截图。
- [ ] 原运动/交互回归与新发行测试通过；独立审查全部变更及文档。
- [ ] 修复重要审查发现并验证；提交干净发行快照，创建 public 仓库、推送、创建可下载 Release。

Pre-flight: capacity→panel consumes AppConfig.max_pets and App.add_pet; cache→App consumes existing image API; panel→tray shares panel.win/hide. All public signatures preserved.
