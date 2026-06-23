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
