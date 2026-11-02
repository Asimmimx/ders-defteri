(function () {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const CSRF = $('meta[name="csrf-token"]')?.content || "";

  window.App = {
    csrf: CSRF,
    // action: { label, fn } verilirse bildirimde tıklanabilir buton çıkar (ör. Geri al)
    toast(msg, action) {
      const t = $("#toast");
      if (!t) return;
      t.textContent = msg;
      t.classList.toggle("has-action", !!action);
      if (action) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "toast-action";
        b.textContent = action.label;
        b.addEventListener("click", () => {
          t.classList.remove("show");
          action.fn();
        }, { once: true });
        t.appendChild(b);
      }
      t.classList.add("show");
      clearTimeout(t._timer);
      t._timer = setTimeout(() => t.classList.remove("show"), action ? 5000 : 2200);
    },
    async api(url, method = "GET", body) {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF, "X-Requested-With": "fetch" },
        body: body ? JSON.stringify(body) : undefined,
        credentials: "same-origin",
      });
      let data = {};
      try { data = await res.json(); } catch (_) { /* boş yanıt */ }
      if (res.status === 401) { location.reload(); }
      if (!res.ok) throw new Error(data.error || "İşlem başarısız");
      return data;
    },
    parseMoney(text) {
      let s = String(text || "").replace(/[₺\s]|TL/g, "");
      if (!s) return NaN;
      if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
      else if ((s.match(/\./g) || []).length > 1 || /^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
      return parseFloat(s);
    },
    num(v) {
      if (!isFinite(v)) return "";
      const r = Math.round(v * 100) / 100;
      return Number.isInteger(r) ? String(r) : r.toFixed(2).replace(".", ",");
    },
    hours(text) {
      const v = parseFloat(String(text || "").replace(",", "."));
      return isFinite(v) && v > 0 ? v : 0;
    },
    money(v) {
      return (Math.round(v * 100) / 100).toLocaleString("tr-TR", { maximumFractionDigits: 2 }) + " ₺";
    },
  };

  // Tıklanabilir tablo satırları
  document.addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-href]");
    if (row && !e.target.closest("a, button, form, input")) {
      location.href = row.dataset.href;
    }
  });

  // Onay isteyen formlar / butonlar
  document.addEventListener("submit", (e) => {
    const f = e.target;
    const btn = e.submitter;
    const msg = (btn && btn.dataset.confirmBtn) || f.dataset.confirm;
    if (msg && !confirm(msg)) e.preventDefault();
  });

  // Renk seçici
  $$("[data-swatches]").forEach((box) => {
    const input = $('input[type="color"]', box);
    box.addEventListener("click", (e) => {
      const sw = e.target.closest(".swatch");
      if (!sw) return;
      input.value = sw.dataset.color;
      $$(".swatch", box).forEach((s) => s.classList.toggle("selected", s === sw));
    });
    input.addEventListener("input", () => $$(".swatch", box).forEach((s) => s.classList.remove("selected")));
  });

  // Öğrenci formu: özel komisyon alanını göster/gizle
  const ctype = $("[data-commission-type]");
  if (ctype) {
    const valBox = $("[data-commission-value]");
    const sync = () => {
      valBox.hidden = !ctype.value;
      $("input", valBox).placeholder = ctype.value === "fixed" ? "ör. 100 (₺ / ders)" : "ör. 20 (%)";
    };
    ctype.addEventListener("change", sync);
    sync();
  }

  // Ödeme formu: tutarı ders sayısından hesapla
  const pform = $("[data-payment-form]");
  if (pform) {
    const sel = $("#student_id", pform);
    const on = $("#online_count", pform);
    const inp = $("#inperson_count", pform);
    const amount = $("[data-amount]", pform);
    let manual = amount.value.trim() !== "";
    const prices = () => {
      const o = sel.selectedOptions[0];
      return { on: parseFloat(o?.dataset.online || 0), inp: parseFloat(o?.dataset.inperson || 0) };
    };
    const recalc = (force) => {
      const p = prices();
      $("[data-price-online]", pform).textContent = p.on ? `Saatlik ${App.money(p.on)}` : "";
      $("[data-price-inperson]", pform).textContent = p.inp ? `Saatlik ${App.money(p.inp)}` : "";
      const total = App.hours(on.value) * p.on + App.hours(inp.value) * p.inp;
      if ((!manual || force) && total > 0) amount.value = App.num(total);
    };
    amount.addEventListener("input", () => { manual = amount.value.trim() !== ""; });
    [on, inp].forEach((el) => el.addEventListener("input", () => { manual = false; recalc(); }));
    sel.addEventListener("change", () => { manual = false; recalc(); });
    recalc();
  }

  // Ödeme detayı: hatırlatma mesajı
  const rem = $("[data-reminder]");
  if (rem) {
    const box = $("[data-msg]", rem);
    const sms = $("[data-sms]", rem);
    const wa = $("[data-wa]", rem);
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const updateLinks = () => {
      const body = encodeURIComponent(box.value);
      if (sms) sms.href = `sms:${sms.dataset.phone}${isIOS ? "&" : "?"}body=${body}`;
      if (wa) wa.href = `https://wa.me/${wa.dataset.phone}?text=${body}`;
    };
    let marked = false;
    const mark = () => {
      if (marked) return;
      marked = true;
      App.api(rem.dataset.markUrl, "POST").catch(() => { marked = false; });
    };
    $$("[data-tpl]", rem).forEach((tab) => tab.addEventListener("click", (e) => {
      e.preventDefault();
      $$("[data-tpl]", rem).forEach((t) => t.classList.toggle("active", t === tab));
      box.value = $(`template[data-tpl-text="${tab.dataset.tpl}"]`, rem).content.textContent;
      updateLinks();
    }));
    box.addEventListener("input", updateLinks);
    updateLinks();
    $("[data-copy]", rem)?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(box.value);
      } catch (_) {
        box.select();
        document.execCommand("copy");
      }
      App.toast("Mesaj kopyalandı");
      mark();
    });
    [sms, wa].forEach((a) => a && a.addEventListener("click", mark));
  }

  // Ödeme tamamlama: komisyonu canlı hesapla
  const comp = $("[data-complete]");
  if (comp) {
    const paid = $("[data-paid]", comp);
    const com = $("[data-commission]", comp);
    let manual = false;
    com.addEventListener("input", () => { manual = true; });
    paid.addEventListener("input", () => {
      if (manual) return;
      const v = App.parseMoney(paid.value);
      const cv = parseFloat(comp.dataset.cvalue) || 0;
      const val = comp.dataset.ctype === "fixed" ? cv * (parseFloat(comp.dataset.lessons) || 0) : (v || 0) * cv / 100;
      com.value = App.num(val);
    });
  }
})();
