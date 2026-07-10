"""
dev_build_hypatia_catalog.py — Star/Constellation Catalog Generator
=====================================================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Turn two public astronomical datasets into the two JSON files
             Hypatia reads (hypatia_stars.json, hypatia_constellations.json).

This is a ONE-OFF, HAND-RUN SCRIPT. It is not imported by anything, not a
Kairos worker, and not part of the running dashboard — Matthew runs it
manually when the catalog needs regenerating:

    python tools/dev_build_hypatia_catalog.py

It NEVER touches hypatia_lore.json. That file is hand-curated, has a
different lifecycle from the generated catalogs, and this script's write
path must not clobber it.

Source:      Stars   — HYG Database v41 CSV (CC BY-SA), astronexus/HYG-Database
                        https://github.com/astronexus/HYG-Database
             Lines   — Stellarium "modern" sky culture (GPL/CC), Stellarium/stellarium
                        https://github.com/Stellarium/stellarium
                        NOTE: the classic constellationship.fab format is gone
                        upstream — Stellarium now ships one index.json per sky
                        culture. This script reads that JSON shape (a "lines"
                        polyline list per constellation, expanded here into
                        consecutive HIP pairs), not the old .fab row format.

Requires:    requests (existing dependency), csv/json/os/tempfile (stdlib).
"""

import csv
import io
import json
import logging
import os
import tempfile
from typing import Any

import requests

log = logging.getLogger("METIS.dev_build_hypatia_catalog")

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARS_OUT = os.path.join(_APP_ROOT, "hypatia_stars.json")
_CONST_OUT = os.path.join(_APP_ROOT, "hypatia_constellations.json")

_HYG_URL = "https://raw.githubusercontent.com/astronexus/HYG-Database/main/hyg/CURRENT/hygdata_v41.csv"
_SKYCULTURE_URL = "https://raw.githubusercontent.com/Stellarium/stellarium/master/skycultures/modern/index.json"

_MAG_LIMIT = 4.5   # locked (HANDOFF §1)
_REQUEST_TIMEOUT = 30

# ── The standard 88 IAU constellations, abbreviation -> full name ────────────
# Stellarium's own common_name is a mythic epithet ("Eagle" for Aql), not the
# proper name ("Aquila") this catalog wants, so we keep our own table.
_IAU_NAMES = {
    "And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aql": "Aquila",
    "Aqr": "Aquarius", "Ara": "Ara", "Ari": "Aries", "Aur": "Auriga",
    "Boo": "Bootes", "CMa": "Canis Major", "CMi": "Canis Minor",
    "CVn": "Canes Venatici", "Cae": "Caelum", "Cam": "Camelopardalis",
    "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia",
    "Cen": "Centaurus", "Cep": "Cepheus", "Cet": "Cetus", "Cha": "Chamaeleon",
    "Cir": "Circinus", "Cnc": "Cancer", "Col": "Columba",
    "Com": "Coma Berenices", "CrA": "Corona Australis", "CrB": "Corona Borealis",
    "Crt": "Crater", "Cru": "Crux", "Crv": "Corvus", "Cyg": "Cygnus",
    "Del": "Delphinus", "Dor": "Dorado", "Dra": "Draco", "Equ": "Equuleus",
    "Eri": "Eridanus", "For": "Fornax", "Gem": "Gemini", "Gru": "Grus",
    "Her": "Hercules", "Hor": "Horologium", "Hya": "Hydra", "Hyi": "Hydrus",
    "Ind": "Indus", "LMi": "Leo Minor", "Lac": "Lacerta", "Leo": "Leo",
    "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus", "Lyn": "Lynx",
    "Lyr": "Lyra", "Men": "Mensa", "Mic": "Microscopium", "Mon": "Monoceros",
    "Mus": "Musca", "Nor": "Norma", "Oct": "Octans", "Oph": "Ophiuchus",
    "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus", "Per": "Perseus",
    "Phe": "Phoenix", "Pic": "Pictor", "PsA": "Piscis Austrinus",
    "Psc": "Pisces", "Pup": "Puppis", "Pyx": "Pyxis", "Ret": "Reticulum",
    "Scl": "Sculptor", "Sco": "Scorpius", "Sct": "Scutum", "Ser": "Serpens",
    "Sex": "Sextans", "Sge": "Sagitta", "Sgr": "Sagittarius", "Tau": "Taurus",
    "Tel": "Telescopium", "TrA": "Triangulum Australe", "Tri": "Triangulum",
    "Tuc": "Tucana", "UMa": "Ursa Major", "UMi": "Ursa Minor", "Vel": "Vela",
    "Vir": "Virgo", "Vol": "Volans", "Vul": "Vulpecula",
}


def _fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _build_stars(csv_text: str) -> dict[int, dict[str, Any]]:
    """
    Parse the HYG CSV into {hip_int: {"name", "ra", "dec", "mag"}}, kept at
    mag <= _MAG_LIMIT with a valid HIP. Multi-star systems repeat a HIP across
    component rows (the 'comp' column) — dedup by HIP, keeping the brightest
    (lowest mag) row so the dict has one entry per HIP.
    """
    stars: dict[int, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        hip_raw = (row.get("hip") or "").strip()
        if not hip_raw:
            continue
        try:
            hip = int(hip_raw)
            mag = float(row["mag"])
            ra_deg = float(row["ra"]) * 15.0   # HYG ra is decimal hours
            dec_deg = float(row["dec"])
        except (TypeError, ValueError):
            continue
        if mag > _MAG_LIMIT:
            continue
        existing = stars.get(hip)
        if existing is not None and existing["mag"] <= mag:
            continue
        stars[hip] = {
            "id": hip,
            "name": (row.get("proper") or "").strip() or None,
            "ra": round(ra_deg, 4),
            "dec": round(dec_deg, 4),
            "mag": round(mag, 2),
        }
    return stars


def _add_missing_endpoints(stars: dict[int, dict[str, Any]], csv_text: str, needed_hips: set[int]) -> int:
    """
    Referential integrity pass: any line-endpoint HIP absent from the trimmed
    star set is pulled in regardless of magnitude, so no constellation draws
    with a broken joint. Returns how many were pulled in.
    """
    missing = needed_hips - set(stars.keys())
    if not missing:
        return 0
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        hip_raw = (row.get("hip") or "").strip()
        if not hip_raw:
            continue
        try:
            hip = int(hip_raw)
        except ValueError:
            continue
        if hip not in missing:
            continue
        try:
            mag = float(row["mag"])
            ra_deg = float(row["ra"]) * 15.0
            dec_deg = float(row["dec"])
        except (TypeError, ValueError):
            continue
        existing = stars.get(hip)
        if existing is not None and existing["mag"] <= mag:
            continue
        stars[hip] = {
            "id": hip,
            "name": (row.get("proper") or "").strip() or None,
            "ra": round(ra_deg, 4),
            "dec": round(dec_deg, 4),
            "mag": round(mag, 2),
        }
    return len(missing & set(stars.keys()))


def _build_constellations(index_json: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Stellarium's index.json ships one polyline list per constellation
    ("lines": [[hip, hip, hip, ...], ...] — consecutive HIPs, not pairs).
    Expand each polyline into consecutive [a, b] segment pairs.
    """
    out = []
    for entry in index_json.get("constellations", []):
        raw_id = entry.get("id", "")
        abbr = raw_id.replace("CON modern ", "").strip()
        if not abbr:
            continue
        name = _IAU_NAMES.get(abbr, abbr)
        pairs = []
        for polyline in entry.get("lines", []):
            for a, b in zip(polyline, polyline[1:]):
                pairs.append([a, b])
        out.append({"abbr": abbr, "name": name, "lines": pairs})
    out.sort(key=lambda c: c["name"])
    return out


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def main() -> None:
    print("[dev_build_hypatia_catalog] Downloading HYG star database...")
    csv_text = _fetch_text(_HYG_URL)
    stars = _build_stars(csv_text)
    print(f"[dev_build_hypatia_catalog] {len(stars)} stars at mag <= {_MAG_LIMIT}")

    print("[dev_build_hypatia_catalog] Downloading Stellarium modern sky culture...")
    index_json = json.loads(_fetch_text(_SKYCULTURE_URL))
    constellations = _build_constellations(index_json)
    print(f"[dev_build_hypatia_catalog] {len(constellations)} constellations")

    needed = {hip for c in constellations for pair in c["lines"] for hip in pair}
    pulled_in = _add_missing_endpoints(stars, csv_text, needed)
    print(f"[dev_build_hypatia_catalog] referential integrity: pulled in {pulled_in} "
          f"additional stars below the magnitude cutoff")

    still_missing = needed - set(stars.keys())
    if still_missing:
        log.warning(f"{len(still_missing)} line-endpoint HIPs never resolved "
                     f"to a star row: {sorted(still_missing)[:10]}...")

    stars_payload = {
        "_source": "HYG Database v41 (CC BY-SA) — astronexus/HYG-Database",
        "stars": list(stars.values()),
    }
    const_payload = {
        "_source": "Stellarium 'modern' sky culture (GPL/CC) — Stellarium/stellarium",
        "constellations": constellations,
    }
    _atomic_write_json(_STARS_OUT, stars_payload)
    _atomic_write_json(_CONST_OUT, const_payload)
    print(f"[dev_build_hypatia_catalog] wrote {_STARS_OUT}")
    print(f"[dev_build_hypatia_catalog] wrote {_CONST_OUT}")


if __name__ == "__main__":
    main()
