// Non-blocking jobs: clicking Scrape/Generate/Send starts a background job and
// lets you keep using the app. A persistent status bar (in base.html) polls
// /jobs and shows progress on every page, so drafting keeps running while you
// navigate, review, and send.

const bar = document.getElementById("jobbar");
const barMsg = document.getElementById("jobbar-msg");
const barActions = document.getElementById("jobbar-actions");
const barSpin = document.getElementById("jobbar-spin");
const barDismiss = document.getElementById("jobbar-dismiss");

const KIND_LINK = { scrape: "/contacts", draft: "/drafts", send: "/drafts", discover: "/contacts" };

// Baselines on this page load, so we can show a non-destructive "N new — refresh".
const baselineDrafts = document.querySelectorAll(".draft").length;
const baselineContacts = document.querySelectorAll("table tbody tr").length;
const page = document.body.dataset.page;
const onDraftsPage = page === "drafts";
const onContactsPage = page === "contacts";

let dismissedKey = "";
// Track running scrape/discover jobs so we can auto-reload when they finish.
let watchedJobId = null;
let lastAutoReload = 0;

barDismiss.addEventListener("click", () => {
  bar.classList.add("hidden");
  dismissedKey = bar.dataset.key || "";
});

function show(html, { spin = false, actions = "" } = {}) {
  barMsg.innerHTML = html;
  barActions.innerHTML = actions;
  barSpin.style.display = spin ? "inline-block" : "none";
  bar.classList.remove("hidden");
}

async function pollJobs() {
  let data;
  try {
    data = await (await fetch("/jobs")).json();
  } catch {
    return; // server momentarily unreachable; try again next tick
  }
  const jobs = data.jobs || [];
  const running = jobs.filter((j) => j.status === "running");
  const lastErr = [...jobs].reverse().find((j) => j.status === "error");

  // Auto-reload the contacts page so new contacts appear without manual refresh.
  if (onContactsPage) {
    const contactJob = running.find((j) => j.kind === "scrape" || j.kind === "discover");
    if (contactJob) {
      watchedJobId = contactJob.id;
      // Reload every 30 s while the job is running and new contacts have arrived.
      const now = Date.now();
      if (data.contact_count > baselineContacts && now - lastAutoReload > 30000) {
        lastAutoReload = now;
        window.location.reload();
        return;
      }
    } else if (watchedJobId) {
      // Job just finished — reload once to show all results.
      watchedJobId = null;
      window.location.reload();
      return;
    }
  }

  // "N new — refresh" prompt (non-destructive — you click it, nothing auto-reloads).
  let refreshAction = "";
  if (onDraftsPage && data.draft_count > baselineDrafts) {
    const n = data.draft_count - baselineDrafts;
    refreshAction = `<a href="/drafts" class="jobbar-link">↻ ${n} new draft${n > 1 ? "s" : ""}, refresh</a>`;
  } else if (onContactsPage && data.contact_count > baselineContacts) {
    const n = data.contact_count - baselineContacts;
    refreshAction = `<a href="/contacts" class="jobbar-link">↻ ${n} new contact${n > 1 ? "s" : ""}, refresh</a>`;
  }

  if (running.length) {
    const labels = running.map((j) => escapeHtml(j.label)).join(" + ");
    const j = running[running.length - 1];
    const last = j.last ? ` — <span class="dim">${escapeHtml(j.last)}</span>` : "";
    show(`<b>${labels}…</b>${last}`, { spin: true, actions: refreshAction });
    return;
  }

  // Nothing running: show the most recent finished/errored job (until dismissed).
  const recent = jobs[jobs.length - 1];
  if (lastErr && dismissedKey !== "err:" + lastErr.id) {
    bar.dataset.key = "err:" + lastErr.id;
    show(`<b class="err">${escapeHtml(lastErr.label)} stopped:</b> ${escapeHtml(lastErr.error || "")}`,
         { actions: refreshAction });
  } else if (recent && recent.status === "done" && dismissedKey !== "done:" + recent.id) {
    bar.dataset.key = "done:" + recent.id;
    const link = KIND_LINK[recent.kind]
      ? `<a href="${KIND_LINK[recent.kind]}" class="jobbar-link">View</a>`
      : "";
    show(`<b class="ok">${escapeHtml(recent.label)} — done.</b>`, { actions: refreshAction || link });
  } else if (refreshAction) {
    show("", { actions: refreshAction });
  } else {
    bar.classList.add("hidden");
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

document.querySelectorAll("button.run").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    const redirect = btn.dataset.redirect;
    const confirmMsg = btn.dataset.confirm;
    const queryInputId = btn.dataset.queryInput;

    if (confirmMsg && !window.confirm(confirmMsg)) return;

    // If the button references a query input, validate and bundle it.
    let fetchOpts = { method: "POST" };
    if (queryInputId) {
      const inputEl = document.getElementById(queryInputId);
      const q = inputEl ? inputEl.value.trim() : "";
      if (!q) { inputEl && inputEl.focus(); return; }
      const body = new FormData();
      body.append("query", q);
      fetchOpts.body = body;
    }

    show(`<b>Starting…</b>`, { spin: true });
    try {
      await fetch(`/run/${action}`, fetchOpts);
    } catch (e) {
      show(`<b class="err">Could not start:</b> ${escapeHtml(String(e))}`);
      return;
    }
    if (redirect) {
      window.location.href = redirect;
    } else {
      pollJobs();
    }
  });
});

pollJobs();
setInterval(pollJobs, 2000);

// ── Contacts search + research filter ─────────────────────────────────────
const searchInput = document.getElementById("contact-search");
const researchInput = document.getElementById("research-filter");
const searchCount = document.getElementById("search-count");

if (searchInput) {
  // Intercept browser Ctrl+F / Cmd+F and focus our search box instead.
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "f") {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    }
    if (e.key === "Escape") {
      if (document.activeElement === searchInput) {
        searchInput.value = "";
        searchInput.blur();
      } else if (document.activeElement === researchInput) {
        researchInput.value = "";
        researchInput.blur();
      }
      applyFilters();
    }
  });

  searchInput.addEventListener("input", applyFilters);
  if (researchInput) researchInput.addEventListener("input", applyFilters);

  function applyFilters() {
    const q = (searchInput.value || "").trim().toLowerCase();
    const r = researchInput ? (researchInput.value || "").trim().toLowerCase() : "";
    const rows = document.querySelectorAll("table tbody tr");
    let visible = 0;
    rows.forEach((row) => {
      // General search: match any cell
      const allText = row.textContent.toLowerCase();
      const passesSearch = !q || allText.includes(q);
      // Research filter: match only the research-interests cell (4th td, index 3)
      const interestsCell = row.querySelectorAll("td")[3];
      const interestsText = interestsCell ? interestsCell.textContent.toLowerCase() : "";
      const passesResearch = !r || r.split(/\s+/).every(word => interestsText.includes(word));
      const show = passesSearch && passesResearch;
      row.style.display = show ? "" : "none";
      if (show) visible++;
    });
    if (searchCount) {
      const active = q || r;
      searchCount.textContent = active ? `${visible} of ${rows.length}` : "";
    }
  }
}
