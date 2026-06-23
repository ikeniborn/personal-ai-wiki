// CSP-safe IIFE: no inline handlers, no eval, summaries via textContent only.
(function () {
  "use strict";

  const cy_el = document.getElementById("cy");
  if (!cy_el) return;

  const domain_id = cy_el.dataset.domain;
  const default_depth = parseInt(cy_el.dataset.depth || "2", 10);

  const root_sel = document.getElementById("root-select");
  const depth_inp = document.getElementById("depth-range");
  const depth_lbl = document.getElementById("depth-label");
  const type_checks = document.querySelectorAll(".type-check");
  const drawer = document.getElementById("graph-drawer");
  const drawer_title = document.getElementById("drawer-title");
  const drawer_summary = document.getElementById("drawer-summary");
  const drawer_link = document.getElementById("drawer-link");

  let cy = null;

  function selected_types() {
    const types = [];
    type_checks.forEach(function (cb) {
      if (cb.checked) types.push(cb.value);
    });
    return types;
  }

  function build_url() {
    const root = root_sel ? root_sel.value : cy_el.dataset.root;
    const depth = depth_inp ? depth_inp.value : default_depth;
    const types = selected_types();
    let url = "/api/v1/graph?domain_id=" + domain_id + "&root=" + root + "&depth=" + depth;
    if (types.length) url += "&types=" + types.join(",");
    return url;
  }

  function render(data) {
    const elements = [];
    (data.nodes || []).forEach(function (n) {
      elements.push({ data: { id: n.id, label: n.title, summary: n.summary || "" } });
    });
    (data.edges || []).forEach(function (e) {
      elements.push({ data: { id: e.id, source: e.src, target: e.dst, label: e.type } });
    });

    if (cy) cy.destroy();
    cy = cytoscape({
      container: cy_el,
      elements: elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "var(--accent, #5c6bc0)",
            color: "var(--fg, #2b2b3a)",
            "text-valign": "bottom",
            "text-halign": "center",
            "font-size": "11px",
          },
        },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            "curve-style": "bezier",
            "target-arrow-shape": "triangle",
            "font-size": "9px",
            "line-color": "var(--border, #d6d8e6)",
            "target-arrow-color": "var(--border, #d6d8e6)",
          },
        },
      ],
      layout: { name: "cose", animate: false },
    });

    cy.on("tap", "node", function (evt) {
      const node = evt.target;
      if (drawer) drawer.style.display = "block";
      if (drawer_title) drawer_title.textContent = node.data("label");
      if (drawer_summary) drawer_summary.textContent = node.data("summary");
      if (drawer_link) {
        drawer_link.href = "/articles/" + node.data("id");
        drawer_link.textContent = "Open";
      }
    });
  }

  function load() {
    fetch(build_url(), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (err) { console.error("graph fetch error", err); });
  }

  if (depth_inp) {
    if (depth_lbl) depth_lbl.textContent = depth_inp.value;
    depth_inp.addEventListener("input", function () {
      if (depth_lbl) depth_lbl.textContent = depth_inp.value;
    });
    depth_inp.addEventListener("change", load);
  }
  if (root_sel) root_sel.addEventListener("change", load);
  type_checks.forEach(function (cb) { cb.addEventListener("change", load); });

  load();
})();
