import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "py_modules"))

from receive_locations import (  # noqa: E402
    build_manifest,
    move_history_items,
    normalize_path,
    recover_move_journal,
    validate_receive_path,
)


class ReceiveLocationTests(unittest.TestCase):
    def make_entry(self, root: str, files: list[tuple[str, str]]) -> dict:
        save_paths = {}
        for item_id, relative in files:
            path = os.path.join(root, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write((item_id + relative).encode("utf-8"))
            save_paths[item_id] = path
        manifest = build_manifest(root, save_paths)
        return {
            "id": "recv-test",
            "manifestVersion": 1,
            "folderPath": root,
            "items": manifest,
            "files": [item["relativePath"] for item in manifest],
        }

    def test_paths_are_absolute_normalized_created_and_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            nested = os.path.join(temporary, "missing", ".", "folder")
            normalized = validate_receive_path(nested)
            self.assertEqual(normalized, os.path.realpath(os.path.join(temporary, "missing", "folder")))
            self.assertTrue(os.path.isdir(normalized))
            with self.assertRaises(ValueError):
                normalize_path("relative/path")

    def test_manifest_uses_exact_successful_files_and_rejects_outside_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = os.path.join(temporary, "receive")
            os.makedirs(root)
            inside = os.path.join(root, "folder", "file.bin")
            outside = os.path.join(temporary, "outside.bin")
            Path(inside).parent.mkdir()
            Path(inside).write_bytes(b"inside")
            Path(outside).write_bytes(b"outside")
            manifest = build_manifest(root, {"inside": inside, "outside": outside, "missing": os.path.join(root, "gone")})
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0]["relativePath"], "folder/file.bin")
            self.assertEqual(manifest[0]["size"], 6)

    def test_move_file_folder_and_whole_transfer_preserve_required_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source")
            destination = os.path.join(temporary, "destination")
            os.makedirs(source)
            entry = self.make_entry(source, [("one", "top.txt"), ("two", "folder/nested.txt")])
            history = [entry]
            history_path = os.path.join(temporary, "history.json")

            result = move_history_items(
                history,
                history_path,
                entry["id"],
                destination,
                selections=["folder/nested.txt"],
            )
            self.assertTrue(result["success"])
            self.assertTrue(os.path.isfile(os.path.join(destination, "nested.txt")))
            self.assertFalse(os.path.exists(os.path.join(source, "folder", "nested.txt")))

            whole_destination = os.path.join(temporary, "whole")
            result = move_history_items(history, history_path, entry["id"], whole_destination, move_entire=True)
            self.assertTrue(result["success"])
            self.assertTrue(os.path.isfile(os.path.join(whole_destination, "top.txt")))
            self.assertTrue(os.path.isfile(os.path.join(whole_destination, "folder", "nested.txt")))
            self.assertFalse(os.path.exists(os.path.join(source, "top.txt")))

    def test_collision_gets_suffix_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source")
            destination = os.path.join(temporary, "destination")
            os.makedirs(source)
            os.makedirs(destination)
            Path(os.path.join(destination, "top.txt")).write_text("existing", encoding="utf-8")
            entry = self.make_entry(source, [("one", "top.txt")])
            history_path = os.path.join(temporary, "history.json")
            result = move_history_items(history := [entry], history_path, entry["id"], destination, move_entire=True)
            self.assertTrue(result["success"])
            self.assertEqual(Path(os.path.join(destination, "top.txt")).read_text(encoding="utf-8"), "existing")
            self.assertTrue(os.path.isfile(os.path.join(destination, "top-2.txt")))
            self.assertTrue(history[0]["items"][0]["currentPath"].endswith("top-2.txt"))

    def test_partial_failure_is_reported_and_successful_items_are_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source")
            destination = os.path.join(temporary, "destination")
            os.makedirs(source)
            entry = self.make_entry(source, [("one", "good.txt"), ("two", "missing.txt")])
            os.remove(os.path.join(source, "missing.txt"))
            history = [entry]
            history_path = os.path.join(temporary, "history.json")
            result = move_history_items(
                history,
                history_path,
                entry["id"],
                destination,
                selections=["good.txt", "missing.txt"],
            )
            self.assertFalse(result["success"])
            self.assertTrue(result["partial"])
            self.assertTrue(os.path.isfile(os.path.join(destination, "good.txt")))
            self.assertTrue(any(failure["selection"] == "missing.txt" for failure in result["failures"]))

    def test_recursive_destination_and_legacy_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source")
            os.makedirs(source)
            entry = self.make_entry(source, [("one", "folder/file.txt")])
            history_path = os.path.join(temporary, "history.json")
            recursive_destination = os.path.join(source, "folder", "inside")
            recursive = move_history_items([entry], history_path, entry["id"], recursive_destination, move_entire=True)
            self.assertFalse(recursive["success"])
            self.assertFalse(os.path.exists(recursive_destination))

            legacy = {"id": "legacy", "manifestVersion": 0, "folderPath": source, "items": []}
            result = move_history_items([legacy], history_path, "legacy", os.path.join(temporary, "other"), move_entire=True)
            self.assertFalse(result["success"])
            self.assertIn("predates", result["error"])

    def test_move_journal_recovery_updates_manifest_and_retries_source_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "source.txt")
            destination = os.path.join(temporary, "destination.txt")
            Path(source).write_text("payload", encoding="utf-8")
            Path(destination).write_text("payload", encoding="utf-8")
            history_path = os.path.join(temporary, "history.json")
            entry = {
                "id": "recv-journal",
                "manifestVersion": 1,
                "items": [{"itemId": "one", "relativePath": "source.txt", "currentPath": source, "size": 7}],
            }
            journal_path = history_path + ".move-journal.json"
            with open(journal_path, "w", encoding="utf-8") as handle:
                json.dump({"historyId": entry["id"], "mappings": [{"itemId": "one", "source": source, "destination": destination}]}, handle)
            self.assertTrue(recover_move_journal([entry], history_path))
            self.assertEqual(entry["items"][0]["currentPath"], destination)
            self.assertFalse(os.path.exists(source))
            self.assertFalse(os.path.exists(journal_path))


if __name__ == "__main__":
    unittest.main()
