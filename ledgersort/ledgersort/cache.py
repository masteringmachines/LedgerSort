"""
cache.py — remember Jev's answers, so weights can change for free.

The cache key is a hash of the exact request payload (model + state +
questions). Change a weight or a threshold and the key doesn't change,
so the stored answers are reused and no API call is made. Change a
question, a level description, the text, or the model and the key
changes, so the model is asked again.
"""

from __future__ import annotations
import hashlib
import json
import os
import threading


class Cache:
    def __init__(self, path: str | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._data = {}  # a corrupt cache is just an empty cache

    @staticmethod
    def key(payload: dict) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, response: dict) -> None:
        with self._lock:
            self._data[key] = response

    def save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with self._lock, open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f)
        os.replace(tmp, self.path)
