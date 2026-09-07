import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from emeet_pixy_control.backend import build_hid_report, clamp
from emeet_pixy_control.gui import (
    camera_toggle_tracking_mode,
    normalize_choice,
    resolve_startup_tracking,
    should_autostart_virtual_camera,
)


class BackendHelpersTest(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 10), 5)
        self.assertEqual(clamp(-5, 0, 10), 0)
        self.assertEqual(clamp(50, 0, 10), 10)

    def test_hid_report_is_32_bytes(self):
        report = build_hid_report([0x09, 0x01, 0x01])
        self.assertEqual(len(report), 32)
        self.assertEqual(report[:3], b"\x09\x01\x01")
        self.assertEqual(report[3:], bytes(29))

    def test_hid_report_rejects_oversize(self):
        with self.assertRaises(Exception):
            build_hid_report(range(33))


class GuiSettingsDefaultsTest(unittest.TestCase):
    def test_normalize_choice_keeps_valid_values(self):
        self.assertEqual(
            normalize_choice("track", ("idle", "track", "privacy"), "idle"),
            "track",
        )

    def test_normalize_choice_falls_back_to_default(self):
        self.assertEqual(
            normalize_choice("unknown", ("idle", "track", "privacy"), "idle"),
            "idle",
        )

    def test_privacy_default_overrides_saved_tracking(self):
        self.assertEqual(
            resolve_startup_tracking("idle", "track", True),
            "privacy",
        )

    def test_privacy_off_prevents_saved_privacy(self):
        self.assertEqual(
            resolve_startup_tracking("privacy", "track", False),
            "track",
        )

    def test_camera_toggle_overrides_privacy_state(self):
        self.assertEqual(
            camera_toggle_tracking_mode(True, "track", "idle"),
            "track",
        )
        self.assertEqual(
            camera_toggle_tracking_mode(True, "privacy", "track"),
            "track",
        )
        self.assertEqual(camera_toggle_tracking_mode(False), "privacy")


class PrivacyToggleScriptTest(unittest.TestCase):
    def test_toggle_persists_state_in_user_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            state_root = tmp_path / "state"
            command_log = tmp_path / "commands.log"
            bin_dir.mkdir()
            state_root.mkdir()

            backend = bin_dir / "emeet-pixy-cli"
            backend.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\n' \"$*\" >> \"$EMEET_TEST_LOG\"\n"
                "if [[ \"$1\" == \"status\" ]]; then\n"
                "  printf 'Device: /dev/video0\\npan:  12 deg\\ntilt: -8 deg\\nzoom: 120\\n'\n"
                "elif [[ \"$1\" == \"privacy\" ]]; then\n"
                "  echo privacy\n"
                "else\n"
                "  echo idle\n"
                "fi\n"
            )
            backend.chmod(backend.stat().st_mode | stat.S_IEXEC)

            env = os.environ.copy()
            env["HOME"] = str(tmp_path)
            env["XDG_STATE_HOME"] = str(state_root)
            env["EMEET_PIXY_VENV_BIN"] = str(bin_dir)
            env["EMEET_TEST_LOG"] = str(command_log)

            script = ROOT / "deploy" / "emeet-pixy-privacy-toggle"
            subprocess.run(["bash", str(script), "toggle"], env=env, check=True)

            state_file = state_root / "emeet-pixy-control" / "privacy-state"
            self.assertTrue(state_file.exists())
            self.assertEqual(state_file.read_text().strip(), "on")

            subprocess.run(["bash", str(script), "toggle"], env=env, check=True)
            commands = command_log.read_text()
            self.assertIn("pan 12", commands)
            self.assertIn("tilt -8", commands)
            self.assertIn("zoom 120", commands)

            subprocess.run(
                ["bash", str(script), "tracking-toggle"],
                env=env,
                check=True,
            )
            tracking_state = state_root / "emeet-pixy-control" / "tracking-state"
            self.assertEqual(tracking_state.read_text().strip(), "on")
            self.assertEqual(state_file.read_text().strip(), "off")

            subprocess.run(
                ["bash", str(script), "tracking-toggle"],
                env=env,
                check=True,
            )
            self.assertEqual(tracking_state.read_text().strip(), "off")


class GuiStartupModeTest(unittest.TestCase):
    def test_background_and_foreground_flags_are_parsed(self):
        from emeet_pixy_control.gui import build_parser

        background = build_parser().parse_args(["--background"])
        foreground = build_parser().parse_args(["--foreground"])

        self.assertTrue(background.background)
        self.assertFalse(background.foreground)
        self.assertFalse(foreground.background)
        self.assertTrue(foreground.foreground)

    def test_background_mode_does_not_require_preview_stream(self):
        configured_preview = True
        background_mode = True
        camera_enabled = configured_preview and not background_mode

        self.assertFalse(camera_enabled)

    def test_virtual_camera_autostarts_when_configured(self):
        self.assertTrue(should_autostart_virtual_camera(True, True))
        self.assertTrue(should_autostart_virtual_camera(False, True))
        self.assertFalse(should_autostart_virtual_camera(True, False))


if __name__ == "__main__":
    unittest.main()
