"""Embodied-AI interface: a thermal scene as an environment an agent (robot, drone, crawler) can act in.

    world   = ThermalWorld("scene.usda")                 # load a built scene once (geometry, temperatures, labels)
    camera  = ThermalCamera.preset("handheld_320")        # realistic thermal-camera model (noise, non-uniformity, optics)
    frame   = world.observe(pose, time_index, camera)     # reading + per-pixel labels (true T, class, instance, provenance)
    env     = InspectionEnv(world, camera=...)            # Gymnasium-style reset()/step() for fault search and coverage
    export_dataset(world, out_dir, ...)                    # COCO-style labelled dataset with radiometric images

With `camera=ThermalCamera.preset("reference")` and realism switched off, `observe` reproduces the reference renderer
(`thermoscene.render.raycast`) exactly; every realism effect is an explicit, documented option.
"""
from .sensor import ThermalCamera  # noqa: F401
from .world import Pose, ThermalWorld  # noqa: F401
