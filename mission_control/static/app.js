const connDot = document.getElementById("conn-dot");
const connLabel = document.getElementById("conn-label");

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function renderLine(evt) {
  const feed = document.getElementById(`feed-${evt.agent}`);
  if (!feed) return;

  const line = document.createElement("div");
  line.className = "feed-line";

  const tag = document.createElement("span");
  tag.className = `tag tag-${evt.action}`;
  tag.textContent = evt.action;
  line.appendChild(tag);

  let text = "";
  if (evt.agent === "Scout") {
    text = `${evt.team} (${evt.signal}, gap=${evt.gap_pct}% vs ${evt.book}) — ${evt.edge_type}`;
  } else if (evt.agent === "Analyst") {
    if (evt.action === "TRADE") {
      text = `${evt.team} — Tier ${evt.tier || "?"}, gap=${evt.gap_pct}%, confidence=${evt.verdict_confidence || "?"}`;
    } else if (evt.action === "SKIP") {
      text = `${evt.team} — gap=${evt.gap_pct}% — ${evt.reason || ""}`;
    } else if (evt.action === "SHADOW") {
      text = `${evt.team} — Tier ${evt.tier || "?"}, gap=${evt.gap_pct}% — ${evt.reason || ""}`;
    }
  } else if (evt.agent === "Auditor") {
    text = `${evt.period_end} — ${evt.n_verdicts} tier/signal verdict(s) — ${evt.assessment || ""}`;
  } else if (evt.agent === "Ledger") {
    text = `${evt.target} — ${evt.note || ""}`;
  }

  const meta = document.createElement("span");
  meta.className = "feed-meta";
  meta.textContent = ` [${evt.desk || ""} ${fmtTime(evt.ts)}] `;

  line.appendChild(meta);
  line.appendChild(document.createTextNode(text));

  feed.prepend(line);
  while (feed.children.length > 40) {
    feed.removeChild(feed.lastChild);
  }
}

function fireStation(agent) {
  const station = document.querySelector(`.station[data-agent="${agent}"]`);
  if (!station) return;
  station.classList.remove("station-dim");
  station.classList.add("firing");
  clearTimeout(station._fireTimeout);
  station._fireTimeout = setTimeout(() => station.classList.remove("firing"), 1400);
}

function connect() {
  const es = new EventSource("/events");

  es.onopen = () => {
    connDot.classList.add("live");
    connLabel.textContent = "live";
  };

  es.onerror = () => {
    connDot.classList.remove("live");
    connLabel.textContent = "reconnecting…";
  };

  es.onmessage = (msg) => {
    try {
      const evt = JSON.parse(msg.data);
      if (evt.agent === "System") return;
      fireStation(evt.agent);
      renderLine(evt);
    } catch (e) {
      console.error("bad event", e, msg.data);
    }
  };
}

connect();
