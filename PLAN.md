# vial-web (xcmkb) — plan & progress log

Goal: a web build of `superxc3/vial-gui@xcmkb` (per-key RGB, layer indicators,
trackpad tab, OLED tab, `.vil` save/restore of those) hosted on GitHub Pages.

## Key facts (non-obvious)

- vial-web is **not a JS rewrite**. `src/build.sh` copies `vialgui/python/*` (the Vial app,
  vendored from `superxc3/vial-gui@xcmkb` at f737912 on 2026-09-16 and maintained here since — the
  vial-gui repo is frozen, no desktop builds) into a CPython 3.11 + PyQt5 tree compiled to
  WebAssembly with Emscripten. Web-specific behaviour is behind `sys.platform == "emscripten"`
  and the tiny `vialglue` C module in `src/main.c`. The page footer's "Vial:" hash is the last
  commit that touched `vialgui/` (CI checks out with `fetch-depth: 0` for that).
- No other repository is cloned at build time except `vial-kb/via-keymap-precompiled`.
- The build is `-pthread -sPROXY_TO_PTHREAD`, so it needs `SharedArrayBuffer`, which needs
  `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`.
  vial.rocks gets these from Cloudflare. GitHub Pages cannot set headers, so
  `src/coi-serviceworker.js` (MIT, vendored) injects them client-side; it must be the first
  script in `index.html`.
- Branches: `main` = clean mirror of `vial-kb/vial-web`, `xcmkb` = custom work + default branch.
  Pages deploys only from `xcmkb`.
- A full toolchain build (emsdk + Qt 5.14 + CPython + PyQt5) takes ~40 min on `ubuntu-22.04`.
  `actions/cache` keeps `emsdk/` + a pruned `deps/` (see `prune-deps.sh`, which keeps exactly the
  paths `src/build.sh` links against) keyed on `version.sh` + build scripts + `patches/**`.
- On the very first visit the service worker is not yet controlling the page, so the runtime must
  not be started until `SharedArrayBuffer` exists; `index.html` gates the `main-*.js` load on that
  and coi-serviceworker reloads once it has taken control.
- Local Linux is not required; everything runs on `ubuntu-22.04` runners.

## Phases

### Phase 1 — fork & inspect  ✅ 2026-09-15
- [x] Fork exists (`superxc3/vial-web`), cloned to `C:\Users\User\vial-web`, `upstream` remote added
- [x] Read all build scripts, `main.c`, `worker.js`, `index.html`; located the single vial-gui reference

### Phase 2 — repoint to the fork  ✅ 2026-09-15
- [x] Workflow clones `superxc3/vial-gui@xcmkb`
- [x] Separate `deploy` job → GitHub Pages (re-runnable without rebuilding)
- [x] `du -sh` size report step (input for caching decision)
- [x] coi-serviceworker vendored, loaded first, copied into the build output
- [x] Favicon path made relative for the `/vial-web/` project URL
- [x] `main` reset to upstream; work moved to `xcmkb`; default branch and Pages
      environment (no branch restriction) set on GitHub
- [x] First green build of `xcmkb` (run 34930649973, build 40m 2s, deploy 10s) → https://superxc3.github.io/vial-web/

### Phase 3 — verify in the browser (Chrome/Edge + keyboard)
- [x] Start button enables (Python booted in the WASM worker), `crossOriginIsolated: true` under the
      service worker — verified in Chrome 152 on 2026-09-15
- [x] First-visit alert "SharedArrayBuffer is not defined" (runtime started before the SW reload) —
      fixed by gating the runtime load in `index.html`
- [ ] Qt UI appears after *Start Vial* + device pick
- [x] WebHID chooser lists the keyboard; keymap edits and lighting effect changes reach the
      firmware (user, 2026-09-15). The earlier "not detected" was the first-visit alert state.
- [x] Keymap tab baseline
- [x] Lighting tab: per-key RGB, indicators — colour pickers used `QColorDialog.exec_()` (nested
      event loop → `emscripten_sleep` error on WASM); converted to `show()` + `finished` like the
      upstream underglow picker (`vial-gui@5466196`). User confirmed working.
- [x] Trackpad tab — verified on macOS Chrome (user, 2026-09-16). Sliders/checkboxes only reach the
      firmware on *Apply Settings to Keyboard*; same as desktop
- [x] OLED tab + OLED layer-name field on Keymap (user confirmed)
- [ ] `.vil` save/load round-trip including trackpad/RGB data — save to Desktop works; save to
      Documents failed in the Windows dialog ("C:/Users/User/Documents/-web.vil File not found").
      Documents is not OneDrive-redirected on that PC. Added `suggestedName` (keyboard product
      name) to the save picker; differential test pending: does vial.rocks (same code) fail too?
- [x] Custom-colour presets and indicator config survive a reload (user confirmed)

### Phase 3b — fixes in `vial-gui@xcmkb` (as surfaced by Phase 3)
- [x] Persistence of the app data dir. Chosen design (prototyped live in Chrome against the
      deployed page, verified across a reload): `index.html` mounts Emscripten's IDBFS on
      `/home/web_user/.local/share` and loads it before `webmain.main`; `vialglue.fs_sync()`
      (new, `main.c`) flushes it; vial-gui calls `util.persist_app_data()` after its two JSON
      writes (`vial-gui@f2f1cb7`). No change to the file-based code itself.
      Gotcha: mounting at `/home/web_user` fails with `VersionError` — Qt's wasm QSettings owns
      an IndexedDB database of that name via `emscripten_idb_async_*` (IDBStore v22 vs IDBFS v21).
- [ ] Anything else found in testing

### Phase 4 — make iteration cheap
- [x] `actions/cache` for `emsdk/` + pruned `deps/` (`prune-deps.sh`) — verified: run 34939455331
      took 1m 31s on a cache hit (vs 40 min)

### Phase 5 — web-first feature work (vial-gui stays the single codebase)
- Decisions (2026-09-15): web is the primary distribution; desktop builds are kept only for
  firmware flashing. No manual version bumps for web — the page footer's commit hashes identify
  a build. Rules for new code: no nested event loops (`exec_()`), non-blocking dialogs, file
  I/O only through `vialglue`, keep it light.
- [x] Keycode search: new first tab "Search" in the keycode picker (`vial-gui@6508fc8`).
      Matches label / QMK ID / alias / description, grouped by the tab the keycode lives in,
      ranked exact > prefix > contains > alias > words, capped at 80, 150 ms debounce.
      Headless-tested on desktop (offscreen Qt); verify look and feel on web.

- [x] Tooltips on web (`vial-gui@f737912` + this commit). Root cause: Qt 5.14's WASM compositor
      activates every new window, tool tips included, and QTipLabel hides itself the moment its
      window is deactivated — so a tip flashed and vanished, and the main window was left inactive
      (no more tips until a click). Qt 5 never fixed it; Qt 6 rewrote the compositor and added a
      `Q_OS_WASM` case in qtooltip.cpp (QTBUG-94583 is still open). `patches/qt/wasm-tooltips.patch`
      backports both: tool-tip windows never take activation in `QWasmCompositor::notifyTopWindowChanged`,
      plus the Qt 6 qtooltip.cpp condition. `WA_AlwaysShowToolTips` on the main window is kept as
      insurance. Changing `patches/**` invalidates the deps cache once (~40 min).

### Phase 6 — firmware flashing from the web page (UF2 / RP2040)  — to-do, decided 2026-09-16
Goal: a client updates the keyboard firmware from the Pages URL without installing anything.
Facts that shape it (verified 2026-09-16):
- All xcmkb boards are RP2040 on the ROM UF2 bootloader (BOOTSEL: VID 0x2E8A PID 0x0003); a firmware
  UF2 is ~200 KB, family ID 0xE48BFF56, ~100 KB of flash.
- Two browser routes, both Chrome/Edge-only like WebHID: **WebUSB → PICOBOOT** (bootrom vendor
  interface; WinUSB auto-binds on Windows via WCID, macOS works, Linux needs a udev rule) using the
  MIT-licensed `piersfinlayson/picoflash` `pkg/` (~50 KB ES modules) + its `js/uf2/uf2.js`; or
  **File System Access → the RPI-RP2 drive** (no driver, but the user must pick the drive and Chrome
  writes through a `.crswap` file, so the board reboots mid-write and `close()` throws after a complete
  write — treat that as success). WebUSB primary, FS Access fallback, plain download as last resort.
- The host cannot currently reboot the board into the bootloader: every xcmkb keymap is
  `VIAL_INSECURE = yes` and vial-qmk compiles `id_bootloader_jump` only for secure builds
  (`quantum/via.c`). *Security → Reboot to bootloader* is therefore a no-op, and on the web build it then
  raises (`Keyboard.reset()` calls `dev.close()`, which the emscripten `hiddevice` in `hidproxy.py` lacks).
  Interim: double-tap reset (enabled in every config.h) or a `QK_BOOT` key.
- Split board → both halves need the UF2. EE_HANDS builds: either half can be the USB master;
  MASTER_LEFT builds: the right half only via double-tap reset.
- The page is COEP `require-corp`: firmware must be same-origin or sent with
  `Access-Control-Allow-Origin` (GitHub Pages does; Drive/Dropbox/GitBook attachments and
  `github.com/…/releases/download/` do not). Decision: same-origin `firmware/` in this repo.
- WebUSB and file pickers exist only on the page thread → the flasher is JS in `src/`; Python gets a thin
  tab plus one `vialglue` call, on the same postMessage pattern as the `.vil` dialogs (keeps the
  user-activation window).
- Board identification (checked 2026-09-16): every keymap sets a USB product string with trackpad,
  orientation, version and — from v5.04 — the PCB batch word (`Legendary` = ano, `Legendary RGB`,
  `Signature RGB` = 2u, none = sofleplus2), e.g. `SoflePLUS2 TPS65 Horizontal v5.10h Signature RGB`.
  WebHID exposes it as `productName` at device pick, before Python boots. Ambiguous for older firmware:
  v5.04–v5.09 `Legendary` covers ano *and* legendaryrgb, ≤ v5.03 has no batch word. VID/PID are shared
  (`0xFC32:0x0287`; only 510h/tps43-510 moved to `0x0288`/`0x0289`) and the Vial UID names the layout,
  not the batch (legendaryrgb shares sofleplus2's), so a manual fallback list stays necessary.

- [x] A — catalogue (2026-09-16): `firmware/manifest.json` + `*.uf2` in this repo, copied to `src/build/firmware/` by
      `src/build.sh`. One entry per *board* (family/trackpad/orientation/batch): `{id, name, batch_label,
      match: {product: [regex…], fw_id?, uid?}, latest, firmware:[{version, date, file, sha256, notes,
      changelog_url}]}`. Drop workflow: keep QMK output names (`xcmkb_<variant>_<keymap>.uf2`, which
      encode board/trackpad/orientation/version), run `firmware/make-manifest.py` (derives board +
      version from the name, adds sha256; you fill date + notes), push to `xcmkb` or use GitHub's
      "Upload files" → CI ~1.5 min → live. Keep the last 2–3 versions per board for rollback.
- [x] A2 v1 (download/changelog/warnings/instructions; Flash button pending B) — **Updates** tab (UX decided 2026-09-16; Qt, after OLED, web build only, titled
      `Updates (beta)`, tab label becomes `Updates *` when an update is found via `setTabText`).
      Detection in Python from `device.desc["product_string"]` with the format below → board +
      version → compare with manifest `latest` by equality; ambiguous/unknown → board combo box.
      Contents: Your keyboard / Latest firmware / Status / release notes; buttons *Download update
      (.uf2)* (new `vialglue.download_file` → JS `<a download>`, same-origin), *Flash from browser
      (beta)* (→ B's overlay via `vialglue.flash_firmware(entry_json)`; hidden until B exists),
      *View changelog* (existing `open_url` bridge → GitBook firmware changelog); warning
      "Updating may reset your keymap — save your layout first; unsaved changes will be lost";
      both-halves instructions (save .vil → double-tap reset on the USB half → RPI-RP2 → copy/flash →
      unplug, cable directly into the OTHER half (TRRS carries no USB) → repeat with the same file →
      back to the usual half, Start Vial, load .vil). Manifest reaches Python by `build.sh` copying
      `firmware/manifest.json` into the preloaded `usr/local/` (read like `qmk_settings.json`).
      Delivery: A → tab v1 (download/changelog/warnings/instructions) → B → Flash button.
- [x] A3 (2026-09-16, comment added; folders still uncommitted) — firmware repo: naming-rule comment above `#define PRODUCT` in
      `sofleplus2/keymaps/{tps65-510,tps43-510,tps65-510h}/config.h` (text agreed 2026-09-16; the other
      variants' 510 keymaps optional). Commit the three untracked 510 keymap folders while there.
- [ ] B — page-side flasher, standalone: vendor picoflash `pkg/` + `uf2.js` into `src/flash/` (MIT notice
      kept, copied by `build.sh`); "Update firmware" on the start screen + an overlay like `#unlock`;
      pre-flight checks (family ID, address range, size cap, sha256 vs manifest); WebUSB erase/write with
      progress, then reboot; FS Access and download fallbacks; bootloader-entry and "flash the other half"
      instructions. Doubles as the recovery path when Vial cannot connect.
- [ ] C — firmware (vial-qmk-xcmkb): allow `id_bootloader_jump` in INSECURE builds (drop the guard or handle
      it in `raw_hid_receive_kb`). Only boards flashed with this or newer can be rebooted by the host;
      older ones go through B manually once.
- [ ] C2 — firmware identity, same release as C: one define per keymap (`XCMKB_FW_ID
      "sofleplus2u/tps65h/5.11h"`) returned by a new VialRGB GET `0x54`; manifest `match.fw_id` takes
      precedence over the product-string rules. Also write the product-string format below into
      `vial-qmk/keyboards/xcmkb/README.md` so firmware work keeps to it.

Product-string format (decided 2026-09-16; it is `#define PRODUCT` in each keymap's `config.h` of the
firmware repo, overriding `keyboard_name` in keyboard.json — not vial.json, nothing on the web side):
`<Family> [<TPS43|TPS65>] [Horizontal] v<major>.<minor>[<letter>] [<batch words>] [Beta …]`
- Family `SoflePLUS` = sofleplus1 (batch 0, `SoflePLUS v1.10`, PID 0x028A); `SoflePLUS2` = the rest.
- Batch words are the trailing phrase, matched whole (`Legendary` is a prefix of `Legendary RGB`):
  none = sofleplus2 (latest batch), `Legendary` = sofleplus2ano (batch 1), `Legendary RGB` =
  sofleplus2legendaryrgb (batch 1), `Signature RGB` = sofleplus2u (batch 2).
- `Horizontal` always pairs with the `h` version suffix (`Horizontal v5.10h`); `Beta …` is never latest.
- Verified 2026-09-16: all four sofleplus2 variants × tps43-510 / tps65-510 / tps65-510h conform.
  Scope decision: auto-detect and the update prompt apply to v5.10 and later only; older boards
  (≤ v5.09 `Legendary` ambiguous, ≤ v5.03 no batch word) pick from a narrowed list. CornePLUS/CornePLUS2
  have no `PRODUCT` define (no version) → manual list unless one is added in this format.
- [ ] D — Vial UI integration (vial-gui@xcmkb + vial-web): `hiddevice.close()` no-op; web-only "Firmware"
      tab replacing the hidden vibl updater on emscripten, listing manifest entries that match the keyboard
      UID (page fetches the manifest, hands JSON to Python like `get_device_desc`); "Save layout first";
      *Update* → `main.lock_ui()` → `keyboard.reset()` → `vialglue.flash_firmware(entry)` → B's overlay with
      the file preselected. MVP ends with `location.reload()`; later rebind `g_device` from
      `navigator.hid.getDevices()` / the `connect` event, refresh the device desc, `on_click_refresh()`,
      restore the backed-up layout; then prompt for the second half.
- [ ] E — verify and document: Windows 11 Chrome/Edge (WinUSB auto-bind, plus Zadig / FS-Access fallback),
      macOS Chrome, both halves, EE_HANDS vs MASTER_LEFT, wrong-file rejection, unplug mid-flash (bootrom is
      safe, retry); client steps in the GitBook.

Estimate: A 1–2 h, B ~1 day, C 30 min + a firmware release, D 1–2 days, E ½ day. B is the first milestone
(works for every existing board today); C + D turn it into the one-click flow.

### Later / optional
- [x] Fork notice: start screen `#notice` + About box (`vial-gui@feff418`) say it is a customised
      XCMKB build, not affiliated with the Vial project, with upstream/source/license links;
      tab title "Vial Web (XCMKB)"
- [x] Links inside the Qt UI work: Qt's openUrl is `window.open()` in the worker, which upstream
      stubbed as a no-op; `worker.js` now posts `open_url` and the page opens a new tab
- ~~`repository_dispatch` from vial-gui CI~~ — obsolete, the app is vendored (2026-09-16)
- [ ] Periodic `git merge upstream/main` into `xcmkb`; upstream vial-gui changes now have to be
      ported by hand into `vialgui/python`

## Log

- 2026-09-15 — Phases 1–2 done. Pushed `01ed96d` (repoint + Pages + coi-serviceworker) then
  `465497b` (deploy from `xcmkb`). Fork Actions enabled, default branch → `xcmkb`,
  Pages source → GitHub Actions, `github-pages` env → no branch restriction.
  Waiting on run 34930649973.
- 2026-09-15 — Run 34930649973 green (build 40 min), Pages live. User hit the first-visit
  SharedArrayBuffer alert and reported the keyboard not being detected. Verified in Chrome that the
  deployed page is cross-origin isolated and boots Python after the SW reload; the alert was the
  runtime starting before that reload. Pushed: gated runtime load, `prune-deps.sh`, dependency
  cache in CI. HID detection still open pending user details.
- 2026-09-15 — User confirms detection + keymap + lighting effect work. Prototyped persistence in
  the live page (IDBFS mount under `.local/share`, found the `/home/web_user` DB-name collision
  with Qt QSettings), then implemented: `vial-gui@f2f1cb7` + this commit. Useful debugging
  channel: with the page booted, `PThread.runningWorkers[0].postMessage({cmd:"py", payload})`
  runs Python in the worker and `print()` lands in the DevTools console.
- 2026-09-15 — Colour pickers fixed (`exec_()` → non-blocking), persistence confirmed by user,
  cache hit brings CI to ~1.5 min. Open: `.vil` save into Documents fails in the Windows dialog
  while Desktop works; added a suggested filename, awaiting a vial.rocks comparison.
- 2026-09-15 — Fork notice added (start screen + About), About-box links made to work on web
  (`open_url` bridge). User plans to share https://superxc3.github.io/vial-web/ with clients.
- 2026-09-15 — Keycode search shipped in vial-gui; this push rebuilds vial-web against it.
- 2026-09-15 — Search field narrowed. Tooltips: WA_AlwaysShowToolTips alone made them flash and
  vanish; traced to window activation in the 5.14 WASM compositor, backported a fix as a Qt patch.
- 2026-09-16 — Trackpad tab confirmed working on macOS (the settings were not applied because
  *Apply Settings to Keyboard* had not been pressed; no bug). UF2 flashing plan recorded as Phase 6
  (to-do, no code yet); hosting decided: same-origin `firmware/` in this repo.
- 2026-09-16 — Auto-detect of board/version for the update prompt designed: product string now, `0x54`
  firmware ID later; recorded as Phase 6 A2/C2.
- 2026-09-16 — Update UX decided: an *Updates (beta)* tab after OLED (asterisk when available, download /
  flash / changelog, keymap warning, both-halves instructions); firmware drops go into `firmware/`.
- 2026-09-16 — Phase 6 A/A2/A3 built: `firmware/` with 10 v5.10 UF2s + `make-manifest.py` (reads the
  product string out of each UF2's USB descriptor, so the catalogue matches what the board announces),
  `build.sh` preloads the manifest and serves `firmware/`, `vialglue.download_file` bridge, and the
  *Updates (beta)* tab in vial-gui (`editor/updates.py`; headless-tested against the real manifest for
  exact hit / rule hit / too-old / unknown / manual pick). Needs vial-gui pushed before vial-web rebuilds.
- 2026-09-16 — Decision: stop maintaining the vial-gui repo. Its Python app (102 files, 796 KB) and
  two JSON resources are vendored into `vialgui/`; CI no longer clones vial-gui. The Updates tab
  (`vialgui/python/editor/updates.py`) ships with this commit.
