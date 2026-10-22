# ofac-sdn-mirror

Daily mirror of OFAC SDN entity names, published as a machine-readable JSON file for use in automated compliance screening.

**Updated:** daily at 01:00 UTC via GitHub Actions
**Source:** US Treasury Office of Foreign Assets Control (public domain)
**Maintained by:** [CLEARAGENT](https://clearagent.dev)

---

## Files

| File | Description |
|------|-------------|
| `ofac_names.json` | Normalised array of SDN entity names + aliases (~10K entries) |
| `metadata.json` | Sync metadata: timestamp, count, source |

## Usage

```js
// Cloudflare Worker / Node.js
const res  = await fetch(
  'https://raw.githubusercontent.com/clearagent/ofac-sdn-mirror/main/ofac_names.json'
);
const names = await res.json();   // string[]
const nameSet = new Set(names);

function isSanctioned(input) {
  const normalised = input.toUpperCase().replace(/[^A-Z0-9 ]/g, '').replace(/\s+/g, ' ').trim();
  return nameSet.has(normalised);
}
```

## Normalisation

Names are normalised to match the KYA Worker's `normName()` function:
1. Uppercase
2. Strip all characters except `A–Z`, `0–9`, space
3. Collapse whitespace, trim

This means `"VTB Bank, JSC"` → `"VTB BANK JSC"` — consistent across all name inputs.

## Sources

- `sdn.csv` — SDN_Name (column 1): primary entity names
- `alt.csv` — alt_name (column 3): aliases and AKAs

Both files are fetched from `sanctionslistservice.ofac.treas.gov` (with `www.treasury.gov` as fallback). US Government works are not subject to copyright — this mirror is freely usable.

## About CLEARAGENT

CLEARAGENT is the compliance layer for AI agent payments — OFAC screening, spend policy enforcement, and W3C Verifiable Credentials for autonomous agents. Live API at [api.clearagent.dev](https://api.clearagent.dev).

Proposing `X-KYA-Token` as a header extension for the [x402 payment protocol](https://github.com/coinbase/x402/issues/1547).

## CI secrets and variables (v0.5.2 pipeline)

`.github/workflows/sync.yml` publishes to production Cloudflare KV and R2, so its token must be able to write both.

| Name | Kind | Value |
|------|------|-------|
| `CLOUDFLARE_API_TOKEN` | secret | Custom API token with **Account → Workers KV Storage: Edit** and **Account → Workers R2 Storage: Edit**, scoped to the ClearAgent account |
| `CLOUDFLARE_ACCOUNT_ID` | secret | ClearAgent account ID |
| `R2_ARCHIVE_BUCKET` | variable | `clearagent-ofac-archive` (raw SDN/ALT archive, spec V3). If unset, archiving is skipped with a warning |

A token without these permissions fails with `401 … {"code":10000,"message":"Authentication error"}` on the first `--remote` call. The poll step swallows that error and rebuilds every hour; the archive or publish step then fails the job. Every wrangler KV/R2 command needs `--remote`: wrangler 4 defaults to local storage, and before v0.5.2 that meant CI never wrote to production at all.
