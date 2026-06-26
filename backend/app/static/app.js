"use strict";

// Bei abgelaufener/fehlender Anmeldung automatisch zur Login-Seite
const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _fetch(...args);
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Nicht angemeldet");
  }
  return res;
};

const $ = (sel) => document.querySelector(sel);
const euro = (n) => Number(n).toLocaleString("de-DE", { style: "currency", currency: "EUR" });
const todayISO = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const fmtDate = (iso) => {
  const [y, m, d] = String(iso).slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
};
const addDays = (n) => {
  const d = new Date();
  d.setDate(d.getDate() + (parseInt(n, 10) || 0));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const daysFromToday = (iso) => {
  if (!iso) return 0;
  const diff = (new Date(iso + "T00:00") - new Date(todayISO() + "T00:00")) / 86400000;
  return Math.max(0, Math.round(diff));
};

let customersCache = [];
let productsCache = [];
let invoicesCache = [];
let currentIsAdmin = false;
let sortKey = "id", sortDir = -1;

// ---------------------- Theme (Hell/Dunkel) ----------------------
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme-toggle").textContent = theme === "light" ? "☀️" : "🌙";
  localStorage.setItem("theme", theme);
}
$("#theme-toggle").onclick = () =>
  applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
applyTheme(localStorage.getItem("theme") || "dark");

// ---------------------- Navigation ----------------------
const views = {
  dashboard: $("#view-dashboard"),
  new: $("#view-new"),
  history: $("#view-history"),
  customers: $("#view-customers"),
  products: $("#view-products"),
  settings: $("#view-settings"),
  users: $("#view-users"),
};
function show(view) {
  for (const [name, el] of Object.entries(views)) el.hidden = name !== view;
  for (const n of Object.keys(views)) {
    const btn = $(`#nav-${n}`);
    if (btn) btn.classList.toggle("active", n === view);
  }
  if (view === "dashboard") loadDashboard();
  if (view === "history") loadHistory();
  if (view === "customers") loadCustomers();
  if (view === "products") loadProducts();
  if (view === "settings") loadSettings();
  if (view === "users") loadUsers();
}
for (const n of Object.keys(views)) {
  const btn = $(`#nav-${n}`);
  if (btn) btn.onclick = () => show(n);
}

// ---------------------- Positionen ----------------------
const itemsBody = $("#items-body");

function addItemRow(desc = "", qty = 1, price = 0) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="i-desc" type="text" list="product-list" placeholder="Leistung / Artikel" value="${desc}"></td>
    <td><input class="i-qty col-num" type="number" min="0" step="0.01" value="${qty}"></td>
    <td><input class="i-price col-num" type="number" min="0" step="0.01" value="${price}"></td>
    <td class="i-sum col-num">0,00 €</td>
    <td><button type="button" class="remove-item" title="Entfernen">✕</button></td>`;
  tr.querySelector(".remove-item").onclick = () => { tr.remove(); recalc(); };
  const descInput = tr.querySelector(".i-desc");
  descInput.addEventListener("input", () => {
    const p = productsCache.find((x) => x.name === descInput.value);
    if (p) tr.querySelector(".i-price").value = p.unit_price;
    recalc();
  });
  tr.querySelectorAll(".i-qty, .i-price").forEach((inp) => inp.addEventListener("input", recalc));
  itemsBody.appendChild(tr);
  recalc();
}
$("#add-item").onclick = () => addItemRow();

function recalc() {
  let subtotal = 0;
  itemsBody.querySelectorAll("tr").forEach((tr) => {
    const qty = parseFloat(tr.querySelector(".i-qty").value) || 0;
    const price = parseFloat(tr.querySelector(".i-price").value) || 0;
    const sum = qty * price;
    subtotal += sum;
    tr.querySelector(".i-sum").textContent = euro(sum);
  });
  const f = $("#invoice-form");
  const discountPct = parseFloat(f.discount_percent.value) || 0;
  const discount = subtotal * discountPct / 100;
  const net = subtotal - discount;
  const smallBiz = f.small_business.checked;
  const taxRate = smallBiz ? 0 : (parseFloat(f.tax_rate.value) || 0);
  const tax = net * taxRate / 100;
  const total = net + tax;
  const skontoPct = parseFloat(f.skonto_percent.value) || 0;
  const skontoDays = parseInt(f.skonto_days.value, 10) || 0;

  let html = `Zwischensumme: ${euro(subtotal)}`;
  if (discountPct > 0) html += ` &nbsp;|&nbsp; Rabatt ${discountPct}%: −${euro(discount)} → Netto ${euro(net)}`;
  html += `<br>`;
  html += smallBiz ? `<span class="muted-line">Kleinunternehmer – keine MwSt.</span><br>`
                   : `MwSt (${taxRate}%): ${euro(tax)}<br>`;
  html += `<strong>Gesamt: ${euro(total)}</strong>`;
  if (skontoPct > 0 && skontoDays > 0) {
    const skonto = total * skontoPct / 100;
    html += `<br><span class="muted-line">Bei Zahlung innerhalb ${skontoDays} Tagen: ` +
      `${skontoPct}% Skonto (−${euro(skonto)}) → Zahlbetrag ${euro(total - skonto)}</span>`;
  }
  $("#form-totals").innerHTML = html;
}
["tax_rate", "skonto_percent", "skonto_days", "discount_percent"].forEach((n) =>
  $(`[name=${n}]`).addEventListener("input", recalc));
$("[name=small_business]").addEventListener("change", recalc);

// ---------------------- Kunden-Auswahl im Formular ----------------------
function fillCustomerDropdown() {
  const sel = $("#customer-select");
  sel.innerHTML = '<option value="">– neuen Kunden eingeben –</option>';
  for (const c of customersCache) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    sel.appendChild(opt);
  }
}
$("#customer-select").addEventListener("change", (e) => {
  const c = customersCache.find((x) => String(x.id) === e.target.value);
  const f = $("#invoice-form");
  if (c) {
    f.customer_name.value = c.name;
    f.customer_address.value = c.address || "";
    f.due_date.value = addDays(c.payment_term_days);
    f.skonto_percent.value = c.skonto_percent || 0;
    f.skonto_days.value = c.skonto_days || 0;
    $("#save-customer").checked = false;
    recalc();
  }
});

function fillProductDatalist() {
  const dl = $("#product-list");
  dl.innerHTML = "";
  for (const p of productsCache) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.label = euro(p.unit_price);
    dl.appendChild(opt);
  }
}

// E-Mail eines Kunden anhand des Namens finden (für Versand-Vorbelegung)
function emailForCustomer(name) {
  const c = customersCache.find((x) => x.name === name);
  return c && c.email ? c.email : "";
}

// ---------------------- Rechnung speichern ----------------------
$("#invoice-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#form-msg");
  const items = [...itemsBody.querySelectorAll("tr")].map((tr) => ({
    description: tr.querySelector(".i-desc").value.trim(),
    quantity: parseFloat(tr.querySelector(".i-qty").value) || 0,
    unit_price: parseFloat(tr.querySelector(".i-price").value) || 0,
  })).filter((it) => it.description);

  if (!items.length) {
    msg.textContent = "Mindestens eine Position mit Beschreibung nötig.";
    msg.className = "err";
    return;
  }

  const f = e.target;
  const payload = {
    customer_name: f.customer_name.value.trim(),
    customer_address: f.customer_address.value,
    due_date: f.due_date.value || null,
    tax_rate: parseFloat(f.tax_rate.value) || 0,
    notes: f.notes.value,
    skonto_percent: parseFloat(f.skonto_percent.value) || 0,
    skonto_days: parseInt(f.skonto_days.value, 10) || 0,
    discount_percent: parseFloat(f.discount_percent.value) || 0,
    small_business: f.small_business.checked,
    items,
  };

  if ($("#save-customer").checked && payload.customer_name) {
    await fetch("/api/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: payload.customer_name, address: payload.customer_address,
        payment_term_days: daysFromToday(payload.due_date),
        skonto_percent: payload.skonto_percent, skonto_days: payload.skonto_days,
      }),
    });
    await refreshCustomers();
  }

  const res = await fetch("/api/invoices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.ok) {
    const inv = await res.json();
    msg.textContent = `✓ Gespeichert als ${inv.number}`;
    msg.className = "ok";
    f.reset();
    f.issue_date.value = todayISO();
    ["skonto_percent", "skonto_days", "discount_percent"].forEach((n) => f[n].value = 0);
    $("#save-customer").checked = false;
    itemsBody.innerHTML = "";
    addItemRow();
    recalc();
  } else {
    msg.textContent = "Fehler beim Speichern.";
    msg.className = "err";
  }
});

// ---------------------- History ----------------------
async function loadHistory() {
  const q = $("#search").value.trim();
  const url = "/api/invoices" + (q ? `?search=${encodeURIComponent(q)}` : "");
  invoicesCache = await (await fetch(url)).json();
  renderHistory();
}

function renderHistory() {
  const status = $("#filter-status").value;
  let rows = invoicesCache.slice();
  if (status === "__overdue") rows = rows.filter((i) => i.is_overdue);
  else if (status) rows = rows.filter((i) => i.status === status);

  rows.sort((a, b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === "string") { va = va.toLowerCase(); vb = (vb || "").toLowerCase(); }
    return (va > vb ? 1 : va < vb ? -1 : 0) * sortDir;
  });

  const body = $("#history-body");
  body.innerHTML = "";
  $("#history-empty").hidden = rows.length > 0;

  for (const inv of rows) {
    const tr = document.createElement("tr");
    const badge = inv.is_overdue ? "ueberfaellig" : inv.status;
    const label = inv.is_overdue ? "überfällig" : inv.status;
    tr.innerHTML = `
      <td>${inv.number}</td>
      <td>${inv.customer_name}</td>
      <td>${fmtDate(inv.issue_date)}</td>
      <td class="col-num">${euro(inv.total)}</td>
      <td class="col-num">${inv.remaining > 0 ? euro(inv.remaining) : "–"}</td>
      <td><span class="badge ${badge}">${label}</span></td>
      <td class="actions">
        <a class="link" href="/api/invoices/${inv.id}/pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" data-id="${inv.id}" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${inv.status !== "storniert" && inv.remaining > 0 ? `<button class="link" data-act="pay" data-id="${inv.id}" title="Zahlung erfassen">💶 <span>Zahlung</span></button>` : ""}
        ${inv.is_overdue ? `<button class="warn" data-act="remind" data-id="${inv.id}" title="Zahlungserinnerung senden">🔔 <span>Mahnen</span></button>` : ""}
        ${inv.status === "storniert"
          ? `<button class="link" data-act="reopen" data-id="${inv.id}" title="Storno rückgängig">↩️ <span>zurück</span></button>`
          : `<button class="warn" data-act="cancel" data-id="${inv.id}" title="Stornieren">🚫 <span>stornieren</span></button>`}
        <button class="danger" data-act="delete" data-id="${inv.id}" title="Endgültig löschen">🗑️ <span>löschen</span></button>
      </td>`;
    body.appendChild(tr);
  }
  body.querySelectorAll("button[data-act]").forEach((btn) => {
    const inv = invoicesCache.find((i) => String(i.id) === btn.dataset.id);
    btn.onclick = () => handleAction(btn.dataset.act, btn.dataset.id, inv);
  });
}

async function handleAction(act, id, inv) {
  if (act === "email" || act === "remind") {
    const prefill = inv ? emailForCustomer(inv.customer_name) : "";
    const to = prompt(act === "remind" ? "Zahlungserinnerung senden an:" : "Rechnung per E-Mail senden an:", prefill);
    if (!to) return;
    const ep = act === "remind" ? "reminder" : "email";
    const res = await fetch(`/api/invoices/${id}/${ep}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to.trim() }),
    });
    if (res.ok) {
      const r = await res.json();
      alert(`✓ ${r.number} an ${r.to} gesendet.\n(Im MailHog-UI ansehen: http://mail.localhost)`);
    } else {
      const e = await res.json().catch(() => ({}));
      alert("Fehler beim Senden: " + (e.detail || res.status));
    }
    return;
  }
  if (act === "pay") {
    const rest = inv ? inv.remaining : 0;
    const amt = prompt(`Zahlungsbetrag (offen: ${euro(rest)}):`, String(rest));
    if (!amt) return;
    const res = await fetch(`/api/invoices/${id}/payment`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: parseFloat(amt.replace(",", ".")) || 0 }),
    });
    if (!res.ok) { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    loadHistory();
    return;
  }
  if (act === "delete") {
    if (!confirm("Rechnung wirklich endgültig löschen?")) return;
    await fetch(`/api/invoices/${id}`, { method: "DELETE" });
  } else {
    const map = { cancel: "storniert", reopen: "offen" };
    await fetch(`/api/invoices/${id}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: map[act] }),
    });
  }
  loadHistory();
}

$("#search").addEventListener("input", () => loadHistory());
$("#filter-status").addEventListener("change", renderHistory);
document.querySelectorAll("#history-table th[data-sort]").forEach((th) => {
  th.style.cursor = "pointer";
  th.onclick = () => {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
    renderHistory();
  };
});

// ---------------------- Monats-Export ----------------------
$("#export-btn").addEventListener("click", () => {
  const month = $("#export-month").value;
  if (!month) { alert("Bitte zuerst einen Monat auswählen."); return; }
  window.location.href = `/api/export?month=${month}`;
});

// ---------------------- Dashboard ----------------------
async function loadDashboard() {
  const s = await (await fetch("/api/stats")).json();
  $("#kpi-grid").innerHTML = [
    ["Umsatz gesamt", euro(s.total_revenue), ""],
    ["Offen", euro(s.open_amount), "blue"],
    ["Überfällig", euro(s.overdue_amount), "red"],
    ["Rechnungen", s.invoice_count, ""],
    ["davon bezahlt", s.paid_count, "green"],
    ["überfällig", s.overdue_count, "red"],
  ].map(([label, val, cls]) =>
    `<div class="kpi ${cls}"><div class="kpi-val">${val}</div><div class="kpi-label">${label}</div></div>`).join("");

  const max = Math.max(1, ...s.months.map((m) => m.revenue));
  $("#chart").innerHTML = s.months.map((m) =>
    `<div class="bar-col" title="${euro(m.revenue)}">
       <div class="bar" style="height:${Math.round(m.revenue / max * 100)}%"></div>
       <div class="bar-val">${m.revenue ? Math.round(m.revenue) : ""}</div>
       <div class="bar-label">${m.label}</div>
     </div>`).join("");
}

// ---------------------- Kunden-Verwaltung ----------------------
async function refreshCustomers() {
  customersCache = await (await fetch("/api/customers")).json();
  fillCustomerDropdown();
}

async function loadCustomers() {
  await refreshCustomers();
  const body = $("#customers-body");
  body.innerHTML = "";
  $("#customers-empty").hidden = customersCache.length > 0;
  for (const c of customersCache) {
    const tr = document.createElement("tr");
    const skonto = c.skonto_percent > 0 && c.skonto_days > 0
      ? `${c.skonto_percent}% / ${c.skonto_days} Tage` : "–";
    tr.innerHTML = `
      <td>${c.name}</td>
      <td>${c.email || "–"}</td>
      <td>${(c.address || "").replace(/\n/g, ", ")}</td>
      <td>${c.payment_term_days} Tage</td>
      <td>${skonto}</td>
      <td class="actions">
        <button class="danger" data-id="${c.id}" title="Kunde löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelector("button").onclick = async () => {
      if (!confirm(`Kunde "${c.name}" löschen?`)) return;
      await fetch(`/api/customers/${c.id}`, { method: "DELETE" });
      loadCustomers();
    };
    body.appendChild(tr);
  }
}

$("#customer-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#customer-msg");
  const res = await fetch("/api/customers", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: f.name.value.trim(), address: f.address.value, email: f.email.value.trim(),
      payment_term_days: parseInt(f.payment_term_days.value, 10) || 0,
      skonto_percent: parseFloat(f.skonto_percent.value) || 0,
      skonto_days: parseInt(f.skonto_days.value, 10) || 0,
    }),
  });
  if (res.ok) {
    msg.textContent = "✓ Gespeichert"; msg.className = "ok";
    f.reset(); f.payment_term_days.value = 14; loadCustomers();
  } else { msg.textContent = "Fehler beim Speichern."; msg.className = "err"; }
});

// ---------------------- Artikel-Verwaltung ----------------------
async function refreshProducts() {
  productsCache = await (await fetch("/api/products")).json();
  fillProductDatalist();
}

async function loadProducts() {
  await refreshProducts();
  const body = $("#products-body");
  body.innerHTML = "";
  $("#products-empty").hidden = productsCache.length > 0;
  for (const p of productsCache) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.name}</td>
      <td class="col-num">${euro(p.unit_price)}</td>
      <td class="actions">
        <button class="danger" data-id="${p.id}" title="Artikel löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelector("button").onclick = async () => {
      if (!confirm(`Artikel "${p.name}" löschen?`)) return;
      await fetch(`/api/products/${p.id}`, { method: "DELETE" });
      loadProducts();
    };
    body.appendChild(tr);
  }
}

$("#product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#product-msg");
  const res = await fetch("/api/products", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: f.name.value.trim(), unit_price: parseFloat(f.unit_price.value) || 0 }),
  });
  if (res.ok) {
    msg.textContent = "✓ Gespeichert"; msg.className = "ok";
    f.reset(); f.unit_price.value = "0"; loadProducts();
  } else { msg.textContent = "Fehler beim Speichern."; msg.className = "err"; }
});

// ---------------------- Firmendaten / Logo ----------------------
async function loadSettings() {
  const s = await (await fetch("/api/settings")).json();
  const f = $("#settings-form");
  for (const k of ["company_name", "email", "phone", "tax_id", "vat_id", "iban", "bic", "address"]) {
    if (f[k]) f[k].value = s[k] || "";
  }
  const img = $("#logo-preview");
  if (s.has_logo) {
    img.src = "/api/settings/logo?ts=" + Date.now();
    img.hidden = false; $("#logo-none").hidden = true;
  } else {
    img.hidden = true; $("#logo-none").hidden = false;
  }
}

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#settings-msg");
  const payload = {};
  for (const k of ["company_name", "email", "phone", "tax_id", "vat_id", "iban", "bic", "address"]) {
    payload[k] = f[k].value;
  }
  const res = await fetch("/api/settings", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) { msg.textContent = "✓ Gespeichert"; msg.className = "ok"; }
  else { const er = await res.json().catch(() => ({})); msg.textContent = "Fehler: " + (er.detail || res.status); msg.className = "err"; }
});

$("#logo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/settings/logo", { method: "POST", body: fd });
  if (res.ok) loadSettings();
  else { const er = await res.json().catch(() => ({})); alert("Fehler: " + (er.detail || res.status)); }
});

// ---------------------- Benutzerverwaltung (Admin) ----------------------
async function loadUsers() {
  const users = await (await fetch("/api/users")).json();
  const body = $("#users-body");
  body.innerHTML = "";
  for (const u of users) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${u.username}</td>
      <td>${u.is_admin ? "👑 Admin" : "Benutzer"}</td>
      <td class="actions">
        <button class="link" data-act="pw" data-id="${u.id}" title="Passwort ändern">🔑 <span>Passwort</span></button>
        <button class="danger" data-act="del" data-id="${u.id}" data-name="${u.username}" title="Benutzer löschen">🗑️ <span>löschen</span></button>
      </td>`;
    body.appendChild(tr);
  }
  body.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.onclick = () => userAction(btn.dataset.act, btn.dataset.id, btn.dataset.name);
  });
}

async function userAction(act, id, name) {
  if (act === "del") {
    if (!confirm(`Benutzer "${name}" wirklich löschen?`)) return;
    const res = await fetch(`/api/users/${id}`, { method: "DELETE" });
    if (!res.ok) { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    loadUsers();
  } else if (act === "pw") {
    const pw = prompt("Neues Passwort (mind. 4 Zeichen):");
    if (!pw) return;
    const res = await fetch(`/api/users/${id}/password`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    alert(res.ok ? "✓ Passwort geändert" : "Fehler beim Ändern");
  }
}

$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#user-msg");
  const res = await fetch("/api/users", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: f.username.value.trim(), password: f.password.value,
      is_admin: $("#user-is-admin").checked,
    }),
  });
  if (res.ok) {
    msg.textContent = "✓ Benutzer angelegt"; msg.className = "ok";
    f.reset(); $("#user-is-admin").checked = false; loadUsers();
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler: " + (err.detail || res.status); msg.className = "err";
  }
});

// ---------------------- Init ----------------------
async function loadCurrentUser() {
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.user) $("#current-user").textContent = me.user;
    currentIsAdmin = !!me.is_admin;
    $("#nav-users").hidden = !currentIsAdmin;
    $("#nav-settings").hidden = !currentIsAdmin;
  } catch (_) { /* fetch leitet bei 401 selbst um */ }
}

$("[name=issue_date]").value = todayISO();
$("#export-month").value = todayISO().slice(0, 7);
addItemRow();
loadCurrentUser();
refreshCustomers();
refreshProducts();
