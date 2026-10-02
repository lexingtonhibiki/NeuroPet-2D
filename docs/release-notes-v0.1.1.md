# NeuroPet 2D v0.1.1

## 中文

Windows x64 便携版，解压 ZIP 后双击 `NeuroPet-2D.exe`。整个文件夹可移动，保存 EXE 与 `_internal/` 在一起。

- **界面语言可切换**：控制面板、设置、记忆窗口与系统托盘菜单都提供 **简体中文 / English**，在 **设置 → 语言** 切换，立即生效并保存，无需重启。
- 首次运行没有语言配置时跟随系统语言（中文系统中文，其余英文，检测失败回退中文）；旧配置和宠物存档原样保留，只新增一个 `language` 字段。
- 切换语言不打断当前操作：选中的宠物、编号、已选食物、暂停与隐藏状态、已打开的窗口及其关联宠物都保持原样；用户自定义的宠物名字不翻译。
- 界面文案集中在内置字典 `neuropet/core/i18n.py`（zh-CN / en），**没有新增第三方依赖**。
- 宠物自己写下的历史记忆是存档内容，保持原文，不重写也不做机器翻译。
- 沿用 v0.1.0 的全部行为：桌面最多 10 只，直接添加、投喂、暂停、隐藏 / 召回，全部暂停与滚动列表，记忆 / 设置按需打开；原来的 2D 身体与步态、图像缓存、渲染与调度参数均未改动。

内存与性能口径不变：**60 MiB 是双宠优化目标，不是已实现结果**；现有便携候选双宠 300 秒工作集约 87.18 MiB、Private 61.60 MiB、峰值约 97 MiB，10 宠源码短测约 83 MiB（未加载托盘）。双语改动沿用这些数据，没有产生新的测量，也没有重新跑测试。见 [performance.md](performance.md)。

## English

Portable Windows x64 build: extract the ZIP and double-click `NeuroPet-2D.exe`. The whole folder is movable — keep the EXE and `_internal/` together.

- **Switchable interface language**: the control panel, settings, memories window and system tray menu all ship in **简体中文 / English**. Switch under **Settings → Language**; it applies immediately and is saved, with no restart.
- Without a stored language the app follows the system (Chinese systems get Chinese, everything else English, detection failures fall back to Chinese). Existing configurations and pet archives are kept as they are; only one `language` field is added.
- Switching language never interrupts what you were doing: the selected pet, its number, the chosen food, paused and hidden states, and any window you already opened (including the pet it belongs to) stay exactly as they were. Pet names you typed yourself are never translated.
- All interface text lives in one built-in dictionary, `neuropet/core/i18n.py` (zh-CN / en), with **no new third-party dependency**.
- What a pet wrote down in its memories is archive content: it keeps its original wording, is never rewritten and never machine-translated.
- All v0.1.0 behaviour is unchanged: up to 10 pets on the desk, direct add / feed / pause / hide / recall, pause-all and a scrolling list, memories and settings on demand; the original 2D body and gait, image cache, rendering and scheduling parameters are untouched.

Memory and performance reporting is unchanged: **60 MiB is the two-pet optimization target, not a measured result**. The existing portable candidate, two pets for 300 seconds, ended at about 87.18 MiB working set, 61.60 MiB Private Bytes and a peak of about 97 MiB; the ten-pet source short run was about 83 MiB without the tray. The bilingual work reuses these numbers: no new measurements were produced and no tests were re-run. See [performance.md](performance.md).

---

v0.1.0 ZIP SHA256（供核对升级包 / to identify the previous package）：`BD279BCA0B114802484CD645F99BFA0077E0DBF68D5E0B266BDC728AAFC4B540`

本次 v0.1.1 便携包 / This v0.1.1 portable package：`dist/NeuroPet-2D-v0.1.1-windows-x64.zip`，21,053,399 字节，1,023 个条目（只含 `NeuroPet-2D.exe`、`_internal/`、`licenses/`、`README.txt`、`LICENSE.txt`）。

SHA256：`16D59461FFF93D109EDE21370DBB7179D7A96E8427757BA5CC0D152D61665EB0`

便携包附项目与依赖许可证、pystray 的 Python 源码轮子。原研究目录未修改，发行包不包含私人存档、日志或 `data/`。

The portable package includes the project and dependency licenses plus pystray's pure-Python source wheel. The original research directory is untouched, and the release package contains no private saves, logs or `data/` folder.