"""Scan tracked/unignored files for likely secrets. Findings never print matched values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'API token': re.compile(rb'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}'),
    'GitHub token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential URL': re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),
    'personal path': re.compile(rb'/(?:Users|home)/[A-Za-z0-9_-]+/'),
}


def main():
    names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).split(b'\0')
    failures=[];count=0
    for name in sorted(set(n for n in names if n)):
        relative=Path(name.decode());path=ROOT/relative
        if not path.is_file():continue
        count+=1
        if path.is_symlink():failures.append((str(relative),'symlink'));continue
        if relative.parts[0] in {'reports','output','logs'} or path.suffix in {'.log','.db','.sqlite','.sqlite3','.pem','.key'} or (path.name.startswith('.env') and path.name!='.env.example') or (relative.parts[0]=='config' and path.name!='model.example.json'):
            failures.append((str(relative),'private runtime/config file'))
        data=path.read_bytes()
        for label,pattern in PATTERNS.items():
            if pattern.search(data):failures.append((str(relative),label))
        if path.name=='.env.example':
            for line in data.decode().splitlines():
                if line.strip() and not line.lstrip().startswith('#') and line.partition('=')[2].strip():
                    failures.append((str(relative),'nonempty example value'))
    for filename,kind in failures:print(f'{filename}: {kind} (value hidden)')
    print(f'Checked {count} files; findings: {len(failures)}')
    return bool(failures)


if __name__=='__main__':sys.exit(main())
