"""Auto mode: zone, weather and camera choices, without EnergyPlus.  Run:  python -m pytest -q tests"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thermoscene import auto  # noqa: E402
from thermoscene.energyplus.model import EPModel, rectangle_vertices  # noqa: E402

LABELS = ["SUMMER 07-15 15:00", "SUMMER 07-16 15:00", "WINTER 01-14 08:00", "WINTER 01-15 08:00"]


def test_zone_choice_prefers_longest_chain_then_area_then_conditioned():
    rows = [dict(zone="A", conditioned=True, chain=8, area=50.0), dict(zone="B", conditioned=True, chain=12, area=20.0),
            dict(zone="C", conditioned=True, chain=12, area=30.0), dict(zone="Attic", conditioned=False, chain=0, area=900.0)]
    assert auto.choose_zones(rows) == ["C"]                         # longest chain; tie broken by floor area
    assert auto.choose_zones(rows, "largest") == ["A"]              # largest conditioned zone
    assert auto.choose_zones(rows, "all") == ["A", "B", "C"]
    assert auto.choose_zones(rows, "attic") == ["Attic"]            # by name, case-insensitive
    free = [dict(r, conditioned=False, chain=0) for r in rows]
    assert auto.choose_zones(free) == ["Attic"]                     # nothing conditioned: the largest zone
    try:
        auto.choose_zones(rows, "Nowhere")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_weather_is_the_nearest_epw_with_a_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for name, lat, lon in (("chicago.epw", 41.98, -87.92), ("phoenix.epw", 33.45, -111.98), ("tampa.epw", 27.97, -82.53)):
            f = Path(tmp) / name
            f.write_text(f"LOCATION,X,ST,USA,TMY3,0,{lat},{lon},-6.0,200.0\n", encoding="latin-1")
            files.append(f)
        assert auto.nearest_epw((41.77, -87.75), files).name == "chicago.epw"
        assert auto.nearest_epw((32.1, -110.9), files).name == "phoenix.epw"     # Tucson -> Phoenix
        assert auto.nearest_epw(None, files, "tampa.epw").name == "tampa.epw"
    m = EPModel({"Site:Location": {"L": {"latitude": 37.62, "longitude": -122.4, "time_zone": -8.0}}})
    assert auto.site_location(m) == (37.62, -122.4) and auto.site_location(EPModel({})) is None


def test_simple_rectangular_surfaces_follow_energyplus_vertex_rules():
    # south wall: upper-left first, counter-clockwise seen from outside (normal -y)
    assert np.allclose(rectangle_vertices(180, 90, 0, 0, 0, 5, 3), [(0, 0, 3), (0, 0, 0), (5, 0, 0), (5, 0, 3)])
    # Floor:Adiabatic of 4ZoneWithShading_Simple_1 (azimuth 90, tilt 180): covers 6..26 x 6..26, normal down
    v = np.array(rectangle_vertices(90, 180, 6, 6, 0, 20, 20))
    assert np.allclose(v, [(26, 6, 0), (6, 6, 0), (6, 26, 0), (26, 26, 0)])
    from thermoscene.energyplus.model import newell_normal
    assert newell_normal(v)[2] < -0.99
    m = EPModel({"Zone": {"Z": {}}, "Wall:Interzone": {"W": {"zone_name": "Z", "outside_boundary_condition_object": "Z2",
                                                            "azimuth_angle": 90, "starting_x_coordinate": 26, "starting_y_coordinate": 6,
                                                            "starting_z_coordinate": 10, "length": 20, "height": 10}},
                 "Roof": {"R": {"zone_name": "Z", "azimuth_angle": 180, "length": 4, "width": 3}}})
    s = {x["name"]: x for x in m.surfaces("Z")}
    assert s["W"]["type"] == "Wall" and s["W"]["boundary"] == "Surface" and newell_normal(s["W"]["verts"])[0] > 0.99
    assert s["R"]["type"] == "Roof" and s["R"]["boundary"] == "Outdoors" and newell_normal(s["R"]["verts"])[2] > 0.99


# ---------------------------------------------------------------- cameras on a synthetic scene (box room + plenum)
def _quad(*pts):
    return [dict(vertex_x_coordinate=x, vertex_y_coordinate=y, vertex_z_coordinate=z) for x, y, z in pts]


def _box_zone(zone, X, Y, z0, z1, top, bottom, top_bc=("Outdoors", None), bottom_bc=("Ground", None)):
    walls = {"S": [(0, 0, z1), (0, 0, z0), (X, 0, z0), (X, 0, z1)], "E": [(X, 0, z1), (X, 0, z0), (X, Y, z0), (X, Y, z1)],
             "N": [(X, Y, z1), (X, Y, z0), (0, Y, z0), (0, Y, z1)], "W": [(0, Y, z1), (0, Y, z0), (0, 0, z0), (0, 0, z1)]}
    out = {f"{zone} Wall {k}": dict(surface_type="Wall", construction_name="C", zone_name=zone, outside_boundary_condition="Outdoors",
                                    vertices=_quad(*v)) for k, v in walls.items()}
    out[f"{zone} {top}"] = dict(surface_type=top, construction_name="C", zone_name=zone, outside_boundary_condition=top_bc[0],
                                outside_boundary_condition_object=top_bc[1],
                                vertices=_quad((0, 0, z1), (X, 0, z1), (X, Y, z1), (0, Y, z1)))
    out[f"{zone} {bottom}"] = dict(surface_type="Floor", construction_name="C", zone_name=zone, outside_boundary_condition=bottom_bc[0],
                                   outside_boundary_condition_object=bottom_bc[1],
                                   vertices=_quad((0, 0, z0), (0, Y, z0), (X, Y, z0), (X, 0, z0)))
    return out


def _scene(diffuser=True, window=True, plenum=True):
    from thermoscene.ontology import Element, SceneGraph
    from thermoscene.scene import spaces
    from thermoscene.scene.geometry import box
    srf = _box_zone("Office", 8, 6, 0, 3, "Ceiling", "Floor", top_bc=("Surface", "Plenum Floor") if plenum else ("Outdoors", None))
    if plenum:
        srf.update(_box_zone("Plenum", 8, 6, 3, 4, "Roof", "Floor", bottom_bc=("Surface", "Office Ceiling")))
    d = {"Zone": {"Office": {}, **({"Plenum": {}} if plenum else {})}, "Construction": {"C": {"outside_layer": "M"}},
         "BuildingSurface:Detailed": srf, "AirLoopHVAC:ReturnPlenum": {"RP": {"zone_name": "Plenum"}} if plenum else {}}
    if window:
        d["FenestrationSurface:Detailed"] = {"Office Window": dict(
            surface_type="Window", construction_name="C", building_surface_name="Office Wall S",
            **{f"vertex_{i + 1}_{a}_coordinate": c for i, p in enumerate([(2, 0, 2.2), (2, 0, 0.9), (5, 0, 0.9), (5, 0, 2.2)])
               for a, c in zip("xyz", p)})}
    cfg = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))
    cfg["scene"]["zone"] = "Office"
    cfg["scene"]["exterior_skins"] = True
    scene = SceneGraph()
    ids = spaces.build(scene, EPModel(d), cfg)

    def el(space, name, cls, faces):
        return scene.add(Element(id=f"{space}/{name}", cls=cls, name=name, space=space, quads=faces, material="galv_bare"))
    if diffuser:
        el(ids["zone"], "Supply_Diffuser_1", "Diffuser", box(3.7, 4.3, 2.7, 3.3, 2.97, 3.0))
    if plenum:
        el(ids["plenum"], "Supply_Main_Duct", "Duct", box(0.3, 7.7, 5.2, 5.6, 3.4, 3.7))
        el(ids["plenum"], "Terminal_Box", "TerminalBox", box(3.5, 4.5, 3.8, 4.4, 3.3, 3.7))
    el("Outdoors", "Pipe", "Pipe", box(0.0, 8.0, 1.0, 1.1, 4.3, 4.4))                # long, thin
    el("Outdoors", "AHU_Fan", "EquipmentCasing", box(3.0, 4.6, 2.0, 3.2, 4.3, 5.2))   # compact, larger surface
    return scene, ids, cfg


def _choose(scene, ids, cfg):
    from thermoscene.render.raycast import accel, raycast
    from thermoscene.usd.writer import scene_meshes

    def meshes_fn():
        pairs = scene_meshes(scene)
        return accel([f for _, f in pairs]), [t.id for t, _ in pairs]
    return {c["name"]: c for c in auto.choose_cameras(scene, ids, cfg, LABELS, meshes_fn, raycast)}


def test_auto_cameras_pick_diffuser_terminal_box_and_largest_rooftop_element():
    scene, ids, cfg = _scene()
    cams = _choose(scene, ids, cfg)
    assert cams["ZoneCamera"]["auto"]["target"] == f"{ids['zone']}/Supply_Diffuser_1"
    assert cams["PlenumCamera"]["auto"]["target"] == f"{ids['plenum']}/Terminal_Box" and cams["PlenumCamera"]["space"] == "plenum"
    assert cams["RooftopCamera"]["auto"]["target"] == "Outdoors/AHU_Fan"
    assert cams["ZoneCamera"]["frames"] == ["WINTER 01-15 08:00", "SUMMER 07-16 15:00"]
    assert cams["ZoneCamera"]["auto"]["height"] <= 0.8 * 3.0


def test_auto_cameras_fall_back_to_window_then_largest_wall_and_skip_missing_spaces():
    scene, ids, cfg = _scene(diffuser=False)
    cams = _choose(scene, ids, cfg)
    assert cams["ZoneCamera"]["auto"]["target"] == f"{ids['zone']}/Office_Window"
    scene, ids, cfg = _scene(diffuser=False, window=False, plenum=False)
    cams = _choose(scene, ids, cfg)
    assert cams["ZoneCamera"]["auto"]["target"] in (f"{ids['zone']}/Office_Wall_S", f"{ids['zone']}/Office_Wall_N")  # 8 m walls
    assert "PlenumCamera" not in cams


def test_auto_frames_fall_back_when_a_time_is_missing():
    assert auto.pick_frames(LABELS, ["WINTER 08:00", "SUMMER 15:00"]) == ["WINTER 01-15 08:00", "SUMMER 07-16 15:00"]
    assert auto.pick_frames(LABELS, ["WINTER 08:30"]) == ["WINTER 01-15 08:00"]           # middle of that run period
