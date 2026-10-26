"""Polls a project folder in a background thread and signals when files change."""
import os
import threading

from PySide6.QtCore import QObject, Signal

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".idea",
             ".vscode", "vendor", "dist", "build", ".next", ".nuxt", ".cache", "writable"}
MAX_FILES = 20000


class FolderWatcher(QObject):
    changed = Signal()

    def __init__(self, interval: float = 0.8, parent=None):
        super().__init__(parent)
        self.interval = interval
        self.folder = None
        self._stop = threading.Event()
        self._thread = None

    def start(self, folder: str):
        self.stop()
        self.folder = folder
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(folder, self._stop), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None
        self.folder = None

    def _run(self, folder, stop):
        last = self._snapshot(folder)
        while not stop.wait(self.interval):
            now = self._snapshot(folder)
            if now != last:
                last = now
                self.changed.emit()        # queued to the GUI thread automatically

    @staticmethod
    def _snapshot(folder):
        """(file count, newest mtime) — changes on edit, add, delete and rename."""
        count, newest = 0, 0.0
        stack = [folder]
        while stack and count < MAX_FILES:
            try:
                entries = os.scandir(stack.pop())
            except OSError:
                continue
            with entries:
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in SKIP_DIRS:
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            count += 1
                            newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
        return count, newest
