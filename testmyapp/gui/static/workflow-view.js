"use strict";

function renderWorkflow(container, nodes, open) {
  const key = JSON.stringify(nodes);
  if (container.dataset.rendered === key) return;
  const focusedTask = container.contains(document.activeElement) ? document.activeElement.dataset.workflowTask : null;
  container.dataset.rendered = key;
  const remaining = new Map(nodes.map(node => [node.task, node]));
  const processed = new Set();
  const levels = [];
  const ruleLabels = { success: "Si réussi", failure: "Si échoué", always: "Dans tous les cas",
    required: "Réussite requise" };
  const statusLabels = { waiting: "En attente", running: "En cours", pass: "Réussie", fail: "Échec",
    blocked: "Bloquée", skipped: "Branche non déclenchée", error: "Erreur", abandoned: "Abandon" };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  if (new Set(nodes.map(node => node.name)).size < nodes.length) {
    levels.push(element("p", "Chaque persona effectue une seule tâche à la fois.", "workflow-status"));
  }
  while (remaining.size) {
    const ready = [...remaining.values()].filter(node => (node.after_tasks || []).every(id => processed.has(id)));
    if (ready.length === 0) {
      levels.push(element("p", "Les dépendances forment une boucle ou désignent une tâche absente."));
      break;
    }
    const list = element("ol", "", "workflow-level");
    for (const node of ready) {
      const card = element("li", "", "workflow-node");
      const label = `Tâche ${node.task} · ${node.name}`;
      const available = open && node.status !== "waiting";
      const title = element(available ? "button" : "strong", label, "workflow-task");
      if (available) {
        title.type = "button";
        title.dataset.workflowTask = node.task;
        title.addEventListener("click", () => open(node.session_id));
      }
      card.append(title);
      if (node.scenario) card.append(element("p", node.scenario));
      if (node.status) card.append(element("span", statusLabels[node.status] || node.status, "workflow-status"));
      const edges = node.after_tasks || [];
      if (edges.length) {
        const requirements = element("ul", "", "workflow-edges");
        for (const id of edges) {
          requirements.append(element("li", `↳ Tâche ${id} · ${ruleLabels[node.conditions?.[id] || "required"]}`));
        }
        card.append(requirements);
      } else {
        card.append(element("p", "Dès que possible", "workflow-status"));
      }
      list.append(card);
      remaining.delete(node.task);
      processed.add(node.task);
    }
    levels.push(list);
  }
  container.replaceChildren(...levels);
  if (focusedTask) {
    [...container.querySelectorAll("[data-workflow-task]")].find(node => node.dataset.workflowTask === focusedTask)
      ?.focus({ preventScroll: true });
  }
}
