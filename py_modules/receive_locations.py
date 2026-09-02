"""Receive destination and manifest move helpers.

This module deliberately has no Decky dependency.  The plugin bridge owns
settings and HTTP calls while these helpers provide deterministic filesystem
behaviour that can be unit tested with temporary directories.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


DEFAULT_LOCATION_ID = "default"
MANIFEST_VERSION = 1
_MOVE_LOCK = threading.RLock()


def normalize_path(path: str) -> str:
    """Return a canonical absolute path without requiring it to exist."""
    value = os.path.expanduser(str(path or "").strip())
    if not value:
        raise ValueError("Receive path cannot be empty")
    if not os.path.isabs(value):
        raise ValueError("Receive path must be absolute")
    return os.path.realpath(os.path.abspath(os.path.normpath(value)))


def validate_receive_path(
    path: str,
    *,
    create: bool = True,
    check_writable: bool = True,
) -> str:
    """Normalize and validate a receive path.

    A small exclusive probe file is used instead of relying solely on
    ``os.access`` because the plugin may run with elevated privileges where
    access checks can be misleading.  The probe is always removed.
    """
    normalized = normalize_path(path)
    if os.path.exists(normalized):
        if not os.path.isdir(normalized):
            raise ValueError("Receive path is not a directory")
    elif create:
        try:
            os.makedirs(normalized, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Cannot create receive directory: {exc}") from exc
    else:
        raise ValueError("Receive directory does not exist")

    if not os.path.isdir(normalized):
        raise ValueError("Receive path is not a directory")

    canonical = os.path.realpath(normalized)
    if check_writable:
        probe = os.path.join(canonical, f".localsendplus-write-test-{uuid.uuid4().hex}")
        try:
            with open(probe, "x", encoding="utf-8") as handle:
                handle.write("")
        except OSError as exc:
            raise ValueError(f"Receive directory is not writable: {exc}") from exc
        finally:
            try:
                os.remove(probe)
            except FileNotFoundError:
                pass
            except OSError:
                # A failed cleanup must not make an otherwise valid path look
                # invalid.  It is logged by callers when appropriate.
                pass
    return canonical


def normalize_relative_path(path: str) -> str:
    """Normalize a manifest path to slash-separated relative notation."""
    value = str(path or "").replace("\\", "/")
    if "\x00" in value:
        raise ValueError("Invalid manifest relative path")
    # Reject parent components before normalization.  Resolving
    # ``folder/../file`` would hide a traversal attempt and make selection
    # validation depend on the host platform's path rules.
    if any(part == ".." for part in value.split("/")):
        raise ValueError("Invalid manifest relative path")
    while value.startswith("./"):
        value = value[2:]
    value = os.path.normpath(value).replace("\\", "/")
    if value in ("", ".") or value.startswith("../") or value == ".." or value.startswith("/"):
        raise ValueError("Invalid manifest relative path")
    return value


def _atomic_write_json(path: str, value: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".localsendplus-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def build_manifest(
    root: str,
    save_paths: Dict[str, str],
    *,
    destination_id: str = "",
    destination_name: str = "",
    flat: bool = False,
    logger: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """Build exact manifest items from Go's complete fileId -> savePath map."""
    canonical_root = normalize_path(root)
    items: List[Dict[str, Any]] = []
    for file_id, raw_path in (save_paths or {}).items():
        try:
            current_path = os.path.realpath(os.path.abspath(os.path.normpath(str(raw_path))))
            relative = os.path.relpath(current_path, canonical_root).replace(os.sep, "/")
            relative = normalize_relative_path(relative)
            # A manifest must never record a path outside the receive root.
            common = os.path.commonpath([canonical_root, current_path])
            if common != canonical_root:
                raise ValueError("saved path is outside receive root")
            stat_result = os.stat(current_path)
            if not os.path.isfile(current_path):
                raise ValueError("saved path is not a regular file")
            item: Dict[str, Any] = {
                "itemId": str(file_id),
                "relativePath": relative,
                "currentPath": current_path,
                "size": int(stat_result.st_size),
                "modifiedAt": float(stat_result.st_mtime),
            }
            items.append(item)
        except (OSError, ValueError) as exc:
            if logger:
                logger(f"Skipping invalid manifest item {file_id}: {exc}")

    items.sort(key=lambda item: (item["relativePath"], item["itemId"]))
    return items


def _safe_item_path(item: Dict[str, Any]) -> str:
    value = str(item.get("currentPath") or "")
    if not value or not os.path.isabs(value):
        raise ValueError("Manifest item has no absolute current path")
    raw_path = os.path.abspath(os.path.normpath(value))
    # Do not follow a symlink recorded as the file itself.  Parent directory
    # symlinks are canonicalized below, so all comparisons use one identity.
    if os.path.islink(raw_path):
        raise ValueError("Manifest item path is a symbolic link")
    return os.path.realpath(raw_path)


def _is_same_or_descendant(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(parent)]) == os.path.realpath(parent)
    except ValueError:
        return False


def _selection_matches(selection: str, relative: str) -> bool:
    return relative == selection or relative.startswith(selection + "/")


def _top_level(value: str) -> str:
    return value.split("/", 1)[0]


def _unique_name(path: str, reserved: set[str]) -> str:
    """Return a collision-safe path using the existing ``name-2`` convention."""
    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    base, extension = os.path.splitext(filename)
    candidate = path
    index = 2
    while os.path.lexists(candidate) or candidate in reserved:
        candidate = os.path.join(directory, f"{base}-{index}{extension}")
        index += 1
    reserved.add(candidate)
    return candidate


def _copy_verified(source: str, destination: str, expected_size: int) -> None:
    if os.path.islink(source) or not os.path.isfile(source):
        raise FileNotFoundError(f"Source file is unavailable: {source}")
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)
    actual_size = os.path.getsize(destination)
    if actual_size != expected_size:
        try:
            os.remove(destination)
        except OSError:
            pass
        raise IOError(f"Copied size mismatch for {source}")


def _cleanup_empty_parents(path: str, roots: Iterable[str]) -> None:
    current = os.path.dirname(path)
    normalized_roots = {
        os.path.realpath(os.path.abspath(root))
        for root in roots
        if root and os.path.isabs(str(root))
    }
    while current and os.path.realpath(current) not in normalized_roots:
        try:
            with os.scandir(current) as entries:
                is_empty = not any(entries)
        except OSError:
            break
        if is_empty:
            try:
                os.rmdir(current)
            except OSError:
                break
            current = os.path.dirname(current)
        else:
            break


def _save_history(history_path: str, history: List[Dict[str, Any]]) -> None:
    _atomic_write_json(history_path, history)


def _journal_path(history_path: str) -> str:
    return f"{history_path}.move-journal.json"


def recover_move_journal(
    history: List[Dict[str, Any]],
    history_path: str,
    *,
    logger: Optional[Callable[[str], None]] = None,
) -> bool:
    """Reconcile one interrupted move operation after plugin startup."""
    path = _journal_path(history_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            journal = json.load(handle)
        entry = next((item for item in history if item.get("id") == journal.get("historyId")), None)
        changed = False
        cleanup_pending = False
        mappings = journal.get("mappings") or []
        if entry is not None:
            by_id = {str(item.get("itemId")): item for item in entry.get("items") or []}
            for mapping in mappings:
                source = str(mapping.get("source") or "")
                destination = str(mapping.get("destination") or "")
                item_id = str(mapping.get("itemId") or "")
                if destination and os.path.exists(destination):
                    target_item = by_id.get(item_id)
                    if target_item is not None and target_item.get("currentPath") != destination:
                        target_item["currentPath"] = destination
                        try:
                            target_item["size"] = int(os.path.getsize(destination))
                            target_item["modifiedAt"] = float(os.path.getmtime(destination))
                        except OSError:
                            pass
                        changed = True
                    if source and os.path.exists(source) and os.path.realpath(source) != os.path.realpath(destination):
                        try:
                            os.remove(source)
                        except OSError as exc:
                            cleanup_pending = True
                            if logger:
                                logger(f"Could not remove recovered source {source}: {exc}")
                # If the destination never appeared, leave the source alone.
            if changed:
                _save_history(history_path, history)
        staging_root = str(journal.get("stagingRoot") or "")
        if staging_root and os.path.isdir(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)
        if not cleanup_pending:
            os.remove(path)
        return changed
    except Exception as exc:
        if logger:
            logger(f"Failed to recover receive move journal: {exc}")
        return False


def _build_move_groups(
    entry: Dict[str, Any],
    selections: List[str],
    move_entire: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    items = entry.get("items") or []
    normalized_items: List[Dict[str, Any]] = []
    for raw in items:
        copy = dict(raw)
        copy["relativePath"] = normalize_relative_path(copy.get("relativePath", ""))
        copy["currentPath"] = _safe_item_path(copy)
        normalized_items.append(copy)

    if move_entire:
        selected = sorted({_top_level(item["relativePath"]) for item in normalized_items})
    else:
        selected = []
        for raw_selection in selections:
            selection = normalize_relative_path(raw_selection)
            if not any(_selection_matches(selection, item["relativePath"]) for item in normalized_items):
                raise ValueError(f"Selection is not present in this transfer: {selection}")
            selected.append(selection)
        # A parent selection already includes its children; remove duplicates and
        # reject an ambiguous child/parent combination deterministically.
        selected = sorted(set(selected), key=lambda value: (value.count("/"), value))
        compact: List[str] = []
        for value in selected:
            if any(_selection_matches(parent, value) for parent in compact):
                continue
            compact.append(value)
        selected = compact

    groups: List[Dict[str, Any]] = []
    for selection in selected:
        matched = [item for item in normalized_items if _selection_matches(selection, item["relativePath"])]
        if not matched:
            continue
        selection_is_file = any(item["relativePath"] == selection for item in matched)
        if selection_is_file:
            group_relative = os.path.basename(selection)
        else:
            group_relative = os.path.basename(selection.rstrip("/"))
        group_items = []
        source_roots: set[str] = set()
        for item in matched:
            if selection_is_file:
                target_relative = group_relative
            else:
                suffix = item["relativePath"][len(selection):].lstrip("/")
                target_relative = "/".join(part for part in (group_relative, suffix) if part)
                source_root = item["currentPath"]
                for _ in suffix.split("/"):
                    source_root = os.path.dirname(source_root)
                source_roots.add(os.path.realpath(source_root))
            group_items.append((item, target_relative))
        groups.append({"selection": selection, "items": group_items, "isFile": selection_is_file, "sourceRoots": source_roots})
    return groups, selected


def _move_history_items_unlocked(
    history: List[Dict[str, Any]],
    history_path: str,
    history_id: str,
    destination_root: str,
    *,
    selections: Optional[List[str]] = None,
    move_entire: bool = False,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Move selected manifest entries safely and update history incrementally."""
    entry = next((item for item in history if item.get("id") == history_id), None)
    if entry is None:
        return {"success": False, "error": "History item not found"}
    if int(entry.get("manifestVersion", 0) or 0) < MANIFEST_VERSION:
        return {"success": False, "error": "This history item predates file manifests"}

    try:
        # Canonicalize without creating anything first.  In particular, a
        # rejected recursive destination must not leave a newly-created empty
        # directory inside the selected source tree.
        destination_candidate = normalize_path(destination_root)
        groups, selected = _build_move_groups(entry, selections or [], move_entire)
        if not groups:
            return {"success": False, "error": "No files selected"}

        for group in groups:
            if group.get("isFile"):
                continue
            source_directories = group.get("sourceRoots") or {
                os.path.dirname(_safe_item_path(item)) for item, _ in group["items"]
            }
            if any(_is_same_or_descendant(destination_candidate, source_dir) for source_dir in source_directories):
                return {"success": False, "error": "Destination is inside a selected source"}

        destination = validate_receive_path(destination_candidate, create=True, check_writable=True)

        reserved: set[str] = set()
        operation_id = uuid.uuid4().hex
        journal_file = _journal_path(history_path)
        mappings: List[Dict[str, Any]] = []
        successful_groups: List[Dict[str, Any]] = []
        skipped: List[str] = []
        failures: List[Dict[str, str]] = []
        staging_root = os.path.join(destination, f".localsendplus-moving-{operation_id}")
        os.makedirs(staging_root, exist_ok=True)
        journal = {
            "version": 1,
            "operationId": operation_id,
            "historyId": history_id,
            "createdAt": time.time(),
            "stagingRoot": staging_root,
            "mappings": mappings,
        }
        journal_file = _journal_path(history_path)
        _atomic_write_json(journal_file, journal)

        for group_index, group in enumerate(groups):
            target_name = _top_level(group["items"][0][1])
            base_final_path = os.path.join(destination, target_name)
            # A transfer already rooted at this exact target is a no-op.
            if all(
                os.path.realpath(item["currentPath"]) == os.path.realpath(
                    os.path.join(base_final_path, target_relative.split("/", 1)[1])
                    if "/" in target_relative else base_final_path
                )
                for item, target_relative in group["items"]
            ):
                skipped.append(group["selection"])
                continue
            final_path = _unique_name(base_final_path, reserved)

            stage_group = os.path.join(staging_root, f"group-{group_index}")
            stage_root = os.path.join(stage_group, target_name)
            group_mappings: List[Dict[str, Any]] = []
            try:
                for item, target_relative in group["items"]:
                    stage_path = os.path.join(stage_group, target_relative)
                    _copy_verified(item["currentPath"], stage_path, int(item.get("size", os.path.getsize(item["currentPath"]))))
                    final_item_path = os.path.join(final_path, target_relative.split("/", 1)[1]) if "/" in target_relative else final_path
                    group_mappings.append({
                        "itemId": str(item.get("itemId") or ""),
                        "source": item["currentPath"],
                        "destination": final_item_path,
                    })
                # Record the intended mapping before publishing.  If the
                # process stops while publishing, startup recovery can inspect
                # whichever side exists and preserve the source.
                journal["mappings"] = mappings + group_mappings
                _atomic_write_json(journal_file, journal)
                os.makedirs(os.path.dirname(final_path), exist_ok=True)
                # Re-check immediately before publishing.  The initial
                # collision scan cannot protect against another process
                # creating the same name while the source is being staged.
                # Refuse the publish rather than replacing anything.
                if os.path.lexists(final_path):
                    raise FileExistsError(f"Destination already exists: {final_path}")
                os.replace(stage_root, final_path)
                mappings.extend(group_mappings)
                successful_groups.append({"selection": group["selection"], "mappings": group_mappings})
            except Exception as exc:
                failures.append({"selection": group["selection"], "error": str(exc)})
                shutil.rmtree(stage_group, ignore_errors=True)

        if not successful_groups:
            shutil.rmtree(staging_root, ignore_errors=True)
            try:
                os.remove(journal_file)
            except FileNotFoundError:
                pass
            if skipped and not failures:
                return {
                    "success": True,
                    "moved": [],
                    "selected": selected,
                    "skipped": skipped,
                    "failures": [],
                    "partial": False,
                }
            return {
                "success": False,
                "error": "No selected items could be moved",
                "selected": selected,
                "skipped": skipped,
                "failures": failures,
            }

        by_id = {str(item.get("itemId")): item for item in entry.get("items") or []}
        for mapping in mappings:
            item = by_id.get(mapping["itemId"])
            if item is not None:
                item["currentPath"] = mapping["destination"]
                try:
                    item["size"] = int(os.path.getsize(mapping["destination"]))
                    item["modifiedAt"] = float(os.path.getmtime(mapping["destination"]))
                except OSError:
                    pass
        _save_history(history_path, history)

        # Remove sources only after the durable history update.  If deletion
        # fails the journal remains and startup recovery can retry it.
        source_failures: List[Dict[str, str]] = []
        cleanup_roots = [entry.get("folderPath", ""), entry.get("destinationPath", "")]
        for mapping in mappings:
            source = mapping["source"]
            destination_path = mapping["destination"]
            if os.path.realpath(source) == os.path.realpath(destination_path):
                continue
            try:
                if os.path.exists(source):
                    os.remove(source)
                    if any(
                        _is_same_or_descendant(source, root)
                        for root in cleanup_roots
                        if root and os.path.isabs(str(root))
                    ):
                        _cleanup_empty_parents(source, cleanup_roots)
            except OSError as exc:
                source_failures.append({"itemId": mapping["itemId"], "error": str(exc)})

        shutil.rmtree(staging_root, ignore_errors=True)
        if not source_failures:
            try:
                os.remove(journal_file)
            except FileNotFoundError:
                pass
        else:
            failures.extend({"selection": item["itemId"], "error": item["error"]} for item in source_failures)

        return {
            "success": not failures,
            "moved": [mapping["itemId"] for mapping in mappings],
            "selected": selected,
            "skipped": skipped,
            "failures": failures,
            "partial": bool(failures),
        }
    except (OSError, ValueError) as exc:
        if logger:
            logger(f"Receive move failed: {exc}")
        return {"success": False, "error": str(exc)}


def move_history_items(
    history: List[Dict[str, Any]],
    history_path: str,
    history_id: str,
    destination_root: str,
    *,
    selections: Optional[List[str]] = None,
    move_entire: bool = False,
    logger: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Serialize move operations so the journal and collision allocation stay coherent."""
    with _MOVE_LOCK:
        return _move_history_items_unlocked(
            history,
            history_path,
            history_id,
            destination_root,
            selections=selections,
            move_entire=move_entire,
            logger=logger,
        )
