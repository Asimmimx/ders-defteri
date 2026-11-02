(function () {
  "use strict";
  const grid = document.getElementById("grid");
  if (!grid) return;

  const START = parseInt(grid.dataset.start, 10);
  const END = parseInt(grid.dataset.end, 10);
  const SLOT = parseInt(grid.dataset.slot, 10);
  const DAYS = window.DAYS;
  const SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
  const times = [];
  for (let m = START * 60; m < END * 60; m += SLOT) times.push(m);

  const palette = document.getElementById("palette");
  const trash = document.getElementById("trash");
  const legend = document.getElementById("legend");
  const dialog = document.getElementById("slot-dialog");
  let slots = [];
  let picked = null; // dokunmatik mod: seçili öğrenci

  const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------------------------------------------------- çizim
  function render() {
    const isMobile = window.matchMedia("(max-width: 600px)").matches;
    let html = '<div class="hcell"></div>';
    for (let d = 0; d < 7; d++) {
      const daySlots = slots.filter((s) => s.day === d);
      const filled = new Set(daySlots.map((s) => s.start_min)).size;
      const pct = Math.round((filled / times.length) * 100);
      const free = filled === 0;
      html += `<div class="hcell ${free ? "free" : ""}">${isMobile ? SHORT[d] : DAYS[d]}
        <span class="occ">${free ? "Müsait" : `${filled}/${times.length} dolu · %${pct}`}</span></div>`;
    }
    for (const t of times) {
      html += `<div class="tcell">${hhmm(t)}</div>`;
      for (let d = 0; d < 7; d++) {
        const here = slots.filter((s) => s.day === d && s.start_min === t);
        html += `<div class="cell ${here.length ? "" : "empty-cell"}" data-day="${d}" data-min="${t}">`;
        for (const s of here) {
          const type = s.lesson_type === "online" ? "Online" : "Yüz yüze";
          html += `<div class="lesson ${s.lesson_type === "online" ? "online" : ""}" style="background:${esc(s.color)}" data-id="${s.id}">${esc(s.student_name)}
            <span class="ltype">${type}${s.note ? " · " + esc(s.note) : ""}</span></div>`;
        }
        html += "</div>";
      }
    }
    grid.innerHTML = html;
    renderLegend();
  }

  function renderLegend() {
    const by = new Map();
    for (const s of slots) {
      const e = by.get(s.student_id) || { name: s.student_name, color: s.color, count: 0 };
      e.count++;
      by.set(s.student_id, e);
    }
    const total = slots.length;
    const busy = new Set(slots.map((s) => `${s.day}-${s.start_min}`)).size;
    const cap = times.length * 7;
    const onlineCount = slots.filter((s) => s.lesson_type === "online").length;
    let html = `<span class="muted">Toplam ${total} ders (${total - onlineCount} yüz yüze, ${onlineCount} online) · Haftalık doluluk %${Math.round((busy / cap) * 100)}</span>`;
    for (const e of by.values()) {
      html += `<span><span class="dot" style="background:${esc(e.color)}"></span>${esc(e.name)} (${e.count})</span>`;
    }
    legend.innerHTML = html;
  }

  async function load() {
    try {
      const data = await App.api("/api/program");
      slots = data.slots;
      render();
    } catch (e) {
      App.toast(e.message);
    }
  }

  // ---------------------------------------------------------- işlemler
  async function createSlot(studentId, day, min) {
    try {
      const type = document.querySelector('input[name="new_type"]:checked')?.value || "inperson";
      await App.api("/api/program", "POST", { student_id: studentId, day, start_min: min, lesson_type: type });
      await load();
      App.toast(type === "online" ? "Online ders eklendi" : "Yüz yüze ders eklendi");
    } catch (e) { App.toast(e.message); }
  }

  async function moveSlot(id, day, min) {
    const s = slots.find((x) => x.id === id);
    if (!s || (s.day === day && s.start_min === min)) return;
    s.day = day; s.start_min = min; render(); // iyimser güncelleme
    try {
      await App.api(`/api/program/${id}`, "PATCH", { day, start_min: min });
    } catch (e) { App.toast(e.message); }
    load();
  }

  async function deleteSlot(id) {
    slots = slots.filter((x) => x.id !== id);
    render();
    try {
      await App.api(`/api/program/${id}`, "DELETE");
      App.toast("Ders silindi");
    } catch (e) { App.toast(e.message); load(); }
  }

  function openDialog(id) {
    const s = slots.find((x) => x.id === id);
    if (!s) return;
    document.getElementById("dlg-title").textContent = `${s.student_name} · ${DAYS[s.day]} ${hhmm(s.start_min)}`;
    const typeSel = document.getElementById("dlg-type");
    const note = document.getElementById("dlg-note");
    typeSel.value = s.lesson_type;
    note.value = s.note || "";
    dialog.returnValue = "";
    dialog.onclose = async () => {
      if (dialog.returnValue === "save") {
        try {
          await App.api(`/api/program/${id}`, "PATCH", { lesson_type: typeSel.value, note: note.value });
          load();
        } catch (e) { App.toast(e.message); }
      } else if (dialog.returnValue === "delete") {
        deleteSlot(id);
      }
    };
    dialog.showModal();
  }

  function setPicked(el) {
    document.querySelectorAll(".pal-item.picked").forEach((x) => x.classList.remove("picked"));
    picked = el && el !== picked ? el : null;
    if (picked) {
      picked.classList.add("picked");
      App.toast(`${picked.dataset.name}: şimdi boş bir saate dokunun`);
    }
  }

  // ---------------------------------------------------------- sürükle-bırak (fare + dokunmatik)
  let drag = null;

  function onPointerDown(e) {
    if (e.button !== undefined && e.button !== 0) return;
    const src = e.target.closest(".pal-item, .lesson");
    if (!src) return;
    drag = { src, x: e.clientX, y: e.clientY, active: false, ghost: null, over: null, pointerId: e.pointerId };
    src.setPointerCapture?.(e.pointerId);
  }

  function targetAt(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    return el.closest(".cell") || (trash && el.closest("#trash"));
  }

  function onPointerMove(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    if (!drag.active) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 6) return;
      drag.active = true;
      const g = drag.src.cloneNode(true);
      g.classList.add("drag-ghost");
      g.classList.remove("picked");
      g.style.width = drag.src.offsetWidth + "px";
      document.body.appendChild(g);
      drag.ghost = g;
      drag.src.classList.add("dragging");
    }
    e.preventDefault();
    drag.ghost.style.left = e.clientX + "px";
    drag.ghost.style.top = e.clientY + "px";
    const t = targetAt(e.clientX, e.clientY);
    if (t !== drag.over) {
      drag.over?.classList.remove("over");
      drag.over = t;
      t?.classList.add("over");
    }
  }

  function onPointerUp(e) {
    if (!drag || e.pointerId !== drag.pointerId) return;
    const d = drag;
    drag = null;
    d.ghost?.remove();
    d.src.classList.remove("dragging");
    d.over?.classList.remove("over");
    const isLesson = d.src.classList.contains("lesson");

    if (!d.active) { // tıklama
      if (isLesson) openDialog(parseInt(d.src.dataset.id, 10));
      else setPicked(d.src);
      return;
    }
    const t = d.over;
    if (!t) return;
    if (t.id === "trash") {
      if (isLesson) deleteSlot(parseInt(d.src.dataset.id, 10));
      return;
    }
    const day = parseInt(t.dataset.day, 10);
    const min = parseInt(t.dataset.min, 10);
    if (isLesson) moveSlot(parseInt(d.src.dataset.id, 10), day, min);
    else createSlot(parseInt(d.src.dataset.student, 10), day, min);
  }

  function onPointerCancel() {
    if (!drag) return;
    drag.ghost?.remove();
    drag.src.classList.remove("dragging");
    drag.over?.classList.remove("over");
    drag = null;
  }

  [grid, palette].forEach((el) => el && el.addEventListener("pointerdown", onPointerDown));
  document.addEventListener("pointermove", onPointerMove, { passive: false });
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerCancel);

  // Dokunmatik mod: seçili öğrenciyi boş hücreye yerleştir
  grid.addEventListener("click", (e) => {
    const cell = e.target.closest(".cell");
    if (!cell || e.target.closest(".lesson") || !picked) return;
    createSlot(parseInt(picked.dataset.student, 10), parseInt(cell.dataset.day, 10), parseInt(cell.dataset.min, 10));
  });

  window.addEventListener("resize", () => { clearTimeout(window._rt); window._rt = setTimeout(render, 150); });

  // ---------------------------------------------------------- PNG dışa aktar
  document.getElementById("export-png").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const node = document.getElementById("schedule-export");
    btn.disabled = true;
    node.classList.add("exporting");
    try {
      const w = Math.max(node.scrollWidth, 900);
      const canvas = await html2canvas(node, {
        scale: 2,
        backgroundColor: "#ffffff",
        windowWidth: w + 600,
        onclone: (doc) => {
          const el = doc.getElementById("schedule-export");
          el.style.width = w + "px";
          el.style.minWidth = w + "px";
        },
      });
      const a = document.createElement("a");
      a.download = `haftalik-program-${new Date().toISOString().slice(0, 10)}.png`;
      a.href = canvas.toDataURL("image/png");
      document.body.appendChild(a);
      a.click();
      a.remove();
      App.toast("PNG indirildi");
    } catch (err) {
      App.toast("PNG oluşturulamadı: " + err.message);
    } finally {
      node.classList.remove("exporting");
      btn.disabled = false;
    }
  });

  // Ders türü seçimini hatırla
  try {
    const saved = localStorage.getItem("odeme_new_type");
    const r = saved && document.querySelector(`input[name="new_type"][value="${saved}"]`);
    if (r) r.checked = true;
  } catch (_) { /* depolama kapalı olabilir */ }
  document.querySelectorAll('input[name="new_type"]').forEach((r) => r.addEventListener("change", () => {
    try { localStorage.setItem("odeme_new_type", r.value); } catch (_) { /* yok say */ }
  }));

  load();
})();
