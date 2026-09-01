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
  credit: $("#view-credit"),
  reports: $("#view-reports"),
  monitoring: $("#view-monitoring"),
  customers: $("#view-customers"),
  products: $("#view-products"),
  settings: $("#view-settings"),
  users: $("#view-users"),
  audit: $("#view-audit"),
  backup: $("#view-backup"),
};
function show(view) {
  if (view !== "new" && currentEditInvoiceId !== null) releaseInvoiceLock();
  if (view !== "quotes" && currentEditQuoteId !== null) releaseQuoteLock();
  if (view !== "delivery" && currentEditDeliveryId !== null) releaseDeliveryLock();
  // Anwesenheit gilt immer nur für die Ansicht, in der der Beleg offen ist.
  if (currentPresence && PRESENCE_VIEWS[currentPresence.type] !== view) stopPresence();
  // Das Monitoring aktualisiert sich nur, solange es auch zu sehen ist.
  if (view !== "monitoring") stopMonitoringAuto();
  for (const [name, el] of Object.entries(views)) el.hidden = name !== view;
  for (const n of Object.keys(views)) {
    const btn = $(`#nav-${n}`);
    if (btn) btn.classList.toggle("active", n === view);
  }
  if (view === "dashboard") loadDashboard();
  if (view === "quotes") loadQuotes();
  if (view === "delivery") loadDeliveryNotes();
  if (view === "history") { historyNotice(""); loadHistory(); }
  if (view === "credit") loadCreditNotes();
  if (view === "reports") loadReports();
  if (view === "monitoring") { loadMonitoring(); startMonitoringAuto(); }
  if (view === "customers") loadCustomers();
  if (view === "products") loadProducts();
  if (view === "settings") loadSettings();
  if (view === "users") loadUsers();
  if (view === "audit") loadAuditLog();
  if (view === "backup") loadBackups();
  syncMobileNav();
}
for (const n of Object.keys(views)) {
  const btn = $(`#nav-${n}`);
  if (btn) btn.onclick = () => show(n);
}

// ---------------------- Burger-Menü (Verwaltungspunkte + Handy) -----------
// Firma/Benutzer/Audit-Log/Backup/Monitoring stehen nicht fest in der
// Leiste, sondern hinter dem Burger-Knopf – sonst bricht die Menüzeile bei
// jedem Admin-Login um. Derselbe Auf/Zu-Mechanismus wie beim
// Beispieldatei-Auswahlfenster (siehe unten, setupExampleMenu).
const navMoreToggle = $("#nav-more-toggle");
const navMoreMenu = $("#nav-more-menu");

function toggleNavMoreMenu(open) {
  navMoreMenu.hidden = !open;
  navMoreToggle.setAttribute("aria-expanded", open ? "true" : "false");
}
navMoreToggle.onclick = (e) => {
  e.stopPropagation();
  toggleNavMoreMenu(navMoreMenu.hidden);
};
// Ein Klick auf einen der Menüpunkte schließt das Fenster gleich mit –
// delegiert auf den Container, gilt also auch für Knöpfe, die erst auf dem
// Handy dorthin wandern (siehe syncMobileNav()).
navMoreMenu.addEventListener("click", (e) => {
  if (e.target.closest("button")) toggleNavMoreMenu(false);
});
document.addEventListener("click", (e) => {
  if (!navMoreMenu.hidden && !navMoreMenu.contains(e.target) && e.target !== navMoreToggle) {
    toggleNavMoreMenu(false);
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !navMoreMenu.hidden) toggleNavMoreMenu(false);
});

// Auf dem Handy bleibt nur der aktive Menüpunkt in der Leiste, alle anderen
// Kernpunkte wandern zusätzlich zu den Verwaltungspunkten ins Burger-Menü
// (TODO.md, Abschnitt "OBERFLÄCHE / RESPONSIVE DESIGN").
// Auf einem breiten Bildschirm bleiben sie wie gehabt in der Leiste.
const MOBILE_NAV_BREAKPOINT = 600;
const navPrimary = $("#nav-primary");
const PRIMARY_NAV_IDS = [...navPrimary.querySelectorAll("button")].map((b) => b.id);
const navMoreAnchor = $("#nav-settings"); // erster Verwaltungspunkt: Einfügepunkt davor

/** Die ausgelagerten Kernpunkte im Burger-Menü in ihre gewohnte Reihenfolge
 *  bringen – nicht in die, in der sie zufällig ausgelagert wurden. Die
 *  Verwaltungspunkte ab #nav-settings bleiben dabei unberührt. */
function sortMoreMenu() {
  for (const id of PRIMARY_NAV_IDS) {
    const btn = $(`#${id}`);
    if (btn && btn.parentElement === navMoreMenu) navMoreMenu.insertBefore(btn, navMoreAnchor);
  }
}

/** Reicht die Breite nicht für alle Punkte, wandern die hinteren einzeln ins
 *  Burger-Menü, bis die Leiste wieder passt. Der aktive Punkt bleibt immer
 *  stehen – man soll sehen, wo man ist, ohne die Leiste seitwärts zu
 *  schieben. Rückgabe: ob etwas ausgelagert wurde, die Leiste also eng ist.
 *
 *  jsdom rechnet kein Layout, dort sind scrollWidth und clientWidth beide 0,
 *  die Schleife läuft also gar nicht erst an. Auf dem Handy steht ohnehin nur
 *  der aktive Punkt in der Leiste, dann gibt es nichts mehr auszulagern. */
function trimPrimaryNav(active) {
  let moved = false;
  for (let guard = PRIMARY_NAV_IDS.length; guard > 0; guard--) {
    if (navPrimary.scrollWidth <= navPrimary.clientWidth) break;
    const spare = [...navPrimary.children].reverse().find((btn) => btn !== active);
    if (!spare) break;
    navMoreMenu.insertBefore(spare, navMoreAnchor);
    // Ohne Burger-Knopf käme man an einen ausgelagerten Punkt nicht mehr heran.
    navMoreToggle.hidden = false;
    moved = true;
  }
  if (moved) sortMoreMenu();
  return moved;
}

function syncMobileNav() {
  const mobile = window.innerWidth <= MOBILE_NAV_BREAKPOINT;
  const active = document.querySelector("#nav-primary button.active, #nav-more-menu button.active");
  for (const id of PRIMARY_NAV_IDS) {
    const btn = $(`#${id}`);
    if (!btn) continue;
    if (!mobile || btn === active) {
      navPrimary.appendChild(btn);
    } else if (btn.parentElement !== navMoreMenu) {
      navMoreMenu.insertBefore(btn, navMoreAnchor);
    }
  }
  // Ohne Admin-Rechte und auf einem breiten Bildschirm gibt es nichts, was
  // das Burger-Menü zeigen könnte; auf dem Handy braucht es jeder, um an die
  // ausgelagerten Kernpunkte zu kommen.
  navMoreToggle.hidden = !(currentIsAdmin || mobile);

  // Bleibt die Leiste zu breit, wird ausgelagert – und der Punkt, in dem man
  // gerade steht, rückt dabei nach vorne. Auf einem Bildschirm, auf dem alles
  // hinpasst, bleibt die gewohnte Reihenfolge dagegen unangetastet.
  if (trimPrimaryNav(active) && active && active.parentElement === navPrimary) {
    navPrimary.insertBefore(active, navPrimary.firstChild);
  }
}
syncMobileNav();
// Debounced: ein Resize feuert viele Events, ein Layout-Umbau reicht einmal.
let navResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(navResizeTimer);
  navResizeTimer = setTimeout(syncMobileNav, 150);
});

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

/** Wie gut passt ein Kunde zur Suche? Größer ist besser. */
function matchScore(c, q) {
  const name = String(c.name || "").toLowerCase();
  const rest = [c.contact_person, c.email, c.address].map((v) => String(v || "").toLowerCase());
  if (name === q) return 100;
  if (name.startsWith(q)) return 80;
  if (rest.some((v) => v.startsWith(q))) return 60;
  if (name.includes(q)) return 40;
  return 20;
}

/** Bester Treffer einer Suche – bei Gleichstand gewinnt der kürzere Name. */
function bestMatch(list, q) {
  let best = null, bestScore = -1;
  for (const c of list) {
    const score = matchScore(c, q);
    const shorter = best && String(c.name || "").length < String(best.name || "").length;
    if (score > bestScore || (score === bestScore && shorter)) { best = c; bestScore = score; }
  }
  return best;
}

function fillPicker(p) {
  const q = p.search.value.trim().toLowerCase();
  const previous = p.select.value || p.pending || "";
  p.select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  p.select.appendChild(placeholder);

  const matches = [];
  for (const c of customersCache) {
    if (!c.active || !customerMatches(c, q)) continue;
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.contact_person ? `${c.name} – ${c.contact_person}` : c.name;
    p.select.appendChild(opt);
    matches.push(c);
  }
  placeholder.textContent = q && !matches.length
    ? "– kein Kunde passt zur Suche –"
    : "– neuen Kunden eingeben –";

  // Eine bereits getroffene Auswahl bleibt stehen, solange sie zur Suche passt.
  if (previous && matches.some((c) => String(c.id) === previous)) {
    p.select.value = previous;
    p.pending = "";
    p.applied = previous;
    return;
  }
  // Wer sucht, meint einen bestimmten Kunden: den besten Treffer gleich
  // auswählen und übernehmen, statt auf "neuen Kunden eingeben" stehen zu
  // bleiben. Übernommen wird nur bei einem Wechsel – sonst würde jeder weitere
  // Tastendruck von Hand geänderte Felder wieder überschreiben.
  const best = q ? bestMatch(matches, q) : null;
  p.select.value = best ? String(best.id) : "";
  if (!best) { p.applied = ""; return; }
  if (String(best.id) !== p.applied) {
    p.applied = String(best.id);
    p.apply(best);
  }
}

/** Auswahl vormerken, solange die Kundenliste noch nicht geladen ist. */
function setPendingCustomer(selectSel, id) {
  const picker = customerPickers.find((p) => p.select === $(selectSel));
  if (!picker) return;
  picker.pending = id ? String(id) : "";
  // Vorgemerkt heißt: die Stammdaten stehen schon im Formular (Entwurf) bzw.
  // sollen bewusst leer bleiben – nicht noch einmal übernehmen.
  picker.applied = picker.pending;
  fillPicker(picker);
}

function registerCustomerPicker(selectSel, searchSel, apply) {
  const select = $(selectSel), search = $(searchSel);
  if (!select || !search) return;
  const picker = { select, search, apply, pending: "", applied: "" };
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

registerCustomerPicker("#credit-customer-select", "#credit-customer-search", (c) => {
  const f = $("#credit-form");
  f.customer_name.value = c.name;
  f.customer_address.value = c.address || "";
  f.customer_contact_person.value = c.contact_person || "";
});

function fillProductDatalist() {
  const active = productsCache.filter((x) => x.active);
  for (const sel of ["#product-list", "#quote-product-list", "#delivery-product-list",
                     "#credit-product-list"]) {
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

// ---------------------- Hinweisbanner über den Formularen -----------------
// Sperre, Anwesenheit und wiederhergestellter Entwurf melden sich über dem
// Formular von Rechnung, Angebot und Lieferschein. Wer den Hinweis gelesen
// hat, blendet ihn mit "✕" aus; er kommt erst wieder, wenn er etwas Neues zu
// sagen hat (anderer Text) oder der Beleg neu geöffnet wird.
const dismissedBanners = {};

function bannerSlot(banner) {
  return banner.querySelector(".banner-text, .draft-text") || banner;
}

function showBanner(sel, text) {
  const banner = $(sel);
  if (!banner) return;
  bannerSlot(banner).textContent = text;
  banner.hidden = dismissedBanners[sel] === text;
}

function hideBanner(sel) {
  const banner = $(sel);
  if (!banner) return;
  banner.hidden = true;
  bannerSlot(banner).textContent = "";
  delete dismissedBanners[sel];
}

document.querySelectorAll("[data-hide-banner]").forEach((btn) => {
  btn.onclick = () => {
    const sel = btn.dataset.hideBanner;
    const banner = $(sel);
    if (!banner) return;
    dismissedBanners[sel] = bannerSlot(banner).textContent;
    banner.hidden = true;
  };
});

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

function renderPresenceBanner(type, others) {
  const sel = PRESENCE_BANNERS[type];
  if (!sel || !$(sel)) return;
  if (!others || !others.length) {
    hideBanner(sel);
    return;
  }
  const names = others.map((o) => o.username).join(", ");
  showBanner(sel, others.length === 1
    ? `👀 ${names} hat ${DOC_LABEL[type]} gerade ebenfalls geöffnet.`
    : `👀 ${names} haben ${DOC_LABEL[type]} gerade ebenfalls geöffnet.`);
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

// ---------------------- Angebot / Lieferschein: Bearbeitungssperre --------
// Dasselbe Prinzip wie bei der Rechnung oben, nur für die beiden Belegarten,
// die bis vor Kurzem gar keine Sperre hatten (nur die Anwesenheitsanzeige).
let currentEditQuoteId = null;
let quoteLockHeartbeatTimer = null;
let currentEditDeliveryId = null;
let deliveryLockHeartbeatTimer = null;

function startQuoteLockHeartbeat(id) {
  stopQuoteLockHeartbeat();
  quoteLockHeartbeatTimer = setInterval(() => {
    fetch(`/api/quotes/${id}/lock`, { method: "POST" }).catch(() => {});
  }, 90000);
}
function stopQuoteLockHeartbeat() {
  if (quoteLockHeartbeatTimer) { clearInterval(quoteLockHeartbeatTimer); quoteLockHeartbeatTimer = null; }
}
function releaseQuoteLock() {
  stopQuoteLockHeartbeat();
  stopPresence();
  if (currentEditQuoteId !== null) {
    const id = currentEditQuoteId;
    currentEditQuoteId = null;
    fetch(`/api/quotes/${id}/lock`, { method: "DELETE" }).catch(() => {});
  }
}

function startDeliveryLockHeartbeat(id) {
  stopDeliveryLockHeartbeat();
  deliveryLockHeartbeatTimer = setInterval(() => {
    fetch(`/api/delivery-notes/${id}/lock`, { method: "POST" }).catch(() => {});
  }, 90000);
}
function stopDeliveryLockHeartbeat() {
  if (deliveryLockHeartbeatTimer) { clearInterval(deliveryLockHeartbeatTimer); deliveryLockHeartbeatTimer = null; }
}
function releaseDeliveryLock() {
  stopDeliveryLockHeartbeat();
  stopPresence();
  if (currentEditDeliveryId !== null) {
    const id = currentEditDeliveryId;
    currentEditDeliveryId = null;
    fetch(`/api/delivery-notes/${id}/lock`, { method: "DELETE" }).catch(() => {});
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
  hideBanner("#lock-banner");
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
  showBanner("#lock-banner",
    `🔒 Rechnung ${inv.number} ist für dich gesperrt, solange du sie bearbeitest.`);
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
    // Nach dem Speichern (neu wie bearbeitet) geht es in die
    // Rechnungsübersicht, in der die Rechnung schon steht.
    show("history");
    historyNotice(`✓ Rechnung ${inv.number} gespeichert.`);
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

// ---------------------- History ----------------------
/** Kurze Bestätigung über der Rechnungsübersicht (z. B. nach dem Speichern). */
function historyNotice(text) {
  const el = $("#history-msg");
  if (!el) return;
  el.textContent = text || "";
  el.hidden = !text;
}

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
        <a class="link" href="/api/invoices/${inv.id}/pdf" data-act="pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" data-id="${inv.id}" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${inv.status !== "storniert" ? `<button class="link" data-act="edit" data-id="${inv.id}" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${inv.status !== "storniert" && inv.remaining > 0 ? `<button class="link" data-act="pay" data-id="${inv.id}" title="Zahlung erfassen">💶 <span>Zahlung</span></button>` : ""}
        ${inv.delivery_note_number
          ? convertedMarker("📦", inv.delivery_note_number)
          : inv.status !== "storniert"
            ? `<button class="link" data-act="to-delivery" data-id="${inv.id}" title="In Lieferschein umwandeln">📦 <span>Lieferschein</span></button>`
            : ""}
        ${inv.status !== "storniert" && inv.remaining > 0
          ? `<button class="link" data-act="credit" data-id="${inv.id}" title="Gutschrift zu dieser Rechnung">↩️ <span>Gutschrift</span></button>`
          : ""}
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
  if (act === "credit") { startCreditNoteForInvoice(inv); return; }
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
  // Auf dem Handy sind sechs Balken zu eng nebeneinander – dort reichen die
  // letzten drei Monate (dieselbe Bildschirmbreite wie die übrigen
  // Handy-Anpassungen in styles.css, @media (max-width: 600px)).
  const isMobile = window.innerWidth <= 600;
  const months = isMobile ? s.months.slice(-3) : s.months;
  const max = Math.max(1, ...months.map((m) => m.revenue));
  const chart = $("#chart");
  chart.innerHTML = "";
  for (const m of months) {
    const col = document.createElement("div");
    col.className = "bar-col";
    col.title = euro(m.revenue);

    const bar = document.createElement("div");
    bar.className = "chart-bar";
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
      <td class="cell-wrap">${esc((c.address || "").replace(/\n/g, ", "))}</td>
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

// ---------------------- Kunden-Import (CSV) ------------------------------
// Ergänzung zur Eingabemaske: eine bestehende Kundenliste (Excel/CSV) lässt
// sich in einem Rutsch übernehmen. Ausgewertet wird die Datei serverseitig
// (POST /api/customers/import), hier hängt nur die Bedienung dran.
// Vorlagen gibt es in beiden Formaten, die der Import versteht – CSV und JSON
// (JSON schluckt auch den Kundenexport dieser App), und jeweils in zwei
// Größen: als Massenimport mit mehreren Datensätzen und als Einzelsatz. Alle
// vier entstehen aus denselben Beispieldaten, damit sie nicht auseinanderlaufen.
const EXAMPLE_CUSTOMERS = [
  { name: "Muster GmbH", email: "info@muster.example", contact_person: "Frau Muster",
    address: "Musterweg 1, 12345 Musterstadt",
    payment_term_days: 30, skonto_percent: 2, skonto_days: 7 },
  { name: "Beispiel AG", email: "kontakt@beispiel.example", contact_person: "Herr Beispiel",
    address: "Beispielstr. 2, 54321 Beispielstadt",
    payment_term_days: 14, skonto_percent: 0, skonto_days: 0 },
  { name: "Probe KG", email: "buero@probe.example", contact_person: "Frau Probe",
    address: "Probegasse 3, 67890 Probedorf",
    payment_term_days: 21, skonto_percent: 3, skonto_days: 10 },
];
const EXAMPLE_PRODUCTS = [
  { name: "Montagestunde", unit_price: 89.5 },
  { name: "Anfahrtspauschale", unit_price: 45 },
  { name: "Schaltschrank Grundausstattung", unit_price: 1250 },
];

// Eine Vorlagenbeschreibung je Import: Spaltenüberschriften, die passenden
// Feldnamen, die JSON-Schlüssel, die der Import versteht (Liste bzw.
// Einzelobjekt) und der Dateiname ohne Endung.
const CUSTOMER_EXAMPLE = {
  rows: EXAMPLE_CUSTOMERS,
  header: ["Name", "E-Mail", "Ansprechpartner", "Anschrift",
           "Zahlungsfrist", "Skonto", "Skonto_Tage"],
  fields: ["name", "email", "contact_person", "address",
           "payment_term_days", "skonto_percent", "skonto_days"],
  listKey: "customers", singleKey: "customer", basename: "kunden-vorlage",
};
const PRODUCT_EXAMPLE = {
  rows: EXAMPLE_PRODUCTS,
  header: ["Name", "Standardpreis"],
  fields: ["name", "unit_price"],
  listKey: "products", singleKey: "product", basename: "artikel-vorlage",
};

/** Beispieldatensätze als CSV – Semikolon, wie es Excel hierzulande erwartet. */
function exampleCsv(spec, rows) {
  return [
    spec.header.join(";"),
    ...rows.map((r) => spec.fields.map((f) => r[f]).join(";")),
  ].join("\r\n");
}

/** Dieselben Datensätze als JSON: mehrere unter dem Listenschlüssel, ein
 *  einzelner als Einzelobjekt – beides versteht der Import (main.py:
 *  _rows_from_json). */
function exampleJson(spec, rows, single) {
  return JSON.stringify(single ? { [spec.singleKey]: rows[0] }
                               : { [spec.listKey]: rows }, null, 2);
}

function downloadExample(spec, format, scope) {
  const single = scope === "single";
  const rows = single ? spec.rows.slice(0, 1) : spec.rows;
  const name = `${spec.basename}${single ? "-einzeln" : ""}.${format}`;
  if (format === "json") {
    downloadFile(name, exampleJson(spec, rows, single),
                 "application/json;charset=utf-8");
  } else {
    // BOM voran, sonst zeigt Excel die Umlaute falsch an.
    downloadFile(name, "\ufeff" + exampleCsv(spec, rows), "text/csv;charset=utf-8");
  }
}

function importMessage(text, cls) {
  const msg = $("#customer-import-msg");
  if (!msg) return;
  msg.textContent = text;
  msg.className = cls ? cls : "hint";
}

function downloadFile(name, content, type) {
  const blob = new Blob([content], { type });
  downloadUrl(URL.createObjectURL(blob), name);
}

/** Datei vom Server holen, ohne die Ansicht zu verlassen. */
function downloadUrl(url, name = "") {
  const a = document.createElement("a");
  a.href = url;
  if (name) a.download = name;
  a.click();
  if (url.startsWith("blob:")) URL.revokeObjectURL(url);
}

// Der Knopf lädt nicht sofort herunter, sondern fragt in einem kleinen Fenster
// nach Format und Umfang. Klick daneben oder Escape schließt das Fenster
// wieder. Denselben Aufbau nutzen Kunden- und Artikelimport.
function setupExampleMenu(buttonSelector, menuSelector, spec) {
  const menu = $(menuSelector);
  const button = $(buttonSelector);
  if (!menu || !button) return;

  const toggle = (open) => {
    menu.hidden = !open;
    button.setAttribute("aria-expanded", open ? "true" : "false");
  };

  button.onclick = (e) => {
    e.stopPropagation();
    toggle(menu.hidden);
  };

  menu.querySelectorAll("button[data-example]").forEach((btn) => {
    btn.onclick = () => {
      downloadExample(spec, btn.dataset.example, btn.dataset.scope);
      toggle(false);
    };
  });

  document.addEventListener("click", (e) => {
    if (!menu.hidden && !menu.contains(e.target)) toggle(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) toggle(false);
  });
}

setupExampleMenu("#customer-import-example", "#customer-import-example-menu",
                 CUSTOMER_EXAMPLE);

// Kein zweistufiges "Datei auswählen" + "Importieren": der Knopf öffnet
// direkt den Dateidialog, die Auswahl startet den Import.
$("#customer-import-btn").onclick = () => $("#customer-import-file").click();

$("#customer-import-file").addEventListener("change", async (e) => {
  const input = e.target;
  const file = input.files && input.files[0];
  if (!file) return;

  importMessage(`Import läuft … (${file.name})`);
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/customers/import", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    importMessage("Import fehlgeschlagen: " + (err.detail || res.status), "err");
    input.value = "";
    return;
  }
  const r = await res.json();
  const parts = [`✓ ${r.created} neu angelegt`, `${r.updated} aktualisiert`];
  if (r.skipped) parts.push(`${r.skipped} übersprungen`);
  importMessage(parts.join(", ") + (r.errors && r.errors.length ? ` – ${r.errors.join("; ")}` : ""),
                r.skipped ? "warn-text" : "ok");
  input.value = "";      // dieselbe Datei soll erneut auswählbar sein
  loadCustomers();
});

$("#customer-export-csv").onclick = () => downloadUrl("/api/customers/export.csv", "kunden-export.csv");

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

// ---------------------- Artikel-Import / -Export ----------------------
// Spiegelbild des Kundenimports (siehe oben): derselbe Ablauf, nur mit den
// zwei Feldern, die ein Artikel hat.
function productImportMessage(text, cls) {
  const msg = $("#product-import-msg");
  if (!msg) return;
  msg.textContent = text;
  msg.className = cls ? cls : "hint";
}

setupExampleMenu("#product-import-example", "#product-import-example-menu",
                 PRODUCT_EXAMPLE);

$("#product-import-btn").onclick = () => $("#product-import-file").click();

$("#product-import-file").addEventListener("change", async (e) => {
  const input = e.target;
  const file = input.files && input.files[0];
  if (!file) return;

  productImportMessage(`Import läuft … (${file.name})`);
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/products/import", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    productImportMessage("Import fehlgeschlagen: " + (err.detail || res.status), "err");
    input.value = "";
    return;
  }
  const r = await res.json();
  const parts = [`✓ ${r.created} neu angelegt`, `${r.updated} aktualisiert`];
  if (r.skipped) parts.push(`${r.skipped} übersprungen`);
  productImportMessage(parts.join(", ") + (r.errors && r.errors.length ? ` – ${r.errors.join("; ")}` : ""),
                       r.skipped ? "warn-text" : "ok");
  input.value = "";
  loadProducts();
});

$("#product-export-csv").onclick = () => downloadUrl("/api/products/export.csv", "artikel-export.csv");

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

async function startQuoteEdit(q) {
  if (currentEditQuoteId !== null && currentEditQuoteId !== q.id) releaseQuoteLock();
  const lock = await (await fetch(`/api/quotes/${q.id}/lock`, { method: "POST" })).json();
  if (!lock.editable) {
    alert(`Dieses Angebot wird gerade von "${lock.locked_by}" bearbeitet. Bitte später erneut versuchen.`);
    return;
  }
  currentEditQuoteId = q.id;

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
  showBanner("#quote-lock-banner",
    `🔒 Angebot ${q.number} ist für dich gesperrt, solange du es bearbeitest.`);
  $("#quote-msg").textContent = "";
  startQuoteLockHeartbeat(q.id);
}

function resetQuoteForm() {
  releaseQuoteLock();
  const f = $("#quote-form");
  f.reset();
  f.quote_id.value = "";
  quoteItemsBody.innerHTML = "";
  addQuoteItemRow();
  recalcQuote();
  $("#quote-form-title").textContent = "Neues Angebot erstellen";
  $("#quote-submit").textContent = "Angebot speichern";
  $("#quote-cancel-edit").hidden = true;
  hideBanner("#quote-lock-banner");
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

/**
 * Marker statt Umwandeln-Knopf: aus diesem Beleg ist bereits ein anderer
 * entstanden. Die API lehnt eine zweite Umwandlung ohnehin ab – hier steht
 * nur, welcher Beleg schon existiert.
 */
function convertedMarker(icon, number) {
  return `<span class="converted-marker" title="Bereits umgewandelt – eine zweite `
       + `Umwandlung würde den Beleg doppeln">${icon} ${esc(number)}</span>`;
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
        <a class="link" href="/api/quotes/${q.id}/pdf" data-act="pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${q.status === "offen" ? `<button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${q.status === "offen" ? `<button class="link" data-act="accept" title="Als angenommen markieren">✅ <span>annehmen</span></button>` : ""}
        ${q.status === "offen" ? `<button class="warn" data-act="decline" title="Als abgelehnt markieren">🚫 <span>ablehnen</span></button>` : ""}
        ${q.status === "umgewandelt" || q.converted_invoice_number
          ? convertedMarker("🧾", q.converted_invoice_number || "umgewandelt")
          : `<button class="link" data-act="convert" title="In Rechnung umwandeln">🧾 <span>zu Rechnung</span></button>`}
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
    if (res.ok) {
      const inv = await res.json().catch(() => null);
      alert(inv && inv.number ? `✓ Rechnung ${inv.number} erstellt.` : "✓ In Rechnung umgewandelt.");
      // Direkt in die Rechnungsübersicht, wo die neue Rechnung schon steht.
      show("history");
    } else {
      const e = await res.json().catch(() => ({}));
      alert("Fehler: " + (e.detail || res.status));
    }
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

async function startDeliveryEdit(d) {
  if (currentEditDeliveryId !== null && currentEditDeliveryId !== d.id) releaseDeliveryLock();
  const lock = await (await fetch(`/api/delivery-notes/${d.id}/lock`, { method: "POST" })).json();
  if (!lock.editable) {
    alert(`Dieser Lieferschein wird gerade von "${lock.locked_by}" bearbeitet. Bitte später erneut versuchen.`);
    return;
  }
  currentEditDeliveryId = d.id;

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
  showBanner("#delivery-lock-banner",
    `🔒 Lieferschein ${d.number} ist für dich gesperrt, solange du ihn bearbeitest.`);
  $("#delivery-msg").textContent = "";
  startDeliveryLockHeartbeat(d.id);
}

function resetDeliveryForm() {
  releaseDeliveryLock();
  const f = $("#delivery-form");
  f.reset();
  f.delivery_id.value = "";
  deliveryItemsBody.innerHTML = "";
  addDeliveryItemRow();
  $("#delivery-form-title").textContent = "Neuen Lieferschein erstellen";
  $("#delivery-submit").textContent = "Lieferschein speichern";
  $("#delivery-cancel-edit").hidden = true;
  hideBanner("#delivery-lock-banner");
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

// "abgeschlossen" bekommt dieselbe grüne Plakette wie eine bezahlte Rechnung.
const DN_BADGE_CLASS = { offen: "offen", abgeschlossen: "bezahlt", storniert: "storniert" };

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
      <td><span class="badge ${DN_BADGE_CLASS[d.status] || "offen"}">${esc(d.status)}</span></td>
      <td class="actions">
        <a class="link" href="/api/delivery-notes/${d.id}/pdf" data-act="pdf"
           title="PDF herunterladen – der Lieferschein gilt danach als abgeschlossen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${d.status !== "storniert" ? `<button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${d.converted_quote_number
          ? convertedMarker("📄", d.converted_quote_number)
          : d.status !== "storniert"
            ? `<button class="link" data-act="to-quote" title="In Angebot umwandeln">📄 <span>zu Angebot</span></button>`
            : ""}
        ${d.status === "offen"
          ? `<button class="warn" data-act="cancel" title="Stornieren">🚫 <span>stornieren</span></button>`
          : `<button class="link" data-act="reopen" title="Wieder auf offen setzen">↩️ <span>wieder öffnen</span></button>`}
        ${d.status === "abgeschlossen"
          ? `<button class="warn" data-act="cancel" title="Stornieren">🚫 <span>stornieren</span></button>`
          : ""}
        <button class="danger" data-act="delete" title="Lieferschein löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => deliveryAction(btn.dataset.act, d);
    });
    // Der PDF-Link lädt ganz normal herunter; der Server setzt dabei den
    // Status auf "abgeschlossen", also holen wir die Liste kurz danach neu.
    tr.querySelector("a[data-act=pdf]").addEventListener("click", () => {
      setTimeout(loadDeliveryNotes, 800);
    });
    body.appendChild(tr);
  }
}

async function deliveryAction(act, d) {
  if (act === "edit") { startDeliveryEdit(d); return; }
  if (act === "to-quote") {
    if (!confirm(`Lieferschein ${d.number} jetzt in ein Angebot umwandeln?`)) return;
    const res = await fetch(`/api/delivery-notes/${d.id}/convert-to-quote`, { method: "POST" });
    if (res.ok) {
      const q = await res.json();
      alert(`✓ Angebot ${q.number} erstellt. Die Preise kommen – soweit vorhanden – `
            + `aus dem Artikelstamm und lassen sich im Angebot anpassen.`);
      show("quotes");
    } else {
      const e = await res.json().catch(() => ({}));
      alert("Fehler: " + (e.detail || res.status));
    }
    return;
  }
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

// ---------------------- Gutschriften ----------------------
// Eigene Belegart: eine Gutschrift mindert den offenen Betrag ihrer Rechnung.
// Aus der Rechnungsübersicht heraus kommen die Positionen gleich mit; von
// Hand geht es auch ohne Rechnungsbezug.
const creditItemsBody = $("#credit-items-body");
let creditCache = [];

const CN_BADGE_CLASS = { offen: "offen", erstattet: "bezahlt", storniert: "storniert" };

function addCreditItemRow(desc = "", qty = 1, price = 0) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input class="ci-desc" type="text" list="credit-product-list" placeholder="Leistung / Artikel"></td>
    <td><input class="ci-qty col-num" type="number" min="0" step="0.01"></td>
    <td><input class="ci-price col-num" type="number" min="0" step="0.01"></td>
    <td><button type="button" class="remove-item" title="Entfernen">✕</button></td>`;
  tr.querySelector(".ci-desc").value = desc;
  tr.querySelector(".ci-qty").value = qty;
  tr.querySelector(".ci-price").value = price;
  tr.querySelector(".remove-item").onclick = () => tr.remove();
  creditItemsBody.appendChild(tr);
}
$("#credit-add-item").onclick = () => addCreditItemRow();

function creditInvoiceHint(invoice) {
  const hint = $("#credit-invoice-hint");
  if (!invoice) { hint.hidden = true; hint.textContent = ""; return; }
  hint.hidden = false;
  hint.textContent = `Gutschrift zu Rechnung ${invoice.number} `
    + `(offen: ${euro(invoice.remaining)}). Positionen lassen sich streichen `
    + `oder ändern – dann wird nur der Rest gutgeschrieben.`;
}

/** „Gutschrift" in der Rechnungsübersicht: Formular mit der Rechnung füllen. */
function startCreditNoteForInvoice(inv) {
  if (!inv) return;
  resetCreditForm();
  const f = $("#credit-form");
  f.invoice_id.value = inv.id;
  f.customer_name.value = inv.customer_name;
  f.customer_address.value = inv.customer_address || "";
  f.customer_contact_person.value = inv.customer_contact_person || "";
  f.tax_rate.value = inv.tax_rate;
  f.small_business.checked = !!inv.small_business;
  // Rabatt der Rechnung steckt im Einzelpreis der Gutschrift.
  const factor = 1 - (inv.discount_percent || 0) / 100;
  creditItemsBody.innerHTML = "";
  for (const it of inv.items || []) {
    addCreditItemRow(it.description, it.quantity,
                     Math.round(it.unit_price * factor * 100) / 100);
  }
  if (!creditItemsBody.children.length) addCreditItemRow();
  creditInvoiceHint(inv);
  show("credit");
}

function startCreditEdit(cn) {
  resetCreditForm();
  const f = $("#credit-form");
  f.credit_id.value = cn.id;
  f.invoice_id.value = cn.invoice_id || "";
  f.customer_name.value = cn.customer_name;
  f.customer_address.value = cn.customer_address || "";
  f.customer_contact_person.value = cn.customer_contact_person || "";
  f.tax_rate.value = cn.tax_rate;
  f.small_business.checked = !!cn.small_business;
  f.reason.value = cn.reason || "";
  creditItemsBody.innerHTML = "";
  for (const it of cn.items) addCreditItemRow(it.description, it.quantity, it.unit_price);
  $("#credit-form-title").textContent = `Gutschrift ${cn.number} bearbeiten`;
  $("#credit-submit").textContent = "Änderungen speichern";
  $("#credit-cancel-edit").hidden = false;
  if (cn.invoice_number) {
    const hint = $("#credit-invoice-hint");
    hint.hidden = false;
    hint.textContent = `Gehört zu Rechnung ${cn.invoice_number}.`;
  }
}

function resetCreditForm() {
  const f = $("#credit-form");
  f.reset();
  f.credit_id.value = "";
  f.invoice_id.value = "";
  f.tax_rate.value = 20;
  creditItemsBody.innerHTML = "";
  addCreditItemRow();
  creditInvoiceHint(null);
  $("#credit-form-title").textContent = "Neue Gutschrift erstellen";
  $("#credit-submit").textContent = "Gutschrift speichern";
  $("#credit-cancel-edit").hidden = true;
  $("#credit-msg").textContent = "";
  setPendingCustomer("#credit-customer-select", "");
}
$("#credit-cancel-edit").addEventListener("click", resetCreditForm);

$("#credit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#credit-msg");
  const items = [...creditItemsBody.querySelectorAll("tr")].map((tr) => ({
    description: tr.querySelector(".ci-desc").value.trim(),
    quantity: parseFloat(tr.querySelector(".ci-qty").value) || 0,
    unit_price: parseFloat(tr.querySelector(".ci-price").value) || 0,
  })).filter((it) => it.description && it.quantity > 0);

  if (!items.length) {
    msg.textContent = "Mindestens eine Position mit Beschreibung und Menge nötig.";
    msg.className = "err";
    return;
  }

  const f = e.target;
  const editId = f.credit_id.value;
  const payload = {
    customer_name: f.customer_name.value.trim(),
    customer_address: f.customer_address.value,
    customer_contact_person: f.customer_contact_person.value.trim(),
    invoice_id: f.invoice_id.value ? Number(f.invoice_id.value) : null,
    reason: f.reason.value,
    tax_rate: parseFloat(f.tax_rate.value) || 0,
    small_business: f.small_business.checked,
    items,
  };

  const res = await fetch(editId ? `/api/credit-notes/${editId}` : "/api/credit-notes", {
    method: editId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.ok) {
    const cn = await res.json();
    resetCreditForm();
    loadCreditNotes();
    msg.textContent = `✓ Gespeichert als ${cn.number}`;
    msg.className = "ok";
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

async function loadCreditNotes() {
  creditCache = await (await fetch("/api/credit-notes")).json();
  renderCreditNotes();
}

function renderCreditNotes() {
  const q0 = $("#credit-search").value.trim().toLowerCase();
  const status = $("#credit-filter-status").value;
  const rows = creditCache.filter((c) =>
    (!status || c.status === status) &&
    (!q0 || `${c.number} ${c.customer_name}`.toLowerCase().includes(q0)));

  const body = $("#credit-body");
  body.innerHTML = "";
  $("#credit-empty").hidden = creditCache.length > 0;
  $("#credit-nomatch").hidden = creditCache.length === 0 || rows.length > 0;
  for (const c of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(c.number)}</td>
      <td>${esc(c.customer_name)}</td>
      <td>${c.invoice_number ? esc(c.invoice_number) : "–"}</td>
      <td>${fmtDate(c.issue_date)}</td>
      <td class="col-num">${euro(c.total)}</td>
      <td><span class="badge ${CN_BADGE_CLASS[c.status] || "offen"}">${esc(c.status)}</span></td>
      <td class="actions">
        <a class="link" href="/api/credit-notes/${c.id}/pdf" data-act="pdf" title="PDF herunterladen">⬇️ <span>PDF</span></a>
        <button class="link" data-act="email" title="Per E-Mail senden">✉️ <span>Mail</span></button>
        ${c.status !== "storniert" ? `<button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>` : ""}
        ${c.status === "offen" ? `<button class="link" data-act="settle" title="Als erstattet markieren">✅ <span>erstattet</span></button>` : ""}
        ${c.status !== "storniert"
          ? `<button class="warn" data-act="cancel" title="Stornieren">🚫 <span>stornieren</span></button>`
          : `<button class="link" data-act="reopen" title="Storno rückgängig">↩️ <span>zurück</span></button>`}
        <button class="danger" data-act="delete" title="Gutschrift löschen">🗑️ <span>löschen</span></button>
      </td>`;
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => creditAction(btn.dataset.act, c);
    });
    body.appendChild(tr);
  }
}

$("#credit-search").addEventListener("input", renderCreditNotes);
$("#credit-filter-status").addEventListener("change", renderCreditNotes);

async function creditAction(act, c) {
  if (act === "edit") { startCreditEdit(c); return; }
  if (act === "email") {
    const to = prompt("Gutschrift per E-Mail senden an:", emailForCustomer(c.customer_name));
    if (!to) return;
    const res = await fetch(`/api/credit-notes/${c.id}/email`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to.trim() }),
    });
    if (res.ok) alert(`✓ ${c.number} gesendet.`);
    else { const e = await res.json().catch(() => ({})); alert("Fehler: " + (e.detail || res.status)); }
    return;
  }
  if (act === "delete") {
    if (!confirm(`Gutschrift ${c.number} wirklich löschen?`)) return;
    await fetch(`/api/credit-notes/${c.id}`, { method: "DELETE" });
    loadCreditNotes();
    return;
  }
  const map = { settle: "erstattet", cancel: "storniert", reopen: "offen" };
  if (map[act]) {
    await fetch(`/api/credit-notes/${c.id}/status`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: map[act] }),
    });
    loadCreditNotes();
  }
}

// ---------------------- Auswertungen ----------------------
function reportPeriod() {
  const from = $("#report-from").value;
  const to = $("#report-to").value;
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  return params.toString();
}

async function loadReports() {
  if (!$("#report-from").value) {
    const year = new Date().getFullYear();
    $("#report-from").value = `${year}-01-01`;
    $("#report-to").value = `${year}-12-31`;
  }
  const query = reportPeriod();
  const [vat, revenue] = await Promise.all([
    fetch(`/api/reports/vat?${query}`).then((r) => r.json()),
    fetch(`/api/reports/revenue?${query}`).then((r) => r.json()),
  ]);
  renderVatReport(vat);
  renderRevenueReport(revenue);
  loadCustomReport();
}

function renderVatReport(report) {
  const body = $("#vat-body");
  body.innerHTML = "";
  for (const r of report.rows || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.tax_rate}&nbsp;%</td>
      <td class="col-num">${euro(r.net)}</td>
      <td class="col-num">${euro(r.tax)}</td>
      <td class="col-num">${euro(r.gross)}</td>
      <td class="col-num">${r.invoice_count}</td>
      <td class="col-num">${r.credit_note_count}</td>`;
    body.appendChild(tr);
  }
  $("#vat-total").innerHTML = `
    <th>Summe</th>
    <th class="col-num">${euro(report.net_total)}</th>
    <th class="col-num">${euro(report.tax_total)}</th>
    <th class="col-num">${euro(report.gross_total)}</th>
    <th></th><th></th>`;
}

function kpiTiles(target, tiles) {
  $(target).innerHTML = tiles.map(([val, label, cls = ""]) =>
    `<div class="kpi ${cls}"><div class="kpi-val">${val}</div>` +
    `<div class="kpi-label">${esc(label)}</div></div>`).join("");
}

function renderRevenueReport(report) {
  kpiTiles("#revenue-kpis", [
    [euro(report.net), "Erlös netto", "blue"],
    [euro(report.gross), "Erlös brutto"],
    [euro(report.credited_net), "Gutschriften netto", "red"],
    [euro(report.paid), "Bezahlt", "green"],
    [euro(report.open_amount), "Noch offen"],
    [String(report.invoice_count), "Rechnungen"],
  ]);

  const months = $("#revenue-month-body");
  months.innerHTML = "";
  for (const m of report.months || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(m.label)}</td>
      <td class="col-num">${euro(m.invoiced_net)}</td>
      <td class="col-num">${euro(m.credited_net)}</td>
      <td class="col-num">${euro(m.net)}</td>
      <td class="col-num">${euro(m.gross)}</td>`;
    months.appendChild(tr);
  }

  const customers = $("#revenue-customer-body");
  customers.innerHTML = "";
  for (const c of report.customers || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(c.customer_name)}</td>
      <td class="col-num">${euro(c.net)}</td>
      <td class="col-num">${euro(c.credited_net)}</td>
      <td class="col-num">${c.invoice_count}</td>`;
    customers.appendChild(tr);
  }
}

$("#report-refresh").onclick = loadReports;
$("#report-year").onclick = () => {
  const year = new Date().getFullYear();
  $("#report-from").value = `${year}-01-01`;
  $("#report-to").value = `${year}-12-31`;
  loadReports();
};
$("#report-from").addEventListener("change", loadReports);
$("#report-to").addEventListener("change", loadReports);
$("#vat-csv").onclick = () => downloadUrl(`/api/reports/vat.csv?${reportPeriod()}`, "ustva.csv");
$("#revenue-csv").onclick = () =>
  downloadUrl(`/api/reports/revenue.csv?${reportPeriod()}`, "erloese.csv");

// ---------------------- Freier Report-Builder ----------------------
function customReportParams() {
  const params = new URLSearchParams(reportPeriod());
  params.set("doc_type", $("#custom-report-doctype").value);
  params.set("group_by", $("#custom-report-groupby").value);
  const status = $("#custom-report-status").value.trim();
  if (status) params.set("status", status);
  return params.toString();
}

const CUSTOM_REPORT_GROUP_LABEL = { none: "Beleg", customer: "Kunde", month: "Monat", status: "Status" };

async function loadCustomReport() {
  const res = await fetch(`/api/reports/custom?${customReportParams()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    $("#custom-report-head").innerHTML = "";
    $("#custom-report-body").innerHTML = "";
    kpiTiles("#custom-report-kpis", []);
    $("#custom-report-empty").hidden = false;
    $("#custom-report-empty").textContent = "Fehler: " + (err.detail || res.status);
    return;
  }
  renderCustomReport(await res.json());
}

function renderCustomReport(report) {
  const grouped = report.group_by !== "none";
  const headLabels = grouped
    ? [CUSTOM_REPORT_GROUP_LABEL[report.group_by], "Anzahl", "Positionen"]
    : ["Nummer", "Kunde", "Datum", "Status", "Positionen"];
  const numericFrom = grouped ? 1 : 4;   // Spalten ab hier rechtsbündig (col-num)
  if (report.has_amounts) headLabels.push("Netto", "Steuer", "Brutto");

  $("#custom-report-head").innerHTML = headLabels
    .map((label, i) => `<th${i >= numericFrom ? ' class="col-num"' : ""}>${esc(label)}</th>`)
    .join("");

  const body = $("#custom-report-body");
  body.innerHTML = "";
  for (const r of report.rows) {
    const cells = grouped
      ? [esc(r.group), String(r.count), String(r.item_count)]
      : [esc(r.number), esc(r.customer_name), fmtDate(r.issue_date), esc(r.status),
         String(r.item_count)];
    if (report.has_amounts) cells.push(euro(r.net), euro(r.tax), euro(r.gross));
    const tr = document.createElement("tr");
    tr.innerHTML = cells
      .map((c, i) => `<td${i >= numericFrom ? ' class="col-num"' : ""}>${c}</td>`)
      .join("");
    body.appendChild(tr);
  }
  $("#custom-report-empty").textContent = "Keine Belege für diese Auswahl.";
  $("#custom-report-empty").hidden = report.rows.length > 0;

  const t = report.totals;
  const tiles = [[String(t.count), grouped ? "Belege gesamt" : "Belege"]];
  if (report.has_amounts) {
    tiles.push([euro(t.net), "Netto"], [euro(t.tax), "Steuer"], [euro(t.gross), "Brutto"]);
  } else {
    tiles.push([String(t.item_count), "Positionen gesamt"]);
  }
  kpiTiles("#custom-report-kpis", tiles);
}

$("#custom-report-refresh").onclick = loadCustomReport;
$("#custom-report-doctype").addEventListener("change", loadCustomReport);
$("#custom-report-groupby").addEventListener("change", loadCustomReport);
$("#custom-report-csv").onclick = () =>
  downloadUrl(`/api/reports/custom.csv?${customReportParams()}`, "report.csv");

// ---------------------- Monitoring (nur Admin) ----------------------
function fmtDuration(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} Tage`;
}

async function loadMonitoring() {
  // Bei 1 s Takt darf sich ein langsamer Abruf nicht mit dem nächsten stapeln.
  if (monitoringBusy) return;
  monitoringBusy = true;
  try {
    await fillMonitoring();
  } finally {
    monitoringBusy = false;
  }
}

async function fillMonitoring() {
  const res = await fetch("/api/admin/metrics");
  if (!res.ok) {
    // Sonst bliebe die Anzeige stumm auf alten Zahlen stehen und der Takt
    // sähe aus, als liefe er nicht.
    monitoringError = "Abruf fehlgeschlagen (" + res.status + ")";
    monitoringStamp();
    return;
  }
  const m = await res.json();

  const errorRate = (m.error_rate * 100).toFixed(2).replace(".", ",");
  kpiTiles("#monitoring-kpis", [
    [fmtDuration(m.uptime_seconds), "Laufzeit"],
    [String(m.requests_total), "Requests"],
    [String(m.server_errors_total), "Serverfehler", m.server_errors_total ? "red" : "green"],
    [`${errorRate} %`, "Fehlerquote"],
    [`${(m.avg_response_seconds * 1000).toFixed(0)} ms`, "Antwortzeit ⌀"],
    [String(m.slow_requests_total), "langsame Requests"],
    [String(m.failed_logins_in_window), `Fehlanmeldungen (${m.alert_window_seconds / 60} min)`,
     m.failed_logins_in_window ? "red" : ""],
    [m.backup_age_hours === null ? "–" : `${Math.round(m.backup_age_hours)} h`, "jüngstes Backup"],
  ]);

  const docs = m.documents || {};
  kpiTiles("#monitoring-documents", [
    [String(docs.invoices ?? 0), "Rechnungen"],
    [String(docs.quotes ?? 0), "Angebote"],
    [String(docs.delivery_notes ?? 0), "Lieferscheine"],
    [String(docs.credit_notes ?? 0), "Gutschriften"],
    [String(docs.customers ?? 0), "Kunden"],
    [String(docs.users ?? 0), "Benutzer"],
  ]);

  const a = m.alerts || {};
  const lastSent = Object.entries(a.last_sent || {})
    .map(([kind, at]) => `${kind}: ${at}`).join(", ");
  $("#monitoring-alerts").textContent = a.enabled
    ? `Alarm-Mails an die Firmen-E-Mail sind aktiv. Schwellen: `
      + `${a.error_threshold} Serverfehler bzw. ${a.login_threshold} Fehlanmeldungen `
      + `je ${m.alert_window_seconds / 60} Minuten, Backup älter als `
      + `${a.backup_max_age_hours} h. Sperrfrist ${a.cooldown_seconds / 60} min.`
      + (lastSent ? ` Zuletzt gemeldet – ${lastSent}.` : "")
    : "Alarm-Mails sind abgeschaltet (ALERTS_ENABLED=false). "
      + "Auffälligkeiten stehen weiterhin hier und im Log.";

  const body = $("#monitoring-errors-body");
  body.innerHTML = "";
  for (const e of m.recent_errors || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(e.at)}</td><td>${esc(e.method)}</td><td>${esc(e.path)}</td>
      <td>${e.status}</td><td>${esc(e.request_id || "–")}</td>`;
    body.appendChild(tr);
  }
  $("#monitoring-noerrors").hidden = (m.recent_errors || []).length > 0;
  monitoringLastAt = new Date();
  monitoringError = "";
  monitoringStamp();
}

// Automatisch aktualisieren: das gewählte Intervall überlebt im localStorage,
// der Takt läuft nur, solange die Monitoring-Ansicht wirklich offen ist.
const MONITORING_INTERVAL_KEY = "rechnung.monitoring.interval";
let monitoringTimer = null;
let monitoringBusy = false;
let monitoringLastAt = null;
let monitoringError = "";

function monitoringSeconds() {
  const select = $("#monitoring-auto");
  return select ? parseInt(select.value, 10) || 0 : 0;
}

// Neben dem Knopf steht, wann die Zahlen zuletzt kamen und in welchem Takt sie
// nachkommen – daran sieht man, dass sich etwas tut, auch wenn die Kennzahlen
// gerade gleich bleiben.
function monitoringStamp() {
  const el = $("#monitoring-stamp");
  if (!el) return;
  const select = $("#monitoring-auto");
  const takt = monitoringSeconds() && select && select.selectedOptions[0]
    ? " · " + select.selectedOptions[0].textContent : "";
  if (monitoringError) {
    el.textContent = monitoringError + takt;
    el.className = "err";
    return;
  }
  el.textContent = (monitoringLastAt
    ? "Stand " + monitoringLastAt.toLocaleTimeString("de-DE")
    : "noch nicht geladen") + takt;
  el.className = "hint";
}

function stopMonitoringAuto() {
  if (monitoringTimer === null) return;
  clearTimeout(monitoringTimer);
  monitoringTimer = null;
}

// Kein setInterval: der nächste Abruf wird erst gestellt, wenn der vorige
// durch ist. So stapeln sich langsame Antworten nicht, und ein Takt, der in
// einem Hintergrund-Tab ausgesetzt hat, läuft beim Zurückkommen weiter,
// statt hängen zu bleiben.
function startMonitoringAuto() {
  stopMonitoringAuto();
  monitoringStamp();
  const seconds = monitoringSeconds();
  if (!seconds || views.monitoring.hidden) return;
  monitoringTimer = setTimeout(async () => {
    monitoringTimer = null;
    // Ein Tab im Hintergrund fragt nicht nach; der Takt selbst läuft weiter.
    if (!document.hidden) await loadMonitoring();
    startMonitoringAuto();
  }, seconds * 1000);
}

$("#monitoring-auto").onchange = () => {
  try { localStorage.setItem(MONITORING_INTERVAL_KEY, $("#monitoring-auto").value); }
  catch (_) { /* Speicher blockiert – dann gilt die Wahl nur für diese Sitzung */ }
  loadMonitoring();          // die Wahl soll sofort etwas bewirken
  startMonitoringAuto();
};

// Zurück im Vordergrund: sofort frische Zahlen, statt bis zum nächsten Takt
// alte anzuzeigen.
document.addEventListener("visibilitychange", () => {
  if (document.hidden || views.monitoring.hidden || !monitoringSeconds()) return;
  loadMonitoring();
  startMonitoringAuto();
});

(function restoreMonitoringInterval() {
  let saved = null;
  try { saved = localStorage.getItem(MONITORING_INTERVAL_KEY); } catch (_) { return; }
  const select = $("#monitoring-auto");
  if (saved !== null && [...select.options].some((o) => o.value === saved)) {
    select.value = saved;
  }
})();

$("#monitoring-refresh").onclick = () => {
  loadMonitoring();
  startMonitoringAuto();     // von Hand geholt heißt: der Takt beginnt neu
};
$("#monitoring-prom").onclick = () => downloadUrl("/api/admin/metrics.prom", "metrics.prom");
$("#monitoring-test-alert").onclick = async () => {
  const msg = $("#monitoring-msg");
  const res = await fetch("/api/admin/metrics/test-alert", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  msg.textContent = res.ok ? `✓ Probealarm an ${data.to} gesendet.`
                           : "Fehler: " + (data.detail || res.status);
  msg.className = res.ok ? "ok" : "err";
};

// ---------------------- Firmendaten / Logo ----------------------
function showLogo(hasLogo) {
  const img = $("#logo-preview");
  if (hasLogo) {
    img.src = "/api/settings/logo?ts=" + Date.now();
    img.hidden = false; $("#logo-none").hidden = true;
  } else {
    img.removeAttribute("src");
    img.hidden = true; $("#logo-none").hidden = false;
  }
}

async function loadSettings() {
  const s = await (await fetch("/api/settings")).json();
  const f = $("#settings-form");
  for (const k of ["company_name", "email", "phone", "tax_id", "vat_id", "iban", "bic", "address"]) {
    if (f[k]) f[k].value = s[k] || "";
  }
  showLogo(s.has_logo);
  loadPdfTemplates();
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
  if (res.ok) {
    // Nur die Vorschau erneuern: ein Neuladen der Einstellungen würde die
    // eingetippten, noch nicht gespeicherten Firmendaten überschreiben.
    showLogo(true);
    e.target.value = "";
    const msg = $("#settings-msg");
    msg.textContent = "✓ Logo hochgeladen";
    msg.className = "ok";
  } else {
    const er = await res.json().catch(() => ({}));
    alert("Fehler: " + (er.detail || res.status));
  }
});

// ---------------------- PDF-Vorlagen ----------------------
// Verwaltung in den Firmendaten (nur Admins), Auswahl beim Download für alle.
let templatesCache = [];

async function refreshPdfTemplates() {
  try {
    const data = await (await fetch("/api/pdf-templates")).json();
    templatesCache = Array.isArray(data) ? data : [];
  } catch (_) {
    templatesCache = [];
  }
}

async function loadPdfTemplates() {
  await refreshPdfTemplates();
  renderPdfTemplates();
}

function renderPdfTemplates() {
  const body = $("#template-body");
  if (!body) return;
  body.innerHTML = "";
  $("#template-empty").hidden = templatesCache.length > 0;
  for (const t of templatesCache) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(t.name)}</td>
      <td>${t.layout === "formular" ? "Formular" : "Standard"}</td>
      <td>${esc(t.font_family)} ${t.font_size} pt</td>
      <td>
        <span class="swatch" data-color="${esc(t.accent_color)}"></span>
        <span class="swatch" data-color="${esc(t.header_color)}"></span>
      </td>
      <td>${t.is_default ? "★ Vorgabe" : ""}</td>
      <td class="actions">
        <a class="link" href="/api/pdf-templates/${t.id}/preview" target="_blank"
           rel="noopener" title="Musterrechnung ansehen">👁️ <span>Vorschau</span></a>
        <button class="link" data-act="edit" title="Bearbeiten">✏️ <span>bearbeiten</span></button>
        ${t.is_default ? "" : `<button class="link" data-act="default" title="Als Vorgabe setzen">★ <span>Vorgabe</span></button>`}
        <button class="danger" data-act="delete" title="Vorlage löschen">🗑️ <span>löschen</span></button>
      </td>`;
    // Die Farbe wird per CSSOM gesetzt, nicht als style-Attribut: die CSP
    // erlaubt keine Inline-Styles (style-src 'self'), ein
    // style="background:..." verwirft der Browser – die beiden Kästchen
    // blieben dann leer. Gleiche Stelle wie bei den Diagrammbalken oben.
    tr.querySelectorAll(".swatch").forEach((sw) => {
      sw.style.background = sw.dataset.color;
    });
    tr.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => templateAction(btn.dataset.act, t);
    });
    body.appendChild(tr);
  }
}

function startTemplateEdit(t) {
  const f = $("#template-form");
  f.template_id.value = t.id;
  f.name.value = t.name;
  f.layout.value = t.layout || "standard";
  f.font_family.value = t.font_family;
  f.font_size.value = t.font_size;
  f.accent_color.value = t.accent_color;
  f.header_color.value = t.header_color;
  f.header_note.value = t.header_note || "";
  f.footer_text.value = t.footer_text || "";
  f.show_logo.checked = !!t.show_logo;
  f.show_qr.checked = !!t.show_qr;
  $("#template-submit").textContent = "Änderungen speichern";
  $("#template-cancel-edit").hidden = false;
  $("#template-msg").textContent = "";
}

function resetTemplateForm() {
  const f = $("#template-form");
  f.reset();
  f.template_id.value = "";
  f.layout.value = "standard";
  f.accent_color.value = "#2d6cdf";
  f.header_color.value = "#2d3748";
  f.font_size.value = 10;
  f.show_logo.checked = true;
  f.show_qr.checked = true;
  $("#template-submit").textContent = "Vorlage speichern";
  $("#template-cancel-edit").hidden = true;
  $("#template-msg").textContent = "";
}
$("#template-cancel-edit").addEventListener("click", resetTemplateForm);

async function templateAction(act, t) {
  if (act === "edit") { startTemplateEdit(t); return; }
  if (act === "default") {
    await fetch(`/api/pdf-templates/${t.id}/default`, { method: "POST" });
    loadPdfTemplates();
    return;
  }
  if (act === "delete") {
    if (!confirm(`Vorlage ${t.name} wirklich löschen?`)) return;
    await fetch(`/api/pdf-templates/${t.id}`, { method: "DELETE" });
    resetTemplateForm();
    loadPdfTemplates();
  }
}

$("#template-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = $("#template-msg");
  const editId = f.template_id.value;
  const payload = {
    name: f.name.value.trim(),
    layout: f.layout.value,
    font_family: f.font_family.value,
    font_size: parseFloat(f.font_size.value) || 10,
    accent_color: f.accent_color.value,
    header_color: f.header_color.value,
    header_note: f.header_note.value,
    footer_text: f.footer_text.value,
    show_logo: f.show_logo.checked,
    show_qr: f.show_qr.checked,
  };
  const res = await fetch(editId ? `/api/pdf-templates/${editId}` : "/api/pdf-templates", {
    method: editId ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    resetTemplateForm();
    loadPdfTemplates();
    msg.textContent = "✓ Vorlage gespeichert.";
    msg.className = "ok";
  } else {
    const err = await res.json().catch(() => ({}));
    msg.textContent = "Fehler beim Speichern: " + (err.detail || res.status);
    msg.className = "err";
  }
});

// Auswahl beim Download: gibt es überhaupt eine Vorlage, fragt ein kleines
// Fenster nach – sonst lädt der Link direkt herunter. (Früher erschien das
// Fenster erst ab zwei Vorlagen; wer nur den Vordruck angelegt hatte, sah
// beim Beleg nie eine Auswahl.)
const pdfTemplateMenu = document.createElement("div");
pdfTemplateMenu.className = "popover floating";
pdfTemplateMenu.id = "pdf-template-menu";
pdfTemplateMenu.hidden = true;
document.body.appendChild(pdfTemplateMenu);

function closePdfTemplateMenu() { pdfTemplateMenu.hidden = true; }

function templateChoiceLabel(t) {
  return t.name + (t.layout === "formular" ? " (Vordruck)" : "")
                + (t.is_default ? " ★" : "");
}

function openPdfTemplateMenu(link) {
  const href = link.getAttribute("href");
  pdfTemplateMenu.innerHTML = `<p class="popover-title">Mit welcher Vorlage?</p>`;
  const choices = templatesCache.map((t) => ({ id: t.id,
                                              name: templateChoiceLabel(t) }));
  // Ohne Vorgabe-Vorlage gibt es zusätzlich das Standardaussehen (kein
  // ?template=); mit Vorgabe steht die schon mit ★ in der Liste.
  if (!templatesCache.some((t) => t.is_default)) {
    choices.unshift({ id: "", name: "Vorgabe (Standardaussehen)" });
  }
  for (const choice of choices) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary";
    btn.textContent = choice.name;
    btn.onclick = () => {
      closePdfTemplateMenu();
      downloadUrl(choice.id ? `${href}?template=${choice.id}` : href);
    };
    pdfTemplateMenu.appendChild(btn);
  }
  const rect = link.getBoundingClientRect();
  pdfTemplateMenu.style.top = `${rect.bottom + 4}px`;
  pdfTemplateMenu.style.left = `${rect.left}px`;
  pdfTemplateMenu.hidden = false;
}

document.addEventListener("click", (e) => {
  const link = e.target.closest && e.target.closest('a[data-act="pdf"]');
  if (!link) {
    if (!pdfTemplateMenu.hidden && !pdfTemplateMenu.contains(e.target)) {
      closePdfTemplateMenu();
    }
    return;
  }
  if (!templatesCache.length) return;      // ohne Vorlage nichts zu wählen
  e.preventDefault();
  openPdfTemplateMenu(link);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closePdfTemplateMenu();
});
// Das Menü steht fest im Fenster (position: fixed) und wird einmal aus der
// Position der Zeile berechnet. Scrollt die Seite oder die Tabelle weiter,
// wandert die Zeile darunter weg – dann lieber zumachen als daneben stehen.
window.addEventListener("scroll", closePdfTemplateMenu, true);

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
        <a class="link" href="/api/admin/backups/${encodeURIComponent(b.name)}/download" title="Backup herunterladen">⬇️ <span>herunterladen</span></a>
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
  hideBanner(`#${name}-draft-banner`);
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
  showBanner(`#${name}-draft-banner`,
    `🗂️ Wiederhergestellt: nicht gespeicherte Eingaben vom ${new Date(savedAt).toLocaleString("de-DE")}.`);
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
    $("#nav-monitoring").hidden = !currentIsAdmin;
    // Der Burger-Knopf selbst hängt zusätzlich vom Handy-Layout ab
    // (syncMobileNav), currentIsAdmin stand beim ersten Aufruf noch nicht fest.
    syncMobileNav();
  } catch (_) { /* fetch leitet bei 401 selbst um */ }
}

$("#invoice-form [name=issue_date]").value = todayISO();
$("#export-month").value = todayISO().slice(0, 7);
addItemRow();
addQuoteItemRow();
addDeliveryItemRow();
addCreditItemRow();
restoreDrafts();
loadCurrentUser();
refreshCustomers();
refreshProducts();
refreshPdfTemplates();
loadDashboard();
