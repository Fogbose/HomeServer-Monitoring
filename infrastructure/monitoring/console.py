"""Private loopback HomeServer console, using existing pinned SSH credentials."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT/'private/monitoring'
PORT = 18765
REMOTE_CACHE = '/var/lib/homeserver-monitoring/status.json'


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def local_config(required=False):
    path = STATE/'site.json'
    if path.is_file(): return json.loads(path.read_text(encoding='utf-8'))
    if required: raise ValueError('Configure private/monitoring/site.json first')
    return {}


def ssh():
    spec = importlib.util.spec_from_file_location('power_client', ROOT/'scripts/windows/HomeServer-Client.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.ssh_command()


def evaluate(data, now=None):
    now = time.time() if now is None else now
    alerts = []
    def alert(level, text): alerts.append({'level': level, 'text': text})
    if data.get('connection') != 'online':
        alert('critical', 'SSH indisponible : alimentation et réseau indéterminés')
        return alerts
    if now-data.get('observed', 0) > 180:
        alert('critical', 'Mesure serveur périmée ; état actuel inconnu')
        return alerts
    for name, limit, label in [('disk_percent',85,'Espace disque'), ('inode_percent',85,'Inodes'), ('memory_percent',90,'Mémoire')]:
        value = data.get(name)
        if value is not None and value >= limit: alert('warning', f'{label} : {value}%')
    nc = data.get('nextcloud', {})
    if nc.get('unknown') or not nc.get('installed') or nc.get('maintenance') or nc.get('needsDbUpgrade'):
        alert('warning', 'Nextcloud indisponible, en maintenance ou migration')
    if not data.get('capabilities', {}).get('privileged'):
        alert('info', 'Collecteur privilégié non installé : conteneurs, capture et actions serveur non vérifiés')
    else:
        age = now-data['capture_epoch'] if data.get('capture_epoch') else None
        if age is None or age > 36*3600: alert('critical', 'Capture serveur absente, inconnue ou âgée de plus de 36 h')
    if data.get('sessions_version')=='1':alert('warning','Correctif de reprise requis : actions d’alimentation temporairement bloquées')
    backup = data.get('windows_backup')
    if not backup or backup.get('Result') != 'Success': alert('warning', 'Copie Windows absente ou dernier résultat en échec')
    else:
        import datetime
        try:
            stamp = datetime.datetime.fromisoformat(backup['CompletedUtc'].replace('Z','+00:00')).timestamp()
            if now-stamp > 36*3600: alert('warning','Copie Windows âgée de plus de 36 h')
        except (KeyError, ValueError, TypeError): alert('warning','Date de copie Windows inconnue')
    for unit, state in data.get('units', {}).items():
        if state.get('Result') not in (None, '', 'success') or state.get('LoadState') != 'loaded':
            alert('warning', 'Unité à contrôler : '+unit)
        if unit.endswith('.timer') and state.get('ActiveState') != 'active':
            alert('warning', 'Timer inactif : '+unit)
    if data.get('failed_units') and isinstance(data['failed_units'],str): alert('warning','Unités systemd en échec : voir Événements')
    return alerts


class Store:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (ts REAL PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, detail TEXT)')
    def save(self, data):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO samples VALUES (?,?)', (data['received'], json.dumps(data)))
            db.execute('DELETE FROM samples WHERE ts < ?', (time.time()-14*86400,))
    def event(self, kind, detail):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO events VALUES (?,?,?)',(time.time(),kind,detail))
    def history(self):
        with closing(sqlite3.connect(self.path)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM (SELECT ts,payload FROM samples ORDER BY ts DESC LIMIT 240) ORDER BY ts')]


class Monitor:
    def __init__(self, demo=False, state=None):
        self.demo = demo
        self.state = state or STATE
        self.state.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.state/'history.sqlite')
        self.current = {'connection':'unknown','alerts':[]}
        self.lock = threading.Lock()
        self.busy = False
        self.stop = threading.Event()
        self.action_lock = threading.Lock()
        self.process = None
        self.tunnel = None
        self.tunnel_retry_after = 0

    def companions(self):
        if self.demo or not local_config().get('enable_cockpit_tunnel', False): return
        if time.time()<self.tunnel_retry_after or (self.tunnel is not None and self.tunnel.poll() is None): return
        cmd=ssh(); target=cmd.pop()
        cmd+=['-N','-T','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
              '-L','127.0.0.1:19090:127.0.0.1:9090',target]
        with (self.state/'tools-tunnel.log').open('ab') as log:
            self.tunnel=subprocess.Popen(cmd,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.tunnel_retry_after=time.time()+60

    def poll(self):
        with self.lock:
            if self.busy: return
            self.busy = True
        try:
            previous = self.current
            if self.demo:
                data = {'hostname':'DEMONSTRATION', 'observed':time.time(), 'memory_percent':34,
                        'disk_percent':23, 'cpu_percent':12, 'disk_free_gib':155,
                        'nextcloud':{'installed':True,'maintenance':False,'needsDbUpgrade':False},
                        'capabilities':{'privileged':False}, 'units':{}}
            else:
                self.companions()
                # Feed reviewed code to Python stdin, never interpolate user parameters.
                remote = "python3 -c 'from pathlib import Path; p=Path(\""+REMOTE_CACHE+"\"); print(p.read_text() if p.is_file() else \"{}\")'"
                cached = subprocess.run(ssh()+[remote],capture_output=True,text=True,timeout=25)
                if cached.returncode: raise RuntimeError('SSH_FAILED')
                data = json.loads(cached.stdout)
                if not data or time.time()-data.get('observed',0)>180:
                    result = subprocess.run(ssh()+['python3 -'],input=(HERE/'probe.py').read_text(encoding='utf-8'),capture_output=True,text=True,encoding='utf-8',timeout=70)
                    if result.returncode: raise RuntimeError('PROBE_FAILED')
                    data = json.loads(result.stdout)
                if data.get('hostname') != local_config(required=True).get('hostname'): raise RuntimeError('WRONG_HOST')
                status = ROOT/'private/backup-last-status.json'
                if status.is_file():
                    raw = json.loads(status.read_text(encoding='utf-8-sig'))
                    data['windows_backup'] = {k:raw.get(k) for k in ('Result','CompletedUtc','Scope')}
            data.update(connection='online', received=time.time(), demo=self.demo)
            if 'cpu_ticks' in data and 'cpu_ticks' in previous:
                total = data['cpu_ticks']-previous['cpu_ticks']; idle=data['cpu_idle']-previous['cpu_idle']
                if total > 0: data['cpu_percent']=round(max(0,min(100,100*(1-idle/total))),1)
            data['alerts'] = evaluate(data)
        except Exception as error:
            data = {'connection':'unknown','received':time.time(),'demo':self.demo,
                    'error':type(error).__name__, 'last_seen':self.current.get('observed',self.current.get('last_seen'))}
            data['alerts'] = evaluate(data)
        finally:
            with self.lock: self.busy = False
        if data.get('connection') != self.current.get('connection'):
            self.store.event('connection',data['connection'])
        self.current = data
        data['actions_enabled'] = not self.demo and local_config().get('allow_actions') is True
        self.store.save(data)
        data['admin_pc']={'state':'online','observed':time.time(),
                          'offline_confidence':'unknown_without_external_observer'}
        if not self.demo:
            tools_path=STATE/'tools-status.json'
            if tools_path.is_file():
                tools=json.loads(tools_path.read_text(encoding='utf-8'))
                cockpit=tools.get('cockpit',{})
                cockpit.update(ready=False,status='Installation serveur requise',url='http://127.0.0.1:19090')
                if data.get('cockpit_available') and self.tunnel and self.tunnel.poll() is None:
                    try:
                        import urllib.request
                        with urllib.request.urlopen(cockpit['url'],timeout=2):pass
                        cockpit.update(ready=True,status='Accès privé par tunnel SSH')
                    except Exception:cockpit['status']='Agent installé · accès à vérifier'
                tools['cockpit']=cockpit
                tools_path.write_text(json.dumps(tools,indent=2),encoding='utf-8')
        export=self.state/'olivetin'
        export.mkdir(exist_ok=True)
        summary='HOMESERVER — '+time.strftime('%d/%m/%Y %H:%M:%S')+'\n'
        summary+='Etat SSH : '+data.get('connection','unknown')+'\n'
        for key,label in [('cpu_percent','CPU %'),('memory_percent','RAM %'),('disk_percent','Disque %'),('disk_free_gib','Libre Gio')]:
            summary+=label+' : '+str(data.get(key,'inconnu'))+'\n'
        summary+='\nPoints a verifier :\n'+'\n'.join(a['text'] for a in data.get('alerts',[]))+'\n'
        (export/'summary.txt').write_text(summary,encoding='utf-8')
        stamp=data.get('capture_epoch')
        capture_date=time.strftime('%d/%m/%Y %H:%M:%S',time.localtime(stamp)) if stamp else 'non vérifiée'
        copy=data.get('windows_backup',{})
        (export/'backups.txt').write_text('RAPPORT DES SAUVEGARDES\n\nCapture serveur : '+capture_date+'\nCopie Windows : '+str(copy.get('Result','inconnue'))+'\nDate de copie (UTC) : '+str(copy.get('CompletedUtc','inconnue'))+'\nPérimètre : '+str(copy.get('Scope','inconnu'))+'\n\nUne copie réussie ne prouve pas une restauration.\n',encoding='utf-8')

    def loop(self):
        while not self.stop.is_set():
            self.poll(); self.stop.wait(60)

    def action(self, name, confirmation):
        if not self.demo and local_config().get('allow_actions') is not True: raise ValueError('Actions disabled by default; review your configuration and deployment first')
        if self.demo: raise ValueError('Actions interdites en démonstration')
        actions = {
            'ssh': ('OUVRIR SSH', [*ssh()]),
            'wol-lan': ('REVEILLER HOMESERVER', [__import__('sys').executable,str(ROOT/'tools/wake.py'),'--send']),
            'windows-shutdown': ('ARRETER CE PC WINDOWS', ['shutdown.exe','/s','/t','0']),
            'windows-reboot': ('REDEMARRER CE PC WINDOWS', ['shutdown.exe','/r','/t','0']),
            'windows-cancel': ('ANNULER ARRET WINDOWS', ['shutdown.exe','/a']),
        }
        remote = {
            'server-prepare': ('DECONNECTER LES SESSIONS ET PREPARER HOMESERVER', 'sudo /usr/bin/python3 -I /usr/local/sbin/homeserver-sessions.py --prepare --disconnect-clients'),
            'server-resume': ('REPRENDRE HOMESERVER', 'sudo /usr/bin/python3 -I /usr/local/sbin/homeserver-sessions.py --resume'),
            'server-check': ('CONTROLER HOMESERVER SANS APPEL', 'sudo /usr/bin/python3 -I /usr/local/sbin/homeserver-sessions.py --check --confirm-no-calls'),
            'server-shutdown': ('ARRETER HOMESERVER SANS APPEL', 'sudo /usr/bin/python3 -I /usr/local/sbin/homeserver-power-v2.py --poweroff --confirm-no-calls'),
            'server-reboot': ('REDEMARRER HOMESERVER SANS APPEL', 'sudo /usr/bin/python3 -I /usr/local/sbin/homeserver-power-v2.py --reboot --confirm-no-calls'),
            'backup-server': ('CAPTURER HOMESERVER', 'sudo systemctl start homeserver-nextcloud-backup.service'),
            'tls-renew': ('RENOUVELER TLS', 'sudo systemctl start homeserver-hpb-cert.service'),
        }
        if name in remote:
            if self.current.get('connection') != 'online' or time.time()-self.current.get('observed',0)>180 or self.current.get('power_version') != '2':
                raise ValueError('Paquet serveur non installé/vérifié ou état périmé')
            if name in ('server-prepare','server-resume','server-check','server-shutdown','server-reboot') and self.current.get('sessions_version')!='2':
                raise ValueError('Actions d’alimentation suspendues : module de reprise non vérifié')
            label, command = remote[name]
            if name in ('server-shutdown','server-reboot') and self.current.get('sessions_version'):
                command=command.replace('homeserver-power-v2.py','homeserver-sessions.py')
            cmd = ssh(); cmd.insert(1,'-t')
            cmd += [command+'; result=$?; printf "\\nCode=%s. Entrée pour fermer.\\n" "$result"; read answer; exit "$result"']
        elif name in actions: label,cmd = actions[name]
        else: raise ValueError('Action inconnue')
        if confirmation != label: raise ValueError('Confirmation explicite incorrecte')
        with self.action_lock:
            if name != 'windows-cancel' and self.process is not None and self.process.poll() is None:
                raise ValueError('Une fenêtre d’action est encore active : attendre ou la fermer')
            self.process = subprocess.Popen(cmd, creationflags=getattr(subprocess,'CREATE_NEW_CONSOLE',0))
        self.store.event('requested',name)
        return {'status':'requested','message':'Demande transmise ; vérifier le résultat dans la fenêtre dédiée. Aucun succès serveur présumé.'}


def handler(monitor, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, value, status=200, mime='application/json'):
            body = value if isinstance(value,bytes) else json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(body)
        def allowed(self):
            host = self.headers.get('Host','')
            return host == f'127.0.0.1:{self.server.server_port}' and secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
        def do_GET(self):
            files={'/':('dashboard.html','text/html; charset=utf-8'),'/app.js':('dashboard.js','text/javascript; charset=utf-8'),'/style.css':('dashboard.css','text/css; charset=utf-8')}
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}': return self.send({},403)
            if self.path in files:
                name,mime=files[self.path]; return self.send((HERE/name).read_bytes(),mime=mime)
            if not self.allowed(): return self.send({'error':'Authentication required'},403)
            if self.path == '/api/status': return self.send(monitor.current)
            if self.path == '/api/history': return self.send(monitor.store.history())
            if self.path == '/api/tools':
                path=STATE/'tools-status.json'
                return self.send(json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {})
            return self.send({},404)
        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.allowed() or origin != f'http://127.0.0.1:{self.server.server_port}': return self.send({},403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 2048: raise ValueError('Invalid body')
                body=json.loads(self.rfile.read(length))
                if self.path == '/api/refresh':
                    threading.Thread(target=monitor.poll,daemon=True).start(); return self.send({'status':'requested'})
                if self.path == '/api/quit':
                    self.send({'status':'stopping'})
                    threading.Thread(target=self.server.shutdown,daemon=True).start(); return
                if self.path != '/api/action': return self.send({},404)
                return self.send(monitor.action(body.get('action'),body.get('confirmation')))
            except (ValueError,TypeError,json.JSONDecodeError) as error: return self.send({'error':str(error)},400)
    return Handler


def main():
    global PORT
    parser=argparse.ArgumentParser(); parser.add_argument('--demo',action='store_true'); parser.add_argument('--no-browser',action='store_true'); parser.add_argument('--once',action='store_true')
    parser.add_argument('--port',type=int,default=PORT)
    args=parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('Use a non-privileged TCP port')
    PORT=args.port
    monitor=Monitor(args.demo, STATE/'demo' if args.demo else STATE)
    if args.once:
        monitor.poll(); print(json.dumps(monitor.current,ensure_ascii=True)); return
    token=secrets.token_urlsafe(32)
    try:
        server=LocalServer(('127.0.0.1',PORT),handler(monitor,token))
    except OSError:
        import urllib.request
        from urllib.parse import urlsplit
        launch=(STATE/'launch-url.txt').read_text(encoding='utf-8').strip()
        parts=urlsplit(launch)
        if parts.scheme!='http' or parts.netloc!=f'127.0.0.1:{PORT}' or not parts.fragment:
            raise RuntimeError('Port occupé et URL locale non reconnue')
        request=urllib.request.Request(f'http://127.0.0.1:{PORT}/api/status',headers={'Authorization':'Bearer '+parts.fragment})
        with urllib.request.urlopen(request,timeout=3) as response: json.load(response)
        if not args.no_browser: webbrowser.open(launch)
        print('CONSOLE_ALREADY_RUNNING : session locale existante'); return
    threading.Thread(target=monitor.loop,daemon=True).start()
    url=f'http://127.0.0.1:{PORT}/#'+token
    # Fragment is not sent over HTTP; only the private launcher holds the URL.
    (STATE/'launch-url.txt').write_text(url,encoding='utf-8')
    print('CONSOLE_READY: loopback only; fermer cette fenêtre pour arrêter.')
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        monitor.stop.set(); server.server_close()
        if monitor.tunnel is not None and monitor.tunnel.poll() is None:monitor.tunnel.terminate()


if __name__ == '__main__': main()
