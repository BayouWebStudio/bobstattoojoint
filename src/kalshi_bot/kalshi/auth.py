"""RSA-PSS request signing for the Kalshi API.

Kalshi authenticates each request with three headers:

    KALSHI-ACCESS-KEY        the API key ID (UUID)
    KALSHI-ACCESS-TIMESTAMP  current time in milliseconds
    KALSHI-ACCESS-SIGNATURE  base64(RSA-PSS-SHA256(timestamp + method + path))

The signed message concatenates the millisecond timestamp, the uppercase HTTP
method, and the request path (the part after the host, *without* the query
string), e.g. ``"1700000000000" + "GET" + "/trade-api/v2/markets"``.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


@lru_cache(maxsize=4)
def load_private_key(path: str) -> rsa.RSAPrivateKey:
    """Load and cache an RSA private key from a PEM file."""
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Kalshi API key must be an RSA private key")
    return key


def sign(private_key: rsa.RSAPrivateKey, timestamp_ms: int, method: str, path: str) -> str:
    """Return the base64-encoded RSA-PSS signature for one request."""
    message = f"{timestamp_ms}{method.upper()}{path}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


def auth_headers(
    api_key_id: str,
    private_key: rsa.RSAPrivateKey,
    timestamp_ms: int,
    method: str,
    path: str,
) -> dict[str, str]:
    """Build the full set of Kalshi auth headers for a request."""
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        "KALSHI-ACCESS-SIGNATURE": sign(private_key, timestamp_ms, method, path),
    }
