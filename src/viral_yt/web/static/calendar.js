// Content calendar: month grid (desktop) / agenda list (phone) of scheduled, running, posted and failed posts.
(() => {
  const app = document.getElementById("calapp"); if (!app) return;
  const csrf = app.dataset.csrf, $ = id => document.getElementById(id);
  const items = JSON.parse($("calData").textContent || "[]").map(i => ({...i, d: new Date(i.at * 1000)}));
  const PL = {youtube: "YouTube", instagram: "Instagram", tiktok: "TikTok"};
  let cur = new Date(); cur = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const stateOf = i => i.status === "queued" ? (i.scheduled ? "scheduled" : "running") : i.status;
  const label = i => ({scheduled: "Scheduled", running: "Posting", done: "Posted", failed: "Failed", cancelled: "Cancelled"}[stateOf(i)] || i.status);
  const time = d => d.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

  function chip(i) {
    const st = stateOf(i), b = el("button", "chip " + st);
    b.type = "button";
    b.textContent = `${time(i.d)} · ${i.platforms.map(p => PL[p] || p).join(", ")} · ${i.clips.map(c => c.title).join(", ")}`;
    b.title = label(i);
    b.addEventListener("click", () => show(i));
    return b;
  }
  function show(i) {
    const box = $("detail"); box.hidden = false; box.innerHTML = "";
    box.append(el("h3", null, `${label(i)} · ${i.d.toLocaleString([], {dateStyle: "medium", timeStyle: "short"})}`));
    const ul = el("ul");
    i.clips.forEach(c => ul.append(el("li", null, c.title)));
    box.append(ul, el("p", "muted small", "To: " + i.platforms.map(p => PL[p] || p).join(", ")));
    (i.posts || []).forEach(p => {
      const line = el("p", "small");
      line.append(el("span", "plat " + p.platform, PL[p.platform] || p.platform), " ");
      if (p.url) { const a = el("a", null, p.status); a.href = p.url; a.target = "_blank"; a.rel = "noopener"; line.append(a); }
      else line.append(p.status + (p.error ? " — " + p.error : ""));
      box.append(line);
    });
    if (i.error) box.append(el("p", "small danger", i.error));
    if (stateOf(i) === "scheduled") {
      const btn = el("button", "btn sm ghost danger", "Cancel this post"); btn.type = "button";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        const r = await fetch(`/api/calendar/${i.id}/cancel`, {method: "POST", credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: "{}"});
        if (r.ok) { i.status = "cancelled"; draw(); show(i); }
        else { btn.disabled = false; box.append(el("p", "small danger", (await r.json().catch(() => ({}))).error || "Couldn't cancel")); }
      });
      box.append(btn);
    }
    box.scrollIntoView({behavior: "smooth", block: "nearest"});
  }
  function draw() {
    $("monthLbl").textContent = cur.toLocaleDateString([], {month: "long", year: "numeric"});
    const grid = $("cal"); grid.innerHTML = "";
    const start = new Date(cur); start.setDate(1 - ((cur.getDay() + 6) % 7));           // weeks start Monday
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(d => grid.append(el("div", "dow", d)));
    const today = new Date();
    for (let k = 0; k < 42; k++) {
      const day = new Date(start); day.setDate(start.getDate() + k);
      const cell = el("div", "day" + (day.getMonth() !== cur.getMonth() ? " out" : "") + (sameDay(day, today) ? " today" : ""));
      cell.append(el("span", "dnum", day.getDate()));
      items.filter(i => sameDay(i.d, day)).sort((a, b) => a.d - b.d).forEach(i => cell.append(chip(i)));
      grid.append(cell);
    }
    const ag = $("agenda"); ag.innerHTML = "";                                              // phone view
    const month = items.filter(i => i.d.getMonth() === cur.getMonth() && i.d.getFullYear() === cur.getFullYear())
      .sort((a, b) => a.d - b.d);
    if (!month.length) ag.append(el("p", "muted", "No posts this month."));
    month.forEach(i => {
      const it = el("div", "item");
      it.append(el("b", null, i.d.toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"})), chip(i));
      ag.append(it);
    });
  }
  $("prevM").addEventListener("click", () => { cur.setMonth(cur.getMonth() - 1); draw(); });
  $("nextM").addEventListener("click", () => { cur.setMonth(cur.getMonth() + 1); draw(); });
  $("todayBtn").addEventListener("click", () => { const t = new Date(); cur = new Date(t.getFullYear(), t.getMonth(), 1); draw(); });
  draw();
})();
