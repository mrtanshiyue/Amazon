# Amazon Keyword Manager — Cloudflare

This directory is the cloud-hosted version of the Amazon keyword manager.

## Architecture

- Cloudflare Worker + Static Assets: UI and API
- Cloudflare D1: stores, products, SKU, ASIN, keywords, bids, negative keywords
- Cloudflare R2: private product images
- Cloudflare Access: required for API requests by default
- GitHub Actions: provisions D1/R2 if missing, applies migrations, and deploys

## Required GitHub Actions secrets

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

The API token needs permissions sufficient for Workers Scripts, D1 and R2 resource management/deployment.

## Security

`ACCESS_REQUIRED` defaults to `true`. API requests require the
`Cf-Access-Authenticated-User-Email` header injected by Cloudflare Access.

Do not set `ACCESS_REQUIRED=false` for production data.
