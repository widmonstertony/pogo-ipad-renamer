#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
cd "$APP_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  print -u2 "未找到 Python 3。请先从 python.org 安装 Python 3.11 或更高版本。"
  read -r "?按回车关闭…"
  exit 1
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  print -u2 "当前 Python 缺少 Tk 图形界面。建议安装 python.org 的 macOS Python，或安装与当前 Python 匹配的 python-tk。"
  read -r "?按回车关闭…"
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  print "首次启动：正在建立本地 Python 环境…"
  python3 -m venv "$VENV_DIR"
fi

print "正在检查本地 OCR 依赖…"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -e "$APP_DIR"
exec "$VENV_DIR/bin/python" "$APP_DIR/launcher_ipad_landscape_v9.py"
