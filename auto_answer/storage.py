from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from .models import AnswerResult, Settings


def data_directory() -> Path:
    override = os.environ.get("AUTO_ANSWER_DATA_DIR")
    path = Path(override) if override else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AutoAnswer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, root: Path):
        self.path = root / "settings.json"
        self.warning = ""

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            return Settings.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError):
            self.warning = "设置文件无效，已采用安全默认值。原文件仍保留。"
            return Settings()

    def save(self, settings: Settings) -> None:
        settings.validate()
        atomic_write(self.path, json.dumps(settings.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"))


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("安全密钥存储仅支持 Windows")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = _Blob(len(data), buffer), _Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                         ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None if decrypt else "AutoAnswer API Key", None, None, None, 1, ctypes.byref(target)):
        raise OSError("无法加密/读取密钥，请用原 Windows 账户重新设置密钥")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


class KeyStore:
    def __init__(self, root: Path):
        self.path = root / "api-key.dpapi"

    def save(self, key: str) -> None:
        key = key.strip()
        if not key or len(key) > 512 or any(char.isspace() for char in key):
            raise ValueError("API Key 不能为空或包含空白")
        atomic_write(self.path, _crypt(key.encode("utf-8")))

    def load(self) -> str:
        return _crypt(self.path.read_bytes(), True).decode("utf-8") if self.path.exists() else ""

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class HistoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.images = root / "screenshots"
        self.images.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(root / "history.sqlite3", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY, created TEXT NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL, certain INTEGER NOT NULL, elapsed REAL NOT NULL, image_name TEXT NOT NULL)""")
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(history)")}
        if "sources_json" not in columns:
            self.connection.execute("ALTER TABLE history ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'")
        if 'answer_kind' not in columns:
            self.connection.execute("ALTER TABLE history ADD COLUMN answer_kind TEXT NOT NULL DEFAULT 'answer'")
        self.connection.commit()
        self._cleanup_orphans()

    def _cleanup_orphans(self) -> None:
        known = {row[0] for row in self.connection.execute("SELECT image_name FROM history")}
        for path in self.images.glob("*.png"):
            if path.name not in known:
                path.unlink(missing_ok=True)

    def add(self, image: bytes, result: AnswerResult, elapsed: float, limit: int) -> str:
        if not result.is_question:
            return ""
        identifier = uuid.uuid4().hex
        name = identifier + ".png"
        path = self.images / name
        with self.lock:
            atomic_write(path, image)
            try:
                with self.connection:
                    self.connection.execute("INSERT INTO history (id,created,question,answer,certain,elapsed,image_name,sources_json,answer_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                                            (identifier, datetime.now(timezone.utc).isoformat(), result.question,
                                             result.display_answer, int(result.certain), elapsed, name,
                                             json.dumps(result.sources, ensure_ascii=False),result.answer_kind))
            except Exception:
                path.unlink(missing_ok=True)
                raise
            rows = self.connection.execute("SELECT id FROM history ORDER BY created DESC LIMIT -1 OFFSET ?", (limit,)).fetchall()
            for row in rows:
                self.delete(row[0])
        return identifier

    def list(self, search: str = "") -> list[dict]:
        with self.lock:
            # 不把通配符当输入语法，筛选只做字面包含。
            rows = self.connection.execute("SELECT * FROM history ORDER BY created DESC").fetchall()
            return [dict(row) for row in rows if not search or search.casefold() in (row["question"] + row["answer"]).casefold()]

    def image_path(self, record: dict) -> Path:
        name = record["image_name"]
        if Path(name).name != name or not name.endswith(".png"):
            raise ValueError("历史图片路径无效")
        return self.images / name

    def delete(self, identifier: str) -> None:
        with self.lock:
            row = self.connection.execute("SELECT image_name FROM history WHERE id=?", (identifier,)).fetchone()
            if row:
                # 先删除图片再删除记录；权限错误不会把仍含截图的记录隐藏。
                self.image_path(dict(row)).unlink(missing_ok=True)
                with self.connection:
                    self.connection.execute("DELETE FROM history WHERE id=?", (identifier,))

    def clear(self) -> None:
        with self.lock:
            for record in self.list():
                self.delete(record["id"])

    def close(self) -> None:
        with self.lock:
            self.connection.close()
