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

  // publish (one clip from its card, or the selection) — now or scheduled
  const dlg = $("postDlg");
  let pubIds = [];
  const cardOf = id => document.querySelector(`.clipcard[data-oid="${id}"]`);
  const socialOf = id => { try { return JSON.parse(cardOf(id).dataset.social || "{}"); } catch (e) { return {}; } };
  function openPublish(ids) {
    pubIds = ids;
    $("postHead").textContent = ids.length === 1 ? "Publish this clip" : `Publish ${ids.length} clips`;
    const one = ids.length === 1, msg = $("postMsg"); if (msg) { msg.textContent = ""; msg.className = "small"; }
    if (!$("caption")) { dlg.showModal(); return; }                 // no accounts connected yet
    $("titleRow").hidden = !one;
    if (one) {
      const soc = socialOf(ids[0]);
      $("ptitle").value = soc.title || cardOf(ids[0]).dataset.title || "";
      $("caption").value = soc.caption ? `${soc.caption}\n\n${(soc.hashtags || []).join(" ")}`.trim() : "";
      $("capHint").textContent = "AI-written for this clip, edit freely";
      $("caption").placeholder = "";
    } else {
      $("ptitle").value = ""; $("caption").value = "";
      $("capHint").textContent = "leave empty to use each clip's own AI caption and hashtags";
      $("caption").placeholder = "Optional: one caption for all selected clips";
    }
    dlg.showModal();
  }
  $("postBtn").addEventListener("click", () => openPublish(selected()));
  document.querySelectorAll(".act-pub").forEach(b => b.addEventListener("click", () => openPublish([b.dataset.oid])));
  const whenAt = $("whenAt");
  dlg.querySelectorAll("input[name=when]").forEach(r => r.addEventListener("change", () => {
    const later = dlg.querySelector("input[name=when]:checked").value === "later";
    if (!whenAt) return;
    whenAt.hidden = !later;
    if (later && !whenAt.value) {                                     // default: tomorrow 6 pm local
      const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(18, 0, 0, 0);
      const pad = n => String(n).padStart(2, "0");
      whenAt.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    $("postGo").textContent = later ? "Schedule" : "Publish";
  }));
  const go = $("postGo");
  if (go) go.addEventListener("click", async () => {
    const msg = $("postMsg"); msg.className = "small";
    const targets = [...dlg.querySelectorAll("input[name=acct]:checked")].map(i => {
      const sel = dlg.querySelector(`[data-privacy-for="${i.value}"]`);
      return {account: i.value, privacy: sel ? sel.value : "public"};
    });
    if (!targets.length) { msg.textContent = "Pick at least one destination."; return; }
    if (!$("consent").checked) { msg.textContent = "Please tick the confirmation box."; return; }
    const later = dlg.querySelector("input[name=when]:checked").value === "later";
    let schedule_at = null;
    if (later) {
      const t = new Date(whenAt.value).getTime();                     // datetime-local is the user's local time
      if (!whenAt.value || isNaN(t)) { msg.textContent = "Pick a date and time."; return; }
      schedule_at = Math.round(t / 1000);
    }
    go.disabled = true; msg.textContent = later ? "Scheduling…" : "Starting…";
    try {
      const r = await api("/api/publish", {output_ids: pubIds, targets, caption: $("caption").value,
        title: pubIds.length === 1 ? $("ptitle").value : "", consent: true, schedule_at});
      if (r.scheduled) {
        msg.innerHTML = "";
        msg.append(`Scheduled for ${new Date(r.at * 1000).toLocaleString([], {dateStyle: "medium", timeStyle: "short"})}. `);
        const a = document.createElement("a"); a.href = "/app/calendar"; a.textContent = "Open calendar →"; msg.append(a);
      } else {
        await waitJob(r.job, msg, "Done. Status shows on each clip.");
      }
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
