#!/usr/bin/env python3
"""Nightly job: build the SDN identifier index used by identifier comparison.

  python ingest_sdn_identifiers.py SDN.CSV ALT.CSV sdn_identifiers.json

Source files: https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/{SDN.CSV,ALT.CSV}
Output: {"listVersion": {...}, "entries": {uid: Entry}} — see spec section 2.2.
Run after the existing name sync; publish both from the same download so
names and identifiers always share one listVersion.
"""
import csv, hashlib, json, re, sys, datetime
from email.utils import parsedate_to_datetime

MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
COUNTRY = {  # OFAC country spellings -> ISO 3166-1 alpha-2. Unmapped values are kept raw.
 "Afghanistan":"AF","Albania":"AL","Algeria":"DZ","Angola":"AO","Argentina":"AR","Armenia":"AM","Australia":"AU",
 "Austria":"AT","Azerbaijan":"AZ","Bahrain":"BH","Bangladesh":"BD","Barbados":"BB","Belarus":"BY","Belgium":"BE",
 "Benin":"BJ","Bolivia":"BO","Bosnia and Herzegovina":"BA","Brazil":"BR","Bulgaria":"BG","Burkina Faso":"BF",
 "Burma":"MM","Cabo Verde":"CV","Cambodia":"KH","Cameroon":"CM","Canada":"CA","Central African Republic":"CF",
 "Chad":"TD","Chile":"CL","China":"CN","Colombia":"CO","Comoros":"KM","Congo, Democratic Republic of the":"CD",
 "Costa Rica":"CR","Croatia":"HR","Cuba":"CU","Cyprus":"CY","Czech Republic":"CZ","Denmark":"DK","Djibouti":"DJ",
 "Dominica":"DM","Dominican Republic":"DO","Ecuador":"EC","Egypt":"EG","El Salvador":"SV","Equatorial Guinea":"GQ",
 "Eritrea":"ER","Estonia":"EE","Ethiopia":"ET","Fiji":"FJ","Finland":"FI","France":"FR","Georgia":"GE",
 "Germany":"DE","Ghana":"GH","Greece":"GR","Guatemala":"GT","Guinea":"GN","Guinea-Bissau":"GW","Guyana":"GY",
 "Haiti":"HT","Honduras":"HN","Hong Kong":"HK","Hungary":"HU","India":"IN","Indonesia":"ID","Iran":"IR","Iraq":"IQ",
 "Ireland":"IE","Israel":"IL","Italy":"IT","Japan":"JP","Jordan":"JO","Kazakhstan":"KZ","Kenya":"KE",
 "Korea, North":"KP","Korea, South":"KR","Kosovo":"XK","Kuwait":"KW","Kyrgyzstan":"KG","Latvia":"LV","Lebanon":"LB",
 "Liberia":"LR","Libya":"LY","Liechtenstein":"LI","Lithuania":"LT","Malaysia":"MY","Maldives":"MV","Mali":"ML",
 "Malta":"MT","Mauritania":"MR","Mexico":"MX","Moldova":"MD","Mongolia":"MN","Montenegro":"ME","Morocco":"MA",
 "Mozambique":"MZ","Netherlands":"NL","New Zealand":"NZ","Nicaragua":"NI","Niger":"NE","Nigeria":"NG",
 "North Macedonia, The Republic of":"MK","Norway":"NO","Oman":"OM","Pakistan":"PK","Palestinian":"PS",
 "Panama":"PA","Paraguay":"PY","Peru":"PE","Philippines":"PH","Poland":"PL","Portugal":"PT","Qatar":"QA",
 "Romania":"RO","Russia":"RU","Rwanda":"RW","Saint Kitts and Nevis":"KN","Saudi Arabia":"SA","Senegal":"SN",
 "Serbia":"RS","Sierra Leone":"SL","Singapore":"SG","Slovakia":"SK","Slovenia":"SI","Somalia":"SO",
 "South Africa":"ZA","South Sudan":"SS","Spain":"ES","Sri Lanka":"LK","Sudan":"SD","Sweden":"SE",
 "Switzerland":"CH","Syria":"SY","Taiwan":"TW","Tajikistan":"TJ","Tanzania":"TZ","Thailand":"TH","The Gambia":"GM",
 "Togo":"TG","Trinidad and Tobago":"TT","Tunisia":"TN","Turkey":"TR","Turkmenistan":"TM","Uganda":"UG",
 "Ukraine":"UA","United Arab Emirates":"AE","United Kingdom":"GB","United States":"US","Uzbekistan":"UZ",
 "Vanuatu":"VU","Venezuela":"VE","Vietnam":"VN","West Bank":"PS","Yemen":"YE","Zimbabwe":"ZW",
}
# Strong document identifiers. Phone numbers, electoral and military registry numbers are excluded on purpose.
ID_LABELS = ["Passport", "National ID No.", "Cedula No.", "Tax ID No.", "Identification Number", "SSN",
             "Birth Certificate Number", "Residency Number", "National Foreign ID Number", "Travel Document Number",
             "Turkish Identification Number", "Kenyan ID No.", "Driver's License No.", "Personal ID Card",
             "C.U.R.P.", "R.F.C.", "NIT #", "RUC #", "Registration Number", "Company Number", "Registration ID"]
ID_RE = re.compile(r"(?:^|; )(?:alt\. )?(" + "|".join(re.escape(l) for l in ID_LABELS) + r") ([A-Za-z0-9][A-Za-z0-9\-\/\. ]{2,40}?)(?: \(([^)]+)\))?(?=;|\.$|$| issued| expires)")
DOB_RE = re.compile(r"(?:^|; )(?:alt\. )?DOB (circa )?([^;]+?)(?=;|\.?$)")
DATE_RE = re.compile(r"^(?:(\d{1,2}) )?(?:([A-Z][a-z]{2}) )?(\d{4})$")
WALLET_RE = re.compile(r"Digital Currency Address - ([A-Z0-9]+) ([A-Za-z0-9]+)")


def norm_id(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _bound(s, end):
    m = DATE_RE.match(s.strip())
    if not m: return None, None
    d, mo, y = m.group(1), m.group(2), int(m.group(3))
    if d and mo:
        dt = datetime.date(y, MONTHS[mo], int(d)); return dt, "day"
    if mo:
        mi = MONTHS[mo]
        if end:
            nxt = datetime.date(y + (mi == 12), mi % 12 + 1, 1)
            return nxt - datetime.timedelta(days=1), "month"
        return datetime.date(y, mi, 1), "month"
    return (datetime.date(y, 12, 31) if end else datetime.date(y, 1, 1)), "year"


def parse_dob(raw, circa):
    """'16 Jun 1977' | '1977' | 'Jun 1977' | '1960 to 1965' | '01 Jan 1960 to 31 Dec 1962' | 'circa 1960' | 'circa 1960-1962'"""
    raw = raw.strip().rstrip(".")
    parts = re.split(r" to |(?<=\d{4})-(?=\d{4})", raw)
    lo, p1 = _bound(parts[0], False)
    hi, p2 = _bound(parts[-1], True)
    if not lo or not hi: return None
    precision = "range" if len(parts) > 1 else p1
    if circa:  # OFAC 'circa' widens the interval by two years each side
        lo, hi = lo.replace(year=lo.year - 2, day=min(lo.day, 28)), hi.replace(year=hi.year + 2, day=min(hi.day, 28))
    return {"from": lo.isoformat(), "to": hi.isoformat(), "precision": precision, "circa": bool(circa), "raw": raw}


def parse_entry(r):
    rem = r[11] if r[11] != "-0- " else ""
    e = {"uid": r[0], "type": "individual" if r[2].strip() == "individual" else (r[2].strip().strip("-0") or "entity"),
         "name": r[1], "programs": [p.strip() for p in r[3].replace("] [", "|").split("|")],
         "aliases": [], "dob": [], "nationality": [], "citizenship": [], "pob": [], "ids": [], "wallets": [],
         "gender": None, "unparsed": []}
    for m in DOB_RE.finditer(rem):
        d = parse_dob(m.group(2), m.group(1))
        (e["dob"].append(d) if d else e["unparsed"].append("DOB " + m.group(2)))
    for kind in ("nationality", "citizen"):
        for v in re.findall(r"(?:^|; )(?:alt\. )?" + kind + r" ([^;]+?)(?=;|\.?$)", rem):
            v = v.strip().removeprefix("possibly ")
            e["nationality" if kind == "nationality" else "citizenship"].append(COUNTRY.get(v, v))
    e["pob"] = [v.strip() for v in re.findall(r"(?:^|; )(?:alt\. )?POB ([^;]+?)(?=;|\.?$)", rem)]
    for m in ID_RE.finditer(rem):
        e["ids"].append({"type": m.group(1), "number": norm_id(m.group(2)), "raw": m.group(2).strip(),
                         "country": COUNTRY.get((m.group(3) or "").strip(), (m.group(3) or "").strip()) or None})
    e["wallets"] = [{"asset": a, "address": w} for a, w in WALLET_RE.findall(rem)]
    g = re.search(r"Gender (Male|Female)", rem); e["gender"] = g.group(1) if g else None
    return e


def build(sdn_path, alt_path, ofac_last_modified=None, fetched_at=None):
    raw = open(sdn_path, "rb").read()
    alt_raw = open(alt_path, "rb").read()
    rows = [r for r in csv.reader(raw.decode("latin-1").splitlines()) if len(r) > 11]
    entries = {r[0]: parse_entry(r) for r in rows}
    for a in csv.reader(alt_raw.decode("latin-1").splitlines()):
        if len(a) > 3 and a[0] in entries: entries[a[0]]["aliases"].append({"type": a[2].strip(), "name": a[3]})
    ind = [e for e in entries.values() if e["type"] == "individual"]
    stats = {"entries": len(entries), "individuals": len(ind),
             "individualsWithDob": sum(bool(e["dob"]) for e in ind),
             "individualsWithStrongId": sum(bool(e["ids"]) for e in ind),
             "unparsedDobFragments": sum(len(e["unparsed"]) for e in entries.values())}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    lv = {"source": "OFAC_SDN",
          "sha256": hashlib.sha256(raw).hexdigest(),
          "altSha256": hashlib.sha256(alt_raw).hexdigest(),
          "builtAt": now,
          "individuals": len(ind),
          "entries": len(entries)}
    if ofac_last_modified:  # HTTP date ("Wed, 14 Oct 2026 14:02:30 GMT") -> ISO 8601 UTC
        lv["ofacLastModified"] = parsedate_to_datetime(ofac_last_modified).astimezone(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lv["fetchedAt"] = fetched_at or now
    # Publish gates G1-G7 run in check_build_gates.py against the built artifacts.
    return {"listVersion": lv, "stats": stats, "entries": entries}


if __name__ == "__main__":
    ofac_lm = sys.argv[4] if len(sys.argv) > 4 else None
    fetched = sys.argv[5] if len(sys.argv) > 5 else None
    idx = build(sys.argv[1], sys.argv[2], ofac_lm, fetched)
    json.dump(idx, open(sys.argv[3], "w"), separators=(",", ":"), ensure_ascii=False)
    print(json.dumps(idx["listVersion"]), json.dumps(idx["stats"]))
    s = idx["stats"]
    # G5: fail the job (keep yesterday's index) if parsing regresses.
    if s["individualsWithDob"] / s["individuals"] < 0.98 or s["unparsedDobFragments"] > 25:
        sys.exit("identifier parse coverage regressed; not publishing")
