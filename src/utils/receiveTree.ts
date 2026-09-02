import type { ReceiveManifestItem } from "../functions/api";

export type ReceiveTreeNode = {
  path: string;
  name: string;
  kind: "folder" | "file";
  size?: number;
  modifiedAt?: number;
  item?: ReceiveManifestItem;
  children?: ReceiveTreeNode[];
};

export const normalizeReceiveRelativePath = (value: string): string => {
  const normalized = String(value || "")
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part && part !== ".")
    .join("/");
  if (!normalized || normalized.split("/").some((part) => part === "..")) {
    throw new Error("Invalid receive manifest path");
  }
  return normalized;
};

export const buildReceiveTree = (items: ReceiveManifestItem[]): ReceiveTreeNode[] => {
  const root: ReceiveTreeNode[] = [];
  const folders = new Map<string, ReceiveTreeNode>();

  const ensureFolder = (path: string): ReceiveTreeNode => {
    const existing = folders.get(path);
    if (existing) return existing;
    const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const folder: ReceiveTreeNode = {
      path,
      name: path.slice(path.lastIndexOf("/") + 1),
      kind: "folder",
      children: [],
    };
    folders.set(path, folder);
    if (parentPath) {
      ensureFolder(parentPath).children!.push(folder);
    } else {
      root.push(folder);
    }
    return folder;
  };

  for (const item of items) {
    const relativePath = normalizeReceiveRelativePath(item.relativePath);
    const parts = relativePath.split("/");
    const file: ReceiveTreeNode = {
      path: relativePath,
      name: parts[parts.length - 1],
      kind: "file",
      size: item.size,
      modifiedAt: item.modifiedAt,
      item,
    };
    if (parts.length === 1) {
      root.push(file);
    } else {
      ensureFolder(parts.slice(0, -1).join("/")).children!.push(file);
    }
  }

  const sortNodes = (nodes: ReceiveTreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === "folder" ? -1 : 1;
      return left.name.localeCompare(right.name);
    });
    nodes.forEach((node) => node.children && sortNodes(node.children));
  };
  sortNodes(root);
  return root;
};

export const getReceiveTreeAtPath = (root: ReceiveTreeNode[], path: string): ReceiveTreeNode[] => {
  if (!path) return root;
  const parts = normalizeReceiveRelativePath(path).split("/");
  let nodes = root;
  let current: ReceiveTreeNode | undefined;
  for (const part of parts) {
    current = nodes.find((node) => node.kind === "folder" && node.name === part);
    if (!current) return [];
    nodes = current.children || [];
  }
  return nodes;
};

export const collectReceiveItemPaths = (node: ReceiveTreeNode): string[] => {
  if (node.kind === "file") return [node.path];
  return (node.children || []).flatMap(collectReceiveItemPaths);
};

export const formatReceiveSize = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes < 1024) return `${Math.max(0, bytes || 0)} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let index = -1;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
};
