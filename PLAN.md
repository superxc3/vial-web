# vial-web (xcmkb) — plan & progress log

Goal: a web build of `superxc3/vial-gui@xcmkb` (per-key RGB, layer indicators,
trackpad tab, OLED tab, `.vil` save/restore of those) hosted on GitHub Pages.

## Key facts (non-obvious)

- vial-web is **not a JS rewrite**. `src/build.sh` copies `../vial-gui/src/main/python/*`
  into a CPython 3.11 + PyQt5 tree compiled to WebAssembly with Emscripten. All custom
  Python from vial-gui comes along as-is; web-specific behaviour is behind
  `sys.platform == "emscripten"` and the tiny `vialglue` C module in `src/main.c`.
- The only upstream-repo reference is the `git clone` in `.github/workflows/main.yml`
  (now `-b xcmkb https://github.com/superxc3/vial-gui.git`).
- The build is `-pthread -sPROXY_TO_PTHREAD`, so it needs `SharedArrayBuffer`, which needs
  `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`.
  vial.rocks gets these from Cloudflare. GitHub Pages cannot set headers, so
  `src/coi-serviceworker.js` (MIT, vendored) injects them client-side; it must be the first
  script in `index.html`.
- Branches mirror the vial-gui fork: `main` = clean mirror of `vial-kb/vial-web`,
  `xcmkb` = custom work + default branch. Pages deploys only from `xcmkb`.
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
- [ ] Trackpad tab
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

### Later / optional
- [x] Fork notice: start screen `#notice` + About box (`vial-gui@feff418`) say it is a customised
      XCMKB build, not affiliated with the Vial project, with upstream/source/license links;
      tab title "Vial Web (XCMKB)"
- [x] Links inside the Qt UI work: Qt's openUrl is `window.open()` in the worker, which upstream
      stubbed as a no-op; `worker.js` now posts `open_url` and the page opens a new tab
- [ ] `repository_dispatch` from vial-gui CI so a push to `vial-gui@xcmkb` rebuilds vial-web
      (needs a fine-grained PAT with Actions: write on vial-web stored as a vial-gui secret;
      until then a vial-gui change is deployed by pushing any commit to vial-web `xcmkb`)
- [ ] Periodic `git merge upstream/main` into `xcmkb` on both repos

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
