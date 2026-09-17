from app.auth.guest import issue_guest_token, verify_guest_token


def test_guest_token_is_signed(monkeypatch):
    monkeypatch.setenv('GUEST_SESSION_SECRET', 'test-secret-that-is-longer-than-thirty-two-bytes')
    token, uid = issue_guest_token()
    assert verify_guest_token(token) == uid
    assert verify_guest_token(token + 'x') is None
