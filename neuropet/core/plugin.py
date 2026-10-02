"""插件系统(一切皆插件)。

设计要点(借鉴 deepseek-harness,详见 docs/references/):
- 插件 = 一个类,带 class 级 ``manifest`` dict;或 plugins/ 目录下的
  ``<文件夹>/manifest.json + <entry 模块>.py``。
- 五种类型:species(物种包)、brain(大脑)、perceptor(感知器)、panel(面板区)、api(未来 LLM 等)。
- 生命周期:register(发现)→ create(实例化)→ on_load → on_unload。
- API 版本:core API=1;manifest 声明 "api": 1,不匹配则拒绝加载。
- 内置物种也走同一插件接口,核心不写死任何物种。

manifest 字段:id, name, version, type, entry("模块:类名",仅目录插件需要), api
"""
from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Type

PLUGIN_TYPES = ("species", "brain", "perceptor", "panel", "api")
CORE_API_VERSION = 1


class PluginError(Exception):
    pass


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    type: str
    entry: str = ""            # 目录插件的 "module:ClassName"
    api: int = CORE_API_VERSION
    description: str = ""
    source: str = "builtin"    # builtin | external

    def validate(self) -> None:
        if self.type not in PLUGIN_TYPES:
            raise PluginError(f"未知插件类型: {self.type}")
        if self.api != CORE_API_VERSION:
            raise PluginError(f"{self.id} 需要 API v{self.api},当前核心 v{CORE_API_VERSION}")


class PluginBase:
    """所有插件的基类。子类需设置类属性 manifest(dict 或 PluginManifest)。"""
    manifest: dict[str, Any] | PluginManifest = {}

    def __init__(self, app: Any = None, **kw: Any) -> None:
        self.app = app

    def on_load(self) -> None: ...
    def on_unload(self) -> None: ...

    @classmethod
    def get_manifest(cls) -> PluginManifest:
        m = cls.manifest
        if isinstance(m, PluginManifest):
            return m
        return PluginManifest(**{**{f.name: getattr(m, f.name, f.default) for f in
                                    __import__("dataclasses").fields(PluginManifest)},
                                 **m})


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, tuple[PluginManifest, Type[PluginBase]]] = {}

    # ---- 注册 ----
    def register_class(self, cls: Type[PluginBase]) -> PluginManifest:
        m = cls.get_manifest()
        m.validate()
        if m.id in self._plugins:
            raise PluginError(f"插件 id 冲突: {m.id}")
        self._plugins[m.id] = (m, cls)
        return m

    def scan_dir(self, root: Path) -> list[tuple[str, str]]:
        """扫描外部插件目录,返回 (id, 错误信息|空) 列表。"""
        results: list[tuple[str, str]] = []
        if not root.exists():
            return results
        root = root.resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for child in sorted(root.iterdir()):
            mf_path = child / "manifest.json"
            if not child.is_dir() or not mf_path.exists():
                continue
            try:
                data = json.loads(mf_path.read_text("utf-8"))
                m = PluginManifest(source="external", **{
                    k: data.get(k, dflt) for k, dflt in
                    (("id", ""), ("name", data.get("id", "")), ("version", "0.1"),
                     ("type", ""), ("entry", ""), ("api", CORE_API_VERSION),
                     ("description", ""), ("source", "external")) if True})
                if not m.entry or ":" not in m.entry:
                    raise PluginError(f"manifest.json 缺少 entry(格式 模块:类名): {mf_path}")
                mod_name, cls_name = m.entry.split(":", 1)
                mod = importlib.import_module(mod_name)
                cls = getattr(mod, cls_name)
                self.register_class(cls)
                # 若类内 manifest 与 json 不同,以类为准重新校验
                results.append((m.id, ""))
            except Exception as exc:
                results.append((child.name, str(exc)))
        return results

    # ---- 查询/实例化 ----
    def manifests(self, type_filter: str | None = None) -> list[PluginManifest]:
        return [m for m, _ in self._plugins.values()
                if type_filter is None or m.type == type_filter]

    def get(self, plugin_id: str) -> tuple[PluginManifest, Type[PluginBase]]:
        if plugin_id not in self._plugins:
            raise PluginError(f"插件未注册: {plugin_id}")
        return self._plugins[plugin_id]

    def create(self, plugin_id: str, app: Any = None, **kw: Any) -> PluginBase:
        m, cls = self.get(plugin_id)
        inst = cls(app=app, **kw)
        inst.on_load()
        return inst
