"""Record only owned pet pixels on a neutral background; never record the desktop."""
import os
import sys
import math
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    with tempfile.TemporaryDirectory(prefix="neuropet-clip-") as temp:
        os.environ["NEUROPET_DATA_DIR"] = temp
        from neuropet.core.windowing import set_dpi_aware
        from neuropet.core.app import App
        from neuropet.core.contracts import Behavior, BehaviorCommand
        set_dpi_aware()
        app = App()
        app.world.screen = (1100, 750)
        app._cursor_getter = lambda: (-4000., -4000.)
        app._prewarm_on = False  # Deterministic recording; no background race.
        for i in range(10):
            species = "species.cockroach" if i%2 == 0 else "species.fruitfly"
            pid = app.add_pet(species, pos=(160+190*(i%5), 270+310*(i//5)))
            h = app.pets[pid]
            h.state.heading = .1 + (i%3)*.3
            def decide(view, state=h.state):
                return BehaviorCommand(Behavior.EXPLORE,
                    target=(state.pos[0]+300*math.cos(state.heading+.18),
                            state.pos[1]+300*math.sin(state.heading+.18)), intensity=.5)
            h.brain.decide = decide
        frames = []
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 23)
        try:
            for tick in range(600):  # 10 seconds, 60 Hz simulation, 15 fps capture.
                app._step_frame(1/60)
                app.root.update()
                # Offline capture must refresh poses by simulation time too.
                for h in app.pets.values():
                    h.last_render = 0
                if tick%4:
                    continue
                frame = Image.new("RGB", (1100,750), "#F3F5F6")
                for image, x, y in app.stage._pet_raw.values():
                    frame.paste(image, (x-image.width//2, y-image.height//2), image)
                draw = ImageDraw.Draw(frame)
                draw.text((32,24), "NeuroPet 2D · 10 只桌宠", font=font, fill="#25323A")
                draw.text((32,60), "程序实际渲染 · 固定 60 Hz 模拟 / 15 fps 录制", font=font, fill="#62717A")
                frames.append(frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
            path = ROOT / "assets" / "demos" / "ten-pets.gif"
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=67, loop=0, optimize=True)
            print(path)
        finally:
            app.shutdown()

if __name__ == "__main__":
    main()
