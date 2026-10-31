"""Background-selection intros without replacing an Omarchy command."""
import ctypes
import fcntl
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import time

ID = "oldjobobo.bg-intros"
HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state"))
# Omarchy itself currently uses these HOME-relative paths, even with XDG set.
CURRENT = HOME / ".local/state/omarchy/current"
MASTER_OFF = HOME / ".local/state/omarchy/toggles/background-intros-off"
ENABLED = STATE / ID / "enabled"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", STATE)) / ID
VIDEOS = ("mp4", "m4v", "mov", "webm", "mkv", "avi")


def run(*args, timeout=2):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def backend():
    """Re-detect on every event so an Omarchy upgrade needs no reinstallation."""
    path = shutil.which("omarchy-theme-bg-set")
    try:
        source = Path(path).read_text() if path else ""
    except OSError:
        source = ""
    if "--resolve-enabled" in source and "switchBackground" in source:
        return "native"
    if "--on-select" in source and "background-intros-on-select" in source:
        return "native-select"
    # Only the known legacy flow is safe to supplement. Unknown flows fail closed.
    if "omarchy-shell -q background set" in source and "ln -nsf" in source:
        return "watcher"
    return "unsupported"


def enabled(mode=None):
    mode = mode or backend()
    if mode in ("native", "native-select"):
        args = ["omarchy-theme-bg-intro"]
        args += ["select", "status"] if mode == "native-select" else ["status"]
        result = run(*args)
        return bool(result and result.returncode == 0)
    return mode == "watcher" and ENABLED.is_file()


def configure(action):
    mode = backend()
    if mode == "unsupported":
        raise RuntimeError("Unsupported Omarchy background API; no playback changes were made.")
    if mode in ("native", "native-select"):
        args = ["omarchy-theme-bg-intro"]
        if mode == "native-select":
            args.append("select")
        result = run(*args, action)
        if not result or result.returncode:
            raise RuntimeError("Omarchy could not change the native intro setting.")
        return
    turn_on = not enabled(mode) if action == "toggle" else action == "enable"
    if turn_on:
        ENABLED.parent.mkdir(parents=True, exist_ok=True)
        ENABLED.touch()
    else:
        ENABLED.unlink(missing_ok=True)


def snapshot():
    try:
        link = CURRENT / "background"
        st = link.lstat()
        background = link.resolve(strict=True)
        theme = CURRENT / "theme.name"
        ts = theme.stat()
        # The generation changes even on a same-theme refresh.
        generation = (ts.st_ino, ts.st_mtime_ns, theme.read_bytes())
        return (st.st_ino, st.st_mtime_ns, background, generation)
    except OSError:
        return None


def resolve_intro(background):
    if background.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        return None
    for extension in VIDEOS:
        intro = background.parent / "intros" / (background.stem + "." + extension)
        if intro.is_file():
            return intro
    return None


def gates():
    if backend() != "watcher" or not enabled("watcher") or MASTER_OFF.exists():
        return False
    result = run("hyprctl", "-j", "getoption", "animations:enabled")
    try:
        return bool(result and result.returncode == 0 and
                    json.loads(result.stdout).get("bool") is True)
    except (ValueError, AttributeError):
        return False


def play(expected):
    if not expected or snapshot() != expected or not gates():
        return None
    intro = resolve_intro(expected[2])
    if not intro:
        return None
    # Theme switches hold this lock across their own intro. Never queue behind it.
    lockpath = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omarchy-theme-set.lock"
    try:
        with lockpath.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = run("owe", "status", timeout=1)
            if not status or status.returncode or snapshot() != expected or not gates():
                return None
            # OWE owns superseding an active clip and restoring the selected still.
            return subprocess.Popen(["owe", "intro", "--start", "first-frame", str(intro)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except (OSError, BlockingIOError):
        return None


def watch():
    """Watch the *directory*: replacing a symlink invalidates a watch on its target."""
    if not CURRENT.is_dir():
        raise RuntimeError("Omarchy current-state directory is missing; log into Omarchy first.")
    # Exactly one listener even during plugin hot-reload. Never play at startup.
    lockdir = RUNTIME
    lockdir.mkdir(parents=True, exist_ok=True)
    with (lockdir / "watch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1")
        children = []
        try:
            # create, delete, moved-from/to, close-write, attributes
            mask = 0x100 | 0x200 | 0x40 | 0x80 | 0x8 | 0x4
            if libc.inotify_add_watch(fd, os.fsencode(CURRENT), mask) < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch")
            previous = snapshot()
            while True:
                select.select([fd], [], [])
                os.read(fd, 65536)
                # Coalesce link replacement and theme.name writes, then take only
                # the newest selection. This is event-driven, not idle polling.
                time.sleep(0.12)
                while select.select([fd], [], [], 0)[0]:
                    os.read(fd, 65536)
                current = snapshot()
                if current and previous and current[:2] != previous[:2] and current[3] == previous[3]:
                    child = play(current)
                    if child:
                        children.append(child)
                previous = current
                children = [child for child in children if child.poll() is None]
        finally:
            os.close(fd)


def main(argv):
    action = argv[0] if argv else "status"
    if action == "status":
        mode = backend()
        print(f"{'enabled' if enabled(mode) else 'disabled'} ({mode})")
        if mode == "native":
            print("Native Omarchy plays selection intros; its master switch also controls login/theme intros.")
        return 0 if enabled(mode) else 1
    if action == "is-enabled":
        return 0 if enabled() else 1
    if action == "is-native":
        return 0 if backend() == "native" else 1
    if action == "master":
        choice = argv[1] if len(argv) == 2 else ""
        if choice not in ("status", "enable", "disable", "toggle"):
            raise RuntimeError("Usage: omarchy-bg-intros master <status|enable|disable|toggle>")
        result = run("omarchy-theme-bg-intro", choice)
        if not result:
            raise RuntimeError("Native Omarchy intro command unavailable.")
        return result.returncode
    if action in ("enable", "disable", "toggle"):
        configure(action)
        return 0
    if action == "watch":
        watch()
        return 0
    if action == "uninstall":
        from lifecycle import uninstall
        uninstall()
        return 0
    if action in ("help", "--help", "-h"):
        print("Usage: omarchy-bg-intros [status|enable|disable|toggle|uninstall]\n"
              "       omarchy-bg-intros master <status|enable|disable|toggle>")
        return 0
    raise RuntimeError("Unknown command. Run omarchy-bg-intros --help.")
