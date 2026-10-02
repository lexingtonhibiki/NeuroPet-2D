"""冒烟测试(无窗口):世界/大脑/身体/渲染管线。运行:python tests/smoke.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    from neuropet.core.contracts import (Behavior, BehaviorCommand, MovementMode,
                                         PetState)
    from neuropet.core.world import WorldModel
    from neuropet.brain.base import ReflexBrain
    from neuropet.body.base import GenericInsectBody
    from neuropet.render.renderer import render_pose
    from neuropet.species.cockroach import PARAMS as ROACH
    from neuropet.species.fruitfly import PARAMS as FLY
    from neuropet.perception.mouse import compute_stimuli

    world = WorldModel(1920, 1080)
    world.add_food((900, 500))
    world.add_zone("cold", (1500, 800))

    for params, flyer, tag in ((ROACH, False, "roach"), (FLY, True, "fly")):
        st = PetState(pet_id=tag, species_id=tag, pos=(600, 500))
        body = GenericInsectBody(st, dict(params))
        brain = ReflexBrain(st, flyer=flyer)
        brain.set_intelligence(3)
        for i in range(240):
            view = world.snapshot(tag, i / 60)
            stimuli = compute_stimuli(st, view, {"wind": 0.3, "vibration": 0.5,
                                                 "shadow": 0.5, "odor_food": 0.8,
                                                 "cold": 0.5})
            brain.observe(view, stimuli, 1 / 60)
            cmd = brain.decide(view)
            if i == 60:
                cmd = BehaviorCommand(Behavior.ESCAPE, target=(1400, 700),
                                      intensity=1.0, priority=90)
            body.apply(cmd, view, 1 / 60)
        pose = body.pose()
        assert pose["half"] == params["window_half"]
        assert len(pose["legs"]) == 6 and len(pose["segments"]) == 3
        img = render_pose(pose, {"body": "#5a3418", "highlight": "#7a4c24",
                                 "dark": "#33200e", "legs": "#3c2210",
                                 "legs_swing": "#6b4a2a", "antenna": "#241407",
                                 "eyes": "#120c06", "wing_cover": True,
                                 "wing_cover_color": "#6b431f"})
        out = Path(__file__).parents[1] / "scratch" / f"smoke_{tag}.png"
        out.parent.mkdir(exist_ok=True)
        img.save(out)
        print(f"[smoke] {tag}: pos={tuple(round(v) for v in st.pos)} "
              f"speed={st.speed:.0f} mode={st.mode.value} act={st.activity.value} "
              f"-> {out.name} {img.size}")

    # 事件与记忆
    st = PetState(pet_id="m", species_id="m")
    brain = ReflexBrain(st)
    brain.on_event("grab", {})
    brain.on_event("fed", {})
    brain.on_event("story", {"about": "cold", "valence": 0.8, "text": "雪景很美"})
    data = brain.save()
    brain2 = ReflexBrain(st)
    brain2.load(data)
    assert brain2.assoc.get("context:cold") is not None
    print("[smoke] 记忆/联想 save-load OK,", len(brain.memory_digest()), "条摘要")
    print("[smoke] ALL OK")


if __name__ == "__main__":
    main()
