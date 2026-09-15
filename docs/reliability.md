# 2026-09-11 reliability repair — live acceptance pending

## Confirmed desktop defects addressed

- Wide name OCR mixed a gender-icon reading (`Q`) and split HP tokens with the
  nickname. The new fallback requires independently positioned whole-title and
  whole-HP evidence, and coordinates for every crop token. It never ignores a
  title-row suffix, even a low-confidence one. Ambiguous Q/HP-only evidence no
  longer authorizes an “already named” skip.
- The deterministic AX field reader previously passed JSON through the model
  prompt formatter's 48,000-character limit. A real ~108 KB response became
  invalid JSON despite containing the full field. Exact parsing now uses the
  untruncated response.
- Stage Manager's visible-only AX query can fail while a complete-tree query
  returns the real editable field. Field reads now explicitly request the full
  tree. These offscreen-inclusive nodes are used for text, not touch targets.
- The runner and native dashboard now count verified renames, not skipped or
  unreadable cards, toward the 50-Pokémon acceptance check.

## Safety and live findings

The iPad is unlocked and Pokémon GO is visible. The default 呱呱泡蛙 with 68/68 HP
was correctly recognized. Complete-field reads succeeded intermittently, but
subsequent live runs still encountered empty AX results. The nested
`axRuntimeMode: inactive` diagnostic is not conclusive by itself: it was present
in a successful response too. Exact current field evidence is required.

Before clearing a name, the reader allows at most three read-only attempts,
with a new read session on retries. A different field value stops immediately.
Failure cancels the unmodified dialog; no unknown value is submitted. The app
shows the actual field-verification wait and returns its display to DETAIL
after cancellation. No game relaunch or plugin installation was performed.

## Verification

The complete Python suite passed 407 tests; the native Swift UI type check and
build passed. Coverage includes positioned metadata, title suffix vetoes,
large AX JSON, exact Unicode, full-tree fresh reads, bounded recovery and real
rename acceptance counts. These tests do **not** substitute for live acceptance.

Live runs starting 2026-09-12T00:32:33Z and 00:39:27Z each stopped on the first
Pokémon without clearing or submitting its name: 0 verified renames. Acceptance
remains pending. Do not restart indefinitely or claim 50 successful renames.
The subsequent diagnostic opened the same proven default-name field without
clearing it: all three new, separate read sessions failed to return its value.
This rules out recovery by simply replacing the current desktop read session.
The diagnostic cancelled its unsubmitted dialog afterward.
Use the current batch state, activity, log and fresh SafeProxy evidence before
resuming. iPad MCP service maintenance requires explicit authority beyond this
repository's Pokémon-only phone-operation scope.

## Authorized MCP maintenance — 2026-09-12T01:23Z

The user explicitly authorized repair. The maintenance console was used only
while the batch worker was stopped, with the same device-run lock and an
unlocked/screen-on precondition for writes. Normal game operations continue to
require SafeProxy.

Two device-side defects were addressed:

- The custom rootless build's `jbroot` compatibility shim returned unchanged
  rootful paths. Helpers actually installed under `/var/jb/usr/bin` were
  reported missing and `run_command` could not find `/bin/sh`. The shim now
  applies the rootless prefix once, preserving already-prefixed paths.
- Compact AX queries serialized repeated remote nodes, requested unused
  attributes, and performed screen-wide hit probes after collecting nodes.
  Queries now deduplicate before expensive serialization, retain exact label
  and value text, filter cross-process leaves, limit redundant frame reads,
  and use a cooperative five-second query budget. Bootstrap recovery stops
  after budget exhaustion instead of queuing more work. This budget cannot
  interrupt an individual blocking private AX call; live field verification
  is still required.

The existing installed HID orientation patch was preserved. The portable DEB
replaces only the server dylib and control version; unchanged helpers were
preserved. USTAR/gzip packaging has no AppleDouble entries or rootful payload.
An independently reread archive passed payload hashes and the dylib passed
ad-hoc signature verification and arm64 architecture checks.

- Package: `.pogo-data/mcp-patches/ios-mcp_1.2.5-stage-manager4-axfix1_iphoneos-arm64.deb`
- SHA-256: `74819c491e964234914e47cb2864f649185397533be33a41b2b4224fb05519cf`
- Source patch: `patches/ios-mcp-stage-manager-ax.patch`, against upstream
  `38cafd5`; includes the preserved HID changes plus the build target and shim.
- Build: `/private/tmp/ios-mcp-source`, using `Makefile.touch-fix`, arm64,
  rootless, minimum iOS 15, with the existing Theos/Xcode toolchain.
- Packaging script: `scripts/package-mcp-ax-repair.py`; preserves the known
  installed `stage-manager3` base package layout. It refuses to overwrite an
  existing artifact. Do not install this package on a different device/scheme.

Filza's terminal confirmed successful `dpkg` unpack/setup. An explicitly
authorized `/var/jb/usr/bin/sbreload` activated it. `/health` now reports
`1.2.5-axfix1`, and `get_device_info(debug=true)` reports all four helpers
executable at their correct `/var/jb/usr/bin` paths.

SpringBoard reload locked the device. One `wake_and_home` was attempted;
the following fresh `describe_screen` showed SpringBoard's actual lock screen
including `已锁定`, and `get_screen_info` reported protected data unavailable.
This is distinct from the earlier incorrect attribution to a lock. The user
has been asked to unlock once. No passcode interaction, game restart, rename,
or extra recovery attempt was made after this observation.

Acceptance is **still 0/50**, not completed. After unlock: verify exact fields
with fresh sessions, cancel the diagnostic without submitting, then resume
the unlimited workflow and require 50 verified renames. Service health and
helper restoration alone do not prove that the intermittent AX fault is fixed.

The full desktop regression suite now passes **408 tests** (`python -m unittest
discover -s tests -q`), including maintenance-state presentation that replaces
the stale Pokémon/IV/error details without incrementing the acceptance count.

## First live result after unlock

After the user unlocked the device, fresh evidence showed Pokémon GO was
already foreground; it was not relaunched. The current default 呱呱泡蛙 was
confirmed from three detail frames. Three separate full-tree AX sessions each
read the exact input value `呱呱泡蛙`, after which the untouched diagnostic
dialog was cancelled and DETAIL was verified.

The unlimited worker then started from that same detail at
2026-09-12T01:44:56Z. It independently measured 13/14/13, generated
`呱呱泡⓭⓮⓭⁸⁹`, verified the original field before clearing, verified the full
target after input, submitted it, and verified the resulting detail name. It
then performed the configured visual left-to-right (→) swipe and confirmed a
different Pokémon. The following already-named card was protected and skipped.
Live acceptance is now **1/50 verified renames**, and the unlimited worker
remains running. Skips do not increment this count.

## Pager direction correction after direct user observation

The user directly observed that the configured visual left-to-right gesture
walked to the previous Pokémon, not the next one. The worker was immediately
stopped at a safe boundary after 27 visited cards (1 verified rename, 26
protected custom-name skips). That run is invalid for forward acceptance.

The root cause was semantic: code treated any verified identity change as
proof of the direction for “next”. Both the previous and next gestures change
identity, so this test cannot establish order. It had persisted `right` and
kept walking backward. Navigation now defines next as the user-verified visual
right-to-left gesture (`left`), varies only the safe artwork lane, and never
probes the opposite direction. Stale `right` state is migrated at startup.
The regression suite remains at 408 passing tests and now includes a stale
opposite-direction value that must still emit only `left` swipes.

## Inline gender-icon identity recovery

The corrected right-to-left run safely stopped at visited card 58 after 6
verified renames and 51 protected custom-name skips. Pokémon GO remained
foreground on an untouched default `火狐狸` (CP 199, 46/46 HP); neither the
game nor the MCP service was stuck. The high-contrast title crop alone read
the male icon at the far-right of the title row as `Q`. Its box slightly
overlapped the independently detected title box, so the old metadata rule
treated the frame as an uncertain custom nickname and eventually exhausted
the bounded same-card recovery.

Gender metadata is now accepted only in the calibrated far-right icon slot
and only when its lower edge extends clearly below the independently located
exact species title. A real `Q` nickname suffix remains a veto because it
stays on the title baseline and ends above the title bottom. The captured
failure frame now resolves to the exact default `火狐狸` in five consecutive
independent analyses; the existing custom-`Q` protection test still passes.
The full desktop suite passes **409 tests**.

Because the former worker exited, its 6 verified renames do not count toward
the user's new uninterrupted 50-card acceptance run. The next worker starts
that acceptance counter at 0 while preserving the unlimited mode.
