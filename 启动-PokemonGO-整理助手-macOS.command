#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
DEPENDENCY_STAMP="$VENV_DIR/.pogo-dependencies.sha256"
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

if ! "$PYTHON_BIN" -c 'import tkinter' >/dev/null 2>&1; then
  print -u2 "当前 Python 缺少 Tk 图形界面。建议安装 python.org 的 macOS Python，或安装与当前 Python 匹配的 python-tk。"
  read -r "?按回车关闭…"
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  print "首次启动：正在建立本地 Python 环境…"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
  print -u2 "虚拟环境缺少 Tk。请删除 .venv 后改用 python.org 的 macOS Python 重新启动。"
  read -r "?按回车关闭…"
  exit 1
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

exec "$VENV_PYTHON" "$APP_DIR/launcher_ipad_landscape_v9.py"
