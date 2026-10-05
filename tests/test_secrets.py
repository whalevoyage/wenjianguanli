from backend import secrets

def test_secret_round_trip_on_current_platform():
    value = 'test-api-key-123'
    encoded = secrets.protect(value)
    assert secrets.unprotect(encoded) == value
