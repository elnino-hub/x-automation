# Directive: Post Tweet via X Automation Service

## Goal
Expose a single HTTP endpoint that accepts a tweet payload and posts it to X (Twitter).

## Inputs
- `POST /tweet` with JSON body:
  - `text` (string, required): Tweet content, max 280 chars
  - `mediaUrls` (list of strings, optional): Public image URLs to attach

## Outputs
- `200 OK`: `{ "success": true, "tweet_id": "<id>" }`
- `401 Unauthorized`: Missing or wrong API key
- `500 Internal Server Error`: `{ "success": false, "error": "<reason>" }`

## Tools / Scripts
- `execution/main.py` — The FastAPI app. Run with: `uvicorn execution.main:app --host 0.0.0.0 --port 8000`

## Environment Variables (`.env`)
| Variable | Purpose |
|---|---|
| `X_AUTH_TOKEN` | `auth_token` cookie from a logged-in X browser session |
| `X_CT0` | `ct0` cookie from a logged-in X browser session |
| `API_KEY` | Secret key sent in the `x-api-key` header to authenticate requests |
| `PROXY_URL` | (Optional) Residential proxy URL — format: `http://user:pass@host:port` |

**All secrets live in `.env` or your hosting provider's environment variables. NEVER commit credentials to the repo.**

## How to Get Your X Cookies

1. Log in to [x.com](https://x.com) in Chrome or Firefox
2. Open DevTools → Application → Cookies → `https://x.com`
3. Copy the values for `auth_token` and `ct0`
4. Set them as `X_AUTH_TOKEN` and `X_CT0` in your `.env`

Cookies last ~12 months. You'll get a clear `AUTH_EXPIRED` error when they expire — just re-export.

## Deployment

This service is designed to run on any platform that supports Python (Render, Railway, Fly.io, etc.).

**Render (recommended):**
1. Create a new Web Service pointing to this repo
2. Set runtime to Python 3.11, start command: `uvicorn execution.main:app --host 0.0.0.0 --port $PORT`
3. Add all 4 env vars in the Render dashboard (never in the repo)
4. *(Optional)* To enable auto-deploy on push, add `RENDER_API_KEY` and `RENDER_SERVICE_ID` as GitHub Secrets — the included workflow handles the rest

> **Note:** Do NOT add a `render.yaml` to this repo if your service was created via the Render dashboard — it will conflict.

## Endpoints
| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/tweet` | `x-api-key` header | Post a tweet |
| `GET` | `/health` | none | Status: queryId source, features source, cache age |
| `GET` | `/ip` | none | Outbound IP (verify proxy is routing correctly) |
| `GET` | `/debug-tweet` | `x-api-key` header | Post a test tweet and return full raw X API response |

## Test Command
```bash
curl -X POST https://your-service-url/tweet \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from X Automation!"}'
```

## n8n Integration Example
- HTTP Request node → `POST https://your-service-url/tweet`
- Header: `x-api-key: <your API_KEY>`
- Body (JSON): `{ "text": "{{$json.tweet_text}}", "mediaUrls": [] }`
- Timeout: **60 seconds** (cold start protection on free-tier hosts)
- Retry: **2 attempts**, **5000ms** between
- Keep-alive: Schedule Trigger node every 14 min → `GET /health` to prevent cold starts

## How the Service Works

### Self-healing behaviors
1. **queryId rotation** — X's GraphQL `queryId` for CreateTweet changes periodically. The service scrapes X's JS bundles at startup, caches for 1 hour, and auto-refreshes on failure.
2. **Feature flags** — `featureSwitches` scraped from JS bundles alongside the queryId. Falls back to hardcoded `FALLBACK_FEATURES` if scraping fails.
3. **x-client-transaction-id** — Generated per-request using the `xclienttransaction` library (parses X homepage animation data). Falls back gracefully if init fails.
4. **x-client-uuid** — Stable UUID4 generated at startup, mimics a persistent browser tab session.
5. **Error classification** — Actionable error labels returned: `AUTH_EXPIRED`, `RATE_LIMIT`, `DUPLICATE_TWEET`, `AUTOMATION_DETECTED`, `PROXY_ERROR`, `ACCOUNT_LOCKED`, etc.
6. **Fallback safety** — If bundle scraping fails, falls back to last-known-good queryId and features.
7. **Browser-version headers** — `sec-ch-ua` headers are NOT set manually; `curl_cffi` injects them consistent with its TLS fingerprint to prevent detection mismatches.

### What still requires manual action
- **Cookie expiry (~12 months)** — You'll get a clear `AUTH_EXPIRED` error. Re-export `auth_token` and `ct0` from browser.
- **Account locked** — You'll get a clear `ACCOUNT_LOCKED` error. Log into X in browser to resolve.

## Key Technical Notes
- **Why curl_cffi?** X performs TLS fingerprint checking (JA3/JA4). Standard Python HTTP libraries (`httpx`, `requests`) produce non-browser TLS handshakes. `curl_cffi` with `impersonate="chrome136"` uses libcurl + BoringSSL to produce an authentic Chrome fingerprint.
- **Why a residential proxy?** Datacenter IPs (Render, AWS, GCP, etc.) are permanently flagged by X since early 2025. A residential proxy is required. DataImpulse and Smartproxy are tested and working.
- **Browser impersonation ages out** — When error 226 returns after a long working period, update the impersonation version (e.g., `chrome136` → `chrome146`). `curl_cffi 0.15.0` supports up to `chrome146`.
- **DUPLICATE_TWEET in a retry = success** — If X returns error 187 during a retry, the earlier attempt posted the tweet. The service correctly returns `success: true, tweet_id: null`.
- **Rate limits:** Keep under ~50 tweets/day. Error 344 = daily limit, resets within 24h.
- **X can return `errors` alongside a successful `tweet_results`** — The service always extracts `tweet_id` first. If `rest_id` is present, the tweet posted successfully.
