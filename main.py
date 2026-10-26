"""Device Preview — view one website on several devices side by side.

Run:  .venv/bin/python main.py
"""
import json
import os
import re
import sys
from urllib.parse import quote

from PySide6.QtCore import (QEvent, QFile, QIODevice, QObject, QSettings, Qt, QTimer, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineScript,
                                     QWebEngineSettings)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMenu, QScrollArea, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from devices import DEFAULT_LAYOUT, DEVICE_LIST, DEVICES, Device, spoof_script
from frame import DeviceFrame
from watcher import FolderWatcher

MAIN_WORLD = QWebEngineScript.ScriptWorldId.MainWorld.value
APP_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value  # isolated from page scripts
HEADER_H = 30
PANEL_MARGIN = 24
PANEL_SPACING = 32
PANEL_MIN_W = 150
SCALES = [("Fit", 0.0), ("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("33%", 0.33)]
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")

# Runs in an isolated JS world: reports scroll position, and applies positions from other devices.
SYNC_JS = r"""
(() => {
  if (window.__dpSync) return;
  window.__dpSync = true;
  let bridge = null, lockUntil = 0, queued = false;
  new QWebChannel(qt.webChannelTransport, ch => { bridge = ch.objects.bridge; });
  const el = () => document.scrollingElement || document.documentElement;
  const ratio = () => {
    const max = el().scrollHeight - window.innerHeight;
    return max > 0 ? window.scrollY / max : 0;
  };
  window.addEventListener('scroll', () => {
    if (!bridge || Date.now() < lockUntil || queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; bridge.report(ratio()); });
  }, { passive: true });
  window.__dpApply = (r) => {
    lockUntil = Date.now() + 250;
    window.scrollTo(0, r * (el().scrollHeight - window.innerHeight));
  };
})();
"""

# Shown before any URL is entered; doubles as a check of what each device reports.
START_HTML = """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui,sans-serif;margin:0;padding:24px;color:#222;background:#f7f7fa}
h1{font-size:20px;margin:0 0 8px}p{color:#666;margin:0 0 18px;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:13px}td{padding:6px 4px;border-bottom:1px solid #e3e3e8;
vertical-align:top;word-break:break-all}td:first-child{color:#888;white-space:nowrap;padding-right:12px}</style>
</head><body><h1>Device Preview</h1><p>Type a URL in the bar above. This is what sites see from this device:</p>
<table id=t></table><script>
const rows = {
  'Viewport': innerWidth + ' × ' + innerHeight,
  'Pixel ratio': devicePixelRatio,
  'Screen': screen.width + ' × ' + screen.height,
  'Platform': navigator.platform,
  'Touch points': navigator.maxTouchPoints,
  'Mobile (UA-CH)': navigator.userAgentData ? navigator.userAgentData.mobile : 'n/a (Safari)',
  'User agent': navigator.userAgent,
};
document.getElementById('t').innerHTML = Object.entries(rows)
  .map(([k, v]) => '<tr><td>' + k + '</td><td>' + v + '</td></tr>').join('');
</script></body></html>"""


# ── per-device browser profiles ──────────────────────────────────────────────
_profiles: dict[str, QWebEngineProfile] = {}


def profile_for(device: Device) -> QWebEngineProfile:
    """One persistent profile per device type: own UA, cookies and logins."""
    prof = _profiles.get(device.key)
    if prof is None:
        prof = QWebEngineProfile(f"device-preview-{device.key}", QApplication.instance())
        prof.setHttpUserAgent(device.user_agent)
        _apply_client_hints(prof, device)
        _profiles[device.key] = prof
    return prof


def _apply_client_hints(prof: QWebEngineProfile, device: Device):
    """Sec-CH-UA request headers (Qt 6.8+).

    Real Safari sends none, but Chromium always sends the three default ones even with
    hints disabled — so for Apple devices make them at least agree with the User-Agent.
    """
    if not hasattr(prof, "clientHints"):
        return
    ua = device.ua_data
    if ua is None:
        iphone = device.platform == "iPhone"
        ua = {"platform": "iOS" if iphone else "macOS", "mobile": iphone, "model": "",
              "brands": [{"brand": "Not?A_Brand", "version": "99"}]}
    try:
        hints = prof.clientHints()
        hints.setAllClientHintsEnabled(True)
        hints.setPlatform(ua["platform"])
        hints.setIsMobile(ua["mobile"])
        hints.setModel(ua.get("model", ""))
        hints.setArch("arm" if ua["mobile"] or device.group == "Tablets" else "x86")
        hints.setFullVersionList({b["brand"]: b["version"] for b in ua["brands"]})
        if hasattr(hints, "setFormFactors"):
            hints.setFormFactors({"Phones": ["Mobile"], "Tablets": ["Tablet"]}.get(device.group, ["Desktop"]))
    except Exception as exc:  # API differences between Qt versions: degrade to UA-only spoofing
        print(f"client hints not applied for {device.key}: {exc}", file=sys.stderr)


def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        return bytes(f.readAll()).decode("utf-8")
    return ""


def normalize_url(text: str) -> QUrl | None:
    t = text.strip()
    if not t:
        return None
    path = os.path.expanduser(t)
    if os.path.exists(path):
        return QUrl.fromLocalFile(os.path.abspath(path))
    if "://" in t or t.startswith(("about:", "data:", "file:")):
        return QUrl(t)
    host = t.split("/")[0]
    if host.split(":")[0] in LOCAL_HOSTS or re.match(r"^(\d{1,3}\.){3}\d{1,3}(:\d+)?$", host) \
            or re.match(r"^[\w.-]+:\d+$", host):
        return QUrl("http://" + t)
    if " " in t or "." not in host:
        return QUrl("https://www.google.com/search?q=" + quote(t))
    return QUrl("https://" + t)


# ── web page / panel ─────────────────────────────────────────────────────────
class ScrollBridge(QObject):
    scrolled = Signal(float)

    @Slot(float)
    def report(self, ratio):
        self.scrolled.emit(ratio)


class DevicePage(QWebEnginePage):
    linkClicked = Signal(QUrl)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        # Only real link clicks are mirrored — not redirects, form posts or history moves.
        if is_main_frame and nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.linkClicked.emit(QUrl(url))
        return True


class DevicePanel(QWidget):
    removeRequested = Signal(object)
    rotated = Signal(object)
    scrolled = Signal(object, float)
    linkClicked = Signal(object, QUrl)
    urlChanged = Signal(object, QUrl)

    def __init__(self, device: Device, landscape: bool = False, parent=None):
        super().__init__(parent)
        self.device = device
        self.scale = 1.0
        self._devtools = None

        self.frame = DeviceFrame(device, landscape)
        self.view = QWebEngineView()
        self.page = DevicePage(profile_for(device), self.view)
        self.view.setPage(self.page)
        self.frame.set_content(self.view)
        if device.touch_points:
            # Phones/tablets use overlay scrollbars that take no space; desktop ones would
            # steal ~15px of width and cause sideways overflow.
            self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)

        self.bridge = ScrollBridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel, APP_WORLD)
        self._install_scripts()

        self.bridge.scrolled.connect(lambda r: self.scrolled.emit(self, r))
        self.page.linkClicked.connect(lambda u: self.linkClicked.emit(self, u))
        self.page.urlChanged.connect(self._on_url_changed)
        self.page.loadFinished.connect(lambda _ok: self.page.setZoomFactor(self.scale))
        self.page.certificateError.connect(self._on_certificate_error)

        self.title = QLabel()
        self.title.setStyleSheet("font-weight:600;color:#333")
        # Let the title be clipped rather than forcing small (fitted) panels wider.
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        header.addWidget(self.title, 1)
        if device.rotatable:
            header.addWidget(self._tool_button("⟲", "Rotate", self.rotate))
        header.addWidget(self._tool_button("</>", "Open DevTools", self.open_devtools))
        header.addWidget(self._tool_button("✕", "Remove device", lambda: self.removeRequested.emit(self)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.frame, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        self._update_title()

    @staticmethod
    def _tool_button(text, tip, slot):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setFixedHeight(HEADER_H - 4)
        b.clicked.connect(slot)
        return b

    @property
    def landscape(self):
        return self.frame.landscape

    def _install_scripts(self):
        scripts = self.page.scripts()
        for old in scripts.find("dp-spoof"):
            scripts.remove(old)
        spoof = QWebEngineScript()
        spoof.setName("dp-spoof")
        spoof.setSourceCode(spoof_script(self.device, self.landscape))
        spoof.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        spoof.setWorldId(MAIN_WORLD)
        spoof.setRunsOnSubFrames(True)
        scripts.insert(spoof)
        if not scripts.find("dp-sync"):
            sync = QWebEngineScript()
            sync.setName("dp-sync")
            sync.setSourceCode(_qwebchannel_js() + SYNC_JS)
            sync.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            sync.setWorldId(APP_WORLD)
            scripts.insert(sync)

    def _update_title(self):
        w, h = self.frame.viewport_css()
        self.title.setText(f"{self.device.name}  ·  {w}×{h}")
        self.title.setToolTip(self.device.user_agent)

    def fit_scale(self, available_h: int) -> float:
        _, css_h = self.frame.css_size()
        return max(0.25, min(1.0, (available_h - HEADER_H - 6) / css_h))

    def set_scale(self, s: float):
        self.scale = s
        self.frame.set_scale(s)
        self.page.setZoomFactor(s)
        self.setFixedWidth(max(self.frame.width(), PANEL_MIN_W))

    def rotate(self):
        self.frame.set_landscape(not self.frame.landscape)
        self._install_scripts()
        self._update_title()
        self.rotated.emit(self)
        self.page.triggerAction(QWebEnginePage.WebAction.Reload)

    def open_devtools(self):
        if self._devtools is None:
            self._devtools = QWebEngineView()
            self._devtools.setWindowTitle(f"DevTools — {self.device.name}")
            self._devtools.resize(1100, 720)
            self.page.setDevToolsPage(self._devtools.page())
        self._devtools.show()
        self._devtools.raise_()
        self._devtools.activateWindow()

    def _on_url_changed(self, url):
        self.page.setZoomFactor(self.scale)   # Chromium can reset zoom when the host changes
        self.urlChanged.emit(self, url)

    @staticmethod
    def _on_certificate_error(error):
        host = error.url().host()
        if host in LOCAL_HOSTS or host.endswith((".test", ".local", ".localhost")):
            error.acceptCertificate()          # self-signed dev certs
        else:
            error.rejectCertificate()

    def shutdown(self):
        if self._devtools is not None:
            self.page.setDevToolsPage(None)
            self._devtools.close()
            self._devtools.deleteLater()
        self.deleteLater()


# ── main window ──────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Device Preview")
        self.settings = QSettings("Kinsmen", "DevicePreview")
        self.panels: list[DevicePanel] = []
        self.primary = None
        self.current_url: QUrl | None = None
        self.loading: dict = {}           # panel -> load progress (0–100)

        self.watcher = FolderWatcher(parent=self)
        self.watcher.changed.connect(lambda: self.live_timer.start(250))
        self.live_timer = QTimer(self, singleShot=True, timeout=self.reload_all)
        self.fit_timer = QTimer(self, singleShot=True, timeout=self.apply_scale)

        self._build_toolbar()
        self.container = QWidget()
        self.container.setObjectName("deck")
        self.container.setStyleSheet("#deck{background:#e8e8ed}")
        self.row = QHBoxLayout(self.container)
        self.row.setContentsMargins(PANEL_MARGIN, PANEL_MARGIN, PANEL_MARGIN, PANEL_MARGIN)
        self.row.setSpacing(PANEL_SPACING)
        self.row.addStretch(1)      # stretches on both sides keep the devices centred
        self.row.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.toolbar)
        v.addWidget(self.scroll, 1)
        self.setCentralWidget(central)
        self._shortcuts()
        self._restore()

    # toolbar ------------------------------------------------------------------
    def _build_toolbar(self):
        self.toolbar = QWidget()
        self.toolbar.setObjectName("bar")
        self.toolbar.setStyleSheet("#bar{background:#f6f6f8;border-bottom:1px solid #d6d6db}")
        h = QHBoxLayout(self.toolbar)
        h.setContentsMargins(10, 6, 10, 6)

        def btn(text, tip, slot):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            h.addWidget(b)
            return b

        btn("←", "Back (Alt+Left)", lambda: self._all(QWebEnginePage.WebAction.Back))
        btn("→", "Forward (Alt+Right)", lambda: self._all(QWebEnginePage.WebAction.Forward))
        self.reload_btn = btn("⟳", "Reload all (F5) — Ctrl+Shift+R bypasses cache", self._reload_or_stop)

        self.url_edit = QLineEdit()
        self._set_url_progress(None)
        self.url_edit.setPlaceholderText("Enter a URL, localhost:8000, or a file path — Enter loads it on every device")
        self.url_edit.returnPressed.connect(self.navigate)
        h.addWidget(self.url_edit, 1)

        add = QToolButton()
        add.setText("+ Add device")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        group = None
        for d in DEVICE_LIST:
            if d.group != group:
                group = d.group
                menu.addSection(group)
            menu.addAction(f"{d.name}  ({d.width}×{d.height})", lambda k=d.key: self.add_device(k))
        add.setMenu(menu)
        h.addWidget(add)

        self.scale_combo = QComboBox()
        self.scale_combo.setToolTip("Size of the device frames")
        for label, value in SCALES:
            self.scale_combo.addItem(label, value)
        self.scale_combo.currentIndexChanged.connect(self.apply_scale)
        h.addWidget(self.scale_combo)

        self.sync_scroll = QCheckBox("Sync scroll")
        self.sync_links = QCheckBox("Sync clicks")
        self.sync_links.setToolTip("Clicking a link on one device opens it on all of them")
        h.addWidget(self.sync_scroll)
        h.addWidget(self.sync_links)

        self.watch_btn = QToolButton()
        self.watch_btn.clicked.connect(self.toggle_watch)
        h.addWidget(self.watch_btn)
        self._update_watch_button()

    def _shortcuts(self):
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: (self.url_edit.setFocus(), self.url_edit.selectAll()))
        QShortcut(QKeySequence("F5"), self, self.reload_all)
        QShortcut(QKeySequence("Ctrl+R"), self, self.reload_all)
        QShortcut(QKeySequence("Ctrl+Shift+R"), self,
                  lambda: self._all(QWebEnginePage.WebAction.ReloadAndBypassCache))
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self._all(QWebEnginePage.WebAction.Back))
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self._all(QWebEnginePage.WebAction.Forward))

    # devices ------------------------------------------------------------------
    def add_device(self, key: str, landscape: bool = False, save: bool = True):
        device = DEVICES.get(key)
        if device is None:
            return
        panel = DevicePanel(device, landscape)
        panel.removeRequested.connect(self.remove_device)
        panel.rotated.connect(lambda _p: (self.apply_scale(), self._save_layout()))
        panel.scrolled.connect(self._on_scrolled)
        panel.linkClicked.connect(self._on_link_clicked)
        panel.urlChanged.connect(self._on_url_changed)
        panel.page.loadStarted.connect(lambda p=panel: self._on_load_progress(p, 0))
        panel.page.loadProgress.connect(lambda v, p=panel: self._on_load_progress(p, v))
        panel.page.loadFinished.connect(lambda _ok, p=panel: self._on_load_progress(p, None))
        self.row.insertWidget(self.row.count() - 1, panel, 0, Qt.AlignmentFlag.AlignTop)
        self.panels.append(panel)
        self.apply_scale()
        if self.current_url is not None:
            panel.page.load(self.current_url)
        else:
            panel.page.setHtml(START_HTML)
        if save:
            self._save_layout()

    def remove_device(self, panel: DevicePanel):
        if panel not in self.panels:
            return
        self.panels.remove(panel)
        self.row.removeWidget(panel)
        self._on_load_progress(panel, None)
        if self.primary is panel:
            self.primary = None
        panel.shutdown()
        self._save_layout()

    def apply_scale(self):
        if not self.panels:
            return
        fixed = self.scale_combo.currentData()
        if fixed:
            for p in self.panels:
                p.set_scale(fixed)
            return
        # Fit: size each device to the window height, then shrink them all by the same
        # factor if together they're still wider than the window.
        vp = self.scroll.viewport()
        avail_h = vp.height() - 2 * PANEL_MARGIN
        avail_w = vp.width() - 2 * PANEL_MARGIN - PANEL_SPACING * (len(self.panels) - 1)
        fits = [p.fit_scale(avail_h) for p in self.panels]
        total_w = sum(p.frame.css_size()[0] * f for p, f in zip(self.panels, fits))
        shrink = min(1.0, avail_w / total_w) if total_w > 0 else 1.0
        for p, f in zip(self.panels, fits):
            p.set_scale(max(0.25, f * shrink))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_timer.start(80)

    # navigation & sync --------------------------------------------------------
    def navigate(self):
        url = normalize_url(self.url_edit.text())
        if url is None:
            return
        self.current_url = url
        for p in self.panels:
            p.page.load(url)

    def reload_all(self):
        self._all(QWebEnginePage.WebAction.Reload)

    def _reload_or_stop(self):
        if self.loading:
            self._all(QWebEnginePage.WebAction.Stop)
        else:
            self.reload_all()

    # loading indicator --------------------------------------------------------
    def _on_load_progress(self, panel, value):
        """value: 0–100 while loading, None when that device has finished."""
        if value is None:
            self.loading.pop(panel, None)
        else:
            self.loading[panel] = value
        if self.loading:
            avg = sum(self.loading.values()) / len(self.loading)
            self._set_url_progress(max(avg, 8) / 100)
            self.reload_btn.setText("✕")
            self.reload_btn.setToolTip("Stop loading")
        else:
            self._set_url_progress(None)
            self.reload_btn.setText("⟳")
            self.reload_btn.setToolTip("Reload all (F5) — Ctrl+Shift+R bypasses cache")

    def _set_url_progress(self, fraction):
        """Fill the address bar from the left as the devices load, Safari-style."""
        if fraction is None:
            bg = "#ffffff"
        else:
            f = min(fraction, 0.995)
            bg = (f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #cfe5ff, stop:{f:.3f} #cfe5ff, "
                  f"stop:{f + 0.004:.3f} #ffffff, stop:1 #ffffff)")
        self.url_edit.setStyleSheet(
            f"QLineEdit{{border:1px solid #c7c7cc; border-radius:5px; padding:3px 6px; background:{bg};}}"
            "QLineEdit:focus{border:1px solid #0a84ff;}")

    def _all(self, action):
        for p in self.panels:
            p.page.triggerAction(action)

    def _on_scrolled(self, source, ratio):
        if not self.sync_scroll.isChecked():
            return
        for p in self.panels:
            if p is not source:
                p.page.runJavaScript(f"window.__dpApply && window.__dpApply({ratio:.6f})", APP_WORLD)

    def _on_link_clicked(self, source, url):
        self.primary = source
        if not self.sync_links.isChecked():
            return
        for p in self.panels:
            if p is not source:
                p.page.load(url)

    def _on_url_changed(self, panel, url):
        primary = self.primary if self.primary in self.panels else (self.panels[0] if self.panels else None)
        if panel is primary and url.scheme() not in ("data", "about"):
            self.current_url = url
            if not self.url_edit.hasFocus():
                self.url_edit.setText(url.toString())

    # live reload --------------------------------------------------------------
    def toggle_watch(self):
        if self.watcher.folder:
            self.watcher.stop()
        else:
            folder = QFileDialog.getExistingDirectory(self, "Reload devices when files in this folder change")
            if folder:
                self.watcher.start(folder)
        self._update_watch_button()

    def _update_watch_button(self):
        if self.watcher.folder:
            self.watch_btn.setText(f"● Live: {os.path.basename(self.watcher.folder)}")
            self.watch_btn.setToolTip(f"Reloading on changes in {self.watcher.folder}\nClick to stop")
        else:
            self.watch_btn.setText("Live reload…")
            self.watch_btn.setToolTip("Pick a project folder; every device reloads when you save a file")

    # persistence --------------------------------------------------------------
    def _save_layout(self):
        layout = [{"key": p.device.key, "landscape": p.landscape} for p in self.panels]
        self.settings.setValue("layout", json.dumps(layout))

    def _restore(self):
        s = self.settings
        if s.value("geometry"):
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1500, 950)
        self.scale_combo.setCurrentIndex(int(s.value("scale_index", 0)))
        self.sync_scroll.setChecked(s.value("sync_scroll", "true") == "true")
        self.sync_links.setChecked(s.value("sync_links", "true") == "true")
        url = s.value("url", "")
        if url:
            self.current_url = QUrl(url)
            self.url_edit.setText(url)
        try:
            layout = json.loads(s.value("layout", "")) or DEFAULT_LAYOUT
        except (TypeError, ValueError):
            layout = DEFAULT_LAYOUT
        for item in layout:
            self.add_device(item.get("key", ""), bool(item.get("landscape")), save=False)
        watch = s.value("watch_dir", "")
        if watch and os.path.isdir(watch):
            self.watcher.start(watch)
            self._update_watch_button()

    def closeEvent(self, event):
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("scale_index", self.scale_combo.currentIndex())
        s.setValue("sync_scroll", "true" if self.sync_scroll.isChecked() else "false")
        s.setValue("sync_links", "true" if self.sync_links.isChecked() else "false")
        s.setValue("url", self.current_url.toString() if self.current_url else "")
        s.setValue("watch_dir", self.watcher.folder or "")
        self._save_layout()
        self.watcher.stop()
        for p in list(self.panels):
            p.shutdown()
        self.panels.clear()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.apply_scale)


def light_palette() -> QPalette:
    """Fixed light theme, so text stays readable whatever the desktop theme is."""
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#f6f6f8", QPalette.ColorRole.WindowText: "#1c1c1e",
        QPalette.ColorRole.Base: "#ffffff", QPalette.ColorRole.AlternateBase: "#f0f0f3",
        QPalette.ColorRole.Text: "#1c1c1e", QPalette.ColorRole.Button: "#ffffff",
        QPalette.ColorRole.ButtonText: "#1c1c1e", QPalette.ColorRole.Highlight: "#0a84ff",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#1c1c1e", QPalette.ColorRole.PlaceholderText: "#8e8e93",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Device Preview")
    app.setOrganizationName("Kinsmen")
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    win = MainWindow()
    win.show()
    code = app.exec()
    # Pages must be destroyed before their profiles, or Qt WebEngine warns on exit.
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for prof in _profiles.values():
        prof.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    sys.exit(code)


if __name__ == "__main__":
    main()
