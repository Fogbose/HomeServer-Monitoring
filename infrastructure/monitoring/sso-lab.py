"""Isolated SSO integration lab. Never reads production credentials or contacts SSH.

All generated keys, fictitious identities, databases and logs stay in private/.
The lab CA is trusted only by the test client, never installed in Windows.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import os
import shutil

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'private/monitoring/sso-lab'
PROJECT = 'homeserver-monitoring-public-lab'
PORT = 19446
IMAGES = {
    'authelia': 'authelia/authelia:4.39.28@sha256:bd97cff4fcbf715b5ff1f9ae286afbe6033afce385302520b0368122d43a6f54',
    'proxy': 'nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236',
    'beszel': 'henrygd/beszel:0.21.2@sha256:b0be90bd4aee2993fde763032a5098ce4386fba43cfe5031ec017b8d9e8c97a2',
    'olivetin': 'ghcr.io/olivetin/olivetin:3000.22.0@sha256:e8441343ab8e3d13daa83f4cb2a00d9fa3510953a6040a556a805918f5636264',
}
AUTH = f'https://auth.monitor.test:{PORT}'
BESZEL = f'https://beszel.monitor.test:{PORT}'
ACTIONS = f'https://actions.monitor.test:{PORT}'


def run(args, **kwargs):
    # Commands that can return secrets are always captured; never print stdout.
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=kwargs.pop('timeout', 120), **kwargs)
    if result.returncode:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / 'last-command-error.log').write_text(result.stderr + result.stdout, encoding='utf-8')
        raise RuntimeError('Lab command failed; private diagnostic retained')
    return result.stdout


def docker(*args, **kwargs):
    return run(['docker', '--context', 'default', *args], **kwargs)


def compose(*args, **kwargs):
    return docker('compose', '-p', PROJECT, '-f', str(STATE/'compose.json'), *args, **kwargs)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) if not isinstance(value, str) else value, encoding='utf-8')


def digest(value):
    salt = secrets.token_bytes(16)
    encode = lambda b: base64.b64encode(b).decode().rstrip('=').replace('+', '.')
    derived = hashlib.pbkdf2_hmac('sha512', value.encode(), salt, 310000)
    return '$pbkdf2-sha512$310000$' + encode(salt) + '$' + encode(derived)


def random_password():
    output = docker('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                    IMAGES['authelia'], 'authelia', 'crypto', 'hash', 'generate', 'argon2',
                    '--random', '--random.length', '32')
    password = re.search(r'Random Password: (\S+)', output)
    hashed = re.search(r'Digest: (\$argon2id\$\S+)', output)
    if not password or not hashed:
        raise RuntimeError('Unexpected hash generator response')
    return {'password': password[1], 'digest': hashed[1]}


def authelia_config(identity, key):
    clients = []
    for name, redirect, pkce in [('beszel', BESZEL+'/api/oauth2-redirect', True),
                                  ('olivetin', ACTIONS+'/oauth/callback', False)]:
        client = {'client_id': name, 'client_name': name.title(),
                  'client_secret': digest(identity['clients'][name]), 'public': False,
                  'authorization_policy': 'monitoring_admins',
                  'redirect_uris': [redirect], 'scopes': ['openid', 'email', 'profile', 'groups'],
                  'response_types': ['code'], 'grant_types': ['authorization_code'],
                  'token_endpoint_auth_method': 'client_secret_basic',
                  'access_token_signed_response_alg': 'none',
                  'userinfo_signed_response_alg': 'none',
                  'consent_mode': 'pre-configured', 'pre_configured_consent_duration': '1h'}
        if pkce:
            client.update(require_pkce=True, pkce_challenge_method='S256')
        clients.append(client)
    return {
        'server': {'address': 'tcp://0.0.0.0:9091/'},
        'log': {'level': 'warn', 'format': 'json'},
        'theme': 'auto',
        'totp': {'issuer': 'HomeServer Monitoring LAB', 'period': 30, 'skew': 1},
        'authentication_backend': {'file': {'path': '/config/users.json'},
                                   'password_reset': {'disable': True}},
        'access_control': {'default_policy': 'deny', 'rules': [
            {'domain': ['auth.monitor.test', 'beszel.monitor.test', 'actions.monitor.test'],
             'policy': 'two_factor', 'subject': 'group:monitoring-admins'}]},
        'session': {'secret': identity['session_secret'], 'name': '__Secure-homeserver-lab',
                    'inactivity': '5m', 'expiration': '15m', 'remember_me': -1,
                    'cookies': [{'domain': 'monitor.test', 'authelia_url': AUTH,
                                 'same_site': 'lax'}]},
        'regulation': {'max_retries': 3, 'find_time': '2m', 'ban_time': '5m'},
        'storage': {'encryption_key': identity['storage_key'],
                    'local': {'path': '/state/db.sqlite3'}},
        'notifier': {'filesystem': {'filename': '/state/notifications.txt'}},
        'identity_validation': {'reset_password': {'jwt_secret': identity['reset_secret']}},
        'identity_providers': {'oidc': {
            'hmac_secret': identity['oidc_secret'],
            'jwks': [{'key_id': 'homeserver-lab', 'algorithm': 'RS256', 'use': 'sig', 'key': key}],
            'authorization_policies': {'monitoring_admins': {
                'default_policy': 'deny', 'rules': [
                    {'policy': 'two_factor', 'subject': 'group:monitoring-admins'}]}},
            'lifespans': {'access_token': '5m', 'id_token': '5m', 'authorize_code': '1m'},
            'clients': clients}}
    }


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    credential_file = STATE/'identities.json'
    if credential_file.exists():
        identity = json.loads(credential_file.read_text(encoding='utf-8'))
    else:
        identity = {name: secrets.token_urlsafe(48) for name in
                    ['session_secret', 'storage_key', 'oidc_secret', 'reset_secret']}
        identity['users'] = {name: random_password() for name in ['lab-admin', 'lab-viewer']}
        identity['clients'] = {name: secrets.token_urlsafe(48) for name in ['beszel', 'olivetin']}
        identity['beszel_bootstrap'] = secrets.token_urlsafe(32)
        write(credential_file, identity)
    for folder in ['tls', 'authelia', 'olivetin', 'state/authelia', 'state/beszel']:
        (STATE/folder).mkdir(parents=True, exist_ok=True)
    openssl = Path(os.environ.get('HOMESERVER_OPENSSL') or shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe')
    if not openssl.is_file():
        raise RuntimeError('Existing OpenSSL executable required; nothing installed automatically')
    tls = STATE/'tls'
    if not (tls/'server.crt').exists():
        run([str(openssl), 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '30',
             '-subj', '/CN=HomeServer isolated SSO lab CA', '-keyout', str(tls/'ca.key'), '-out', str(tls/'ca.crt')])
        run([str(openssl), 'req', '-new', '-newkey', 'rsa:3072', '-nodes',
             '-subj', '/CN=auth.monitor.test', '-keyout', str(tls/'server.key'), '-out', str(tls/'server.csr')])
        write(tls/'extensions.cnf', 'subjectAltName=DNS:auth.monitor.test,DNS:beszel.monitor.test,DNS:actions.monitor.test\n'
                                   'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
        run([str(openssl), 'x509', '-req', '-in', str(tls/'server.csr'), '-CA', str(tls/'ca.crt'),
             '-CAkey', str(tls/'ca.key'), '-CAcreateserial', '-days', '14',
             '-extfile', str(tls/'extensions.cnf'), '-out', str(tls/'server.crt')])
    if not (tls/'oidc.key').exists():
        run([str(openssl), 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
             '-out', str(tls/'oidc.key')])
    write(STATE/'authelia/configuration.json', authelia_config(identity, (tls/'oidc.key').read_text()))
    users = {}
    for name, item in identity['users'].items():
        users[name] = {'displayname': name, 'password': item['digest'],
                       'email': name+'@monitor.test',
                       'groups': ['monitoring-admins' if name == 'lab-admin' else 'viewers']}
    write(STATE/'authelia/users.json', {'users': users})
    health_env = STATE/'authelia/healthcheck.env'
    if not health_env.exists():
        write(health_env, '')
    (STATE/'olivetin/lab-ca.crt').write_bytes((tls/'ca.crt').read_bytes())
    write(STATE/'olivetin/config.yaml', {
        'listenAddressSingleHTTPFrontend': '0.0.0.0:1337', 'logLevel': 'WARN',
        'authRequireGuestsToLogin': True,
        'authOAuth2RedirectUrl': ACTIONS+'/oauth/callback',
        'authOAuth2Providers': {'authelia': {
            'name': 'authelia', 'title': 'HomeServer SSO', 'clientId': 'olivetin',
            'clientSecret': identity['clients']['olivetin'],
            'authUrl': AUTH+'/api/oidc/authorization', 'tokenUrl': AUTH+'/api/oidc/token',
            'whoamiUrl': AUTH+'/api/oidc/userinfo', 'usernameField': 'preferred_username',
            'userGroupField': 'groups', 'scopes': ['openid', 'profile', 'email', 'groups'],
            'certBundlePath': '/config/lab-ca.crt'}},
        'defaultPermissions': {'view': False, 'exec': False, 'logs': False},
        'defaultPolicy': {'showDiagnostics': False, 'showLogList': False},
        'accessControlLists': [{'name': 'admins', 'matchUsergroups': ['monitoring-admins'],
                               'addToEveryAction': True,
                               'permissions': {'view': True, 'exec': True, 'logs': True}}],
        'actions': [{'title': 'Laboratoire uniquement', 'shell': 'printf SSO_LAB_ONLY', 'timeout': 5}]
    })
    # Disable access logging: authorization codes and state never go to proxy logs.
    nginx = '''pid /tmp/nginx.pid;
events { worker_connections 128; }
http {
 access_log off; server_tokens off;
 client_body_temp_path /tmp/client_temp; proxy_temp_path /tmp/proxy_temp;
 fastcgi_temp_path /tmp/fastcgi_temp; uwsgi_temp_path /tmp/uwsgi_temp; scgi_temp_path /tmp/scgi_temp;
 map $http_upgrade $connection_upgrade { default upgrade; '' close; }
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_certificate /tls/server.crt; ssl_certificate_key /tls/server.key;
 add_header X-Content-Type-Options nosniff always;
 add_header Referrer-Policy no-referrer always;
 proxy_set_header Host $http_host;
 proxy_set_header X-Forwarded-Proto https;
 proxy_set_header X-Forwarded-Host $http_host;
 proxy_set_header X-Forwarded-For $remote_addr;
 proxy_set_header X-Forwarded-Port 19446;
 proxy_set_header Upgrade $http_upgrade;
 proxy_set_header Connection $connection_upgrade;
 proxy_http_version 1.1;
 server { listen 19446 ssl default_server; server_name _; return 444; }
 server { listen 19446 ssl; server_name auth.monitor.test; location / { proxy_pass http://authelia:9091; } }
 APP_SERVERS
}
'''
    app_servers = []
    for hostname, upstream in [('beszel.monitor.test', 'beszel:8090'), ('actions.monitor.test', 'olivetin:1337')]:
        app_servers.append('''server {
 listen 19446 ssl; server_name HOSTNAME;
 location = /internal/auth {
  internal; proxy_pass http://authelia:9091/api/authz/auth-request;
  proxy_pass_request_body off; proxy_set_header Content-Length "";
  proxy_set_header Authorization "";
  proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
  proxy_set_header X-Original-Method $request_method;
  proxy_set_header X-Forwarded-For $remote_addr;
 }
 location /api/ { auth_request /internal/auth; proxy_pass http://UPSTREAM; }
 location / {
  auth_request /internal/auth;
  error_page 401 =302 https://auth.monitor.test:19446/?rd=$scheme://$http_host$request_uri;
  proxy_pass http://UPSTREAM;
 }
}'''.replace('HOSTNAME', hostname).replace('UPSTREAM', upstream))
    nginx = nginx.replace('APP_SERVERS', '\n'.join(app_servers))
    write(STATE/'nginx.conf', nginx)
    common = {'restart': 'no', 'security_opt': ['no-new-privileges:true'], 'cap_drop': ['ALL'],
              'read_only': True, 'tmpfs': ['/tmp'], 'networks': ['lab'],
              'logging': {'driver': 'json-file', 'options': {'max-size': '2m', 'max-file': '2'}}}
    services = {
        'authelia': {**common, 'image': IMAGES['authelia'], 'user': '1000:1000',
                     'command': ['authelia', '--config', '/config/configuration.json'],
                     'volumes': ['./authelia:/config:ro', './state/authelia:/state',
                                 './authelia/healthcheck.env:/app/.healthcheck.env']},
        'beszel': {**common, 'image': IMAGES['beszel'], 'user': '1000:1000',
                   'environment': {'APP_URL': BESZEL, 'SSL_CERT_FILE': '/certs/lab-ca.crt',
                                   'SHARE_ALL_SYSTEMS': 'false'},
                   'volumes': ['./state/beszel:/beszel_data', './tls/ca.crt:/certs/lab-ca.crt:ro']},
        'olivetin': {**common, 'image': IMAGES['olivetin'], 'user': '1000:1000',
                     'volumes': ['./olivetin:/config', './olivetin/config.yaml:/config/config.yaml:ro']},
        'proxy': {**common, 'image': IMAGES['proxy'], 'user': '101:101',
                  'entrypoint': ['nginx', '-g', 'daemon off;'],
                  'ports': [f'127.0.0.1:{PORT}:{PORT}'],
                  'volumes': ['./nginx.conf:/etc/nginx/nginx.conf:ro', './tls/server.crt:/tls/server.crt:ro',
                              './tls/server.key:/tls/server.key:ro'],
                  'depends_on': {'authelia': {'condition': 'service_healthy'},
                                 'beszel': {'condition': 'service_started'},
                                 'olivetin': {'condition': 'service_started'}},
                  'networks': {'frontend': {}, 'lab': {'aliases': ['auth.monitor.test', 'beszel.monitor.test', 'actions.monitor.test']}}}
    }
    write(STATE/'compose.json', {'name': PROJECT, 'services': services,
                               'networks': {'lab': {'internal': True}, 'frontend': {}}})
    compose('config', '--quiet')
    write(STATE/'profile.json', {'name': 'three-origin', 'auth': AUTH, 'beszel': BESZEL, 'actions': ACTIONS})
    print('SSO_LAB_PREPARED: pinned images; loopback HTTPS; fictional accounts; no global CA or server change')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'start', 'stop', 'status'])
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare()
    elif args.operation == 'start':
        if not (STATE/'compose.json').is_file():
            raise RuntimeError('Prepare first')
        compose('config', '--quiet')
        compose('up', '-d', '--wait', '--wait-timeout', '120', timeout=180)
        print('SSO_LAB_RUNNING: isolated project only; no production access')
    elif args.operation == 'stop':
        compose('stop', timeout=120)
        print('SSO_LAB_STOPPED: containers stopped; data retained')
    else:
        print(compose('ps', '--format', '{{.Service}} {{.State}}').strip())


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('SSO_LAB_STOPPED_ON_ERROR: ' + type(exc).__name__ + '; private diagnostic if available')
        raise SystemExit(1)
