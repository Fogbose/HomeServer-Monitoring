"""Exercise the selected production URL layout, with fictitious lab users only."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('lab', HERE/'sso-lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
ORIGIN = 'https://auth.monitor.test:19446'


def prepare():
    lab.AUTH = ORIGIN+'/auth'
    lab.BESZEL = ORIGIN+'/metrics'
    lab.ACTIONS = ORIGIN
    lab.prepare()
    path = lab.STATE/'authelia/configuration.json'
    configuration = json.loads(path.read_text(encoding='utf-8'))
    configuration['server']['address'] = 'tcp://0.0.0.0:9091/auth'
    lab.write(path, configuration)
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
 proxy_set_header Host $http_host; proxy_set_header X-Forwarded-Proto https;
 proxy_set_header X-Forwarded-Host $http_host; proxy_set_header X-Forwarded-For $remote_addr;
 proxy_set_header X-Forwarded-Port 19446;
 proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade;
 proxy_http_version 1.1;
 server { listen 19446 ssl default_server; server_name _; return 444; }
 server {
  listen 19446 ssl; server_name auth.monitor.test;
  location = /auth { return 302 /auth/; }
  location /auth/ { proxy_pass http://authelia:9091; }
  location = /internal/auth {
   internal; proxy_pass http://authelia:9091/auth/api/authz/auth-request;
   proxy_pass_request_body off; proxy_set_header Content-Length ""; proxy_set_header Authorization "";
   proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
   proxy_set_header X-Original-Method $request_method;
   proxy_set_header X-Forwarded-For $remote_addr;
  }
  location = /metrics { return 302 /metrics/; }
  location /metrics/api/ { auth_request /internal/auth; proxy_pass http://beszel:8090/api/; }
  location /metrics/ {
   auth_request /internal/auth;
   error_page 401 =302 https://auth.monitor.test:19446/auth/?rd=$scheme://$http_host$request_uri;
   proxy_pass http://beszel:8090/;
  }
  location /api/ { auth_request /internal/auth; proxy_pass http://olivetin:1337; }
  location / {
   auth_request /internal/auth;
   error_page 401 =302 https://auth.monitor.test:19446/auth/?rd=$scheme://$http_host$request_uri;
   proxy_pass http://olivetin:1337;
  }
 }
}
'''
    lab.write(lab.STATE/'nginx.conf', nginx)
    lab.write(lab.STATE/'profile.json', {'name': 'single-origin', 'auth': lab.AUTH,
                                        'beszel': lab.BESZEL, 'actions': lab.ACTIONS})
    print('SSO_SINGLE_ORIGIN_PREPARED: root OliveTin, /auth/ identity provider, /metrics/ Beszel; private lab only')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'start', 'test', 'stop'])
    operation = parser.parse_args().operation
    if operation == 'prepare':
        prepare()
    elif operation == 'start':
        lab.compose('up', '-d', '--wait', '--wait-timeout', '120', timeout=180)
        print('SSO_SINGLE_ORIGIN_RUNNING')
    elif operation == 'test':
        subprocess.run([sys.executable, str(HERE/'sso-lab-test.py')], check=True)
    else:
        lab.compose('stop')
        print('SSO_SINGLE_ORIGIN_STOPPED: data preserved')


if __name__ == '__main__':
    main()
