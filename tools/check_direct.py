"""Probe the three official NWP archives directly (no API key, no relay).

Purpose: document — and make reproducible — whether ECMWF IFS, NOAA GFS and DWD ICON
publish their own layered-cloud fields, what the transport looks like, and what it
costs to ingest them. The product itself reads these models through Open-Meteo
(one HTTP call, four models); this script shows the raw alternative.

Run:  python tools/check_direct.py
"""
import bz2
import datetime as dt
import io
import sys
import urllib.request

UA = {"User-Agent": "skyglow/2.0 (+research)"}
now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
RUN = now - dt.timedelta(hours=8)          # publication lag
RUN = RUN.replace(hour=(RUN.hour // 6) * 6, minute=0, second=0, microsecond=0)
Y, M, D, H = RUN.year, RUN.month, RUN.day, RUN.hour


def head(url, rng=None):
    req = urllib.request.Request(url, headers=UA)
    if rng:
        req.add_header("Range", "bytes=%s" % rng)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            n = r.headers.get("Content-Length")
            first = r.read(4)
            return r.status, n, first
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", "ERR"), str(e)[:60], b""


def gfs():
    print("== NOAA GFS 0.25° (NOMADS) ==")
    base = ("https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.%04d%02d%02d/%02d/atmos/"
            % (Y, M, D, H))
    name = "gfs.t%02dz.pgrb2.0p25.f000" % H
    code, n, _ = head(base + name + ".idx")
    print("  idx   :", code, n)
    if code != 200:
        return
    idx = urllib.request.urlopen(urllib.request.Request(base + name + ".idx", headers=UA),
                                 timeout=40).read().decode()
    want = {"LCDC": None, "MCDC": None, "HCDC": None}
    for line in idx.splitlines():
        parts = line.split(":")
        if len(parts) > 3 and parts[3] in want:
            want[parts[3]] = int(parts[1])
    print("  offsets:", want)
    if None in want.values():
        print("  -> layered cloud not found in this file")
        return
    # single byte range covering LCDC..HCDC
    start = want["LCDC"]
    stop = want["HCDC"] + 900_000
    code, n, magic = head(base + name, "%d-%d" % (start, stop))
    print("  range :", code, n, "magic", magic)
    try:
        import eccodes
        raw = urllib.request.urlopen(
            urllib.request.Request(base + name, headers={**UA, "Range": "bytes=%d-%d" % (start, stop)}),
            timeout=60).read()
        gid = eccodes.codes_new_from_message(raw)
        print("  decode: %s shortName=%s Ni=%s Nj=%s" % (
            eccodes.codes_get(gid, "edition"),
            eccodes.codes_get(gid, "shortName"),
            eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")))
        eccodes.codes_release(gid)
        print("  verdict: GFS 可直接按字节区间取云量层，需 GRIB2 解码（eccodes）")
    except ImportError:
        print("  decode: eccodes 未安装（pip install eccodes）")
    except Exception as e:  # noqa: BLE001
        print("  decode failed:", e)


def ecmwf():
    print("== ECMWF IFS 0.25° open data ==")
    url = ("https://data.ecmwf.int/forecasts/%04d%02d%02d/%02dz/ifs/0p25/oper/"
           "%04d%02d%02d%02d0000-0h-oper-fc.grib2" % (Y, M, D, H, Y, M, D, H))
    code, n, _ = head(url, "0-10")
    print("  data.ecmwf.int :", code, n)
    s3 = ("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/%04d%02d%02d/%02dz/ifs/0p25/oper/"
          "%04d%02d%02d%02d0000-0h-oper-fc.grib2" % (Y, M, D, H, Y, M, D, H))
    code3, n3, _ = head(s3, "0-10")
    print("  S3 mirror      :", code3, n3)
    print("  参数：lcc / mcc / hcc（低/中/高云量），GRIB2，每步一个整场文件")


def icon():
    print("== DWD ICON global (opendata.dwd.de) ==")
    url = ("https://opendata.dwd.de/weather/nwp/icon/grib/%02d/clcl/"
           "icon_global_icosahedral_single-level_%04d%02d%02d%02d_000_CLCL.grib2.bz2"
           % (H, Y, M, D, H))
    code, n, _ = head(url)
    print("  CLCL  :", code, n)
    if code != 200:
        return
    try:
        import eccodes
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90).read()
        gid = eccodes.codes_new_from_message(bz2.decompress(raw))
        gt = eccodes.codes_get(gid, "gridType")
        print("  decode: gridType=%s numberOfPoints=%s" % (
            gt, eccodes.codes_get(gid, "numberOfDataPoints")))
        eccodes.codes_release(gid)
        print("  verdict: 可取，但全球 ICON 为二十面体非结构网格，取单点需最近邻插值")
    except ImportError:
        print("  decode: eccodes 未安装")
    except Exception as e:  # noqa: BLE001
        print("  decode failed:", e)


if __name__ == "__main__":
    print("run", RUN.isoformat(timespec="hours"), "UTC\n")
    gfs()
    print()
    ecmwf()
    print()
    icon()
