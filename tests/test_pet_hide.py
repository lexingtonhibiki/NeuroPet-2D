"""§6 宠物隐藏/召回(隐藏保留记忆)验收。

运行:python tests/test_pet_hide.py

覆盖(任务书 4 项 + 边界,共 10 项判据):
  ① 隐藏生效:pet 移出 app.pets / app.world.pets / 舞台 canvas 项
     (stage._pet_items),进入 _hidden 整包保留(同一 PetHandle 对象)。
  ② 主循环不 tick 隐藏宠:app._step 多帧后隐藏宠 state.age_s 逐位冻结,
     可见对照组正常增长;世界快照不再含隐藏宠。
  ③ 幂等:重复 hide 同一 id / 重复 recall 同一 id 均无副作用(返回 False,
     集合与句柄不变);recall 未隐藏 id 同样幂等。
  ④ 召回连续性:召回后 state 字段(pos/heading/speed/stomach/age_s)、
     七维情绪(trust 等)、记忆摘要逐位不变,且 brain/body 是同一对象。
  ⑤ 多宠混合:A/B/C 三宠隐藏 A、C,B 保持可见 tick;召回 A 后三者
     状态正确(A、B 可见,C 仍隐藏)。
  ⑥ save↔load 往返含隐藏态:hide → save_all 落盘隐藏宠 profile 与
     session.json;新 App 实例 _restore_hidden 按原 pet_id 还原隐藏态,
     记忆摘要一致(跨"重启")。
  ⑦ 可见名额边界:max_pets 满时 recall 拒绝且宠物保持在隐藏态不丢
     状态;名额放宽后可召回。
  ⑧ 召回后与世界交互正常(FoodView):世界快照含该宠与食物,低饱食度
     + 近处食物会寻食/进食(stomach 回升或行为进入 seek_food/eat)。
  ⑨ remove_pet 对隐藏宠有效(隐藏态也能彻底移除,集合一致)。
  ⑩ clear_memory 语义不变:仅对可见宠生效;面板接线:隐藏后列表/数量
     同步,全部召回后清空(App._panel 回调链)。

注意:profile_dir 与 _SESSION_FILE 均打入临时目录,不污染真实 data/。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ---------------- 公共脚手架:App + 临时 profile/会话目录 ----------------
def _make_app(tmp: Path):
    import neuropet.core.app as appmod

    appmod._SESSION_FILE = tmp / "session.json"
    orig_profile = appmod.profile_dir

    def _tmp_profile(pet_id: str) -> Path:
        d = tmp / "profiles" / pet_id      # 与真实 profile_dir 同契约:mkdir
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tmp_profile
    app = appmod.App()
    app.cfg.max_pets = 6
    return app, appmod, orig_profile


def _teardown(appmod, orig_profile) -> None:
    appmod.profile_dir = orig_profile


def _feed_memory(app, pid: str) -> list[str]:
    """按 smoke.py 同款配方产生记忆(grab/fed/story),返回记忆摘要。"""
    h = app.pets[pid]
    h.brain.on_event("grab", {})
    h.brain.on_event("fed", {})
    h.brain.on_event("story", {"about": "cold", "valence": 0.8, "text": "雪景很美"})
    return list(h.brain.memory_digest())


# ---------------- ① 隐藏生效:三处移除 + 整包保留 ----------------
def test_hide_removes_from_sim_render_world(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
        assert pid in app.pets and pid not in app._hidden
        assert pid in app.world.pets, "可见宠应参与世界快照"
        assert pid in app.stage._pet_items, "可见宠应有舞台 canvas 项"
        h_before = app.pets[pid]

        ok = app.hide_pet(pid)
        record("hide.return_true", ok is True, "hide_pet 返回 True")
        record("hide.out_of_pets", pid not in app.pets and pid in app._hidden,
               "移出 self.pets 且进入 _hidden")
        record("hide.world_removed", pid not in app.world.pets,
               "世界快照不再含隐藏宠(其他宠感知不到)")
        record("hide.stage_item_removed", pid not in app.stage._pet_items,
               "舞台 canvas 项已删除(停渲染)")
        record("hide.handle_kept", app._hidden[pid] is h_before,
               "PetHandle 整包保留(同一对象:state/body/brain/体型档)")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ② 主循环不 tick 隐藏宠(计数器:age_s 冻结) ----------------
def test_hidden_pet_not_ticked(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pa = app.add_pet("species.cockroach", pos=(500.0, 300.0))   # 对照(可见)
        pb = app.add_pet("species.cockroach", pos=(900.0, 700.0))   # 将隐藏
        app.hide_pet(pb)
        a0, b0 = app.pets[pa].state.age_s, app._hidden[pb].state.age_s
        for _ in range(10):
            app._step(1.0 / 60.0)
        a1 = app.pets[pa].state.age_s
        b1 = app._hidden[pb].state.age_s
        record("step.visible_ticked", a1 > a0,
               f"可见宠 age_s {a0:.4f}→{a1:.4f}(正常推进)")
        record("step.hidden_frozen", b1 == b0,
               f"隐藏宠 age_s 逐位冻结({b0} == {b1},10 帧 sim 未触碰)")
        view = app.world.snapshot(pa, 0.0)
        record("step.snapshot_excludes", pa in view.pets and pb not in view.pets,
               "世界快照:可见宠在、隐藏宠不在")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ③ 幂等:重复 hide / 重复 recall ----------------
def test_idempotent(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
        assert app.hide_pet(pid) is True
        h = app._hidden[pid]
        again = app.hide_pet(pid)
        record("idem.hide_twice", again is False and app._hidden.get(pid) is h
               and pid not in app.pets,
               "重复 hide 返回 False,句柄与集合不变")
        assert app.recall_pet(pid) is True
        h2 = app.pets[pid]
        again_r = app.recall_pet(pid)
        record("idem.recall_twice", again_r is False and app.pets.get(pid) is h2
               and pid not in app._hidden,
               "重复 recall 返回 False,句柄与集合不变")
        ghost = app.recall_pet("no-such-pet")
        record("idem.recall_unknown", ghost is False and not app._hidden,
               "召回不存在的 id 幂等返回 False")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ④ 召回连续性:字段逐位不变 + 同一 brain/body ----------------
def test_recall_continuity_bitwise(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(700.0, 500.0))
        h = app.pets[pid]
        _feed_memory(app, pid)
        st = h.state
        pos0, hd0, sp0, stom0, age0 = (tuple(st.pos), st.heading, st.speed,
                                       st.stomach, st.age_s)
        emo0 = h.brain.emotion().as_dict()
        mem0 = list(h.brain.memory_digest())
        brain0, body0 = h.brain, h.body

        app.hide_pet(pid)
        for _ in range(30):                    # 隐藏期间主循环空转
            app._step(1.0 / 60.0)
        app.recall_pet(pid)
        h2 = app.pets[pid]
        st2 = h2.state
        record("recall.same_handle", h2 is h and h2.brain is brain0
               and h2.body is body0, "召回的是同一 PetHandle(brain/body 对象同一)")
        record("recall.state_bitwise",
               (tuple(st2.pos), st2.heading, st2.speed, st2.stomach, st2.age_s)
               == (pos0, hd0, sp0, stom0, age0),
               "pos/heading/speed/stomach/age_s 逐位不变(冻结语义)")
        record("recall.trust_bitwise", h2.brain.emotion().as_dict() == emo0,
               f"七维情绪逐位不变(trust={emo0.get('trust')})")
        record("recall.memory_kept", list(h2.brain.memory_digest()) == mem0,
               f"记忆摘要逐位不变({len(mem0)} 条)")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ⑤ 多宠混合隐藏/可见 ----------------
def test_mixed_hide_recall(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pa = app.add_pet("species.cockroach", pos=(400.0, 300.0))
        pb = app.add_pet("species.fruitfly", pos=(960.0, 540.0))
        pc = app.add_pet("species.cockroach", pos=(1400.0, 800.0))
        assert app.hide_pet(pa) and app.hide_pet(pc)
        record("mix.only_b_visible", set(app.pets) == {pb}
               and set(app._hidden) == {pa, pc}, "隐藏 A、C 后仅 B 可见")
        b0 = app.pets[pb].state.age_s
        a0, c0 = app._hidden[pa].state.age_s, app._hidden[pc].state.age_s
        for _ in range(5):
            app._step(1.0 / 60.0)
        record("mix.only_b_ticked",
               app.pets[pb].state.age_s > b0
               and app._hidden[pa].state.age_s == a0
               and app._hidden[pc].state.age_s == c0,
               "5 帧 sim:B tick,A/C 冻结")
        assert app.recall_pet(pa)
        record("mix.recall_one", set(app.pets) == {pa, pb}
               and set(app._hidden) == {pc},
               "召回 A:A、B 可见,C 仍隐藏")
        record("mix.reworld", pa in app.world.pets and pc not in app.world.pets,
               "世界快照随之正确(A 回归,C 不在)")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ⑥ save↔load 往返含隐藏态(跨"重启") ----------------
def test_save_load_roundtrip_hidden(tmp: Path) -> None:
    app1, appmod, orig = _make_app(tmp)
    try:
        pid = app1.add_pet("species.cockroach", pos=(700.0, 500.0),
                           pet_id="species.cockroach-hid01")
        _feed_memory(app1, pid)
        digest1 = list(app1.pets[pid].brain.memory_digest())
        assert digest1, "前置:喂食记忆后摘要非空"
        assert app1.hide_pet(pid)
        app1.save_all()
        sess = appmod._SESSION_FILE
        record("rt.session_written", sess.exists() and pid in sess.read_text("utf-8"),
               "save_all 写出 session.json 且含隐藏宠 id")
        record("rt.profile_saved",
               (tmp / "profiles" / pid / "memory.json").exists(),
               "隐藏宠 memory.json 已落盘")
        app1.root.destroy()

        app2, _, _ = _make_app(tmp)        # 模拟重启:新 App 实例
        try:
            assert not app2.pets and not app2._hidden
            app2._restore_hidden()
            h2 = app2._hidden.get(pid)
            record("rt.hidden_restored",
                   pid not in app2.pets and h2 is not None
                   and pid not in app2.world.pets
                   and pid not in app2.stage._pet_items,
                   "启动载入后隐藏态还原:仍隐藏(不 sim/不渲染)")
            record("rt.memory_roundtrip",
                   h2 is not None
                   and list(h2.brain.memory_digest()) == digest1,
                   f"还原宠记忆摘要一致({len(digest1)} 条)")
        finally:
            app2.root.destroy()
    finally:
        _teardown(appmod, orig)


# ---------------- ⑦ 可见名额边界:max_pets 满时 recall 拒绝 ----------------
def test_recall_respects_max_pets(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        app.cfg.max_pets = 1
        pa = app.add_pet("species.cockroach", pos=(500.0, 400.0))
        assert app.hide_pet(pa)
        assert app.recall_pet(pa)          # 可见 0 < 1:可召回
        assert app.hide_pet(pa)
        pb = app.add_pet("species.fruitfly", pos=(900.0, 600.0))   # 可见名额占满
        h = app._hidden[pa]
        refused = app.recall_pet(pa)
        record("max.recall_refused",
               refused is False and app._hidden.get(pa) is h
               and pa not in app.pets,
               "名额满:recall 拒绝且宠物保持在隐藏态(句柄不丢)")
        app.cfg.max_pets = 2
        record("max.recall_after_widen",
               app.recall_pet(pa) is True and set(app.pets) == {pa, pb},
               "名额放宽后可召回")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ⑧ 召回后 FoodView 与世界交互正常 ----------------
def test_foodview_after_recall(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(800.0, 450.0))
        st = app.pets[pid].state
        pos0 = tuple(st.pos)
        assert app.hide_pet(pid) and app.recall_pet(pid)
        record("food.pos_continuous", tuple(app.pets[pid].state.pos) == pos0,
               "召回后位置连续(原地继续)")
        app.drop_food(int(pos0[0]) + 8, int(pos0[1]))
        import time as _t
        view = app.world.snapshot(pid, _t.perf_counter())
        record("food.view_ok", pid in view.pets and bool(view.foods)
               and view.nearest_food(view.pets[pid].pos) is not None,
               "FoodView 正常:快照含该宠与食物,且能查询最近食物")
        st.stomach = 0.10                    # 饿(≤0.15 触发 hungry 事件)
        ever_seek, stomax = False, st.stomach
        for _ in range(180):                 # 最长 3s 仿真
            app._step(1.0 / 60.0)
            ever_seek |= st.activity.value in ("seek_food", "eat")
            stomax = max(stomax, st.stomach)
        record("food.interacts", ever_seek or stomax > 0.11,
               f"低饱食度+近处食物:出现觅食/进食行为或饱食度回升"
               f"(activity={st.activity.value}, stomach_max={stomax:.3f})")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ⑨ remove_pet 对隐藏宠有效 ----------------
def test_remove_hidden_pet(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
        app.hide_pet(pid)
        app.remove_pet(pid)
        record("rm.hidden_removed",
               pid not in app.pets and pid not in app._hidden
               and pid not in app.world.pets and pid not in app.stage._pet_items,
               "隐藏态 remove_pet:四处集合一致移除")
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ---------------- ⑩ clear_memory 语义不变 + 面板接线 ----------------
def test_clear_memory_and_panel(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
        _feed_memory(app, pid)
        assert app.memory_of(pid), "前置:记忆非空"
        app.hide_pet(pid)
        app.clear_memory(pid)               # 隐藏宠:语义不变 = 只查 self.pets,no-op
        h = app._hidden[pid]
        record("cm.hidden_untouched", bool(h.brain.memory_digest()),
               "clear_memory 对隐藏宠 no-op(语义不变,记忆保留)")

        # 面板接线:真实 ControlPanel(app._panel 回调链)
        from neuropet.ui.panel import ControlPanel
        app.recall_pet(pid)
        panel = ControlPanel(app)
        app._panel = panel
        app.hide_pet(pid)                   # 触发 panel.refresh_pets 回调
        items = list(panel.hidden_list.get(0, "end"))
        record("panel.hidden_listed",
               any(x.startswith(pid) for x in items)
               and panel.hidden_count_var.get() == "隐藏中:1 只"
               and pid not in panel.tree.get_children(),
               f"隐藏后:列表含 {pid},数量展示「{panel.hidden_count_var.get()}」,"
               "可见列表不含隐藏宠")
        panel.recall_all()
        record("panel.recall_all",
               pid in app.pets and not app.hidden_pets()
               and panel.hidden_count_var.get() == "隐藏中:0 只",
               "全部召回:宠物回归可见,隐藏列表/计数清零")
        panel.win.destroy()
    finally:
        app.root.destroy()
        _teardown(appmod, orig)


# ============================================================ 主流程
def main() -> None:
    with tempfile.TemporaryDirectory(prefix="neuropet_hide_") as td:
        tmp = Path(td)
        test_hide_removes_from_sim_render_world(tmp)
        test_hidden_pet_not_ticked(tmp)
        test_idempotent(tmp)
        test_recall_continuity_bitwise(tmp)
        test_mixed_hide_recall(tmp)
        test_save_load_roundtrip_hidden(tmp)
        test_recall_respects_max_pets(tmp)
        test_foodview_after_recall(tmp)
        test_remove_hidden_pet(tmp)
        test_clear_memory_and_panel(tmp)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[宠物隐藏/召回] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, detail in failed:
            print(f"  FAIL: {tag} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
