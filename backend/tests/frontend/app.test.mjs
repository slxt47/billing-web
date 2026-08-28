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
    return response(path in table ? table[path] : { ok: true });
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

  pickFile(window, "#customer-import-file", "kunden.csv", "text/csv");
  window.document.querySelector("#customer-import-btn").click();
  await settle(50);

  const upload = requests.find((r) => r.url === "/api/customers/import");
  assert.ok(upload, "POST /api/customers/import");
  assert.equal(upload.options.method, "POST");
  assert.equal(upload.options.headers.get("X-CSRF-Token"), "token-aus-dem-cookie");

  const msg = window.document.querySelector("#customer-import-msg").textContent;
  assert.match(msg, /2 neu angelegt/);
  assert.match(msg, /1 aktualisiert/);
});

test("Ohne ausgewählte Datei wird nichts hochgeladen", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();

  window.document.querySelector("#customer-import-btn").click();
  await settle(20);

  assert.ok(!requests.some((r) => r.url === "/api/customers/import"));
  assert.match(window.document.querySelector("#customer-import-msg").textContent,
               /CSV-Datei auswählen/);
});
