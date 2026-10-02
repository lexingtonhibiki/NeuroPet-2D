# 性能测量 / Performance measurements

**中文 | English**(本文件为中英对照 / this file is bilingual)

测量机器为 i5-10400、12 逻辑处理器、1920×1080。用户要求停止追加审查与测试，下面保留已完成的候选测量，未进一步扩大验证。默认 1× 大小、交替物种强制行走、鼠标输入隔离。

Measurement machine: i5-10400, 12 logical processors, 1920×1080. Review and testing were stopped by request; the measurements below are the candidates that were already completed and were not extended afterwards. Default 1× size, alternating species forced to walk, mouse input isolated.

## 便携构建候选（实际托盘与生产计时器）

双宠、面板开启、onedir 单进程、实际 TrayIcon 和 App._tick，持续 300 秒：末工作集 **87.18 MiB**，末 Private Bytes **61.60 MiB**，进程峰值 **97.00 MiB**。该测量完成后仅继续收尾调度公平性与发行资料；未追加最终版本长测。

Portable build candidate (real tray and production timers): two pets, panel open, single-process onedir, real TrayIcon and App._tick, 300 seconds — final working set **87.18 MiB**, final Private Bytes **61.60 MiB**, process peak **97.00 MiB**. Only scheduling fairness and release material followed; no long run of the final build was added.

## 源码前后对照（未加载托盘）

以下使用真实 Tk mainloop。采样时长不同，表示已观察到的资源量，不是所有使用状态的上限。

The table below uses a real Tk mainloop. Different sampling durations mean these are observed amounts, not an upper bound for every usage state.

| 场景 / Scenario | 优化前工作集 / 峰值 · Before (working set / peak) | 当前源码短测工作集 / 峰值 · Current source, short run |
|---|---:|---:|
| 双宠 / Two pets | 142.59 / 279.49 MiB | 67.58 / 74.46 MiB（20 秒，中间候选 / 20 s, intermediate candidate） |
| 10 宠 / Ten pets | 145.69 / 280.79 MiB | 82.95 / 89.46 MiB（30 秒，批量合成候选 / 30 s, batch-composition candidate） |

工作集、Private Bytes、进程峰值分别统计；没有调用 EmptyWorkingSet 或定期清空系统工作集。强制行走没有覆盖果蝇持续飞行、全部 2× 放大等更重负载，不能把这些数据当作所有场景的内存上限。

Working set, Private Bytes and process peak are counted separately; neither EmptyWorkingSet nor periodic system working-set trimming was called. Forced walking does not cover heavier loads such as continuous fruit fly flight or all pets at 2×, so these numbers must not be read as a memory ceiling for every scenario.

双宠目标 60 MiB 尚未达成。优化集中于辅助 alpha / 阴影缓存的引用寿命与字节限额，保持身体及步态算法。

The two-pet goal of 60 MiB has not been reached. Optimization focused on reference lifetimes and byte budgets for auxiliary alpha / shadow caches, leaving the body and gait algorithms unchanged.

## 翻译改动与内存口径

v0.1.1 的中英双语只新增一个内置字典模块（无第三方依赖），沿用以上已完成的测量，**没有**产生新的内存数据，也**没有**针对双语改动重新做过测量或测试。数字口径不变。

The v0.1.1 bilingual work adds one built-in dictionary module (no third-party dependency) and reuses the measurements above. It produced **no new memory data**, and neither the measurements nor the tests were re-run for the bilingual changes. The measurement basis is unchanged.

原始数字见 [双宠便携候选](benchmarks/portable-2.json)、[双宠原始基线](benchmarks/baseline-2.json)、[10 宠原始基线](benchmarks/baseline-10.json)、[10 宠批量合成候选](benchmarks/batch-10.json)。10 宠候选是 30 秒源码测量，未加托盘；它不是便携版长测。

Raw numbers: [two-pet portable candidate](benchmarks/portable-2.json), [two-pet original baseline](benchmarks/baseline-2.json), [ten-pet original baseline](benchmarks/baseline-10.json), [ten-pet batch-composition candidate](benchmarks/batch-10.json). The ten-pet candidate is a 30-second source measurement without the tray; it is not a long portable run.