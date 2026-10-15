#!/usr/bin/env python3
"""v0.5.1 publish gates (spec 3.2). Run after the build, before the KV push.

  python check_build_gates.py sdn.csv sdn_identifiers.json metadata.json

Exits non-zero on any G1-G6 failure, so the workflow stops and KV keeps the previous
index. G7 (freshness) only warns. The Worker builds its UID-carrying name index from
sdn_identifiers.json (primary + alias names per entry), so that is the name index the
G2/G3 checks model.
"""
import csv, datetime, hashlib, json, re, sys, unicodedata

CANARIES = ["45391", "34598", "16688", "58525", "22790", "26946"]


def norm_name(s):  # mirrors worker normName() closely enough to tell "usable" from "empty"
    s = unicodedata.normalize("NFKD", s).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s)).strip()


def check(sdn_path, idx_path, meta_path):
    raw = open(sdn_path, "rb").read()
    idx = json.load(open(idx_path))
    meta = json.load(open(meta_path))
    lv, stats, entries = idx["listVersion"], idx.get("stats", {}), idx["entries"]
    rows = [r for r in csv.reader(raw.decode("latin-1").splitlines()) if len(r) > 11]
    csv_uids = {r[0] for r in rows}
    csv_individuals = {r[0] for r in rows if r[2].strip() == "individual"}
    fails, warns = [], []

    # G1 same source: name list, identifier index and the csv on disk share one download.
    sha = hashlib.sha256(raw).hexdigest()
    if not (lv.get("sha256") == sha == meta.get("sha256")):
        fails.append(f"G1 sha256 mismatch: csv={sha} ids={lv.get('sha256')} names={meta.get('sha256')}")

    # Name index rows as the Worker builds them: (uid, normalised name) per primary + alias.
    name_rows = [(uid, norm_name(n)) for uid, e in entries.items()
                 for n in [e["name"], *(a["name"] for a in e.get("aliases", []))]]
    name_rows = [(u, n) for u, n in name_rows if n]

    # G2 UID coverage: individuals reachable by name == individuals in the identifier index.
    idx_ind = {u for u, e in entries.items() if e["type"] == "individual"}
    name_ind = {u for u, _ in name_rows} & idx_ind
    if name_ind != idx_ind:
        fails.append(f"G2 {len(idx_ind - name_ind)} individual UIDs unreachable by name: {sorted(idx_ind - name_ind)[:10]}")

    # G3 row integrity: every name row carries a UID present in SDN.CSV.
    bad = sorted({u for u, _ in name_rows if not u or u not in csv_uids})
    if bad: fails.append(f"G3 {len(bad)} name rows with UID not in SDN.CSV: {bad[:10]}")

    # G4 counts match SDN.CSV.
    want = {"individuals": len(csv_individuals), "entries": len(csv_uids)}
    for k, v in want.items():
        if lv.get(k) != v or stats.get(k) != v or (k == "entries" and len(entries) != v):
            fails.append(f"G4 {k}: listVersion={lv.get(k)} stats={stats.get(k)} csv={v}")
    if idx_ind != csv_individuals:
        fails.append(f"G4 individual UID set differs from SDN.CSV ({len(idx_ind ^ csv_individuals)} UIDs)")

    # G5 parse coverage.
    if not stats.get("individuals") or stats["individualsWithDob"] / stats["individuals"] < 0.98 \
            or stats["unparsedDobFragments"] > 25:
        fails.append(f"G5 parse coverage: {stats.get('individualsWithDob')}/{stats.get('individuals')} with DOB, "
                     f"{stats.get('unparsedDobFragments')} unparsed fragments")

    # G6 canaries in both indexes.
    name_uids = {u for u, _ in name_rows}
    miss = [u for u in CANARIES if u not in entries or u not in name_uids]
    if miss: fails.append(f"G6 canary UIDs missing: {miss}")

    # G7 freshness (alert only).
    lm, fa = lv.get("ofacLastModified"), lv.get("fetchedAt")
    if not lm:
        warns.append("G7 ofacLastModified missing")
    elif fa:
        p = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        age = (p(fa) - p(lm)).total_seconds() / 3600
        if age > 36: warns.append(f"G7 OFAC file is {age:.0f}h older than fetchedAt")
    return fails, warns


if __name__ == "__main__":
    fails, warns = check(*sys.argv[1:4])
    for w in warns: print(f"::warning::{w}", file=sys.stderr)
    for f in fails: print(f"::error::{f}", file=sys.stderr)
    if fails: sys.exit(f"{len(fails)} publish gate(s) failed; not publishing")
    print("✓ publish gates G1-G6 passed" + (f" ({len(warns)} G7 warning)" if warns else ""))
