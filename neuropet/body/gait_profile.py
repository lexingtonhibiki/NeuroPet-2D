"""GaitProfile 步态参数表(冻结点 F2):加载 / 校验 / 回退(运行时零训练)。

F2 冻结内容(决策记录 §5.0):
  JSON = schema_version + species + preset + params(具名常量)+ provenance。
  缺失/损坏 → 内置默认回退(= 本波未训练保守版,行为与历史版一致)。

params 白名单(训练向量 θ,共 29 维 ≤ 30,D2 §3.2 对齐):
  步频 3(hz_rest/hz_cruise/hz_sprint)+ duty 3 + 组相位 2(group_phase)
  + 触发 3(trigger_k/min/max)+ 钳制 3(rest_cap_k/land_cap_k/over_k)
  + 前馈 1(ff_gain)+ 抬腿 3(lift_k)+ 差速 2(diff_k_in/out)
  + metachronal δ 1 + 应急 2(emergency_err_k/stride_k)
  + 摆窗 1(swing_win_k)+ 体摆 3(sway_amp_px/sway_freq_ratio/pitch_amp_deg)
  + 微弹性 2(sag_tau_ms/sag_max_deg)
其中调度类 22 维由 TripodGait.apply_profile 消费,体摆/微弹性 7 维由
body/poses.py + body/rig.py 消费;其余键(如 rest_pose 关节偏置)为渲染/
姿态层预留,本模块透传不解释。

查找顺序(data/gait/,F5 训练产物通路):
  1. <data_dir>/<species>_<preset>.json   (训练产物,六份命名)
  2. <data_dir>/gait_profile_default.json (随库保守默认)
  3. 内置 DEFAULT(= gait.py 历史常数)
任何读取/校验失败都静默降级到下一级——损坏配置不得破坏运行时。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
# r23 绿色版(exe):exe 旁 data/gait 优先(用户可覆盖),打包内 _MEIPASS 兜底;
# 开发运行仍以仓库根为锚。
import sys as _sys
if getattr(_sys, "frozen", False):
    _exe_side = Path(_sys.executable).resolve().parent / "data" / "gait"
    DATA_DIR = _exe_side if _exe_side.exists() \
        else Path(getattr(_sys, "_MEIPASS", ".")) / "data" / "gait"
else:
    DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "gait"

# ---- 内置默认(未训练保守版;与 body/gait.py 历史常数逐项一致) ----
DEFAULT_PARAMS: dict[str, Any] = {
    # 步频/占空(Hz;巡航 3~8Hz、冲刺 10~15Hz,文献比对验收线)
    "hz_rest": 3.0, "hz_cruise": 8.0, "hz_sprint": 14.0,
    "duty_patrol": 0.65, "duty_cruise": 0.50, "duty_sprint": 0.42,
    "group_phase": [0.0, 0.5],           # 三角步态组相位
    "trigger_k": 0.30, "trigger_min_k": 0.10, "trigger_max_k": 0.40,
    "rest_cap_k": 0.78, "land_cap_k": 0.86, "over_k": 0.88,
    "ff_gain": 0.60,
    "lift_k": [0.30, 0.22, 0.38],        # 抬腿内折弧系数(前/中/后)
    "diff_k_in": 0.20, "diff_k_out": 0.20,   # 转弯内外侧步幅差速
    "metachronal_deg": 0.0,              # 低速波状相位偏移(默认关=纯三角)
    "emergency_err_k": 3.0, "emergency_stride_k": 1.3,
    "swing_win_k": 0.70,
    # 体摆(D2 §3.2-7/8;默认 0=不改变现状观感)
    "sway_amp_px": 0.0, "sway_freq_ratio": 1.0, "pitch_amp_deg": 0.0,
    # 微弹性(D2 §2.4/§3.2-9:τ 30~80ms、下沉 2~6°)
    "sag_tau_ms": 60.0, "sag_max_deg": 4.0,
}

# 数值键的合法区间(校验;越界=损坏→回退)
_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "hz_rest": (0.5, 20.0), "hz_cruise": (1.0, 25.0), "hz_sprint": (2.0, 40.0),
    "duty_patrol": (0.3, 0.95), "duty_cruise": (0.3, 0.95), "duty_sprint": (0.3, 0.95),
    "trigger_k": (0.05, 0.6), "trigger_min_k": (0.02, 0.3), "trigger_max_k": (0.1, 0.6),
    "rest_cap_k": (0.5, 0.88), "land_cap_k": (0.5, 0.88), "over_k": (0.8, 0.95),
    "ff_gain": (0.0, 1.2),
    "diff_k_in": (0.0, 0.6), "diff_k_out": (0.0, 0.6),
    "metachronal_deg": (-25.0, 25.0),
    "emergency_err_k": (1.5, 8.0), "emergency_stride_k": (1.05, 3.0),
    "swing_win_k": (0.3, 0.95),
    "sway_amp_px": (0.0, 0.05), "sway_freq_ratio": (0.0, 3.0),
    "pitch_amp_deg": (0.0, 10.0),
    "sag_tau_ms": (20.0, 120.0), "sag_max_deg": (1.0, 8.0),
}
_TUPLE3_KEYS = ("lift_k",)


def default_params() -> dict[str, Any]:
    """内置默认参数的深拷贝(调用方可自由改写)。"""
    return json.loads(json.dumps(DEFAULT_PARAMS))


def validate_params(params: dict) -> bool:
    """校验参数表:白名单内数值键在合法区间、向量键长度正确。

    注意键间约束(duty 单调、cap 阶序 rest<land≤touch<over、hz 单调)不在此
    强校验——训练产物由训练器保证,运行时只防"数量级离谱"(越界即回退)。
    """
    for key, val in params.items():
        if key in _PARAM_RANGES:
            try:
                v = float(val)
            except (TypeError, ValueError):
                return False
            lo, hi = _PARAM_RANGES[key]
            if not (math.isfinite(v) and lo <= v <= hi):
                return False
        elif key in _TUPLE3_KEYS:
            try:
                if len(val) != 3 or not all(math.isfinite(float(x)) for x in val):
                    return False
            except (TypeError, ValueError):
                return False
        elif key == "group_phase":
            try:
                if len(val) != 2 or not all(0.0 <= float(x) < 1.0 for x in val):
                    return False
            except (TypeError, ValueError):
                return False
        # 白名单外的键透传不校验(渲染/姿态层预留)
    return True


def _read_profile(path: Path) -> dict | None:
    """读单份 profile JSON;返回 params 子树或 None(任何异常→None)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("schema_version", -1)) != SCHEMA_VERSION:
            return None
        params = data.get("params")
        if not isinstance(params, dict) or not validate_params(params):
            return None
        return params
    except (OSError, ValueError, TypeError):
        return None


# 物种别名归一(AG2 交接修复,2026-09-16):QF 终验(tests/test_acceptance_final.py)
# 以展示 tag("roach"/"fly")调用 load_profile,而训练产物/运行时
# (body/base.py 的 gait_species)用全名("cockroach"/"fruitfly")——别名不归一
# 时 QF 永远查不到 <tag>_default.json 而静默落到 default_file 层,训练产物
# 无法经加载链被终验消费。与 tools/train_gait.py 的 SPECIES_ALIASES 同表。
_SPECIES_ALIASES = {"roach": "cockroach", "fly": "fruitfly"}


def load_profile(species: str, preset: str = "default",
                 data_dir: str | Path | None = None) -> dict:
    """加载完整 profile(含 species/preset/provenance 元数据)。

    返回 {"source": "trained"|"default_file"|"builtin", "path": str|None,
          "params": merged_dict}——source 标明回退层级(调试/审计用)。
    species 接受别名 roach/fly(归一到 cockroach/fruitfly,见 _SPECIES_ALIASES)。
    """
    species = _SPECIES_ALIASES.get(str(species), str(species))
    base = Path(data_dir) if data_dir else DATA_DIR
    for rel, source in ((f"{species}_{preset}.json", "trained"),
                        (f"{species}_default.json", "trained"),
                        ("gait_profile_default.json", "default_file")):
        params = _read_profile(base / rel)
        if params is not None:
            return {"source": source, "path": str(base / rel),
                    "params": _merge(DEFAULT_PARAMS, params)}
    return {"source": "builtin", "path": None, "params": default_params()}


def load_params(species: str, preset: str = "default",
                data_dir: str | Path | None = None) -> dict:
    """运行时便捷入口:只取参数字典(回退链见 load_profile)。"""
    return load_profile(species, preset, data_dir)["params"]


def _merge(base: dict, override: dict) -> dict:
    """白名单内覆盖、白名单外透传;override 数值键越界时回退 base 值。"""
    merged = json.loads(json.dumps(base))
    for key, val in override.items():
        merged[key] = val
    if not validate_params(merged):        # 覆盖后整体越界 → 逐键剔除可疑项
        for key in list(override):
            trial = json.loads(json.dumps(base))
            for k2, v2 in override.items():
                if k2 != key:
                    trial[k2] = v2
            if validate_params(trial):
                merged = trial
            else:
                merged.pop(key, None)
    return merged


def save_profile(params: dict, species: str, preset: str,
                 path: str | Path, provenance: dict | None = None) -> None:
    """训练产物落盘(F5:训练只产数据;溯源块含 seed/代数/评估数/适应度)。"""
    doc = {"schema_version": SCHEMA_VERSION, "species": species, "preset": preset,
           "provenance": provenance or {}, "params": params}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                          encoding="utf-8")
