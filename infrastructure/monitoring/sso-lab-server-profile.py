"""Run the production renderer with fictitious identities and explicit lab TLS."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import shutil

HERE=Path(__file__).resolve().parent


def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,HERE/filename)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab=load('lab','sso-lab.py')
settings=load('settings','sso-server-config.py')
def prepare():
    identity=json.loads((lab.STATE/'identities.json').read_text())
    identity['password_digest']=settings.shared.digest(identity['users']['lab-admin']['password'])
    identity['email']='lab-admin@monitor.test'
    rendered=settings.render('https://auth.monitor.test:19446',identity,(lab.STATE/'tls/oidc.key').read_text(),{},laboratory=True)
    users=rendered['config/authelia/users.json']['users']
    users['lab-viewer']={'displayname':'lab-viewer','email':'lab-viewer@monitor.test',
                         'password':identity['users']['lab-viewer']['digest'],'groups':['viewers']}
    compose=rendered['compose.json']
    compose['name']=lab.PROJECT
    for service in compose['services'].values():
        service['restart']='no'
    compose['services']['beszel'].pop('ports')
    compose['services']['beszel']['environment']['SSL_CERT_FILE']='/certs/lab-ca.crt'
    compose['services']['beszel']['volumes'].append('./tls/ca.crt:/certs/lab-ca.crt:ro')
    rendered['config/olivetin.json']['authOAuth2Providers']['authelia']['certBundlePath']='/config/lab-ca.crt'
    compose['services']['olivetin']['volumes'].append('./tls/ca.crt:/config/lab-ca.crt:ro')
    compose['services']['proxy']['ports']=['127.0.0.1:19446:19446']
    nginx=rendered['config/nginx.conf'].replace('listen 19444','listen 19446 ssl')
    nginx=nginx.replace('http {','http {\n ssl_certificate /tls/node.crt; ssl_certificate_key /tls/node.key; ssl_protocols TLSv1.2 TLSv1.3;',1)
    rendered['config/nginx.conf']=nginx
    for folder in ['data/authelia','data/beszel','data/olivetin','reports','portal','config/tls']:
        (lab.STATE/folder).mkdir(parents=True,exist_ok=True)
    shutil.copyfile(lab.STATE/'tls/server.crt',lab.STATE/'config/tls/node.crt')
    shutil.copyfile(lab.STATE/'tls/server.key',lab.STATE/'config/tls/node.key')
    (lab.STATE/'data/olivetin/lab-ca.crt').write_bytes((lab.STATE/'tls/ca.crt').read_bytes())
    for name,value in rendered.items():
        lab.write(lab.STATE/name,value)
    for name,source in [('index.html','sso-home.html'),('style.css','sso-home.css'),('app.js','sso-home.js')]:
        lab.write(lab.STATE/'portal'/name,(HERE/source).read_text(encoding='utf-8'))
    lab.write(lab.STATE/'data/healthcheck.env','')
    for filename in ['summary.txt','backups.txt']:
        lab.write(lab.STATE/'reports'/filename,'LABORATORY ONLY\n')
    lab.write(lab.STATE/'profile.json',{'name':'server-profile','auth':'https://auth.monitor.test:19446/auth',
              'beszel':'https://auth.monitor.test:19446/metrics','actions':'https://auth.monitor.test:19446',
              'admin_username':'monitor-admin'})
    lab.compose('config','--quiet')


def main():
    if "--run-prepared" not in sys.argv: prepare()
    if "--prepare-only" in sys.argv: return
    lab.compose('up','-d','--wait','--wait-timeout','180',timeout=240)
    try:
        subprocess.run([sys.executable,str(HERE/'sso-lab-test.py')]+(['--expiry'] if '--expiry' in sys.argv else []),check=True)
    finally:
        lab.compose('stop')
        print('SSO_SERVER_PROFILE_STOPPED: data retained; no production change')


if __name__=="__main__": main()
