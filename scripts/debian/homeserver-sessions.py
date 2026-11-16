#!/usr/bin/python3
"""Explicit session disconnection and reversible preparation; no implicit poweroff."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import tempfile

STATE=Path('/var/lib/homeserver-monitoring/preparation.json')
SESSIONS_API_VERSION = 2
spec=importlib.util.spec_from_file_location('power','/usr/local/sbin/homeserver-power-v2.py')
# Defer platform-specific loading to main so tests can supply an isolated backend.
p=None


def ssh_sessions():
    current=p.run('loginctl','show-session','self','-p','Id','--value')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',current): raise RuntimeError('Session de contrôle non identifiée')
    sessions=json.loads(p.run('loginctl','list-sessions','--json=short','--no-pager'))
    others=[]
    for row in sessions:
        sid=str(row.get('session',''))
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',sid): raise RuntimeError('Identifiant de session inconnu')
        if sid==current: continue
        values=p.run('loginctl','show-session',sid,'-p','Service','-p','Remote','-p','Class','--no-pager')
        fields=dict(line.split('=',1) for line in values.splitlines() if '=' in line)
        if fields.get('Class') in ('manager','background','manager-early'): continue
        if fields.get('Remote')!='yes' or not (fields.get('Service','').startswith('sshd') or fields.get('Service')=='cockpit'):
            raise RuntimeError('Autre session non SSH : fermeture automatique refusée')
        others.append(sid)
    return current,others


def saved(policy,same_boot=False):
    if not STATE.exists(): raise RuntimeError('Préparation absente : préparer avant cette action')
    data=p.owned_preparation(policy)
    if same_boot and data.get('boot')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip(): raise RuntimeError('Préparation d’un autre démarrage : reprendre les services')
    if not isinstance(data.get('ids'),dict): raise RuntimeError('État incomplet')
    return data


def write_state(data):
    p.trusted(STATE.parent)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=STATE.parent,prefix='.preparation-',delete=False) as stream:
        temporary=Path(stream.name);json.dump(data,stream);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600);os.replace(temporary,STATE)
    fd=os.open(STATE.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def recover(policy):
    p.start_registered(policy)
    STATE.unlink(missing_ok=True)


def prepare(policy,disconnect=False):
    if not disconnect: raise RuntimeError('Confirmation explicite de déconnexion requise')
    if STATE.exists():
        data=saved(policy,same_boot=True)
        if data.get('phase')!='prepared':raise RuntimeError('Préparation incomplète : reprendre')
        p.common_checks(check_sessions=False)
        current,others=ssh_sessions()
        for sid in others:
            p.common_checks(check_sessions=False);p.run('loginctl','terminate-session',sid)
        data['time']=time.time();write_state(data)
        print('SESSIONS_PREPARED : préparation conservée, autres sessions refermées ; aucun nouvel arrêt applicatif')
        return
    p.common_checks(check_sessions=False)
    ids=p.containers(policy)
    current,others=ssh_sessions()
    status=json.loads(p.compose(policy['core'],'exec','-T','-u','www-data','app','php','occ','status','--output=json'))
    if not status.get('installed') or status.get('maintenance') or status.get('needsDbUpgrade'):raise RuntimeError('Nextcloud non prêt')
    if not p.cron_idle(p.run('docker','top',ids['cron'],'-eo','pid,comm')):raise RuntimeError('Cron occupé : aucune déconnexion')
    data={'schema':2,'owner':'homeserver-sessions','boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
          'policy':hashlib.sha256(p.POLICY.read_bytes()).hexdigest(),'configuration':p.configuration_hashes(policy),
          'version':status['versionstring'],'maintenance_owned':True,'time':time.time(),'ids':ids,'phase':'preparing'}
    write_state(data)
    paused=False
    try:
        p.compose(policy['core'],'exec','-T','-u','www-data','app','php','occ','maintenance:mode','--on')
        p.run('docker','pause',ids['cron']);paused=True
        if not p.cron_idle(p.run('docker','top',ids['cron'],'-eo','pid,comm')):raise RuntimeError('Cron devenu actif : annulation')
        # Session IDs are discovered server-side; never supplied by the browser.
        for sid in others:
            # Recheck protected work before terminating a session; never kill-user/root.
            p.common_checks(check_sessions=False)
            p.run('loginctl','terminate-session',sid)
        for module in policy['modules']:p.stop_module(module)
        # Graceful application stop drains HTTP requests; check for forced exits.
        p.compose(policy['core'],'stop','--timeout','120','app')
        app=json.loads(p.run('docker','inspect','--format','{{json .State}}',ids['app']))
        if app.get('Running') or app.get('ExitCode')==137:raise RuntimeError('Arrêt web non propre : reprise requise')
        p.run('docker','unpause',ids['cron']);paused=False
        p.compose(policy['core'],'stop','--timeout','120','cron')
        cron=json.loads(p.run('docker','inspect','--format','{{json .State}}',ids['cron']))
        if cron.get('Running') or cron.get('ExitCode')==137:raise RuntimeError('Arrêt cron non propre')
        # Never leave a persistent Nextcloud maintenance flag while waiting for a
        # second UI action. Web/cron stay stopped, database/cache remain running.
        p.compose(policy['core'],'run','--rm','--no-deps','-T','-u','www-data','--entrypoint','php','app','occ','maintenance:mode','--off')
        data.update(phase='prepared',maintenance_owned=False,time=time.time());write_state(data)
        print('SESSIONS_PREPARED : accès applicatifs arrêtés, autres SSH fermés, base/cache conservés ; reprendre ou éteindre explicitement')
    except Exception:
        if paused:
            try:p.run('docker','unpause',ids['cron'])
            except Exception:pass
        try:recover(policy)
        except Exception:print('RECOVERY_INCOMPLETE : reprise manuelle nécessaire',file=sys.stderr)
        raise


def check_prepared(policy):
    data=saved(policy,same_boot=True)
    if data.get('phase')!='prepared' or time.time()-data.get('time',0)>900:raise RuntimeError('Préparation incomplète ou trop ancienne : reprendre puis préparer de nouveau')
    p.common_checks()
    ids=data['ids'];expected={ids['db'],ids['redis']}
    if set(p.run('docker','ps','--no-trunc','-q').splitlines())!=expected:raise RuntimeError('Services modifiés depuis préparation : arrêt refusé')
    for name,cid in ids.items():
        state=json.loads(p.run('docker','inspect','--format','{{json .State}}',cid))
        if state.get('Paused') or state.get('ExitCode')==137 or bool(state.get('Running'))!=(name in ('db','redis')):raise RuntimeError('État imprévu : '+name)
    return ids


def finish(policy,operation):
    ids=check_prepared(policy)
    try:
        p.compose(policy['core'],'run','--rm','--no-deps','-T','-u','www-data','--entrypoint','php','app','occ','maintenance:mode','--off')
        p.compose(policy['core'],'stop','--timeout','120','db','redis')
        for name in ('db','redis'):
            state=json.loads(p.run('docker','inspect','--format','{{json .State}}',ids[name]))
            if state.get('Running') or state.get('ExitCode')==137:raise RuntimeError('Arrêt des données non propre')
        p.run('systemctl',operation)
        print('POWER_REQUESTED : extinction/reprise physique à vérifier')
    except Exception:
        recover(policy);raise


def main():
    global p
    parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
    for name in ('prepare','resume','poweroff','reboot','status','check','recover-expired'):group.add_argument('--'+name,action='store_true')
    parser.add_argument('--disconnect-clients',action='store_true');parser.add_argument('--confirm-no-calls',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0 or socket.gethostname()!=p.read_policy().get('hostname'):raise RuntimeError('Mauvaise cible')
    p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
    policy=p.read_policy()
    with open(p.read_policy()['lock_file'],'r') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            if args.recover_expired:print('RECOVERY_DEFERRED : opération protégée active');return
            raise RuntimeError('Verrou de sauvegarde occupé : réessayer après sa fin')
        if args.status:print(json.dumps({'prepared':STATE.exists()}));return
        if args.recover_expired:
            if not STATE.exists():return
            data=saved(policy)
            if data.get('boot')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip() or time.time()-data.get('time',0)>900:
                p.common_checks(check_sessions=False,ignore_job_units=('homeserver-preparation-recovery.service',));recover(policy);print('EXPIRED_PREPARATION_RESUMED : services rétablis, aucune extinction')
            return
        if args.check:
            if STATE.exists():check_prepared(policy)
            else:p.preflight(policy,args.confirm_no_calls)
            print('CHECK_OK : état courant contrôlé, aucune modification');return
        if args.prepare:prepare(policy,args.disconnect_clients)
        elif args.resume:saved(policy);recover(policy);print('SERVICES_RESUMED')
        elif STATE.exists():finish(policy,'reboot' if args.reboot else 'poweroff')
        else:p.transition(policy,p.preflight(policy,args.confirm_no_calls),'reboot' if args.reboot else 'poweroff')


if __name__=='__main__':
    try:main()
    except Exception as error:print('SESSIONS_REFUSED : '+str(error),file=sys.stderr);sys.exit(1)
