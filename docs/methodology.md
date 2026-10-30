# Methodology

This page describes what `degurba_world` does with a postcode and which files it downloads. The results are described in [outputs.md](outputs.md).

## Background

The Degree of Urbanisation has two stages, described in full in the methodological manual (Eurostat, 2021, listed under [How to cite](../README.md#how-to-cite)). Stage I classifies the 1 km² cells of a population grid by population density and by the size of the contiguous cluster they belong to. Stage II classifies administrative units by the share of their population that lives in each class of cell. GHSL provides both stages as global datasets: GHS-SMOD for stage I, and GHS-DUC for stage II applied to the administrative units of GADM 4.1. The package uses release R2023A of both.

## 1. Input

The country can be an ISO 3166-1 alpha-2 or alpha-3 code or an English name, resolved with `pycountry`. Eurostat codes (`EL`, `UK`, `XK`) and some common Spanish and Catalan names are also accepted. A name that cannot be resolved raises an error instead of being guessed.

The postcode is read as text, upper-cased and stripped. Values that went through a spreadsheet are repaired: `1067.0` becomes `1067`, and the thousands separator in `1.067` is removed. For countries whose codes can start with zero (`PAD_LENGTH` in the code), lost leading zeros are restored, so `1067` becomes `01067` in Germany. Spaces and hyphens are placed where the country uses them, following the example codes in Google's address metadata (`sw1a1aa` becomes `SW1A 1AA` in the United Kingdom). Empty values and the non-answers listed in `NON_ANSWERS` are not checked further.

## 2. Postcode checks

The checks separate real postcodes from typing errors, placeholders and invented values, so that an invented code does not receive a class.

1. Format. The postcode must match the country's pattern in Google's address metadata, which are maintained upstream and distributed offline in the `google-i18n-address` package. `RULE_OVERRIDES` replaces a pattern that is too loose (at present, the one for Ireland). For the countries in `LOCALITY_PREFIXES`, a code shortened to the part that identifies the locality is also accepted; at present this applies to Portugal, whose postcodes have seven digits, the first four of which identify the locality.
2. Ranges. Where the metadata give a postcode range for every province or state of a country (for example the United States, Brazil or Japan), the code must fall within one of them.
3. Existence. The code is looked up in the sources listed in [section 4](#4-locating-the-postcode), and the result is recorded in `postcode_status`.
4. Plausibility. Codes made of one repeated character or of consecutive digits are flagged as possible placeholders, and the codes in `SPECIAL_POSTCODES` and `SPECIAL_PREFIXES` as non-residential.
5. Consistency. If you give the region reported with the postcode, the code is compared with the postcode range of that region (`region_match`).

A code that the reference data contain is accepted with a warning even if the format rule rejects it.

The region can be given as a name in any language included in Google's metadata, as a code (`NY`) or as an ISO 3166-2 code (`US-NY`). For Spain, autonomous communities are mapped to their provinces (`REGION_ALIASES`). The comparison needs postcode ranges by region, which the metadata provide for some countries only.

## 3. Missing country

Postcode formats overlap between countries, and a wrong country would give a wrong class without any warning. A country is therefore inferred only when one answer remains:

1. Keep the countries whose format and ranges fit the code as written. Restoring lost leading zeros is tried only if no country fits.
2. If several fit, look the code up in each of them and keep those where it exists. Countries without postcode data cannot be checked and are listed separately (`country_candidates_unchecked`). The first time, this step downloads the postcode file of each candidate country; `infer_country(..., check_existence=False)` skips it.
3. Assign the country if exactly one remains and no candidate was left unchecked. A list of study countries (`among`) restricts the search and is taken as an assurance that the answer is in that list, so a single match is then accepted even if some candidates could not be checked; the row carries a warning naming them.

`candidate_countries` returns the countries of step 1, and `infer_country` the outcome of all three steps.

## 4. Locating the postcode

The coordinates come from the first source that knows the code. Files come before online services because they are read offline and have no rate limit.

1. A postcode table you registered for the country (`register_postcodes`, `postcode_data` or `--postcode-data`). If the exact code is missing, the longest prefix of it in the table is used; for a code shorter than those in the table, the mean of all codes that start with it is used. Both cases are marked `partial`.
2. For Canada, the United Kingdom and the Netherlands, the GeoNames files with full codes, because the standard GeoNames files only contain the first part of their codes.
3. The GeoNames postal code file of the country, read with `pgeocode`. Codes are compared without spaces or hyphens, and a code listed with several places takes the mean of their coordinates. If one of those places lies more than 10 km from the mean (`PLACE_SPREAD_KM`), `geocode_note` says so, because the mean may then fall outside the area the postcode serves. For Canada, the United Kingdom, Ireland and the Netherlands, a code that is not found may be located from its first part; for the countries in `LOCALITY_PREFIXES`, a shortened code is located at the mean of all full codes that start with it. Both cases are marked `partial`.
4. OpenStreetMap Nominatim, with a structured postcode search, if `use_nominatim_fallback=True` and the format check did not reject the code. Codes from countries for which no format is known are also searched. The package waits one second before each request, as the Nominatim usage policy requires.
5. The reported town or city (`place`), searched by name in Nominatim under the same conditions. The row is then located at the centre of that locality and gets the status `found_by_place`.

A postcode is reduced to a single point because postcode boundaries are not openly available for most countries.

## 5. Grid classification

The point is placed on GHS-SMOD (WGS84, 30 arc-seconds, about 1 km at the equator and narrower in longitude towards the poles). There is one file per epoch, read directly from its zip archive.

The package reads the class of the point's own cell and the most frequent class in a `window` × `window` block of cells centred on it (3 × 3 by default), leaving out water and cells without data; ties go to the more urban class. The block makes the result less sensitive to small errors in the position of the centroid, which matter near class boundaries.

## 6. Unit classification

The GADM 4.1 file of the country is downloaded once, and the point is assigned to the unit that contains it at the chosen level. If no unit contains it, as happens with centroids that fall just offshore, the nearest unit within `max_snap_km` (25 km by default) is used. Beyond that distance the point is treated as lying outside the country, and the unit columns stay empty. Otherwise, the GADM identifier of the unit is looked up in the GHS-DUC table for that level and epoch.

By default the package uses the most detailed level available both in the country's GADM file and in GHS-DUC, up to level 5, which is often the level closest to a municipality. What a level means varies between countries (level 2 is a county in the United States and level 5 a commune in France), so check `gadm_name` for a few known postcodes and set `gadm_level` explicitly when comparing countries.

## Downloaded files and cache

The package downloads each file the first time it is needed:

| data | files | source |
|---|---|---|
| GHS-SMOD | `GHS_SMOD_E{epoch}_GLOBE_R2023A_4326_30ss_V2_0.zip`, one per epoch | [JRC open data](https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_SMOD_GLOBE_R2023A/) |
| GHS-DUC | `GHS_DUC_MT_GLOBE_R2023A_V2_0.zip`, about 350 MB | [JRC open data](https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_DUC_GLOBE_R2023A/) |
| GADM | `gadm41_{ISO3}.gpkg`, one per country | [GADM](https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/) |
| GeoNames postal codes | One file per country, read with `pgeocode`, plus the full-code files `{CC}_full.csv.zip` | [GeoNames](https://download.geonames.org/export/zip/) |

The files are kept in the cache folder (`cache_dir`):

```
~/.cache/degurba_world/
├── GHS_SMOD_E{epoch}_4326_30ss.zip
├── GHS_DUC_MT_GLOBE_R2023A_V2_0.zip
├── geonames/{CC}_full.csv.zip
└── gadm/gadm41_{ISO3}.gpkg
```

`pgeocode` keeps the other GeoNames files in its own folder, which can be set with the `PGEOCODE_DATA_DIR` environment variable. To work on a computer without internet access, copy these folders to it. The download URLs are defined at the top of `degurba_world.py` (`SMOD_URL`, `DUC_URL`, `GADM_URL`, `GEONAMES_FULL_URL`); if they are changed, GHS-DUC and GADM must keep matching versions.
