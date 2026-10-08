"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class MockElement {
  constructor() {
    this.listeners = {};
    this.classList = { add() {}, remove() {}, toggle() {} };
    this.style = {};
    this.value = "";
    this.hidden = false;
    this.textContent = "";
    this.className = "";
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this[name] = String(value); }
  querySelector() { return new MockElement(); }
  querySelectorAll() { return []; }
  focus() {}
}

const elements = new Map();
const getElement = selector => {
  if (!elements.has(selector)) elements.set(selector, new MockElement());
  return elements.get(selector);
};
const storage = new Map();
const context = {
  console,
  Blob,
  URL,
  setTimeout,
  clearTimeout,
  requestAnimationFrame: callback => callback(),
  fetch: async () => { throw new Error("unexpected fetch"); },
  document: {
    querySelector: getElement,
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => new MockElement(),
    body: { appendChild() {} },
  },
  window: { addEventListener() {}, scrollTo() {} },
  localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
  },
};
vm.createContext(context);

const appSource = fs.readFileSync(require.resolve("../web/app.js"), "utf8");
const checks = `
(async () => {
  const output = {
    steps: [{ index: 0, screenshot_paths: ["assets/a.png"] }],
    final_screenshot_paths: ["assets/a.png", "assets/final.png"],
  };
  const frames = framesFor(output);
  assert.equal(frames.length, 2);
  assert.equal(frames[0].final, true);
  assert.equal(frames[1].final, true);
  assert.equal(pathUrl("assets/a.png"), "/assets/a.png");
  assert.equal(pathUrl("https://example.test/tracker.png"), "");
  assert.equal(pathUrl("assets/../server.py"), "");

  state.manifest = {
    dataset_id: "d",
    cases: [{ case_id: "c", rubrics: [{ rubric_id: "R1" }] }],
  };
  state.annotator = "Alice";
  state.record = {
    dataset_id: "d",
    annotator: "Alice",
    updated_at: "2026-01-01T00:00:00Z",
    client_revision: 1,
    annotations: { c: { rubrics: { R1: { A: "yes", B: "unsure" } } } },
  };
  assert.equal(isComplete(state.manifest.cases[0]), true);
  assert.deepEqual(slotsFor({ outputs: [{ slot: "B" }, { slot: "A" }, { slot: "C" }] }), ["B", "A", "C"]);
  assert.deepEqual(slotsFor({ rubrics: [{ rubric_id: "R1" }] }), ["A", "B"]);
  state.manifest.cases[0].outputs = [{ slot: "A" }, { slot: "C" }];
  assert.equal(isComplete(state.manifest.cases[0]), false);
  state.record.annotations.c.rubrics.R1.C = "no";
  assert.equal(isComplete(state.manifest.cases[0]), true);
  delete state.manifest.cases[0].outputs;
  delete state.record.annotations.c.rubrics.R1.C;

  const local = structuredClone(state.record);
  local.client_revision = 2;
  local.annotations.c.rubrics.R1.B = "no";
  localStorage.setItem(localKey(), JSON.stringify(local));
  state.localRecovered = false;
  const recovered = recoverLocal(state.record);
  assert.equal(recovered.client_revision, 2);
  assert.equal(state.localRecovered, true);

  state.record = recovered;
  let firstResolve;
  const bodies = [];
  let call = 0;
  fetch = (_url, options) => {
    bodies.push(JSON.parse(options.body));
    call += 1;
    if (call === 1) {
      return new Promise(resolve => { firstResolve = resolve; });
    }
    return Promise.resolve({ ok: true, json: async () => ({ ok: true, updated_at: "2026-01-01T00:00:02Z" }) });
  };
  const firstSave = saveRemote();
  state.record.client_revision = 3;
  state.record.client_updated_at = "2026-01-01T00:00:03Z";
  state.pendingSave = true;
  const coalescedSave = saveRemote();
  assert.equal(firstSave, coalescedSave);
  firstResolve({ ok: true, json: async () => ({ ok: true, updated_at: "2026-01-01T00:00:01Z" }) });
  assert.equal(await firstSave, true);
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0].client_revision, 2);
  assert.equal(bodies[1].client_revision, 3);
  assert.equal(state.record.updated_at, "2026-01-01T00:00:02Z");
  assert.equal(JSON.parse(localStorage.getItem(localKey())).client_revision, 3);
  console.log("frontend checks passed");
})()`;

vm.runInContext(`${appSource}\n${checks}`, vm.createContext({ ...context, assert, structuredClone }))
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
