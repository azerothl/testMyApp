"use strict";

let editorProject;
let editorData;
let editorId = "";
let modelBusy = false;
let nextTask = 1;

function modelSelect(select, choices, value) {
  select.replaceChildren(...choices.map(([id, label]) => new Option(label, id)));
  if (choices.some(([id]) => id === value)) select.value = value;
}

function editorProofs() {
  return editorData.proofs.filter(proof => proof.target === byId("model-target").value);
}

function updateModelRows() {
  const rows = [...byId("model-tasks").children];
  const people = new Map(editorData.personas.map(item => [item.id, item.name]));
  const proofs = editorProofs();
  const used = new Set(rows.flatMap(row => [row.querySelector("[data-after]").value, ...extraPrerequisites(row)]));
  for (const row of rows) {
    row.querySelector("legend").textContent = `Tâche ${row.dataset.task.slice(5)}`;
    const after = row.querySelector("[data-after]");
    const choices = rows.filter(item => item !== row).map(item => {
      const name = people.get(item.querySelector("[data-person]").value);
      const goal = item.querySelector("[data-goal]").value.trim();
      return [item.dataset.task, `Tâche ${item.dataset.task.slice(5)} · ${name}${goal ? " · " + goal.slice(0, 55) : ""}`];
    });
    modelSelect(after, [["", "Dès que possible"], ...choices], after.value);
    const extra = row.querySelector("[data-extra]");
    extra.closest("details").hidden = choices.filter(([id]) => id !== after.value).length === 0;
    const selected = new Set(extraPrerequisites(row).filter(id => id !== after.value));
    row.dataset.waitFor = JSON.stringify([...selected]);
    extra.replaceChildren(...choices.filter(([id]) => id !== after.value).map(([id, name]) => {
      const label = el("label", "", "model-prerequisite");
      const box = el("input");
      box.type = "checkbox";
      box.value = id;
      box.checked = selected.has(id);
      box.addEventListener("change", () => {
        const dependencies = [...extra.querySelectorAll("input:checked")].map(input => input.value);
        row.dataset.waitFor = JSON.stringify(dependencies);
        updateModelRows();
        [...extra.querySelectorAll("input")].find(input => input.value === id)?.focus();
      });
      label.append(box, document.createTextNode(name));
      return label;
    }));
    const edgeRules = row.querySelector("[data-rules]");
    const rules = JSON.parse(row.dataset.conditions);
    const dependencies = [after.value, ...selected].filter(Boolean);
    row.dataset.conditions = JSON.stringify(Object.fromEntries(dependencies.filter(id => rules[id]).map(id => [id, rules[id]])));
    edgeRules.replaceChildren(...dependencies.map(id => {
      const label = el("label", `Condition après la tâche ${id.slice(5)}`);
      const mode = el("select");
      mode.setAttribute("aria-label", label.textContent);
      modelSelect(mode, [["", "Réussite requise"], ["success", "Si réussi"], ["failure", "Si échoué"],
        ["always", "Dans tous les cas"]], rules[id]);
      mode.addEventListener("change", () => {
        const current = JSON.parse(row.dataset.conditions);
        if (mode.value) current[id] = mode.value;
        else delete current[id];
        row.dataset.conditions = JSON.stringify(current);
        updateEditorGraph();
      });
      label.append(mode);
      return label;
    }));
    const proof = row.querySelector("[data-proof]");
    modelSelect(proof, [["", "Texte visible dans l’application"], ...proofs.map(item => [item.id, item.name])], proof.value);
    const option = proofs.find(item => item.id === proof.value);
    row.querySelector(".hint").textContent = option?.description || "";
    row.querySelector("[data-title-field]").hidden = !option || Boolean(option.requires_parent);
    row.querySelector("[data-remove]").disabled = rows.length === 1 || used.has(row.dataset.task);
  }
  const proofUsed = rows.some(row => row.querySelector("[data-proof]").value);
  byId("model-title-field").hidden = !proofUsed;
  byId("model-add").disabled = rows.length >= 24;
  updateEditorGraph();
}

function updateEditorGraph() {
  const names = new Map(editorData.personas.map(person => [person.id, person.name]));
  const nodes = editableModel().rows.map(row => ({
    task: row.id.slice(5), name: names.get(row.persona), scenario: row.goal,
    after_tasks: [row.after, ...row.wait_for].filter(Boolean).map(id => id.slice(5)),
    conditions: Object.fromEntries(Object.entries(row.conditions).map(([id, rule]) => [id.slice(5), rule])),
  }));
  renderWorkflow(byId("editor-graph"), nodes);
}

function extraPrerequisites(row) {
  return JSON.parse(row.dataset.waitFor || "[]");
}

function modelRow(task = {}) {
  const container = byId("model-tasks");
  const card = el("fieldset", "", "model-task");
  card.dataset.task = task.id || `task-${nextTask++}`;
  card.dataset.waitFor = JSON.stringify(task.wait_for || []);
  card.dataset.conditions = JSON.stringify(task.conditions || {});
  card.innerHTML = `<legend></legend>
    <div class="editor-grid">
      <label>Persona <select data-person aria-label="Persona" required></select></label>
      <label>Commence après <select data-after aria-label="Commence après"></select></label>
    </div>
    <details><summary>Autres prérequis</summary><div data-extra></div></details>
    <div class="editor-grid" data-rules></div>
    <label>Preuve à vérifier <select data-proof aria-label="Preuve à vérifier"></select></label>
    <p class="hint"></p>
    <label data-title-field hidden>Titre de la nouvelle tâche dans l’application <input data-title maxlength="240"></label>
    <label>Objectif <input data-goal maxlength="500" required></label>
    <label>Texte attendu <input data-result maxlength="500" required></label>
    <div><button type="button" data-remove class="secondary">Retirer cette tâche</button></div>`;
  const reserved = new Set([...container.querySelectorAll("[data-person]")].map(item => item.value));
  const person = card.querySelector("[data-person]");
  const proof = card.querySelector("[data-proof]");
  const goal = card.querySelector("[data-goal]");
  const expected = card.querySelector("[data-result]");
  modelSelect(person, editorData.personas.map(item => [item.id, item.name]),
    task.persona || editorData.personas.find(item => !reserved.has(item.id))?.id);
  modelSelect(proof, [["", "Texte visible dans l’application"], ...editorProofs().map(item => [item.id, item.name])], task.proof);
  if (task.after) modelSelect(card.querySelector("[data-after]"), [[task.after, task.after]], task.after);
  card.querySelector("[data-title]").value = task.title || "";
  goal.value = task.goal || "";
  expected.value = task.expected || "";
  proof.addEventListener("change", () => {
    const option = editorProofs().find(item => item.id === proof.value);
    if (option) {
      goal.value = option.goal;
      expected.value = option.expected;
    }
    updateModelRows();
  });
  person.addEventListener("change", updateModelRows);
  goal.addEventListener("change", updateModelRows);
  card.querySelector("[data-after]").addEventListener("change", updateModelRows);
  card.querySelector("[data-remove]").addEventListener("click", () => {
    card.remove();
    updateModelRows();
    byId("model-add").focus();
  });
  container.append(card);
}

function openModel(model) {
  editorId = model?.id || "";
  byId("model-list").value = editorId;
  byId("model-form").hidden = false;
  byId("model-name").value = model?.name || "";
  byId("model-title").value = model?.title || "";
  modelSelect(byId("model-target"), editorData.targets.map(item => [item.id, item.id]), model?.target);
  byId("model-tasks").replaceChildren();
  const rows = model?.rows || [];
  const legacyIds = new Map(rows.map((row, index) => [row.persona, `task-${index + 1}`]));
  const tasks = model?.version === 2 ? rows : rows.map(row => ({
    ...row, id: legacyIds.get(row.persona), after: legacyIds.get(row.after),
  }));
  nextTask = Math.max(0, ...tasks.map(row => Number(row.id.slice(5)))) + 1;
  for (const row of tasks.length ? tasks : [{}]) modelRow(row);
  updateModelRows();
  byId("model-start").disabled = !editorId;
  byId("model-copy").hidden = !editorId;
  byId("model-status").textContent = "";
  showError(byId("model-error"));
}

function renderCampaignEditor(project, data, allProjects, changingProject) {
  editorProject = project;
  editorData = data;
  byId("model-editor").hidden = !data.targets.length || !data.personas.length;
  if (changingProject) {
    editorId = "";
    byId("model-form").hidden = true;
    byId("model-status").textContent = "";
    showError(byId("model-error"));
  }
  byId("model-picker").hidden = !data.models.length;
  modelSelect(byId("model-list"), [["", "Choisir un modèle"], ...data.models.map(item => [item.id, item.name])], editorId);
  modelSelect(byId("model-destination"), allProjects.map(item => [item.id, item.name]), project.id);
}

function editableModel() {
  return {
    ...(editorId ? { id: editorId } : {}),
    name: byId("model-name").value,
    target: byId("model-target").value,
    title: byId("model-title").value,
    version: 2,
    rows: [...byId("model-tasks").children].map(row => ({
      id: row.dataset.task,
      title: row.querySelector("[data-title-field]").hidden ? "" : row.querySelector("[data-title]").value,
      persona: row.querySelector("[data-person]").value,
      after: row.querySelector("[data-after]").value,
      wait_for: extraPrerequisites(row),
      conditions: JSON.parse(row.dataset.conditions),
      proof: row.querySelector("[data-proof]").value,
      goal: row.querySelector("[data-goal]").value,
      expected: row.querySelector("[data-result]").value,
    })),
  };
}

function initCampaignEditor() {
  const status = byId("model-status");
  const error = byId("model-error");
  const form = byId("model-form");
  byId("model-new").addEventListener("click", () => { openModel(); byId("model-name").focus(); });
  byId("model-list").addEventListener("change", event => {
    const model = editorData.models.find(item => item.id === event.target.value);
    if (model) openModel(model);
    else form.hidden = true;
  });
  byId("model-target").addEventListener("change", updateModelRows);
  byId("model-add").addEventListener("click", () => {
    modelRow();
    updateModelRows();
    byId("model-tasks").lastElementChild.querySelector("[data-person]").focus();
  });
  async function save(button, duplicate = false, start = false) {
    if (modelBusy || !form.reportValidity()) return;
    const source = editorProject;
    const model = editableModel();
    const destination = duplicate ? byId("model-destination").value : source.id;
    if (duplicate) {
      delete model.id;
      model.name = `${model.name.slice(0, 111)} (copie)`;
    }
    modelBusy = true;
    button.disabled = true;
    showError(error);
    status.textContent = "";
    try {
      const saved = await api(`/api/projects/${destination}/models`, model);
      if (editorProject.id !== source.id) return;
      if (start) {
        const floor = await api(`/api/projects/${source.id}/start`,
          { campaign: saved.id, app_version: byId("model-app-version").value });
        location.assign(floor.url);
        return;
      }
      if (destination === source.id) {
        await selectProject(source);
        if (editorProject.id !== source.id) return;
        openModel(saved);
      }
      status.textContent = duplicate ? "Modèle dupliqué dans le projet sélectionné." : "Modèle enregistré.";
    } catch (err) {
      if (editorProject.id === source.id) showError(error, err.message);
    } finally {
      modelBusy = false;
      button.disabled = false;
    }
  }
  form.addEventListener("submit", event => { event.preventDefault(); save(event.submitter); });
  byId("model-start").addEventListener("click", event => save(event.currentTarget, false, true));
  byId("model-duplicate").addEventListener("click", event => save(event.currentTarget, true));
}
