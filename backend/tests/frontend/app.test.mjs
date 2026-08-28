/**
 * Frontend-Tests für app.js (Kundenauswahl, Entwurfs-Zwischenspeicher,
 * Such-/Filterfunktionen, CSRF-Header).
 *
 * Läuft mit dem Node-Testrunner gegen ein jsdom-Fenster – dieselbe index.html
 * und dieselbe app.js, die auch ausgeliefert werden:
 *
 *     cd backend/tests/frontend && npm install && npm test
 *
 * Die Backend-Tests (pytest) laufen unabhängig davon; wer kein Node hat,
 * verliert nur diese Datei.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test, { after } from "node:test";
import { JSDOM } from "jsdom";

// Jedes startApp() öffnet ein jsdom-Fenster mit laufenden Timern (Entwurfs-
// Debounce, Presence-Heartbeat). Ohne close() am Ende hielten die den
// Node-Prozess offen und der Testlauf würde nie beenden.
const openWindows = [];
after(() => {
  for (const window of openWindows) window.close();
});

const HTML = readFileSync(new URL("../../app/static/index.html", import.meta.url), "utf8");
const APP_JS = readFileSync(new URL("../../app/static/app.js", import.meta.url), "utf8");

const CUSTOMERS = [
  { id: 1, name: "Alpha AG", address: "Alphaweg 1", contact_person: "Frau Alpha",
    email: "info@alpha.example", payment_term_days: 30, skonto_percent: 2,
    skonto_days: 7, active: true },
  { id: 2, name: "Beta GmbH", address: "Betaweg 2", contact_person: "Herr Beta",
    email: "kontakt@beta.example", payment_term_days: 14, skonto_percent: 0,
    skonto_days: 0, active: true },
  { id: 3, name: "Gamma KG", address: "", contact_person: "", email: "",
    payment_term_days: 14, skonto_percent: 0, skonto_days: 0, active: false },
];

const QUOTES = [
  { id: 1, number: "AN-2026-0001", customer_name: "Alpha AG", issue_date: "2026-01-05",
    total: 500, status: "offen", items: [] },
  { id: 2, number: "AN-2026-0002", customer_name: "Beta GmbH", issue_date: "2026-01-06",
    total: 700, status: "angenommen", items: [] },
];

const STATS = {
  total_revenue: 1200, open_amount: 500, overdue_amount: 0, invoice_count: 2,
  paid_count: 1, overdue_count: 0,
  months: [{ label: "Jan", revenue: 100 }, { label: "Feb", revenue: 0 }],
};

function response(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(),
    json: async () => data,
    clone() { return this; },
  };
}

/**
 * jsdom kennt den „named property getter" von HTMLFormElement nicht, den
 * Browser haben: `form.customer_name` ist dort undefined, obwohl
 * `form.elements.customer_name` existiert. app.js nutzt (wie im Browser
 * üblich) die Kurzform – deshalb hier nachgerüstet.
 */
function addFormNamedAccess(window) {
  for (const form of window.document.querySelectorAll("form")) {
    for (const el of [...form.elements]) {
      if (!el.name || Object.getOwnPropertyDescriptor(form, el.name)) continue;
      Object.defineProperty(form, el.name, {
        configurable: true,
        get: () => form.elements[el.name],
      });
    }
  }
}

/** Startet die App in einem frischen jsdom-Fenster. */
function startApp({ customers = CUSTOMERS, quotes = QUOTES, storage = {},
                    presence = [], others = [], routes = {} } = {}) {
  const dom = new JSDOM(HTML, { url: "https://rechnungen.localhost/",
                                runScripts: "outside-only", pretendToBeVisual: true });
  const { window } = dom;
  openWindows.push(window);
  const requests = [];

  window.Headers = Headers;
  window.alert = () => {};
  window.confirm = () => true;
  window.prompt = () => null;
  window.document.cookie = "csrftoken=token-aus-dem-cookie";
  for (const [key, value] of Object.entries(storage)) {
    window.localStorage.setItem(key, value);
  }

  window.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options });
    const path = String(url).split("?")[0];
    const table = {
      "/api/me": { user: "tester", is_admin: true, csrf_token: "t" },
      "/api/customers": customers,
      "/api/products": [{ id: 1, name: "Beratung", unit_price: 100, active: true }],
      "/api/stats": STATS,
      "/api/quotes": quotes,
      "/api/presence": presence,
      "/api/delivery-notes": [],
      "/api/invoices": [],
      "/api/settings": { company_name: "", has_logo: false },
      "/api/users": [{ id: 1, username: "admin", is_admin: true }],
      "/api/audit-log": [],
      "/api/admin/backups": [],
      ...routes,
    };
    if (/^\/api\/presence\/[a-z_]+\/\d+$/.test(path)) {
      return response({ others });
    }
    const route = path in table ? table[path] : { ok: true };
    // Eine Route darf eine Funktion sein, wenn GET und POST sich
    // unterscheiden müssen (z. B. Liste holen vs. Rechnung anlegen).
    return response(typeof route === "function" ? route(options) : route);
  };

  addFormNamedAccess(window);
  window.eval(APP_JS);
  return { window, requests };
}

/** Legt eine Datei in ein <input type="file"> (jsdom kennt keinen Dialog). */
function pickFile(window, selector, name, type) {
  const input = window.document.querySelector(selector);
  const file = new window.File(["xxx"], name, { type });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  fire(input, "change");
  return input;
}

/** Wartet, bis die beim Start ausgelösten fetch-Ketten durchgelaufen sind. */
const settle = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

function optionsOf(window, selector) {
  return [...window.document.querySelector(selector).options]
    .map((o) => o.textContent);
}

function fire(el, type = "input") {
  el.dispatchEvent(new el.ownerDocument.defaultView.Event(type, { bubbles: true }));
}

// ------------------------------------------------ Kundenauswahl (Feature 1)
test("Angebot und Lieferschein bieten dieselbe Kundenauswahl wie die Rechnung", async () => {
  const { window } = startApp();
  await settle();

  for (const sel of ["#customer-select", "#quote-customer-select", "#delivery-customer-select"]) {
    const labels = optionsOf(window, sel);
    assert.equal(labels[0], "– neuen Kunden eingeben –");
    assert.deepEqual(labels.slice(1),
      ["Alpha AG — Frau Alpha", "Beta GmbH — Herr Beta"],
      `${sel}: nur aktive Kunden, mit Ansprechpartner`);
  }
});

test("Inaktive Kunden tauchen in der Auswahl nicht auf", async () => {
  const { window } = startApp();
  await settle();
  assert.ok(!optionsOf(window, "#quote-customer-select").some((l) => l.includes("Gamma")));
});

test("Kundenauswahl füllt das Angebotsformular", async () => {
  const { window } = startApp();
  await settle();

  const select = window.document.querySelector("#quote-customer-select");
  select.value = "1";
  fire(select, "change");

  const form = window.document.querySelector("#quote-form");
  assert.equal(form.customer_name.value, "Alpha AG");
  assert.equal(form.customer_address.value, "Alphaweg 1");
  assert.equal(form.customer_contact_person.value, "Frau Alpha");
});

test("Kundenauswahl füllt das Lieferscheinformular", async () => {
  const { window } = startApp();
  await settle();

  const select = window.document.querySelector("#delivery-customer-select");
  select.value = "2";
  fire(select, "change");

  const form = window.document.querySelector("#delivery-form");
  assert.equal(form.customer_name.value, "Beta GmbH");
  assert.equal(form.customer_contact_person.value, "Herr Beta");
});

test("Kundenauswahl der Rechnung übernimmt weiterhin Zahlungsziel und Skonto", async () => {
  const { window } = startApp();
  await settle();

  const select = window.document.querySelector("#customer-select");
  select.value = "1";
  fire(select, "change");

  const form = window.document.querySelector("#invoice-form");
  assert.equal(form.skonto_percent.value, "2");
  assert.equal(form.skonto_days.value, "7");
  assert.ok(form.due_date.value, "Fälligkeitsdatum aus der Zahlungsfrist");
});

// -------------------------------------- Suche in der Auswahl (Feature 3)
test("Das Suchfeld filtert die Kundenauswahl", async () => {
  const { window } = startApp();
  await settle();

  const search = window.document.querySelector("#quote-customer-search");
  search.value = "beta";
  fire(search);
  assert.deepEqual(optionsOf(window, "#quote-customer-select").slice(1), ["Beta GmbH — Herr Beta"]);

  search.value = "kontakt@beta";        // findet auch über die E-Mail
  fire(search);
  assert.equal(optionsOf(window, "#quote-customer-select").length, 2);

  search.value = "gibtesnicht";
  fire(search);
  const empty = optionsOf(window, "#quote-customer-select");
  assert.deepEqual(empty, ["– kein Kunde passt zur Suche –"]);

  search.value = "";
  fire(search);
  assert.equal(optionsOf(window, "#quote-customer-select").length, 3);
});

// ------------------------------------------ Listen-Suche/Filter (Feature 3)
test("Die Angebotsliste lässt sich durchsuchen und nach Status filtern", async () => {
  const { window } = startApp();
  await settle();

  window.document.querySelector("#nav-quotes").click();
  await settle();
  const rows = () => window.document.querySelectorAll("#quotes-body tr").length;
  assert.equal(rows(), 2);

  const search = window.document.querySelector("#quote-search");
  search.value = "beta";
  fire(search);
  assert.equal(rows(), 1);

  search.value = "AN-2026-0001";        // auch die Belegnummer greift
  fire(search);
  assert.equal(rows(), 1);

  search.value = "";
  fire(search);
  const status = window.document.querySelector("#quote-filter-status");
  status.value = "angenommen";
  fire(status, "change");
  assert.equal(rows(), 1);

  status.value = "abgelehnt";
  fire(status, "change");
  assert.equal(rows(), 0);
  assert.equal(window.document.querySelector("#quotes-nomatch").hidden, false);
});

test("Die Kundenliste lässt sich durchsuchen und nach aktiv/inaktiv filtern", async () => {
  const { window } = startApp();
  await settle();

  window.document.querySelector("#nav-customers").click();
  await settle();
  const rows = () => window.document.querySelectorAll("#customers-body tr").length;
  assert.equal(rows(), 3);

  const search = window.document.querySelector("#customer-list-search");
  search.value = "alphaweg";            // Treffer über die Anschrift
  fire(search);
  assert.equal(rows(), 1);

  search.value = "";
  fire(search);
  const filter = window.document.querySelector("#customer-filter-active");
  filter.value = "0";
  fire(filter, "change");
  assert.equal(rows(), 1);              // nur der inaktive Kunde
});

// ------------------------------------------------- Entwürfe (Feature 2)
test("Eingaben im Angebotsformular landen im Zwischenspeicher", async () => {
  const { window } = startApp();
  await settle();

  const form = window.document.querySelector("#quote-form");
  form.customer_name.value = "Noch nicht gespeichert GmbH";
  fire(form.customer_name);
  form.notes.value = "Bitte bis Freitag";
  fire(form.notes);
  await settle(600);                    // Debounce abwarten

  const drafts = JSON.parse(window.localStorage.getItem("rechnung.drafts.v1"));
  assert.equal(drafts.quote.data.fields.customer_name, "Noch nicht gespeichert GmbH");
  assert.equal(drafts.quote.data.fields.notes, "Bitte bis Freitag");
  assert.ok(drafts.quote.saved_at > 0);
});

test("Ein Entwurf wird nach dem Neuladen wiederhergestellt", async () => {
  const first = startApp();
  await settle();

  const form = first.window.document.querySelector("#quote-form");
  form.customer_name.value = "Wiederkehr AG";
  fire(form.customer_name);
  const desc = first.window.document.querySelector("#quote-items-body .qi-desc");
  desc.value = "Workshop";
  fire(desc);
  const price = first.window.document.querySelector("#quote-items-body .qi-price");
  price.value = "1250";
  fire(price);
  await settle(600);

  const saved = first.window.localStorage.getItem("rechnung.drafts.v1");

  // Neues Fenster = Seite neu geladen
  const second = startApp({ storage: { "rechnung.drafts.v1": saved } });
  await settle();

  const restored = second.window.document.querySelector("#quote-form");
  assert.equal(restored.customer_name.value, "Wiederkehr AG");
  assert.equal(second.window.document.querySelector("#quote-items-body .qi-desc").value,
               "Workshop");
  assert.equal(second.window.document.querySelector("#quote-items-body .qi-price").value,
               "1250");

  const banner = second.window.document.querySelector("#quote-draft-banner");
  assert.equal(banner.hidden, false);
  assert.match(banner.querySelector(".draft-text").textContent, /Wiederhergestellt/);
});

test("Auch die gewählte Kundenauswahl kommt zurück", async () => {
  const first = startApp();
  await settle();
  const select = first.window.document.querySelector("#delivery-customer-select");
  select.value = "2";
  fire(select, "change");
  await settle(600);

  const second = startApp({
    storage: { "rechnung.drafts.v1": first.window.localStorage.getItem("rechnung.drafts.v1") },
  });
  await settle();
  assert.equal(second.window.document.querySelector("#delivery-customer-select").value, "2");
  assert.equal(second.window.document.querySelector("#delivery-form").customer_name.value,
               "Beta GmbH");
});

test("Leere Formulare erzeugen keinen Entwurf", async () => {
  const { window } = startApp();
  await settle();
  const form = window.document.querySelector("#quote-form");
  form.tax_rate.value = "19";           // nur eine Zahl, kein echter Inhalt
  fire(form.tax_rate);
  await settle(600);
  const drafts = JSON.parse(window.localStorage.getItem("rechnung.drafts.v1") || "{}");
  assert.deepEqual(drafts, {});
});

test("„Entwurf verwerfen“ leert Formular und Speicher", async () => {
  const { window } = startApp();
  await settle();

  const form = window.document.querySelector("#quote-form");
  form.customer_name.value = "Weg damit";
  fire(form.customer_name);
  await settle(600);
  assert.ok(window.localStorage.getItem("rechnung.drafts.v1").includes("Weg damit"));

  window.document.querySelector("[data-discard-draft=quote]").click();
  await settle(600);

  assert.equal(form.customer_name.value, "");
  assert.equal(window.document.querySelector("#quote-draft-banner").hidden, true);
  const drafts = JSON.parse(window.localStorage.getItem("rechnung.drafts.v1") || "{}");
  assert.equal(drafts.quote, undefined);
});

test("Der Reiter Neue Rechnung wirft einen wiederhergestellten Entwurf nicht weg", async () => {
  const first = startApp();
  await settle();
  const form = first.window.document.querySelector("#invoice-form");
  form.customer_name.value = "Halbfertig GmbH";
  fire(form.customer_name);
  await settle(600);

  const second = startApp({
    storage: { "rechnung.drafts.v1": first.window.localStorage.getItem("rechnung.drafts.v1") },
  });
  await settle();

  // Der Reiter zeigt den Entwurf an ...
  assert.ok(second.window.document.querySelector("#nav-new").classList.contains("has-draft"));
  // ... und ein Klick darauf behält ihn.
  second.window.document.querySelector("#nav-new").click();
  await settle();
  assert.equal(second.window.document.querySelector("#invoice-form").customer_name.value,
               "Halbfertig GmbH");
});

test("Nach dem Speichern ist der Entwurf weg", async () => {
  const { window } = startApp();
  await settle();
  const form = window.document.querySelector("#quote-form");
  form.customer_name.value = "Fertig AG";
  fire(form.customer_name);
  const desc = window.document.querySelector("#quote-items-body .qi-desc");
  desc.value = "Leistung";
  fire(desc);
  await settle(600);
  assert.ok(window.document.querySelector("#nav-quotes").classList.contains("has-draft"));

  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(600);

  const drafts = JSON.parse(window.localStorage.getItem("rechnung.drafts.v1") || "{}");
  assert.equal(drafts.quote, undefined);
  assert.equal(window.document.querySelector("#nav-quotes").classList.contains("has-draft"), false);
  assert.equal(form.customer_name.value, "");
});

test("Veraltete Entwürfe werden verworfen", async () => {
  const old = JSON.stringify({
    quote: { saved_at: Date.now() - 30 * 24 * 3600 * 1000,
             data: { fields: { customer_name: "Uralt AG" }, items: [] } },
  });
  const { window } = startApp({ storage: { "rechnung.drafts.v1": old } });
  await settle();
  assert.equal(window.document.querySelector("#quote-form").customer_name.value, "");
  assert.equal(window.document.querySelector("#quote-draft-banner").hidden, true);
});

test("Kaputter Speicherinhalt legt die App nicht lahm", async () => {
  const { window } = startApp({ storage: { "rechnung.drafts.v1": "{kein json" } });
  await settle();
  assert.equal(optionsOf(window, "#quote-customer-select").length, 3);
});

// --------------------------------------------------------------- Sicherheit
test("Schreibende Requests bekommen den CSRF-Header", async () => {
  const { window, requests } = startApp();
  await settle();

  const form = window.document.querySelector("#customer-form");
  form.name.value = "Neuer Kunde";
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(50);

  const post = requests.find((r) => r.url === "/api/customers" && r.options.method === "POST");
  assert.ok(post, "POST /api/customers wurde abgeschickt");
  assert.equal(post.options.headers.get("X-CSRF-Token"), "token-aus-dem-cookie");
  assert.equal(post.options.headers.get("Content-Type"), "application/json");
});

test("Lesende Requests brauchen keinen CSRF-Header", async () => {
  const { requests } = startApp();
  await settle();
  const get = requests.find((r) => r.url === "/api/customers" && !r.options.method);
  assert.ok(get);
  assert.ok(!get.options.headers);
});

test("HTML in Stammdaten wird beim Rendern entschärft", async () => {
  const { window } = startApp({
    customers: [{ id: 1, name: '<img src=x onerror="alert(1)">', address: "",
                  contact_person: "", email: "", payment_term_days: 14,
                  skonto_percent: 0, skonto_days: 0, active: true }],
  });
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  const cell = window.document.querySelector("#customers-body tr td");
  assert.equal(window.document.querySelectorAll("#customers-body img").length, 0,
               "kein echtes img-Element im DOM");
  assert.equal(cell.textContent, '<img src=x onerror="alert(1)">');
});


// ------------------------------------------- Live-Anzeige (IDEAS-Punkt 1)
test("Beim Bearbeiten eines Angebots wird ein Heartbeat geschickt", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  const beat = requests.find((r) => r.url === "/api/presence/quote/1"
                                 && r.options.method === "POST");
  assert.ok(beat, "POST /api/presence/quote/1");
  assert.equal(beat.options.headers.get("X-CSRF-Token"), "token-aus-dem-cookie");
});

test("Andere Anwesende erscheinen als Banner über dem Formular", async () => {
  const { window } = startApp({ others: [{ username: "anna", last_seen: "2026-08-27T10:00:00" }] });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  const banner = window.document.querySelector("#quote-presence");
  assert.equal(banner.hidden, true, "vor dem Öffnen ist nichts zu sehen");

  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  assert.equal(banner.hidden, false);
  assert.match(banner.textContent, /anna/);
  assert.match(banner.textContent, /dieses Angebot/);
});

test("Mehrere Anwesende werden im Plural genannt", async () => {
  const { window } = startApp({
    others: [{ username: "anna", last_seen: "x" }, { username: "bernd", last_seen: "x" }],
  });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  const text = window.document.querySelector("#quote-presence").textContent;
  assert.match(text, /anna, bernd/);
  assert.match(text, /haben/);
});

test("Beim Abbrechen wird abgemeldet und das Banner verschwindet", async () => {
  const { window, requests } = startApp({ others: [{ username: "anna", last_seen: "x" }] });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);
  assert.equal(window.document.querySelector("#quote-presence").hidden, false);

  window.document.querySelector("#quote-cancel-edit").click();
  await settle(50);

  assert.equal(window.document.querySelector("#quote-presence").hidden, true);
  assert.ok(requests.find((r) => r.url === "/api/presence/quote/1"
                              && r.options.method === "DELETE"));
});

test("Ein Ansichtswechsel meldet den Beleg ab", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  window.document.querySelector("#nav-dashboard").click();
  await settle(50);
  assert.ok(requests.find((r) => r.url === "/api/presence/quote/1"
                              && r.options.method === "DELETE"));
});

test("In der Liste markiert ein Symbol Belege, an denen jemand ist", async () => {
  const { window } = startApp({
    presence: [{ doc_type: "quote", doc_id: 2,
                 users: [{ username: "anna", last_seen: "x" }] }],
  });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  const rows = window.document.querySelectorAll("#quotes-body tr");
  assert.equal(rows[0].querySelectorAll(".presence-dot").length, 0);
  const marker = rows[1].querySelector(".presence-dot");
  assert.ok(marker, "Angebot 2 ist markiert");
  assert.match(marker.getAttribute("title"), /anna/);
});

test("Ohne Anwesende bleibt die Liste unmarkiert", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  assert.equal(window.document.querySelectorAll("#quotes-body .presence-dot").length, 0);
});

// -------------------------------- Bester Suchtreffer (NEUE IDEEN)
test("Die Suche wählt den besten Treffer gleich aus", async () => {
  const { window } = startApp();
  await settle();

  const search = window.document.querySelector("#quote-customer-search");
  search.value = "beta";
  fire(search);

  assert.equal(window.document.querySelector("#quote-customer-select").value, "2");
  assert.equal(window.document.querySelector("#quote-form").customer_name.value, "Beta GmbH");
});

test("Der exakte Name schlägt den zufälligen Teiltreffer", async () => {
  const { window } = startApp({
    customers: [
      { id: 7, name: "Alpha Bauunternehmung", address: "", contact_person: "",
        email: "", payment_term_days: 14, skonto_percent: 0, skonto_days: 0, active: true },
      { id: 8, name: "Alpha", address: "", contact_person: "", email: "",
        payment_term_days: 14, skonto_percent: 0, skonto_days: 0, active: true },
    ],
  });
  await settle();

  const search = window.document.querySelector("#customer-search");
  search.value = "alpha";
  fire(search);
  assert.equal(window.document.querySelector("#customer-select").value, "8");
});

test("Ohne Treffer bleibt es bei „neuen Kunden eingeben“", async () => {
  const { window } = startApp();
  await settle();
  const search = window.document.querySelector("#quote-customer-search");
  search.value = "gibtesnicht";
  fire(search);
  assert.equal(window.document.querySelector("#quote-customer-select").value, "");
  assert.equal(window.document.querySelector("#quote-form").customer_name.value, "");
});

test("Eine getroffene Auswahl wird von der Suche nicht überschrieben", async () => {
  const { window } = startApp();
  await settle();
  const select = window.document.querySelector("#quote-customer-select");
  select.value = "1";
  fire(select, "change");

  const search = window.document.querySelector("#quote-customer-search");
  search.value = "a";                  // passt auf beide Kunden
  fire(search);

  assert.equal(select.value, "1");
  assert.equal(window.document.querySelector("#quote-form").customer_name.value, "Alpha AG");
});

// -------------------------------- Hinweise ausblenden (NEUE IDEEN)
test("Der Anwesenheits-Hinweis lässt sich wegklicken", async () => {
  const { window } = startApp({ others: [{ username: "anna", last_seen: "x" }] });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  const banner = window.document.querySelector("#quote-presence");
  assert.equal(banner.hidden, false);

  banner.querySelector(".banner-close").click();
  assert.equal(banner.hidden, true);

  await settle(50);                    // der nächste Heartbeat holt ihn nicht zurück
  assert.equal(banner.hidden, true);
});

test("Der Hinweis auf einen wiederhergestellten Entwurf lässt sich ausblenden, ohne ihn zu verwerfen", async () => {
  const draft = JSON.stringify({
    quote: { saved_at: Date.now(), data: { fields: { customer_name: "Wieder AG" }, items: [] } },
  });
  const { window } = startApp({ storage: { "rechnung.drafts.v1": draft } });
  await settle();

  const banner = window.document.querySelector("#quote-draft-banner");
  assert.equal(banner.hidden, false);

  banner.querySelector(".banner-close").click();
  assert.equal(banner.hidden, true);
  assert.match(window.localStorage.getItem("rechnung.drafts.v1"), /Wieder AG/,
               "ausblenden ist kein verwerfen");
  assert.equal(window.document.querySelector("#quote-form").customer_name.value, "Wieder AG");
});

// -------------------------------- Firmendaten & Logo (NEUE IDEEN)
test("Ein Logo-Upload löscht die eingetippten Firmendaten nicht", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-settings").click();
  await settle();

  const form = window.document.querySelector("#settings-form");
  form.company_name.value = "Neue Firma GmbH";
  form.iban.value = "DE02120300000000202051";

  pickFile(window, "#logo-input", "logo.png", "image/png");
  await settle(50);

  assert.ok(requests.find((r) => r.url === "/api/settings/logo" && r.options.method === "POST"));
  assert.equal(form.company_name.value, "Neue Firma GmbH");
  assert.equal(form.iban.value, "DE02120300000000202051");
  assert.equal(window.document.querySelector("#logo-preview").hidden, false);
});

// -------------------------------- Kunden-Import (NEUE IDEEN)
test("Der Kunden-Import schickt die Datei und meldet das Ergebnis", async () => {
  const { window, requests } = startApp({
    routes: { "/api/customers/import": { created: 2, updated: 1, skipped: 0, errors: [] } },
  });
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  // Die Auswahl allein startet den Import – kein zweiter Klick nötig.
  pickFile(window, "#customer-import-file", "kunden.csv", "text/csv");
  await settle(50);

  const upload = requests.find((r) => r.url === "/api/customers/import");
  assert.ok(upload, "POST /api/customers/import");
  assert.equal(upload.options.method, "POST");
  assert.equal(upload.options.headers.get("X-CSRF-Token"), "token-aus-dem-cookie");

  const msg = window.document.querySelector("#customer-import-msg").textContent;
  assert.match(msg, /2 neu angelegt/);
  assert.match(msg, /1 aktualisiert/);
});

test("Der Import-Knopf öffnet nur den Dateidialog", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  const input = window.document.querySelector("#customer-import-file");
  let opened = 0;
  input.click = () => { opened += 1; };
  window.document.querySelector("#customer-import-btn").click();
  await settle(20);

  assert.equal(opened, 1, "der Knopf öffnet die Dateiauswahl");
  assert.ok(!requests.some((r) => r.url === "/api/customers/import"),
            "ohne Datei wird nichts hochgeladen");
});

test("Auch eine JSON-Datei wird importiert", async () => {
  const { window, requests } = startApp({
    routes: { "/api/customers/import": { created: 1, updated: 0, skipped: 0, errors: [] } },
  });
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  pickFile(window, "#customer-import-file", "kunde-1-export.json", "application/json");
  await settle(50);

  assert.ok(requests.some((r) => r.url === "/api/customers/import"));
  assert.match(window.document.querySelector("#customer-import-msg").textContent,
               /1 neu angelegt/);
});

test("Die Dateiauswahl selbst ist nicht sichtbar", async () => {
  const { window } = startApp();
  await settle();
  assert.equal(window.document.querySelector("#customer-import-file").hidden, true);
});

// -------------------------------- Keine Umwandlung doppelt
const INVOICE_ROW = {
  id: 3, number: "RE-2026-0003", customer_name: "Alpha AG", issue_date: "2026-01-10",
  total: 100, remaining: 0, status: "bezahlt", is_overdue: false, items: [],
};

test("Eine Rechnung mit Lieferschein bietet keine zweite Umwandlung an", async () => {
  const { window } = startApp({
    routes: {
      "/api/invoices": [{ ...INVOICE_ROW, delivery_note_number: "LS-2026-0003" }],
    },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  const row = window.document.querySelector("#history-body tr");
  assert.equal(row.querySelector("button[data-act=to-delivery]"), null,
               "kein Knopf „Lieferschein“ mehr");
  const marker = row.querySelector(".converted-marker");
  assert.ok(marker, "stattdessen der Verweis auf den vorhandenen Beleg");
  assert.match(marker.textContent, /LS-2026-0003/);
});

test("Ohne Lieferschein bleibt der Knopf an der Rechnung", async () => {
  const { window } = startApp({ routes: { "/api/invoices": [INVOICE_ROW] } });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  const row = window.document.querySelector("#history-body tr");
  assert.ok(row.querySelector("button[data-act=to-delivery]"));
  assert.equal(row.querySelector(".converted-marker"), null);
});

test("Ein bereits umgewandelter Lieferschein bietet „zu Angebot“ nicht mehr an", async () => {
  const { window } = startApp({
    routes: {
      "/api/delivery-notes": [
        { id: 5, number: "LS-2026-0005", customer_name: "Alpha AG",
          issue_date: "2026-02-01", status: "abgeschlossen", items: [],
          converted_quote_number: "AN-2026-0005" },
      ],
    },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  const row = window.document.querySelector("#delivery-body tr");
  assert.equal(row.querySelector("button[data-act=to-quote]"), null);
  assert.match(row.querySelector(".converted-marker").textContent, /AN-2026-0005/);
});

test("Ein umgewandeltes Angebot zeigt die Rechnungsnummer statt des Knopfs", async () => {
  const { window } = startApp({
    quotes: [{ id: 1, number: "AN-2026-0001", customer_name: "Alpha AG",
               issue_date: "2026-01-05", total: 500, status: "umgewandelt",
               converted_invoice_number: "RE-2026-0001", items: [] }],
  });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  const row = window.document.querySelector("#quotes-body tr");
  assert.equal(row.querySelector("button[data-act=convert]"), null);
  assert.match(row.querySelector(".converted-marker").textContent, /RE-2026-0001/);
});

test("Weist der Server die zweite Umwandlung ab, sagt die App das", async () => {
  const { window } = startApp({
    routes: { "/api/delivery-notes": [DN_OPEN_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  // Der Knopf ist da (der Server kennt die Dublette, das Frontend hier nicht):
  // die Fehlermeldung des Servers muss beim Benutzer ankommen.
  const messages = [];
  window.alert = (text) => messages.push(text);
  window.fetch = async () => ({
    ok: false, status: 400, headers: new Headers(), clone() { return this; },
    json: async () => ({ detail: "Aus diesem Lieferschein wurde bereits das Angebot AN-2026-0005 erstellt" }),
  });

  window.document.querySelector("#delivery-body button[data-act=to-quote]").click();
  await settle(50);

  assert.equal(messages.length, 1);
  assert.match(messages[0], /AN-2026-0005/);
  assert.equal(window.document.querySelector("#view-quotes").hidden, true,
               "und es wird nicht in die Angebotsansicht gewechselt");
});

// -------------------------------- Beispieldatei: CSV oder JSON
/**
 * Fängt die Downloads eines Fensters ab. jsdom kennt weder
 * URL.createObjectURL noch echte Downloads; der überschriebene Klick auf den
 * Anker verhindert außerdem den Navigationsversuch.
 */
function captureDownloads(window) {
  const downloads = [];
  let lastContent = "";
  const NativeBlob = window.Blob;
  // jsdoms Blob kennt kein text(), also den Inhalt beim Anlegen mitschneiden.
  window.Blob = function (parts, options) {
    lastContent = (parts || []).join("");
    return new NativeBlob(parts, options);
  };
  window.URL.createObjectURL = () => "blob:test";
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () {
    if (this.download) downloads.push({ name: this.download, content: lastContent });
  };
  return downloads;
}

test("Die Beispieldatei fragt erst nach dem Format", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  const menu = window.document.querySelector("#customer-import-example-menu");
  const button = window.document.querySelector("#customer-import-example");
  assert.equal(menu.hidden, true, "das Fenster ist zu, solange niemand fragt");
  assert.equal(button.getAttribute("aria-expanded"), "false");

  button.click();
  assert.equal(menu.hidden, false, "der Knopf öffnet das Auswahlfenster");
  assert.equal(button.getAttribute("aria-expanded"), "true");
  assert.deepEqual([...menu.querySelectorAll("button[data-example]")]
                     .map((b) => b.dataset.example), ["csv", "json"]);
});

test("Beispieldatei als CSV", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();
  const downloads = captureDownloads(window);

  window.document.querySelector("#customer-import-example").click();
  window.document.querySelector("[data-example=csv]").click();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].name, "kunden-vorlage.csv");
  const text = downloads[0].content;
  assert.match(text.split("\r\n")[0], /^\ufeff?Name;E-Mail;/);
  assert.match(text, /Muster GmbH;info@muster\.example/);
  assert.equal(window.document.querySelector("#customer-import-example-menu").hidden, true,
               "nach der Auswahl schließt sich das Fenster");
});

test("Beispieldatei als JSON – im Format, das der Import versteht", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();
  const downloads = captureDownloads(window);

  window.document.querySelector("#customer-import-example").click();
  window.document.querySelector("[data-example=json]").click();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].name, "kunden-vorlage.json");
  const data = JSON.parse(downloads[0].content);
  assert.ok(Array.isArray(data.customers), "{\"customers\": [...]}");
  assert.equal(data.customers[0].name, "Muster GmbH");
  assert.equal(data.customers[0].payment_term_days, 30);
  assert.equal(window.document.querySelector("#customer-import-example-menu").hidden, true);
});

test("Ein Klick daneben schließt das Auswahlfenster", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  window.document.querySelector("#customer-import-example").click();
  assert.equal(window.document.querySelector("#customer-import-example-menu").hidden, false);
  window.document.body.click();
  assert.equal(window.document.querySelector("#customer-import-example-menu").hidden, true);
});

// -------------------------------- Lieferschein: PDF schließt ab
const DN_OPEN_ROW = {
  id: 5, number: "LS-2026-0005", customer_name: "Alpha AG",
  issue_date: "2026-02-01", status: "offen", items: [],
};

test("Der PDF-Download lädt die Lieferscheinliste neu", async () => {
  const { window, requests } = startApp({
    routes: { "/api/delivery-notes": [DN_OPEN_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();
  // Der Anker ist ein echter Download – jsdom soll nicht zu navigieren versuchen.
  window.document.addEventListener("click", (e) => e.preventDefault(), true);

  const before = requests.filter((r) => r.url.split("?")[0] === "/api/delivery-notes").length;
  const link = window.document.querySelector("#delivery-body a[data-act=pdf]");
  assert.ok(link, "PDF-Link in der Lieferscheinliste");
  assert.equal(link.getAttribute("href"), "/api/delivery-notes/5/pdf");
  link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  await settle(900);

  const after = requests.filter((r) => r.url.split("?")[0] === "/api/delivery-notes").length;
  assert.ok(after > before, "die Liste wird nach dem Download neu geholt");
});

test("Ein abgeschlossener Lieferschein steht grün in der Liste", async () => {
  const { window } = startApp({
    routes: {
      "/api/delivery-notes": [{ ...DN_OPEN_ROW, status: "abgeschlossen" }],
    },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  const badge = window.document.querySelector("#delivery-body .badge");
  assert.equal(badge.textContent, "abgeschlossen");
  // "bezahlt" ist die grüne Plakette – dieselbe wie bei einer bezahlten Rechnung.
  assert.ok(badge.classList.contains("bezahlt"), "grüne Plakette");

  const acts = [...window.document.querySelectorAll("#delivery-body button[data-act]")]
    .map((b) => b.dataset.act);
  assert.ok(acts.includes("reopen"), "lässt sich wieder öffnen");
  assert.ok(acts.includes("cancel"), "lässt sich weiterhin stornieren");
  assert.ok(acts.includes("edit"));
  assert.ok(window.document.querySelector("#delivery-filter-status option[value=abgeschlossen]"),
            "der Statusfilter kennt „abgeschlossen“");
});

test("Wieder öffnen setzt den Lieferschein zurück auf offen", async () => {
  const { window, requests } = startApp({
    routes: { "/api/delivery-notes": [{ ...DN_OPEN_ROW, status: "abgeschlossen" }] },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  window.document.querySelector("#delivery-body button[data-act=reopen]").click();
  await settle(50);

  const call = requests.find((r) => r.url === "/api/delivery-notes/5/status");
  assert.ok(call, "PATCH /api/delivery-notes/5/status");
  assert.equal(JSON.parse(call.options.body).status, "offen");
});

// -------------------------------- Rechnung speichern -> Rechnungsübersicht
test("Nach dem Speichern einer Rechnung landet man in der Rechnungsübersicht", async () => {
  const { window } = startApp({
    routes: {
      "/api/invoices": (o) => (o.method === "POST" ? { id: 7, number: "RE-2026-0007" } : []),
    },
  });
  await settle();

  const form = window.document.querySelector("#invoice-form");
  form.customer_name.value = "Alpha AG";
  const desc = window.document.querySelector("#items-body .i-desc");
  desc.value = "Beratung";
  fire(desc);
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(100);

  assert.equal(window.document.querySelector("#view-history").hidden, false,
               "die Rechnungsübersicht ist offen");
  assert.equal(window.document.querySelector("#view-new").hidden, true);
  const notice = window.document.querySelector("#history-msg");
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /RE-2026-0007/);
});

test("Ein Wechsel in eine andere Ansicht räumt die Meldung weg", async () => {
  const { window } = startApp({
    routes: {
      "/api/invoices": (o) => (o.method === "POST" ? { id: 7, number: "RE-2026-0007" } : []),
    },
  });
  await settle();

  const form = window.document.querySelector("#invoice-form");
  form.customer_name.value = "Alpha AG";
  const desc = window.document.querySelector("#items-body .i-desc");
  desc.value = "Beratung";
  fire(desc);
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(100);

  window.document.querySelector("#nav-history").click();
  await settle(50);
  assert.equal(window.document.querySelector("#history-msg").hidden, true);
});

// -------------------------------- Angebot -> Rechnung
test("Nach dem Umwandeln eines Angebots steht man in der Rechnungsübersicht", async () => {
  const { window, requests } = startApp({
    routes: { "/api/quotes/1/convert": { id: 11, number: "RE-2026-0011" } },
  });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  const row = window.document.querySelector("#quotes-body tr");
  const button = row.querySelector("button[data-act=convert]");
  assert.ok(button, "Knopf „zu Rechnung“ in der Angebotsliste");
  button.click();
  await settle(50);

  const call = requests.find((r) => r.url === "/api/quotes/1/convert");
  assert.ok(call, "POST /api/quotes/1/convert");
  assert.equal(call.options.method, "POST");
  assert.equal(window.document.querySelector("#view-history").hidden, false,
               "danach steht man in der Rechnungsübersicht");
  assert.equal(window.document.querySelector("#view-quotes").hidden, true);
  assert.ok(requests.some((r) => r.url.split("?")[0] === "/api/invoices"),
            "die Rechnungsübersicht wird dabei frisch geladen");
});

// -------------------------------- Lieferschein -> Angebot
test("Ein Lieferschein lässt sich in ein Angebot umwandeln", async () => {
  const { window, requests } = startApp({
    routes: {
      "/api/delivery-notes": [
        { id: 5, number: "LS-2026-0005", customer_name: "Alpha AG",
          issue_date: "2026-02-01", status: "offen", items: [] },
      ],
      "/api/delivery-notes/5/convert-to-quote": { id: 9, number: "AN-2026-0005" },
    },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  const row = window.document.querySelector("#delivery-body tr");
  const button = row.querySelector("button[data-act=to-quote]");
  assert.ok(button, "Knopf „zu Angebot“ in der Lieferscheinliste");
  button.click();
  await settle(50);

  const call = requests.find((r) => r.url === "/api/delivery-notes/5/convert-to-quote");
  assert.ok(call, "POST /api/delivery-notes/5/convert-to-quote");
  assert.equal(call.options.method, "POST");
  assert.equal(window.document.querySelector("#view-quotes").hidden, false,
               "danach steht man in der Angebotsansicht");
});

test("Ein stornierter Lieferschein bietet keine Umwandlung an", async () => {
  const { window } = startApp({
    routes: {
      "/api/delivery-notes": [
        { id: 6, number: "LS-2026-0006", customer_name: "Beta GmbH",
          issue_date: "2026-02-02", status: "storniert", items: [] },
      ],
    },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  assert.equal(window.document.querySelectorAll("#delivery-body button[data-act=to-quote]").length, 0);
});

// -------------------------------- Leere Banner sind wirklich weg
// Regression: die Banner setzen `display: flex`, und Autoren-CSS schlägt die
// Browser-Vorgabe `[hidden] { display: none }`. Ohne die !important-Regel in
// styles.css standen die leeren Kästen dauerhaft über den Formularen –
// im DOM "hidden", auf dem Bildschirm sichtbar. Deshalb wird hier mit der
// echten styles.css gerechnet statt nur das Attribut zu prüfen.
test("Leere Hinweisbanner werden nicht angezeigt", () => {
  const css = readFileSync(new URL("../../app/static/styles.css", import.meta.url), "utf8");
  const dom = new JSDOM(HTML.replace("</head>", `<style>${css}</style></head>`),
                        { pretendToBeVisual: true });
  openWindows.push(dom.window);

  for (const sel of ["#lock-banner", "#invoice-presence", "#quote-presence",
                     "#delivery-presence", "#invoice-draft-banner",
                     "#quote-draft-banner", "#delivery-draft-banner",
                     "#customer-import-file", "#customer-import-example-menu",
                     "#history-msg"]) {
    const el = dom.window.document.querySelector(sel);
    assert.equal(el.hidden, true, `${sel}: hidden-Attribut`);
    assert.equal(dom.window.getComputedStyle(el).display, "none",
                 `${sel}: darf nicht als leerer Kasten stehen bleiben`);
  }
});
