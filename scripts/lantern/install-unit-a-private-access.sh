#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 || $(uname -m) != aarch64 ]]; then
  echo 'Run on the provisioned ARM64 device with sudo.' >&2
  exit 1
fi
if [[ $# -lt 2 || $1 != --activate ]]; then
  echo 'Usage: install-unit-a-private-access.sh --activate /absolute/prebuilt-app-stage [--clear-test-history]' >&2
  exit 1
fi
readonly stage=$2
readonly clear_history=${3:-}
[[ $# -le 3 && ( -z "$clear_history" || "$clear_history" == --clear-test-history ) ]]
readonly repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly python=/opt/sage-lantern/venv/bin/python
readonly app=/home/radxa/sage-commons/apps/lantern-fleet-admin
readonly config=/etc/sage-lantern/lantern.toml
readonly marker=/etc/sage-lantern-device-lock.enabled
readonly setup_marker=/etc/sage-lantern-vault-setup.enabled
readonly stamp="$(date -u +%Y%m%dT%H%M%SZ)"
readonly backup="/opt/sage-lantern/before-private-access-${stamp}"
readonly runtime="$($python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')/sage_lantern"
test -x "$python"
test -d "$runtime"
test -f "$config"
test -f "$stage/dist/server/index.js"
test -d "$stage/node_modules"
test -d "$app"
test ! -e "$backup"
test ! -e "$marker"
test ! -e "$setup_marker"
test ! -e /etc/systemd/system/sage-lantern-fleet-admin.service.d/90-device-lock.conf
test ! -e /etc/systemd/system/sage-lantern-voice-loop.service.d/90-conversation-storage.conf
for service in sage-lantern-llm sage-lantern-personal-sage; do
  systemctl is-active --quiet "$service"
done
"$python" - "$stage" "$app" <<'PY'
from pathlib import Path
import sys
stage, app = map(Path, sys.argv[1:])
if not stage.is_absolute() or stage.resolve() != stage or stage == app or app in stage.parents or stage in app.parents:
    raise SystemExit('Use a separate absolute prebuilt stage without symlinks.')
for entry in stage.rglob('*'):
    if entry.is_symlink() and not entry.resolve().is_relative_to(stage):
        raise SystemExit('App stage contains an external symlink.')
PY
"$python" - <<'PY'
from pathlib import Path
import json
path = Path('/run/sage-lantern-voice/state.json')
if not path.is_file() or json.loads(path.read_text()).get('phase') in {'listening', 'processing'}:
    raise SystemExit('Wait for the active voice operation to finish before activation.')
PY
needs_memory=$($python - "$config" <<'PY'
from pathlib import Path
import sys, tomllib
value = tomllib.loads(Path(sys.argv[1]).read_text()).get('sage', {}).get('conversation_dir')
if value not in (None, '/var/lib/sage-lantern/conversations-v1'):
    raise SystemExit('Review the existing conversation directory before activation.')
print('yes' if value is None else 'no')
PY
)
if [[ $needs_memory == yes && $clear_history != --clear-test-history ]]; then
  echo 'Initial SAGE history cutover needs explicit --clear-test-history authorization.' >&2
  exit 1
fi
install -d -m 0700 "$backup"
install -d -m 0700 "$backup/release"
"$python" - "$repo" "$backup/release" "$runtime" <<'PY'
from pathlib import Path
import hashlib, json, os, shutil, stat, sys
repo, frozen, runtime = map(Path, sys.argv[1:])
files = list((repo / 'software/runtime/src/sage_lantern').glob('*.py'))
files += [repo / f'scripts/lantern/unit-a-{name}.py' for name in ('voice-loop', 'camera-server', 'status-server', 'device-auth')]
files += [repo / f'software/systemd/sage-lantern-{name}.service' for name in ('camera', 'status', 'device-auth')]
required = {'device_access.py', 'device_lock.py', 'device_profile.py', 'vault_setup.py', 'history_media.py', 'conversation_context.py', 'conversation_sage.py', 'conversation_store.py', 'conversation_service.py'}
if not required.issubset({path.name for path in files}):
    raise SystemExit('Incomplete runtime source inventory.')
manifest = {}
for source in files:
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or source.resolve() != source:
        raise SystemExit('Unsafe release source.')
    relative = source.relative_to(repo)
    target = frozen / relative
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
    manifest[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
for target in runtime.glob('*.py'):
    if target.is_symlink():
        raise SystemExit('Runtime destination contains a symlink.')
with (frozen / 'manifest.json').open('x') as stream:
    json.dump(manifest, stream, sort_keys=True)
    stream.flush()
    os.fsync(stream.fileno())
PY
readonly frozen="$backup/release"
freeze_app() {
  "$python" - "$1" "$2" "$3" <<'FREEZE_APP_PY'
from pathlib import Path
import hashlib, json, os, shutil, stat, sys
source, target, manifest_path = map(Path, sys.argv[1:])
if source.resolve() != source or target.exists() or target.is_symlink():
    raise SystemExit('Unsafe application artifact path.')
target.mkdir(mode=0o700)
manifest = {}
for current, directories, files, source_fd in os.fwalk(source, follow_symlinks=False):
    relative = Path(current).relative_to(source)
    destination = target / relative
    destination.mkdir(mode=0o755, exist_ok=True)
    os.chmod(destination, 0o755)
    manifest[str(relative)] = {'kind': 'directory', 'mode': 0o755}
    for name in directories + files:
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        output = destination / name
        key = str(relative / name)
        if stat.S_ISLNK(metadata.st_mode):
            resolved = (Path(current) / name).resolve(strict=True)
            if not resolved.is_relative_to(source):
                raise SystemExit('External application symlink.')
            link = os.path.relpath(target / resolved.relative_to(source), destination)
            output.symlink_to(link)
            manifest[key] = {'kind': 'symlink', 'target': link}
        elif stat.S_ISDIR(metadata.st_mode):
            continue
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
            with os.fdopen(descriptor, 'rb') as reader:
                opened = os.fstat(reader.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_nlink) != (
                        metadata.st_dev, metadata.st_ino, metadata.st_mode, 1):
                    raise SystemExit('Application source changed during freeze.')
                mode = 0o755 if opened.st_mode & 0o111 else 0o644
                with output.open('xb') as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fchmod(writer.fileno(), mode)
                    os.fsync(writer.fileno())
            manifest[key] = {'kind': 'file', 'mode': mode,
                             'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}
        else:
            raise SystemExit('Unsafe application file type or hard link.')
for current, directories, files in os.walk(target, topdown=False, followlinks=False):
    descriptor = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
with manifest_path.open('x') as stream:
    os.fchmod(stream.fileno(), 0o600)
    json.dump(manifest, stream, sort_keys=True)
    stream.flush()
    os.fsync(stream.fileno())
for parent in {target.parent, manifest_path.parent}:
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
FREEZE_APP_PY
}
freeze_app "$stage" "$backup/app-frozen" "$backup/app-manifest.json"

durable_containment() {
  "$python" - /etc/systemd/system "${private_services[@]}" sage-lantern-device-auth <<'CONTAIN_PY'
from pathlib import Path
import os, sys, uuid
directory = Path(sys.argv[1])
if directory.resolve() != directory:
    raise SystemExit('Unsafe unit directory.')
descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for service in sys.argv[2:]:
        temporary = '.lantern-mask-' + str(uuid.uuid4())
        try:
            os.symlink('/dev/null', temporary, dir_fd=descriptor)
            os.replace(temporary, service + '.service', src_dir_fd=descriptor, dst_dir_fd=descriptor)
            os.fsync(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
finally:
    os.close(descriptor)
CONTAIN_PY
}

durable_tree() {
  "$python" - "$@" <<'SYNC_TREE_PY'
from pathlib import Path
import os, stat, sys
def sync(path):
    if path.is_symlink():
        return
    metadata = path.stat()
    if stat.S_ISDIR(metadata.st_mode):
        for child in path.iterdir():
            sync(child)
    elif not stat.S_ISREG(metadata.st_mode):
        raise SystemExit('Unsafe durability target.')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if path.is_dir() else 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
for name in sys.argv[1:]:
    path = Path(name)
    sync(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
SYNC_TREE_PY
}
history_snapshot=$($python - <<'PY'
from pathlib import Path
import hashlib
path = Path('/var/lib/sage-lantern/history/history.json')
print(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'absent')
PY
)
cp -a "$runtime" "$backup/runtime"
cp -a "$config" "$backup/lantern.toml"
for name in voice-loop camera-server status-server; do
  cp -a "/usr/local/libexec/sage-lantern-${name}" "$backup/${name}"
done
for name in voice-loop camera status fleet-admin; do
  fragment=$(systemctl show "sage-lantern-${name}" -p FragmentPath --value)
  test -f "$fragment"
  cp -L "$fragment" "$backup/${name}.service"
done
private_services=(sage-lantern-fleet-admin sage-lantern-voice-loop sage-lantern-camera sage-lantern-status)
failed() {
  trap - ERR INT TERM
  local contained=yes
  durable_containment || contained=no
  systemctl daemon-reload || contained=no
  systemctl stop "${private_services[@]}" sage-lantern-device-auth || contained=no
  for service in "${private_services[@]}" sage-lantern-device-auth; do
    case "$(systemctl show "$service" -p ActiveState --value 2>/dev/null)" in
      inactive|failed) ;;
      *) contained=no; echo "Containment not confirmed for $service" >&2 ;;
    esac
  done
  echo "Activation failed. Containment confirmed: $contained. Code backup: $backup" >&2
  echo "App paths: $app ; $app.before-private-access-${stamp} ; $stage" >&2
  echo 'Do not remove access gates or restore an unguarded server as automatic rollback.' >&2
  exit 1
}
trap failed ERR INT TERM
durable_tree "$backup"
durable_containment
systemctl daemon-reload
systemctl stop "${private_services[@]}" sage-lantern-device-auth
for file in "$frozen"/software/runtime/src/sage_lantern/*.py; do
  install -o root -g root -m 0644 "$file" "$runtime/$(basename "$file")"
done
for name in voice-loop camera-server status-server device-auth; do
  test ! -L "/usr/local/libexec/sage-lantern-${name}"
  install -o root -g root -m 0755 "$frozen/scripts/lantern/unit-a-${name}.py" "/usr/local/libexec/sage-lantern-${name}"
done
runuser -u sage-lantern -- "$python" - <<'PY'
from pathlib import Path
from sage_lantern.vault_setup import RootFileSigner, VaultSetup
vault = VaultSetup(RootFileSigner(Path('/var/lib/sage-lantern/sage-personal/agent.key')), enable_allowed=lambda: False)
status = vault.status()
if status['has_existing_vault'] and not status['enabled']:
    raise SystemExit('Existing disabled vault requires operator recovery; no key will be replaced.')
print('Current operator can inspect the vault; no encryption or PIN change made.')
PY
if [[ $needs_memory == yes ]]; then
  runuser -u sage-lantern -- "$python" - "$history_snapshot" <<'PY'
from pathlib import Path
import hashlib, importlib.util, sys
path = Path('/var/lib/sage-lantern/history/history.json')
actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'absent'
if actual != sys.argv[1]:
    raise SystemExit('History changed during preparation; preserve it and review before cutover.')
spec = importlib.util.spec_from_file_location('lantern_history_cutover', '/usr/local/libexec/sage-lantern-voice-loop')
if spec is None:
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader('lantern_history_cutover', SourceFileLoader('lantern_history_cutover', '/usr/local/libexec/sage-lantern-voice-loop'))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
result = module.LocalHistory(path.parent).request('clear', {})
if result['items'] != []:
    raise SystemExit('History clear did not complete.')
PY
fi
install -d -o sage-lantern -g sage-lantern -m 0700 /var/lib/sage-lantern/conversations-v1
install -d -o root -g root -m 0755 /etc/systemd/system/sage-lantern-voice-loop.service.d /etc/systemd/system/sage-lantern-fleet-admin.service.d
if [[ $needs_memory == yes ]]; then
  "$python" - "$config" <<'PY'
from pathlib import Path
import os, sys, tempfile
path = Path(sys.argv[1])
content = path.read_text()
if content.count('[sage]') != 1 or 'conversation_dir' in content:
    raise SystemExit('Unexpected runtime config; refuse automatic edit.')
content = content.replace('[sage]', '[sage]\nconversation_dir = "/var/lib/sage-lantern/conversations-v1"')
info = path.stat()
descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.private-access-')
try:
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), info.st_mode & 0o777)
        os.fchown(stream.fileno(), info.st_uid, info.st_gid)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    Path(temporary).unlink(missing_ok=True)
PY
fi
"$python" - <<'PY'
from pathlib import Path
import os, tempfile
files = {
    '/etc/sage-lantern-device-lock.enabled': '1\n',
    '/etc/sage-lantern-vault-setup.enabled': 'allow-vault-initialize\n',
    '/etc/systemd/system/sage-lantern-voice-loop.service.d/90-conversation-storage.conf': '[Service]\nReadWritePaths=/var/lib/sage-lantern/conversations-v1\n',
    '/etc/systemd/system/sage-lantern-fleet-admin.service.d/90-device-lock.conf': '[Service]\nEnvironment=LANTERN_DEVICE_LOCK=1\n',
}
for filename, content in files.items():
    path = Path(filename)
    if path.exists() or path.is_symlink() or path.parent.resolve() != path.parent:
        raise SystemExit('Unexpected access-policy destination.')
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.lantern-policy-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
PY
readonly candidate="$app.private-access-${stamp}"
freeze_app "$backup/app-frozen" "$candidate" "$backup/app-installed-manifest.json"
cmp "$backup/app-manifest.json" "$backup/app-installed-manifest.json"
test ! -e "$app.before-private-access-${stamp}"
test ! -L "$app.before-private-access-${stamp}"
"$python" - "$app" "$candidate" "$app.before-private-access-${stamp}" <<'SWAP_APP_PY'
from pathlib import Path
import os, sys
app, candidate, previous = map(Path, sys.argv[1:])
if app.resolve() != app or candidate.resolve() != candidate:
    raise SystemExit('Unsafe app replacement path.')
descriptor = os.open(app.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.rename(app, previous)
    os.fsync(descriptor)
    os.rename(candidate, app)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
SWAP_APP_PY
durable_tree "$runtime" "$backup" "$config" /var/lib/sage-lantern/conversations-v1
for name in voice-loop camera-server status-server device-auth; do
  durable_tree "/usr/local/libexec/sage-lantern-${name}"
done
install -d -m 0700 "$backup/ready-units"
for name in voice-loop fleet-admin; do
  install -o root -g root -m 0644 "$backup/${name}.service" "$backup/ready-units/sage-lantern-${name}.service"
done
for name in camera status device-auth; do
  install -o root -g root -m 0644 "$frozen/software/systemd/sage-lantern-${name}.service" "$backup/ready-units/sage-lantern-${name}.service"
done
release_containment() {
  "$python" - "$backup/ready-units" /etc/systemd/system <<'RELEASE_PY'
from pathlib import Path
import os, sys, tempfile
source, target = map(Path, sys.argv[1:])
directory = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for unit in sorted(source.glob('*.service')):
        destination = target / unit.name
        if not destination.is_symlink() or os.readlink(destination) != '/dev/null':
            raise SystemExit('Persistent containment changed unexpectedly.')
        descriptor, temporary = tempfile.mkstemp(dir=target, prefix='.lantern-unit-')
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(unit.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            os.fsync(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
finally:
    os.close(directory)
RELEASE_PY
}
durable_tree "$backup/ready-units"
runuser -u sage-lantern -- "$python" - /usr/local/libexec/sage-lantern-device-auth "$setup_marker" <<'MARKER_CHECK_PY'
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys
loader = SourceFileLoader('lantern_installed_setup_check', sys.argv[1])
spec = spec_from_loader(loader.name, loader)
module = module_from_spec(spec)
sys.modules[spec.name] = module
loader.exec_module(module)
if module.initialization_allowed(Path(sys.argv[2])) is not True:
    raise SystemExit('Installed auth daemon cannot read the setup authorization marker.')
MARKER_CHECK_PY
release_containment
systemctl daemon-reload
systemctl enable sage-lantern-device-auth
systemctl start sage-lantern-device-auth
systemctl start "${private_services[@]}"
for attempt in {1..30}; do
  if curl -fsS --max-time 2 http://127.0.0.1:3196/state >/dev/null && curl -fsS --max-time 2 http://127.0.0.1:3100/device >/dev/null; then
    for service in sage-lantern-device-auth "${private_services[@]}"; do
      systemctl is-active --quiet "$service"
    done
    "$python" - <<'SETUP_SMOKE_PY'
import http.client, json
def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate setup field.')
        result[key] = value
    return result
connection = http.client.HTTPConnection('127.0.0.1', 3196, timeout=3)
try:
    connection.request('POST', '/setup/status', body=b'{}', headers={
        'Origin': 'http://127.0.0.1:3100', 'Content-Type': 'application/json',
        'X-Lantern-Auth': 'control', 'Connection': 'close',
    })
    response = connection.getresponse()
    raw = response.read(2049)
    if response.status != 200 or len(raw) > 2048:
        raise SystemExit('Setup status unavailable.')
    value = json.loads(raw, object_pairs_hook=unique_object)
    fields = {'enabled', 'recovery_backup_confirmed', 'has_existing_vault', 'can_initialize'}
    if type(value) is not dict or set(value) != fields or any(type(value[field]) is not bool for field in fields):
        raise SystemExit('Invalid setup status fields.')
    if ((value['enabled'] and not value['has_existing_vault'])
            or (value['recovery_backup_confirmed'] and not value['enabled'])
            or (value['can_initialize'] and (value['enabled'] or value['has_existing_vault']))):
        raise SystemExit('Inconsistent setup status.')
    if not value['enabled'] and not value['has_existing_vault'] and not value['can_initialize']:
        raise SystemExit('Fresh device setup authorization unavailable.')
finally:
    connection.close()
print('Read-only setup status verified; no vault or PIN change made.')
SETUP_SMOKE_PY
    "$python" - <<'PRIVATE_SMOKE_PY'
import http.client, json

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate state field.')
        result[key] = value
    return result

def invalid_constant(value):
    raise ValueError('Invalid JSON constant.')

def verify(port, method, path, expected_status, *, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request(method, path, body=body, headers={'Connection': 'close', **(headers or {})})
        response = connection.getresponse()
        response_headers = response.getheaders()
        if response.status != expected_status or any(name.lower() == 'location' for name, value in response_headers):
            raise ValueError('Unexpected direct response.')
        if expected_status == 200:
            types = [value.split(';', 1)[0].strip().lower() for name, value in response_headers
                     if name.lower() == 'content-type']
            if types != ['application/json']:
                raise ValueError('Unexpected state content type.')
            raw = response.read(257)
            if len(raw) > 256:
                raise ValueError('State response too large.')
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                               parse_constant=invalid_constant)
            if type(value) is not dict or set(value) != {'locked'} or type(value['locked']) is not bool or value['locked'] is not True:
                raise ValueError('Expected exact locked state.')
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        raise SystemExit('Direct private-access verification failed; activation remains unverified.') from None
    finally:
        connection.close()

verify(3196, 'GET', '/state', 200)
verify(3199, 'GET', '/status', 403)
verify(3197, 'POST', '/history/list', 403, body=b'{}', headers={
    'Origin': 'http://127.0.0.1:3100', 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control',
})
verify(3198, 'POST', '/preview', 403, body=b'', headers={
    'Origin': 'http://127.0.0.1:3100', 'X-Lantern-Camera': 'preview',
})
print('Locked state and direct private-route denial verified.')
PRIVATE_SMOKE_PY
    trap - ERR INT TERM
    echo 'Private access installed. Owner PIN setup and physical acceptance remain required.'
    exit 0
  fi
  sleep 1
done
false
