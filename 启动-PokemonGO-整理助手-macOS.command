#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
DEPENDENCY_STAMP="$VENV_DIR/.pogo-dependencies.sha256"
NATIVE_SOURCE="$APP_DIR/macos/PokemonGoOrganizerApp.swift"
NATIVE_PLIST="$APP_DIR/macos/Info.plist"
NATIVE_ICON_RENDERER="$APP_DIR/macos/CreateIcon.swift"
NATIVE_APP="$APP_DIR/.pogo-data/PokemonGOOrganizer.app"
NATIVE_CONTENTS="$NATIVE_APP/Contents"
NATIVE_BINARY="$NATIVE_CONTENTS/MacOS/PokemonGoOrganizer"
NATIVE_ICON="$NATIVE_CONTENTS/Resources/PokemonGoOrganizer.icns"
NATIVE_STAMP="$APP_DIR/.pogo-data/PokemonGOOrganizer.source.sha256"
NATIVE_SWIFT_TARGET="arm64-apple-macosx27.0"
cd "$APP_DIR"

PYTHON_BIN="${POGO_PYTHON:-$(command -v python3 || true)}"
if [[ -z "$PYTHON_BIN" ]]; then
  print -u2 "未找到 Python 3。请先从 python.org 安装 Python 3.11 或更高版本。"
  read -r "?按回车关闭…"
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
  print -u2 "需要 Python 3.11 或更高版本。当前解释器：$PYTHON_BIN"
  read -r "?按回车关闭…"
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  print "首次启动：正在建立本地 Python 环境…"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

PROJECT_HASH="$(/usr/bin/shasum -a 256 "$APP_DIR/pyproject.toml" | /usr/bin/awk '{print $1}')"
INSTALLED_HASH=""
if [[ -f "$DEPENDENCY_STAMP" ]]; then
  INSTALLED_HASH="$(<"$DEPENDENCY_STAMP")"
fi

if [[ "$PROJECT_HASH" != "$INSTALLED_HASH" ]] || \
   ! "$VENV_PYTHON" -c 'import PIL, onnxruntime, rapidocr' >/dev/null 2>&1; then
  print "首次启动或依赖已更新：正在安装本地 OCR 运行环境…"
  if ! "$VENV_PYTHON" -m pip install --disable-pip-version-check -e "$APP_DIR"; then
    print -u2 "依赖安装失败。首次安装需要网络；请检查网络后重试。"
    read -r "?按回车关闭…"
    exit 1
  fi
  print -r -- "$PROJECT_HASH" >| "$DEPENDENCY_STAMP"
else
  print "本地 OCR 依赖已就绪；本次无需联网。"
fi

if ! command -v swiftc >/dev/null 2>&1; then
  print -u2 "未找到 Swift 编译器。请安装 Xcode 或 Xcode Command Line Tools 后重试。"
  read -r "?按回车关闭…"
  exit 1
fi

NATIVE_HASH="$({ print -r -- "$NATIVE_SWIFT_TARGET"; /usr/bin/shasum -a 256 "$NATIVE_SOURCE" "$NATIVE_PLIST" "$NATIVE_ICON_RENDERER"; } | /usr/bin/shasum -a 256 | /usr/bin/awk '{print $1}')"
BUILT_HASH=""
if [[ -f "$NATIVE_STAMP" ]]; then
  BUILT_HASH="$(<"$NATIVE_STAMP")"
fi

if [[ "$NATIVE_HASH" != "$BUILT_HASH" ]] || [[ ! -x "$NATIVE_BINARY" ]] || [[ ! -f "$NATIVE_ICON" ]]; then
  print "正在构建原生 macOS 界面…"
  mkdir -p "$NATIVE_CONTENTS/MacOS" "$NATIVE_CONTENTS/Resources"
  /usr/bin/swiftc -target "$NATIVE_SWIFT_TARGET" -parse-as-library -framework SwiftUI -framework AppKit \
    "$NATIVE_SOURCE" -o "$NATIVE_BINARY"
  /bin/cp "$NATIVE_PLIST" "$NATIVE_CONTENTS/Info.plist"
  ICON_BUILD_DIR="$(/usr/bin/mktemp -d)"
  ICONSET_DIR="$ICON_BUILD_DIR/PokemonGoOrganizer.iconset"
  mkdir -p "$ICONSET_DIR"
  /usr/bin/swiftc -target "$NATIVE_SWIFT_TARGET" -framework AppKit "$NATIVE_ICON_RENDERER" -o "$ICON_BUILD_DIR/render-icon"
  "$ICON_BUILD_DIR/render-icon" "$ICONSET_DIR"
  /usr/bin/iconutil -c icns "$ICONSET_DIR" -o "$NATIVE_ICON"
  /bin/rm -rf "$ICON_BUILD_DIR"
  # A legacy build stored its stamp inside the bundle root, which makes a
  # signed app look tampered with to LaunchServices. It is generated metadata.
  /bin/rm -f "$NATIVE_APP/.source.sha256"
  /usr/bin/codesign --force --sign - "$NATIVE_APP"
  print -r -- "$NATIVE_HASH" >| "$NATIVE_STAMP"
fi

# LaunchServices applies the bundle icon and integrates the process with Dock,
# Stage Manager, dark mode, and window restoration. AppRoot also discovers the
# project from the bundle, so this works without relying on inherited shell env.
/usr/bin/open "$NATIVE_APP"
