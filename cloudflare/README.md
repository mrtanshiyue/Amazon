# Amazon Keyword Manager — Cloudflare

Cloud-hosted multi-store Amazon keyword manager.

## Architecture

- Cloudflare Worker + Static Assets: UI and API
- Cloudflare R2 (private): structured state JSON and product images
- ETag conditional writes: prevents silent overwrite from stale devices
- Cloudflare Access: required for API requests by default
- GitHub Actions: creates the R2 bucket if missing and deploys the Worker

D1 is intentionally not used because the account is already at its D1 database limit. Existing D1 databases are left untouched.

## Required GitHub Actions secrets

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

The token needs permissions sufficient for Workers Scripts and Workers R2 Storage.

## Storage layout

- `app/state.json` — stores, products, SKU, ASIN, keywords, bids, negative keywords
- `product-images/<product-id>.jpg` — compressed product images

## Security

`ACCESS_REQUIRED` defaults to `true`. API requests require the
`Cf-Access-Authenticated-User-Email` header injected by Cloudflare Access.

Do not set `ACCESS_REQUIRED=false` for production.
