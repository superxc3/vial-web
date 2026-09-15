#!/bin/bash

# Reduce deps/ to only what src/build.sh needs (headers, static libs, the CPython usr tree) so the
# toolchain can be cached between CI runs. Every path here corresponds to a -I/-L/cp in src/build.sh.
# Builds the slim tree first and only swaps it in at the end, so a failure leaves deps/ untouched.

set -e

source ./version.sh

KEEP=(
    cpython/Include
    cpython/builddir/emscripten-browser/libpython3.11.a
    cpython/builddir/emscripten-browser/Modules/_decimal/libmpdec/libmpdec.a
    cpython/builddir/emscripten-browser/Modules/expat/libexpat.a
    cpython/builddir/emscripten-browser/usr
    qt5/qtbase/lib
    qt5/qtbase/plugins/platforms
    qt5/qtbase/plugins/iconengines
    qt5/qtbase/plugins/imageformats
    PyQt5-${PYQT5_VER}/QtCore/libQtCore.a
    PyQt5-${PYQT5_VER}/QtGui/libQtGui.a
    PyQt5-${PYQT5_VER}/QtWidgets/libQtWidgets.a
    PyQt5-${PYQT5_VER}/QtSvg/libQtSvg.a
    PyQt5_sip-${PYQT5SIP_VER}/libsip.a
    xz-${XZ_VER}/prefix/lib
)

rm -rf deps-slim
mkdir deps-slim

for p in "${KEEP[@]}"; do
    mkdir -p "deps-slim/$(dirname "$p")"
    cp -a "deps/$p" "deps-slim/$p"
done

# pyconfig.h and friends live in the build dir root (-I .../emscripten-browser/)
cp -a deps/cpython/builddir/emscripten-browser/*.h deps-slim/cpython/builddir/emscripten-browser/

rm -rf deps
mv deps-slim deps

du -sh deps emsdk
