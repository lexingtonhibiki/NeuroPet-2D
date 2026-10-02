"""r16 Task E 验收:启动名册读配置(data/pets.json)+ 果蝇模型核查不变量。

运行:python tests/test_roster_config.py

五判据(任务书 §1)+ 收尾波补判据:
  ① 缺省生成:pets.json 缺失 → load_roster 自动生成缺省文件,内容与旧
     run() 硬编码等价(cockroach+fruitfly 各一只,顺序=内置注册顺序),
     老用户无感;
  ② 读取生效:名册条目决定启动宠物(物种/顺序/可选 pos),取代硬编码;
  ③ 未知物种跳过:打印警告并继续,不崩;条目保留在文件里(装回插件仍生效);
  ④ 增删回写:面板/托盘入口(App.add/remove/hide/recall)后 pets.json =
     当前可见名册;隐藏宠不进名册(session.json 独立机制);
  ⑤ max_pets 上限:名册条目超上限时忽略多余条目,不崩;
  ⑥ 坏档不崩(十六轮收尾波补,防御代码已在、缺测试):pets.json 坏
     JSON / 合法 JSON 但非清单(dict)→ load_roster 返回缺省/空名册
     不崩,且**不覆盖原文件**(保留现场供用户排查)。

果蝇核查不变量(任务书 §2,"果蝇套蟑螂模型"报障的反证):
  A. 两物种均 PartsBaker(十六轮合流后事实):baker_for("cockroach") 为
     13 部件(体 1 + 6 腿×2,分割扫描件,带图集纹理);baker_for("fruitfly")
     为 40 部件(NMF 解剖件:l_wing/r_wing/l_haltere/arista 等);
  B. 分发路径正确:render3d_pipeline._species_key 两物种各归其类;
  C. 两物种渲染输出显著不同:同 yaw 各自显示尺寸,像素差 >40%(实测
     81.3%;参照系:同物种仅 yaw 差 30° 为 14.8%)。
  证据图:scratch/_r16_evidence_fly_vs_roach/(结论:分派正确,静息翅
  平展的顶视棕色翼面与蟑螂剪影观感相近 = 观感问题,非错挂)。

隔离纪律:名册/profile/会话文件全部打入临时目录(app._pets_path 实例属性 +
appmod.profile_dir/_SESSION_FILE 补丁,与 tests/test_pet_hide.py 同惯例),
不污染仓库真实 data/;另设判据验证"未 run() 不落盘"的防污染闸门本身。

r25 新增:名册 ↔ pet_id(缺陷链条与修复口径见 r25-architecture.md §19)
  ⑦ **重启保真**(原判据:无 —— 名册只存 ``species``,重启 add_pet 发新
     uuid → profile_dir 换目录 → ``_load_profile`` 读空 → 记忆/信任/等级/
     年龄全部作废,实测 ``data/profiles/`` 累积 2284 个孤儿目录)。新判据:
     add_pet 回写名册(**真落盘**)→ **新 App 实例**读同一文件载入 → pet_id
     复用、档案目录路径不变、脑关键状态(等级/信任/年龄/记忆条数/n_fed)与
     重启前一致。为什么:这是"养一只有记忆的宠物"这一卖点在重启后能否成立
     的唯一直接证据;**全程走真实路径,不手工注入 pet_id**(坑表第 6 条)。
  ⑧ **向后兼容**(原判据:无)。新判据:无 ``pet_id`` 的旧条目 → 正常发新 id、
     不崩;预置的旧孤儿档案**不被追认**(新宠仍是出厂态)。为什么:老玩家升级
     不许崩,且"无法匹配"必须显式落成"不追认",而不是碰运气匹配上。
  ⑨ **零新孤儿**(原判据:无)。新判据:同一名册连续重启 2 次,profiles/ 下
     目录数不变(仅建宠那一次新增 1 个)。为什么:孤儿目录是缺陷的物理后果,
     目录计数是"修好了"最直接的可跑证据。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ---------------- 公共脚手架:App + 临时名册/profile/会话目录 ----------------
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
    app._pets_path = tmp / "pets.json"     # 实例属性重定向(与 _SESSION_FILE 同类)
    app.cfg.max_pets = 6
    return app, appmod, orig_profile


def _teardown(app, appmod, orig_profile) -> None:
    app.root.destroy()
    appmod.profile_dir = orig_profile


def _roster_file(tmp: Path) -> Path:
    return tmp / "pets.json"


def _write_raw(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")


def _read_raw(path: Path) -> list[dict]:
    return json.loads(path.read_text("utf-8"))


# ---------------- ① 缺省生成:缺失 → 自动生成,与旧硬编码等价 ----------------
def test_default_generation(tmp: Path) -> None:
    from neuropet.core.config import default_roster, load_roster

    path = _roster_file(tmp)
    assert not path.exists(), "前置:名册文件不存在"
    entries = load_roster(path)
    record("roster.default_generated", path.exists() and len(entries) == 2,
           f"缺失时自动生成:文件存在={path.exists()},条目={len(entries)}")

    # 与旧 run() 硬编码等价:逐物种各一只,顺序 = 内置物种注册顺序
    from neuropet.core.plugin import PluginRegistry
    from neuropet.species import cockroach, fruitfly
    reg = PluginRegistry()
    for mod in (cockroach, fruitfly):
        reg.register_class(mod.PLUGIN_CLS)
    old_order = [m.id for m in reg.manifests("species")]
    got = [e["species"] for e in entries]
    record("roster.default_equivalent",
           got == old_order == [e["species"] for e in default_roster()],
           f"缺省名册 {got} == 旧硬编码顺序 {old_order}(老用户无感)")

    again = load_roster(path)              # 二次读取:读文件,不再重写
    record("roster.default_readable",
           [e["species"] for e in again] == got and
           _read_raw(path) and len(_read_raw(path)) == 2,
           "生成后可正常读回(内容一致,文件合法 JSON)")


# ---------------- ② 读取生效:条目决定启动宠物(物种/顺序/pos) ----------------
def test_roster_read(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        _write_raw(_roster_file(tmp), [
            {"species": "species.fruitfly"},
            {"species": "species.cockroach", "pos": [1234.0, 567.0]},
        ])
        app._load_startup_roster()
        species_seq = [h.state.species_id for h in app.pets.values()]
        record("read.species_and_order",
               species_seq == ["species.fruitfly", "species.cockroach"],
               f"按名册载入(旧硬编码做不到的 fly 在前): {species_seq}")
        pos = next(h.state.pos for h in app.pets.values()
                   if h.state.species_id == "species.cockroach")
        record("read.pos_honored",
               abs(pos[0] - 1234.0) < 1.0 and abs(pos[1] - 567.0) < 1.0,
               f"可选 pos 生效: cockroach pos=({pos[0]:.0f}, {pos[1]:.0f})"
               "(经 clamp_to_screen,画幅内不截断)")
        # 载入期不回写:未知物种/超限条目保留在文件里(装回插件仍生效)
        record("read.no_rewrite_on_load",
               len(_read_raw(_roster_file(tmp))) == 2,
               "启动载入不重写文件(条目保真)")
    finally:
        _teardown(app, appmod, orig)


# ---------------- ③ 未知物种跳过:警告不崩,条目保留 ----------------
def test_unknown_species_skipped(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        _write_raw(_roster_file(tmp), [
            {"species": "species.mantis"},          # 未注册物种
            {"species": "species.cockroach"},
            "garbage-entry",                        # 非 dict:静默丢弃
            {"pos": [1.0, 2.0]},                    # 缺 species:丢弃
        ])
        app._load_startup_roster()                  # 不抛异常 = 不崩
        species_seq = [h.state.species_id for h in app.pets.values()]
        record("skip.unknown_and_malformed",
               species_seq == ["species.cockroach"],
               f"未知物种/坏条目跳过,正常条目生效: {species_seq}")
        raw = _read_raw(_roster_file(tmp))
        record("skip.entry_preserved",
               any(isinstance(e, dict) and e.get("species") == "species.mantis"
                   for e in raw),
               "未知物种条目保留在文件(装回插件后重启仍生效)")
    finally:
        _teardown(app, appmod, orig)


# ---------------- ④ 增删回写:可见名册实时落盘,隐藏宠不进名册 ----------------
def test_writeback(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        app._roster_live = True                # run() 完成后的会话态
        pa = app.add_pet("species.cockroach")
        file_seq = [e["species"] for e in _read_raw(_roster_file(tmp))]
        record("wb.add_writeback",
               file_seq == ["species.cockroach"],
               f"add_pet 后 pets.json = 可见名册: {file_seq}")

        pb = app.add_pet("species.fruitfly")
        file_seq = [e["species"] for e in _read_raw(_roster_file(tmp))]
        record("wb.add_second", file_seq == ["species.cockroach",
                                             "species.fruitfly"],
               f"再加一只: {file_seq}")

        app.hide_pet(pb)
        file_seq = [e["species"] for e in _read_raw(_roster_file(tmp))]
        in_session = pb in (appmod._SESSION_FILE.read_text("utf-8")
                            if appmod._SESSION_FILE.exists() else "")
        record("wb.hide_leaves_roster",
               file_seq == ["species.cockroach"] and in_session,
               f"隐藏宠退出名册 {file_seq} 且进 session.json(独立机制)")
        record("wb.roster_matches_visible",
               _read_raw(_roster_file(tmp)) == app._visible_roster(),
               "文件内容逐字节口径 == 当前可见名册快照")

        app.recall_pet(pb)
        file_seq = [e["species"] for e in _read_raw(_roster_file(tmp))]
        record("wb.recall_back", file_seq == ["species.cockroach",
                                              "species.fruitfly"],
               f"召回宠回归名册: {file_seq}")

        app.remove_pet(pb)
        file_seq = [e["species"] for e in _read_raw(_roster_file(tmp))]
        record("wb.remove_writeback", file_seq == ["species.cockroach"],
               f"remove_pet 后名册: {file_seq}")

        app.remove_pet(pa)                 # 移除最后一只可见宠
        record("wb.remove_last", _read_raw(_roster_file(tmp)) == [],
               "移除全部可见宠:名册为空列表(下次启动零宠,不再凭空生成)")
    finally:
        _teardown(app, appmod, orig)


# ---------------- ⑤ max_pets 上限:超限条目忽略,不崩 ----------------
def test_max_pets_cap(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        app.cfg.max_pets = 1
        _write_raw(_roster_file(tmp), [
            {"species": "species.cockroach"},
            {"species": "species.fruitfly"},
        ])
        app._load_startup_roster()
        species_seq = [h.state.species_id for h in app.pets.values()]
        record("cap.roster_capped", species_seq == ["species.cockroach"],
               f"max_pets=1:仅载入第 1 条,多余忽略: {species_seq}")
        full = False
        try:
            app.add_pet("species.fruitfly")
        except RuntimeError:
            full = True
        record("cap.add_refused", full and len(app.pets) == 1,
               "名额满时 add_pet 仍拒绝(add_pet 语义不变)")
        # 载入期不回写:被忽略条目保留(名额放宽后重启即可生效)
        record("cap.entry_preserved",
               len(_read_raw(_roster_file(tmp))) == 2,
               "超限条目保留在文件(不因上限被抹掉)")
    finally:
        _teardown(app, appmod, orig)


# ---------------- ⑥ 坏档不崩:坏 JSON/非清单 dict → 缺省,不覆盖原文件 ----------------
def test_bad_file_no_crash(tmp: Path) -> None:
    """收尾波补判据⑥:防御代码在 load_roster 内(坏档→缺省不覆盖),此处钉死。"""
    from neuropet.core.config import default_roster, load_roster

    path = _roster_file(tmp)
    path.parent.mkdir(parents=True, exist_ok=True)
    bad_json = "{ this is not json ]"
    path.write_text(bad_json, "utf-8")
    entries = load_roster(path)                    # 不抛异常 = 不崩
    record("badfile.bad_json",
           entries == default_roster()
           and path.read_text("utf-8") == bad_json,
           f"坏 JSON → 返回缺省名册({len(entries)} 条)不崩,原文件逐字节"
           "保留(留现场供排查,不覆盖)")

    non_list = '{"species": "species.cockroach"}'  # 合法 JSON 但非清单
    path.write_text(non_list, "utf-8")
    entries2 = load_roster(path)
    record("badfile.non_list_dict",
           entries2 == [] and path.read_text("utf-8") == non_list,
           "合法 JSON 但非清单(dict)→ 空名册不崩,原文件保留不被覆盖")


# ---------------- ⑦⑧⑨ r25:名册 ↔ pet_id ----------------
def _n_profile_dirs(tmp: Path) -> int:
    """profiles/ 下的档案目录数(真实 data/profiles 的临时同路径替身)。

    目录由 ``profile_dir()`` 的 ``mkdir`` 产生 —— 孤儿目录正是这么攒出来的,
    故计数即"是否产生新孤儿"的直接观测量。"""
    root = tmp / "profiles"
    return len([d for d in root.iterdir() if d.is_dir()]) if root.exists() else 0


def _brain_readout(h) -> dict:
    """脑状态读数:用脑自己的 ``save()``(app 落盘的同一份口径)。"""
    d = h.brain.save()
    return {"level": d.get("level"), "trust": d.get("trust"),
            "age_s": d.get("age_s"), "n_fed": d.get("n_fed"),
            "n_mem": len(d.get("episodes", []))}


def test_restart_fidelity(tmp: Path) -> None:
    """⑦ 重启保真:**真落盘 → 新 App 实例 → 真名册载入**(无任何手工注入)。

    状态构造:等级经 ``add_pet(intelligence=4)``(add_pet 的既有参数);
    信任/记忆/投喂数经 ``brain.on_event("fed", ...)``——这正是 ``app._try_eat``
    唯一的那一句,即真实投喂路径的同一调用;年龄 ``state.age_s`` 真实路径由
    sim 逐帧累计,此处直接构造(落盘/载入走真实 memory.json 路径)。
    载入侧**不做任何注入**:只调 ``_load_startup_roster()``(run() 的第一句)。
    """
    app, appmod, orig = _make_app(tmp)
    try:
        app._roster_live = True                     # run() 置位后的会话态
        pid = app.add_pet("species.cockroach", intelligence=4)
        h = app.pets[pid]
        for _ in range(3):
            h.brain.on_event("fed", {"food_kind": "leaf"})
        h.state.age_s = 1234.0
        app.save_all()                              # 真实落盘(shutdown 同路径)
        before = _brain_readout(h)
        roster_before = _read_raw(_roster_file(tmp))
        path_before = str(appmod.profile_dir(pid))
    finally:
        _teardown(app, appmod, orig)

    record("fidelity.roster_carries_pet_id",
           [e.get("pet_id") for e in roster_before] == [pid],
           f"名册落盘带 pet_id(修复前只有 species): {roster_before}")

    app2, appmod2, orig2 = _make_app(tmp)           # 新实例 = 重启
    try:
        app2.cfg.intelligence = 2      # 基线压到 2:否则基线=5 会 max() 掩盖档案等级
        app2._load_startup_roster()                 # 真读同一份 pets.json
        ids = list(app2.pets)
        h2 = app2.pets.get(pid)
        path_after = str(appmod2.profile_dir(pid)) if h2 else None
        record("fidelity.same_profile_dir",
               h2 is not None and ids == [pid] and path_after == path_before,
               f"pet_id 复用={ids == [pid]};档案目录 {'不变' if path_after == path_before else '变了'}"
               f"(前 {path_before} / 后 {path_after})")
        after = _brain_readout(h2) if h2 else {}
        same = {k: (before[k], after.get(k)) for k in before}
        record("fidelity.brain_state_survives",
               h2 is not None and after == before and before["n_mem"] == 3
               and before["n_fed"] == 3 and before["age_s"] == 1234.0,
               f"重启后脑状态逐键一致(等级/信任/年龄/记忆/投喂): {same} "
               f"(出厂态=0 条记忆/0 次投喂/年龄 0 → 一致只可能来自档案)")
    finally:
        _teardown(app2, appmod2, orig2)


def test_legacy_roster_without_pet_id(tmp: Path) -> None:
    """⑧ 向后兼容:无 pet_id 的旧名册 → 发新 id、不崩;旧孤儿档案不追认。"""
    app, appmod, orig = _make_app(tmp)
    try:
        orphan = tmp / "profiles" / "species.cockroach-dead00"   # 旧版遗留孤儿档案
        orphan.mkdir(parents=True, exist_ok=True)
        (orphan / "memory.json").write_text(json.dumps(
            {"brain": "brain.roach", "version": 1, "level": 3, "trust": 0.99,
             "age_s": 9999.0, "n_fed": 7,
             "episodes": [{"ts": 0.0, "summary": "旧档记忆", "salience": 0.9}]}),
            "utf-8")
        _write_raw(_roster_file(tmp), [{"species": "species.cockroach"}])
        n_before = _n_profile_dirs(tmp)
        app._load_startup_roster()                  # 不抛异常 = 不崩
        ids = list(app.pets)
        got = _brain_readout(app.pets[ids[0]]) if ids else {}
        # 出厂态:cfg 基线等级 + 0 记忆 + 0 投喂(旧档的 0.99 信任/9999 岁/7 次投喂没被追认)
        record("compat.legacy_entry_gets_new_id",
               len(ids) == 1 and ids[0].startswith("species.cockroach-")
               and ids[0] != "species.cockroach-dead00",
               f"旧条目(无 pet_id)不崩且发新 id: {ids}(旧档 dead00 不被冒名)")
        record("compat.orphan_not_adopted",
               got.get("n_mem") == 0 and got.get("n_fed") == 0
               and got.get("trust") == 0.2 and got.get("age_s") == 0.0,
               f"旧孤儿档案不追认(无法匹配 → 一次性损失): 新宠读数 {got}")
        record("compat.new_dir_only_for_new_pet",
               _n_profile_dirs(tmp) == n_before + 1,
               f"新宠建自己的新档案目录({n_before}→{_n_profile_dirs(tmp)}),旧孤儿原样留存")
    finally:
        _teardown(app, appmod, orig)


def test_restarts_create_no_new_profile_dirs(tmp: Path) -> None:
    """⑨ 零新孤儿:同一名册连续重启 2 次,profiles/ 下**不新增目录**。

    修复前:每次重启 add_pet 发新 uuid → profile_dir 每次 mkdir 一个**新**
    目录 → 目录数 1→2→3(本机实测累积出 2284 个)。修复后三次读数应恒等。
    """
    ids, counts = [], []
    for gen in range(3):
        app, appmod, orig = _make_app(tmp)
        try:
            if gen == 0:
                app._roster_live = True
                app.add_pet("species.cockroach")      # 建宠 + 名册真回写
            else:
                app._load_startup_roster()            # 重启:读同一份名册
            ids.append(list(app.pets))
            app.save_all()
            counts.append(_n_profile_dirs(tmp))
        finally:
            _teardown(app, appmod, orig)
    record("no_orphan.restarts_reuse_profile_dir",
           counts[0] == counts[1] == counts[2] and ids[0] == ids[1] == ids[2]
           and len(ids[0]) == 1,
           f"建宠 + 重启×2:profiles/ 目录数 {counts}(修复前 1→2→3),pet_id {ids}")


# ---------------- 防污染闸门:未 run() 不落盘(真实 data/ 零写入) ----------------
def test_not_live_no_pollution(tmp: Path) -> None:
    import hashlib
    import neuropet.core.config as cfgmod

    real = cfgmod.PETS_PATH
    before = hashlib.sha1(real.read_bytes()).hexdigest() if real.exists() else None
    app, appmod, orig = _make_app(tmp)
    try:
        app.add_pet("species.cockroach")       # _roster_live=False:不得写盘
        after = (hashlib.sha1(real.read_bytes()).hexdigest()
                 if real.exists() else None)
        record("gate.no_write_before_run", before == after,
               "未 run() 的 App 实例增删不落盘(测试/离线脚本防污染闸门)")
    finally:
        _teardown(app, appmod, orig)


# ============================================================ 主流程
# r24:原 test_fly_model_dispatch / test_fly_vs_roach_pixels 为 3D 管线专属
# (model3d.mesh_pipeline / render3d_pipeline),随 3D 剔除退役;跨物种渲染
# 分派的 2D 口径由 tests/test_species_render_dispatch.py 覆盖。
def main() -> None:
    with tempfile.TemporaryDirectory(prefix="neuropet_roster_") as td:
        tmp = Path(td)
        test_default_generation(tmp)
        test_roster_read(tmp)
        test_unknown_species_skipped(tmp)
        test_writeback(tmp)
        test_max_pets_cap(tmp)
        test_bad_file_no_crash(tmp)
        test_restart_fidelity(tmp)                 # r25 ⑦⑧⑨:名册 ↔ pet_id
        test_legacy_roster_without_pet_id(tmp)
        test_restarts_create_no_new_profile_dirs(tmp)
        test_not_live_no_pollution(tmp)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[启动名册+果蝇核查] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, d in failed:
            print(f"  FAIL: {tag} — {d}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
