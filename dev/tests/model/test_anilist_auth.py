"""Exercise the real loopback callback and mock only AniList's HTTPS exchange."""
import http.client
import queue
import threading
import time
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from model import anilist_auth as auth


@pytest.fixture
def login():
    attempts = []
    def create(exchange=None):
        attempt = auth.OAuthAttempt('55046', 'local-test-client-secret', port=0,
                                    exchange=exchange or (lambda *args: ('test-access-token', 12345)))
        attempts.append(attempt)
        return attempt
    yield create
    for attempt in attempts:
        attempt.close()


def callback(attempt, params, host=None):
    connection = http.client.HTTPConnection('127.0.0.1', attempt.server.server_port, timeout=3)
    try:
        connection.request('GET', '/anilist/callback?' + urlencode(params), headers={'Host': host or attempt.host})
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def test_authorization_uses_code_and_never_exposes_secret_in_url(login):
    attempt = login()
    values = parse_qs(urlsplit(attempt.url).query)
    assert values == dict(client_id=['55046'], response_type=['code'],
                          redirect_uri=[attempt.redirect_uri], state=[attempt.state])
    assert 'secret' not in attempt.url


def test_real_callback_exchanges_code_after_state_and_host_validation(login):
    calls = []
    def exchange(*args):
        calls.append(args)
        return 'test-access-token', 12345
    attempt = login(exchange)
    assert callback(attempt, dict(code='test-code', state='wrong'))[0] == 400
    assert callback(attempt, dict(code='test-code', state=attempt.state), host='attacker.test')[0] == 404
    assert callback(attempt, [('code', 'test-code'), ('state', attempt.state), ('state', attempt.state)])[0] == 400
    assert not calls
    status, body = callback(attempt, dict(code='test-code', state=attempt.state))
    assert status == 200 and 'history.replaceState' in body
    assert attempt.results.get(timeout=1) == ('test-access-token', 12345)
    assert calls == [('55046', 'local-test-client-secret', 'test-code', attempt.redirect_uri)]
    assert 'test-access-token' not in body and 'test-code' not in body
    attempt.thread.join(1)
    assert not attempt.thread.is_alive() and attempt._client_secret is None


@pytest.mark.parametrize('params', [
    [('code', 'a'), ('code', 'b')], [('x' + str(i), 'a') for i in range(25)],
    [('code', 'a' * 8193)], [('code', '')], [('code', 'a'), ('state', '日本語')],
])
def test_malformed_callbacks_cannot_exchange_a_code(login, params):
    calls = []
    attempt = login(lambda *args: calls.append(args))
    if not any(key == 'state' for key, _ in params):
        params = params + [('state', attempt.state)]
    assert callback(attempt, params)[0] == 400
    assert not calls and attempt.results.empty()


def test_cancelled_authorization_reports_failure_without_token_exchange(login):
    calls = []
    attempt = login(lambda *args: calls.append(args))
    assert callback(attempt, dict(error='access_denied', state=attempt.state))[0] == 200
    assert not calls and attempt.results.empty()
    assert 'abgebrochen' in attempt.errors.get(timeout=1)


def test_exchange_posts_the_documented_grant_and_does_not_follow_redirects(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return NS(status_code=200, json=lambda: dict(access_token='test-access-token', expires_in=3600, token_type='Bearer'))
    monkeypatch.setattr(auth.requests, 'post', post)
    before = time.time()
    token, expires = auth.exchange_authorization_code('55046', 'client-secret', 'code', auth.REDIRECT_URI)
    assert token == 'test-access-token' and before + 3600 <= expires <= time.time() + 3600
    url, options = calls[0]
    assert url == auth.TOKEN_URL and options['allow_redirects'] is False
    assert options['json'] == dict(grant_type='authorization_code', client_id='55046',
                                   client_secret='client-secret', code='code', redirect_uri=auth.REDIRECT_URI)


@pytest.mark.parametrize('response', [
    NS(status_code=400, json=lambda: {'error': 'invalid_grant', 'secret': 'do-not-display'}),
    NS(status_code=200, json=lambda: {'access_token': 'short', 'expires_in': 3600}),
    NS(status_code=200, json=lambda: {'access_token': 'test-access-token', 'expires_in': -1}),
    NS(status_code=200, json=lambda: []),
])
def test_exchange_failures_do_not_expose_server_payloads(monkeypatch, response):
    monkeypatch.setattr(auth.requests, 'post', lambda *args, **kwargs: response)
    with pytest.raises(ValueError) as raised:
        auth.exchange_authorization_code('55046', 'client-secret', 'code', auth.REDIRECT_URI)
    assert 'do-not-display' not in str(raised.value) and 'client-secret' not in str(raised.value)


def test_client_secrets_use_separate_credentials_for_each_public_client():
    saved = {}
    class Store:
        def __init__(self, target): self.target = target
        def read(self): return saved.get(self.target)
        def write(self, value): saved[self.target] = value
    store = auth.ClientSecretStore(factory=Store)
    store.write('55046', 'client-a-secret')
    store.write('55047', 'client-b-secret')
    assert store.read('55046') == 'client-a-secret'
    assert store.read('55047') == 'client-b-secret'
    assert store.read('55048') == ''
    assert auth.TARGET not in saved


def test_controller_surfaces_callback_failure_immediately():
    from controller.anilist_controller import AniListController
    controller = object.__new__(AniListController)
    controller.epoch, controller.events = 0, queue.Queue()
    messages, closed = [], []
    attempt = NS(errors=queue.Queue(), results=queue.Queue(), close=lambda: closed.append(True))
    attempt.errors.put('Client Secret prüfen')
    controller.attempt = attempt
    controller.status = NS(set=messages.append)
    controller.poll()
    assert messages == ['Client Secret prüfen'] and closed and controller.attempt is None
