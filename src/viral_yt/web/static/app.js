// Small progressive enhancements (CSP: no inline scripts anywhere).
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("form.confirm").forEach(f => f.addEventListener("submit", e => {
    if (!confirm(f.dataset.confirm || "Are you sure?")) e.preventDefault();
  }));
  document.querySelectorAll("[data-epoch]").forEach(el => {
    const t = parseInt(el.dataset.epoch, 10); if (t) el.textContent = new Date(t * 1000).toLocaleString();
  });
  const st = document.getElementById("jobstatus");
  if (st && ["queued", "running", "waiting"].includes(st.dataset.status)) {
    const poll = async () => {
      try {
        const r = await fetch(`/api/jobs/${st.dataset.job}`, {credentials: "same-origin"});
        if (!r.ok) return;
        const j = await r.json();
        document.getElementById("jobpill").textContent = j.status;
        document.getElementById("jobprogress").textContent = j.error || j.progress || "";
        if (j.status === "done" || j.status === "failed") { location.reload(); return; }
      } catch (e) { /* retry */ }
      setTimeout(poll, j_delay());
    };
    const j_delay = () => (st.dataset.status === "waiting" ? 30000 : 3000);
    setTimeout(poll, 2500);
  }
});
