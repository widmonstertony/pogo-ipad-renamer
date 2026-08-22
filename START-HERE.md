# 从这里开始

这个项目已经指向当前 iPhone MCP：

```text
http://127.0.0.1:8090/mcp
```

默认使用电脑里已安装的本地模型 `qwen3.8:27b`。手机写操作默认关闭；先只读演练，再启用最多 20 只的人工观察试运行。

## 1. 检查连接

```powershell
cd D:\Documents\GitHub\pogo-iphone-renamer
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

## 2. 启动 Ollama 和 OpenCode

确认 Ollama 正在运行，然后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-opencode.ps1
```

启动脚本会强制选择 `ollama/qwen3.8:27b`，并加载项目中的 `AGENTS.md` 安全规则和本地安全 MCP 代理。

## 3. 第一次只读演练

在 OpenCode 中输入：

```text
只做只读演练。连接 iPhone，确认 Pokémon GO 位于前台，观察当前宝可梦详情页和 Poke Genie 生成的昵称；说明下一步原本会点哪里、输入什么，但不要调用任何写工具。遇到无法确认的界面立即停止。
```

## 4. 开启小批量试运行

先关闭 OpenCode，把 `.env` 中这一行改为：

```text
POGO_WRITE_ENABLED=true
```

重启 OpenCode，再输入：

```text
开始一个最多 20 只的改名试运行。我会看着屏幕。只处理仍为繁体中文物种默认名的宝可梦；必须使用 Poke Genie 已生成的完整昵称，原样保留星标、A/D/S 圆圈值、IV 上标百分比和绝版技能 (+) 标记。每次写操作前重新观察屏幕，改名后核验结果并写日志。任何不确定、界面异常、出现传送/Transfer 相关界面时立即停止。绝不传送宝可梦。
```

## 关键限制

- 只允许安全代理暴露的界面读取、点击、滑动和文本输入工具。
- 只给“当前昵称严格等于物种默认名”的宝可梦改名。
- Poke Genie 没有给出完整昵称时跳过，不让模型自行猜 IV、招式或标记。
- 没有自动传送功能，也不向模型暴露文件、Shell、安装、卸载或系统设置工具。
- 日志保存在项目的 `data` 目录，异常时可审计并从最后成功项继续。
