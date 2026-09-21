"""Codex App Server の標準入出力。Blender 非依存、認証情報は扱わない。"""
import collections
import glob
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading


def find_codex(explicit=""):
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file() and path.suffix.lower() == ".exe":
            return str(path)
        raise FileNotFoundError("Codex の実行ファイル（codex.exe）が見つかりません。")
    direct = shutil.which("codex.exe")
    if direct:
        return direct
    roots = set()
    for name in ("codex", "codex.cmd", "codex.ps1"):
        found = shutil.which(name)
        if found:
            roots.add(str(Path(found).parent))
    roots.add(str(Path(os.environ.get("APPDATA", "")) / "npm"))
    for root in sorted(roots):
        for pattern in (
            "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/*/bin/codex.exe",
            "node_modules/@openai/codex/vendor/*/codex/codex.exe",
            "node_modules/@openai/codex/vendor/*/bin/codex.exe",
        ):
            candidates = glob.glob(str(Path(root) / pattern))
            if candidates:
                return candidates[0]
    raise FileNotFoundError("Codex CLI が見つかりません。設定で codex.exe を指定してください。")


class Transport:
    def __init__(self, executable=""):
        self.executable = find_codex(executable)
        self.events = queue.Queue()
        self.stderr = collections.deque(maxlen=60)
        self.process = None
        self._counter = 0
        self._write_lock = threading.Lock()
        self._closing = False

    def start(self, cwd):
        self.process = subprocess.Popen(
            [self.executable, "app-server", "--stdio"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", text=True, bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True, name="助人・受信").start()
        threading.Thread(target=self._read_errors, daemon=True, name="助人・診断").start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    self.stderr.append(line.rstrip()[:2000])
        finally:
            if not self._closing:
                self.events.put({"method": "suketto/disconnected", "params": {}})

    def _read_errors(self):
        for line in self.process.stderr:
            self.stderr.append(line.rstrip()[:2000])

    def send(self, message):
        with self._write_lock:
            if not self.process or self.process.poll() is not None:
                raise ConnectionError("Codex との接続が切れました。再接続してください。")
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def request(self, method, params):
        self._counter += 1
        self.send({"id": self._counter, "method": method, "params": params})
        return self._counter

    def reply(self, request_id, result):
        self.send({"id": request_id, "result": result})

    def close(self):
        self._closing = True
        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
