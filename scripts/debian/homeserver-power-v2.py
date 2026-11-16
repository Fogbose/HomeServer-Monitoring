#!/usr/bin/python3
"""Manual, fail-closed multi-project shutdown. No automatic power operations."""
import argparse
import fcntl
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

POLICY = Path('/etc/homeserver-power.json')
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'}
CORE = ['app','cron','db','redis']
PREPARATION = Path('/var/lib/homeserver-monitoring/preparation.json')


def run(*args,timeout=180):
    result=subprocess.run(args,capture_output=True,text=True,env=ENV,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Commande échouée : '+' '.join(args[:3])+' ; '+result.stderr.strip()[-1400:])
    return result.stdout.strip()


def trusted(path):
    path = Path(path)
    for part in [path,*path.parents]:
        value=part.lstat()
        if part.is_symlink() or value.st_uid != 0 or value.st_mode & 0o022:
            raise RuntimeError('Chemin non contrôlé par root : '+str(part))


def read_policy():
    trusted(POLICY)
    data=json.loads(POLICY.read_text())
    if data.get('version') != 2 or not isinstance(data.get('modules'),list):
        raise RuntimeError('Politique inconnue')
    if not data.get('hostname') or data.get('hostname').endswith('.invalid'): raise RuntimeError('Configure the exact deployment hostname')
    lock = Path(data.get('lock_file',''))
    if not lock.is_absolute() or not lock.is_relative_to('/run/lock/'): raise RuntimeError('Lock must be under /run/lock')
    trusted(lock)
    names=set()
    for module in [data['core'],*data['modules']]:
        if module['project'] in names or not module['project'].replace('-','').isalnum():
            raise RuntimeError('Projet dupliqué/invalide')
        names.add(module['project'])
        for filename in module.get('files',[module['file']]):
            path=Path(filename)
            if not path.is_absolute() or not path.is_relative_to('/srv/'):
                raise RuntimeError('Compose hors /srv')
            trusted(path)
            for sibling in path.parent.iterdir():
                if sibling.is_file(): trusted(sibling)
        if not module['services'] or any(not s.replace('-','').isalnum() for s in module['services']):
            raise RuntimeError('Services invalides')
        if module.get('activity') not in ('http','manual-no-calls','none'):
            raise RuntimeError('Sonde activité non définie')
    if data['core']['services'] != CORE: raise RuntimeError('Services Nextcloud inattendus')
    return data


def compose(module,*args,timeout=None):
    files=[]
    for file in module.get('files',[module['file']]): files.extend(['-f',file])
    command=('docker','compose','--project-directory',str(Path(module['file']).parent),'-p',module['project'],*files,*args)
    return run(*command,timeout=timeout) if timeout is not None else run(*command)


def configuration_hashes(policy):
    return {filename:hashlib.sha256(Path(filename).read_bytes()).hexdigest()
            for module in [policy['core'],*policy['modules']]
            for filename in module.get('files',[module['file']])}


def owned_preparation(policy):
    if not PREPARATION.exists():return None
    trusted(PREPARATION)
    data=json.loads(PREPARATION.read_text())
    if data.get('schema')!=2 or data.get('owner')!='homeserver-sessions':raise RuntimeError('Préparation non reconnue : pas de reprise automatique')
    if data.get('policy')!=hashlib.sha256(POLICY.read_bytes()).hexdigest() or data.get('configuration')!=configuration_hashes(policy):
        raise RuntimeError('Configuration modifiée : reprise automatique refusée')
    return data


def nextcloud_status(core):
    last=None
    for attempt in range(18):
        try:
            status=json.loads(compose(core,'exec','-T','-u','www-data','app','php','occ','status','--output=json',timeout=20))
        except (RuntimeError,ValueError,subprocess.TimeoutExpired) as error:last=error
        else:
            if status.get('installed') is True:
                if status.get('needsDbUpgrade') is not False:raise RuntimeError('Migration en attente : aucune désactivation automatique de maintenance')
                return status
        if attempt<17:time.sleep(3)
    raise RuntimeError('Statut Nextcloud non vérifié : '+str(last))


def start_registered(policy):
    core=policy['core'];marker=owned_preparation(policy)
    # Existing containers only: no recreation, image pull or general app upgrade.
    compose(core,'start','db','redis','app',timeout=180)
    status=nextcloud_status(core)
    if status.get('maintenance'):
        if not marker or not marker.get('maintenance_owned') or marker.get('version')!=status.get('versionstring'):
            raise RuntimeError('Maintenance non attribuable à notre préparation : arrêt de la reprise, contrôle manuel requis')
        compose(core,'exec','-T','-u','www-data','app','php','occ','maintenance:mode','--off')
    compose(core,'start','--wait','--wait-timeout','600',*CORE,timeout=660)
    for module in reversed(policy['modules']):
        compose(module,'start','--wait','--wait-timeout','180',*module['services'],timeout=240)
    if marker:PREPARATION.unlink(missing_ok=True)


def containers(policy):
    known={}; coreids={}
    for module in [policy['core'],*policy['modules']]:
        for service in module['services']:
            cid=compose(module,'ps','-q',service)
            if not cid or '\n' in cid: raise RuntimeError('Service absent/ambigu : '+module['project']+'/'+service)
            state=json.loads(run('docker','inspect','--format','{{json .State}}',cid))
            if not state.get('Running') or state.get('Paused') or state.get('Health',{}).get('Status') not in (None,'healthy'):
                raise RuntimeError('Service non prêt : '+service)
            known[cid]=module['project']+'/'+service
            if module is policy['core']: coreids[service]=cid
    if set(run('docker','ps','--no-trunc','-q').splitlines()) != set(known):
        raise RuntimeError('Conteneur supplémentaire non enregistré : arrêt refusé')
    return coreids


def http_active(cid,port=80):
    pid=run('docker','inspect','--format','{{.State.Pid}}',cid)
    if not pid.isdigit() or int(pid)<1: raise RuntimeError('PID inconnu')
    return bool(run('nsenter','-t',pid,'-n','ss','-Htn','state','established',f'sport = :{port}'))


def common_checks(check_sessions=True,ignore_job_units=()):
    if os.geteuid()!=0 or socket.gethostname()!=read_policy().get('hostname'): raise RuntimeError('Mauvaise cible ou utilisateur')
    inhibitors=Path('/run/homeserver-power-inhibitors')
    if not inhibitors.is_dir() or any(inhibitors.iterdir()): raise RuntimeError('Inhibiteur présent ou état inconnu')
    if 'shutdown' in run('systemd-inhibit','--list','--no-legend','--no-pager'): raise RuntimeError('Inhibiteur systemd')
    if run('systemctl','show','homeserver-nextcloud-backup.service','-p','ActiveState','--value')!='inactive': raise RuntimeError('Sauvegarde active/inconnue')
    for line in run('systemctl','list-jobs','--no-pager','--no-legend').splitlines():
        fields=line.split()
        if len(fields)!=4 or fields[1] not in ignore_job_units:raise RuntimeError('Opération système en cours : '+line)
    if check_sessions and len(run('who').splitlines())>1: raise RuntimeError('Autres sessions ouvertes')
    for name in ('apt','apt-get','dpkg','unattended-upgr','pg_restore','restic','rsync','scp','sftp-server'):
        result=subprocess.run(['pgrep','-x',name],env=ENV,capture_output=True)
        if result.returncode!=1: raise RuntimeError('Opération active ou sonde impossible : '+name)


def preflight(policy,no_calls=False):
    common_checks()
    ids=containers(policy)
    status=json.loads(compose(policy['core'],'exec','-T','-u','www-data','app','php','occ','status','--output=json'))
    if not status.get('installed') or status.get('maintenance') is not False or status.get('needsDbUpgrade') is not False: raise RuntimeError('Nextcloud non disponible')
    if http_active(ids['app']): raise RuntimeError('Connexion Nextcloud active')
    talk_idle(policy,no_calls)
    return ids


def talk_idle(policy,no_calls):
    if any(m['activity']=='manual-no-calls' for m in policy['modules']) and not no_calls:
        raise RuntimeError('Talk : absence d’appel non prouvée. Fermer les appels puis confirmer explicitement --confirm-no-calls ; jamais automatique')
    for module in policy['modules']:
        if module['activity']=='manual-no-calls' and 'hpb' in module['services']:
            cid=compose(module,'ps','-q','hpb')
            if http_active(cid,8081): raise RuntimeError('Connexion de signalisation HPB active : fermer les clients Talk')


def cron_idle(text):
    rows=text.splitlines()[1:]
    return bool(rows) and all(len(row.split())==2 and row.split()[0].isdigit() and row.split()[1] in ('busybox','crond','docker-init','tini') for row in rows)


def stop_module(module):
    ids=[compose(module,'ps','-q',service) for service in module['services']]
    compose(module,'stop','--timeout','120',*module['services'])
    for cid in ids:
        state=json.loads(run('docker','inspect','--format','{{json .State}}',cid))
        if state.get('Running') or state.get('ExitCode')==137: raise RuntimeError('Arrêt forcé ou incomplet : '+module['project'])


def transition(policy,ids,operation):
    core=policy['core']; changed=[]; maintenance=paused=False; touched=False
    try:
        # Stop signaling/admission before web/data, and TURN after signaling.
        for module in policy['modules']:
            changed.append(module); stop_module(module)
        maintenance=True
        compose(core,'exec','-T','-u','www-data','app','php','occ','maintenance:mode','--on')
        paused=True; run('docker','pause',ids['cron'])
        if not cron_idle(run('docker','top',ids['cron'],'-eo','pid,comm')): raise RuntimeError('Cron occupé : annulation')
        if http_active(ids['app']): raise RuntimeError('Connexion web encore active')
        touched=True; compose(core,'stop','--timeout','120','app')
        state=json.loads(run('docker','inspect','--format','{{json .State}}',ids['app']))
        if state.get('Running') or state.get('ExitCode')==137: raise RuntimeError('Arrêt app forcé')
        run('docker','unpause',ids['cron']); paused=False
        compose(core,'stop','--timeout','120','cron')
        state=json.loads(run('docker','inspect','--format','{{json .State}}',ids['cron']))
        if state.get('Running') or state.get('ExitCode')==137: raise RuntimeError('Arrêt cron forcé')
        compose(core,'run','--rm','--no-deps','-T','-u','www-data','--entrypoint','php','app','occ','maintenance:mode','--off')
        maintenance=False
        compose(core,'stop','--timeout','120','db','redis')
        for cid in ids.values():
            state=json.loads(run('docker','inspect','--format','{{json .State}}',cid))
            if state.get('Running') or state.get('ExitCode')==137: raise RuntimeError('Service arrêté de force')
        run('systemctl',operation)
    except Exception:
        # Best effort recovery must attempt every step, even if one fails.
        errors=[]
        recovery=[]
        if paused: recovery.append(lambda:run('docker','unpause',ids['cron']))
        if touched: recovery.append(lambda:compose(core,'start','db','redis','app'))
        if maintenance: recovery.append(lambda:compose(core,'exec','-T','-u','www-data','app','php','occ','maintenance:mode','--off'))
        if touched: recovery.append(lambda:compose(core,'start','cron'))
        for module in reversed(changed): recovery.append(lambda module=module:compose(module,'start'))
        for task in recovery:
            try: task()
            except Exception as error: errors.append(type(error).__name__)
        if errors: print('RECOVERY_INCOMPLETE : contrôle manuel requis',file=sys.stderr)
        raise


def main():
    parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group()
    group.add_argument('--poweroff',action='store_true'); group.add_argument('--reboot',action='store_true')
    parser.add_argument('--confirm-no-calls',action='store_true'); parser.add_argument('--start',action='store_true')
    args=parser.parse_args()
    if args.start and (args.poweroff or args.reboot): parser.error('Démarrage incompatible avec arrêt')
    if os.geteuid()!=0 or socket.gethostname()!=read_policy().get('hostname'): raise RuntimeError('Mauvaise cible')
    policy=read_policy()
    with open(read_policy()['lock_file'],'r') as lock:
        if args.start:
            for attempt in range(61):
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if attempt==60:raise RuntimeError('Verrou de sauvegarde encore occupé après 5 minutes')
                    time.sleep(5)
        else:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.start:
            start_registered(policy)
            print('START_READY : projets enregistrés démarrés'); return
        ids=preflight(policy,args.confirm_no_calls)
        if not (args.poweroff or args.reboot):
            print('CHECK_OK : aucun changement ; confirmation humaine et contrôles à refaire avant arrêt'); return
        transition(policy,ids,'reboot' if args.reboot else 'poweroff')
        print('POWER_REQUESTED : retour des services et état physique à vérifier')


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('REFUSED_OR_FAILED : '+str(error),file=sys.stderr); sys.exit(1)
