// EzzyClips Ultra: progressive enhancement only (strict CSP: no inline scripts or style attributes).
document.addEventListener("DOMContentLoaded", () => {
  // confirm dialogs
  document.querySelectorAll("form.confirm").forEach(f => f.addEventListener("submit", e => {
    if (!confirm(f.dataset.confirm || "Are you sure?")) e.preventDefault();
  }));
  // local dates
  document.querySelectorAll("[data-epoch]").forEach(el => {
    const t = parseInt(el.dataset.epoch, 10); if (t) el.textContent = new Date(t * 1000).toLocaleString();
  });
  // score rings & bars (set through CSSOM, which CSP allows)
  document.querySelectorAll(".ring[data-score]").forEach(r => r.style.setProperty("--p", r.dataset.score));
  document.querySelectorAll(".bar[data-w]").forEach(b => b.style.setProperty("--w", b.dataset.w + "%"));

  // dashboard: upload chooser + contextual rights fields
  const fileIn = document.getElementById("fileIn"), urlIn = document.getElementById("urlIn");
  const fields = document.getElementById("rightsFields"), fname = document.getElementById("fileName");
  const isYT = u => /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)\//i.test(u || "");
  const refresh = () => {
    if (!fields) return;
    const needRights = (fileIn && fileIn.files.length) || (urlIn && urlIn.value && !isYT(urlIn.value));
    fields.classList.toggle("hide", !needRights);
  };
  if (fileIn) fileIn.addEventListener("change", () => {
    if (fileIn.files.length) { fname.textContent = "Selected: " + fileIn.files[0].name; if (urlIn) urlIn.value = ""; }
    refresh();
  });
  if (urlIn) urlIn.addEventListener("input", refresh);
  refresh();
  const nj = document.getElementById("newjob");
  if (nj) nj.addEventListener("submit", () => {
    const b = nj.querySelector("button.btn.lg"); if (b) { b.disabled = true; b.textContent = "Starting…"; }
  });

  // job page: live stepper
  const st = document.getElementById("jobstatus");
  if (!st) return;
  const order = ["queued", "rights", "download", "ai", "render", "done"];
  const stage = (status, p) => {
    p = (p || "").toLowerCase();
    if (status === "done") return "done";
    if (status === "waiting") return "rights";
    if (p.includes("render")) return "render";
    if (p.includes("transcrib") || p.includes("highlight") || p.includes("select")) return "ai";
    if (p.includes("download")) return "download";
    if (p.includes("rights")) return "rights";
    return "queued";
  };
  const paint = (status, progress) => {
    const k = stage(status, progress), i = order.indexOf(k);
    document.querySelectorAll("#stepper li").forEach((li, n) => {
      li.classList.toggle("done", n < i || status === "done");
      li.classList.toggle("now", n === i && status !== "done");
      li.classList.toggle("bad", status === "failed" && n === i);
    });
    const pct = status === "done" ? 100 : Math.max(6, Math.round((i / (order.length - 1)) * 100));
    document.getElementById("pfill").style.setProperty("--w", pct + "%");
    st.classList.toggle("failed", status === "failed");
  };
  paint(st.dataset.status, st.dataset.progress);
  if (["queued", "running", "waiting"].includes(st.dataset.status)) {
    const poll = async () => {
      try {
        const r = await fetch(`/api/jobs/${st.dataset.job}`, {credentials: "same-origin"});
        if (r.ok) {
          const j = await r.json();
          paint(j.status, j.progress);
          document.getElementById("jobprogress").textContent = j.error || j.progress || "";
          if (j.status === "done" || j.status === "failed") { location.reload(); return; }
          st.dataset.status = j.status;
        }
      } catch (e) { /* retry */ }
      setTimeout(poll, st.dataset.status === "waiting" ? 30000 : 2500);
    };
    setTimeout(poll, 1500);
  }
});
