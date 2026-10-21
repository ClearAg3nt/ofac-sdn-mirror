#!/usr/bin/env python3
"""G8/G9 gate tests against built artifacts (v0.5.2 spec 6.1). python test_build_gates.py"""
import copy, json, os, tempfile
from check_build_gates import check

idx = json.load(open("sdn_identifiers.json"))
rows = [[e["uid"], n, e["type"]] for e in idx["entries"].values()
        for n in [e["name"], *(a["name"] for a in e.get("aliases", []))]]
tmp = tempfile.mkdtemp()
def path(name, obj):
    p = os.path.join(tmp, name); json.dump(obj, open(p, "w")); return p
meta = path("meta.json", {"sha256": idx["listVersion"]["sha256"]})
def gates(ids, names, prev=None):
    fails, _ = check("sdn.csv", path("ids.json", ids), meta, path("names.json", names), prev and path("prev.json", prev))
    return {f.split(" ")[0] for f in fails}

names = {"listVersion": idx["listVersion"], "rows": rows}
assert not gates(idx, names, idx) & {"G8", "G9"}, "clean build passes G8/G9"

# G8: a UID removed from SDN.CSV (present in the previous build) is still published.
stale = copy.deepcopy(idx); stale["entries"]["999999"] = {"uid": "999999", "type": "entity", "name": "REMOVED CO", "aliases": []}
assert "G8" in gates(stale, {**names, "rows": rows + [["999999", "REMOVED CO", "entity"]]}, stale)
# G8 canary: TORNADO CASH must not be published.
assert "G8" in gates(idx, {**names, "rows": rows + [[rows[0][0], "TORNADO CASH", "entity"]]})
# G9: a name row whose UID isn't in the identifier index, and a names index from another build.
assert "G9" in gates(idx, {**names, "rows": rows + [["888888", "ORPHAN NAME", "entity"]]})
assert "G9" in gates(idx, {**names, "listVersion": {**idx["listVersion"], "sha256": "0" * 64}})
print("✓ G8/G9 gate tests passed")
