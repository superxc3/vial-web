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
- CI has no dependency cache yet: every run rebuilds Qt 5.14 + CPython + PyQt5 (~1-2 h).
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
- [ ] First green build of `xcmkb` (run 34930649973, started 2026-09-15 04:54 UTC)

### Phase 3 — verify in the browser (Chrome/Edge + keyboard)
- [ ] Start button enables, Qt UI appears (proves COOP/COEP shim works)
- [ ] Keymap tab baseline
- [ ] Lighting tab: per-key RGB, indicators
- [ ] Trackpad tab
- [ ] OLED tab + OLED layer-name field on Keymap
- [ ] `.vil` save/load round-trip including trackpad/RGB data
- [ ] Custom-colour presets — **known gap**: `rgb_configurator.py` writes JSON to
      `QStandardPaths.AppLocalDataLocation`; on WASM that is in-memory and lost on reload

### Phase 3b — fixes in `vial-gui@xcmkb` (as surfaced by Phase 3)
- [ ] `localStorage` bridge: `vialglue.storage_get/storage_set` in `main.c`, handler in
      `index.html`, used by `rgb_configurator.py` under `sys.platform == "emscripten"`
- [ ] Anything else found in testing

### Phase 4 — make iteration cheap
- [ ] `actions/cache` for `emsdk/` + `deps/` keyed on `version.sh` + `patches/**`
      (only if the size report says it fits GitHub's 10 GB cache) → runs drop to minutes

### Later / optional
- [ ] Branding in `index.html` (title, start button, gitbook link)
- [ ] `repository_dispatch` from vial-gui CI so a push to `vial-gui@xcmkb` rebuilds vial-web
- [ ] Periodic `git merge upstream/main` into `xcmkb` on both repos

## Log

- 2026-09-15 — Phases 1–2 done. Pushed `01ed96d` (repoint + Pages + coi-serviceworker) then
  `465497b` (deploy from `xcmkb`). Fork Actions enabled, default branch → `xcmkb`,
  Pages source → GitHub Actions, `github-pages` env → no branch restriction.
  Waiting on run 34930649973.
