# SPDX-License-Identifier: MIT
"""
degurba_world.py
================

Degree of Urbanisation (DEGURBA) for postcodes in any country, from the
Global Human Settlement Layer (GHSL R2023A) of the European Commission's
Joint Research Centre.

Each postcode is checked and located, and the point is then classified on the
GHS-SMOD grid and by its GADM 4.1 administrative unit in GHS-DUC. Start with
``DegurbaWorld``.

Documentation (usage, method, output columns, data sources and how to cite):
README.md and the docs folder, https://github.com/ivancasanovaas/degurba_world

Licence: MIT (c) 2026 Ivan Casanovas Rodríguez. See LICENSE.
"""

from __future__ import annotations

import difflib
import io
import math
import os
import re
import shutil
import time
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

__version__ = "0.1.0"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
CACHE_DIR = Path.home() / ".cache" / "degurba_world"

GHSL_FTP = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"
SMOD_URL = (
    GHSL_FTP
    + "/GHS_SMOD_GLOBE_R2023A/GHS_SMOD_E{epoch}_GLOBE_R2023A_4326_30ss/V2-0/"
    + "GHS_SMOD_E{epoch}_GLOBE_R2023A_4326_30ss_V2_0.zip"
)
DUC_URL = GHSL_FTP + "/GHS_DUC_GLOBE_R2023A/V2-0/GHS_DUC_MT_GLOBE_R2023A_V2_0.zip"
GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_{iso3}.gpkg"
# GeoNames files with complete postcodes for CA, GB and NL (see
# FULL_POSTCODE_COUNTRIES). The other GeoNames files are downloaded by pgeocode.
GEONAMES_FULL_URL = "https://download.geonames.org/export/zip/{cc}_full.csv.zip"

EPOCHS = tuple(range(1975, 2031, 5))  # GHSL R2023A epochs

SMOD_L2 = {
    30: "Urban centre",
    23: "Dense urban cluster",
    22: "Semi-dense urban cluster",
    21: "Suburban or peri-urban",
    13: "Rural cluster",
    12: "Low density rural",
    11: "Very low density rural",
    10: "Water",
}
DUC_L2 = {
    30: "City",
    23: "Dense town",
    22: "Semi-dense town",
    21: "Suburban or peri-urban area",
    13: "Village",
    12: "Dispersed rural area",
    11: "Mostly uninhabited area",
    10: "Water",
}
L1 = {3: "City", 2: "Town and semi-dense area", 1: "Rural area"}
SMOD_NODATA = -200

# How far a point may be snapped to the nearest administrative unit when no
# polygon contains it. A postcode centroid can fall just offshore, which is
# worth repairing; anything farther is a point outside the country, which must
# not be. See GADMLocator.locate.
MAX_SNAP_KM = 25.0


def l2_to_l1(code) -> Optional[int]:
    """30 -> 3, 21/22/23 -> 2, 11/12/13 -> 1, 10 (water)/nodata -> None."""
    if code is None or pd.isna(code):
        return None
    code = int(code)
    if code in (1, 2, 3):
        return code
    if code == 10 or code < 10:
        return None
    return code // 10


# ----------------------------------------------------------------------------
# Download helpers
# ----------------------------------------------------------------------------
def _download(url: str, dest: Path, quiet: bool = False) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if not quiet:
        print(f"[degurba_world] downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": f"degurba_world/{__version__}"})
    with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    tmp.rename(dest)
    return dest


def _check_epoch(epoch: int) -> int:
    epoch = int(epoch)
    if epoch not in EPOCHS:
        raise ValueError(f"epoch must be one of {EPOCHS}, got {epoch}")
    return epoch


# ----------------------------------------------------------------------------
# Country codes
# ----------------------------------------------------------------------------
# Codes used by Eurostat (and other sources) that differ from ISO 3166-1.
COUNTRY_ALIASES = {"UK": "GB", "EL": "GR"}
KOSOVO = ("XK", "XKO")  # not in ISO 3166-1; XKO is the GADM code

# Common names that pycountry does not recognise (keys are lower-case, no
# accents): English short names plus frequent Spanish/Catalan names.
COUNTRY_NAME_ALIASES = {
    "russia": "RU", "turkey": "TR", "ivory coast": "CI", "holland": "NL",
    "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "great britain": "GB", "britain": "GB", "macedonia": "MK", "swaziland": "SZ",
    "burma": "MM", "cape verde": "CV", "east timor": "TL", "vatican": "VA",
    "dr congo": "CD", "drc": "CD", "democratic republic of the congo": "CD",
    "republic of the congo": "CG", "north korea": "KP", "south korea": "KR",
    "korea": "KR", "laos": "LA", "syria": "SY", "moldova": "MD",
    "venezuela": "VE", "palestine": "PS", "brunei": "BN", "micronesia": "FM",
    "espana": "ES", "espanya": "ES", "brasil": "BR", "estados unidos": "US",
    "eeuu": "US", "ee uu": "US", "reino unido": "GB", "regne unit": "GB",
    "alemania": "DE", "alemanya": "DE", "francia": "FR", "franca": "FR",
    "italia": "IT", "paises bajos": "NL", "holanda": "NL", "suiza": "CH",
    "suissa": "CH", "belgica": "BE", "suecia": "SE", "noruega": "NO",
    "dinamarca": "DK", "finlandia": "FI", "grecia": "GR", "irlanda": "IE",
    "polonia": "PL", "rumania": "RO", "japon": "JP", "marruecos": "MA",
    "marroc": "MA", "argelia": "DZ", "rusia": "RU", "turquia": "TR",
    "sudafrica": "ZA", "nueva zelanda": "NZ", "corea del sur": "KR",
    "filipinas": "PH", "egipto": "EG",
}


@lru_cache(maxsize=None)
def country_codes(country: str) -> tuple[str, str]:
    """Return (ISO2, ISO3) from an ISO2 / ISO3 code or an English name.

    Eurostat codes are accepted (UK -> GB, EL -> GR, XK = Kosovo), and so are
    common English and Spanish/Catalan names (Russia, España, Brasil). Names
    are matched exactly or with a small spelling tolerance, never against
    provinces, so an unknown value raises LookupError instead of a wrong guess.
    """
    import pycountry

    c = str(country).strip()
    if not c:
        raise LookupError("empty country")
    c = COUNTRY_ALIASES.get(c.upper(), c)
    c = COUNTRY_NAME_ALIASES.get(_norm_name(c), c)
    if c.upper() in ("XK", "XKX", "XKO") or _norm_name(c) == "kosovo":
        return KOSOVO
    rec = None
    if len(c) == 2:
        rec = pycountry.countries.get(alpha_2=c.upper())
    elif len(c) == 3:
        rec = pycountry.countries.get(alpha_3=c.upper())
    if rec is None:
        try:
            rec = pycountry.countries.lookup(c)
        except LookupError:
            rec = None
    if rec is None:
        names = {}
        for r in pycountry.countries:
            for attr in ("name", "official_name", "common_name"):
                value = getattr(r, attr, None)
                if value:
                    names[_norm_name(value)] = r
        close = difflib.get_close_matches(_norm_name(c), list(names), n=1, cutoff=0.85)
        if not close:
            raise LookupError(f"unknown country {country!r}; use an ISO 3166 code")
        rec = names[close[0]]
    return rec.alpha_2, rec.alpha_3


def _norm_name(s) -> str:
    """Case-, accent- and punctuation-insensitive form of a name."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[\W_]+", " ", s).strip()


# ----------------------------------------------------------------------------
# 1) Postcode checking
# ----------------------------------------------------------------------------
# Countries whose postcodes can start with 0, with their length in digits.
# Leading zeros are lost when postcodes are stored as numbers (US
# "02134" -> 2134), so shorter all-digit inputs are padded back.
PAD_LENGTH = {
    "ES": 5, "FR": 5, "IT": 5, "DE": 5, "US": 5, "MX": 5, "FI": 5, "TR": 5,
    "UA": 5, "MY": 5, "KR": 5, "PL": 5, "LT": 5,
    "DK": 4, "NO": 4, "AU": 4, "NZ": 4, "ZA": 4,
    "CO": 6, "CN": 6, "SG": 6, "RO": 6,
    "JP": 7, "BR": 8,
}

# Stricter patterns where Google's is too loose to tell countries apart.
# Eircode: routing key (letter, digit, digit or W) + 4 characters from the
# Eircode alphabet; Google's rule accepts any 7 letters/digits.
RULE_OVERRIDES = {"IE": r"[ACDEFHKNPRTVWXY]\d[\dW] ?[\dACDEFHKNPRTVWXY]{4}"}

# Countries where a shortened postcode still identifies a locality: the pattern
# such a shortened code must match, and the warning that explains it.
# Portuguese codes have 4+3 digits (NNNN-NNN) and only the full form is
# official, but the first four digits identify the locality. Shortened codes
# pass the format check and are geocoded from their prefix ("partial").
LOCALITY_PREFIXES = {
    "PT": (r"\d{4}",
           "only the first 4 digits of a Portuguese 4+3 postcode were given; "
           "they identify the locality, not the street"),
}

# Survey answers that mean "no postcode given" rather than a postcode.
NON_ANSWERS = {"", "NA", "N/A", "NAN", "NONE", "NULL", "-", "--", "?",
               "NS", "NC", "NS/NC", "UNKNOWN", "DESCONOCIDO"}

# Real postcodes that are not home addresses.
SPECIAL_POSTCODES = {
    ("CA", "H0H 0H0"): "H0H 0H0 is Canada Post's address for letters to Santa Claus",
}
SPECIAL_PREFIXES = {
    ("CA", "K1A"): "K1A codes are reserved for federal government offices in "
                   "Ottawa-Gatineau, not homes",
}
# US military and diplomatic post offices: 090-098 (Armed Forces Europe), 340
# (Americas) and 962-966 (Pacific). In GeoNames these ranges hold only APO/FPO
# codes, and their coordinates are the overseas base served, not a US address.
SPECIAL_PREFIXES.update({
    ("US", f"{n:03d}"): ("US military or diplomatic post office (APO/FPO/DPO), "
                         "not a residential address; the coordinates are the "
                         "overseas base it serves, not a place in the US")
    for n in list(range(90, 99)) + [340] + list(range(962, 967))
})

# Higher-level regions that people often report instead of a province/state,
# mapped to the province keys of Google's address data.
REGION_ALIASES = {
    "ES": {  # autonomous communities -> provinces
        ("andalucia", "andalusia"): "AL CA CO GR H J MA SE",
        ("aragon",): "HU TE Z",
        ("principado de asturias",): "O",
        ("canarias", "islas canarias", "canary islands"): "GC TF",
        ("castilla la mancha", "castile la mancha"): "AB CR CU GU TO",
        ("castilla y leon", "castile and leon"): "AV BU LE P SA SG SO VA ZA",
        ("cataluna", "catalunya", "catalonia"): "B GI L T",
        ("comunidad valenciana", "comunitat valenciana",
         "valencian community"): "A CS V",
        ("extremadura",): "BA CC",
        ("galicia", "galiza"): "C LU OR PO",
        ("comunidad de madrid", "community of madrid"): "M",
        ("region de murcia",): "MU",
        ("comunidad foral de navarra", "nafarroa"): "NA",
        ("pais vasco", "euskadi", "basque country"): "VI SS BI",
        ("balearic islands",): "PM",
    },
}


def _clean_raw(value) -> str:
    """Input value -> stripped string ('' for None/NaN).

    Repairs numbers that went through a spreadsheet: 8001.0 -> '8001' and
    '28.001' (thousands separator) -> '28001'.
    """
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return s


def _is_missing(raw: str) -> bool:
    return raw.strip().upper() in NON_ANSWERS


def _compact(pc) -> str:
    return re.sub(r"[\s\-]", "", str(pc).upper())


@lru_cache(maxsize=None)
def _address_data(iso2: str) -> dict:
    """Google address metadata for one country (bundled in google-i18n-address)."""
    import i18naddress

    try:
        return i18naddress.load_validation_data(iso2.lower())
    except Exception:  # unknown country code
        return {}


@lru_cache(maxsize=None)
def _postcode_rule(iso2: str):
    """(full-match regex, province/state prefix regexes, example codes)."""
    top = _address_data(iso2).get(iso2, {})
    pattern = RULE_OVERRIDES.get(iso2) or top.get("zip")
    rule = re.compile(pattern) if pattern else None
    keys = top["sub_keys"].split("~") if top.get("sub_keys") else []
    zips = top["sub_zips"].split("~") if top.get("sub_zips") else []
    # Prefixes are enforced only when *every* province/state has a range.
    prefixes = ([re.compile(z) for z in zips]
                if keys and len(zips) == len(keys) and all(zips) else None)
    examples = [e.strip() for e in top.get("zipex", "").split(",") if e.strip()]
    return rule, prefixes, examples


def _example_mask(iso2: str, n: int) -> Optional[str]:
    """Separator layout (e.g. '#####-###') shared by all examples of length n."""
    masks = {re.sub(r"[0-9A-Z]", "#", e.upper())
             for e in _postcode_rule(iso2)[2] if len(_compact(e)) == n}
    return masks.pop() if len(masks) == 1 else None


def _layout(s: str) -> tuple:
    """Positions (counted in letters/digits) where a space or hyphen occurs."""
    out, n = [], 0
    for ch in s:
        if ch in " -":
            if n and (not out or out[-1] != n):
                out.append(n)
        else:
            n += 1
    return tuple(out)


def _normalise_postcode(postcode, iso2: str, pad: bool = True) -> str:
    """Canonical spelling of a postcode for a country.

    Upper-cases, restores lost leading zeros (US 2134 -> 02134) and puts
    spaces/hyphens where the country uses them (GB sw1a1aa -> SW1A 1AA,
    BR 01310100 -> 01310-100).
    """
    pc = re.sub(r"\s+", " ", _clean_raw(postcode).upper())
    compact = _compact(pc)
    n = PAD_LENGTH.get(iso2)
    if pad and n and compact.isdigit() and len(compact) < n:
        compact = pc = compact.zfill(n)
    # One space before the last part (GB inward code, CA LDU, NL letters,
    # IE unique identifier), unless the user put a separator elsewhere.
    spaced = None
    if iso2 == "GB":
        if compact.startswith("BFPO"):
            spaced = f"BFPO {compact[4:]}"
        elif 5 <= len(compact) <= 7:
            spaced = f"{compact[:-3]} {compact[-3:]}"
    elif iso2 == "CA" and re.fullmatch(r"[A-Z]\d[A-Z]\d[A-Z]\d", compact):
        spaced = f"{compact[:3]} {compact[3:]}"
    elif iso2 == "NL" and re.fullmatch(r"\d{4}[A-Z]{2}", compact):
        spaced = f"{compact[:4]} {compact[4:]}"
    elif iso2 == "IE" and len(compact) == 7:
        spaced = f"{compact[:3]} {compact[3:]}"
    if spaced is not None:
        return spaced if _layout(pc) in ((), _layout(spaced)) else pc
    mask = _example_mask(iso2, len(compact))
    if mask:
        chars = iter(compact)
        candidate = "".join(next(chars) if m == "#" else m for m in mask)
        rule = _postcode_rule(iso2)[0]
        # Re-space only if the input has no separators, the same layout, or is
        # already valid; '01310-100' must not become Lebanese '0131 0100'.
        compatible = (not _layout(pc) or _layout(pc) == _layout(mask)
                      or (rule is not None and rule.fullmatch(pc)))
        if compatible and (rule is None or rule.fullmatch(candidate)):
            return candidate
    return pc


@lru_cache(maxsize=None)
def _locality_prefix_rule(iso2: str):
    pattern = LOCALITY_PREFIXES.get(iso2, (None, None))[0]
    return re.compile(pattern) if pattern else None


def _is_locality_prefix(pc: str, iso2: str) -> bool:
    """Is this a postcode shortened to the part that identifies the locality?"""
    rule = _locality_prefix_rule(iso2)
    return rule is not None and rule.fullmatch(_compact(pc)) is not None


def _format_ok(pc: str, iso2: str) -> Optional[bool]:
    rule, prefixes, _ = _postcode_rule(iso2)
    if rule is None:
        return None
    if not rule.fullmatch(pc):
        # A code shortened to its locality part (Portuguese 4 of 4+3) is
        # accepted; it is then geocoded from the prefix.
        return _is_locality_prefix(pc, iso2)
    return prefixes is None or any(p.match(pc) for p in prefixes)


def _placeholder_reason(compact: str) -> Optional[str]:
    if len(compact) >= 3 and len(set(compact)) == 1:
        return "the same character repeated"
    if compact.isdigit() and len(compact) >= 4 and (
            compact in "0123456789" or compact in "9876543210"):
        return "consecutive digits"
    return None


def check_postcode(postcode, country: str) -> dict:
    """Check a postcode's format for a country, without looking it up.

    Returns a dict with:

    * ``postcode``: canonical spelling (``'sw1a1aa'`` -> ``'SW1A 1AA'``).
    * ``format_ok``: True / False, or None when no format is known for the
      country (e.g. countries without postcodes). Formats come from Google's
      address metadata (package ``google-i18n-address``). Where that data
      gives a postcode range for every province or state (e.g. US, BR, JP),
      the code must also fall in one of them.
    * ``warnings``: reasons to doubt a well-formed code (placeholder patterns
      such as ``00000``, known non-residential codes, a code shortened to the
      part that identifies the locality).
    """
    iso2, _ = country_codes(country)
    raw = _clean_raw(postcode)
    if _is_missing(raw):
        return {"postcode": "", "format_ok": None, "warnings": []}
    pc = _normalise_postcode(raw, iso2)
    fmt = _format_ok(pc, iso2)
    warnings = []
    reason = _placeholder_reason(_compact(pc))
    if reason:
        warnings.append(f"looks like a placeholder ({reason})")
    if _is_locality_prefix(pc, iso2):
        warnings.append(LOCALITY_PREFIXES[iso2][1])
    if (iso2, pc) in SPECIAL_POSTCODES:
        warnings.append(SPECIAL_POSTCODES[(iso2, pc)])
    for (cc, prefix), message in SPECIAL_PREFIXES.items():
        if cc == iso2 and pc.startswith(prefix):
            warnings.append(message)
    if fmt is None:
        warnings.append(f"no postcode format is known for {iso2}, so it was not checked")
    return {"postcode": pc, "format_ok": fmt, "warnings": warnings}


@lru_cache(maxsize=None)
def _region_table(iso2: str) -> tuple[dict, dict]:
    """(normalised region name/code -> region keys, region key -> postcode prefix)."""
    names: dict[str, set] = {}
    zips: dict[str, Optional[str]] = {}
    for k, entry in _address_data(iso2).items():
        base = k.split("--")[0]            # 'ES/L--ca' -> 'ES/L' (language variant)
        if base.count("/") != 1 or not base.startswith(iso2 + "/"):
            continue
        key = entry.get("key") or base.split("/")[1]
        if entry.get("zip") or key not in zips:
            zips[key] = entry.get("zip") or zips.get(key)
        for label in (key, entry.get("name"), entry.get("lname"), entry.get("isoid")):
            if label:
                names.setdefault(_norm_name(label), set()).add(key)
    for labels, keys in REGION_ALIASES.get(iso2, {}).items():
        for label in labels:
            names.setdefault(_norm_name(label), set()).update(keys.split())
    return names, zips


_REGION_WORDS = re.compile(
    r"^(?:provincia|province|prov|state|estado|departamento|department)"
    r"(?: (?:de|del|of))? | (?:province|provincia|state)$")


def _region_match(pc: str, iso2: str, region) -> tuple[Optional[bool], Optional[str]]:
    """(match, reason) -- reason explains why match is None."""
    names, zips = _region_table(iso2)
    label = _norm_name(re.sub(r"\(.*?\)", " ", str(region)))
    tries = [label]
    if label.startswith(iso2.casefold() + " "):   # ISO 3166-2, e.g. 'US-NY'
        tries.append(label[len(iso2) + 1:])
    tries.append(_REGION_WORDS.sub("", label).strip())
    keys = next((names[t] for t in tries if t in names), None)
    if not keys:
        return None, f"region '{region}' not recognised for {iso2}, so it was not checked"
    prefixes = [zips[k] for k in keys if zips.get(k)]
    if not prefixes:
        return None, f"no postcode ranges are known for the regions of {iso2}"
    return any(re.match(p, pc) for p in prefixes), None


def region_check(postcode, country: str, region) -> Optional[bool]:
    """Is the postcode inside the reported province/state?

    ``region`` can be a name in any of the country's languages in Google's
    address data ('New York'), a code ('NY') or an ISO 3166-2 code ('US-NY');
    for Spain, autonomous communities are accepted too. Returns True/False,
    or None when the region is not recognised or no postcode ranges are known.
    """
    iso2, _ = country_codes(country)
    return _region_match(_normalise_postcode(postcode, iso2), iso2, region)[0]


@lru_cache(maxsize=None)
def _countries_with_formats() -> tuple:
    import i18naddress

    codes = []
    for f in sorted(os.listdir(i18naddress.VALIDATION_DATA_DIR)):
        m = re.fullmatch(r"([a-z]{2})\.json", f)
        if m and m.group(1) != "zz" and _postcode_rule(m.group(1).upper())[0] is not None:
            codes.append(m.group(1).upper())
    return tuple(codes)


def _parse_among(among) -> Optional[list]:
    if among is None:
        return None
    if isinstance(among, str):
        among = re.split(r"[,;\s]+", among.strip())
    codes = [country_codes(c)[0] for c in among if str(c).strip()]
    return list(dict.fromkeys(codes)) or None


def candidate_countries(postcode, among=None) -> list:
    """Countries (ISO2) whose postcode format fits this postcode.

    ``among`` restricts the search, e.g. to your study countries. Only the
    format is checked: '10115' fits dozens of countries, 'SW1A 1AA' only the
    United Kingdom.
    """
    raw = _clean_raw(postcode)
    if _is_missing(raw):
        return []
    pool = _parse_among(among) or _countries_with_formats()
    # Countries where the code fits as written come first; restoring lost
    # leading zeros (2134 -> 02134) is only tried if none fits.
    exact = [cc for cc in pool if _format_ok(_normalise_postcode(raw, cc, pad=False), cc)]
    return exact or [cc for cc in pool if _format_ok(_normalise_postcode(raw, cc), cc)]


@dataclass
class CountryGuess:
    country: Optional[str]
    candidates: list              # countries whose *format* fits
    method: Optional[str] = None  # 'format', 'existence' or None
    existing: Optional[list] = None     # of those, where the code really exists
                                        # (None when existence was not checked)
    unverifiable: Optional[list] = None  # of those, the ones with no postcode
                                         # data, so neither present nor absent


def infer_country(postcode, among=None, check_existence: Optional[bool] = None,
                  cache_dir: Path = CACHE_DIR) -> CountryGuess:
    """Work out a postcode's country, but only when the answer is unique.

    1. Keep the countries whose format fits (``candidate_countries``).
    2. Unless ``check_existence=False``, look the code up in each of them and
       keep those where it really exists.

    Fitting a format means little on its own: a five-digit code fits the
    format of dozens of countries but may exist in only a few of them.
    ``candidates`` is always the long format list; ``existing`` is the short
    answer.

    A country with no postcode data (``has_postcode_data``) can be neither
    confirmed nor ruled out, so it is listed in ``unverifiable`` instead of
    being silently dropped.

    ``country`` is set only when exactly one country survives: uniquely by
    format, or uniquely by existence provided nothing was left unverifiable
    (an explicit ``among`` counts as your assurance that the answer is in that
    list, so the check is allowed to conclude there).

    The first call builds a postcode index per country, which downloads a few
    MB each; afterwards lookups are in-memory, so checking further postcodes
    costs milliseconds. Pass ``check_existence=False`` to skip it entirely.
    """
    cands = candidate_countries(postcode, among)
    if len(cands) == 1:
        return CountryGuess(cands[0], cands, "format")
    if check_existence is None:
        check_existence = True
    if len(cands) > 1 and check_existence:
        found, unverifiable = [], []
        for cc in cands:
            if not has_postcode_data(cc):
                unverifiable.append(cc)      # no data: not evidence of absence
            elif pd.notna(geocode_postcode(postcode, cc, use_nominatim_fallback=False,
                                           cache_dir=cache_dir)["lat"]):
                found.append(cc)
        if len(found) == 1 and (not unverifiable or among is not None):
            return CountryGuess(found[0], cands, "existence", found, unverifiable)
        return CountryGuess(None, cands, None, found, unverifiable)
    return CountryGuess(None, cands, None)


# ----------------------------------------------------------------------------
# 2) Postcode -> coordinates
# ----------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _pgeocode_nomi(iso2: str):
    import pgeocode

    return pgeocode.Nominatim(iso2)


# ----------------------------------------------------------------------------
# Your own postcode tables
# ----------------------------------------------------------------------------
# Postcode tables registered with register_postcodes(), by ISO2 country code.
# They cover countries whose postcodes GeoNames does not publish, or correct
# and extend a country it does.
POSTCODE_TABLES: dict[str, Path] = {}

# Accepted column names in a postcode table, in order of preference.
_TABLE_COLUMNS = {
    "postcode": ("postcode", "postal_code", "postalcode", "zip", "zipcode",
                 "zip_code", "code", "mikud", "cod_postal"),
    "lat": ("lat", "latitude", "y"),
    "lon": ("lon", "lng", "long", "longitude", "x"),
    "place": ("place", "place_name", "locality", "city", "town", "name",
              "settlement"),
}
# Shortest prefix of a postcode that may still be matched against a table.
MIN_PREFIX = 2


def register_postcodes(country: str, path) -> int:
    """Use your own postcode table for a country, and report how many it holds.

    ``path`` is a CSV/TSV file (plain, ``.gz`` or ``.zip``) with one row per
    postcode and columns for the code, latitude and longitude, plus an optional
    place name. Column names are matched case-insensitively against common
    spellings (``postcode``/``postal_code``/``zip``,
    ``lat``/``latitude``, ``lon``/``lng``/``longitude``,
    ``place``/``city``/``locality``). Lines starting with ``#`` are ignored, so
    the file can record where the data came from.

    The table is searched *before* GeoNames, so it also lets you correct or
    extend a country GeoNames already covers. Codes are matched ignoring spaces
    and hyphens. A table does not have to be complete or full-precision: a
    query longer than the codes in the table falls back to its longest matching
    prefix, and a query shorter than them is matched against every code that
    starts with it. Either way ``geocode_precision`` becomes ``"partial"``, so
    a table of postcode prefixes still classifies full codes.
    """
    iso2, _ = country_codes(country)
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    POSTCODE_TABLES[iso2] = path
    _local_postcodes.cache_clear()
    return len(_local_postcodes(iso2, str(path)))


def clear_postcodes(country=None) -> None:
    """Forget the table registered for a country, or all of them."""
    if country is None:
        POSTCODE_TABLES.clear()
    else:
        POSTCODE_TABLES.pop(country_codes(country)[0], None)
    _local_postcodes.cache_clear()


def _pick_column(columns, wanted: tuple) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in columns}
    return next((lower[w] for w in wanted if w in lower), None)


@lru_cache(maxsize=None)
def _local_postcodes(iso2: str, path: str) -> pd.DataFrame:
    """A registered postcode table, indexed by compact code."""
    sep = "\t" if Path(path).name.lower().endswith((".tsv", ".tsv.gz", ".tab")) else None
    df = pd.read_csv(path, sep=sep, engine="python", dtype=str,
                     keep_default_na=False, comment="#")
    cols = {name: _pick_column(df.columns, wanted)
            for name, wanted in _TABLE_COLUMNS.items()}
    missing = [n for n in ("postcode", "lat", "lon") if cols[n] is None]
    if missing:
        raise ValueError(
            f"{path}: no column found for {', '.join(missing)}. Expected one of "
            + "; ".join(f"{n}: {'/'.join(_TABLE_COLUMNS[n])}" for n in missing))
    out = pd.DataFrame({
        "key": df[cols["postcode"]].map(_compact),
        "lat": pd.to_numeric(df[cols["lat"]], errors="coerce"),
        "lon": pd.to_numeric(df[cols["lon"]], errors="coerce"),
        "place": df[cols["place"]] if cols["place"] else None,
    })
    out = out[out["key"] != ""].dropna(subset=["lat", "lon"])
    return out.groupby("key").agg(lat=("lat", "mean"), lon=("lon", "mean"),
                                  place=("place", _modal_place))


def _local_lookup(iso2: str, pc: str) -> Optional[dict]:
    """Look a postcode up in the table registered for its country, if any.

    Exact code first, then the longest prefix of the code that the table knows
    (a locality-level table resolving a full code), then every code in the
    table that starts with the query (a short code against a full table).
    """
    path = POSTCODE_TABLES.get(iso2)
    if path is None:
        return None
    table = _local_postcodes(iso2, str(path))
    if table.empty:
        return None
    key = _compact(pc)
    if key in table.index:
        r = table.loc[key]
        return _hit(r.lat, r.lon, r.place, "local", "full")
    for n in range(len(key) - 1, MIN_PREFIX - 1, -1):
        if key[:n] in table.index:
            r = table.loc[key[:n]]
            return _hit(r.lat, r.lon, r.place, "local", "partial")
    if len(key) >= MIN_PREFIX:
        longer = table.index.str.startswith(key)
        if longer.any():
            rows = table[longer]
            return _hit(rows["lat"].mean(), rows["lon"].mean(),
                        _modal_place(rows["place"]), "local", "partial")
    return None


@lru_cache(maxsize=None)
def _geonames_countries() -> frozenset:
    """ISO2 codes for which GeoNames publishes a postcode dump (empty if unknown)."""
    try:
        import pgeocode

        return frozenset(pgeocode.COUNTRIES_VALID)
    except Exception:
        return frozenset()


def has_postcode_data(country: str) -> bool:
    """Can this country's postcodes be looked up at all?

    True if you registered a table for it (``register_postcodes``) or GeoNames
    publishes one. Many countries that use postcodes are not in GeoNames.
    Without a table of your own, their codes come back as
    ``no_reference_data`` unless OpenStreetMap knows them, and the town or
    city can be given instead (``place=``).
    """
    iso2 = country_codes(country)[0]
    if iso2 in POSTCODE_TABLES:
        return True
    known = _geonames_countries()
    if not known:            # pgeocode unavailable: cannot tell, assume it is
        return True
    return iso2 in known


# Countries for which GeoNames' standard dump only holds the first part of the
# postcode (CA: 3-character FSA; GB: outward code; NL: 4 digits). GeoNames
# publishes separate *_full files with complete codes; we use them first.
FULL_POSTCODE_COUNTRIES = ("CA", "GB", "NL")
PARTIAL_COUNTRIES = ("CA", "GB", "IE", "NL")  # first part usable as a fallback

# GeoNames can list one postcode with several places; the point used is their
# mean. If any of those places lies farther than this from the mean, the point
# may not represent the postcode, and geocode_note says so.
PLACE_SPREAD_KM = 10.0


@lru_cache(maxsize=None)
def _full_postcodes(iso2: str, cache_dir: str = str(CACHE_DIR)) -> Optional[pd.DataFrame]:
    """Full-precision GeoNames postcodes, indexed by compact code. None if unavailable."""
    try:
        z = _download(GEONAMES_FULL_URL.format(cc=iso2),
                      Path(cache_dir) / "geonames" / f"{iso2}_full.csv.zip")
    except Exception as e:  # offline, blocked, removed upstream...
        print(f"[degurba_world] full postcodes for {iso2} unavailable ({e}); "
              "falling back to partial codes")
        return None
    with zipfile.ZipFile(z) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith((".csv", ".txt")))
        raw = zf.read(member)
    df = None
    for sep in ("\t", ","):
        try:
            d = pd.read_csv(io.BytesIO(raw), sep=sep, header=None, dtype=str,
                            keep_default_na=False)
        except Exception:
            continue
        if d.shape[1] >= 11:
            df = d
            break
    if df is None:
        return None
    # GeoNames postal layout: 0 country, 1 postcode, 2 place, ..., 9 lat, 10 lon
    out = pd.DataFrame({
        "key": df[1].map(_compact),
        "place": df[2],
        "lat": pd.to_numeric(df[9], errors="coerce"),
        "lon": pd.to_numeric(df[10], errors="coerce"),
    }).dropna(subset=["lat", "lon"])
    return out.groupby("key").agg(lat=("lat", "mean"), lon=("lon", "mean"),
                                  place=("place", "first"))


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (works on scalars and arrays)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _join_places(names: pd.Series) -> Optional[str]:
    """All distinct place names of a postcode, in file order."""
    names = [str(n) for n in names if pd.notna(n) and str(n).strip()]
    return ", ".join(dict.fromkeys(names)) or None


def _geonames_index(nomi) -> Optional[pd.DataFrame]:
    """A country's GeoNames postcodes indexed by compact code, so that spacing
    and hyphens never prevent a match. Cached on the pgeocode object.

    Columns: ``lat``/``lon`` (mean of the places listed for the code),
    ``place`` (their names), ``n_places`` and ``spread_km`` (distance from
    the mean to the farthest place, used to warn about scattered codes).
    """
    idx = getattr(nomi, "_degurba_index", None)
    if idx is None:
        # pgeocode keeps every GeoNames row in _data and one row per code in
        # _data_frame. Both are private; _data is preferred because the spread
        # of the places can only be measured from the individual rows.
        df = getattr(nomi, "_data", None)
        if df is None or "postal_code" not in df:
            df = getattr(nomi, "_data_frame", None)
        if df is None or "postal_code" not in df:
            return None
        raw = (pd.DataFrame({"key": df["postal_code"].astype(str).map(_compact),
                             "lat": pd.to_numeric(df["latitude"], errors="coerce"),
                             "lon": pd.to_numeric(df["longitude"], errors="coerce"),
                             "place": df["place_name"]})
               .dropna(subset=["lat", "lon"]))
        groups = raw.groupby("key")
        idx = groups.agg(lat=("lat", "mean"), lon=("lon", "mean"),
                         place=("place", _join_places), n_places=("lat", "size"))
        centre = idx.loc[raw["key"], ["lat", "lon"]].to_numpy()
        dist = _haversine_km(raw["lat"].to_numpy(), raw["lon"].to_numpy(),
                             centre[:, 0], centre[:, 1])
        idx["spread_km"] = pd.Series(dist, index=raw.index).groupby(raw["key"]).max()
        nomi._degurba_index = idx
    return idx


def _modal_place(names: pd.Series):
    """Most frequent place name in a group (None if there is none)."""
    modes = names.mode()
    return modes.iat[0] if len(modes) else None


def _prefix_index(nomi, n: int) -> Optional[pd.DataFrame]:
    """A country's GeoNames postcodes aggregated by their first n characters.

    Used for countries where a shortened code identifies a locality but the
    dump only holds full codes (PT): the point is the mean of every full code
    sharing the prefix, and ``place`` its most frequent place name. Cached on
    the pgeocode object, per prefix length.
    """
    cache = getattr(nomi, "_degurba_prefixes", None)
    if cache is None:
        cache = nomi._degurba_prefixes = {}
    if n not in cache:
        idx = _geonames_index(nomi)
        if idx is None:
            return None
        cache[n] = (idx.groupby(idx.index.str[:n])
                    .agg(lat=("lat", "mean"), lon=("lon", "mean"),
                         place=("place", _modal_place)))
    return cache[n]


def _hit(lat, lon, place, geocoder, precision, n_places=None, spread_km=None) -> dict:
    spread = None
    if (n_places is not None and spread_km is not None and pd.notna(spread_km)
            and int(n_places) > 1):
        spread = (int(n_places), round(float(spread_km), 1))
    return {"lat": float(lat), "lon": float(lon),
            "place": None if pd.isna(place) else place,
            "geocoder": geocoder, "precision": precision, "spread": spread}


def _geonames_lookup(nomi, pc: str, iso2: str) -> Optional[dict]:
    first = pc.split()[0] if iso2 in PARTIAL_COUNTRIES and " " in pc else None
    idx = _geonames_index(nomi)
    if idx is not None:
        key = _compact(pc)
        alt = key[len(iso2):] if key.startswith(iso2) else iso2 + key  # 'LV-1010' vs '1010'
        for k in (key, alt):
            if k in idx.index:
                r = idx.loc[k]
                return _hit(r.lat, r.lon, r.place, "geonames", "full",
                            r.get("n_places"), r.get("spread_km"))
        if first and _compact(first) in idx.index:
            r = idx.loc[_compact(first)]
            return _hit(r.lat, r.lon, r.place, "geonames", "partial")
        if _is_locality_prefix(pc, iso2):
            # The dump holds only full codes (PT), so aggregate those sharing
            # the prefix: 'PT 1000' -> every '1000-xxx' code.
            pref = _prefix_index(nomi, len(key))
            if pref is not None and key in pref.index:
                r = pref.loc[key]
                return _hit(r.lat, r.lon, r.place, "geonames", "partial")
        return None
    # Fallback on pgeocode's public API
    for query in (pc, first):
        if not query:
            continue
        res = nomi.query_postal_code(query)
        if pd.notna(res.get("latitude")) and pd.notna(res.get("longitude")):
            matched = _compact(res.get("postal_code") or "")
            partial = query is first or len(matched) < len(_compact(pc))
            return _hit(res.latitude, res.longitude, res.get("place_name"),
                        "geonames", "partial" if partial else "full")
    return None


def geocode_postcode(postcode, country: str, use_nominatim_fallback: bool = True,
                     user_agent: str = "degurba_world",
                     cache_dir: Path = CACHE_DIR) -> dict:
    """Return {'lat', 'lon', 'place', 'geocoder', 'precision', 'spread'} for a postcode.

    Order of lookup:
    0. A postcode table you registered for the country (``register_postcodes``),
       the way to cover countries that GeoNames omits. ``geocoder`` is then
       ``"local"``.
    1. For CA, GB and NL: GeoNames *_full* file with complete postcodes.
    2. GeoNames standard dump via pgeocode. For CA/GB/IE/NL, if the full code
       is missing, its first part is used (e.g. the outward code of a UK
       postcode). For PT, a code shortened to its first four digits is matched
       against every full code with that prefix. Either way the point is the
       centroid of a larger area and ``precision`` is ``"partial"``.
    3. Optionally OpenStreetMap Nominatim (online, max 1 request/second).

    ``precision`` is ``"full"``, ``"partial"`` or ``None`` (not found).
    ``spread`` is ``(number of places, km from their mean to the farthest)``
    when GeoNames lists the code with several places, otherwise ``None``.
    This function does not check the format; see ``check_postcode``.
    """
    iso2, _ = country_codes(country)
    pc = _normalise_postcode(postcode, iso2)
    empty = {"lat": np.nan, "lon": np.nan, "place": None, "geocoder": None,
             "precision": None, "spread": None}
    if _is_missing(pc):
        return empty

    hit = _local_lookup(iso2, pc)   # your own table wins over GeoNames
    if hit:
        return hit

    if iso2 in FULL_POSTCODE_COUNTRIES:
        full = _full_postcodes(iso2, str(cache_dir))
        key = _compact(pc)
        if full is not None and key in full.index:
            r = full.loc[key]
            return _hit(r.lat, r.lon, r.place, "geonames_full", "full")

    try:
        nomi = _pgeocode_nomi(iso2)
    except ValueError:
        nomi = None  # country not covered by GeoNames
    if nomi is not None:
        hit = _geonames_lookup(nomi, pc, iso2)
        if hit:
            return hit

    if use_nominatim_fallback:
        from geopy.geocoders import Nominatim

        geo = Nominatim(user_agent=user_agent, timeout=20)
        time.sleep(1.0)  # Nominatim usage policy
        loc = geo.geocode({"postalcode": pc}, country_codes=iso2.lower())
        if loc is not None:
            return _hit(loc.latitude, loc.longitude, loc.address, "nominatim", "full")

    return empty


def geocode_place(place, country: str, user_agent: str = "degurba_world") -> dict:
    """Coordinates of a locality *by name*, through OpenStreetMap Nominatim.

    The fallback for postcodes that no dataset can locate, typically in
    countries missing from GeoNames (``has_postcode_data``). The query is
    structured (``city=`` + ``countrycodes=``) rather than free text, because
    a free-text search for a bare numeric code can return an unrelated place.

    Returns the same dict as ``geocode_postcode``, with ``geocoder =
    "nominatim_place"`` and ``precision = "locality"``: the point is the centre
    of the whole locality, so it is coarser than any postcode match. Requires
    network access (max 1 request/second).
    """
    from geopy.geocoders import Nominatim

    iso2, _ = country_codes(country)
    name = _clean_raw(place)
    empty = {"lat": np.nan, "lon": np.nan, "place": None, "geocoder": None,
             "precision": None, "spread": None}
    if _is_missing(name):
        return empty
    geo = Nominatim(user_agent=user_agent, timeout=20)
    time.sleep(1.0)  # Nominatim usage policy
    loc = geo.geocode({"city": name}, country_codes=iso2.lower())
    if loc is None:
        return empty
    return _hit(loc.latitude, loc.longitude, loc.address, "nominatim_place", "locality")


def build_postcode_table(pairs, country: str, out_path, prefix: Optional[int] = None,
                         user_agent: str = "degurba_world",
                         progress: bool = True) -> pd.DataFrame:
    """Turn (postcode, locality) pairs into a reusable postcode table.

    The practical way to cover a country GeoNames omits when you have no
    licensed dataset: survey answers usually carry both the postcode and the
    town, so each distinct locality is geocoded once (structured Nominatim
    ``city`` search) and the result written to ``out_path`` as CSV. Register
    that file with ``register_postcodes`` and those codes resolve offline from
    then on.

    ``pairs`` is any iterable of ``(postcode, locality)``.

    ``prefix`` truncates every code to that many characters, so the table also
    answers codes it has never seen: with ``prefix=4``, any code that starts
    with the same four characters resolves too (as ``partial``). Without it
    the table only answers the exact codes you supplied. Pick the shortest
    prefix that still distinguishes the localities in your data.

    Needs network access (1 request/second). Cite the result as OpenStreetMap
    (ODbL) plus your own postcode-to-locality input.
    """
    iso2, _ = country_codes(country)
    seen: dict[tuple, None] = {}
    for pc, place in pairs:
        code = _compact(_clean_raw(pc))
        key = (code[:prefix] if prefix else code, _clean_raw(place))
        if key[0] and not _is_missing(key[1]):
            seen.setdefault(key, None)
    rows, places = [], {}
    for i, (pc, place) in enumerate(seen, 1):
        if place not in places:            # one lookup per distinct locality
            places[place] = geocode_place(place, iso2, user_agent)
        g = places[place]
        if pd.notna(g["lat"]):
            rows.append({"postcode": pc, "lat": g["lat"], "lon": g["lon"],
                         "place": place})
        if progress and (i % 25 == 0 or i == len(seen)):
            print(f"[degurba_world] {i}/{len(seen)} postcode/locality pairs, "
                  f"{len(places)} localities looked up")
    table = pd.DataFrame(rows, columns=["postcode", "lat", "lon", "place"])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_path, index=False)
    print(f"[degurba_world] wrote {len(table)} postcodes to {out_path}; "
          f"register it with register_postcodes({iso2!r}, {str(out_path)!r})")
    return table


def _postcode_note(pc: str, iso2: str, precision: Optional[str],
                   spread: Optional[tuple] = None) -> Optional[str]:
    """Warnings about how well the point represents the postcode.

    ``spread`` is ``(number of places, km)`` from ``geocode_postcode``.
    """
    notes = []
    if precision == "partial":
        notes.append("only the first part of the postcode was matched; "
                     "the point is the centroid of a larger area")
    elif precision == "locality":
        notes.append("the postcode was not located; the point is the centre of "
                     "the reported locality, so it represents the whole town or city")
    if spread is not None and spread[1] > PLACE_SPREAD_KM:
        notes.append(f"GeoNames lists {spread[0]} places for this postcode, up to "
                     f"{spread[1]:g} km from their mean, which is the point used; "
                     "check that it lies in the right area")
    if iso2 == "CA" and len(pc) >= 2 and pc[1] == "0":
        notes.append("rural Canadian FSA (second character 0): the postcode "
                     "can cover a very large area")
    return "; ".join(notes) or None


# ----------------------------------------------------------------------------
# 3) GRID classification: GHS-SMOD
# ----------------------------------------------------------------------------
class SMODGrid:
    """Lazy reader for the global GHS-SMOD raster (WGS84, 30 arc-seconds ~1 km).

    The zip (tens of MB) is downloaded once; the GeoTIFF is read directly
    inside the zip with windowed reads, so nothing big is loaded into memory.
    """

    def __init__(self, epoch: int = 2025, cache_dir: Path = CACHE_DIR,
                 path: Optional[str | Path] = None):
        import rasterio

        self.epoch = _check_epoch(epoch)
        if path is None:
            zpath = _download(SMOD_URL.format(epoch=self.epoch),
                              Path(cache_dir) / f"GHS_SMOD_E{self.epoch}_4326_30ss.zip")
            with zipfile.ZipFile(zpath) as z:
                tif = next(n for n in z.namelist() if n.lower().endswith(".tif"))
            path = f"/vsizip/{zpath}/{tif}"
        self.ds = rasterio.open(str(path))
        self.nodata = self.ds.nodata if self.ds.nodata is not None else SMOD_NODATA

    def classify(self, lat: float, lon: float, window: int = 3) -> dict:
        """Class at the point and modal class in a `window` x `window` block."""
        from rasterio.windows import Window

        out = {"smod_cell_l2": None, "smod_mode_l2": None, "smod_mode_share": None}
        if lat is None or lon is None or np.isnan(lat) or np.isnan(lon):
            return out
        row, col = self.ds.index(lon, lat)
        h = window // 2
        r0, c0 = max(row - h, 0), max(col - h, 0)
        r1, c1 = min(row + h + 1, self.ds.height), min(col + h + 1, self.ds.width)
        if r0 >= r1 or c0 >= c1:
            return out
        arr = self.ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        cell = int(arr[row - r0, col - c0])
        out["smod_cell_l2"] = cell if cell != self.nodata else None

        vals = arr[(arr != self.nodata) & (arr != 10)]  # ignore water/nodata
        if vals.size:
            u, n = np.unique(vals, return_counts=True)
            # ties -> the more urban class (higher code)
            best = max(zip(n, u))
            out["smod_mode_l2"] = int(best[1])
            out["smod_mode_share"] = round(best[0] / vals.size, 3)
        return out


# ----------------------------------------------------------------------------
# 4) UNIT classification: GADM 4.1 polygon + GHS-DUC table
# ----------------------------------------------------------------------------
class DUCTable:
    """GHS-DUC R2023A classification of GADM 4.1 units.

    The package ships one CSV per GADM level (0-5) and epoch. Column names are
    detected automatically: the unit key is the deepest ``GID_<n>`` column and
    the classes are columns whose name contains 'DEGURBA'/'DUC' plus 'L1'/'L2'.
    Run ``DUCTable.inspect()`` once to see the actual file and column names,
    and pass ``l1_col`` / ``l2_col`` explicitly if auto-detection picks wrong.
    """

    def __init__(self, epoch: int = 2025, cache_dir: Path = CACHE_DIR,
                 zip_path: Optional[str | Path] = None,
                 l1_col: Optional[str] = None, l2_col: Optional[str] = None):
        self.epoch = _check_epoch(epoch)
        self.zip_path = Path(zip_path) if zip_path else _download(
            DUC_URL, Path(cache_dir) / "GHS_DUC_MT_GLOBE_R2023A_V2_0.zip")
        self.l1_col, self.l2_col = l1_col, l2_col
        self._tables: dict[int, pd.DataFrame] = {}
        self._index = self._index_csvs()

    # -- discovery ----------------------------------------------------------
    def _index_csvs(self) -> dict[int, str]:
        """Map GADM level -> CSV member name for the chosen epoch."""
        idx = {}
        with zipfile.ZipFile(self.zip_path) as z:
            for name in z.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                base = Path(name).name
                if str(self.epoch) not in base:
                    continue
                with z.open(name) as f:
                    header = f.readline().decode("utf-8-sig", "replace")
                gids = [int(m) for m in re.findall(r"\bGID_(\d)\b", header)]
                if gids:
                    idx[max(gids)] = name
        if not idx:
            raise RuntimeError(
                "Could not find per-level CSVs for epoch "
                f"{self.epoch} in {self.zip_path}. Run DUCTable.inspect(path).")
        return idx

    @staticmethod
    def inspect(zip_path: Optional[str | Path] = None, n: int = 3) -> None:
        """Print the members of the DUC zip and the header of each CSV."""
        zip_path = zip_path or _download(DUC_URL, CACHE_DIR / "GHS_DUC_MT_GLOBE_R2023A_V2_0.zip")
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                print(name)
                if name.lower().endswith(".csv"):
                    with z.open(name) as f:
                        print(pd.read_csv(f, nrows=n, encoding="utf-8-sig").to_string(), "\n")

    @property
    def levels(self) -> list[int]:
        return sorted(self._index)

    def _pick_cols(self, df: pd.DataFrame) -> tuple[Optional[str], Optional[str]]:
        cols = list(df.columns)
        up = {c: c.upper() for c in cols}
        cls = [c for c in cols if ("DEGURBA" in up[c] or "DUC" in up[c] or "DEGUR" in up[c])]
        l1 = self.l1_col or next((c for c in cls if "L1" in up[c]), None)
        l2 = self.l2_col or next((c for c in cls if "L2" in up[c]), None)
        if l1 is None and l2 is None and cls:
            # single class column: decide by its values
            v = pd.to_numeric(df[cls[0]], errors="coerce").dropna()
            if v.max() > 3:
                l2 = cls[0]
            else:
                l1 = cls[0]
        return l1, l2

    def table(self, level: int) -> pd.DataFrame:
        if level not in self._tables:
            with zipfile.ZipFile(self.zip_path) as z, z.open(self._index[level]) as f:
                df = pd.read_csv(f, encoding="utf-8-sig", low_memory=False)
            l1, l2 = self._pick_cols(df)
            key = f"GID_{level}"
            keep = pd.DataFrame({"gid": df[key].astype(str)})
            keep["duc_l2"] = pd.to_numeric(df[l2], errors="coerce") if l2 else np.nan
            keep["duc_l1"] = (pd.to_numeric(df[l1], errors="coerce") if l1
                              else keep["duc_l2"].map(l2_to_l1))
            self._tables[level] = keep.drop_duplicates("gid").set_index("gid")
        return self._tables[level]

    def lookup(self, gid: str, level: int) -> dict:
        t = self.table(level)
        if gid in t.index:
            r = t.loc[gid]
            return {"duc_l1": _int_or_none(r.duc_l1), "duc_l2": _int_or_none(r.duc_l2)}
        return {"duc_l1": None, "duc_l2": None}


def _int_or_none(x):
    return None if x is None or pd.isna(x) else int(x)


class GADMLocator:
    """Point-in-polygon against per-country GADM 4.1 GeoPackages (cached)."""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self._layers: dict = {}    # (iso3, level) -> GeoDataFrame in EPSG:4326
        self._layers_m: dict = {}  # same, projected to EPSG:3857 for nearest search

    def _gpkg(self, iso3: str) -> Path:
        return _download(GADM_URL.format(iso3=iso3),
                         self.cache_dir / "gadm" / f"gadm41_{iso3}.gpkg")

    def available_levels(self, iso3: str) -> list[int]:
        import pyogrio

        names = [n for n, _ in pyogrio.list_layers(self._gpkg(iso3))]
        return sorted(int(n[-1]) for n in names if re.fullmatch(r"ADM_ADM_\d", n))

    def layer(self, iso3: str, level: int):
        import geopandas as gpd

        k = (iso3, level)
        if k not in self._layers:
            g = gpd.read_file(self._gpkg(iso3), layer=f"ADM_ADM_{level}")
            cols = [c for c in g.columns if re.fullmatch(r"(GID|NAME)_\d", c)]
            self._layers[k] = g[cols + ["geometry"]].to_crs(4326)
        return self._layers[k]

    def locate(self, lat: float, lon: float, iso3: str, level: int,
               max_snap_km: float = MAX_SNAP_KM) -> dict:
        import geopandas as gpd
        from shapely.geometry import Point

        g = self.layer(iso3, level)
        pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs=4326)
        hit = gpd.sjoin(pt, g, predicate="within", how="left")
        if hit[f"GID_{level}"].isna().all():
            # Nothing contains the point. A postcode centroid that fell just
            # offshore belongs to the unit beside it, so snap, but only within
            # max_snap_km: a point outside the country (e.g. a military post
            # office abroad) must not be assigned to a distant unit.
            k = (iso3, level)
            if k not in self._layers_m:  # project once; large countries are slow
                self._layers_m[k] = g.to_crs(3857)
            ptm = pt.to_crs(3857)
            # EPSG:3857 inflates distance by 1/cos(latitude); undo that.
            km = (self._layers_m[k].distance(ptm.geometry.iloc[0]).min()
                  * math.cos(math.radians(lat)) / 1000)
            if km > max_snap_km:
                return {"gadm_level": level, "gadm_gid": None, "gadm_name": None}
            hit = gpd.sjoin_nearest(ptm, self._layers_m[k], how="left")
        r = hit.iloc[0]
        return {"gadm_level": level, "gadm_gid": r.get(f"GID_{level}"),
                "gadm_name": r.get(f"NAME_{level}")}


# ----------------------------------------------------------------------------
# High-level API
# ----------------------------------------------------------------------------
@dataclass
class DegurbaResult:
    """One checked and classified postcode. docs/outputs.md explains every field."""
    # postcode check
    postcode: str
    postcode_status: Optional[str] = None
    postcode_warning: Optional[str] = None
    # country
    country: Optional[str] = None
    country_inferred: bool = False
    country_candidates: Optional[str] = None            # where the code EXISTS
    country_candidates_unchecked: Optional[str] = None  # ... and where it could
                                                        # not be checked at all
                                                        # (no postcode data)
    region: Optional[str] = None
    region_match: Optional[bool] = None
    place_reported: Optional[str] = None
    # geocoding
    lat: float = np.nan
    lon: float = np.nan
    place: Optional[str] = None
    geocoder: Optional[str] = None
    geocode_precision: Optional[str] = None
    geocode_note: Optional[str] = None
    # grid (GHS-SMOD)
    smod_cell_l2: Optional[int] = None
    smod_mode_l2: Optional[int] = None
    smod_mode_share: Optional[float] = None
    smod_l1: Optional[int] = None
    smod_label: Optional[str] = None
    # unit (GHS-DUC on GADM)
    gadm_level: Optional[int] = None
    gadm_gid: Optional[str] = None
    gadm_name: Optional[str] = None
    duc_l1: Optional[int] = None
    duc_l2: Optional[int] = None
    duc_label: Optional[str] = None
    duc_l1_label: Optional[str] = None


POSTCODE_STATUSES = ("found", "found_by_place", "not_found", "no_reference_data",
                     "invalid_format", "missing", "country_ambiguous")

# Columns given pandas' nullable types in DataFrames, so that empty values do
# not turn whole integer columns into floats (30 -> 30.0) or booleans into
# objects.
_INT_COLUMNS = ("smod_cell_l2", "smod_mode_l2", "smod_l1", "gadm_level",
                "duc_l1", "duc_l2")
_BOOL_COLUMNS = ("country_inferred", "region_match")


def _results_frame(rows: list) -> pd.DataFrame:
    """DataFrame of result dicts with nullable integer and boolean columns."""
    df = pd.DataFrame(rows)
    for col in _INT_COLUMNS:
        if col in df:
            df[col] = df[col].astype("Int64")
    for col in _BOOL_COLUMNS:
        if col in df:
            df[col] = df[col].astype("boolean")
    return df


class DegurbaWorld:
    """Postcode + country -> Degree of Urbanisation, worldwide.

    Parameters
    ----------
    epoch : GHSL epoch (1975, 1980, ..., 2030). 2020 or 2025 are the natural
        choices for present-day analyses (Eurostat's LAU DEGURBA uses 2021).
    grid, units : enable the SMOD grid and/or the DUC admin-unit method.
        With both off, postcodes are only checked and located (no GHSL/GADM
        downloads).
    gadm_level : GADM level for the unit method. ``None`` = deepest level that
        exists for the country *and* in GHS-DUC, capped at ``max_gadm_level``.
        Note GADM levels mean different things per country (e.g. level 2 is
        a county in the US and level 5 a commune in France).
    window : side (in ~1 km cells) of the block used for the modal SMOD class.
    use_nominatim_fallback : look up postcodes missing from GeoNames on
        OpenStreetMap (online; never used for codes with an invalid format).
    postcode_data : ``{country: path}`` of your own postcode tables, registered
        before anything else runs and searched before GeoNames, e.g. to cover
        countries GeoNames omits; see ``register_postcodes`` for the file
        format.
    max_snap_km : how far (in km) a point that no administrative unit contains
        may be snapped to the nearest unit (default 25).
    """

    def __init__(self, epoch: int = 2025, grid: bool = True, units: bool = True,
                 gadm_level: Optional[int] = None, max_gadm_level: int = 5,
                 window: int = 3, use_nominatim_fallback: bool = True,
                 cache_dir: Path = CACHE_DIR,
                 smod_path: Optional[str | Path] = None,
                 duc_zip_path: Optional[str | Path] = None,
                 postcode_data: Optional[dict] = None,
                 max_snap_km: float = MAX_SNAP_KM):
        for cc, path in (postcode_data or {}).items():
            register_postcodes(cc, path)
        self.epoch = _check_epoch(epoch)
        self.window = window
        self.gadm_level = gadm_level
        self.max_gadm_level = max_gadm_level
        self.use_nominatim_fallback = use_nominatim_fallback
        self.max_snap_km = max_snap_km
        self.cache_dir = Path(cache_dir)
        self.smod = SMODGrid(epoch, cache_dir, smod_path) if grid else None
        self.duc = DUCTable(epoch, cache_dir, duc_zip_path) if units else None
        self.gadm = GADMLocator(cache_dir) if units else None

    def _level_for(self, iso3: str) -> int:
        if self.gadm_level is not None:
            return self.gadm_level
        levels = set(self.gadm.available_levels(iso3)) & set(self.duc.levels)
        levels = [lvl for lvl in levels if lvl <= self.max_gadm_level]
        return max(levels) if levels else 0

    def from_coords(self, lat: float, lon: float, country: str,
                    postcode: str = "", place=None, geocoder=None,
                    precision=None, spread=None) -> DegurbaResult:
        """Classify a point you already have (skips postcode checks and geocoding)."""
        iso2, iso3 = country_codes(country)
        res = DegurbaResult(postcode=str(postcode), country=iso2, lat=lat, lon=lon,
                            place=place, geocoder=geocoder, geocode_precision=precision,
                            geocode_note=_postcode_note(str(postcode), iso2, precision,
                                                        spread))
        if lat is None or pd.isna(lat):
            return res

        if self.smod is not None:
            s = self.smod.classify(lat, lon, self.window)
            res.smod_cell_l2, res.smod_mode_l2, res.smod_mode_share = (
                s["smod_cell_l2"], s["smod_mode_l2"], s["smod_mode_share"])
            best = res.smod_mode_l2 if res.smod_mode_l2 is not None else res.smod_cell_l2
            res.smod_l1 = l2_to_l1(best)
            res.smod_label = SMOD_L2.get(best)

        if self.duc is not None:
            level = self._level_for(iso3)
            loc = self.gadm.locate(lat, lon, iso3, level, self.max_snap_km)
            res.gadm_level, res.gadm_gid, res.gadm_name = (
                loc["gadm_level"], loc["gadm_gid"], loc["gadm_name"])
            if res.gadm_gid is not None and pd.notna(res.gadm_gid):
                d = self.duc.lookup(res.gadm_gid, level)
                res.duc_l1, res.duc_l2 = d["duc_l1"], d["duc_l2"]
                res.duc_label = DUC_L2.get(res.duc_l2)
                res.duc_l1_label = L1.get(res.duc_l1)
        return res

    def _classify_one(self, postcode, country=None, region=None, place=None,
                      among=None) -> DegurbaResult:
        """One postcode, one result. The worker behind ``classify`` (which
        expands a countryless postcode into every possibility) and behind
        ``classify_many`` (which must keep one row per input row). With no
        ``country`` it infers one, and only accepts a unique answer; see
        ``classify`` for what every argument means."""
        raw = _clean_raw(postcode)
        region = _clean_raw(region) or None
        place = _clean_raw(place) or None
        country = _clean_raw(country) or None

        if _is_missing(raw):
            iso2 = country_codes(country)[0] if country else None
            return DegurbaResult(postcode="", country=iso2, postcode_status="missing",
                                 region=region, place_reported=place)

        inferred = False
        candidates = unchecked = None
        extra_warning = None
        if country is None:
            guess = infer_country(raw, among, cache_dir=self.cache_dir)
            candidates = " ".join(guess.existing or []) or None
            unchecked = " ".join(guess.unverifiable or []) or None
            if guess.unverifiable:
                k = len(guess.unverifiable)
                extra_warning = (
                    ("1 country whose format fits has" if k == 1 else
                     f"{k} of the countries whose format fits have")
                    + " no postcode data, so the code could be theirs too "
                    + f"({', '.join(guess.unverifiable[:8])}"
                    + (", ..." if k > 8 else "") + ")")
            if guess.country is None:
                if guess.candidates:
                    status = "country_ambiguous"
                    n = len(guess.candidates)
                    if guess.existing is None:          # existence not checked
                        shown = ", ".join(guess.candidates[:10])
                        more = f", ... ({n} in all)" if n > 10 else ""
                        warning = (f"fits the postcode format of {n} countries "
                                   f"({shown}{more}); existence was not checked")
                    else:
                        warning = (f"fits the postcode format of {n} countries and "
                                   "exists in "
                                   + (", ".join(guess.existing) or "none of them"))
                    if extra_warning:
                        warning += "; " + extra_warning
                    warning += ("; give the country, or a list of study countries "
                                "with among=")
                else:
                    status = "invalid_format"
                    warning = ("does not fit the postcode format of "
                               + ("any of the given countries" if among else "any country"))
                return DegurbaResult(postcode=raw.upper(), country=None,
                                     postcode_status=status, postcode_warning=warning,
                                     country_candidates=candidates,
                                     country_candidates_unchecked=unchecked,
                                     region=region,
                                     place_reported=place)
            country, inferred = guess.country, True

        iso2, _ = country_codes(country)
        check = check_postcode(raw, iso2)
        pc, fmt, warnings = check["postcode"], check["format_ok"], list(check["warnings"])

        g = geocode_postcode(pc, iso2,
                             use_nominatim_fallback=self.use_nominatim_fallback and fmt is not False,
                             cache_dir=self.cache_dir)
        covered = has_postcode_data(iso2)
        # Last resort: place the row from the reported locality. For countries
        # with no postcode dataset this is the only thing that can work.
        if (pd.isna(g["lat"]) and place and fmt is not False
                and self.use_nominatim_fallback):
            g = geocode_place(place, iso2)

        if pd.notna(g["lat"]):
            if g["precision"] == "locality":
                status = "found_by_place"
                gap = "" if covered else f" (GeoNames has no postcodes for {iso2})"
                warnings.append(f"the postcode itself was not located{gap}; the row "
                                f"was placed from the reported locality '{place}'")
            else:
                status = "found"
            if fmt is False:
                warnings.append("found in the reference data although it does not "
                                "match the usual format")
            res = self.from_coords(g["lat"], g["lon"], iso2, pc, g["place"],
                                   g["geocoder"], g["precision"], g.get("spread"))
        else:
            if fmt is False:
                status = "invalid_format"
            elif not covered:
                status = "no_reference_data"
                if self.use_nominatim_fallback:
                    warnings.append(
                        f"no postcode dataset covers {iso2}: GeoNames does not "
                        "publish its postcodes and OpenStreetMap does not know this "
                        "code, so it could not be checked against anything. Pass the "
                        "town or city as place= to classify the row, or geocode it "
                        "yourself and use from_coords()")
                else:
                    warnings.append(
                        f"no postcode dataset covers {iso2}: GeoNames does not "
                        "publish its postcodes and OpenStreetMap was not searched "
                        "(use_nominatim_fallback=False), so the code could not be "
                        "checked. Register a postcode table, allow the OpenStreetMap "
                        "search, or geocode the row yourself and use from_coords()")
            else:
                status = "not_found"
            res = DegurbaResult(postcode=pc, country=iso2)

        region_match = None
        if region is not None and fmt is not False:
            region_match, problem = _region_match(pc, iso2, region)
            if problem:
                warnings.append(problem)
            elif region_match is False:
                warnings.append(f"postcode is not in the reported region '{region}'")

        res.country_inferred = inferred
        res.country_candidates = candidates
        res.country_candidates_unchecked = unchecked
        res.postcode_status = status
        if inferred and extra_warning:
            warnings.append(f"country inferred as {iso2}, but " + extra_warning)
        res.postcode_warning = "; ".join(warnings) or None
        res.region, res.region_match = region, region_match
        res.place_reported = place
        return res

    def classify_candidates(self, postcode, region=None, place=None,
                            among=None) -> pd.DataFrame:
        """Fully classify a postcode in **every country it could belong to**.

        For a bare numeric postcode there is often no single answer: the same
        code can exist in several countries. This returns one complete row per
        country where the code really exists -- every column of ``DegurbaResult``, so the coordinates,
        the grid class and the unit class, not just the country name. Use it to
        see the possibilities side by side and pick, or to confirm that the
        answer does not depend on which country it is.

        Rows are ordered as ``candidate_countries`` returns them, and each one
        carries ``country_candidates`` and ``country_candidates_unchecked``
        so you can see the
        inference it came from. ``country_inferred`` is True throughout.

        If the code exists nowhere, or fits no format, or is a non-answer, you
        get a single row carrying that verdict rather than an empty frame.

        Note the cost when ``units=True``: each country needs its own GADM
        file (often tens to hundreds of MB), downloaded the first time.
        ``DegurbaWorld(units=False)`` or an ``among`` list keeps it small.
        """
        raw = _clean_raw(postcode)
        one = lambda cc: self._classify_one(raw, cc, region, place, among)
        if _is_missing(raw):
            return _results_frame([asdict(one(None))])
        guess = infer_country(raw, among, cache_dir=self.cache_dir)
        targets = guess.existing if guess.existing is not None else guess.candidates
        if not targets:
            # No country fits, or none of those that fit has the code: the
            # single result explains which, and lists what could not be checked.
            return _results_frame([asdict(one(None))])
        candidates = " ".join(guess.existing or []) or None
        unchecked = " ".join(guess.unverifiable or []) or None
        rows = []
        for cc in targets:
            res = one(cc)
            res.country_inferred = True
            res.country_candidates = candidates
            res.country_candidates_unchecked = unchecked
            rows.append(asdict(res))
        return _results_frame(rows)

    def classify(self, postcode, country=None, region=None, place=None,
                 among=None, expand: Optional[bool] = None):
        """Check, locate and classify one postcode.

        Returns a single ``DegurbaResult`` when you name the ``country``, and a
        **DataFrame of every possible answer** when you do not -- one fully
        classified row per country where the code exists (see
        ``classify_candidates``). The return type depends only on whether
        ``country`` was given, never on the data, and ``expand=False`` forces a
        single result in either case. ``classify_many`` always returns one row
        per input row, whatever the country column holds.

        country : ISO2/ISO3 code or English name. If missing, every country the
            postcode could belong to is classified instead of one being
            guessed.
        region : optional province or state reported with the postcode, as a
            name ('New York'), a code ('NY') or an ISO 3166-2 code ('US-NY').
            The postcode is checked against it (``region_match``).
        place : optional town or city reported with the postcode. Used only if
            the postcode itself cannot be located, which is common in
            countries missing from GeoNames (see ``has_postcode_data``). The row is then classified from the centre
            of that locality, with ``postcode_status = "found_by_place"`` and
            ``geocode_precision = "locality"``. Needs Nominatim, so it does
            nothing when ``use_nominatim_fallback=False``.
        among : countries to consider when ``country`` is missing.
        expand : override the choice above. False always gives one
            ``DegurbaResult``; True always gives the DataFrame.
        """
        if expand is None:
            expand = country is None
        if not expand:
            return self._classify_one(postcode, country, region, place, among)
        if country is not None:      # expand=True with a country: a 1-row frame
            return _results_frame([asdict(
                self._classify_one(postcode, country, region, place, among))])
        return self.classify_candidates(postcode, region, place, among)

    def classify_many(self, postcodes: Iterable, countries=None, regions=None,
                      places=None, among=None, progress: bool = True) -> pd.DataFrame:
        """Batch version of ``classify``; returns one row per input row.

        countries / regions / places : a single value for all rows, one value
            per row (missing values allowed), or None. Rows without a country
            are inferred from the postcode (see ``among``); ``places`` is the
            reported town or city, used only for postcodes that cannot be
            located (see ``classify``).
        Identical (postcode, country, region, place) rows are computed only once.
        """
        postcodes = list(postcodes)
        n = len(postcodes)
        among = _parse_among(among)  # fail fast on an unknown study country

        def per_row(values, name):
            if values is None or isinstance(values, str):
                return [values] * n
            values = list(values)
            if len(values) != n:
                raise ValueError(f"{name} has {len(values)} values for {n} postcodes")
            return values

        rows = [(_clean_raw(p), _clean_raw(c), _clean_raw(r), _clean_raw(pl))
                for p, c, r, pl in zip(postcodes,
                                       per_row(countries, "countries"),
                                       per_row(regions, "regions"),
                                       per_row(places, "places"))]
        uniq = list(dict.fromkeys(rows))
        out = {}
        for i, key in enumerate(uniq, 1):
            pc, c, r, pl = key
            try:
                out[key] = asdict(self._classify_one(pc, c or None, r or None,
                                                     pl or None, among=among))
            except Exception as e:  # keep going on bad rows
                out[key] = {"postcode": pc, "country": c or None, "error": repr(e)}
            if progress and (i % 50 == 0 or i == len(uniq)):
                print(f"[degurba_world] {i}/{len(uniq)} unique postcodes")
        return _results_frame([out[k] for k in rows])


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        prog="degurba-world",
        description="Check postcodes and get their Degree of Urbanisation, worldwide (GHSL).")
    ap.add_argument("postcode", nargs="?", help="single postcode")
    ap.add_argument("country", nargs="?",
                    help="ISO2/ISO3 code or English name; omit it to infer it from the postcode")
    ap.add_argument("--region", help="reported province/state, to check the postcode against it")
    ap.add_argument("--postcode-data", action="append", metavar="CC=FILE",
                    help="your own postcode table for a country, e.g. CN=postcodes.csv "
                         "(repeatable; searched before GeoNames)")
    ap.add_argument("--place", help="reported town or city; used to place the row when the "
                                    "postcode cannot be located")
    ap.add_argument("--among", help="study countries used to infer missing countries, e.g. DE,ES")
    ap.add_argument("--csv", help="input CSV with a 'postcode' column and optional "
                                  "'country', 'region' and 'place' columns")
    ap.add_argument("--out", default="degurba_output.csv",
                    help="output CSV for --csv (default: degurba_output.csv)")
    ap.add_argument("--epoch", type=int, default=2025,
                    help="GHSL epoch, 1975 to 2030 in steps of 5 (default 2025)")
    ap.add_argument("--gadm-level", type=int, default=None,
                    help="GADM level for the unit classification (default: chosen per country)")
    ap.add_argument("--no-grid", action="store_true", help="skip the grid classification")
    ap.add_argument("--no-units", action="store_true", help="skip the unit classification")
    ap.add_argument("--check-only", action="store_true",
                    help="only check and locate postcodes (no GHSL/GADM downloads)")
    ap.add_argument("--no-nominatim", action="store_true",
                    help="never query OpenStreetMap Nominatim, so that no postcode or "
                         "place leaves your computer")
    ap.add_argument("--inspect-duc", action="store_true", help="print DUC zip contents and exit")
    a = ap.parse_args(argv)

    if a.inspect_duc:
        DUCTable.inspect()
        return
    if not a.csv and not a.postcode:
        ap.error("give a postcode, or --csv FILE")
    for spec in a.postcode_data or []:
        if "=" not in spec:
            ap.error(f"--postcode-data expects CC=FILE, got {spec!r}")
        cc, path = spec.split("=", 1)
        print(f"[degurba_world] {register_postcodes(cc, path)} postcodes "
              f"registered for {cc.upper()} from {path}")
    dw = DegurbaWorld(epoch=a.epoch, grid=not (a.no_grid or a.check_only),
                      units=not (a.no_units or a.check_only), gadm_level=a.gadm_level,
                      use_nominatim_fallback=not a.no_nominatim)
    if a.csv:
        df = pd.read_csv(a.csv, dtype=str)
        res = dw.classify_many(df["postcode"], df["country"] if "country" in df else None,
                               df["region"] if "region" in df else None,
                               df["place"] if "place" in df else a.place, among=a.among)
        extra = df.drop(columns=[c for c in ("postcode", "country", "region", "place")
                                 if c in df])
        out = pd.concat([extra.reset_index(drop=True), res], axis=1)
        path = a.out
        out.to_csv(path, index=False)
        print(f"written {path}")
        if "postcode_status" in res:
            print(res["postcode_status"].value_counts(dropna=False).to_string())
    else:
        out = dw.classify(a.postcode, a.country, a.region, a.place, among=a.among)
        if isinstance(out, pd.DataFrame):
            if len(out) == 1:
                out = DegurbaResult(**out.iloc[0].to_dict())
            else:
                print(f"{a.postcode!r} could belong to {len(out)} countries; "
                      "each is classified below. Pass the country, or --among, "
                      "to narrow it.\n")
                w = max(len(c) for c in out.columns)
                for _, row in out.iterrows():
                    print(f"--- {row['country']} "
                          f"({row['place'] or 'no place name'}) ---")
                    for k, v in row.items():
                        if not k.startswith("country_candidates"):
                            print(f"{k:>{w}}: {v}")
                    print()
                print(f"{'country_candidates':>{w}}: "
                      f"{out['country_candidates'].iat[0]}")
                print(f"{'... unchecked':>{w}}: "
                      f"{out['country_candidates_unchecked'].iat[0]}")
                return
        fields = asdict(out)
        w = max(len(k) for k in fields)
        for k, v in fields.items():
            print(f"{k:>{w}}: {v}")


if __name__ == "__main__":
    _main()
