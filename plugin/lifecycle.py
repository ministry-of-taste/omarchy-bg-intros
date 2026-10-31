"""Conservative, transactional per-user installation. No package-owned writes."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from bg_intros import CONFIG, HOME, ID, RUNTIME, STATE, backend

TARGET = CONFIG / "omarchy/plugins" / ID
COMMAND = HOME / ".local/bin/omarchy-bg-intros"
SHELL = CONFIG / "omarchy/shell.json"
MENU = CONFIG / "omarchy/extensions/omarchy-menu.jsonc"
RECEIPT = TARGET / "receipt.json"
BEGIN = "// omarchy-bg-intros:start"
END = "// omarchy-bg-intros:end"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError(f"Refusing symlink-managed install path: {part}")
        if part == HOME:
            break


def read(path):
    safe(path)
    return path.read_bytes() if path.exists() else None


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".bg-intros-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked():
    directory = RUNTIME
    safe(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def commit(changes):
    """Preflight every path, roll back file contents on any write failure."""
    original = {}
    created_directories = set()
    for path in changes:
        for parent in path.parents:
            if parent.exists():
                break
            created_directories.add(parent)
        data = read(path)
        original[path] = (data, path.stat().st_mode & 0o777 if data is not None else 0o644)
    written = []
    try:
        for path, (data, mode) in changes.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, data, mode)
            written.append(path)
    except BaseException:
        for path in reversed(written):
            data, mode = original[path]
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, data, mode)
        for directory in sorted(created_directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def receipt():
    raw = read(RECEIPT)
    if raw is None:
        if TARGET.exists() or COMMAND.exists():
            raise RuntimeError("Install destination already exists without our receipt; nothing overwritten.")
        return None
    value = json.loads(raw)
    if value.get("schema") != 1:
        raise RuntimeError("Unknown installation receipt schema.")
    for name, checksum in value["files"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise RuntimeError("Invalid installation receipt path.")
        data = read(TARGET / name)
        if data is None or digest(data) != checksum:
            raise RuntimeError(f"Locally modified or missing installed file: {name}; restore it before continuing.")
    known = set(value["files"]) | {"receipt.json"}
    known.update(str(parent) for name in value["files"] for parent in Path(name).parents if str(parent) != ".")
    if any(str(p.relative_to(TARGET)) not in known or p.is_symlink() for p in TARGET.rglob("*")):
        raise RuntimeError("Untracked files in plugin directory; move them out before updating/removing.")
    if read(COMMAND) != base64.b64decode(value["command"]):
        raise RuntimeError("Installed launcher was modified; nothing overwritten.")
    return value


def shell_value(raw):
    value = json.loads(raw) if raw is not None else {}
    if not isinstance(value, dict) or not isinstance(value.get("plugins", []), list):
        raise RuntimeError("shell.json must be an object with a plugins array.")
    return value


def is_ours(entry):
    return entry == ID or isinstance(entry, dict) and entry.get("id") == ID


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def pack(data):
    return None if data is None else base64.b64encode(data).decode()


def unpack(data):
    return None if data is None else base64.b64decode(data)


def menu_block(command):
    # Quote an absolute path; never depend on the graphical session's PATH.
    import shlex
    cmd = shlex.quote(str(command))
    rows = {
        "appearance.intros": {"icon": "󰕧", "label": "Background Intros",
                              "keywords": "wallpaper video intro animation"},
        "appearance.intros.on-theme": {"icon": "󰸌", "label": "Intros Enabled",
            "description": "Master switch for login, theme and background intros",
            "checked": cmd + " master status", "action": cmd + " master toggle"},
        "appearance.intros.on-select": {"icon": "󰋩", "label": "On Background Change",
            "description": "Play a matching intro when selecting a wallpaper",
            "when": "! " + cmd + " is-native",
            "checked": cmd + " is-enabled", "action": cmd + " toggle"},
    }
    return BEGIN + "\n" + "".join("  " + json.dumps(key) + ": " + json.dumps(row, ensure_ascii=False) + ",\n"
                                        for key, row in rows.items()) + END + "\n"


def install(source):
    with locked():
        old = receipt()
        for command in ("omarchy-shell", "omarchy-theme-bg-intro", "owe", "hyprctl"):
            if not shutil.which(command):
                raise RuntimeError(f"Missing dependency: {command}")
        if backend() == "unsupported":
            raise RuntimeError("Unsupported Omarchy background API; installation aborted.")
        files = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and p.name != "receipt.json"}
        raw_shell = read(SHELL)
        value = shell_value(raw_shell)
        if not old and any(is_ours(entry) for entry in value.get("plugins", [])):
            raise RuntimeError("Plugin already registered without our receipt.")
        value.setdefault("plugins", [])
        value["plugins"] = [entry for entry in value["plugins"] if not is_ours(entry)] + [{"id": ID}]
        new_shell = encode(value)
        raw_menu = read(MENU)
        text = (raw_menu or b"{\n}\n").decode()
        block = menu_block(COMMAND)
        if old:
            if text.count(old["menu_block"]) != 1:
                raise RuntimeError("Managed menu block changed; restore it before updating.")
            text = text.replace(old["menu_block"], block, 1)
        else:
            if BEGIN in text or END in text or '"appearance.intros' in text:
                raise RuntimeError("Menu IDs/markers already exist; nothing overwritten.")
            # JSONC starts with optional comments/whitespace and an object.
            import re
            opening = re.match(r"(?:\s|//[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*\{", text)
            if not opening:
                raise RuntimeError("Menu extension must be a JSONC object.")
            pos = opening.end()
            text = text[:pos] + "\n" + block + text[pos:]
        launcher = ("#!/bin/sh\nexec python3 " + __import__("shlex").quote(str(TARGET / "bin/omarchy-bg-intros")) + ' "$@"\n').encode()
        new = {"schema": 1, "files": {name: digest(data) for name, data in files.items()},
               "command": pack(launcher), "menu_block": block,
               "shell_before": old["shell_before"] if old else pack(raw_shell),
               "menu_before": old["menu_before"] if old else pack(raw_menu),
               "shell_after": digest(new_shell), "menu_after": digest(text.encode())}
        # Preserve the first baseline across reinstalls when nothing else changed.
        if old and digest(raw_shell or b"") != old["shell_after"]:
            clean = shell_value(raw_shell)
            clean["plugins"] = [e for e in clean.get("plugins", []) if not is_ours(e)]
            new["shell_before"] = pack(encode(clean))
        if old and digest(raw_menu or b"") != old["menu_after"]:
            new["menu_before"] = pack((raw_menu.decode().replace(old["menu_block"], "", 1)).encode())
        changes = {TARGET / name: (data, 0o755 if name.startswith("bin/") else 0o644)
                   for name, data in files.items()}
        if old:
            changes.update({TARGET / name: (None, 0o644) for name in old["files"] if name not in files})
        changes.update({COMMAND: (launcher, 0o755),
                        SHELL: (new_shell, SHELL.stat().st_mode & 0o777 if SHELL.exists() else 0o600),
                        MENU: (text.encode(), MENU.stat().st_mode & 0o777 if MENU.exists() else 0o644),
                        RECEIPT: (encode(new), 0o600)})
        commit(changes)
        print(f"Installed {ID}. Playback setting unchanged. Run: omarchy-bg-intros enable")


def uninstall():
    with locked():
        old = receipt()
        if old is None:
            print("Not installed.")
            return
        raw_shell, raw_menu = read(SHELL), read(MENU)
        if digest(raw_shell or b"") == old["shell_after"]:
            restored_shell = unpack(old["shell_before"])
        else:
            value = shell_value(raw_shell)
            value["plugins"] = [entry for entry in value.get("plugins", []) if not is_ours(entry)]
            restored_shell = encode(value) if raw_shell is not None else None
        if raw_menu is None:
            restored_menu = None
        elif digest(raw_menu) == old["menu_after"]:
            restored_menu = unpack(old["menu_before"])
        else:
            text = raw_menu.decode()
            if text.count(old["menu_block"]) != 1:
                raise RuntimeError("Managed menu block changed; restore it before uninstalling.")
            restored_menu = text.replace(old["menu_block"], "", 1).encode()
        changes = {SHELL: (restored_shell, SHELL.stat().st_mode & 0o777 if SHELL.exists() else 0o600),
                   MENU: (restored_menu, MENU.stat().st_mode & 0o777 if MENU.exists() else 0o644),
                   COMMAND: (None, 0o755)}
        changes.update({TARGET / name: (None, 0o644) for name in old["files"]})
        changes[RECEIPT] = (None, 0o600)
        commit(changes)
        for directory in sorted((p for p in TARGET.rglob("*") if p.is_dir()), reverse=True):
            directory.rmdir()
        TARGET.rmdir()
        # Leave the watch lock inode in place until the hot-reloaded service exits;
        # unlinking a held lock can permit a second watcher. No native setting,
        # videos, login marker or other Omarchy state is ever deleted here.
        (STATE / ID / "enabled").unlink(missing_ok=True)
        try:
            (STATE / ID).rmdir()
        except (FileNotFoundError, OSError):
            pass
        print("Uninstalled. Native Omarchy settings and your media were left untouched.")
