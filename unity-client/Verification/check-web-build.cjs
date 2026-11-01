"use strict";

// 只通过 HTTP 和二进制格式校验网页产物，不启动或操作浏览器。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const zlib = require("node:zlib");
const crypto = require("node:crypto");

async function main() {
  const baseUrl = process.argv[2] || "http://127.0.0.1:18766/";
  const webRoot = path.resolve(__dirname, "../Builds/Web");
  const homepage = await fetch(baseUrl);
  assert.equal(homepage.status, 200);
  const expectedProject = crypto.createHash("sha256").update(webRoot).digest("hex");
  assert.equal(homepage.headers.get("x-qiongtu-web"), expectedProject);
  const html = await homepage.text();
  assert.ok(html.includes('lang="zh-CN"'));
  assert.ok(!html.includes("{{{"), "构建变量应已展开");
  const references = [...html.matchAll(/(?:src|href)="([^"]+)"|(?:loaderUrl|dataUrl|frameworkUrl|codeUrl):\s*"([^"]+)"/g)]
    .map(match => match[1] || match[2]);
  references.push("ThirdPartyNotices/NotoSansCJK-LICENSE.txt");
  for (const resource of references) {
    const response = await fetch(new URL(resource, baseUrl));
    assert.equal(response.status, 200, resource);
    const body = Buffer.from(await response.arrayBuffer());
    const localFile = fs.readFileSync(path.join(webRoot, resource));
    assert.ok(body.equals(localFile), "服务内容须与本地构建文件一致：" + resource);
    if (resource.endsWith(".js")) new vm.Script(body.toString("utf8"), { filename: resource });
    if (resource.endsWith(".unityweb")) {
      const decoded = zlib.gunzipSync(body);
      if (resource.includes(".wasm")) {
        assert.equal(decoded.subarray(0, 4).toString("hex"), "0061736d");
        await WebAssembly.compile(decoded);
      }
      if (resource.includes(".framework.js")) {
        const source = decoded.toString("utf8");
        new vm.Script(source, { filename: resource });
        assert.ok(source.includes("qiongtu:sandbox:v1:"), "浏览器存储桥接应进入最终构建");
        assert.ok(source.includes("qiongtu-exit"), "网页退出桥接应进入最终构建");
      }
    }
    process.stdout.write("通过：HTTP 及构建内容 " + resource + "\n");
  }
  assert.equal((await fetch(new URL(".git/config", baseUrl))).status, 404);
  assert.equal((await fetch(new URL("Build/", baseUrl))).status, 404);
  process.stdout.write("通过：工程源码不可访问，构建目录不允许列举\n");
  process.stdout.write("网页产物 HTTP、Gzip、脚本和 WebAssembly 编译检查通过；不代表浏览器渲染或交互验收。\n");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
