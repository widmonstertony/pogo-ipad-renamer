# Pokémon GO iPad 橫屏整理助手

[English](README.md) · [英文上手指南](docs/getting-started.md) · [穩定性說明](docs/reliability.md)

一個完全本地、確定性的 Python 桌面程式：通過局域網 iOS MCP 讀取 iPad 上的
Pokémon GO，識別繁中名稱和遊戲自帶鑑定條，只給仍使用完整預設名稱的寶可夢添加
IV 暱稱；已有自訂或 IV 暱稱原樣保留並自動繼續下一隻。

它不使用 Ollama 或任何本地/雲端大模型。名稱由 RapidOCR 識別，IV 由像素測量，
點擊只使用已校準錨點和當前截圖驗證。

## 主要行為

- 從 iPad 當前已確認的 Pokémon GO 畫面接續，逐隻處理。
- 批量數量可有限或不限，並顯示當前寶可夢、畫面、步驟、即時 iPad 預覽和計數。
- 可安全暫停：完成當前安全單元後暫停，繼續時重新核對身份與畫面。
- 已有暱稱自動跳過，不打開改名欄。
- 鑑定不穩定時進行有限次只讀重測；仍無法確認時保留原名並繼續。
- 翻頁被遊戲吞掉時，只有在重新證明仍為同一安全詳情頁後才會有限重試。
- macOS 批次由獨立後台進程運行；關閉控制視窗或 Mac 鎖屏不會停止已啟動的任務。
- 輸入前逐字核驗，提交後驗證彈窗消失並回到詳情頁。
- 不包含傳送、強化、進化、定位修改、完整性繞過或反偵測功能。

iPad 本身鎖屏時，寫入操作會等待使用者解鎖，不會繞過鎖屏保護。

## macOS 27 啟動

需要 Apple Silicon Mac、Python 3.11 或更高版本，以及含 macOS 27 SDK 的 Xcode 或
Xcode Command Line Tools。首次右鍵打開：

```text
啟動-PokemonGO-整理助手-macOS.command
```

啟動器會在倉庫內建立 `.venv`，安裝 RapidOCR、ONNX Runtime 和 Pillow，然後構建並
打開面向 macOS 27 的原生 SwiftUI App。後續啟動會重用未變更的環境與 App。

從 GitHub ZIP 下載後若雙擊沒有執行權限，在終端執行一次：

```bash
chmod +x ./啟動-PokemonGO-整理助手-macOS.command
```

在 App 的「偏好設定」中填入 iOS MCP 的 `/mcp` 網址，檢查連線，並選擇停止數量或
「不限量」。介面預設跟隨系統語言和外觀，也可手動選擇中文/English 及系統/淺色/深色。

macOS 與 iPad 必須能通過可信局域網互訪。請參閱完整的[英文上手指南](docs/getting-started.md)。

## Windows 啟動

安裝 Python 3.11+，執行 `python -m pip install -e .`，然後雙擊：

```text
release\啟動-PokemonGO-整理助手.cmd
```

開發方式：

```powershell
python launch_desktop.py
```

## 測試

macOS：

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -s tests
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests
```

## 代碼結構

- `macos/`：原生 SwiftUI 介面、App 圖示與 bundle 設定。
- `src/pogo_iphone_renamer/batch_agent.py`：批處理狀態機。
- `batch_navigation.py`、`game_navigation.py`：詳情翻頁與頁面驗證。
- `name_recognition.py`、`text_localization.py`：名稱分類和文字位置識別。
- `appraisal_*.py`：鑑定條定位、解碼、校準和穩定畫面複核。
- `rename_*.py`、`name_input.py`：改名彈窗、輸入核驗、提交與復原。
- `device_controller.py`、`device_recovery.py`：安全觸控映射、鎖屏與截圖復原。
- `legacy_gui*.py`：Windows 相容介面；macOS 不使用這些模組。
- `tests/`：與生產模組對應的回歸測試。

## 隱私與復原

`.env`、`.venv`、`.pogo-data/` 和 `.pogo-journal/` 均被 Git 忽略。動作審計日誌、診斷截圖、
MCP 網址和進度不會上傳到倉庫。重新打開 macOS App 會重新連接現有的後台批次，
不會啟動第二個同時觸控 iPad 的任務。

## 風險

自動操作 Pokémon GO 介面可能違反遊戲服務條款並帶來帳號風險。越獄裝置不受 Niantic 支援。
本專案不嘗試規避偵測；建議先用小批量人工監看驗證裝置配置。
