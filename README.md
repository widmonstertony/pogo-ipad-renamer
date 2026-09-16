# Pokémon GO iPad Rename Assistant

[Traditional Chinese](README.zh-Hant.md) · [Getting started](docs/getting-started.md) · [Reliability notes](docs/reliability.md)

A local, deterministic desktop assistant that walks through Pokémon GO on an
iPad and adds IV-based nicknames to Pokémon that still use their complete
default Traditional Chinese species name.

The assistant reads the game through an iOS MCP service on your local network.
It uses RapidOCR for names and pixel measurements for the in-game appraisal
bars. It does **not** use Ollama, a local language model, or a cloud model.

## What it does

- Starts from the current, confirmed Pokémon GO screen and continues through
  Pokémon one at a time.
- Renames only a Pokémon whose visible name is verified as its complete default
  species/form name.
- Preserves existing custom and IV nicknames without opening the rename field.
- Measures Attack, Defense, Stamina, and total IV from the appraisal screen.
- Verifies the original name before editing and verifies the final nickname
  character by character after submission.
- Supports a fixed batch size or an unlimited run.
- Shows the current Pokémon, current screen, current step, live iPad preview,
  counters, and any action required from the user.
- Pauses at a safe boundary and resumes only after rechecking the current page.
- Keeps a macOS batch running in a detached worker when the dashboard closes or
  the Mac locks.

When a name or IV cannot be read reliably, the Pokémon is left unchanged. The
assistant never guesses.

## What it does not do

The project has no transfer, power-up, evolve, purify, trade, catch, battle,
location-spoofing, integrity-bypass, or anti-detection features. It does not
attempt to bypass a locked iPad. If the iPad locks, interaction waits until it
is unlocked again.

## Requirements

### macOS

- Apple silicon Mac running macOS 27
- Python 3.11 or later
- Xcode or Xcode Command Line Tools, including the macOS 27 SDK
- An iPad running Pokémon GO and a compatible iOS MCP service
- The Mac and iPad on a trusted network where the Mac can reach the MCP URL

The currently calibrated layouts use a 1366×1024 MCP touch space and include
the supported full-screen and Stage Manager capture variants. Unknown screen
geometry is rejected instead of being treated as safe to click.

### Windows

- Windows with Python 3.11 or later
- The same compatible iPad/iOS MCP setup

Windows uses the compatibility desktop interface. The current macOS experience
is the primary interface.

## Quick start on macOS

1. Install Python 3.11+ and Xcode Command Line Tools:

   ```bash
   xcode-select --install
   ```

2. Clone or download this repository.

3. Make sure Pokémon GO is open on the iPad and the iOS MCP health endpoint is
   reachable from the Mac.

4. Right-click and open:

   ```text
   启动-PokemonGO-整理助手-macOS.command
   ```

   If a ZIP download removed its executable permission, run once:

   ```bash
   chmod +x ./启动-PokemonGO-整理助手-macOS.command
   ```

5. In **Preferences**:

   - enter the iOS MCP URL, including `/mcp`;
   - click **Check connection**;
   - choose a batch limit or enable **Unlimited**;
   - click **Save connection & scope**.

6. Return to **Task Overview** and choose **Start batch rename**.

On first launch, the script creates `.venv`, installs the local OCR runtime,
builds the native SwiftUI app, and opens it. Later launches reuse both the
environment and app unless their sources have changed.

The interface follows the system appearance and language by default. You can
also choose English or Chinese and System, Light, or Dark appearance in
**Preferences**; the selection is saved locally.

See the full [Getting Started Guide](docs/getting-started.md) for MCP URL
examples, screen preparation, status meanings, safe pause/resume behavior, and
recovery steps.

## Quick start on Windows

Install the package once:

```powershell
python -m pip install -e .
```

Then double-click:

```text
release\启动-PokemonGO-整理助手.cmd
```

For development, run:

```powershell
python launch_desktop.py
```

## How safety and recovery work

- Every touch is tied to the latest verified screen and calibrated geometry.
- A second desktop window cannot control the same iPad concurrently.
- MCP transport loss is handled with bounded backoff; no write is replayed when
  its result is unknown.
- Black or stale capture frames are not treated as proof that the game closed.
- A swallowed swipe or tap is retried only after the assistant proves that the
  same safe page is still visible.
- Pause finishes the current safe unit of work before stopping interaction.
- Stop exits text-entry and dialog states before the worker ends.
- Reopening the macOS app reconnects to the existing detached batch instead of
  launching a second worker.

Runtime state, previews, diagnostics, and audit logs are stored under
`.pogo-data/` and `.pogo-journal/`. They are ignored by Git.

## Development

Install in editable mode:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Run the test suite on macOS:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -s tests
```

Run it in Windows PowerShell:

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests
```

## Project layout

- `macos/` — native SwiftUI app, icon renderer, and bundle configuration.
- `src/pogo_iphone_renamer/batch_agent.py` — batch state-machine coordinator.
- `batch_navigation.py`, `game_navigation.py` — verified detail navigation.
- `name_recognition.py`, `text_localization.py` — name classification and OCR
  geometry.
- `appraisal_*.py` — appraisal location, decoding, calibration, and stable-frame
  verification.
- `rename_*.py`, `name_input.py` — rename dialog, exact input verification,
  submission, and recovery.
- `device_controller.py`, `device_recovery.py` — safe touch mapping and capture
  recovery.
- `legacy_gui*.py` — Windows compatibility interface.
- `tests/` — regression tests organized around production modules.

Historical numbered implementations and one-off experimental launchers have
been removed. Git history is the source of previous versions.

## Privacy

All OCR and appraisal processing runs locally. `.env`, `.venv`, `.pogo-data/`,
and `.pogo-journal/` are ignored by Git. MCP addresses, screenshots, progress,
and action journals are not committed to the repository.

## Risk notice

Automating the Pokémon GO interface may violate the game's Terms of Service and
can put an account at risk. Jailbroken devices are not supported by Niantic.
This project does not attempt to evade detection. Validate a device layout with
a small supervised batch before starting a long run.
