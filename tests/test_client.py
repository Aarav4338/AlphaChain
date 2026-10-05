"""
Unit tests for RoostooClient and RateLimiter.
Validates HMAC-SHA256 signature generation against Roostoo's official documentation specifications.
"""
import pytest
from client.roostoo_client import RoostooClient
from client.rate_limiter import RateLimiter


def test_roostoo_signature_spec_example():
    """
    Verifies that our cryptographic signature implementation matches
    the exact test vector specified in the official Roostoo Public API documentation.
    """
    test_api_key = "USEAPIKEYASMYID"
    test_secret_key = "S1XP1e3UZj6A7H5fATj0jNhqPxxdSJYdInClVN65XAbvqqMKjVHjA7PZj4W12oep"

    client = RoostooClient(api_key=test_api_key, secret_key=test_secret_key)

    # Payload from official Roostoo documentation
    payload = {
        "pair": "BNB/USD",
        "quantity": "2000",
        "side": "BUY",
        "type": "MARKET",
        "timestamp": "1580774512000",
    }

    # Override get_timestamp for deterministic testing
    client.get_timestamp = lambda: "1580774512000"

    headers, total_params = client._sign_payload(payload)

    expected_params = "pair=BNB/USD&quantity=2000&side=BUY&timestamp=1580774512000&type=MARKET"
    expected_signature = "20b7fd5550b67b3bf0c1684ed0f04885261db8fdabd38611e9e6af23c19b7fff"

    assert total_params == expected_params, f"Params mismatch: got {total_params}, expected {expected_params}"
    assert headers["RST-API-KEY"] == test_api_key
    assert headers["MSG-SIGNATURE"] == expected_signature, f"Signature mismatch: got {headers['MSG-SIGNATURE']}"


def test_rate_limiter_tokens():
    """Verifies that the token-bucket rate limiter properly dispenses tokens."""
    limiter = RateLimiter(max_calls=5, period_seconds=1.0)
    assert limiter.get_available_tokens() == pytest.approx(5.0, abs=0.1)

    # Consume 3 tokens
    assert limiter.acquire(tokens_needed=3.0, timeout=1.0) is True
    assert limiter.get_available_tokens() <= 2.1


def test_public_server_time():
    """Verifies live network reachability to Roostoo public serverTime endpoint."""
    client = RoostooClient()
    res = client.check_server_time()
    assert res is not None, "Failed to connect to Roostoo serverTime endpoint"
    assert "ServerTime" in res, f"Expected 'ServerTime' in response: {res}"
    assert isinstance(res["ServerTime"], int)
    assert res["ServerTime"] > 1700000000000  # Valid recent epoch millisecond timestamp
