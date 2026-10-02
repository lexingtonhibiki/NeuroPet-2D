# v0.2.1 最小必要验证

用户于 2026-10-02 允许最小必要验证。本轮没有派审查代理、没有全量测试或长时间资源压测，没有修改本机自启动项；设置/启动数据全部隔离。

## 复现与修复

| 实际问题 | 复现信号 | 修复 |
| --- | --- | --- |
| 所有设置回调被忽略 | 真 Tk 下拉事件后语言仍 zh-CN、大小仍 1× | 回调读取当前 `_settings_loading`，不捕获构建时永久为 true 的局部值 |
| 关闭自启动错误地报告开启 | 内存 winreg 删除成功，`App.set_autostart(False)` 却返回 true | 将操作成功与实际注册状态分开，UI 返回重新读取的注册状态 |
| 高速宠物越出窗口后消失 | 未满轮询间隔时宠物矩形右缘 1140、窗口右缘仍 1100 | 每帧廉价检查缓存宠物矩形；越界立即扩窗，完整 Canvas 包围盒仍节流 |
| 缩放后世界原点跳到 0 | 连续缩放序列中窗口 x=900，canvasx(0)=0 | Canvas 使用 `confine=False`，避免异步 Configure 期间按旧尺寸钳制世界原点 |
| 可见拖尾超出长度上限 | 高速轨迹在截断处从旧端点插值，超过 240px | 从较新端点向历史方向截断，验证只累计实际可见线项 |

## 最终检查

各 Tk 测试文件在独立 Python 进程运行，避免反复创建/销毁 Tcl 解释器的已知夹具问题；屏幕取帧与其它 GUI 检查顺序执行，以免两个测试窗口互相遮挡。

```powershell
python -m pytest tests/test_v020_minimal.py -q --tb=short
# 12 passed：语言默认项/GC 后可见值、语言实时落盘、大小/速度改动与重开值、
# 真实 Combobox 弹层投喂放行、开关保存、关闭仅关设置、隔离自启动开关、
# 四边任务栏位移连续/区外不单帧夹回、逼近风压与逃速、拖尾长度/项数/清理。

python -m pytest tests/test_release_stage.py -q --tb=short
# 6 passed：图片复用、重叠合成与分离、视口坐标、奇数尺寸裁剪、
# 高速越界立即扩窗、原生 resize 受控屏幕抽帧。

python tests/test_hook_passthrough.py
# ALL OK：投喂钩子放行边界及异常处理。

python tools/smoke_portable.py
# PASS：最终 EXE 两次各5秒启动；2只宠物/容量10，tray=true，panel=1；
# uploads=248/235，同ID重启和两份档案保持。
```

实际解释器：`D:\DevTools\IDEs\Python\Python313\python.exe`。

受控绘制取帧仅截取本测试的纯色背景窗口，约 2 秒：55 帧、54 次 resize 更新、最大纯黑像素 0（本机记录 `logs/viewport-paint-check.txt`）。这不是完整桌面或持续使用的无闪烁保证。

首轮便携烟测曾返回 `panel=0`（tray=true、uploads=228）；给烟测增加 panel_state/config_visible/startup_tray_only 诊断后重建，最终两个短启动均返回 normal/true/false 且 panel=1。首轮面板不可见的原因未定位，保留为偶发风险，不宣称后续通过已排除该现象。

自启动真实 HKCU Run 写入/登录启动未执行，启用/关闭与路径引号通过内存注册表替身验证。没有新增内存测量，60MiB 仍只是目标。

## 产物

`dist/NeuroPet-2D-v0.2.1-windows-x64.zip`：21,064,257 字节、1,023 条目。

SHA256：`796CF61D4F6B47976C8BF60639EA8FEC430F02762BB447085DCB079F019F302C`

ZIP 顶层白名单保持 EXE、_internal、licenses、README.txt、LICENSE.txt，不含运行存档或日志。
