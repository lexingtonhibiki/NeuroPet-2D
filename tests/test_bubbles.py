# -*- coding: utf-8 -*-
"""提示文案与 tooltip 内容源测试(第十轮转型版)。运行:python tests/test_bubbles.py

第十轮用户裁决:引导信息不画在宠物头顶,一律进控制面板("i" 悬停
tooltip)。故舞台气泡层(圆角矩形/Bayer 淡出/位置跟随)已从 bubbles.py
删除,BubbleLayer 降级为静默兼容壳(app.py 旧接线由主 agent 移除)。
本测试只覆盖转型后的模块职责,不依赖 app 接线(无 tk 弹窗):

判据:
 1) BUBBLE_TEXTS:引导池 "guide" 非空、无占位符;旧键(fed/transform/
    trust_up/grab_first)保留;hide_first 随 ADR-0034 剔除
 2) pick_text/tier_text:模板可格式化、rng 确定性、trust 三档齐备
 3) tooltip_text(状态 dict):非空多行;情绪四维(愉悦/恐惧/好奇/信任)
    中文名与数值都出现;能量(饱食度)+当前行为出现;附一条引导文案;
    空dict/缺字段/坏值安全兜底永不抛异常
 4) guide_text:rng 注入确定性、池内取值
 5) BubbleLayer 退役壳:show 恒 False、current 恒 None、对真实桩 canvas
    零绘制调用(舞台不再出现气泡项)、close/update/move_to/set_hires/
    set_enabled 幂等无异常;bubble_anchor 纯函数保留(旧接线兼容)
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.ui import bubbles as B
from neuropet.ui.bubbles import (COOLDOWN_S, BubbleLayer, TOOLTIP_EMOTIONS,
                                 bubble_anchor, guide_text, pick_text,
                                 tier_text, tooltip_text)


class RecCanvas:
    """记录型桩 canvas:任何 create_* 调用都会被抓住(舞台零绘制断言)。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_polygon(self, *a, **k):
        self.calls.append(("create_polygon", a, k))
        return 1

    def create_text(self, *a, **k):
        self.calls.append(("create_text", a, k))
        return 2

    def coords(self, *a, **k):
        self.calls.append(("coords", a, k))

    def itemconfig(self, *a, **k):
        self.calls.append(("itemconfig", a, k))

    def delete(self, *a, **k):
        self.calls.append(("delete", a, k))

    def tag_raise(self, *a, **k):
        self.calls.append(("tag_raise", a, k))

    def bbox(self, *_a):
        return (0, 0, 100, 20)


def main() -> None:
    # ---------- 1) 文案表结构 ----------
    assert B.BUBBLE_TEXTS.get("guide"), "缺引导文案池 guide"
    assert len(B.BUBBLE_TEXTS["guide"]) >= 4, "引导池至少 4 条(悬停不重复感)"
    for g in B.BUBBLE_TEXTS["guide"]:
        assert isinstance(g, str) and g and "{" not in g, f"guide 不得带占位符: {g!r}"
    for key in ("fed_first", "fed", "transform", "grab_first",
                "trust_up"):
        assert B.BUBBLE_TEXTS.get(key), f"过渡期文案键保留: {key}"
    assert COOLDOWN_S == 8.0
    print("[bubbles] 1) 文案表结构(guide 池/旧键保留/无占位符) OK")

    # ---------- 2) pick_text / tier_text ----------
    assert "蓝糖珠" in pick_text("fed", rng=random.Random(1), label="蓝糖珠")
    assert pick_text("fed", rng=random.Random(7), label="x") == \
        pick_text("fed", rng=random.Random(7), label="x"), "同 rng 必须同文案"
    assert all(t in B.BUBBLE_TEXTS["trust_up"] for t in (1, 2, 3))
    assert tier_text(2, rng=random.Random(0)) == B.BUBBLE_TEXTS["trust_up"][2][0]
    assert bubble_anchor((500, 400), 60) == (500, 400 - 60 - 18)
    print("[bubbles] 2) pick_text/tier_text/bubble_anchor(过渡兼容面) OK")

    # ---------- 3) tooltip_text:面板 "i" 悬停解释 ----------
    status = {
        "name": "小强", "activity": "explore", "mode": "crawl",
        "stomach": 0.62, "frozen": False, "held": True,
        "emotion": {"fear": 0.25, "hunger": 0.4, "curiosity": 0.6,
                    "trust": 0.8, "valence": -0.3, "arousal": 0.2},
    }
    txt = tooltip_text(status, rng=random.Random(0))
    assert txt and "\n" in txt, "非空多行"
    for key, zh in TOOLTIP_EMOTIONS:          # 四维:中文名与数值都出现
        want = f"{float(status['emotion'][key]):+.2f}"
        assert zh in txt and want in txt, f"{zh} 数值 {want} 必须在文案里"
    assert "62%" in txt, "能量(饱食度)出现"
    assert "探索" in txt, "当前行为(中文映射)出现"
    assert "被抓着" in txt, "状态标记出现"
    assert any(g in txt for g in B.BUBBLE_TEXTS["guide"]), "附一条引导文案"
    # 空 dict / 缺字段 / 坏值:永不抛异常、永不空串
    assert tooltip_text({})
    assert tooltip_text({"emotion": None, "stomach": None, "activity": ""})
    assert tooltip_text({"emotion": {"valence": "bad"}, "stomach": "x",
                         "activity": "weird_act"})
    assert "weird_act" in tooltip_text({"activity": "weird_act"}), "未知行为回退原文"
    print("[bubbles] 3) tooltip_text(四维数值/能量/行为/引导/兜底) OK")

    # ---------- 4) guide_text ----------
    assert guide_text(random.Random(3)) == guide_text(random.Random(3))
    pool = {guide_text(random.Random(i)) for i in range(64)}
    assert pool <= set(B.BUBBLE_TEXTS["guide"]), "只从池内取"
    assert len(pool) >= 2, "池确实在轮换"
    print("[bubbles] 4) guide_text(rng 确定性/池内取值) OK")

    # ---------- 5) BubbleLayer 退役壳:舞台零绘制 ----------
    c = RecCanvas()
    lay = BubbleLayer(c, bitmap_factory=lambda i, cc: f"stub{i}")
    assert lay.show("不该出现", 0, 0, now=0.0) is False, "舞台气泡已退役:show 恒 False"
    assert lay.current is None, "永远没有可见气泡"
    assert not c.calls, f"对舞台 canvas 零绘制调用, 实际: {c.calls}"
    # 旧接线全部方法幂等无异常
    lay.show("x", 1, 2, dur_s=1.0, kind="hint", pet_id="p")
    lay.move_to(3, 4)
    assert lay.update(100.0) is False
    lay.set_hires(True)
    lay.set_enabled(False)
    assert lay.enabled is False
    lay.set_enabled(True)
    lay.close()
    lay.close()                               # 幂等
    lay.clear()
    assert lay.current is None and not c.calls, "全程零绘制"
    print("[bubbles] 5) BubbleLayer 退役壳(show=False/current=None/零绘制/幂等) OK")

    print("[bubbles] 全部判据通过(5 组;舞台气泡职责已删除,文案源+tooltip API 就位)")


if __name__ == "__main__":
    main()
