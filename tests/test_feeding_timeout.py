# -*- coding: utf-8 -*-
"""投喂限时离线测试:feeding_expired 纯函数全分支 / AppConfig 序列化往返。

运行:python tests/test_feeding_timeout.py
全程离线:不创建窗口、不安装鼠标钩子。
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------- 纯函数:超时判定
def test_feeding_expired_branches() -> None:
    from neuropet.core.app import feeding_expired

    # 未开启投喂:无论 until 是什么都永不超时
    print("[test] feeding_expired:未开启投喂 → 永不超时")
    assert feeding_expired(False, None, 100.0) is False
    assert feeding_expired(False, 50.0, 100.0) is False

    # 开启但 until=None(不限时,ttl<=0):永不超时
    print("[test] feeding_expired:until=None 不限时 → 永不超时")
    assert feeding_expired(True, None, 0.0) is False
    assert feeding_expired(True, None, 12345.6) is False

    # 未到期:now < until
    print("[test] feeding_expired:未到期 → False")
    assert feeding_expired(True, 100.0, 0.0) is False
    assert feeding_expired(True, 100.0, 99.999) is False

    # 恰好到期:now == until(边界,应判定已超时)
    print("[test] feeding_expired:恰好到期(now==until)→ True")
    assert feeding_expired(True, 100.0, 100.0) is True

    # 已超时:now > until
    print("[test] feeding_expired:已超时 → True")
    assert feeding_expired(True, 100.0, 100.001) is True
    assert feeding_expired(True, 100.0, 500.0) is True


# ---------------------------------------------------------------- 配置:序列化往返
def test_app_config_roundtrip() -> None:
    import neuropet.core.config as cfgmod
    from neuropet.core.config import AppConfig

    # 默认值:feeding_timeout_s == 15
    print("[test] AppConfig:默认 feeding_timeout_s == 15")
    assert AppConfig().feeding_timeout_s == 15

    # asdict 序列化包含新字段,且可无损重建(dataclass 往返)
    print("[test] AppConfig:asdict 往返含 feeding_timeout_s")
    d = asdict(AppConfig(feeding_timeout_s=7))
    assert d["feeding_timeout_s"] == 7
    assert AppConfig(**d).feeding_timeout_s == 7

    # save/load 真实 JSON 往返(临时目录,不动 data/config.json)
    print("[test] AppConfig:save/load JSON 往返(临时目录)")
    orig_path, orig_dir = cfgmod.CONFIG_PATH, cfgmod.DATA_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            cfgmod.DATA_DIR = Path(td)
            cfgmod.CONFIG_PATH = Path(td) / "config.json"
            cfgmod.save_config(AppConfig(feeding_timeout_s=7, fps=30))
            loaded = cfgmod.load_config()
            assert loaded.feeding_timeout_s == 7, loaded
            assert loaded.fps == 30, loaded
            saved = json.loads(cfgmod.CONFIG_PATH.read_text("utf-8"))
            assert saved["app"]["feeding_timeout_s"] == 7, saved

            # 旧配置文件缺 feeding_timeout_s 字段 → 合并默认值 15,其余字段保留
            print("[test] AppConfig:旧配置缺字段 → 默认 15")
            del saved["app"]["feeding_timeout_s"]
            cfgmod.CONFIG_PATH.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")
            loaded2 = cfgmod.load_config()
            assert loaded2.feeding_timeout_s == 15, loaded2
            assert loaded2.fps == 30, loaded2

            # 配置文件完全损坏 → 整体回退默认 AppConfig(15)
            print("[test] AppConfig:配置损坏 → 全默认(15)")
            cfgmod.CONFIG_PATH.write_text("not-json{{", "utf-8")
            assert cfgmod.load_config().feeding_timeout_s == 15
    finally:
        cfgmod.CONFIG_PATH, cfgmod.DATA_DIR = orig_path, orig_dir

    # load_config 的合并语义:旧字典缺字段时 dataclass 默认值兜底(不落盘版本)
    print("[test] AppConfig:内存合并语义(旧 dict 缺字段取默认)")
    merged = AppConfig(**{**asdict(AppConfig()), **{"fps": 45}})
    assert merged.feeding_timeout_s == 15 and merged.fps == 45, merged


def main() -> None:
    test_feeding_expired_branches()
    test_app_config_roundtrip()
    print("[feeding-timeout] ALL OK")


if __name__ == "__main__":
    main()
