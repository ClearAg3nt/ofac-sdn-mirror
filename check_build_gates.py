#!/usr/bin/env python3
"""Publish gates G1-G10 (v0.5.1 spec 3.2, v0.5.2 spec 2.4, CA-521). Run after the build, before the KV push.

  python check_build_gates.py sdn.csv sdn_identifiers.json metadata.json names.json \
      [--previous prev_identifiers.json] [--manifest-out manifest.json] [--wallets wallets.json]

Exits non-zero on any G1-G6, G8-G10 failure, so the workflow publishes nothing and KV keeps
the previous manifest. G7 (freshness vs --polled-last-modified) only warns, and is recorded in the manifest. On success, --manifest-out writes the
sdn:current manifest (spec 2.2) for this build.
"""
import argparse, csv, datetime, hashlib, json, re, sys, unicodedata

CANARIES = ["45391", "34598", "16688", "58525", "22790", "26946", "36025", "58930"]


def norm_name(s):  # mirrors worker normName() closely enough to tell "usable" from "empty"
    s = unicodedata.normalize("NFKD", s).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s)).strip()


def check(sdn_path, idx_path, meta_path, names_path, prev_path=None):
    raw = open(sdn_path, "rb").read()
    idx = json.load(open(idx_path))
    meta = json.load(open(meta_path))
    names = json.load(open(names_path))
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

    # G8 delisting: UIDs in the previous build but no longer in SDN.CSV are in no new index.
    published_uids = set(entries) | {r[0] for r in names["rows"]}
    if prev_path:
        removed = set(json.load(open(prev_path))["entries"]) - csv_uids
        stale = sorted(removed & published_uids)
        if stale: fails.append(f"G8 {len(stale)} delisted UIDs still published: {stale[:10]}")
    if any(norm_name(r[1]) == "TORNADO CASH" for r in names["rows"]):
        fails.append("G8 canary: an entity named TORNADO CASH is published (delisted 2025-03-21)")

    # G9 no orphan sources: the names index the Worker matches against is this build, row for row.
    if names.get("listVersion", {}).get("sha256") != sha:
        fails.append(f"G9 names index sha {names.get('listVersion', {}).get('sha256')} != SDN.CSV {sha}")
    orphans = sorted({r[0] for r in names["rows"] if r[0] not in entries})
    if orphans: fails.append(f"G9 {len(orphans)} name rows with UID not in the identifier index: {orphans[:10]}")
    if len(names["rows"]) != len(name_rows) + sum(1 for uid, e in entries.items()
            for n in [e["name"], *(a["name"] for a in e.get("aliases", []))] if not norm_name(n)):
        fails.append(f"G9 names index has {len(names['rows'])} rows, identifier index has a different variant count")
    return fails, warns


def check_g10(wallets_path, prev_path=None):
    """G10 (CA-521): wallet address count validation. Every chain must have >= 1 address.
    Any drop of 10% or more vs the previous manifest needs a matching delisting."""
    fails = []
    if not wallets_path:
        return fails
    wallets = json.load(open(wallets_path))
    chains = wallets.get("wallets", {})
    required = ["eth", "btc", "trx"]  # chains OFAC always publishes; others may be empty
    for c in required:
        if c not in chains or len(chains[c]) < 1:
            fails.append(f"G10 chain '{c}' has 0 addresses (must have >= 1)")
    # Check for >10% drop against previous build's wallet counts.
    if prev_path:
        try:
            prev = json.load(open(prev_path))
            prev_wallets = prev.get("wallets", {})
            for c, addrs in chains.items():
                prev_count = len(prev_wallets.get(c, []))
                if prev_count > 0 and len(addrs) < prev_count * 0.9:
                    fails.append(f"G10 chain '{c}' dropped from {prev_count} to {len(addrs)} addresses (>{10}% drop)")
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            pass  # no previous wallet data to compare against
    return fails


G7_MAX_LAG_SECONDS = 7200


def g7(lv, polled_last_modified):
    """G7 freshness at publish time (alert only, never blocks a publish): how far the OFAC
    Last-Modified seen by this run's poll is ahead of the file this build was made from."""
    p = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    try:
        lag = max(0, int((p(polled_last_modified) - p(lv["ofacLastModified"])).total_seconds()))
    except (KeyError, TypeError, ValueError, AttributeError):   # no poll value / unparseable
        return {"ok": False, "lagSeconds": None, "polledLastModified": polled_last_modified}
    return {"ok": lag <= G7_MAX_LAG_SECONDS, "lagSeconds": lag, "polledLastModified": polled_last_modified}


def manifest(idx, fails, g7_result, has_wallets=False):
    lv = idx["listVersion"]
    sha = lv["sha256"]
    gate_names = ["G1", "G2", "G3", "G4", "G5", "G6", "G8", "G9"]
    if has_wallets:
        gate_names.append("G10")
    gates = {g: not any(f.startswith(g + " ") for f in fails) for g in gate_names}
    keys = {"names": f"names:{sha}", "identifiers": f"ids:{sha}"}
    if has_wallets:
        keys["wallets"] = f"wallets:{sha}"
    return {"listVersion": lv,
            "keys": keys,
            "archive": {"sdn": f"r2://ofac/sdn/{sha}.csv", "alt": f"r2://ofac/alt/{lv.get('altSha256')}.csv"},
            "gates": {**gates, "G7": g7_result["ok"]},
            "gateDetail": {"G7": g7_result}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sdn"); ap.add_argument("ids"); ap.add_argument("meta"); ap.add_argument("names")
    ap.add_argument("--previous"); ap.add_argument("--manifest-out")
    ap.add_argument("--wallets", help="wallets.json from build_wallets_index.py (CA-521)")
    ap.add_argument("--polled-last-modified", help="OFAC SDN.CSV Last-Modified seen by this run's poll (ISO 8601)")
    a = ap.parse_args()
    fails, warns = check(a.sdn, a.ids, a.meta, a.names, a.previous)
    # G10 (CA-521): wallet address count validation.
    g10_fails = check_g10(a.wallets)
    fails.extend(g10_fails)
    g7_result = g7(json.load(open(a.ids))["listVersion"], a.polled_last_modified)
    if not g7_result["ok"]: warns.append(f"G7 build is behind OFAC: {g7_result}")
    for w in warns: print(f"::warning::{w}", file=sys.stderr)
    for f in fails: print(f"::error::{f}", file=sys.stderr)
    if fails: sys.exit(f"{len(fails)} publish gate(s) failed; not publishing")
    has_wallets = a.wallets is not None
    if a.manifest_out: json.dump(manifest(json.load(open(a.ids)), fails, g7_result, has_wallets), open(a.manifest_out, "w"), indent=1)
    gates_label = "G1-G6, G8-G10" if has_wallets else "G1-G6, G8, G9"
    print(f"✓ publish gates {gates_label} passed" + (f" ({len(warns)} G7 warning)" if warns else ""))
