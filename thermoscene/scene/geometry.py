"""Geometry helpers shared by the space builder and the generators."""
from __future__ import annotations

import mapbox_earcut as earcut
import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from ..energyplus.model import newell_normal


def frame(pts):
    p = np.asarray(pts, float)
    n = newell_normal(p)
    up = np.array([0.0, 0.0, 1.0])
    u = np.cross(up, n) if abs(n[2]) < 0.95 else np.array([1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return p[0], u, v, n


def to2d(pts, o, u, v):
    p = np.asarray(pts, float) - o
    return np.c_[p @ u, p @ v]


def triangulate(outer, holes=()) -> list[list[tuple]]:
    """Triangles (3D) of a planar polygon with holes; orientation follows the outer ring's normal."""
    o, u, v, n = frame(outer)
    rings = [to2d(outer, o, u, v)] + [to2d(h, o, u, v) for h in holes]
    poly = Polygon(rings[0], [r for r in rings[1:]]).buffer(0)
    if poly.is_empty:
        return []
    polys = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
    tris = []
    for pg in polys:
        ext = np.asarray(pg.exterior.coords)[:-1]
        ints = [np.asarray(i.coords)[:-1] for i in pg.interiors]
        verts = np.vstack([ext] + ints)
        ends = np.cumsum([len(ext)] + [len(i) for i in ints]).astype(np.uint32)
        idx = earcut.triangulate_float64(verts, ends).reshape(-1, 3)
        p3 = o + verts[:, :1] * u + verts[:, 1:2] * v
        for a, b, c in idx:
            t = [p3[a], p3[b], p3[c]]
            if np.dot(np.cross(t[1] - t[0], t[2] - t[0]), n) < 0:
                t = [t[0], t[2], t[1]]
            tris.append([tuple(x) for x in t])
    return tris


def offset(pts, normal, d):
    return [tuple(np.asarray(p, float) + d * np.asarray(normal)) for p in pts]


def plan_polygon(pts) -> Polygon:
    p = np.asarray(pts, float)
    return Polygon(p[:, :2]).buffer(0)


def grid_split(tris_outer, holes, step):
    """Triangulate a horizontal polygon clipped into grid cells (for per-vertex colour fields)."""
    outer = np.asarray(tris_outer, float)
    z = float(outer[:, 2].mean())
    poly = Polygon(outer[:, :2], [np.asarray(h)[:, :2] for h in holes]).buffer(0)
    xmin, ymin, xmax, ymax = poly.bounds
    out = []
    normal_up = newell_normal(outer)[2] > 0
    for x0 in np.arange(xmin, xmax, step):
        for y0 in np.arange(ymin, ymax, step):
            cell = Polygon([(x0, y0), (x0 + step, y0), (x0 + step, y0 + step), (x0, y0 + step)]).intersection(poly)
            if cell.is_empty or cell.area < 1e-6:
                continue
            for g in ([cell] if cell.geom_type == "Polygon" else [g for g in getattr(cell, "geoms", []) if g.geom_type == "Polygon"]):
                g = orient(g, sign=1.0)                     # counter-clockwise seen from +z
                ring = [(x, y, z) for x, y in np.asarray(g.exterior.coords)[:-1]]
                if not normal_up:
                    ring = ring[::-1]
                holes3 = [[(x, y, z) for x, y in np.asarray(i.coords)[:-1]] for i in g.interiors]
                out += triangulate(ring, holes3)
    return out


# ------------------------------------------------------------------ solids (as polygon faces)
def box(x0, x1, y0, y1, z0, z1):
    return [[(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)], [(x1, y1, z1), (x1, y1, z0), (x0, y1, z0), (x0, y1, z1)],
            [(x0, y1, z1), (x0, y1, z0), (x0, y0, z0), (x0, y0, z1)], [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)],
            [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]]


def oriented_box(center, axis, width, height, length):
    """Box centred at `center`, long side along horizontal unit `axis`, width across, height vertical."""
    c = np.asarray(center, float); a = np.r_[np.asarray(axis, float)[:2], 0.0]; a /= np.linalg.norm(a)
    w = np.array([-a[1], a[0], 0.0]); z = np.array([0.0, 0.0, 1.0])
    corners = lambda sa, sw, sz: tuple(c + sa * length / 2 * a + sw * width / 2 * w + sz * height / 2 * z)
    P = {(i, j, k): corners(i, j, k) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)}
    faces = [((-1, -1, 1), (-1, -1, -1), (1, -1, -1), (1, -1, 1)), ((1, 1, 1), (1, 1, -1), (-1, 1, -1), (-1, 1, 1)),
             ((-1, 1, 1), (-1, 1, -1), (-1, -1, -1), (-1, -1, 1)), ((1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),
             ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)), ((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1))]
    return [[P[k] for k in f] for f in faces]


def cyl(p0, p1, r, n=16, caps=False):
    p0, p1 = np.array(p0, float), np.array(p1, float)
    a = p1 - p0; a /= np.linalg.norm(a)
    u = np.cross(a, [0, 0, 1] if abs(a[2]) < 0.9 else [1, 0, 0]); u /= np.linalg.norm(u)
    v = np.cross(a, u)
    ring = [r * (np.cos(t) * u + np.sin(t) * v) for t in np.linspace(0, 2 * np.pi, n, endpoint=False)]
    q = [[tuple(p0 + ring[i]), tuple(p1 + ring[i]), tuple(p1 + ring[(i + 1) % n]), tuple(p0 + ring[(i + 1) % n])]
         for i in range(n)]
    if caps:
        for c in (p0, p1):
            q += [[tuple(c), tuple(c + ring[i]), tuple(c + ring[(i + 1) % n]), tuple(c + ring[(i + 2) % n])]
                  for i in range(0, n, 2)]
    return q


def path(points, r, n=16):
    q = []
    for a, b in zip(points[:-1], points[1:]):
        if np.linalg.norm(np.subtract(b, a)) > 1e-6:
            q += cyl(a, b, r, n)
    return q


def manhattan(p0, p1, axis, z=None):
    """Two-leg orthogonal route from p0 to p1: first along `axis`, then across (both horizontal)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    a = np.asarray(axis, float); a = a / np.linalg.norm(a)
    z = p0[2] if z is None else z
    mid = p0 + ((p1 - p0) @ a) * a
    return [tuple(p0[:2]) + (z,), tuple(mid[:2]) + (z,), tuple(p1[:2]) + (z,)]
