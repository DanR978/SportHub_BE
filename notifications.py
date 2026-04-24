"""
APNs push notification sender using token-based authentication.

Requires environment variables:
- APNS_TEAM_ID: 10-char Apple Developer team ID
- APNS_KEY_ID: 10-char key ID from the .p8 AuthKey
- APNS_AUTH_KEY_PATH: absolute path to the AuthKey_<KEYID>.p8 file
- APNS_BUNDLE_ID: iOS app bundle identifier (e.g. com.sportmap.app)
- APNS_USE_SANDBOX: "true" for dev builds, "false" for App Store (default: true)

If any required var is missing, send_push() logs a warning and no-ops. This
lets dev and test environments run without APNs credentials.

Invalid tokens returned by APNs (410 Gone, BadDeviceToken) are deleted from
the database so we stop sending to stale devices.
"""
import asyncio
import logging
import os
import time
from typing import Iterable

import httpx
import jwt as pyjwt
from sqlalchemy.orm import Session

from models.db_device_token import DBDeviceToken

logger = logging.getLogger(__name__)

_APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"
_APNS_PROD_HOST = "https://api.push.apple.com"

_APNS_JWT_LIFETIME_SECONDS = 45 * 60  # Apple rejects tokens older than ~1 hour
_token_cache = {"value": None, "expires_at": 0}


def _config() -> dict | None:
    team = os.getenv("APNS_TEAM_ID")
    key_id = os.getenv("APNS_KEY_ID")
    key_path = os.getenv("APNS_AUTH_KEY_PATH")
    bundle = os.getenv("APNS_BUNDLE_ID")
    if not (team and key_id and key_path and bundle):
        return None
    use_sandbox = os.getenv("APNS_USE_SANDBOX", "true").lower() == "true"
    return {
        "team_id": team,
        "key_id": key_id,
        "key_path": key_path,
        "bundle_id": bundle,
        "host": _APNS_SANDBOX_HOST if use_sandbox else _APNS_PROD_HOST,
    }


def _get_provider_token(cfg: dict) -> str:
    now = int(time.time())
    if _token_cache["value"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["value"]
    with open(cfg["key_path"], "rb") as f:
        private_key = f.read()
    token = pyjwt.encode(
        payload={"iss": cfg["team_id"], "iat": now},
        key=private_key,
        algorithm="ES256",
        headers={"kid": cfg["key_id"]},
    )
    _token_cache["value"] = token
    _token_cache["expires_at"] = now + _APNS_JWT_LIFETIME_SECONDS
    return token


async def _send_to_token(client: httpx.AsyncClient, cfg: dict, device_token: str, payload: dict) -> tuple[int, str]:
    provider_token = _get_provider_token(cfg)
    resp = await client.post(
        f"{cfg['host']}/3/device/{device_token}",
        headers={
            "authorization": f"bearer {provider_token}",
            "apns-topic": cfg["bundle_id"],
            "apns-push-type": "alert",
            "apns-priority": "10",
        },
        json=payload,
    )
    return resp.status_code, resp.text


async def _send_many(cfg: dict, tokens: Iterable[str], payload: dict) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
        for t in tokens:
            try:
                status, _body = await _send_to_token(client, cfg, t, payload)
                results.append((t, status))
            except Exception:
                logger.exception("APNs send failed for token")
                results.append((t, 0))
    return results


def send_push(db: Session, user_id, title: str, body: str, data: dict | None = None) -> None:
    """Send a push notification to all of user_id's registered devices.

    Runs asynchronously (fire-and-forget) so calling request handlers don't block.
    If APNs is not configured, this is a no-op with a debug log.
    """
    cfg = _config()
    if not cfg:
        logger.debug("APNs not configured; skipping push for user %s", user_id)
        return

    tokens = [row.token for row in db.query(DBDeviceToken).filter(DBDeviceToken.user_id == user_id).all()]
    if not tokens:
        return

    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
        },
    }
    if data:
        payload.update(data)

    async def run_and_cleanup():
        results = await _send_many(cfg, tokens, payload)
        # Delete tokens that APNs says are stale
        stale = [t for t, status in results if status in (400, 410)]
        if stale:
            db.query(DBDeviceToken).filter(DBDeviceToken.token.in_(stale)).delete(synchronize_session=False)
            db.commit()
            logger.info("Removed %d stale APNs tokens", len(stale))

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(run_and_cleanup())
    except RuntimeError:
        # Not in an async context — run synchronously. Only expected in scripts/cron.
        asyncio.run(run_and_cleanup())
