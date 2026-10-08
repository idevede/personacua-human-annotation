"use strict";

const state = {
  manifest: null,
  record: null,
  annotator: "",
  caseIndex: 0,
  frameIndex: { A: 0, B: 0 },
  selectedSlot: "A",
  filters: { scenario: "all", persona: "all", status: "all" },
  saveTimer: null,
  savePromise: null,
  saving: false,
  pendingSave: false,
  localRecovered: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const el = {
  login: $("#login"), app: $("#app"), loginForm: $("#login-form"),
  name: $("#annotator-name"), loginError: $("#login-error"),
  datasetCaption: $("#dataset-caption"), annotatorBadge: $("#annotator-badge"),
  saveStatus: $("#save-status"), progressLabel: $("#progress-label"),
  progressPercent: $("#progress-percent"), progressBar: $("#progress-bar"),
  progressTrack: $("#progress-track"),
  scenarioFilter: $("#scenario-filter"), personaFilter: $("#persona-filter"),
  statusFilter: $("#status-filter"), filteredCount: $("#filtered-count"),
  caseList: $("#case-list"), scenarioChip: $("#scenario-chip"),
  suiteChip: $("#suite-chip"), personaChip: $("#persona-chip"),
  scenarioPanel: $("#scenario-panel"),
  casePosition: $("#case-position"), taskTitle: $("#task-title"),
  taskTitleEn: $("#task-title-en"), taskDescriptionZh: $("#task-description-zh"),
  originalEnglish: $("#original-english"),
  taskDescription: $("#task-description"), modelSwitch: $("#model-switch"),
  rewriteNote: $("#rewrite-note"), modelGrid: $("#model-grid"),
  rubricList: $("#rubric-list"), overallNote: $("#overall-note"),
  caseCompletion: $("#case-completion"), previousCase: $("#previous-case"),
  saveNext: $("#save-next"), nextIncomplete: $("#next-incomplete"),
  exportButton: $("#export-button"), switchUser: $("#switch-user"),
  main: $("#main"), imageDialog: $("#image-dialog"),
  dialogImage: $("#dialog-image"), dialogCaption: $("#dialog-caption"),
  closeImage: $("#close-image"), toast: $("#toast"),
};

function text(value) {
  return value == null ? "" : String(value);
}

function escapeHtml(value) {
  return text(value).replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function pretty(value) {
  if (value == null || value === "") return "";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function cases() { return state.manifest?.cases || []; }
function currentCase() { return cases()[state.caseIndex]; }

function caseRecord(caseId, create = false) {
  let record = state.record.annotations[caseId];
  if (!record && create) {
    record = { rubrics: {}, overall_note: "", updated_at: new Date().toISOString() };
    state.record.annotations[caseId] = record;
  }
  return record || { rubrics: {}, overall_note: "" };
}

function rubricValue(caseId, rubricId, slot) {
  return caseRecord(caseId).rubrics?.[rubricId]?.[slot] || "";
}

function slotsFor(item) {
  const slots = [];
  (item?.outputs || []).forEach(output => {
    if (output?.slot && !slots.includes(output.slot)) slots.push(output.slot);
  });
  return slots.length ? slots : ["A", "B"];
}

function activeSlot(item) {
  const slots = slotsFor(item);
  return slots.includes(state.selectedSlot) ? state.selectedSlot : slots[0];
}

function slotDone(item, slot) {
  return (item?.rubrics || []).every(rubric =>
    ["yes", "no", "unsure"].includes(rubricValue(item.case_id, rubric.rubric_id, slot))
  );
}

function isComplete(item) {
  if (!item || !(item.rubrics || []).length) return false;
  return slotsFor(item).every(slot => slotDone(item, slot));
}

function zhLabel(group, key, fallback = "") {
  const table = state.manifest?.labels_zh?.[group] || {};
  return table[key] || fallback || key || "";
}

function localKey() {
  return `personacua-annotation:${state.manifest.dataset_id}:${state.annotator}`;
}

function saveLocal() {
  localStorage.setItem(localKey(), JSON.stringify(state.record));
}

function recoverLocal(serverRecord) {
  try {
    const local = JSON.parse(localStorage.getItem(localKey()) || "null");
    if (!local || local.dataset_id !== serverRecord.dataset_id ||
        local.annotator !== serverRecord.annotator ||
        !local.annotations || typeof local.annotations !== "object" || Array.isArray(local.annotations)) {
      return serverRecord;
    }
    const serverRevision = Number(serverRecord.client_revision || 0);
    const localRevision = Number(local.client_revision || 0);
    const serverTime = Date.parse(serverRecord.client_updated_at || serverRecord.updated_at || 0);
    const localTime = Date.parse(local.client_updated_at || local.updated_at || 0);
    const newerRevision = Number.isFinite(localRevision) && localRevision > serverRevision;
    const sameRevisionNewerTime = localRevision === serverRevision &&
      Number.isFinite(localTime) && (!Number.isFinite(serverTime) || localTime > serverTime);
    if (newerRevision || sameRevisionNewerTime) {
      state.localRecovered = true;
      toast("已恢复浏览器中尚未同步的进度");
      return local;
    }
  } catch (error) {
    console.warn("Could not read local backup", error);
  }
  return serverRecord;
}

async function bootstrap(name) {
  el.loginError.textContent = "";
  const button = $("button[type='submit']", el.loginForm);
  button.disabled = true;
  button.textContent = "正在加载…";
  try {
    const response = await fetch(`/api/bootstrap?annotator=${encodeURIComponent(name)}`);
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "无法加载数据");
    state.manifest = data.manifest;
    state.annotator = data.annotation.annotator;
    state.record = data.annotation;
    state.record.annotations ||= {};
    state.localRecovered = false;
    state.record = recoverLocal(state.record);
    state.caseIndex = 0;
    state.selectedSlot = "A";
    state.frameIndex = { A: 0, B: 0 };
    state.filters = { scenario: "all", persona: "all", status: "all" };
    localStorage.setItem("personacua-last-annotator", state.annotator);
    initializeApp();
  } catch (error) {
    el.loginError.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "开始";
  }
}

function initializeApp() {
  el.login.hidden = true;
  el.app.hidden = false;
  el.annotatorBadge.textContent = state.annotator;
  const taskCount = new Set(cases().map(item => item.task_id).filter(Boolean)).size;
  const familyCount = Object.keys(state.manifest?.scenario_catalog?.families || {}).length
    || new Set(cases().map(item => item.scenario).filter(Boolean)).size;
  el.datasetCaption.textContent = `${familyCount} 个场景 · ${taskCount} 个任务 · ${cases().length} 条`;
  populateFilters();
  const firstIncomplete = cases().findIndex(item => !isComplete(item));
  state.caseIndex = firstIncomplete >= 0 ? firstIncomplete : 0;
  renderAll();
  if (state.localRecovered) scheduleSave();
}

function uniqueBy(values) {
  return [...new Set(values.filter(Boolean))];
}

function personaId(item) {
  return item.persona?.id || item.persona_id || text(item.persona);
}

function personaRole(item) {
  return item.persona?.role || item.persona?.label || personaId(item);
}

function personaLabel(item) {
  return zhLabel("role", personaRole(item), personaRole(item));
}

function scenarioCatalog() {
  return state.manifest?.scenario_catalog || null;
}

function scenarioFamily(scenario) {
  return scenarioCatalog()?.families?.[scenario] || null;
}

function scenarioName(scenario) {
  return scenarioFamily(scenario)?.label || zhLabel("family", scenario, scenario || "");
}

function sourceTag(value) {
  const tag = value?.tag || value?.suite || "";
  if (tag === "real") return "REAL";
  if (tag === "odysseys") return "Odysseys";
  return tag;
}

function populateFilters() {
  const catalog = scenarioCatalog();
  const scenarioNames = catalog?.family_order?.length
    ? catalog.family_order.filter(name => scenarioFamily(name))
    : uniqueBy(cases().map(item => item.scenario));
  const roles = uniqueBy(cases().map(item => item.persona?.role).filter(Boolean));
  el.scenarioFilter.innerHTML = '<option value="all">全部场景</option>' + scenarioNames
    .map(value => `<option value="${escapeHtml(value)}">${escapeHtml(scenarioName(value))}</option>`).join("");
  el.personaFilter.innerHTML = '<option value="all">全部人设</option>' + roles
    .map(value => `<option value="${escapeHtml(value)}">${escapeHtml(zhLabel("role", value, value))}</option>`).join("");
}

function filteredCases() {
  return cases().map((item, index) => ({ item, index })).filter(({ item }) => {
    if (state.filters.scenario !== "all" && item.scenario !== state.filters.scenario) return false;
    if (state.filters.persona !== "all" && personaRole(item) !== state.filters.persona) return false;
    if (state.filters.status === "complete" && !isComplete(item)) return false;
    if (state.filters.status === "incomplete" && isComplete(item)) return false;
    return true;
  });
}

function renderAll() {
  renderProgress();
  renderCaseList();
  renderCase();
}

function renderProgress() {
  const done = cases().filter(isComplete).length;
  const total = cases().length;
  const percent = total ? Math.round(done / total * 100) : 0;
  el.progressLabel.textContent = `${done} / ${total} 已完成`;
  el.progressPercent.textContent = `${percent}%`;
  el.progressBar.style.width = `${percent}%`;
  el.progressTrack.setAttribute("aria-valuenow", String(done));
  el.progressTrack.setAttribute("aria-valuemax", String(total));
  el.progressTrack.setAttribute("aria-valuetext", `${done} / ${total} 已完成`);
}

function renderCaseList() {
  const entries = filteredCases();
  el.filteredCount.textContent = entries.length;
  el.caseList.innerHTML = entries.map(({ item, index }) => `
    <button class="case-link ${index === state.caseIndex ? "active" : ""}" data-index="${index}" type="button" ${index === state.caseIndex ? 'aria-current="true"' : ""}>
      <span class="case-index">${String(index + 1).padStart(2, "0")}</span>
      <span class="case-label">
        <strong>${escapeHtml(scenarioName(item.scenario))} · ${escapeHtml(item.title_zh || item.title || item.task_id)}</strong>
        <span>${escapeHtml(sourceTag(item))} · ${escapeHtml(personaLabel(item))}</span>
      </span>
      <span class="case-dot ${isComplete(item) ? "complete" : ""}" aria-label="${isComplete(item) ? "已完成" : "未完成"}"></span>
    </button>`).join("") || '<p class="muted compact">当前筛选没有结果</p>';
  $$(".case-link", el.caseList).forEach(button => button.addEventListener("click", () => {
    navigateTo(Number(button.dataset.index));
  }));
  requestAnimationFrame(() => $(".case-link.active", el.caseList)?.scrollIntoView({ block: "nearest" }));
}

function renderScenarioPanel(item) {
  const catalog = scenarioCatalog();
  const family = scenarioFamily(item.scenario);
  if (!catalog || !family) {
    el.scenarioPanel.hidden = true;
    el.scenarioPanel.innerHTML = "";
    return;
  }
  const tasks = family.tasks || [];
  const current = tasks.find(task => task.task_id === item.task_id);
  const folder = item.task_folder || current?.folder || "";
  el.scenarioPanel.hidden = false;
  el.scenarioPanel.innerHTML = `
    <p>${escapeHtml(catalog.principle || "")}</p>
    <p>${escapeHtml(family.principle || "")} ${escapeHtml(catalog.roles_principle || "")}</p>
    ${folder ? `<p>这条任务的目录：<code>${escapeHtml(folder)}</code></p>` : ""}
    <p>四个角色之外还有一组对照（persona 是 none）。它也是一条要打分的标注，开场是没有改写过的原始任务。</p>
    ${current?.task_file ? `<p>任务定义：<code>${escapeHtml(current.task_file)}</code></p>` : ""}
    ${family.persona_files?.length ? `<p>人设文件：<code>${escapeHtml(family.persona_files[0].replace(/\/[^/]+$/, "/"))}</code></p>` : ""}
    <details>
      <summary>这个场景要标的任务，共 ${tasks.length} 个</summary>
      <ul class="family-tasks">
        ${tasks.map(task => `
          <li class="${task.task_id === item.task_id ? "here" : ""}">
            ${escapeHtml(sourceTag(task))}
            · ${escapeHtml(task.title_zh || task.title)}
            · ${escapeHtml(task.domain_zh || task.domain || "")}
            <div><code>${escapeHtml(task.folder || "")}</code></div>
          </li>`).join("")}
      </ul>
    </details>`;
}

function setChip(node, value) {
  if (!node) return;
  node.hidden = !value;
  node.textContent = value || "";
}

function renderCase() {
  const item = currentCase();
  if (!item) return;
  state.selectedSlot = activeSlot(item);
  setChip(el.scenarioChip, item.scenario ? `场景 · ${scenarioName(item.scenario)}` : "");
  setChip(el.suiteChip, sourceTag(item));
  setChip(el.personaChip, personaLabel(item) ? `人设 · ${personaLabel(item)}` : "");
  renderScenarioPanel(item);
  el.casePosition.textContent = `${state.caseIndex + 1} / ${cases().length}`;
  el.taskTitle.textContent = item.title_zh || item.title || item.task_id;
  el.taskTitleEn.textContent = item.title_zh && item.title ? item.title : "";
  el.taskTitleEn.hidden = !el.taskTitleEn.textContent;
  const chinese = item.description_zh || "";
  const original = item.task_description || item.confirmed_task || "";
  el.taskDescriptionZh.textContent = chinese || original;
  el.taskDescription.textContent = original;
  el.originalEnglish.hidden = !chinese;
  renderModels(item);
  renderRubrics(item);
  el.overallNote.value = caseRecord(item.case_id).overall_note || "";
  el.previousCase.disabled = state.caseIndex === 0;
  updateSaveNextLabel(item);
  updateCaseCompletion(item);
}

function updateSaveNextLabel(item) {
  const pending = slotsFor(item).find(slot => slot !== activeSlot(item) && !slotDone(item, slot));
  el.saveNext.textContent = pending ? `保存并改看模型 ${pending} →` : "保存并进入下一个 →";
}

function outputsBySlot(item) {
  const map = {};
  (item.outputs || []).forEach(output => { map[output.slot] = output; });
  return map;
}

function pathUrl(path) {
  if (!path) return "";
  const normalized = text(path).replace(/\\/g, "/").replace(/^\.\//, "");
  if (normalized.startsWith("/assets/") && !normalized.includes("..")) return normalized;
  if (normalized.startsWith("assets/") && !normalized.includes("..")) return `/${normalized}`;
  return "";
}

function framesFor(output) {
  const frames = [];
  (output?.steps || []).forEach((step, stepOffset) => {
    const screenshots = step.screenshot_paths || (step.screenshot_path ? [step.screenshot_path] : []);
    screenshots.forEach((path, shotOffset) => frames.push({
      path, step, stepOffset, shotOffset, final: false,
    }));
  });
  const existing = new Map(frames.map(frame => [frame.path, frame]));
  (output?.final_screenshot_paths || []).forEach((path, offset) => {
    if (existing.has(path)) {
      existing.get(path).final = true;
    } else {
      const frame = {
        path, step: { index: "final", kind: "final", tool: "final_state", result_preview: "最终页面状态" },
        stepOffset: (output.steps || []).length + offset, shotOffset: 0, final: true,
      };
      frames.push(frame);
      existing.set(path, frame);
    }
  });
  return frames;
}

function renderModelSwitch(item) {
  const current = activeSlot(item);
  el.modelSwitch.innerHTML = slotsFor(item).map(slot => {
    const done = (item.rubrics || []).filter(rubric =>
      ["yes", "no", "unsure"].includes(rubricValue(item.case_id, rubric.rubric_id, slot))
    ).length;
    const total = (item.rubrics || []).length;
    return `
      <button class="slot-button ${slot === current ? "active" : ""}" type="button" role="tab" data-select-slot="${escapeHtml(slot)}" aria-selected="${slot === current}">
        <span>模型 ${escapeHtml(slot)}</span>
        <small>${done} / ${total} 已判</small>
      </button>`;
  }).join("");
  $$("[data-select-slot]", el.modelSwitch).forEach(button => button.addEventListener("click", () => {
    state.selectedSlot = button.dataset.selectSlot;
    renderCase();
  }));
}

function renderRewriteNote(item, outputs, slot) {
  const openings = slotsFor(item).map(name => text((outputs[name] || {}).opening).trim());
  const differs = new Set(openings.filter(Boolean)).size > 1;
  const current = text((outputs[slot] || {}).opening).trim();
  const another = slotsFor(item).find(name => name !== slot && text((outputs[name] || {}).opening).trim() !== current);
  el.rewriteNote.textContent = differs
    ? `上面那份原始任务，每个模型都一样。下面是按人设改写之后，模型 ${slot} 真正收到的说法${another ? `，和模型 ${another} 不一样` : ""}。请按这一版往下看它的操作。`
    : "上面那份原始任务，每个模型都一样。这一条里，改写之后各模型收到的说法也相同。";
}

function renderModels(item) {
  const outputs = outputsBySlot(item);
  const slot = activeSlot(item);
  state.selectedSlot = slot;
  renderModelSwitch(item);
  renderRewriteNote(item, outputs, slot);
  const output = outputs[slot] || { slot, steps: [] };
  const frames = framesFor(output);
  const laterUserMessages = (output.conversation || [])
    .filter(message => message && message.role === "user")
    .slice(1)
    .map(message => message.text ?? message.content)
    .filter(message => typeof message === "string" && message.trim());
  state.frameIndex[slot] = Math.min(state.frameIndex[slot] || 0, Math.max(0, frames.length - 1));
  el.modelGrid.innerHTML = `
    <article class="model-card" data-slot="${escapeHtml(slot)}">
      <header class="model-card-header">
        <span class="model-name">模型 ${escapeHtml(slot)}</span>
        <span class="step-total">${frames.length} 张截图 · ${(output.steps || []).length} 个步骤</span>
      </header>
      <details class="opening" open>
        <summary>改写后的任务入口</summary>
        <div class="opening-copy">${escapeHtml(output.opening || "（没有记录）")}</div>
      </details>
      ${laterUserMessages.length ? `
        <details class="opening follow-up" open>
          <summary>做的过程中，用户又补了 ${laterUserMessages.length} 句</summary>
          <div class="opening-copy">${laterUserMessages.map((message, index) =>
            `<strong>补充 ${index + 1}</strong>\n${escapeHtml(message)}`
          ).join("\n\n")}</div>
        </details>` : ""}
      <div class="trace-viewer" data-trace="${escapeHtml(slot)}"></div>
      <details class="final-answer" open>
        <summary>最后的回答${output.stop_reason ? ` · ${escapeHtml(output.stop_reason)}` : ""}</summary>
        <div class="answer-copy">${escapeHtml(output.final_answer || "（没有最后的回答）")}</div>
      </details>
    </article>`;
  renderTrace(slot, output);
}

function renderTrace(slot, output) {
  const root = $(`[data-trace="${slot}"]`);
  if (!root) return;
  const frames = framesFor(output);
  const index = Math.min(state.frameIndex[slot] || 0, Math.max(0, frames.length - 1));
  state.frameIndex[slot] = index;
  const frame = frames[index];
  const step = frame?.step || {};
  const stepArgs = pretty(step.args);
  const result = step.result_preview || step.result || step.observation || "";
  const screenshotUrl = pathUrl(frame?.path);
  root.innerHTML = `
    <div class="screenshot-stage">
      ${screenshotUrl
        ? `<button class="screenshot-zoom" type="button" data-zoom="${slot}" aria-label="放大模型 ${slot} 第 ${index + 1} 张截图"><img src="${escapeHtml(screenshotUrl)}" alt="模型 ${slot} 第 ${index + 1} 张截图" loading="eager" /></button>`
        : '<div class="empty-shot">这个轨迹没有可显示的截图</div>'}
      ${frame?.final ? '<span class="final-frame-badge">最终状态</span>' : ""}
    </div>
    <div class="trace-controls">
      <button class="trace-button" type="button" data-prev="${slot}" ${index <= 0 ? "disabled" : ""} aria-label="模型 ${slot} 上一张截图">←</button>
      <input class="trace-range" data-range="${slot}" type="range" min="0" max="${Math.max(0, frames.length - 1)}" value="${index}" ${frames.length < 2 ? "disabled" : ""} aria-label="模型 ${slot} 截图进度" aria-valuetext="第 ${frames.length ? index + 1 : 0} 张，共 ${frames.length} 张" />
      <button class="trace-button" type="button" data-next="${slot}" ${index >= frames.length - 1 ? "disabled" : ""} aria-label="模型 ${slot} 下一张截图">→</button>
      <span class="frame-counter">${frames.length ? `${index + 1} / ${frames.length}` : "0 / 0"}</span>
    </div>
    <div class="step-card">
      <div class="step-heading">
        <span class="tool-name">${escapeHtml(step.tool || step.kind || "没有步骤信息")}</span>
        <span class="step-index">第 ${escapeHtml(step.index ?? frame?.stepOffset ?? "—")} 步</span>
      </div>
      ${stepArgs ? `<pre>${escapeHtml(stepArgs)}</pre>` : ""}
      ${result ? `<pre>${escapeHtml(text(result))}</pre>` : ""}
    </div>`;
  $(`[data-prev="${slot}"]`, root)?.addEventListener("click", () => moveFrame(slot, output, -1));
  $(`[data-next="${slot}"]`, root)?.addEventListener("click", () => moveFrame(slot, output, 1));
  $(`[data-range="${slot}"]`, root)?.addEventListener("change", event => {
    state.frameIndex[slot] = Number(event.target.value);
    renderTrace(slot, output);
  });
  const zoomButton = $(`[data-zoom="${slot}"]`, root);
  const screenshot = zoomButton ? $("img", zoomButton) : null;
  screenshot?.addEventListener("error", () => {
    const stage = $(".screenshot-stage", root);
    if (stage) stage.innerHTML = '<div class="empty-shot" role="status">截图文件无法加载</div>';
  });
  zoomButton?.addEventListener("click", () => {
    el.dialogImage.src = screenshot.src;
    el.dialogCaption.textContent = `模型 ${slot} · 第 ${index + 1} / ${frames.length} 张`;
    el.imageDialog.showModal();
  });
}

function moveFrame(slot, output, delta) {
  const max = Math.max(0, framesFor(output).length - 1);
  state.frameIndex[slot] = Math.max(0, Math.min(max, (state.frameIndex[slot] || 0) + delta));
  renderTrace(slot, output);
}

const decisionLabels = { yes: "得分", no: "不得分", unsure: "证据不足" };

function renderRubrics(item) {
  const record = caseRecord(item.case_id);
  const slot = activeSlot(item);
  const credit = state.manifest?.guide_credit || "Grok 4.7";
  el.rubricList.innerHTML = (item.rubrics || []).map(rubric => {
    const rubricRecord = record.rubrics?.[rubric.rubric_id] || {};
    const criterion = rubric.criterion_zh || rubric.criterion || rubric.requirement || "";
    const english = rubric.criterion_zh && rubric.criterion ? rubric.criterion : "";
    const others = slotsFor(item).filter(name => name !== slot).map(name => {
      const value = rubricRecord[name];
      return `模型 ${name}：${decisionLabels[value] || "还没选"}`;
    }).join(" · ");
    const checkerOnly = /prints SUCCESS|state checker/i.test(rubric.verification || "");
    return `
      <article class="rubric-row" data-rubric="${escapeHtml(rubric.rubric_id)}">
        <div class="rubric-copy">
          <span class="rubric-id">${escapeHtml(rubric.rubric_id)}</span>
          <div class="rubric-criterion">${escapeHtml(criterion)}</div>
          ${english ? `<details class="rubric-english"><summary>英文标准</summary><div>${escapeHtml(english)}</div></details>` : ""}
          ${rubric.hint_zh ? `<div class="hint"><div class="hint-kicker">${escapeHtml(credit)} 的提示</div><p>${escapeHtml(rubric.hint_zh)}</p></div>` : ""}
          ${!rubric.hint_zh && rubric.verification && !checkerOnly ? `<div class="rubric-verification">可以对照：${escapeHtml(rubric.verification)}</div>` : ""}
        </div>
        <div class="decision-box" role="radiogroup" aria-label="${escapeHtml(rubric.rubric_id)} 模型 ${slot} 判定">
          <div class="decision-heading">模型 ${escapeHtml(slot)}</div>
          <div class="decision-options">
            ${Object.entries(decisionLabels).map(([value, label]) => `
              <label>
                <input type="radio" name="${escapeHtml(item.case_id)}-${escapeHtml(rubric.rubric_id)}-${slot}" data-decision data-rubric-id="${escapeHtml(rubric.rubric_id)}" data-slot="${slot}" value="${value}" aria-label="模型 ${slot}：${label}" ${rubricRecord[slot] === value ? "checked" : ""} />
                <span>${label}</span>
              </label>`).join("")}
          </div>
        </div>
        ${others ? `<div class="other-slots">${escapeHtml(others)}</div>` : ""}
        <div class="rubric-note">
          <input type="text" data-rubric-note="${escapeHtml(rubric.rubric_id)}" value="${escapeHtml(rubricRecord.note || "")}" aria-label="${escapeHtml(rubric.rubric_id)} 备注" placeholder="这一项你为什么这么判（可选）" />
        </div>
      </article>`;
  }).join("");

  $$('[data-decision]', el.rubricList).forEach(input => input.addEventListener("change", event => {
    const target = event.currentTarget;
    const caseData = caseRecord(item.case_id, true);
    caseData.rubrics[target.dataset.rubricId] ||= {};
    caseData.rubrics[target.dataset.rubricId][target.dataset.slot] = target.value;
    changed(item);
  }));
  $$('[data-rubric-note]', el.rubricList).forEach(input => input.addEventListener("input", event => {
    const caseData = caseRecord(item.case_id, true);
    const rubricId = event.currentTarget.dataset.rubricNote;
    caseData.rubrics[rubricId] ||= {};
    caseData.rubrics[rubricId].note = event.currentTarget.value;
    changed(item);
  }));
}

function changed(item) {
  const now = new Date().toISOString();
  const record = caseRecord(item.case_id, true);
  record.updated_at = now;
  state.record.client_revision = Number(state.record.client_revision || 0) + 1;
  state.record.client_updated_at = now;
  saveLocal();
  updateCaseCompletion(item);
  renderProgress();
  renderCaseList();
  renderModelSwitch(item);
  updateSaveNextLabel(item);
  scheduleSave();
}

function updateCaseCompletion(item) {
  const complete = isComplete(item);
  el.caseCompletion.textContent = complete ? "这条已经判完" : "还没判完";
  el.caseCompletion.classList.toggle("complete", complete);
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.pendingSave = true;
  setSaveStatus("等待保存…", "saving");
  state.saveTimer = setTimeout(() => {
    state.saveTimer = null;
    void saveRemote();
  }, 700);
}

function saveRemote() {
  if (!state.record) return Promise.resolve(false);
  clearTimeout(state.saveTimer);
  state.saveTimer = null;
  state.pendingSave = true;
  if (state.savePromise) return state.savePromise;

  const record = state.record;
  const annotator = state.annotator;
  state.savePromise = (async () => {
    let succeeded = true;
    while (state.pendingSave && state.record === record && state.annotator === annotator) {
      state.pendingSave = false;
      state.saving = true;
      setSaveStatus("正在保存…", "saving");
      saveLocal();
      const payload = JSON.stringify(record);
      try {
        const response = await fetch("/api/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || "保存失败");
        if (state.record === record && state.annotator === annotator) {
          record.updated_at = data.updated_at;
          saveLocal();
          setSaveStatus(`已保存 · ${new Date(data.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`, "");
        }
      } catch (error) {
        succeeded = false;
        state.pendingSave = false;
        setSaveStatus("网络保存失败，已保存在本机", "error");
        console.error(error);
      }
    }
    return succeeded;
  })().finally(() => {
    state.saving = false;
    state.savePromise = null;
  });
  return state.savePromise;
}

function setSaveStatus(label, className) {
  el.saveStatus.textContent = label;
  el.saveStatus.className = `save-status ${className}`;
}

function navigateTo(index) {
  if (index < 0 || index >= cases().length) return;
  state.caseIndex = index;
  state.frameIndex = { A: 0, B: 0 };
  renderAll();
  el.main.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function navigateRelative(delta) { navigateTo(state.caseIndex + delta); }

function goNextIncomplete() {
  if (!cases().length) return;
  for (let offset = 1; offset <= cases().length; offset += 1) {
    const index = (state.caseIndex + offset) % cases().length;
    if (!isComplete(cases()[index])) { navigateTo(index); return; }
  }
  toast("都判完了");
}

function toast(message) {
  el.toast.textContent = message;
  el.toast.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.toast.classList.remove("visible"), 2600);
}

function exportRecord() {
  saveLocal();
  const blob = new Blob([JSON.stringify(state.record, null, 2) + "\n"], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  const safeName = state.annotator.replace(/[^\p{L}\p{N}_.-]+/gu, "_");
  link.download = `${safeName || "annotation"}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
  toast("本地备份已下载");
}

el.loginForm.addEventListener("submit", event => {
  event.preventDefault();
  bootstrap(el.name.value);
});
el.overallNote.addEventListener("input", () => {
  const item = currentCase();
  const record = caseRecord(item.case_id, true);
  record.overall_note = el.overallNote.value;
  changed(item);
});
[el.scenarioFilter, el.personaFilter, el.statusFilter].forEach(input => input.addEventListener("change", () => {
  state.filters = {
    scenario: el.scenarioFilter.value,
    persona: el.personaFilter.value,
    status: el.statusFilter.value,
  };
  renderCaseList();
}));
el.previousCase.addEventListener("click", () => navigateRelative(-1));
el.saveNext.addEventListener("click", async () => {
  await saveRemote();
  const item = currentCase();
  const pending = item && slotsFor(item).find(slot => slot !== activeSlot(item) && !slotDone(item, slot));
  if (pending) {
    state.selectedSlot = pending;
    renderCase();
    document.getElementById("model-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  navigateRelative(1);
});
el.nextIncomplete.addEventListener("click", goNextIncomplete);
el.exportButton.addEventListener("click", exportRecord);
el.switchUser.addEventListener("click", async () => {
  await saveRemote();
  el.app.hidden = true;
  el.login.hidden = false;
  el.name.value = "";
  el.name.focus();
});
el.closeImage.addEventListener("click", () => el.imageDialog.close());
el.imageDialog.addEventListener("click", event => {
  if (event.target === el.imageDialog) el.imageDialog.close();
});

document.addEventListener("keydown", event => {
  if (el.app.hidden || el.imageDialog.open || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
  const item = currentCase();
  if (!item) return;
  const slot = activeSlot(item);
  const output = outputsBySlot(item)[slot] || {};
  const slots = slotsFor(item);
  const handled = event.key.toLowerCase() === "a" || event.key === "ArrowLeft"
    || event.key.toLowerCase() === "d" || event.key === "ArrowRight"
    || /^[1-9]$/.test(event.key) || event.key === "[" || event.key === "]";
  if (!handled) return;
  event.preventDefault();
  if (event.key.toLowerCase() === "a" || event.key === "ArrowLeft") moveFrame(slot, output, -1);
  else if (event.key.toLowerCase() === "d" || event.key === "ArrowRight") moveFrame(slot, output, 1);
  else if (/^[1-9]$/.test(event.key) && slots[Number(event.key) - 1]) {
    state.selectedSlot = slots[Number(event.key) - 1];
    renderCase();
  } else if (event.key === "[") navigateRelative(-1);
  else if (event.key === "]") navigateRelative(1);
});

window.addEventListener("beforeunload", () => {
  if (state.record) saveLocal();
});

const lastAnnotator = localStorage.getItem("personacua-last-annotator");
if (lastAnnotator) el.name.value = lastAnnotator;
