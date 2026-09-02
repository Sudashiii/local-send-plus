import os
import asyncio
import subprocess
import time
import json
import uuid

from typing import Any, Dict, Optional


import decky  # pyright: ignore[reportMissingModuleSource]


from http_utils import do_request, parse_response  # pyright: ignore[reportMissingImports]
from config_utils import (  # pyright: ignore[reportMissingImports]
    read_config_yaml,
    load_json_settings,
    save_json_settings,
)
from file_utils import (  # pyright: ignore[reportMissingImports]
    list_folder_files,
    load_receive_history,
    save_receive_history,
    create_receive_history_entry,
    write_temp_text_file,
)
from receive_locations import (  # pyright: ignore[reportMissingImports]
    DEFAULT_LOCATION_ID,
    MANIFEST_VERSION,
    build_manifest,
    move_history_items,
    normalize_path,
    recover_move_journal,
    validate_receive_path,
)
from notify_server import NotifyServer  # pyright: ignore[reportMissingImports]


class Plugin:
    def __init__(self):
        self.loop = None
        self.process = None
        self.log_file = None
        self.backend_port = 53317
        self.config_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "localsend.yaml")
        self.upload_dir = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "uploads")
        self.binary_path = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "localsend-core")
        self.settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "plugin-settings.json")

        # Scan Config
        self.legacy_mode = False
        self.use_mixed_scan = True  # Default to Mixed mode
        self.skip_notify = False
        self.multicast_address = "224.0.0.167"
        self.multicast_port = 53317

        # Plugin Settings
        self.alias = ""
        self.pin = ""
        self.auto_save = False
        self.auto_save_from_favorites = False
        self.use_https = True
        self.notify_on_download = True
        self.save_receive_history = True
        self.use_download = False  # Enable Download API (share via link)
        self.do_not_make_session_folder = False
        self.disable_info_logging = False
        self.scan_timeout = 500  # scan timeout in seconds, default 500
        self.network_interface = "*"  # "*" means all interfaces
        
        # Unix Domain Socket notification server
        self.socket_path = "/tmp/localsend-notify.sock"
        self.notify_server = NotifyServer(
            socket_path=self.socket_path,
            logger_info=lambda msg: decky.logger.info(msg),
            logger_error=lambda msg: decky.logger.error(msg),
            logger_warning=lambda msg: decky.logger.warning(msg),
        )
        self.notify_server.set_notification_handler(self._handle_notification)
        
        # Upload session tracking
        self.upload_sessions: Dict[str, Dict[str, Any]] = {}
        
        # File receive history
        self.receive_history_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "receive-history.json")
        self.receive_history: list = []
        self.receive_locations: list = []
        self.default_receive_location_id = DEFAULT_LOCATION_ID

        self._load_settings()
        self._load_receive_history()

    @property
    def backend_url(self) -> str:
        """Dynamically compute backend URL based on use_https setting"""
        protocol = "https" if self.use_https else "http"
        return f"{protocol}://127.0.0.1:{self.backend_port}"

    def _get_default_settings(self) -> Dict[str, Any]:
        """Get default settings dictionary"""
        return {
            "alias": self.alias,
            "legacy_mode": self.legacy_mode,
            "use_mixed_scan": self.use_mixed_scan,
            "skip_notify": self.skip_notify,
            "multicast_address": self.multicast_address,
            "multicast_port": self.multicast_port,
            "pin": self.pin,
            "auto_save": self.auto_save,
            "auto_save_from_favorites": self.auto_save_from_favorites,
            "use_https": self.use_https,
            "network_interface": self.network_interface,
            "notify_on_download": self.notify_on_download,
            "save_receive_history": self.save_receive_history,
            "use_download": self.use_download,
            "do_not_make_session_folder": self.do_not_make_session_folder,
            "disable_info_logging": self.disable_info_logging,
            "download_folder": self.upload_dir,
            "receive_locations": self.receive_locations,
            "default_receive_location_id": self.default_receive_location_id,
            "scan_timeout": self.scan_timeout,
        }

    def _load_settings(self):
        """Load plugin settings from disk"""
        try:
            os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
            
            data = load_json_settings(
                self.settings_path,
                self._get_default_settings(),
                logger=lambda msg: decky.logger.info(msg)
            )
            
            # Load alias: prefer JSON, fallback to YAML config
            json_alias = str(data.get("alias", "")).strip()
            if json_alias:
                self.alias = json_alias
            else:
                # Fallback to YAML config (generated by backend)
                yaml_config = read_config_yaml(
                    self.config_path,
                    logger=lambda msg: decky.logger.error(msg)
                )
                self.alias = str(yaml_config.get("alias", "")).strip()
            self.legacy_mode = False
            self.use_mixed_scan = True
            self.skip_notify = bool(data.get("skip_notify", self.skip_notify))
            self.multicast_address = str(data.get("multicast_address", self.multicast_address)).strip()
            multicast_port = data.get("multicast_port", self.multicast_port)
            try:
                self.multicast_port = int(multicast_port or 0)
            except (ValueError, TypeError):
                self.multicast_port = 53317
            self.pin = str(data.get("pin", "")).strip()
            self.auto_save = bool(data.get("auto_save", self.auto_save))
            self.auto_save_from_favorites = bool(data.get("auto_save_from_favorites", self.auto_save_from_favorites))
            self.use_https = bool(data.get("use_https", self.use_https))
            self.network_interface = str(data.get("network_interface", self.network_interface)).strip() or "*"
            self.notify_on_download = bool(data.get("notify_on_download", self.notify_on_download))
            self.save_receive_history = bool(data.get("save_receive_history", self.save_receive_history))
            self.use_download = bool(data.get("use_download", self.use_download))
            self.do_not_make_session_folder = bool(data.get("do_not_make_session_folder", self.do_not_make_session_folder))
            self.disable_info_logging = bool(data.get("disable_info_logging", False))
            scan_timeout = data.get("scan_timeout", self.scan_timeout)
            try:
                self.scan_timeout = int(scan_timeout or 500)
            except (ValueError, TypeError):
                self.scan_timeout = 500
            upload_dir = str(data.get("download_folder", "")).strip()
            if upload_dir:
                try:
                    self.upload_dir = normalize_path(upload_dir)
                except ValueError:
                    decky.logger.warning(f"Ignoring invalid configured download folder: {upload_dir}")

            self._load_receive_locations(data)
            decky.logger.info("Settings loaded successfully")
        except Exception as e:
            decky.logger.warning(f"Failed to load settings: {e}")

    def _load_receive_locations(self, data: Dict[str, Any]):
        """Load named receive locations and migrate the legacy folder setting."""
        raw_locations = data.get("receive_locations")
        locations: list = []
        seen_names: set[str] = set()
        seen_paths: set[str] = set()

        if isinstance(raw_locations, list):
            for raw in raw_locations:
                if not isinstance(raw, dict):
                    continue
                try:
                    # Keep a canonical path even when a removable drive is
                    # temporarily unavailable.  The location remains visible
                    # (and can be repathed) while availability is reported by
                    # _location_payload; malformed/non-absolute paths are the
                    # only entries discarded during migration.
                    path = normalize_path(str(raw.get("path", "")))
                except ValueError:
                    continue
                try:
                    validate_receive_path(path, create=True, check_writable=True)
                except (OSError, ValueError) as exc:
                    decky.logger.warning(f"Receive location unavailable at startup ({path}): {exc}")
                name = str(raw.get("name", "")).strip() or "Location"
                name_key = name.casefold()
                if name_key in seen_names or path in seen_paths:
                    continue
                location_id = str(raw.get("id", "")).strip() or f"loc-{uuid.uuid4().hex[:12]}"
                if any(item.get("id") == location_id for item in locations):
                    location_id = f"loc-{uuid.uuid4().hex[:12]}"
                locations.append({"id": location_id, "name": name, "path": path})
                seen_names.add(name_key)
                seen_paths.add(path)

        if not locations:
            # Existing installations only have download_folder.  Preserve it
            # as the built-in default location instead of changing where files
            # are received.
            legacy_path = self.upload_dir
            try:
                legacy_path = validate_receive_path(
                    legacy_path,
                    create=True,
                    check_writable=True,
                )
            except (OSError, ValueError) as exc:
                decky.logger.warning(f"Falling back to runtime receive folder: {exc}")
                legacy_path = normalize_path(os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "uploads"))
                os.makedirs(legacy_path, exist_ok=True)
            locations = [{"id": DEFAULT_LOCATION_ID, "name": "Default", "path": legacy_path}]

        default_id = str(data.get("default_receive_location_id", "")).strip()
        if not default_id or not any(item["id"] == default_id for item in locations):
            default_id = locations[0]["id"]
        self.receive_locations = locations
        self.default_receive_location_id = default_id
        default_location = next(item for item in locations if item["id"] == default_id)
        self.upload_dir = default_location["path"]

        # Persist migration immediately.  The write is best effort; normal
        # settings changes will retry it later.
        self._save_settings()

    def _load_receive_history(self):
        """Load file receive history from disk"""
        self.receive_history = load_receive_history(
            self.receive_history_path,
            logger=lambda msg: decky.logger.info(msg)
        )
        recover_move_journal(
            self.receive_history,
            self.receive_history_path,
            logger=lambda msg: decky.logger.warning(msg),
        )

    def _save_receive_history(self):
        """Persist file receive history to disk"""
        save_receive_history(
            self.receive_history_path,
            self.receive_history,
            logger=lambda msg: decky.logger.error(msg)
        )

    def _add_receive_history(
        self,
        folder_path: str,
        files: list,
        title: str = "",
        is_text: bool = False,
        text_content: str = "",
        total_files: Optional[int] = None,
        success_files: Optional[int] = None,
        failed_files: Optional[int] = None,
        failed_file_ids: Optional[list] = None,
        manifest: Optional[list] = None,
        destination_id: str = "",
        destination_name: str = "",
        destination_path: str = "",
        receive_layout: str = "",
        session_id: str = "",
    ):
        """Add a new entry to receive history"""
        if not self.save_receive_history:
            return
        
        entry = create_receive_history_entry(
            folder_path=folder_path,
            files=files,
            title=title,
            is_text=is_text,
            text_content=text_content,
            current_count=len(self.receive_history),
            total_files=total_files,
            success_files=success_files,
            failed_files=failed_files,
            failed_file_ids=failed_file_ids,
            manifest=manifest,
            destination_id=destination_id,
            destination_name=destination_name,
            destination_path=destination_path,
            receive_layout=receive_layout,
            session_id=session_id,
        )
        
        self.receive_history.insert(0, entry)  # Insert at beginning (newest first)
        
        # Keep only last 100 records
        if len(self.receive_history) > 100:
            self.receive_history = self.receive_history[:100]
        
        self._save_receive_history()
        if is_text:
            decky.logger.info(f"Added receive history (text): {len(text_content)} characters")
        else:
            decky.logger.info(f"Added receive history: {folder_path} ({len(files)} files)")
        return entry

    def _save_settings(self):
        """Persist plugin settings to disk"""
        settings = {
            "alias": self.alias,
            "legacy_mode": self.legacy_mode,
            "use_mixed_scan": self.use_mixed_scan,
            "skip_notify": self.skip_notify,
            "multicast_address": self.multicast_address,
            "multicast_port": self.multicast_port,
            "pin": self.pin,
            "auto_save": self.auto_save,
            "auto_save_from_favorites": self.auto_save_from_favorites,
            "use_https": self.use_https,
            "network_interface": self.network_interface,
            "notify_on_download": self.notify_on_download,
            "save_receive_history": self.save_receive_history,
            "use_download": self.use_download,
            "do_not_make_session_folder": self.do_not_make_session_folder,
            "disable_info_logging": self.disable_info_logging,
            "download_folder": self.upload_dir,
            "receive_locations": self.receive_locations,
            "default_receive_location_id": self.default_receive_location_id,
            "scan_timeout": self.scan_timeout,
        }
        save_json_settings(
            self.settings_path,
            settings,
            logger=lambda msg: decky.logger.error(msg)
        )

    def _ensure_dirs(self):
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(decky.DECKY_PLUGIN_LOG_DIR, exist_ok=True)

    def _location_by_id(self, location_id: str) -> Optional[dict]:
        return next(
            (location for location in self.receive_locations if location.get("id") == location_id),
            None,
        )

    def _location_is_available(self, location: dict) -> bool:
        try:
            validate_receive_path(location.get("path", ""), create=True, check_writable=True)
            return True
        except (OSError, ValueError):
            return False

    def _location_payload(self) -> dict:
        locations = []
        for location in self.receive_locations:
            locations.append({
                **location,
                "isDefault": location.get("id") == self.default_receive_location_id,
                "available": self._location_is_available(location),
            })
        return {
            "locations": locations,
            "defaultId": self.default_receive_location_id,
        }

    def _default_location(self) -> dict:
        location = self._location_by_id(self.default_receive_location_id)
        if location is None:
            location = self.receive_locations[0]
            self.default_receive_location_id = location["id"]
        return location

    def _manifest_for_notification(self, session_id: str) -> Optional[dict]:
        """Fetch the complete Go manifest while it is still cached.

        The Go upload-end notification is sent synchronously from a goroutine;
        the backend retains its save-path cache until the notification socket
        request receives a response.  This synchronous request therefore runs
        before cleanup and avoids the socket notification size limit.
        """
        if not session_id or not self._is_running():
            return None
        try:
            url = f"{self.backend_url}/api/self/v1/receive-manifest?sessionId={session_id}"
            response_data, status_code, content_type = do_request(
                "GET",
                url,
                max_retries=0,
                timeout=5,
                logger=lambda msg: decky.logger.warning(msg),
            )
            if status_code != 200:
                decky.logger.warning(f"Receive manifest request failed: {status_code}")
                return None
            response = parse_response(response_data, content_type)
            if isinstance(response, dict):
                return response.get("data") if isinstance(response.get("data"), dict) else response
        except Exception as exc:
            decky.logger.warning(f"Failed to fetch receive manifest: {exc}")
        return None

    def _build_notification_manifest(
        self,
        session_id: str,
        manifest_data: Optional[dict],
        fallback_root: str,
        fallback_paths: Optional[dict],
    ) -> tuple[list, str, str, str, str, str]:
        """Return (items, root, destination_id, destination_name, layout, destination_path)."""
        data = manifest_data or {}
        root = str(data.get("root") or fallback_root or "").strip()
        try:
            root = normalize_path(root)
        except ValueError:
            root = os.path.normpath(os.path.abspath(fallback_root)) if fallback_root else ""
        save_paths = data.get("savePaths") if isinstance(data.get("savePaths"), dict) else fallback_paths or {}
        items = build_manifest(root, save_paths, logger=lambda msg: decky.logger.warning(msg)) if root else []

        layout = str(data.get("layout") or ("flat" if data.get("flat") else "session"))
        destination_path = str(data.get("destinationPath") or (os.path.dirname(root) if layout == "session" else root))
        try:
            destination_path = normalize_path(destination_path)
        except ValueError:
            destination_path = os.path.dirname(root) if layout == "session" else root
        destination_id = str(data.get("destinationId") or "")
        destination_name = str(data.get("destinationName") or "")
        if not destination_id or not destination_name:
            for location in self.receive_locations:
                try:
                    location_path = normalize_path(location.get("path", ""))
                except ValueError:
                    continue
                if layout == "session":
                    parent = os.path.dirname(root)
                else:
                    parent = root
                if location_path == parent:
                    destination_id = destination_id or str(location.get("id", ""))
                    destination_name = destination_name or str(location.get("name", ""))
                    destination_path = location_path
                    break
        return items, root, destination_id, destination_name, layout, destination_path

    def _emit_notification_safe(self, title: str, message: str):
        """Safely emit notification from thread to main event loop"""
        if self.loop is None or self.loop.is_closed():
            decky.logger.warning("Event loop not available, skipping notification emit")
            return
        
        try:
            asyncio.run_coroutine_threadsafe(
                decky.emit("unix_socket_notification", {
                    "type": "info",
                    "title": title,
                    "message": message,
                }),
                self.loop
            )
        except Exception as e:
            decky.logger.error(f"Failed to emit notification: {e}")

    def _emit_notification_event(self, payload: dict):
        """Emit full notification payload to frontend"""
        if self.loop is None or self.loop.is_closed():
            decky.logger.warning("Event loop not available, skipping notification emit")
            return

        try:
            asyncio.run_coroutine_threadsafe(
                decky.emit("unix_socket_notification", payload),
                self.loop
            )
        except Exception as e:
            decky.logger.error(f"Failed to emit notification: {e}")
    
    def _handle_notification(self, notification: dict):
        """Handle incoming notification from Go backend"""
        try:
            notification_type = notification.get('type')
            title = notification.get('title', '')
            message = notification.get('message', '')
            # Guard against JSON null: .get('data', {}) returns None when key exists with value null
            notification_data = notification.get('data') or {}
            is_text_only = notification.get('isTextOnly', False)
            manifest_data = None

            # Include the current location snapshot in the confirmation event
            # so the modal can render immediately without an async settings
            # round-trip while the sender is waiting.
            if notification_type == "confirm_recv":
                notification_data = {
                    **notification_data,
                    "receiveLocations": self._location_payload()["locations"],
                    "defaultReceiveLocationId": self.default_receive_location_id,
                }

            # Fetch the untruncated manifest before the Go backend removes its
            # completed-session cache.  Text-only notifications do not create
            # a file manifest and retain their existing behaviour.
            if notification_type == "upload_end" and not is_text_only:
                manifest_data = self._manifest_for_notification(str(notification_data.get("sessionId") or ""))

            self._emit_notification_event({
                "type": notification_type,
                "title": title,
                "message": message,
                "data": notification_data,
            })

            # device_discovered / device_updated: no session payload, skip upload logic
            if notification_type in ('device_discovered', 'device_updated'):
                decky.logger.debug(f"Device notification: {notification_type} - {title}: {message}")
                return

            # text_received: single text/plain with preview, receiver returns 204 after user dismisses
            if notification_type == 'text_received':
                from_alias = notification_data.get('from', '')
                content = notification_data.get('content', '') or message
                file_name = notification_data.get('fileName', 'clipboard.txt')
                display_title = notification_data.get('title') or title or 'Text Received'
                session_id = notification_data.get('sessionId', '') or ''
                self._add_receive_history(
                    folder_path='',
                    files=[file_name],
                    title=display_title,
                    is_text=True,
                    text_content=content
                )
                asyncio.run_coroutine_threadsafe(
                    decky.emit("text_received", {
                        "title": display_title,
                        "content": content,
                        "fileName": file_name,
                        "sessionId": session_id,
                    }),
                    self.loop
                )
                decky.logger.info(f"📝 Text received (no upload) from {from_alias}: {len(content)} chars")
                return

            session_id = notification_data.get('sessionId', '') or ''

            if notification_type == 'upload_start':
                # New format: session-level with files array (guard against null from backend)
                total_files = notification_data.get('totalFiles') or 0
                total_size = notification_data.get('totalSize') or 0
                files = notification_data.get('files') or []
                
                decky.logger.info(f"📤 Upload session started: {session_id}")
                decky.logger.info(f"   Total files: {total_files}, Total size: {total_size} bytes")
                
                # Initialize session
                do_not_make_session_folder = notification_data.get('doNotMakeSessionFolder', False)
                upload_folder = notification_data.get('uploadFolder') or ''
                if session_id not in self.upload_sessions:
                    self.upload_sessions[session_id] = {
                        '_meta': {
                            'total_files': total_files,
                            'total_size': total_size,
                            'start_time': time.time(),
                            'is_text_only': is_text_only,
                            'do_not_make_session_folder': do_not_make_session_folder,
                            'upload_folder': upload_folder,
                        }
                    }
                
                # Save each file's information
                for file_info in files:
                    file_id = file_info.get('fileId', '')
                    file_name = file_info.get('fileName', '')
                    file_size = file_info.get('size', 0)
                    file_type = file_info.get('fileType', '')
                    
                    decky.logger.info(f"   - {file_name} ({file_size} bytes, {file_type})")
                    
                    self.upload_sessions[session_id][file_id] = {
                        'file_id': file_id,
                        'file_name': file_name,
                        'file_size': file_size,
                        'file_type': file_type,
                        'start_time': time.time(),
                        'status': 'uploading',
                        'is_text_only': is_text_only
                    }
                
            elif notification_type == 'upload_end':
                # New format: session-level summary (guard against null from backend)
                total_files = notification_data.get('totalFiles') or 0
                success_files = notification_data.get('successFiles') or 0
                failed_files = notification_data.get('failedFiles') or 0
                failed_file_ids = notification_data.get('failedFileIds') or []
                save_paths = notification_data.get('savePaths') or {}
                saved_file_names = notification_data.get('savedFileNames') or []
                do_not_make_session_folder = notification_data.get('doNotMakeSessionFolder', False)
                upload_folder = notification_data.get('uploadFolder') or self.upload_dir
                # Display path: always the receive root (session dir), not a subfolder of first file
                display_folder_path = notification_data.get('receiveRoot') or (
                    upload_folder if do_not_make_session_folder else os.path.join(upload_folder, session_id)
                )
                if not os.path.isabs(display_folder_path):
                    display_folder_path = os.path.normpath(os.path.join(os.path.abspath(self.upload_dir), display_folder_path))
                else:
                    display_folder_path = os.path.normpath(display_folder_path)

                manifest_items, manifest_root, destination_id, destination_name, receive_layout, destination_path = self._build_notification_manifest(
                    session_id,
                    manifest_data,
                    display_folder_path,
                    save_paths,
                )
                if manifest_items:
                    display_folder_path = manifest_root
                    folder_path = manifest_root
                    files_in_folder = [item.get("relativePath", "") for item in manifest_items]
                else:
                    folder_path = display_folder_path
                if save_paths:
                    # Keep the legacy fallback for older Go binaries that do
                    # not expose the manifest endpoint.
                    first_path = next(iter(save_paths.values()))
                    if not manifest_items:
                        folder_path = os.path.dirname(first_path) if not do_not_make_session_folder else display_folder_path
                if not manifest_items and saved_file_names:
                    files_in_folder = list(saved_file_names)
                elif not manifest_items and save_paths:
                    files_in_folder = [os.path.basename(p) for p in save_paths.values()]
                elif not manifest_items and do_not_make_session_folder and folder_path == upload_folder:
                    # Avoid listing entire upload folder; use session file list for this session only
                    if session_id in self.upload_sessions:
                        files_in_folder = [
                            self.upload_sessions[session_id][fid]['file_name']
                            for fid in self.upload_sessions[session_id]
                            if fid != '_meta' and fid not in failed_file_ids
                        ]
                    else:
                        files_in_folder = []
                elif not manifest_items:
                    files_in_folder = os.listdir(folder_path) if os.path.isdir(folder_path) else []

                decky.logger.info(f"✅ Upload session completed: {session_id}")
                decky.logger.info(f"   Total: {total_files}, Success: {success_files}, Failed: {failed_files}")
                if failed_file_ids:
                    decky.logger.info(f"   Failed file IDs: {failed_file_ids}")
                
                # Update upload session status for all files
                if session_id in self.upload_sessions:
                    for fid, file_session in self.upload_sessions[session_id].items():
                        # Skip metadata entry
                        if fid == '_meta':
                            continue
                        if fid in failed_file_ids:
                            file_session['status'] = 'failed'
                        else:
                            file_session['status'] = 'completed'
                        file_session['end_time'] = time.time()
                        duration = file_session['end_time'] - file_session.get('start_time', 0)
                        decky.logger.info(f"   File {fid}: {file_session['status']} ({duration:.2f}s)")
                
                # Check if this is a text-only session
                if is_text_only:
                    decky.logger.info(f"📝 Text-only notification detected")
                    # Read text content from uploaded file
                    try:
                        file_path = folder_path
                        txt_files = [f for f in os.listdir(file_path) if f.endswith('.txt')]
                        text_file_name = txt_files[0] if txt_files else ''
                        if text_file_name and os.path.exists(os.path.join(file_path, text_file_name)):
                            with open(os.path.join(file_path, text_file_name), 'r', encoding='utf-8') as f:
                                text_content = f.read()
                            
                            # Save to receive history
                            self._add_receive_history(
                                folder_path=file_path,
                                files=[text_file_name],
                                title=title or "Text Received",
                                is_text=True,
                                text_content=text_content
                            )
                            
                            # Send text content to frontend with special event type
                            asyncio.run_coroutine_threadsafe(
                                decky.emit("text_received", {
                                    "title": title or "Text Received",
                                    "content": text_content,
                                    "fileName": text_file_name
                                }),
                                self.loop
                            )
                            decky.logger.info(f"📝 Text content sent to frontend: {len(text_content)} characters")
                        else:
                            decky.logger.warning(f"Text file not found: {file_path}")
                    except Exception as e:
                        decky.logger.error(f"Failed to read text content: {e}")
                else:
                    # Send file received notification if enabled
                    try:
                        # Save to receive history (use display path and actual totals)
                        history_entry = self._add_receive_history(
                            display_folder_path,
                            files_in_folder,
                            title or "File Received",
                            total_files=total_files,
                            success_files=success_files,
                            failed_files=failed_files,
                            failed_file_ids=failed_file_ids,
                            manifest=manifest_items if manifest_data is not None else None,
                            destination_id=destination_id,
                            destination_name=destination_name,
                            destination_path=destination_path,
                            receive_layout=receive_layout,
                            session_id=session_id,
                        )
                        
                        # Send notification if enabled (folderPath = receive root; fileCount = actual total)
                        if self.notify_on_download:
                            asyncio.run_coroutine_threadsafe(
                                decky.emit("file_received", {
                                    "title": title or "File Received",
                                    "folderPath": display_folder_path,
                                    "fileCount": total_files,
                                    "files": files_in_folder,
                                    "totalFiles": total_files,
                                    "successFiles": success_files,
                                    "failedFiles": failed_files,
                                    "failedFileIds": failed_file_ids,
                                    "historyId": history_entry.get("id") if history_entry else "",
                                    "manifestVersion": MANIFEST_VERSION if manifest_items else 0,
                                }),
                                self.loop
                            )
                            decky.logger.info(f"📁 File received notification sent: {display_folder_path}")
                    except Exception as e:
                        decky.logger.error(f"Failed to send file received notification: {e}")
                    
            elif notification_type == 'info':
                decky.logger.info(f"ℹ️  {title}: {message}")
            elif notification_type == 'send_progress':
                # Sender-side progress; already forwarded to frontend via _emit_notification_event
                return
            else:
                decky.logger.warning(f"⚠️  Unknown notification type: {notification_type}")
        except Exception as e:
            decky.logger.error(f"❌ Error processing notification: {str(e)}")
            self._emit_notification_safe(
                "Error",
                f"Error processing notification: {str(e)}"
            )

    def _is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _start_backend(self):
        if self._is_running():
            return
        if not os.path.exists(self.binary_path):
            raise FileNotFoundError(f"backend binary not found: {self.binary_path}")

        # Do not let the Go binary silently fall back to its own relative
        # "uploads" directory when a persisted default (for example an
        # unmounted SD card) is unavailable.  Surface the validation error to
        # Decky and keep the previous location intact instead.
        self.upload_dir = validate_receive_path(
            self._default_location().get("path", self.upload_dir),
            create=True,
            check_writable=True,
        )
        self._default_location()["path"] = self.upload_dir
        self._ensure_dirs()
        log_path = os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "localsend-backend.log")
        self.log_file = open(log_path, "a", encoding="utf-8")
        env = os.environ.copy()

        # Build startup command
        log_level = "none" if self.disable_info_logging else "prod"
        cmd = [
            self.binary_path,
            "-useConfigPath",
            self.config_path,
            "-log",
            log_level,
            "-useDefaultUploadFolder",
            self.upload_dir,
            "-useReferNetworkInterface",
            self.network_interface,
        ]
        if self.multicast_address:
            cmd.extend(["-useMultcastAddress", self.multicast_address])
        if self.multicast_port > 0:
            cmd.extend(["-useMultcastPort", str(self.multicast_port)])
        if self.skip_notify:
            cmd.append("-skipNotify")
        if self.pin:
            cmd.extend(["-usePin", self.pin])
        if self.alias:
            cmd.extend(["-useAlias", self.alias])
        if self.scan_timeout > 0:
            cmd.extend(["-scanTimeout", str(self.scan_timeout)])
        cmd.append(f"-useAutoSave={'true' if self.auto_save else 'false'}")
        cmd.append(f"-useAutoSaveFromFavorites={'true' if self.auto_save_from_favorites else 'false'}")
        cmd.append(f"-useHttp={'true' if not self.use_https else 'false'}")
        if self.use_download:
            cmd.append("-useDownload")
        if self.do_not_make_session_folder:
            cmd.append("-doNotMakeSessionFolder")

        self.process = subprocess.Popen(
            cmd,
            stdout=self.log_file,
            stderr=self.log_file,
            env=env,
        )
        decky.logger.info(f"localsend backend started with config: {self.config_path}")

    def _stop_backend(self):
        if not self._is_running():
            return
        self.process.terminate()
        for _ in range(20):
            if self.process.poll() is not None:
                break
            time.sleep(0.1)
        if self.process.poll() is None:
            self.process.kill()
        self.process = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        decky.logger.info("localsend backend stopped")

    async def start_backend(self):
        try:
            self.notify_server.start()
            self._start_backend()
            return {"running": True, "url": self.backend_url}
        except Exception as error:
            decky.logger.error(f"failed to start backend: {error}")
            return {"running": False, "error": str(error), "url": self.backend_url}

    async def stop_backend(self):
        self._stop_backend()
        return {"running": False, "url": self.backend_url}

    async def get_backend_status(self):
        return {"running": self._is_running(), "url": self.backend_url}

    async def _proxy_request(self, method: str, path: str, **kwargs):
        if not self._is_running():
            return {"error": "Backend not running"}, 503

        url = f"{self.backend_url}{path}"

        try:
            loop = asyncio.get_event_loop()
            
            # Prepare request data and headers
            data = None
            headers = {}
            
            if 'json' in kwargs:
                data = json.dumps(kwargs['json']).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            elif 'data' in kwargs:
                data = kwargs['data']
                if isinstance(data, str):
                    data = data.encode('utf-8')
            
            if 'headers' in kwargs:
                headers.update(kwargs['headers'])
            
            response_data, status_code, content_type = await loop.run_in_executor(
                None,
                lambda: do_request(method, url, data=data, headers=headers)
            )

            parsed_data = parse_response(response_data, content_type)
            return parsed_data, status_code
        except Exception as e:
            decky.logger.error(f"Proxy request failed: {e}")
            return {"error": str(e)}, 500

    # used in frontend to get data from backend.
    async def proxy_get(self, path: str):
        data, status = await self._proxy_request("GET", path)
        return {"data": data, "status": status}

    # used in frontend to send data to backend.
    async def proxy_post(self, path: str, json_data: dict = None, body: bytes = None):
        kwargs = {}
        if json_data is not None:
            kwargs['json'] = json_data
        if body is not None:
            kwargs['data'] = bytes(body) if isinstance(body, list) else body
            kwargs['headers'] = {'Content-Type': 'application/octet-stream'}

        data, status = await self._proxy_request("POST", path, **kwargs)
        return {"data": data, "status": status}

    # used in frontend to delete data from backend.
    async def proxy_delete(self, path: str):
        data, status = await self._proxy_request("DELETE", path)
        return {"data": data, "status": status}

    # used in frontend to get upload session records.
    async def get_upload_sessions(self):
        """Get upload session records"""
        sessions = []
        for session_id, files in self.upload_sessions.items():
            for file_id, file_data in files.items():
                # Skip metadata entry
                if file_id == '_meta':
                    continue
                sessions.append({
                    'session_id': session_id,
                    **file_data
                })
        # Sort by start time in descending order
        sessions.sort(key=lambda x: x.get('start_time', 0), reverse=True)
        return sessions
    
    # used in frontend to clear upload session records.
    async def clear_upload_sessions(self):
        """Clear upload session records"""
        self.upload_sessions.clear()
        decky.logger.info("Upload session records cleared")
        return {"success": True}
    
    # used in frontend to get notification server status.
    async def get_notify_server_status(self):
        """Get notification server status"""
        return self.notify_server.get_status()

    # Receive history API
    async def get_receive_history(self):
        """Get file receive history"""
        return self.receive_history

    async def get_receive_locations(self):
        """Return named receive locations and the current default."""
        return self._location_payload()

    async def upsert_receive_location(self, location: dict):
        """Create or update a named receive location."""
        if not isinstance(location, dict):
            return {"success": False, "error": "Invalid location"}
        location_id = str(location.get("id") or "").strip()
        name = str(location.get("name") or "").strip()
        if not name:
            return {"success": False, "error": "Location name cannot be empty"}
        if len(name) > 64:
            return {"success": False, "error": "Location name is too long"}
        try:
            path = validate_receive_path(str(location.get("path") or ""), create=True, check_writable=True)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

        name_key = name.casefold()
        for existing in self.receive_locations:
            if existing.get("id") == location_id:
                continue
            if str(existing.get("name", "")).casefold() == name_key:
                return {"success": False, "error": "A location with this name already exists"}
            try:
                if normalize_path(existing.get("path", "")) == path:
                    return {"success": False, "error": "A location with this path already exists"}
            except ValueError:
                continue

        previous_locations = [dict(item) for item in self.receive_locations]
        previous_default_id = self.default_receive_location_id
        previous_upload_dir = self.upload_dir
        first_location = not self.receive_locations
        if location_id:
            existing = self._location_by_id(location_id)
            if existing is None:
                return {"success": False, "error": "Location not found"}
            existing.update({"name": name, "path": path})
        else:
            location_id = f"loc-{uuid.uuid4().hex[:12]}"
            self.receive_locations.append({"id": location_id, "name": name, "path": path})

        if first_location:
            # Keep the invariant even if a caller is recovering from a
            # hand-edited/empty settings list.
            self.default_receive_location_id = location_id

        # Updating the default location also updates the legacy upload_dir
        # mirror and the running Go process.
        if location_id == self.default_receive_location_id:
            self.upload_dir = path
            runtime_result = await self._set_runtime_receive_root(path)
            if not runtime_result.get("success"):
                self.receive_locations = previous_locations
                self.default_receive_location_id = previous_default_id
                self.upload_dir = previous_upload_dir
                return runtime_result
        self._save_settings()
        return {"success": True, **self._location_payload()}

    async def delete_receive_location(self, location_id: str):
        location_id = str(location_id or "").strip()
        if location_id == self.default_receive_location_id:
            return {"success": False, "error": "The default location cannot be deleted"}
        before = len(self.receive_locations)
        self.receive_locations = [item for item in self.receive_locations if item.get("id") != location_id]
        if len(self.receive_locations) == before:
            return {"success": False, "error": "Location not found"}
        self._save_settings()
        return {"success": True, **self._location_payload()}

    async def set_default_receive_location(self, location_id: str):
        location_id = str(location_id or "").strip()
        location = self._location_by_id(location_id)
        if location is None:
            return {"success": False, "error": "Location not found"}
        try:
            path = validate_receive_path(location.get("path", ""), create=True, check_writable=True)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        return await self._set_default_receive_location(location_id, path)

    async def _set_default_receive_location(self, location_id: str, path: str):
        previous_id = self.default_receive_location_id
        previous_path = self.upload_dir
        self.default_receive_location_id = location_id
        self.upload_dir = path
        runtime_result = await self._set_runtime_receive_root(path)
        if not runtime_result.get("success"):
            self.default_receive_location_id = previous_id
            self.upload_dir = previous_path
            return runtime_result
        self._save_settings()
        return {"success": True, **self._location_payload()}

    async def _set_runtime_receive_root(self, path: str):
        if not self._is_running():
            return {"success": True}
        data, status = await self._proxy_request(
            "POST",
            "/api/self/v1/set-receive-root",
            json={"path": path},
        )
        if status != 200:
            return {"success": False, "error": data.get("error", "Failed to update receive root") if isinstance(data, dict) else str(data)}
        return {"success": True}

    async def confirm_receive(
        self,
        session_id: str,
        confirmed: bool,
        destination_id: str = "",
        destination_path: str = "",
    ):
        """Confirm an incoming transfer and optionally choose its destination."""
        if not confirmed:
            destination = ""
        elif destination_id:
            location = self._location_by_id(str(destination_id).strip())
            if location is None:
                return {"success": False, "error": "Location not found"}
            try:
                destination = validate_receive_path(location.get("path", ""), create=True, check_writable=True)
            except (OSError, ValueError) as exc:
                return {"success": False, "error": str(exc)}
        else:
            try:
                destination = validate_receive_path(destination_path, create=True, check_writable=True)
            except (OSError, ValueError) as exc:
                return {"success": False, "error": str(exc)}

        data, status = await self._proxy_request(
            "POST",
            "/api/self/v1/confirm-recv",
            json={
                "sessionId": str(session_id or ""),
                "confirmed": bool(confirmed),
                "destinationPath": destination,
            },
        )
        if status != 200:
            return {
                "success": False,
                "status": status,
                "error": data.get("error", "Confirm request failed") if isinstance(data, dict) else str(data),
            }
        return {"success": True, "status": status, "data": data}

    async def move_receive_history_items(
        self,
        history_id: str,
        selections: Optional[list] = None,
        destination_id: str = "",
        move_entire: bool = False,
    ):
        location = self._location_by_id(str(destination_id or "").strip())
        if location is None:
            return {"success": False, "error": "Named destination not found"}
        try:
            destination = validate_receive_path(location.get("path", ""), create=True, check_writable=True)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}
        result = move_history_items(
            self.receive_history,
            self.receive_history_path,
            str(history_id or ""),
            destination,
            selections=[str(value) for value in (selections or [])],
            move_entire=bool(move_entire),
            logger=lambda msg: decky.logger.warning(msg),
        )
        if result.get("success") or result.get("partial"):
            self._save_receive_history()
        return result

    async def clear_receive_history(self):
        """Clear file receive history"""
        self.receive_history.clear()
        self._save_receive_history()
        decky.logger.info("Receive history cleared")
        return {"success": True}

    async def delete_receive_history_item(self, item_id: str):
        """Delete a single receive history item"""
        original_len = len(self.receive_history)
        self.receive_history = [item for item in self.receive_history if item.get("id") != item_id]
        if len(self.receive_history) < original_len:
            self._save_receive_history()
            decky.logger.info(f"Deleted receive history item: {item_id}")
            return {"success": True}
        return {"success": False, "error": "Item not found"}

    async def get_backend_config(self):
        # If alias not set in JSON, read from YAML config (generated by backend)
        alias = self.alias
        if not alias:
            yaml_config = read_config_yaml(
                self.config_path,
                logger=lambda msg: decky.logger.error(msg)
            )
            alias = str(yaml_config.get("alias", "")).strip()
        return {
            "alias": alias,
            "download_folder": self.upload_dir,
            "legacy_mode": self.legacy_mode,
            "use_mixed_scan": self.use_mixed_scan,
            "skip_notify": self.skip_notify,
            "multicast_address": self.multicast_address,
            "multicast_port": self.multicast_port,
            "pin": self.pin,
            "auto_save": self.auto_save,
            "auto_save_from_favorites": self.auto_save_from_favorites,
            "use_https": self.use_https,
            "network_interface": self.network_interface,
            "notify_on_download": self.notify_on_download,
            "save_receive_history": self.save_receive_history,
            "use_download": self.use_download,
            "do_not_make_session_folder": self.do_not_make_session_folder,
            "disable_info_logging": self.disable_info_logging,
            "scan_timeout": self.scan_timeout,
            "receive_locations": self.receive_locations,
            "default_receive_location_id": self.default_receive_location_id,
        }

    async def set_backend_config(self, config: dict):
        alias = str(config.get("alias", "")).strip()
        download_folder = str(config.get("download_folder", "")).strip()
        legacy_mode = bool(config.get("legacy_mode", False))
        use_mixed_scan = bool(config.get("use_mixed_scan", False))
        skip_notify = bool(config.get("skip_notify", False))
        multicast_address = str(config.get("multicast_address", "")).strip()
        multicast_port_raw = config.get("multicast_port", 0)
        pin = str(config.get("pin", "")).strip()
        auto_save = bool(config.get("auto_save", True))
        auto_save_from_favorites = bool(config.get("auto_save_from_favorites", False))
        use_https = bool(config.get("use_https", True))
        network_interface = str(config.get("network_interface", "*")).strip() or "*"
        notify_on_download = bool(config.get("notify_on_download", False))
        save_receive_history = bool(config.get("save_receive_history", True))
        use_download = bool(config.get("use_download", False))
        do_not_make_session_folder = bool(config.get("do_not_make_session_folder", False))
        disable_info_logging = bool(config.get("disable_info_logging", False))
        scan_timeout_raw = config.get("scan_timeout", 500)
        try:
            scan_timeout = int(scan_timeout_raw or 500)
        except (ValueError, TypeError):
            scan_timeout = 500

        if download_folder:
            try:
                normalized_download_folder = validate_receive_path(
                    download_folder,
                    create=True,
                    check_writable=True,
                )
            except (OSError, ValueError) as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "restarted": False,
                    "running": self._is_running(),
                }
            default_location = self._default_location()
            for location in self.receive_locations:
                if location.get("id") == default_location.get("id"):
                    continue
                try:
                    if normalize_path(location.get("path", "")) == normalized_download_folder:
                        return {
                            "success": False,
                            "error": "A location with this path already exists",
                            "restarted": False,
                            "running": self._is_running(),
                        }
                except ValueError:
                    continue
            default_location["path"] = normalized_download_folder
            self.upload_dir = normalized_download_folder

        self.alias = alias
        self.legacy_mode = False
        self.use_mixed_scan = True
        self.skip_notify = skip_notify
        self.multicast_address = multicast_address
        try:
            self.multicast_port = int(multicast_port_raw or 0)
        except (ValueError, TypeError):
            self.multicast_port = 0
        self.pin = pin
        self.auto_save = auto_save
        self.auto_save_from_favorites = auto_save_from_favorites
        self.use_https = use_https
        self.network_interface = network_interface
        self.notify_on_download = notify_on_download
        self.save_receive_history = save_receive_history
        self.use_download = use_download
        self.do_not_make_session_folder = do_not_make_session_folder
        self.disable_info_logging = disable_info_logging
        self.scan_timeout = scan_timeout

        self._save_settings()
        self._ensure_dirs()

        restarted = False
        try:
            if self._is_running():
                self._stop_backend()
                self._start_backend()
                restarted = True
        except Exception as e:
            decky.logger.error(f"Failed to restart backend: {e}")
            return {
                "success": False,
                "error": str(e),
                "restarted": restarted,
                "running": self._is_running(),
            }

        return {
            "success": True,
            "restarted": restarted,
            "running": self._is_running(),
        }

    # used in frontend to list all files in a folder recursively.
    async def list_folder_files(self, folder_path: str):
        """List all files in a folder recursively, returning their paths relative to the folder"""
        return list_folder_files(
            folder_path,
            logger=lambda msg: decky.logger.error(msg)
        )

    async def write_temp_text_file(self, text_content: str, file_name: str):
        """Write text to a temp file for share session. Returns { success, path? } or { success: false, error? }"""
        share_temp_dir = os.path.join(self.upload_dir, "share_temp")
        return write_temp_text_file(
            base_dir=share_temp_dir,
            text_content=text_content,
            file_name=file_name or "text.txt",
            logger=lambda msg: decky.logger.error(msg)
        )

    async def factory_reset(self):
        """Reset all settings to default and delete config files"""
        try:
            # Stop backend if running
            if self._is_running():
                self._stop_backend()
            
            # Delete plugin settings file
            if os.path.exists(self.settings_path):
                os.remove(self.settings_path)
                decky.logger.info(f"Deleted settings file: {self.settings_path}")
            
            # Delete backend config file
            if os.path.exists(self.config_path):
                os.remove(self.config_path)
                decky.logger.info(f"Deleted config file: {self.config_path}")
            
            # Delete receive history file
            if os.path.exists(self.receive_history_path):
                os.remove(self.receive_history_path)
                decky.logger.info(f"Deleted receive history file: {self.receive_history_path}")
            move_journal_path = f"{self.receive_history_path}.move-journal.json"
            if os.path.exists(move_journal_path):
                os.remove(move_journal_path)
                decky.logger.info(f"Deleted receive move journal: {move_journal_path}")
            
            # Reset instance variables to defaults
            self.alias = ""
            self.legacy_mode = False
            self.use_mixed_scan = True  # Default to Mixed mode
            self.skip_notify = False
            self.multicast_address = "224.0.0.167"
            self.multicast_port = 53317
            self.pin = ""
            self.auto_save = True
            self.auto_save_from_favorites = False
            self.use_https = True
            self.network_interface = "*"
            self.notify_on_download = False
            self.save_receive_history = True
            self.use_download = False
            self.disable_info_logging = True
            self.scan_timeout = 500
            self.upload_dir = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "uploads")
            self.receive_locations = [{
                "id": DEFAULT_LOCATION_ID,
                "name": "Default",
                "path": self.upload_dir,
            }]
            self.default_receive_location_id = DEFAULT_LOCATION_ID
            
            # Clear upload sessions and receive history
            self.upload_sessions.clear()
            self.receive_history.clear()
            
            decky.logger.info("Factory reset completed")
            return {"success": True, "message": "Factory reset completed"}
        except Exception as e:
            decky.logger.error(f"Factory reset failed: {e}")
            return {"success": False, "error": str(e)}



    # BASE decky python-backend.
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self.notify_server.start()
        decky.logger.info("localsend plugin loaded")

    async def _unload(self):
        self._stop_backend()
        self.notify_server.stop()

    async def _uninstall(self):
        self._stop_backend()
        self.notify_server.stop()

    async def _migration(self):
        decky.logger.info("Migrating")
