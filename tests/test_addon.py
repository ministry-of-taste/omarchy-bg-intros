import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
import bg_intros as bg
import lifecycle as life


class AddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests", prefix=".sandbox-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home with spaces"
        self.commands = Path(self.temp.name) / "commands"
        self.commands.mkdir()
        self.home.mkdir()
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "XDG_RUNTIME_DIR": str(self.home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(self.commands) + ":/usr/bin:/bin",
            "TEST_LOG": str(self.home / "calls")})
        self.env.start()
        self.addCleanup(self.env.stop)
        importlib.reload(bg)
        importlib.reload(life)
        self.stub("omarchy-shell", "exit 0")
        self.stub("omarchy-theme-bg-intro", 'printf "%s\\n" "$*" >> "$TEST_LOG"')
        self.stub("hyprctl", "echo '{\"bool\":true}'")
        self.stub("owe", 'printf "%s\\n" "$*" >> "$TEST_LOG"')
        self.legacy()
        self.current = bg.CURRENT
        self.current.mkdir(parents=True)
        (self.current / "theme.name").write_text("theme-a\n")
        self.media = self.home / "media"
        (self.media / "intros").mkdir(parents=True)
        for name in ("first", "second"):
            (self.media / f"{name}.png").touch()
            (self.media / "intros" / f"{name}.mp4").touch()
        self.select("first.png")

    def stub(self, name, body):
        path = self.commands / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)
        return path

    def legacy(self):
        self.stub("omarchy-theme-bg-set", '# ln -nsf\n# omarchy-shell -q background set')

    def select(self, name):
        link = self.current / "background"
        temporary = self.current / "next-background"
        temporary.symlink_to(self.media / name)
        temporary.replace(link)

    def install(self):
        life.install(ROOT / "plugin")

    def wait_log(self, text):
        for _ in range(100):
            if (self.home / "calls").exists() and text in (self.home / "calls").read_text():
                return
            time.sleep(0.02)
        self.fail(f"Timed out waiting for {text}")

    def test_clean_install_reinstall_uninstall(self):
        life.SHELL.parent.mkdir(parents=True)
        before_shell = b'{"plugins": [{"id":"other"}], "custom":42}\n'
        life.SHELL.write_bytes(before_shell)
        life.MENU.parent.mkdir(parents=True)
        before_menu = b'{\n // Keep this comment\n "personal": {"label":"Mine"},\n}\n'
        life.MENU.write_bytes(before_menu)
        self.install()
        self.assertFalse(bg.ENABLED.exists())
        self.assertEqual(json.loads(life.SHELL.read_text())["plugins"][-1]["id"], bg.ID)
        first_menu = life.MENU.read_bytes()
        self.install()
        self.assertEqual(first_menu, life.MENU.read_bytes())
        life.uninstall()
        self.assertEqual(before_shell, life.SHELL.read_bytes())
        self.assertEqual(before_menu, life.MENU.read_bytes())
        self.assertFalse(life.TARGET.exists())
        self.assertFalse(life.COMMAND.exists())
        life.uninstall()

    def test_new_shared_files_removed(self):
        self.install()
        life.uninstall()
        self.assertFalse(life.SHELL.exists())
        self.assertFalse(life.MENU.exists())

    def test_keep_user_edits_across_upgrade(self):
        self.install()
        value = json.loads(life.SHELL.read_text())
        value["custom"] = "later edit"
        value["plugins"].append({"id": "other.plugin"})
        life.SHELL.write_text(json.dumps(value))
        life.MENU.write_text(life.MENU.read_text().replace("{\n", '{\n "personal": {"label":"Later"},\n', 1))
        self.install()
        life.uninstall()
        self.assertEqual(json.loads(life.SHELL.read_text())["custom"], "later edit")
        self.assertEqual(json.loads(life.SHELL.read_text())["plugins"], [{"id": "other.plugin"}])
        self.assertIn('"Later"', life.MENU.read_text())
        self.assertNotIn(life.BEGIN, life.MENU.read_text())

    def test_modified_owned_file_refuses_without_partial_writes(self):
        self.install()
        before = life.SHELL.read_bytes()
        (life.TARGET / "Service.qml").write_text("user edit")
        with self.assertRaisesRegex(RuntimeError, "Locally modified"):
            life.uninstall()
        self.assertEqual(before, life.SHELL.read_bytes())
        self.assertTrue(life.COMMAND.exists())

    def test_conflicts_invalid_config_and_symlinks_refused(self):
        life.COMMAND.parent.mkdir(parents=True)
        life.COMMAND.write_text("someone else's command")
        with self.assertRaises(RuntimeError):
            self.install()
        life.COMMAND.unlink()
        life.SHELL.parent.mkdir(parents=True)
        life.SHELL.write_text("invalid json")
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(life.TARGET.exists())
        life.SHELL.unlink()
        life.SHELL.symlink_to(self.home / "elsewhere")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.install()

    def test_modified_menu_block_refuses_removal(self):
        self.install()
        life.MENU.write_text(life.MENU.read_text().replace("On Background Change", "My Edit"))
        with self.assertRaisesRegex(RuntimeError, "menu block changed"):
            life.uninstall()
        self.assertTrue(life.TARGET.exists())

    def test_transaction_rolls_back(self):
        path1, path2 = self.home / "a", self.home / "b"
        path1.write_bytes(b"original")
        real = life.atomic
        def fail(path, data, mode):
            if path == path2:
                raise OSError("simulated write failure")
            real(path, data, mode)
        with patch.object(life, "atomic", side_effect=fail):
            with self.assertRaises(OSError):
                life.commit({path1: (b"changed", 0o600), path2: (b"new", 0o600)})
        self.assertEqual(path1.read_bytes(), b"original")
        self.assertFalse(path2.exists())

    def test_install_failure_leaves_retryable_home(self):
        real = life.atomic
        def fail(path, data, mode):
            if path == life.MENU:
                raise OSError("simulated menu failure")
            real(path, data, mode)
        with patch.object(life, "atomic", side_effect=fail):
            with self.assertRaises(OSError):
                self.install()
        self.assertFalse(life.TARGET.exists())
        self.assertFalse(life.SHELL.exists())
        self.assertFalse(life.COMMAND.exists())
        self.install()
        life.uninstall()

    def test_install_failure_restores_existing_shared_files(self):
        life.SHELL.parent.mkdir(parents=True)
        life.SHELL.write_bytes(b'{"plugins": []}\n')
        life.SHELL.chmod(0o640)
        life.MENU.parent.mkdir(parents=True)
        life.MENU.write_bytes(b'{ /* my menu */ }\n')
        before_shell, before_menu = life.SHELL.read_bytes(), life.MENU.read_bytes()
        real = life.atomic
        def fail(path, data, mode):
            if path == life.MENU:
                raise OSError("simulated existing menu failure")
            real(path, data, mode)
        with patch.object(life, "atomic", side_effect=fail):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(life.SHELL.read_bytes(), before_shell)
        self.assertEqual(life.MENU.read_bytes(), before_menu)
        self.assertEqual(life.SHELL.stat().st_mode & 0o777, 0o640)
        self.assertFalse(life.TARGET.exists())
        self.install()
        life.uninstall()
        self.assertEqual(life.SHELL.stat().st_mode & 0o777, 0o640)

    def test_untracked_empty_directory_is_not_deleted(self):
        self.install()
        (life.TARGET / "my-data").mkdir()
        with self.assertRaisesRegex(RuntimeError, "Untracked"):
            life.uninstall()
        self.assertTrue((life.TARGET / "Service.qml").exists())

    def test_menu_controls_use_quoted_absolute_paths_and_native_master(self):
        import shlex
        block = life.menu_block(life.COMMAND)
        entries = json.loads("{" + block.split("\n", 1)[1].rsplit(life.END, 1)[0].rstrip().rstrip(",") + "}")
        action = entries["appearance.intros.on-select"]["action"]
        self.assertEqual(shlex.split(action), [str(life.COMMAND), "toggle"])
        self.assertEqual(bg.main(["master", "toggle"]), 0)
        self.assertIn("toggle", (self.home / "calls").read_text())
        self.assertEqual(entries["appearance.intros.on-select"]["when"], "! " + shlex.quote(str(life.COMMAND)) + " is-native")

    def test_settings_and_installed_launcher(self):
        self.install()
        self.assertEqual(subprocess.run([str(life.COMMAND), "is-enabled"]).returncode, 1)
        subprocess.run([str(life.COMMAND), "enable"], check=True)
        self.assertTrue(bg.enabled())
        bg.configure("toggle")
        self.assertFalse(bg.enabled())
        bg.configure("enable")
        subprocess.run([str(life.COMMAND), "uninstall"], check=True)
        self.assertFalse(bg.ENABLED.exists())
        self.assertFalse(life.TARGET.exists())

    def test_resolution_and_playback_gates(self):
        bg.configure("enable")
        child = bg.play(bg.snapshot())
        self.assertIsNotNone(child)
        child.wait(timeout=2)
        self.assertIn("intro --start first-frame", (self.home / "calls").read_text())
        self.assertIsNone(bg.resolve_intro(self.media / "first.mp4"))
        self.assertIsNone(bg.resolve_intro(self.media / "missing.png"))
        bg.MASTER_OFF.parent.mkdir(parents=True)
        bg.MASTER_OFF.touch()
        self.assertIsNone(bg.play(bg.snapshot()))
        bg.MASTER_OFF.unlink()
        self.stub("hyprctl", "echo '{\"bool\":false}'")
        self.assertIsNone(bg.play(bg.snapshot()))
        self.stub("hyprctl", "echo 'broken json'")
        self.assertIsNone(bg.play(bg.snapshot()))

    def test_superseded_and_theme_locked_playback_suppressed(self):
        import fcntl
        bg.configure("enable")
        old = bg.snapshot()
        self.select("second.png")
        self.assertIsNone(bg.play(old))
        with (self.home / "omarchy-theme-set.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertIsNone(bg.play(bg.snapshot()))

    def test_native_detection_and_upgrade_defers(self):
        bg.configure("enable")
        self.stub("omarchy-theme-bg-set", '# --on-select background-intros-on-select')
        self.stub("omarchy-theme-bg-intro", 'printf "%s\\n" "$*" >> "$TEST_LOG"')
        self.assertEqual(bg.backend(), "native-select")
        bg.configure("toggle")
        self.assertIn("select toggle", (self.home / "calls").read_text())
        self.assertIsNone(bg.play(bg.snapshot()))
        self.stub("omarchy-theme-bg-set", '# --resolve-enabled switchBackground')
        self.assertEqual(bg.backend(), "native")
        bg.configure("disable")
        self.assertIn("disable", (self.home / "calls").read_text())
        self.assertIsNone(bg.play(bg.snapshot()))
        self.stub("omarchy-theme-bg-set", '# unknown future API')
        self.assertEqual(bg.backend(), "unsupported")
        with self.assertRaises(RuntimeError):
            bg.configure("enable")

    def test_slow_owe_status_rechecks_selection(self):
        bg.configure("enable")
        def change_selection(*args, **kwargs):
            self.select("second.png")
            return subprocess.CompletedProcess(args, 0, "", "")
        real_run = bg.run
        def run(*args, **kwargs):
            return change_selection(*args, **kwargs) if args == ("owe", "status") else real_run(*args, **kwargs)
        with patch.object(bg, "run", side_effect=run):
            self.assertIsNone(bg.play(bg.snapshot()))
        self.assertFalse((self.home / "calls").exists())

    def test_single_watcher_and_rapid_selections(self):
        bg.configure("enable")
        command = [sys.executable, str(ROOT / "plugin/bin/omarchy-bg-intros"), "watch"]
        process = subprocess.Popen(command)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        time.sleep(0.2)
        second = subprocess.run(command, timeout=2)
        self.assertEqual(second.returncode, 0)
        self.select("second.png")
        self.select("first.png")
        self.wait_log("intros/first.mp4")
        time.sleep(0.2)
        self.assertNotIn("intros/second.mp4", (self.home / "calls").read_text())
        process.terminate()
        process.wait(timeout=2)

    def test_event_watcher_no_startup_or_theme_double_play(self):
        bg.configure("enable")
        process = subprocess.Popen([sys.executable, str(ROOT / "plugin/bin/omarchy-bg-intros"), "watch"])
        self.addCleanup(lambda: process.poll() is None and process.kill())
        time.sleep(0.2)
        self.assertFalse((self.home / "calls").exists())
        self.select("second.png")
        self.wait_log("intros/second.mp4")
        time.sleep(0.1)
        count = (self.home / "calls").read_text().count("intro --start")
        (self.current / "theme.name").write_text("theme-b\n")
        self.select("first.png")
        time.sleep(0.4)
        self.assertEqual(count, (self.home / "calls").read_text().count("intro --start"))
        process.terminate()
        process.wait(timeout=2)


if __name__ == "__main__":
    unittest.main()
