"use strict";

// 使用 Node 内置模块检查实际网页存储桥接，不安装测试框架。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const bridgePath = path.join(__dirname, "../Assets/Plugins/WebGL/SandboxBrowser.jslib");
const entries = new Map();
let denyRead = false;
let denyWrite = false;
let count = 0;
const storage = {
  get length() { if (denyRead) throw new Error("存储拒绝访问"); return entries.size; },
  key(index) { return [...entries.keys()][index]; },
  getItem(key) { if (denyRead) throw new Error("存储拒绝访问"); return entries.get(key) ?? null; },
  setItem(key, value) { if (denyWrite) throw new Error("配额不足"); entries.set(key, value); }
};
const context = {
  LibraryManager: { library: {} },
  mergeInto: (target, values) => Object.assign(target, values),
  window: { location: { pathname: "/sandbox/index.html" }, localStorage: storage },
  UTF8ToString: value => value
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(bridgePath, "utf8"), context, { filename: bridgePath });
const bridge = context.LibraryManager.library;
context.QiongtuStoragePrefix = bridge.$QiongtuStoragePrefix;
context.QiongtuReturnString = value => value;
const saveId = "0123456789abcdef0123456789abcdef";
const secondId = "fedcba9876543210fedcba9876543210";
const content = JSON.stringify({ name: "青岚河谷 · 浏览器存档", snapshot: { step: 18 } });

function check(name, callback) {
  callback();
  count++;
  process.stdout.write("通过：" + name + "\n");
}

check("中文内容写入并原样读回", () => {
  assert.equal(bridge.QiongtuStorageWrite(saveId, content), "");
  const result = JSON.parse(bridge.QiongtuStorageRead());
  assert.equal(result.records.length, 1);
  assert.equal(result.records[0].content, content);
});
check("同编号不覆盖已有存档", () => {
  assert.notEqual(bridge.QiongtuStorageWrite(saveId, "替换内容"), "");
  assert.equal(JSON.parse(bridge.QiongtuStorageRead()).records[0].content, content);
});
check("不同部署目录的存档隔离", () => {
  context.window.location.pathname = "/other/index.html";
  assert.equal(JSON.parse(bridge.QiongtuStorageRead()).records.length, 0);
  context.window.location.pathname = "/sandbox/";
  assert.equal(JSON.parse(bridge.QiongtuStorageRead()).records.length, 1);
});
check("保留同源其他应用的数据", () => {
  entries.set("other-app", "其他应用数据");
  assert.equal(JSON.parse(bridge.QiongtuStorageRead()).records.length, 1);
  assert.equal(entries.get("other-app"), "其他应用数据");
});
check("无效编号不能写入", () => {
  assert.notEqual(bridge.QiongtuStorageWrite("../invalid", content), "");
  assert.equal(entries.size, 2);
});
check("配额不足明确失败且保留历史记录", () => {
  denyWrite = true;
  assert.notEqual(bridge.QiongtuStorageWrite(secondId, content), "");
  assert.equal(entries.size, 2);
  denyWrite = false;
});
check("读取被禁用时返回提示", () => {
  denyRead = true;
  const result = JSON.parse(bridge.QiongtuStorageRead());
  assert.equal(result.records.length, 0);
  assert.notEqual(result.warning, "");
  assert.notEqual(bridge.QiongtuStorageWrite(secondId, content), "");
  denyRead = false;
});
check("损坏的业务内容保留给 C# 校验而不删除", () => {
  entries.set(context.QiongtuStoragePrefix() + secondId, "{损坏记录");
  const result = JSON.parse(bridge.QiongtuStorageRead());
  assert.equal(result.records.length, 2);
  assert.equal(entries.size, 3);
});
context.QiongtuSettingsKey = bridge.$QiongtuSettingsKey;
check("设置可更新且不覆盖游戏快照", () => {
  assert.equal(bridge.QiongtuSettingsWrite('{"formatVersion":1,"showGrid":true}'), "");
  assert.equal(bridge.QiongtuSettingsWrite('{"formatVersion":1,"showGrid":false}'), "");
  assert.equal(JSON.parse(JSON.parse(bridge.QiongtuSettingsRead()).content).showGrid, false);
  assert.equal(JSON.parse(bridge.QiongtuStorageRead()).records.length, 2);
});
check("设置按部署目录隔离", () => {
  context.window.location.pathname = "/other/index.html";
  assert.equal(JSON.parse(bridge.QiongtuSettingsRead()).content, "");
  context.window.location.pathname = "/sandbox/";
  assert.notEqual(JSON.parse(bridge.QiongtuSettingsRead()).content, "");
});
check("设置写入配额失败时保留上次内容", () => {
  const before = JSON.parse(bridge.QiongtuSettingsRead()).content;
  denyWrite = true;
  assert.notEqual(bridge.QiongtuSettingsWrite("replacement"), "");
  denyWrite = false;
  assert.equal(JSON.parse(bridge.QiongtuSettingsRead()).content, before);
});
check("设置读取被禁用时返回提示且不清空记录", () => {
  denyRead = true;
  assert.notEqual(JSON.parse(bridge.QiongtuSettingsRead()).warning, "");
  denyRead = false;
  assert.notEqual(JSON.parse(bridge.QiongtuSettingsRead()).content, "");
});
process.stdout.write("网页存储桥接检查通过：" + count + " 项；Unity 互操作及刷新恢复仍须浏览器运行验收。\n");
