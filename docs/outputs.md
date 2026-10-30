# Output reference

The fields below are the attributes of a `DegurbaResult` and, in the same order, the columns of the DataFrames returned by `classify_many` and by `classify` without a country. `classify_many` adds an `error` column when a row raises an exception; the other rows are not affected. In DataFrames, the int columns use pandas' nullable `Int64` type and the bool columns the nullable `boolean` type, so empty values stay empty and classes are written as `30`, not `30.0`. How each value is obtained is described in [methodology.md](methodology.md).

## Postcode check

| column | type | description |
|---|---|---|
| `postcode` | str | Postcode in the country's standard form (see [Input](methodology.md#1-input)). |
| `postcode_status` | str | Result of the check. See [Status values](#status-values). |
| `postcode_warning` | str | Reasons to doubt the postcode, separated by semicolons. See [Warnings](#warnings). |
| `country` | str | ISO 3166-1 alpha-2 code. Empty if no country was given and none could be inferred. |
| `country_inferred` | bool | `True` if the country was inferred from the postcode. |
| `country_candidates` | str | Filled when no country was given and the formats of several countries fit: the countries where the code exists, as space-separated ISO codes. |
| `country_candidates_unchecked` | str | Filled in the same case: countries whose format fits but that have no postcode data, so the code could also belong to them. |
| `region` | str | Region given with `region` or `regions`. |
| `region_match` | bool | `True` if the postcode lies in that region and `False` if it does not. Empty if no region was given or the region could not be checked. |
| `place_reported` | str | Town or city given with `place` or `places`. |

### Status values

| `postcode_status` | meaning |
|---|---|
| `found` | The postcode was located. `geocoder` gives the source. |
| `found_by_place` | The postcode was not located, but the town or city given with `place` was. |
| `not_found` | The format is valid and the country has postcode data, but the code is not in that data or in OpenStreetMap. It may be invented, very new, or a special code such as a PO box. A country counts as covered once you register a table for it, even if the table is incomplete. |
| `no_reference_data` | The format is valid, but no postcode data cover the country, so the code could not be checked. This says nothing about whether the code is real. |
| `invalid_format` | The code does not match the country's postcode format, or falls outside every provincial range where these are known. |
| `missing` | The value is empty or a common non-answer such as `N/A` or `-`. |
| `country_ambiguous` | No country was given and the code could not be assigned to a single country. Returned by `classify_many` and `classify(..., expand=False)`, and by `classify` without a country when the code exists in none of the countries whose format fits; the warning then says it "exists in none of them". |

### Warnings

`postcode_warning` can contain:

- `looks like a placeholder`. Some of these codes are real, so the row may still be `found`.
- A note on a known non-residential code, for example a US military post office.
- A note that a shortened code identifies only the locality.
- `postcode is not in the reported region`, `region ... not recognised` or `no postcode ranges are known for the regions of ...`, from the region check.
- `found in the reference data although it does not match the usual format`.
- `no postcode format is known for ...`: Google's address metadata have no format for the country, so only existence was checked.
- `no postcode dataset covers ...`, together with `no_reference_data`.
- `the postcode itself was not located ...`, together with `found_by_place`.
- When no country was given: how many countries' formats fit, where the code exists, and which countries could not be checked.

## Location

| column | type | description |
|---|---|---|
| `lat`, `lon` | float | WGS84 coordinates of the point that is classified. Empty if no location was found. |
| `place` | str | Place name returned with the coordinates. |
| `geocoder` | str | Source of the coordinates: `local` (a table you registered), `geonames_full` (GeoNames file with full codes), `geonames`, `nominatim` (OpenStreetMap search for the postcode) or `nominatim_place` (OpenStreetMap search for the town or city in `place_reported`). |
| `geocode_precision` | str | `full` if the whole postcode was matched; `partial` if only its first part or a prefix was, so the point is the centroid of a larger area; `locality` if the point is the centre of the reported town or city. |
| `geocode_note` | str | Notes on how well the point represents the postcode: partial matches, codes that GeoNames lists with places more than 10 km from their mean, and rural Canadian postal areas (second character `0`), which can be very large. |

## Grid classification (GHS-SMOD)

| column | type | description |
|---|---|---|
| `smod_cell_l2` | int | Level-2 class of the cell that contains the point. Empty if the cell has no data. |
| `smod_mode_l2` | int | Most frequent level-2 class in the window around the point. This is the recommended grid value. |
| `smod_mode_share` | float | Share of the valid, non-water cells in the window that have the most frequent class. Values well below 1 mean the point lies near a class boundary. |
| `smod_l1` | int | Level-1 class derived from `smod_mode_l2`, or from `smod_cell_l2` if the window has no valid cells. |
| `smod_label` | str | Name of the level-2 class used for `smod_l1`. |

Grid classes, as defined by GHSL:

| level 2 | label | definition | level 1 |
|---|---|---|---|
| 30 | Urban centre | Cells with at least 1,500 inhabitants per km² (or mostly built up) in a cluster of at least 50,000 inhabitants | 3 |
| 23 | Dense urban cluster | Cells with at least 1,500 inhabitants per km² in a cluster of 5,000 to 50,000 inhabitants | 2 |
| 22 | Semi-dense urban cluster | Cells with at least 300 inhabitants per km² in a cluster of at least 5,000 inhabitants, apart from urban centres and dense clusters | 2 |
| 21 | Suburban or peri-urban | Other cells of urban clusters | 2 |
| 13 | Rural cluster | Cells with at least 300 inhabitants per km² in a cluster of 500 to 5,000 inhabitants | 1 |
| 12 | Low density rural | Other cells with at least 50 inhabitants per km² | 1 |
| 11 | Very low density rural | Cells with fewer than 50 inhabitants per km² | 1 |
| 10 | Water | Cells that are mostly water | none |

## Unit classification (GHS-DUC on GADM)

| column | type | description |
|---|---|---|
| `gadm_level` | int | GADM level used. |
| `gadm_gid` | str | GADM 4.1 identifier of the unit, which can be used to join other GADM-based data. |
| `gadm_name` | str | Name of the unit. |
| `duc_l1` | int | Level-1 class of the unit. |
| `duc_l2` | int | Level-2 class of the unit. |
| `duc_label`, `duc_l1_label` | str | Names of the level-2 and level-1 classes. |

Unit classes, as defined in the methodological manual:

| level 1 | label | rule |
|---|---|---|
| 3 | City | At least 50% of the population lives in urban centres. |
| 2 | Town and semi-dense area | Less than 50% lives in urban centres and less than 50% in rural grid cells. |
| 1 | Rural area | At least 50% lives in rural grid cells. |

| level 2 | label | the largest share of the population lives in |
|---|---|---|
| 30 | City | urban centres |
| 23 | Dense town | dense urban clusters |
| 22 | Semi-dense town | semi-dense urban clusters |
| 21 | Suburban or peri-urban area | suburban or peri-urban cells |
| 13 | Village | rural clusters |
| 12 | Dispersed rural area | low density rural cells |
| 11 | Mostly uninhabited area | very low density rural cells |

## Which classification to use

The unit classification (`duc_*`) is the one to use for comparisons with official statistics, which are published by administrative unit, and when the analysis is at the municipal level. The grid classification (`smod_*`) describes the surroundings of the location itself, and is more informative where administrative units are large and mix cities with rural land.

The two can disagree. A suburban postcode can lie in an urban centre cell while its municipality is a town, and a village can belong to a large municipality classed as a city. Treat a row with care when `postcode_warning` is not empty, `region_match` is `False`, `smod_mode_share` is below 0.5, `geocoder` is `nominatim` or `nominatim_place`, `geocode_precision` is `partial` or `locality`, or `geocode_note` is not empty.

## Comparison with Eurostat

Eurostat numbers the level-1 classes in the opposite order: 1 = cities, 2 = towns and suburbs, 3 = rural areas. To compare with Eurostat data, use `4 - duc_l1`. The classes can still differ from Eurostat's for the same place, because GHS-DUC classifies GADM units instead of the LAUs used by Eurostat, and it is computed from another population grid and for other reference years.

## Empty classes

A class column is empty when:

- the row was not located (`postcode_status` is not `found` or `found_by_place`);
- the window contains only water or cells without data (grid columns);
- the unit has no match in GHS-DUC at that level (try a lower `gadm_level`), or the point lies outside the country (unit columns).
