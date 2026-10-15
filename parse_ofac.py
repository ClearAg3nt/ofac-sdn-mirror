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

import hashlib
meta = {
    "syncedAt": datetime.now(timezone.utc).isoformat(),
    # Raw-byte hash of the sdn.csv this name list came from; gate G1 requires it to
    # equal sdn_identifiers.json listVersion.sha256 (same download for both indexes).
    "sha256":   hashlib.sha256(open("sdn.csv", "rb").read()).hexdigest(),
    "count":    len(names_list),
    "source":   "OFAC SDN + ALT lists (ofac.treas.gov)",
    "columns":  {"sdn.csv": "SDN_Name (index 1)", "alt.csv": "alt_name (index 3)"},
}

with open("ofac_names.json", "w") as f:
    json.dump(names_list, f, separators=(",", ":"))   # compact — smaller file

with open("metadata.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"✓ Written ofac_names.json ({len(names_list):,} names) + metadata.json")


# ── Identifier + type index (ofac_identifiers.json) ───────────────────────────
# Secondary identifiers let /v1/screen downgrade a name-only BLOCK to REVIEW when the
# screened party's DOB / nationality / ID demonstrably differ from the listed party.
# Everything here comes from sdn.csv: SDN_Type (col 2) and the free-text Remarks
# (col 11). Addresses live in ADD.CSV, which this pipeline does not fetch, so address
# is never published and always compares as NOT_PROVIDED_BY_LIST.
import re as _re

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_dobs(remarks: str):
    """Return [{'value': ISO-ish, 'precision': 'day'|'month'|'year', 'circa': bool}]."""
    out = []
    for raw in _re.findall(r"(?:alt\.\s*)?DOB\s+([^;]+)", remarks):
        chunk = raw.strip()
        circa = bool(_re.search(r"\bcirca\b", chunk, _re.I))
        # "1948 to 1950" — a range: publish each endpoint as a year-precision value
        years = _re.findall(r"\b(1[89]\d{2}|20\d{2})\b", chunk)
        m = _re.match(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", chunk)
        if m:
            d, mon, y = int(m.group(1)), _MONTHS.get(m.group(2).lower()), int(m.group(3))
            if mon:
                out.append({"value": f"{y:04d}-{mon:02d}-{d:02d}", "precision": "day", "circa": circa})
                continue
        m = _re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{4})", chunk)
        if m and _MONTHS.get(m.group(1).lower()):
            out.append({"value": f"{int(m.group(2)):04d}-{_MONTHS[m.group(1).lower()]:02d}",
                        "precision": "month", "circa": circa})
            continue
        for y in years:
            out.append({"value": y, "precision": "year", "circa": circa})
    # de-duplicate, keep order
    seen, uniq = set(), []
    for d in out:
        k = (d["value"], d["precision"])
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


def _parse_list(remarks: str, pattern: str):
    vals = []
    for raw in _re.findall(pattern, remarks):
        v = raw.strip().strip(".").strip()
        if v and v.lower() not in ("unknown", "n/a"):
            vals.append(v)
    return list(dict.fromkeys(vals))


def _parse_ids(remarks: str, label: str):
    out = []
    for raw in _re.findall(label + r"\s+([A-Za-z0-9\-/ ]{3,30})", remarks):
        v = _re.sub(r"[^A-Za-z0-9]", "", raw).upper()
        if v:
            out.append(v)
    return list(dict.fromkeys(out))


def build_identifier_index():
    by_ent = {}
    with open("sdn.csv", encoding="utf-8", errors="replace") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 12:
                continue
            ent, name, sdn_type, remarks = row[0].strip(), row[1].strip(), row[2].strip(), row[11]
            if remarks.strip() == "-0-":
                remarks = ""
            rec = {"t": "individual" if sdn_type == "individual" else sdn_type if sdn_type in
                   ("vessel", "aircraft") else "entity"}
            dobs = _parse_dobs(remarks)
            nat = list(dict.fromkeys(_parse_list(remarks, r"nationality\s+([^;]+)")
                                     + _parse_list(remarks, r"citizen\s+([^;]+)")))
            pob = _parse_list(remarks, r"POB\s+([^;]+)")
            nid = _parse_ids(remarks, r"National ID No\.")
            pas = _parse_ids(remarks, r"Passport")
            if dobs: rec["dob"] = dobs
            if nat:  rec["nat"] = nat
            if pob:  rec["pob"] = pob
            if nid:  rec["nid"] = nid
            if pas:  rec["pas"] = pas
            by_ent[ent] = rec
            rec.setdefault("names", set()).add(norm_name(name))
    # aliases inherit their primary entry's identifiers
    with open("alt.csv", encoding="utf-8", errors="replace") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0 or len(row) < 4:
                continue
            rec = by_ent.get(row[0].strip())
            if rec:
                rec["names"].add(norm_name(row[3].strip()))
    index = {}
    for rec in by_ent.values():
        names = rec.pop("names")
        for nm in names:
            if len(nm) < 3:
                continue
            index.setdefault(nm, []).append(rec)
    return index


ident = build_identifier_index()
# Types-only projection: tiny enough to keep warm in the Worker isolate on every
# request, which is what the person-vs-entity disposition rule needs. The full
# identifier index above is only read when a caller supplies cpIdentifiers.
types = {nm: recs[0]["t"][0] for nm, recs in ident.items() if recs}
with open("ofac_types.json", "w") as f:
    json.dump(types, f, separators=(",", ":"))
print(f"✓ Written ofac_types.json ({len(types):,} names, "
      f"{sum(1 for v in types.values() if v == 'i'):,} individuals)")
with open("ofac_identifiers.json", "w") as f:
    json.dump(ident, f, separators=(",", ":"))
typed = sum(1 for v in ident.values() if v)
print(f"✓ Written ofac_identifiers.json ({len(ident):,} names, "
      f"{sum(1 for v in ident.values() if any('dob' in r for r in v)):,} with DOB)")
