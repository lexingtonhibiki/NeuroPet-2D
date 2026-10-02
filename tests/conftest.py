"""pytest 共享 fixture。
`tmp`: HEAD 的 test_roster_config.py 使用名为 tmp 的目录 fixture,
历史上由未跟踪的 conftest 提供;2026-09-19 回退后补齐(= tmp_path 别名)。
"""
import pytest


@pytest.fixture
def tmp(tmp_path):
    return tmp_path
