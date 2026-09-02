import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const typescript = require("typescript");
const sourcePath = new URL("../src/utils/receiveTree.ts", import.meta.url);
const source = await readFile(sourcePath, "utf8");
const transpiled = typescript.transpileModule(source, {
  compilerOptions: { module: typescript.ModuleKind.CommonJS, target: typescript.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} };
vm.runInNewContext(transpiled, { module, exports: module.exports }, { filename: sourcePath.pathname });
const {
  buildReceiveTree,
  collectReceiveItemPaths,
  compactReceiveSelections,
  getReceiveTreeAtPath,
  normalizeReceiveRelativePath,
  toggleReceiveSelection,
} = module.exports;

const items = [
  { itemId: "1", relativePath: "photos/2026/a.jpg", currentPath: "/receive/photos/2026/a.jpg", size: 10 },
  { itemId: "2", relativePath: "photos/2026/b.jpg", currentPath: "/receive/photos/2026/b.jpg", size: 20 },
  { itemId: "3", relativePath: "notes.txt", currentPath: "/receive/notes.txt", size: 5 },
];

test("derives sorted folders and files from a manifest", () => {
  const tree = buildReceiveTree(items);
  assert.equal(Array.from(tree, (node) => `${node.kind}:${node.path}`).join(","), "folder:photos,file:notes.txt");
  assert.equal(Array.from(getReceiveTreeAtPath(tree, "photos/2026"), (node) => node.path).join(","), "photos/2026/a.jpg,photos/2026/b.jpg");
  assert.equal(Array.from(collectReceiveItemPaths(tree[0])).join(","), "photos/2026/a.jpg,photos/2026/b.jpg");
});

test("normalizes slash variants and rejects traversal", () => {
  assert.equal(normalizeReceiveRelativePath("./photos\\2026\\a.jpg"), "photos/2026/a.jpg");
  assert.throws(() => normalizeReceiveRelativePath("../secret.txt"));
  assert.throws(() => getReceiveTreeAtPath(buildReceiveTree(items), "../secret"));
});

test("overlapping folder/file selections can be compacted by their paths", () => {
  assert.deepEqual(
    Array.from(compactReceiveSelections(["photos", "photos/2026/a.jpg", "notes.txt"])),
    ["notes.txt", "photos"],
  );
});

test("individual files toggle independently and survive navigation", () => {
  let selected = toggleReceiveSelection(new Set(), "photos/2026/a.jpg");
  selected = toggleReceiveSelection(selected, "photos/2026/b.jpg");
  assert.deepEqual(Array.from(selected).sort(), ["photos/2026/a.jpg", "photos/2026/b.jpg"]);

  selected = toggleReceiveSelection(selected, "photos/2026/a.jpg");
  assert.deepEqual(Array.from(selected), ["photos/2026/b.jpg"]);
  assert.deepEqual(Array.from(getReceiveTreeAtPath(buildReceiveTree(items), "photos/2026"), (node) => node.path), [
    "photos/2026/a.jpg",
    "photos/2026/b.jpg",
  ]);
});

test("folder selections remain recursive while child selections compact", () => {
  const folder = buildReceiveTree(items)[0];
  assert.deepEqual(Array.from(collectReceiveItemPaths(folder)), ["photos/2026/a.jpg", "photos/2026/b.jpg"]);
  assert.deepEqual(Array.from(compactReceiveSelections(["photos", "photos/2026/b.jpg"])), ["photos"]);
});
