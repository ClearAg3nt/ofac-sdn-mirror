#!/usr/bin/env python3
"""v0.5.2 wallet index (CA-521; spec 2.1 step 4): every chain's sanctioned addresses, from the
same OFAC publication as the name and identifier indexes.

  python build_wallets_index.py SDN.XML sdn_identifiers.json wallets.json "<SDN.XML Last-Modified>"

Source is SDN.XML, not SDN.CSV: the CSV truncates remarks at 1,000 chars and drops about half
the XBT addresses. G1 checks that the XML is the same publication (same UID set) as the CSV.
Published as wallets:{sha256 of SDN.CSV}; the Worker screens addresses only against it.
"""
import email.utils, hashlib, json, re, sys
import xml.etree.ElementTree as ET

xml_path, idx_path, out_path = sys.argv[1:4]
xml_lm = sys.argv[4] if len(sys.argv) > 4 else None
raw = open(xml_path, "rb").read()
idx = json.load(open(idx_path))
root = ET.fromstring(raw)
ns = {"o": root.tag.split("}")[0].strip("{")}

EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
TRON = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
# Worker chain sets. An EVM-format address is sanctioned on every EVM chain, so eth takes the
# 0x addresses of every asset (ARB, BSC, ETC, ...); likewise trx takes every Tron-format address.
chains = {k: set() for k in ("eth", "btc", "trx", "usdt", "usdc", "sol")}
owners, by_asset, uids = {}, {}, set()
for e in root.iterfind("o:sdnEntry", ns):
    uid = e.findtext("o:uid", namespaces=ns)
    uids.add(uid)
    for i in e.iterfind("o:idList/o:id", ns):
        t = i.findtext("o:idType", "", ns)
        if not t.startswith("Digital Currency Address - "):
            continue
        asset, addr = t.rsplit(" ", 1)[1], i.findtext("o:idNumber", "", ns).strip()
        by_asset[asset] = by_asset.get(asset, 0) + 1
        key = addr if asset == "SOL" else addr.lower()
        if asset == "XBT": chains["btc"].add(key)
        if asset == "SOL": chains["sol"].add(key)
        if asset == "USDT": chains["usdt"].add(key)
        if asset == "USDC": chains["usdc"].add(key)
        if EVM.match(addr): chains["eth"].add(key)
        if TRON.match(addr): chains["trx"].add(key)
        name = idx["entries"].get(uid, {}).get("name")
        owners[addr.lower()] = [uid, name]

pub = root.find("o:publshInformation", ns)
source = {"file": "SDN.XML", "sha256": hashlib.sha256(raw).hexdigest(),
          "publishDate": pub.findtext("o:Publish_Date", namespaces=ns),
          "recordCount": int(pub.findtext("o:Record_Count", "0", ns)), "uids": len(uids)}
if xml_lm:
    source["ofacLastModified"] = email.utils.parsedate_to_datetime(xml_lm).strftime("%Y-%m-%dT%H:%M:%SZ")
out = {"listVersion": idx["listVersion"], "walletSource": source,
       "wallets": {k: sorted(v) for k, v in chains.items()}, "owners": owners, "assets": by_asset,
       "uidSet": sorted(uids, key=int)}
json.dump(out, open(out_path, "w"), separators=(",", ":"))
print("wallets index:", json.dumps({k: len(v) for k, v in chains.items()}), "from", source)
