# X Automation Service

![Product Siddha](https://img.shields.io/badge/Maintained%20by-Product%20Siddha-blue)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)

An open-source, resilient FastAPI service that allows you to post tweets to X (Twitter) without using the expensive official API. Instead, it interacts directly with X's internal GraphQL API using browser-grade TLS fingerprinting (`curl_cffi`) and dynamic session management.

> 🏢 **Brought to you by [Product Siddha](https://productsiddha.com)**  
> We built this internal tool and decided to open-source it to help the automation community. While we try to give you as much guidance as possible here in the repo, running browser fingerprinting APIs can be technically challenging.  
> **Love the idea but don't want to manage the code?** [Hire our agency](https://productsiddha.com) to replicate, host, or customize this workflow for your business.

---

## 🛑 The Problem: Why Does This Exist?

In early 2023, X (Twitter) severely restricted its API access, eliminating the standard free tier that developers used for simple automation and bot posting. Today, the "Basic" official API tier costs a staggering **$100 per month**—which is prohibitively expensive if you just want to post automated tweets, schedule content, or integrate a simple n8n/Make webhook.

We needed a way to automate our agency's tweets *without* paying $1,200 a year for the privilege. 

This repository solves that by mapping a Python backend directly to X's internal Web API (the exact same API your browser uses when you click "Tweet" on x.com). By spoofing a real browser's identity and using your active session cookies, this service completely bypasses the official developer API paywalls.

---

## 🌟 Features

- **No Official API Required:** Runs entirely on session cookies (`auth_token` and `ct0`).
- **Browser Fingerprinting:** Uses `curl_cffi` to mimic real Chrome (Chrome 136+) TLS patterns to bypass JA3/JA4 checks.
- **Dynamic Session Extraction:** Auto-scrapes X's JavaScript bundles on startup to find the latest GraphQL `queryId` and `featureSwitches`.
- **Advanced Header Management:** Dynamically generates `x-client-transaction-id` and maintains a stable `x-client-uuid` per session.
- **Actionable Error Handling:** Cleans up ambiguous X API errors into readable flags (`AUTH_EXPIRED`, `RATE_LIMIT`, `DUPLICATE_TWEET`, `AUTOMATION_DETECTED`).
- **n8n / Make Friendly:** Perfect for triggering from any workflow automation tool via a simple POST request.

---

## 🚀 Setup & Installation

### 1. Prerequisites
- Python 3.11+
- Residential Proxy (Datacenter IPs from Render, AWS, GCP, etc. are typically blocked by X)

### 2. Clone and Install
```bash
git clone https://github.com/elnino-hub/x-automation.git
cd x-automation
pip install -r requirements.txt
```

### 3. Environment Variables
Copy `.env.example` to a new `.env` file:
```bash
cp .env.example .env
```
Fill in the variables:

| Variable | Purpose |
|---|---|
| `X_AUTH_TOKEN` | `auth_token` cookie from a logged-in X browser session |
| `X_CT0` | `ct0` cookie from a logged-in X browser session |
| `API_KEY` | Secret key sent in the `x-api-key` header to authenticate requests securely |
| `PROXY_URL` | (Required in cloud) Residential proxy URL — format: `http://user:pass@host:port` |

**How to get your X Cookies:**
1. Log in to [x.com](https://x.com) in your browser.
2. Open DevTools (F12) → Application → Cookies → `https://x.com`.
3. Copy the values for `auth_token` and `ct0`.
*(Note: Cookies generally last ~12 months before needing rotation).*

### 4. Run the Service
```bash
uvicorn execution.main:app --host 0.0.0.0 --port 8000
```

---

## 📡 Endpoints

All mutating endpoints require your `API_KEY` to be passed in the `x-api-key` header.

### `POST /tweet`
Post a tweet to the authenticated account.
**Request:**
```bash
curl -X POST http://localhost:8000/tweet \
  -H "x-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello world from the unofficial API!"}'
```
**Response:**
```json
{
  "success": true,
  "tweet_id": "184719247192847120"
}
```

### `GET /health`
Returns the status of the dynamic GraphQL feature scraping. No authentication required. Useful for Keep-Alive pings.

### `GET /ip`
Returns the current outbound IP of the service. Highly recommended to verify your `PROXY_URL` is configured correctly.

### `GET /debug-tweet`
Fires a test tweet and returns the absolute raw response from X. Useful if something is breaking and you need to see exactly what X is returning.

---

## ☁️ Deployment

This service is container-ready and runs on any Python hosting provider (Render, Railway, Fly.io).

**Render Deployment (Recommended):**
1. Create a new "Web Service" pointing to your fork.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `uvicorn execution.main:app --host 0.0.0.0 --port $PORT`
4. Add all required secrets (`X_AUTH_TOKEN`, `X_CT0`, `API_KEY`, `PROXY_URL`) directly in the Render dashboard.

## 🤖 n8n Workflow Integration
To use this with n8n:
- **Node:** HTTP Request
- **URL:** `POST https://your-service-url.com/tweet`
- **Header:** `key: x-api-key`, `value: <YOUR_API_KEY>`
- **Body:** Send JSON with `{ "text": "Your tweet here" }`
- **Settings:** Set a timeout of `60 seconds` (to handle cold starts). Set retries to `2 attempts` spaced `5000ms` apart.

*(Pro-Tip: Set up a Cron trigger to hit `GET /health` every 14 minutes to prevent your cloud container from spinning down).*

---

## ⚠️ Limitations & Caveats
- **Rate Limits:** Keep it under ~50 tweets/day. Pushing this library too hard will result in X locking your account. 
- **Browser Impersonation Ages Out:** X constantly monitors TLS versions. If you suddenly get `AUTOMATION_DETECTED` errors, the hardcoded `BROWSER = "chrome136"` in `main.py` may need to be incremented to match the latest typical browser version supported by `curl_cffi`.
- **"Duplicate Tweet" Error During Retry:** If a request pauses during transmission and retries, X might accept the first and reject the second as a duplicate. The API handles this gracefully (`success: true, tweet_id: null`).

---

## 🤝 Need Help?

As mentioned, this toolkit is a little complex underneath the hood! If you're running a business and love this automation but:
- Don't know how to deploy it
- Keep getting flagged or proxy-banned
- Need custom functionality (Media uploads, DMs, thread scheduling)

Reach out to us. **[Product Siddha](https://productsiddha.com)** specializes in building robust, un-breakable automation infrastructure for growing businesses.

<div align="center">
  <br>
  <a href="https://productsiddha.com">
    <b>Consult with our Automations Team</b>
  </a>
</div>
