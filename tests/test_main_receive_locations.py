import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "py_modules"))


class _Logger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class MainReceiveLocationTests(unittest.TestCase):
    def make_plugin(self, temporary: str, settings: dict):
        settings_dir = Path(temporary) / "settings"
        runtime_dir = Path(temporary) / "runtime"
        plugin_dir = Path(temporary) / "plugin"
        log_dir = Path(temporary) / "logs"
        settings_dir.mkdir()
        (settings_dir / "plugin-settings.json").write_text(json.dumps(settings), encoding="utf-8")
        decky_stub = types.ModuleType("decky")
        decky_stub.DECKY_PLUGIN_SETTINGS_DIR = str(settings_dir)
        decky_stub.DECKY_PLUGIN_RUNTIME_DIR = str(runtime_dir)
        decky_stub.DECKY_PLUGIN_DIR = str(plugin_dir)
        decky_stub.DECKY_PLUGIN_LOG_DIR = str(log_dir)
        decky_stub.logger = _Logger()
        decky_stub.emit = lambda *_args, **_kwargs: None
        previous_decky = sys.modules.get("decky")
        sys.modules["decky"] = decky_stub
        sys.modules.pop("main", None)
        import main  # pylint: disable=import-outside-toplevel

        plugin = main.Plugin()

        def cleanup():
            if previous_decky is None:
                sys.modules.pop("decky", None)
            else:
                sys.modules["decky"] = previous_decky
            sys.modules.pop("main", None)

        return plugin, settings_dir, cleanup

    def test_legacy_download_folder_migrates_to_default_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            legacy = str(Path(temporary) / "legacy" / "." / "receive")
            plugin, settings_dir, cleanup = self.make_plugin(temporary, {"download_folder": legacy})
            try:
                self.assertEqual(len(plugin.receive_locations), 1)
                self.assertEqual(plugin.receive_locations[0]["id"], "default")
                self.assertEqual(plugin.receive_locations[0]["name"], "Default")
                self.assertEqual(plugin.upload_dir, str(Path(legacy).resolve()))
                persisted = json.loads((settings_dir / "plugin-settings.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["download_folder"], plugin.upload_dir)
                self.assertEqual(persisted["default_receive_location_id"], "default")
                self.assertEqual(persisted["receive_locations"][0]["path"], plugin.upload_dir)
            finally:
                cleanup()

    def test_crud_enforces_unique_paths_and_default_delete_rule(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin, _settings_dir, cleanup = self.make_plugin(temporary, {})
            try:
                second_path = str(Path(temporary) / "second")
                created = asyncio.run(plugin.upsert_receive_location({"name": "Second", "path": second_path}))
                self.assertTrue(created["success"])
                second_id = next(item["id"] for item in plugin.receive_locations if item["name"] == "Second")
                duplicate = asyncio.run(plugin.upsert_receive_location({"name": "Duplicate", "path": second_path}))
                self.assertFalse(duplicate["success"])
                self.assertEqual(plugin.default_receive_location_id, "default")

                changed = asyncio.run(plugin.set_default_receive_location(second_id))
                self.assertTrue(changed["success"])
                self.assertEqual(plugin.default_receive_location_id, second_id)
                self.assertEqual(plugin.upload_dir, str(Path(second_path).resolve()))
                self.assertFalse(asyncio.run(plugin.delete_receive_location(second_id))["success"])
                self.assertTrue(asyncio.run(plugin.delete_receive_location("default"))["success"])
                self.assertEqual(len(plugin.receive_locations), 1)
            finally:
                cleanup()

    def test_unavailable_configured_location_is_retained_for_repath(self):
        with tempfile.TemporaryDirectory() as temporary:
            unavailable_path = Path(temporary) / "not-writable"
            unavailable_path.write_text("mounted drive placeholder", encoding="utf-8")
            unavailable = str(unavailable_path)
            settings = {
                "receive_locations": [{"id": "sd", "name": "SD card", "path": unavailable}],
                "default_receive_location_id": "sd",
            }
            plugin, _settings_dir, cleanup = self.make_plugin(temporary, settings)
            try:
                self.assertEqual(plugin.receive_locations[0]["id"], "sd")
                self.assertEqual(plugin.receive_locations[0]["path"], str(Path(unavailable).resolve()))
                self.assertEqual(plugin.default_receive_location_id, "sd")
            finally:
                cleanup()


if __name__ == "__main__":
    unittest.main()
