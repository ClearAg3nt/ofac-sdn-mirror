#!/usr/bin/env python3
"""v0.5.2 name index (spec 2.1 step 4): one row per name variant, carrying uid and type,
built from the same identifier-index build, so both share one sha256.

  python build_names_index.py sdn_identifiers.json names.json

Published as names:{sha256}; the Worker matches names only against this (gate G9).
"""
import json, sys

idx = json.load(open(sys.argv[1]))
rows = [[e["uid"], n, e["type"]] for e in idx["entries"].values()
        for n in [e["name"], *(a["name"] for a in e.get("aliases", []))]]
json.dump({"listVersion": idx["listVersion"], "rows": rows}, open(sys.argv[2], "w"),
          separators=(",", ":"), ensure_ascii=False)
print(f"names index: {len(rows)} rows, sha256 {idx['listVersion']['sha256']}")
