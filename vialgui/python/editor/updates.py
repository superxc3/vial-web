# SPDX-License-Identifier: GPL-2.0-or-later
"""Updates tab (web build only).

Tells the client whether newer firmware exists for their board and hands them the .uf2 with the
instructions to flash it. The board and its version come from the USB product string the keyboard
announces (see the naming rule and PRODUCT_RE in vial-web/firmware/make-manifest.py — keep them in
sync); the catalogue is firmware_manifest.json, preloaded by vial-web's build.

Also hosts the "Keymap mirrored?" fix for SoflePLUS2 (mostly older batches): after an update the half with the
USB cable can come up as the wrong hand (EE_HANDS). The page cannot fire a keycode itself, so it puts
MAGIC_EE_HANDS_LEFT and QK_REBOOT on two keys for the client to press, and puts the original keycodes
back the next time the keyboard connects (see MirrorFix).
"""
import html
import json
import os
import pathlib
import re
import sys

from PyQt5.QtCore import Qt, QUrl, QRectF, QStandardPaths
from PyQt5.QtGui import QDesktopServices, QPainter, QColor, QPen, QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QDialog,
                             QPushButton, QComboBox, QScrollArea, QFrame, QSizePolicy, QToolButton)

from editor.basic_editor import BasicEditor
from util import tr, persist_app_data
from vial_device import VialKeyboard

# Keep in sync with vial-web/firmware/make-manifest.py
PRODUCT_RE = re.compile(
    r"^(?P<family>SoflePLUS2?|CornePLUS2?)"
    r"(?: (?P<year>\(2025\)))?"
    r"(?: (?P<trackpad>TPS43|TPS65))?"
    r"(?: (?P<horizontal>Horizontal))?"
    r" v(?P<version>\d+\.\d+(?:\.\d+)?[a-z]*)"
    r"(?: (?P<batch>Legendary RGB|Legendary|Signature RGB))?"
    r"(?: (?P<beta>Beta.*))?$"
)

# Older SoflePLUS2 firmware names the batch inconsistently (or not at all), so the board is only
# detected from v5.10 on; anything older gets the manual list.
_DETECT_FROM = (5, 10)

# SoflePLUS2 firmware enters update mode when the 3 outer top-row keys are held, from v5.11b on
# (the "h" in e.g. 5.11hb only marks the Horizontal build)
_CHORD_FROM = (5, 11, 0, "b")

# Batches whose half with the USB cable can come up as the wrong hand after an update (EE_HANDS)

# (glyph, colour) per status; the same convention as app stores / browsers: green = fine,
# amber = needs attention, grey = cannot tell
_STATUS_STYLES = {
    "ok": ("✓", "#3cb371"),
    "update": ("⚠", "#e6a817"),
    "unknown": ("?", "#9e9e9e"),
    "info": ("•", ""),
}
_MUTED = "color: #9e9e9e"
_AMBER = "#e6a817"
_GREEN = "#3cb371"

_BATCH_HINT = ("“Batch” is the PCB generation of your keyboard (see your order or the XCMKB docs), "
               "not the firmware version. Not sure which SoflePLUS version you have? Check your batch in the "
               "<a href=\"{url}\">firmware changelog</a>. CornePLUS2: choose “2025, no disc” if your keyboard "
               "has no spinning disc, “current, with disc” if it has one.")

_FALLBACK_CHANGELOG = "https://xcmkb-docs.gitbook.io/doc/firmware-changelog"

_OVERRIDE_NOTE = ("Your keyboard reports “{product}”, but you chose “{board}”. Update now will replace the "
                  "firmware with the version you select. If a flash goes wrong it is always recoverable: double-press "
                  "the reset button and write the correct file to the RPI-RP2 drive.")

_STEPS = """Both halves must run the same version. Update one half, then the other.

1.  Click Update now and confirm that your layout (.vil) is saved. The half with the USB cable restarts into update mode by itself (LED blinking, a drive named RPI-RP2 appears).
2.  Choose Write to RPI-RP2 drive: in the folder dialog select the drive named RPI-RP2 itself and click Select Folder (the firmware is already loaded, no driver needed). Or use Select keyboard (USB). The half restarts by itself when the write is done.
3.  Unplug the USB cable and plug it directly into the OTHER half (the cable between the halves does not carry the update). Click Reload page, press Start Vial, and repeat steps 1 and 2 on this tab.
4.  Finish: plug back into the half you normally use, reload, press Start Vial and load your .vil if needed.

If a half does not go into update mode by itself: unplug the cable, hold its top outer corner key (left half: `, right half: -) and plug the cable back in while holding it. On SoflePLUS2 v5.11b or newer the 3 top corner keys (` 1 2 or - 0 9) work too. Or double-press the reset button on the bottom of that half, about half a second apart.

Keys mirrored after an update (pressing 1 types 0)? Use Keymap mirrored?.

Without WebUSB (or if the update dialog cannot see the keyboard): Download .uf2, put the half into update mode by hand as above and copy the .uf2 onto the RPI-RP2 drive that appears."""


def _version_key(version):
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?([a-z]*)$", version)
    if not m:
        return (0, 0, 0, version)
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4)


def _has_chord(board, version):
    """Whether the running firmware enters update mode on the 3-key hold."""
    if not board or board["family"] != "SoflePLUS2" or not version:
        return False
    key = _version_key(version)
    return key[:3] + (key[3].replace("h", ""),) >= _CHORD_FROM


def _wrapped(text="", bold=False, style=""):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    css = ["font-weight: bold"] if bold else []
    if style:
        css.append(style)
    if css:
        lbl.setStyleSheet("; ".join(css))
    return lbl


def _primary(button):
    button.setDefault(True)
    button.setStyleSheet("font-weight: bold")
    return button


def _show_modal(dialog):
    """How dialogs are shown on the web build (same as Keymap's reset/restore questions and About):
    no parent, application-modal, show(). exec_() needs a nested event loop the web build does not
    have ("compile with async support"), and a parented open() (window-modal) is drawn as an empty
    frame there."""
    dialog.setModal(True)
    dialog.adjustSize()
    dialog.show()


def _centered_scroll(inner_layout):
    """Same framing as the Trackpad/OLED tabs: natural width, centred, scrollable."""
    content = QWidget()
    content.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    content.setLayout(inner_layout)
    outer = QVBoxLayout()
    outer.addWidget(content)
    outer.setAlignment(content, Qt.AlignHCenter)
    w = QWidget()
    w.setLayout(outer)
    w.setObjectName("updatesInner")
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea { background-color: transparent; }")
    w.setStyleSheet("#updatesInner { background-color: transparent; }")
    scroll.setWidgetResizable(True)
    scroll.setWidget(w)
    return scroll


class MiniHalf(QWidget):
    """A small left half (6 x 4 keys, column stagger) with some keys marked: {(row, col): (colour, label)}.
    Only a pointer to where the keys are, so it stays small (about a sixth of the Matrix tester)."""

    KEY = 20
    GAP = 4
    STAGGER = (3, 3, 0, -2, 0, 1)

    def __init__(self, marks, parent=None):
        super().__init__(parent)
        self.marks = marks
        step = self.KEY + self.GAP
        self.setFixedSize(6 * step + 2, 4 * step + 8)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        step = self.KEY + self.GAP
        font = QFont(p.font())
        font.setBold(True)
        font.setPixelSize(12)
        p.setFont(font)
        base = self.palette().color(self.foregroundRole())
        for r in range(4):
            for c in range(6):
                rect = QRectF(c * step + 1, r * step + 4 + self.STAGGER[c], self.KEY, self.KEY)
                mark = self.marks.get((r, c))
                if mark:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(mark[0]))
                else:
                    edge = QColor(base)
                    edge.setAlpha(90)
                    p.setPen(QPen(edge, 1))
                    p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect, 3, 3)
                if mark and mark[1]:
                    p.setPen(QColor("#1e1e1e"))
                    p.drawText(rect, Qt.AlignCenter, mark[1])
        p.end()


class SaveLayoutCheck(QDialog):
    """Asked before every update: the flash can reset the keymap."""

    def __init__(self, keyboard, parent=None):
        super().__init__(parent)
        self.keyboard = keyboard
        self.setWindowTitle(tr("Updates", "Before updating"))
        self.setFixedWidth(460)     # lets the wrapped labels work out their height
        lay = QVBoxLayout(self)
        lay.addWidget(_wrapped(tr("Updates", "Have you saved your layout (.vil)?"), bold=True))
        lay.addWidget(_wrapped(tr("Updates", "Updating can reset your keymap. Save it first so you can load it "
                                             "back afterwards (File → Load saved layout…).")))
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("Updates", "Cancel"))
        cancel.clicked.connect(self.reject)
        self.btn_save = QPushButton(tr("Updates", "Save layout now…"))
        self.btn_save.clicked.connect(self._save)
        self.btn_go = _primary(QPushButton(tr("Updates", "Yes, continue")))
        self.btn_go.clicked.connect(self.accept)
        for b in (cancel, self.btn_save, self.btn_go):
            buttons.addWidget(b)
        lay.addLayout(buttons)

    def _save(self):
        if sys.platform != "emscripten":
            return
        import vialglue
        # same path as File → Save current layout…: the page opens the browser's save dialog
        vialglue.save_layout(self.keyboard.save_layout())
        self.btn_go.setText(tr("Updates", "Saved — continue"))
        self.btn_go.setFocus()
        self.layout().activate()


class MirrorFix:
    """Mostly older batches: after an update the half with the USB cable can think it is the right half
    (EE_HANDS handedness lost), so the left keys come out mirrored (1 types 0). The fix puts
    MAGIC_EE_HANDS_LEFT on the top-left key and QK_REBOOT on the key below it, on layer 0, at the
    left-half position AND its right-half mirror (while mirrored the left half reports the right
    half's rows). The original keycodes are kept in the app data dir and written back on the next
    connect, before the other tabs read the keymap."""

    FILE = "mirror_fix_pending.json"
    SET_LEFT = "MAGIC_EE_HANDS_LEFT"
    REBOOT = "QK_REBOOT"

    @classmethod
    def _path(cls):
        directory = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
        return os.path.join(directory, cls.FILE)

    @staticmethod
    def positions(keyboard):
        """{(row, col): keycode} for the two marked keys and their mirrors, or None if the matrix is
        not split into two equal halves of rows (left rows first, outer column 0 on both)."""
        rows = keyboard.rows
        if rows < 4 or rows % 2:
            return None
        half = rows // 2
        targets = {(0, 0): MirrorFix.SET_LEFT, (half, 0): MirrorFix.SET_LEFT,
                   (1, 0): MirrorFix.REBOOT, (half + 1, 0): MirrorFix.REBOOT}
        for (r, c) in targets:
            if (0, r, c) not in keyboard.layout:
                return None
        return targets

    @classmethod
    def apply(cls, keyboard, product):
        targets = cls.positions(keyboard)
        if targets is None:
            raise RuntimeError("unexpected matrix")
        pending = cls.load()
        if pending and pending.get("product") == product:
            originals = pending["keys"]     # applied before and not restored yet: keep the real originals
        else:
            originals = {"{},{}".format(r, c): keyboard.layout[(0, r, c)] for (r, c) in targets}
        with open(cls._path(), "w", encoding="utf-8") as outf:
            json.dump({"product": product, "keys": originals}, outf)
        persist_app_data()
        for (r, c), code in targets.items():
            keyboard.set_key(0, r, c, code)

    @classmethod
    def load(cls):
        try:
            with open(cls._path(), "r", encoding="utf-8") as inf:
                data = json.load(inf)
            return data if isinstance(data.get("keys"), dict) else None
        except (OSError, ValueError, AttributeError):
            return None

    @classmethod
    def restore(cls, keyboard, product):
        """Write the saved keycodes back if a fix is pending for this keyboard; True if it did."""
        pending = cls.load()
        if not pending or pending.get("product") != product:
            return False
        for pos, code in pending["keys"].items():
            r, c = (int(x) for x in pos.split(","))
            if (0, r, c) in keyboard.layout:
                keyboard.set_key(0, r, c, code)
        try:
            os.remove(cls._path())
        except OSError:
            pass
        persist_app_data()
        return True


class MirrorFixDialog(QDialog):

    def __init__(self, keyboard, product, parent=None):
        super().__init__(parent)
        self.keyboard = keyboard
        self.product = product
        self.applied = False
        self.setWindowTitle(tr("Updates", "Fix a mirrored keymap"))
        self.setFixedWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(_wrapped(tr("Updates", "Fix a mirrored keymap"), bold=True))
        lay.addWidget(_wrapped(tr("Updates", "After an update the keymap can come out mirrored (mostly on older batches) when "
                                             "the USB cable is in the left half (pressing 1 types 0). This tells the "
                                             "keyboard that the half with the cable is the left half.")))
        lay.addWidget(_wrapped(tr("Updates", "Make sure the USB cable is plugged into the LEFT half."), bold=True))

        pic = MiniHalf({(0, 0): (_AMBER, "A"), (1, 0): (_GREEN, "B")})
        lay.addWidget(pic, 0, Qt.AlignHCenter)

        self.lbl_steps = _wrapped(tr("Updates", "Click Set up keys: the page changes two keys of the left half for a "
                                                "moment (A and B in the picture)."))
        lay.addWidget(self.lbl_steps)
        self.lbl_error = _wrapped(style="color: " + _AMBER)
        self.lbl_error.hide()
        lay.addWidget(self.lbl_error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.btn_cancel = QPushButton(tr("Updates", "Cancel"))
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_go = _primary(QPushButton(tr("Updates", "Set up keys")))
        self.btn_go.clicked.connect(self._go)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_go)
        lay.addLayout(buttons)

    def _go(self):
        if not self.applied:
            try:
                MirrorFix.apply(self.keyboard, self.product)
            except Exception as e:
                self.lbl_error.setText(tr("Updates", "Could not change the keys ({}). Reload the page and try "
                                                     "again.").format(e))
                self.lbl_error.show()
                return
            self.applied = True
            self.lbl_steps.setText(tr("Updates",
                                      "1.  Press key A (top-left corner) once: the keyboard now knows it is the left half.\n"
                                      "2.  Press key B (just below it) once: the keyboard restarts by itself.\n"
                                      "3.  Click Reconnect, then Start Vial. Keys A and B go back to normal "
                                      "automatically."))
            self.btn_cancel.setText(tr("Updates", "Undo and close"))
            self.btn_go.setText(tr("Updates", "Reconnect"))
            self.adjustSize()
            return
        if sys.platform == "emscripten":
            import vialglue
            if hasattr(vialglue, "reload_page"):
                vialglue.reload_page()
                return
        self.lbl_error.setText(tr("Updates", "Reload this page (F5), then click Start Vial."))
        self.lbl_error.show()

    def _cancel(self):
        if self.applied:
            try:
                MirrorFix.restore(self.keyboard, self.product)
            except Exception:
                pass      # the keyboard already restarted: the keys are put back on the next connect
        self.reject()

    def closeEvent(self, ev):
        # closing with the window button leaves the fix in place: the keys come back on reconnect
        super().closeEvent(ev)


class Updates(BasicEditor):

    def __init__(self, appctx):
        super().__init__()

        self.manifest = self._load_manifest(appctx)
        self.boards = self.manifest.get("boards", []) if self.manifest else []
        self.product = ""
        self.board = None        # manifest board the keyboard was matched (or chosen) to
        self.entry = None        # manifest firmware entry of the running version, if catalogued
        self.detected = False    # board came from the product string, not from the combo box
        self.override = False    # client asked to choose the board themselves (wrong-version troubleshooting)
        self.auto_board = None   # what the product string matched, kept while overriding
        self.auto_entry = None
        self.update_available = False
        self.mirror_restored = False

        inner = QVBoxLayout()
        inner.setContentsMargins(4, 4, 4, 4)
        inner.setSpacing(10)

        # 1. what matters first: the status and the one main action
        head = QHBoxLayout()
        head.setSpacing(16)
        self.lbl_status = _wrapped(bold=True)
        self.lbl_status.setMinimumWidth(420)
        head.addWidget(self.lbl_status, 1)
        self.btn_update = _primary(QPushButton(tr("Updates", "Update now")))
        self.btn_update.setToolTip(tr("Updates", "Writes the selected version to the keyboard from this page "
                                                 "(Chrome/Edge)."))
        self.btn_update.clicked.connect(self._on_update_now)
        head.addWidget(self.btn_update, 0, Qt.AlignTop)
        inner.addLayout(head)
        self.lbl_sub = _wrapped(style=_MUTED)
        inner.addWidget(self.lbl_sub)
        self.lbl_mirror_done = _wrapped(style="color: " + _GREEN)
        inner.addWidget(self.lbl_mirror_done)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        inner.addWidget(line)

        # 2. details
        info = QGridLayout()
        info.setHorizontalSpacing(16)
        info.setVerticalSpacing(6)
        info.setColumnMinimumWidth(1, 420)
        self.lbl_notes = _wrapped()
        self.combo_board = QComboBox()
        for b in self.boards:
            self.combo_board.addItem(b["name"], b["id"])
        self.combo_board.currentIndexChanged.connect(self._on_board_chosen)
        self.combo_version = QComboBox()
        self.combo_version.currentIndexChanged.connect(lambda _: self._show_notes())
        self.lbl_hint = _wrapped(style=_MUTED)
        self.lbl_hint.setTextFormat(Qt.RichText)
        self.lbl_hint.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.lbl_hint.setText(tr("Updates", _BATCH_HINT).format(url=html.escape(self._changelog_url(), quote=True)))
        self.lbl_hint.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.rows = {}
        for r, (key, caption, widget) in enumerate([
            ("choose", "Choose your board:", self.combo_board),
            ("hint", "", self.lbl_hint),
            ("version", "Version:", self.combo_version),
            ("notes", "Release notes:", self.lbl_notes),
        ]):
            cap = QLabel(tr("Updates", caption))
            cap.setAlignment(Qt.AlignRight | Qt.AlignTop)
            info.addWidget(cap, r, 0)
            info.addWidget(widget, r, 1)
            self.rows[key] = (cap, widget)
        inner.addLayout(info)

        # 3. secondary actions
        buttons = QHBoxLayout()
        self.btn_download = QPushButton(tr("Updates", "Download .uf2"))
        self.btn_download.clicked.connect(self._on_download)
        self.btn_changelog = QPushButton(tr("Updates", "View changelog"))
        self.btn_changelog.clicked.connect(self._on_changelog)
        self.btn_mirror = QPushButton(tr("Updates", "Keymap mirrored?"))
        self.btn_mirror.setToolTip(tr("Updates", "Keys come out mirrored after an update "
                                                 "(pressing 1 types 0)? Fix it here."))
        self.btn_mirror.clicked.connect(self._on_mirror)
        self.btn_troubleshoot = QPushButton()
        self.btn_troubleshoot.clicked.connect(self._on_troubleshoot)
        for b in (self.btn_download, self.btn_changelog, self.btn_mirror, self.btn_troubleshoot):
            buttons.addWidget(b)
        buttons.addStretch()
        inner.addLayout(buttons)

        # 4. the long instructions, folded away
        self.btn_steps = QToolButton()
        self.btn_steps.setText(tr("Updates", "How to update (both halves, step by step)"))
        self.btn_steps.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_steps.setArrowType(Qt.RightArrow)
        self.btn_steps.setCheckable(True)
        self.btn_steps.setAutoRaise(True)
        self.btn_steps.toggled.connect(self._on_steps_toggled)
        inner.addWidget(self.btn_steps)
        self.lbl_steps = _wrapped(tr("Updates", _STEPS))
        self.lbl_steps.setContentsMargins(20, 0, 0, 0)
        self.lbl_steps.hide()
        inner.addWidget(self.lbl_steps)

        inner.addWidget(_wrapped(tr("Updates", "Firmware updates are in beta. Please report anything odd to XCMKB."),
                                 style=_MUTED + "; font-size: 11px"))

        self.addWidget(_centered_scroll(inner))

    @staticmethod
    def _load_manifest(appctx):
        # Only the web build ships the catalogue; without it the tab stays hidden
        try:
            path = appctx.get_resource("firmware_manifest.json")
        except Exception:
            return None
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as inf:
                return json.load(inf)
        except (OSError, ValueError):
            return None

    def _changelog_url(self):
        return (self.manifest or {}).get("changelog_url") or _FALLBACK_CHANGELOG

    def _manual(self):
        """The board list is shown: detection failed, or the client is troubleshooting a wrong version."""
        return not self.detected or self.override

    @property
    def keyboard(self):
        return self.device.keyboard if isinstance(self.device, VialKeyboard) else None

    # ---- detection ----

    def _detect(self, product):
        """(board, entry): an exact catalogue hit, a board matched by the naming rule, or (None, None)."""
        for b in self.boards:
            for fw in b["firmware"]:
                if fw["product"] == product:
                    return b, fw
        m = PRODUCT_RE.match(product)
        if not m:
            return None, None
        p = m.groupdict()
        if p["family"] == "SoflePLUS2" and _version_key(p["version"])[:2] < _DETECT_FROM:
            return None, None
        for b in self.boards:
            if (b["family"], b["trackpad"], b["horizontal"], b["batch"]) == \
                    (p["family"], p["trackpad"] or "", bool(p["horizontal"]), p["batch"] or ("2025" if p["year"] else "")):
                return b, None
        return None, None

    def _running_version(self):
        if self.entry:
            return self.entry["version"]
        m = PRODUCT_RE.match(self.product)
        return m.group("version") if m else ""

    @staticmethod
    def _firmware(board, version):
        for fw in board["firmware"]:
            if fw["version"] == version:
                return fw
        return None

    def _selected_firmware(self):
        if not self.board:
            return None
        return self._firmware(self.board, self.combo_version.currentData())

    def _set_status(self, kind, text):
        glyph, colour = _STATUS_STYLES[kind]
        self.lbl_status.setText(glyph + "  " + text)
        self.lbl_status.setStyleSheet("font-weight: bold; font-size: 15px" + ("; color: " + colour if colour else ""))

    def _show_notes(self):
        fw = self._selected_firmware()
        self.lbl_notes.setText((fw or {}).get("notes") or "-")

    def _refresh(self):
        self.update_available = False
        running = self._running_version()
        reports = tr("Updates", "your keyboard reports “{}”").format(self.product) if self.product \
            else tr("Updates", "your keyboard reports no product name")

        self.combo_version.blockSignals(True)
        self.combo_version.clear()
        if self.board:
            latest = self.board["latest"]
            for fw in self.board["firmware"]:
                text = "v{}  ({})".format(fw["version"], fw["date"])
                if fw["beta"]:
                    text += "  beta"
                if fw["version"] == latest:
                    text += "  ← latest"
                self.combo_version.addItem(text, fw["version"])
            self.combo_version.setCurrentIndex(max(0, self.combo_version.findData(latest)))
            if self.override and self.auto_board is not None and self.board is not self.auto_board:
                self._set_status("update", tr("Updates", _OVERRIDE_NOTE).format(
                    product=self.product, board=self.board["name"]))
            elif self.entry and running == latest:
                self._set_status("ok", tr("Updates", "Up to date (v{})").format(running))
            elif self.entry:
                self.update_available = True
                self._set_status("update", tr("Updates", "Update available: v{} → v{}").format(running, latest))
            elif running:
                self.update_available = True
                self._set_status("update", tr("Updates", "Update available: v{} (your v{} is not in the "
                                                        "catalogue)").format(latest, running))
            else:
                self._set_status("info", tr("Updates", "Latest for this board is v{}").format(latest))
            how = tr("Updates", "chosen by you") if self._manual() else tr("Updates", "detected")
            self.lbl_sub.setText("{} · {} · {}".format(self.board["name"], how, reports))
        else:
            self._set_status("unknown", tr("Updates", "Your board could not be identified from this firmware "
                                                    "(automatic detection needs v5.10 or newer). Choose it from the list."))
            self.lbl_sub.setText(reports[:1].upper() + reports[1:])
        self.combo_version.blockSignals(False)

        for key in ("choose", "hint"):
            for w in self.rows[key]:
                w.setVisible(self._manual())
        for key in ("version", "notes"):
            for w in self.rows[key]:
                w.setVisible(self.board is not None)
        self.btn_update.setEnabled(self.board is not None)
        self.btn_download.setEnabled(self.board is not None)
        mirror_board = self.auto_board or self.board
        self.btn_mirror.setVisible(bool(mirror_board) and mirror_board["family"] == "SoflePLUS2"
                                   and self.keyboard is not None and MirrorFix.positions(self.keyboard) is not None)
        self.lbl_mirror_done.setVisible(self.mirror_restored)
        self.lbl_mirror_done.setText(tr("Updates", "✓ Mirror fix done: keys A and B are back to normal. Still "
                                                   "mirrored? Run Keymap mirrored? again."))
        self.btn_troubleshoot.setVisible(self.auto_board is not None)
        self.btn_troubleshoot.setText(tr("Updates", "Use detected board") if self.override
                                      else tr("Updates", "Wrong version?"))
        self.btn_troubleshoot.setToolTip(tr("Updates", "Flashed the wrong variant? Choose your board yourself "
                                                       "and flash the correct firmware."))
        self._show_notes()

    # ---- slots ----

    def _on_steps_toggled(self, shown):
        self.btn_steps.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)
        self.lbl_steps.setVisible(shown)

    def _on_board_chosen(self, index):
        if index < 0 or not self._manual():
            return
        self.board = self.boards[index]
        self.entry = self.auto_entry if self.board is self.auto_board else None
        self._refresh()

    def _on_troubleshoot(self):
        self.override = not self.override
        if not self.override:
            self.board, self.entry = self.auto_board, self.auto_entry
        self.combo_board.blockSignals(True)
        self.combo_board.setCurrentIndex(self.boards.index(self.board) if self.board else -1)
        self.combo_board.blockSignals(False)
        self._refresh()

    def _on_mirror(self):
        if self.keyboard is None:
            return
        self._dialog = MirrorFixDialog(self.keyboard, self.product)
        _show_modal(self._dialog)

    def _on_update_now(self):
        fw = self._selected_firmware()
        if not fw or sys.platform != "emscripten" or self.keyboard is None:
            return
        self._dialog = SaveLayoutCheck(self.keyboard)
        self._dialog.accepted.connect(lambda: self._start_flash(fw))
        _show_modal(self._dialog)

    def _start_flash(self, fw):
        import vialglue
        # the 3-key hold is a feature of the firmware running NOW, so it follows the detected board
        chord = _has_chord(self.auto_board, self._running_version())
        entry = {"board": self.board["name"], "version": fw["version"], "file": fw["file"],
                 "size": fw["size"], "sha256": fw["sha256"], "product": fw["product"],
                 "reboot_requested": True, "chord": chord}
        vialglue.flash_firmware(json.dumps(entry))
        # Ask the running firmware to restart into its bootloader (VIA id_bootloader_jump). Firmware
        # that refuses (Vial keeps it locked) just echoes the packet; the client holds the 3 keys or
        # double-presses reset instead. Nothing talks to the keyboard after this until the page reloads.
        try:
            self.keyboard.reset()
        except Exception:
            pass

    def _on_download(self):
        fw = self._selected_firmware()
        if not fw:
            return
        if sys.platform == "emscripten":
            import vialglue
            vialglue.download_file("firmware/" + fw["file"])

    def _on_changelog(self):
        url = (self.board or {}).get("changelog_url") or self._changelog_url()
        QDesktopServices.openUrl(QUrl(url))

    # ---- BasicEditor interface ----

    def valid(self):
        return sys.platform == "emscripten" and bool(self.boards) and isinstance(self.device, VialKeyboard)

    def tab_title(self):
        title = tr("MainWindow", "Updates (beta)")
        return title + " *" if self.update_available else title

    def restore_pending(self, device):
        """Called by the main window before any tab reads the keymap: puts back the keys a mirror fix
        changed, so the Keymap tab never shows them."""
        self.mirror_restored = False
        if sys.platform != "emscripten" or not isinstance(device, VialKeyboard):
            return
        try:
            self.mirror_restored = MirrorFix.restore(device.keyboard, device.desc.get("product_string", "") or "")
        except Exception:
            self.mirror_restored = False

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return
        self.product = device.desc.get("product_string", "") or ""
        self.board, self.entry = self._detect(self.product)
        self.auto_board, self.auto_entry = self.board, self.entry
        self.override = False
        self.detected = self.board is not None
        self.combo_board.blockSignals(True)
        self.combo_board.setCurrentIndex(self.boards.index(self.board) if self.detected else -1)
        self.combo_board.blockSignals(False)
        self._refresh()
