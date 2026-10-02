"""r17 Task H 验收:species_id 贯通——3d 果蝇错挂根治(活体路径回归)。

根因(用户两轮报障,第十五轮误判为观感问题,评审记录在案):
  App._render_traits 从不写 species_id;render_pose_3d 以
  traits.get("species_id", "species.cockroach") 静默缺省 → 活体果蝇
  一直被渲染成蟑螂(240² 蟑螂图,scratch/_r17_livepath_fly_as_default.png)。
  hybrid 不受影响(按色板键取色,不需要 species_id)。
  此前离线测试全绿因测试自拼了带 species_id 的 traits——活体路径零覆盖
  (踩坑 #13 再度应验),本文件即补该覆盖。

判据:
  ① traits 携带物种键:_render_traits 产物含 species_id 且与实例一致
     (蟑/蝇各验一次);
  ② 真路径分派:App 构造 traits → render_pose 真实全链(speed=0 静态),
     果蝇出 120² 且与 baker_for("fruitfly") 参考逐位一致;蟑螂出 240²。
     修复前此判据红(果蝇请求 → 240² 蟑螂 = 用户所见);
  ③ 两物种互异:同法渲染像素差 >40%(参照系:同物种 yaw30° 底噪 14.8%)。

隔离纪律:名册/profile/会话全部打入临时目录(与 tests/test_roster_config.py
同惯例),不污染仓库真实 data/。
运行:python tests/test_species_render_dispatch.py
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


def _make_app(tmp: Path):
    import neuropet.core.app as appmod

    appmod._SESSION_FILE = tmp / "session.json"
    orig_profile = appmod.profile_dir

    def _tmp_profile(pet_id: str) -> Path:
        d = tmp / "profiles" / pet_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tmp_profile
    app = appmod.App()
    app._pets_path = tmp / "pets.json"
    return app, appmod, orig_profile


def _teardown(app, appmod, orig_profile) -> None:
    app.root.destroy()
    appmod.profile_dir = orig_profile


def _static_pose() -> dict:
    return {"half": 120, "segments": [(0, 0, 0.0)], "speed_norm": 0.0}


def main() -> None:
    import os
    from neuropet.render.renderer import render_pose

    old_mode = os.environ.get("NEUROPET_RENDER")
    os.environ["NEUROPET_RENDER"] = "hybrid"   # 被测路径;_render_mode 每次读 env
    try:
        _run(render_pose)
    finally:
        if old_mode is None:
            os.environ.pop("NEUROPET_RENDER", None)
        else:
            os.environ["NEUROPET_RENDER"] = old_mode
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"[物种分派回归] {n_pass}/{len(RESULTS)} 项通过")
    sys.exit(0 if n_pass == len(RESULTS) else 1)


def _run(render_pose) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        app, appmod, orig_profile = _make_app(tmp)
        try:
            ids = {
                "fly": app.add_pet("species.fruitfly"),
                "roach": app.add_pet("species.cockroach"),
            }

            # ① traits 携带物种键(源头注入)
            ok1, det1 = True, []
            for short, pid in ids.items():
                h = app.pets[pid]
                traits = app._render_traits(h)
                sid = traits.get("species_id")
                want = h.state.species_id
                ok1 &= sid == want
                det1.append(f"{short}={sid!r}")
            record("sd.traits_carry_species", ok1,
                   f"_render_traits 产物 species_id: {', '.join(det1)}")

            # ② 真路径分派(App traits → render_pose 全链;r18 画布口径:
            #    half·body_len/115,蟑螂 240²;果蝇 r19 R2 体长 30→40 → 83²;
            #    r18 修复前果蝇请求=240²蟑螂)
            import numpy as np
            ok2, det2 = True, []
            refs = {}
            for short, pid in ids.items():
                traits = app._render_traits(app.pets[pid])
                img = render_pose(_static_pose(), traits)
                want_canvas = 240
                nz = int((np.asarray(img.convert("RGBA"))[..., 3] > 0).sum())
                same = (img.size == (want_canvas, want_canvas) and nz > 100)
                ok2 &= same
                det2.append(f"{short}={img.size}/{nz}px"
                            + ("" if same else f"(≠{want_canvas}²口径)"))
                refs[short] = img
            record("sd.live_dispatch", ok2,
                   f"真路径渲染: {', '.join(det2)}(画布={want_canvas}²)")

            # ③ 两物种互异(非键色像素差 >40%)
            import numpy as np
            a = np.asarray(refs["fly"].convert("RGBA"), dtype=np.int16)
            b = np.asarray(refs["roach"].convert("RGBA"), dtype=np.int16)
            fly_px = int((a[..., 3] > 0).sum())
            roach_px = int((b[..., 3] > 0).sum())
            diff_ratio = float(np.mean(np.any(a[..., 3] != b[..., 3], axis=0)
                                       | np.any(np.abs(a[..., :3] - b[..., :3]).max(axis=-1) > 8,
                                                axis=0))) if a.shape == b.shape else 1.0
            record("sd.species_distinct", diff_ratio > 0.4,
                   f"像素差 {diff_ratio:.1%} >40%(非键色 蝇{fly_px}/蟑{roach_px}px)")

        finally:
            _teardown(app, appmod, orig_profile)


if __name__ == "__main__":
    main()
