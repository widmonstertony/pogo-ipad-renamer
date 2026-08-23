#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
VENV_PYTHON="$APP_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  print -u2 "找不到本地 Python 环境。请先运行一次“启动-PokemonGO-整理助手-macOS.command”。"
  read -r "?按回车关闭…"
  exit 1
fi

exec env PYTHONPATH="$APP_DIR/src" "$VENV_PYTHON" -u -m \
  pogo_iphone_renamer.headless_batch_launcher \
  --root "$APP_DIR" --mode rename --from-current-detail
