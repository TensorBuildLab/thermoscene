"""Tests. Pure-python tests always run; tests that need EnergyPlus or built case outputs skip otherwise.
Run:  python -m pytest -q tests      (or: python tests/test_core.py)
"""
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from thermoscene.energyplus.model import newell_normal  # noqa: E402
from thermoscene.scene.geometry import grid_split, triangulate  # noqa: E402
from thermoscene.thermal import radiometry as rad  # noqa: E402

CASES = ["A_single_room", "B_hvac_system", "C_doe_medium_office"]


def area(tris):
    return sum(0.5 * np.linalg.norm(np.cross(np.subtract(b, a), np.subtract(c, a))) for a, b, c in tris)


def test_triangulation_with_hole_keeps_area_and_orientation():
    wall = [(0, 0, 3), (0, 0, 0), (5, 0, 0), (5, 0, 3)]
    win = [(1, 0, 2.2), (1, 0, 0.9), (3, 0, 0.9), (3, 0, 2.2)]
    t = triangulate(wall, [win])
    assert abs(area(t) - (15 - 2.6)) < 1e-9
    assert np.allclose(newell_normal(t[0]), [0, -1, 0])


def test_grid_split_preserves_orientation():
    up = grid_split([(0, 0, 3), (5, 0, 3), (5, 4, 3), (0, 4, 3)], [], 0.5)
    down = grid_split([(0, 0, 0), (0, 4, 0), (5, 4, 0), (5, 0, 0)], [], 0.5)
    assert newell_normal(up[0])[2] > 0 and newell_normal(down[0])[2] < 0 and abs(area(up) - 20) < 1e-9


def test_radiometry_roundtrip_and_low_emissivity_misreading():
    assert abs(rad.apparent_temperature(rad.observed_radiance(25.0, 0.95, 20.0)) - 25.0) < 0.01
    metal = rad.apparent_temperature(rad.observed_radiance(35.0, 0.25, 24.0))
    assert 26 < metal < 29           # a 35 C galvanised duct reads ~27 C on a default-settings camera


def test_radiometry_covers_cold_sky_and_hot_equipment():
    """The lookup table must not clamp clear-sky temperatures of cold climates or hot rooftop equipment."""
    for t in (-80.0, -45.0, 0.0, 95.0, 200.0):
        assert abs(rad.apparent_temperature(rad.observed_radiance(t, 1.0, 20.0), 1.0, 20.0) - t) < 0.01, t


def test_envelope_defect_cavity_is_never_an_exposed_air_gap():
    """EnergyPlus rejects Material:AirGap as the outermost or innermost layer of a construction."""
    from thermoscene.energyplus.model import EPModel
    from thermoscene.scene.faults import envelope_defect
    mats = {"Brick": {"thickness": 0.1, "conductivity": 0.9}, "Ins": {"thickness": 0.1, "conductivity": 0.04},
            "Board": {"thickness": 0.013, "conductivity": 0.16}}
    verts = [dict(vertex_x_coordinate=x, vertex_y_coordinate=0.0, vertex_z_coordinate=z) for x, z in ((0, 3), (0, 0), (5, 0), (5, 3))]

    def wall(layers):
        return EPModel({"GlobalGeometryRules": {"g": {"vertex_entry_direction": "Counterclockwise", "coordinate_system": "Relative"}},
                        "Zone": {"Z": {}}, "Material": mats,
                        "Construction": {"C": {"outside_layer": layers[0], **{f"layer_{i + 2}": x for i, x in enumerate(layers[1:])}}},
                        "BuildingSurface:Detailed": {"W": {"surface_type": "Wall", "construction_name": "C", "zone_name": "Z",
                                                           "outside_boundary_condition": "Outdoors", "vertices": verts}}})
    for layers, cls in ((["Brick", "Ins", "Board"], "Material:AirGap"), (["Ins", "Board"], "Material:NoMass"),
                        (["Brick", "Ins"], "Material:NoMass")):
        objs, _ = envelope_defect(wall(layers), {"surface": "W", "rect": [1, 2, 0.5, 1.5]}, {"cavity_resistance": 0.17})
        assert objs[0].startswith(cls), (layers, objs[0])
        assert "_Cavity" in objs[1] and objs[2].startswith("FenestrationSurface:Detailed")


def test_run_period_without_weekday_is_valid_for_energyplus():
    """A run period without start_day_of_week leaves the field blank (EnergyPlus rejects 'UseWeatherFile')."""
    from thermoscene.energyplus.idf_text import IDFText, run_period
    o = IDFText(run_period("YEAR", "01-01", "12-31")).objects[0]
    assert o[:7] == ["RunPeriod", "YEAR", "1", "1", "", "12", "31"] and o[8] == ""
    assert IDFText(run_period("W", "1/14", "1/15", "Wednesday")).objects[0][8] == "Wednesday"


# ---------------------------------------------------------------- robustness on models thermoscene has never seen
def _vertex_fields(pts):
    return {f"vertex_{i + 1}_{a}_coordinate": v for i, p in enumerate(pts) for a, v in zip("xyz", p)}


def _wall_model(extra=None, walls=("W",), construction="C"):
    verts = [dict(vertex_x_coordinate=x, vertex_y_coordinate=0.0, vertex_z_coordinate=z) for x, z in ((0, 3), (0, 0), (5, 0), (5, 3))]
    d = {"Zone": {"Z": {}}, "Construction": {"C": {"outside_layer": "M"}},
         "BuildingSurface:Detailed": {w: {"surface_type": "Wall", "construction_name": construction, "zone_name": "Z",
                                          "outside_boundary_condition": "Outdoors", "vertices": verts} for w in walls}}
    d.update(extra or {})
    return d


def test_fenestration_vertex_count_autocalculate_and_blank():
    """'Autocalculate' and a blank (omitted) number of vertices: count the vertices given (triangles too)."""
    from thermoscene.energyplus.model import EPModel
    fen = {"Quad": {"building_surface_name": "W", "surface_type": "Window", "number_of_vertices": "Autocalculate",
                    **_vertex_fields([(1, 0, 2), (1, 0, 1), (2, 0, 1), (2, 0, 2)])},
           "Tri": {"building_surface_name": "W", "surface_type": "Door", **_vertex_fields([(3, 0, 2), (3, 0, 1), (4, 0, 1)])}}
    m = EPModel(_wall_model({"FenestrationSurface:Detailed": fen}))
    subs = {s["name"]: len(s["verts"]) for s in m.subsurfaces(m.surface("W"))}
    assert subs == {"Quad": 4, "Tri": 3}


def test_idf_text_keeps_bytes_and_writes_injected_names_as_utf8():
    """EnergyPlus needs UTF-8 names: injected objects must be UTF-8 while every other byte of the file is kept."""
    import tempfile
    from thermoscene.energyplus.idf_text import IDFText
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.idf", Path(tmp) / "out.idf"
        src.write_bytes("Zone, Büro;\nMaterial:NoMass, 试验, Rough, 1.0;\nSchedule:Constant, S\xfc, , 1;\n".encode("utf-8")
                        .replace(b"S\xc3\xbc", b"S\xfc"))           # one stray Latin-1 byte must survive unchanged
        idf = IDFText.read(src)
        idf.append("FenestrationSurface:Detailed, TS_DEFECT_Wänd, Door, 空洞;")
        idf.write(dst)
        out = dst.read_bytes()
        assert "Büro".encode() in out and "试验".encode() in out and b"S\xfc," in out
        assert "TS_DEFECT_Wänd".encode() in out and "空洞".encode() in out


def test_frame_specs_are_case_insensitive_and_allow_spaces():
    from thermoscene.render.cameras import resolve_frames
    labels = [f"SUMMER WEEK 07-{d} {h}" for d in (15, 16) for h in ("07:00", "14:00")] + ["WINTER 01-14 14:00"]
    assert resolve_frames(["Summer Week 14:00", "summer week 07-15 07:00", "WINTER 14:00"], labels) == \
        ["SUMMER WEEK 07-16 14:00", "SUMMER WEEK 07-15 07:00", "WINTER 01-14 14:00"]
    try:
        resolve_frames(["SUMMER 14:00"], labels)            # 'SUMMER' is not the environment 'SUMMER WEEK'
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_name_registry_suffixes_collisions_and_keeps_first_names():
    from thermoscene.ontology import NameRegistry, SceneGraph
    from thermoscene.scene import spaces
    import yaml
    r = NameRegistry({"/World": {"Systems"}})
    assert [r.claim("s", "A", 1), r.claim("s", "A", 2), r.claim("s", "A", 1), r.claim("s", "A"), r.claim("t", "A")] == \
        ["A", "A_2", "A", "A_3", "A"]
    assert r.claim("/World", "Systems", "zone") == "Systems_2"
    from thermoscene.energyplus.model import EPModel
    cfg = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))
    cfg["scene"]["zone"] = "Z"
    scene = SceneGraph()
    spaces.build(scene, EPModel(_wall_model(walls=("Wall 1", "Wall-1", "北墙", "南墙"))), cfg)
    ids = sorted(s.id for s in scene.things.values() if s.cls == "Surface")
    assert ids == ["Outdoors/Wall_1_ext", "Outdoors/Wall_1_ext_2", "Outdoors/___ext", "Outdoors/___ext_2",
                   "Z/Wall_1", "Z/Wall_1_2", "Z/__", "Z/___2"], ids


def test_usd_writer_never_merges_prims_with_the_same_sanitised_name():
    import tempfile
    import yaml
    from pxr import Usd, UsdGeom
    from thermoscene.ontology import Element, Provenance, SceneGraph, SimComponent, Space
    from thermoscene.scene.geometry import box
    from thermoscene.usd.writer import Writer
    cfg = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8")); cfg["name"] = "t"
    sc = SceneGraph(); n = 2
    sc.add(Space(id="Outdoors", cls="Outdoors", name="Outdoors"))
    for i, nm in enumerate(["AHU_送风机", "AHU_回风机", "AHU_排风机"]):
        e = sc.add(Element(id=f"Outdoors/{nm}", cls="EquipmentCasing", name=nm, space="Outdoors",
                           quads=box(i, i + 1, 0, 1, 0, 1), material="painted_steel"))
        sc.set_series(e.id, "surfaceTemperature", np.full(n, 30.0 + i), Provenance.HEURISTIC)
    for nm in ["送风机", "回风机"]:
        sc.add(SimComponent(id=f"FAN:VARIABLEVOLUME:{nm}", cls="Fan", name=nm, sim_type="Fan:VariableVolume"))
    for mode in ("timesamples", "sidecar"):
        cfg["storage"]["time_series"] = mode
        with tempfile.TemporaryDirectory() as tmp:
            usd = Path(tmp) / "t.usda"
            Writer(sc, cfg, ["a", "b"], 15).write(usd)
            st = Usd.Stage.Open(str(usd))
            meshes = sorted(str(p.GetPath()) for p in Usd.PrimRange(st.GetPrimAtPath("/World")) if p.IsA(UsdGeom.Mesh))
            assert meshes == ["/World/Outdoors/Elements/AHU____", "/World/Outdoors/Elements/AHU_____2",
                              "/World/Outdoors/Elements/AHU_____3"], meshes
            assert len(st.GetPrimAtPath("/World/Systems").GetChildren()) == 2
            if mode == "sidecar":
                from thermoscene.render.shading import Series
                ser = Series(st)
                temps = [ser.get(st.GetPrimAtPath(m), "thermal:surfaceTemperature", 1) for m in meshes]
                assert temps == [30.0, 31.0, 32.0], temps


def test_viewer_frames_follow_subsampled_time_axis():
    from thermoscene.web.export import time_index, viewer_frame, viewer_step_minutes
    idx = time_index(96, 50)
    k = viewer_frame(idx, 55)
    assert len(idx) == 50 and abs(idx[k] - 55) <= 1 and k < 50
    assert viewer_step_minutes(15, 96, idx) == round(15 * 95 / 49, 2) and viewer_step_minutes(15, 96, time_index(96)) == 15


def _branch(*comps):
    return {"components": [dict(component_object_type=t, component_name=n, component_inlet_node_name=i,
                                component_outlet_node_name=o) for t, n, i, o in comps]}


def test_water_cooled_chiller_condenser_is_water():
    """CondenserLoop demand branches (epJSON: condenser_demand_side_branch_list_name) carry water."""
    from thermoscene.energyplus.model import EPModel
    from thermoscene.energyplus.topology import HVACGraph
    from thermoscene.ontology import Medium
    d = {"CondenserLoop": {"CL": {"condenser_side_branch_list_name": "CS BL", "condenser_demand_side_branch_list_name": "CD BL"}},
         "BranchList": {"CD BL": {"branches": [{"branch_name": "CD B"}]}, "CS BL": {"branches": [{"branch_name": "CS B"}]}},
         "Branch": {"CD B": _branch(("Chiller:Electric:EIR", "CH", "CW IN", "CW OUT")),
                    "CS B": _branch(("CoolingTower:SingleSpeed", "CT", "CT IN", "CT OUT"))},
         "Chiller:Electric:EIR": {"CH": {"chilled_water_inlet_node_name": "CHW IN", "chilled_water_outlet_node_name": "CHW OUT",
                                         "condenser_inlet_node_name": "CW IN", "condenser_outlet_node_name": "CW OUT"}},
         "CoolingTower:SingleSpeed": {"CT": {"water_inlet_node_name": "CT IN", "water_outlet_node_name": "CT OUT"}}}
    g = HVACGraph(EPModel(d))
    ch = g.comps["CHILLER:ELECTRIC:EIR:CH"]
    assert {p.medium for p in ch.ports if "condenser" in p.name} == {Medium.WATER}
    assert ("condenser_demand", "CL") in ch.attrs["memberships"]


def test_coils_inside_a_unitary_system_are_air_loop_supply_equipment():
    """A blow-through DX coil shares no node with its unitary container; the container's declaration links it."""
    from thermoscene.energyplus.model import EPModel
    from thermoscene.energyplus.topology import HVACGraph
    d = {"AirLoopHVAC": {"AL": {"branch_list_name": "AL BL"}},
         "BranchList": {"AL BL": {"branches": [{"branch_name": "AL B"}]}},
         "Branch": {"AL B": _branch(("AirLoopHVAC:UnitaryHeatCool", "RTU", "RA", "SA"))},
         "AirLoopHVAC:UnitaryHeatCool": {"RTU": {"unitary_system_air_inlet_node_name": "RA", "unitary_system_air_outlet_node_name": "SA",
                                                 "supply_fan_object_type": "Fan:OnOff", "supply_fan_name": "F",
                                                 "cooling_coil_object_type": "Coil:Cooling:DX:SingleSpeed", "cooling_coil_name": "DX",
                                                 "heating_coil_object_type": "Coil:Heating:Electric", "heating_coil_name": "HC"}},
         "Fan:OnOff": {"F": {"air_inlet_node_name": "RA", "air_outlet_node_name": "F OUT"}},
         "Coil:Cooling:DX:SingleSpeed": {"DX": {"air_inlet_node_name": "F OUT", "air_outlet_node_name": "DX OUT"}},
         "Coil:Heating:Electric": {"HC": {"air_inlet_node_name": "DX OUT", "air_outlet_node_name": "SA"}}}
    g = HVACGraph(EPModel(d))
    for k in ("FAN:ONOFF:F", "COIL:COOLING:DX:SINGLESPEED:DX", "COIL:HEATING:ELECTRIC:HC"):
        assert g.has_role(g.comps[k], "air_supply"), k
    assert g.comps["COIL:COOLING:DX:SINGLESPEED:DX"].role == "air_supply"


def test_air_boundaries_are_skipped_and_unreadable_zones_explained():
    import yaml
    from thermoscene.energyplus.model import EPModel
    from thermoscene.ontology import SceneGraph
    from thermoscene.scene import spaces
    cfg = yaml.safe_load((ROOT / "thermoscene" / "defaults.yaml").read_text(encoding="utf-8"))
    cfg["scene"]["zone"] = "Z"
    d = _wall_model(walls=("W", "AIRWALL"))
    d["BuildingSurface:Detailed"]["AIRWALL"]["construction_name"] = "Air Wall"
    d["Construction:AirBoundary"] = {"Air Wall": {}}
    scene = SceneGraph(); spaces.build(scene, EPModel(d), cfg)
    assert sorted(t.id for t in scene.things.values() if t.cls == "Surface") == ["Outdoors/W_ext", "Z/W"]
    simple = {"Zone": {"Z": {}}, "Wall:Exterior": {"X": {"zone_name": "Z"}}}
    try:
        spaces.build(SceneGraph(), EPModel(simple), cfg)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Wall:Exterior" in str(e)


def test_weather_file_location_drives_the_sun():
    """EnergyPlus uses the weather file's location for weather-file run periods, not Site:Location."""
    import tempfile
    from thermoscene.energyplus.model import EPModel
    from thermoscene.thermal.environment import site
    m = EPModel({"Site:Location": {"L": {"latitude": 41.77, "longitude": -87.75, "time_zone": -6.0}}})
    with tempfile.TemporaryDirectory() as tmp:
        epw = Path(tmp) / "w.epw"
        epw.write_text("LOCATION,Phoenix,AZ,USA,TMY3,722780,33.45,-111.98,-7.0,337.0\n", encoding="latin-1")
        assert site(m, epw) == dict(lat=33.45, lon=-111.98, tz=-7.0)
    assert site(m, None) == dict(lat=41.77, lon=-87.75, tz=-6.0)


def _built(case):
    return (ROOT / "case_studies" / case / "output" / f"{case}.usda").exists()


def test_isaac_shading_path_matches_reference_renderer():
    """Feed the reference segmentation and depth through the Isaac script's reconstruction; outputs must match."""
    from pxr import Usd
    from thermoscene.render.raycast import accel, raycast, usd_faces
    from thermoscene.render.shading import Series, camera_rays, cameras, labels, thermal_frame, thermal_prims
    checked = 0
    for case in CASES:
        if not _built(case):
            continue
        out = ROOT / "case_studies" / case / "output"
        st = Usd.Stage.Open(str(out / f"{case}.usda"))
        prims, ser, labs = thermal_prims(st), Series(st), labels(st)
        meshes = accel([usd_faces(p) for p in prims])
        for cam in cameras(st):
            frames = list(cam.GetAttribute("thermal:previewFrames").Get() or [])
            ref = out / "renders" / cam.GetName() / f"{frames[-1].replace(' ', '_').replace(':', '')}_apparent.npy"
            if not ref.exists():
                continue
            o, d, (h, w) = camera_rays(cam)
            hit, t = raycast(o, d.reshape(-1, 3), meshes)
            depth = np.where(np.isfinite(t), t, 0.0).reshape(h, w)          # = Isaac distance_to_image_plane
            owner = np.where(hit >= 0, hit, len(prims)).reshape(h, w)
            tc = labs.index(frames[-1])
            _, app = thermal_frame(st, ser, tc, prims, owner, o + d * depth[..., None], cam, seed=tc)
            assert np.abs(app - np.load(ref)).max() < 1e-4, (case, cam.GetName())
            checked += 1
    if not checked:
        print("skipped: no built cases")


def test_case_outputs_have_no_missing_temperatures():
    from pxr import Usd
    from thermoscene.render.shading import Series, labels, thermal_prims
    for case in CASES:
        if not _built(case):
            continue
        st = Usd.Stage.Open(str(ROOT / "case_studies" / case / "output" / f"{case}.usda"))
        ser, n = Series(st), len(labels(st))
        for p in thermal_prims(st):
            v = [ser.get(p, "thermal:surfaceTemperature", tc) for tc in (0, n // 2, n - 1)]
            assert all(np.isfinite(v)) and all(-60 < x < 120 for x in v), (case, p.GetPath(), v)


def test_topology_on_case_b():
    if not os.environ.get("THERMOSCENE_ENERGYPLUS") or not _built("B_hvac_system"):
        return
    from thermoscene.energyplus.model import EPModel
    from thermoscene.energyplus.topology import HVACGraph
    ep = next((ROOT / "case_studies/B_hvac_system/output/_work/post").glob("*.epJSON"))
    g = HVACGraph(EPModel(ep))
    classes = {c.cls for c, _ in g.serving("Office")["components"]}
    assert {"VAVTerminalUnit", "HotWaterCoil", "Fan", "CoolingCoil", "OutdoorAirMixer", "Chiller", "Boiler", "Pump"} <= classes
    coil = next(c for c in g.comps.values() if c.cls == "CoolingCoil")
    assert [s.cls for s in g.plant_sources(coil)] == ["Chiller"]


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("ok", name)
