# Getting Started

This guide covers the current macOS 27 workflow. It replaces older instructions
that used Ollama, OpenCode, or numbered launcher scripts; those components are
not part of the production rename path.

## 1. Prepare the Mac

You need an Apple silicon Mac with:

- macOS 27;
- Python 3.11 or later;
- Xcode or Xcode Command Line Tools with the macOS 27 SDK.

Install the command-line tools if needed:

```bash
xcode-select --install
```

Confirm Python is available:

```bash
python3 --version
```

The result must be Python 3.11 or newer.

## 2. Prepare the iPad and MCP service

1. Start the compatible iOS MCP service on the iPad.
2. Keep the iPad and Mac on a trusted network where they can reach each other.
3. Note the MCP address. It normally looks like:

   ```text
   http://192.168.x.x:8090/mcp
   ```

4. Open Pokémon GO and leave it visible. The assistant can safely continue from
   a confirmed detail, Pokémon storage, game menu, or map screen. Starting from a
   detail page gives the most direct result.
5. Keep the iPad unlocked for interaction. The assistant cannot and should not
   bypass the iPad lock screen.

Do not expose the MCP port to the public internet. It is intended for a trusted
local network only.

## 3. Launch the macOS app

From Finder, right-click and open:

```text
启动-PokemonGO-整理助手-macOS.command
```

If macOS says the file is not executable after downloading a ZIP, open Terminal
in the repository folder and run:

```bash
chmod +x ./启动-PokemonGO-整理助手-macOS.command
```

Then open it again.

The first launch may take several minutes because it:

1. creates the local `.venv` environment;
2. installs RapidOCR, ONNX Runtime, and Pillow;
3. builds the native SwiftUI application;
4. signs the local app bundle and opens it.

The first dependency installation needs internet access. OCR and appraisal
processing are local after installation. Unchanged dependencies and interface
sources are reused on later launches.

## 4. Configure the app

Open **Preferences** in the left sidebar.

### Appearance & language

- **Interface language**: System, Chinese, or English.
- **Appearance**: System, Light, or Dark.

Both changes apply immediately and are kept for the next launch.

### iPad connection

1. Enter the complete MCP address ending in `/mcp`.
2. Click **Check connection**.
3. Confirm that the status changes to **Connected** and identifies the MCP
   service.

The health URL is derived from the same address automatically.

### Batch scope

- Enable **Unlimited** to continue until the end of the current traversal or
  until you stop it.
- Disable **Unlimited** and set **Stop after** for a bounded supervised run.

Click **Save connection & scope** before returning to the task overview.

## 5. Start a batch

1. Make sure Pokémon GO is still visible and the iPad is unlocked.
2. Open **Task Overview**.
3. Click **Start batch rename**.
4. Read the confirmation message and click **Start**.

The worker rechecks the current screen before touching anything. It does not
assume that an old screenshot or coordinate is still valid.

For each Pokémon, the normal flow is:

1. identify the current detail page and full displayed name;
2. preserve and skip an existing custom nickname;
3. open appraisal only for a verified default name;
4. measure Attack, Defense, and Stamina from stable frames;
5. build the local IV nickname;
6. recheck the original field before clearing it;
7. type and verify the complete target nickname;
8. submit and verify the resulting detail page;
9. swipe to the next Pokémon and prove that the identity changed.

If any required evidence is uncertain, the original name is preserved.

## 6. Read the dashboard

The macOS app exposes the active state instead of requiring you to interpret the
raw worker log:

- **Current Pokémon** shows the best verified identity available.
- **Current screen** tells you whether the worker sees detail, appraisal,
  rename, storage, menu, map, lock screen, or a recovery state.
- **Current step** explains what the worker is doing or waiting for.
- **Live preview** shows the latest frame already used by the worker; opening
  the dashboard does not trigger a second MCP screenshot stream.
- **Renamed**, **Skipped**, and **Unreadable** are separate counters.
- **Action required** appears when the worker needs an unlock, MCP recovery, or
  another user-visible condition.
- **Activity** shows recent worker events for diagnosis.

Skipped Pokémon do not count as successful renames. Acceptance counters use
verified completed renames only.

## 7. Pause, resume, and stop

### Safe pause

Click **Safe pause** when you want interaction to stop without abandoning a
partially completed rename. The request takes effect at the next safe boundary.

### Resume

Click **Resume**. The worker rechecks the current Pokémon and screen before it
continues; it does not blindly replay the last action.

### Stop

Click **Stop** and confirm. The worker first leaves input or dialog states safely
and then exits. Stopping does not transfer or delete a Pokémon and does not
relaunch the game.

Closing the macOS window does not stop an active batch. Reopen the app with the
same launcher to reconnect to it. The app will not start a second worker for the
same device.

## 8. Expected recovery behavior

### The Mac locks or the app window closes

The detached worker and temporary `caffeinate` lease remain active. Reopen the
dashboard whenever you want to monitor or control the task.

### The iPad locks

The task waits safely. Unlock the iPad and return Pokémon GO to a recognizable
screen. The worker must obtain fresh evidence before continuing.

### MCP disconnects

The background runner waits with bounded backoff and updates the dashboard. It
does not repeatedly relaunch the game and does not replay a write whose result
is unknown. Once MCP is healthy, it rereads the current screen.

### The screenshot is black, unchanged, or stale

The assistant treats this as a capture-channel problem, not proof that Pokémon
GO closed. It performs bounded read-only recovery and waits when safety cannot be
re-established.

### A tap or swipe appears to be ignored

The worker verifies whether the page actually changed. It retries only after it
proves that the same safe page remains visible. Unknown geometry or an unexpected
screen causes a safe stop or wait instead of a blind click.

### A Pokémon is skipped

Check the visible reason in the dashboard or the Activity page. Common safe
reasons include:

- the Pokémon already has a custom nickname;
- the complete species/form name cannot be verified;
- the appraisal bars are not stable enough to measure;
- a special icon or form marker cannot be separated from the name reliably;
- the MCP field value cannot be read exactly before editing.

The app intentionally prefers a skipped rename to a guessed or destructive edit.

## 9. Local files and privacy

Operational data lives in:

- `.pogo-data/batch-state.json` — current worker and counters;
- `.pogo-data/live-activity.json` — structured dashboard state;
- `.pogo-data/background-worker.log` — detached worker history;
- `.pogo-data/gui-settings.json` — saved MCP URL and batch scope;
- `.pogo-journal/actions.jsonl` — safety audit journal.

These paths, captured frames, `.env`, and `.venv` are ignored by Git. Do not add
diagnostic screenshots or local MCP addresses to a commit.

## 10. Verify a development checkout

Run the complete regression suite:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -s tests
```

You can also verify the production command without controlling an iPad:

```bash
.venv/bin/python -m pogo_iphone_renamer.headless_batch_launcher --help
```

For implementation-specific repair history and known device findings, see
[Reliability Notes](reliability.md).
