"""Create a private, disabled configuration without touching keys or hosts."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
target=ROOT/'private/monitoring/site.json'
if target.exists():
    raise SystemExit('Configuration exists; edit it explicitly, never overwrite')
target.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(ROOT/'config/site.example.json',target)
print('Private example created. Replace placeholders; verify SSH keys separately. Actions remain disabled.')
