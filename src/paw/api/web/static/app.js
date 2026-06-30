// CSP-safe: external file, no inline handlers, no eval. Tab toggle + 409 conflict banner.
document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-tab]");
  if (!tab) return;
  const root = tab.closest("[data-tabs]");
  if (!root) return;
  const name = tab.getAttribute("data-tab");
  root.querySelectorAll("[data-panel]").forEach((p) => {
    p.style.display = p.getAttribute("data-panel") === name ? "block" : "none";
  });
});

document.body.addEventListener("htmx:responseError", (e) => {
  if (e.detail.xhr.status === 409) {
    const banner = document.getElementById("conflict-banner");
    if (banner) banner.style.display = "block";
  }
});

// Sidebar parent/child tree filter (CSP-safe: external file, no inline handlers).
document.addEventListener("input", (e) => {
  if (e.target.id !== "tree-filter") return;
  const needle = e.target.value.toLowerCase();
  document.querySelectorAll(".tree-item").forEach((li) => {
    li.style.display = li.dataset.title.includes(needle) ? "" : "none";
  });
});

// Language switcher (CSP-safe: external file, no inline handlers).
document.addEventListener("change", (e) => {
  if (e.target.name !== "ui_language") return;
  e.target.form.requestSubmit();
});

const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function formatJobEvent(eventData) {
  if (typeof eventData === "string") return eventData;
  if (!eventData || typeof eventData !== "object") return String(eventData);

  const pieces = [];
  if (typeof eventData.step === "string") pieces.push(eventData.step);
  if (typeof eventData.status === "string") pieces.push(eventData.status);
  if (typeof eventData.slug === "string") pieces.push(eventData.slug);
  if (typeof eventData.topic === "string") pieces.push(eventData.topic);
  if (typeof eventData.issue_id === "string") pieces.push(eventData.issue_id);
  if (typeof eventData.done === "number" && typeof eventData.total === "number") {
    pieces.push(`${eventData.done}/${eventData.total}`);
  }
  if (typeof eventData.count === "number") pieces.push(String(eventData.count));

  return pieces.length ? pieces.join(" - ") : JSON.stringify(eventData);
}

function appendJobMessage(job, eventData) {
  const messages = job.querySelector("[data-job-messages]");
  if (!messages) return;
  const item = document.createElement("li");
  item.textContent = formatJobEvent(eventData);
  messages.appendChild(item);
}

function updateJobProgress(job, eventData) {
  const progress = job.querySelector("progress");
  if (!progress) return;
  if (eventData && typeof eventData === "object" && TERMINAL_JOB_STATUSES.has(eventData.status)) {
    progress.value = progress.max;
    return;
  }
  progress.value = Math.min(progress.max, Number(progress.value || 0) + 1);
}

function initJobEventStreams(root) {
  if (!window.EventSource) return;
  root.querySelectorAll("[data-job-events]").forEach((job) => {
    if (job.dataset.jobEventsStarted === "true") return;
    job.dataset.jobEventsStarted = "true";
    const source = new EventSource(job.dataset.jobEvents);
    source.addEventListener("message", (event) => {
      let eventData;
      try {
        eventData = JSON.parse(event.data);
      } catch {
        eventData = event.data;
      }
      appendJobMessage(job, eventData);
      updateJobProgress(job, eventData);
      if (eventData && typeof eventData === "object" && TERMINAL_JOB_STATUSES.has(eventData.status)) {
        source.close();
      }
    });
    source.addEventListener("error", () => {
      if (!job.isConnected) source.close();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initJobEventStreams(document);
});

document.body.addEventListener("htmx:afterSwap", (e) => {
  initJobEventStreams(e.detail.target || document);
});
