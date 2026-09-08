const state = { projectId: null, projects: [], graph: null };

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function flash(message, error = false) {
  const target = byId("flash");
  target.textContent = message;
  target.classList.toggle("error", error);
  window.clearTimeout(flash.timer);
  flash.timer = window.setTimeout(() => { target.textContent = ""; }, 6000);
}

function formatDate(value) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function item(title, detail, meta = "", className = "") {
  return `<div class="item ${className}"><div class="item-header"><h3>${escapeHtml(title)}</h3></div>${detail ? `<p>${escapeHtml(detail)}</p>` : ""}${meta ? `<div class="meta">${escapeHtml(meta)}</div>` : ""}</div>`;
}

async function refreshProjects(selectId = state.projectId) {
  state.projects = await request("/projects");
  const picker = byId("project-picker");
  picker.innerHTML = state.projects.length ? state.projects.map((project) => `<option value="${project.id}">${escapeHtml(project.name)}</option>`).join("") : "<option>No projects yet</option>";
  state.projectId = selectId && state.projects.some((project) => project.id === selectId) ? selectId : state.projects[0]?.id || null;
  if (state.projectId) picker.value = state.projectId;
  byId("empty-state").hidden = Boolean(state.projectId);
  byId("workspace").hidden = !state.projectId;
}

function taskOptions(graph) {
  const tasks = graph.nodes.filter((node) => node.kind === "task");
  const target = byId("progress-task");
  target.innerHTML = tasks.length ? tasks.map((task) => `<option value="${task.id}">${escapeHtml(task.label)}</option>`).join("") : '<option value="field-update">Field update (unlinked)</option>';
}

function renderRecommendations(recommendations) {
  const target = byId("recommendations");
  if (!recommendations.length) { target.innerHTML = item("No recommendations yet", "Run intelligence after recording evidence or context."); return; }
  target.innerHTML = recommendations.map((recommendation) => {
    const pending = recommendation.status === "pending_review";
    const actions = pending ? `<div class="review-actions"><button class="button primary" data-review="approved" data-id="${recommendation.id}">Approve and create task</button><button class="button quiet" data-review="rejected" data-id="${recommendation.id}">Reject</button></div>` : `<div class="meta">Reviewed by ${escapeHtml(recommendation.reviewer || "a reviewer")} · ${escapeHtml(recommendation.status)}</div>`;
    return `<div class="item"><div class="item-header"><h3>${escapeHtml(recommendation.title)}</h3><span class="severity ${escapeHtml(recommendation.severity)}">${escapeHtml(recommendation.severity)}</span></div><p>${escapeHtml(recommendation.rationale)}</p><div class="meta">Evidence: ${escapeHtml(recommendation.evidence_ids.join(", "))}</div>${actions}</div>`;
  }).join("");
}

function renderWorkspace({ graph, evidence, recommendations, events, runs }) {
  state.graph = graph;
  const project = graph.project;
  byId("project-summary").innerHTML = `<div><p class="eyebrow">ACTIVE PROJECT</p><h2>${escapeHtml(project.name)}</h2><p>${escapeHtml(project.location || "Location not recorded")}</p></div><div class="project-id">${escapeHtml(project.id)}</div>`;
  const tasks = graph.nodes.filter((node) => node.kind === "task");
  byId("metric-evidence").textContent = evidence.length;
  byId("metric-review").textContent = recommendations.filter((recommendation) => recommendation.status === "pending_review").length;
  byId("metric-tasks").textContent = tasks.length;
  byId("metric-runs").textContent = runs.length;
  taskOptions(graph);
  renderRecommendations(recommendations);
  byId("evidence").innerHTML = evidence.length ? evidence.map((record) => item(record.kind.replaceAll("_", " "), record.source, `${formatDate(record.captured_at)} · ${record.id}`)).join("") : item("No evidence recorded", "Add a field update, document, image, or weather context.");
  byId("agent-runs").innerHTML = runs.length ? runs.map((run) => item(run.agent_name, "Input and output hashes retained.", `${formatDate(run.created_at)} · ${run.id}`)).join("") : item("No agent runs", "Run intelligence to create an auditable trace.");
  byId("timeline").innerHTML = events.length ? events.slice(0, 12).map((event) => item(event.kind.replaceAll("_", " "), "", `${formatDate(event.created_at)} · ${event.subject_id || "project"}`)).join("") : item("No events", "The project timeline will record every material action.");
}

async function loadWorkspace() {
  if (!state.projectId) return;
  try {
    const [graph, evidence, recommendations, events, runs] = await Promise.all([
      request(`/projects/${state.projectId}/graph`), request(`/projects/${state.projectId}/evidence`), request(`/projects/${state.projectId}/recommendations`), request(`/projects/${state.projectId}/timeline`), request(`/projects/${state.projectId}/agent-runs`),
    ]);
    renderWorkspace({ graph, evidence, recommendations, events, runs });
  } catch (error) { flash(error.message, true); }
}

async function setBusy(button, callback) {
  const original = button.textContent;
  button.disabled = true;
  try { await callback(); } catch (error) { flash(error.message, true); } finally { button.disabled = false; button.textContent = original; }
}

async function loadWalkthrough() {
  const project = await request("/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "Bengaluru Corridor Expansion — walkthrough", location: "Bengaluru, Karnataka", metadata: { latitude: 12.9716, longitude: 77.5946, demo_fixture: true } }) });
  const task = await request(`/projects/${project.id}/tasks`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: "Foundation F-12 concrete placement", attributes: { planned_quantity_m3: 42, assigned_crew: "Crew B" } }) });
  await request(`/projects/${project.id}/updates`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task_ref: task.id, reported_percent: 85, planned_quantity: 42, completed_quantity: 35.5, material_units: 60, reporter: "worker.raj@example.com" }) });
  await request(`/projects/${project.id}/context/weather`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source: "demo-fixture:weather", rain_probability: .72, hours_until: 30, summary: "Synthetic walkthrough context; refresh weather for live sourced data." }) });
  await request(`/projects/${project.id}/observations`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source: "demo-fixture:site-gate-01", model: "demo-fixture-not-a-model", observations: [{ label: "temporary_barrier", confidence: .97 }, { label: "access_obstruction", confidence: .88 }] }) });
  await request(`/projects/${project.id}/orchestrate`, { method: "POST" });
  await refreshProjects(project.id);
  await loadWorkspace();
  flash("Synthetic walkthrough loaded. All sample evidence is explicitly marked as a fixture.");
}

function projectFormData(form) {
  const formData = new FormData(form);
  const metadata = {};
  for (const key of ["latitude", "longitude"]) if (formData.get(key)) metadata[key] = Number(formData.get(key));
  return { name: formData.get("name"), location: formData.get("location") || null, metadata };
}

async function submitUpload(event, kind) {
  event.preventDefault();
  if (!state.projectId) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const endpoint = kind === "image" ? "images" : "documents";
  const result = await request(`/projects/${state.projectId}/assets/${endpoint}`, { method: "POST", body: formData });
  form.reset();
  flash(`${kind === "image" ? "Image" : "Document"} stored as local evidence.`);
  await loadWorkspace();
  if (kind === "image" && confirm("Analyze this image with the configured local provider now?")) {
    await request(`/projects/${state.projectId}/assets/images/${result.id}/analyze`, { method: "POST" });
    flash("Local image analysis recorded as evidence.");
    await loadWorkspace();
  }
}

function bindEvents() {
  byId("project-picker").addEventListener("change", async (event) => { state.projectId = event.target.value; await loadWorkspace(); });
  byId("reload").addEventListener("click", () => setBusy(byId("reload"), async () => { await refreshProjects(); await loadWorkspace(); flash("Workspace refreshed."); }));
  byId("open-project").addEventListener("click", () => byId("project-dialog").showModal());
  byId("close-project").addEventListener("click", () => byId("project-dialog").close());
  byId("project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    await setBusy(event.submitter, async () => { const project = await request("/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(projectFormData(form)) }); byId("project-dialog").close(); form.reset(); await refreshProjects(project.id); await loadWorkspace(); flash("Project created. Add a task or field update to begin its record."); });
  });
  byId("walkthrough").addEventListener("click", () => setBusy(byId("walkthrough"), loadWalkthrough));
  byId("task-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement);
    await setBusy(event.submitter, async () => { const attributes = {}; if (form.get("planned_quantity_m3")) attributes.planned_quantity_m3 = Number(form.get("planned_quantity_m3")); await request(`/projects/${state.projectId}/tasks`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ title: form.get("title"), attributes }) }); formElement.reset(); await loadWorkspace(); flash("Work package added to the project graph."); });
  });
  byId("progress-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await setBusy(event.submitter, async () => { await request(`/projects/${state.projectId}/updates`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task_ref: form.get("task_ref"), reported_percent: Number(form.get("reported_percent")), planned_quantity: Number(form.get("planned_quantity")), completed_quantity: Number(form.get("completed_quantity")), reporter: form.get("reporter") }) }); await loadWorkspace(); flash("Field update recorded as evidence."); });
  });
  byId("document-form").addEventListener("submit", (event) => setBusy(event.submitter, () => submitUpload(event, "document")));
  byId("image-form").addEventListener("submit", (event) => setBusy(event.submitter, () => submitUpload(event, "image")));
  byId("weather").addEventListener("click", () => setBusy(byId("weather"), async () => { await request(`/projects/${state.projectId}/context/weather/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ horizon_hours: 48 }) }); await loadWorkspace(); flash("Live weather context recorded with provider evidence."); }));
  byId("orchestrate").addEventListener("click", () => setBusy(byId("orchestrate"), async () => { const result = await request(`/projects/${state.projectId}/orchestrate`, { method: "POST" }); await loadWorkspace(); flash(result.recommendations.length ? `${result.recommendations.length} recommendation(s) require review.` : "No new recommendations for the current evidence."); }));
  byId("recommendations").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-review]"); if (!button) return;
    await setBusy(button, async () => { await request(`/recommendations/${button.dataset.id}/approve`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ reviewer: "workspace.reviewer@example.com", decision: button.dataset.review, comment: "Reviewed in BuildMesh workspace" }) }); await loadWorkspace(); flash(button.dataset.review === "approved" ? "Recommendation approved; the proposed task was created." : "Recommendation rejected and retained in the audit trail."); });
  });
  byId("ask-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const button = event.currentTarget.querySelector("button"); const question = new FormData(event.currentTarget).get("question");
    await setBusy(button, async () => { const answer = await request(`/projects/${state.projectId}/ask`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ question }) }); const target = byId("answer"); target.hidden = false; target.innerHTML = `<strong>${escapeHtml(answer.answer)}</strong><small>${escapeHtml(answer.mode)} · confidence ${Math.round(answer.confidence * 100)}% · evidence: ${escapeHtml(answer.evidence_ids.join(", ") || "none")}</small>`; await loadWorkspace(); });
  });
}

async function start() { bindEvents(); await refreshProjects(); await loadWorkspace(); }
start().catch((error) => flash(error.message, true));
