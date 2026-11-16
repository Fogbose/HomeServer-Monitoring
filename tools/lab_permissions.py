"""Explicit Linux-only fixture ownership, never a deployment permissions repair."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'private/monitoring/sso-lab'
if os.name!='posix' or os.geteuid()!=0:
    raise SystemExit('Run explicitly with sudo on Linux, for synthetic lab fixtures only')
if target.is_symlink() or not target.resolve().is_relative_to(ROOT.resolve()):
    raise SystemExit('Unsafe fixture path')
profile=json.loads((target/'profile.json').read_text())
identities=json.loads((target/'identities.json').read_text())
if profile.get('name')!='server-profile' or set(identities.get('users',{}))!={'lab-admin','lab-viewer'}:
    raise SystemExit('Expected synthetic server-profile fixture only')
members=[target/'data/healthcheck.env',target/'config/tls/node.key']
for directory in ['data/authelia','data/beszel','data/olivetin']:
    folder=target/directory
    members += [folder,*folder.rglob('*')]
for item in members:
    if item.is_symlink() or not item.resolve().is_relative_to(target.resolve()):
        raise SystemExit('Unsafe fixture member; no ownership changes applied')
for directory in ['data/authelia','data/beszel','data/olivetin']:
    folder=target/directory
    for item in [folder,*folder.rglob('*')]:
        if item.is_symlink():
            raise SystemExit('Symlink refused')
        os.chown(item,65532,65532)
        os.chmod(item,0o700 if item.is_dir() else 0o600)
os.chown(target/'data/healthcheck.env',65532,65532)
os.chmod(target/'data/healthcheck.env',0o600)
os.chown(target/'config/tls/node.key',0,101)
os.chmod(target/'config/tls/node.key',0o640)
print('LAB_OWNERSHIP_READY: fixed synthetic data and proxy key only; no system account/trust changes')
