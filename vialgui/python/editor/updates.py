# SPDX-License-Identifier: GPL-2.0-or-later
"""Updates tab (web build only).

Tells the client whether newer firmware exists for their board and hands them the .uf2 with the
instructions to flash it. The board and its version come from the USB product string the keyboard
announces (see the naming rule and PRODUCT_RE in vial-web/firmware/make-manifest.py — keep them in
sync); the catalogue is firmware_manifest.json, preloaded by vial-web's build.
"""
import json
import os
import re
import sys

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
                             QPushButton, QComboBox, QScrollArea, QFrame, QSizePolicy)

from editor.basic_editor import BasicEditor
from util import tr
from vial_device import VialKeyboard

# Keep in sync with vial-web/firmware/make-manifest.py
PRODUCT_RE = re.compile(
    r"^(?P<family>SoflePLUS2?|CornePLUS2?)"
    r"(?: (?P<trackpad>TPS43|TPS65))?"
    r"(?: (?P<horizontal>Horizontal))?"
    r" v(?P<version>\d+\.\d+(?:\.\d+)?[a-z]*)"
    r"(?: (?P<batch>Legendary RGB|Legendary|Signature RGB))?"
    r"(?: (?P<beta>Beta.*))?$"
)

# Older SoflePLUS2 firmware names the batch inconsistently (or not at all), so the board is only
# detected from v5.10 on; anything older gets the manual list.
_DETECT_FROM = (5, 10)

# (glyph, colour) per status; the same convention as app stores / browsers: green = fine,
# amber = needs attention, grey = cannot tell
_STATUS_STYLES = {
    "ok": ("✓", "#3cb371"),
    "update": ("⚠", "#e6a817"),
    "unknown": ("?", "#9e9e9e"),
    "info": ("•", ""),
}

_BATCH_HINT = ("“Batch” is the PCB generation of your keyboard (see your order or the XCMKB docs), "
               "not the firmware version.")

_WARNING = ("Updating may reset your keymap. Save your layout first (File → Save current layout…). "
            "Any unsaved changes will be lost.")

_STEPS = """Both halves must run the same version.

1.  Save your layout (.vil).
2.  Click Update now. On recent firmware the keyboard restarts into update mode by itself; on older firmware the dialog asks you to double-tap the reset button on the half that has the USB cable (its LED blinks). Pick “RP2 Boot” in the browser prompt the first time, then wait for the write to finish — the keyboard restarts by itself.
3.  Unplug the USB cable and plug it directly into the OTHER half (the cable between the halves does not carry the update). Choose “Update the other half” and repeat step 2.
4.  Finish: plug back into the half you normally use, press Start Vial again and load your .vil if needed.

Without WebUSB (or if the update dialog cannot see the keyboard): Download update, put the half into update mode as in step 2 and copy the .uf2 onto the RPI-RP2 drive that appears."""


def _version_key(version):
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?([a-z]*)$", version)
    if not m:
        return (0, 0, 0, version)
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4)


def _wrapped(text="", bold=False):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if bold:
        lbl.setStyleSheet("font-weight: bold")
    return lbl


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


class Updates(BasicEditor):

    def __init__(self, appctx):
        super().__init__()

        self.manifest = self._load_manifest(appctx)
        self.boards = self.manifest.get("boards", []) if self.manifest else []
        self.product = ""
        self.board = None        # manifest board the keyboard was matched (or chosen) to
        self.entry = None        # manifest firmware entry of the running version, if catalogued
        self.detected = False    # board came from the product string, not from the combo box
        self.update_available = False

        inner = QVBoxLayout()
        inner.setContentsMargins(4, 4, 4, 4)
        inner.setSpacing(8)

        inner.addWidget(_wrapped(tr("Updates", "Firmware updates are in beta: please report anything odd to XCMKB.")))

        info = QGridLayout()
        info.setHorizontalSpacing(16)
        info.setVerticalSpacing(4)
        info.setColumnMinimumWidth(1, 420)
        self.lbl_keyboard = _wrapped()
        self.lbl_board = _wrapped()
        self.lbl_latest = _wrapped()
        self.lbl_status = _wrapped(bold=True)
        self.lbl_notes = _wrapped()
        self.combo_board = QComboBox()
        for b in self.boards:
            self.combo_board.addItem(b["name"], b["id"])
        self.combo_board.currentIndexChanged.connect(self._on_board_chosen)
        self.combo_version = QComboBox()
        self.combo_version.currentIndexChanged.connect(lambda _: self._show_notes())
        self.rows = {}
        for r, (key, caption, widget) in enumerate([
            ("keyboard", "Your keyboard:", self.lbl_keyboard),
            ("board", "Board:", self.lbl_board),
            ("choose", "Choose your board:", self.combo_board),
            ("hint", "", _wrapped(tr("Updates", _BATCH_HINT))),
            ("latest", "Latest firmware:", self.lbl_latest),
            ("status", "Status:", self.lbl_status),
            ("version", "Version to download:", self.combo_version),
            ("notes", "Release notes:", self.lbl_notes),
        ]):
            cap = QLabel(tr("Updates", caption))
            cap.setAlignment(Qt.AlignRight | Qt.AlignTop)
            info.addWidget(cap, r, 0)
            info.addWidget(widget, r, 1)
            self.rows[key] = (cap, widget)
        inner.addLayout(info)

        buttons = QHBoxLayout()
        self.btn_update = QPushButton(tr("Updates", "Update now"))
        self.btn_update.setToolTip(tr("Updates", "Writes the selected version to the keyboard from this page "
                                                 "(Chrome/Edge). You will be asked to put the keyboard into "
                                                 "update mode and to pick it in a browser dialog."))
        self.btn_update.clicked.connect(self._on_update_now)
        buttons.addWidget(self.btn_update)
        self.btn_download = QPushButton(tr("Updates", "Download update (.uf2)"))
        self.btn_download.clicked.connect(self._on_download)
        self.btn_changelog = QPushButton(tr("Updates", "View changelog"))
        self.btn_changelog.clicked.connect(self._on_changelog)
        self.btn_changelog.setEnabled(bool(self.manifest and self.manifest.get("changelog_url")))
        buttons.addWidget(self.btn_download)
        buttons.addWidget(self.btn_changelog)
        buttons.addStretch()
        inner.addLayout(buttons)

        inner.addWidget(_wrapped("⚠ " + tr("Updates", _WARNING), bold=True))

        steps_group = QGroupBox(tr("Updates", "How to update"))
        steps_layout = QVBoxLayout()
        steps_layout.addWidget(_wrapped(tr("Updates", _STEPS)))
        steps_group.setLayout(steps_layout)
        inner.addWidget(steps_group)

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
                    (p["family"], p["trackpad"] or "", bool(p["horizontal"]), p["batch"] or ""):
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
        self.lbl_status.setStyleSheet("font-weight: bold" + ("; color: " + colour if colour else ""))

    def _show_notes(self):
        fw = self._selected_firmware()
        self.lbl_notes.setText((fw or {}).get("notes") or "-")

    def _refresh(self):
        self.lbl_keyboard.setText(self.product or tr("Updates", "(no product name reported)"))
        self.update_available = False
        running = self._running_version()

        self.combo_version.blockSignals(True)
        self.combo_version.clear()
        if self.board:
            latest = self.board["latest"]
            self.lbl_board.setText(self.board["name"])
            fw_latest = self._firmware(self.board, latest)
            self.lbl_latest.setText("v{}  ({})".format(latest, fw_latest["date"]) if fw_latest else "v" + latest)
            for fw in self.board["firmware"]:
                text = "v{}  ({})".format(fw["version"], fw["date"])
                if fw["beta"]:
                    text += "  beta"
                if fw["version"] == latest:
                    text += "  ← latest"
                self.combo_version.addItem(text, fw["version"])
            self.combo_version.setCurrentIndex(max(0, self.combo_version.findData(latest)))
            if self.entry and running == latest:
                self._set_status("ok", tr("Updates", "Up to date (v{})").format(running))
            elif self.entry:
                self.update_available = True
                self._set_status("update", tr("Updates", "Update available: v{} → v{}").format(running, latest))
            elif running:
                self.update_available = True
                self._set_status("update", tr("Updates", "Your firmware v{} is not in the catalogue; "
                                                        "the latest for this board is v{}").format(running, latest))
            else:
                self._set_status("info", tr("Updates", "Latest for this board is v{}").format(latest))
        else:
            self.lbl_board.setText("")
            self.lbl_latest.setText("-")
            self._set_status("unknown", tr("Updates", "Your board could not be identified from this firmware "
                                                    "(automatic detection needs v5.10 or newer). Choose it from the list."))
        self.combo_version.blockSignals(False)

        for key in ("choose", "hint"):
            for w in self.rows[key]:
                w.setVisible(not self.detected)
        for w in self.rows["board"]:
            w.setVisible(self.board is not None)
        self.btn_update.setEnabled(self.board is not None)
        self.btn_download.setEnabled(self.board is not None)
        self._show_notes()

    # ---- slots ----

    def _on_board_chosen(self, index):
        if index < 0 or self.detected:
            return
        self.board = self.boards[index]
        self.entry = None
        self._refresh()

    def _on_update_now(self):
        fw = self._selected_firmware()
        if not fw or sys.platform != "emscripten":
            return
        import vialglue
        entry = {"board": self.board["name"], "version": fw["version"], "file": fw["file"],
                 "size": fw["size"], "sha256": fw["sha256"], "product": fw["product"],
                 "reboot_requested": True}
        vialglue.flash_firmware(json.dumps(entry))
        # Ask the running firmware to restart into its bootloader (VIA id_bootloader_jump). Firmware
        # without that command just echoes the packet and the page falls back to "double-tap reset".
        # Nothing talks to the keyboard after this until the page reloads.
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
        QDesktopServices.openUrl(QUrl(self.manifest["changelog_url"]))

    # ---- BasicEditor interface ----

    def valid(self):
        return sys.platform == "emscripten" and bool(self.boards) and isinstance(self.device, VialKeyboard)

    def tab_title(self):
        title = tr("MainWindow", "Updates (beta)")
        return title + " *" if self.update_available else title

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return
        self.product = device.desc.get("product_string", "") or ""
        self.board, self.entry = self._detect(self.product)
        self.detected = self.board is not None
        self.combo_board.blockSignals(True)
        self.combo_board.setCurrentIndex(self.boards.index(self.board) if self.detected else -1)
        self.combo_board.blockSignals(False)
        self._refresh()
