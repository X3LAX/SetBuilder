"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = s => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const fmtH = m => { m = Math.round(m); return m >= 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : `${m} min`; };

const S = { state: null, proj: null, view: "home", rows: null, sort: ["bpm", 1], filter: "", lock: -1 };
const native = () => window.pywebview && window.pywebview.api;

// ------------------------------------------------------------------ API

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const m = j.detail || `Erreur ${r.status}`; toast(m, true); throw new Error(m); }
  return j;
}

let jobBusy = false;
async function job(path, body, label, onp) {
  if (jobBusy) { toast("Une tâche est déjà en cours, attends qu'elle finisse."); throw new Error("busy"); }
  jobBusy = true;
  const box = $("#job"); box.hidden = false;
  $(".msg", box).textContent = label || "…"; $(".bar i", box).style.width = "3%"; $(".jsteps", box).textContent = "";
  const stop = $("#jobStop", box); stop.disabled = false; stop.textContent = "Arrêter";
  const t0 = Date.now();
  try {
    const { job: id } = await api(path, body);
    stop.onclick = () => { stop.disabled = true; stop.textContent = "Arrêt…"; api(`/api/jobs/${id}/cancel`, {}); };
    for (;;) {
      await new Promise(r => setTimeout(r, 450));
      const j = await api(`/api/jobs/${id}`);
      if (j.total) $(".bar i", box).style.width = `${Math.max(3, 100 * j.done / j.total)}%`;
      $(".msg", box).textContent = `${label || ""}${j.total ? ` — ${j.done}/${j.total}` : ""}${j.message ? " · " + j.message : ""}`;
      const now = (Date.now() - t0) / 1000, st = j.steps || [];
      $(".jsteps", box).textContent = st.length > 1 || now > 5 ? st.map((s, i) =>
        `${s.name} ${Math.round((i + 1 < st.length ? st[i + 1].start : now) - s.start)} s`).join("  ·  ") + `  —  ${Math.round(now)} s au total` : "";
      if (onp) onp(j);
      if (j.state === "done") return j.result;
      if (j.state === "cancelled") { toast("Arrêté."); throw new Error("cancelled"); }
      if (j.state === "error") { toast(j.error, true); throw new Error(j.error); }
    }
  } finally { jobBusy = false; box.hidden = true; }
}

function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "on" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.className = "", err ? 6000 : 3200);
}

function modal(html) { const m = $("#modal"); $(".card", m).innerHTML = html; m.hidden = false; return $(".card", m); }
function closeModal() { $("#modal").hidden = true; }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

async function pickFolders(multiple = true) {
  if (native()) return multiple ? await native().pick_folders() : [await native().pick_folder()].filter(Boolean);
  const p = prompt("Chemin du dossier :"); return p ? [p] : [];
}
async function pickFiles() {
  if (native()) return await native().pick_files();
  const p = prompt("Chemin du fichier :"); return p ? [p] : [];
}
async function pickImage() {
  if (native()) return await native().pick_image();
  return prompt("Chemin de l'image (jpg/png) :");
}

// ------------------------------------------------------------------ helpers

function keyChip(k) {
  const n = parseInt(k); if (!n) return `<span class="key" style="background:#444;color:#ccc">?</span>`;
  const h = ((n - 1) * 30 + 10) % 360, b = k.endsWith("B");
  return `<span class="key" style="background:hsl(${h},${b ? 75 : 60}%,${b ? 68 : 56}%)">${k}</span>`;
}
const coverUrl = t => `/api/cover?root=${encodeURIComponent(S.proj.root)}&id=${t.id}&v=${t.cover_source || ""}`;
const thumb = t => t.has_cover ? `<img class="thumb" loading="lazy" src="${coverUrl(t)}" onerror="this.outerHTML='<div class=\\'thumb none\\'>♪</div>'">` : `<div class="thumb none" title="Sans pochette">♪</div>`;
const eBar = e => `<div class="ebar" title="Énergie ${e.toFixed(2)}"><i style="width:${Math.min(100, Math.max(4, (e + 2) * 25))}%"></i></div>`;
const stars = k => k ? `<span class="stars" title="Connu : ${k}/10">${"★".repeat(Math.round(k / 2))}</span>` : "";
function flags(t) {
  const f = [];
  if (t.remix) f.push(`<span class="chip remix">remix</span>`);
  if (t.known >= 4) f.push(`<span class="chip known">connu</span>`);
  if (t.duplicate) f.push(`<span class="chip warn" title="Plusieurs versions du même titre">doublon</span>`);
  if (t.short) f.push(`<span class="chip bad">extrait</span>`);
  if (t.quiet) f.push(`<span class="chip warn" title="Mixé moins fort : monte le gain">volume faible</span>`);
  if (!t.has_cover) f.push(`<span class="chip">sans pochette</span>`);
  if (t.bitrate && t.bitrate < 200 && !/flac|wav|aiff/i.test(t.kind)) f.push(`<span class="chip" title="Qualité">${t.bitrate} kbps</span>`);
  if (t.health && t.health !== "ok") f.push(healthChip(t));
  if (t.forced) f.push(`<span class="chip ok">forcé</span>`);
  if (t.excluded) f.push(`<span class="chip bad">exclu</span>`);
  return f.join("");
}
function healthChip(t) {
  if (!t.health || t.health === "ok") return t.health === "ok" ? `<span class="chip ok" title="Fichier vérifié : complet et lisible">✓ fichier OK</span>` : "";
  const tip = esc((t.issues || []).join(" · "));
  return t.health === "bad" ? `<span class="chip bad" title="${tip}">⚠ fichier abîmé</span>`
    : `<span class="chip warn" title="${tip}">${esc(shortIssue(t.issues))}</span>`;
}
function shortIssue(issues = []) {
  const i = issues[0] || "";
  if (i.startsWith("faux")) return "faux 320";
  if (i.startsWith("silence")) return "silence";
  if (i.startsWith("qualité faible")) return "basse qualité";
  if (i.startsWith("extension")) return "mauvaise extension";
  if (i.startsWith("très court")) return "très court";
  if (i.includes("erreur")) return "petites erreurs";
  return "à vérifier";
}
// Show a file selected in a Finder / Explorer window.
const FM = () => (S.state && S.state.os === "windows") ? "l'Explorateur" : "le Finder";
const reveal = path => api("/api/reveal", { path });
const trackPath = t => `${S.proj.root}/${t.file}`;
const byId = () => Object.fromEntries(S.proj.tracks.map(t => [t.id, t]));

// ------------------------------------------------------------------ navigation

$$("nav a").forEach(a => a.addEventListener("click", () => go(a.dataset.view)));
$("#projBtn").addEventListener("click", () => go("home"));

function go(view) {
  if (["import", "library", "set", "export"].includes(view) && !S.proj) { toast("Choisis ou crée d'abord un projet."); view = "home"; }
  S.view = view;
  $$("nav a").forEach(a => a.classList.toggle("on", a.dataset.view === view));
  $("#projBtn").textContent = S.proj ? S.proj.name : "Choisir un projet";
  badges();
  ({ home: renderHome, import: renderImport, library: renderLibrary, set: renderSet, export: renderExport,
     soundcloud: renderSoundcloud, spotify: renderSpotify, discover: renderDiscover, settings: renderSettings })[view]();
}

// Where am I? small counters next to each step.
function badges() {
  const P = S.proj, set = (id, txt, cls = "") => { const b = $(id); b.textContent = txt; b.className = "badge " + cls; };
  if (!P) { ["#bImport", "#bLibrary", "#bSet", "#bExport"].forEach(i => set(i, "")); return; }
  const n = P.tracks.length, a = P.tracks.filter(t => t.analysed).length;
  set("#bImport", n ? `${n}` : "", n ? "done" : "");
  set("#bLibrary", !n ? "" : a < n ? `${n - a} à analyser` : "✓", !n ? "" : a < n ? "todo" : "done");
  set("#bSet", P.set.length ? fmtH(P.set_minutes) : "", P.set.length ? "done" : "");
}

async function loadProject(root) { S.proj = await api(`/api/project?root=${encodeURIComponent(root)}`); }
async function refresh() { await loadProject(S.proj.root); go(S.view); }

// ------------------------------------------------------------------ home

async function renderHome() {
  $("#title").textContent = "Projets";
  S.state = await api("/api/state");
  const ps = S.state.projects;
  $("#view").innerHTML = `
    <div class="panel"><h2>Nouveau projet</h2>
      <p class="hint">Un projet = un dossier dans <code>Musique</code> avec tes morceaux rangés dans <code>Tracks</code>. Tes fichiers d'origine ne sont jamais modifiés : ils sont copiés.</p>
      <div class="row"><input id="npName" type="text" placeholder="Nom du set, ex. Latin Tech House" style="width:320px">
      <button class="btn primary" id="npGo">Créer</button><span class="spacer"></span>
      <button class="btn" id="opGo">Ouvrir un dossier existant…</button></div>
      <div class="muted" id="npPath" style="margin-top:8px"></div></div>
    <div class="cards">${ps.map(p => `<div class="pcard" data-root="${esc(p.root)}"><h3>${esc(p.name)}</h3>
      <div class="muted">${p.tracks} morceaux · ${p.set ? p.set + " dans le set" : "pas encore de set"}</div>
      <div class="path">${esc(p.root)}</div></div>`).join("") || `<div class="empty"><b>Aucun projet pour l'instant</b>Crée-en un, ou ouvre un dossier existant comme Musique/Latine.</div>`}</div>`;
  const upd = () => $("#npPath").textContent = $("#npName").value.trim() ? `Dossier : ${S.state.default_root}/${$("#npName").value.trim()}` : "";
  $("#npName").addEventListener("input", upd);
  $("#npGo").onclick = async () => {
    const name = $("#npName").value.trim(); if (!name) return toast("Donne un nom au projet.");
    S.proj = await api("/api/projects/new", { name }); go("import");
  };
  $("#opGo").onclick = async () => {
    const [root] = await pickFolders(false); if (!root) return;
    S.proj = await api("/api/projects/open", { root }); go(S.proj.tracks.length ? "library" : "import");
  };
  $$(".pcard").forEach(c => c.onclick = async () => { await loadProject(c.dataset.root); go(S.proj.tracks.length ? "library" : "import"); });
}

// ------------------------------------------------------------------ import

function renderImport() {
  $("#title").textContent = `Importer — ${S.proj.name}`;
  const rows = S.rows;
  const aiOk = S.state?.ai?.ok;
  $("#view").innerHTML = `
    <div class="panel"><h2>Ajouter des morceaux</h2>
      <p class="hint">Dossiers mal rangés acceptés : sous-dossiers, noms bizarres, fichiers sans pochette. L'app trouve tous les fichiers audio, propose des noms propres « Artiste - Titre (Remix) », repère les doublons, puis copie tout dans le projet et ajoute les pochettes manquantes. Tes originaux ne sont jamais modifiés.</p>
      <div class="row"><button class="btn primary" id="iFolders">Choisir des dossiers…</button><button class="btn" id="iFiles">Choisir des fichiers…</button>
        <span class="spacer"></span><button class="btn" id="iSc">☁ Depuis SoundCloud</button></div>
      <div class="drop" id="iDrop">…ou glisse des dossiers / fichiers ici</div></div>
    ${rows ? `<div class="panel">
      <div class="row" style="margin-bottom:12px"><h2 style="margin:0">${rows.length} fichiers trouvés</h2>
        <span class="chip warn">${rows.filter(r => !r.confident).length} à vérifier</span>
        <span class="chip">${rows.filter(r => !r.has_cover).length} sans pochette</span>
        <span class="chip warn">${rows.filter(r => r.dup_of).length} doublons</span>
        ${rows.some(r => r.health === "bad") ? `<span class="chip bad" title="Décochés : coche-les si tu veux quand même les importer">${rows.filter(r => r.health === "bad").length} fichiers abîmés</span>` : ""}<span class="spacer"></span>
        <button class="btn small" id="iClear">Vider</button>
        <button class="btn" id="iAI" ${aiOk ? "" : "disabled title='Connecte une IA dans Réglages'"}>✦ Corriger les noms douteux</button>
        <label class="check"><input type="checkbox" id="iCovers" checked> Pochettes</label>
        <label class="check"><input type="checkbox" id="iAn" checked> Analyser ensuite</label>
        <button class="btn primary" id="iRun">Importer ${rows.filter(r => !r.skip).length} morceaux</button></div>
      <table><thead><tr><th style="width:28px"></th><th style="width:28px"></th><th>Fichier d'origine</th><th style="width:26%">Artiste</th><th style="width:34%">Titre</th><th>Durée</th><th>État</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr data-i="${i}">
        <td><input type="checkbox" class="inc" ${r.skip ? "" : "checked"}></td><td class="num"><button class="icon pv" title="Écouter">▶</button><button class="icon finder" title="Afficher dans ${FM()}">⌕</button></td>
        <td class="name"><div class="t" title="${esc(r.src)}">${esc(r.rel)}</div><div class="a">${esc(r.folder)}</div></td>
        <td><input type="text" class="ia" value="${esc(r.artist)}"></td><td><input type="text" class="it" value="${esc(r.title)}"></td>
        <td class="num muted">${fmt(r.duration)}</td>
        <td>${r.by_ai ? `<span class="chip ai">IA</span>` : ""}${r.confident ? "" : `<span class="chip warn">à vérifier</span>`}${r.dup_of ? `<span class="chip warn">doublon</span>` : ""}${r.has_cover ? "" : `<span class="chip">sans pochette</span>`}${r.duration < 110 ? `<span class="chip bad">extrait</span>` : ""}${healthChip(r)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  $("#iFolders").onclick = async () => scanSources(await pickFolders(true));
  $("#iFiles").onclick = async () => scanSources(await pickFiles());
  $("#iSc").onclick = () => go("soundcloud");
  bindDrop($("#iDrop"));
  if (!rows) return;
  $$("tbody tr").forEach(tr => {
    const r = rows[tr.dataset.i];
    $(".inc", tr).onchange = e => { r.skip = !e.target.checked; $("#iRun").textContent = `Importer ${rows.filter(x => !x.skip).length} morceaux`; };
    $(".ia", tr).oninput = e => r.artist = e.target.value;
    $(".it", tr).oninput = e => r.title = e.target.value;
    $(".finder", tr).onclick = () => reveal(r.src);
    $(".pv", tr).onclick = () => player.playUrl(`/api/preview-audio?path=${encodeURIComponent(r.src)}`, r.title || r.rel, r.artist || r.folder);
  });
  $("#iClear").onclick = () => { S.rows = null; renderImport(); };
  $("#iAI").onclick = async () => { S.rows = await job("/api/import/ai", { rows }, "L'IA corrige les noms douteux"); renderImport(); toast("Noms corrigés : vérifie les lignes encore « à vérifier »."); };
  $("#iRun").onclick = async () => {
    const r = await job("/api/import/run", { root: S.proj.root, rows, fetch_covers: $("#iCovers").checked }, "Import");
    const analyse = $("#iAn").checked;
    S.rows = null; toast(`${r.added} morceaux importés, ${r.covers} pochettes ajoutées.`);
    if (analyse) await job("/api/analyze", { root: S.proj.root }, "Analyse BPM / tonalité / énergie").catch(() => {});
    await job("/api/quality", { root: S.proj.root }, "Contrôle des fichiers").catch(() => {});
    await loadProject(S.proj.root); go("library");
  };
}

async function scanSources(sources) {
  if (!sources?.length) return;
  S.state = S.state || await api("/api/state");
  const rows = await job("/api/import/preview", { sources }, "Recherche des fichiers audio");
  rows.forEach(r => r.skip = r.skip || r.duration < 20);
  const known = new Set((S.rows || []).map(x => x.src));
  S.rows = (S.rows || []).concat(rows.filter(x => !known.has(x.src)));
  if (S.view !== "import") go("import"); else renderImport();
}

// Drag & drop: the app window passes the real paths of dropped files (window.onNativeDrop).
function bindDrop(el) {
  ["dragenter", "dragover"].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.add("on"); }));
  ["dragleave", "drop"].forEach(ev => el.addEventListener(ev, () => el.classList.remove("on")));
}
window.onNativeDrop = paths => {
  if (!S.proj) return toast("Choisis d'abord un projet.");
  scanSources(paths);
};
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => { e.preventDefault(); if (!native()) toast("Le glisser-déposer marche dans l'app SetBuilder ; ici, utilise les boutons."); });

// ------------------------------------------------------------------ library

function renderLibrary() {
  const P = S.proj, ts = P.tracks;
  $("#title").textContent = `Bibliothèque — ${P.name}`;
  const q = S.filter.toLowerCase();
  const [sk, sd] = S.sort;
  const list = ts.filter(t => !q || `${t.artist} ${t.title}`.toLowerCase().includes(q))
    .sort((a, b) => { const x = a[sk], y = b[sk]; return (typeof x === "string" ? x.localeCompare(y) : (x || 0) - (y || 0)) * sd; });
  const nA = ts.filter(t => t.analysed).length, nC = ts.filter(t => !t.has_cover).length, inSet = new Set(P.set);
  const has = S.state?.ai?.ok;
  const th = (k, l) => `<th class="sort" data-k="${k}">${l}${sk === k ? (sd > 0 ? " ↑" : " ↓") : ""}</th>`;
  $("#view").innerHTML = `
    ${ts.length && nA < ts.length ? `<div class="banner warn"><b>${ts.length - nA} morceaux pas encore analysés.</b><span>BPM, tonalité et énergie sont nécessaires pour créer le set.</span><span class="spacer"></span><button class="btn primary" id="lAn2">Analyser maintenant</button></div>` : ""}
    ${ts.some(t => t.health === "bad") ? `<div class="banner warn"><b>${ts.filter(t => t.health === "bad").length} fichiers abîmés</b><span>Coupures ou sauts à la lecture : ils sont écartés des sets. Retélécharge-les (survole « ⚠ fichier abîmé » pour le détail).</span><span class="spacer"></span><button class="btn small" id="lBad">Les afficher</button></div>` : ""}
    ${ts.length && !ts.some(t => t.health) ? `<div class="banner"><b>Fichiers pas encore contrôlés.</b><span>Vérifie qu'ils sont complets, lisibles et de bonne qualité.</span><span class="spacer"></span><button class="btn primary" id="lQ2">Vérifier les fichiers</button></div>` : ""}
    ${ts.length && nA === ts.length && !P.set.length ? `<div class="banner"><b>Tout est analysé.</b><span>Prochaine étape : choisir tes artistes et générer le set.</span><span class="spacer"></span><button class="btn primary" id="lGoSet">Aller au set →</button></div>` : ""}
    <div class="panel"><div class="row">
      <div class="stats"><div class="stat"><b>${ts.length}</b><span>morceaux</span></div>
        <div class="stat"><b>${nA}/${ts.length}</b><span>analysés</span></div>
        <div class="stat"><b>${fmtH(ts.reduce((s, t) => s + t.duration, 0) / 60)}</b><span>de musique</span></div>
        <div class="stat"><b>${ts.filter(t => t.known >= 4).length}</b><span>titres connus</span></div></div>
      <span class="spacer"></span>
      <button class="btn primary" id="lAn">${nA < ts.length ? `Analyser ${ts.length - nA} morceaux` : "Ré-analyser"}</button>
      <button class="btn" id="lCov" ${nC ? "" : "disabled"}>Pochettes manquantes (${nC})</button>
      <button class="btn" id="lFix">Ranger les noms</button>
      <button class="btn" id="lQ">Vérifier les fichiers</button>
      <button class="btn" id="lKnown" ${has ? "" : "disabled title='Connecte ton abonnement Claude ou une clé API dans Réglages'"}>✦ Titres connus (IA)</button>
      <button class="btn" id="lAdd">+ Importer</button><button class="btn" id="lDir" title="Ouvrir le dossier des morceaux">Ouvrir le dossier</button></div></div>
    <div class="row" style="margin-bottom:10px"><input id="lQ" type="text" placeholder="Rechercher…" value="${esc(S.filter)}" style="width:280px">
      <span class="muted">Clique sur un nom pour le modifier · ★ = à quel point le titre est connu (modifiable)</span></div>
    ${ts.length ? `<table><thead><tr><th></th><th></th>${th("artist", "Morceau")}${th("bpm", "BPM")}${th("camelot", "Clé")}${th("energy", "Énergie")}${th("duration", "Durée")}${th("known", "Connu")}<th>Infos</th><th></th></tr></thead>
    <tbody>${list.map(t => `<tr data-id="${t.id}">
      <td><button class="icon play" title="Écouter">▶</button></td><td>${thumb(t)}</td>
      <td class="name"><div class="t edit" title="Modifier">${esc(t.title)}</div><div class="a edit">${esc(t.artist) || "<i>artiste inconnu</i>"}</div></td>
      <td class="num">${t.bpm ? t.bpm.toFixed(t.bpm % 1 ? 1 : 0) : "—"}</td><td>${t.analysed ? keyChip(t.camelot) : "—"}</td>
      <td>${t.analysed ? eBar(t.energy) : ""}</td><td class="num muted">${fmt(t.duration)}</td>
      <td><select class="kn" title="Connu (0-10) — ${t.known_source}">${[...Array(11).keys()].map(k => `<option ${k === t.known ? "selected" : ""}>${k}</option>`).join("")}</select></td>
      <td>${flags(t)}${inSet.has(t.id) ? `<span class="chip ok">dans le set</span>` : ""}</td>
      <td class="num"><button class="icon toset" title="Ajouter au set">＋</button><button class="icon force ${t.forced ? "on" : ""}" title="Forcer dans le set">★</button><button class="icon excl ${t.excluded ? "on" : ""}" title="Exclure du set">⊘</button><button class="icon cov" title="Choisir une pochette">🖼</button><button class="icon finder" title="Afficher dans ${FM()}">⌕</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty"><b>Projet vide</b>Va dans « Importer » pour ajouter des morceaux.</div>`}`;
  $("#lQ").oninput = e => { S.filter = e.target.value; const pos = e.target.selectionStart; renderLibrary(); const i = $("#lQ"); i.focus(); i.setSelectionRange(pos, pos); };
  $$("th.sort").forEach(h => h.onclick = () => { S.sort = [h.dataset.k, S.sort[0] === h.dataset.k ? -S.sort[1] : 1]; renderLibrary(); });
  $("#lAn").onclick = async () => {
    const r = await job("/api/analyze", { root: P.root, force: nA === ts.length }, "Analyse BPM / tonalité / énergie");
    const errs = Object.keys(r.errors); toast(errs.length ? `${errs.length} fichiers illisibles : ${errs.slice(0, 3).join(", ")}` : `${r.analysed} morceaux analysés.`, !!errs.length);
    refresh();
  };
  if ($("#lAn2")) $("#lAn2").onclick = () => $("#lAn").click();
  const runQ = async force => {
    const r = await job("/api/quality", { root: P.root, force }, "Contrôle des fichiers");
    const c = r.counts; toast(`${c.ok || 0} fichiers OK · ${c.warn || 0} à vérifier · ${c.bad || 0} abîmés.`, !!c.bad); refresh();
  };
  $("#lQ").onclick = () => runQ(true);
  if ($("#lQ2")) $("#lQ2").onclick = () => runQ(false);
  if ($("#lBad")) $("#lBad").onclick = () => { S.sort = ["health", 1]; S.filter = ""; renderLibrary(); };
  if ($("#lGoSet")) $("#lGoSet").onclick = () => go("set");
  $("#lCov").onclick = async () => { const r = await job("/api/covers/fetch", { root: P.root }, "Recherche des pochettes"); toast(`${r.covers} pochettes trouvées sur ${r.asked}.`); refresh(); };
  $("#lFix").onclick = () => {
    const c = modal(`<h2>Ranger les noms des fichiers</h2><p class="hint">Renomme chaque fichier en « Artiste - Titre (Version) » et met à jour ses tags. Avec l'IA, les noms illisibles sont aussi corrigés.</p>
      <p class="hint" style="color:var(--warn)">⚠ Si ces morceaux sont déjà importés dans Rekordbox, les renommer casse le lien : il faudra ré-exporter et ré-importer le XML.</p>
      <div class="row"><button class="btn" id="fxA">Règles automatiques</button><button class="btn primary" id="fxB" ${has ? "" : "disabled"}>✦ Avec l'IA</button><span class="spacer"></span><button class="btn" id="fxC">Annuler</button></div>`);
    const run = async useAI => { closeModal(); const r = await job("/api/tracks/fix-names", { root: P.root, ai: useAI }, "Rangement des noms"); toast(`${r.changed} fichiers renommés.`); refresh(); };
    $("#fxA", c).onclick = () => run(false); $("#fxB", c).onclick = () => run(true); $("#fxC", c).onclick = closeModal;
  };
  $("#lKnown").onclick = async () => { const r = await job("/api/known/ai", { root: P.root }, "L'IA note les titres connus"); toast(`${r.rated} morceaux notés.`); refresh(); };
  $("#lAdd").onclick = () => go("import");
  $("#lDir").onclick = () => api("/api/open", { path: `${P.root}/Tracks` });
  const tmap = byId();
  $$("tbody tr").forEach(tr => {
    const t = tmap[tr.dataset.id];
    $(".play", tr).onclick = () => player.play(t);
    const upd = async body => { S.proj = await api("/api/tracks/update", { root: P.root, id: t.id, ...body }); renderLibrary(); };
    $(".kn", tr).onchange = e => upd({ known: +e.target.value });
    $(".force", tr).onclick = () => upd({ forced: !t.forced, excluded: false });
    $(".excl", tr).onclick = () => upd({ excluded: !t.excluded, forced: false });
    $(".finder", tr).onclick = () => reveal(trackPath(t));
    $(".cov", tr).onclick = async () => { const img = await pickImage(); if (img) { S.proj = await api("/api/covers/set", { root: P.root, id: t.id, image: img }); renderLibrary(); } };
    $(".toset", tr).onclick = () => addToSet(t);
    $$(".edit", tr).forEach(el => el.onclick = () => editTrack(t));
  });
}

function editTrack(t) {
  const c = modal(`<h2>Modifier le morceau</h2>
    <div class="params"><label class="field">Artiste<input type="text" id="eA" value="${esc(t.artist)}"></label>
    <label class="field">Titre (avec la version entre parenthèses)<input type="text" id="eT" value="${esc(t.title)}"></label>
    <div class="two"><label class="field">BPM (corriger l'analyse)<input type="number" step="0.01" id="eB" value="${t.bpm || ""}"></label>
    <label class="field">Clé Camelot<input type="text" id="eK" value="${t.camelot}" placeholder="8A"></label></div>
    <p class="hint">Le fichier est renommé « Artiste - Titre » et ses tags mis à jour.</p>
    <div class="row"><span class="spacer"></span><button class="btn" id="eC">Annuler</button><button class="btn primary" id="eS">Enregistrer</button></div></div>`);
  $("#eC", c).onclick = closeModal;
  $("#eS", c).onclick = async () => {
    const body = { root: S.proj.root, id: t.id };
    if ($("#eA", c).value !== t.artist || $("#eT", c).value !== t.title) Object.assign(body, { artist: $("#eA", c).value, title: $("#eT", c).value });
    const b = parseFloat($("#eB", c).value); if (b && b !== t.bpm) body.bpm_override = b;
    const k = $("#eK", c).value.trim().toUpperCase(); if (/^(1[0-2]|[1-9])[AB]$/.test(k) && k !== t.camelot) body.key_override = k;
    S.proj = await api("/api/tracks/update", body); closeModal(); go(S.view);
  };
}

async function addToSet(t) {
  const set = [...S.proj.set]; if (set.includes(t.id)) return toast("Déjà dans le set.");
  const tm = byId(); let i = set.findIndex(id => tm[id].bpm > t.bpm); if (i < 0) i = set.length;
  set.splice(i, 0, t.id); S.proj = await api("/api/set/save", { root: S.proj.root, set });
  toast(`Ajouté au set en position ${i + 1}.`); go(S.view);
}

// ------------------------------------------------------------------ set

function renderSet() {
  const P = S.proj, tm = byId(), pr = Object.assign({ duration: 90, bpm_start: 122, bpm_end: 129, shape: "progressive", strictness: "normale", known_min: 4 }, P.params);
  const analysed = P.tracks.filter(t => t.analysed);
  if (!analysed.length) { $("#title").textContent = `Set — ${P.name}`; $("#view").innerHTML = `<div class="empty"><b>Analyse d'abord tes morceaux</b>Bibliothèque → « Analyser ».</div>`; return; }
  const bpms = analysed.map(t => t.bpm).sort((a, b) => a - b);
  $("#title").textContent = `Set — ${P.name}`;
  let start = 0;
  const rows = P.set.map((id, i) => {
    const t = tm[id], st = start; start += t.play;
    const tr = P.transitions[i];
    return `<tr data-i="${i}" draggable="true" class="${i <= S.lock ? "locked" : ""}">
      <td class="handle num" title="Glisser pour déplacer">⋮⋮ ${i + 1}</td><td class="num muted">${fmt(st)}</td><td>${thumb(t)}</td>
      <td class="name"><div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}</div></td>
      <td class="num">${t.bpm.toFixed(t.bpm % 1 ? 1 : 0)}</td><td>${keyChip(t.camelot)}</td><td>${eBar(t.energy)}</td><td>${stars(t.known)}</td>
      <td>${t.remix ? `<span class="chip remix">remix</span>` : ""}${t.quiet ? `<span class="chip warn">volume faible</span>` : ""}</td>
      <td class="num"><button class="icon play" title="Écouter">▶</button><button class="icon mix" title="Pré-écouter la transition vers le suivant" ${i + 1 < P.set.length ? "" : "disabled"}>⇄</button>
      <button class="icon rep" title="Remplacer par un morceau compatible">↻</button><button class="icon lock ${i <= S.lock ? "on" : ""}" title="Garder le début du set jusqu'ici lors d'une nouvelle génération">🔒</button>
      <button class="icon del" title="Retirer du set">✕</button><button class="icon finder" title="Afficher dans ${FM()}">⌕</button></td></tr>
      ${tr ? `<tr class="trans"><td></td><td colspan="9"><span class="tdot ${tr}">${{ ok: "● enchaînement OK", moyen: "● enchaînement possible", clash: "● clash de tonalité ou de tempo" }[tr]}</span></td></tr>` : ""}`;
  }).join("");
  const clashes = P.transitions.filter(x => x === "clash").length;
  const badInSet = P.set.filter(i => tm[i]?.health === "bad");
  $("#view").innerHTML = `
    ${badInSet.length ? `<div class="banner warn"><b>${badInSet.length} fichier(s) abîmé(s) dans ce set</b><span>${badInSet.map(i => esc(tm[i].title)).join(", ")} : remplace-les (↻) ou clique sur « Regénérer le set ».</span></div>` : ""}
    <div class="grid2">
      <div class="panel params"><h2>Réglages du set</h2>
        <label class="field">Durée : <b id="dV">${fmtH(pr.duration)}</b><input type="range" id="pD" min="30" max="300" step="5" value="${pr.duration}"></label>
        <div class="two"><label class="field">BPM de départ<input type="number" id="pS" value="${pr.bpm_start}" step="0.5"></label>
        <label class="field">BPM d'arrivée<input type="number" id="pE" value="${pr.bpm_end}" step="0.5"></label></div>
        <div class="muted" style="font-size:12px">Ta bibliothèque va de ${bpms[0]} à ${bpms[bpms.length - 1]} BPM.</div>
        <label class="field">Montée<select id="pSh"><option value="progressive">Progressive (échauffement doux)</option><option value="plateau">Montée rapide puis plateau</option><option value="lineaire">Linéaire</option></select></label>
        <label class="field">Harmonie<select id="pH"><option value="stricte">Stricte</option><option value="normale">Normale</option><option value="souple">Souple (plus de titres connus)</option></select></label>
        <label class="field">Titre « connu » à partir de<select id="pK">${[2, 3, 4, 5, 6, 7].map(k => `<option ${k == pr.known_min ? "selected" : ""}>${k}</option>`).join("")}</select></label>
        <button class="btn primary" id="pGo">${P.set.length ? "Regénérer le set" : "Générer le set"}</button>
        ${S.lock >= 0 ? `<div class="muted" style="font-size:12px">🔒 Les ${S.lock + 1} premiers morceaux seront gardés. <a href="#" id="unlock">Déverrouiller</a></div>` : ""}
      </div>
      <div class="panel"><div class="row" style="margin-bottom:8px"><h2 style="margin:0">Courbe du set</h2><span class="spacer"></span>
        <span class="muted"><span style="color:var(--accent)">●</span> BPM <span style="color:var(--blue);margin-left:10px">▮</span> énergie <span style="color:var(--muted);margin-left:10px">┄</span> cible</span></div>
        <canvas id="chart"></canvas>
        <div class="stats" style="margin-top:10px"><div class="stat"><b>${P.set.length}</b><span>morceaux</span></div><div class="stat"><b>${fmtH(P.set_minutes)}</b><span>durée de mix</span></div>
        <div class="stat"><b>${P.set.filter(i => tm[i].known >= pr.known_min).length}</b><span>titres connus</span></div>
        <div class="stat"><b style="color:${clashes ? "var(--bad)" : "var(--ok)"}">${clashes}</b><span>clash</span></div></div></div>
    </div>
    <div class="panel" id="artPanel"></div>
    ${P.set.length ? `<div class="panel"><table><thead><tr><th>#</th><th>Début</th><th></th><th>Morceau</th><th>BPM</th><th>Clé</th><th>Énergie</th><th>Connu</th><th></th><th></th></tr></thead><tbody id="setBody">${rows}</tbody></table></div>
    ${P.blocks.length > 1 ? `<details class="panel fold"><summary><h2>Set étendu</h2><span class="muted">${P.blocks.slice(1).reduce((n, b) => n + b.ids.length, 0)} morceaux de plus</span></summary>
      <div class="row"><span class="spacer"></span><button class="btn small" id="ext">Recalculer la suite</button></div>
      <p class="hint" style="margin-top:8px">Même début que le set, puis les titres connus restants, puis le reste en vagues qui remontent à chaque fois.</p>
      ${P.blocks.slice(1).map(b => `<div class="block"><h3>${esc(b.name)} <span>${b.ids.length} morceaux · ${fmtH(b.ids.reduce((s, i) => s + (tm[i]?.play || 0), 0) / 60)}</span></h3>
      <table><tbody>${b.ids.map(i => tm[i]).filter(Boolean).map(t => `<tr data-id="${t.id}"><td style="width:30px"><button class="icon play">▶</button></td><td class="name"><div class="t">${esc(t.artist)} — ${esc(t.title)}</div></td><td class="num" style="width:60px">${t.bpm}</td><td style="width:60px">${keyChip(t.camelot)}</td><td style="width:90px">${stars(t.known)}</td><td style="width:30px"><button class="icon finder" title="Afficher dans ${FM()}">⌕</button></td></tr>`).join("")}</tbody></table></div>`).join("")}</details>` : ""}`
      : `<div class="empty"><b>Pas encore de set</b>Règle la durée et le tempo, puis « Générer le set ».</div>`}`;
  $("#pSh").value = pr.shape; $("#pH").value = pr.strictness;
  $("#pD").oninput = e => $("#dV").textContent = fmtH(+e.target.value);
  $("#pGo").onclick = async () => {
    const params = { duration: +$("#pD").value, bpm_start: +$("#pS").value, bpm_end: +$("#pE").value, shape: $("#pSh").value, strictness: $("#pH").value, known_min: +$("#pK").value };
    S.proj = await job("/api/set/generate", { root: P.root, params, keep_prefix: S.lock + 1 }, "Construction du set");
    toast(`Set de ${S.proj.set.length} morceaux (${fmtH(S.proj.set_minutes)}).`); renderSet();
  };
  if ($("#unlock")) $("#unlock").onclick = e => { e.preventDefault(); S.lock = -1; renderSet(); };
  if ($("#ext")) $("#ext").onclick = async () => { S.proj = await job("/api/set/extend", { root: P.root }, "Calcul de la suite"); renderSet(); };
  drawChart(pr);
  renderArtists();
  const save = async set => { S.proj = await api("/api/set/save", { root: P.root, set }); renderSet(); };
  $$("#setBody tr[data-i]").forEach(tr => {
    const i = +tr.dataset.i, t = tm[P.set[i]];
    $(".play", tr).onclick = () => player.play(t);
    $(".mix", tr).onclick = () => player.transition(t, tm[P.set[i + 1]]);
    $(".finder", tr).onclick = () => reveal(trackPath(t));
    $(".del", tr).onclick = () => { const s = [...P.set]; s.splice(i, 1); if (S.lock >= i) S.lock--; save(s); };
    $(".lock", tr).onclick = () => { S.lock = S.lock === i ? -1 : i; renderSet(); };
    $(".rep", tr).onclick = () => replaceDialog(i);
    tr.addEventListener("dragstart", e => { e.dataTransfer.setData("text/plain", i); tr.classList.add("drag"); });
    tr.addEventListener("dragend", () => tr.classList.remove("drag"));
    tr.addEventListener("dragover", e => { e.preventDefault(); tr.classList.add("over"); });
    tr.addEventListener("dragleave", () => tr.classList.remove("over"));
    tr.addEventListener("drop", e => {
      e.preventDefault(); tr.classList.remove("over");
      const from = +e.dataTransfer.getData("text/plain"); if (from === i) return;
      const s = [...P.set]; const [m] = s.splice(from, 1); s.splice(i, 0, m); save(s);
    });
  });
  $$(".block tr[data-id]").forEach(tr => {
    $(".play", tr).onclick = () => player.play(tm[tr.dataset.id]);
    $(".finder", tr).onclick = () => reveal(trackPath(tm[tr.dataset.id]));
  });
}

async function renderArtists() {
  const box = $("#artPanel"); if (!box) return;
  const P = S.proj;
  const data = await api(`/api/artists?root=${encodeURIComponent(P.root)}`);
  let chosen = data.chosen.slice();
  const draw = () => {
    const q = ($("#artQ")?.value || "").toLowerCase();
    const pool = data.all.filter(a => !chosen.some(c => c.toLowerCase() === a.name.toLowerCase()) && (!q || a.name.toLowerCase().includes(q)));
    box.innerHTML = `<div class="row" style="margin-bottom:10px"><h2 style="margin:0">Artistes à mettre en avant</h2>
        <span class="muted">pour ce set · clique pour ajouter, glisse pour classer (le 1er compte le plus)</span><span class="spacer"></span>
        ${chosen.length ? `<button class="btn small" id="artClr">Tout retirer</button>` : ""}</div>
      <ol class="achosen">${chosen.map((a, i) => `<li draggable="true" data-i="${i}"><b>${i + 1}</b>${esc(a)}<button class="icon" data-rm="${i}" title="Retirer">✕</button></li>`).join("") || `<li class="muted empty-li">Aucun artiste choisi : le set suit l'IA (titres connus) ou seulement le tempo et l'harmonie.</li>`}</ol>
      <input type="text" id="artQ" placeholder="Filtrer les ${data.all.length} artistes…" value="${esc(q)}" style="width:260px;margin:10px 0 8px">
      <div class="apool">${pool.map(a => `<button class="chip achip" data-n="${esc(a.name)}">${esc(a.name)} <span>${a.count}</span></button>`).join("")}</div>`;
    const qi = $("#artQ"); qi.oninput = () => { const pos = qi.selectionStart; draw(); const n = $("#artQ"); n.focus(); n.setSelectionRange(pos, pos); };
    $$(".achip", box).forEach(b => b.onclick = () => { chosen.push(b.dataset.n); commit(); });
    $$("[data-rm]", box).forEach(b => b.onclick = () => { chosen.splice(+b.dataset.rm, 1); commit(); });
    if ($("#artClr")) $("#artClr").onclick = () => { chosen = []; commit(); };
    $$(".achosen li[draggable]", box).forEach(li => {
      li.addEventListener("dragstart", e => e.dataTransfer.setData("text/plain", li.dataset.i));
      li.addEventListener("dragover", e => { e.preventDefault(); li.classList.add("over"); });
      li.addEventListener("dragleave", () => li.classList.remove("over"));
      li.addEventListener("drop", e => { e.preventDefault(); const f = +e.dataTransfer.getData("text/plain"), t = +li.dataset.i; const [m] = chosen.splice(f, 1); chosen.splice(t, 0, m); commit(); });
    });
  };
  let timer = null;
  const commit = () => {
    draw();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      S.proj = await api("/api/artists", { root: P.root, chosen });
      toast("Artistes enregistrés. Clique sur « Regénérer le set » pour les appliquer.");
    }, 500);
  };
  draw();
}

async function replaceDialog(i) {
  const P = S.proj, tm = byId(), cur = tm[P.set[i]];
  const { ids } = await api(`/api/set/suggest?root=${encodeURIComponent(P.root)}&pos=${i}`);
  const c = modal(`<h2>Remplacer « ${esc(cur.title)} »</h2><p class="hint">Morceaux compatibles avec le précédent et le suivant (tempo et tonalité), les plus connus en premier.</p>
    <table><tbody>${ids.map(id => tm[id]).map(t => `<tr data-id="${t.id}"><td><button class="icon play">▶</button></td><td>${thumb(t)}</td><td class="name"><div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}</div></td>
    <td class="num">${t.bpm}</td><td>${keyChip(t.camelot)}</td><td>${stars(t.known)}</td><td><button class="btn small pick">Choisir</button></td></tr>`).join("") || `<tr><td>Aucun morceau compatible.</td></tr>`}</tbody></table>
    <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn" id="rC">Fermer</button></div>`);
  $("#rC", c).onclick = closeModal;
  $$("tr[data-id]", c).forEach(tr => {
    $(".play", tr).onclick = () => player.play(tm[tr.dataset.id]);
    $(".pick", tr).onclick = async () => { const s = [...P.set]; s[i] = tr.dataset.id; S.proj = await api("/api/set/save", { root: P.root, set: s }); closeModal(); renderSet(); };
  });
}

function drawChart(pr) {
  const cv = $("#chart"); if (!cv) return;
  const P = S.proj, tm = byId(), dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight; cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const pad = { l: 34, r: 10, t: 10, b: 20 };
  const total = Math.max(P.set_minutes, pr.duration, 1);
  const pts = []; let m = 0;
  for (const id of P.set) { const t = tm[id]; pts.push({ m, bpm: t.bpm, e: t.energy, len: t.play / 60 }); m += t.play / 60; }
  const all = pts.map(p => p.bpm).concat([pr.bpm_start, pr.bpm_end]);
  const lo = Math.floor(Math.min(...all) - 1), hi = Math.ceil(Math.max(...all) + 1);
  const X = v => pad.l + (W - pad.l - pad.r) * v / total, Y = v => pad.t + (H - pad.t - pad.b) * (1 - (v - lo) / (hi - lo));
  g.font = "10px -apple-system"; g.fillStyle = "#8b90a0"; g.strokeStyle = "#232733"; g.lineWidth = 1;
  for (let b = lo; b <= hi; b += Math.max(1, Math.round((hi - lo) / 5))) { g.beginPath(); g.moveTo(pad.l, Y(b)); g.lineTo(W - pad.r, Y(b)); g.stroke(); g.fillText(b, 4, Y(b) + 3); }
  for (let t = 0; t <= total; t += 15) g.fillText(fmtH(t), X(t) - 8, H - 5);
  // energy bars
  for (const p of pts) { const h = Math.max(2, (p.e + 2) * (H - pad.t - pad.b) / 4.5); g.fillStyle = "rgba(77,163,255,.22)"; g.fillRect(X(p.m) + 1, H - pad.b - h, Math.max(1, X(p.m + p.len) - X(p.m) - 2), h); }
  // target curve
  const shape = pr.shape, d = pr.bpm_end - pr.bpm_start;
  const keys = shape === "plateau" ? [[0, 0], [.25, .5], [.85, .8], [1, 1]] : shape === "lineaire" ? [[0, 0], [1, 1]] : [[0, 0], [.2, .25], [.45, .55], [.72, .75], [1, 1]];
  g.setLineDash([4, 4]); g.strokeStyle = "#8b90a0"; g.beginPath();
  keys.forEach(([f, v], i) => { const x = X(f * pr.duration), y = Y(pr.bpm_start + v * d); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); g.setLineDash([]);
  // bpm line
  g.strokeStyle = "#ff5a36"; g.lineWidth = 2; g.beginPath();
  pts.forEach((p, i) => { const x = X(p.m + p.len / 2), y = Y(p.bpm); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
  g.fillStyle = "#ff5a36"; pts.forEach(p => { g.beginPath(); g.arc(X(p.m + p.len / 2), Y(p.bpm), 2.5, 0, 7); g.fill(); });
}
window.addEventListener("resize", () => { if (S.view === "set") drawChart(Object.assign({ duration: 90, bpm_start: 122, bpm_end: 129, shape: "progressive" }, S.proj?.params)); });

// ------------------------------------------------------------------ export

function renderExport() {
  const P = S.proj, s = S.state?.settings || {};
  $("#title").textContent = `Export — ${P.name}`;
  $("#view").innerHTML = `
    ${P.set.length ? "" : `<div class="banner warn"><b>Pas encore de set.</b><span>L'export Rekordbox contiendra seulement la collection, et la clé USB a besoin d'un set.</span><span class="spacer"></span><button class="btn primary" id="xToSet">Créer le set →</button></div>`}
    <div class="cols2">
      <div class="panel"><h2>Rekordbox</h2>
        <p class="hint">Un fichier XML que Rekordbox importe avec tes playlists. Sans hot cues ni beatgrid : Rekordbox refait l'analyse (BPM et clé mesurés sont dans les commentaires).</p>
        <div class="params">
          <label class="check"><input type="checkbox" id="xM" checked> XML pour cet ordinateur</label>
          <label class="check"><input type="checkbox" id="xW"> XML pour Windows</label>
          <label class="field" id="xWr" style="margin-left:24px">Dossier sur le PC (le projet « ${esc(P.name)} » sera dedans)<input type="text" id="xWroot" value="${esc(s.windows_root || "C:/DJ Music")}"></label>
          <label class="check"><input type="checkbox" id="xU" checked> Playlists .m3u8</label>
          <label class="check"><input type="checkbox" id="xH" checked> Tracklist imprimable</label>
          <div><button class="btn primary" id="xGo">Exporter</button></div></div>
        <div id="xRes"></div>
        <details class="guide"><summary>Comment l'importer dans Rekordbox</summary><ol class="steps">
          <li>Morceaux déjà dans ta Collection avec une vieille analyse : sélectionne-les › clic droit › <b>Supprimer de la collection</b> (les fichiers restent sur le disque).</li>
          <li>Préférences › <b>Vue</b> › Mise en page : coche <b>rekordbox xml</b>.</li>
          <li>Préférences › <b>Avancé</b> › Base de données › rekordbox xml › <b>Bibliothèque importée</b> : choisis le XML (hors « Windows ») dans <code>${esc(P.root)}/Export</code>.</li>
          <li>Barre latérale › <b>rekordbox xml</b> › Playlists › <b>${esc(P.name)}</b> › clic droit sur chaque playlist › <b>Importer la playlist</b>.</li>
          <li>Après un nouvel export : icône <b>Actualiser</b> à côté de « rekordbox xml », supprime l'ancienne playlist, ré-importe. Si Rekordbox demande d'écraser les infos des morceaux : <b>Non</b>.</li>
          <li>Ne déplace pas le dossier <code>${esc(P.root)}</code> après l'import.</li></ol></details>
        <details class="guide"><summary>Clé USB complète (grilles, hot cues, formes d'onde)</summary><ol class="steps">
          <li>Importe le XML dans Rekordbox et laisse-le analyser les morceaux.</li>
          <li>Branche la clé : elle apparaît sous <b>Devices</b> dans la barre latérale de Rekordbox.</li>
          <li>Glisse la playlist sur la clé : Rekordbox copie les fichiers et sa base (dossier <code>PIONEER</code>).</li></ol></details></div>
      <div class="panel"><h2>Clé USB directe (CDJ / XDJ)</h2>
        <p class="hint">Sans passer par Rekordbox. Clé en <b>FAT32</b> ou <b>exFAT</b>.</p>
        <div id="usbList" class="muted">Recherche des clés…</div>
        <div class="params" style="margin-top:12px">
          <label class="check"><input type="radio" name="uMode" value="folders" checked> <span><b>Mode dossiers</b> (fiable) — un dossier par playlist, morceaux numérotés. Sur le lecteur : USB › FOLDER. Sans grille ni forme d'onde.</span></label>
          <label class="check"><input type="radio" name="uMode" value="rekordbox"> <span><b>Clé Rekordbox</b> <span class="chip warn">expérimental</span> — playlists, BPM, tonalité, grille et formes d'onde comme une clé Rekordbox, pour les lecteurs « Device Library » (XDJ-RX3, CDJ-3000, XDJ-XZ, CDJ-2000NXS2…).</span></label>
          <label class="check" id="uRepL" style="display:none;margin-left:24px"><input type="checkbox" id="uRep"> Cette clé a déjà une bibliothèque Rekordbox : la sauvegarder (renommée) et la remplacer</label>
          <label class="check"><input type="checkbox" id="uExt" checked> Ajouter la suite (set étendu)</label>
          <label class="check" id="uFlacL"><input type="checkbox" id="uFlac"> FLAC → AIFF (anciens lecteurs : CDJ-2000NXS, CDJ-900NXS, XDJ-RX…)</label></div>
        <div class="row" style="margin-top:10px"><button class="btn primary" id="uGo" disabled>Copier sur la clé</button><button class="btn" id="uRef">Actualiser</button><button class="btn" id="uEj" disabled>Éjecter</button></div>
        <div id="uRes"></div></div>
    </div>`;
  if ($("#xToSet")) $("#xToSet").onclick = () => go("set");
  const sync = () => $("#xWr").style.display = $("#xW").checked ? "" : "none"; sync(); $("#xW").onchange = sync;
  const gb = b => `${(b / 1e9).toFixed(1)} Go`;
  const loadUsb = async () => {
    const { volumes } = await api("/api/usb/volumes");
    S.usbVolumes = volumes;
    $("#usbList").innerHTML = volumes.length ? volumes.map((v, i) => `<label class="check" style="margin:4px 0"><input type="radio" name="usbv" value="${esc(v.path)}" ${i ? "" : "checked"}>
        <b>${esc(v.name)}</b> <span class="muted">${v.fs.toUpperCase() || "?"} · ${gb(v.free)} libres sur ${gb(v.size)}</span>
        ${v.pioneer_ok ? "" : `<span class="chip bad">format non lu par les CDJ : reformate en FAT32 ou exFAT</span>`}${v.has_rekordbox ? `<span class="chip warn">contient déjà une bibliothèque Rekordbox</span>` : ""}</label>`).join("")
      : "Aucune clé USB détectée. Branche-la puis clique sur « Actualiser ».";
    $("#uGo").disabled = $("#uEj").disabled = !volumes.length || !P.set.length;
    syncMode();
  };
  const vol = () => $("input[name=usbv]:checked")?.value;
  const mode = () => $("input[name=uMode]:checked").value;
  const syncMode = () => {
    const rb = mode() === "rekordbox";
    $("#uFlacL").style.display = rb ? "none" : "";
    const v = (S.usbVolumes || []).find(x => x.path === vol());
    $("#uRepL").style.display = rb && v?.has_rekordbox ? "" : "none";
    $("#uGo").textContent = rb ? "Créer la clé Rekordbox" : "Copier sur la clé";
  };
  $$("input[name=uMode]").forEach(r => r.onchange = syncMode);
  $("#usbList").addEventListener("change", syncMode);
  loadUsb(); $("#uRef").onclick = loadUsb;
  $("#uGo").onclick = async () => {
    const rb = mode() === "rekordbox";
    const r = await job("/api/usb/export", { root: P.root, volume: vol(), mode: mode(), replace: $("#uRep").checked,
      extended: $("#uExt").checked, convert_flac: $("#uFlac").checked }, rb ? "Création de la clé Rekordbox (analyse des formes d'onde)" : "Copie sur la clé USB");
    $("#uRes").innerHTML = rb
      ? `<div class="banner" style="margin-top:12px"><span class="chip ok">${r.total} morceaux</span><span>Clé Rekordbox créée et relue sans erreur. Sur la XDJ / CDJ : <b>USB › Playlist</b>.</span></div>`
      : `<div class="row" style="margin-top:12px"><span class="chip ok">${r.total} morceaux sur la clé</span><span class="muted">${r.copied} copiés (les autres y étaient déjà) · ${esc(r.folder)}</span></div>`;
    toast("Clé prête. Éjecte-la avant de la débrancher.");
  };
  $("#uEj").onclick = async () => { await api("/api/usb/eject", { volume: vol() }); toast("Clé éjectée, tu peux la retirer."); loadUsb(); };
  $("#xGo").onclick = async () => {
    if ($("#xW").checked) S.state = await api("/api/settings", { windows_root: $("#xWroot").value.trim() || "C:/DJ Music" });
    const r = await api("/api/export", { root: P.root, mac: $("#xM").checked, windows: $("#xW").checked, m3u: $("#xU").checked, html: $("#xH").checked });
    $("#xRes").innerHTML = `<div style="margin-top:14px">${r.files.map(f => `<div class="row" style="margin-bottom:6px"><span class="chip ok">créé</span><code>${esc(f.split("/").pop())}</code>
      <button class="btn small" data-f="${esc(f)}" data-a="reveal">Afficher dans ${FM()}</button>${f.endsWith(".html") ? `<button class="btn small" data-f="${esc(f)}" data-a="open">Ouvrir</button>` : ""}</div>`).join("")}</div>`;
    $$("#xRes button").forEach(b => b.onclick = () => api(`/api/${b.dataset.a}`, { path: b.dataset.f }));
    toast("Export terminé.");
  };
}

// ------------------------------------------------------------------ SoundCloud (tool)

async function scRun() {
  if (S.scRun) return;
  S.scRun = true; S.scPause = false;
  try {
    for (;;) {
      const it = S.scQ.find(x => x.st === "wait"); if (!it || S.scPause) break;
      while (jobBusy) await new Promise(r => setTimeout(r, 500));
      it.st = "run"; it.pct = 0; it.msg = ""; scDraw();
      try {
        const r = await job("/api/soundcloud", it.root ? { root: it.root, url: it.url } : { url: it.url, dest: it.dest }, "Téléchargement SoundCloud",
          j => { it.pct = j.total ? Math.round(100 * j.done / j.total) : 0; it.msg = j.message || ""; scDraw(); });
        it.count = r.count; it.files = r.files;
        if (!r.count) { it.st = "err"; it.msg = r.errors[0] || "Rien de nouveau (déjà téléchargé ?)"; }
        else {
          it.st = "ok"; it.msg = r.errors.length ? `${r.errors.length} non téléchargés` : "";
          if (it.root) {
            const known = new Set((S.rows || []).map(x => x.src));
            S.rows = (S.rows || []).concat(r.rows.filter(x => !known.has(x.src)));
            if (S.view === "import") renderImport();
          }
        }
      } catch (e) {
        it.st = "err"; it.msg = e.message === "cancelled" ? "arrêté" : e.message;
        if (e.message === "cancelled") S.scPause = true;
      }
      scDraw();
    }
  } finally {
    S.scRun = false; scDraw();
    if (!S.scQ.some(x => x.st === "wait" || x.st === "run")) {
      const n = S.scQ.filter(x => x.st === "ok" && x.root && !x.seen).length;
      S.scQ.forEach(x => x.seen = true);
      if (n) toast(`${n} téléchargement${n > 1 ? "s" : ""} terminé${n > 1 ? "s" : ""} : vérifie les noms puis importe.`);
    }
  }
}
function scDraw() {
  const box = $("#scQ"); if (!box || S.view !== "soundcloud") return;
  const Q = S.scQ, ST = { wait: "en attente", run: "téléchargement…", ok: "terminé", err: "erreur" };
  box.innerHTML = Q.length ? `<div class="row" style="margin:14px 0 6px"><b>File d'attente</b><span class="spacer"></span>
      ${S.scPause && Q.some(x => x.st === "wait") ? `<button class="btn small primary" id="scResume">Reprendre</button>` : ""}
      <button class="btn small" id="scClr" ${Q.some(x => x.st === "ok" || x.st === "err") ? "" : "disabled"}>Vider les terminés</button></div>` +
    Q.map((x, i) => `<div class="row" style="gap:10px;padding:4px 0;border-top:1px solid var(--line)">
      <span class="chip ${x.st === "ok" ? "ok" : x.st === "err" ? "bad" : ""}" style="min-width:96px">${x.st === "ok" ? `terminé (${x.count} morceau${x.count > 1 ? "x" : ""})` : x.st === "run" ? `téléchargement… ${x.pct || 0} %` : ST[x.st]}</span>
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.url)}">${esc(x.url.replace(/^https?:\/\/(www\.|m\.)?soundcloud\.com\//i, ""))}${x.st === "err" || x.st === "ok" && x.msg ? ` <span class="muted">· ${esc(x.msg)}</span>` : ""}</span>
      ${x.st === "ok" && !x.root && x.files?.length ? `<button class="btn small" data-show="${i}">Afficher</button>` : ""}
      ${x.st === "wait" ? `<button class="icon" data-rm="${i}" title="Retirer">✕</button>` : ""}</div>`).join("") : "";
  $$("[data-rm]", box).forEach(b => b.onclick = () => { S.scQ.splice(+b.dataset.rm, 1); scDraw(); });
  $$("[data-show]", box).forEach(b => b.onclick = () => api("/api/reveal", { path: S.scQ[+b.dataset.show].files[0] }));
  const c = $("#scClr", box); if (c) c.onclick = () => { S.scQ = S.scQ.filter(x => x.st === "wait" || x.st === "run"); scDraw(); };
  const r = $("#scResume", box); if (r) r.onclick = scRun;
}

function renderSoundcloud() {
  $("#title").textContent = "SoundCloud";
  const P = S.proj; S.scQ = S.scQ || [];
  $("#view").innerHTML = `
    <div class="panel"><h2>Télécharger depuis SoundCloud</h2>
      <p class="hint">Colle un ou plusieurs liens (un par ligne) : morceau, playlist (set), profil ou likes (<code>…/likes</code>). L'app prend le fichier original quand l'artiste autorise le téléchargement, sinon la meilleure qualité d'écoute (souvent 128–160 kbps). Pochette et infos sont ajoutées, et les formats que Rekordbox ne lit pas sont convertis. Les liens sont téléchargés l'un après l'autre.</p>
      <div class="row" style="align-items:flex-start"><textarea id="scUrl" rows="3" placeholder="https://soundcloud.com/…" style="flex:1;min-width:320px"></textarea>
        <button class="btn primary" id="scGo">Ajouter à la file</button></div>
      <div class="row" style="margin-top:12px">
        <label class="check"><input type="radio" name="scDest" value="project" ${P ? "checked" : "disabled"}> Dans le projet ${P ? `« ${esc(P.name)} » (puis vérifier les noms et importer)` : "(choisis d'abord un projet)"}</label>
        <label class="check"><input type="radio" name="scDest" value="folder" ${P ? "" : "checked"}> Dans un dossier : <code id="scFolder">${esc(S.scFolder || (S.state?.default_root || "~/Music") + "/SoundCloud")}</code></label>
        <button class="btn small" id="scPick">Changer…</button></div>
      <div id="scQ"></div></div>`;
  $("#scPick").onclick = async () => { const [f] = await pickFolders(false); if (f) { S.scFolder = f; $("#scFolder").textContent = f; $("input[value=folder]").checked = true; } };
  $("#scGo").onclick = () => {
    const urls = ($("#scUrl").value.match(/https?:\/\/[^\s,;]+/g) || []).filter((u, i, a) => a.indexOf(u) === i);
    if (!urls.length) return toast("Colle un lien SoundCloud.");
    const bad = urls.filter(u => !/^https?:\/\/([\w-]+\.)?soundcloud\.com\//i.test(u) && !/^https?:\/\/on\.soundcloud\.com\//i.test(u));
    if (bad.length) return toast(`Lien non SoundCloud : ${bad[0]}`, true);
    const toProject = $("input[name=scDest]:checked").value === "project", dest = $("#scFolder").textContent;
    urls.forEach(url => S.scQ.push({ url, st: "wait", ...(toProject ? { root: P.root } : { dest }) }));
    $("#scUrl").value = ""; scDraw(); scRun();
  };
  scDraw();
}

// ------------------------------------------------------------------ Spotify (tool)

async function renderSpotify() {
  $("#title").textContent = "Spotify";
  const P = S.proj, s = (S.state || await api("/api/state")).settings;
  const st = await api("/api/spotify/status");
  const hasSet = P && P.set.length;
  $("#view").innerHTML = `
    ${st.connected ? `<div class="banner"><span class="sp-ok">●</span><b>Connecté à Spotify : ${esc(st.name)}</b><span class="spacer"></span><button class="btn small" id="spOut">Déconnecter</button></div>`
    : `<div class="panel"><h2>Connecter Spotify (une seule fois)</h2>
      <p class="hint">Spotify demande une petite « app développeur » gratuite (2 min) :</p>
      <ol class="steps"><li>Va sur <code>developer.spotify.com/dashboard</code>, connecte-toi, « Create app ».</li>
        <li>Nom : SetBuilder. <b>Redirect URI</b> : <code id="spRU">${esc(st.redirect_uri)}</code> <button class="btn small" id="spCopy">Copier</button>. Coche « Web API », enregistre.</li>
        <li>Copie le <b>Client ID</b> de l'app ici :</li></ol>
      <div class="row"><input type="text" id="spId" value="${esc(s.spotify_client_id || "")}" placeholder="Client ID" style="width:340px">
        <button class="btn primary" id="spIn">Connecter Spotify</button><button class="btn" id="spRef">Actualiser</button></div>
      ${st.error ? `<div class="muted" style="margin-top:8px;color:var(--warn)">${esc(st.error)}</div>` : ""}</div>`}
    <div class="panel"><h2>Ton set en playlist Spotify</h2>
      ${hasSet ? `<p class="hint">Recrée « ${esc(P.name)} » sur Spotify, dans l'ordre du set. Si un remix n'existe pas sur Spotify, l'app met la version originale du titre (jamais le remix de quelqu'un d'autre) et te dit lesquels.</p>
      <div class="row">
        <label class="check"><input type="radio" name="spsW" value="set" checked> Le set (${fmtH(P.set_minutes)})</label>
        <label class="check"><input type="radio" name="spsW" value="extended" ${P.blocks.length > 1 ? "" : "disabled"}> Le set + la suite</label>
        <label class="check"><input type="checkbox" id="spsExt" checked> Version originale en extended quand elle existe</label>
        <label class="check"><input type="checkbox" id="spsPub"> Publique</label>
        <button class="btn primary" id="spsGo" ${st.connected ? "" : "disabled"}>Créer la playlist du set</button></div>
      <div id="spsRes"></div>`
      : `<p class="hint" style="margin:0">${P ? "Génère d'abord un set (étape 3)." : "Choisis un projet avec un set pour le recréer sur Spotify."}</p>`}</div>
    <div class="panel"><h2>Ajouter des morceaux similaires</h2>
      <p class="hint">Trouve des morceaux du même style et d'un tempo proche, à partir d'une de tes playlists Spotify ou du set de ton projet, et les ajoute sur ton Spotify, en version extended quand elle existe. Les morceaux déjà présents sont ignorés.</p>
      <div class="params" style="max-width:720px">
        <div class="row">
          <label class="check"><input type="radio" name="spFrom" value="pl" ${hasSet ? "" : "checked"}> Une playlist Spotify</label>
          <label class="check"><input type="radio" name="spFrom" value="set" ${hasSet ? "checked" : "disabled"}> Le set ${P ? `« ${esc(P.name)} »` : ""} ${hasSet ? "" : "(aucun set généré)"}</label></div>
        <div id="spPlBox" class="row">
          <input type="text" id="spUrl" placeholder="https://open.spotify.com/playlist/…" style="flex:1;min-width:300px" value="${esc(S.spUrl || "")}">
          <label class="field">Ajouter dans<select id="spTgt"><option value="same">Cette même playlist</option><option value="new">Une nouvelle playlist</option></select></label></div>
        <div class="row">
          <label class="field">Nombre<select id="spN"><option>10</option><option selected>20</option><option>40</option><option>60</option><option>100</option></select></label>
          <label class="field">Source<select id="spSrc"><option value="auto">IA + Deezer</option><option value="ia">IA seulement</option><option value="deezer">Deezer seulement (sans IA)</option></select></label>
          <label class="check" id="spSetL" style="margin-top:18px"><input type="checkbox" id="spSet"> Mettre aussi les morceaux du set</label>
          <label class="check" style="margin-top:18px"><input type="checkbox" id="spPub"> Nouvelle playlist publique</label></div>
        <div><button class="btn primary" id="spGo" ${st.connected ? "" : "disabled"}>Ajouter les morceaux</button></div></div>
      <div id="spRes"></div></div>`;
  const sync = () => {
    const pl = $("input[name=spFrom]:checked").value === "pl";
    $("#spPlBox").style.display = pl ? "" : "none";
    $("#spSetL").style.display = pl ? "none" : "";
  };
  $$("input[name=spFrom]").forEach(r => r.onchange = sync); sync();
  if ($("#spOut")) $("#spOut").onclick = async () => { await api("/api/spotify/logout", {}); renderSpotify(); };
  if ($("#spCopy")) $("#spCopy").onclick = () => { navigator.clipboard?.writeText($("#spRU").textContent); toast("Redirect URI copiée."); };
  if ($("#spRef")) $("#spRef").onclick = () => renderSpotify();
  if ($("#spIn")) $("#spIn").onclick = async () => {
    const id = $("#spId").value.trim(); if (!id) return toast("Colle le Client ID de ton app Spotify.", true);
    S.state = await api("/api/settings", { spotify_client_id: id });
    await api("/api/spotify/login", {});
    toast("Autorise SetBuilder dans ton navigateur, puis clique sur « Actualiser ».");
  };
  if ($("#spsGo")) $("#spsGo").onclick = async () => {
    const r = await job("/api/spotify/set-playlist", { root: P.root, which: $("input[name=spsW]:checked").value,
      prefer_extended: $("#spsExt").checked, public: $("#spsPub").checked }, "Spotify : playlist du set");
    const list = (title, items, cls) => items.length ? `<details class="guide" ${cls === "bad" ? "open" : ""}><summary>${title} (${items.length})</summary><ol style="margin:6px 0 0;padding-left:20px;font-size:12.5px;color:var(--muted)">${items.map(x => `<li>${esc(x)}</li>`).join("")}</ol></details>` : "";
    $("#spsRes").innerHTML = `<div class="row" style="margin-top:14px"><span class="chip ok">${r.updated ? "playlist mise à jour" : "playlist créée"} · ${r.total} morceaux</span>
        <span class="chip ok">${r.exact.length} bonne version</span>${r.original.length ? `<span class="chip warn">${r.original.length} en version originale</span>` : ""}${r.missing.length ? `<span class="chip bad">${r.missing.length} introuvables</span>` : ""}
        <button class="btn small" id="spsOpen">Ouvrir dans Spotify</button></div>
      ${list("Remix absent de Spotify, remplacé par l'original", r.original, "warn")}${list("Introuvables sur Spotify", r.missing, "bad")}${list("Trouvés dans la bonne version", r.exact)}`;
    $("#spsOpen").onclick = () => api("/api/open", { path: r.url });
  };
  $("#spGo").onclick = async () => {
    const pl = $("input[name=spFrom]:checked").value === "pl", url = $("#spUrl").value.trim();
    if (pl && !url) return toast("Colle le lien de ta playlist Spotify.", true);
    S.spUrl = url;
    const r = await job("/api/spotify/similar", { root: pl ? null : P.root, playlist: pl ? url : "", target: $("#spTgt").value,
      count: +$("#spN").value, source: $("#spSrc").value, include_set: $("#spSet").checked, public: $("#spPub").checked }, "Spotify : morceaux similaires");
    $("#spRes").innerHTML = `<div class="row" style="margin-top:14px"><span class="chip ok">${r.similar.length} morceaux ajoutés</span><button class="btn small" id="spOpen">Ouvrir dans Spotify</button></div>
      <ol style="margin:10px 0 0;padding-left:20px;font-size:12.5px;color:var(--muted)">${r.similar.map(x => `<li>${esc(x)}</li>`).join("")}</ol>`;
    $("#spOpen").onclick = () => api("/api/open", { path: r.url });
  };
}

// ------------------------------------------------------------------ Découverte (tool)

const compact = n => { n = +n || 0; return n >= 1e6 ? `${(n / 1e6).toFixed(1).replace(".", ",")} M` : n >= 1e3 ? `${(n / 1e3).toFixed(1).replace(".", ",")} k` : String(n); };
const ageFr = d => {
  d = Math.round(d || 0);
  if (d < 1) return "aujourd'hui"; if (d < 14) return `il y a ${d} j`;
  if (d < 60) return `il y a ${Math.round(d / 7)} sem.`; if (d < 730) return `il y a ${Math.round(d / 30)} mois`;
  return `il y a ${Math.round(d / 365)} ans`;
};
const discAudio = (() => {
  const a = new Audio(); a.preload = "none"; let cur = null;
  const mark = (url, st) => $$("#dList .dprev").forEach(b => { const on = b.dataset.url === url; b.textContent = on ? (st === "load" ? "…" : st === "play" ? "❚❚" : "▶") : "▶"; });
  a.addEventListener("playing", () => mark(cur, "play")); a.addEventListener("pause", () => mark(cur, "pause"));
  a.addEventListener("waiting", () => mark(cur, "load"));
  a.addEventListener("ended", () => mark(cur, "pause"));
  a.addEventListener("error", () => { if (cur) { toast("Aperçu indisponible.", true); mark(null); cur = null; } });
  return { toggle(url) {
    if (cur === url && !a.paused) return a.pause();
    if (cur === url && a.src) return a.play().catch(() => {});
    [$("#a1"), $("#a2")].forEach(x => x.pause());
    cur = url; a.src = `/api/discover/preview?url=${encodeURIComponent(url)}`; mark(url, "load"); a.play().catch(() => {});
  }, stop() { a.pause(); cur = null; mark(null); } };
})();

function renderDiscover() {
  $("#title").textContent = "Découverte";
  const P = S.proj;
  if (!P) { $("#view").innerHTML = `<div class="empty"><b>Aucun projet ouvert</b>Choisis ou crée d'abord un projet.</div>`; return; }
  S.dSeeds = S.dSeeds || []; S.dOpt = S.dOpt || { hide: true, count: 40, showOff: false };
  const KINDS = [["remix", "Remix"], ["edit", "Edit"], ["bootleg", "Bootleg"], ["flip", "Flip"], ["mashup", "Mashup"]];
  S.dOpt.f = Object.assign({ era: "all", original: "all", uploaded: 0, kinds: KINDS.map(k => k[0]) }, S.dOpt.f || {});
  const F = S.dOpt.f;
  const opts = (list, cur) => list.map(([v, l]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${l}</option>`).join("");
  $("#view").innerHTML = `
    <div class="panel"><h2>Morceaux de départ</h2>
      <p class="hint">Jusqu'à 3 morceaux servent de référence pour trouver des remixes et versions peu connus qui montent sur SoundCloud.</p>
      <div id="dSeeds" class="row" style="margin-bottom:12px"></div>
      <div class="row" style="align-items:flex-start">
        <div style="flex:1;min-width:300px"><div class="row">
          <input type="text" id="dUrl" placeholder="Lien SoundCloud d'un morceau" style="flex:1"><button class="btn" id="dAddUrl">Ajouter</button></div>
          <div class="row" style="margin-top:8px"><button class="btn" id="dSet">Depuis le set</button></div><div id="dSetList" class="dlib"></div></div>
        <div style="flex:1;min-width:300px"><input type="text" id="dQ" placeholder="Chercher dans la bibliothèque (artiste, titre)" style="width:100%">
          <div id="dLib" class="dlib"></div></div></div></div>
    <div class="panel"><div class="row">
      <label class="check"><input type="checkbox" id="dHide" ${S.dOpt.hide ? "checked" : ""}> Masquer ce qui est déjà sorti officiellement (vérifié sur Deezer)</label>
      <label class="check"><input type="checkbox" id="dShowOff" ${S.dOpt.showOff ? "checked" : ""}> Afficher aussi les remix officiels (liste séparée)</label>
      <label class="field" style="flex-direction:row;align-items:center;gap:8px">Résultats<select id="dN">${[20, 40, 60].map(n => `<option ${S.dOpt.count === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <span class="spacer"></span><button class="btn primary" id="dGo">Chercher</button></div>
      <div class="row dpresets" id="dPre"></div>
      <div class="row dfilters">
        <label class="field">Époque de l'original<select id="fEra">${opts([["all", "Toutes"], ["90s", "Années 90"], ["2000s", "Années 2000"], ["2010s", "Années 2010"], ["recent", "Récent"]], F.era)}</select></label>
        <label class="field">Original<select id="fOrig">${opts([["all", "Tous"], ["hits", "Tubes connus"], ["niche", "Niche"]], F.original)}</select></label>
        <label class="field">Remix mis en ligne<select id="fUp">${opts([[0, "Peu importe"], [30, "< 1 mois"], [180, "< 6 mois"], [365, "< 1 an"]], F.uploaded)}</select></label>
        <div class="field">Types<div class="dkinds" id="fKinds"></div></div>
        <span class="muted" id="dHint"></span></div></div>
    <div id="dRes"></div>`;
  const seedBox = () => {
    $("#dSeeds").innerHTML = S.dSeeds.map((d, i) => `<span class="dseed">${d.cover ? `<img class="thumb" src="${esc(d.cover)}" onerror="this.outerHTML='<div class=\\'thumb none\\'>♪</div>'">` : `<div class="thumb none">♪</div>`}
      <span class="dt" title="${esc(d.label)}">${esc(d.label)}</span><button class="icon" data-rm="${i}" title="Retirer">✕</button></span>`).join("")
      || `<span class="muted">Aucun morceau choisi.</span>`;
    $$("#dSeeds [data-rm]").forEach(b => b.onclick = () => { S.dSeeds.splice(+b.dataset.rm, 1); seedBox(); libBox(); });
  };
  const addSeed = d => { if (S.dSeeds.length >= 3) return toast("3 morceaux au maximum."); S.dSeeds.push(d); seedBox(); libBox(); };
  const libBox = () => {
    const q = $("#dQ").value.trim().toLowerCase(); S.dQ = $("#dQ").value;
    if (!q) { $("#dLib").innerHTML = ""; return; }
    const used = new Set(S.dSeeds.map(d => d.id).filter(Boolean));
    const hits = P.tracks.filter(t => !used.has(t.id) && `${t.artist} ${t.title}`.toLowerCase().includes(q)).slice(0, 8);
    $("#dLib").innerHTML = hits.map(t => `<div class="dhit" data-id="${t.id}">${thumb(t)}<div class="name"><div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}</div></div></div>`).join("") || `<div class="muted" style="padding:6px 2px">Aucun résultat.</div>`;
    $$("#dLib .dhit").forEach(h => h.onclick = () => {
      const t = P.tracks.find(x => String(x.id) === h.dataset.id); if (!t) return;
      addSeed({ id: t.id, label: `${t.artist} - ${t.title}`, cover: t.has_cover ? coverUrl(t) : "" });
    });
  };
  $("#dQ").value = S.dQ || ""; $("#dQ").oninput = libBox;
  $("#dSet").onclick = () => {
    const box = $("#dSetList"); if (box.innerHTML) { box.innerHTML = ""; return; }
    const tm = byId(), ids = [...new Set([...P.set, ...(P.blocks || []).slice(1).flatMap(b => b.ids)])].map(i => tm[i]).filter(Boolean);
    if (!P.set.length) { box.innerHTML = `<div class="muted" style="padding:6px 2px">Aucun set généré.</div>`; return; }
    const used = new Set(S.dSeeds.map(d => d.id).filter(Boolean));
    box.innerHTML = `<div style="max-height:260px;overflow:auto">` + ids.filter(t => !used.has(t.id)).map(t => `<div class="dhit" data-id="${t.id}">${thumb(t)}<div class="name"><div class="t">${esc(t.title)}</div><div class="a">${esc(t.artist)}</div></div></div>`).join("") + `</div>`;
    $$("#dSetList .dhit").forEach(h => h.onclick = () => {
      const t = tm[h.dataset.id]; if (!t) return;
      addSeed({ id: t.id, label: `${t.artist} - ${t.title}`, cover: t.has_cover ? coverUrl(t) : "" }); h.remove();
    });
  };
  $("#dAddUrl").onclick = () => {
    const url = $("#dUrl").value.trim(); if (!url) return;
    if (!/^https?:\/\/(www\.|m\.|on\.)?soundcloud\.com\//i.test(url)) return toast("Lien SoundCloud invalide.", true);
    addSeed({ url, label: url.replace(/^https?:\/\/(www\.|m\.)?soundcloud\.com\//i, "") }); $("#dUrl").value = "";
  };
  $("#dUrl").addEventListener("keydown", e => { if (e.key === "Enter") $("#dAddUrl").click(); });
  $("#dHide").onchange = e => S.dOpt.hide = e.target.checked;
  $("#dShowOff").onchange = e => S.dOpt.showOff = e.target.checked;
  $("#dN").onchange = e => S.dOpt.count = +e.target.value;
  const kindBox = () => {
    $("#fKinds").innerHTML = KINDS.map(([k, l]) => `<button type="button" class="kchip ${F.kinds.includes(k) ? "on" : ""}" data-k="${k}">${l}</button>`).join("");
    $$("#fKinds .kchip").forEach(b => b.onclick = () => {
      const k = b.dataset.k, on = F.kinds.includes(k);
      if (on && F.kinds.length === 1) return;
      F.kinds = on ? F.kinds.filter(x => x !== k) : F.kinds.concat(k);
      kindBox(); if (S.dRes) drawRes();
    });
  };
  const hint = () => {
    const f = S.dRes && S.dRes._f;
    $("#dHint").textContent = f && (f.era !== F.era || f.original !== F.original) ? "Relance la recherche pour appliquer" : "";
  };
  const sync = () => { $("#fEra").value = F.era; $("#fOrig").value = F.original; $("#fUp").value = String(F.uploaded); kindBox(); hint(); if (S.dRes) drawRes(); };
  $("#fEra").onchange = e => { F.era = e.target.value; hint(); };
  $("#fOrig").onchange = e => { F.original = e.target.value; hint(); };
  $("#fUp").onchange = e => { F.uploaded = +e.target.value; if (S.dRes) drawRes(); };
  // the user's own presets: current filters saved under a name, kept in the settings
  const savePresets = async list => { S.state = await api("/api/settings", { discover_presets: list }); preBox(); };
  const preBox = () => {
    const list = S.state.settings.discover_presets || [];
    $("#dPre").innerHTML = `<span class="muted">Mes préréglages</span>` + list.map((p, i) =>
      `<span class="dpre"><button class="btn small" data-pre="${i}">${esc(p.name)}</button><button class="icon" data-pdel="${i}" title="Supprimer">✕</button></span>`).join("")
      + `<button class="btn small" id="dPreAdd">+ Enregistrer ces filtres</button>`;
    $$("#dPre [data-pre]").forEach(b => b.onclick = () => { const { name, ...f } = list[+b.dataset.pre]; Object.assign(F, f); sync(); });
    $$("#dPre [data-pdel]").forEach(b => b.onclick = () => savePresets(list.filter((_, i) => i !== +b.dataset.pdel)));
    $("#dPreAdd").onclick = () => {
      const c = modal(`<h2>Enregistrer ces filtres</h2><input type="text" id="pName" placeholder="Nom, ex. Throwback reggaeton" style="width:100%">
        <div class="row" style="margin-top:14px"><span class="spacer"></span><button class="btn" id="pNo">Annuler</button><button class="btn primary" id="pOk">Enregistrer</button></div>`);
      const ok = () => { const name = $("#pName", c).value.trim(); if (!name) return; closeModal();
        savePresets([...list.filter(p => p.name !== name), { name, era: F.era, original: F.original, uploaded: F.uploaded, kinds: [...F.kinds] }]); };
      $("#pNo", c).onclick = closeModal; $("#pOk", c).onclick = ok;
      $("#pName", c).onkeydown = e => { if (e.key === "Enter") ok(); }; $("#pName", c).focus();
    };
  };
  preBox();
  kindBox();
  $("#dGo").onclick = async () => {
    if (!S.dSeeds.length) return toast("Ajoute au moins un morceau de départ.", true);
    const seeds = S.dSeeds.map(d => d.id !== undefined ? { id: d.id } : { url: d.url });
    discAudio.stop();
    const r = await job("/api/discover", { root: P.root, seeds, count: S.dOpt.count, hide_spotify: S.dOpt.hide, show_official: !!S.dOpt.showOff, filters: { ...F, kinds: [...F.kinds] } }, "Découverte SoundCloud");
    r._f = { era: F.era, original: F.original }; S.dRes = r; S.dSel = new Set(); drawRes(); hint();
  };
  const dl = async it => {
    const r = await job("/api/soundcloud", { root: P.root, url: it.url }, "Téléchargement SoundCloud");
    if (!r.count) { toast(r.errors?.[0] || "Rien de nouveau à télécharger (déjà téléchargé ?).", !!r.errors?.length); return false; }
    const known = new Set((S.rows || []).map(x => x.src));
    S.rows = (S.rows || []).concat(r.rows.filter(x => !known.has(x.src)));
    it.added = true; S.dSel.delete(it.url); return true;
  };
  const rowHtml = (it, i, off) => {
        const tr = Math.max(0, Math.min(1, +it.trend > 1 ? it.trend / 100 : +it.trend || 0));
        const bd = (it.badges || []).map(b => `<span class="chip ${/hausse/i.test(b) ? "rising" : /gratuit/i.test(b) ? "free" : ""}">${esc(b)}</span>`).join("")
          + (it.free_download && !(it.badges || []).some(b => /gratuit/i.test(b)) ? `<span class="chip free">Téléchargement gratuit</span>` : "");
        const KL = { remix: "Remix", edit: "Edit", bootleg: "Bootleg", flip: "Flip", mashup: "Mashup" }, PL = { hit: "tube", known: "connu", niche: "niche" };
        const meta = (it.kind && KL[it.kind] && !(it.badges || []).some(b => b.toLowerCase() === KL[it.kind].toLowerCase()) ? `<span class="chip remix">${KL[it.kind]}</span>` : "")
          + (it.orig_year ? `<span class="chip meta">original ${it.orig_year}</span>` : "") + (PL[it.orig_pop] ? `<span class="chip meta">${PL[it.orig_pop]}</span>` : "");
        return `<div class="drow" data-i="${i}">
          <input type="checkbox" class="dsel" ${S.dSel.has(it.url) ? "checked" : ""} ${it.added ? "disabled" : ""}>
          ${it.cover ? `<img class="thumb big" loading="lazy" src="${esc(it.cover)}" onerror="this.outerHTML='<div class=\\'thumb big none\\'>♪</div>'">` : `<div class="thumb big none">♪</div>`}
          <button class="icon dprev" data-url="${esc(it.url)}" title="Écouter un extrait">▶</button>
          <div class="dinfo"><div class="t" title="${esc(it.title)}">${esc(it.title)}</div>
            <div class="a">${esc(it.artist)} · ${fmt(it.duration)} · ▶ ${compact(it.plays)} · ♥ ${compact(it.likes)} · ${ageFr(it.age_days)}</div>
            <div>${off ? `<span class="chip warn">Officiel</span>` : ""}${bd}${meta}</div>${it.reason ? `<div class="reason muted">${esc(it.reason)}</div>` : ""}</div>
          <div class="ebar" title="Tendance ${Math.round(tr * 100)} %"><i style="width:${Math.max(4, tr * 100)}%"></i></div>
          <div class="dact">${it.added ? `<span class="chip ok">Ajouté</span>` : `<button class="btn small primary ddl">Télécharger</button>`}
            <button class="btn small dopen">Ouvrir</button><button class="btn small dno" title="Ne plus proposer ce morceau">Pas pour moi</button></div></div>`;
  };
  function drawRes() {
    const r = S.dRes; if (!r) { $("#dRes").innerHTML = ""; return; }
    const st = r.stats || {};
    const quick = it => (!F.uploaded || it.age_days == null || it.age_days <= F.uploaded) && (!it.kind || F.kinds.includes(it.kind));
    const items = (r.results || []).filter(quick), offs = (r.official_results || []).filter(quick);
    const SKIP = { era: "époque de l'original", original: "popularité de l'original", uploaded: "date de mise en ligne", kinds: "types" };
    const skipped = (st.filters_skipped || []).map(k => SKIP[k] || k);
    $("#dRes").innerHTML = `<div class="panel"><div class="row" style="margin-bottom:10px"><h2 style="margin:0">${items.length} résultats</h2>
        <span class="muted">${st.candidates ?? "?"} candidats analysés · ${st.kept ?? items.length} gardés · IA : ${st.ai ? "oui" : "non"}${st.official_checked == null ? "" : ` · ${st.official_checked ? `Officiels vérifiés sur Deezer : ${st.official_checked}` : items.length ? "Deezer n'a pas répondu, rien de masqué" : ""}`}</span><span class="spacer"></span>
        <button class="btn" id="dAll" ${S.dSel.size ? "" : "disabled"}>Tout télécharger (${S.dSel.size})</button></div>
      ${skipped.length ? `<div class="muted dskip">Filtres non appliqués sans IA : ${esc(skipped.join(", "))}</div>` : ""}
      <div id="dList">${items.map((it, i) => rowHtml(it, i, false)).join("") || `<div class="empty"><b>Aucun résultat</b>Essaie d'autres morceaux de départ.</div>`}</div>
      ${offs.length ? `<details class="doff" ${S.dOffOpen ? "open" : ""}><summary>Remix officiels (${offs.length})</summary><div id="dOff">${offs.map((it, i) => rowHtml(it, i, true)).join("")}</div></details>` : ""}
      ${(r.queries || []).length ? `<details class="guide"><summary>Recherches utilisées (${r.queries.length})</summary><ul class="dq">${r.queries.map(q => `<li>${esc(q)}</li>`).join("")}</ul></details>` : ""}</div>`;
    const od = $("#dRes .doff"); if (od) od.ontoggle = () => S.dOffOpen = od.open;
    const bind = (sel, list, key) => $$(sel + " .drow").forEach(row => {
      const it = list[+row.dataset.i];
      $(".dsel", row).onchange = e => { e.target.checked ? S.dSel.add(it.url) : S.dSel.delete(it.url); $("#dAll").textContent = `Tout télécharger (${S.dSel.size})`; $("#dAll").disabled = !S.dSel.size; };
      $(".dprev", row).onclick = () => discAudio.toggle(it.url);
      $(".dopen", row).onclick = () => api("/api/open", { path: it.url });
      if ($(".ddl", row)) $(".ddl", row).onclick = async () => { if (await dl(it)) { toast("Ajouté à la liste d'import."); drawRes(); } };
      $(".dno", row).onclick = async () => {
        await api("/api/discover/ignore", { root: P.root, url: it.url });
        S.dRes[key] = (S.dRes[key] || []).filter(x => x !== it); S.dSel.delete(it.url); drawRes();
      };
    });
    bind("#dList", items, "results"); bind("#dOff", offs, "official_results");
    $("#dAll").onclick = async () => {
      let n = 0;
      for (const it of items.concat(offs).filter(x => S.dSel.has(x.url) && !x.added)) { try { if (await dl(it)) n++; } catch { break; } }
      drawRes(); if (n) toast(`${n} morceaux ajoutés : vérifie les noms dans « Importer ».`);
    };
  }
  seedBox(); libBox(); if (S.dRes) { S.dSel = S.dSel || new Set(); drawRes(); }
}

// ------------------------------------------------------------------ settings

async function renderSettings() {
  $("#title").textContent = "Réglages";
  S.state = await api("/api/state");
  const s = S.state.settings, ai = S.state.ai;
  const tool = (name, label, st, extra) => `
    <div class="panel"><h2>${label}</h2>${extra}
      <div class="row">${!st.installed ? `<span class="chip bad">non installé</span><button class="btn primary" data-act="install" data-n="${name}">Installer</button>`
        : st.logged_in ? `<span class="chip ok">connecté${st.method ? " · " + esc(st.method) : ""}</span><button class="btn" data-act="test" data-n="${name}">Tester</button><button class="btn" data-act="login" data-n="${name}">Changer de compte</button>`
        : `<span class="chip warn">non connecté</span><button class="btn primary" data-act="login" data-n="${name}">Connecter</button>`}
        <button class="btn sRef">Actualiser</button></div></div>`;
  $("#view").innerHTML = `
    <div class="panel"><h2>IA — optionnelle et économe</h2>
      <p class="hint">L'IA ne sert qu'à 3 choses, toujours via un bouton ✦ : corriger les noms que les règles n'arrivent pas à lire, noter si un titre est connu, et proposer des morceaux similaires pour Spotify. L'analyse BPM / tonalité, les pochettes et la création du set n'utilisent pas l'IA. Seules les lignes douteuses sont envoyées, avec le modèle le plus léger, et chaque réponse est mémorisée pour ne jamais être redemandée.</p>
      <label class="field" style="max-width:460px">Utiliser<select id="sProv">
        <option value="auto">Automatique (Claude, sinon Codex, sinon clé API)</option>
        <option value="claude">Mon abonnement Claude</option><option value="codex">Mon abonnement ChatGPT (Codex)</option><option value="api">Une clé API Claude</option></select></label>
      <div class="row" style="margin-top:12px;align-items:flex-start">
        <label class="field" style="width:300px">Noms et « titres connus »<select id="sLvN">
          <option value="eco">Économique — Haiku (recommandé)</option><option value="equilibre">Équilibré — Sonnet</option><option value="max">Maximum — Opus</option></select></label>
        <label class="field" style="width:300px">Morceaux similaires (Spotify)<select id="sLvS">
          <option value="eco">Économique — Haiku</option><option value="equilibre">Équilibré — Sonnet (recommandé)</option><option value="max">Maximum — Opus</option></select></label></div>
      <div class="row" style="margin-top:12px;align-items:flex-start">
        <label class="field" style="width:300px">Découverte – idées de recherche<select id="sLvD">
          <option value="eco">Économique — Haiku</option><option value="equilibre">Équilibré — Sonnet</option><option value="max">Maximum — Opus</option></select></label>
        <label class="field" style="width:300px">Découverte – tri des résultats<select id="sLvDS">
          <option value="eco">Économique — Haiku</option><option value="equilibre">Équilibré — Sonnet</option><option value="max">Maximum — Opus</option></select></label></div>
      <p class="hint" style="margin:8px 0 0">Les noms et notes sont des tâches simples : Haiku suffit. Pour les morceaux similaires, il faut une vraie culture musicale : Sonnet invente moins de titres. Avec Codex, le niveau règle l'effort de réflexion (modèle ci-dessous).</p>
      <div class="muted" style="margin-top:8px">État : ${ai.ok ? `<span class="chip ok">prête</span> via ${esc(ai.label)}` : `<span class="chip warn">non configurée</span>`}</div></div>
    ${tool("claude", "Abonnement Claude (Pro / Max)", ai.claude, `<p class="hint">Passe par Claude Code connecté à ton compte : compté dans ton forfait, rien à payer en plus.</p>`)}
    ${tool("codex", "Abonnement ChatGPT (Codex)", ai.codex, `<p class="hint">Passe par Codex connecté à ton compte ChatGPT : compté dans ton forfait. Un peu plus lent (≈15 s par lot).</p>
      <label class="field" style="max-width:300px;margin-bottom:10px">Modèle Codex (vide = celui par défaut)<input type="text" id="sCxM" value="${esc(s.codex_model || "")}" placeholder="ex. un modèle « mini »"></label>`)}
    <div class="panel"><h2>Clé API Claude (alternative)</h2>
      <p class="hint">Facturée à l'usage (modèle Haiku, le moins cher : moins d'un centime pour une centaine de morceaux). Gardée dans le trousseau macOS.</p>
      <div class="row"><input type="password" id="sKey" placeholder="${S.state.has_key ? "•••••••• clé enregistrée" : "sk-ant-…"}" style="width:340px">
      <button class="btn primary" id="sKS">Enregistrer</button><button class="btn" data-act="test" data-n="api" ${S.state.has_key ? "" : "disabled"}>Tester</button>
      <button class="btn" id="sKD" ${S.state.has_key ? "" : "disabled"}>Supprimer</button></div></div>
    <div class="panel"><h2>Spotify</h2><p class="hint" style="margin:0">La connexion Spotify se gère maintenant dans l'outil <a href="#" id="sGoSp">Spotify</a>.</p></div>
    <div class="panel"><h2>Priorités du set</h2>
      <p class="hint">Les artistes à mettre en avant se choisissent maintenant pour chaque set, dans l'écran « Set », parmi les artistes de tes morceaux.</p>
      <div class="params" style="max-width:420px"><label class="field">Bonus remix : <b id="rbV">${s.remix_bonus}</b><input type="range" id="sRB" min="0" max="3" step="0.25" value="${s.remix_bonus}"></label></div>
      <button class="btn primary" id="sSave" style="margin-top:8px">Enregistrer</button></div>`;
  const prov = s.ai_provider === "subscription" ? "claude" : (s.ai_provider || "auto");
  $("#sProv").value = prov;
  $("#sLvN").value = s.ai_level_names || "eco"; $("#sLvS").value = s.ai_level_similar || "equilibre";
  $("#sLvD").value = s.ai_level_discover || "equilibre"; $("#sLvDS").value = s.ai_level_discover_sort || "eco";
  $("#sLvD").onchange = e => api("/api/settings", { ai_level_discover: e.target.value }).then(() => toast("Enregistré."));
  $("#sLvDS").onchange = e => api("/api/settings", { ai_level_discover_sort: e.target.value }).then(() => toast("Enregistré."));
  $("#sLvN").onchange = e => api("/api/settings", { ai_level_names: e.target.value }).then(() => toast("Enregistré."));
  $("#sLvS").onchange = e => api("/api/settings", { ai_level_similar: e.target.value }).then(() => toast("Enregistré."));
  $("#sProv").onchange = async e => { await api("/api/settings", { ai_provider: e.target.value }); renderSettings(); };
  $$(".sRef").forEach(b => b.onclick = () => renderSettings());
  $$("[data-act]").forEach(b => b.onclick = async () => {
    const { act, n } = b.dataset;
    if (act === "test") { toast("Test en cours…"); const r = await api(`/api/ai/test/${n}`, {}); return toast(r.ok ? "Ça fonctionne." : r.error, !r.ok); }
    await api(`/api/ai/${act}/${n}`, {});
    toast(act === "install" ? "Installation lancée dans le Terminal. Clique ensuite sur « Actualiser »." : "Connecte-toi dans la fenêtre qui s'ouvre, puis clique sur « Actualiser ».");
  });
  $("#sCxM").onchange = e => api("/api/settings", { codex_model: e.target.value.trim() });
  $("#sKS").onclick = async () => { const key = $("#sKey").value.trim(); if (!key) return; await api("/api/key", { key }); toast("Clé enregistrée."); renderSettings(); };
  $("#sKD").onclick = async () => { await api("/api/key", { key: "" }); toast("Clé supprimée."); renderSettings(); };
  $("#sRB").oninput = e => $("#rbV").textContent = e.target.value;
  $("#sGoSp").onclick = e => { e.preventDefault(); go("spotify"); };
  $("#sSave").onclick = async () => {
    S.state = await api("/api/settings", { remix_bonus: +$("#sRB").value });
    if (S.proj) await loadProject(S.proj.root);
    toast("Réglages enregistrés.");
  };
}

// ------------------------------------------------------------------ player

const player = (() => {
  const a1 = $("#a1"), a2 = $("#a2");
  let active = a1, timer = null, current = null;
  const src = t => `/api/audio?root=${encodeURIComponent(S.proj.root)}&id=${t.id}`;
  const vol = () => +$("#pVol").value / 100;
  function show(t) {
    current = t; $("#pTitle").textContent = t.title; $("#pSub").textContent = `${t.artist} · ${t.bpm || "?"} BPM · ${t.camelot}`;
    $("#pCover").src = t.has_cover ? coverUrl(t) : "";
  }
  function stopAll() { clearInterval(timer); [a1, a2].forEach(a => { a.pause(); a.volume = vol(); }); }
  function play(t) {
    stopAll(); active = a1; a1.src = src(t); a1.volume = vol(); a1.play().catch(e => toast("Lecture impossible : " + e.message, true)); show(t);
  }
  // Crossfade: last ~30 s of A, B comes in 12 s before A's end, 10 s fade.
  function transition(a, b) {
    stopAll(); active = a1; a1.src = src(a); a2.src = src(b); a2.volume = 0; show(a);
    a1.addEventListener("loadedmetadata", function go() {
      a1.removeEventListener("loadedmetadata", go);
      a1.currentTime = Math.max(0, a1.duration - 32); a1.play();
      let started = false;
      timer = setInterval(() => {
        const left = a1.duration - a1.currentTime;
        if (!started && left <= 14) { started = true; a2.currentTime = 0; a2.play(); }
        if (started) {
          const f = Math.min(1, (14 - left) / 10);
          a1.volume = vol() * (1 - f); a2.volume = vol() * f;
          if (f >= 1 || a1.ended) { a1.pause(); clearInterval(timer); active = a2; show(b); }
        }
      }, 100);
    });
    toast(`Transition : ${a.title} → ${b.title}`);
  }
  $("#pPlay").onclick = () => { if (!current) return; active.paused ? active.play() : active.pause(); };
  $("#pVol").oninput = () => { if (!timer || active === a2) active.volume = vol(); };
  $("#pSeek").oninput = e => { if (active.duration) active.currentTime = active.duration * e.target.value / 1000; };
  setInterval(() => {
    $("#pPlay").textContent = active.paused ? "▶" : "❚❚";
    if (active.duration) { $("#pSeek").value = 1000 * active.currentTime / active.duration; $("#pTime").textContent = `${fmt(active.currentTime)} / ${fmt(active.duration)}`; }
  }, 300);
  function playUrl(url, title, sub) {
    stopAll(); active = a1; a1.src = url; a1.volume = vol(); a1.play(); current = { title };
    $("#pTitle").textContent = title; $("#pSub").textContent = sub || ""; $("#pCover").src = "";
  }
  return { play, transition, playUrl };
})();

document.addEventListener("keydown", e => { if (e.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) { e.preventDefault(); $("#pPlay").click(); } if (e.key === "Escape") closeModal(); });

api("/api/state").then(s => { S.state = s; go("home"); });
