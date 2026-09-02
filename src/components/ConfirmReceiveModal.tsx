import {
  DialogBody,
  DialogButton,
  DialogButtonPrimary,
  DialogFooter,
  DialogHeader,
  Focusable,
  ModalRoot,
  DropdownItem,
} from "@decky/ui";
import { FileSelectionType, openFilePicker } from "@decky/api";
import { useEffect, useMemo, useState } from "react";
import type { ReceiveLocation } from "../functions/api";
import { t } from "../i18n";

interface ConfirmReceiveModalProps {
  title?: string;
  from: string;
  fileCount: number;
  files: { fileName: string; size?: number; fileType?: string }[];
  totalFiles?: number;
  locations?: ReceiveLocation[];
  defaultLocationId?: string;
  expiresAt?: number;
  onConfirm: (confirmed: boolean, destinationId?: string, destinationPath?: string) => void;
  closeModal?: () => void;
}

export const ConfirmReceiveModal = ({
  title,
  from,
  fileCount,
  files,
  totalFiles,
  locations = [],
  defaultLocationId,
  expiresAt,
  onConfirm,
  closeModal,
}: ConfirmReceiveModalProps) => {
  const fallbackLocationId =
    (defaultLocationId && locations.some((location) => location.id === defaultLocationId) ? defaultLocationId : undefined) ||
    locations.find((location) => location.isDefault)?.id ||
    locations[0]?.id ||
    "";
  const [selectedLocationId, setSelectedLocationId] = useState(fallbackLocationId);
  const [customPath, setCustomPath] = useState("");
  const [remainingSeconds, setRemainingSeconds] = useState(() =>
    expiresAt ? Math.max(0, Math.ceil(expiresAt - Date.now() / 1000)) : 0,
  );
  const expired = expiresAt != null && remainingSeconds <= 0;
  const hasDestination = Boolean(customPath || selectedLocationId);

  useEffect(() => {
    if (expiresAt == null) return;
    const timer = window.setInterval(() => {
      setRemainingSeconds(Math.max(0, Math.ceil(expiresAt - Date.now() / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  const locationOptions = useMemo(
    () => locations.map((location) => ({
      data: location.id,
      label: `${location.name}${location.isDefault ? ` (${t("receiveLocations.default")})` : ""}`,
    })),
    [locations],
  );

  const handleConfirm = (confirmed: boolean) => {
    closeModal?.();
    if (!confirmed) {
      onConfirm(false);
      return;
    }
    onConfirm(true, customPath ? undefined : selectedLocationId, customPath || undefined);
  };

  const handleBrowse = async () => {
    try {
      const result = await openFilePicker(
        FileSelectionType.FOLDER,
        locations.find((location) => location.id === selectedLocationId)?.path || "/home/deck",
      );
      const path = result.realpath ?? result.path;
      if (path) {
        setCustomPath(path);
        setSelectedLocationId("");
      }
    } catch {
      // Decky rejects the picker promise when the user cancels; no toast is
      // needed for that normal interaction.
    }
  };

  return (
    <ModalRoot onCancel={() => handleConfirm(false)} closeModal={closeModal}>
      <DialogHeader>{title || t("confirmReceive.title")}</DialogHeader>
      <DialogBody>
        <div style={{ marginBottom: "10px", fontSize: "12px", color: "#b8b6b4" }}>
          {t("confirmReceive.from")}: <strong>{from || "Unknown"}</strong> ({fileCount} {t("common.files")})
        </div>
        <div style={{ marginBottom: "10px" }}>
          {locations.length > 0 && (
            <DropdownItem
              label={t("confirmReceive.destination")}
              rgOptions={locationOptions}
              selectedOption={selectedLocationId}
              onChange={(option) => {
                setSelectedLocationId(String(option.data));
                setCustomPath("");
              }}
              disabled={expired}
            />
          )}
          <DialogButton onClick={handleBrowse} disabled={expired} style={{ marginTop: "6px" }}>
            {t("confirmReceive.browse")}
          </DialogButton>
          {customPath && (
            <div style={{ color: "#8fc7ff", fontSize: "11px", marginTop: "5px", wordBreak: "break-all" }}>
              {customPath}
            </div>
          )}
        </div>
        {expiresAt != null && (
          <div style={{ color: expired ? "#ff6b6b" : "#b8b6b4", fontSize: "11px", marginBottom: "8px" }}>
            {expired
              ? t("confirmReceive.expired")
              : t("confirmReceive.expiresIn").replace("{seconds}", String(remainingSeconds))}
          </div>
        )}
        {files.length > 0 && (
          <Focusable style={{ maxHeight: "240px", overflowY: "auto" }}>
            {files.map((file, idx) => (
              <div key={`${file.fileName}-${idx}`} style={{ padding: "4px 0", fontSize: "12px" }}>
                {file.fileName}
                {typeof file.size === "number" ? ` (${file.size} bytes)` : ""}
              </div>
            ))}
            {totalFiles != null && totalFiles > files.length && (
              <div style={{ marginTop: "6px", color: "#8a8a8a", fontSize: "12px" }}>
                {t("fileReceived.andMoreFiles").replace("{count}", String(totalFiles - files.length))}
              </div>
            )}
          </Focusable>
        )}
      </DialogBody>
      <DialogFooter>
        <DialogButton onClick={() => handleConfirm(false)} style={{marginTop: "10px"}}>{t("confirmReceive.reject")}</DialogButton>
        <DialogButtonPrimary onClick={() => handleConfirm(true)} disabled={expired || !hasDestination} style={{marginTop: "10px"}}>{t("confirmReceive.accept")}</DialogButtonPrimary>
      </DialogFooter>
    </ModalRoot>
  );
};
