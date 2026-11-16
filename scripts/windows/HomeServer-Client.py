"""Pinned SSH client construction only. No key discovery or provisioning."""
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]


def ssh_command():
    site=json.loads((ROOT/'private/monitoring/site.json').read_text(encoding='utf-8'))
    ssh=site['ssh']
    for field in ['host','host_key_alias']:
        value=ssh[field]
        if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]*',value) or value.endswith('.invalid'):
            raise ValueError('Configure a valid SSH '+field)
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*',ssh['user']):
        raise ValueError('Invalid SSH user')
    identity=(ROOT/ssh['identity_file']).resolve()
    known=(ROOT/ssh['known_hosts_file']).resolve()
    if not identity.is_file() or not known.is_file():
        raise ValueError('Supply an existing identity and independently verified known_hosts file')
    return ['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10',
            '-o','StrictHostKeyChecking=yes','-o','HostKeyAlias='+ssh['host_key_alias'],
            '-o','UserKnownHostsFile='+str(known),'-o','GlobalKnownHostsFile='+('NUL' if __import__('os').name=='nt' else '/dev/null'),
            '-i',str(identity),ssh['user']+'@'+ssh['host']]
