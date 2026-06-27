from src import auth


def test_hash_verify_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong password", h)


def test_hash_is_salted():
    # Same password hashed twice should differ (random salt).
    assert auth.hash_password("same-pw-here") != auth.hash_password("same-pw-here")


def test_verify_rejects_bad_format():
    assert not auth.verify_password("anything", "not-a-valid-hash")
    assert not auth.verify_password("anything", "")
