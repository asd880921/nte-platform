import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from html.parser import HTMLParser
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_PACKAGES = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES):
    sys.path[:0] = [
        SITE_PACKAGES,
        os.path.join(SITE_PACKAGES, "win32"),
        os.path.join(SITE_PACKAGES, "win32", "lib"),
        os.path.join(SITE_PACKAGES, "Pythonwin"),
    ]
    dll_dir = os.path.join(SITE_PACKAGES, "pywin32_system32")
    if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)
MODULE_PATH = os.path.join(ROOT, "launcher", "app.py")
sys.modules.setdefault("webview", types.SimpleNamespace())
SPEC = importlib.util.spec_from_file_location("launcher_app", MODULE_PATH)
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.add(element_id)


class HotkeySettingsTests(unittest.TestCase):
    def test_normalize_accepts_f1_through_f12_case_insensitively(self):
        self.assertEqual(
            APP.normalize_hotkey_settings("f12", " F3 "),
            {"start_key": "F12", "stop_key": "F3"},
        )

    def test_normalize_rejects_invalid_or_duplicate_keys(self):
        with self.assertRaisesRegex(ValueError, "F1 到 F12"):
            APP.normalize_hotkey_settings("A", "F2")
        with self.assertRaisesRegex(ValueError, "不能相同"):
            APP.normalize_hotkey_settings("F5", "F5")

    def test_settings_round_trip_and_invalid_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "settings.json")
            saved = APP.save_hotkey_settings("F8", "F9", path)
            self.assertEqual(saved, {"start_key": "F8", "stop_key": "F9"})
            self.assertEqual(APP.load_hotkey_settings(path), saved)

            with open(path, "w", encoding="utf-8") as f:
                json.dump({"start_key": "F4", "stop_key": "F4"}, f)
            self.assertEqual(
                APP.load_hotkey_settings(path),
                APP.DEFAULT_HOTKEY_SETTINGS,
            )

    def test_meta_controls_use_saved_hotkeys(self):
        meta = {
            "controls": [
                {"action": "start", "key": "F1", "label": "開始循環"},
                {"action": "stop", "key": "F2", "label": "停止"},
            ]
        }
        rendered = APP.meta_with_hotkeys(
            meta,
            {"start_key": "F7", "stop_key": "F11"},
        )
        self.assertEqual(
            [control["key"] for control in rendered["controls"]],
            ["F7", "F11"],
        )
        self.assertEqual(meta["controls"][0]["key"], "F1")

    def test_runner_passes_hotkeys_to_script_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = os.path.join(folder, "main.py")
            with open(entry, "w", encoding="utf-8") as f:
                f.write("")
            runner = APP.ScriptRunner(
                {"id": "test", "entry": "main.py", "modes": ["foreground"]},
                folder,
            )
            process = mock.Mock()
            process.stdout = []
            popen = mock.Mock(return_value=process)
            thread = mock.Mock()
            with (
                mock.patch.object(APP.subprocess, "Popen", popen),
                mock.patch.object(APP.threading, "Thread", return_value=thread),
            ):
                ok, _ = runner.start(
                    "foreground",
                    {"start_key": "F6", "stop_key": "F10"},
                )

            self.assertTrue(ok)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["NTE_START_KEY"], "F6")
            self.assertEqual(env["NTE_STOP_KEY"], "F10")

    def test_api_rejects_changes_while_a_script_is_running(self):
        api = object.__new__(APP.Api)
        api.hotkeys = dict(APP.DEFAULT_HOTKEY_SETTINGS)
        api.runners = {"test": types.SimpleNamespace(running=True)}
        result = api.save_hotkey_settings("F3", "F4")
        self.assertFalse(result["ok"])
        self.assertIn("停止", result["message"])

    def test_api_saves_changes_when_all_scripts_are_idle(self):
        api = object.__new__(APP.Api)
        api.hotkeys = dict(APP.DEFAULT_HOTKEY_SETTINGS)
        api.runners = {"test": types.SimpleNamespace(running=False)}
        saved = {"start_key": "F4", "stop_key": "F9"}
        with mock.patch.object(
            APP,
            "save_hotkey_settings",
            return_value=saved,
        ) as save:
            result = api.save_hotkey_settings("F4", "F9")

        self.assertEqual(result, {"ok": True, **saved})
        self.assertEqual(api.hotkeys, saved)
        save.assert_called_once_with("F4", "F9")

    def test_all_scripts_read_configured_function_keys(self):
        script_ids = ("manager_picks", "fishing", "999_nights")
        for script_id in script_ids:
            path = os.path.join(ROOT, "scripts", script_id, "main.py")
            spec = importlib.util.spec_from_file_location(
                f"hotkey_script_{script_id}",
                path,
            )
            module = importlib.util.module_from_spec(spec)
            with mock.patch.dict(
                os.environ,
                {"NTE_START_KEY": "f12", "NTE_STOP_KEY": "F7"},
            ):
                spec.loader.exec_module(module)
            with self.subTest(script=script_id):
                self.assertEqual(module.START_KEY, "F12")
                self.assertEqual(module.STOP_KEY, "F7")
                self.assertEqual(module.VK_START, 0x7B)
                self.assertEqual(module.VK_STOP, 0x76)

    def test_frontend_wires_settings_dialog_to_hotkey_api(self):
        html_path = os.path.join(ROOT, "launcher", "web", "index.html")
        js_path = os.path.join(ROOT, "launcher", "web", "app.js")
        collector = IdCollector()
        with open(html_path, encoding="utf-8") as f:
            collector.feed(f.read())
        self.assertTrue(
            {
                "settingsBtn",
                "settingsOverlay",
                "settingsForm",
                "functionKeyGrid",
                "settingsSave",
            }.issubset(collector.ids)
        )
        with open(js_path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("get_hotkey_settings()", source)
        self.assertIn("save_hotkey_settings(", source)


if __name__ == "__main__":
    unittest.main()
