# Directive: Post Tweet via X Automation Service

## Goal
Expose HTTP endpoints that accept a tweet payload (including X session credentials) and post it to X (Twitter). Credentials are passed per-request — the service holds no secrets server-side.

## Inputs
- `POST /tweet` with JSON body:
  - `auth_token` (string, required): `auth_token` cookie from a logged-in X browser session
  - `ct0` (string, required): `ct0` cookie from a logged-in X browser session
  - `text` (string, required): Tweet content, max 280 chars
  - `mediaUrls` (list of strings, optional): Public image URLs to attach

- `POST /debug-tweet` with JSON body:
  - `auth_token` (string, required): `auth_token` cookie from a logged-in X browser session
  - `ct0` (string, required): `ct0` cookie from a logged-in X browser session

## Outputs
- `200 OK`: `{ "success": true, "tweet_id": "<id>" }`
- `200 OK` (error): `{ "success": false, "error": "<reason>" }` — errors are returned as 200 with `success: false` for caller convenience
- `422 Unprocessable Entity`: Missing or invalid required fields in request body

## Tools / Scripts
- `execution/main.py` — The FastAPI app. Run with: `uvicorn execution.main:app --host 0.0.0.0 --port 8000`

## Environment Variables (`.env`)
| Variable | Required | Purpose |
|---|---|---|
| `PROXY_URL` | No (but strongly recommended on cloud) | Residential proxy URL — format: `http://user:pass@host:port` |

**X session cookies (`auth_token` and `ct0`) are passed per-request in the JSON body — never stored as env vars.**

## How to Get Your X Cookies

1. Log in to [x.com](https://x.com) in Chrome or Firefox
2. Open DevTools → Application → Cookies → `https://x.com`
3. Copy the values for `auth_token` and `ct0`
4. Pass them directly in the JSON body of each request

Cookies last ~12 months. You'll get a clear `AUTH_EXPIRED` error when they expire — just re-export.

## Deployment

This service is designed to run on any platform that supports Python (Render, Railway, Fly.io, etc.).

**Render (recommended):**
1. Create a new Web Service pointing to this repo
2. Set runtime to Python 3.11, start command: `uvicorn execution.main:app --host 0.0.0.0 --port $PORT`
3. Add `PROXY_URL` in the Render dashboard if using a residential proxy
4. *(Optional)* To enable auto-deploy on push, add `RENDER_API_KEY` and `RENDER_SERVICE_ID` as GitHub Secrets — the included workflow handles the rest

> **Note:** Do NOT add a `render.yaml` to this repo if your service was created via the Render dashboard — it will conflict.

## Endpoints
| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/tweet` | `auth_token` + `ct0` in body | Post a tweet |
| `POST` | `/debug-tweet` | `auth_token` + `ct0` in body | Post a test tweet and return full raw X API response |
| `GET` | `/health` | none | Status: queryId source, features source, cache age. Reads from cache only — does NOT trigger a scrape. Safe for high-frequency keep-alive pings. |
| `GET` | `/ip` | none | Outbound IP (verify proxy is routing correctly) |

## Test Command
```bash
curl -X POST https://your-service-url/tweet \
  -H "Content-Type: application/json" \
  -d '{
    "auth_token": "YOUR_AUTH_TOKEN_COOKIE",
    "ct0": "YOUR_CT0_COOKIE",
    "text": "Hello from X Automation!"
  }'
```

## n8n Integration Example
- HTTP Request node → `POST https://your-service-url/tweet`
- Body (JSON):
  ```json
  {
    "auth_token": "{{ $env.X_AUTH_TOKEN }}",
    "ct0": "{{ $env.X_CT0 }}",
    "text": "{{ $json.tweet_text }}",
    "mediaUrls": []
  }
  ```
- Timeout: **60 seconds** (cold start protection on free-tier hosts)
- Retry: **2 attempts**, **5000ms** between
- Keep-alive: Schedule Trigger node every 14 min → `GET /health` to prevent cold starts

## How the Service Works

### Self-healing behaviors
1. **queryId rotation** — X's GraphQL `queryId` for CreateTweet changes periodically. The service scrapes X's JS bundles at startup, caches for 1 hour, and auto-refreshes on failure.
2. **Scrape retry backoff** — If a bundle scrape fails (e.g. network timeout on Render free tier), the service waits 60 seconds (`SCRAPE_RETRY_COOLDOWN`) before retrying. Prevents retry storms from hammering x.com on every request. Resets to immediate retry after a successful scrape.
3. **Feature flags** — `featureSwitches` scraped from JS bundles alongside the queryId. Falls back to hardcoded `FALLBACK_FEATURES` if scraping fails.
4. **x-client-transaction-id** — Generated per-request using the `xclienttransaction` library (parses X homepage animation data). Falls back gracefully if init fails.
5. **x-client-uuid** — Stable UUID4 generated at startup, mimics a persistent browser tab session.
6. **Error classification** — Actionable error labels returned: `AUTH_EXPIRED`, `RATE_LIMIT`, `DUPLICATE_TWEET`, `AUTOMATION_DETECTED`, `PROXY_ERROR`, `ACCOUNT_LOCKED`, etc.
7. **Fallback safety** — If bundle scraping fails, falls back to last-known-good queryId and features.
8. **Browser-version headers** — `sec-ch-ua` headers are NOT set manually; `curl_cffi` injects them consistent with its TLS fingerprint to prevent detection mismatches.

### What still requires manual action
- **Cookie expiry (~12 months)** — You'll get a clear `AUTH_EXPIRED` error. Re-export `auth_token` and `ct0` from browser and update your caller.
- **Account locked** — You'll get a clear `ACCOUNT_LOCKED` error. Log into X in browser to resolve.

## Key Technical Notes
- **Why curl_cffi?** X performs TLS fingerprint checking (JA3/JA4). Standard Python HTTP libraries (`httpx`, `requests`) produce non-browser TLS handshakes. `curl_cffi` with `impersonate="chrome136"` uses libcurl + BoringSSL to produce an authentic Chrome fingerprint.
- **Why a residential proxy?** Datacenter IPs (Render, AWS, GCP, etc.) are permanently flagged by X since early 2025. A residential proxy is required. DataImpulse and Smartproxy are tested and working.
- **Browser impersonation ages out** — When error 226 returns after a long working period, update the impersonation version (e.g., `chrome136` → `chrome146`). `curl_cffi 0.15.0` supports up to `chrome146`.
- **DUPLICATE_TWEET in a retry = success** — If X returns error 187 during a retry, the earlier attempt posted the tweet. The service correctly returns `success: true, tweet_id: null`.
- **Rate limits:** Keep under ~50 tweets/day. Error 344 = daily limit, resets within 24h.
- **X can return `errors` alongside a successful `tweet_results`** — The service always extracts `tweet_id` first. If `rest_id` is present, the tweet posted successfully.
- **Per-request credentials** — `auth_token` and `ct0` are passed in every request body, making the service fully stateless. A single deployment can serve multiple X accounts simultaneously.

## 📦 Open Source / Repository Context (For Claude / AI Agents)
- **Status:** This internal codebase was officially scrubbed, prepared, and pushed to the public GitHub repository (`elnino-hub/x-automation`) to act as an open-source lead-generation template for Product Siddha.
- **Documentation:** A comprehensive `README.md` was generated, explaining the business problem (X's $100/mo API paywall).
- **Security:** Dead code was removed using `ruff` and `vulture`, and git history was validated to contain no leaked `.env` secrets.
- **Credential model:** `auth_token` and `ct0` are passed per-request (not stored server-side). `PROXY_URL` is the only server-level env var. This design lets one deployment serve multiple X accounts and avoids credentials ever living in env vars or config files.
- **Future Changes:** When creating future scripts or modifying this directive, remember this code is now publicly visible. Changes must uphold strict security (no hardcoded payloads) and open-source readability standards. Ensure `README.md` is updated synchronously if this directive's underlying logic changes.
