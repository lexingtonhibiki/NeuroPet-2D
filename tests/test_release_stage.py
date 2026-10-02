"""Actual Tk images retain their pixels during inexpensive position updates."""
import sys
from pathlib import Path
import tkinter as tk
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture(scope="module")
def stage():
    from neuropet.core.windowing import OverlayStage, set_dpi_aware
    set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    view = OverlayStage(root, 1920, 1080)
    yield view
    view.destroy()
    root.destroy()

def test_position_updates_reuse_photo_and_merge_split_correctly(stage):
    image = Image.new("RGBA", (40, 40), (120, 40, 30, 255))
    stage.update_pet("a", 300, 300, image)
    stage.update_pet("b", 600, 300, image)
    original = stage._pet_photos["a"][0]
    stage.move_pets({"a": (310, 310), "b": (600, 300)})
    assert stage._pet_photos["a"][0] is original
    assert stage.canvas.coords(stage._pet_items["a"]) == [310, 310]
    stage.move_pets({"a": (580, 300), "b": (600, 300)})
    assert stage._merged_groups == {"a|b": ("a", "b")}
    stage.move_pets({"a": (310, 310), "b": (600, 300)})
    assert not stage._merged_groups
    assert stage.canvas.coords(stage._pet_items["a"]) == [310, 310]

def test_viewport_bounds_and_screen_coordinate_mapping(stage):
    stage.fit_viewport(force=True)
    stage.win.update()
    assert stage.win.winfo_width() < 1000
    assert stage.win.winfo_height() < 600
    assert abs(stage.canvas.canvasx(0) - stage.win.winfo_x()) <= 1
    assert abs(stage.canvas.canvasy(0) - stage.win.winfo_y()) <= 1
    stage.move_pets({"a": (1850, 1000), "b": (600, 300)})
    stage.fit_viewport(force=True)
    stage.win.update()
    right = stage.win.winfo_x() + stage.win.winfo_width()
    assert right >= 1870
    assert right <= 1920

def test_batch_uploads_only_the_final_overlap_composition(stage, monkeypatch):
    calls = []
    original = stage._render_merged
    monkeypatch.setattr(stage, "_render_merged", lambda key, members: (calls.append(key), original(key, members)))
    image = Image.new("RGBA", (40, 40), (20, 100, 40, 255))
    stage.begin_frame()
    stage.update_pet("a", 600, 300, image)
    stage.update_pet("b", 610, 300, image)
    assert calls == []
    stage.end_frame()
    assert calls == ["a|b"]

def test_transparent_crop_preserves_world_pixels_for_odd_sizes(stage):
    image = Image.new("RGBA", (101, 99))
    image.paste((100, 80, 40, 255), (10, 20, 61, 73))
    stage.update_pet("crop", 1000, 500, image)
    raw, x, y = stage._pet_raw["crop"]
    assert raw.size == (51, 53)
    assert x-raw.width//2 == 1000-101//2+10
    assert y-raw.height//2 == 500-99//2+20
    stage.move_pets({"crop": (1100, 550)})
    raw, x, y = stage._pet_raw["crop"]
    assert (x-raw.width//2, y-raw.height//2) == (1100-101//2+10, 550-99//2+20)
    stage.remove_pet("crop")

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
