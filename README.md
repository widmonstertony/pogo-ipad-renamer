# Pokémon GO iPad 横屏整理助手

一个完全本地、确定性的 Python 桌面程序：通过局域网 iOS MCP 读取 iPad 上的
Pokémon GO，识别繁中名称和游戏自带鉴定条，只给仍使用完整默认名称的宝可梦添加
IV 昵称；已有自定义/IV 昵称原样保留并自动继续下一只。

它不使用 Ollama 或任何本地/云端大模型。名称由 RapidOCR 识别，IV 由像素测量，
点击只使用已校准锚点和当前截图验证。

## 当前设备布局

- iPad14,6，iPadOS 16.1
- iOS MCP 触控空间 1366×1024
- Pokémon GO 在横屏/Stage Manager 布局中显示
- 默认 MCP 地址：`http://127.0.0.1:8090/mcp`（请在 GUI 中改为设备实际地址）

其他设备和分辨率必须重新校准；程序不会把未知方向当成可点击页面。

## 主要行为

- 批量数量可有限或不限，并显示当前第几只、改名/跳过/不可读计数。
- 可安全暂停：完成当前一只并回到详情页后暂停，继续时复核身份。
- 已有昵称自动跳过，不打开改名框。
- 鉴定不稳定时有限只读重测；单只仍不可读则保留原名并继续。
- 翻页被游戏吞掉时，在重新证明仍为同一详情页后有限重试。
- Windows 与 macOS 批次运行期间临时保持电脑和显示器唤醒；退出后恢复。
- 输入前逐字核验，提交后验证弹窗消失并返回详情页。
- 不包含传送、强化、进化、定位修改、完整性绕过或反检测功能。

## Windows 启动

当前机器可双击：

```text
release\启动-PokemonGO-整理助手-v26.cmd
```

开发方式：安装 Python 3.11+，执行 `python -m pip install -e .`，然后运行：

```powershell
python launcher_ipad_landscape_v9.py
```

## macOS 启动

支持 Intel 与 Apple Silicon Mac。安装带 Tk 的 Python 3.11+（推荐 python.org 官方
安装包），首次右键打开：

```text
启动-PokemonGO-整理助手-macOS.command
```

启动器会先校验 Python 版本和 Tk，在仓库内创建 `.venv`，安装 RapidOCR、ONNX
Runtime 和 Pillow，然后打开同一套 GUI。依赖与 `pyproject.toml` 没有变化时，后续
双击不会再次访问网络。若使用 Homebrew Python 且提示缺少 Tk，请安装与 Python
版本匹配的 `python-tk`；也可临时用环境变量 `POGO_PYTHON` 指定解释器。

从 GitHub ZIP 下载后若双击没有执行权限，在终端运行一次：

```bash
chmod +x ./启动-PokemonGO-整理助手-macOS.command
```

macOS 与 iPad 必须能通过局域网互访；在 GUI 顶部填写 iOS MCP 的 `/mcp` 地址。
批次期间使用系统自带 `caffeinate` 临时防止 Mac 与显示器睡眠，任务结束后自动释放；
设备全局锁使用 macOS 原生 `flock`，不会与第二个窗口同时触控 iPad。

## 测试

Windows PowerShell：

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests
```

macOS：

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -s tests
```

## 隐私与恢复

`.env`、`.pogo-data/`、`.pogo-journal/` 和虚拟环境均被 Git 忽略。动作审计日志、
诊断截图、MCP 地址设置和运行进度不会上传到仓库。任务可以从任意已验证详情页重启；
因为已有昵称必定跳过，重复运行不会再次改写它们。

## 风险

自动操作 Pokémon GO 界面可能违反游戏服务条款并带来账号风险。越狱设备不受
Niantic 支持。本项目不尝试规避检测；建议先用只读扫描和小批次验证布局。
