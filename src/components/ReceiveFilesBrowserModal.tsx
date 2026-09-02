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
import { toaster } from "@decky/api";
import { useMemo, useState } from "react";
import { FaChevronRight, FaFile, FaFolder, FaLevelUpAlt } from "react-icons/fa";
import type { ReceiveHistoryItem, ReceiveLocation } from "../functions/api";
import { moveReceiveHistoryItems } from "../functions/api";
import {
  buildReceiveTree,
  collectReceiveItemPaths,
  formatReceiveSize,
  getReceiveTreeAtPath,
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

  const nodes = getReceiveTreeAtPath(tree, currentPath);
  const breadcrumbs = currentPath ? currentPath.split("/") : [];
  const selectedFiles = useMemo(() => {
    const paths = new Set<string>();
    for (const selection of selected) {
      const node = tree
        .flatMap((rootNode) => [rootNode])
        .find((rootNode) => rootNode.path === selection);
      if (node) collectReceiveItemPaths(node).forEach((path) => paths.add(path));
      (entry.items || []).forEach((item) => {
        if (item.relativePath === selection || item.relativePath.startsWith(`${selection}/`)) {
          paths.add(item.relativePath);
        }
      });
    }
    return paths;
  }, [entry.items, selected, tree]);

  const toggleNode = (node: ReceiveTreeNode) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(node.path)) next.delete(node.path);
      else next.add(node.path);
      return next;
    });
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
    if (!destinationId || (!moveEntire && selected.size === 0)) return;
    setMoving(true);
    try {
      const result = await moveReceiveHistoryItems(
        entry.id,
        Array.from(selected),
        destinationId,
        moveEntire,
      );
      if (result.success || result.partial) {
        toaster.toast({
          title: result.partial ? t("receiveHistory.movePartial") : t("receiveHistory.moveComplete"),
          body: result.failures?.length
            ? result.failures.map((failure) => `${failure.selection}: ${failure.error}`).join("; ")
            : `${result.moved?.length || selectedFiles.size} ${t("common.files")}`,
        });
        await onMoved();
        closeModal();
      } else {
        toaster.toast({ title: t("common.failed"), body: result.error || t("receiveHistory.moveFailed") });
      }
    } catch (error) {
      toaster.toast({ title: t("common.failed"), body: String(error) });
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
        <div style={{ color: "#b8b6b4", fontSize: "12px", marginBottom: "8px" }}>
          {entry.destinationName || entry.destinationPath || entry.folderPath}
        </div>
        <Focusable style={{ display: "flex", alignItems: "center", gap: "4px", marginBottom: "8px" }}>
          <DialogButton
            onClick={() => setCurrentPath("")}
            style={{ minWidth: "36px", padding: "4px 8px" }}
          >
            <FaFolder size={11} />
          </DialogButton>
          {breadcrumbs.map((part, index) => (
            <div key={`${part}-${index}`} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              <FaChevronRight size={9} />
              <DialogButton onClick={() => navigateToBreadcrumb(index)} style={{ padding: "4px 8px" }}>
                {part}
              </DialogButton>
            </div>
          ))}
        </Focusable>
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
              return (
                <div
                  key={node.path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "6px",
                    padding: "6px 4px",
                    borderBottom: "1px solid #3d3d3d",
                    background: isSelected ? "rgba(26,159,255,0.18)" : undefined,
                  }}
                >
                  <button
                    onClick={() => toggleNode(node)}
                    aria-label={isSelected ? t("receiveHistory.deselect") : t("receiveHistory.select")}
                    style={{ width: "22px", height: "22px", padding: 0 }}
                  >
                    {isSelected ? "☑" : "☐"}
                  </button>
                  <button
                    onClick={() => node.kind === "folder" && setCurrentPath(node.path)}
                    disabled={node.kind !== "folder"}
                    style={{
                      flex: 1,
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      textAlign: "left",
                      background: "transparent",
                      border: 0,
                      color: "#e8e8e8",
                      padding: 0,
                    }}
                  >
                    {node.kind === "folder" ? <FaFolder size={12} color="#4a9eff" /> : <FaFile size={12} color="#b8b6b4" />}
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {node.name}
                    </span>
                    <span style={{ color: "#888", fontSize: "10px", marginLeft: "auto" }}>
                      {node.kind === "file"
                        ? `${formatReceiveSize(node.size || 0)}${node.modifiedAt ? ` · ${new Date(node.modifiedAt * 1000).toLocaleString()}` : ""}${node.item?.currentPath ? ` · ${node.item.currentPath}` : ""}`
                        : `${collectReceiveItemPaths(node).length} ${t("common.files")}`}
                    </span>
                  </button>
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
      </DialogBody>
      <DialogFooter>
        <DialogButton onClick={closeModal} disabled={moving}>{t("common.cancel")}</DialogButton>
        <DialogButton onClick={() => move(false)} disabled={moving || selected.size === 0 || !destinationId}>
          {t("receiveHistory.moveSelected")}
        </DialogButton>
        <DialogButtonPrimary onClick={() => move(true)} disabled={moving || !destinationId}>
          {t("receiveHistory.moveEntire")}
        </DialogButtonPrimary>
      </DialogFooter>
    </ModalRoot>
  );
};
