# blender-auto-ime

**Keep Blender keyboard shortcuts working on macOS while a CJK input method (Korean / Japanese / Chinese) is on.**

On macOS, Blender's single-key shortcuts (`G`, `R`, `S`, `X`, …) stop working while a CJK IME is in its native composing mode — the IME swallows the bare keys before they ever reach Blender. This tool fixes that by **automatically switching your input source to English whenever Blender is the active app, and restoring your previous input source when you leave.**

It is not Korean-specific: it simply remembers and restores whatever input method you were using.

> Platform: **macOS**. Verified on Blender 5.x / macOS 26 with the Korean 2-Set IME — but works with any IME, and (optionally) any app.

---

## Quick start — menu bar app (recommended)

1. Download `KO-Focus-Switch-macos.zip` from the **[latest release](https://github.com/Zimins/blender-auto-ime/releases/latest)**.
2. Unzip, then **right-click → Open** `KO Focus Switch.app`. (The build is unsigned, so the first launch needs right-click → Open to get past Gatekeeper. After that, double-click works.)
3. A **⌨︎** icon appears in the menu bar. Its menu:
   - top line shows live status (front app · current input source);
   - **On/Off** (`활성화`) — enable / disable switching;
   - **Start at login** (`로그인 시 자동 실행`) — keep it running across reboots;
   - **Quit** (`종료`).

Done. From now on:

| When | What happens |
|---|---|
| You focus **Blender** | Switches to **English** (so `G/R/S/X` work), remembering your current IME |
| You leave **Blender** | Restores the IME you had before (Korean → Korean, Japanese → Japanese, …) |
| You switch to an IME **inside** Blender (e.g. to type 3D text) | Left untouched — type freely |

---

## Command line (no install, zero dependencies)

The same engine is a single self-contained script, `ko_focus_switch.py` (Python 3.9+, macOS only, standard library + `ctypes`).

```sh
# Run in the foreground and watch the logs (Ctrl+C to stop)
python3 ko_focus_switch.py run

# Install as a background LaunchAgent that starts at login (remove with: uninstall)
python3 ko_focus_switch.py install
#   logs:  tail -f ~/Library/Logs/dev.blender-ko.focus-switch.log

# Helpers
python3 ko_focus_switch.py current     # current input-source ID
python3 ko_focus_switch.py list        # all installed input-source IDs
python3 ko_focus_switch.py watch       # print the frontmost app's bundle ID
python3 ko_focus_switch.py selftest    # verify IME switching works on your Mac (auto-reverts)
```

> Use the menu bar app **or** the CLI LaunchAgent — not both at once (they would both try to switch).

---

## Configuration

Options apply to `run` and `install`:

| Option | Default | Purpose |
|---|---|---|
| `--english <id>` | `com.apple.keylayout.ABC` | The shortcut-friendly source forced while in the target app |
| `--blender-bundle <id>` | `org.blender.blender`, `org.blenderfoundation.blender` | Target app(s). Repeatable — point it at **any** app |
| `--interval <sec>` | `0.3` | Foreground-app poll interval |
| `--settle <sec>` | `0.15` | Run-loop pump after an IME switch (keep ≥ 0.15 on macOS 26) |
| `--retries <n>` | `4` | Bounce retries if an IME switch doesn't take effect |
| `--backend <name>` | `AUTO` | `AUTO` / `CTYPES` / `MACISM` |
| `--macism <path>` | auto-detect | Path to the optional `macism` CLI |

Find input-source IDs with `list` / `current`. Find an app's bundle ID with `watch` (focus the app, read the printed `bundle=`).

**Any IME, any app.** Because it remembers and restores your previous source, it works with Korean, Japanese, Chinese, Vietnamese, … Point `--blender-bundle` at a different app to turn it into a general "force English while app X is focused" switcher.

---

## Build the `.app` yourself

```sh
bash packaging/build_macos.sh          # → dist/KO Focus Switch.app  +  a distributable .zip
```

Or let CI build it — push a tag and GitHub Actions produces the `.app` and attaches it to a release:

```sh
git tag v0.2.2 && git push --tags
```

For warning-free distribution, add Apple code-signing + notarization to the workflow (Apple Developer account required); the current builds are unsigned (right-click → Open on first launch).

For Python users: `pipx install .`, then `ko-focus-switch run` (CLI) or `ko-focus-switch-menubar` (menu bar). The core is dependency-free; only the menu bar GUI uses `rumps`.

---

## How it works

- **Frontmost app** — `NSWorkspace.frontmostApplication`, called through the Obj-C runtime with `ctypes` (no PyObjC needed for the CLI).
- **Input source** — Carbon `TISSelectInputSource`. Switching *into* a CJK IME is unreliable on recent macOS: it reports success but doesn't actually take effect until the app is refocused. The fix is to pump the run loop briefly (`CFRunLoopRunInMode`, ~150 ms) right after switching, which commits the change **with no external tool**. If your Mac still misses it, install [`macism`](https://github.com/laishulu/macism) — `--backend AUTO` uses it automatically when present.
- **Why English fixes shortcuts** — macOS Blender derives `event.type` from the physical keycode, so a shortcut fires as long as the keystroke reaches Blender. A CJK IME intercepts bare letter keys for composition, so they never arrive; keeping the input source in English (ASCII) is the only reliable fix.

---

## Alternative: Blender add-on

If you'd rather not run a background app, two Blender add-ons are included:

- **`blender_ko_switch.py`** — switches the input source from inside Blender: English on file load and when leaving 3D-text edit, plus `Cmd+Option+E` (force English) / `Cmd+Option+K` (toggle) hotkeys that bypass the IME.
- **`blender_ko_diag.py`** — a diagnostic add-on that logs the key events Blender actually receives (useful for confirming the behavior on your machine).

Install via *Edit > Preferences > Add-ons > Install from Disk…*. The menu bar app is recommended over the add-on, because Blender's Python API can't reliably tell when a text field is focused.

---

## References

- `macism` — reliable input-source switching and the CJK race condition: <https://github.com/laishulu/macism>
- Blender upstream issues (macOS IME swallows shortcut keys): [#93421](https://projects.blender.org/blender/blender/issues/93421), [#130662](https://projects.blender.org/blender/blender/issues/130662)
- Cmd/Ctrl combinations bypass the IME: Blender commits `8b44b756d85` / `7336af325937`

License: MIT.
