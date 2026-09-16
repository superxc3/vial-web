# src/flash — in-browser firmware update

- `picoboot/` — the PICOBOOT-over-WebUSB library from
  [piersfinlayson/picoflash](https://github.com/piersfinlayson/picoflash) `pkg/` at commit 6783554
  (2026-08-25), MIT (see `picoboot/LICENSE`). Unmodified except that `index.js` is left out: at that
  commit it re-exports constants that `constants.js` does not define, which makes the module fail to load.
  `flasher.js` imports the individual files instead.
- `uf2.js` — picoflash's UF2 → flash-image converter (`js/uf2/uf2.js`), MIT.
- `flasher.js` — ours: pre-flight checks, the `#flash` overlay flow, chunked write with progress,
  read-back verification and reboot. Loaded on demand by `index.html` when the Updates tab asks for
  an update (`vialglue.flash_firmware`).
