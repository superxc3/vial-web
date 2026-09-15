# vialgui/ — the Vial application

The Python/PyQt5 Vial app that `src/build.sh` compiles into the web page.

- `python/` — imported from [superxc3/vial-gui](https://github.com/superxc3/vial-gui/tree/xcmkb)
  `src/main/python` at commit f737912 (2026-09-15) and maintained **here** since; that repository is
  frozen and no longer built for desktop.
- `qmk_settings.json` — was `src/main/resources/base/qmk_settings.json`.
- `build_settings.json` — was `src/build/settings/base.json` (app name/version shown in About).

Rules for changes (see PLAN.md): no nested event loops (`exec_()`), non-blocking dialogs, file I/O only
through `vialglue`, web-specific behaviour behind `sys.platform == "emscripten"`.

Headless test on Windows with the old checkout's venv, e.g.:

    QT_QPA_PLATFORM=offscreen C:/Users/User/vial-gui/venv/Scripts/python.exe -c "import sys; sys.path.insert(0, 'vialgui/python'); ..."

Upstream Vial is GPL-2.0-or-later; so is this tree.
