"""提示文案与 tooltip 内容源(第十轮转型,原"气泡引导层"C2)。

用户裁决(返工根因 #2):引导信息**不画在宠物头顶**,一律进控制面板——
宠物列表每行的 "i" 图标悬停弹出解释 tooltip。故本模块职责转型:

- 保留/改造 BUBBLE_TEXTS:不再是舞台气泡文案,而是**提示文案源**
  (fed/transform/trust_up 等键暂留,供 app 旧接线过渡;新增 "guide"
  键 = 面板 tooltip 的引导文案池);
- 新增 tooltip 内容 API:`tooltip_text(状态dict) -> 多行文案`,
  含情绪四维(愉悦/恐惧/好奇/信任)+能量+当前行为+一条引导文案;
  `guide_text()` 单取引导文案(食物选择器等处复用);
- 舞台绘制机制(圆角矩形/Bayer 淡出/位置跟随)已整体删除;
  `BubbleLayer` 降级为静默兼容壳(见类注释),app.py 旧接线由主 agent
  移除后可整体删除该类与 `bubble_anchor`。

色键约束因此不再适用:tooltip 在普通 Tk 窗口(控制面板)内,不进色键
舞台,可用真彩色深底浅字,无 stipple 需求。模块保持纯文案+纯函数,
离线测试无需 tk(test_bubbles 惯例)。
"""
from __future__ import annotations

import random

# ===================== 文案表(集中此处,便于修改) =====================
BUBBLE_TEXTS: dict = {
    # 面板引导文案池("i" tooltip / 食物选择器悬停提示随机取一条;无占位符)
    "guide": [
        "定时投喂,它会更亲人;不同食物带来不同变身效果",
        "投喂前先在「宠物」页选好食物,再点「投喂」放到桌面",
        "它饥饿(饱食度 < 45%)时会循气味主动找食物",
        "抓起它会降低信任;放下后别马上追它,等它冷静",
        "智能等级 1-5 越高,它探索与解决问题的本事越大",
        "隐藏再召回,情绪、记忆与学会的本领都原样保留",
        "先讲「雪景很美」再放冷区,它会主动趋近寒冷",
        "信任足够高后,它会主动来靠近你",
    ],
    # 首次投喂(教学:每会话一次,kind=milestone 不受冷却;app 过渡期仍用)
    "fed_first": ["它记住了{label}的味道(信任↑)。定时喂,它会更亲人"],
    # 日常投喂趣味文案(随机抽一条,kind=hint 受全局冷却;app 过渡期仍用)
    "fed": ["嗯,{label},喜欢!", "{label}的味道…记住了", "谢谢投喂,{label}不错",
            "{label}!今天也按时开饭", "嚼嚼嚼…{label}真香"],
    # 变身解锁(与 brain 情景记忆同帧;app 过渡期仍用)
    "transform": ["第一次吃到{label},身体起了变化!", "好像…变身了?({label}的力量)"],
    # 信任档位升级(下标 = 新档位 1..3:警惕/习惯/主动靠近)
    "trust_up": {1: ["它不再那么怕你了"],
                 2: ["它开始习惯你了"],
                 3: ["它会主动来找你了"]},
    # 首次躲藏(ADR-0029 状态机首次进入 HIDE;app 过渡期仍用)

    # 首次被抓起(app 过渡期仍用)
    "grab_first": ["它被抓起来了(信任↓)。放下后别马上追它"],
}

# ===================== tooltip 内容 API(面板 "i" 图标用) =====================
# 情绪四维:(状态 dict 键, 中文名);顺序即 tooltip 呈现顺序
TOOLTIP_EMOTIONS: tuple[tuple[str, str], ...] = (
    ("valence", "愉悦"), ("fear", "恐惧"),
    ("curiosity", "好奇"), ("trust", "信任"),
)

# 行为值中文映射(与 panel.BEHAVIOR_ZH 同口径的本地副本,避免循环 import;
# 未知值回退原文)
_ACT_ZH: dict[str, str] = {
    "idle": "待机", "explore": "探索", "groom": "理毛", "seek_food": "觅食",
    "eat": "进食", "escape": "逃逸", "rest": "休息",
    "turn": "转向",
    "takeoff": "起飞", "fly_wander": "飞行", "land": "降落", "glide": "滑翔",
}


def guide_text(rng: random.Random | None = None) -> str:
    """随机取一条引导文案(注入 rng 即确定性可测;池=BUBBLE_TEXTS["guide"])。"""
    rng = rng or random
    return rng.choice(BUBBLE_TEXTS["guide"])


def _fmt_dim(v) -> str:
    """情绪维度数值格式:带符号两位小数(文案里必须看得出数值本身)。"""
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return "+0.00"


def tooltip_text(status: dict, rng: random.Random | None = None) -> str:
    """宠物状态 dict(app.pet_status 口径)→ 面板 "i" 悬停解释文案。

    结构(空 dict/缺字段逐项兜底,永不抛异常、永不返回空串):
      情绪  愉悦 +0.12  恐惧 +0.03  好奇 +0.55  信任 +0.10
      能量(饱食度)62%  当前行为:探索
      状态标记:…(冻结/被抓着,正常时省略)
      提示:<BUBBLE_TEXTS["guide"] 一条引导文案>
    """
    status = status or {}
    emo = status.get("emotion") or {}
    dims = "  ".join(f"{zh} {_fmt_dim(emo.get(key, 0.0))}"
                     for key, zh in TOOLTIP_EMOTIONS)
    try:
        energy = max(0.0, min(1.0, float(status.get("stomach", 0.0))))
    except (TypeError, ValueError):
        energy = 0.0
    raw_act = str(status.get("activity", "") or "")
    act = _ACT_ZH.get(raw_act, raw_act or "未知")
    lines = [f"情绪  {dims}",
             f"能量(饱食度){energy:.0%}   当前行为:{act}"]
    flags = []
    if status.get("frozen"):
        flags.append("冻结")
    if status.get("held"):
        flags.append("被抓着")
    if flags:
        lines.append("状态标记:" + "、".join(flags))
    lines.append("提示:" + guide_text(rng))
    return "\n".join(lines)


# ===================== 旧接线过渡区(主 agent 移除接线后删除) =====================
COOLDOWN_S = 8.0               # hint 类提示全局冷却(app 过渡期仍引用)
ANCHOR_DY = 18                 # 旧气泡锚点偏移(bubble_anchor 纯函数保留)


def bubble_anchor(pos: tuple[float, float], half: float,
                  dy: float = ANCHOR_DY) -> tuple[float, float]:
    """【已退役】旧舞台气泡锚点纯函数(app.py 过渡期仍 import)。"""
    return (float(pos[0]), float(pos[1]) - float(half) - float(dy))


def pick_text(key: str, rng: random.Random | None = None, **fmt) -> str:
    """从文案表取文案:列表则随机抽一条(可注入 rng),再 format。"""
    rng = rng or random
    tpl = rng.choice(BUBBLE_TEXTS[key])
    return tpl.format(**fmt)


def tier_text(tier: int, rng: random.Random | None = None) -> str:
    """信任档位升级文案(档位 1..3;档位键查表)。"""
    rng = rng or random
    return rng.choice(BUBBLE_TEXTS["trust_up"][int(tier)])


class BubbleLayer:
    """【已退役】舞台气泡层兼容壳(第十轮转型:引导气泡迁入控制面板)。

    仅保留 app.py 旧接线的调用面(show/close/clear/update/move_to/
    set_hires/set_enabled/current),全部静默 no-op:`current` 恒为
    None = 舞台上永不出现气泡项,show 恒返回 False。主 agent 移除
    app.py 接线后,本类与 bubble_anchor 可整体删除。
    """

    def __init__(self, canvas=None, bitmap_factory=None) -> None:
        self.canvas = canvas
        self.enabled = True
        self._hires = False

    @property
    def current(self):
        return None                # 舞台气泡已退役:永远没有可见气泡

    def show(self, *args, **kwargs) -> bool:
        return False               # 不再画任何东西

    def move_to(self, *args, **kwargs) -> None:
        return None

    def update(self, now: float = 0.0) -> bool:
        return False

    def set_hires(self, on: bool) -> None:
        self._hires = bool(on)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)

    def close(self) -> None:
        return None

    clear = close                  # clear 别名(语义:清空气泡层)
