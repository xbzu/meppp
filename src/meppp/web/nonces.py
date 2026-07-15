from __future__ import annotations

import secrets

SESSION_KEY = "_meppp_form_nonces"
MAX_NONCES_PER_PURPOSE = 8


def issue_nonce(request, *, purpose: str) -> str:
    nonces = request.session.get(SESSION_KEY, {})
    purpose_nonces = list(nonces.get(purpose, []))
    token = secrets.token_urlsafe(24)
    purpose_nonces.append(token)
    nonces[purpose] = purpose_nonces[-MAX_NONCES_PER_PURPOSE:]
    request.session[SESSION_KEY] = nonces
    return token


def nonce_is_issued(request, *, purpose: str, token: str) -> bool:
    nonces = request.session.get(SESSION_KEY, {})
    return any(secrets.compare_digest(candidate, token) for candidate in nonces.get(purpose, []))


def consume_nonce(request, *, purpose: str, token: str) -> bool:
    nonces = request.session.get(SESSION_KEY, {})
    purpose_nonces = list(nonces.get(purpose, []))
    if token not in purpose_nonces:
        return False
    purpose_nonces.remove(token)
    if purpose_nonces:
        nonces[purpose] = purpose_nonces
    else:
        nonces.pop(purpose, None)
    request.session[SESSION_KEY] = nonces
    return True
