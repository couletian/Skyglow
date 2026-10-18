"""Spectral model of sunset/sunrise light.

Physics
-------
Sunlight reaching a cloud base at height h after travelling through the atmosphere
is attenuated by Rayleigh scattering (air molecules) and Mie scattering (aerosol).
For a sun below the horizon the ray grazes the Earth: its minimum (tangent) height is

    h_t = h - R * alpha^2 / 2          (alpha = sun depression, rad)

so high cloud bases stay illuminated (and stay bright) much longer than low ones,
and only the long-wavelength part of the spectrum survives -> the glow turns red.

The slant column of an exponential absorber of scale height H is
sqrt(2*pi*R*H) * exp(-h_t/H); dividing by the vertical column H gives the airmass.
The Rayleigh scale height is known (7.5 km), the aerosol scale height is taken as
1.4 km and the aerosol airmass coefficient is calibrated (see tools/calibrate.py).

References
----------
- Wyman, Sloan & Shirley (2013), "Simple Analytic Approximations to the CIE XYZ
  Color Matching Functions", J. Graphics Tools 18(1).
- Bohren & Huffman, "Absorption and Scattering of Light by Small Particles" (1983).
- sunsetbot.top 火烧云预报教程 1.3 节 (optical depth / extinction / colour).
"""
import math

R_EARTH = 6371.0088  # km
RAYLEIGH_TAU_550 = 0.098  # vertical optical depth of a standard atmosphere at 550 nm
AEROSOL_ANGSTROM = 1.3
AEROSOL_SCALE_HEIGHT = 1.4  # km
RAYLEIGH_SCALE_HEIGHT = 7.5  # km
AEROSOL_AIRMASS_GAIN = 12.0  # calibrated slant-column gain at sea level
SUN_TEMP = 5778.0


def planck(lam_nm, temp=SUN_TEMP):
    """Relative spectral radiance of a black body (arbitrary scale)."""
    lam = lam_nm * 1e-9
    h, c, k = 6.62607015e-34, 2.99792458e8, 1.380649e-23
    return (2 * h * c * c / lam ** 5) / (math.exp(h * c / (lam * k * temp)) - 1)


def _g(x, mu, s1, s2):
    s = s1 if x < mu else s2
    return math.exp(-0.5 * ((x - mu) / s) ** 2)


def cie_xyz(lam_nm):
    """Wyman et al. (2013) multi-lobe Gaussian fit of the CIE 1931 CMFs."""
    x = (1.056 * _g(lam_nm, 599.8, 37.9, 31.0)
         + 0.362 * _g(lam_nm, 442.0, 16.0, 26.7)
         - 0.065 * _g(lam_nm, 501.1, 20.4, 26.2))
    y = (0.821 * _g(lam_nm, 568.8, 46.9, 40.5)
         + 0.286 * _g(lam_nm, 530.9, 16.3, 31.1))
    z = (1.217 * _g(lam_nm, 437.0, 11.8, 36.0)
         + 0.681 * _g(lam_nm, 459.0, 26.0, 13.8))
    return x, y, z


_LAMBDAS = [400 + 5 * i for i in range(61)]  # 400..700 nm
_CMF = {l: cie_xyz(l) for l in _LAMBDAS}
_PLANCK_NORM = {l: planck(l) for l in _LAMBDAS}
_MAXP = max(_PLANCK_NORM.values())


def xyz_to_linear_rgb(x, y, z):
    r = 3.2406 * x - 1.5372 * y - 0.4986 * z
    g = -0.9689 * x + 1.8758 * y + 0.0415 * z
    b = 0.0557 * x - 0.2040 * y + 1.0570 * z
    return r, g, b


def _gamma(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _airmass(sun_elev_deg, cloud_base_km):
    """(rayleigh_airmass, aerosol_airmass, tangent_height_km) for the light path.

    For a sun above the horizon the path is plane-parallel (1/sin e), but it can
    never exceed the grazing (tangent) column at the cloud base, which is what
    limits the path when the sun is very low.
    """
    tangent_ray = 38.0 * math.exp(-cloud_base_km / RAYLEIGH_SCALE_HEIGHT)
    tangent_aer = AEROSOL_AIRMASS_GAIN * math.exp(-cloud_base_km / AEROSOL_SCALE_HEIGHT)
    if sun_elev_deg >= 0:
        s = max(math.sin(math.radians(sun_elev_deg)), 0.005)
        return min(tangent_ray, 1.0 / s), min(tangent_aer, 0.75 / s), cloud_base_km
    alpha = math.radians(-sun_elev_deg)
    h_t = max(0.0, cloud_base_km - R_EARTH * alpha * alpha / 2.0)
    m_ray = 38.0 * math.exp(-h_t / RAYLEIGH_SCALE_HEIGHT)
    m_aer = AEROSOL_AIRMASS_GAIN * math.exp(-h_t / AEROSOL_SCALE_HEIGHT)
    return m_ray, m_aer, h_t


def deck_light(cloud_base_km, sun_elev_deg, aod):
    """Spectral transmission and perceived colour of a lit cloud base.

    Returns dict with:
      lum     relative luminance of the direct beam (1.0 = unattenuated sun)
      rgb     chromaticity-normalised display colour (0-255), brightness-independent
      redness ratio of 620 nm to 470 nm transmission (colour purity proxy)
      tau550  total optical depth at 550 nm
      tangent minimum height of the light path (km)
    """
    if sun_elev_deg >= -0.05 and cloud_base_km <= 0.05:
        # sun on the horizon, cloud at ground level: essentially no path
        m_ray, m_aer, h_t = _airmass(max(sun_elev_deg, 0.0), 0.001)
    else:
        m_ray, m_aer, h_t = _airmass(sun_elev_deg, cloud_base_km)
    aod = max(0.0, aod if aod is not None else 0.15)

    X = Y = Z = 0.0
    Xw = Yw = Zw = 0.0
    t470 = t620 = 1.0
    tau550 = 0.0
    for lam in _LAMBDAS:
        tau = (RAYLEIGH_TAU_550 * (550.0 / lam) ** 4.1 * m_ray
               + aod * (550.0 / lam) ** AEROSOL_ANGSTROM * m_aer)
        T = math.exp(-tau)
        base = _PLANCK_NORM[lam] / _MAXP
        spec = base * T
        cx, cy, cz = _CMF[lam]
        X += spec * cx
        Y += spec * cy
        Z += spec * cz
        Xw += base * cx
        Yw += base * cy
        Zw += base * cz
        if lam == 550:
            tau550 = tau
        if lam == 470:
            t470 = T
        if lam == 620:
            t620 = T

    lum = Y / max(Yw, 1e-9)
    if lum <= 1e-9:
        return {"lum": 0.0, "rgb": [120, 90, 80], "redness": 1.0, "tau550": tau550,
                "tangent_km": h_t, "m_ray": m_ray, "m_aer": m_aer}

    # chromaticity of the transmitted beam; luminance is carried separately
    denom = max(X + Y + Z, 1e-12)
    cx, cy = X / denom, Y / denom
    Yn = max(lum, 1e-6)
    Xn = cx / max(cy, 1e-6) * Yn
    Zn = (1.0 - cx - cy) / max(cy, 1e-6) * Yn
    r, g, b = xyz_to_linear_rgb(Xn, Yn, Zn)
    r, g, b = max(0.0, r), max(0.0, g), max(0.0, b)
    mx = max(r, g, b, 1e-9)
    r, g, b = r / mx, g / mx, b / mx
    shade = 0.55 + 0.45 * min(1.0, (lum / 0.25) ** 0.35)
    rgb = [round(255 * _gamma(c) * shade) for c in (r, g, b)]
    return {
        "lum": lum,
        "rgb": rgb,
        "redness": t620 / max(t470, 1e-9),
        "tau550": tau550,
        "tangent_km": h_t,
        "m_ray": m_ray,
        "m_aer": m_aer,
    }


def colour_name(rgb, lum):
    if lum < 0.004:
        return "暗红余晖"
    r, g, b = rgb
    if r > 200 and g > 150 and b < 120:
        return "金黄"
    if r > 210 and g > 110:
        return "橘黄"
    if r > 190 and g > 60:
        return "橘红"
    if r > 150 and b < 110:
        return "绯红"
    return "玫瑰红"
