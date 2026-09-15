#!/usr/bin/env python3
"""Regenerate firmware/manifest.json from the .uf2 files in this directory.

Each UF2 is placed under a *board* by the USB product string embedded in it (the
same string the keyboard announces over WebHID), so the manifest matches what the
web app will see. The naming rule is:

    <Family> [<TPS43|TPS65>] [Horizontal] v<major>.<minor>[<letter>] [<batch words>] [Beta ...]

`date` and `notes` already present in manifest.json are kept for a file of the same
name; everything else is derived. A board's `latest` is its highest non-beta version
unless the existing entry carries `"latest_override"`.

Usage:  python firmware/make-manifest.py            (stdlib only)
"""
import datetime
import hashlib
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
CHANGELOG_URL = "https://xcmkb-docs.gitbook.io/doc/firmware-changelog"

UF2_MAGIC0, UF2_MAGIC1, UF2_MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
RP2040_FAMILY = 0xE48BFF56
FLASH_BASE, FLASH_MAX = 0x10000000, 0x10000000 + 16 * 1024 * 1024

# Keep in sync with vial-gui editor/updates.py
PRODUCT_RE = re.compile(
    r"^(?P<family>SoflePLUS2?|CornePLUS2?)"
    r"(?: (?P<trackpad>TPS43|TPS65))?"
    r"(?: (?P<horizontal>Horizontal))?"
    r" v(?P<version>\d+\.\d+(?:\.\d+)?[a-z]*)"
    r"(?: (?P<batch>Legendary RGB|Legendary|Signature RGB))?"
    r"(?: (?P<beta>Beta.*))?$"
)

# (family, batch words) -> (firmware keyboard dir, label the client recognises)
BATCHES = {
    ("SoflePLUS", ""): ("sofleplus1", "Batch 0 - SoflePLUS v1"),
    ("SoflePLUS2", ""): ("sofleplus2", "Latest batch"),
    ("SoflePLUS2", "Legendary"): ("sofleplus2ano", "Batch 1 - Legendary"),
    ("SoflePLUS2", "Legendary RGB"): ("sofleplus2legendaryrgb", "Batch 1 - Legendary RGB"),
    ("SoflePLUS2", "Signature RGB"): ("sofleplus2u", "Batch 2 - Signature RGB"),
    ("CornePLUS", ""): ("corneplus", "CornePLUS"),
    ("CornePLUS2", ""): ("corneplus2", "CornePLUS2"),
}


def read_uf2(path):
    """Return (flash image bytes, base address) after validating every block."""
    data = open(path, "rb").read()
    if len(data) % 512 or not data:
        raise ValueError("size is not a multiple of 512")
    blocks = {}
    for i in range(len(data) // 512):
        b = data[i * 512:(i + 1) * 512]
        m0, m1, flags, addr, size, _, _, family = struct.unpack("<8I", b[:32])
        if (m0, m1, struct.unpack("<I", b[508:512])[0]) != (UF2_MAGIC0, UF2_MAGIC1, UF2_MAGIC_END):
            raise ValueError("bad UF2 magic in block {}".format(i))
        if family != RP2040_FAMILY:
            raise ValueError("block {} family 0x{:08X} is not RP2040".format(i, family))
        if not FLASH_BASE <= addr < FLASH_MAX or size > 476:
            raise ValueError("block {} target 0x{:08X} outside RP2040 flash".format(i, addr))
        blocks[addr] = b[32:32 + size]
    base, end = min(blocks), max(blocks) + 256
    image = bytearray(b"\xff" * (end - base))
    for addr, payload in blocks.items():
        image[addr - base:addr - base + len(payload)] = payload
    return bytes(image), base


def product_string(image):
    """The USB product string descriptor is UTF-16LE; return the one matching the naming rule."""
    hits = set()
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", image):
        s = m.group().decode("utf-16-le")
        if PRODUCT_RE.match(s):
            hits.add(s)
    if len(hits) != 1:
        raise ValueError("expected exactly one product string, found {}".format(sorted(hits)))
    return hits.pop()


def version_key(version):
    """'5.10h' -> (5, 10, 0, 'h'); '5.4.2' -> (5, 4, 2, '')"""
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?([a-z]*)$", version)
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4)


def main():
    previous = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            previous = json.load(f)
    prev_entries = {fw["file"]: fw for b in previous.get("boards", []) for fw in b["firmware"]}
    prev_boards = {b["id"]: b for b in previous.get("boards", [])}

    boards = {}
    problems = []
    for name in sorted(os.listdir(HERE)):
        if not name.lower().endswith(".uf2"):
            continue
        path = os.path.join(HERE, name)
        try:
            image, _ = read_uf2(path)
            product = product_string(image)
        except ValueError as e:
            problems.append("{}: {}".format(name, e))
            continue
        p = PRODUCT_RE.match(product).groupdict()
        family, batch = p["family"], p["batch"] or ""
        if (family, batch) not in BATCHES:
            problems.append("{}: no batch mapping for {!r} {!r}".format(name, family, batch))
            continue
        kb_dir, batch_label = BATCHES[(family, batch)]
        # the file name QMK produces is xcmkb_<keyboard dir>_<keymap>.uf2; warn if it disagrees
        if not name.startswith("xcmkb_{}_".format(kb_dir)) and name != "xcmkb_{}.uf2".format(kb_dir):
            problems.append("{}: product string {!r} says {} but the file name does not".format(name, product, kb_dir))
        trackpad = (p["trackpad"] or "").lower()
        board_id = kb_dir + ("/" + trackpad + ("h" if p["horizontal"] else "") if trackpad else "")
        board = boards.setdefault(board_id, {
            "id": board_id,
            "name": " ".join(x for x in [family, p["trackpad"], p["horizontal"], batch] if x),
            "family": family,
            "trackpad": p["trackpad"] or "",
            "horizontal": bool(p["horizontal"]),
            "batch": batch,
            "batch_label": batch_label,
            "latest": "",
            "firmware": [],
        })
        with open(path, "rb") as f:
            data = f.read()
        old = prev_entries.get(name, {})
        board["firmware"].append({
            "version": p["version"],
            "beta": bool(p["beta"]),
            "product": product,
            "file": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "date": old.get("date") or datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat(),
            "notes": old.get("notes", ""),
        })

    for board in boards.values():
        board["firmware"].sort(key=lambda fw: version_key(fw["version"]), reverse=True)
        release = [fw for fw in board["firmware"] if not fw["beta"]] or board["firmware"]
        board["latest"] = release[0]["version"]
        override = prev_boards.get(board["id"], {}).get("latest_override")
        if override:
            if any(fw["version"] == override for fw in board["firmware"]):
                board["latest"] = override
                board["latest_override"] = override
            else:
                problems.append("{}: latest_override {!r} matches no file".format(board["id"], override))

    manifest = {
        "schema": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "changelog_url": previous.get("changelog_url", CHANGELOG_URL),
        "boards": [boards[k] for k in sorted(boards)],
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    for board in manifest["boards"]:
        print("{:<32} latest {:<8} {}".format(board["id"], board["latest"],
              ", ".join(fw["version"] + (" (beta)" if fw["beta"] else "") for fw in board["firmware"])))
    for line in problems:
        print("WARNING:", line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
