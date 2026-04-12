"""
X Automation Service
====================
A FastAPI service that posts tweets to X (Twitter) via the internal GraphQL API,
using curl_cffi for browser-grade TLS fingerprinting.

Setup:
    1. Copy .env.example to .env and fill in your values.
    2. pip install -r requirements.txt
    3. uvicorn execution.main:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /tweet          — Post a tweet (requires x-api-key header)
    GET  /health         — Service status and cache diagnostics
    GET  /ip             — Outbound IP (verify proxy routing)
    GET  /debug-tweet    — Post a test tweet and return raw X response
"""

import datetime
import json
import logging
import os
import re
import time
import uuid

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from fastapi import FastAPI, HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from dotenv import load_dotenv
from x_client_transaction import ClientTransaction
from x_client_transaction.constants import (
    ON_DEMAND_FILE_REGEX,
    ON_DEMAND_HASH_PATTERN,
    ON_DEMAND_FILE_URL,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("x-automation")

AUTH_TOKEN = os.environ["X_AUTH_TOKEN"]
CT0 = os.environ["X_CT0"]
API_KEY = os.environ["API_KEY"]
PROXY_URL = os.environ.get("PROXY_URL")

BROWSER = "chrome136"

# Stable session UUID — mimics a single browser tab
CLIENT_UUID = str(uuid.uuid4())

# Public bearer token — same for every X web client
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# ── Dynamic GraphQL config ──────────────────────────────────────────
# Scraped at runtime from X's JS bundles. Fallback to last-known-good.
FALLBACK_QUERY_ID = "S1qcGUn68_U0lDKdMlYSGg"
FALLBACK_FEATURES = {
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "articles_preview_enabled": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "longform_notetweets_rich_text_read_enabled": True,
    "post_ctas_fetch_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_profile_redirect_enabled": False,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "rweb_tipjar_consumption_enabled": False,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "verified_phone_label_enabled": True,
    "view_counts_everywhere_api_enabled": True,
}

# In-memory cache
_gql_cache: dict = {}
_features_cache: dict = {}
_transaction_ctx: ClientTransaction | None = None
_cache_ts: float = 0
_last_scrape_attempt: float = 0
CACHE_TTL = 3600  # 1 hour
SCRAPE_RETRY_COOLDOWN = 60  # seconds to wait after a failed scrape before retrying


async def _scrape_gql_config() -> dict[str, str]:
    """Fetch current GraphQL queryIds, features, and transaction context from X's JS bundles."""
    global _gql_cache, _features_cache, _transaction_ctx, _cache_ts, _last_scrape_attempt

    if _gql_cache and (time.time() - _cache_ts) < CACHE_TTL:
        return _gql_cache

    # If the last scrape attempt failed recently, don't hammer the network
    now = time.time()
    if _last_scrape_attempt and (now - _last_scrape_attempt) < SCRAPE_RETRY_COOLDOWN:
        return _gql_cache  # return empty dict; caller will use fallback

    _last_scrape_attempt = now
    log.info("Scraping X JS bundles for fresh queryIds + features...")
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if PROXY_URL else None

    try:
        async with AsyncSession(impersonate=BROWSER, proxies=proxies) as s:
            # Fetch a public X page to find script URLs
            resp = await s.get("https://x.com/x", timeout=15)
            html = resp.text

            # Handle JS redirect
            if ">document.location =" in html:
                url = html.split('document.location = "')[1].split('"')[0]
                resp = await s.get(url, timeout=15)
                html = resp.text

            # ── Build ClientTransaction for x-client-transaction-id ──
            try:
                soup = BeautifulSoup(html, "html.parser")

                # Find the ondemand.s JS file hash from the main page
                ondemand_match = ON_DEMAND_FILE_REGEX.search(html)
                if ondemand_match:
                    chunk_id = ondemand_match.group(1)
                    hash_pattern = ON_DEMAND_HASH_PATTERN.format(chunk_id)
                    hash_match = re.search(hash_pattern, html)
                    if hash_match:
                        ondemand_url = ON_DEMAND_FILE_URL.format(
                            filename=hash_match.group(1)
                        )
                        od_resp = await s.get(ondemand_url, timeout=15)
                        if od_resp.status_code == 200:
                            _transaction_ctx = ClientTransaction(soup, od_resp.text)
                            log.info("ClientTransaction initialized successfully")
                        else:
                            log.warning(
                                f"ondemand.s fetch returned {od_resp.status_code}"
                            )
                    else:
                        log.warning("Could not find ondemand hash in page source")
                else:
                    log.warning("Could not find ondemand chunk ID in page source")
            except Exception as exc:
                log.warning(f"ClientTransaction init failed: {exc}")

            # ── Extract queryIds and features from JS bundles ──
            script_urls = re.findall(
                r'src="(https://abs\.twimg\.com/responsive-web/client-web[^"]+\.js)"',
                html,
            )
            log.info(f"Found {len(script_urls)} JS bundles to scan")

            ops = {}
            ops_features = {}
            for url in script_urls:
                try:
                    js_resp = await s.get(url, timeout=15)
                    if js_resp.status_code != 200:
                        continue
                    js_text = js_resp.text

                    # Extract queryId + operationName pairs
                    pairs = re.findall(
                        r'queryId:"([^"]+)".+?operationName:"([^"]+)"',
                        js_text,
                    )
                    for qid, op_name in pairs:
                        ops[op_name] = qid

                    # Extract featureSwitches for CreateTweet
                    # Bundle format: queryId:"...",operationName:"CreateTweet",...,metadata:{featureSwitches:[...],fieldToggles:[...]}
                    ct_features_match = re.search(
                        r'operationName:"CreateTweet".*?featureSwitches:\[([^\]]+)\]',
                        js_text,
                    )
                    if ct_features_match:
                        raw = ct_features_match.group(1)
                        feature_names = re.findall(r'"([^"]+)"', raw)
                        if feature_names:
                            # featureSwitches are sent as true by the real client
                            ops_features = {name: True for name in feature_names}
                            log.info(
                                f"Scraped {len(ops_features)} feature flags for CreateTweet"
                            )

                except Exception:
                    continue

            if ops:
                _gql_cache = ops
                _cache_ts = time.time()
                _last_scrape_attempt = 0  # reset so next cache expiry retries immediately
                ct_id = ops.get("CreateTweet", "NOT FOUND")
                log.info(f"Scraped {len(ops)} operations. CreateTweet={ct_id}")
            else:
                log.warning(
                    "Bundle scrape returned 0 operations, keeping cache/fallback"
                )

            if ops_features:
                _features_cache = ops_features
            elif not _features_cache:
                log.warning("No features scraped, will use FALLBACK_FEATURES")

    except Exception as exc:
        log.warning(f"Bundle scrape failed: {exc}, using fallback")

    return _gql_cache


async def _get_create_tweet_id(force_refresh: bool = False) -> str:
    """Return the current CreateTweet queryId."""
    global _gql_cache, _cache_ts

    if force_refresh:
        _cache_ts = 0  # bust cache

    ops = await _scrape_gql_config()
    qid = ops.get("CreateTweet")
    if qid:
        return qid

    log.warning("CreateTweet not found in scraped ops, using fallback")
    return FALLBACK_QUERY_ID


def _get_features() -> dict:
    """Return the best available features dict."""
    return _features_cache if _features_cache else FALLBACK_FEATURES


# ── App setup ───────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
app = FastAPI(title="X Automation Service")


@app.on_event("startup")
async def startup():
    """Pre-warm the queryId cache on deploy so first tweet is fast."""
    await _scrape_gql_config()


def verify_api_key(key: str = Security(api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key


def _build_headers(method: str = "POST", path: str = "") -> dict:
    headers = {
        "authorization": f"Bearer {BEARER}",
        "cookie": f"auth_token={AUTH_TOKEN}; ct0={CT0}",
        "x-csrf-token": CT0,
        "content-type": "application/json",
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "x-client-uuid": CLIENT_UUID,
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://x.com",
        "referer": "https://x.com/compose/post",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    # Generate x-client-transaction-id if we have the context
    if _transaction_ctx and method and path:
        try:
            tid = _transaction_ctx.generate_transaction_id(method=method, path=path)
            headers["x-client-transaction-id"] = tid
        except Exception as exc:
            log.warning(f"Transaction ID generation failed: {exc}")

    return headers


def _build_tweet_payload(
    text: str, query_id: str, media_ids: list[str] | None = None
) -> dict:
    media_entities = []
    if media_ids:
        media_entities = [{"media_id": mid, "tagged_users": []} for mid in media_ids]

    return {
        "variables": {
            "tweet_text": text,
            "dark_request": False,
            "media": {
                "media_entities": media_entities,
                "possibly_sensitive": False,
            },
            "semantic_annotation_ids": [],
        },
        "features": _get_features(),
        "queryId": query_id,
    }


# ── Error classification ────────────────────────────────────────────
def _classify_error(data: dict, status_code: int) -> str:
    """Turn raw X response into an actionable error message."""
    if status_code == 401 or status_code == 403:
        return "AUTH_EXPIRED: Cookies (auth_token/ct0) are invalid or expired. Re-export from browser."

    errors = data.get("errors", [])
    if not errors:
        return ""

    code = errors[0].get("code") or errors[0].get("extensions", {}).get("code")
    msg = errors[0].get("message", "")

    error_map = {
        32: "AUTH_EXPIRED: Could not authenticate. Re-export cookies from browser.",
        36: "ACCOUNT_SUSPENDED: This account is suspended.",
        64: "ACCOUNT_SUSPENDED: This account is suspended.",
        89: "AUTH_EXPIRED: Invalid or expired token. Re-export cookies from browser.",
        130: "RATE_LIMIT: X is over capacity. Wait and retry.",
        131: "INTERNAL_ERROR: X internal error. Wait and retry.",
        187: "DUPLICATE_TWEET: This exact text was already posted recently. Make it unique.",
        226: "AUTOMATION_DETECTED: Request flagged as automated. Check TLS fingerprint / proxy.",
        261: "APP_SUSPENDED: Application write access suspended.",
        326: "ACCOUNT_LOCKED: Account temporarily locked. Log in via browser to unlock.",
        344: "RATE_LIMIT: Daily tweet limit reached. Wait 24h.",
    }

    return error_map.get(code, f"X_ERROR_{code}: {msg}")


class TweetRequest(BaseModel):
    text: str
    mediaUrls: list[str] = []


class TweetResponse(BaseModel):
    success: bool
    tweet_id: str | None = None
    error: str | None = None


async def _attempt_tweet(text: str, query_id: str) -> dict:
    """Fire a single CreateTweet request. Returns raw parsed JSON."""
    path = f"/i/api/graphql/{query_id}/CreateTweet"
    url = f"https://x.com{path}"
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if PROXY_URL else None
    async with AsyncSession(impersonate=BROWSER, proxies=proxies) as session:
        body = _build_tweet_payload(text, query_id)
        resp = await session.post(
            url, headers=_build_headers(method="POST", path=path), json=body, timeout=30
        )
    return {"status_code": resp.status_code, "data": resp.json()}


def _extract_tweet_id(data: dict) -> str | None:
    """Pull tweet_id from the nested response, tolerating shape variations."""
    try:
        tweet_results = data["data"]["create_tweet"]["tweet_results"]
        result = tweet_results.get("result") or tweet_results.get("tweet", {})
        return result.get("rest_id") or result.get("tweet", {}).get("rest_id")
    except (KeyError, TypeError, AttributeError):
        return None


@app.post("/tweet", response_model=TweetResponse)
async def post_tweet(payload: TweetRequest, _: str = Depends(verify_api_key)):
    try:
        # Attempt 1: use cached queryId
        query_id = await _get_create_tweet_id()
        result = await _attempt_tweet(payload.text, query_id)
        data = result["data"]
        status = result["status_code"]

        # Non-200 → classify and return
        if status != 200:
            err = _classify_error(data, status) or f"X API {status}: {json.dumps(data)}"
            return TweetResponse(success=False, error=err)

        # Try to extract tweet_id first — X may include both errors AND a valid result
        tweet_id = _extract_tweet_id(data)
        if tweet_id:
            return TweetResponse(success=True, tweet_id=tweet_id)

        # No tweet_id found — check for explicit errors
        if "errors" in data:
            err = _classify_error(data, status)
            if err.startswith("DUPLICATE_TWEET"):
                return TweetResponse(success=True, tweet_id=None)
            return TweetResponse(success=False, error=err)

        # Empty tweet_results → likely stale queryId. Force-refresh and retry once.
        log.warning("Empty tweet_results. Refreshing queryId and retrying...")
        new_query_id = await _get_create_tweet_id(force_refresh=True)

        log.info(f"queryId: {query_id} → {new_query_id}. Retrying...")
        result = await _attempt_tweet(payload.text, new_query_id)
        data = result["data"]
        status = result["status_code"]

        if status != 200:
            err = _classify_error(data, status) or f"X API {status}: {json.dumps(data)}"
            return TweetResponse(success=False, error=err)

        tweet_id = _extract_tweet_id(data)
        if tweet_id:
            return TweetResponse(success=True, tweet_id=tweet_id)

        if "errors" in data:
            err = _classify_error(data, status)
            if err.startswith("DUPLICATE_TWEET"):
                return TweetResponse(success=True, tweet_id=None)
            return TweetResponse(success=False, error=err)

        # Still empty after retry
        return TweetResponse(
            success=False,
            error=f"EMPTY_RESULT: Tweet may have been silently rejected (rate-limit, duplicate, or account restriction). Response: {json.dumps(data)}",
        )

    except Exception as exc:
        error_str = str(exc)
        if "proxy" in error_str.lower() or "connect" in error_str.lower():
            return TweetResponse(success=False, error=f"PROXY_ERROR: {error_str}")
        return TweetResponse(success=False, error=error_str)


@app.get("/health")
async def health():
    """Health check — reports current cache state without triggering a scrape."""
    features = _get_features()
    query_id = _gql_cache.get("CreateTweet", FALLBACK_QUERY_ID)
    return {
        "status": "ok",
        "create_tweet_query_id": query_id,
        "query_id_source": "scraped" if _gql_cache else "fallback",
        "features_source": "scraped" if _features_cache else "fallback",
        "features_count": len(features),
        "transaction_ctx": "active" if _transaction_ctx else "unavailable",
        "cache_age_seconds": int(time.time() - _cache_ts) if _cache_ts else None,
    }


@app.get("/ip")
async def check_ip():
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if PROXY_URL else None
    async with AsyncSession(impersonate=BROWSER, proxies=proxies) as session:
        resp = await session.get("https://api.ipify.org?format=json", timeout=10)
        return {"proxy_configured": bool(PROXY_URL), "ip": resp.json()}


@app.get("/debug-tweet")
async def debug_tweet(_: str = Depends(verify_api_key)):
    """Fire a test tweet and return the full raw X response for debugging."""
    text = f"debug {datetime.datetime.utcnow().isoformat()}"
    query_id = await _get_create_tweet_id()
    path = f"/i/api/graphql/{query_id}/CreateTweet"
    url = f"https://x.com{path}"
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if PROXY_URL else None
    async with AsyncSession(impersonate=BROWSER, proxies=proxies) as session:
        body = _build_tweet_payload(text, query_id)
        resp = await session.post(
            url, headers=_build_headers(method="POST", path=path), json=body, timeout=30
        )
    return {
        "status_code": resp.status_code,
        "response_headers": dict(resp.headers),
        "response_body": resp.json(),
        "query_id_used": query_id,
        "features_source": "scraped" if _features_cache else "fallback",
        "transaction_id_active": _transaction_ctx is not None,
        "tweet_text": text,
    }
