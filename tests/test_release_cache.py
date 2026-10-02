"""A bounded image cache must release old render inputs, not just its primary keys."""
import gc
import sys
import weakref
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from neuropet.render import renderer


def test_old_alpha_inputs_are_released():
    renderer._SNAP_CACHE.clear()
    first = Image.new("RGBA", (256, 256), (100, 80, 40, 200))
    reference = weakref.ref(first)
    output = renderer._snap_torso_alpha(first)
    assert output.getpixel((0, 0)) == (100, 80, 40, 255)
    del first, output
    for i in range(100):
        renderer._snap_torso_alpha(Image.new("RGBA", (256, 256), (i, 80, 40, 200)))
    gc.collect()
    assert reference() is None, "An evicted source remains alive in the auxiliary alpha cache"


if __name__ == "__main__":
    test_old_alpha_inputs_are_released()
    print("PASS: old alpha inputs released; pixels unchanged")
