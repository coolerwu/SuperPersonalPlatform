"""Snapshot explicitly selected files for durable delivery to the current run target."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 10


def image_mime(data: bytes) -> str:
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'image/webp'
    return ''


def outbox(run_dir: Path) -> Path:
    state = json.loads((run_dir / 'state.json').read_text())
    generation = int(state.get('rerun_count') or 0)
    return run_dir / 'outgoing' / str(generation)


def list_attachments(run_dir: Path) -> list[dict]:
    path = outbox(run_dir) / 'index.json'
    return json.loads(path.read_text()) if path.exists() else []


def read_attachment(run_dir: Path, item: dict) -> bytes:
    file_id = item['id']
    if len(file_id) != 64 or any(c not in '0123456789abcdef' for c in file_id):
        raise ValueError('Invalid outgoing attachment ID')
    path = outbox(run_dir) / file_id
    if path.is_symlink(): raise PermissionError('Outgoing attachment cannot be a symlink')
    with path.open("rb") as stream:
        data = stream.read(MAX_ATTACHMENT_BYTES + 1)
    if len(data) > MAX_ATTACHMENT_BYTES or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Outgoing attachment snapshot changed')
    return data


def queue_attachment(run_dir: Path, backend, file_path: str, kind: str = 'auto') -> dict:
    if kind not in {'auto', 'image', 'file'}: raise ValueError('kind must be auto, image or file')
    if not file_path.startswith('/') or '\\' in file_path or any(p in {'.', '..'} for p in file_path.split('/')):
        raise PermissionError('Use an absolute Agent file-tool path')
    source_backend, key = backend._get_backend_and_key(file_path)
    source = source_backend._resolve_path(key)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ATTACHMENT_BYTES:
            raise ValueError('Attachment must be a regular file no larger than 20 MiB')
        data = stream.read(MAX_ATTACHMENT_BYTES + 1)
    if not data or len(data) > MAX_ATTACHMENT_BYTES: raise ValueError('Attachment must contain 1 byte to 20 MiB')
    detected = image_mime(data)
    if kind == 'image' and not detected: raise ValueError('Image must be PNG, JPEG, GIF or WebP')
    selected_kind = ('image' if detected else 'file') if kind == 'auto' else kind
    filename = Path(file_path).name
    digest = hashlib.sha256(data).hexdigest()
    file_id = hashlib.sha256((selected_kind + '\0' + filename + '\0' + digest).encode()).hexdigest()
    item = {'id': file_id, 'filename': filename, 'kind': selected_kind, 'size': len(data), 'sha256': digest, 'source_path': file_path}
    directory = outbox(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = list_attachments(run_dir)
        existing = next((entry for entry in entries if entry['id'] == file_id), None)
        if existing: return existing
        if len(entries) >= MAX_ATTACHMENTS: raise ValueError('At most 10 attachments per run')
        destination = directory / file_id
        content_temp = directory / (file_id + '.tmp')
        with content_temp.open('wb') as output: output.write(data)
        content_temp.replace(destination)
        temp = directory / 'index.tmp'
        temp.write_text(json.dumps([*entries, item], ensure_ascii=False))
        temp.replace(directory / 'index.json')
    return item
