import { callable } from "@decky/api";
import type { BackendStatus } from "../types/backend";

// Backend API
export const startBackend = callable<[], BackendStatus>("start_backend");
export const stopBackend = callable<[], BackendStatus>("stop_backend");
export const getBackendStatus = callable<[], BackendStatus>("get_backend_status");

// File API
export const writeTempTextFile = callable<
  [string, string],
  { success: boolean; path?: string; error?: string }
>("write_temp_text_file");

export const listFolderFiles = callable<
  [string],
  {
    success: boolean;
    files: Array<{ path: string; displayPath: string; fileName: string }>;
    folderName?: string;
    count?: number;
    error?: string;
  }
>("list_folder_files");

// Upload Sessions API
export const getUploadSessions = callable<[], any[]>("get_upload_sessions");
export const clearUploadSessions = callable<[], { success: boolean }>("clear_upload_sessions");

// Notification API
export const getNotifyServerStatus = callable<
  [],
  { running: boolean; socket_path: string; socket_exists: boolean }
>("get_notify_server_status");

// Factory Reset API
export const factoryReset = callable<
  [],
  { success: boolean; message?: string; error?: string }
>("factory_reset");

// Receive History API
export interface ReceiveHistoryItem {
  id: string;
  timestamp: number;
  title: string;
  folderPath: string;
  fileCount: number;
  files: string[];
  isText?: boolean;
  textPreview?: string;
  textContent?: string;
  totalFiles?: number;
  successFiles?: number;
  failedFiles?: number;
  failedFileIds?: string[];
  manifestVersion?: number;
  items?: ReceiveManifestItem[];
  destinationId?: string;
  destinationName?: string;
  destinationPath?: string;
  receiveLayout?: "flat" | "session" | string;
  sessionId?: string;
}

export interface ReceiveManifestItem {
  itemId: string;
  relativePath: string;
  currentPath: string;
  size: number;
  modifiedAt?: number;
}

export const getReceiveHistory = callable<[], ReceiveHistoryItem[]>("get_receive_history");

export const clearReceiveHistory = callable<[], { success: boolean }>("clear_receive_history");

export const deleteReceiveHistoryItem = callable<
  [string],
  { success: boolean; error?: string }
>("delete_receive_history_item");

export interface ReceiveLocation {
  id: string;
  name: string;
  path: string;
  isDefault?: boolean;
  available?: boolean;
}

export interface ReceiveLocationsResult {
  locations: ReceiveLocation[];
  defaultId: string;
}

export const getReceiveLocations = callable<[], ReceiveLocationsResult>("get_receive_locations");

export const upsertReceiveLocation = callable<
  [{ id?: string; name: string; path: string }],
  { success: boolean; error?: string; locations?: ReceiveLocation[]; defaultId?: string }
>("upsert_receive_location");

export const deleteReceiveLocation = callable<
  [string],
  { success: boolean; error?: string; locations?: ReceiveLocation[]; defaultId?: string }
>("delete_receive_location");

export const setDefaultReceiveLocation = callable<
  [string],
  { success: boolean; error?: string; locations?: ReceiveLocation[]; defaultId?: string }
>("set_default_receive_location");

export const confirmReceive = callable<
  [string, boolean, string?, string?],
  { success: boolean; status?: number; error?: string; data?: unknown }
>("confirm_receive");

export const moveReceiveHistoryItems = callable<
  [string, string[], string, boolean?],
  {
    success: boolean;
    partial?: boolean;
    error?: string;
    moved?: string[];
    skipped?: string[];
    selected?: string[];
    failures?: Array<{ selection: string; error: string }>;
  }
>("move_receive_history_items");

// Backend Config API
export const getBackendConfig = callable<
  [],
  {
    alias: string;
    download_folder: string;
    legacy_mode: boolean;
    use_mixed_scan: boolean;
    skip_notify: boolean;
    multicast_address: string;
    multicast_port: number;
    pin: string;
    auto_save: boolean;
    auto_save_from_favorites: boolean;
    use_https: boolean;
    network_interface: string;
    notify_on_download: boolean;
    save_receive_history: boolean;
    enable_experimental: boolean;
    use_download: boolean;
    do_not_make_session_folder: boolean;
    disable_info_logging: boolean;
    scan_timeout: number;
    receive_locations?: ReceiveLocation[];
    default_receive_location_id?: string;
  }
>("get_backend_config");

export const setBackendConfig = callable<
  [
    {
      alias: string;
      download_folder: string;
      legacy_mode: boolean;
      use_mixed_scan: boolean;
      skip_notify: boolean;
      multicast_address: string;
      multicast_port: number | string;
      pin: string;
      auto_save: boolean;
      auto_save_from_favorites: boolean;
      use_https: boolean;
      network_interface: string;
      notify_on_download: boolean;
      save_receive_history: boolean;
      enable_experimental: boolean;
      use_download: boolean;
      do_not_make_session_folder?: boolean;
      disable_info_logging: boolean;
      scan_timeout: number | string;
    }
  ],
  { success: boolean; restarted: boolean; running: boolean; error?: string }
>("set_backend_config");
