"""Versioned, expiring artifact cache. Never stores keys, photos or chain of thought."""
import hashlib
import json
import time
import fcntl
from contextlib import contextmanager
from pathlib import Path
from .storage import Storage


class ArtifactCache:
    def __init__(self, path: Path):
        self.storage = Storage(path)

    @staticmethod
    def key(*parts):
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def get(self, key):
        value = self.storage.get('artifact_cache', key)
        if value and value['expires_at'] > time.time():
            return value['value']
        return None

    def put(self, key, value, ttl=7*86400):
        self.storage.put('artifact_cache', key, {'expires_at': time.time()+ttl, 'value': value})

    @contextmanager
    def lock(self, key, timeout=30):
        """Process-scoped advisory lock; worker termination releases it automatically."""
        directory = self.storage.path.parent / 'cache-locks'
        directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with (directory/key).open('a') as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic()-started >= timeout:
                        raise TimeoutError('相同产品仍在处理中，请稍后重试；未发送重复模型请求')
                    time.sleep(.1)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
