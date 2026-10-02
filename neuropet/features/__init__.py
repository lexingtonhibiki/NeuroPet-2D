"""功能插件(ADR-0031):从 App god-module 迁出的主循环内联特性。

每个功能一个 FeaturePlugin,经 core.kernel.FeatureKernel.mount() 接入;
副作用全部经 FeatureContext 登记(作用域回收)。迁移范围与余项见 ADR-0031
决策 8(hide/drag 本轮入内核;feeding-fx/screen-monitor 留 App)。
"""
from .drag import DragFeature

__all__ = ["DragFeature"]
