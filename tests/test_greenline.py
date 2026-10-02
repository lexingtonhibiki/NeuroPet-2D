# -*- coding: utf-8 -*-
"""C4 绿线护栏:numpy 永不进 exe(「零第三方运行时依赖」从约定变可判)。

运行:python tests/test_greenline.py

背景(r25 架构 §1.6):`neuropet/body/gait_sim.py` 住在生产包内却在模块顶层硬
`import numpy as np` —— 生产路径今天不 import 它,exe 里因此恰好没有 numpy,
但任何一处新增 import 或打包器收集策略变化就会把 numpy(约 31MB)卷进 exe,
打破「单文件、零第三方运行时依赖、约 32MB」这条卖点。本套件把那扇门锁上。

判据(任务书 C4,三条):
  ⓪ 拦截器自证:`sys.meta_path` 插入后 `import numpy` 确实抛 ImportError
     (否则整套判据会退化成恒真);
  ① 子进程里拦截 numpy / numpy.* 后:`import neuropet.core.app` + 构造 App
     (不跑 mainloop)+ `render_pose` 走一帧 全部成功,且 `"numpy" not in
     sys.modules`;
  ② 同一子进程里 `import neuropet.body.gait_sim` 成功(训练模块在无 numpy 环境
     下公开符号面完整:名/签名可导入,签名可内省),且导入后 numpy **仍未**
     被拉进 `sys.modules`(= 懒加载,不是"导入了但没人用");
  ③ `build_exe.bat` **有效行**(逐行判,跳过 `rem` / `::` 注释行)含
     `--exclude-module numpy`(打包层兜底);
  ④ 同子进程里**整包穷举**:文件系统枚举 `neuropet/**/*.py` 逐个 import,
     失败数 == 0、模块数与文件系统枚举数一致、numpy 仍未进 `sys.modules`,
     并打印实际模块数(今天 51)让数量变化在输出里可见;
  ⑤ **静态 AST**:扫生产代码全部 `import numpy` / `from numpy import ...`
     语句(含**函数体内的延迟导入**),白名单外一个都不许有。

判据 ③ 与 ⑤ 的来历(r25 C4 黑盒验收 r25-test-C4.md 报出的两条真实漏报):
  · ③ 原为**纯子串匹配** `"--exclude-module numpy" in bat`:把那行改成
    `rem --exclude-module numpy ^`(注释掉)后判据**仍 PASS** —— 而 bat 的
    排除是打包层**唯一**的锁(PyInstaller 的 modulegraph 扫字节码,看得见
    `_LazyNumpy.__getattr__` 里那句 `import numpy`,所以"懒加载"本身挡不住
    它进 exe)。门没锁而检查说锁了 = 假绿,故改为只认有效行,并加负向对照
    自证"注释掉即变红"。
  · ⑤ 原四条判据只覆盖**导入期**:①④ 走的是导入路径,函数体里的延迟
    `import numpy`(如 drag/tray/panel 路径)不触发就全绿绕过。静态扫描零
    运行成本、正好补上即时性;白名单**按 文件+所在函数 精确放行**(仅
    `gait_sim._LazyNumpy.__getattr__`),不按文件一刀切 —— 后者等于没锁。

判据 ④ 的来历(为什么不是"①已经够了"):
  · ① 只覆盖 `neuropet.core.app` **一条**导入路径;而绿线卖点是**整包**
    属性(「零第三方运行时依赖」需要 `neuropet/` 下每个模块在无 numpy 时都
    可导入),单路径通过 ≠ 整包保证。
  · 第一次尝试用 `pkgutil.walk_packages` 枚举,**只拿到 11 个**模块:
    `walk_packages` 遇到导入失败的包会**静默跳过且不再向下遍历**,
    于是"缺模块"会伪装成"模块少",判据退化成空转(覆盖面虚高)。
    → 改用**文件系统枚举**(`Path.rglob('*.py')`,`__init__.py` 归包名),
    预期间隔由测试自身算好并通过环境变量传给子进程,两边数量对不上即红。

对应改动:`neuropet/body/gait_sim.py`(顶层 import → 首次使用才加载)、
`build_exe.bat`(排除 numpy)。全程离线、无窗口主循环、无第三方依赖。
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BAT = ROOT / "build_exe.bat"

# 静态判据(⑤)扫描面 = 真正会被打包进 exe 的生产代码:包内全部 .py + 根入口
# `NeuroPet.py`(build_exe.bat 末行那个文件)。tests/、scratch/ 不在其列
# (它们本就大量用 numpy,扫进来只会产生噪音)。
STATIC_ROOTS = (ROOT / "neuropet", ROOT / "NeuroPet.py")

# numpy 导入白名单:**文件 + 所在函数**精确放行,不按文件一刀切 —— 只放行
# gait_sim.py 的 `_LazyNumpy.__getattr__` 那一处(懒加载代理)。同一文件里模块级
# 或别的函数再来一句 `import numpy` 必须照报,否则判据等于没锁(把白名单放宽到
# 文件粒度就是"把判据改死",⑤ 的白名单粒度自证项专门防这一手)。
NUMPY_WHITELIST = {("neuropet/body/gait_sim.py", "_LazyNumpy.__getattr__")}

# bat 注释行:`rem ...` / `:: ...`(含 `@rem` 前缀,大小写不敏感)。判据 ③ 必须
# 只看**有效行**:纯子串匹配下 `rem --exclude-module numpy ^` 也能骗过判据
# (r25 C4 黑盒实测漏报),而那行是打包层唯一的锁。
_BAT_COMMENT_RE = re.compile(r"^\s*@?\s*(?:rem(?:\s|$)|::)", re.IGNORECASE)

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ===================================================================
# 子进程载荷:拦截器必须在任何 neuropet 导入之前装好
# ===================================================================
CHILD = r'''
import inspect, json, os, pathlib, sys, tempfile

root = os.environ["NEUROPET_GREENLINE_ROOT"]
sys.path.insert(0, root)

res = {"blocker_works": None, "numpy_at_start": None, "app_ok": None,
       "render_ok": None, "render_detail": "", "numpy_after_render": None,
       "gait_sim_ok": None, "gait_sim_detail": "", "numpy_after_gait_sim": None,
       "sweep_files": None, "sweep_mods": None, "sweep_expected": None,
       "sweep_failed": None, "sweep_bad": [], "sweep_missing": [],
       "numpy_after_sweep": None,
       "err": None}
out = []


class _NumpyBlocker:
    """绿线拦截器:numpy / numpy.* 一律抛 ImportError。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "numpy" or fullname.startswith("numpy."):
            raise ImportError("greenline: numpy 被绿线拦截器阻断")
        return None


sys.meta_path.insert(0, _NumpyBlocker())

try:
    # --- ⓪ 拦截器自证:不真的拦住,numpy 已经在 sys.modules 里,后面全恒真 ---
    try:
        import numpy            # noqa: F401
        res["blocker_works"] = False
    except ImportError:
        res["blocker_works"] = True
    res["numpy_at_start"] = "numpy" in sys.modules

    # --- ① 生产路径:导入图 → App 构造 → 真实渲染一帧 ---
    import neuropet.core.app as appmod
    from neuropet.render.renderer import render_pose

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="greenline_"))
    appmod._SESSION_FILE = tmp / "session.json"

    def _tmp_profile(pet_id):
        d = tmp / "profiles" / pet_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tmp_profile          # 名册/profile 全落临时目录
    app = appmod.App()                         # 不跑 mainloop
    app._pets_path = tmp / "pets.json"
    app.add_pet("species.cockroach")
    h = next(iter(app.pets.values()))
    sp = app.species(h.state.species_id)
    traits = dict(sp.render_traits())
    traits["species_id"] = h.state.species_id  # 与 app._render_traits 同键
    traits["scale"] = float(h.scale)
    img = render_pose(h.body.pose(), traits)
    a = img.convert("RGBA").getchannel("A")
    lit = sum(1 for v in a.getdata() if v > 0)
    res["app_ok"] = True
    res["render_ok"] = img.size == (240, 240) and lit > 0
    res["render_detail"] = (f"App+1 帧 {img.size} {img.mode} 非透明像素={lit}"
                            f"(宠 {len(app.pets)} 只)")
    res["numpy_after_render"] = "numpy" in sys.modules
    app.shutdown()

    # --- ② 同子进程:训练模块符号面(无 numpy 环境) ---
    import neuropet.body.gait_sim as gs
    need = ("SpeciesGeometry", "RolloutMetrics", "Trace", "param_spec",
            "decode_theta", "encode_theta", "default_theta", "theta_to_params",
            "resolve_gait_constants", "rollout_batch", "fitness_from_metrics",
            "evaluate_population", "evaluate_params", "species_geometry",
            "species_params", "preset_speeds", "params_to_pv", "project_params",
            "project_pv", "consistency_max_error", "closed_loop_metrics",
            "THETA_KEYS", "N_DIM", "SPEED_PLAN", "TRAIN_SEEDS", "EVAL_SEEDS",
            "EVAL_FRAMES", "EVAL_WARMUP")
    missing = [n for n in need if not hasattr(gs, n)]
    sigs = []
    for n in ("rollout_batch", "evaluate_population", "evaluate_params",
              "decode_theta", "params_to_pv", "species_geometry"):
        try:
            sigs.append(str(inspect.signature(getattr(gs, n))))
        except Exception as exc:                       # 签名面破损
            missing.append(f"{n}(signature: {type(exc).__name__})")
    res["gait_sim_ok"] = not missing
    res["gait_sim_detail"] = (f"符号 {len(need)}/{len(need)} 齐,签名可内省 "
                              f"{len(sigs)} 项,np 代理={type(gs.np).__name__}"
                              + (f";缺 {missing}" if missing else ""))
    res["numpy_after_gait_sim"] = "numpy" in sys.modules
    # 真调用训练数值函数:必须抛"缺 numpy"的明确 ImportError(而不是
    # AttributeError: 'NoneType'...),证明懒加载是"用到才要",错误可读。
    try:
        gs.default_theta("cockroach")
        res["lazy_call_err"] = "未抛错(不合预期)"
    except ImportError as exc:
        res["lazy_call_err"] = ("ImportError:" +
                                ("含 pip 指引" if "pip install numpy" in str(exc)
                                 else f"信息含糊 {exc}"))
    except Exception as exc:
        res["lazy_call_err"] = f"{type(exc).__name__}: {exc}"
    res["numpy_after_call"] = "numpy" in sys.modules

    # --- ④ 整包穷举:文件系统枚举(不用 pkgutil.walk_packages —— 它遇导入
    #        失败的包会静默跳过且不再向下遍历,实测只剩 11 个模块) ---
    import importlib
    pkg = pathlib.Path(root) / "neuropet"
    files = sorted(pkg.rglob("*.py"))
    mods = []
    for p in files:
        rel = p.relative_to(pkg.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":        # __init__.py 归到包名
            parts = parts[:-1]
        if parts:
            mods.append(".".join(parts))
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as exc:           # 逐个留痕,便于定位
            bad.append(f"{m}: {type(exc).__name__}: {exc}")
    # 枚举自身不得被静默缩小:哨兵模块必须在枚举结果里
    sentinels = ("neuropet.core.app", "neuropet.body.gait_sim",
                 "neuropet.render.renderer", "neuropet.ui.panel",
                 "neuropet.brain.roach_brain")
    res["sweep_files"] = len(files)
    res["sweep_mods"] = len(set(mods))
    res["sweep_expected"] = int(os.environ.get("NEUROPET_GREENLINE_NMODS", "-1"))
    res["sweep_failed"] = len(bad)
    res["sweep_bad"] = bad[:10]
    res["sweep_missing"] = [m for m in sentinels if m not in set(mods)]
    res["numpy_after_sweep"] = "numpy" in sys.modules
except Exception as exc:                               # 任何一步失败都要留痕
    import traceback
    res["err"] = f"{type(exc).__name__}: {exc}"
    out.append(traceback.format_exc()[-600:])

for ln in out:
    sys.stderr.write(ln)
print("@@" + json.dumps(res, ensure_ascii=False))
sys.exit(0 if res["err"] is None else 1)
'''


def _enumerate_modules(pkg: Path) -> list[str]:
    """文件系统枚举包内全部模块(`__init__.py` 归到包名)。

    **不要**换成 `pkgutil.walk_packages`:它遇到导入失败的包会静默跳过且
    不再向下遍历(实测只拿到 11/51),枚举缩水会被误读成"覆盖面变小",
    判据随即退化成空转。
    """
    mods = []
    for p in sorted(pkg.rglob("*.py")):
        parts = list(p.relative_to(pkg.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mods.append(".".join(parts))
    return mods


def _effective_bat_lines(bat: str) -> list[str]:
    """bat 里真正会被 cmd 执行的行(去空行、去 `rem` / `::` 注释行)。"""
    return [ln for ln in bat.splitlines()
            if ln.strip() and not _BAT_COMMENT_RE.match(ln)]


def _exclude_numpy_lines(bat: str) -> list[str]:
    """有效行里的 `--exclude-module numpy`(判据 ③ 的唯一口径)。

    注释行不算数:被 `rem` 掉的那行对 PyInstaller 零作用,而 numpy 能不能
    进 exe 全看这个开关,所以"命中一处注释"必须判红。
    """
    return [ln for ln in _effective_bat_lines(bat)
            if "--exclude-module numpy" in ln]


def _collect_numpy_imports(node: ast.AST, rel: str, stack: tuple[str, ...],
                           hits: list[str], allowed: list[str]) -> None:
    """递归下降,边下边记限定函数名(白名单精确匹配 + 报错定位都用它)。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect_numpy_imports(child, rel, stack + (child.name,), hits, allowed)
            continue
        if isinstance(child, ast.Import):
            mods = [a.name for a in child.names]
        elif isinstance(child, ast.ImportFrom):
            mods = [child.module or ""]
        else:
            mods = []
        for m in mods:
            if m != "numpy" and not m.startswith("numpy."):
                continue
            where = ".".join(stack) or "<模块级>"
            (allowed if (rel, where) in NUMPY_WHITELIST else hits).append(
                f"{rel}:{child.lineno}: {where} · {ast.unparse(child).strip()}")
        _collect_numpy_imports(child, rel, stack, hits, allowed)


def _scan_numpy_imports() -> tuple[list[str], list[str], list[str], int]:
    """AST 扫生产代码,返回 (白名单外命中, 白名单命中, 无法解析的文件, 文件数)。

    覆盖**导入期之外**的面:函数体内的延迟 `import numpy` 不经过任何导入路径,
    ①④ 走不到那里,只有静态扫描逮得住。`allowed` 非空同时是"扫描确实钻进
    了嵌套函数体"的自证 —— 若哪天扫描退化成只看模块级,白名单就再也匹配
    不上,该自证项立刻变红,不会让 ⑤ 静默变成恒真。
    """
    hits: list[str] = []
    allowed: list[str] = []
    broken: list[str] = []
    targets: list[Path] = []
    for r in STATIC_ROOTS:
        targets.extend(sorted(r.rglob("*.py")) if r.is_dir() else [r])
    for p in targets:
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:      # 解析不了 = 无法保证"没有隐藏导入"
            broken.append(f"{rel}:{exc.lineno}: SyntaxError: {exc.msg}")
            continue
        _collect_numpy_imports(tree, rel, (), hits, allowed)
    return hits, allowed, broken, len(targets)


def _run_child() -> dict:
    env = dict(os.environ)
    env["NEUROPET_GREENLINE_ROOT"] = str(ROOT)
    env["NEUROPET_RENDER"] = "hybrid"      # 固定生产管线,不继承外部残留
    # 预期间隔:numpy 拦截下应能导入的模块数(父子两侧各自枚举,对不上即红)
    env["NEUROPET_GREENLINE_NMODS"] = str(len(_enumerate_modules(ROOT / "neuropet")))
    env.pop("PYTHONPATH", None)
    r = subprocess.run([sys.executable, "-c", CHILD], env=env,
                       capture_output=True, text=True, timeout=300,
                       cwd=str(ROOT))
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("@@")), None)
    if line is None:
        return {"err": f"子进程无结果(rc={r.returncode}):"
                       f"{(r.stderr or r.stdout)[-400:]}"}
    return json.loads(line[2:])


def main() -> None:
    res = _run_child()
    err = res.get("err")
    n_files = len(list((ROOT / "neuropet").rglob("*.py")))
    expect_mods = len(_enumerate_modules(ROOT / "neuropet"))

    record("⓪ 拦截器自证(numpy 导入确实抛 ImportError,判据不退化成恒真)",
           res.get("blocker_works") is True and res.get("numpy_at_start") is False,
           f"blocker={res.get('blocker_works')} numpy_at_start="
           f"{res.get('numpy_at_start')}" + (f";err={err}" if err else ""))

    record("① 无 numpy 子进程:import app + App() + render_pose 一帧 全成功",
           res.get("app_ok") is True and res.get("render_ok") is True,
           str(res.get("render_detail") or err))

    record("① 渲染后 \"numpy\" not in sys.modules",
           res.get("numpy_after_render") is False,
           f"numpy_in_modules={res.get('numpy_after_render')}")

    record("② 同子进程 import neuropet.body.gait_sim 成功(符号面/签名完整)",
           res.get("gait_sim_ok") is True, str(res.get("gait_sim_detail") or err))

    record("② 导入 gait_sim 后 numpy 仍未被拉进 sys.modules(懒加载)",
           res.get("numpy_after_gait_sim") is False,
           f"numpy_in_modules={res.get('numpy_after_gait_sim')}")

    record("② 真调用训练数值函数才报错,且错误信息明确(非 NoneType 崩)",
           str(res.get("lazy_call_err", "")).startswith("ImportError:含 pip 指引")
           and res.get("numpy_after_call") is False,
           f"{res.get('lazy_call_err')}")

    # --- ④ 整包穷举(未拦截时 51/51 可导入;拦截 numpy 后必须仍是 51/51) ---
    bad = res.get("sweep_bad") or []
    record("④ 无 numpy 子进程:穷举 import neuropet/ 下每个 .py 模块,失败数 == 0",
           res.get("sweep_failed") == 0 and res.get("sweep_mods") is not None,
           f"枚举 {res.get('sweep_mods')} 个模块,失败 {res.get('sweep_failed')} 个"
           + ("".join(f"\n      · {b}" for b in bad) if bad else "")
           + (f";err={err}" if err else ""))

    record("④ 覆盖面无虚高:两边枚举数一致(=文件数)且哨兵模块都在",
           res.get("sweep_mods") == expect_mods == res.get("sweep_expected")
           and res.get("sweep_files") == n_files and not res.get("sweep_missing"),
           f"子进程 {res.get('sweep_mods')} / 父进程 {expect_mods} / 文件 {n_files} "
           f"(子进程见 {res.get('sweep_files')} 文件);缺哨兵="
           f"{res.get('sweep_missing') or '无'}")

    record("④ 穷举后 \"numpy\" not in sys.modules",
           res.get("numpy_after_sweep") is False,
           f"numpy_in_modules={res.get('numpy_after_sweep')}")

    print(f"[绿线护栏·整包枚举] 模块数={expect_mods}(无 numpy 子进程内实测 "
          f"{res.get('sweep_mods')}),失败={res.get('sweep_failed')} 个 "
          f"—— 数量与上次不同即说明 neuropet/ 增删了模块,请核对覆盖面")

    bat = BAT.read_text(encoding="utf-8", errors="replace")
    hits = _exclude_numpy_lines(bat)
    record("③ build_exe.bat 有效行(非 rem/::)含 --exclude-module numpy",
           bool(hits),
           f"{BAT.name} {len(bat)}B,有效行命中={len(hits)} 处"
           + (f" · {hits[0].strip()}" if hits else " · 无有效命中(=门没锁)"))

    # ③ 的负向对照(判据自证,不改磁盘):把命中行逐行 `rem` 掉 → 新判据必须
    # 变红,而**旧的裸子串匹配仍会 PASS**(它只看 `in bat`)。两者对照即证明
    # ③ 已具备鉴别力、不再被注释行骗过。
    muted = "\n".join(("rem " + ln) if ln in hits else ln
                      for ln in bat.splitlines())
    record("③ 负向对照:该行注释掉后 ③ 变红(裸子串匹配则会漏报)",
           bool(hits) and not _exclude_numpy_lines(muted)
           and "--exclude-module numpy" in muted,
           f"注释掉 {len(hits)} 行 → 有效命中 0 处,而裸子串仍命中 "
           f"{muted.count('--exclude-module numpy')} 处"
           f"(旧判据在此 PASS = 假绿来源)")

    # --- ⑤ 静态 AST:函数体内的延迟 import numpy 也拦得住 ---
    ast_hits, ast_allowed, ast_broken, ast_files = _scan_numpy_imports()
    record("⑤ 静态 AST:生产代码无白名单外的 import numpy(含函数体内延迟导入)",
           not ast_hits and not ast_broken and bool(ast_allowed),
           f"扫 {ast_files} 文件,白名单外命中 {len(ast_hits)} 处;白名单放行 "
           f"{len(ast_allowed)} 处(=扫描确实钻进了嵌套函数体);无法解析 "
           f"{len(ast_broken)} 个"
           + "".join(f"\n      · {h}" for h in ast_hits + ast_broken))

    # ⑤ 的白名单粒度自证:同一文件里**模块级/别的函数**再插 numpy 必须照报 ——
    # 谁把白名单放宽成"按文件"来消红,这一项会当场变红。
    probe_hits: list[str] = []
    probe_allowed: list[str] = []
    _collect_numpy_imports(
        ast.parse("import numpy as np\n\n\ndef other():\n    import numpy\n"),
        "neuropet/body/gait_sim.py", (), probe_hits, probe_allowed)
    record("⑤ 白名单按 文件+函数 放行(gait_sim 别处再插 numpy 仍报)",
           len(probe_hits) == 2 and not probe_allowed,
           f"探针:gait_sim.py 模块级 + other() 内各插 1 处 → 报出 "
           f"{len(probe_hits)} 处 / 放行 {len(probe_allowed)} 处"
           + ("".join(f"\n      · {h}" for h in probe_hits) or ""))

    failed = [t for t, ok, _ in RESULTS if not ok]
    print(f"\n[绿线护栏] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for tag, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {tag} — {detail}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
