#!/usr/bin/env python3
"""Start the local Splunk lab and index one events CSV into index=card_testing_lab.

    python3 splunk/start_lab.py                                   # bundled baseline
    python3 splunk/start_lab.py --csv ~/Downloads/card_testing_events.csv

Safe to re-run. The container is the source of truth: the CSV is copied in once,
and a different CSV is refused instead of being mixed into the same index.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import secrets
import shlex
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / 'splunk' / 'app'
COMPOSE_FILE = ROOT / 'splunk' / 'compose.yaml'
ENV_FILE = ROOT / '.lab-state' / 'splunk.env'
DEFAULT_CSV = ROOT / 'data' / 'card_testing_events.csv'
CONTAINER = 'card-testing-splunk'
APP_PATH = '/opt/splunk/etc/apps/card_testing_lab'
CSV_IN_APP = 'data/card_testing_events.csv'  # monitored by app/local/inputs.conf
CSV_HEADER = 'event_id,ts,epoch,'
LOW_DISK_MB = 2000  # Splunk refuses to search below 1,000 MB free (app/local/server.conf)
RESET_COMMAND = 'docker compose --env-file .lab-state/splunk.env -f splunk/compose.yaml down -v'


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(['docker', *args], capture_output=True, text=True)


def docker_or_exit(*args: str, stdin: bytes | None = None) -> None:
    result = subprocess.run(['docker', *args], input=stdin, capture_output=True)
    if result.returncode:
        raise SystemExit(f'docker {args[0]} failed: {result.stderr.decode().strip()}')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_env_file() -> None:
    """The image needs a bootstrap admin password. Splunk Free itself has no login."""
    if ENV_FILE.exists():
        return
    ENV_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    ENV_FILE.write_text(f'CARD_LAB_SPLUNK_PASSWORD=Lab9!{secrets.token_urlsafe(24)}\n')
    ENV_FILE.chmod(0o600)


def wait_until_healthy(timeout_s: int = 1200) -> None:
    started, next_note = time.monotonic(), 60
    while True:
        state = docker('inspect', '--format',
                       '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}', CONTAINER)
        status, _, health = state.stdout.strip().partition(' ')
        if health == 'healthy':
            return
        if state.returncode or status not in ('created', 'running', 'restarting'):
            raise SystemExit(f'Splunk container is {status or "missing"}. See: docker logs {CONTAINER}')
        elapsed = time.monotonic() - started
        if elapsed > timeout_s:
            raise SystemExit(f'Splunk was not healthy after {timeout_s // 60} minutes. See: docker logs {CONTAINER}')
        if elapsed >= next_note:
            print(f'  still starting ({int(elapsed) // 60} min)...', flush=True)
            next_note += 60
        time.sleep(5)


def hashes_in_container(names: list[str]) -> dict[str, str]:
    """sha256 of each app file already in the container. Missing files are omitted.

    Runs as root: the image's default user cannot read files that were copied in as 0600.
    """
    command = f'cd {APP_PATH} 2>/dev/null && sha256sum {shlex.join(names)} 2>/dev/null'
    found = {}
    for line in docker('exec', '-u', '0', CONTAINER, 'sh', '-c', command).stdout.splitlines():
        digest, _, name = line.partition('  ')
        found[name] = digest
    return found


def copy_into_app(files: dict[str, Path]) -> None:
    """Copy files into the Splunk app as one tar stream, then hand them to the splunk user."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for folder in sorted({str(PurePosixPath(name).parent) for name in files} - {'.'}):
            info = tarfile.TarInfo(folder)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(info)
        for name, source in sorted(files.items()):
            data = source.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    docker_or_exit('exec', '-u', '0', CONTAINER, 'mkdir', '-p', APP_PATH)
    docker_or_exit('cp', '-', f'{CONTAINER}:{APP_PATH}', stdin=buffer.getvalue())
    docker_or_exit('exec', '-u', '0', CONTAINER, 'chown', '-R', 'splunk:splunk', APP_PATH)


def warn_if_low_disk() -> None:
    lines = docker('exec', CONTAINER, 'df', '-Pm', '/opt/splunk/var').stdout.splitlines()
    try:
        free_mb = int(lines[1].split()[3])
    except (IndexError, ValueError):
        return
    if free_mb < LOW_DISK_MB:
        print(f'\nWarning: Docker has {free_mb:,} MB free. Splunk stops running searches below 1,000 MB.\n'
              '  Free Docker disk space (for example, `docker builder prune` removes unused build cache)\n'
              '  or raise the disk limit in Docker Desktop > Settings > Resources.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--csv', type=Path, default=DEFAULT_CSV,
                        help='events CSV from card_testing_lab.py or the notebook (default: data/card_testing_events.csv)')
    args = parser.parse_args()
    csv_path = args.csv.expanduser().resolve()
    if not csv_path.is_file():
        raise SystemExit(f'CSV not found: {csv_path}')
    with csv_path.open() as handle:
        if not handle.readline().startswith(CSV_HEADER):
            raise SystemExit(f'{csv_path.name} is not a lab events CSV (its header should start {CSV_HEADER}...)')

    ensure_env_file()
    print('Starting Splunk. The first start takes 3-5 minutes on Apple Silicon.', flush=True)
    if subprocess.run(['docker', 'compose', '--env-file', str(ENV_FILE), '-f', str(COMPOSE_FILE), 'up', '-d']).returncode:
        raise SystemExit('docker compose failed. Is Docker Desktop running?')
    wait_until_healthy()

    # POSIX names, whatever the host OS: they become paths inside the Linux container.
    configs = {path.relative_to(APP_DIR).as_posix(): path for path in sorted(APP_DIR.rglob('*.conf'))}
    present = hashes_in_container([*configs, CSV_IN_APP])
    if CSV_IN_APP in present and present[CSV_IN_APP] != sha256(csv_path):
        raise SystemExit('This lab already holds a different events CSV. Mixing datasets would corrupt every count.\n'
                         f'To start over, which deletes the lab index, run from the repository root:\n  {RESET_COMMAND}')
    to_copy = {name: path for name, path in configs.items() if present.get(name) != sha256(path)}
    if CSV_IN_APP not in present:
        to_copy[CSV_IN_APP] = csv_path
    if to_copy:
        print(f'Loading {", ".join(sorted(to_copy))} and restarting Splunk...', flush=True)
        copy_into_app(to_copy)
        docker_or_exit('restart', CONTAINER)
        wait_until_healthy()
    else:
        print('The lab app and events are already loaded.')

    warn_if_low_disk()
    print('\nSplunk is ready at http://127.0.0.1:8000 (Splunk Free, no login).\n'
          'Set the time range to All time: the events are dated 26 September 2026 (UTC).\n'
          'Check the results with: python3 splunk/verify_lab.py')


if __name__ == '__main__':
    main()
