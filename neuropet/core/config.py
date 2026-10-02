"""应用配置:JSON 持久化到 data/config.json,内存对象带默认值。"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .i18n import resolve as resolve_language

# r23 绿色版(exe):数据目录放 **exe 旁**而非解压临时目录(_MEIPASS 退出即焚,
# 宠物记忆/名册/配置无法持久)。开发运行仍以仓库根为锚。
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("NEUROPET_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
CONFIG_PATH = DATA_DIR / "config.json"
PETS_PATH = DATA_DIR / "pets.json"   # 启动名册(r16 Task E;与 session.json 同目录)
PROFILES_DIR = DATA_DIR / "profiles"
PLUGINS_DIR = PROJECT_ROOT / "plugins"


@dataclass
class AppConfig:
    fps: int = 60
    # 1..5 熟练度**基线**(新宠的初始等级;r25 A4 起不再由面板滑杆设定)。
    # 运行期 app._mastery_beat 按经历自适应的结果**只写 brain、不回写本字段**
    # (派生值可由 stats 复现,写回会冲掉基线并让测试静默改写受版本控制的
    # data/config.json);仅手动入口 app.set_intelligence 会写本字段。
    # 旧配置里的 1..5 值(如 "intelligence": 5)语义不变、照常载入
    # (load_config 不校验范围,set_intelligence/mastery_of 两侧都做 1..5 钳制)。
    intelligence: int = 3
    panel_visible: bool = True
    max_pets: int = 10
    log_events: bool = False
    feeding_timeout_s: int = 15      # 投喂模式限时(秒);<=0 表示不自动退出
    bubbles_enabled: bool = True     # 提示气泡(C2;面板勾选,默认开)
    # 界面语言(v0.1.1):zh-CN / en。空串 = 跟随系统语言(首次运行时判定并保存);
    # 旧配置没有本字段 → load_config 填系统语言,不改用户的文件。
    language: str = ""
    # 全局爬行速度倍率(v0.2.0)。0.5~8,默认 2(现状明显更快);只改运动参数,
    # **不缩放全局时间 dt** —— 饥饿、记忆、食物与飞行节奏全部照旧。吸附到
    # CRAWL_SPEED_CHOICES 的档位,旧配置缺失时 load_config 填默认值。
    crawl_speed: float = 2.0
    # 高速拖尾(v0.2.0):默认开启,可在设置里关闭。
    trail_enabled: bool = True
    # 各物种数量偏好由面板管理,不在此硬编码


# v0.2.0 爬行速度档位(易用刻度;滑杆式下拉直接用这 7 档)。下限 0.5 保证
# 最慢也还在动,上限 8 配合 body 的绝对安全上限(ABS_SPEED_MAX)。
CRAWL_SPEED_CHOICES = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
CRAWL_SPEED_DEFAULT = 2.0


def snap_crawl_speed(value) -> float:
    """任意值吸附到最近的爬行速度档位(脏值 → 默认档)。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return CRAWL_SPEED_DEFAULT
    if not (v == v):                       # NaN
        return CRAWL_SPEED_DEFAULT
    return min(CRAWL_SPEED_CHOICES, key=lambda c: abs(c - v))


DEFAULTS: dict[str, dict] = {
    "app": asdict(AppConfig()),
}


def load_config() -> AppConfig:
    try:
        raw = json.loads(CONFIG_PATH.read_text("utf-8"))
        values = {**asdict(AppConfig()), **raw.get("app", {})}
        # Old releases persisted a three-pet cap. Loading upgrades capacity without
        # changing the user's file; the next intentional settings save persists it.
        try:
            values["max_pets"] = max(10, int(values["max_pets"]))
        except (TypeError, ValueError, OverflowError):
            values["max_pets"] = 10
        # 旧配置无 language / 写了不认识的值 → 跟随系统语言(中文系统中文,
        # 否则英文;检测失败回退中文)。宠物存档与其它字段一律不动。
        values["language"] = resolve_language(values.get("language"))
        # v0.2.0 爬行速度:旧配置没有本键 → 默认档(2×);脏值同样落回默认档,
        # 绝不因为一个坏字段让宠物不动或飞出去。trail_enabled 缺省即开。
        values["crawl_speed"] = snap_crawl_speed(
            values.get("crawl_speed", CRAWL_SPEED_DEFAULT))
        return AppConfig(**values)
    except Exception:
        # 首次运行/文件损坏:同样给出确定语言码,不留空串。
        cfg = AppConfig()
        cfg.language = resolve_language("")
        return cfg


def save_config(cfg: AppConfig) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"app": asdict(cfg)}, ensure_ascii=False, indent=2), "utf-8")


def profile_dir(pet_id: str) -> Path:
    d = PROFILES_DIR / pet_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- 启动名册(data/pets.json;r16 Task E) ----------------
# 用户报障:旧 run() 硬编码"每个注册物种各 add 一只",每次启动都凭空多出
# 两只。改为读 data/pets.json(元素如 {"species": "species.cockroach",
# "pos": [x, y] 可选});文件缺失时自动生成缺省名册,内容与旧硬编码等价
# (cockroach+fruitfly 各一只),老用户无感。
def default_roster() -> list[dict]:
    """缺省启动名册:每内置物种各一只(顺序与旧 run() 遍历注册表一致)。"""
    return [{"species": "species.cockroach"},
            {"species": "species.fruitfly"}]


def load_roster(path: Path = PETS_PATH) -> list[dict]:
    """读启动名册;文件缺失 → 生成缺省文件(save_roster 落盘)。

    条目容错:非 dict / species 非字符串或为空 / pos 非 [x, y] 数值对的一律
    丢弃;JSON 解析失败 → 返回缺省名册但**不覆盖原文件**(保留现场供用户
    排查,与"缺失才生成"的口径一致)。物种是否已注册由调用方(app)判定。

    ``pet_id``(**可选**,r25 重启保真修复):非空字符串才保留,否则**只丢该
    字段、条目照收**(不因一个坏 id 丢掉整只宠);旧名册无此键 → 与修复前
    行为一致(由 app 发新 uuid)。"""
    try:
        if not path.exists():
            entries = default_roster()
            save_roster(entries, path)
            return entries
        raw = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        print(f"[config] 启动名册载入失败({path}): {exc}")
        return default_roster()
    entries: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            if not isinstance(e, dict):
                continue
            sp = e.get("species")
            if not isinstance(sp, str) or not sp:
                continue
            item = {"species": sp}
            pid = e.get("pet_id")
            if isinstance(pid, str) and pid:
                item["pet_id"] = pid          # r25:重启复用同一档案目录
            pos = e.get("pos")
            if (isinstance(pos, (list, tuple)) and len(pos) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in pos)):
                item["pos"] = [float(pos[0]), float(pos[1])]
            entries.append(item)
    return entries


def save_roster(entries: list[dict], path: Path = PETS_PATH) -> None:
    """可见名册落盘(app 增删/隐藏/召回后的回写;隐藏宠不进名册)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), "utf-8")
