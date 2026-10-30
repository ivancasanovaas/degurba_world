"""Run degurba_world on the example table.

From the repository folder:

    python quickstart/quickstart.py

The script reads postcodes_input.csv and writes the results to
postcodes_output.csv, both in this folder. The first run downloads GHSL and
GADM data (several hundred MB) to ~/.cache/degurba_world.
"""
import sys
from pathlib import Path

import pandas as pd

FOLDER = Path(__file__).resolve().parent

# Python searches for modules in the folder of the script it runs (quickstart),
# not in the repository folder that contains degurba_world.py. Adding that
# folder lets the example run without installing the package.
sys.path.insert(0, str(FOLDER.parent))
from degurba_world import DegurbaWorld  # noqa: E402

# Read postcodes as text so that leading zeros are kept.
df = pd.read_csv(FOLDER / "postcodes_input.csv", dtype=str)

dw = DegurbaWorld(epoch=2020)
res = dw.classify_many(df["postcode"], df["country"], df["region"], df["place"])
out = df[["id"]].join(res)

cols = ["id", "postcode", "country", "postcode_status", "region_match",
        "gadm_name", "duc_l1_label", "smod_label"]
print(out[cols].to_string(index=False))

# Eurostat numbers the classes the other way round:
# 1 = cities, 2 = towns and suburbs, 3 = rural areas.
out["degurba_eurostat"] = (4 - pd.to_numeric(out["duc_l1"])).astype("Int64")
out.to_csv(FOLDER / "postcodes_output.csv", index=False)
