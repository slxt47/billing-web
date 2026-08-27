"use strict";

function cookie(name) {
  const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

// Zentraler fetch-Wrapper:
//  * hängt bei schreibenden Requests das CSRF-Token an (siehe app/security.py)
//  * schickt bei abgelaufener/fehlender Anmeldung zur Login-Seite
const _fetch = window.fetch.bind(window);
const SAFE_METHODS = ["GET", "HEAD", "OPTIONS"];
let csrfReloaded = false;

window.fetch = async (input, init) => {
  const options = init ? { ...init } : {};
  const method = (options.method || "GET").toUpperCase();
  if (!SAFE_METHODS.includes(method)) {
    const token = cookie("csrftoken");
    if (token) {
      const headers = new Headers(options.headers || {});
      headers.set("X-CSRF-Token", token);
      options.headers = headers;
    }
  }
  const res = await _fetch(input, options);
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Nicht angemeldet");
  }
  if (res.status === 403 && !csrfReloaded) {
    const body = await res.clone().json().catch(() => ({}));
    if (String(body.detail || "").startsWith("CSRF")) {
      // Token abgelaufen (z. B. Neustart des Servers): einmal neu laden reicht.
      csrfReloaded = true;
      location.reload();
      throw new Error("CSRF-Token abgelaufen");
    }
  }
  return res;
};

const $ = (sel) => document.querySelector(sel);
// Alles, was aus der Datenbank kommt (Kundenname, Beschreibung, ...), muss vor
// dem Einsetzen in innerHTML entschärft werden – sonst zerlegt schon ein
// Anführungszeichen im Kundennamen die Tabelle.
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ESC_MAP[ch]);
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
let quotesCache = [];
let deliveryCache = [];
let usersCache = [];
let auditCache = [];
let currentIsAdmin = false;
let sortKey = "id", sortDir = -1;
let currentEditInvoiceId = null;
let lockHeartbeatTimer = null;

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
  quotes: $("#view-quotes"),
  delivery: $("#view-delivery"),
  history: $("#view-history"),
  customers: $("#view-customers"),
  products: $("#view-products"),
  settings: $("#view-settings"),
  users: $("#view-users"),
  audit: $("#view-audit"),
  backup: $("#view-backup"),
};
function show(view) {
  if (view !== "new" && currentEditInvoiceId !== null) releaseInvoiceLock();
  // Anwesenheit gilt immer nur für die Ansicht, in der der Beleg offen ist.
  if (currentPresence && PRESENCE_VIEWS[currentPresence.type] !== view) stopPresence();
  for (const [name, el] of Object.entries(views)) el.hidden = name !== view;
  for (const n of Object.keys(views)) {
    const btn = $(`#nav-${n}`);
    if (btn) btn.classList.toggle("active", n === view);
  }
  if (view === "dashboard") loadDashboard();
  if (view === "quotes") loadQuotes();
  if (view === "delivery") loadDeliveryNotes();
  if (view === "history") loadHistory();
  if (view === "customers") loadCustomers();
  if (view === "products") loadProducts();
  if (view === "settings") loadSettings();
  if (view === "users") loadUsers();
  if (view === "audit") loadAuditLog();
  if (view === "backup") loadBackups();
}
for (const n of Object.keys(views)) {
  const btn = $(`#nav-${n}`);
  if (btn) btn.onclick = () => show(n);
}
// "Neue Rechnung" bricht eine laufende Bearbeitung ab (inkl. Sperre) und
// beginnt mit einem leeren Formular. Ein noch nicht abgeschickter Entwurf für
// eine NEUE Rechnung bleibt dagegen stehen – genau dafür ist er da; zum
// Leeren gibt es "Entwurf verwerfen".
$("#nav-new").onclick = () => {
  if (currentEditInvoiceId !== null || $("#invoice-form").invoice_id.value) {
    resetInvoiceForm();
  }
  show("new");
};

// ---------------------- Positionen (Rechnung) ----------------------
const itemsBody = $("#items-body");

function addItemRow(desc = "", qty = 1, price = 0) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="i-desc" type="text" list="product-list" placeholder="Leistung / Artikel"></td>
    <td><input class="i-qty col-num" type="number" min="0" step="0.01"></td>
    <td><input class="i-price col-num" type="number" min="0" step="0.01"></td>
    <td class="i-sum col-num">0,00 €</td>
    <td><button type="button" class="remove-item" title="Entfernen">✕</button></td>`;
  tr.querySelector(".i-desc").value = desc;
  tr.querySelector(".i-qty").value = qty;
  tr.querySelector(".i-price").value = price;
  tr.querySelector(".remove-item").onclick = () => {
    tr.remove(); recalc(); saveDraftSoon("invoice");
  };
  const descInput = tr.querySelector(".i-desc");
  descInput.addEventListener("input", () => {
    const p = productsCache.find((x) => x.name === descInput.value);
    if (p) tr.querySelector(".i-price").value = p.unit_price;
    recalc();
  });
  tr.querySelectorAll(".i-qty, .i-price").forEach((inp) => inp.addEventListener("input", recalc));
  itemsBody.appendChild(tr);
  recalc();
  saveDraftSoon("invoice");
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
  $(`#invoice-form [name=${n}]`).addEventListener("input", recalc));
$("#invoice-form [name=small_business]").addEventListener("change", recalc);

// -------- Kunden-Auswahl (Rechnung, Angebot, Lieferschein) ---------------
// Jedes der drei Belegformulare hat dasselbe Bedienmuster: ein Suchfeld
// filtert die Liste der gespeicherten Kunden, die Auswahl übernimmt die
// Stammdaten ins Formular. Nur aktive Kunden werden angeboten.
const customerPickers = [];

function customerMatches(c, q) {
  if (!q) return true;
  return [c.name, c.contact_person, c.email, c.address]
    .some((v) => String(v || "").toLowerCase().includes(q));
}

function fillPicker(p) {
  const q = p.search.value.trim().toLowerCase();
  const previous = p.select.value || p.pending || "";
  p.select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  p.select.appendChild(placeholder);

  let hits = 0;
  for (const c of customersCache) {
    if (!c.active || !customerMatches(c, q)) continue;
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.contact_person ? `${c.name} — ${c.contact_person}` : c.name;
    p.select.appendChild(opt);
    hits++;
  }
  placeholder.textContent = q && !hits
    ? "– kein Kunde passt zur Suche –"
    : "– neuen Kunden eingeben –";
  const stillThere = [...p.select.options].some((o) => o.value === previous);
  p.select.value = stillThere ? previous : "";
  if (stillThere) p.pending = "";
}

/** Auswahl vormerken, solange die Kundenliste noch nicht geladen ist. */
function setPendingCustomer(selectSel, id) {
  const picker = customerPickers.find((p) => p.select === $(selectSel));
  if (!picker) return;
  picker.pending = id ? String(id) : "";
  fillPicker(picker);
}

function registerCustomerPicker(selectSel, searchSel, apply) {
  const select = $(selectSel), search = $(searchSel);
  if (!select || !search) return;
  const picker = { select, search, apply, pending: "" };
  customerPickers.push(picker);
  search.addEventListener("input", () => fillPicker(picker));
  select.addEventListener("change", () => {
    const c = customersCache.find((x) => String(x.id) === select.value);
    if (c) apply(c);
  });
}

function fillCustomerDropdown() {
  customerPickers.forEach(fillPicker);
}

registerCustomerPicker("#customer-select", "#customer-search", (c) => {
  const f = $("#invoice-form");
  f.customer_name.value = c.name;
  f.customer_address.value = c.address || "";
  f.customer_contact_person.value = c.contact_person || "";
  f.due_date.value = addDays(c.payment_term_days);
  f.skonto_percent.value = c.skonto_percent || 0;
  f.skonto_days.value = c.skonto_days || 0;
  $("#save-customer").checked = false;
  recalc();
  saveDraftSoon("invoice");
});

registerCustomerPicker("#quote-customer-select", "#quote-customer-search", (c) => {
  const f = $("#quote-form");
  f.customer_name.value = c.name;
  f.customer_address.value = c.address || "";
  f.customer_contact_person.value = c.contact_person || "";
  recalcQuote();
  saveDraftSoon("quote");
});

registerCustomerPicker("#delivery-customer-select", "#delivery-customer-search", (c) => {
  const f = $("#delivery-form");
  f.customer_name.value = c.name;
  f.customer_address.value = c.address || "";
  f.customer_contact_person.value = c.contact_person || "";
  saveDraftSoon("delivery");
});

function fillProductDatalist() {
  const active = productsCache.filter((x) => x.active);
  for (const sel of ["#product-list", "#quote-product-list", "#delivery-product-list"]) {
    const dl = $(sel);
    dl.innerHTML = "";
    for (const p of active) {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.label = euro(p.unit_price);
      dl.appendChild(opt);
    }
  }
}

// E-Mail eines Kunden anhand des Namens finden (für Versand-Vorbelegung)
function emailForCustomer(name) {
  const c = customersCache.find((x) => x.name === name);
  return c && c.email ? c.email : "";
}

// ---------------------- Live-Anzeige: wer ist noch hier? ------------------
// Ergänzung zur Bearbeitungssperre: die verhindert zwar, dass zwei Leute
// dieselbe Rechnung gleichzeitig speichern, sagt einem aber während des
// Tippens nichts. Der Heartbeat meldet den eigenen Beleg alle 10 s an den
// Server und bekommt zurück, wer sonst noch darauf ist. Angebote und
// Lieferscheine haben gar keine Sperre – dort ist der Hinweis noch wichtiger.
const PRESENCE_INTERVAL = 10000;
const PRESENCE_VIEWS = { invoice: "new", quote: "quotes", delivery_note: "delivery" };
const PRESENCE_BANNERS = {
  invoice: "#invoice-presence",
  quote: "#quote-presence",
  delivery_note: "#delivery-presence",
};
const DOC_LABEL = {
  invoice: "diese Rechnung",
  quote: "dieses Angebot",
  delivery_note: "diesen Lieferschein",
};

let currentPresence = null;      // { type, id } – höchstens ein Beleg gleichzeitig
let presenceTimer = null;
let presenceMap = {};            // "typ:id" -> [{username, …}] für die Listen

function presenceBanner(type) {
  return $(PRESENCE_BANNERS[type]);
}

function renderPresenceBanner(type, others) {
  const banner = presenceBanner(type);
  if (!banner) return;
  if (!others || !others.length) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  const names = others.map((o) => o.username).join(", ");
  banner.textContent = others.length === 1
    ? `👀 ${names} hat ${DOC_LABEL[type]} gerade ebenfalls geöffnet.`
    : `👀 ${names} haben ${DOC_LABEL[type]} gerade ebenfalls geöffnet.`;
  banner.hidden = false;
}

async function sendPresenceHeartbeat() {
  if (!currentPresence) return;
  const { type, id } = currentPresence;
  try {
    const res = await fetch(`/api/presence/${type}/${id}`, { method: "POST" });
    if (!res.ok) return;
    const data = await res.json();
    // Zwischenzeitlich weitergeklickt? Dann gehört die Antwort nicht mehr hierher.
    if (currentPresence && currentPresence.type === type && currentPresence.id === id) {
      renderPresenceBanner(type, data.others);
    }
  } catch (_) { /* Netzaussetzer: der nächste Heartbeat versucht es erneut */ }
}

function startPresence(type, id) {
  stopPresence();
  currentPresence = { type, id: String(id) };
  sendPresenceHeartbeat();
  presenceTimer = setInterval(sendPresenceHeartbeat, PRESENCE_INTERVAL);
}

function stopPresence() {
  if (presenceTimer) { clearInterval(presenceTimer); presenceTimer = null; }
  if (!currentPresence) return;
  const { type, id } = currentPresence;
  currentPresence = null;
  renderPresenceBanner(type, []);
  // Abmelden ist nur Kosmetik – ohne Lebenszeichen verfällt der Eintrag
  // ohnehin nach 45 s (z. B. wenn der Tab einfach geschlossen wird).
  fetch(`/api/presence/${type}/${id}`, { method: "DELETE" }).catch(() => {});
}

/** Anwesende für die Listenansichten laden (ein Request je Listenaufbau). */
async function refreshPresenceMap() {
  try {
    const rows = await (await fetch("/api/presence")).json();
    presenceMap = {};
    for (const row of rows) presenceMap[`${row.doc_type}:${row.doc_id}`] = row.users;
  } catch (_) { presenceMap = {}; }
}

/** Kleiner Marker für eine Tabellenzeile – leer, wenn dort niemand ist. */
function presenceMarker(type, id) {
  const users = presenceMap[`${type}:${id}`];
  if (!users || !users.length) return "";
  const names = users.map((u) => u.username).join(", ");
  const title = users.length === 1
    ? `${names} hat diesen Beleg gerade geöffnet`
    : `${names} haben diesen Beleg gerade geöffnet`;
  return ` <span class="presence-dot" title="${esc(title)}">👀</span>`;
}

// ---------------------- Rechnung: Bearbeitungssperre ----------------------
function startLockHeartbeat(id) {
  stopLockHeartbeat();
  // Serverseitiges Timeout liegt bei 5 Minuten – alle 90s erneuern reicht.
  lockHeartbeatTimer = setInterval(() => {
    fetch(`/api/invoices/${id}/lock`, { method: "POST" }).catch(() => {});
  }, 90000);
}
function stopLockHeartbeat() {
  if (lockHeartbeatTimer) { clearInterval(lockHeartbeatTimer); lockHeartbeatTimer = null; }
}
function releaseInvoiceLock() {
  stopLockHeartbeat();
  stopPresence();
  if (currentEditInvoiceId !== null) {
    const id = currentEditInvoiceId;
    currentEditInvoiceId = null;
    fetch(`/api/invoices/${id}/lock`, { method: "DELETE" }).catch(() => {});
  }
}

function resetInvoiceForm() {
  releaseInvoiceLock();
  const f = $("#invoice-form");
  f.reset();
  f.invoice_id.value = "";
  f.issue_date.value = todayISO();
  ["skonto_percent", "skonto_days", "discount_percent"].forEach((n) => f[n].value = 0);
  $("#save-customer").checked = false;
  $("#auto-email").checked = false;
  itemsBody.innerHTML = "";
  addItemRow();
  recalc();
  $("#invoice-form-title").textContent = "Neue Rechnung erstellen";
  $("#invoice-submit").textContent = "Rechnung speichern";
  $("#invoice-cancel-edit").hidden = true;
  $("#auto-email-row").hidden = false;
  $("#lock-banner").hidden = true;
  $("#form-msg").textContent = "";
  clearDraft("invoice");
  setPendingCustomer("#customer-select", "");
}
$("#invoice-cancel-edit").addEventListener("click", resetInvoiceForm);

async function openInvoiceForEdit(id) {
  if (currentEditInvoiceId !== null && currentEditInvoiceId !== id) releaseInvoiceLock();
  const lock = await (await fetch(`/api/invoices/${id}/lock`, { method: "POST" })).json();
  if (!lock.editable) {
    alert(`Diese Rechnung wird gerade von "${lock.locked_by}" bearbeitet. Bitte später erneut versuchen.`);
    return;
  }
  const inv = await (await fetch(`/api/invoices/${id}`)).json();
  currentEditInvoiceId = id;

  clearDraft("invoice");
  const f = $("#invoice-form");
  f.invoice_id.value = id;
  f.customer_name.value = inv.customer_name;
  f.customer_address.value = inv.customer_address || "";
  f.customer_contact_person.value = inv.customer_contact_person || "";
  f.issue_date.value = inv.issue_date;
  f.due_date.value = inv.due_date || "";
  f.tax_rate.value = inv.tax_rate;
  f.notes.value = inv.notes || "";
  f.skonto_percent.value = inv.skonto_percent;
  f.skonto_days.value = inv.skonto_days;
  f.discount_percent.value = inv.discount_percent;
  f.small_business.checked = inv.small_business;
  itemsBody.innerHTML = "";
  for (const it of inv.items) addItemRow(it.description, it.quantity, it.unit_price);
  recalc();

  $("#invoice-form-title").textContent = `Rechnung ${inv.number} bearbeiten`;
  $("#invoice-submit").textContent = "Änderungen speichern";
  $("#invoice-cancel-edit").hidden = false;
  $("#auto-email-row").hidden = true;
  $("#lock-banner").hidden = false;
  $("#lock-banner").textContent = `🔒 Rechnung ${inv.number} ist für dich gesperrt, solange du sie bearbeitest.`;
  $("#form-msg").textContent = "";
  startLockHeartbeat(id);
  show("new");
  startPresence("invoice", id);
}

// ---------------------- Rechnung speichern (neu/bearbeiten) ----------------------
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
  const editId = f.invoice_id.value;
  const payload = {
    customer_name: f.customer_name.value.trim(),
    customer_address: f.customer_address.value,
    customer_contact_person: f.customer_contact_person.value.trim(),
    due_date: f.due_date.value || null,
    tax_rate: parseFloat(f.tax_rate.value) || 0,
    notes: f.notes.value,
    skonto_percent: parseFloat(f.skonto_percent.value) || 0,
    skonto_days: parseInt(f.skonto_days.value, 10) || 0,
    discount_percent: parseFloat(f.discount_percent.value) || 0,
    small_business: f.small_business.checked,
    items,
    auto_email: !editId && $("#auto-email").checked,
  };

  if (!editId && $("#save-customer").checked && payload.customer_name) {
    await fetch("/api/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: payload.customer_name, address: payload.customer_address,
        contact_person: payload.customer_contact_person,
        payment_term_days: daysFromToday(payload.due_date),
        skonto_percent: payload.skonto_percent, skonto_days: payload.skonto_days,
      }),
    });
    await refreshCustomers();
  }

  const res = await fetch(editId ? `/api/invoices/${editId}` : "/api/invoices", {
    method: editId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.ok) {
    const inv = await res.json();
    msg.textContent = `✓ Gespeichert als ${inv.number}`;
    msg.className = "ok";
    resetInvoiceForm();
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

// ---------------------- History ----------------------
async function loadHistory() {
  const q = $("#search").value.trim();
  const url = "/api/invoices" + (q ? `?search=${encodeURIComponent(q)}` : "");
  const [rows] = await Promise.all([
    fetch(url).then((r) => r.json()),
    refreshPresenceMap(),
  ]);
  invoicesCache = rows;
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
      <td>${esc(inv.number)}${presenceMarker("invoice", inv.id)}</td>
      <td>${esc(inv.customer_name)}</td>
      <td>${fmtDate(inv.issue_date)}</td>
      <td class="col-num">${euro(inv.total)}</td>
      <td class="col-num">${inv.remaining > 0 ? euro(inv.remaining) : "–"}</td>
      <td><span class="badge ${badge}">${label}</span></td>
      <td class="actions">
        <a class="link" href="/api/invoices/${inv.id}/pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" data-id="${inv.id}" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${inv.status !== "storniert" ? `<button class="link" data-act="edit" data-id="${inv.id}" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${inv.status !== "storniert" && inv.remaining > 0 ? `<button class="link" data-act="pay" data-id="${inv.id}" title="Zahlung erfassen">💶 <span>Zahlung</span></button>` : ""}
        ${inv.status !== "storniert" ? `<button class="link" data-act="to-delivery" data-id="${inv.id}" title="In Lieferschein umwandeln">📦 <span>Lieferschein</span></button>` : ""}
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
  if (act === "edit") { openInvoiceForEdit(id); return; }
  if (act === "to-delivery") {
    if (!confirm(`Rechnung ${inv ? inv.number : id} jetzt in einen Lieferschein umwandeln?`)) return;
    const res = await fetch(`/api/invoices/${id}/convert-to-delivery-note`, { method: "POST" });
    if (res.ok) { alert("✓ Lieferschein erstellt."); show("delivery"); }
    else { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    return;
  }
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

  // Balken werden per DOM gebaut statt über ein style-Attribut: die CSP
  // erlaubt keine Inline-Styles (style-src 'self').
  const max = Math.max(1, ...s.months.map((m) => m.revenue));
  const chart = $("#chart");
  chart.innerHTML = "";
  for (const m of s.months) {
    const col = document.createElement("div");
    col.className = "bar-col";
    col.title = euro(m.revenue);

    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${Math.round(m.revenue / max * 100)}%`;

    const val = document.createElement("div");
    val.className = "bar-val";
    val.textContent = m.revenue ? Math.round(m.revenue) : "";

    const label = document.createElement("div");
    label.className = "bar-label";
    label.textContent = m.label;

    col.append(bar, val, label);
    chart.appendChild(col);
  }
}

// ---------------------- Kunden-Verwaltung ----------------------
async function refreshCustomers() {
  customersCache = await (await fetch("/api/customers")).json();
  fillCustomerDropdown();
}

async function loadCustomers() {
  await refreshCustomers();
  renderCustomers();
}

function renderCustomers() {
  const q0 = $("#customer-list-search").value.trim().toLowerCase();
  const activeFilter = $("#customer-filter-active").value;
  const rows = customersCache.filter((c) =>
    (activeFilter === "" || String(c.active ? 1 : 0) === activeFilter) &&
    (!q0 || customerMatches(c, q0)));

  const body = $("#customers-body");
  body.innerHTML = "";
  $("#customers-empty").hidden = customersCache.length > 0;
  $("#customers-nomatch").hidden = customersCache.length === 0 || rows.length > 0;
  for (const c of rows) {
    const tr = document.createElement("tr");
    if (!c.active) tr.classList.add("inactive-row");
    const skonto = c.skonto_percent > 0 && c.skonto_days > 0
      ? `${c.skonto_percent}% / ${c.skonto_days} Tage` : "–";
    tr.innerHTML = `
      <td>${esc(c.name)}</td>
      <td>${esc(c.email) || "–"}</td>
      <td>${esc(c.contact_person) || "–"}</td>
      <td>${esc((c.address || "").replace(/\n/g, ", "))}</td>
      <td>${c.payment_term_days} Tage</td>
      <td>${skonto}</td>
      <td><span class="badge ${c.active ? "bezahlt" : "inactive"}">${c.active ? "aktiv" : "inaktiv"}</span></td>
      <td class="actions">
        <button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>
        <button class="link" data-act="toggle" title="${c.active ? "Deaktivieren" : "Aktivieren"}">${c.active ? "⏸️" : "▶️"} <span>${c.active ? "deaktivieren" : "aktivieren"}</span></button>
        ${currentIsAdmin ? `<button class="link" data-act="export" title="Daten exportieren (DSGVO Art. 15)">📤 <span>Export</span></button>` : ""}
        ${currentIsAdmin ? `<button class="warn" data-act="anonymize" title="Anonymisieren (DSGVO Art. 17)">🕶️ <span>anonymisieren</span></button>` : ""}
        <button class="danger" data-act="delete" title="Kunde löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => customerAction(btn.dataset.act, c);
    });
    body.appendChild(tr);
  }
}

async function customerAction(act, c) {
  if (act === "delete") {
    if (!confirm(`Kunde "${c.name}" löschen?`)) return;
    await fetch(`/api/customers/${c.id}`, { method: "DELETE" });
    loadCustomers();
  } else if (act === "edit") {
    startCustomerEdit(c);
  } else if (act === "toggle") {
    await fetch(`/api/customers/${c.id}/active`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: !c.active }),
    });
    loadCustomers();
  } else if (act === "export") {
    const res = await fetch(`/api/customers/${c.id}/export`);
    if (!res.ok) { alert("Fehler beim Export."); return; }
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `kunde-${c.id}-export.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  } else if (act === "anonymize") {
    if (!confirm(`Personenbezogene Daten von "${c.name}" unwiderruflich anonymisieren?\nBereits ausgestellte Rechnungen bleiben aus steuerrechtlichen Gründen erhalten.`)) return;
    const res = await fetch(`/api/customers/${c.id}/anonymize`, { method: "POST" });
    if (!res.ok) { alert("Fehler bei der Anonymisierung."); return; }
    loadCustomers();
  }
}

function startCustomerEdit(c) {
  const f = $("#customer-form");
  f.customer_id.value = c.id;
  f.name.value = c.name;
  f.email.value = c.email || "";
  f.contact_person.value = c.contact_person || "";
  f.payment_term_days.value = c.payment_term_days;
  f.skonto_percent.value = c.skonto_percent;
  f.skonto_days.value = c.skonto_days;
  f.address.value = c.address || "";
  $("#customer-submit").textContent = "Änderungen speichern";
  $("#customer-cancel-edit").hidden = false;
}

function resetCustomerForm() {
  const f = $("#customer-form");
  f.reset();
  f.customer_id.value = "";
  f.payment_term_days.value = 14;
  $("#customer-submit").textContent = "Kunde speichern";
  $("#customer-cancel-edit").hidden = true;
  $("#customer-msg").textContent = "";
}
$("#customer-cancel-edit").addEventListener("click", resetCustomerForm);

$("#customer-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#customer-msg");
  const editId = f.customer_id.value;
  const payload = {
    name: f.name.value.trim(), address: f.address.value, email: f.email.value.trim(),
    contact_person: f.contact_person.value.trim(),
    payment_term_days: parseInt(f.payment_term_days.value, 10) || 0,
    skonto_percent: parseFloat(f.skonto_percent.value) || 0,
    skonto_days: parseInt(f.skonto_days.value, 10) || 0,
  };
  const res = await fetch(editId ? `/api/customers/${editId}` : "/api/customers", {
    method: editId ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    msg.textContent = "✓ Gespeichert"; msg.className = "ok";
    resetCustomerForm(); loadCustomers();
  } else { msg.textContent = "Fehler beim Speichern."; msg.className = "err"; }
});

// ---------------------- Artikel-Verwaltung ----------------------
async function refreshProducts() {
  productsCache = await (await fetch("/api/products")).json();
  fillProductDatalist();
}

async function loadProducts() {
  await refreshProducts();
  renderProducts();
}

function renderProducts() {
  const q0 = $("#product-list-search").value.trim().toLowerCase();
  const activeFilter = $("#product-filter-active").value;
  const rows = productsCache.filter((p) =>
    (activeFilter === "" || String(p.active ? 1 : 0) === activeFilter) &&
    (!q0 || String(p.name || "").toLowerCase().includes(q0)));

  const body = $("#products-body");
  body.innerHTML = "";
  $("#products-empty").hidden = productsCache.length > 0;
  $("#products-nomatch").hidden = productsCache.length === 0 || rows.length > 0;
  for (const p of rows) {
    const tr = document.createElement("tr");
    if (!p.active) tr.classList.add("inactive-row");
    tr.innerHTML = `
      <td>${esc(p.name)}</td>
      <td class="col-num">${euro(p.unit_price)}</td>
      <td><span class="badge ${p.active ? "bezahlt" : "inactive"}">${p.active ? "aktiv" : "inaktiv"}</span></td>
      <td class="actions">
        <button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>
        <button class="link" data-act="toggle" title="${p.active ? "Deaktivieren" : "Aktivieren"}">${p.active ? "⏸️" : "▶️"} <span>${p.active ? "deaktivieren" : "aktivieren"}</span></button>
        <button class="danger" data-act="delete" title="Artikel löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => productAction(btn.dataset.act, p);
    });
    body.appendChild(tr);
  }
}

async function productAction(act, p) {
  if (act === "delete") {
    if (!confirm(`Artikel "${p.name}" löschen?`)) return;
    await fetch(`/api/products/${p.id}`, { method: "DELETE" });
    loadProducts();
  } else if (act === "edit") {
    startProductEdit(p);
  } else if (act === "toggle") {
    await fetch(`/api/products/${p.id}/active`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: !p.active }),
    });
    loadProducts();
  }
}

function startProductEdit(p) {
  const f = $("#product-form");
  f.product_id.value = p.id;
  f.name.value = p.name;
  f.unit_price.value = p.unit_price;
  $("#product-submit").textContent = "Änderungen speichern";
  $("#product-cancel-edit").hidden = false;
}

function resetProductForm() {
  const f = $("#product-form");
  f.reset();
  f.product_id.value = "";
  f.unit_price.value = "0";
  $("#product-submit").textContent = "Artikel speichern";
  $("#product-cancel-edit").hidden = true;
  $("#product-msg").textContent = "";
}
$("#product-cancel-edit").addEventListener("click", resetProductForm);

$("#product-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#product-msg");
  const editId = f.product_id.value;
  const payload = { name: f.name.value.trim(), unit_price: parseFloat(f.unit_price.value) || 0 };
  const res = await fetch(editId ? `/api/products/${editId}` : "/api/products", {
    method: editId ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    msg.textContent = "✓ Gespeichert"; msg.className = "ok";
    resetProductForm(); loadProducts();
  } else { msg.textContent = "Fehler beim Speichern."; msg.className = "err"; }
});

// ---------------------- Angebote (Quotes) ----------------------
const quoteItemsBody = $("#quote-items-body");

function addQuoteItemRow(desc = "", qty = 1, price = 0) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="qi-desc" type="text" list="quote-product-list" placeholder="Leistung / Artikel"></td>
    <td><input class="qi-qty col-num" type="number" min="0" step="0.01"></td>
    <td><input class="qi-price col-num" type="number" min="0" step="0.01"></td>
    <td class="qi-sum col-num">0,00 €</td>
    <td><button type="button" class="remove-item" title="Entfernen">✕</button></td>`;
  tr.querySelector(".qi-desc").value = desc;
  tr.querySelector(".qi-qty").value = qty;
  tr.querySelector(".qi-price").value = price;
  tr.querySelector(".remove-item").onclick = () => {
    tr.remove(); recalcQuote(); saveDraftSoon("quote");
  };
  const descInput = tr.querySelector(".qi-desc");
  descInput.addEventListener("input", () => {
    const p = productsCache.find((x) => x.name === descInput.value);
    if (p) tr.querySelector(".qi-price").value = p.unit_price;
    recalcQuote();
  });
  tr.querySelectorAll(".qi-qty, .qi-price").forEach((inp) => inp.addEventListener("input", recalcQuote));
  quoteItemsBody.appendChild(tr);
  recalcQuote();
  saveDraftSoon("quote");
}
$("#quote-add-item").onclick = () => addQuoteItemRow();

function recalcQuote() {
  let subtotal = 0;
  quoteItemsBody.querySelectorAll("tr").forEach((tr) => {
    const qty = parseFloat(tr.querySelector(".qi-qty").value) || 0;
    const price = parseFloat(tr.querySelector(".qi-price").value) || 0;
    const sum = qty * price;
    subtotal += sum;
    tr.querySelector(".qi-sum").textContent = euro(sum);
  });
  const f = $("#quote-form");
  const discountPct = parseFloat(f.discount_percent.value) || 0;
  const discount = subtotal * discountPct / 100;
  const net = subtotal - discount;
  const smallBiz = f.small_business.checked;
  const taxRate = smallBiz ? 0 : (parseFloat(f.tax_rate.value) || 0);
  const tax = net * taxRate / 100;
  const total = net + tax;

  let html = `Zwischensumme: ${euro(subtotal)}`;
  if (discountPct > 0) html += ` &nbsp;|&nbsp; Rabatt ${discountPct}%: −${euro(discount)} → Netto ${euro(net)}`;
  html += `<br>`;
  html += smallBiz ? `<span class="muted-line">Kleinunternehmer – keine MwSt.</span><br>`
                   : `MwSt (${taxRate}%): ${euro(tax)}<br>`;
  html += `<strong>Gesamt: ${euro(total)}</strong>`;
  $("#quote-form-totals").innerHTML = html;
}
["tax_rate", "discount_percent"].forEach((n) =>
  $(`#quote-form [name=${n}]`).addEventListener("input", recalcQuote));
$("#quote-form [name=small_business]").addEventListener("change", recalcQuote);

function startQuoteEdit(q) {
  const f = $("#quote-form");
  clearDraft("quote");
  startPresence("quote", q.id);
  f.quote_id.value = q.id;
  f.customer_name.value = q.customer_name;
  f.customer_address.value = q.customer_address || "";
  f.customer_contact_person.value = q.customer_contact_person || "";
  f.tax_rate.value = q.tax_rate;
  f.valid_until.value = q.valid_until || "";
  f.discount_percent.value = q.discount_percent;
  f.small_business.checked = q.small_business;
  f.notes.value = q.notes || "";
  quoteItemsBody.innerHTML = "";
  for (const it of q.items) addQuoteItemRow(it.description, it.quantity, it.unit_price);
  recalcQuote();
  $("#quote-form-title").textContent = `Angebot ${q.number} bearbeiten`;
  $("#quote-submit").textContent = "Änderungen speichern";
  $("#quote-cancel-edit").hidden = false;
  $("#quote-msg").textContent = "";
}

function resetQuoteForm() {
  const f = $("#quote-form");
  f.reset();
  f.quote_id.value = "";
  quoteItemsBody.innerHTML = "";
  addQuoteItemRow();
  recalcQuote();
  stopPresence();
  $("#quote-form-title").textContent = "Neues Angebot erstellen";
  $("#quote-submit").textContent = "Angebot speichern";
  $("#quote-cancel-edit").hidden = true;
  $("#quote-msg").textContent = "";
  clearDraft("quote");
  setPendingCustomer("#quote-customer-select", "");
}
$("#quote-cancel-edit").addEventListener("click", resetQuoteForm);

$("#quote-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#quote-msg");
  const items = [...quoteItemsBody.querySelectorAll("tr")].map((tr) => ({
    description: tr.querySelector(".qi-desc").value.trim(),
    quantity: parseFloat(tr.querySelector(".qi-qty").value) || 0,
    unit_price: parseFloat(tr.querySelector(".qi-price").value) || 0,
  })).filter((it) => it.description);

  if (!items.length) {
    msg.textContent = "Mindestens eine Position mit Beschreibung nötig.";
    msg.className = "err";
    return;
  }

  const f = e.target;
  const editId = f.quote_id.value;
  const payload = {
    customer_name: f.customer_name.value.trim(),
    customer_address: f.customer_address.value,
    customer_contact_person: f.customer_contact_person.value.trim(),
    valid_until: f.valid_until.value || null,
    tax_rate: parseFloat(f.tax_rate.value) || 0,
    notes: f.notes.value,
    discount_percent: parseFloat(f.discount_percent.value) || 0,
    small_business: f.small_business.checked,
    items,
  };

  const res = await fetch(editId ? `/api/quotes/${editId}` : "/api/quotes", {
    method: editId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.ok) {
    const q = await res.json();
    msg.textContent = `✓ Gespeichert als ${q.number}`;
    msg.className = "ok";
    resetQuoteForm();
    loadQuotes();
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

const QUOTE_BADGE_CLASS = { offen: "offen", angenommen: "bezahlt", abgelehnt: "storniert", umgewandelt: "bezahlt" };

async function loadQuotes() {
  const [rows] = await Promise.all([
    fetch("/api/quotes").then((r) => r.json()),
    refreshPresenceMap(),
  ]);
  quotesCache = rows;
  renderQuotes();
}

function renderQuotes() {
  const q0 = $("#quote-search").value.trim().toLowerCase();
  const status = $("#quote-filter-status").value;
  const rows = quotesCache.filter((q) =>
    (!status || q.status === status) &&
    (!q0 || `${q.number} ${q.customer_name}`.toLowerCase().includes(q0)));

  const body = $("#quotes-body");
  body.innerHTML = "";
  $("#quotes-empty").hidden = quotesCache.length > 0;
  $("#quotes-nomatch").hidden = quotesCache.length === 0 || rows.length > 0;
  for (const q of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(q.number)}${presenceMarker("quote", q.id)}</td>
      <td>${esc(q.customer_name)}</td>
      <td>${fmtDate(q.issue_date)}</td>
      <td class="col-num">${euro(q.total)}</td>
      <td><span class="badge ${QUOTE_BADGE_CLASS[q.status] || "offen"}">${esc(q.status)}</span></td>
      <td class="actions">
        <a class="link" href="/api/quotes/${q.id}/pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${q.status === "offen" ? `<button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${q.status === "offen" ? `<button class="link" data-act="accept" title="Als angenommen markieren">✅ <span>annehmen</span></button>` : ""}
        ${q.status === "offen" ? `<button class="warn" data-act="decline" title="Als abgelehnt markieren">🚫 <span>ablehnen</span></button>` : ""}
        ${q.status !== "umgewandelt" ? `<button class="link" data-act="convert" title="In Rechnung umwandeln">🧾 <span>zu Rechnung</span></button>` : ""}
        <button class="danger" data-act="delete" title="Angebot löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => quoteAction(btn.dataset.act, q);
    });
    body.appendChild(tr);
  }
}

async function quoteAction(act, q) {
  if (act === "edit") { startQuoteEdit(q); return; }
  if (act === "email") {
    const to = prompt("Angebot per E-Mail senden an:", emailForCustomer(q.customer_name));
    if (!to) return;
    const res = await fetch(`/api/quotes/${q.id}/email`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to.trim() }),
    });
    if (res.ok) alert(`✓ ${q.number} gesendet.`);
    else { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    return;
  }
  if (act === "delete") {
    if (!confirm(`Angebot ${q.number} wirklich löschen?`)) return;
    await fetch(`/api/quotes/${q.id}`, { method: "DELETE" });
    loadQuotes();
    return;
  }
  if (act === "convert") {
    if (!confirm(`Angebot ${q.number} jetzt in eine Rechnung umwandeln?`)) return;
    const res = await fetch(`/api/quotes/${q.id}/convert`, { method: "POST" });
    if (res.ok) { alert("✓ In Rechnung umgewandelt."); loadQuotes(); }
    else { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    return;
  }
  const map = { accept: "angenommen", decline: "abgelehnt" };
  if (map[act]) {
    await fetch(`/api/quotes/${q.id}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: map[act] }),
    });
    loadQuotes();
  }
}

// ---------------------- Lieferscheine (Delivery Notes) ----------------------
const deliveryItemsBody = $("#delivery-items-body");

function addDeliveryItemRow(desc = "", qty = 1) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="di-desc" type="text" list="delivery-product-list" placeholder="Leistung / Artikel"></td>
    <td><input class="di-qty col-num" type="number" min="0" step="0.01"></td>
    <td><button type="button" class="remove-item" title="Entfernen">✕</button></td>`;
  tr.querySelector(".di-desc").value = desc;
  tr.querySelector(".di-qty").value = qty;
  tr.querySelector(".remove-item").onclick = () => {
    tr.remove(); saveDraftSoon("delivery");
  };
  deliveryItemsBody.appendChild(tr);
  saveDraftSoon("delivery");
}
$("#delivery-add-item").onclick = () => addDeliveryItemRow();

function startDeliveryEdit(d) {
  const f = $("#delivery-form");
  clearDraft("delivery");
  startPresence("delivery_note", d.id);
  f.delivery_id.value = d.id;
  f.customer_name.value = d.customer_name;
  f.customer_address.value = d.customer_address || "";
  f.customer_contact_person.value = d.customer_contact_person || "";
  f.notes.value = d.notes || "";
  deliveryItemsBody.innerHTML = "";
  for (const it of d.items) addDeliveryItemRow(it.description, it.quantity);
  $("#delivery-form-title").textContent = `Lieferschein ${d.number} bearbeiten`;
  $("#delivery-submit").textContent = "Änderungen speichern";
  $("#delivery-cancel-edit").hidden = false;
  $("#delivery-msg").textContent = "";
}

function resetDeliveryForm() {
  const f = $("#delivery-form");
  f.reset();
  f.delivery_id.value = "";
  deliveryItemsBody.innerHTML = "";
  addDeliveryItemRow();
  stopPresence();
  $("#delivery-form-title").textContent = "Neuen Lieferschein erstellen";
  $("#delivery-submit").textContent = "Lieferschein speichern";
  $("#delivery-cancel-edit").hidden = true;
  $("#delivery-msg").textContent = "";
  clearDraft("delivery");
  setPendingCustomer("#delivery-customer-select", "");
}
$("#delivery-cancel-edit").addEventListener("click", resetDeliveryForm);

$("#delivery-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#delivery-msg");
  const items = [...deliveryItemsBody.querySelectorAll("tr")].map((tr) => ({
    description: tr.querySelector(".di-desc").value.trim(),
    quantity: parseFloat(tr.querySelector(".di-qty").value) || 0,
  })).filter((it) => it.description);

  if (!items.length) {
    msg.textContent = "Mindestens eine Position mit Beschreibung nötig.";
    msg.className = "err";
    return;
  }

  const f = e.target;
  const editId = f.delivery_id.value;
  const payload = {
    customer_name: f.customer_name.value.trim(),
    customer_address: f.customer_address.value,
    customer_contact_person: f.customer_contact_person.value.trim(),
    notes: f.notes.value,
    items,
  };

  const res = await fetch(editId ? `/api/delivery-notes/${editId}` : "/api/delivery-notes", {
    method: editId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.ok) {
    const d = await res.json();
    msg.textContent = `✓ Gespeichert als ${d.number}`;
    msg.className = "ok";
    resetDeliveryForm();
    loadDeliveryNotes();
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

async function loadDeliveryNotes() {
  const [rows] = await Promise.all([
    fetch("/api/delivery-notes").then((r) => r.json()),
    refreshPresenceMap(),
  ]);
  deliveryCache = rows;
  renderDeliveryNotes();
}

function renderDeliveryNotes() {
  const q0 = $("#delivery-search").value.trim().toLowerCase();
  const status = $("#delivery-filter-status").value;
  const rows = deliveryCache.filter((d) =>
    (!status || d.status === status) &&
    (!q0 || `${d.number} ${d.customer_name}`.toLowerCase().includes(q0)));

  const body = $("#delivery-body");
  body.innerHTML = "";
  $("#delivery-empty").hidden = deliveryCache.length > 0;
  $("#delivery-nomatch").hidden = deliveryCache.length === 0 || rows.length > 0;
  for (const d of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(d.number)}${presenceMarker("delivery_note", d.id)}</td>
      <td>${esc(d.customer_name)}</td>
      <td>${fmtDate(d.issue_date)}</td>
      <td><span class="badge ${d.status === "storniert" ? "storniert" : "offen"}">${esc(d.status)}</span></td>
      <td class="actions">
        <a class="link" href="/api/delivery-notes/${d.id}/pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${d.status !== "storniert" ? `<button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${d.status !== "storniert"
          ? `<button class="warn" data-act="cancel" title="Stornieren">🚫 <span>stornieren</span></button>`
          : `<button class="link" data-act="reopen" title="Storno rückgängig">↩️ <span>zurück</span></button>`}
        <button class="danger" data-act="delete" title="Lieferschein löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => deliveryAction(btn.dataset.act, d);
    });
    body.appendChild(tr);
  }
}

async function deliveryAction(act, d) {
  if (act === "edit") { startDeliveryEdit(d); return; }
  if (act === "email") {
    const to = prompt("Lieferschein per E-Mail senden an:", emailForCustomer(d.customer_name));
    if (!to) return;
    const res = await fetch(`/api/delivery-notes/${d.id}/email`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to.trim() }),
    });
    if (res.ok) alert(`✓ ${d.number} gesendet.`);
    else { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    return;
  }
  if (act === "delete") {
    if (!confirm(`Lieferschein ${d.number} wirklich löschen?`)) return;
    await fetch(`/api/delivery-notes/${d.id}`, { method: "DELETE" });
    loadDeliveryNotes();
    return;
  }
  const map = { cancel: "storniert", reopen: "offen" };
  if (map[act]) {
    await fetch(`/api/delivery-notes/${d.id}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: map[act] }),
    });
    loadDeliveryNotes();
  }
}

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
  usersCache = await (await fetch("/api/users")).json();
  renderUsers();
}

function renderUsers() {
  const q0 = $("#user-search").value.trim().toLowerCase();
  const users = usersCache.filter((u) => !q0 || u.username.toLowerCase().includes(q0));
  const body = $("#users-body");
  body.innerHTML = "";
  for (const u of users) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(u.username)}</td>
      <td>${u.is_admin ? "👑 Admin" : "Benutzer"}</td>
      <td class="actions">
        <button class="link" data-act="pw" data-id="${u.id}" title="Passwort ändern">🔑 <span>Passwort</span></button>
        <button class="danger" data-act="del" data-id="${u.id}" data-name="${esc(u.username)}" title="Benutzer löschen">🗑️ <span>löschen</span></button>
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

// ---------------------- Audit-Log (Admin, DSGVO) ----------------------
async function loadAuditLog() {
  auditCache = await (await fetch("/api/audit-log")).json();
  renderAuditLog();
}

function renderAuditLog() {
  const q0 = $("#audit-search").value.trim().toLowerCase();
  const rows = auditCache.filter((r) => !q0 ||
    `${r.username} ${r.action} ${r.target_type} ${r.target_id || ""} ${r.detail || ""}`
      .toLowerCase().includes(q0));
  const body = $("#audit-body");
  body.innerHTML = rows.map((r) => `
    <tr>
      <td>${new Date(r.timestamp).toLocaleString("de-DE")}</td>
      <td>${esc(r.username)}</td>
      <td>${esc(r.action)}</td>
      <td>${esc(r.target_type)}${r.target_id ? " #" + r.target_id : ""}</td>
      <td>${esc(r.detail) || "–"}</td>
    </tr>`).join("");
}

// ---------------------- Such-/Filterfelder der Listen --------------------
// Alle Listen filtern rein clientseitig über den bereits geladenen Cache –
// kein zusätzlicher Request je Tastendruck. Ausnahme ist die History, die
// serverseitig sucht (dort können es viele Rechnungen werden).
[
  ["#quote-search", "input", renderQuotes],
  ["#quote-filter-status", "change", renderQuotes],
  ["#delivery-search", "input", renderDeliveryNotes],
  ["#delivery-filter-status", "change", renderDeliveryNotes],
  ["#customer-list-search", "input", renderCustomers],
  ["#customer-filter-active", "change", renderCustomers],
  ["#product-list-search", "input", renderProducts],
  ["#product-filter-active", "change", renderProducts],
  ["#user-search", "input", renderUsers],
  ["#audit-search", "input", renderAuditLog],
].forEach(([sel, event, handler]) => {
  const el = $(sel);
  if (el) el.addEventListener(event, handler);
});

// ---------------------- Backup & Wiederherstellung (Admin) ----------------------
async function loadBackups() {
  const files = await (await fetch("/api/admin/backups")).json();
  const body = $("#backup-body");
  body.innerHTML = "";
  $("#backup-empty").hidden = files.length > 0;
  for (const b of files) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(b.name)}</td>
      <td>${new Date(b.modified).toLocaleString("de-DE")}</td>
      <td class="col-num">${(b.size / 1024).toFixed(0)} KB</td>
      <td class="actions">
        <a class="link" href="/api/admin/backups/${encodeURIComponent(b.name)}/download" title="Herunterladen">⬇️ <span>Download</span></a>
        <button class="danger" title="Datenbank mit diesem Backup überschreiben">♻️ <span>wiederherstellen</span></button>
      </td>`;
    tr.querySelector("button").onclick = () => restoreBackup(b.name);
    body.appendChild(tr);
  }
}

async function restoreBackup(name) {
  if (!confirm(`ACHTUNG: Der aktuelle Datenbankstand wird komplett durch das Backup\n"${name}"\nersetzt. Alle Änderungen seit diesem Backup gehen unwiderruflich verloren.\n\nFortfahren?`)) return;
  if (!confirm(`Letzte Bestätigung: "${name}" jetzt wirklich wiederherstellen?`)) return;
  const res = await fetch(`/api/admin/backups/${encodeURIComponent(name)}/restore`, { method: "POST" });
  if (res.ok) {
    alert("✓ Wiederhergestellt. Die Seite wird jetzt neu geladen.");
  } else {
    const e = await res.json().catch(() => ({}));
    alert("Fehler bei der Wiederherstellung: " + (e.detail || res.status));
  }
  location.reload();
}

// ---------------------- Zwischenspeicher für Eingaben --------------------
// Nicht abgeschickte Formulareingaben überstehen ein Neuladen der Seite
// (F5, versehentlich geschlossener Tab, abgelaufene Sitzung) und werden beim
// nächsten Öffnen automatisch wiederhergestellt. Die Entwürfe liegen nur im
// Browser (localStorage), es geht nichts davon an den Server.
// Beim Bearbeiten eines bestehenden Belegs wird bewusst KEIN Entwurf
// angelegt: dort ist der Serverstand maßgeblich und es hängt eine
// Bearbeitungssperre daran.
const DRAFT_KEY = "rechnung.drafts.v1";
const DRAFT_MAX_AGE_MS = 7 * 24 * 3600 * 1000;

function readDrafts() {
  try { return JSON.parse(localStorage.getItem(DRAFT_KEY)) || {}; }
  catch (_) { return {}; }
}
function writeDrafts(all) {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify(all)); } catch (_) { /* Speicher voll/blockiert */ }
}

function draftHasContent(d) {
  if (!d) return false;
  const f = d.fields || {};
  const texts = [f.customer_name, f.customer_address, f.customer_contact_person, f.notes];
  return texts.some((v) => String(v || "").trim() !== "") ||
    (d.items || []).some((it) => String(it.description || "").trim() !== "");
}

function storeDraft(name, data) {
  const all = readDrafts();
  if (draftHasContent(data)) all[name] = { saved_at: Date.now(), data };
  else delete all[name];
  writeDrafts(all);
  updateDraftHints();
}

function clearDraft(name) {
  const all = readDrafts();
  delete all[name];
  writeDrafts(all);
  const banner = $(`#${name}-draft-banner`);
  if (banner) banner.hidden = true;
  updateDraftHints();
}

// Kleiner Punkt am Reiter, solange dort ein ungespeicherter Entwurf liegt –
// sonst fiele er nach einem Neuladen (Startseite ist das Dashboard) niemandem auf.
const DRAFT_NAV = { invoice: "#nav-new", quote: "#nav-quotes", delivery: "#nav-delivery" };
function updateDraftHints() {
  const all = readDrafts();
  for (const [name, sel] of Object.entries(DRAFT_NAV)) {
    const btn = $(sel);
    if (btn) btn.classList.toggle("has-draft", Boolean(all[name]));
  }
}

const draftTimers = {};
function saveDraftSoon(name) {
  clearTimeout(draftTimers[name]);
  draftTimers[name] = setTimeout(() => saveDraftNow(name), 400);
}
function saveDraftNow(name) {
  clearTimeout(draftTimers[name]);
  storeDraft(name, DRAFTS[name].collect());
}

function showDraftBanner(name, savedAt) {
  const banner = $(`#${name}-draft-banner`);
  if (!banner) return;
  banner.querySelector(".draft-text").textContent =
    `🗂️ Wiederhergestellt: nicht gespeicherte Eingaben vom ${new Date(savedAt).toLocaleString("de-DE")}.`;
  banner.hidden = false;
}

// --- Rechnung ---
function collectInvoiceDraft() {
  const f = $("#invoice-form");
  if (f.invoice_id.value) return null;   // Bearbeitungsmodus: kein Entwurf
  return {
    fields: {
      customer_id: $("#customer-select").value,
      customer_name: f.customer_name.value,
      customer_address: f.customer_address.value,
      customer_contact_person: f.customer_contact_person.value,
      due_date: f.due_date.value,
      tax_rate: f.tax_rate.value,
      notes: f.notes.value,
      skonto_percent: f.skonto_percent.value,
      skonto_days: f.skonto_days.value,
      discount_percent: f.discount_percent.value,
      small_business: f.small_business.checked,
      save_customer: $("#save-customer").checked,
      auto_email: $("#auto-email").checked,
    },
    items: [...itemsBody.querySelectorAll("tr")].map((tr) => ({
      description: tr.querySelector(".i-desc").value,
      quantity: tr.querySelector(".i-qty").value,
      unit_price: tr.querySelector(".i-price").value,
    })),
  };
}

function applyInvoiceDraft(d) {
  const f = $("#invoice-form");
  const x = d.fields || {};
  f.customer_name.value = x.customer_name || "";
  f.customer_address.value = x.customer_address || "";
  f.customer_contact_person.value = x.customer_contact_person || "";
  f.due_date.value = x.due_date || "";
  f.tax_rate.value = x.tax_rate ?? 20;
  f.notes.value = x.notes || "";
  f.skonto_percent.value = x.skonto_percent ?? 0;
  f.skonto_days.value = x.skonto_days ?? 0;
  f.discount_percent.value = x.discount_percent ?? 0;
  f.small_business.checked = !!x.small_business;
  $("#save-customer").checked = !!x.save_customer;
  $("#auto-email").checked = !!x.auto_email;
  itemsBody.innerHTML = "";
  for (const it of (d.items || [])) addItemRow(it.description, it.quantity, it.unit_price);
  if (!itemsBody.children.length) addItemRow();
  recalc();
  setPendingCustomer("#customer-select", x.customer_id);
}

// --- Angebot ---
function collectQuoteDraft() {
  const f = $("#quote-form");
  if (f.quote_id.value) return null;
  return {
    fields: {
      customer_id: $("#quote-customer-select").value,
      customer_name: f.customer_name.value,
      customer_address: f.customer_address.value,
      customer_contact_person: f.customer_contact_person.value,
      valid_until: f.valid_until.value,
      tax_rate: f.tax_rate.value,
      notes: f.notes.value,
      discount_percent: f.discount_percent.value,
      small_business: f.small_business.checked,
    },
    items: [...quoteItemsBody.querySelectorAll("tr")].map((tr) => ({
      description: tr.querySelector(".qi-desc").value,
      quantity: tr.querySelector(".qi-qty").value,
      unit_price: tr.querySelector(".qi-price").value,
    })),
  };
}

function applyQuoteDraft(d) {
  const f = $("#quote-form");
  const x = d.fields || {};
  f.customer_name.value = x.customer_name || "";
  f.customer_address.value = x.customer_address || "";
  f.customer_contact_person.value = x.customer_contact_person || "";
  f.valid_until.value = x.valid_until || "";
  f.tax_rate.value = x.tax_rate ?? 20;
  f.notes.value = x.notes || "";
  f.discount_percent.value = x.discount_percent ?? 0;
  f.small_business.checked = !!x.small_business;
  quoteItemsBody.innerHTML = "";
  for (const it of (d.items || [])) addQuoteItemRow(it.description, it.quantity, it.unit_price);
  if (!quoteItemsBody.children.length) addQuoteItemRow();
  recalcQuote();
  setPendingCustomer("#quote-customer-select", x.customer_id);
}

// --- Lieferschein ---
function collectDeliveryDraft() {
  const f = $("#delivery-form");
  if (f.delivery_id.value) return null;
  return {
    fields: {
      customer_id: $("#delivery-customer-select").value,
      customer_name: f.customer_name.value,
      customer_address: f.customer_address.value,
      customer_contact_person: f.customer_contact_person.value,
      notes: f.notes.value,
    },
    items: [...deliveryItemsBody.querySelectorAll("tr")].map((tr) => ({
      description: tr.querySelector(".di-desc").value,
      quantity: tr.querySelector(".di-qty").value,
    })),
  };
}

function applyDeliveryDraft(d) {
  const f = $("#delivery-form");
  const x = d.fields || {};
  f.customer_name.value = x.customer_name || "";
  f.customer_address.value = x.customer_address || "";
  f.customer_contact_person.value = x.customer_contact_person || "";
  f.notes.value = x.notes || "";
  deliveryItemsBody.innerHTML = "";
  for (const it of (d.items || [])) addDeliveryItemRow(it.description, it.quantity);
  if (!deliveryItemsBody.children.length) addDeliveryItemRow();
  setPendingCustomer("#delivery-customer-select", x.customer_id);
}

const DRAFTS = {
  invoice: { collect: collectInvoiceDraft, apply: applyInvoiceDraft, reset: () => resetInvoiceForm() },
  quote: { collect: collectQuoteDraft, apply: applyQuoteDraft, reset: () => resetQuoteForm() },
  delivery: { collect: collectDeliveryDraft, apply: applyDeliveryDraft, reset: () => resetDeliveryForm() },
};

function restoreDrafts() {
  const all = readDrafts();
  let pruned = false;
  for (const [name, entry] of Object.entries(all)) {
    const tooOld = !entry || !entry.saved_at ||
      (Date.now() - entry.saved_at) > DRAFT_MAX_AGE_MS;
    if (!DRAFTS[name] || tooOld) { delete all[name]; pruned = true; continue; }
    try {
      DRAFTS[name].apply(entry.data || {});
      showDraftBanner(name, entry.saved_at);
    } catch (err) {
      console.warn("Entwurf konnte nicht wiederhergestellt werden:", name, err);
      delete all[name];
      pruned = true;
    }
  }
  if (pruned) writeDrafts(all);
  updateDraftHints();
}

for (const [name, sel] of [["invoice", "#invoice-form"], ["quote", "#quote-form"],
                           ["delivery", "#delivery-form"]]) {
  const form = $(sel);
  form.addEventListener("input", () => saveDraftSoon(name));
  form.addEventListener("change", () => saveDraftSoon(name));
}

// Beim Verlassen der Seite noch offene Änderungen sofort sichern.
window.addEventListener("beforeunload", () => {
  for (const name of Object.keys(DRAFTS)) saveDraftNow(name);
  stopPresence();
});

document.querySelectorAll("[data-discard-draft]").forEach((btn) => {
  btn.onclick = () => DRAFTS[btn.dataset.discardDraft].reset();
});

// ---------------------- Init ----------------------
async function loadCurrentUser() {
  try {
    const me = await (await fetch("/api/me")).json();
    if (me.user) $("#current-user").textContent = me.user;
    currentIsAdmin = !!me.is_admin;
    $("#nav-users").hidden = !currentIsAdmin;
    $("#nav-settings").hidden = !currentIsAdmin;
    $("#nav-audit").hidden = !currentIsAdmin;
    $("#nav-backup").hidden = !currentIsAdmin;
  } catch (_) { /* fetch leitet bei 401 selbst um */ }
}

$("#invoice-form [name=issue_date]").value = todayISO();
$("#export-month").value = todayISO().slice(0, 7);
addItemRow();
addQuoteItemRow();
addDeliveryItemRow();
restoreDrafts();
loadCurrentUser();
refreshCustomers();
refreshProducts();
loadDashboard();
