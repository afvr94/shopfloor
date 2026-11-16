"use strict";
const PALETTE = ["#c9a0ff", "#7bd0c4", "#f0a05a", "#8fb8ff", "#e88fb0", "#b5d36a"];
const STAGE_ORDER = ["needs_you", "claimed", "planning", "waiting", "building", "in_review",
                     "merged", "done", "stopped", "cancelled"];
const STAGE_LABEL = {
  needs_you: "Needs you", claimed: "Claimed", planning: "Planning", waiting: "Waiting",
  building: "Building", in_review: "In review", merged: "Merged", done: "Done",
  stopped: "Stopped", cancelled: "Cancelled",
};
const CLOSED = new Set(["merged", "done", "stopped", "cancelled"]);
const POLL_MS = 5000;
const lanes = new Map();
const lessonsOpen = new Set();
const runNotes = new Map();
const FEED_LINES = 6;

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids) if (k != null) n.append(k);
  return n;
}

function safeUrl(u) {
  try {
    const p = new URL(String(u));
    return p.protocol === "http:" || p.protocol === "https:" ? p.href : null;
  } catch (e) { return null; }
}

function link(url, text) {
  const href = safeUrl(url);
  return href ? el("a", { href, target: "_blank", rel: "noopener noreferrer" }, text) : null;
}

function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? String(iso || "") : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Shopfloor": "1" },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error body */ }
  return { status: res.status, ok: res.ok, data };
}

function ticketRow(crewId, t) {
  const links = el("div", { class: "links" });
  for (const pr of t.prs || []) {
    const url = typeof pr === "string" ? pr : pr && pr.url;
    const label = pr && pr.number ? "PR " + (pr.repo ? pr.repo : "") + "#" + pr.number : "PR";
    const a = link(url, label);
    if (a) links.append(a);
  }
  const issue = link(t.url, "Ticket");
  if (issue) links.append(issue);
  links.append(el("a", { href: "/briefing/" + encodeURIComponent(crewId) + "/" + encodeURIComponent(t.key) }, "Briefing"));
  return el("li", { class: "ticket" },
    el("div", {}, el("span", { class: "key" }, t.key), el("span", { class: "title" }, t.title || "")),
    links);
}

function questionBox(crewId, q) {
  const text = el("textarea", { "aria-label": "Answer for " + q.key, required: true, maxlength: 4000 });
  const note = el("span", { class: "note", role: "status" });
  const send = el("button", { type: "submit" }, "Send answer");
  const form = el("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      if (!text.value.trim()) return;
      send.disabled = true;
      try {
        const r = await post("/api/command", { crew: crewId, key: q.key, text: text.value });
        if (r.ok) { text.value = ""; note.textContent = "Sent."; }
        else note.textContent = (r.data && r.data.message) || "Could not send (" + r.status + ").";
      } catch (err) { note.textContent = "Could not reach the board."; }
      send.disabled = false;
    },
  }, text, send, note);
  return el("div", { class: "question" },
    el("strong", {}, q.key + (q.title ? " - " + q.title : "")),
    el("p", {}, q.question || ""),
    form);
}

function runNowButton(crewId) {
  const note = el("span", { class: "note", role: "status" }, runNotes.get(crewId) || "");
  const say = (msg) => { runNotes.set(crewId, msg); note.textContent = msg; };
  const btn = el("button", {
    class: "run-now", type: "button",
    onclick: async () => {
      btn.disabled = true;
      try {
        const r = await post("/api/run", { crew: crewId });
        say(r.status === 409 ? "Already running."
          : r.ok ? "Started." : (r.data && r.data.message) || "Could not start (" + r.status + ").");
      } catch (err) { say("Could not reach the board."); }
      setTimeout(() => { btn.disabled = false; }, 2000);
    },
  }, "Run now");
  return [btn, note];
}

function stageSection(crewId, stage, tickets) {
  const list = el("ul", {}, ...tickets.map((t) => ticketRow(crewId, t)));
  const title = (STAGE_LABEL[stage] || stage) + " (" + tickets.length + ")";
  if (CLOSED.has(stage)) return el("details", {}, el("summary", {}, title), list);
  return el("section", {}, el("h3", {}, title), list);
}

function runClass(result) {
  const r = String(result || "").toLowerCase();
  if (/^(build failed|build timed out|failed|error|stopped|cancelled)\b/.test(r)) return "bad";
  return /^(merged|build finished|done|success)$/.test(r) ? "ok" : "";
}

function sessionPanel(list) {
  const box = el("div", {});
  box.append(el("h3", {}, "Sessions (" + list.length + ")"));
  for (const s of list) {
    const title = s.kind + (s.arg ? " " + s.arg : "");
    const head = el("div", {}, el("strong", {}, title), " ",
      el("span", { class: "badge" + (s.running ? " run" : "") }, s.running ? "Active" : "Off shift"));
    const stats = el("p", { class: "meta" }, s.action + " · " + s.actions + " actions · " + s.snags + " snags"
      + (s.helpersActive ? " · " + s.helpersActive + " helpers" : "") + " · last " + when(s.lastAt * 1000));
    const feed = (s.feed || []).slice(-FEED_LINES).map((f) => el("li", { class: "feed " + (f.tone || "") },
      el("span", { class: "ts" }, new Date(f.at * 1000).toLocaleTimeString()), f.text));
    box.append(el("div", { class: "session" }, head, stats, el("ul", {}, ...feed)));
  }
  return box;
}

function buildLane(c, color, sess) {
  const lane = el("article", { class: "lane", style: "--lane:" + color });
  const head = el("div", { class: "lane-head" }, el("h2", {}, c.name || c.id));
  lane.append(head);
  if (c.error) { lane.append(el("div", { class: "error", role: "alert" }, c.error)); return lane; }

  if (c.dryRun) head.append(el("span", { class: "badge dry" }, "Dry run"));
  if (c.paused) head.append(el("span", { class: "badge paused" }, "Paused"));
  if (c.running) head.append(el("span", { class: "badge run" }, "Running"));
  if (c.building) head.append(el("span", { class: "badge run" }, "Building"));
  head.append(...runNowButton(c.id));
  const meta = [c.gitFlow && c.gitFlow.type, c.nextRun ? "Next run " + when(c.nextRun) : "No scheduled run"];
  lane.append(el("p", { class: "meta" }, meta.filter(Boolean).join(" · ")));

  const questions = c.questions || [];
  if (questions.length) {
    lane.append(el("h3", {}, "Open questions (" + questions.length + ")"));
    for (const q of questions) lane.append(questionBox(c.id, q));
  }

  const byStage = {};
  for (const t of c.tickets || []) (byStage[t.stage] = byStage[t.stage] || []).push(t);
  const stages = STAGE_ORDER.filter((s) => byStage[s]).concat(Object.keys(byStage).filter((s) => !STAGE_ORDER.includes(s)));
  if (!stages.length) lane.append(el("p", { class: "meta" }, "No tickets yet."));
  for (const s of stages) lane.append(stageSection(c.id, s, byStage[s]));

  if ((c.lessons || []).length) {
    const d = el("details", { ontoggle: () => (d.open ? lessonsOpen.add(c.id) : lessonsOpen.delete(c.id)) },
      el("summary", {}, "Lessons (" + c.lessons.length + ")"),
      ...c.lessons.map((l) => el("div", { class: "lesson" }, el("span", { class: "ts" }, l.id + " · " + l.area), l.text)));
    d.open = lessonsOpen.has(c.id);
    lane.append(d);
  }

  if (sess.length) lane.append(sessionPanel(sess));

  const runs = (c.runs || []).slice(-8).reverse();
  if (runs.length) {
    lane.append(el("h3", {}, "Recent runs"));
    lane.append(el("ul", {}, ...runs.map((r) => el("li", { class: "run" },
      el("span", { class: "ts" }, when(r.ts)),
      el("span", { class: "res " + runClass(r.result) }, r.result || ""),
      r.key ? r.key + " " : "", r.note || ""))));
  }
  return lane;
}

function busy(lane) {
  const a = document.activeElement;
  if (!lane || !lane.contains(a)) return false;
  return a.tagName === "TEXTAREA" || [...lane.querySelectorAll("textarea")].some((t) => t.value);
}

function render(state) {
  const root = document.getElementById("lanes");
  const banner = document.getElementById("banner");
  banner.hidden = !state.error;
  banner.textContent = state.error || "";
  const crews = state.crews || [];
  const seen = new Set();
  crews.forEach((c, i) => {
    seen.add(c.id);
    const sess = (state.sessions || []).filter((s) => s.crew === c.id);
    const sig = JSON.stringify([c, sess]);
    const prev = lanes.get(c.id);
    if (prev && prev.sig === sig) return;
    if (prev && busy(prev.node)) return;
    const node = buildLane(c, PALETTE[i % PALETTE.length], sess);
    if (prev) prev.node.replaceWith(node); else root.append(node);
    lanes.set(c.id, { sig, node });
  });
  for (const [id, v] of lanes) if (!seen.has(id)) { v.node.remove(); lanes.delete(id); }
  crews.forEach((c) => root.append(lanes.get(c.id).node));
  const empty = document.getElementById("empty");
  if (!crews.length && !empty) root.append(el("p", { id: "empty", class: "empty" }, "No crews yet. Run shopfloor init to add one."));
  if (crews.length && empty) empty.remove();
}

async function poll() {
  const stamp = document.getElementById("updated");
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    render(await res.json());
    stamp.textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) {
    stamp.textContent = "Board unreachable, retrying...";
  }
}

poll();
setInterval(poll, POLL_MS);
