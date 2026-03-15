#!/usr/bin/env python3
"""
parse_ofac.py — Extract and normalise entity names from OFAC SDN CSV files.

Outputs:
  ofac_names.json   — JSON array of normalised name strings (sorted)
  metadata.json     — sync metadata (count, timestamp, source)

Name normalisation matches the KYA Worker's normName() function:
  1. Uppercase
  2. Strip everything except A-Z, 0-9, space
  3. Collapse whitespace, trim
"""

import csv
import json
import re
import sys
from datetime import datetime, timezone


def norm_name(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", "", name.upper())
    return re.sub(r"\s+", " ", n).strip()


def extract_names(filename: str, col_idx: int) -> set:
    """Read a CSV file and extract normalised names from the given column."""
    names = set()
    try:
        with open(filename, encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i == 0:          # skip header row
                    continue
                if len(row) <= col_idx:
                    continue
                raw = row[col_idx].strip()
                if not raw or len(raw) < 3:
                    continue
                # Skip OFAC placeholder values
                if raw in ("-0-", "NULL", "N/A"):
                    continue
                normalised = norm_name(raw)
                if len(normalised) >= 3:
                    names.add(normalised)
    except FileNotFoundError:
        print(f"⚠️  {filename} not found — skipping", file=sys.stderr)
    return names


all_names: set = set()

# sdn.csv  — SDN_Name is column index 1
sdn_names = extract_names("sdn.csv", 1)
all_names |= sdn_names
print(f"  sdn.csv  → {len(sdn_names):,} primary entity names")

# alt.csv  — alt_name is column index 3
alt_names = extract_names("alt.csv", 3)
all_names |= alt_names
print(f"  alt.csv  → {len(alt_names):,} alias names")

names_list = sorted(all_names)
print(f"  total    → {len(names_list):,} unique normalised names")

meta = {
    "syncedAt": datetime.now(timezone.utc).isoformat(),
    "count":    len(names_list),
    "source":   "OFAC SDN + ALT lists (ofac.treas.gov)",
    "columns":  {"sdn.csv": "SDN_Name (index 1)", "alt.csv": "alt_name (index 3)"},
}

with open("ofac_names.json", "w") as f:
    json.dump(names_list, f, separators=(",", ":"))   # compact — smaller file

with open("metadata.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"✓ Written ofac_names.json ({len(names_list):,} names) + metadata.json")
