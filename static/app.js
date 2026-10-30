const form = document.getElementById("run-form");
const submitBtn = document.getElementById("submit-btn");
const errorsBox = document.getElementById("form-errors");
const overall = document.getElementById("overall-status");
const stepsEl = document.getElementById("steps");
const resultMsg = document.getElementById("result-message");
const logLines = document.getElementById("log-lines");
const filterInput = document.getElementById("table-filter");

const STEP_LABELS = {};
stepsEl.querySelectorAll(".step").forEach(li => (STEP_LABELS[li.dataset.name] = li.textContent));

let pollTimer = null;
let currentRun = null;

const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

document.getElementById("gen-id").addEventListener("click", () => {
  form.employee_id.value = "E" + String(Date.now()).slice(-6);
});

form.addEventListener("submit", async e => {
  e.preventDefault();
  errorsBox.hidden = true;
  const payload = Object.fromEntries(new FormData(form).entries());

  const clientErrors = [];
  if (!payload.username) clientErrors.push("Username is required.");
  if (!payload.password) clientErrors.push("Password is required.");
  if (!payload.first_name.trim()) clientErrors.push("First name is required.");
  if (!payload.last_name.trim()) clientErrors.push("Last name is required.");
  if (!payload.employee_id.trim()) clientErrors.push("Employee ID is required.");
  if (clientErrors.length) return showErrors(clientErrors);

  resetView();
  setBusy(true);
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      setBusy(false);
      setOverall("idle");
      return showErrors(data.errors || ["Request failed."]);
    }
    currentRun = data.run_id;
    pollTimer = setInterval(poll, 1200);
  } catch (err) {
    setBusy(false);
    setOverall("idle");
    showErrors(["Could not reach the server: " + err.message]);
  }
});

async function poll() {
  try {
    const [statusRes, logRes] = await Promise.all([
      fetch(`/api/status/${currentRun}`),
      fetch(`/api/logs/${currentRun}`),
    ]);
    const job = await statusRes.json();
    const logs = await logRes.json();
    renderSteps(job.steps);
    logLines.textContent = logs.lines.join("\n") || "Starting...";
    logLines.scrollTop = logLines.scrollHeight;

    if (job.status !== "running") {
      clearInterval(pollTimer);
      setBusy(false);
      renderResult(job.result);
    }
  } catch (err) {
    console.error("Polling error", err);
  }
}

function renderSteps(steps) {
  steps.forEach(s => {
    let li = stepsEl.querySelector(`[data-name="${s.name}"]`);
    if (!li) {
      li = document.createElement("li");
      li.dataset.name = s.name;
      STEP_LABELS[s.name] = s.name;
      stepsEl.appendChild(li);
    }
    li.className = `step ${s.status}`;
    const shot = s.screenshot ? ` · <a href="/files/${esc(s.screenshot)}" target="_blank">screenshot</a>` : "";
    const detail = s.detail || s.status === "success" ? `<span class="detail">${s.time} · ${esc(s.detail)}${shot}</span>` : "";
    li.innerHTML = esc(STEP_LABELS[s.name]) + detail;
  });
}

function renderResult(r) {
  setOverall(r.success ? "success" : "failed");
  resultMsg.hidden = false;
  resultMsg.className = `message ${r.success ? "success" : "failed"}`;
  resultMsg.textContent = (r.success ? "✓ " : "✕ ") + r.message;

  const emp = r.employee;
  const verified = r.verified_record;
  const rows = [
    ["First Name", emp.first_name],
    ["Middle Name", emp.middle_name || "—"],
    ["Last Name", emp.last_name],
    ["Employee ID", emp.employee_id],
    ["Verified in PIM", verified
      ? '<span class="pill ok">Yes</span>'
      : '<span class="pill no">No</span>', true],
    ["Run ID", r.run_id],
    ["Started", r.started_at.replace("T", " ")],
    ["Finished", r.finished_at.replace("T", " ")],
  ];
  if (verified) {
    rows.splice(5, 0, ["Job Title", verified["Job Title"] || "—"], ["Employment Status", verified["Employment Status"] || "—"]);
  }
  document.querySelector("#employee-table tbody").innerHTML = rows
    .map(([k, v, raw]) => `<tr><th>${k}</th><td>${raw ? v : esc(v)}</td></tr>`)
    .join("");
  document.getElementById("employee-table").hidden = false;
  document.getElementById("employee-empty").hidden = true;

  const dl = document.getElementById("downloads");
  const links = [];
  if (r.csv_file) links.push(`<a href="/files/${esc(r.csv_file)}">⬇ CSV</a>`);
  if (r.excel_file) links.push(`<a href="/files/${esc(r.excel_file)}">⬇ Excel</a>`);
  dl.innerHTML = links.join("");
  dl.hidden = !links.length;

  renderList(r.employees, emp.employee_id);
}

function renderList(rows, highlightId) {
  const table = document.getElementById("list-table");
  if (!rows || !rows.length) {
    table.hidden = true;
    filterInput.hidden = true;
    document.getElementById("list-empty").hidden = false;
    document.getElementById("list-empty").textContent = "No employee data was extracted.";
    document.getElementById("row-count").textContent = "";
    return;
  }
  const cols = Object.keys(rows[0]);
  table.querySelector("thead").innerHTML = `<tr><th>#</th>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr>`;
  table.querySelector("tbody").innerHTML = rows
    .map((row, i) => `<tr class="${row.Id === highlightId ? "highlight" : ""}"><td>${i + 1}</td>${cols
      .map(c => `<td>${esc(row[c])}</td>`).join("")}</tr>`)
    .join("");
  table.hidden = false;
  filterInput.hidden = false;
  filterInput.value = "";
  document.getElementById("list-empty").hidden = true;
  document.getElementById("row-count").textContent = `(${rows.length} rows)`;
}

filterInput.addEventListener("input", () => {
  const q = filterInput.value.toLowerCase();
  document.querySelectorAll("#list-table tbody tr").forEach(tr => {
    tr.hidden = q && !tr.textContent.toLowerCase().includes(q);
  });
});

function resetView() {
  stepsEl.querySelectorAll(".step").forEach(li => {
    if (!li.dataset.name || !(li.dataset.name in STEP_LABELS)) return;
    if (li.dataset.name.includes("cleanup")) return li.remove();
    li.className = "step pending";
    li.textContent = STEP_LABELS[li.dataset.name];
  });
  resultMsg.hidden = true;
  logLines.textContent = "Starting...";
  setOverall("running");
}

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? "Running automation..." : "Run Automation";
  form.querySelectorAll("input, select, button").forEach(el => (el.disabled = busy));
}

function setOverall(state) {
  overall.className = `badge badge-${state}`;
  overall.textContent = { idle: "Idle", running: "Running", success: "Success", failed: "Failed" }[state];
}

function showErrors(list) {
  errorsBox.innerHTML = `<ul>${list.map(e => `<li>${esc(e)}</li>`).join("")}</ul>`;
  errorsBox.hidden = false;
}
