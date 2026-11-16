"""Real TLS/OAuth2/MFA integration tests using fictitious lab identities only."""
import base64
import hashlib
import hmac
import http.client
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import secrets
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sso_lab', HERE/'sso-lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
profile = lab.STATE/'profile.json'
if profile.exists():
    layout = json.loads(profile.read_text(encoding='utf-8'))
    if layout.get('name') not in ('single-origin', 'three-origin', 'server-profile') or any(
            urllib.parse.urlsplit(layout[k]).scheme != 'https' or
            urllib.parse.urlsplit(layout[k]).hostname not in {'auth.monitor.test', 'beszel.monitor.test', 'actions.monitor.test'} or
            urllib.parse.urlsplit(layout[k]).port != 19446 for k in ['auth','beszel','actions']):
        raise ValueError('Unexpected laboratory layout')
    lab.AUTH, lab.BESZEL, lab.ACTIONS = (layout[k] for k in ['auth','beszel','actions'])
HOSTS = {'auth.monitor.test', 'beszel.monitor.test', 'actions.monitor.test'}
CHECKS = []


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    CHECKS.append(label)


class LabConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.host not in HOSTS or self.port != lab.PORT:
            raise ValueError('Only isolated laboratory hosts are permitted')
        raw = socket.create_connection(('127.0.0.1', self.port), self.timeout)
        # Validate CA and requested DNS hostname, despite routing to loopback.
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class LabTLS(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(LabConnection, request, context=self._context)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class Browser:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.csrf = None
        context = ssl.create_default_context(cafile=str(lab.STATE/'tls/ca.crt'))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                        LabTLS(context=context), NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, url, body=None, method=None, headers=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in HOSTS or parts.port != lab.PORT:
            raise ValueError('Unexpected test destination')
        head = {'Accept': 'application/json', 'Origin': f'https://{parts.netloc}',
                'Referer': f'https://{parts.netloc}/'}
        if body is not None:
            head['Content-Type'] = 'application/json'
        if self.csrf:
            head['X-CSRF-Token'] = self.csrf
        head.update(headers or {})
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         method=method, headers=head)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, response_headers, raw = response.status, response.headers, response.read(2_000_000)
        if response_headers.get('X-CSRF-Token'):
            self.csrf = response_headers['X-CSRF-Token']
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            value = raw.decode('utf-8', errors='replace')
        return status, response_headers, value


def totp(secret):
    key = base64.b32decode(secret.upper()+'='*((-len(secret)) % 8))
    value = hmac.new(key, struct.pack('>Q', int(time.time())//30), hashlib.sha1).digest()
    offset = value[-1] & 15
    return str((struct.unpack('>I', value[offset:offset+4])[0] & 0x7fffffff) % 1_000_000).zfill(6)


def bootstrap(identity, browser):
    email = 'bootstrap@monitor.test'
    result = browser.request(lab.BESZEL+'/api/collections/_superusers/auth-with-password',
                             {'identity': email, 'password': identity['beszel_bootstrap']})
    if result[0] != 200:
        # Only a new synthetic account inside this lab. No production key/account.
        lab.docker('exec', PROJECT_CONTAINER('beszel'), '/beszel', 'superuser', 'create',
                   email, identity['beszel_bootstrap'])
        result = browser.request(lab.BESZEL+'/api/collections/_superusers/auth-with-password',
                                 {'identity': email, 'password': identity['beszel_bootstrap']})
    check(result[0] == 200, 'lab Beszel bootstrap authenticated')
    auth = {'Authorization': result[2]['token']}
    oauth = {'enabled': True, 'providers': [{
        'name': 'oidc', 'displayName': 'HomeServer SSO', 'clientId': 'beszel',
        'clientSecret': identity['clients']['beszel'],
        'authURL': lab.AUTH+'/api/oidc/authorization', 'tokenURL': lab.AUTH+'/api/oidc/token',
        'userInfoURL': lab.AUTH+'/api/oidc/userinfo',
    }]}
    if profile.exists() and layout.get('name') == 'server-profile':
        oauth['providers'][0]['tokenURL'] = 'https://auth.monitor.test:19445/auth/api/oidc/token'
        oauth['providers'][0]['userInfoURL'] = 'https://auth.monitor.test:19445/auth/api/oidc/userinfo'
    result = browser.request(lab.BESZEL+'/api/collections/users', {'oauth2': oauth}, method='PATCH', headers=auth)
    check(result[0] == 200, 'native Beszel OIDC configured')
    records = browser.request(lab.BESZEL+'/api/collections/users/records?perPage=100', headers=auth)[2]['items']
    if not any(row['email'] == 'lab-admin@monitor.test' for row in records):
        result = browser.request(lab.BESZEL+'/api/collections/users/records', {
            'email': 'lab-admin@monitor.test', 'password': identity['users']['lab-admin']['password'],
            'passwordConfirm': identity['users']['lab-admin']['password'], 'verified': True, 'role': 'admin'}, headers=auth)
        check(result[0] == 200, 'existing admin role prepared for explicit linking')


def enroll_totp(user='lab-admin'):
    if user not in ('lab-admin', 'lab-viewer'):
        raise ValueError('Only fictitious lab identities are permitted')
    actual = layout.get('admin_username',user) if profile.exists() and user=='lab-admin' else user
    if actual not in ('lab-admin','lab-viewer','monitor-admin'):
        raise ValueError('Unexpected fictitious laboratory user')
    prefix = layout['name']+'-' if profile.exists() else ''
    uri_file = lab.STATE/(prefix+actual+'-totp-uri.txt')
    if not uri_file.exists():
        output = lab.docker('exec', PROJECT_CONTAINER('authelia'), 'authelia', 'storage', 'user', 'totp',
                            'generate', actual, '--force', '--config', '/config/configuration.json')
        # The CLI returns a provisioning URI. This secret is never printed.
        import re
        match = re.search(r"otpauth://[^\s\x1b\"']+", output)
        if not match:
            lab.write(lab.STATE/'totp-cli-private.log', output)
            raise RuntimeError('TOTP provisioning URI missing; inspect private diagnostic')
        lab.write(uri_file, match[0])
    uri = urllib.parse.urlsplit(uri_file.read_text(encoding='utf-8').strip().strip("\"'"))
    return urllib.parse.parse_qs(uri.query)['secret'][0]


def PROJECT_CONTAINER(service):
    return lab.PROJECT+'-'+service+'-1'


def login(browser, identity, user):
    browser.request(lab.AUTH+'/api/state')
    result = browser.request(lab.AUTH+'/api/firstfactor', {
        'username': layout.get('admin_username',user) if profile.exists() and user=='lab-admin' else user,
        'password': identity['users'][user]['password'], 'keepMeLoggedIn': False})
    check(result[0] == 200, 'first factor accepted for '+user)
    return browser.request(lab.AUTH+'/api/state')[2]


def authorize(browser, url, client):
    for _ in range(6):
        status, headers, body = browser.request(url)
        location = headers.get('Location', '')
        if status in (302, 303, 307) and location:
            location = urllib.parse.urljoin(url, location)
            parts = urllib.parse.urlsplit(location)
            query = urllib.parse.parse_qs(parts.query)
            if 'code' in query or 'error' in query:
                return location
            if 'flow_id' in query:
                flow_id = query['flow_id'][0]
                result = browser.request(lab.AUTH+'/api/oidc/consent?flow_id='+urllib.parse.quote(flow_id))
                if result[0] != 200:
                    raise RuntimeError('Consent lookup failed: HTTP '+str(result[0]))
                result = browser.request(lab.AUTH+'/api/oidc/consent', {
                    'client_id': client, 'flow_id': flow_id, 'consent': True, 'pre_configure': True,
                    'claims': result[2].get('data', {}).get('claims') or []})
                if result[0] != 200:
                    raise RuntimeError('Consent acceptance failed: HTTP '+str(result[0]))
                url = result[2]['data']['redirect_uri']
            else:
                url = location
        else:
            # No code: login or second factor is still required.
            return url
    raise RuntimeError('Unexpected authorization redirect loop')


def main():
    configuration = json.loads((lab.STATE/'compose.json').read_text(encoding='utf-8'))
    for name, service in configuration['services'].items():
        check(not service.get('privileged') and service.get('cap_drop') == ['ALL']
              and service.get('read_only') and 'no-new-privileges:true' in service.get('security_opt', []),
              name+' runs without Linux capabilities and with read-only root')
        check('@sha256:' in service['image'], name+' image is immutable')
        check(not any('docker.sock' in mount or 'id_ed25519' in mount for mount in service.get('volumes', [])),
              name+' has no production key or Docker socket')
    check(all(not configuration['services'][name].get('ports') for name in ['authelia', 'beszel', 'olivetin']),
          'application backends have no host port bypass')
    ports = json.loads(lab.docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', PROJECT_CONTAINER('proxy')))
    check(ports.get('19446/tcp') == [{'HostIp': '127.0.0.1', 'HostPort': '19446'}], 'actual HTTPS listener is loopback only')
    # Negative TLS tests use no authentication material and never disable validation.
    for context, hostname, label in [
        (ssl.create_default_context(), 'auth.monitor.test', 'untrusted lab CA rejected by normal trust store'),
        (ssl.create_default_context(cafile=str(lab.STATE/'tls/ca.crt')), 'wrong.monitor.test', 'wrong certificate hostname rejected')]:
        rejected = False
        try:
            with socket.create_connection(('127.0.0.1', lab.PORT), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=hostname):
                    pass
        except ssl.SSLCertVerificationError:
            rejected = True
        check(rejected, label)
    identity = json.loads((lab.STATE/'identities.json').read_text(encoding='utf-8'))
    secret = enroll_totp()
    browser = Browser()
    result = browser.request(lab.AUTH+'/.well-known/openid-configuration')
    check(result[0] == 200 and result[2]['issuer'] == lab.AUTH, 'issuer and TLS hostname verified')
    check(browser.request(lab.AUTH+'/')[0] == 200, 'verified HTTPS auth.monitor.test')
    for base in [lab.BESZEL, lab.ACTIONS]:
        check(browser.request(base+'/')[0] == 302, 'unauthenticated app redirects to identity provider')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 401, 'anonymous actions denied by gateway')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {}, headers={'Remote-User': 'lab-admin',
          'X-Forwarded-User': 'lab-admin'})[0] == 401, 'forged identity headers denied')
    state = login(browser, identity, 'lab-admin')
    check(state.get('data', {}).get('authentication_level') == 1, 'password alone is not MFA')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 401,
          'gateway withholds application access before second factor')
    result = browser.request(lab.AUTH+'/api/secondfactor/totp', {'token': totp(secret)})
    check(result[0] == 200, 'TOTP second factor accepted')
    state = browser.request(lab.AUTH+'/api/state')[2]
    check(state.get('data', {}).get('authentication_level') == 2, 'MFA session established')
    if profile.exists() and layout.get('name') == 'server-profile':
        home=browser.request(lab.ACTIONS+'/home/')
        check(home[0]==200 and 'Administration HomeServer' in home[2], 'authenticated administration home loads')
        check("frame-ancestors 'none'" in home[1].get('Content-Security-Policy',''), 'home denies framing')
        check('text/css' in browser.request(lab.ACTIONS+'/home/style.css')[1].get('Content-Type',''), 'home CSS has correct MIME type')
        check('javascript' in browser.request(lab.ACTIONS+'/home/app.js')[1].get('Content-Type',''), 'home JavaScript has correct MIME type')
    # Real HTML asset addresses must survive subpath hosting, not just API calls.
    import re
    for base in [lab.BESZEL, lab.ACTIONS]:
        page = browser.request(base+'/')
        check(page[0] == 200 and isinstance(page[2], str), 'authenticated native app HTML loads')
        assets = re.findall(r'(?:src|href)=[\"\']([^\"\']+\.(?:js|css)(?:\?[^\"\']*)?)[\"\']', page[2])
        check(bool(assets), 'native app HTML declares JS/CSS assets')
        for asset in assets:
            check(browser.request(urllib.parse.urljoin(base+'/', asset))[0] == 200,
                  'native app asset loads at selected URL layout')
    bootstrap(identity, browser)
    methods = browser.request(lab.BESZEL+'/api/collections/users/auth-methods')[2]
    provider = next(p for p in methods['oauth2']['providers'] if p['name'] == 'oidc')
    authorize_url = provider['authURL']+urllib.parse.quote(lab.BESZEL+'/api/oauth2-redirect', safe='')
    callback = authorize(browser, authorize_url, 'beszel')
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(callback).query)
    check('code' in query and query.get('state') == [provider['state']], 'Beszel authorization code and state matched')
    result = browser.request(lab.BESZEL+'/api/collections/users/auth-with-oauth2', {
        'provider': 'oidc', 'code': query['code'][0], 'codeVerifier': provider['codeVerifier'],
        'redirectURL': lab.BESZEL+'/api/oauth2-redirect'})
    if result[0] != 200:
        lab.write(lab.STATE/'oauth-failure-private.json', {'status':result[0],'response':result[2]})
    check(result[0] == 200 and result[2].get('record', {}).get('role') == 'admin', 'native Beszel SSO retains intended admin account')
    beszel_token = result[2]['token']
    replay = browser.request(lab.BESZEL+'/api/collections/users/auth-with-oauth2', {
        'provider': 'oidc', 'code': query['code'][0], 'codeVerifier': provider['codeVerifier'],
        'redirectURL': lab.BESZEL+'/api/oauth2-redirect'})
    check(replay[0] != 200, 'used authorization code cannot be replayed')
    start = browser.request(lab.ACTIONS+'/oauth/login?provider=authelia')
    check(start[0] == 302 and start[1].get('Location', '').startswith(lab.AUTH+'/'), 'native OliveTin OAuth2 initiated')
    wrong = browser.request(lab.ACTIONS+'/oauth/callback?state=forged&code=invalid')
    check(wrong[0] == 400, 'OliveTin rejects forged callback state')
    callback = authorize(browser, start[1]['Location'], 'olivetin')
    check('code' in urllib.parse.parse_qs(urllib.parse.urlsplit(callback).query),
          'second application reuses existing MFA session without password')
    result = browser.request(callback)
    check(result[0] in (302, 303), 'native OliveTin callback completed')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 200, 'OliveTin admin dashboard authorized')
    cookie = next(c for c in browser.cookies if c.name == 'olivetin-sid-oauth')
    check(cookie.secure and cookie.has_nonstandard_attr('HttpOnly'), 'OliveTin session cookie Secure and HttpOnly')
    # A legitimate account outside the allowed group must not obtain a code.
    viewer = Browser()
    login(viewer, identity, 'lab-viewer')
    viewer_secret = enroll_totp('lab-viewer')
    check(viewer.request(lab.AUTH+'/api/secondfactor/totp', {'token': totp(viewer_secret)})[0] == 200,
          'non-admin has valid second factor for independent RBAC test')
    check(viewer.request(lab.AUTH+'/api/state')[2].get('data', {}).get('authentication_level') == 2,
          'non-admin MFA confirmed before permission test')
    denied = viewer.request(start[1]['Location'])
    deny_location = denied[1].get('Location', '')
    check('code' not in urllib.parse.parse_qs(urllib.parse.urlsplit(deny_location).query), 'non-admin does not obtain authorization code')
    check(viewer.request(lab.ACTIONS+'/api/GetDashboard', {})[0] in (401, 403), 'non-admin cannot access action dashboard')
    check(browser.request(lab.ACTIONS+'/api/Logout', {})[0] == 200, 'explicit OliveTin logout accepted')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 403, 'explicit OliveTin logout revokes native access')
    start = browser.request(lab.ACTIONS+'/oauth/login?provider=authelia')
    callback = authorize(browser, start[1]['Location'], 'olivetin')
    browser.request(callback)
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 200, 'application can reconnect using existing MFA session')
    # Explicitly measure the important SSO versus application-session distinction.
    check(browser.request(lab.AUTH+'/api/logout', {})[0] == 200, 'identity-provider logout accepted')
    state = browser.request(lab.AUTH+'/api/state')[2]
    check(state.get('data', {}).get('authentication_level') == 0, 'identity-provider session closed')
    check(any(c.name == 'olivetin-sid-oauth' for c in browser.cookies), 'native cookie retained to test enforced gateway revocation')
    check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 401,
          'gateway blocks retained OliveTin session after central logout')
    check(browser.request(lab.BESZEL+'/api/collections/users/auth-refresh', {},
                         headers={'Authorization': beszel_token})[0] == 401,
          'gateway blocks retained Beszel token after central logout')
    lab.write(lab.STATE/'test-result.json', {'checks': CHECKS,
              'production_changed': False, 'production_ready': False,
              'blockers': ['Cockpit system-session integration not implemented',
                           'Production routing, backups, shutdown registry and user MFA enrollment pending']})
    print('SSO_LAB_CHECKS_OK: '+str(len(CHECKS))+'; Beszel + OliveTin native SSO and MFA; production unchanged')
    print('SSO_PRODUCTION_NOT_READY: Cockpit and production deployment are pending')


def expiry_test():
    """Exercise real idle expiry with shortened lab-only timers, then restore."""
    path = lab.STATE/('config/authelia/configuration.json' if profile.exists() and layout.get('name')=='server-profile' else 'authelia/configuration.json')
    original = path.read_text(encoding='utf-8')
    identity = json.loads((lab.STATE/'identities.json').read_text(encoding='utf-8'))
    configuration = json.loads(original)
    configuration['session'].update(inactivity='3s', expiration='8s')
    try:
        lab.write(path, configuration)
        lab.compose('restart', 'authelia')
        wait_ready()
        # A TOTP accepted earlier must not be reused. Wait for a fresh time step.
        step = int(time.time())//30
        deadline = time.monotonic()+32
        while int(time.time())//30 == step:
            if time.monotonic() > deadline:
                raise RuntimeError('Clock did not advance')
            time.sleep(.25)
        browser = Browser()
        login(browser, identity, 'lab-admin')
        check(browser.request(lab.AUTH+'/api/secondfactor/totp', {'token': totp(enroll_totp())})[0] == 200,
              'short-lived test session authenticated')
        time.sleep(4)
        check(browser.request(lab.ACTIONS+'/api/GetDashboard', {})[0] == 401,
              'expired central session is denied by application gateway')
        check(browser.request(lab.AUTH+'/api/state')[2].get('data', {}).get('authentication_level') == 0,
              'real inactivity expiry closes identity-provider session')
        lab.write(lab.STATE/'expiry-result.json', {'idle_expiry_verified': True,
                  'test_inactivity_seconds': 3, 'test_expiration_seconds': 8})
        print('SSO_IDLE_EXPIRY_OK: real timed expiry and gateway refusal verified')
    finally:
        lab.write(path, original)
        lab.compose('restart', 'authelia')
        wait_ready()
        print('SSO_LAB_SESSION_POLICY_RESTORED: 5m inactivity, 15m maximum')


def wait_ready():
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        try:
            status, _, body = Browser().request(lab.AUTH+'/api/health')
            if status == 200 and isinstance(body, dict) and body.get('status') == 'OK':
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(.5)
    raise RuntimeError('Identity provider did not become ready within 30 seconds')


if __name__ == '__main__':
    try:
        import sys
        if '--expiry-only' not in sys.argv:
            main()
        if '--expiry' in sys.argv or '--expiry-only' in sys.argv:
            expiry_test()
    except Exception as error:
        print('SSO_LAB_TEST_FAILED: '+type(error).__name__+'; '+str(error)[:180])
        raise SystemExit(1)
