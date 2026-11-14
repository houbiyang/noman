"use strict";

// 使用隔离页面对象验证只读展示，不发送业务命令。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const html = fs.readFileSync(path.resolve(__dirname, "../../backend/agent-workbench.html"), "utf8");
const elements = new Map();
for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    elements.set(match[1], { innerHTML: "", textContent: "", value: "", hidden: false,
        classList: { toggle() {} }, querySelectorAll() { return []; } });
}
const context = vm.createContext({
    document: { getElementById: id => elements.get(id), querySelectorAll: () => [] },
    setInterval() {}, console, structuredClone,
});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
assert.equal(vm.runInContext("typeof renderCooperation", context), "function");
const fixture = {
    role: "red", context: { branch_id: "branch-original" }, clock: { simulation_step: 12 },
    units: [{ unit_id: "unit-observer", unit_type: "recon", name: "<img src=x onerror=alert(1)>", energy: 0 },
        { unit_id: "unit-service", unit_type: "support", supply: 80, energy: 40 }],
    contacts: [], last_seen_records: [{ unit_id: "old-contact", observer_unit_id: "unit-observer", observed_step: 0 }],
    coordination: { points: [{ point_id: "TASK_A" }], plans: { red: { status: "adopted", tasks: [
        { task_id: "observe-zero", unit_id: "unit-observer", action: "observe", status: "succeeded",
            depends_on: [], completed_step: 12, result: { contact_count: 0, observed_step: 12 } },
        { task_id: "service-zero", unit_id: "unit-service", action: "resupply", status: "waiting",
            target_unit_id: "unit-observer", depends_on: ["observe-zero"], result: { transferred: 0 } },
    ] } } },
    victory: { mode: "qinglan_control" },
    timeline: [{ event_id: "event-1", simulation_step: 12, title: "已保存", detail: "<script>alert(1)</script>" }],
};
context.fixture = fixture;
vm.runInContext("renderCooperation(fixture)", context);
assert.match(elements.get("cooperationTasks").innerHTML, /本次观察0条/);
assert.match(elements.get("cooperationTasks").innerHTML, /累计转移0/);
assert.match(elements.get("cooperationTasks").innerHTML, /已完成/);
assert.match(elements.get("cooperationUnits").innerHTML, /&lt;img/);
assert.doesNotMatch(elements.get("cooperationUnits").innerHTML, /<img/);
assert.match(elements.get("observationRecords").innerHTML, /历史记录/);
assert.match(elements.get("observationRecords").innerHTML, /第0步/);
assert.match(elements.get("cooperationTimeline").innerHTML, /&lt;script&gt;/);
const previous = elements.get("cooperationTimeline").innerHTML;
vm.runInContext("renderCooperation(fixture)", context);
assert.equal(elements.get("cooperationTimeline").innerHTML, previous);
vm.runInContext("renderCooperation({role:'host',clock:{simulation_step:0},units:[]})", context);
assert.doesNotMatch(elements.get("cooperationTasks").innerHTML, /observe-zero/);
assert.match(elements.get("cooperationTasks").innerHTML, /本方席位/);
vm.runInContext("renderCooperation(null)", context);
for (const id of ["cooperationUnits", "cooperationTasks", "observationRecords", "cooperationTimeline"]) {
    assert.doesNotMatch(elements.get(id).innerHTML, /unit-observer|event-1|old-contact/);
}
assert.match(elements.get("cooperationIdentity").textContent, /未连接/);
async function verifySeatSwitch() {
    vm.runInContext("renderCooperation(fixture)", context);
    elements.get('role').onchange();
    assert.match(elements.get('cooperationIdentity').textContent, /未连接/);
    assert.equal(elements.get('configPanel').hidden, true);
    assert.equal(elements.get('tasks').innerHTML, '');
    let releaseOldResponse;
    context.delayedResponse = new Promise(resolve => { releaseOldResponse = resolve; });
    const pendingRefresh = vm.runInContext("session={role:'red'}; request=()=>delayedResponse; refresh()", context);
    elements.get('role').onchange();
    releaseOldResponse(fixture);
    await pendingRefresh;
    assert.match(elements.get('cooperationIdentity').textContent, /未连接/);
    assert.doesNotMatch(elements.get('cooperationUnits').innerHTML, /unit-observer/);
    console.log("通过：零值、依赖状态、缺失数据、角色清空、过期响应隔离、文本转义和重复刷新。");
}
verifySeatSwitch().catch(error => { console.error(error); process.exitCode = 1; });
