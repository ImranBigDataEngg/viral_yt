// Review editor: trim (handles or numbers), live caption overlay preview, caption text/timing edits, final render.
(() => {
  const ed = document.getElementById("editor"); if (!ed) return;
  const oid = ed.dataset.oid, csrf = ed.dataset.csrf;
  const $ = id => document.getElementById(id);
  const player = $("player"), ovCap = $("ovCap"), ovHook = $("ovHook"), loading = $("ovLoading");
  const tl = $("timeline"), sel = $("tlSel"), head = $("tlHead"), hIn = $("hIn"), hOut = $("hOut");
  const inStart = $("inStart"), inEnd = $("inEnd"), capsBox = $("caps");
  let S = null;               // server state
  let preview = null;         // {range:[a,b], qd}
  let trim = [0, 0];          // desired source range
  let caps = [];              // [{start,end,text}] output timeline
  let win = [0, 1];           // timeline window (source seconds)
  const HOOK_S = 2.6;

  const api = async (path, body) => {
    const r = await fetch(path, {method: body ? "POST" : "GET", credentials: "same-origin",
      headers: body ? {"Content-Type": "application/json", "X-CSRF-Token": csrf} : {}, body: body ? JSON.stringify(body) : undefined});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  };
  const fmt = t => (Math.round(t * 10) / 10).toFixed(1);
  const esc = s => s.replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  // -------- timeline ---------------------------------------------------------------------------------------
  const pct = t => ((t - win[0]) / (win[1] - win[0])) * 100;
  function drawTimeline() {
    sel.style.left = pct(trim[0]) + "%"; sel.style.width = (pct(trim[1]) - pct(trim[0])) + "%";
    hIn.style.left = pct(trim[0]) + "%"; hOut.style.left = pct(trim[1]) + "%";
    inStart.value = fmt(trim[0]); inEnd.value = fmt(trim[1]);
  }
  function drag(handle, idx) {
    const move = e => {
      const rect = tl.getBoundingClientRect();
      const x = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
      let t = win[0] + x * (win[1] - win[0]);
      if (idx === 0) t = Math.min(t, trim[1] - 3); else t = Math.max(t, trim[0] + 3);
      trim[idx] = Math.round(t * 10) / 10; drawTimeline();
    };
    handle.addEventListener("pointerdown", e => {
      handle.setPointerCapture(e.pointerId); handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", () => handle.removeEventListener("pointermove", move), {once: true});
    });
    handle.addEventListener("keydown", e => {
      const d = e.key === "ArrowLeft" ? -0.1 : e.key === "ArrowRight" ? 0.1 : 0;
      if (d) { trim[idx] = Math.round((trim[idx] + d) * 10) / 10; drawTimeline(); e.preventDefault(); }
    });
  }
  [inStart, inEnd].forEach((inp, i) => inp.addEventListener("change", () => {
    const v = parseFloat(inp.value); if (!isNaN(v)) { trim[i] = v; drawTimeline(); }
  }));

  // -------- captions table ---------------------------------------------------------------------------------
  function renderCaps() {
    capsBox.innerHTML = "";
    caps.forEach((c, i) => {
      const row = document.createElement("div"); row.className = "caprow"; row.dataset.i = i;
      row.innerHTML = `<input class="s" type="number" step="0.05" value="${fmt(c.start)}" aria-label="start">` +
        `<input class="e" type="number" step="0.05" value="${fmt(c.end)}" aria-label="end">` +
        `<input class="t" value="${esc(c.text)}" maxlength="200" aria-label="caption text">` +
        `<button type="button" title="Delete" aria-label="Delete caption">×</button>`;
      row.querySelector(".s").addEventListener("change", e => { c.start = parseFloat(e.target.value) || 0; });
      row.querySelector(".e").addEventListener("change", e => { c.end = parseFloat(e.target.value) || c.start + 0.5; });
      row.querySelector(".t").addEventListener("input", e => { c.text = e.target.value; });
      row.querySelector(".t").addEventListener("focus", () => { player.currentTime = c.start + 0.01; });
      row.querySelector("button").addEventListener("click", () => { caps.splice(i, 1); renderCaps(); });
      capsBox.appendChild(row);
    });
  }
  $("addCap").addEventListener("click", () => {
    const t = player.currentTime || 0;
    caps.push({start: t, end: t + 1.2, text: "New caption"}); caps.sort((a, b) => a.start - b.start); renderCaps();
  });

  // -------- overlay preview (approximates the burned-in ASS styles) ----------------------------------------
  function overlay() {
    const t = player.currentTime || 0, style = $("style").value;
    ovHook.style.display = t < HOOK_S && $("hook").value ? "block" : "none";
    ovHook.textContent = $("hook").value;
    const c = caps.find(x => t >= x.start && t < x.end);
    ovCap.className = "ov-cap " + style;
    if (!c) { ovCap.innerHTML = ""; }
    else {
      const words = c.text.split(/\s+/).filter(Boolean);
      const k = Math.min(words.length - 1, Math.floor(((t - c.start) / Math.max(c.end - c.start, 0.01)) * words.length));
      const html = words.map((w, i) => (style === "clean_lower" ? esc(w) :
        (style === "karaoke_fill" ? (i <= k ? `<span class="hi">${esc(w)}</span>` : esc(w)) :
          (i === k ? `<span class="hi">${esc(w)}</span>` : esc(w))))).join(" ");
      ovCap.innerHTML = style === "boxed" ? `<span class="box">${html}</span>` : html;
    }
    document.querySelectorAll(".caprow").forEach(r => r.classList.toggle("active", caps[r.dataset.i] === c));
    const dur = player.duration || 0;
    $("timeLbl").textContent = `${fmt(t)} / ${fmt(dur)}`;
    if (preview) {
      const src = t < preview.qd ? null : preview.range[0] + (t - preview.qd);
      head.style.display = src === null ? "none" : "block";
      if (src !== null) head.style.left = pct(src) + "%";
    }
    requestAnimationFrame(overlay);
  }
  $("playBtn").addEventListener("click", () => { player.paused ? player.play() : player.pause(); });
  player.addEventListener("play", () => $("playBtn").textContent = "Pause");
  player.addEventListener("pause", () => $("playBtn").textContent = "Play");

  // -------- server calls ------------------------------------------------------------------------------------
  async function loadPreview(body) {
    loading.classList.remove("hidden"); loading.textContent = "Preparing preview…";
    try {
      const r = await api(`/api/outputs/${oid}/preview`, body);
      preview = {range: r.range, qd: r.cold_open_seconds};
      caps = r.captions; renderCaps();
      player.src = r.url; await player.load();
      loading.classList.add("hidden");
    } catch (e) { loading.textContent = "Preview failed: " + e.message; }
  }
  $("previewBtn").addEventListener("click", () => {
    if (trim[1] - trim[0] < 3) { alert("A clip must be at least 3 seconds."); return; }
    loadPreview({start: trim[0], end: trim[1], cold_open: $("cold").checked});
  });
  $("cold").addEventListener("change", () => loadPreview({start: trim[0], end: trim[1], cold_open: $("cold").checked}));

  $("renderBtn").addEventListener("click", async () => {
    const btn = $("renderBtn"), status = $("renderStatus");
    btn.disabled = true; status.textContent = "Queued…";
    try {
      const body = {start: trim[0], end: trim[1], captions: caps.filter(c => c.text.trim()), hook: $("hook").value,
        title: $("title").value, caption_style: $("style").value, cold_open: $("cold").checked};
      const {job} = await api(`/api/outputs/${oid}/render`, body);
      const poll = async () => {
        const j = await api(`/api/jobs/${job}`);
        status.textContent = j.error || j.progress || j.status;
        if (j.status === "done") {
          status.innerHTML = `Done. <a href="/media/${oid}/mp4?download=1">Download MP4</a> · <a href="/media/${oid}/srt?download=1">SRT</a>`;
          btn.disabled = false; return;
        }
        if (j.status === "failed") { btn.disabled = false; return; }
        setTimeout(poll, 2500);
      };
      poll();
    } catch (e) { status.textContent = "Error: " + e.message; btn.disabled = false; }
  });

  // -------- boot ----------------------------------------------------------------------------------------------
  (async () => {
    S = await api(`/api/outputs/${oid}`);
    trim = [...S.range]; win = [Math.max(0, S.range[0] - 30), S.range[1] + 30];
    $("title").value = S.title || ""; $("hook").value = S.hook || ""; $("style").value = S.caption_style;
    $("cold").checked = !!S.cold_open;
    drag(hIn, 0); drag(hOut, 1); drawTimeline();
    await loadPreview({start: trim[0], end: trim[1], cold_open: S.cold_open});
    requestAnimationFrame(overlay);
  })().catch(e => { loading.textContent = "Could not load clip: " + e.message; });
})();
