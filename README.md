# vial-web (xcmkb)

Web build of the [superxc3/vial-gui](https://github.com/superxc3/vial-gui/tree/xcmkb) fork
(per-key RGB, layer indicators, trackpad and OLED tabs), forked from
[vial-kb/vial-web](https://github.com/vial-kb/vial-web).

The same vial-gui Python source is compiled to WebAssembly (CPython 3.11 + PyQt5 via Emscripten);
there is no separate JavaScript port. CI builds on every push and deploys `xcmkb` to GitHub Pages; `main` mirrors upstream.

## Building

```
git clone -b xcmkb https://github.com/superxc3/vial-web.git
cd vial-web
git clone -b xcmkb https://github.com/superxc3/vial-gui.git
git clone https://github.com/vial-kb/via-keymap-precompiled.git
./fetch-emsdk.sh
./fetch-deps.sh
./build-deps.sh
cd src
./build.sh
```

Output lands in `src/build`. It uses a pthread build, so the page must be served
cross-origin isolated (COOP/COEP headers); `coi-serviceworker.js` handles that on hosts
such as GitHub Pages that cannot set headers. WebHID requires Chrome/Chromium/Edge.
