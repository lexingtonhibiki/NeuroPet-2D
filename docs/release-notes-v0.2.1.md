# NeuroPet 2D v0.2.1

## 中文

修复最小必要验证中复现的问题：

- 设置回调不再永久停留在初始化状态，语言、大小、速度和开关改动可以真正生效并实时保存；速度说明同步刷新。
- 自启动开关返回实际注册状态，关闭成功不再被当作仍然开启或报错。
- 高速宠物越出透明窗口时立即扩窗，不再等待 0.2 秒的完整包围盒轮询而短暂消失。
- Canvas 世界原点不再因异步缩放时的旧画布尺寸而被夹回 0，避免窗口移动后内容偏移。
- 拖尾从正确端点截断，保持可见长度不超过 240px。

最小检查：设置/运动/拖尾 12 项、真实 Tk 舞台 6 项通过，投喂鼠标钩子放行检查通过。设置使用隔离临时存档；自启动启用/关闭使用内存注册表替身，没有改本机 Run 项。

受控窗口抽帧约 2 秒，55 帧、54 次 resize 更新，捕获的纯黑像素为 0。此结果仅覆盖本次受控场景，不保证所有 Windows 显示环境都不会偶发闪烁。最终便携包两次各 5 秒启动与同 ID 重启检查通过；首轮曾采到一次面板不可见，增加诊断后未重现，原因仍未定位。沿用既有性能数据，没有新增内存达标结论。见[最小验证记录](https://github.com/lexingtonhibiki/NeuroPet-2D/blob/main/docs/minimal-verification-v0.2.1.md)。

## English

Fixes reproduced by the focused verification:

- Settings callbacks no longer remain permanently in initialization mode. Language, size, speed and switches now apply and save immediately; the speed caption updates too.
- The Windows startup switch reports the actual registered state, so successfully disabling it no longer looks like an enabled state or an error.
- A fast-moving pet expands the transparent viewport immediately instead of disappearing until the next 0.2-second bounds poll.
- The canvas world origin is no longer clamped to zero using an outdated size during asynchronous resizing.
- Trails are clipped from the correct endpoint and stay within the 240px visible-length limit.

Focused checks passed: 12 settings/motion/trail cases, six real-Tk stage cases and the feeding-hook pass-through checks. Settings used temporary archives; startup enable/disable used an in-memory registry substitute and did not modify the machine's Run entry.

About two seconds of capture on an owned test backdrop sampled 55 frames during 54 resize updates with zero pure-black pixels. This only covers that controlled scenario, not every intermittent flicker on every Windows display setup. The final portable build passed two five-second launches and same-ID restart checks. An earlier startup sample reported an invisible panel; it did not recur after adding diagnostics, but its cause remains unresolved. Existing performance numbers remain unchanged. See the [verification record](https://github.com/lexingtonhibiki/NeuroPet-2D/blob/main/docs/minimal-verification-v0.2.1.md).

---

Windows x64 ZIP: **21,064,257 bytes** / 字节。

SHA256: `796CF61D4F6B47976C8BF60639EA8FEC430F02762BB447085DCB079F019F302C`
