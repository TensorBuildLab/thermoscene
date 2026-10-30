"""
Thermal cameras in Isaac Sim for any thermoscene USD.

For every camera under /World/Cameras: render instance-ID segmentation and depth once (geometry is static),
convert depth to world hit points, then shade each requested time code with thermoscene.render.shading
(the same code as the reference renderer). Works with time-sampled and sidecar (NPZ) scenes.

Usage (Isaac Sim's Python; the repository root must be importable):
  ~/isaacsim/python.sh thermoscene/isaac/thermal_camera.py scene.usda
  ~/isaacsim/python.sh thermoscene/isaac/thermal_camera.py scene.usda --cameras PlenumCamera --all-frames
  ~/isaacsim/python.sh thermoscene/isaac/thermal_camera.py scene.usda --interactive

Written against the Isaac Sim 4.5-6.x Python API. The shading path is tested offline (tests/test_core.py);
the Replicator calls themselves need a GPU and were not executed by the authors of this script.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ap = argparse.ArgumentParser()
ap.add_argument("usd")
ap.add_argument("--out", default=None)
ap.add_argument("--cameras", nargs="*")
ap.add_argument("--frames", nargs="*", help="time-code labels (default: each camera's thermal:previewFrames)")
ap.add_argument("--all-frames", action="store_true")
ap.add_argument("--no-rgb", action="store_true")
ap.add_argument("--interactive", action="store_true")
args, _ = ap.parse_known_args()

from isaacsim import SimulationApp  # noqa: E402  (must precede omni.* imports)

app = SimulationApp({"headless": not args.interactive})

import numpy as np  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402

from thermoscene.render.shading import Series, camera_rays, cameras, labels, thermal_frame, thermal_prims  # noqa: E402

ctx = omni.usd.get_context()
ctx.open_stage(os.path.abspath(args.usd))
for _ in range(30):
    app.update()
stage = ctx.get_stage()
timeline = omni.timeline.get_timeline_interface()
tcps = stage.GetTimeCodesPerSecond()

if args.interactive:
    print("Playing the simulated period (viewport colours = surface temperature). Cameras are under /World/Cameras.")
    timeline.play()
    while app.is_running():
        app.update()
    app.close(); sys.exit(0)

import omni.replicator.core as rep  # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None

series, labs, prims = Series(stage), labels(stage), thermal_prims(stage)
index = {str(p.GetPath()): i for i, p in enumerate(prims)}
out_root = Path(args.out or Path(args.usd).resolve().parent / "isaac_renders")


def render():
    try:
        rep.orchestrator.step(rt_subframes=8, delta_time=0.0, pause_timeline=True)
    except TypeError:
        rep.orchestrator.step(rt_subframes=8)


def owner_of(path):
    while path and path != "/":
        if path in index:
            return index[path]
        path = path.rsplit("/", 1)[0]
    return len(prims)


def save_png(a, path):
    if Image is not None:
        lo, hi = np.percentile(a, 1), np.percentile(a, 99.5)
        Image.fromarray((np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)).save(path)


for cam in cameras(stage):
    name = cam.GetName()
    if args.cameras and name not in args.cameras:
        continue
    w, h = cam.GetAttribute("thermal:resolution").Get()
    rp = rep.create.render_product(str(cam.GetPath()), (int(w), int(h)))
    seg_a = rep.AnnotatorRegistry.get_annotator("instance_id_segmentation_fast", init_params={"colorize": False})
    dep_a = rep.AnnotatorRegistry.get_annotator("distance_to_image_plane")
    seg_a.attach([rp]); dep_a.attach([rp])
    rgb_a = None
    if not args.no_rgb:
        rgb_a = rep.AnnotatorRegistry.get_annotator("rgb"); rgb_a.attach([rp])
    timeline.set_current_time(0.0); render()
    seg = seg_a.get_data()
    ids = np.asarray(seg["data"]).reshape(int(h), int(w))
    lut = {int(k): owner_of(v) for k, v in seg["info"]["idToLabels"].items()}
    owner = np.vectorize(lambda i: lut.get(int(i), len(prims)))(ids)
    depth = np.asarray(dep_a.get_data()).reshape(int(h), int(w)).astype(float)
    o, d, _ = camera_rays(cam)
    hits = o + d * np.where(np.isfinite(depth), depth, 0.0)[..., None]       # z-depth along camera-space z=-1 rays
    out = out_root / name; out.mkdir(parents=True, exist_ok=True)
    np.save(out / "segmentation.npy", owner)
    frames = range(len(labs)) if args.all_frames else [labs.index(l) for l in (args.frames or cam.GetAttribute("thermal:previewFrames").Get() or [])]
    for tc in frames:
        true_map, app_map = thermal_frame(stage, series, tc, prims, owner, hits, cam, seed=tc)
        tag = labs[tc].replace(" ", "_").replace(":", "")
        np.save(out / f"{tag}_true.npy", true_map.astype(np.float32))
        np.save(out / f"{tag}_apparent.npy", app_map.astype(np.float32))
        save_png(app_map, out / f"{tag}_apparent.png")
        if rgb_a is not None:
            timeline.set_current_time(tc / tcps); render()
            rgb = np.asarray(rgb_a.get_data())
            if Image is not None and rgb.size:
                Image.fromarray(rgb[..., :3].astype(np.uint8)).save(out / f"{tag}_falsecolor_rtx.png")
        print(f"[{name}] {labs[tc]}: apparent {app_map.min():.1f}..{app_map.max():.1f} C")
    ref = Path(args.usd).resolve().parent / "renders" / name / "segmentation.npy"
    if ref.exists() and np.load(ref).shape == owner.shape:
        print(f"[{name}] segmentation agreement with the reference renderer: {(np.load(ref) == owner).mean():.1%}")
app.close()
