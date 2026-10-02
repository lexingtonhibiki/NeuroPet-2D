"""舞台宠物重叠合成渲染验收(ADR-0032;第十二轮用户反馈 #2"宠物碰到宠物
有正方形遮挡")。运行:python tests/test_window_overlap.py

根因:同一 canvas 上的图元项是矩形不透明位图,两宠重叠时上层项的键色像素
把下层宠挖成方块(色键窗的逐像素透明只发生在"窗 vs 桌面"层,不发生 在
"canvas 项 vs canvas 项"层)。

修复:舞台缓存每宠最新原始 RGBA;重叠组(≥2 且 bbox 相交)按成员序
α 合成为单 canvas 项,成员单项挪出屏;分离瞬间从缓存恢复单项。

判据(6 组):
  ① 分离态回归:无重叠时单项路径逐位不变(无合成项,坐标/像素正确);
  ② 成组:重叠后成员单项挪出屏(-9999),合成项存在且居中于联合 bbox;
  ③ 合成正确性:下层宠在合成图中可见(上层透明像素不再挖方块);
  ④ 分离恢复:分开后合成项删除、两宠单项立即从缓存恢复(位置正确);
  ⑤ 移除成员:重叠中 remove_pet → 余者恢复、合成项消失;
  ⑥ 遮裁互斥:active 遮裁下合成图同样被裁(重叠+躲窗同帧正确)。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


def make_sprite(color: tuple[int, int, int]) -> Image.Image:
    """100² 精灵:中心 60² 不透明色块,其余透明。"""
    a = np.zeros((100, 100, 4), dtype=np.uint8)
    a[20:80, 20:80] = (*color, 255)
    return Image.fromarray(a)


def photo_px(photo, x: int, y: int) -> tuple[int, int, int]:
    img = getattr(photo, "pil", photo)     # spy 捕获的 PIL 图;直传亦可
    r, g, b = img.convert("RGB").getpixel((x, y))[:3]
    return (int(r), int(g), int(b))


import PIL.ImageTk as ITk  # noqa: E402


class _PhotoSpy(ITk.PhotoImage):
    """真 PhotoImage 子类:记录构造入参的 PIL 图(供像素级断言)。"""

    def __init__(self, image=None, **kw) -> None:
        self.pil = image
        super().__init__(image, **kw)


def main() -> None:
    from neuropet.core.windowing import OverlayStage

    root = tk.Tk()
    root.withdraw()
    st = OverlayStage(root, 900, 700)
    RED, BLUE = (200, 40, 40), (40, 60, 200)
    KEY = (1, 1, 1)
    real = ITk.PhotoImage
    ITk.PhotoImage = _PhotoSpy
    try:
        # ① 分离态:单项路径,无合成项
        st.update_pet("A", 250.0, 300.0, make_sprite(RED))
        st.update_pet("B", 700.0, 300.0, make_sprite(BLUE))
        pa = st._pet_photos["A"][0]
        record("① apart_no_merged",
               not st._merged_items and st._merged_groups == {}
               and photo_px(pa, 50, 50) == RED,
               f"分离态无合成项,A 项像素正确 {photo_px(pa, 50, 50)}")

        # ② 成组:B 挪近与 A 重叠
        st.update_pet("B", 300.0, 330.0, make_sprite(BLUE))
        key = "|".join(sorted(("A", "B")))
        has_merged = key in st._merged_items
        ax = st.canvas.coords(st._pet_items["A"])
        bx = st.canvas.coords(st._pet_items["B"])
        record("② members_hidden_merged_exists",
               has_merged and ax == [-9999.0, -9999.0] and bx == [-9999.0, -9999.0],
               f"重叠后成员单项挪出屏,合成项存在({key})")
        # 合成项位置 = 联合 bbox 中心(A(250,300) 100² + B(300,330) 100²
        # → union x∈[200,350] y∈[250,380] → 中心 (275,315))
        mx, my = st.canvas.coords(st._merged_items[key])
        record("② merged_centered",
               abs(mx - 275.0) <= 1.0 and abs(my - 315.0) <= 1.0,
               f"合成项位于联合 bbox 中心: ({mx},{my}) ≈ (275,315)")

        # ③ 合成正确性:联合图中两层都可见(无方块挖洞)
        pm = st._merged_photos[key][0]
        # 联合图原点 (200,250);A 色块中心 (250,300) → 局部 (50,50) 红
        # B 色块中心 (300,330) → 局部 (100,80) 蓝(A 在此处透明)
        # 两块都透明处 → 键色
        got_red = photo_px(pm, 50, 50)
        got_blue = photo_px(pm, 100, 80)
        got_key = photo_px(pm, 10, 10)
        record("③ both_visible_no_square",
               got_red == RED and got_blue == BLUE and got_key == KEY,
               f"下层红 {got_red}、上层蓝 {got_blue}、空白 {got_key}"
               f"(旧实现下层被上层矩形挖成键色方块)")

        # ④ 分离恢复:B 挪远 → 合成项删除,两宠单项立即恢复
        st.update_pet("B", 700.0, 300.0, make_sprite(BLUE))
        pa2 = st._pet_photos["A"][0]
        pb2 = st._pet_photos["B"][0]
        ax2 = st.canvas.coords(st._pet_items["A"])
        record("④ separated_restored",
               key not in st._merged_items and not st._merged_items
               and ax2 == [250.0, 300.0]
               and photo_px(pa2, 50, 50) == RED
               and photo_px(pb2, 50, 50) == BLUE,
               f"分离后合成项删除、A/B 单项立即恢复(A 坐标 {ax2})")

        # ⑤ 重叠中移除成员:余者恢复
        st.update_pet("B", 300.0, 330.0, make_sprite(BLUE))
        assert key in st._merged_items
        st.remove_pet("B")
        pa3 = st._pet_photos.get("A", (None,))[0]
        ax3 = st.canvas.coords(st._pet_items["A"])
        record("⑤ remove_member_restores",
               key not in st._merged_items and pa3 is not None
               and ax3 == [250.0, 300.0] and photo_px(pa3, 50, 50) == RED,
               "重叠中移除 B:A 单项立即恢复、合成项消失")

        record("⑥ occlusion_retired", True,
               "遮挡裁剪已随 ADR-0034 退役(判据移除)")
    finally:
        ITk.PhotoImage = real
        st.destroy()
        root.destroy()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[重叠合成验收] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, detail in failed:
            print(f"  FAIL: {tag} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
