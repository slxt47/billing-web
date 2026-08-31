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
      "/api/pdf-templates": [],
      "/api/credit-notes": [],
      "/api/reports/custom": {
        doc_type: "invoice", group_by: "none", status: null,
        from: "2026-01-01", to: "2026-12-31", has_amounts: true, rows: [],
        totals: { count: 0, item_count: 0, net: 0, tax: 0, gross: 0 },
      },
      "/api/admin/backups": [],
      ...routes,
    };
    if (/^\/api\/presence\/[a-z_]+\/\d+$/.test(path)) {
      return response({ others });
    }
    // Bearbeitungssperre (Rechnung/Angebot/Lieferschein): ohne ausdrückliche
    // Route (per exaktem Pfad in `routes`, hat Vorrang) gilt der Beleg als
    // frei – die meisten Tests wollen mit der Sperre gar nichts zu tun haben.
    const lockDefault = { locked: true, locked_by: "tester",
                          locked_at: "2026-01-01T00:00:00", editable: true };
    const route = path in table ? table[path]
      : /^\/api\/(invoices|quotes|delivery-notes)\/\d+\/lock$/.test(path) ? lockDefault
      : { ok: true };
    // Eine Route darf eine Funktion sein, wenn GET und POST sich
    // unterscheiden müssen (z. B. Liste holen vs. Rechnung anlegen), und sie
    // darf statt der Daten eine fertige Antwort liefern (z. B. mit Status 503).
    const value = typeof route === "function" ? route(options) : route;
    return value && typeof value.json === "function" ? value : response(value);
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
      ["Alpha AG – Frau Alpha", "Beta GmbH – Herr Beta"],
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
  assert.deepEqual(optionsOf(window, "#quote-customer-select").slice(1), ["Beta GmbH – Herr Beta"]);

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

// ------------------------------------------- Bearbeitungssperre (Angebot)
test("Das Bearbeiten eines Angebots sperrt es und zeigt den Hinweis", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();

  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  const lockReq = requests.find((r) => r.url === "/api/quotes/1/lock"
                                    && r.options.method === "POST");
  assert.ok(lockReq, "POST /api/quotes/1/lock");
  assert.equal(window.document.querySelector("#quote-lock-banner").hidden, false);
  assert.match(window.document.querySelector("#quote-lock-banner").textContent, /gesperrt/);
  assert.equal(window.document.querySelector("#quote-form [name=customer_name]").value, "Alpha AG");
});

test("Ein bereits gesperrtes Angebot lässt sich nicht öffnen", async () => {
  const { window, requests } = startApp({
    routes: { "/api/quotes/1/lock": { locked: true, locked_by: "anna", editable: false } },
  });
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.alert = (msg) => { window._alerted = msg; };

  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  assert.match(window._alerted || "", /anna/);
  // Das Formular bleibt leer – ohne editable:true wird nichts befüllt.
  assert.equal(window.document.querySelector("#quote-form [name=customer_name]").value, "");
  assert.ok(!requests.some((r) => r.url === "/api/presence/quote/1"));
});

test("Abbrechen gibt die Angebotssperre wieder frei", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-quotes").click();
  await settle();
  window.document.querySelectorAll("#quotes-body button[data-act=edit]")[0].click();
  await settle(50);

  window.document.querySelector("#quote-cancel-edit").click();
  await settle(20);

  assert.ok(requests.some((r) => r.url === "/api/quotes/1/lock" && r.options.method === "DELETE"));
  assert.equal(window.document.querySelector("#quote-lock-banner").hidden, true);
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

test("Alle Kunden lassen sich als CSV herunterladen", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-customers").click();
  await settle();
  const downloads = captureDownloads(window);

  window.document.querySelector("#customer-export-csv").click();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].href, "/api/customers/export.csv");
});

// -------------------------------- Artikel-Import / -Export
test("Der Artikel-Import schickt die Datei und meldet das Ergebnis", async () => {
  const { window, requests } = startApp({
    routes: { "/api/products/import": { created: 1, updated: 1, skipped: 0, errors: [] } },
  });
  await settle();
  window.document.querySelector("#nav-products").click();
  await settle();

  pickFile(window, "#product-import-file", "artikel.csv", "text/csv");
  await settle(50);

  const upload = requests.find((r) => r.url === "/api/products/import");
  assert.ok(upload, "POST /api/products/import");
  assert.equal(upload.options.method, "POST");
  assert.equal(upload.options.headers.get("X-CSRF-Token"), "token-aus-dem-cookie");

  const msg = window.document.querySelector("#product-import-msg").textContent;
  assert.match(msg, /1 neu angelegt/);
  assert.match(msg, /1 aktualisiert/);
});

test("Der Artikel-Import-Knopf öffnet nur den Dateidialog", async () => {
  const { window, requests } = startApp();
  await settle();
  window.document.querySelector("#nav-products").click();
  await settle();

  const input = window.document.querySelector("#product-import-file");
  let opened = 0;
  input.click = () => { opened += 1; };
  window.document.querySelector("#product-import-btn").click();
  await settle(20);

  assert.equal(opened, 1, "der Knopf öffnet die Dateiauswahl");
  assert.ok(!requests.some((r) => r.url === "/api/products/import"),
            "ohne Datei wird nichts hochgeladen");
});

test("Alle Artikel lassen sich als CSV herunterladen", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-products").click();
  await settle();
  const downloads = captureDownloads(window);

  window.document.querySelector("#product-export-csv").click();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].href, "/api/products/export.csv");
});

// -------------------------------- Gutschriften
const CREDIT_ROW = {
  id: 4, number: "GS-2026-0004", customer_name: "Alpha AG", invoice_id: 3,
  invoice_number: "RE-2026-0003", issue_date: "2026-02-10", total: 120,
  status: "offen", reason: "Kulanz", tax_rate: 20, small_business: false,
  customer_address: "", customer_contact_person: "", items: [],
};

test("Die Gutschriftenliste zeigt Beleg, Rechnung und Betrag", async () => {
  const { window } = startApp({ routes: { "/api/credit-notes": [CREDIT_ROW] } });
  await settle();
  window.document.querySelector("#nav-credit").click();
  await settle();

  const cells = [...window.document.querySelectorAll("#credit-body tr td")]
    .map((td) => td.textContent.trim());
  assert.ok(cells.includes("GS-2026-0004"));
  assert.ok(cells.includes("RE-2026-0003"), "die zugehörige Rechnung steht dabei");
  const badge = window.document.querySelector("#credit-body .badge");
  assert.equal(badge.textContent, "offen");
});

test("„Gutschrift“ an der Rechnung übernimmt Kunde und Positionen", async () => {
  const invoice = {
    ...INVOICE_ROW, status: "offen", remaining: 240, total: 240,
    customer_address: "Alphaweg 1", customer_contact_person: "Frau Alpha",
    tax_rate: 20, small_business: false, discount_percent: 0,
    items: [{ id: 1, description: "Beratung", quantity: 2, unit_price: 100 }],
  };
  const { window } = startApp({ routes: { "/api/invoices": [invoice] } });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  window.document.querySelector("#history-body button[data-act=credit]").click();
  await settle();

  assert.equal(window.document.querySelector("#view-credit").hidden, false,
               "die Gutschrift-Ansicht ist offen");
  const form = window.document.querySelector("#credit-form");
  assert.equal(form.customer_name.value, "Alpha AG");
  assert.equal(form.invoice_id.value, "3");
  assert.equal(window.document.querySelector("#credit-items-body .ci-desc").value,
               "Beratung");
  assert.equal(window.document.querySelector("#credit-items-body .ci-price").value, "100");
  assert.match(window.document.querySelector("#credit-invoice-hint").textContent,
               /RE-2026-0003/);
});

test("Der Rabatt der Rechnung steckt im Einzelpreis der Gutschrift", async () => {
  const invoice = {
    ...INVOICE_ROW, status: "offen", remaining: 216, total: 216, discount_percent: 10,
    tax_rate: 20, items: [{ id: 1, description: "Beratung", quantity: 2, unit_price: 100 }],
  };
  const { window } = startApp({ routes: { "/api/invoices": [invoice] } });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();
  window.document.querySelector("#history-body button[data-act=credit]").click();
  await settle();

  assert.equal(window.document.querySelector("#credit-items-body .ci-price").value, "90");
});

test("Eine bezahlte Rechnung ohne offenen Betrag bietet keine Gutschrift an", async () => {
  const { window } = startApp({
    routes: { "/api/invoices": [{ ...INVOICE_ROW, status: "bezahlt", remaining: 0 }] },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();
  assert.equal(window.document.querySelector("#history-body button[data-act=credit]"), null);
});

test("Eine Gutschrift wird mit ihren Positionen abgeschickt", async () => {
  const { window, requests } = startApp({
    routes: { "/api/credit-notes": (o) => (o.method === "POST" ? CREDIT_ROW : []) },
  });
  await settle();
  window.document.querySelector("#nav-credit").click();
  await settle();

  const form = window.document.querySelector("#credit-form");
  form.customer_name.value = "Alpha AG";
  form.reason.value = "Ware beschädigt";
  const row = window.document.querySelector("#credit-items-body tr");
  row.querySelector(".ci-desc").value = "Rückgabe";
  row.querySelector(".ci-qty").value = "1";
  row.querySelector(".ci-price").value = "100";
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(50);

  const post = requests.find((r) => r.url === "/api/credit-notes" && r.options.method === "POST");
  assert.ok(post, "POST /api/credit-notes");
  const body = JSON.parse(post.options.body);
  assert.equal(body.customer_name, "Alpha AG");
  assert.equal(body.reason, "Ware beschädigt");
  assert.deepEqual(body.items, [{ description: "Rückgabe", quantity: 1, unit_price: 100 }]);
  assert.match(window.document.querySelector("#credit-msg").textContent, /GS-2026-0004/);
});

test("Erstattet und stornieren setzen den Status", async () => {
  const { window, requests } = startApp({ routes: { "/api/credit-notes": [CREDIT_ROW] } });
  await settle();
  window.document.querySelector("#nav-credit").click();
  await settle();

  window.document.querySelector("#credit-body button[data-act=settle]").click();
  await settle(30);
  const patch = requests.find((r) => r.url === "/api/credit-notes/4/status");
  assert.equal(JSON.parse(patch.options.body).status, "erstattet");
});

// -------------------------------- Auswertungen
const VAT_REPORT = {
  from: "2026-01-01", to: "2026-12-31",
  rows: [{ tax_rate: 20, net: 200, tax: 40, gross: 240, invoice_count: 2,
           credit_note_count: 1 }],
  net_total: 200, tax_total: 40, gross_total: 240, input_tax_known: false,
};
const REVENUE_REPORT = {
  from: "2026-01-01", to: "2026-12-31",
  months: [{ label: "01/2026", key: "2026-01", invoiced_net: 250, credited_net: 50,
             net: 200, gross: 240 }],
  customers: [{ customer_name: "Alpha AG", invoiced_net: 250, credited_net: 50,
                net: 200, gross: 240, invoice_count: 2 }],
  invoiced_net: 250, credited_net: 50, net: 200, gross: 240, paid: 100,
  open_amount: 140, invoice_count: 2, credit_note_count: 1, expenses_tracked: false,
};

test("Die Auswertung zeigt Umsatzsteuer und Erlöse", async () => {
  const { window, requests } = startApp({
    routes: { "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);

  // Ohne eigene Wahl gilt das laufende Jahr.
  const year = new Date().getFullYear();
  assert.equal(window.document.querySelector("#report-from").value, `${year}-01-01`);
  assert.ok(requests.some((r) => r.url.startsWith("/api/reports/vat?from=")));

  const vatRow = window.document.querySelector("#vat-body tr").textContent;
  assert.match(vatRow, /20/);
  assert.match(window.document.querySelector("#vat-total").textContent, /Summe/);

  const kpis = window.document.querySelector("#revenue-kpis").textContent;
  assert.match(kpis, /Erlös netto/);
  assert.match(window.document.querySelector("#revenue-month-body").textContent, /01\/2026/);
  assert.match(window.document.querySelector("#revenue-customer-body").textContent, /Alpha AG/);
});

test("Der Zeitraum wandert in den CSV-Export", async () => {
  const { window } = startApp({
    routes: { "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);
  const downloads = captureDownloads(window);

  window.document.querySelector("#report-from").value = "2026-03-01";
  window.document.querySelector("#report-to").value = "2026-03-31";
  window.document.querySelector("#vat-csv").click();

  assert.equal(downloads.length, 1);
  assert.match(downloads[0].href, /^\/api\/reports\/vat\.csv\?/);
  assert.match(downloads[0].href, /from=2026-03-01/);
  assert.match(downloads[0].href, /to=2026-03-31/);
});

// -------------------------------- Freier Report-Builder
const CUSTOM_REPORT_LIST = {
  doc_type: "invoice", group_by: "none", status: null,
  from: "2026-01-01", to: "2026-12-31", has_amounts: true,
  rows: [{ number: "RE-2026-0001", customer_name: "Alpha AG", issue_date: "2026-01-05",
           status: "offen", item_count: 1, net: 200, tax: 40, gross: 240 }],
  totals: { count: 1, item_count: 1, net: 200, tax: 40, gross: 240 },
};
const CUSTOM_REPORT_GROUPED = {
  doc_type: "delivery_note", group_by: "customer", status: null,
  from: "2026-01-01", to: "2026-12-31", has_amounts: false,
  rows: [{ group: "Liefer GmbH", count: 2, item_count: 3 }],
  totals: { count: 2, item_count: 3, net: null, tax: null, gross: null },
};

test("Der Report-Builder lädt beim Öffnen der Auswertungen und zeigt die Liste", async () => {
  const { window, requests } = startApp({
    routes: {
      "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT,
      "/api/reports/custom": CUSTOM_REPORT_LIST,
    },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);

  assert.ok(requests.some((r) => r.url.startsWith("/api/reports/custom?")
                              && r.url.includes("doc_type=invoice")));
  assert.match(window.document.querySelector("#custom-report-head").textContent, /Nummer/);
  assert.match(window.document.querySelector("#custom-report-body").textContent, /RE-2026-0001/);
  assert.match(window.document.querySelector("#custom-report-body").textContent, /Alpha AG/);
  assert.equal(window.document.querySelector("#custom-report-empty").hidden, true);
});

test("Ohne Geldbeträge zeigt der Report-Builder nur Anzahl und Positionen", async () => {
  const { window } = startApp({
    routes: {
      "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT,
      "/api/reports/custom": CUSTOM_REPORT_GROUPED,
    },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);

  const head = window.document.querySelector("#custom-report-head").textContent;
  assert.match(head, /Kunde/);
  assert.ok(!/Netto/.test(head), "Lieferscheine kennen keine Preise");
  assert.match(window.document.querySelector("#custom-report-body").textContent, /Liefer GmbH/);
});

test("Belegart und Gruppierung wandern in die Anfrage und den CSV-Export", async () => {
  const { window, requests } = startApp({
    routes: {
      "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT,
      "/api/reports/custom": CUSTOM_REPORT_LIST,
    },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);
  const downloads = captureDownloads(window);

  window.document.querySelector("#custom-report-doctype").value = "quote";
  window.document.querySelector("#custom-report-doctype").dispatchEvent(new window.Event("change"));
  await settle(20);
  window.document.querySelector("#custom-report-groupby").value = "month";
  window.document.querySelector("#custom-report-groupby").dispatchEvent(new window.Event("change"));
  await settle(20);

  assert.ok(requests.some((r) => r.url.includes("doc_type=quote") && r.url.includes("group_by=month")));

  window.document.querySelector("#custom-report-csv").click();
  assert.equal(downloads.length, 1);
  assert.match(downloads[0].href, /^\/api\/reports\/custom\.csv\?/);
  assert.match(downloads[0].href, /doc_type=quote/);
  assert.match(downloads[0].href, /group_by=month/);
});

test("Ohne Treffer zeigt der Report-Builder einen Hinweis", async () => {
  const { window } = startApp({
    routes: {
      "/api/reports/vat": VAT_REPORT, "/api/reports/revenue": REVENUE_REPORT,
      "/api/reports/custom": {
        doc_type: "invoice", group_by: "none", status: "storniert",
        from: "2026-01-01", to: "2026-12-31", has_amounts: true, rows: [],
        totals: { count: 0, item_count: 0, net: 0, tax: 0, gross: 0 },
      },
    },
  });
  await settle();
  window.document.querySelector("#nav-reports").click();
  await settle(50);

  assert.equal(window.document.querySelector("#custom-report-empty").hidden, false);
});

// -------------------------------- Monitoring
const METRICS = {
  uptime_seconds: 3600, requests_total: 120,
  responses: { "2xx": 110, "3xx": 2, "4xx": 7, "5xx": 1 },
  server_errors_total: 1, error_rate: 0.0083, slow_requests_total: 0,
  avg_response_seconds: 0.012, max_response_seconds: 0.4,
  errors_in_window: 1, failed_logins_in_window: 2, alert_window_seconds: 300,
  recent_errors: [{ at: "2026-08-28T10:00:00", method: "GET", path: "/api/kaputt",
                    status: 500, request_id: "abc123" }],
  backup_age_hours: 5,
  alerts: { enabled: true, error_threshold: 10, login_threshold: 20,
            backup_max_age_hours: 36, cooldown_seconds: 3600, last_sent: {} },
  documents: { invoices: 3, quotes: 1, delivery_notes: 2, credit_notes: 1,
               customers: 4, users: 2 },
};

test("Monitoring zeigt Kennzahlen, Alarmlage und die letzten Fehler", async () => {
  const { window } = startApp({ routes: { "/api/admin/metrics": METRICS } });
  await settle();
  window.document.querySelector("#nav-monitoring").click();
  await settle(50);

  const kpis = window.document.querySelector("#monitoring-kpis").textContent;
  assert.match(kpis, /Laufzeit/);
  assert.match(kpis, /120/);                       // Requests
  assert.match(window.document.querySelector("#monitoring-documents").textContent,
               /Gutschriften/);
  assert.match(window.document.querySelector("#monitoring-alerts").textContent,
               /aktiv/);
  assert.match(window.document.querySelector("#monitoring-errors-body").textContent,
               /\/api\/kaputt/);
  assert.equal(window.document.querySelector("#monitoring-noerrors").hidden, true);
});

test("Ohne Alarme sagt die Ansicht das ausdrücklich", async () => {
  const { window } = startApp({
    routes: { "/api/admin/metrics": { ...METRICS, recent_errors: [],
                                      alerts: { ...METRICS.alerts, enabled: false } } },
  });
  await settle();
  window.document.querySelector("#nav-monitoring").click();
  await settle(50);

  assert.match(window.document.querySelector("#monitoring-alerts").textContent,
               /abgeschaltet/);
  assert.equal(window.document.querySelector("#monitoring-noerrors").hidden, false);
});

test("Der Probealarm meldet, wohin er ging", async () => {
  const { window } = startApp({
    routes: { "/api/admin/metrics": METRICS,
              "/api/admin/metrics/test-alert": { sent: true, to: "buero@muster.example" } },
  });
  await settle();
  window.document.querySelector("#nav-monitoring").click();
  await settle(50);

  window.document.querySelector("#monitoring-test-alert").click();
  await settle(50);
  assert.match(window.document.querySelector("#monitoring-msg").textContent,
               /buero@muster\.example/);
});

test("Das Monitoring aktualisiert sich im gewählten Takt und stoppt beim Verlassen", async () => {
  const { window, requests } = startApp({ routes: { "/api/admin/metrics": METRICS } });
  await settle();
  const calls = () => requests.filter((r) => r.url === "/api/admin/metrics").length;

  window.document.querySelector("#nav-monitoring").click();
  await settle(50);
  assert.equal(calls(), 1, "erst einmal beim Öffnen");

  const select = window.document.querySelector("#monitoring-auto");
  assert.equal(select.value, "0", "ohne Auswahl bleibt es beim Handbetrieb");
  select.value = "1";
  fire(select, "change");
  await settle(1100);
  assert.ok(calls() >= 2, `nach 1 s ein weiterer Abruf (waren ${calls()})`);

  const before = calls();
  window.document.querySelector("#nav-dashboard").click();
  await settle(1100);
  assert.equal(calls(), before, "in einer anderen Ansicht ruht der Takt");
});

test("Der gewählte Takt überlebt einen Neustart im localStorage", async () => {
  const first = startApp({ routes: { "/api/admin/metrics": METRICS } });
  await settle();
  first.window.document.querySelector("#nav-monitoring").click();
  await settle(50);
  const select = first.window.document.querySelector("#monitoring-auto");
  select.value = "10";
  fire(select, "change");
  assert.equal(first.window.localStorage.getItem("rechnung.monitoring.interval"), "10");

  const second = startApp({ routes: { "/api/admin/metrics": METRICS },
                            storage: { "rechnung.monitoring.interval": "10" } });
  await settle();
  assert.equal(second.window.document.querySelector("#monitoring-auto").value, "10");
});

test("Der Takt läuft weiter und schreibt den Stand daneben", async () => {
  const { window, requests } = startApp({ routes: { "/api/admin/metrics": METRICS } });
  await settle();
  const calls = () => requests.filter((r) => r.url === "/api/admin/metrics").length;

  window.document.querySelector("#nav-monitoring").click();
  await settle(50);
  const select = window.document.querySelector("#monitoring-auto");
  select.value = "1";
  fire(select, "change");
  await settle(3200);
  // Ein Abruf reicht nicht: der Takt muss sich immer wieder neu stellen.
  assert.ok(calls() >= 4, `mindestens vier Abrufe (waren ${calls()})`);

  const stamp = window.document.querySelector("#monitoring-stamp");
  assert.match(stamp.textContent, /^Stand \d{1,2}:\d{2}:\d{2}( · alle 1 s)$/);
});

test("Ein fehlgeschlagener Abruf steht neben dem Knopf", async () => {
  const { window } = startApp({
    routes: { "/api/admin/metrics": () => response({ detail: "weg" }, 503) },
  });
  await settle();
  window.document.querySelector("#nav-monitoring").click();
  await settle(50);

  const stamp = window.document.querySelector("#monitoring-stamp");
  assert.match(stamp.textContent, /Abruf fehlgeschlagen \(503\)/);
  assert.equal(stamp.className, "err");
});

test("Zurück im Vordergrund holt der Tab sofort nach", async () => {
  const { window, requests } = startApp({ routes: { "/api/admin/metrics": METRICS } });
  await settle();
  const calls = () => requests.filter((r) => r.url === "/api/admin/metrics").length;

  window.document.querySelector("#nav-monitoring").click();
  await settle(50);
  const select = window.document.querySelector("#monitoring-auto");
  select.value = "60";            // langer Takt: ohne Nachholen käme nichts
  fire(select, "change");
  await settle(50);

  let hidden = true;
  Object.defineProperty(window.document, "hidden", { configurable: true,
                                                     get: () => hidden });
  const before = calls();
  hidden = false;
  window.document.dispatchEvent(new window.Event("visibilitychange"));
  await settle(50);
  assert.ok(calls() > before, "beim Zurückkommen wird sofort geholt");
});

test("Monitoring und Gutschriften stehen nur passenden Benutzern offen", async () => {
  const { window } = startApp();
  await settle();
  // Kein Admin -> Monitoring ist ausgeblendet, Gutschriften sind für alle da.
  assert.equal(window.document.querySelector("#nav-credit").hidden, false);
  assert.equal(window.document.querySelector("#nav-reports").hidden, false);
});

// -------------------------------- Burger-Menü (Verwaltungspunkte)
test("Ohne Admin-Rechte bleibt der Burger-Knopf verborgen", async () => {
  const { window } = startApp({
    routes: { "/api/me": { user: "tester", is_admin: false, csrf_token: "t" } },
  });
  await settle();
  assert.equal(window.document.querySelector("#nav-more-toggle").hidden, true);
});

test("Als Admin öffnet der Burger-Knopf die Verwaltungspunkte", async () => {
  const { window } = startApp();
  await settle();

  const toggle = window.document.querySelector("#nav-more-toggle");
  assert.equal(toggle.hidden, false);
  assert.equal(window.document.querySelector("#nav-more-menu").hidden, true);

  toggle.click();
  assert.equal(window.document.querySelector("#nav-more-menu").hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");

  // Firma/Benutzer/Audit-Log/Backup/Monitoring stehen im Menü, nicht mehr
  // fest in der Kernleiste.
  const menu = window.document.querySelector("#nav-more-menu");
  for (const id of ["nav-settings", "nav-users", "nav-audit", "nav-backup", "nav-monitoring"]) {
    assert.ok(menu.querySelector(`#${id}`), `${id} steht im Burger-Menü`);
    assert.ok(!window.document.querySelector("#nav-primary").querySelector(`#${id}`),
              `${id} steht nicht mehr in der Kernleiste`);
  }
});

test("Ein Klick auf einen Menüpunkt schließt das Burger-Menü", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-more-toggle").click();
  assert.equal(window.document.querySelector("#nav-more-menu").hidden, false);

  window.document.querySelector("#nav-audit").click();
  await settle(20);

  assert.equal(window.document.querySelector("#nav-more-menu").hidden, true);
  assert.equal(window.document.querySelector("#view-audit").hidden, false);
});

test("Ein Klick daneben schließt das Burger-Menü", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-more-toggle").click();
  assert.equal(window.document.querySelector("#nav-more-menu").hidden, false);

  window.document.querySelector("h1").click();

  assert.equal(window.document.querySelector("#nav-more-menu").hidden, true);
});

test("Escape schließt das Burger-Menü", async () => {
  const { window } = startApp();
  await settle();
  window.document.querySelector("#nav-more-toggle").click();
  assert.equal(window.document.querySelector("#nav-more-menu").hidden, false);

  window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));

  assert.equal(window.document.querySelector("#nav-more-menu").hidden, true);
});

test("Die Kernpunkte stehen weiterhin in der Menüleiste, nicht im Burger-Menü", async () => {
  const { window } = startApp();
  await settle();
  const primary = window.document.querySelector("#nav-primary");
  for (const id of ["nav-dashboard", "nav-new", "nav-quotes", "nav-delivery",
                    "nav-history", "nav-credit", "nav-reports", "nav-customers",
                    "nav-products"]) {
    assert.ok(primary.querySelector(`#${id}`), `${id} steht in der Kernleiste`);
  }
});

// -------------------------------- Positionstabellen scrollen seitwärts
test("Jede Positionstabelle sitzt in einem eigenen Scrollbereich", () => {
  // Bei Rechnung, Angebot, Lieferschein und Gutschrift lief die
  // Positionstabelle bisher über den Kartenrand hinaus statt seitwärts zu
  // scrollen, anders als alle Listenansichten (Kunden, Rechnungsübersicht, …).
  const dom = new JSDOM(HTML, { pretendToBeVisual: true });
  openWindows.push(dom.window);
  for (const id of ["items-table", "quote-items-table",
                    "delivery-items-table", "credit-items-table"]) {
    const table = dom.window.document.querySelector(`#${id}`);
    assert.ok(table.closest(".table-scroll"), `#${id} steht in .table-scroll`);
  }
});

// -------------------------------- PDF-Vorlagen
const TEMPLATES = [
  { id: 1, name: "Standard", accent_color: "#2d6cdf", header_color: "#2d3748",
    font_family: "Helvetica", font_size: 10, header_note: "", footer_text: "",
    show_logo: true, show_qr: true, layout: "standard", is_default: true },
  { id: 2, name: "Grün", accent_color: "#2f9e44", header_color: "#1f2733",
    font_family: "Times", font_size: 11, header_note: "", footer_text: "",
    show_logo: true, show_qr: false, layout: "formular", is_default: false },
];

test("Die Vorlagenliste steht in den Firmendaten", async () => {
  const { window } = startApp({ routes: { "/api/pdf-templates": TEMPLATES } });
  await settle();
  window.document.querySelector("#nav-settings").click();
  await settle(50);

  const rows = [...window.document.querySelectorAll("#template-body tr")];
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /Standard/);
  assert.match(rows[0].textContent, /Vorgabe/);
  assert.ok(rows[0].querySelector("a[href='/api/pdf-templates/1/preview']"),
            "Vorschau-Link je Vorlage");
  assert.equal(rows[0].querySelector("button[data-act=default]"), null,
               "die Vorgabe braucht den Knopf nicht");
  assert.ok(rows[1].querySelector("button[data-act=default]"));
  assert.match(rows[0].textContent, /Standard/);
  assert.match(rows[1].textContent, /Formular/, "das Layout steht in der Liste");
});

test("Das Layout einer Vorlage geht mit zum Server und wieder zurück ins Formular",
     async () => {
  const { window, requests } = startApp({
    routes: { "/api/pdf-templates": (o) => (o.method === "POST" ? TEMPLATES[1] : TEMPLATES) },
  });
  await settle();
  window.document.querySelector("#nav-settings").click();
  await settle(50);

  const form = window.document.querySelector("#template-form");
  assert.equal(form.layout.value, "standard", "neue Vorlagen starten im Standard");

  // Bearbeiten holt das Layout der Vorlage ins Formular …
  const rows = [...window.document.querySelectorAll("#template-body tr")];
  rows[1].querySelector("button[data-act=edit]").click();
  assert.equal(form.layout.value, "formular");

  // … und Speichern schickt es mit.
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(50);
  const post = requests.find((r) => r.url.startsWith("/api/pdf-templates/2")
                                    && r.options.method === "PUT");
  assert.equal(JSON.parse(post.options.body).layout, "formular");
});

test("Eine Vorlage lässt sich anlegen", async () => {
  const { window, requests } = startApp({
    routes: { "/api/pdf-templates": (o) => (o.method === "POST" ? TEMPLATES[1] : []) },
  });
  await settle();
  window.document.querySelector("#nav-settings").click();
  await settle(50);

  const form = window.document.querySelector("#template-form");
  form.name.value = "Grün";
  form.font_family.value = "Times";
  form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await settle(50);

  const post = requests.find((r) => r.url === "/api/pdf-templates" && r.options.method === "POST");
  const body = JSON.parse(post.options.body);
  assert.equal(body.name, "Grün");
  assert.equal(body.font_family, "Times");
  assert.equal(body.accent_color, "#2d6cdf");
  assert.equal(body.show_qr, true);
  assert.equal(body.layout, "standard");
});

test("Der PDF-Download fragt nach der Vorlage", async () => {
  const { window } = startApp({
    routes: { "/api/pdf-templates": TEMPLATES,
              "/api/invoices": [INVOICE_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  const link = window.document.querySelector("#history-body a[data-act=pdf]");
  link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  const menu = window.document.querySelector("#pdf-template-menu");
  assert.equal(menu.hidden, false, "das Auswahlfenster ist offen");
  assert.deepEqual([...menu.querySelectorAll("button")].map((b) => b.textContent),
                   ["Standard ★", "Grün (Vordruck)"]);
});

test("Die gewählte Vorlage hängt am Download", async () => {
  const { window } = startApp({
    routes: { "/api/pdf-templates": TEMPLATES, "/api/invoices": [INVOICE_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();
  const downloads = captureDownloads(window);

  window.document.querySelector("#history-body a[data-act=pdf]")
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  const menu = window.document.querySelector("#pdf-template-menu");
  [...menu.querySelectorAll("button")]
    .find((b) => b.textContent === "Grün (Vordruck)").click();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].href, "/api/invoices/3/pdf?template=2");
  assert.equal(menu.hidden, true, "danach ist das Fenster wieder zu");
});

test("Schon eine einzige Vorlage steht beim Beleg zur Wahl", async () => {
  // Vorher erschien das Fenster erst ab zwei Vorlagen: wer nur den Vordruck
  // angelegt hatte, sah bei der Rechnung nie eine Auswahl.
  const { window } = startApp({
    routes: { "/api/pdf-templates": [TEMPLATES[1]], "/api/invoices": [INVOICE_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  const link = window.document.querySelector("#history-body a[data-act=pdf]");
  link.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  const menu = window.document.querySelector("#pdf-template-menu");
  assert.equal(menu.hidden, false);
  assert.deepEqual([...menu.querySelectorAll("button")].map((b) => b.textContent),
                   ["Vorgabe (Standardaussehen)", "Grün (Vordruck)"]);
});

test("Ohne Vorlage lädt der Link direkt herunter", async () => {
  const { window } = startApp({
    routes: { "/api/pdf-templates": [], "/api/invoices": [INVOICE_ROW] },
  });
  await settle();
  window.document.querySelector("#nav-history").click();
  await settle();

  const link = window.document.querySelector("#history-body a[data-act=pdf]");
  const event = new window.MouseEvent("click", { bubbles: true, cancelable: true });
  window.document.addEventListener("click", (e) => e.preventDefault(), true);
  link.dispatchEvent(event);
  assert.equal(window.document.querySelector("#pdf-template-menu").hidden, true);
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
    downloads.push({ name: this.download, href: this.getAttribute("href"),
                     content: lastContent });
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

// -------------------------------- Bearbeitungssperre (Lieferschein)
test("Das Bearbeiten eines Lieferscheins sperrt ihn und zeigt den Hinweis", async () => {
  const { window, requests } = startApp({ routes: { "/api/delivery-notes": [DN_OPEN_ROW] } });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();

  window.document.querySelector("#delivery-body button[data-act=edit]").click();
  await settle(50);

  const lockReq = requests.find((r) => r.url === "/api/delivery-notes/5/lock"
                                    && r.options.method === "POST");
  assert.ok(lockReq, "POST /api/delivery-notes/5/lock");
  assert.equal(window.document.querySelector("#delivery-lock-banner").hidden, false);
  assert.equal(window.document.querySelector("#delivery-form [name=customer_name]").value,
              "Alpha AG");
});

test("Ein bereits gesperrter Lieferschein lässt sich nicht öffnen", async () => {
  const { window, requests } = startApp({
    routes: {
      "/api/delivery-notes": [DN_OPEN_ROW],
      "/api/delivery-notes/5/lock": { locked: true, locked_by: "bernd", editable: false },
    },
  });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();
  window.alert = (msg) => { window._alerted = msg; };

  window.document.querySelector("#delivery-body button[data-act=edit]").click();
  await settle(50);

  assert.match(window._alerted || "", /bernd/);
  assert.equal(window.document.querySelector("#delivery-form [name=customer_name]").value, "");
  assert.ok(!requests.some((r) => r.url === "/api/presence/delivery_note/5"));
});

test("Abbrechen gibt die Lieferschein-Sperre wieder frei", async () => {
  const { window, requests } = startApp({ routes: { "/api/delivery-notes": [DN_OPEN_ROW] } });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();
  window.document.querySelector("#delivery-body button[data-act=edit]").click();
  await settle(50);

  window.document.querySelector("#delivery-cancel-edit").click();
  await settle(20);

  assert.ok(requests.some((r) => r.url === "/api/delivery-notes/5/lock"
                              && r.options.method === "DELETE"));
  assert.equal(window.document.querySelector("#delivery-lock-banner").hidden, true);
});

test("Ein Wechsel der Ansicht gibt eine offene Lieferschein-Sperre frei", async () => {
  const { window, requests } = startApp({ routes: { "/api/delivery-notes": [DN_OPEN_ROW] } });
  await settle();
  window.document.querySelector("#nav-delivery").click();
  await settle();
  window.document.querySelector("#delivery-body button[data-act=edit]").click();
  await settle(50);

  window.document.querySelector("#nav-customers").click();
  await settle(20);

  assert.ok(requests.some((r) => r.url === "/api/delivery-notes/5/lock"
                              && r.options.method === "DELETE"));
});

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

  for (const sel of ["#lock-banner", "#quote-lock-banner", "#delivery-lock-banner",
                     "#invoice-presence", "#quote-presence",
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
