"""Broadcastify JWT minting.

The API does not take the raw API key as a bearer token. It takes a short-lived
HS256 JWT whose header carries the API Key ID (kid), whose payload carries the
application ID (iss), and which is signed with the API key as the shared secret.
See bcfy.io/dev/docs/api/start/.

Hand-rolled with hmac/hashlib so the project keeps its "no extra dependency"
posture; PyJWT would add a dependency for about fifteen lines of work.
"""
import base64, hashlib, hmac, json, time


def _b64(raw: bytes) -> str:
    """base64url with trailing '=' stripped, as the spec requires."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _seg(obj) -> str:
    # Compact separators matter: the signature covers these exact bytes.
    return _b64(json.dumps(obj, separators=(",", ":")).encode())


def mint(api_key, key_id, app_id, ttl=3600, now=None, user=None):
    """Return a signed JWT string.

    ttl is capped at 24h by the API; the docs recommend <= 3600s and minting a
    fresh token per run rather than reusing a long-lived one.
    user, when given, is (user_id:int, user_token:str) for methods that need an
    authenticated Broadcastify user (premium archives, feed-owner methods).
    """
    if not (api_key and key_id and app_id):
        raise ValueError("bcfy JWT needs api_key, key_id and app_id")
    iat = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT", "kid": str(key_id)}
    payload = {"iss": str(app_id), "iat": iat, "exp": iat + int(ttl)}
    if user:
        payload["sub"] = int(user[0])       # must be a number, not a string
        payload["utk"] = str(user[1])
    signing_input = f"{_seg(header)}.{_seg(payload)}".encode()
    sig = hmac.new(api_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode()}.{_b64(sig)}"
