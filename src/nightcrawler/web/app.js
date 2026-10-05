// Renders data/*.json. Every value comes from third-party sites: text is set with
// textContent only, and links are kept only if they are plain http(s) URLs.
"use strict";

// Time zone comes from the zone config (report.json); formatters are built once it is known.
let TZ, dayFmt, timeFmt, keyFmt;
function setTimeZone(tz) {
  TZ = tz;
  dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
  timeFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  keyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }); // YYYY-MM-DD
}

const STATUS_LABEL = {
  structured: "agenda lisible",
  platform_only: "billetterie externe",
  no_agenda: "agenda non lisible",
  robots_blocked: "refus robots.txt",
  fetch_error: "site injoignable",
  no_website: "pas de site",
};

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function safeLink(url, text) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    const a = el("a", text);
    a.href = u.href;
    a.rel = "noopener noreferrer nofollow";
    a.target = "_blank";
    return a;
  } catch {
    return null;
  }
}

function renderConcerts(concerts) {
  const root = document.getElementById("concerts");
  root.replaceChildren();
  if (!concerts.length) {
    root.append(el("p", "Aucun concert trouvé pour le moment.", "muted"));
    return;
  }
  let currentDay = null;
  let list = null;
  for (const c of concerts) {
    const start = new Date(c.start);
    const key = keyFmt.format(start);
    if (key !== currentDay) {
      currentDay = key;
      root.append(el("h2", dayFmt.format(start)));
      list = el("ul", null, "concerts");
      root.append(list);
    }
    const li = el("li");
    const midnight = timeFmt.format(start) === "00:00";
    li.append(el("span", midnight ? "—" : timeFmt.format(start), "time"));
    const body = el("div", null, "body");
    body.append(el("strong", c.title));
    body.append(el("span", c.venue_name, "venue"));
    if (c.performers && c.performers.length) {
      body.append(el("span", c.performers.join(" · "), "performers"));
    }
    const links = el("span", null, "links");
    const page = c.url && safeLink(c.url, "Page");
    const ticket = c.ticket_url && c.ticket_url !== c.url && safeLink(c.ticket_url, "Billets");
    if (page) links.append(page);
    if (ticket) links.append(ticket);
    if (links.childElementCount) body.append(links);
    li.append(body);
    list.append(li);
  }
}

function renderSources(venues, report) {
  const s = report.probe_status || {};
  document.getElementById("coverage").textContent =
    `${report.venues} lieux trouvés, ${report.venues_with_website} avec un site, ` +
    `${s.structured || 0} avec un agenda lisible, ` +
    `${report.venues_with_concerts} avec au moins un concert à venir.`;
  const order = Object.keys(STATUS_LABEL);
  const rows = [...venues].sort(
    (a, b) => order.indexOf(a.probe.status) - order.indexOf(b.probe.status) || b.concerts - a.concerts,
  );
  const tbody = document.getElementById("venue-rows");
  tbody.replaceChildren();
  for (const v of rows) {
    const tr = el("tr");
    const name = el("td");
    name.append((v.website && safeLink(v.website, v.name)) || el("span", v.name));
    tr.append(name, el("td", v.category));
    const method = v.probe.method ? ` (${v.probe.method})` : "";
    tr.append(el("td", (STATUS_LABEL[v.probe.status] || v.probe.status) + method, v.probe.status));
    tr.append(el("td", String(v.concerts)));
    tbody.append(tr);
  }
}

function setupTabs() {
  for (const btn of document.querySelectorAll("nav button")) {
    btn.addEventListener("click", () => {
      for (const other of document.querySelectorAll("nav button")) {
        const on = other === btn;
        other.setAttribute("aria-pressed", String(on));
        document.getElementById(other.dataset.tab).hidden = !on;
      }
    });
  }
}

async function main() {
  setupTabs();
  try {
    const [concerts, venues, report] = await Promise.all(
      ["concerts", "venues", "report"].map((n) => fetch(`data/${n}.json`).then((r) => r.json())),
    );
    setTimeZone(report.zone.timezone || "Europe/Paris");
    document.getElementById("zone").textContent =
      `${concerts.length} concerts dans les ${report.zone.window_days} prochains jours · ${report.zone.name}`;
    document.getElementById("generated").textContent =
      `Mis à jour le ${new Date(report.generated_at).toLocaleString("fr-FR", { timeZone: TZ })}.`;
    renderConcerts(concerts);
    renderSources(venues, report);
  } catch (err) {
    document.getElementById("concerts").replaceChildren(el("p", "Données indisponibles.", "muted"));
  }
}

main();
