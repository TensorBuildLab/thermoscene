"""Thermal-camera model for embodied agents: what an uncooled microbolometer camera reports for a radiance field.

The chain follows the physics of a real camera, in this order (parameters in `defaults.yaml`, section `sensors`):

    scene radiance L (8-14 um band, per sub-pixel, from ThermalWorld)
      -> optics: lens distortion (Brown radial k1, k2) and pixel integration (super-sampled box average)
      -> optics blur: Gaussian approximation of the diffraction-limited Airy core (sigma = 0.42 lambda N / pitch)
         combined in quadrature with lens aberration/defocus
      -> detector, in radiance: per-pixel gain and offset non-uniformity left after factory and shutter correction,
         column and row offsets, radial shading (vignetting / narcissus), offset drift that grows between flat-field
         corrections (FFC) and resets at each one, temporal noise with sigma = NETD * dL/dT at 30 degC
      -> radiometric inversion with the camera's emissivity and reflected-temperature settings
      -> calibration error within the datasheet accuracy (offset and gain, drawn once per camera unit)
      -> dead pixels, quantisation of the radiometric output (e.g. 0.01 K)
      -> optional 8-bit output with plateau histogram equalisation (automatic gain control, AGC)

Offsets and noise are given in K referred to a 30 degC scene, the way datasheets quote NETD; converted to radiance at
that reference, their temperature equivalent grows in colder scenes, as for a real microbolometer. Every random field
(non-uniformity, column/row offsets, dead pixels, calibration error) is drawn once per camera *unit* (`seed`), so a
camera keeps its own fixed pattern across frames, while temporal noise is drawn per frame.

Preset "reference" reproduces the reference renderer (white noise after a 0.7-pixel blur, in temperature) exactly;
preset "ideal" applies no sensor effect at all.
"""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import yaml

from ..thermal import radiometry as rad

_DEFAULTS = Path(__file__).resolve().parents[1] / "defaults.yaml"
T_REF_C = 30.0


def _sensor_cfg() -> dict:
    with open(_DEFAULTS, encoding="utf-8") as f:
        return yaml.safe_load(f)["sensors"]


def dLdT(t_c: float = T_REF_C, dt: float = 0.05) -> float:
    """Slope of the band radiance (W/m2/sr/K) at t_c."""
    return float((rad.radiance(t_c + dt) - rad.radiance(t_c - dt)) / (2 * dt))


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur with edge padding (same kernel as thermal.radiometry.sensor_effects)."""
    if sigma <= 0:
        return img
    r = int(np.ceil(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-x ** 2 / (2 * sigma ** 2)); k /= k.sum()
    pad = np.pad(img, r, mode="edge")
    out = np.apply_along_axis(lambda m: np.convolve(m, k, "valid"), 0, pad)
    return np.apply_along_axis(lambda m: np.convolve(m, k, "valid"), 1, out)


def agc8(t_app: np.ndarray, plateau: float = 0.02, bins: int = 512, lo_hi: tuple | None = None) -> np.ndarray:
    """8-bit image with plateau histogram equalisation, the automatic gain control of most thermal cores: each
    histogram bin is clipped at `plateau` x pixel count before the cumulative mapping, so large uniform areas do not
    take all the grey levels. `lo_hi` fixes the input range (default: 0.5th-99.5th percentile)."""
    t = np.asarray(t_app, float)
    lo, hi = lo_hi or (np.percentile(t, 0.5), np.percentile(t, 99.5))
    hi = max(hi, lo + 1e-3)
    h, edges = np.histogram(np.clip(t, lo, hi), bins=bins, range=(lo, hi))
    h = np.minimum(h, max(1, int(plateau * t.size)))
    cdf = np.cumsum(h).astype(float); cdf /= cdf[-1]
    idx = np.clip(((t - lo) / (hi - lo) * bins).astype(int), 0, bins - 1)
    return np.round(255 * cdf[idx]).astype(np.uint8)


class ThermalCamera:
    """A thermal camera *unit*: parameters plus its own fixed-pattern realisation.

        cam = ThermalCamera.preset("handheld_320", seed=3)      # a particular unit of that camera class
        cam = ThermalCamera.preset("lepton35").randomized(rng)  # domain randomisation around the preset
    """

    def __init__(self, params: dict, seed: int = 0, name: str = "custom"):
        cfg = _sensor_cfg()
        p = copy.deepcopy(cfg["defaults"]); p.update(params)
        self.p, self.name, self.seed = p, name, int(seed)
        self.w, self.h = (int(v) for v in p["resolution"])
        self.ss = max(1, int(p.get("supersample", 1)))
        self.hfov = float(p["hfov_deg"])
        self.tx = float(np.tan(np.radians(self.hfov) / 2))
        self.ty = self.tx * self.h / self.w
        self.k1, self.k2 = float(p["distortion_k1"]), float(p["distortion_k2"])
        lam = float(p["band_center_um"]) * 1e-6
        sigma_diff = 0.42 * lam * float(p["f_number"]) / (float(p["pixel_pitch_um"]) * 1e-6)
        self.blur_px = float(p["blur_px"]) if "blur_px" in params else float(np.hypot(sigma_diff, p["lens_blur_px"]))
        self.reference = bool(p.get("reference_mode"))
        self._dL = dLdT()
        self._realise()
        self.t_since_ffc = 0.0

    # ---------------------------------------------------------------- construction
    @classmethod
    def preset(cls, name: str, seed: int = 0, **over) -> "ThermalCamera":
        cfg = _sensor_cfg()
        if name not in cfg or name in ("defaults", "randomize"):
            raise KeyError(f"unknown sensor preset {name}; available: {cls.presets()}")
        p = copy.deepcopy(cfg[name]); p.update(over)
        return cls(p, seed, name)

    @staticmethod
    def presets() -> list[str]:
        return [k for k in _sensor_cfg() if k not in ("defaults", "randomize")]

    def randomized(self, rng: np.random.Generator) -> "ThermalCamera":
        """A new unit whose non-ideal parameters are drawn around this preset (ranges in sensors.randomize)."""
        ranges = _sensor_cfg()["randomize"]
        p = copy.deepcopy(self.p)
        for k, (a, b) in ranges.items():
            if k in ("emissivity_setting", "reflected_setting_c"):
                p[k] = float(rng.uniform(a, b))
            elif k in p:
                p[k] = float(p[k]) * float(rng.uniform(a, b))
        if "lens_blur_px" in ranges and not p.get("reference_mode"):
            p.pop("blur_px", None)            # the randomised lens blur then sets the optics blur
        return ThermalCamera(p, int(rng.integers(2 ** 31)), self.name + "+random")

    def _realise(self):
        """Draw the unit's fixed patterns (they stay with the camera across frames)."""
        p, rng = self.p, np.random.default_rng(self.seed)
        h, w = self.h, self.w
        self.offset_map = rng.normal(0, p["fpn_offset_k"], (h, w))
        self.gain_map = rng.normal(0, p["fpn_gain"], (h, w))
        self.col = rng.normal(0, p["stripe_col_k"], w)[None, :]
        self.row = rng.normal(0, p["stripe_row_k"], h)[:, None]
        yy, xx = np.mgrid[0:h, 0:w]
        r2 = (((xx + 0.5) / w - 0.5) ** 2 + ((yy + 0.5) / h - 0.5) ** 2 * (h / w) ** 2) / (0.25 * (1 + (h / w) ** 2))
        self.radial = r2                                            # 0 at the centre, 1 in the corners
        sign = rng.choice([-1.0, 1.0])
        self.vignetting = sign * p["vignetting_k"] * r2             # centre-to-corner shading, sign of the housing
        self.dead = rng.random((h, w)) < p["dead_pixel_fraction"]
        lo, hi = p["dead_pixel_range_c"]
        self.dead_value = rng.uniform(lo, hi, (h, w))
        ak, af = float(p["accuracy_k"]), float(p["accuracy_frac"])
        self.bias_k = float(np.clip(rng.normal(0, ak / 2), -ak, ak)) if ak else 0.0
        self.gain_err = float(np.clip(rng.normal(0, af / 2), -af, af)) if af else 0.0

    # ---------------------------------------------------------------- geometry the world must render
    def render_grid(self):
        """(width, height, tan half-extents x and y) of the undistorted sub-pixel image the world should render."""
        sx = sy = 1.0
        if self.k1 or self.k2:
            u, v = self._undistort(np.array([1.0, 1.0, 0.0]), np.array([self.ty / self.tx, 0.0, self.ty / self.tx]))
            sx, sy = max(1.0, float(np.max(np.abs(u)))), max(1.0, float(np.max(np.abs(v)) * self.tx / self.ty))
        return self.w * self.ss, self.h * self.ss, self.tx * sx, self.ty * sy

    def _undistort(self, xd, yd, it=20):
        """Undistorted normalised coordinates (units of tan(hfov/2)) of distorted ones: Newton's method on the radius
        of r_u (1 + k1 r_u^2 + k2 r_u^4) = r_d. The model must stay monotonic over the image (checked)."""
        xd, yd = np.asarray(xd, float), np.asarray(yd, float)
        rd = np.hypot(xd, yd)
        r = rd.copy()
        for _ in range(it):
            g = r * (1 + self.k1 * r ** 2 + self.k2 * r ** 4) - rd
            dg = 1 + 3 * self.k1 * r ** 2 + 5 * self.k2 * r ** 4
            if np.any(dg <= 0.05):
                raise ValueError(f"lens distortion k1={self.k1}, k2={self.k2} folds over inside the image")
            r = r - g / dg
        s = np.where(rd > 1e-12, r / np.maximum(rd, 1e-12), 1.0)
        return xd * s, yd * s

    def sample_map(self):
        """For every output sub-pixel, the continuous (column, row) position in the rendered image, or None when the
        camera has no distortion (the rendered grid is then the sub-pixel grid itself)."""
        if not (self.k1 or self.k2):
            return None
        if getattr(self, "_smap", None) is not None:
            return self._smap
        W, H, txr, tyr = self.render_grid()
        jj, ii = np.meshgrid(np.arange(W), np.arange(H))
        xd = ((jj + 0.5) / W * 2 - 1)                                  # units of tan(hfov/2)
        yd = (1 - (ii + 0.5) / H * 2) * self.ty / self.tx
        xu, yu = self._undistort(xd, yd)
        col = ((xu * self.tx / txr) + 1) / 2 * W - 0.5
        row = (1 - yu * self.tx / tyr) / 2 * H - 0.5
        self._smap = (col, row)
        return self._smap

    # ---------------------------------------------------------------- measurement
    def integrate(self, sub: np.ndarray, how: str = "mean") -> np.ndarray:
        """Sub-pixel image (h*ss, w*ss) -> pixel image (h, w): box average ('mean') or centre sample ('center')."""
        s = self.ss
        if s == 1:
            return sub
        if how == "center":
            return sub[s // 2::s, s // 2::s]
        return sub.reshape(self.h, s, self.w, s).mean(axis=(1, 3))

    def advance(self, seconds: float) -> bool:
        """Advance the camera clock; returns True if a flat-field correction happened (drift reset)."""
        self.t_since_ffc += float(seconds)
        T = float(self.p["ffc_interval_s"] or 0)
        if T > 0 and self.t_since_ffc >= T:
            self.t_since_ffc %= T
            return True
        return False

    def measure(self, L: np.ndarray, rng: np.random.Generator | None = None, frame_seed: int = 0) -> np.ndarray:
        """Camera reading (deg C, h x w) for the pixel-integrated scene radiance L (h x w, W/m2/sr)."""
        p = self.p
        if self.reference:                       # the reference renderer's camera, bit for bit
            app = rad.apparent_temperature(L, p["emissivity_setting"], p["reflected_setting_c"])
            return rad.sensor_effects(app, p["netd_k"], self.blur_px, seed=frame_seed)
        rng = rng or np.random.default_rng(frame_seed)
        L = gaussian_blur(np.asarray(L, float), self.blur_px)
        drift = p["drift_k_per_min"] * self.t_since_ffc / 60.0
        share = p["drift_vignetting_share"]
        offs_k = self.offset_map + self.col + self.row + self.vignetting + drift * ((1 - share) + share * self.radial)
        L = L * (1 + self.gain_map) + self._dL * offs_k
        if p["netd_k"] > 0:
            L = L + rng.normal(0, p["netd_k"] * self._dL, L.shape)
        t = rad.apparent_temperature(L, p["emissivity_setting"], p["reflected_setting_c"])
        t = t + self.bias_k + self.gain_err * t
        t = np.where(self.dead, self.dead_value, t)
        if p["quant_k"] > 0:
            t = np.round(t / p["quant_k"]) * p["quant_k"]
        return t

    def describe(self) -> dict:
        """Parameters and the realised unit (for dataset metadata)."""
        return dict(preset=self.name, seed=self.seed, resolution=[self.w, self.h], hfov_deg=self.hfov,
                    supersample=self.ss, blur_px=round(self.blur_px, 4), calibration_bias_k=round(self.bias_k, 4),
                    calibration_gain_error=round(self.gain_err, 5), dead_pixels=int(self.dead.sum()),
                    parameters={k: v for k, v in self.p.items() if not isinstance(v, (dict,))})
