"""Read-only Linux telemetry. No service changes or disk wake-up/SMART tests."""
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.request


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        return {'unknown': True, 'exit': result.returncode}
    return result.stdout.strip()


def collect(privileged=False):
    result = {'schema': 1, 'observed': time.time(), 'hostname': socket.gethostname(),
              'capabilities': {'privileged': privileged}, 'errors': []}
    try:
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        total = int(memory['MemTotal'].split()[0])
        available = int(memory['MemAvailable'].split()[0])
        swap = int(memory['SwapTotal'].split()[0]); swapfree = int(memory['SwapFree'].split()[0])
        cpu = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        usage = shutil.disk_usage('/srv')
        fs = os.statvfs('/srv')
        result.update(memory_percent=round(100*(1-available/total), 1),
                      swap_percent=round(100*(1-swapfree/swap), 1) if swap else 0,
                      cpu_ticks=sum(cpu), cpu_idle=cpu[3]+cpu[4],
                      load=os.getloadavg(), disk_percent=round(100*usage.used/usage.total, 1),
                      disk_free_gib=round(usage.free/2**30, 1),
                      inode_percent=round(100*(1-fs.f_favail/fs.f_files), 1) if fs.f_files else None,
                      uptime_seconds=float(Path('/proc/uptime').read_text().split()[0]),
                      boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        network = {}
        for row in Path('/proc/net/dev').read_text().splitlines()[2:]:
            interface, counters = row.split(':'); values = counters.split()
            if interface.strip() != 'lo':
                network[interface.strip()] = {'rx': int(values[0]), 'tx': int(values[8])}
        result['network'] = network
        result['raid'] = Path('/proc/mdstat').read_text() if Path('/proc/mdstat').exists() else None
    except Exception as error:
        result['errors'].append(type(error).__name__)
    result['units'] = {}
    for unit in ('homeserver-nextcloud-backup.service', 'homeserver-nextcloud-backup.timer',
                 'homeserver-hpb-cert.service', 'homeserver-hpb-cert.timer'):
        value = command('systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState',
                        '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainExitTimestamp')
        result['units'][unit] = dict(line.split('=', 1) for line in value.splitlines() if '=' in line) if isinstance(value, str) else value
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/status.php', timeout=8) as response:
            status = json.load(response)
        result['nextcloud'] = {k: status.get(k) for k in ('installed','maintenance','needsDbUpgrade','versionstring')}
    except Exception:
        result['nextcloud'] = {'unknown': True}
    result['failed_units'] = command('systemctl', '--failed', '--no-legend', '--no-pager', '--plain')
    if privileged:
        result['containers'] = command('docker', 'ps', '-a', '--format', '{{json .}}')
        result['container_stats'] = command('docker', 'stats', '--no-stream', '--format', '{{json .}}')
        # Only an allowlisted summary is exported; never docker inspect Env or logs.
        result['power_version'] = '2' if Path('/usr/local/sbin/homeserver-power-v2.py').is_file() else None
        revision=Path('/etc/homeserver-monitoring-revision.json')
        result['sessions_version'] = str(json.loads(revision.read_text()).get('sessions',1)) if revision.is_file() else ('1' if Path('/usr/local/sbin/homeserver-sessions.py').is_file() else None)
        preparation=Path('/var/lib/homeserver-monitoring/preparation.json')
        prepared=json.loads(preparation.read_text()) if preparation.is_file() else {}
        result['power_prepared']=prepared.get('phase')=='prepared'
        result['power_phase']=prepared.get('phase')
        result['power_resume_at']=prepared.get('time',0)+900 if prepared else None
        result['cockpit_available']=command('systemctl','is-active','cockpit.socket')=='active'
        result['beszel_agent_active']=command('systemctl','is-active','homeserver-beszel-agent.service')=='active'
        pointer = Path('/srv/backups/nextcloud/LATEST')
        try:
            name = pointer.read_text().strip()
            if not name.startswith('capture.') or '/' in name or '\\' in name:
                raise ValueError('Invalid capture pointer')
            result['capture_epoch'] = int((pointer.parent/name/'created-epoch').read_text())
        except Exception:
            result['capture_epoch'] = None
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--cache-group', help='Existing group explicitly allowed to read the root cache')
    args = parser.parse_args()
    if args.cache and not args.cache_group: parser.error('--cache requires --cache-group')
    data = collect(privileged=os.geteuid() == 0)
    if args.cache:
        if os.geteuid() != 0 or args.cache != Path('/var/lib/homeserver-monitoring/status.json'):
            raise SystemExit('Unexpected cache destination or user')
        import grp
        args.cache.parent.mkdir(mode=0o750, exist_ok=True)
        temporary = args.cache.with_suffix('.new')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.chown(temporary, 0, grp.getgrnam(args.cache_group).gr_gid)
        os.chmod(temporary, 0o640)
        os.chown(args.cache.parent, 0, grp.getgrnam(args.cache_group).gr_gid)
        os.replace(temporary, args.cache)
    else:
        print(json.dumps(data))
