# vial-web (xcmkb)

Web build of Vial for XCMKB keyboards (per-key RGB, layer indicators, trackpad, OLED and Updates tabs),
forked from [vial-kb/vial-web](https://github.com/vial-kb/vial-web).

The Vial application itself (Python/PyQt5) lives in `vialgui/` — vendored from
[superxc3/vial-gui@xcmkb](https://github.com/superxc3/vial-gui/tree/xcmkb) at f737912 and maintained here
since — and is compiled to WebAssembly (CPython 3.11 + PyQt5 via Emscripten); there is no separate
JavaScript port. CI builds on every push and deploys `xcmkb` to GitHub Pages; `main` mirrors upstream.

## Building

```
git clone -b xcmkb https://github.com/superxc3/vial-web.git
cd vial-web
git clone https://github.com/vial-kb/via-keymap-precompiled.git
./fetch-emsdk.sh
./fetch-deps.sh
./build-deps.sh
cd src
./build.sh
```

CI caches `emsdk/` and a `prune-deps.sh`-trimmed `deps/` so only the first run pays the ~40 min toolchain build.

Output lands in `src/build`. It uses a pthread build, so the page must be served
cross-origin isolated (COOP/COEP headers); `coi-serviceworker.js` handles that on hosts
such as GitHub Pages that cannot set headers. WebHID requires Chrome/Chromium/Edge.

## Firmware catalogue

`firmware/` holds the `.uf2` files offered by the *Updates* tab and `manifest.json`, which is generated
from them. To publish a firmware:

1. Drop the `.uf2` into `firmware/` (keep QMK's name, e.g. `xcmkb_sofleplus2u_tps65-510h.uf2`).
2. Optionally drop release notes next to it with the same stem (`xcmkb_sofleplus2u_tps65-510h.md`):
   a short, client-facing summary of what changed in that version. The tab shows them for the selected
   version; the full history stays on the GitBook changelog.
3. Run `python firmware/make-manifest.py`. It reads the USB product string embedded in each file
   (`SoflePLUS2 TPS65 Horizontal v5.10h Signature RGB` …), groups files by board (PCB batch + trackpad),
   computes sizes and SHA-256 and picks each board's latest non-beta version. Older versions stay
   listed for rollback; move files you no longer want offered into `firmware/archive/`, which is
   neither scanned nor deployed.
4. Commit and push `xcmkb`; CI copies the catalogue into the deployed page.

The product string must follow the naming rule documented in the generator; the *Updates* tab uses it to
identify the client's board (from v5.10 on) and compares the version with `latest`.
