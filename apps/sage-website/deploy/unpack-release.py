from pathlib import Path
import hashlib,json,tarfile
manifest=json.loads(Path('/tmp/release-manifest.json').read_text())
release=manifest['release']
if not release.startswith('20260909-') or '/' in release: raise SystemExit('Invalid release')
root=Path('/var/www/sage-website/releases')/release
root.mkdir(parents=True,exist_ok=False)
with tarfile.open('/tmp/sage-website-release.tar.gz') as archive:
    names={m.name for m in archive.getmembers()}
    if names!=set(manifest['files']): raise SystemExit('Unexpected archive contents')
    for member in archive.getmembers():
        if not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts: raise SystemExit('Unsafe member')
    archive.extractall(root,filter='data')
for name,digest in manifest['files'].items():
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:raise SystemExit('Digest mismatch '+name)
for p in root.rglob('*'):p.chmod(0o755 if p.is_dir() else 0o644)
root.chmod(0o755)
print('Verified',len(manifest['files']),'files:',root)
