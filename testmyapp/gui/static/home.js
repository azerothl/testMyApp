"use strict";

const byId = id => document.getElementById(id);
const statusLabels = {
  pass: "Réussie", fail: "Échec", error: "Erreur d’exécution",
  interrupted: "Interrompue", archived: "Archivée", running: "En cours",
};
const dateFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "medium" });
const scoreFormat = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const message = byId("message");
const projectList = byId("projects");
const formError = byId("form-error");
const campaignForm = byId("campaign-form");
const campaignSelect = byId("campaign");
const personaOptions = byId("persona-options");
const taskOptions = byId("task-options");
const launchButton = campaignForm.querySelector('button[type="submit"]');
const customTask = "#custom";
let projects = [];
let current = null;
let campaignChoices = [];
let availablePersonas = [];
let requestVersion = 0;
let savedModels = [];
let pinnedReference = null;
const campaignName = id => campaignChoices.find(campaign => campaign.id === id)?.name ||
  savedModels.find(model => model.id === id)?.name || id;

async function api(path, data) {
  const response = await fetch(path, data === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === "string" ? payload.detail : "TestMyApp n’a pas pu répondre. Réessayez.");
  }
  return response.json();
}

function el(tag, label, className) {
  const node = document.createElement(tag);
  if (label) node.textContent = label;
  if (className) node.className = className;
  return node;
}

function showError(node, error = "") {
  node.textContent = error;
  node.hidden = !error;
}

async function launch(button, url, data) {
  button.disabled = true;
  showError(message);
  try {
    const floor = await api(url, data);
    location.assign(floor.url);
  } catch (error) {
    showError(message, error.message);
    message.scrollIntoView({ block: "nearest" });
  } finally {
    button.disabled = false;
  }
}

function actionLink(label, url, className) {
  const link = el("a", label, className);
  link.href = url;
  return link;
}

function runRow(project, run) {
  const row = el("li", "", "run");
  const details = el("div");
  const title = el("strong", campaignName(run.campaign) || "Campagne archivée", "run-name");
  title.title = run.id;
  const meta = el("div", "", "run-meta");
  const date = new Date(run.date);
  if (Number.isFinite(date.getTime())) {
    const time = el("time", dateFormat.format(date));
    time.dateTime = date.toISOString();
    meta.append(time);
  }
  const status = el("span", statusLabels[run.status] || "Verdict indisponible", "status");
  if (Object.hasOwn(statusLabels, run.status)) status.classList.add(run.status);
  meta.append(status);
  if (Number.isFinite(run.score)) meta.append(el("span", scoreFormat.format(run.score) + "/100"));
  if (run.app_version) meta.append(el("span", `Version ${run.app_version}`));
  details.append(title, meta);
  const actions = el("div", "", "run-actions");
  const archive = `/projects/${project.id}/runs/${encodeURIComponent(run.id)}/report`;
  const endpoint = `/api/projects/${project.id}/runs/${encodeURIComponent(run.id)}`;
  if (run.report) actions.append(actionLink("Lire le rapport", archive + "/report.html", "primary"));
  if (run.pdf) actions.append(actionLink("PDF", archive + "/report.pdf", "secondary"));
  if (run.comparable && !run.live) {
    const pin = el("button", pinnedReference?.id === run.id ? "Réépingler" : "Épingler comme référence", "secondary");
    pin.addEventListener("click", async () => {
      pin.disabled = true;
      const source = current;
      try {
        await api(endpoint + "/reference", {});
        if (current?.id === source.id) {
          await selectProject(source);
          byId("compare-runs").open = true;
          byId("compare-before").value = "@reference";
          byId("compare-runs").querySelector("summary").focus();
        }
      } catch (error) {
        if (current?.id === source.id) showError(message, error.message);
      } finally { pin.disabled = false; }
    });
    actions.append(pin);
  }
  if (run.live) actions.append(actionLink("Suivre la campagne", run.live, "primary"));
  if (run.rerunnable && !run.live) {
    const rerun = el("button", "Relancer", "secondary");
    rerun.addEventListener("click", () => launch(rerun, endpoint + "/rerun", {}));
    actions.append(rerun);
  }
  if (run.resumable) {
    const resume = el("button", "Reprendre la campagne", run.report ? "secondary" : "primary");
    resume.addEventListener("click", () => launch(resume, endpoint + "/resume", {}));
    actions.append(resume);
  }
  if (run.retry?.length && !run.live) {
    const recovery = el("details", "", "run-recovery");
    recovery.dataset.recovery = run.id;
    const select = el("select");
    select.setAttribute("aria-label", "Tâche à reprendre");
    select.append(...run.retry.map(task => new Option(task.label, task.id)));
    const retry = el("button", "Reprendre cette tâche", "primary");
    retry.addEventListener("click", () => launch(retry, endpoint + "/tasks/retry", { session: select.value }));
    recovery.append(el("summary", "Reprendre une tâche en échec"),
      el("p", "Les tâches réussies et leurs preuves sont conservées.", "hint"), select, retry);
    actions.append(recovery);
  }
  if (run.replay && !run.live) {
    const replay = el("button", "Rejouer", "secondary");
    replay.addEventListener("click", () => launch(replay, endpoint + "/replay", {}));
    actions.append(replay);
  }
  row.append(details, actions);
  return row;
}

function selectedPersonas() {
  return Array.from(personaOptions.querySelectorAll("input:checked"), input => input.value);
}

function updatePersonaCount() {
  const selection = selectedPersonas();
  const count = selection.length;
  const campaign = campaignChoices.find(item => item.id === campaignSelect.value);
  const missing = Object.entries(campaign?.depends_on || {}).find(([id, dependency]) =>
    selection.includes(id) && !selection.includes(dependency));
  const name = id => availablePersonas.find(persona => persona.id === id)?.name || id;
  launchButton.disabled = count === 0 || Boolean(missing);
  byId("persona-count").textContent = missing ? `La tâche de ${name(missing[0])} dépend de celle de ${name(missing[1])}.`
    : count === 0 ? "Sélectionnez au moins un persona."
    : count === 1 ? "1 persona sélectionné." : `${count} personas sélectionnés.`;
}

function renderTasks() {
  const campaign = campaignChoices.find(item => item.id === campaignSelect.value);
  const selected = selectedPersonas();
  taskOptions.replaceChildren();
  byId("discovery").hidden = !campaign?.tasks?.length || !selected.length;
  if (byId("discovery").hidden) return;
  const key = `testmyapp-tasks/${current.id}/${campaign.id}`;
  const drafts = JSON.parse(localStorage.getItem(key) || "{}");
  const byPersonaId = new Map(availablePersonas.map(persona => [persona.id, persona]));
  for (const id of selected) {
    const persona = byPersonaId.get(id);
    const card = el("div", "", "task-card");
    const owner = /^[aeiouy]/i.test(persona.name.normalize("NFD")) ? `d’${persona.name}` : `de ${persona.name}`;
    const title = el("label", `Tâche ${owner}`);
    const select = el("select");
    select.dataset.scenario = "";
    for (const task of campaign.tasks) {
      const option = el("option", task.name);
      option.value = task.id;
      select.append(option);
    }
    if (campaign.custom_task) {
      const option = el("option", "Créer une tâche libre");
      option.value = customTask;
      select.append(option);
    }
    const preferred = drafts[persona.id]?.scenario || campaign.discovery[persona.id];
    if (Array.from(select.options).some(option => option.value === preferred)) select.value = preferred;
    title.append(select);
    const hint = el("p", "", "hint");
    const dependency = campaign.depends_on?.[id];
    const prerequisite = dependency ? el("p",
      `Ce parcours commence après la réussite de ${byPersonaId.get(dependency)?.name || dependency}.`, "hint") : null;
    const messageLabel = el("label", `Message à saisir pour ${persona.name}`);
    const input = el("input");
    input.maxLength = 1000;
    messageLabel.append(input);
    const goalLabel = el("label", `Objectif ${owner}`);
    const goal = el("input");
    goal.dataset.goal = "";
    goal.maxLength = 500;
    goal.placeholder = "Ouvrir le graphe d’exécution";
    goalLabel.append(goal);
    const expectedLabel = el("label", `Résultat attendu pour ${persona.name}`);
    const expected = el("input");
    expected.dataset.expected = "";
    expected.maxLength = 500;
    expected.placeholder = "Graphe d’exécution";
    expectedLabel.append(expected);
    goal.value = drafts[persona.id]?.goal || "";
    expected.value = drafts[persona.id]?.expected || "";
    function save() {
      drafts[persona.id] = { scenario: select.value };
      if (!input.disabled) drafts[persona.id].text = input.value;
      if (!goal.disabled) {
        drafts[persona.id].goal = goal.value;
        drafts[persona.id].expected = expected.value;
      }
      localStorage.setItem(key, JSON.stringify(drafts));
    }
    function showTask(text) {
      const task = campaign.tasks.find(item => item.id === select.value);
      const free = select.value === customTask;
      hint.textContent = free ? "Décrivez ce que le persona veut accomplir. Indiquez ensuite un texte qui doit devenir visible, ou le libellé d’une option suivi de « coché »." : task?.description;
      goalLabel.hidden = expectedLabel.hidden = goal.disabled = expected.disabled = !free;
      goal.required = expected.required = free;
      messageLabel.hidden = input.disabled = task?.text == null;
      input.required = !input.disabled;
      input.value = text ?? task?.text ?? "";
    }
    showTask(drafts[persona.id]?.scenario === select.value ? drafts[persona.id].text : undefined);
    select.addEventListener("change", () => { showTask(); save(); });
    input.addEventListener("input", save);
    goal.addEventListener("input", save);
    expected.addEventListener("input", save);
    card.dataset.persona = persona.id;
    card.append(title, hint, messageLabel, goalLabel, expectedLabel);
    if (prerequisite) card.append(prerequisite);
    taskOptions.append(card);
  }
}

function renderPersonaOptions(selection) {
  const defaults = campaignChoices.find(campaign => campaign.id === campaignSelect.value)?.personas || [];
  const checked = new Set(selection ?? defaults);
  const byPersonaId = new Map(availablePersonas.map(persona => [persona.id, persona]));
  const order = new Set([...defaults, ...byPersonaId.keys()]);
  const options = [];
  for (const id of order) {
    const persona = byPersonaId.get(id);
    if (!persona) continue;
    const card = el("div", "", "persona-card");
    const label = el("label", "", "persona-option");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.value = id;
    checkbox.checked = checked.has(id);
    checkbox.setAttribute("aria-label", persona.name);
    const avatar = el("span", persona.name.slice(0, 1).toLocaleUpperCase("fr"), "persona-avatar");
    avatar.setAttribute("aria-hidden", "true");
    avatar.dataset.tone = String(options.length % 4);
    const identity = el("span", "", "persona-identity");
    const description = el("span",
      [persona.interaction, persona.pace].filter(Boolean).join(" · "), "persona-summary");
    description.id = `persona-description-${options.length}`;
    checkbox.setAttribute("aria-describedby", description.id);
    identity.append(el("strong", persona.name), description);
    label.append(avatar, identity, checkbox);
    card.append(label);
    const details = el("details", "", "persona-details");
    const summary = el("summary", "Voir le profil");
    summary.setAttribute("aria-label", `Voir le profil de ${persona.name}`);
    details.append(summary);
    if (persona.bio) details.append(el("p", persona.bio));
    if (persona.skill) details.append(el("p", `Aisance numérique : ${persona.skill}.`));
    for (const goal of persona.goals || []) details.append(el("p", `Objectif : ${goal}`));
    if (persona.recovery) details.append(el("p", `En cas de difficulté, a tendance à ${persona.recovery.toLowerCase()}.`));
    card.append(details);
    options.push(card);
  }
  personaOptions.replaceChildren(...options);
  updatePersonaCount();
  renderTasks();
}

function renderDetails(project, data, changingProject) {
  if (!data.available) showError(message, "Ce dossier est introuvable ou ne contient plus les données de test.");
  const selected = changingProject ? "" : campaignSelect.value;
  const selection = selectedPersonas();
  campaignChoices = data.campaigns;
  savedModels = data.models;
  pinnedReference = data.reference;
  renderCampaignEditor(project, data, projects, changingProject);
  availablePersonas = data.personas;
  campaignSelect.replaceChildren(...campaignChoices.map(campaign => {
    const label = campaign.name || (campaign.id === campaign.target ? campaign.id : `${campaign.id} · ${campaign.target}`);
    const option = el("option", label);
    option.value = campaign.id;
    return option;
  }));
  if (campaignChoices.some(campaign => campaign.id === selected)) campaignSelect.value = selected;
  renderPersonaOptions(selected && campaignSelect.value === selected ? selection : undefined);
  byId("campaign-empty").hidden = campaignChoices.length > 0;
  campaignForm.hidden = campaignChoices.length === 0;
  byId("campaign-notes").hidden = data.warnings.length === 0;
  byId("campaign-warnings").replaceChildren(...data.warnings.map(warning => el("li", warning)));
  byId("runs").replaceChildren(...data.runs.map(run => runRow(project, run)));
  const recoverRun = new URLSearchParams(location.search).get("retry");
  const recover = [...byId("runs").querySelectorAll("[data-recovery]")].find(item => item.dataset.recovery === recoverRun);
  if (recover) {
    recover.open = true;
    recover.id = "recovery";
    recover.querySelector("summary").focus();
  }
  renderCompareOptions(data.runs, changingProject);
  byId("runs-empty").hidden = !data.available || data.runs.length > 0;
  const count = data.runs.length;
  const latest = data.runs[0];
  byId("latest-run").hidden = !latest;
  if (latest) {
    byId("latest-status").textContent = statusLabels[latest.status] || "Verdict indisponible";
    const date = new Date(latest.date);
    byId("latest-date").textContent = Number.isFinite(date.getTime()) ? dateFormat.format(date) : "";
    byId("latest-date").dateTime = Number.isFinite(date.getTime()) ? date.toISOString() : "";
  }
  byId("run-count").textContent = count ? `(${count})` : "";
  byId("project-detail").hidden = false;
  byId("history-link").hidden = false;
  byId("preparation-link").hidden = false;
  byId("announcement").textContent = `${project.name}. ${count} campagne${count > 1 ? "s" : ""} dans l’historique.`;
}

function renderCompareOptions(runs, changingProject) {
  const candidates = runs.filter(run => run.comparable && !run.live);
  const panel = byId("compare-runs");
  const controls = byId("reference-controls");
  controls.hidden = !pinnedReference;
  byId("reference-label").textContent = pinnedReference
    ? `Référence enregistrée · ${pinnedReference.context.app_version || "version non indiquée"} · ${dateFormat.format(new Date(pinnedReference.saved_at))}` : "";
  byId("reference-rerun").hidden = !candidates.some(run => run.id === pinnedReference?.id && run.rerunnable);
  panel.hidden = candidates.length < (pinnedReference ? 1 : 2);
  if (changingProject || panel.hidden) {
    panel.open = false;
    byId("comparison").replaceChildren();
  }
  if (panel.hidden) return;
  for (const [id, index] of [["compare-before", 1], ["compare-after", 0]]) {
    const select = byId(id);
    const selected = changingProject ? "" : select.value;
    select.replaceChildren(...candidates.map(run => new Option(
      `${campaignName(run.campaign) || "Campagne archivée"} · ${run.app_version ? run.app_version + " · " : ""}${dateFormat.format(new Date(run.date))}`, run.id)));
    if (id === "compare-before" && pinnedReference) select.prepend(new Option("Référence épinglée", "@reference"));
    const fallback = id === "compare-before" && pinnedReference ? "@reference" : candidates[index]?.id;
    select.value = [...select.options].some(option => option.value === selected) ? selected : fallback;
  }
}

byId("reference-rerun").addEventListener("submit", event => {
  event.preventDefault();
  if (!pinnedReference || !current) return;
  launch(event.submitter, `/api/projects/${current.id}/runs/${encodeURIComponent(pinnedReference.id)}/rerun`,
    { app_version: byId("reference-version").value });
});

function compareContext(result, contexts) {
  const describe = context => [context.app_version || "Version non indiquée",
    context.browsers.join(", ") || "Navigateur non relevé",
    context.starting_data.length ? `${context.starting_data.length} contrôles des données de départ` : "État initial non vérifié"].join(" · ");
  const { before, after } = contexts;
  for (const [label, context] of [["Référence", before], ["Exécution comparée", after]]) {
    result.append(el("p", `${label} · ${describe(context)}`));
    if (context.starting_data.length) {
      const details = el("details");
      details.append(el("summary", `Données de départ · ${label}`));
      const list = el("ul");
      for (const check of context.starting_data) {
        const condition = context.starting_labels?.[`${check.session}:${check.step}`] || `Étape ${check.step}`;
        const count = check.count == null ? "" : ` · ${check.count} trouvé(s), ${check.expected_count} attendu(s)`;
        list.append(el("li", `${condition} · ${check.ok ? "réussi" : "échoué"}${count}`));
      }
      details.append(list);
      result.append(details);
    }
  }
  const checks = el("ul");
  const same = (left, right, label, observed) => checks.append(el("li", !observed ? `${label} · non vérifiés`
    : `${label} · ${JSON.stringify(left) === JSON.stringify(right) ? "identiques" : "différents"}`));
  same(before.configuration, after.configuration, "Réglages de test", before.configuration && after.configuration);
  same(before.browsers, after.browsers, "Navigateurs", before.browsers.length && after.browsers.length);
  same(before.starting_data, after.starting_data, "Contrôles de départ", before.starting_data.length && after.starting_data.length);
  result.append(checks);
}

function compareSteps(article, steps, contexts, project) {
  for (const step of steps) {
    const row = el("section", "", "compare-step");
    row.append(el("h5", `Étape ${step.number}${step.changed ? " · résultat différent" : ""}`));
    const pair = el("div", "", "compare-pair");
    for (const [side, label] of [["before", "Référence"], ["after", "Exécution comparée"]]) {
      const column = el("div");
      const item = step[side];
      const status = { passed: "Réussie", failed: "Échouée", warned: "Avertissement", skipped: "Ignorée" };
      column.append(el("p", `${label} · ${status[item?.status] || "Résultat indisponible"}`));
      if (item?.label) column.append(el("p", item.label));
      if (item?.reason) column.append(el("p", item.reason));
      if (item?.capture) {
        const path = `/projects/${project}/runs/${encodeURIComponent(contexts[side].run)}/captures/${encodeURIComponent(item.capture)}`;
        const link = actionLink("", path);
        const image = el("img");
        image.src = path;
        image.alt = `Capture de l’étape ${step.number} · ${label}`;
        image.loading = "lazy";
        link.append(image);
        column.append(link);
      }
      pair.append(column);
    }
    row.append(pair);
    article.append(row);
  }
}

byId("compare-form").addEventListener("submit", async event => {
  event.preventDefault();
  const version = requestVersion;
  const query = new URLSearchParams({ before: byId("compare-before").value, after: byId("compare-after").value });
  const result = byId("comparison");
  const button = event.submitter;
  const state = value => ({ ...statusLabels, abandoned: "Abandon", blocked: "Bloqué", skipped: "Branche non déclenchée" })[value] || "Absent";
  const score = value => Number.isFinite(value) ? `${scoreFormat.format(value)}/100` : "indisponible";
  button.disabled = true;
  result.textContent = "Comparaison en cours…";
  try {
    const data = await api(`/api/projects/${current.id}/compare?${query}`);
    if (version !== requestVersion) return;
    const { before, after } = data;
    result.replaceChildren(el("p", `${state(before.status)} (${score(before.score)}) → ${state(after.status)} (${score(after.score)})`));
    compareContext(result, data.contexts);
    if (!data.same_campaign) result.append(el("p", "Ces exécutions concernent des campagnes différentes."));
    if (Number.isFinite(before.score) && Number.isFinite(after.score)) {
      const delta = after.score - before.score;
      result.append(el("p", delta ? `Écart de score ${delta > 0 ? "+" : ""}${scoreFormat.format(delta)} points.` : "Score identique."));
    }
    const rows = el("ul");
    for (const row of data.sessions) {
      const persona = availablePersonas.find(item => item.id === row.persona)?.name || row.persona;
      const scenario = row.label || row.scenario;
      const item = el("li");
      const details = el("details");
      const firstChange = row.steps.find(step => step.changed);
      const change = firstChange ? ` · premier écart à l’étape ${firstChange.number}` : "";
      details.append(el("summary", `${persona} · ${scenario} · ${state(row.before)} → ${state(row.after)}${change}`));
      compareSteps(details, row.steps, data.contexts, current.id);
      item.append(details);
      rows.append(item);
    }
    for (const finding of data.new_findings) rows.append(el("li", `Nouveau constat · ${finding}`));
    for (const finding of data.missing_findings) rows.append(el("li", `Constat absent de la seconde exécution · ${finding}`));
    result.append(rows);
  } catch (error) {
    if (version === requestVersion) result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

async function selectProject(project) {
  const changingProject = current?.id !== project.id;
  current = project;
  localStorage.setItem("testmyapp-project", project.id);
  for (const button of projectList.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.project === project.id));
  }
  byId("project-title").textContent = project.name;
  byId("project-location").textContent = project.path;
  if (changingProject) {
    byId("project-detail").hidden = true;
    byId("latest-run").hidden = true;
  }
  showError(message);
  const version = ++requestVersion;
  const panel = byId("project-panel");
  panel.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/projects/${project.id}`);
    if (version === requestVersion) renderDetails(project, data, changingProject);
  } catch (error) {
    if (version === requestVersion) showError(message, error.message);
  } finally {
    if (version === requestVersion) panel.removeAttribute("aria-busy");
  }
}

async function loadProjects(preferred) {
  projects = await api("/api/projects");
  projectList.replaceChildren(...projects.map(project => {
    const item = el("li");
    const button = el("button", project.name, "project-tab");
    button.prepend(el("span", project.name.slice(0, 1).toLocaleUpperCase("fr"), "project-initial"));
    button.firstChild.setAttribute("aria-hidden", "true");
    button.dataset.project = project.id;
    button.addEventListener("click", () => selectProject(project));
    item.append(button);
    return item;
  }));
  byId("projects-empty").hidden = projects.length > 0;
  if (projects.length) {
    await selectProject(projects.find(project => project.id === preferred) || projects[0]);
  } else {
    byId("add-project").open = true;
  }
}

byId("project-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  button.disabled = true;
  showError(formError);
  try {
    const project = await api("/api/projects", Object.fromEntries(new FormData(form)));
    await loadProjects(project.id);
    byId("add-project").open = false;
    form.reset();
    projectList.querySelector('[aria-pressed="true"]')?.focus();
  } catch (error) {
    showError(formError, error.message);
  } finally {
    button.disabled = false;
  }
});

campaignSelect.addEventListener("change", () => renderPersonaOptions());
personaOptions.addEventListener("change", () => { updatePersonaCount(); renderTasks(); });
campaignForm.addEventListener("submit", async event => {
  event.preventDefault();
  const personas = selectedPersonas();
  if (!current || !personas.length) return;
  const picker = byId("persona-picker");
  const data = { campaign: campaignSelect.value, personas, app_version: byId("app-version").value };
  if (taskOptions.children.length) {
    data.tasks = Object.fromEntries(Array.from(taskOptions.children, card => {
      const task = { scenario: card.querySelector("select").value };
      if (task.scenario === customTask) {
        return [card.dataset.persona, { goal: card.querySelector("[data-goal]").value.trim(),
          expected: card.querySelector("[data-expected]").value.trim() }];
      }
      const input = card.querySelector("input");
      if (!input.disabled) task.text = input.value.trim();
      return [card.dataset.persona, task];
    }));
  }
  picker.disabled = byId("discovery").disabled = true;
  try {
    await launch(event.submitter, `/api/projects/${current.id}/start`, data);
  } finally {
    picker.disabled = byId("discovery").disabled = false;
    updatePersonaCount();
  }
});
byId("refresh").addEventListener("click", () => { if (current) selectProject(current); });
window.addEventListener("pageshow", event => { if (event.persisted && current) selectProject(current); });
initCampaignEditor();
loadProjects(new URLSearchParams(location.search).get("project") || localStorage.getItem("testmyapp-project"))
  .catch(error => showError(message, error.message));
