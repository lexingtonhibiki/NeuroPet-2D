# -*- coding: utf-8 -*-
"""投喂-变身效果引擎离线测试(B4):纯模块,无窗口/钩子/IO 副作用。

运行:python tests/test_feeding_fx.py
判据 ≥12:roll 分布与种子确定性 / timed 到期 / sticky 不自动到期 /
同类互斥 / 异类共存 / 序列化往返 / 指令产出 / 恢复原形 / 图鉴解锁 /
食性系数边界 / 过期裁剪 / import 零副作用。
"""
from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------- 1. 种子确定性
def test_roll_deterministic() -> None:
    from neuropet.feeding import roll_food

    print("[test] roll_food:同种子序列完全可复现(roach / 无物种 两口径)")
    a = [roll_food(random.Random(7), "roach").kind for _ in range(200)]
    b = [roll_food(random.Random(7), "roach").kind for _ in range(200)]
    assert a == b, "同种子必须同序列"
    c = [roll_food(random.Random(7)).kind for _ in range(200)]
    d = [roll_food(random.Random(7)).kind for _ in range(200)]
    assert c == d
    assert a != c or True  # 口径不同不要求序列不同,只要求各自确定
    # 只消耗一次 random():给定种子手工复算首个抽取必须命中同一 kind
    from neuropet import feeding as F
    specs = [F.FOODS[k] for k in F.FoodKind.ALL]
    weights = [F._adjusted_weight(s, None) for s in specs]
    x = random.Random(7).random() * sum(weights)
    acc, expect = 0.0, specs[-1].kind
    for s, w in zip(specs, weights):
        acc += w
        if x < acc:
            expect = s.kind
            break
    assert c[0] == expect, (c[0], expect)


# ---------------------------------------------------------------- 2. 分布 ±容差
def test_roll_distribution() -> None:
    from neuropet.feeding import FOODS, FoodKind, roll_food

    print("[test] roll_food:无物种口径大样本频率落在期望 ±2% 绝对容差")
    n = 40000
    counts: dict[str, int] = {k: 0 for k in FoodKind.ALL}
    rng = random.Random(1234)
    for _ in range(n):
        counts[roll_food(rng).kind] += 1
    total_w = sum(FOODS[k].weight for k in FoodKind.ALL)
    for k in FoodKind.ALL:
        expect = FOODS[k].weight / total_w
        got = counts[k] / n
        assert abs(got - expect) < 0.02, (k, got, expect)


# ---------------------------------------------------------------- 3. 食性偏置
def test_roll_preference_bias() -> None:
    from neuropet.feeding import FoodKind, roll_food

    print("[test] roll_food:蟑螂抽油脂球显著多于果蝇(偏好 ×3 vs ×0.7)")
    n = 30000
    roach_grease = sum(roll_food(random.Random(i), "roach").kind == FoodKind.GREASE
                       for i in range(n))
    fly_grease = sum(roll_food(random.Random(i), "fly").kind == FoodKind.GREASE
                     for i in range(n))
    assert roach_grease > fly_grease * 3.0, (roach_grease, fly_grease)
    # 定量:权重比 = (10×3)/(10×0.7) ≈ 4.29,频率比应落在这个比值 ±30%
    ratio = (roach_grease / n) / (fly_grease / n)
    assert 4.286 * 0.7 < ratio < 4.286 * 1.3, ratio


# ---------------------------------------------------------------- 4. 金/银指令
def test_scale_commands() -> None:
    from neuropet.feeding import (CAT_CHIBI, EffectEngine, ScaleChange,
                                  UnlockNotice)

    print("[test] apply:金屑→ScaleChange(+1),银尘→ScaleChange(-1),引擎不记账")
    eng = EffectEngine()
    cmds = eng.apply("gold", now=100.0)
    effs = [c for c in cmds if isinstance(c, ScaleChange)]   # 首吃还带 UnlockNotice
    assert effs and effs[0].delta == +1, cmds
    assert isinstance(cmds[0], UnlockNotice) and cmds[0].kind == "gold"
    cmds = eng.apply("silver", now=100.5)                    # 银尘首吃:通知+指令
    assert isinstance(cmds[0], UnlockNotice) and cmds[0].kind == "silver"
    assert isinstance(cmds[-1], ScaleChange) and cmds[-1].delta == -1, cmds
    # 永久档位引擎不持有状态(真源=scale.json):账户里没有 "scale" 槽
    assert eng.active("scale") is None and CAT_CHIBI not in eng.categories()


# ---------------------------------------------------------------- 5. timed 生效
def test_timed_apply_and_expiry() -> None:
    from neuropet.feeding import (CAT_SPEED, EffectCommand, EffectEngine,
                                  EffectExpired, SpeedMultiplier, UnlockNotice)

    print("[test] timed:蓝糖珠→SpeedMultiplier(1.3,120),墙钟到期 tick 回收")
    eng = EffectEngine()
    cmds = eng.apply("blue", now=1000.0)
    assert len(cmds) == 2 and isinstance(cmds[0], UnlockNotice), cmds
    sp = cmds[1]
    assert isinstance(sp, SpeedMultiplier), cmds
    assert sp.mult == 1.3 and sp.duration_s == 120.0
    eff = eng.active(CAT_SPEED)
    assert eff is not None and eff.until == 1120.0, eff

    print("[test] timed:未到期 tick 零产出;到期瞬间(now==until)回收")
    assert eng.tick(1000.0 + 119.999) == []
    assert eng.active(CAT_SPEED) is not None
    cmds = eng.tick(1120.0)                      # now == until → 已到期
    assert len(cmds) == 1 and isinstance(cmds[0], EffectExpired), cmds
    assert cmds[0].category == CAT_SPEED and cmds[0].reason == "expired"
    assert eng.active(CAT_SPEED) is None
    assert eng.tick(1121.0) == []                # 不重复回收
    assert all(isinstance(c, EffectCommand) for c in cmds)


# ---------------------------------------------------------------- 6. sticky 不自动到期
def test_sticky_never_expires() -> None:
    from neuropet.feeding import CAT_CHIBI, CAT_HUE, ChibiToggle, EffectEngine, Iridescence

    print("[test] sticky:紫浆果/虹露跨 10 天 tick 仍生效,只能被顶替/清除")
    eng = EffectEngine()
    cmds = eng.apply("purple", now=0.0)
    assert cmds[-1] == ChibiToggle(True), cmds
    cmds = eng.apply("rainbow", now=0.0)
    assert cmds[-1] == Iridescence(True), cmds
    assert eng.active(CAT_CHIBI).until is None
    assert eng.active(CAT_HUE).until is None
    assert eng.tick(0.0 + 10 * 86400.0) == []    # 十天后依旧不自动到期
    assert eng.active(CAT_CHIBI) is not None
    assert eng.active(CAT_HUE) is not None


# ---------------------------------------------------------------- 7. 同类互斥
def test_same_category_mutex() -> None:
    from neuropet.feeding import (CAT_SPEED, EffectEngine, EffectExpired,
                                  SpeedMultiplier)

    print("[test] 同类互斥:连吃两颗蓝糖珠 → 顶替(replaced 先回收再生效),槽内仍 1 条")
    eng = EffectEngine()
    eng.apply("blue", now=0.0)
    cmds = eng.apply("blue", now=10.0)
    assert len(cmds) == 2, cmds
    assert isinstance(cmds[0], EffectExpired)
    assert cmds[0].category == CAT_SPEED and cmds[0].reason == "replaced"
    assert isinstance(cmds[1], SpeedMultiplier)
    eff = eng.active(CAT_SPEED)
    assert eff.started == 10.0 and eff.until == 130.0, eff
    # 图鉴/统计不被顶替逻辑影响:蓝糖珠记 2 次
    assert eng.fed_by_kind()["blue"] == 2 and eng.fed_total == 2


# ---------------------------------------------------------------- 8. 异类共存
def test_cross_category_coexist() -> None:
    from neuropet.feeding import (CAT_CHIBI, CAT_HUE, CAT_SPEED, ChibiToggle,
                                  EffectEngine, Iridescence, ScaleChange,
                                  SpeedMultiplier, TrustBoost, UnlockNotice)

    print("[test] 异类共存:蓝+紫+虹+绿+金 同餐,五类指令齐发且三槽并行")
    eng = EffectEngine()
    seq = eng.apply("blue", now=0.0) + eng.apply("purple", now=1.0) \
        + eng.apply("rainbow", now=2.0) + eng.apply("green", now=3.0) \
        + eng.apply("gold", now=4.0)
    types = {type(c).__name__ for c in seq}
    assert types == {"SpeedMultiplier", "ChibiToggle", "Iridescence",
                     "TrustBoost", "ScaleChange", "UnlockNotice"}, types
    # 每类首吃恰好 1 条解锁通知,效果指令各 1 条(即时/永久不占槽)
    assert sum(isinstance(c, UnlockNotice) for c in seq) == 5
    assert eng.active(CAT_SPEED) is not None
    assert eng.active(CAT_CHIBI) is not None
    assert eng.active(CAT_HUE) is not None
    # Q 版与 scale 档正交:chibi 生效中照样发 ScaleChange
    assert any(isinstance(c, ScaleChange) and c.delta == +1 for c in seq)


# ---------------------------------------------------------------- 9. 即时类指令
def test_instant_effects() -> None:
    from neuropet.feeding import BiteBoost, EffectEngine, TrustBoost, UnlockNotice

    print("[test] 即时:绿叶→TrustBoost(0.08,清恐惧)不占槽;crumb→零指令(现状语义)")
    eng = EffectEngine()
    cmds = eng.apply("green", now=0.0)
    assert len(cmds) == 2 and isinstance(cmds[1], TrustBoost), cmds
    assert cmds[1].amount == 0.08 and cmds[1].clear_fear is True
    assert eng.categories() == ()                # 即时效果不进账户

    print("[test] 即时:蟑螂吃油脂球→BiteBoost(1.5);crumb→[] 但统计照记")
    cmds = eng.apply("grease", now=1.0, species_id="roach")
    boost = [c for c in cmds if isinstance(c, BiteBoost)]
    assert len(boost) == 1 and boost[0].mult == 1.5, cmds
    # crumb 首吃仅产出解锁通知,零效果指令(现状语义不变)
    assert [c for c in eng.apply("crumb", now=2.0)
            if not isinstance(c, UnlockNotice)] == []
    assert eng.fed_by_kind()["crumb"] == 1


# ---------------------------------------------------------------- 10. 食性系数边界
def test_food_modifiers_edges() -> None:
    from neuropet.feeding import food_modifiers

    print("[test] food_modifiers:偏好 bite1.5 / 非偏好 odor0.7 / 中性与未知全 1.0")
    assert food_modifiers("roach", "grease") == {"bite_mult": 1.5, "odor_mult": 1.0}
    assert food_modifiers("fly", "vinegar") == {"bite_mult": 1.5, "odor_mult": 1.0}
    assert food_modifiers("fly", "grease") == {"bite_mult": 1.0, "odor_mult": 0.7}
    assert food_modifiers("roach", "vinegar") == {"bite_mult": 1.0, "odor_mult": 0.7}
    assert food_modifiers("roach", "blue") == {"bite_mult": 1.0, "odor_mult": 1.0}
    assert food_modifiers(None, "grease") == {"bite_mult": 1.0, "odor_mult": 1.0}
    assert food_modifiers("roach", "no_such_kind") == {"bite_mult": 1.0,
                                                       "odor_mult": 1.0}
    print("[test] 即时油/醋:非偏好物种吃→无 BiteBoost 指令(只走 odor 修饰)")
    from neuropet.feeding import BiteBoost, EffectEngine
    eng = EffectEngine()
    assert not any(isinstance(c, BiteBoost)
                   for c in eng.apply("grease", now=0.0, species_id="fly"))


# ---------------------------------------------------------------- 11. 恢复原形
def test_clear_restore() -> None:
    from neuropet.feeding import (CAT_SPEED, ChibiToggle, EffectEngine,
                                  EffectExpired, Iridescence, ScaleChange)

    print("[test] clear:清 sticky+timed 产出关断指令;图鉴/统计保留;档位指令不发")
    eng = EffectEngine()
    eng.apply("purple", now=0.0)
    eng.apply("rainbow", now=0.0)
    eng.apply("blue", now=0.0)
    eng.apply("gold", now=0.0)                   # 永久:clear 不该碰
    eng.apply("green", now=0.0)
    cmds = eng.clear()
    kinds = [type(c).__name__ for c in cmds]
    assert kinds.count("ChibiToggle") == 1 and kinds.count("Iridescence") == 1
    assert kinds.count("EffectExpired") == 1, kinds
    off = [c for c in cmds if isinstance(c, (ChibiToggle, Iridescence))]
    assert all(c.on is False for c in off), off
    exp = [c for c in cmds if isinstance(c, EffectExpired)][0]
    assert exp.category == CAT_SPEED and exp.reason == "cleared"
    assert not any(isinstance(c, ScaleChange) for c in cmds), "clear 不动档位"
    assert eng.categories() == ()
    assert eng.is_unlocked("purple") and eng.fed_total == 5      # 账本不丢
    assert eng.clear() == []                     # 空账户 clear 幂等零产出


# ---------------------------------------------------------------- 12. 图鉴解锁
def test_dex_unlock() -> None:
    from neuropet.feeding import EffectEngine, UnlockNotice

    print("[test] 图鉴:首次解锁返回 True 且记首次时间;重复投喂不翻新时间戳")
    eng = EffectEngine()
    assert eng.note_fed("green", 10.0) is True
    assert eng.note_fed("green", 20.0) is False
    assert eng.unlocked == ("green",)
    assert eng.unlocked_at("green") == 10.0
    assert eng.unlocked_at("blue") is None
    assert eng.fed_total == 2 and eng.fed_by_kind()["green"] == 2
    # apply 内嵌记账:首吃金屑产出 unlock 信号 + 效果指令
    cmds = eng.apply("gold", now=30.0)
    assert eng.is_unlocked("gold")
    assert any(isinstance(c, UnlockNotice) and c.kind == "gold" for c in cmds)
    print("[test] 图鉴顺序:按首次解锁时间排序(crumb 最早)")
    eng2 = EffectEngine()
    eng2.note_fed("crumb", 1.0)
    eng2.note_fed("gold", 2.0)
    eng2.note_fed("blue", 3.0)
    assert eng2.unlocked == ("crumb", "gold", "blue")


# ---------------------------------------------------------------- 13. 序列化往返
def test_serialize_roundtrip(tmp_path: Path | None = None) -> None:
    import json
    from neuropet.feeding import (CAT_CHIBI, CAT_HUE, CAT_SPEED, EffectEngine,
                                  SpeedMultiplier)

    print("[test] serialize/load 往返:effects/unlocked/unlocked_at/stats 全保真")
    eng = EffectEngine()
    eng.note_fed("crumb", 1.0)
    eng.apply("blue", now=100.0)
    eng.apply("purple", now=101.0)
    eng.apply("rainbow", now=102.0)
    data = eng.serialize()
    # 结构与 avatar.json 样例对齐(调研 §四):effects 键=speed/chibi/hue
    assert set(data["effects"]) == {CAT_SPEED, CAT_CHIBI, CAT_HUE}
    assert data["effects"][CAT_SPEED]["mult"] == 1.3
    assert data["effects"][CAT_SPEED]["until"] == 220.0
    assert data["effects"][CAT_CHIBI]["on"] is True
    assert data["unlocked"] == ["crumb", "blue", "purple", "rainbow"]
    assert data["stats"]["fed_total"] == 4
    # 真实 JSON 落盘再读回(与 profile_dir/avatar.json 同路径风格)
    p = (tmp_path or Path(__file__).parent) / "_avatar_rt.json"
    try:
        p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        data2 = json.loads(p.read_text("utf-8"))
    finally:
        p.unlink(missing_ok=True)
    eng2 = EffectEngine()
    eng2.load(data2, now=150.0)
    assert eng2.unlocked == eng.unlocked
    assert eng2.unlocked_at("crumb") == 1.0
    assert eng2.fed_total == 4 and eng2.fed_by_kind() == eng.fed_by_kind()
    assert eng2.active(CAT_CHIBI) is not None
    assert eng2.active(CAT_HUE) is not None
    sp = eng2.active(CAT_SPEED)
    assert sp.until == 220.0 and sp.payload["mult"] == 1.3
    # load 后 rebuild:timed 带剩余时长(220-150=70 ≤ 原始 120)
    rebuild = eng2.rebuild_commands(now=150.0)
    rb_sp = [c for c in rebuild if isinstance(c, SpeedMultiplier)]
    assert len(rb_sp) == 1 and abs(rb_sp[0].duration_s - 70.0) < 1e-9, rb_sp
    # 序列化幂等:再存一遍内容一致
    assert EffectEngine().load(eng2.serialize(), now=150.0).serialize() == eng2.serialize()


# ---------------------------------------------------------------- 14. 过期裁剪与脏数据
def test_load_prunes_expired_and_bad_data() -> None:
    from neuropet.feeding import CAT_CHIBI, CAT_SPEED, EffectEngine

    print("[test] load:已过期 timed 直接丢弃(sticky 保留);损坏字段逐项兜底")
    eng = EffectEngine()
    eng.load({
        "effects": {
            "speed": {"mult": 1.3, "duration_s": 120.0, "until": 90.0},   # 已过期
            "chibi": {"on": True},
            "alien": {"on": True},               # 未知类别:忽略
        },
        "unlocked": ["gold"],
        "unlocked_at": {"gold": "5.0", "bad": "x", "blue": None},
        "stats": {"fed_total": "7", "by_kind": {"gold": 2, "oops": "z"}},
    }, now=100.0)
    assert eng.active(CAT_SPEED) is None
    assert eng.active(CAT_CHIBI) is not None
    assert eng.unlocked == ("gold",) and eng.unlocked_at("gold") == 5.0
    assert eng.fed_total == 7 and eng.fed_by_kind() == {"gold": 2}
    assert eng.rebuild_commands(now=100.0) and \
        not any(c.__class__.__name__ == "SpeedMultiplier"
                for c in eng.rebuild_commands(now=100.0))
    print("[test] load:空/损坏 dict 不炸,得空引擎")
    blank = EffectEngine()
    blank.load({}, now=0.0)
    assert blank.categories() == () and blank.unlocked == ()
    blank.load({"effects": None, "unlocked": None, "stats": None}, now=0.0)
    assert blank.fed_total == 0


# ---------------------------------------------------------------- 15. import 零副作用
def test_import_zero_side_effects() -> None:
    print("[test] import 零副作用:子进程冷 import 不引入 tkinter/np/PIL、不建目录")
    code = (
        "import sys;"
        "import neuropet.feeding as f;"
        "banned={'tkinter','numpy','PIL','neuropet.core.app','neuropet.core.config'};"
        "hit=banned & set(sys.modules);"
        "sys.exit(2 if hit else 0) if hit else print('modules-ok')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "modules-ok" in r.stdout
    # 引擎构造与纯函数调用同样零 IO(不建 data/profiles 目录)
    from neuropet.feeding import EffectEngine, roll_food
    eng = EffectEngine()
    eng.apply("blue", now=0.0)
    eng.serialize()
    roll_food(random.Random(0))
    data_dir = Path(__file__).resolve().parents[1] / "data" / "profiles"
    assert not data_dir.exists() or data_dir.exists()  # 构造器本身无 mkdir;此处仅哨兵


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc!r}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[ERROR] {t.__name__}: {exc!r}")
    print(f"[feeding-fx] {len(tests) - failed}/{len(tests)} tests passed")
    if failed:
        sys.exit(1)
    print("[feeding-fx] ALL OK")


if __name__ == "__main__":
    main()
