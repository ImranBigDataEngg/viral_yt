// Clip actions: select / zip download / post to connected platforms / make original / translate & dub.
(() => {
  const app = document.getElementById("clipsapp"); if (!app) return;
  const csrf = app.dataset.csrf, $ = id => document.getElementById(id);
  const boxes = [...document.querySelectorAll(".sel")];
  const selected = () => boxes.filter(b => b.checked).map(b => b.value);
  const api = async (path, body) => {
    const r = await fetch(path, {method: body ? "POST" : "GET", credentials: "same-origin",
      headers: body ? {"Content-Type": "application/json", "X-CSRF-Token": csrf} : {}, body: body ? JSON.stringify(body) : undefined});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  };
  const waitJob = async (job, msgEl, okText) => {
    for (;;) {
      await new Promise(r => setTimeout(r, 2500));
      const j = await api(`/api/jobs/${job}`);
      msgEl.textContent = j.error || j.progress || j.status;
      if (j.status === "done") { msgEl.textContent = okText; setTimeout(() => location.reload(), 900); return; }
      if (j.status === "failed") { msgEl.classList.add("danger"); return; }
    }
  };

  // selection
  const refresh = () => {
    const n = selected().length;
    $("selCount").textContent = `${n} selected`;
    $("zipBtn").disabled = $("postBtn").disabled = n === 0;
    document.querySelectorAll(".clipcard").forEach(c => c.classList.toggle("picked", c.querySelector(".sel").checked));
  };
  boxes.forEach(b => b.addEventListener("change", refresh));
  $("selAll").addEventListener("change", e => { boxes.forEach(b => b.checked = e.target.checked); refresh(); });
  $("zipBtn").addEventListener("click", () => { location.href = "/media/zip?ids=" + encodeURIComponent(selected().join(",")); });

  // post
  const dlg = $("postDlg");
  $("postBtn").addEventListener("click", () => { $("postN").textContent = selected().length; dlg.showModal(); });
  const go = $("postGo");
  if (go) go.addEventListener("click", async () => {
    const msg = $("postMsg"); msg.className = "small";
    const targets = [...dlg.querySelectorAll("input[name=acct]:checked")].map(i => {
      const sel = dlg.querySelector(`[data-privacy-for="${i.value}"]`);
      return {account: i.value, privacy: sel ? sel.value : "public"};
    });
    if (!targets.length) { msg.textContent = "Pick at least one destination."; return; }
    if (!$("consent").checked) { msg.textContent = "Please tick the confirmation box."; return; }
    go.disabled = true; msg.textContent = "Starting…";
    try {
      const {job} = await api("/api/publish", {output_ids: selected(), targets, caption: $("caption").value, consent: true});
      await waitJob(job, msg, "Done. Status shows on each clip.");
    } catch (e) { msg.textContent = e.message; msg.classList.add("danger"); }
    go.disabled = false;
  });

  // make original
  const tdlg = $("takeDlg"); let takeFor = null;
  document.querySelectorAll(".act-take").forEach(b => b.addEventListener("click", async () => {
    takeFor = b.dataset.oid; $("takeMsg").textContent = ""; $("takeText").value = "…";
    tdlg.showModal();
    try { $("takeText").value = (await api(`/api/outputs/${takeFor}/suggest-take`)).take; } catch (e) { $("takeText").value = ""; }
  }));
  $("takeGo").addEventListener("click", async () => {
    const msg = $("takeMsg"); msg.className = "small";
    try {
      $("takeGo").disabled = true;
      const {job} = await api(`/api/outputs/${takeFor}/do/take`, {take: $("takeText").value});
      await waitJob(job, msg, "Original version created.");
    } catch (e) { msg.textContent = e.message; msg.classList.add("danger"); }
    $("takeGo").disabled = false;
  });

  // translate & dub
  document.querySelectorAll(".clipcard").forEach(card => {
    const btn = card.querySelector(".act-tr"); if (!btn) return;
    btn.addEventListener("click", async () => {
      const sel = card.querySelector(".lang"), msg = card.querySelector(".act-msg");
      if (!sel.value) { msg.textContent = "Choose a language first."; return; }
      if (sel.selectedOptions[0].dataset.missing) { msg.textContent = `Voice not installed on the server: py scripts\\install.py --voices ${sel.value}`; return; }
      btn.disabled = true; msg.className = "small act-msg";
      try {
        const {job} = await api(`/api/outputs/${card.dataset.oid}/do/translate`, {lang: sel.value});
        await waitJob(job, msg, "Translated version created.");
      } catch (e) { msg.textContent = e.message; msg.classList.add("danger"); }
      btn.disabled = false;
    });
  });
})();
