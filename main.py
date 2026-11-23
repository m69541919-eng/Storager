"""
Хранилище — быстрый анализатор диска в стиле iOS 18
+ превью фото, почти все файлы диска, меньше «Прочее»
"""
import os
import sys
import heapq
import shutil
import ctypes
import platform
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, QSequentialAnimationGroup

# ─── Fix Qt platform plugin (Windows) ───────────────────────────────────────
if sys.platform == "win32":
    try:
        import PyQt5
        _pyqt = os.path.dirname(PyQt5.__file__)
        for candidate in (
            os.path.join(_pyqt, "Qt5", "plugins", "platforms"),
            os.path.join(_pyqt, "Qt", "plugins", "platforms"),
            os.path.join(_pyqt, "plugins", "platforms"),
        ):
            if os.path.isdir(candidate):
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = candidate
                break
    except Exception:
        pass

OS = platform.system()
HOME = os.path.expanduser("~")
BIG_MIN = 40 * 1024 * 1024
BIG_KEEP = 180
MAX_ROWS = 400
RED = "#ff453a"
THUMB_SIZE = 40

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tiff", ".tif", ".ico"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts"}

# ───────────────────────── права администратора ─────────────────────────

def is_admin():
    try:
        if OS == "Windows":
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def relaunch_as_admin():
    if OS != "Windows":
        return False
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            params = " ".join([f'"{os.path.abspath(sys.argv[0])}"'] + [f'"{a}"' for a in sys.argv[1:]])
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32
    except Exception:
        return False


# ───────────────────────── тема iOS 18 ─────────────────────────

def system_dark():
    try:
        if OS == "Windows":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if OS == "Darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                               capture_output=True, text=True)
            return r.stdout.strip() == "Dark"
    except Exception:
        pass
    return True


DARK = dict(
    bg="#000000", card="#1c1c1e", card2="#2c2c2e", text="#ffffff", dim="#8e8e93",
    sep="#38383a", track="#2c2c2e", blue="#0a84ff", hl="#3a3a3c", field="#1c1c1e",
)
LIGHT = dict(
    bg="#f2f2f7", card="#ffffff", card2="#f2f2f7", text="#000000", dim="#8a8a8e",
    sep="#c6c6c8", track="#e5e5ea", blue="#007aff", hl="#e5e5ea", field="#e3e3e8",
)
DARK_MODE = system_dark()
T = DARK if DARK_MODE else LIGHT
FONT_FAMILY = "Segoe UI"
LABEL_COLOR = {"Временное": "#5ac8fa", "Прочее": "#636366"}
GLYPH = {
    "Система": "⚙", "Приложения": "▦", "Скачанное": "↓", "Документы": "▤",
    "Фото": "▣", "Видео": "▶", "Музыка": "♪", "Рабочий стол": "▭", "Прочее": "…",
    "Крупные файлы": "◆", "Временные файлы": "✦"
}


def qfont(px, weight=QtGui.QFont.Normal):
    f = QtGui.QFont(FONT_FAMILY)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleStrategy(QtGui.QFont.PreferAntialias)
    return f


def fmt(n):
    n = float(n)
    for u in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024:
            return f"{int(n)} Б" if u == "Б" else f"{n:.1f} {u}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} ПБ".replace(".", ",")


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXT


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


# ───────────────────────── сканирование ─────────────────────────

def build_categories():
    h = lambda *a: os.path.join(HOME, *a)
    if OS == "Windows":
        win = os.environ.get("SystemRoot", r"C:\Windows")
        drive = os.path.splitdrive(win)[0] + "\\"
        cats = [
            ("Система", "#8e8e93", "Системное", [win, os.path.join(drive, "$Recycle.Bin")]),
            ("Приложения", "#ff9f0a", "Приложение", [
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.environ.get("ProgramData", r"C:\ProgramData"), h("AppData")]),
        ]
    elif OS == "Darwin":
        drive = "/"
        cats = [("Система", "#8e8e93", "Системное", ["/System", "/Library", "/usr", "/private", "/opt"]),
                ("Приложения", "#ff9f0a", "Приложение", ["/Applications", h("Applications"), h("Library")])]
    else:
        drive = "/"
        cats = [("Система", "#8e8e93", "Системное", ["/usr", "/var", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/boot"]),
                ("Приложения", "#ff9f0a", "Приложение", ["/opt", "/snap", h(".local"), h(".cache"), h(".config")])]

    cats = [
        ("Скачанное", "#0a84ff", "Скачанное", [h("Downloads"), h("Загрузки")]),
        ("Документы", "#ffcc00", "Документ", [h("Documents"), h("Документы")]),
        ("Фото", "#bf5af2", "Фото", [h("Pictures"), h("Изображения"), h("OneDrive", "Pictures")]),
        ("Видео", "#ff375f", "Видео", [h("Videos"), h("Movies"), h("Видео"), h("OneDrive", "Videos")]),
        ("Музыка", "#30d158", "Музыка", [h("Music"), h("Музыка")]),
        ("Рабочий стол", "#64d2ff", "Рабочий стол", [h("Desktop"), h("Рабочий стол")]),
    ] + cats

    seen, out = set(), []
    for name, color, label, paths in cats:
        good = []
        for p in paths:
            try:
                rp = os.path.realpath(p)
                if os.path.isdir(p) and rp not in seen:
                    seen.add(rp)
                    good.append(p)
            except Exception:
                pass
        out.append((name, color, label, good))
    return drive, out, seen


def push_big(big, item):
    if len(big) < BIG_KEEP:
        heapq.heappush(big, item)
    else:
        heapq.heappushpop(big, item)


def scan_dir_size(path, label, big, max_depth=14):
    total = 0
    stack = [(path, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, depth + 1))
                        elif e.is_file(follow_symlinks=False):
                            s = e.stat(follow_symlinks=False).st_size
                            total += s
                            if s >= BIG_MIN:
                                push_big(big, (s, e.path, label))
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def scan_root(path, label, big):
    items = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        items.append((e.name, e.path, scan_dir_size(e.path, label, big), True, label))
                    elif e.is_file(follow_symlinks=False):
                        s = e.stat(follow_symlinks=False).st_size
                        items.append((e.name, e.path, s, False, label))
                        if s >= BIG_MIN:
                            push_big(big, (s, e.path, label))
                except OSError:
                    pass
    except OSError:
        pass
    items.sort(key=lambda x: x[2], reverse=True)
    return items


def scan_other(drive, seen, big):
    """Сканируем корень диска и собираем всё, что не попало в категории → Прочее"""
    items = []
    label = "Прочее"
    try:
        with os.scandir(drive) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    rp = os.path.realpath(e.path)
                    if any(rp == s or rp.startswith(s + os.sep) for s in seen):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        name_l = e.name.lower()
                        if name_l in ("$recycle.bin", "system volume information", "recovery", "config.msi"):
                            continue
                        size = scan_dir_size(e.path, label, big, max_depth=10)
                        if size > 0:
                            items.append((e.name, e.path, size, True, label))
                    elif e.is_file(follow_symlinks=False):
                        s = e.stat(follow_symlinks=False).st_size
                        items.append((e.name, e.path, s, False, label))
                        if s >= BIG_MIN:
                            push_big(big, (s, e.path, label))
                except OSError:
                    pass
    except OSError:
        pass

    try:
        with os.scandir(HOME) as it:
            for e in it:
                try:
                    if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                        continue
                    rp = os.path.realpath(e.path)
                    if any(rp == s or rp.startswith(s + os.sep) for s in seen):
                        continue
                    if e.name.startswith(".") and OS != "Windows":
                        continue
                    size = scan_dir_size(e.path, label, big, max_depth=8)
                    if size > 5 * 1024 * 1024:
                        items.append((e.name, e.path, size, True, label))
                except OSError:
                    pass
    except OSError:
        pass

    items.sort(key=lambda x: x[2], reverse=True)
    return items


class Scanner(QtCore.QThread):
    category = QtCore.pyqtSignal(str, int, list)
    finished_all = QtCore.pyqtSignal(list, list, list)

    def __init__(self, cats, drive, seen):
        super().__init__()
        self.cats = cats
        self.drive = drive
        self.seen = seen

    def run(self):
        def job(cat):
            name, _, label, paths = cat
            big, items = [], []
            for p in paths:
                items += scan_root(p, label, big)
            items.sort(key=lambda x: x[2], reverse=True)
            return name, items, big

        bigs = []
        with ThreadPoolExecutor(max_workers=min(8, len(self.cats) + 2)) as ex:
            futures = [ex.submit(job, c) for c in self.cats]
            for f in as_completed(futures):
                name, items, big = f.result()
                bigs += big
                self.category.emit(name, sum(i[2] for i in items), items)

        other = scan_other(self.drive, self.seen, bigs)
        self.category.emit("Прочее", sum(i[2] for i in other), other)

        temp = scan_root(tempfile.gettempdir(), "Временное", [])
        bigs.sort(reverse=True)
        big_items = [(os.path.basename(p), p, s, False, l) for s, p, l in bigs[:BIG_KEEP]]
        self.finished_all.emit(big_items, temp, other)


class DirWorker(QtCore.QThread):
    result = QtCore.pyqtSignal(list)

    def __init__(self, path, label):
        super().__init__()
        self.path, self.label = path, label

    def run(self):
        self.result.emit(scan_root(self.path, self.label, []))


# ───────────────────────── системные действия ─────────────────────────

def reveal(path):
    try:
        if OS == "Windows":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif OS == "Darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


def is_protected(path, label):
    if label == "Системное":
        return True
    p = os.path.normcase(os.path.abspath(path))
    if OS == "Windows":
        roots = [os.environ.get("SystemRoot", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", "")]
    else:
        roots = ["/usr", "/bin", "/sbin", "/lib", "/etc", "/System", "/Library", "/boot"]
    return any(r and p.startswith(os.path.normcase(r)) for r in roots)


def recycle(path):
    if OS == "Windows":
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        op = SHFILEOPSTRUCTW()
        op.wFunc = 3
        op.pFrom = os.path.normpath(path) + "\0"
        op.fFlags = 0x40 | 0x10 | 0x4
        return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
    try:
        from send2trash import send2trash
        send2trash(path)
        return True
    except Exception:
        return False


# ───────────────────────── кэш превью ─────────────────────────

_thumb_cache = {}

def get_thumbnail(path, size=THUMB_SIZE):
    key = (path, size)
    if key in _thumb_cache:
        return _thumb_cache[key]
    try:
        img = QtGui.QImage(path)
        if img.isNull():
            return None
        img = img.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        w, h = img.width(), img.height()
        if w > size or h > size:
            x = max(0, (w - size) // 2)
            y = max(0, (h - size) // 2)
            img = img.copy(x, y, size, size)
        pix = QtGui.QPixmap.fromImage(img)
        _thumb_cache[key] = pix
        if len(_thumb_cache) > 300:
            _thumb_cache.pop(next(iter(_thumb_cache)))
        return pix
    except Exception:
        return None


# ───────────────────────── виджеты ─────────────────────────

class Page(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.dead = False

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(T["bg"]))


def centered(widget, max_w=720):
    widget.setMaximumWidth(max_w)
    box = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch(1)
    lay.addWidget(widget, 100)
    lay.addStretch(1)
    return box


class Card(QtWidgets.QWidget):
    def __init__(self, margins=(0, 0, 0, 0), radius=20):
        super().__init__()
        self.radius = radius
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(0)

    def add_rows(self, rows):
        for i, r in enumerate(rows):
            r.first, r.last = i == 0, i == len(rows) - 1
            self.lay.addWidget(r)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()), self.radius, self.radius)
        p.fillPath(path, QtGui.QColor(T["card"]))


def draw_icon(p, rect, color, glyph):
    p.setPen(Qt.NoPen)
    p.setBrush(QtGui.QColor(color))
    p.drawRoundedRect(rect, 9, 9)
    p.setPen(QtGui.QColor("#ffffff"))
    p.setFont(qfont(16, QtGui.QFont.DemiBold))
    p.drawText(rect, Qt.AlignCenter, glyph)


def draw_chevron(p, x, cy):
    pen = QtGui.QPen(QtGui.QColor(T["dim"]), 2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QtCore.QPointF(x, cy - 5), QtCore.QPointF(x + 5.5, cy), QtCore.QPointF(x, cy + 5))


class Row(QtWidgets.QWidget):
    clicked = QtCore.pyqtSignal()

    def __init__(self, title, color, glyph, value="", sub="", chevron=True):
        super().__init__()
        self.title, self.color, self.glyph = title, color, glyph
        self.value, self.sub, self.chevron = value, sub, chevron
        self.first = self.last = False
        self.active = True
        self._down = False
        self._hover = 0.0
        self.setFixedHeight(64 if sub else 56)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover_anim = QtCore.QVariantAnimation(self)
        self._hover_anim.setDuration(180)
        self._hover_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover)

    def _on_hover(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        if self.active:
            self._hover_anim.stop()
            self._hover_anim.setStartValue(self._hover)
            self._hover_anim.setEndValue(1.0)
            self._hover_anim.start()

    def leaveEvent(self, e):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    def set_value(self, v, active=True):
        self.value, self.active = v, active
        self.update()

    def mousePressEvent(self, e):
        if self.active and e.button() == Qt.LeftButton:
            self._down = True
            self.update()

    def mouseReleaseEvent(self, e):
        was, self._down = self._down, False
        self.update()
        if was and self.rect().contains(e.pos()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r, w, h = self.rect(), self.width(), self.height()

        # hover / press background
        if self._down or self._hover > 0.01:
            alpha = 1.0 if self._down else self._hover * 0.7
            col = QtGui.QColor(T["hl"])
            col.setAlphaF(alpha)
            ext = QtCore.QRectF(r)
            if not self.first:
                ext.setTop(-20)
            if not self.last:
                ext.setBottom(h + 20)
            path = QtGui.QPainterPath()
            path.addRoundedRect(ext, 20, 20)
            p.save()
            p.setClipRect(r)
            p.fillPath(path, col)
            p.restore()

        # slight scale on hover
        scale = 1.0 + self._hover * 0.035
        if scale != 1.0:
            p.translate(w / 2, h / 2)
            p.scale(scale, scale)
            p.translate(-w / 2, -h / 2)

        draw_icon(p, QtCore.QRectF(16, (h - 32) / 2, 32, 32), self.color, self.glyph)
        right = w - (38 if self.chevron else 16)
        p.setFont(qfont(15))
        vw = p.fontMetrics().horizontalAdvance(self.value)
        p.setPen(QtGui.QColor(T["dim"]))
        p.drawText(QtCore.QRectF(right - vw, 0, vw, h), Qt.AlignVCenter | Qt.AlignRight, self.value)
        avail = right - vw - 72
        p.setPen(QtGui.QColor(T["text"]))
        p.setFont(qfont(17))
        fm = p.fontMetrics()
        if self.sub:
            p.drawText(QtCore.QRectF(62, 10, avail, 24), Qt.AlignVCenter,
                       fm.elidedText(self.title, Qt.ElideRight, int(avail)))
            p.setFont(qfont(13))
            p.setPen(QtGui.QColor(T["dim"]))
            p.drawText(QtCore.QRectF(62, 34, avail, 20), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(avail)))
        else:
            p.drawText(QtCore.QRectF(62, 0, avail, h), Qt.AlignVCenter,
                       fm.elidedText(self.title, Qt.ElideRight, int(avail)))
        if self.chevron:
            draw_chevron(p, w - 24, h / 2)
        if not self.last:
            p.setPen(QtGui.QColor(T["sep"]))
            p.drawLine(62, h - 1, w, h - 1)


class StorageBar(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(24)
        self.total = 1
        self.old, self.new, self.colors, self.order = {}, {}, {}, []
        self.p = 1.0
        self.anim = QtCore.QVariantAnimation(self)
        self.anim.setDuration(820)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QtCore.QEasingCurve.OutBack)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self.p = float(v)
        self.update()

    def set_data(self, segs, total):
        self.total = max(total, 1)
        self.old = {n: self.old.get(n, 0) + (self.new.get(n, 0) - self.old.get(n, 0)) * self.p
                    for n in set(self.old) | set(self.new)}
        self.new = {n: s for n, c, s in segs}
        self.colors = {n: c for n, c, s in segs}
        self.order = [n for n, c, s in segs]
        self.anim.stop()
        self.anim.start()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = QtCore.QRectF(self.rect())
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.setClipPath(clip)
        p.fillRect(r, QtGui.QColor(T["track"]))
        x = 0.0
        for n in self.order:
            cur = self.old.get(n, 0) + (self.new[n] - self.old.get(n, 0)) * self.p
            sw = r.width() * cur / self.total
            if sw >= 0.8:
                p.fillRect(QtCore.QRectF(x, 0, max(sw - 1.2, 0.6), r.height()), QtGui.QColor(self.colors[n]))
            x += sw


class Toast(QtWidgets.QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet(
            f"background:{'#3a3a3c' if DARK_MODE else '#2c2c2e'};"
            "color:white;border-radius:20px;padding:12px 22px;font-size:15px;font-weight:500;"
        )
        self.fx = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fx)
        self.fx.setOpacity(0)
        self.hide()
        self.group = None

    def say(self, text):
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 42)
        self.show()
        self.raise_()
        if self.group:
            self.group.stop()
        g = QtCore.QSequentialAnimationGroup(self)
        an = QtCore.QPropertyAnimation(self.fx, b"opacity")
        an.setDuration(280)
        an.setStartValue(0)
        an.setEndValue(1)
        an.setEasingCurve(QtCore.QEasingCurve.OutBack)
        g.addAnimation(an)
        g.addPause(2100)
        out = QtCore.QPropertyAnimation(self.fx, b"opacity")
        out.setDuration(380)
        out.setStartValue(1)
        out.setEndValue(0)
        out.setEasingCurve(QtCore.QEasingCurve.InCubic)
        g.addAnimation(out)
        g.finished.connect(self.hide)
        self.group = g
        g.start()


class NavStack(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.pages = []
        self.busy = False

    def push(self, page):
        if self.busy:
            return
        page.setParent(self)
        w, h = self.width(), self.height()
        old = self.pages[-1] if self.pages else None
        self.pages.append(page)
        if old is None:
            page.setGeometry(0, 0, w, h)
            page.show()
            return
        page.setGeometry(w, 0, w, h)
        page.setWindowOpacity(0.0)
        page.show()
        page.raise_()
        self._slide(old, page, True)

    def pop(self):
        if self.busy or len(self.pages) < 2:
            return
        top = self.pages.pop()
        below = self.pages[-1]
        below.setGeometry(-int(self.width() * 0.28), 0, self.width(), self.height())
        below.setWindowOpacity(0.85)
        below.show()
        top.raise_()
        self._slide(top, below, False)

    def _slide(self, a, b, forward):
        self.busy = True
        w = self.width()
        g = QtCore.QParallelAnimationGroup(self)

        def mk_pos(widget, s, e, duration=380):
            an = QtCore.QPropertyAnimation(widget, b"pos")
            an.setDuration(duration)
            an.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            an.setStartValue(QtCore.QPoint(s, 0))
            an.setEndValue(QtCore.QPoint(e, 0))
            g.addAnimation(an)

        def mk_opacity(widget, s, e, duration=340):
            an = QtCore.QPropertyAnimation(widget, b"windowOpacity")
            an.setDuration(duration)
            an.setEasingCurve(QtCore.QEasingCurve.OutCubic)
            an.setStartValue(s)
            an.setEndValue(e)
            g.addAnimation(an)

        if forward:
            mk_pos(a, 0, -int(w * 0.28))
            mk_pos(b, w, 0)
            mk_opacity(a, 1.0, 0.75)
            mk_opacity(b, 0.0, 1.0)
        else:
            mk_pos(a, 0, w)
            mk_pos(b, -int(w * 0.28), 0)
            mk_opacity(a, 1.0, 0.0)
            mk_opacity(b, 0.85, 1.0)

        def done():
            self.busy = False
            if forward:
                a.hide()
                a.setWindowOpacity(1.0)
            else:
                a.dead = True
                a.deleteLater()
            self._relayout()

        g.finished.connect(done)
        g.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _relayout(self):
        for pg in self.pages:
            pg.setGeometry(0, 0, self.width(), self.height())
            pg.setWindowOpacity(1.0)

    def resizeEvent(self, e):
        if not self.busy:
            self._relayout()


# ───────────────────────── главная страница ─────────────────────────

def section_label(text):
    lb = QtWidgets.QLabel(text.upper())
    lb.setFont(qfont(13, QtGui.QFont.Medium))
    lb.setStyleSheet(f"color:{T['dim']};padding:26px 0 8px 18px;letter-spacing:0.6px;")
    return lb


SCROLL_CSS = (
    "QScrollArea{background:transparent;border:0;}"
    "QScrollArea>QWidget>QWidget{background:transparent;}"
    f"QScrollBar:vertical{{width:6px;background:transparent;margin:4px 2px;}}"
    f"QScrollBar::handle:vertical{{background:{T['sep']};border-radius:3px;min-height:40px;}}"
    f"QScrollBar::handle:vertical:hover{{background:{T['dim']};}}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
)


class HomePage(Page):
    open_category = QtCore.pyqtSignal(str)

    def __init__(self, drive, cats, admin):
        super().__init__()
        self.cats, self.drive = cats, drive
        total, used, free = shutil.disk_usage(drive)
        self.total, self.used, self.free = total, used, free
        self.sizes = {}

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_CSS)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        inner = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(inner)
        col.setContentsMargins(20, 28, 20, 48)
        col.setSpacing(0)
        content = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        col.addWidget(centered(content))
        col.addStretch(1)
        scroll.setWidget(inner)

        title = QtWidgets.QLabel("Хранилище")
        title.setFont(qfont(36, QtGui.QFont.Bold))
        title.setStyleSheet(f"color:{T['text']};padding-left:18px;")
        sub = QtWidgets.QLabel(platform.node() or "Этот ПК")
        sub.setFont(qfont(15))
        sub.setStyleSheet(f"color:{T['dim']};padding:4px 0 18px 18px;")
        cl.addWidget(title)
        cl.addWidget(sub)

        self.disk = Card((18, 16, 18, 18), radius=22)
        top = QtWidgets.QHBoxLayout()
        dn = QtWidgets.QLabel(f"Диск {os.path.splitdrive(drive)[0] or '/'}")
        dn.setFont(qfont(17, QtGui.QFont.DemiBold))
        dn.setStyleSheet(f"color:{T['text']};")
        self.disk_val = QtWidgets.QLabel(f"{fmt(used)} из {fmt(total)}")
        self.disk_val.setFont(qfont(15))
        self.disk_val.setStyleSheet(f"color:{T['dim']};")
        top.addWidget(dn)
        top.addStretch(1)
        top.addWidget(self.disk_val)
        self.disk.lay.addLayout(top)
        self.disk.lay.addSpacing(14)
        self.bar = StorageBar()
        self.disk.lay.addWidget(self.bar)
        self.disk.lay.addSpacing(14)
        self.legend = QtWidgets.QLabel()
        self.legend.setFont(qfont(13))
        self.legend.setTextFormat(Qt.RichText)
        self.legend.setStyleSheet(f"color:{T['dim']};")
        self.disk.lay.addWidget(self.legend)
        cl.addWidget(self.disk)

        cl.addWidget(section_label("Рекомендации"))
        self.big_row = Row("Крупные файлы", RED, GLYPH["Крупные файлы"], "Подсчёт…",
                           sub=f"Файлы больше {fmt(BIG_MIN)}")
        self.tmp_row = Row("Временные файлы", "#5ac8fa", GLYPH["Временные файлы"], "Подсчёт…",
                           sub="Можно безопасно очистить")
        rec = Card(radius=20)
        rec.add_rows([self.big_row, self.tmp_row])
        cl.addWidget(rec)
        self.big_row.clicked.connect(lambda: self.open_category.emit("Крупные файлы"))
        self.tmp_row.clicked.connect(lambda: self.open_category.emit("Временные файлы"))
        self.big_row.active = self.tmp_row.active = False

        cl.addWidget(section_label("Категории"))
        self.rows = {}
        rows = []
        for name, color, label, paths in cats:
            r = Row(name, color, GLYPH.get(name, "▤"), "Подсчёт…")
            r.active = False
            r.clicked.connect(lambda n=name: self.open_category.emit(n))
            self.rows[name] = r
            rows.append(r)
        self.other_row = Row("Прочее", "#636366", GLYPH["Прочее"], "Подсчёт…")
        self.other_row.active = False
        self.other_row.clicked.connect(lambda: self.open_category.emit("Прочее"))
        self.rows["Прочее"] = self.other_row
        rows.append(self.other_row)
        card = Card(radius=20)
        card.add_rows(rows)
        cl.addWidget(card)

        note = ("mbeginnings / github.com/m69541919-eng" if admin else
                "mbeginnings / github.com/m69541919-eng")
        foot = QtWidgets.QLabel(note)
        foot.setWordWrap(True)
        foot.setFont(qfont(13))
        foot.setStyleSheet(f"color:{T['dim']};padding:14px 18px 8px 18px;")
        cl.addWidget(foot)
        self.refresh_bar(False)

    def refresh_bar(self, finished):
        segs = [(n, c, self.sizes[n]) for n, c, _, _ in self.cats if self.sizes.get(n, 0) > 0]
        if self.sizes.get("Прочее", 0) > 0:
            segs.append(("Прочее", "#636366", self.sizes["Прочее"]))
        self.bar.set_data(segs, self.total)
        cells = [f'<span style="color:{c}">●</span>&nbsp;{n}' for n, c, _ in segs]
        cells.append(f'<span style="color:{T["track"]}">●</span>&nbsp;Свободно {fmt(self.free)}')
        html = "<table cellspacing='0' cellpadding='4'>"
        for i in range(0, len(cells), 3):
            html += "<tr>" + "".join(f"<td width='33%'>{c}</td>" for c in cells[i:i + 3]) + "</tr>"
        self.legend.setText(html + "</table>")

    def set_category(self, name, size):
        self.sizes[name] = size
        if name in self.rows:
            self.rows[name].set_value(fmt(size), True)
        self.refresh_bar(False)

    def finish(self, big, temp):
        self.big_row.set_value(fmt(sum(i[2] for i in big)), True)
        self.tmp_row.set_value(fmt(sum(i[2] for i in temp)), True)
        self.refresh_bar(True)


# ───────────────────────── список файлов с превью ─────────────────────────

class ItemDelegate(QtWidgets.QStyledItemDelegate):
    def sizeHint(self, opt, idx):
        return QtCore.QSize(100, 68)

    def paint(self, p, opt, idx):
        name, path, size, is_dir, label = idx.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = opt.rect

        if opt.state & (QtWidgets.QStyle.State_Selected | QtWidgets.QStyle.State_MouseOver):
            p.fillRect(r, QtGui.QColor(T["hl"]))

        icon_rect = QtCore.QRectF(r.left() + 14, r.top() + 14, THUMB_SIZE, THUMB_SIZE)

        thumb = None
        if not is_dir and is_image(path):
            thumb = get_thumbnail(path, THUMB_SIZE)

        if thumb and not thumb.isNull():
            path_clip = QtGui.QPainterPath()
            path_clip.addRoundedRect(icon_rect, 8, 8)
            p.setClipPath(path_clip)
            p.drawPixmap(icon_rect.toRect(), thumb)
            p.setClipping(False)
            p.setPen(QtGui.QPen(QtGui.QColor(T["sep"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(icon_rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        else:
            color = LABEL_COLOR.get(label, "#8e8e93")
            if is_video(path):
                glyph = "▶"
                color = "#ff375f"
            elif is_dir:
                glyph = "▤"
            else:
                glyph = "▫"
            draw_icon(p, icon_rect, color, glyph)

        right = r.right() - (36 if is_dir else 16)
        p.setFont(qfont(15))
        sz = fmt(size)
        vw = p.fontMetrics().horizontalAdvance(sz)
        p.setPen(QtGui.QColor(T["dim"]))
        p.drawText(QtCore.QRectF(right - vw, r.top(), vw, r.height()), Qt.AlignVCenter | Qt.AlignRight, sz)

        avail = right - vw - r.left() - 70
        p.setPen(QtGui.QColor(T["text"]))
        p.setFont(qfont(16))
        p.drawText(QtCore.QRectF(r.left() + 66, r.top() + 12, avail, 24), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(name, Qt.ElideMiddle, int(avail)))

        p.setPen(QtGui.QColor(T["dim"]))
        p.setFont(qfont(12))
        sub = f"{label} · {os.path.dirname(path)}"
        if is_video(path):
            sub = "Видео · " + os.path.dirname(path)
        elif is_image(path):
            sub = "Фото · " + os.path.dirname(path)
        p.drawText(QtCore.QRectF(r.left() + 66, r.top() + 36, avail, 20), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(sub, Qt.ElideMiddle, int(avail)))

        if is_dir:
            draw_chevron(p, r.right() - 24, r.center().y())
        if idx.row() < idx.model().rowCount() - 1:
            p.setPen(QtGui.QColor(T["sep"]))
            p.drawLine(r.left() + 66, r.bottom(), r.right(), r.bottom())
        p.restore()


class ListPage(Page):
    back = QtCore.pyqtSignal()
    open_dir = QtCore.pyqtSignal(tuple)
    ask_delete = QtCore.pyqtSignal(object, tuple)

    def __init__(self, title, back_title, items=None):
        super().__init__()
        self.title = title
        self.items = []
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 20)
        outer.setSpacing(0)
        body = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        outer.addWidget(centered(body), 1)

        btn = QtWidgets.QPushButton("‹  " + back_title)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFont(qfont(17))
        btn.setStyleSheet(
            f"QPushButton{{color:{T['blue']};background:transparent;border:none;"
            f"text-align:left;padding:8px 12px 8px 6px;border-radius:10px;}}"
            f"QPushButton:hover{{background:{T['hl']};}}"
            f"QPushButton:pressed{{color:{T['dim']};background:{T['hl']};}}"
        )
        btn.clicked.connect(self.back.emit)
        lay.addWidget(btn, 0, Qt.AlignLeft)

        t = QtWidgets.QLabel(title)
        t.setFont(qfont(32, QtGui.QFont.Bold))
        t.setStyleSheet(f"color:{T['text']};padding:4px 0 0 6px;")
        self.sub = QtWidgets.QLabel("Подсчёт…")
        self.sub.setFont(qfont(14))
        self.sub.setStyleSheet(f"color:{T['dim']};padding:2px 0 14px 6px;")
        lay.addWidget(t)
        lay.addWidget(self.sub)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("🔍  Поиск")
        self.search.setFont(qfont(16))
        self.search.setStyleSheet(
            f"QLineEdit{{background:{T['field']};color:{T['text']};border:none;"
            "border-radius:12px;padding:11px 14px;}}"
        )
        self.search.textChanged.connect(self.apply_filter)
        lay.addWidget(self.search)
        lay.addSpacing(16)

        self.list = QtWidgets.QListWidget()
        self.list.setItemDelegate(ItemDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setStyleSheet(
            f"QListWidget{{background:{T['card']};border-radius:20px;padding:6px 0;outline:none;}}"
            f"QScrollBar:vertical{{width:6px;background:transparent;margin:8px 2px;}}"
            f"QScrollBar::handle:vertical{{background:{T['sep']};border-radius:3px;min-height:40px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{T['dim']};}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self.list.itemClicked.connect(self.on_click)
        self.list.customContextMenuRequested.connect(self.on_menu)
        lay.addWidget(self.list, 1)
        if items is not None:
            self.set_items(items)

    def set_items(self, items):
        if self.dead:
            return
        self.items = items
        self.list.clear()
        for it in items[:MAX_ROWS]:
            li = QtWidgets.QListWidgetItem()
            li.setData(Qt.UserRole, it)
            self.list.addItem(li)
        self.update_sub()

    def update_sub(self):
        n = len(self.items)
        self.sub.setText("Пусто" if n == 0 else f"{n} объектов · {fmt(sum(i[2] for i in self.items))}")

    def remove(self, data):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == data:
                self.list.takeItem(i)
                break
        self.items = [x for x in self.items if x != data]
        self.update_sub()

    def apply_filter(self, text):
        q = text.lower().strip()
        for i in range(self.list.count()):
            d = self.list.item(i).data(Qt.UserRole)
            self.list.item(i).setHidden(bool(q) and q not in (d[0] + d[1]).lower())

    def on_click(self, item):
        d = item.data(Qt.UserRole)
        if d[3]:
            self.open_dir.emit(d)
        else:
            reveal(d[1])

    def on_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        d = item.data(Qt.UserRole)
        m = QtWidgets.QMenu(self)
        m.setStyleSheet(
            f"QMenu{{background:{T['card']};color:{T['text']};border:1px solid {T['sep']};"
            f"border-radius:14px;padding:6px;font-size:15px;}}"
            f"QMenu::item{{padding:10px 24px;border-radius:10px;}}"
            f"QMenu::item:selected{{background:{T['hl']};}}"
            f"QMenu::separator{{height:1px;background:{T['sep']};margin:4px 10px;}}"
        )
        a1 = m.addAction("Показать в проводнике")
        a2 = m.addAction("Копировать путь")
        m.addSeparator()
        a3 = m.addAction("Удалить в корзину…")
        r = m.exec_(self.list.viewport().mapToGlobal(pos))
        if r == a1:
            reveal(d[1])
        elif r == a2:
            QtWidgets.QApplication.clipboard().setText(d[1])
        elif r == a3:
            self.ask_delete.emit(self, d)


# ───────────────────────── окно ─────────────────────────

class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Хранилище")
        self.resize(560, 920)
        self.setMinimumSize(420, 640)
        self.nav = NavStack()
        self.setCentralWidget(self.nav)
        self.drive, self.cats, self.seen = build_categories()
        for n, c, l, _ in self.cats:
            LABEL_COLOR[l] = c
        self.items, self.big, self.temp, self.other, self.workers = {}, [], [], [], []
        self.home = HomePage(self.drive, self.cats, is_admin())
        self.nav.push(self.home)
        self.toast = Toast(self)
        self.home.open_category.connect(self.open_category)
        self.scanner = Scanner(self.cats, self.drive, self.seen)
        self.scanner.category.connect(self.on_category)
        self.scanner.finished_all.connect(self.on_finished)
        self.scanner.start()

    def showEvent(self, e):
        super().showEvent(e)
        if OS == "Windows" and DARK_MODE:
            try:
                v = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(v), 4)
            except Exception:
                pass

    def on_category(self, name, size, items):
        self.items[name] = items
        self.home.set_category(name, size)

    def on_finished(self, big, temp, other):
        self.big, self.temp, self.other = big, temp, other
        self.items["Прочее"] = other
        self.home.finish(big, temp)

    def open_category(self, name):
        if self.nav.busy:
            return
        data = {
            "Крупные файлы": self.big,
            "Временные файлы": self.temp,
            "Прочее": self.other
        }.get(name, self.items.get(name, []))
        self.push_list(ListPage(name, "Хранилище", data))

    def push_list(self, page):
        page.back.connect(self.nav.pop)
        page.open_dir.connect(lambda d, pg=page: self.open_dir(d, pg.title))
        page.ask_delete.connect(self.delete)
        self.nav.push(page)

    def open_dir(self, d, parent_title):
        if self.nav.busy:
            return
        name, path, _, _, label = d
        page = ListPage(name, parent_title)
        self.push_list(page)
        w = DirWorker(path, label)
        w.result.connect(page.set_items)
        self.workers.append(w)
        w.start()

    def delete(self, page, d):
        name, path, size, is_dir, label = d
        protected = is_protected(path, label)

        box1 = QtWidgets.QMessageBox(self)
        box1.setWindowTitle("Удалить?")
        box1.setText(f"Переместить «{name}» в корзину?")
        info = f"Освободится {fmt(size)}. Файл можно восстановить из корзины."
        if protected:
            info += "\n\n⚠ Это системный файл/папка. Удаление может нарушить работу Windows."
        box1.setInformativeText(info)
        box1.setIcon(QtWidgets.QMessageBox.Warning)
        yes1 = box1.addButton("Удалить", QtWidgets.QMessageBox.DestructiveRole)
        box1.addButton("Отмена", QtWidgets.QMessageBox.RejectRole)
        box1.exec_()
        if box1.clickedButton() is not yes1:
            return

        if protected:
            box2 = QtWidgets.QMessageBox(self)
            box2.setWindowTitle("Точно удалить системный файл?")
            box2.setText(f"Вы уверены, что хотите удалить системный объект?\n\n«{name}»")
            box2.setInformativeText(
                "Это действие может сломать систему, драйверы или программы.\n"
                "Удаляйте только если точно знаете, что делаете.\n\n"
                "Файл всё равно попадёт в корзину — его можно будет восстановить."
            )
            box2.setIcon(QtWidgets.QMessageBox.Critical)
            yes2 = box2.addButton("Да, удалить", QtWidgets.QMessageBox.DestructiveRole)
            box2.addButton("Отмена", QtWidgets.QMessageBox.RejectRole)
            box2.exec_()
            if box2.clickedButton() is not yes2:
                return

        if recycle(path):
            page.remove(d)
            self.toast.say(f"В корзине · {fmt(size)}")
        else:
            self.toast.say("Не удалось удалить (нет доступа или занят)")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2,
                            self.height() - self.toast.height() - 42)


def main():
    global FONT_FAMILY
    if OS == "Windows" and not is_admin():
        if relaunch_as_admin():
            sys.exit(0)

    QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)

    have = set(QtGui.QFontDatabase().families())
    for f in ("SF Pro Display", "SF Pro Text", "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue", "Inter", "Arial"):
        if f in have:
            FONT_FAMILY = f
            break

    app.setFont(qfont(15))
    app.setStyleSheet(
        f"QMessageBox{{background:{T['card']};}}"
        f"QMessageBox QLabel{{color:{T['text']};}}"
        f"QMessageBox QPushButton{{background:{T['hl']};color:{T['text']};border:none;"
        "border-radius:12px;padding:10px 20px;min-width:78px;}}"
    )
    w = Window()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
