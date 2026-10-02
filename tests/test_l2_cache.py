# -*- coding: utf-8 -*-
"""r25 单元 2 —— L2 清空记账缺 `global`(T1)：护栏 L5 清空后 L2 缓存永久空转。

被测承诺(`neuropet/render/torso.py`)：L2 成品桶被 `invalidate_l2()`/`invalidate()`
清空后，**模块级记账**必须同步归零，使清空之后 L2 仍然可用 —— 而不是
「记账虚高（残留旧和）+ 条目为空」⇒ 之后每次新插入立刻被自己踢掉 ⇒ 缓存永久为空。

判据（确定性、进程内、不依赖时序/负载/光标；口径 = docs/handoff/r25-goal-synthesis-r1.md §2④）：
  a1 全清归零     : 装填(scale≠1)→ `invalidate_l2()` → `l2_bytes == 0` 且 `l2_entries == 0`
  a2 同键必命中   : 清空后同键再查必须命中(l2_hit +1、重建计数不增、返回同一对象)
  a3 物种分支不抛 : `torso.invalidate(sid)`(该物种有条目时)不得抛；唯一调用方是
                    `torso.py:249`，而 `:249` **不在** `:244-248` 的 try 内 ⇒ 抛就是**响的**
  a4 物种分支真清 : 该支路**自己**必须有采样点：清后立刻 `l2_entries == 0 且 l2_bytes == 0`
                    并记账自洽。G-1(验收方两轴收敛)：其后若只接「全清」采样，支路自身的
                    记账漂移会被全清整体抹平 ⇒ 判据对该支路零鉴别力（实测：注入
                    「物种支 pop 不漏减 bytes」，未补采样点的判据仍全绿，补后必红）
  b  自洽         : 任意 invalidate 之后 `l2_entries == 0 ⟺ l2_bytes == 0`，
                    且 `Σ entry_bytes == l2_bytes`(单一真源；§6「同一份状态不许两个说法」)

现状(`:84` 的赋值因缺 `global` 绑成局部名)⇒ a1/a2/a3/b 全红；`torso.py:80` 加 1 行 `global` 后全绿。
负向对照：注掉那行 `global` ⇒ 必红（原始输出 + 两个 sha256 见 docs/handoff/r25-l2cache-r1.md）。

装填次数由**活预算**推出，不改预算（本轮禁改预算，见 §2⑥）：n = ⌊预算/单桶⌋+1
⇒ 装填后残留记账 > 预算 − 单桶 ⇒ 清空后第一次插入必被自己踢掉（现状的真机制）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core import instr as _I        # noqa: E402
from neuropet.render import torso as T       # noqa: E402

SID = "species.cockroach"
HALF = 360                    # 成品桶 = (2*HALF)²*4 = 2,073,600 B（与 logs/u0_s3 的 720² 桶同口径）
K = 1.5                       # scale ≠ 1 ⇒ 走 L2 成品桶路径
FOLD = 0.5
TRAITS = {"species_id": SID, "scale": K, "body_len_base": 120}
FAILS: list[str] = []


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


def counters() -> dict:
    return dict(_I.report()["counters"])


def stats_line() -> str:
    s = T.l2_stats()
    return f"l2_bytes={s['l2_bytes']} l2_entries={s['l2_entries']}"


def entry_bytes_sum() -> int:
    return sum(e[1] for e in T._L2.values())


def check_selfconsistent(tag: str) -> None:
    """判据 b（两个说法必须一致）。"""
    s = T.l2_stats()
    nb, ne = s["l2_bytes"], s["l2_entries"]
    check((ne == 0) == (nb == 0),
          f"b[{tag}] entries==0 ⟺ bytes==0：bytes={nb} entries={ne}")
    tot = entry_bytes_sum()
    check(tot == nb,
          f"b[{tag}] Σ entry_bytes == l2_bytes：Σ={tot} bytes={nb}")


def main() -> None:
    budget = T._L2_budget["roach"]                 # 只读：本轮不许改预算
    one = (HALF * 2) ** 2 * 4
    n_fill = budget // one + 1
    heads = [float(i * 2) for i in range(n_fill)]  # 2° 桶 ⇒ 互不相同的键
    print(f"== 单元 2：L2 清空记账  (roach 预算={budget} 单桶={one} ⇒ 装填 {n_fill} 次) ==")
    check(2 <= n_fill <= 8, f"装填次数合理：n_fill={n_fill}")

    # ---- 装填 ----
    for h in heads:
        T.get_torso(SID, HALF, h, FOLD, TRAITS)
    s = T.l2_stats()
    check(s["l2_bytes"] > 0 and s["l2_entries"] > 0,
          f"装填后有缓存可清：{stats_line()}")
    check_selfconsistent("装填后")

    # ---- a3：物种分支（唯一调用方 torso.py:249，try 之外 ⇒ 抛就是响的）----
    raised = None
    try:
        T.invalidate(SID)
    except Exception as e:                          # noqa: BLE001 —— 现状 = UnboundLocalError
        raised = f"{type(e).__name__}: {e}"
    check(raised is None,
          f"a3 物种分支 invalidate({SID!r}) 不抛：raised={raised}")

    # ---- a4（G-1）：物种支路**自己**的采样点。本测试只装了该物种条目 ⇒ 清后必须真空。
    #      其后若只接 `invalidate_l2()` 全清采样，支路自身的漂移会被全清抹平（无鉴别力）。
    check_selfconsistent("a3 物种分支后")
    s = T.l2_stats()
    check(s["l2_entries"] == 0 and s["l2_bytes"] == 0,
          f"a4 物种分支真清该物种条目（本测试无他物种条目）：{stats_line()}")

    # 重新装填（现状下 a3 抛在 pop 之前 ⇒ 状态未变，这里是全命中；修后是真重装）
    for h in heads:
        T.get_torso(SID, HALF, h, FOLD, TRAITS)

    # ---- a1：全清必须真的归零 ----
    T.invalidate_l2()
    s = T.l2_stats()
    check(s["l2_bytes"] == 0, f"a1 全清后 l2_bytes == 0：{stats_line()}")
    check(s["l2_entries"] == 0, f"a1 全清后 l2_entries == 0：{stats_line()}")
    check_selfconsistent("全清后")

    # ---- a2：清空后同键再查必须命中（现状：自踢 ⇒ 必 miss）----
    was_on = _I.enabled()
    _I.set_enabled(True)                            # 设计用途即「测试/探针」
    try:
        _I.reset()
        c0 = counters()
        spr1 = T.get_torso(SID, HALF, heads[0], FOLD, TRAITS)
        c1 = counters()
        spr2 = T.get_torso(SID, HALF, heads[0], FOLD, TRAITS)
        c2 = counters()
    finally:
        _I.set_enabled(was_on)
        _I.reset()
    miss1 = c1.get("l2_miss", 0) - c0.get("l2_miss", 0)
    miss2 = c2.get("l2_miss", 0) - c1.get("l2_miss", 0)
    hit2 = c2.get("l2_hit", 0) - c1.get("l2_hit", 0)
    check(miss1 == 1, f"a2 首查重建 1 次（清空后缓存必为空）：重建+{miss1}")
    check(hit2 == 1 and miss2 == 0,
          f"a2 同键再查命中、重建不增：hit+{hit2} 重建+{miss2}")
    check(spr1 is spr2, "a2 同键返回同一对象（命中 ⇒ 未重建）")
    check_selfconsistent("命中后")

    # ---- 末次全清仍自洽（长期成立类）----
    T.invalidate_l2()
    check_selfconsistent("末次全清后")

    print("\n" + "=" * 74)
    if FAILS:
        print(f"失败 {len(FAILS)} 项:")
        for f in FAILS:
            print(f"  x {f}")
    else:
        print("全部判据通过")
    print("=" * 74)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
