"""Render the verified single-origin layout; no secrets printed, no side effects."""
import importlib.util
from pathlib import Path
import re
import urllib.parse

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('sso_shared', HERE/'sso-lab.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def hostname(origin):
    parts = urllib.parse.urlsplit(origin)
    if parts.scheme != 'https' or parts.port is not None or parts.path or parts.query or parts.fragment or parts.username:
        raise ValueError('Exact private HTTPS origin required')
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+', parts.hostname or ''):
        raise ValueError('Dedicated valid DNS hostname required')
    return parts.hostname


def render(origin, identity, key, old_actions, *, laboratory=False):
    if laboratory:
        if origin != 'https://auth.monitor.test:19446':
            raise ValueError('Only the fixed isolated laboratory origin is permitted')
        host = 'auth.monitor.test'
    else:
        host = hostname(origin)
    shared.AUTH, shared.BESZEL, shared.ACTIONS = origin+'/auth', origin+'/metrics', origin
    auth = shared.authelia_config(identity, key)
    auth['server']['address'] = 'tcp://0.0.0.0:9091/auth'
    auth['totp']['issuer'] = 'HomeServer Administration'
    auth['session']['name'] = '__Secure-homeserver-admin'
    auth['session']['cookies'][0].update(domain=host, authelia_url=origin+'/auth', default_redirection_url=origin+'/home/')
    auth['access_control']['rules'][0]['domain'] = host
    auth['identity_providers']['oidc']['jwks'][0]['key_id'] = 'homeserver-admin'
    users = {'users': {'monitor-admin': {'displayname': 'Administration HomeServer',
              'password': identity['password_digest'], 'email': identity['email'], 'groups': ['monitoring-admins']}}}
    actions = {
        'listenAddressSingleHTTPFrontend': '0.0.0.0:1337', 'logLevel': 'WARN',
        'authRequireGuestsToLogin': True,
        'authLocalUsers': old_actions.get('authLocalUsers', {'enabled': False}),
        'authOAuth2RedirectUrl': origin+'/oauth/callback',
        'authOAuth2Providers': {'authelia': {
            'name': 'authelia', 'title': 'Connexion HomeServer', 'clientId': 'olivetin',
            'clientSecret': identity['clients']['olivetin'],
            'authUrl': origin+'/auth/api/oidc/authorization',
            'tokenUrl': 'https://'+host+':19445/auth/api/oidc/token',
            'whoamiUrl': 'https://'+host+':19445/auth/api/oidc/userinfo',
            'usernameField': 'preferred_username', 'userGroupField': 'groups',
            'scopes': ['openid', 'profile', 'email', 'groups']}},
        'defaultPermissions': {'view': False, 'exec': False, 'logs': False},
        'defaultPolicy': {'showDiagnostics': False, 'showLogList': False},
        'accessControlLists': [{'name': 'sso-admins', 'matchUsergroups': ['monitoring-admins'],
                 'addToEveryAction': True, 'permissions': {'view': True, 'exec': True, 'logs': True}}],
        'actions': [
            {'title': 'État du serveur', 'shell': 'cat /reports/summary.txt', 'timeout': 5},
            {'title': 'Sauvegardes et reprise', 'shell': 'cat /reports/backups.txt', 'timeout': 5},
            {'title': 'Où lancer les opérations administrateur ?',
             'shell': "printf '%s\\n' 'Les actions serveur restent dans la console Windows et Cockpit, avec confirmation administrateur. Aucun accès root implicite depuis OliveTin.'", 'timeout': 5}]
    }
    nginx = '''pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
 include /etc/nginx/mime.types;
 default_type application/octet-stream;
 access_log off; server_tokens off;
 client_body_temp_path /tmp/client_temp; proxy_temp_path /tmp/proxy_temp;
 fastcgi_temp_path /tmp/fastcgi_temp; uwsgi_temp_path /tmp/uwsgi_temp; scgi_temp_path /tmp/scgi_temp;
 map $http_upgrade $connection_upgrade { default upgrade; '' close; }
 add_header X-Content-Type-Options nosniff always;
 add_header Referrer-Policy no-referrer always;
 proxy_set_header Host $http_host;
 proxy_set_header X-Forwarded-Proto https;
 proxy_set_header X-Forwarded-Host $http_host;
 proxy_set_header X-Forwarded-For $remote_addr;
 proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade;
 proxy_http_version 1.1;
 server {
  listen 19445 ssl; server_name HOST;
  ssl_certificate /tls/node.crt; ssl_certificate_key /tls/node.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  location ~ ^/auth/api/oidc/(token|userinfo)$ {
   proxy_set_header Host CANONICAL;
   proxy_set_header X-Forwarded-Host CANONICAL;
   proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_pass http://authelia:9091;
  }
  location / { return 404; }
 }
 server { listen 19444 default_server; server_name _; return 444; }
 server {
  listen 19444; server_name HOST;
  location = /healthz { return 200 'OK'; }
  location = /auth { return 302 /auth/; }
  location /auth/ { proxy_pass http://authelia:9091; }
  location = /internal/auth {
   internal; proxy_pass http://authelia:9091/auth/api/authz/auth-request;
   proxy_pass_request_body off; proxy_set_header Content-Length ""; proxy_set_header Authorization "";
   proxy_set_header X-Original-URL https://$http_host$request_uri;
   proxy_set_header X-Original-Method $request_method;
   proxy_set_header X-Forwarded-For $remote_addr;
  }
  location /home/ {
   auth_request /internal/auth;
   error_page 401 =302 ORIGIN/auth/?rd=ORIGIN/home/;
   alias /portal/; index index.html;
   add_header X-Content-Type-Options nosniff always;
   add_header Referrer-Policy no-referrer always;
   add_header Content-Security-Policy "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'" always;
  }
  location = /metrics { return 302 /metrics/; }
  location /metrics/api/ { auth_request /internal/auth; proxy_pass http://beszel:8090/api/; }
  location /metrics/ {
   auth_request /internal/auth;
   error_page 401 =302 ORIGIN/auth/?rd=ORIGIN/metrics/;
   proxy_pass http://beszel:8090/;
  }
  location /api/ { auth_request /internal/auth; proxy_pass http://olivetin:1337; }
  location / {
   auth_request /internal/auth;
   error_page 401 =302 ORIGIN/auth/?rd=ORIGIN/;
   proxy_pass http://olivetin:1337;
  }
 }
}
'''.replace('ORIGIN', origin).replace('HOST', host).replace('CANONICAL', urllib.parse.urlsplit(origin).netloc)
    common = {'restart': 'unless-stopped', 'read_only': True, 'cap_drop': ['ALL'],
              'security_opt': ['no-new-privileges:true'], 'tmpfs': ['/tmp'], 'networks': ['backend'],
              'logging': {'driver': 'json-file', 'options': {'max-size': '2m', 'max-file': '2'}}}
    services = {
        'authelia': {**common, 'image': shared.IMAGES['authelia'], 'user': '65532:65532',
                     'command': ['authelia', '--config', '/config/configuration.json'],
                     'volumes': ['./config/authelia:/config:ro', './data/authelia:/state',
                                 './data/healthcheck.env:/app/.healthcheck.env']},
        'beszel': {**common, 'image': shared.IMAGES['beszel'], 'user': '65532:65532',
                   'environment': {'APP_URL': origin+'/metrics', 'DISABLE_SSH': 'true',
                                   'CHECK_UPDATES': 'false', 'USER_CREATION': 'false'},
                   'ports': ['127.0.0.1:18091:8090'],
                   'volumes': ['./data/beszel:/beszel_data'], 'networks': ['backend', 'frontend']},
        'olivetin': {**common, 'image': shared.IMAGES['olivetin'], 'user': '65532:65532',
                     'volumes': ['./data/olivetin:/config', './config/olivetin.json:/config/config.yaml:ro',
                                 './reports:/reports:ro']},
        'proxy': {**common, 'image': shared.IMAGES['proxy'], 'user': '101:101',
                  'entrypoint': ['nginx', '-g', 'daemon off;'],
                  'ports': ['127.0.0.1:19444:19444'],
                  'volumes': ['./config/nginx.conf:/etc/nginx/nginx.conf:ro', './portal:/portal:ro',
                              './config/tls:/tls:ro'],
                  'depends_on': {'authelia': {'condition': 'service_healthy'},
                                 'beszel': {'condition': 'service_started'},
                                 'olivetin': {'condition': 'service_started'}},
                  'networks': {'backend': {'aliases': [host]}, 'frontend': {}}}}
    compose = {'name': 'homeserver-monitoring-sso', 'services': services,
               'networks': {'backend': {'internal': True}, 'frontend': {}}}
    return {'config/authelia/configuration.json': auth, 'config/authelia/users.json': users,
            'config/olivetin.json': actions, 'config/nginx.conf': nginx, 'compose.json': compose}
