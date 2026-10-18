"""Solar geometry — NOAA Solar Calculator algorithm (Meeus-based).

Accuracy: ~0.02 deg for elevation, sufficient for twilight timing (1 min ~ 0.25 deg).
Reference: NOAA Global Monitoring Laboratory, "Solar Calculator" equations.
"""
import math
from datetime import datetime, timedelta, timezone

R_EARTH = 6371.0088  # km
REFRACTION = -0.833  # standard sunrise/sunset altitude of solar disk centre (deg)


def _julian(dt):
    return dt.timestamp() / 86400.0 + 2440587.5


def solar_position(lat, lon, dt):
    """Return (elevation_deg, azimuth_deg) for an aware UTC datetime.

    Azimuth: degrees clockwise from true north.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    jd = _julian(dt)
    T = (jd - 2451545.0) / 36525.0
    L0 = (280.46646 + 36000.76983 * T + 0.0003032 * T * T) % 360.0
    M = 357.52911 + 35999.05029 * T - 0.0001537 * T * T
    e = 0.016708634 - 0.000042037 * T - 0.0000001267 * T * T
    Mr = math.radians(M % 360.0)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(Mr)
         + (0.019993 - 0.000101 * T) * math.sin(2 * Mr)
         + 0.000289 * math.sin(3 * Mr))
    true_long = L0 + C
    omega = 125.04 - 1934.136 * T
    lam = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23.0 + (26.0 + (21.448 - 46.815 * T - 0.00059 * T * T + 0.001813 * T ** 3) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam)))
    y = math.tan(math.radians(eps) / 2.0) ** 2
    L0r = math.radians(L0)
    eqtime = 4.0 * math.degrees(
        y * math.sin(2 * L0r) - 2.0 * e * math.sin(Mr)
        + 4.0 * e * y * math.sin(Mr) * math.cos(2 * L0r)
        - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * Mr)
    )
    utc_min = dt.hour * 60.0 + dt.minute + dt.second / 60.0
    tst = (utc_min + eqtime + 4.0 * lon) % 1440.0
    ha = tst / 4.0 - 180.0

    latr = math.radians(lat)
    har = math.radians(ha)
    cosz = (math.sin(latr) * math.sin(decl)
            + math.cos(latr) * math.cos(decl) * math.cos(har))
    cosz = max(-1.0, min(1.0, cosz))
    zen = math.degrees(math.acos(cosz))
    elev = 90.0 - zen

    sinz = math.sin(math.radians(zen))
    if sinz < 1e-9:
        az = 0.0
    else:
        # cos(azimuth from north) = (cos(lat)sin(dec) - sin(lat)cos(dec)cos(H)) / cos(elev)
        v = (math.cos(latr) * math.sin(decl)
             - math.sin(latr) * math.cos(decl) * math.cos(har)) / max(1e-9, math.cos(math.radians(elev)))
        v = max(-1.0, min(1.0, v))
        az = math.degrees(math.acos(v))
        if ha > 0:  # afternoon -> western half
            az = (360.0 - az) % 360.0
    return elev, az


def _find_crossing(lat, lon, day_local_start, hours_span=24.0, rising=False, step_min=2):
    """Find the sunrise/sunset crossing of -0.833 deg within a local day."""
    prev_t = day_local_start
    prev_e, _ = solar_position(lat, lon, prev_t)
    for i in range(1, int(hours_span * 60 / step_min) + 1):
        t = day_local_start + timedelta(minutes=i * step_min)
        e, _ = solar_position(lat, lon, t)
        crossed = (prev_e < REFRACTION <= e) if rising else (prev_e >= REFRACTION > e)
        if crossed:
            # bisection to 5 seconds
            lo, hi = prev_t, t
            for _ in range(18):
                mid = lo + (hi - lo) / 2
                em, _ = solar_position(lat, lon, mid)
                if (em < REFRACTION) == rising:
                    lo = mid
                else:
                    hi = mid
            return lo + (hi - lo) / 2
        prev_t, prev_e = t, e
    return None


def event_times(lat, lon, local_date, tz_offset_hours, step_min=2, which=None):
    """Sunrise & sunset for a local calendar date.

    local_date: datetime.date ; tz_offset_hours: e.g. 8 for Asia/Shanghai.
    step_min: coarse scan step (10 min is enough for map grids).
    which: 'sunrise' / 'sunset' to compute only one event.
    Returns dict with aware UTC datetimes (or None for polar day/night).
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    start = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
    sunrise = None if which == "sunset" else _find_crossing(lat, lon, start, 24.0, True, step_min)
    sunset = None if which == "sunrise" else _find_crossing(lat, lon, start, 24.0, False, step_min)
    return {"sunrise": sunrise, "sunset": sunset}


def elevation_curve(lat, lon, center, minutes_before=60, minutes_after=60, step=1):
    out = []
    for m in range(-minutes_before, minutes_after + 1, step):
        t = center + timedelta(minutes=m)
        e, az = solar_position(lat, lon, t)
        out.append({"t": t, "minute": m, "elev": e, "az": az})
    return out


def illumination_limit(cloud_base_km):
    """Sun elevation (deg) below which a cloud base at this height enters Earth's shadow."""
    if cloud_base_km <= 0:
        return 0.0
    return -math.degrees(math.sqrt(2.0 * cloud_base_km / R_EARTH))
