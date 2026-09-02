import {
  DialogBody,
  DialogButton,
  DialogButtonPrimary,
  DialogFooter,
  DialogHeader,
  DropdownItem,
  Focusable,
  ModalRoot,
} from "@decky/ui";
import { useMemo, useState } from "react";
import { FaChevronRight, FaFile, FaFolder, FaLevelUpAlt } from "react-icons/fa";
import type { ReceiveHistoryItem, ReceiveLocation } from "../functions/api";
import { moveReceiveHistoryItems } from "../functions/api";
import {
  buildReceiveTree,
  compactReceiveSelections,
  collectReceiveItemPaths,
  formatReceiveSize,
  getReceiveTreeAtPath,
  toggleReceiveSelection,
  type ReceiveTreeNode,
} from "../utils/receiveTree";
import { t } from "../i18n";

interface ReceiveFilesBrowserModalProps {
  entry: ReceiveHistoryItem;
  locations: ReceiveLocation[];
  onMoved: () => Promise<void> | void;
  closeModal: () => void;
}

const destinationOptions = (locations: ReceiveLocation[]) =>
  locations.map((location) => ({
    data: location.id,
    label: `${location.name}${location.isDefault ? ` (${t("receiveLocations.default")})` : ""}`,
  }));

type MoveNotice = {
  title: string;
  body: string;
  tone: "success" | "partial" | "error";
};

export const ReceiveFilesBrowserModal = ({
  entry,
  locations,
  onMoved,
  closeModal,
}: ReceiveFilesBrowserModalProps) => {
  const tree = useMemo(() => buildReceiveTree(entry.items || []), [entry.items]);
  const [currentPath, setCurrentPath] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [destinationId, setDestinationId] = useState(
    locations.find((location) => location.isDefault)?.id || locations[0]?.id || "",
  );
  const [moving, setMoving] = useState(false);
  const [moveNotice, setMoveNotice] = useState<MoveNotice | null>(null);

  const nodes = getReceiveTreeAtPath(tree, currentPath);
  const breadcrumbs = currentPath ? currentPath.split("/") : [];
  const selectedFiles = useMemo(() => {
    const paths = new Set<string>();
    for (const selection of compactReceiveSelections(selected)) {
      (entry.items || []).forEach((item) => {
        if (item.relativePath === selection || item.relativePath.startsWith(`${selection}/`)) {
          paths.add(item.relativePath);
        }
      });
    }
    return paths;
  }, [entry.items, selected, tree]);

  const toggleNode = (node: ReceiveTreeNode) => {
    setSelected((previous) => toggleReceiveSelection(previous, node.path));
  };

  const selectVisible = () => {
    setSelected((previous) => {
      const next = new Set(previous);
      const allVisible = nodes.every((node) => next.has(node.path));
      nodes.forEach((node) => (allVisible ? next.delete(node.path) : next.add(node.path)));
      return next;
    });
  };

  const move = async (moveEntire: boolean) => {
    const selections = compactReceiveSelections(selected);
    if (!destinationId || (!moveEntire && selections.length === 0)) return;
    setMoving(true);
    try {
      const result = await moveReceiveHistoryItems(
        entry.id,
        selections,
        destinationId,
        moveEntire,
      );
      if (result.success || result.partial) {
        const movedCount = Array.isArray(result.moved) ? result.moved.length : selectedFiles.size;
        const failureDetails = result.failures?.length
          ? result.failures.map((failure) => `${failure.selection}: ${failure.error}`).join("; ")
          : "";
        const title = result.partial ? t("receiveHistory.movePartial") : t("common.success");
        const body = result.partial
          ? `${movedCount} ${t("common.files")}${failureDetails ? `; ${failureDetails}` : ""}`
          : `${t("receiveHistory.moveComplete")}: ${movedCount} ${t("common.files")}`;

        setMoveNotice({ title, body, tone: result.partial ? "partial" : "success" });
        try {
          await onMoved();
        } catch (refreshError) {
          console.warn("Receive history refresh failed after move:", refreshError);
        }
      } else {
        setMoveNotice({
          title: t("common.failed"),
          body: result.error || t("receiveHistory.moveFailed"),
          tone: "error",
        });
      }
    } catch (error) {
      setMoveNotice({ title: t("common.failed"), body: String(error), tone: "error" });
    } finally {
      setMoving(false);
    }
  };

  const navigateToBreadcrumb = (index: number) => {
    setCurrentPath(breadcrumbs.slice(0, index + 1).join("/"));
  };

  return (
    <ModalRoot onCancel={closeModal} closeModal={closeModal}>
      <DialogHeader>{t("receiveHistory.browseTitle")}</DialogHeader>
      <DialogBody>
        {moveNotice ? (
          <Focusable
            role="status"
            style={{
              padding: "14px",
              borderRadius: "6px",
              border: `1px solid ${moveNotice.tone === "success" ? "#4caf50" : moveNotice.tone === "partial" ? "#d99a28" : "#d9534f"}`,
              backgroundColor: moveNotice.tone === "success"
                ? "rgba(76,175,80,0.14)"
                : moveNotice.tone === "partial"
                  ? "rgba(217,154,40,0.14)"
                  : "rgba(217,83,79,0.14)",
            }}
          >
            <div style={{ fontSize: "14px", fontWeight: "bold", color: "#e8e8e8" }}>
              {moveNotice.title}
            </div>
            <div
              style={{
                marginTop: "8px",
                color: "#b8b6b4",
                fontSize: "12px",
                lineHeight: "1.45",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                wordBreak: "break-word",
              }}
            >
              {moveNotice.body}
            </div>
          </Focusable>
        ) : (
          <>
            <div style={{ color: "#b8b6b4", fontSize: "12px", marginBottom: "8px" }}>
              {entry.destinationName || entry.destinationPath || entry.folderPath}
            </div>
            {breadcrumbs.length > 0 && (
              <Focusable style={{ display: "flex", alignItems: "center", gap: "4px", marginBottom: "8px" }}>
                {breadcrumbs.map((part, index) => (
                  <div key={`${part}-${index}`} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                    <FaChevronRight size={9} />
                    <DialogButton onClick={() => navigateToBreadcrumb(index)} style={{ padding: "4px 8px" }}>
                      {part}
                    </DialogButton>
                  </div>
                ))}
              </Focusable>
            )}
            {currentPath && (
              <DialogButton
                onClick={() => setCurrentPath(breadcrumbs.slice(0, -1).join("/"))}
                style={{ marginBottom: "8px" }}
              >
                <FaLevelUpAlt size={11} /> {t("receiveHistory.up")}
              </DialogButton>
            )}
            <Focusable style={{ maxHeight: "270px", overflowY: "auto" }}>
              {nodes.length === 0 ? (
                <div style={{ color: "#888", fontSize: "12px", padding: "12px" }}>
                  {t("receiveHistory.noFilesInEntry")}
                </div>
              ) : (
                nodes.map((node) => {
                  const isSelected = selected.has(node.path);
                  const metadata = node.kind === "file"
                    ? [
                        node.item?.currentPath || node.path,
                        formatReceiveSize(node.size || 0),
                        typeof node.modifiedAt === "number"
                          ? new Date(node.modifiedAt * 1000).toLocaleString()
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : `${collectReceiveItemPaths(node).length} ${t("common.files")}`;
                  const nodeContent = (
                    <div style={{ width: "100%", minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: "6px",
                          width: "100%",
                          minWidth: 0,
                        }}
                      >
                        {node.kind === "folder" ? (
                          <FaFolder size={12} color="#4a9eff" style={{ flexShrink: 0, marginTop: "2px" }} />
                        ) : (
                          <FaFile size={12} color="#b8b6b4" style={{ flexShrink: 0, marginTop: "2px" }} />
                        )}
                        <span
                          style={{
                            flex: 1,
                            minWidth: 0,
                            width: "100%",
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            lineHeight: "1.35",
                          }}
                        >
                          {node.name}
                        </span>
                      </div>
                      <div
                        style={{
                          width: "100%",
                          minWidth: 0,
                          marginTop: "3px",
                          color: "#888",
                          fontSize: "10px",
                          lineHeight: "1.35",
                          whiteSpace: "normal",
                          overflowWrap: "anywhere",
                          wordBreak: "break-word",
                        }}
                      >
                        {metadata}
                      </div>
                    </div>
                  );
                  return (
                    <div
                      key={node.path}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        padding: "6px 4px",
                        width: "100%",
                        minWidth: 0,
                        boxSizing: "border-box",
                        borderBottom: "1px solid #3d3d3d",
                        background: isSelected ? "rgba(26,159,255,0.18)" : undefined,
                      }}
                    >
                      <DialogButton
                        onClick={() => toggleNode(node)}
                        disabled={moving}
                        focusable={!moving}
                        onOKActionDescription={isSelected ? t("receiveHistory.deselect") : t("receiveHistory.select")}
                        style={{
                          flexShrink: 0,
                          minWidth: "30px",
                          width: "30px",
                          height: "30px",
                          padding: "3px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: "16px",
                        }}
                      >
                        {isSelected ? "☑" : "☐"}
                      </DialogButton>
                      {node.kind === "folder" ? (
                        <DialogButton
                          onClick={() => setCurrentPath(node.path)}
                          disabled={moving}
                          focusable={!moving}
                          style={{
                            flex: 1,
                            minWidth: 0,
                            width: "100%",
                            padding: 0,
                            display: "block",
                            textAlign: "left",
                            background: "transparent",
                            border: 0,
                            color: "#e8e8e8",
                            overflow: "hidden",
                          }}
                        >
                          {nodeContent}
                        </DialogButton>
                      ) : (
                        <div style={{ flex: 1, minWidth: 0, width: "100%", color: "#e8e8e8" }}>
                          {nodeContent}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </Focusable>
            <DialogButton onClick={selectVisible} style={{ marginTop: "8px" }} disabled={!nodes.length}>
              {t("receiveHistory.selectVisible")}
            </DialogButton>
            <div style={{ marginTop: "10px" }}>
              <DropdownItem
                label={t("receiveHistory.moveDestination")}
                rgOptions={destinationOptions(locations)}
                selectedOption={destinationId}
                onChange={(option) => setDestinationId(String(option.data))}
                disabled={moving || locations.length === 0}
              />
            </div>
            <div style={{ color: "#888", fontSize: "11px", marginTop: "6px" }}>
              {selectedFiles.size} {t("receiveHistory.selectedFiles")}
            </div>
          </>
        )}
      </DialogBody>
      <DialogFooter>
        {moveNotice ? (
          <DialogButtonPrimary onClick={closeModal} disabled={moving}>
            {t("fileReceived.close")}
          </DialogButtonPrimary>
        ) : (
          <>
            <DialogButton onClick={closeModal} disabled={moving}>{t("common.cancel")}</DialogButton>
            <DialogButton onClick={() => move(false)} disabled={moving || selected.size === 0 || !destinationId}>
              {t("receiveHistory.moveSelected")}
            </DialogButton>
            <DialogButtonPrimary onClick={() => move(true)} disabled={moving || !destinationId}>
              {t("receiveHistory.moveEntire")}
            </DialogButtonPrimary>
          </>
        )}
      </DialogFooter>
    </ModalRoot>
  );
};
