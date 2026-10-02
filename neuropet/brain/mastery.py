"""r25 A4:熟练度(mastery)—— 从行为统计派生 1..5 等级,取代手调「智能等级」滑杆。

**为什么有这个模块**:原「智能等级」是一个把内部长期统计量(记忆容量 / 学习
增益 / 探索-温度)直接暴露成 1..5 的滑杆,玩家在看得见的时间尺度上无法把
它的效果归因到行为(架构 §1.3 取证)。裁决 = 删控件 + 机制改自适应 + 换成
可感知读数(架构 §2 A.5):等级不再由用户拧,而是从**它自己的经历**里长出来。

本模块只做一件事:把「经历统计」映射成 1..5。纯函数、确定性、无随机、
无第三方依赖;不 import 任何 neuropet 模块(避免与 brain/app 形成环)。

**两件必须知道的事(下一个动这份代码的人)**:

1. **等级只上调**(``app._mastery_beat`` 的 ratchet):自动派生值 ≤ 当前等级时
   一律不写 brain。下调会经 ``EpisodicMemory.set_level → trim()`` **永久删除**
   记忆,并形成「等级降 → 容量缩 → 记忆计数降 → 等级再降」的正反馈螺旋。
   要下调只有手动入口 ``app.set_intelligence``(插件/调试 API,权限不变)。
2. **要"冻住"等级用 ``NEUROPET_MASTERY=off``** —— 语义 = **停止自适应**:脑保持
   当前等级,节拍不派生、**不写任何等级**;``cfg.intelligence`` 只作新宠的种子
   (``app.add_pet``)。自适应开着时手动下调 ≤1 个 2s 节拍就被派生值抬回去;
   手动**上调**则是永久下界。

   ⚠ **原语义("回退到 cfg.intelligence":节拍把脑内等级下调对齐回基线)已废弃**
   (r25 A4-r2 主控裁决)。黑盒实测它是**破坏性**的:基线 2 + 档案 L5/220 条
   → 载入后首拍对齐回基线 2 → ``EpisodicMemory.set_level → trim()`` 按 cap(2)=40
   **静默删掉 180 条记忆**。回退开关的存在意义是**安全**:用户为诊断设一个环境
   变量,代价不该是 180 条记忆且毫无提示。故:任何自动路径都不下调等级
   (ratchet 对 on/off **一视同仁**)、不得触发记忆删除;off 只是"别自适应了"。

--------- 阈值表(标定锚点:等级 3 ≈ 熟手 5 分钟)---------

各分量先按参考值饱和到 [0,1](``min(1, x/ref)``),再加权求和得 xp。
每个分量的边际贡献随输入**非增**,故整体对任一输入单调不减。

======================  ==============  ======  ==========================
分量(键)               参考满额(ref)  权重    语义
======================  ==============  ======  ==========================
``age_s``               600 s (10 min)  1.00    活过多久(最慢、最稳的主项)
``n_memory``            12 条           0.60    记得多少件事(联想 + 情景)
``n_fed``               8 次            0.50    被投喂几次(正向互动)
``trust``               1.0             0.30    信任读数的当前值
``n_danger``            6 次            0.10    遇过几次险(经历也算见识,
                                                 权重最低,保证单调不减)
======================  ==============  ======  ==========================
xp 上限 = **2.50**

等级门槛(xp 下界,含;低于 L2 门槛即 1):

======  ======  ====================================================
等级    下界    标定校验
======  ======  ====================================================
1       —      出厂新宠:xp = 0.2(初始信任)×0.3 = **0.06** → 1
2       0.40   养着不怎么互动:5 min 纯年龄 0.50 + 0.06 = **0.56** → 2
3       0.85   **熟手 5 分钟**:年龄 0.50 + 投喂 4 次 0.25 +
               记得 6 件 0.30 + 信任 0.06 = **1.11** → 3(未到 L4)
4       1.40   长期互动:年龄 600s 满 1.00 + 记得 12 件 0.60 +
               投喂 8 次 0.50(已超 L4,未到 L5)
5       2.00   满档:x 2.50 → 5(需持续饲养,非单次操作可达)
======  ======  ====================================================
"""

from __future__ import annotations

import math
import os

#: 回退开关(架构 §2 A.5「风险与回退」):``NEUROPET_MASTERY=off`` →
#: **停止自适应**。``app._mastery_beat`` 不派生、不写任何等级(脑保持当前
#: 等级),``app.mastery_of`` 读数 = 脑内等级(单一真源);``cfg.intelligence``
#: 只作新宠的种子(``app.add_pet``)。
#:
#: **用法(调试/插件常用)**:这是"冻住"等级的唯一入口 —— 档位不再自己往上
#: 走,要改动只有手动 ``app.set_intelligence``。自适应开着时,手动
#: ``app.set_intelligence(pid, N)`` 的下调会在 ≤1 个 2s 节拍内被派生值重新
#: 抬高(``_mastery_beat`` 的 ratchet 只上不下),而上调则是永久下界。
#:
#: ⚠ **原语义("回退到 ``cfg.intelligence``":off 节拍把脑内等级下调回基线)
#: 已废弃** —— 见模块 docstring 第 2 条:下调经 ``trim()`` **静默删记忆**
#: (黑盒实测 220 → 40 条)。记录用，勿照旧实现。
MASTERY_ENV = "NEUROPET_MASTERY"

# ---- 参考满额(饱和点) ----
AGE_REF_S = 600.0      # 年龄记分参考:10 min 满额
MEM_REF = 12.0         # 记忆条数参考
FED_REF = 8.0          # 投喂次数参考
DANGER_REF = 6.0       # 遇险次数参考

# ---- 分量权重(全部 ≥ 0:任一输入单调增 → xp 不减 → 等级不减) ----
W_AGE = 1.00
W_MEM = 0.60
W_FED = 0.50
W_TRUST = 0.30
W_DANGER = 0.10

# ---- 等级门槛(xp 下界,含) ----
L2_AT = 0.40
L3_AT = 0.85
L4_AT = 1.40
L5_AT = 2.00

LEVELS = 5


def mastery_enabled() -> bool:
    """自适应是否开启(默认开;``off``/``0``/``false``/``no`` 关闭)。

    每次调用读环境(不缓存):测试与运行期排查都可用环境变量即时切换。
    """
    return os.environ.get(MASTERY_ENV, "1").strip().lower() \
        not in ("0", "off", "false", "no")


def _num(v) -> float:
    """把 stats 里的任意值安全转成 float(缺键/脏值 → 0.0,不抛)。

    **fail-closed**(r25 A4 黑盒缺陷 C):``NaN`` / ``±inf`` / **负值** /
    非数(``str``(含数字串)/None/容器)一律记 **0.0** —— 统计变脏绝不"白送"
    等级。旧实现 ``float('nan')`` 能过关,而 ``min(1.0, nan) == 1.0`` 把 NaN 当
    **满额**(全 NaN stats → 5 级 / 满容量),与本函数"脏值 → 0"的承诺相反
    (fail-open)。负值本就由 ``_sat`` 兜 0,这里一并堵死;超大整数(float
    溢出)同记 0,不抛。

    **``str`` 一律拒收**(r25 A4-r2 主控裁决;此前 docstring 与实现不符):
    旧实现直接走 ``float(v)``,数字串 ``'7'`` 被**静默采纳**(实测
    ``n_memory='7'`` → 2 级)。数值统计量里出现字符串**是上游 bug 的信号**,
    静默转换会把 bug 藏起来 —— 按 docstring 的契约兑现:``'7'``/``''`` → 0。
    """
    if isinstance(v, str):          # 数字串也不收:上游 bug 的信号,不静默转
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(x) or x < 0.0:
        return 0.0
    return x


def _sat(x: float, ref: float) -> float:
    """饱和度:0 以下记 0,ref 以上记 1,中间线性(边际贡献非增)。"""
    if x <= 0.0:
        return 0.0
    return min(1.0, x / ref) if ref > 0 else 0.0


def mastery_level(stats: dict) -> int:
    """熟练度等级:输入经历统计,输出 **1..5** 的整数。

    参数 ``stats``(缺键按 0 处理,脏值不抛):
      - ``n_memory``(int):``len(app.memory_of(pid))``
      - ``trust``(float):``brain._trust``(两物种均有)
      - ``age_s``(float):``state.as_summary()["age_s"]``——**随脑存档落盘**
        (``roach_brain``/``fly_brain`` 的 save/load),重启不归零。这不是可选
        项:年龄归零会让重启后等级必降 ≥1 档,而 ``EpisodicMemory.set_level``
        → ``trim()`` 会按缩小后的容量**永久删除**记忆(r25 A4 黑盒缺陷 A:
        实测 220→140 条,静默且不可逆)。所以 ``app._mastery_beat`` 的自动
        派生**只上调、不下调**(ratchet),下调只留手动入口。
      - ``n_fed``(int):投喂事件计数(``brain.on_event("fed")``)
      - ``n_danger``(int):危险事件计数(``brain.on_event("grab")``)

    性质(判据在 ``tests/test_panel_ia.py`` ⑪):
      - **单调不减**:任一输入单调增 → 输出不减(所有分量权重 ≥ 0 且饱和,
        故 xp 对每个输入非减;门槛比较用 ``>=`` 无随机);
      - **值域**:恒 ⊆ [1, 5](xp 上限 2.50,不足 L2 门槛即 1);
      - **确定性**:相同输入 → 相同输出(纯算术,无 RNG、无时间、无环境)。

    等级门槛与标定见模块 docstring 的阈值表(等级 3 ≈ 熟手 5 分钟)。
    """
    xp = (W_AGE * _sat(_num(stats.get("age_s")), AGE_REF_S)
          + W_MEM * _sat(_num(stats.get("n_memory")), MEM_REF)
          + W_FED * _sat(_num(stats.get("n_fed")), FED_REF)
          + W_TRUST * _sat(_num(stats.get("trust")), 1.0)
          + W_DANGER * _sat(_num(stats.get("n_danger")), DANGER_REF))
    if xp >= L5_AT:
        return 5
    if xp >= L4_AT:
        return 4
    if xp >= L3_AT:
        return 3
    if xp >= L2_AT:
        return 2
    return 1


def clamp_level(level) -> int:
    """把任意等级读数值钳到 1..5(回退路径与外部入参共用)。"""
    try:
        v = int(round(float(level)))
    except (TypeError, ValueError):
        return 1
    return max(1, min(LEVELS, v))


def learned_from_summary(summary: str) -> str:
    """从 ``brain.learning_summary()`` 文本里抽出「偏好 X」片段。

    learning_summary 两物种同构(roach_brain.py:1044 / fly_brain.py:1470):
    ``... | 偏好 觅食趋味 μ=0.76(n=3)`` → 返回 ``觅食趋味``;取不到则 ``"-"``。
    纯字符串处理:格式漂移时优雅降级,不抛。
    """
    txt = str(summary or "")
    i = txt.find("偏好 ")
    if i < 0:
        return "-"
    seg = txt[i + len("偏好 "):]
    for sep in (" μ=", " |", "(", "\n"):
        j = seg.find(sep)
        if j >= 0:
            seg = seg[:j]
    seg = seg.strip()
    return seg or "-"


def readout(level: int, n_memory: int, learned: str = "-") -> str:
    """「看它」页的熟练度读数行(纯函数,便于判据直接读文本)。

    形如 ``熟练度 3/5 · 记得 12 件事 · 最近学会:偏好 觅食``(架构 §2 A.5 第 4 步)。
    """
    try:
        n = int(n_memory)
    except (TypeError, ValueError):
        n = 0
    return (f"熟练度 {clamp_level(level)}/{LEVELS} · 记得 {n} 件事 · "
            f"最近学会:{learned or '-'}")
