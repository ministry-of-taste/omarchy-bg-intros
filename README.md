# Omarchy Background Intros

Play a wallpaper's matching intro when selecting it—not just at login or on a theme change. A small, service-only Omarchy plugin with reversible per-user installation and an **Appearance → Background Intros** menu.

**Behavior only. No videos or wallpapers are included.** OWE remains responsible for playback, superseding a playing clip, muting and handing back to the still image.

## Requirements

- Linux with Omarchy 4's Quickshell plugin system, JSONC menu extensions and OWE intro support.
- Python 3.10+, `omarchy-shell`, `omarchy-theme-bg-set`, `omarchy-theme-bg-intro`, `owe` and `hyprctl` on PATH.
- A supported background command (see Compatibility below).

No sudo, pip packages, background network requests or systemd services.

## Install

From a downloaded or cloned checkout:

```sh
python3 install.py
omarchy-bg-intros enable
```

The installer registers the plugin in your user `shell.json`, adds a marked menu extension and installs `~/.local/bin/omarchy-bg-intros`. Keep that directory on your shell's PATH. The menu uses an absolute path and does not depend on PATH containing it. Plugin code is **copied**, so moving or deleting the checkout does not break the installation.

Installation preserves the existing playback setting. On legacy builds the new selection behavior is off until enabled. The shell normally hot-reloads the plugin and menu; if needed, run:

```sh
omarchy-shell shell rescanPlugins
```

**Appearance → Background Intros** contains:

- **Intros Enabled**: Omarchy's existing master switch, including login and theme intros.
- **On Background Change**: the independent selection opt-in, where supported. The master switch and enabled animations are still required.

Newer native Omarchy builds already play intros on wallpaper selection. On those builds only the master menu item is shown, because there is no independent selection setting.

## Commands

```sh
omarchy-bg-intros status
omarchy-bg-intros enable
omarchy-bg-intros disable
omarchy-bg-intros toggle
omarchy-bg-intros master status
omarchy-bg-intros master toggle
```

`status` prints the effective backend and enabled state; it exits 0 when enabled, 1 when disabled. Errors exit 2. A master-off or animations-off setting can still suppress an enabled selection opt-in.

**Native builds:** `enable`, `disable` and `toggle` use Omarchy's native control. Where Omarchy only has one master setting, these also affect login/theme intros. The add-on never pretends an independent native setting exists.

## Your media

Use the existing Omarchy filename convention, for example:

```text
backgrounds/
├── forest.webp
└── intros/
    └── forest.mp4
```

The image and clip must have the same stem. The intro is resolved beside the selected image, not from some other theme. Image extensions: JPG, JPEG, PNG, BMP, WebP. Intro extensions, in preference order: MP4, M4V, MOV, WebM, MKV, AVI (lowercase video filenames). Video wallpapers do not get additional intro playback.

## Compatibility and update safety

The backend is detected again on each selection, not frozen at installation:

| Backend | Behavior |
| --- | --- |
| `native-select` | Your original opt-in implementation (`--on-select`). Defer to `omarchy theme bg intro select`; never play a second clip. |
| `native` | Newer Omarchy's `--resolve-enabled` / `switchBackground` flow. Defer entirely to native playback and the native master switch. |
| `watcher` | The earlier `ln -nsf` + `omarchy-shell -q background set` flow. Observe link replacements with inotify and play the matching clip after selection. |
| `unsupported` | Unknown background implementation. No extra playback; controls fail with an explicit error. |

The watcher initializes silently and ignores theme-generation changes. It never waits behind a theme-switch lock, never touches the once-per-login marker, rechecks the current selection after contacting OWE, and respects the master intro switch and Hyprland animations. Rapid selections are coalesced for 120 ms. Reselecting the same image is recognized when Omarchy replaces its background symlink. Ordinary file-content edits do not trigger playback.

There are **no patches, PATH shadows of Omarchy commands, package-owned file edits, copied shell renderers, or post-update patch reapplication hooks**. Updating Omarchy cannot overwrite this plugin's files. A newly detected native implementation takes over automatically; its own setting becomes authoritative. Unknown future APIs fail closed rather than risking duplicate playback. This protects package updates; it does not promise compatibility with every future Omarchy release.

The plugin is service-only: disabling it through Omarchy stops the legacy watcher. Native playback and menu controls remain native behavior. Uninstall to remove the menu integration as well.

## Upgrade the add-on

Update/download this checkout and run `python3 install.py` again. This replaces only verified add-on-owned files and preserves unrelated configuration and preferences. It does not change the playback setting.

## Uninstall

```sh
omarchy-bg-intros uninstall
```

Or, from the checkout:

```sh
python3 install.py uninstall
```

Removal deletes the installed plugin, launcher, its legacy opt-in preference and only its marked menu block/registration. It restores the original shared files byte-for-byte if nobody has edited them since installation. Otherwise it removes its own entries while retaining other menu content and shell settings. Shared directories are not removed. Small advisory lock files remain in the session runtime directory until logout; they prevent races during hot-reload and concurrent install/remove.

**Your media, native Omarchy toggles, current background and login marker are never removed or restored to stale values.** If you enabled a native setting, it remains enabled after uninstall. This is intentional: uninstalling the add-on does not undo native choices you made.

Installation/removal aborts before making changes if an owned file or managed menu block was modified, an install path is symlink-managed, or a destination is occupied by something else. Move your local edits aside or restore the affected installed file before retrying. Symlink-managed dotfile installations need manual integration; the installer intentionally will not modify their backing files. Shared `shell.json` must be valid JSON; menu extensions may use JSONC. Existing user overrides under `appearance.intros` are a conflict and are not overwritten.

## Development and verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
omarchy-plugin-validate plugin
qmllint plugin/Service.qml
```

Tests use isolated homes beneath `tests/`, real inotify events and stubbed compositor/player commands. They cover install/reinstall/removal, restoring existing and absent shared files, preserving independent edits, refusing conflicts and symlinks, transactional rollback, playback gates, stale selections, theme locks, native takeover and no startup/theme double playback.

Before publishing a release, also verify in an actual Omarchy session: install, select a still with an intro from the picker and `bg next`, toggle the menu items, disable animations, switch themes, reload/disable the plugin and uninstall. Automated tests do not establish rendered desktop acceptance.

## Origin and license

Extracted from OldJobobo's Omarchy background-intro-on-select change, commit `e5fa7abeda2742c22a349f0fb483c7830fecb98e`. The independent integration avoids redistributing patched Omarchy commands. MIT licensed; the original Omarchy copyright notice is retained in `LICENSE`.
