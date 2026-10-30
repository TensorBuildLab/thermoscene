"""
Long-wave infrared (LWIR, 8-14 um) radiometry shared by the reference renderer
and the Isaac Sim thermal camera, so both produce identical numbers.

Model (indoor, short range, atmosphere transmittance ~= 1):
    L_obs = eps * L(T_surface) + (1 - eps) * L(T_reflected)
The camera then converts L_obs to an *apparent* temperature using ITS OWN
emissivity / reflected-temperature settings, as a real camera does. When the
camera's settings differ from reality (e.g. glass, metal), it misreads.
"""
import numpy as np

H_PLANCK, C_LIGHT, K_B = 6.62607015e-34, 2.99792458e8, 1.380649e-23
BAND_UM = (8.0, 14.0)


def _band_radiance(t_kelvin, n=400):
    lam = np.linspace(BAND_UM[0], BAND_UM[1], n) * 1e-6
    t = np.atleast_1d(np.asarray(t_kelvin, float))[:, None]
    b = 2 * H_PLANCK * C_LIGHT**2 / lam**5 / np.expm1(H_PLANCK * C_LIGHT / (lam * K_B * t))
    return np.trapezoid(b, lam, axis=1) if hasattr(np, "trapezoid") else np.trapz(b, lam, axis=1)


# Lookup table from -40 C to 150 C for fast forward/inverse conversion
_T_LUT_C = np.linspace(-40.0, 150.0, 3801)
_L_LUT = _band_radiance(_T_LUT_C + 273.15)


def radiance(t_c):
    """Band radiance (W/m2/sr) for temperature(s) in deg C."""
    return np.interp(t_c, _T_LUT_C, _L_LUT)


def temperature(l):
    """Inverse: band radiance -> temperature in deg C."""
    return np.interp(l, _L_LUT, _T_LUT_C)


def observed_radiance(t_surface_c, emissivity, t_reflected_c):
    return emissivity * radiance(t_surface_c) + (1.0 - emissivity) * radiance(t_reflected_c)


def apparent_temperature(l_obs, cam_emissivity=0.95, cam_reflected_c=20.0):
    """What the camera reports, given its user settings (defaults = typical untuned camera)."""
    l_obj = (l_obs - (1.0 - cam_emissivity) * radiance(cam_reflected_c)) / cam_emissivity
    return temperature(l_obj)


def sensor_effects(t_app, netd_k=0.05, blur_px=0.7, seed=0):
    """Optics blur + temporal noise (NETD). Keeps the simulation from looking 'too clean'."""
    rng = np.random.default_rng(seed)
    out = t_app.astype(float)
    if blur_px > 0:
        r = int(np.ceil(3 * blur_px))
        x = np.arange(-r, r + 1)
        k = np.exp(-x**2 / (2 * blur_px**2)); k /= k.sum()
        pad = np.pad(out, r, mode="edge")
        out = np.apply_along_axis(lambda m: np.convolve(m, k, "valid"), 0, pad)
        out = np.apply_along_axis(lambda m: np.convolve(m, k, "valid"), 1, out)
    return out + rng.normal(0, netd_k, out.shape)
