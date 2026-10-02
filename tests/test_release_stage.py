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
    assert abs(stage.canvas.canvasx(0) - stage.win.winfo_x()) <= 1, (
        stage._viewport, stage.win.winfo_width(), stage.canvas.winfo_width(), stage.canvas.xview())
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


def test_fast_position_move_expands_viewport_without_waiting_for_poll(stage):
    import time
    for pid in list(stage._pet_raw):
        stage.remove_pet(pid)
    image = Image.new("RGBA", (40, 40), (120, 40, 30, 255))
    stage.update_pet("fast", 1000, 500, image)
    stage._apply_viewport((900, 400, 1100, 600))
    stage._fit_checked = time.perf_counter()
    stage.move_pets({"fast": (1120, 500)})
    stage.fit_viewport()
    stage.win.update()
    assert stage._viewport[2] >= 1140
    assert abs(stage.canvas.canvasx(0) - stage.win.winfo_x()) <= 1
    stage.remove_pet("fast")


def test_viewport_resize_does_not_show_black_rectangles_on_owned_backdrop(stage):
    """Short screen capture, restricted to a solid test-owned window.

    This samples native painting during resize; it does not certify that every
    intermittent frame in a normal desktop session is flicker-free.
    """
    import math
    import threading
    import time
    from PIL import ImageGrab
    for pid in list(stage._pet_raw):
        stage.remove_pet(pid)
    backdrop = tk.Toplevel(stage.win.master)
    backdrop.overrideredirect(True)
    backdrop.configure(bg="#6aaabb")
    backdrop.geometry("640x400+80+80")
    backdrop.attributes("-topmost", True)
    backdrop.update()
    stage._apply_viewport((100, 140, 280, 320))
    stage.win.lift()
    stage.win.update()
    sprite = Image.new("RGBA", (36, 36), (220, 100, 40, 255))
    stage.update_pet("paint", 190, 230, sprite)
    stage.win.update()
    samples, errors = [], []
    stop = threading.Event()
    out = Path(__file__).resolve().parents[1] / "logs"
    out.mkdir(exist_ok=True)
    def capture():
        while not stop.is_set():
            try:
                frame = ImageGrab.grab(bbox=(80, 80, 720, 480), all_screens=True)
                blacks = frame.convert("L").histogram()[0]
                samples.append(blacks)
                if blacks > 64:
                    frame.save(out / "viewport-black-frame.png")
                    return
            except Exception as exc:
                errors.append(str(exc))
                return
    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    deadline = time.perf_counter() + 2.0
    ticks = 0
    try:
        while time.perf_counter() < deadline:
            left = round(300 + 190 * math.sin(ticks / 9))
            stage.move_pets({"paint": (left + 90, 230)})
            stage._apply_viewport((left, 140, left + 180, 320))
            stage.win.update()
            ticks += 1
            time.sleep(0.01)
        stop.set()
        worker.join(timeout=3)
        assert not worker.is_alive() and not errors, errors
        assert len(samples) >= 5
        assert max(samples) <= 64, f"black pixels in owned backdrop: {max(samples)}"
        (out / "viewport-paint-check.txt").write_text(
            f"frames={len(samples)}, resize_updates={ticks}, max_black_pixels={max(samples)}\n",
            encoding="utf-8")
    finally:
        stop.set()
        worker.join(timeout=3)
        stage.remove_pet("paint")
        backdrop.destroy()

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
