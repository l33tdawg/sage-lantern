#!/usr/bin/env python3
"""Install a pre-uploaded SAGE static release and add exactly one TLS route."""
from pathlib import Path
import os, shutil, subprocess, sys, time
release=Path(sys.argv[1]).resolve()
base=Path('/var/www/sage-website')
if release.parent != base / 'releases' or not (release/'index.html').is_file():
    raise SystemExit('Unexpected or incomplete release')
config=Path('/etc/nginx/conf.d/30-sage-website.conf')
sni=Path('/etc/nginx/stream-conf.d/10-sage-sni.conf')
current=base/'current'
backup=base/('backup-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
backup.mkdir(parents=True)
for p in (config,sni): shutil.copy2(p,backup/p.name)
prior=os.readlink(current) if current.is_symlink() else None
if current.exists() and not current.is_symlink(): raise SystemExit('Current path is not a symlink')
text=sni.read_text()
route='    sage.ngo    127.0.0.1:9444;\n'
if 'sage.ngo' not in text:
    marker='    default                 127.0.0.1:9;'
    if text.count(marker)!=1: raise SystemExit('Unexpected SNI map; refusing broad change')
    text=text.replace(marker,route+marker)
elif route.strip() not in text:
    raise SystemExit('Natter route already exists with a different target')
try:
    shutil.copyfile('/tmp/sage-website-final.conf',config)
    sni.write_text(text)
    stage=base/'current.next'
    if stage.is_symlink(): stage.unlink()
    stage.symlink_to(release)
    os.replace(stage,current)
    subprocess.run(['nginx','-t'],check=True)
    subprocess.run(['systemctl','reload','nginx'],check=True)
except BaseException:
    for p in (config,sni): shutil.copy2(backup/p.name,p)
    if current.is_symlink(): current.unlink()
    if prior: current.symlink_to(prior)
    subprocess.run(['nginx','-t'],check=True)
    subprocess.run(['systemctl','reload','nginx'],check=True)
    raise
print('Activated',release.name)
print('Rollback configuration:',backup)
