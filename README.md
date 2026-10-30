# degurba_world

Degree of Urbanisation (DEGURBA) for postcodes worldwide, from the JRC Global Human Settlement Layer.

[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

`degurba_world` is a Python package and command-line tool that tells whether a postcode lies in a city, a town and semi-dense area, or a rural area, following the Degree of Urbanisation. It is meant for survey and other research data in which the postcode is the only location available.

## Contents

- [Why](#why)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
- [Output](#output)
- [Limitations](#limitations)
- [Data sources](#data-sources)
- [How to cite](#how-to-cite)
- [Support](#support)
- [Licence](#licence)

## Why

The Degree of Urbanisation is the method endorsed by the UN Statistical Commission in 2020 to classify territory into cities, towns and semi-dense areas, and rural areas in a way that can be compared between countries. Eurostat publishes the classification for the local administrative units (LAUs) of Europe, but outside Europe there was no simple way to attach it to records that only have a postcode.

`degurba_world` was written for that case:

- It can be used in any country, because the classification comes from the Global Human Settlement Layer (GHSL) of the European Commission's Joint Research Centre, which applies the method worldwide.
- It checks each postcode before classifying it and flags malformed, unknown and suspicious codes.
- It gives two classifications, one for the grid cells around the postcode and one for the administrative unit that contains it. The unit classification is the global counterpart of Eurostat's classification of LAUs.
- It reports where each location comes from and how precise it is, so that doubtful rows can be identified.
- It accepts every GHSL epoch, so the classification can match the year of the data.

## How it works

1. Check the postcode. It is normalised and compared with the postcode format of the country.
2. Locate it. The coordinates of the postcode centroid come from GeoNames postal code data, OpenStreetMap or a postcode table of your own.
3. Classify its surroundings. The centroid is placed on the GHS-SMOD grid, and the classes of the cells around it give the grid classification.
4. Classify the administrative unit. The centroid is located in its GADM unit, and the class of that unit is read from GHS-DUC.

[docs/methodology.md](docs/methodology.md) describes each step.

## Installation

The package requires Python 3.10 or later and can be installed with conda or pip. Three files describe the installation:

| file | tool | what it installs |
|---|---|---|
| `environment.yml` | conda | A `degurba` environment with all dependencies, including the GDAL library |
| `requirements.txt` | pip | The dependencies |
| `pyproject.toml` | pip | The package itself, with its dependencies and the `degurba-world` command |

The package is a single module, `degurba_world.py`, so once the dependencies are installed you can use it from the repository folder, with `import degurba_world` in Python or `python degurba_world.py` on the command line. The optional last step below installs the package itself, which lets you use it from any folder and adds the `degurba-world` command.

The data are downloaded the first time they are needed and kept in a local cache, so the first run takes longer. [docs/methodology.md](docs/methodology.md#downloaded-files-and-cache) lists the files.

### conda

Conda is the easiest route, because `rasterio` and `geopandas` need the GDAL library, which conda installs with them:

```bash
git clone https://github.com/ivancasanovaas/degurba_world.git
cd degurba_world
conda env create -f environment.yml
conda activate degurba
pip install -e .        # optional
```

### pip

```bash
git clone https://github.com/ivancasanovaas/degurba_world.git
cd degurba_world
pip install -r requirements.txt
pip install -e .        # optional
```

This only works if pip finds a prebuilt `rasterio` for your platform. If pip tries to compile it and fails with `A GDAL API version must be specified`, install GDAL first or use conda.

To install the package and its dependencies in one step, without downloading the repository, run `pip install "git+https://github.com/ivancasanovaas/degurba_world.git"`.

## Usage

### Python

```python
from degurba_world import DegurbaWorld

dw = DegurbaWorld(epoch=2025)          # GHSL data for 2025, the default

r = dw.classify("08028", "ES")         # country as an ISO code or an English name
r.postcode_status                      # 'found': 08028 is a postcode in Barcelona
r.duc_l1_label, r.smod_label           # unit class and grid class
```

`classify` returns a `DegurbaResult` with one attribute per output column (see [Output](#output)).

To check the postcode against the province or state reported with it, pass `region`:

```python
r = dw.classify("10001", "US", region="California")
r.region_match                         # False: 10001 is in New York
```

Without a country, `classify` returns a DataFrame with one fully classified row per country in which the postcode exists:

```python
dw.classify("SW1A 1AA")                # one row: only the United Kingdom uses this format
```

For a table, use `classify_many`, which returns a DataFrame with one row per input row. Read postcodes as text so that leading zeros are kept:

```python
import pandas as pd

df = pd.read_csv("quickstart/postcodes_input.csv", dtype=str)
out = dw.classify_many(df["postcode"], df["country"], df["region"], df["place"])
```

`quickstart/quickstart.py` runs this example and saves the result to `quickstart/postcodes_output.csv`. The copy in the repository comes from one run of the script; your results can differ slightly if GeoNames or OpenStreetMap have changed since.

The country, region and place arguments take one value per row, a single value for all rows, or nothing. A missing country is inferred from the postcode if only one country fits ([how](docs/methodology.md#3-missing-country)), and `among` limits the search to the countries of your study:

```python
out = dw.classify_many(df["postcode"], "ES")                  # one country for all rows
out = dw.classify_many(df["postcode"], among=["DE", "ES"])    # infer it among these
```

Other functions:

- `dw.from_coords(lat, lon, country)` classifies coordinates you already have.
- `check_postcode`, `region_check` and `candidate_countries` check formats offline, without looking anything up.

### Countries without postcode data

GeoNames does not cover the postcodes of every country, and `has_postcode_data(country)` is `False` for the countries it leaves out. For them, you can pass the town or city reported with the postcode, which is used when the postcode itself cannot be located:

```python
r = dw.classify("100000", "CN", place="Beijing")
```

The place is only a fallback. In the quickstart example OpenStreetMap knows the postcode `100000`, so Beijing is not needed and the row is `found`; when the place is used, the status is `found_by_place`.

You can also register a postcode table of your own, a CSV file with the columns `postcode`, `lat`, `lon` and, optionally, `place`:

```python
from degurba_world import register_postcodes

register_postcodes("CN", "my_postcodes.csv")
```

`build_postcode_table` creates such a table from pairs of postcodes and towns, for example two columns of a survey, by geocoding each town once with OpenStreetMap.

### Main options

| option | default | description |
|---|---|---|
| `epoch` | `2025` | GHSL epoch, from 1975 to 2030 in steps of 5 years. Choose the one closest to the year of your data. |
| `grid`, `units` | `True` | Compute the grid classification and the unit classification. With both set to `False`, the package only checks and locates postcodes, without downloading GHSL or GADM data. |
| `gadm_level` | `None` | GADM level for the unit classification. With `None`, the package chooses it ([how](docs/methodology.md#6-unit-classification)). |
| `max_gadm_level` | `5` | Deepest GADM level the package may choose when `gadm_level` is `None`. |
| `max_snap_km` | `25` | Largest distance, in km, at which a point outside every administrative unit is assigned to the nearest one. |
| `window` | `3` | Side, in grid cells, of the window used for the most frequent grid class. |
| `use_nominatim_fallback` | `True` | Search OpenStreetMap for postcodes missing from GeoNames, and for towns or cities given with `place`. |
| `postcode_data` | `None` | Your own postcode tables, as `{country: path}`. |
| `cache_dir` | `~/.cache/degurba_world` | Folder for downloaded data. |
| `smod_path`, `duc_zip_path` | `None` | Local copies of the GHSL files, used instead of downloading them. |

### Command line

```bash
degurba-world 08028 ES
degurba-world 10001 US --region "New York"
degurba-world --csv quickstart/postcodes_input.csv
degurba-world --csv quickstart/postcodes_input.csv --out postcodes_output.csv
degurba-world --csv quickstart/postcodes_input.csv --check-only --no-nominatim
degurba-world --help
```

With `--csv`, the input needs a `postcode` column; `country`, `region` and `place` columns are optional, and any other columns are copied to the output. The results are written to `degurba_output.csv` in the current folder, or to the file given with `--out`.

| flag | effect |
|---|---|
| `--region`, `--place` | Region and town or city reported with a single postcode. |
| `--among DE,ES` | Countries to consider when the country is missing. |
| `--epoch`, `--gadm-level` | Set the options of the same name. |
| `--no-grid`, `--no-units` | Turn off `grid` or `units`; `--check-only` turns off both. |
| `--no-nominatim` | Sets `use_nominatim_fallback=False`, so that nothing is sent to OpenStreetMap. |
| `--postcode-data CC=FILE` | Registers a postcode table of your own for a country; can be repeated. |
| `--inspect-duc` | Prints the contents of the GHS-DUC archive and exits. |

`window`, `max_gadm_level`, `max_snap_km` and `cache_dir` are only available from Python.

## Output

Results contain these fields:

| group | columns |
|---|---|
| Postcode check | `postcode`, `postcode_status`, `postcode_warning`, `country`, `country_inferred`, `country_candidates`, `country_candidates_unchecked`, `region`, `region_match`, `place_reported` |
| Location | `lat`, `lon`, `place`, `geocoder`, `geocode_precision`, `geocode_note` |
| Grid classification | `smod_cell_l2`, `smod_mode_l2`, `smod_mode_share`, `smod_l1`, `smod_label` |
| Unit classification | `gadm_level`, `gadm_gid`, `gadm_name`, `duc_l1`, `duc_l2`, `duc_label`, `duc_l1_label` |

The main results are `duc_l1` for the unit and `smod_l1` for the grid, coded from 3 (most urban) to 1 (rural). [docs/outputs.md](docs/outputs.md) describes every column, the status values and warnings, the classes, and when a class is left empty.

## Limitations

- GeoNames, the main source of postcode coordinates, covers fewer than 100 countries. In the others, many postcodes can be neither checked nor located (`no_reference_data`) without a postcode table of your own (see [Countries without postcode data](#countries-without-postcode-data)).
- A postcode is represented by its centroid. A postcode that covers several municipalities or types of settlement takes the class found at its centroid, so precision depends on the size of postcodes in each country.
- Some locations are approximate. When only part of a postcode is matched, or a row is located from the reported town or city, the point stands for a larger area; `geocode_precision` and `geocode_note` record these cases.
- The checks show whether a postcode exists, not whether it belongs to the respondent. Only `region_match` can reveal a real postcode from another region.
- Postcode formats come from Google's address metadata and may lag behind changes in national postcode systems.
- Results can differ from Eurostat's classification of LAUs (see [Comparison with Eurostat](docs/outputs.md#comparison-with-eurostat)), and GADM levels mean different things in different countries (see [Unit classification](docs/methodology.md#6-unit-classification)).
- GHSL layers are model estimates, and the 2025 and 2030 epochs are projections.
- OpenStreetMap Nominatim is an online service and receives the postcodes and places it is asked about. Set `use_nominatim_fallback=False` (`--no-nominatim` on the command line) to keep all lookups on your computer.
- GeoNames sometimes lists one postcode with several places far apart, and the point used is their mean. `geocode_note` flags codes whose places lie more than 10 km from that mean.

## Data sources

None of these data are included in the repository, apart from the values in the example output `quickstart/postcodes_output.csv`.

| data | version | used for | licence |
|---|---|---|---|
| [GHS-SMOD](https://human-settlement.emergency.copernicus.eu/ghs_smod2023.php) | R2023A | Grid classification | European Commission reuse notice (attribution) |
| [GHS-DUC](https://human-settlement.emergency.copernicus.eu/ghs_duc2023.php) | R2023A, V2.0 | Unit classification | European Commission reuse notice (attribution) |
| [GADM](https://gadm.org) | 4.1 | Administrative boundaries | Academic and non-commercial use; no redistribution without permission ([licence](https://gadm.org/license.html)) |
| [GeoNames postal codes](https://www.geonames.org) | Files current at download time | Postcode coordinates | CC BY 4.0 |
| [OpenStreetMap Nominatim](https://nominatim.org) | Online service | Fallback geocoding | ODbL; [usage policy](https://operations.osmfoundation.org/policies/nominatim/) |
| Google address metadata, in [google-i18n-address](https://pypi.org/project/google-i18n-address/) | Installed package | Postcode formats and ranges | BSD-3-Clause (package) |

To reproduce a result, record the version of this package (`degurba_world.__version__`), the epoch, the version of `google-i18n-address` and the date on which the data were downloaded, and keep the cache folders described in [docs/methodology.md](docs/methodology.md#downloaded-files-and-cache). The GHSL and GADM releases are fixed in the code, but GeoNames updates its postal code files without version numbers.

## How to cite

Please cite the software and the data it uses. The software citation is in `CITATION.cff`, which GitHub shows under "Cite this repository".

- Casanovas, I. (2026). *degurba_world: Degree of Urbanisation for postcodes worldwide* (Version 0.1.0) [Computer software]. https://github.com/ivancasanovaas/degurba_world
- Schiavina, M., Melchiorri, M., Pesaresi, M. (2023). *GHS-SMOD R2023A: GHS settlement layers, application of the Degree of Urbanisation methodology (stage I) to GHS-POP R2023A and GHS-BUILT-S R2023A, multitemporal (1975–2030).* European Commission, Joint Research Centre. https://doi.org/10.2905/A0DF7A6F-49DE-46EA-9BDE-563437A6E2BA
- Schiavina, M., Melchiorri, M., Freire, S. (2023). *GHS-DUC R2023A: GHS Degree of Urbanisation Classification, application of the Degree of Urbanisation methodology (stage II) to GADM 4.1 layer, multitemporal (1975–2030).* European Commission, Joint Research Centre. https://doi.org/10.2905/DC0EB21D-472C-4F5A-8846-823C50836305
- Eurostat (2021). *Applying the Degree of Urbanisation: A methodological manual to define cities, towns and rural areas for international comparisons* (2021 edition). Publications Office of the European Union. https://doi.org/10.2785/706535
- GADM, version 4.1. https://gadm.org
- GeoNames postal code data. https://www.geonames.org

## Support

`degurba_world` is developed and maintained by Ivan Casanovas Rodríguez ([ORCID 0009-0003-6168-7459](https://orcid.org/0009-0003-6168-7459)) at the OpenSystems group, Universitat de Barcelona (https://www.ub.edu/opensystems/). Questions, bug reports and suggestions are welcome as [GitHub issues](https://github.com/ivancasanovaas/degurba_world/issues).

## Licence

© 2026 Ivan Casanovas Rodríguez. The code is licensed under the [MIT License](LICENSE): you may use, copy, modify and distribute it for any purpose, including commercial use, provided the copyright notice and the licence text are kept. The documentation (this README and the `docs` folder) is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): you may share and adapt it if you give credit (see [How to cite](#how-to-cite)), link to the licence and say whether you made changes.

The licence does not cover the data that the package downloads (GHSL, GADM, GeoNames, OpenStreetMap) or the Google address metadata in `google-i18n-address`; they keep their own terms (see [Data sources](#data-sources)). The same applies to the values taken from those sources in `quickstart/postcodes_output.csv`. GADM allows only academic and non-commercial use, so commercial use of the unit classification needs GADM's permission.
