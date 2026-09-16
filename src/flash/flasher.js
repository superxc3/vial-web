// SPDX-License-Identifier: GPL-2.0-or-later
//
// Firmware update from the Updates tab: writes a catalogue UF2 to an RP2040 keyboard that is in
// BOOTSEL mode, over WebUSB using the bootrom's PICOBOOT interface (picoboot/ is
// piersfinlayson/picoflash, MIT). Driven by the #flash overlay in index.html.
//
// Flow: fetch + verify the file -> the keyboard gets into update mode (Vial asks it to reboot;
// if the firmware ignores that, the user double-taps reset) -> the bootloader device is picked
// automatically when this page already has permission for it, otherwise in the browser chooser
// -> reset interface, exit XIP, erase, write (chunked for progress), read back, reboot -> the
// same image is offered for the other half. Alternative that needs no driver at all: the UF2 is
// written into the RPI-RP2 drive the bootloader exposes (File System Access API).
//
// The command sequence mirrors picoflash.org's, which is exercised on real boards. WebUSB
// transfers never time out on their own, so every step is wrapped in a timeout.

import { Picoboot } from "./picoboot/picoboot.js";
import { Target } from "./picoboot/target.js";
import { FLASH_START, SECTOR_SIZE, PAGE_SIZE, UF2_RP2040_FAMILY_ID } from "./picoboot/constants.js";
import { NotFoundError, UsbError } from "./picoboot/errors.js";
import { uf2ToFlashBuffer } from "./uf2.js";

const WRITE_CHUNK = 16 * PAGE_SIZE;     // 4 KB per PICOBOOT write: ~25 progress steps for a 100 KB image
const FLASH_LIMIT = 16 * 1024 * 1024;   // largest RP2040 flash
const STEP_TIMEOUT = 10000;             // per USB step
const REBOOT_WAIT = 4000;               // for the keyboard to drop off HID after Vial's reboot request
const BOOTSEL_WAIT = 4000;              // for a permitted RP2 Boot device to appear afterwards
const TARGET = new Target("RP2040");

const TEXT = {
    prepare: "Preparing the firmware file…",
    rebooting: "Asking the keyboard to restart into update mode…",
    waiting: "Waiting for the keyboard to reappear in update mode…",
    bootsel: "Put the keyboard into update mode: double-tap the reset button on the half that has the " +
             "USB cable. Its LED starts blinking, the keyboard disappears from Vial and an RPI-RP2 drive " +
             "appears. Then either click Copy to RPI-RP2 drive and pick that drive (no driver needed), or " +
             "Select keyboard (USB) and choose \u201cRP2 Boot\u201d.",
    other: "Unplug the USB cable and plug it directly into the OTHER half. Double-tap that half's reset " +
           "button (LED blinking), then click Copy to RPI-RP2 drive or Select keyboard (USB) again.",
    drive: "Writing the firmware to the RPI-RP2 drive. Do not unplug the keyboard.",
    flashing: "Writing the firmware. Do not unplug the keyboard.",
    done: "Done: the keyboard is restarting with the new firmware.",
};

let ui = null;
let lastTrace = "";

function els() {
    if (!ui) {
        ui = {};
        for (const id of ["flash", "flash_title", "flash_file", "flash_step", "flash_progress", "flash_log",
                          "flash_select", "flash_drive", "flash_other", "flash_finish", "flash_cancel"]) {
            ui[id] = document.getElementById(id);
        }
    }
    return ui;
}

function log(line) {
    const u = els();
    u.flash_log.textContent += line + "\n";
    u.flash_log.scrollTop = u.flash_log.scrollHeight;
}

// picoboot/ reports every USB step through console.log/error; mirror it into the overlay so a
// client can copy a complete trace without opening DevTools
function captureConsole(run) {
    const orig = { log: console.log, error: console.error, warn: console.warn };
    for (const k of Object.keys(orig)) {
        console[k] = (...args) => {
            orig[k].apply(console, args);
            try {
                lastTrace = args.map(a => (a && a.message) ? a.message : String(a)).join(" ");
                log("  · " + lastTrace);
            } catch (e) {}
        };
    }
    return run().finally(() => Object.assign(console, orig));
}

function setStep(key) {
    els().flash_step.textContent = TEXT[key];
}

function setProgress(fraction) {
    const u = els();
    u.flash_progress.value = fraction === null ? 0 : fraction;
    u.flash_progress.style.visibility = fraction === null ? "hidden" : "visible";
}

function showButtons(visible) {
    const u = els();
    for (const id of ["flash_select", "flash_drive", "flash_other", "flash_finish", "flash_cancel"]) {
        u[id].style.display = visible.includes(id) ? "" : "none";
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function withTimeout(promise, label, ms = STEP_TIMEOUT) {
    let timer;
    const guard = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(label + " timed out after " + ms / 1000 + " s")), ms);
    });
    return Promise.race([promise, guard]).finally(() => clearTimeout(timer));
}

function platform() {
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    return /win/i.test(p) ? "windows" : /mac/i.test(p) ? "mac" : /linux/i.test(p) ? "linux" : "other";
}

const CLAIM_HINT = {
    windows: "Windows did not hand RP2 Boot over to the browser. Use Copy to RPI-RP2 drive instead (no " +
             "driver needed), or fix the driver: the first time a board is in update mode, " +
             "Windows spends 10–30 s installing its driver: leave the keyboard in update mode, wait, " +
             "RELOAD this page and try again. If it keeps failing, Windows bound the wrong driver: in Device " +
             "Manager ‘RP2 Boot’ must sit under ‘Universal Serial Bus devices’; install WinUSB " +
             "for ‘RP2 Boot (Interface 1)’ once with Zadig.",
    mac: "macOS did not hand RP2 Boot over to the browser. RELOAD this page, put the keyboard into update " +
             "mode again and retry; make sure nothing else (picotool, a virtual machine) is using the device.",
    linux: "Linux did not hand RP2 Boot over to the browser: a udev rule for USB ID 2e8a:0003 is needed. " +
             "Add it, replug, RELOAD this page and retry.",
    other: "The operating system did not hand RP2 Boot over to the browser. RELOAD this page, put the " +
             "keyboard into update mode again and retry.",
};

function explain(e) {
    if (e && e.claimHang) {
        return CLAIM_HINT[platform()];
    }
    if (e instanceof NotFoundError) {
        return "No keyboard was selected. Make sure it is in update mode (LED blinking), then click Select keyboard again.";
    }
    if (e instanceof UsbError) {
        return "Could not talk to the keyboard (" + e.message + "). Unplug it, put it into update mode again and retry. " +
               "On Windows the driver installs itself the first time — wait a few seconds after the LED starts " +
               "blinking. On Linux a udev rule for USB ID 2e8a:0003 is required.";
    }
    return "Update failed: " + e.message + ". The keyboard is still in update mode: reload this page and retry, " +
           "or copy the downloaded .uf2 onto the RPI-RP2 drive instead.";
}

function hex(buf) {
    return Array.from(new Uint8Array(buf), b => b.toString(16).padStart(2, "0")).join("");
}

// Every block must be a valid RP2040 UF2 block inside flash; returns the block count
export function checkUf2(bytes) {
    if (bytes.length === 0 || bytes.length % 512 !== 0) {
        throw new Error("not a UF2 file (size " + bytes.length + ")");
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const total = bytes.length / 512;
    for (let i = 0; i < total; i++) {
        const o = i * 512;
        if (view.getUint32(o, true) !== 0x0A324655 || view.getUint32(o + 4, true) !== 0x9E5D5157 ||
                view.getUint32(o + 508, true) !== 0x0AB16F30) {
            throw new Error("corrupted UF2 block " + i);
        }
        const addr = view.getUint32(o + 12, true);
        const size = view.getUint32(o + 16, true);
        const family = view.getUint32(o + 28, true);
        if (family !== UF2_RP2040_FAMILY_ID) {
            throw new Error("block " + i + " is not for an RP2040 (family 0x" + family.toString(16) + ")");
        }
        if (addr < FLASH_START || addr + size > FLASH_START + FLASH_LIMIT || size > 476) {
            throw new Error("block " + i + " targets 0x" + addr.toString(16) + ", outside flash");
        }
        if (view.getUint32(o + 24, true) !== total) {
            throw new Error("block count mismatch in block " + i);
        }
    }
    return total;
}

async function prepare(entry) {
    setStep("prepare");
    setProgress(null);
    const url = "firmware/" + entry.file;
    log("Downloading " + url);
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) {
        throw new Error("could not download " + entry.file + " (HTTP " + resp.status + ")");
    }
    const bytes = new Uint8Array(await resp.arrayBuffer());
    if (entry.size && bytes.length !== entry.size) {
        throw new Error("downloaded size " + bytes.length + " does not match the catalogue (" + entry.size + ")");
    }
    if (entry.sha256) {
        const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
        if (digest !== entry.sha256) {
            throw new Error("checksum mismatch, the download is corrupted");
        }
        log("SHA-256 verified");
    }
    const blocks = checkUf2(bytes);
    const image = uf2ToFlashBuffer(bytes);
    if (image.address !== FLASH_START || image.address % SECTOR_SIZE !== 0) {
        throw new Error("firmware does not start at the beginning of flash (0x" + image.address.toString(16) + ")");
    }
    if (image.data.length % PAGE_SIZE !== 0) {
        const padded = new Uint8Array(Math.ceil(image.data.length / PAGE_SIZE) * PAGE_SIZE).fill(0xFF);
        padded.set(image.data);
        image.data = padded;
    }
    log(blocks + " UF2 blocks, " + image.data.length + " bytes of flash at 0x" + image.address.toString(16));
    return { image, bytes };
}

// Driver-free route: write the UF2 into the RPI-RP2 drive. The bootrom programs each block as it
// arrives and restarts as soon as the last one lands, so the final close() (Chrome renames its
// .crswap temp file into place) fails with the drive gone - after a complete write that is success.
async function writeViaDrive(bytes, fileName) {
    if (!window.showDirectoryPicker) {
        throw new Error("this browser cannot write to drives; use Download update and copy the file yourself");
    }
    setStep("drive");
    setProgress(null);
    const dir = await window.showDirectoryPicker({ id: "rpi-rp2", mode: "readwrite" });
    let infoText = "";
    try {
        const info = await dir.getFileHandle("INFO_UF2.TXT");
        infoText = await (await info.getFile()).text();
    } catch (e) {
        throw new Error("\u201c" + dir.name + "\u201d is not the RPI-RP2 drive (no INFO_UF2.TXT in it)");
    }
    if (!/RPI-RP2|UF2/i.test(infoText)) {
        throw new Error("\u201c" + dir.name + "\u201d does not look like the RP2040 bootloader drive");
    }
    log("Drive " + dir.name + ": " + infoText.split("\n")[0].trim());
    const handle = await dir.getFileHandle(fileName, { create: true });
    const writable = await handle.createWritable({ keepExistingData: false });
    let written = false;
    try {
        await writable.write(bytes);
        written = true;
        await writable.close();
        log("File written and closed");
    } catch (e) {
        if (!written) {
            throw new Error("could not write to the drive (" + e.message + ")");
        }
        log("Drive vanished while finishing the copy (" + e.message + ") \u2014 that is the keyboard restarting");
    }
    setProgress(1);
}

// A bootloader device this page was already allowed to use (WebUSB remembers the permission), or null
async function permittedDevice() {
    try {
        const devices = await Picoboot.getDevices([TARGET]);
        return devices.length ? devices[0] : null;
    } catch (e) {
        return null;
    }
}

// Vial asked the keyboard to reboot into the bootloader (firmware with host-triggered bootloader
// jump). Wait for it to drop off HID, then for a permitted RP2 Boot device; null if either does
// not happen, in which case the user takes over with double-tap reset and the chooser.
async function waitForAutomaticBootsel(requestedAt) {
    setStep("rebooting");
    const gone = () => window.g_hid_disconnected_at && window.g_hid_disconnected_at >= requestedAt - 1000;
    const deadline = requestedAt + REBOOT_WAIT;
    while (Date.now() < deadline && !gone()) {
        await sleep(200);
    }
    if (!gone()) {
        log("The keyboard did not restart by itself (its firmware predates that feature) — double-tap reset instead");
        return null;
    }
    log("Keyboard left normal mode, looking for the bootloader…");
    setStep("waiting");
    const until = Date.now() + BOOTSEL_WAIT;
    while (Date.now() < until) {
        const dev = await permittedDevice();
        if (dev) {
            return dev;
        }
        await sleep(500);
    }
    log("No previously authorised bootloader found — please pick it in the browser dialog");
    return null;
}

async function recover(picoboot) {
    try {
        await withTimeout(picoboot.resetInterface(), "Interface reset", 3000);
    } catch (e) {
        log("Recovery reset failed: " + e.message);
    }
    try {
        await withTimeout(picoboot.disconnect(), "Disconnect", 3000);
    } catch (e) {
        log("Disconnect failed: " + e.message);
    }
}

async function writeImage(image, picoboot) {
    setStep("flashing");
    setProgress(0);
    if (!picoboot) {
        log("Requesting the keyboard (RP2 Boot)…");
        picoboot = await Picoboot.requestDevice([TARGET]);
    }
    const info = picoboot.getUsbDeviceInfo();
    log("Bootloader: " + (info.productName || "RP2 Boot") + " serial " + (info.serialNumber || "-"));
    let conn;
    try {
        try {
            conn = await withTimeout(picoboot.connect(), "Connect");
        } catch (e) {
            // opened and configured but the interface claim never came back: the OS is holding it
            if (/timed out/.test(e.message) && /Configuration selected|Device opened/.test(lastTrace)) {
                e.claimHang = true;
            }
            throw e;
        }
        await withTimeout(conn.resetInterface(), "Interface reset");
        await withTimeout(conn.exitXip(), "Exit XIP");

        const eraseSize = Math.ceil(image.data.length / SECTOR_SIZE) * SECTOR_SIZE;
        log("Erasing " + eraseSize + " bytes");
        await withTimeout(conn.flashErase(image.address, eraseSize), "Erase", 30000);
        setProgress(0.1);

        log("Writing " + image.data.length + " bytes");
        for (let off = 0; off < image.data.length; off += WRITE_CHUNK) {
            const chunk = image.data.subarray(off, Math.min(off + WRITE_CHUNK, image.data.length));
            await withTimeout(conn.flashWrite(image.address + off, chunk), "Write at 0x" + (image.address + off).toString(16));
            setProgress(0.1 + 0.8 * (off + chunk.length) / image.data.length);
        }

        // Read back and compare (READ of flash addresses is served by the bootrom with XIP exited,
        // the same way picoflash's own read works). A read that fails is logged and skipped after
        // an interface reset; a mismatch is an error.
        let mismatch = -1;
        try {
            await withTimeout(conn.resetInterface(), "Interface reset");
            await withTimeout(conn.exitXip(), "Exit XIP");
            for (let off = 0; off < image.data.length && mismatch < 0; off += WRITE_CHUNK) {
                const len = Math.min(WRITE_CHUNK, image.data.length - off);
                const readBack = await withTimeout(conn.flashRead(image.address + off, len), "Read at 0x" + (image.address + off).toString(16));
                for (let i = 0; i < len; i++) {
                    if (readBack[i] !== image.data[off + i]) {
                        mismatch = off + i;
                        break;
                    }
                }
                setProgress(0.9 + 0.1 * (off + len) / image.data.length);
            }
            log(mismatch < 0 ? "Read back and verified" : "Read-back differs at offset 0x" + mismatch.toString(16));
        } catch (e) {
            log("Read-back not available (" + e.message + "), skipped");
            try {
                await withTimeout(conn.resetInterface(), "Interface reset", 3000);
            } catch (e2) {
                log("Interface reset failed: " + e2.message);
            }
        }
        if (mismatch >= 0) {
            throw new Error("verification failed at offset 0x" + mismatch.toString(16) +
                            " — do not unplug, retry the update");
        }
        setProgress(1);

        log("Rebooting the keyboard");
        try {
            await withTimeout(conn.reboot(100), "Reboot", 3000);
        } catch (e) {
            // the write is complete and verified; a missing ACK usually means it already restarted
            log("Reboot command not acknowledged (" + e.message + "); if the keyboard did not restart, unplug and replug it");
        }
    } catch (e) {
        await recover(picoboot);
        throw e;
    }
    await withTimeout(picoboot.disconnect(), "Disconnect", 3000).catch(() => {});
}

export async function startFlash(entry) {
    const u = els();
    const requestedAt = Date.now();
    let image = null;
    let bytes = null;
    let busy = false;

    u.flash_title.textContent = "Firmware update — " + (entry.board || "");
    u.flash_file.textContent = "v" + entry.version + "  (" + entry.file + ")";
    u.flash_log.textContent = "";
    u.flash.style.display = "";
    showButtons(["flash_cancel"]);

    const finish = (ok) => {
        busy = false;
        if (ok) {
            setStep("done");
            log("Firmware v" + entry.version + " written. Now update the other half, or finish and reconnect Vial.");
            showButtons(["flash_other", "flash_finish"]);
        } else {
            showButtons(["flash_drive", "flash_select", "flash_cancel"]);
        }
    };
    const run = async (job) => {
        if (busy) {
            return;
        }
        busy = true;
        showButtons([]);
        try {
            await captureConsole(job);
            finish(true);
        } catch (e) {
            console.error(e);
            const why = (e && e.name === "AbortError") ? "No drive was chosen." : explain(e);
            log("ERROR: " + why);
            u.flash_step.textContent = why;
            setProgress(null);
            finish(false);
        }
    };

    u.flash_cancel.onclick = () => { if (!busy) { u.flash.style.display = "none"; } };
    u.flash_finish.onclick = () => { location.reload(); };
    u.flash_select.onclick = () => run(() => writeImage(image, null));
    u.flash_drive.onclick = () => run(() => writeViaDrive(bytes, entry.file));
    u.flash_other.onclick = () => {
        log("--- other half ---");
        setProgress(null);
        setStep("other");
        showButtons(["flash_drive", "flash_select", "flash_cancel"]);
    };

    try {
        ({ image, bytes } = await prepare(entry));
    } catch (e) {
        console.error(e);
        log("ERROR: " + e.message);
        u.flash_step.textContent = "Could not prepare the firmware: " + e.message;
        showButtons(["flash_cancel"]);
        return;
    }

    if (entry.reboot_requested) {
        const dev = await captureConsole(() => waitForAutomaticBootsel(requestedAt));
        if (dev) {
            await run(() => writeImage(image, dev));
            return;
        }
    }
    setStep("bootsel");
    showButtons(["flash_drive", "flash_select", "flash_cancel"]);
}
