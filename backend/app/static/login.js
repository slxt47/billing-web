"use strict";
// Ausgelagert aus login.html: die Content-Security-Policy erlaubt keine
// Inline-Skripte mehr (siehe app/security.py).

function cookie(name) {
  const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

// CSRF-Token aus dem Cookie in das Formular übernehmen. Der Server hat es
// beim Ausliefern dieser Seite gesetzt und prüft es beim Absenden.
document.getElementById("csrf-token").value = cookie("csrftoken");

const params = new URLSearchParams(location.search);

if (params.get("error")) {
  document.getElementById("login-error").style.display = "block";
}

if (params.get("csrf")) {
  const el = document.getElementById("login-locked");
  el.textContent = "Die Sitzung ist abgelaufen. Bitte erneut anmelden.";
  el.style.display = "block";
}

const locked = parseInt(params.get("locked"), 10);
if (locked > 0) {
  const mins = Math.ceil(locked / 60);
  const el = document.getElementById("login-locked");
  el.textContent = `Zu viele Fehlversuche. Bitte in ca. ${mins} Minute${mins === 1 ? "" : "n"} erneut versuchen.`;
  el.style.display = "block";
  document.querySelector(".login-card button[type=submit]").disabled = true;
}
