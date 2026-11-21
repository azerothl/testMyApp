/* TestMyApp — Test Floor (spec §11)
 * Canvas pixel art : personas 12×16 (rendu ×3), scène top-down 26×15 tuiles,
 * bulles d'émotion, bulles de dialogue, effets, panneaux latéraux, timeline.
 * Client : interpolation des mouvements (serveur 10 Hz → 60 fps affichage).
 * prefers-reduced-motion : positions posées sans interpolation, effets off.
 */
"use strict";

const TILE = 32;
const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
let reduced = motionQuery.matches;
motionQuery.addEventListener("change", event => {
  reduced = event.matches;
  effects.length = 0;
});

/* ----------------------------------------------------------- sprites pixel */
/* Palette de base par défaut (palettes réelles venant du serveur). */
const DEFAULT_PAL = { skin: "#e8b48a", hair: "#3a2a1a", top: "#4a6a8a", bottom: "#2a2a34" };
const REPLAY_TOPS = ["#638bd7", "#8ac99c", "#e49b77", "#b88de1", "#dfba66"];
const OUTLINE = "#10121a";

/* Persona 12×16, vue de face. Chars : H hair, S skin, T top, B bottom,
 * E eye, O outline/chaussures, . transparent */
const MAP_FACE = [
  "....HHHH....",
  "..HHHHHHHH..",
  "..HHHHHHHH..",
  "..HSSSSSSH..",
  "..SSESSSES..",
  "..SSSSSSSS..",
  "...SSSSSS...",
  "..TTTTTTTT..",
  ".TTTTTTTTTT.",
  ".STTTTTTTTS.",
  ".STTTTTTTTS.",
  "..TTTTTTTT..",
  "..BBBBBBBB..",
  "..BBB..BBB..",
  "..BBB..BBB..",
  "..OOO..OOO..",
];
/* Vue de dos (travail) : visage remplacé par cheveux. */
const MAP_BACK = MAP_FACE.map((row, i) => (i >= 3 && i <= 6 ? row.replaceAll("S", "H").replaceAll("E", "H") : row));
const WORK_POSES = {
  type: [".STTTTTTTTS.", "..STTTTTTS.."],
  keys: ["S.TTTTTTTT.S", "..TTTTTTTT.."],
  click: [".TTTTTTTTSSS", ".TTTTTTTSSS."],
  navigate: [".TTTTTTTSS..", ".TTTTTTTTSS."],
  verify: [".STTSSSSTTS.", ".STSSSSSTTS."],
  wait: ["..TTSSSSTT..", "..TTSSSSTT.."],
};
/* Jambes alternées (marche) : dérivées de la vue de face. */
const MAP_WALK = [
  [
    ...MAP_FACE.slice(0, 13),
    ".BBB....BBB.",
    ".BBB....BBB.",
    ".OOO....OOO.",
  ],
  [
    ...MAP_FACE.slice(0, 13),
    "..BBB..BBB..",
    "..BB....BB..",
    "..OO....OO..",
  ],
];
/* Triste : tête baissée (visage décalé), yeux fermés. */
const MAP_SAD = [
  "............",
  "....HHHH....",
  "..HHHHHHHH..",
  "..HHHHHHHH..",
  "..HSSSSSSH..",
  "..SSSSSSSS..",
  "..SSESSSES..",
  "...SSSSSS...",
  "..TTTTTTTT..",
  ".TTTTTTTTTT.",
  ".STTTTTTTTS.",
  "..TTTTTTTT..",
  "..BBBBBBBB..",
  "..BBB..BBB..",
  "..BBB..BBB..",
  "..OOO..OOO..",
];
/* Bras levés (célébration). */
const MAP_CHEER = [
  "S...HHHH...S",
  "S.HHHHHHHH.S",
  "SSHHHHHHHHSS",
  "..HSSSSSSH..",
  "..SSESSSES..",
  "..SSSSSSSS..",
  "...SSSSSS...",
  "..TTTTTTTT..",
  ".TTTTTTTTTT.",
  "..TTTTTTTT..",
  "..TTTTTTTT..",
  "..TTTTTTTT..",
  "..BBBBBBBB..",
  "..BBB..BBB..",
  "..BBB..BBB..",
  "..OOO..OOO..",
];

function drawMap(ctx, map, pal, ox, oy, scale) {
  for (let y = 0; y < map.length; y++) {
    const row = map[y];
    for (let x = 0; x < row.length; x++) {
      const c = row[x];
      if (c === ".") continue;
      let color;
      switch (c) {
        case "H": color = pal.hair || DEFAULT_PAL.hair; break;
        case "S": color = pal.skin || DEFAULT_PAL.skin; break;
        case "T": color = pal.top || DEFAULT_PAL.top; break;
        case "B": color = pal.bottom || DEFAULT_PAL.bottom; break;
        case "E": color = OUTLINE; break;
        case "O": color = OUTLINE; break;
        default: color = OUTLINE;
      }
      ctx.fillStyle = color;
      ctx.fillRect(ox + x * scale, oy + y * scale, scale, scale);
    }
  }
}

const spriteCache = new Map();
function getSprite(pid, pal, anim, frame) {
  const key = `${pid}|${JSON.stringify(pal)}|${anim}|${frame}`;
  if (spriteCache.has(key)) return spriteCache.get(key);
  const cv = document.createElement("canvas");
  cv.width = 12 * 3; cv.height = 16 * 3;
  const ctx = cv.getContext("2d");
  let map = MAP_FACE;
  if (anim === "walk") map = MAP_WALK[frame % MAP_WALK.length];
  else if (anim === "work") map = MAP_BACK;
  else if (anim === "idle") map = frame % 4 === 3 ? MAP_SAD : MAP_FACE; // clignement simplifié
  else if (anim === "frustrated") map = MAP_SAD;
  else if (anim === "cheer") map = MAP_CHEER;
  else if (WORK_POSES[anim]) {
    map = [...(anim === "wait" || anim === "verify" ? MAP_FACE : MAP_BACK)];
    map[9] = WORK_POSES[anim][frame % 2];
  }
  drawMap(ctx, map, pal, 0, 0, 3);
  spriteCache.set(key, cv);
  return cv;
}

/* ------------------------------------------------------------------- état */
const state = {
  meta: {}, personas: {}, stats: {}, stations: [], door: [2, 12], sofa: [20, 12],
  desk: [13, 12], tiles: [26, 15], selected: null, selectedSession: null,
  stationSessions: {}, controls: false, canStart: false,
};
const walkers = {}; // pid -> {phase, jitter, facing, blinkT, lastTs}
const activities = new Map();
const activityTypes = {
  type: { label: "Saisie", color: "#75e5cd", pixels: ["11111", "10001", "10101", "10001", "11111"] },
  keys: { label: "Clavier", color: "#c7a9ff", pixels: ["00100", "01110", "11111", "00100", "00100"] },
  click: { label: "Clic", color: "#ffd382", pixels: ["10000", "11000", "11100", "11000", "10100"] },
  navigate: { label: "Navigation", color: "#8cbdff", pixels: ["00100", "00010", "11111", "00010", "00100"] },
  verify: { label: "Vérification", color: "#a9eca9", pixels: ["01110", "10001", "10001", "01110", "00011"] },
  wait: { label: "Attente", color: "#e8bba5", pixels: ["11111", "01010", "00100", "01010", "11111"] },
};
const actionKinds = {
  type: "type", set_value: "type", press: "keys", hotkey: "keys", focus: "keys",
  click: "click", tap: "click", invoke: "click", select: "click", check: "click",
  hover: "click", dblclick: "click", navigate: "navigate", scroll: "navigate",
  wait: "wait", wait_for: "wait", wait_for_url: "wait", wait_for_exit: "wait",
};

function activityFrom(event) {
  if (["step.passed", "step.warned", "step.failed"].includes(event.type)) {
    return event.evidence?.some(item => item.expect) ? "verify" : null;
  }
  if (event.type !== "action.performed") return null;
  const kind = actionKinds[event.action];
  return kind === "click" && event.interaction === "keyboard" ? "keys" : kind;
}

function recordActivity(event) {
  const kind = activityFrom(event);
  const sid = event.session_id;
  if (!kind || !state.personas[sid]) return;
  if (!activities.has(sid)) activities.set(sid, { queue: [], current: null });
  const track = activities.get(sid);
  track.queue.push({ kind, label: event.action === "tap" && kind === "click" ? "Appui tactile" : activityTypes[kind].label,
                     target: event.intent_target || event.evidence?.find(item => item.detail?.selector)?.detail.selector,
                     failed: event.ok === false || event.type === "step.failed" });
  if (track.queue.length > 8) track.queue.shift();
}

function advanceActivities(now) {
  for (const [sid, track] of activities) {
    if (!state.personas[sid]) {
      activities.delete(sid);
      continue;
    }
    if (track.current && now < track.until) continue;
    track.current = track.queue.shift() || null;
    if (track.current) {
      track.started = now;
      track.until = now + (track.current.kind === "wait" ? 1000 : 650);
    }
  }
}

function paletteFor(pid) {
  if (state.meta[pid]?.palette) return state.meta[pid].palette;
  const hue = Array.from(pid).reduce((hash, ch) => hash + ch.codePointAt(0), 0);
  return { ...DEFAULT_PAL, top: pid === "eric" ? DEFAULT_PAL.top : REPLAY_TOPS[hue % REPLAY_TOPS.length] };
}

const cv = document.getElementById("floor");
const ctx = cv.getContext("2d");
const backdrop = document.createElement("canvas");
backdrop.width = cv.width;
backdrop.height = cv.height;
const backdropContext = backdrop.getContext("2d");
let sceneKey = "";
ctx.imageSmoothingEnabled = false;

function hexToRgba(hex, a) {
  const m = hex.replace("#", "");
  const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/* ---------------------------------------------------------------- effets */
const effects = [];
function spawnEffect(kind, tx, ty) {
  if (reduced) return; // pas d'effets si mouvement réduit (§11.5)
  const n = kind === "confetti" ? 14 : kind === "smoke" ? 8 : 7;
  const parts = [];
  const colors = ["#e86a6a", "#e8b64a", "#5ad19a", "#7a9ae8", "#d97ae8"];
  for (let i = 0; i < n; i++) {
    parts.push({
      dx: (Math.random() - 0.5) * TILE * 1.4,
      dy: kind === "smoke" ? -Math.random() * TILE : Math.random() * -TILE * 1.2,
      color: kind === "smoke" ? "#9aa0aa" : kind === "stars" ? "#ffe27a" : colors[i % colors.length],
      delay: Math.random() * 0.3,
    });
  }
  effects.push({ kind, tx, ty, parts, t0: performance.now() });
}

/* ------------------------------------------------------------ rendu scène */
function drawBackdrop(ctx) {
  const W = state.tiles[0] * TILE, H = state.tiles[1] * TILE;
  ctx.clearRect(0, 0, W, H);
  // sol : parquet damier discret
  for (let y = 0; y < state.tiles[1]; y++) {
    for (let x = 0; x < state.tiles[0]; x++) {
      const even = (x + y) % 2 === 0;
      ctx.fillStyle = even ? "#23243b" : "#202236";
      ctx.fillRect(x * TILE, y * TILE, TILE, TILE);
      // lattes
      ctx.fillStyle = "#1c1e31";
      if (y % 2 === 0) ctx.fillRect(x * TILE, y * TILE, TILE, 2);
    }
  }
  function tile(x, y, width, height, color) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, width, height);
  }
  // Bande technique au fond du labo, panneaux vitrés et armoires de matériel.
  tile(0, 0, W, 84, "#16182d");
  tile(0, 83, W, 4, "#373b63");
  tile(0, 87, W, 5, "#181a2d");
  for (const left of [103, 594]) {
    tile(left, 13, 128, 55, "#111426");
    tile(left + 4, 17, 120, 47, "#344869");
    for (let x = left + 6; x < left + 120; x += 8) {
      tile(x, 19, 3, 43, x % 3 === 0 ? "#364e70" : "#3c567a");
    }
    tile(left + 61, 15, 5, 50, "#1e263e");
    tile(left + 5, 43, 118, 3, "#233550");
    for (let n = 0; n < 3; n++) {
      const x = left + 7 + n * 36;
      tile(x, 59, 21, 2, "#5a7e9d");
    }
  }
  for (const left of [237, 552]) {
    tile(left, 24, 26, 52, "#101326");
    tile(left + 2, 22, 22, 49, "#373b57");
    for (let row = 0; row < 4; row++) {
      tile(left + 5, 27 + row * 10, 16, 7, "#25283d");
      tile(left + 16, 29 + row * 10, 2, 2, row % 2 ? "#91d2bf" : "#e6b981");
    }
  }
  // Chemin de circulation et dallage de l’espace de pause.
  tile(110, H - 22, W - 222, 2, "#515078");
  for (let x = 110; x < W - 112; x += 16) tile(x, H - 16, 6, 2, "#393c5e");
  // porte
  const [dx, dy] = state.door;
  ctx.fillStyle = "#4a4669"; ctx.fillRect(dx * TILE - 6, dy * TILE - 10, TILE + 12, TILE + 10);
  ctx.fillStyle = "#16172a"; ctx.fillRect(dx * TILE - 2, dy * TILE - 6, TILE + 4, TILE + 6);
  ctx.fillStyle = "#8a92a8"; ctx.fillRect(dx * TILE + TILE - 2, dy * TILE + TILE / 2, 4, 4);
  // canapé
  const [sx, sy] = state.sofa;
  tile(sx * TILE - 22, sy * TILE + 12, 111, 49, "#2d2c4a");
  tile(sx * TILE - 21, sy * TILE + 55, 108, 3, "#575077");
  tile(sx * TILE - 21, sy * TILE + 48, 108, 2, "#443f65");
  ctx.fillStyle = "#5a4a6a"; ctx.fillRect(sx * TILE, sy * TILE - 8, TILE * 2, TILE);
  ctx.fillStyle = "#6a5a7a"; ctx.fillRect(sx * TILE, sy * TILE - 16, TILE * 2, 10);
  tile(sx * TILE - 4, sy * TILE + 3, 7, 22, "#73648b");
  tile(sx * TILE + 61, sy * TILE + 3, 7, 22, "#73648b");
  tile(sx * TILE + 30, sy * TILE + 1, 3, 19, "#4d3e60");
  tile(sx * TILE + 15, sy * TILE + 33, 36, 13, "#4b3c58");
  tile(sx * TILE + 17, sy * TILE + 32, 31, 8, "#8c6b7c");
  tile(sx * TILE + 22, sy * TILE + 31, 6, 5, "#e7a582");
  // plantes
  for (const [px, py] of [[1, 1], [24, 1], [1, 13]]) {
    ctx.fillStyle = "#5a3a2a"; ctx.fillRect(px * TILE + 8, py * TILE + 18, 16, 12);
    ctx.fillStyle = "#4a8a4a"; ctx.fillRect(px * TILE + 4, py * TILE + 2, 24, 18);
    ctx.fillStyle = "#5aa85a"; ctx.fillRect(px * TILE + 8, py * TILE, 16, 10);
  }
  // tableau des scores au mur
  ctx.fillStyle = "#20242f"; ctx.fillRect(9 * TILE, TILE * 0.5, 8 * TILE, TILE * 1.6);
  ctx.strokeStyle = "#2e3448"; ctx.strokeRect(9 * TILE + 2, TILE * 0.5 + 2, 8 * TILE - 4, TILE * 1.6 - 4);
  ctx.fillStyle = "#dfe4f2"; ctx.font = "bold 16px monospace"; ctx.textAlign = "center";
  const scoreTxt = state.stats.score != null ? `${Math.round(state.stats.score)}/100` : "—";
  ctx.fillText(`SCORE ${scoreTxt}`, 13 * TILE, TILE * 1.5);
  ctx.textAlign = "left";
  // stations
  state.stations.forEach((st, i) => {
    const [x, y] = st;
    tile(x * TILE - 13, y * TILE - 26, 57, 67, "#2b2d48");
    tile(x * TILE - 13, y * TILE - 26, 57, 2, "#4a4b77");
    tile(x * TILE - 13, y * TILE + 39, 57, 2, "#171a2c");
    ctx.fillStyle = "#454264"; ctx.fillRect(x * TILE - 6, y * TILE + 10, TILE + 12, TILE - 6); // bureau
    ctx.fillStyle = "#5b5683"; ctx.fillRect(x * TILE - 6, y * TILE + 10, TILE + 12, 6);
    // écran
    ctx.fillStyle = "#20242f"; ctx.fillRect(x * TILE + 2, y * TILE - 18, TILE - 4, 26);
    ctx.fillStyle = "#454875"; ctx.fillRect(x * TILE + 5, y * TILE - 15, TILE - 10, 20);
    ctx.fillStyle = "#333a4a"; ctx.fillRect(x * TILE + TILE / 2 - 2, y * TILE + 8, 4, 3);
    tile(x * TILE + 5, y * TILE + 13, 16, 3, "#a3a0c8");
    tile(x * TILE + 26, y * TILE + 12, 4, 5, "#8997be");
    tile(x * TILE - 3, y * TILE + 17, 3, 14, "#272940");
    tile(x * TILE + 32, y * TILE + 17, 3, 14, "#272940");
    // étiquette station
    ctx.fillStyle = "#8a92a8"; ctx.font = "9px monospace";
    ctx.fillText(`ST-${i + 1}`, x * TILE + 2, y * TILE - 30);
  });
  // bureau rapport + pile de papier qui grandit (§11.2)
  const [rx, ry] = state.desk;
  ctx.fillStyle = "#454264"; ctx.fillRect(rx * TILE - 8, ry * TILE + 8, TILE + 16, TILE - 4);
  const done = state.stats.sessions_done || 0;
  for (let i = 0; i < Math.min(done, 10); i++) {
    ctx.fillStyle = i % 2 ? "#e8e4d8" : "#d8d4c8";
    ctx.fillRect(rx * TILE + 2, ry * TILE + 4 - i * 2, TILE - 8, 3);
  }
  ctx.fillStyle = "#8a92a8"; ctx.font = "9px monospace";
  ctx.fillText("RAPPORTS", rx * TILE - 6, ry * TILE + TILE + 12);
}

function drawStationActivity(now) {
  for (const [sid, track] of activities) {
    const activity = track.current;
    const station = state.stations[state.personas[sid]?.station];
    if (!activity || !station || state.stationSessions[state.personas[sid].station] !== sid) continue;
    const [x, y] = station.map(value => value * TILE);
    const style = activityTypes[activity.kind];
    const pulse = !reduced && Math.floor((now - track.started) / 130) % 2 === 0;
    const color = activity.failed ? "#ff97a6" : style.color;
    ctx.fillStyle = hexToRgba(color, pulse ? .24 : .14);
    ctx.fillRect(x - 9, y - 23, 50, 35);
    ctx.fillStyle = "#202642";
    ctx.fillRect(x + 5, y - 15, 22, 20);
    ctx.fillStyle = color;
    style.pixels.forEach((row, py) => {
      for (let px = 0; px < row.length; px++) {
        if (row[px] === "1") ctx.fillRect(x + 11 + px * 2, y - 10 + py * 2, 2, 2);
      }
    });
    ctx.fillRect(x + 24, y + 1, 2, 2);
  }
}

function drawScene() {
  const key = [state.stations.length, state.stats.score, state.stats.sessions_done].join("/");
  if (key !== sceneKey) {
    sceneKey = key;
    drawBackdrop(backdropContext);
  }
  ctx.drawImage(backdrop, 0, 0);
}

function ensureWalker(sid) {
  if (!walkers[sid]) {
    const actor = state.personas[sid];
    walkers[sid] = {
      px: actor.x, py: actor.y, jitter: 0.85 + Math.random() * 0.3,
      facing: 1, phase: Math.random() * 6, stage: "station", reportTimer: 0,
    };
  }
  return walkers[sid];
}

function stepWalkers(dt) {
  for (const [sid, p] of Object.entries(state.personas)) {
    const w = ensureWalker(sid);
    const [stationX, stationY] = state.stations[p.station] || state.door;
    const station = [stationX, stationY + .9];
    if (reduced) {
      [w.px, w.py] = p.outcome ? state.desk : station;
      w.stage = p.outcome ? "gone" : "working";
      continue;
    }
    const activity = activities.get(sid);
    if (w.stage === "working" && p.outcome && !activity?.current && !activity?.queue.length) w.stage = "report";
    if (w.stage === "reporting") {
      w.reportTimer -= dt;
      if (w.reportTimer <= 0) w.stage = "leaving";
    }
    const [tx, ty] = w.stage === "station" ? station : w.stage === "report" ? state.desk : state.door;
    if (!["station", "report", "leaving"].includes(w.stage)) continue;
    const dx = tx - w.px, dy = ty - w.py;
    const distance = Math.hypot(dx, dy);
    const speed = 4.0 * w.jitter * dt;
    if (distance > Math.max(.02, speed)) {
      w.px += dx / distance * speed;
      w.py += dy / distance * speed;
      if (Math.abs(dx) > .1) w.facing = Math.sign(dx);
    } else {
      w.px = tx;
      w.py = ty;
      if (w.stage === "station") w.stage = "working";
      else if (w.stage === "report") { w.stage = "reporting"; w.reportTimer = 1.3; }
      else w.stage = "gone";
    }
  }
}

function drawPersonas(now) {
  for (const [pid, p] of Object.entries(state.personas)) {
    const w = ensureWalker(pid);
    if (w.stage === "gone") continue;
    const meta = state.meta[p.persona_id] || {};
    const pal = Object.assign({}, DEFAULT_PAL, paletteFor(p.persona_id));
    let anim = ["station", "report", "leaving"].includes(w.stage) ? "walk" : "idle";
    const activity = activities.get(pid)?.current;
    const atStation = w.stage === "working";
    if (w.stage === "working") anim = activity?.kind || (p.anim === "frustrated" ? "frustrated" : "work");
    const activeBubble = p.bubble && Date.now() / 1000 - p.bubble.ts < 3;
    if (!reduced && activeBubble && p.bubble.emotion === "celebrate" && w.stage === "working" && !activity) anim = "cheer";
    if (reduced) anim = w.stage === "working" ? (activity?.kind || "work") : "idle";
    w.phase += reduced ? 0 : 0.16;
    const frame = reduced ? 0 : anim === "idle" ? Math.floor(now / 350) % 4 : Math.floor(now / 105 + w.phase) % 2;
    const bobY = anim === "cheer" && Math.floor(now / 120) % 2 === 0 ? -2 : 0;
    const sx = (w.px * TILE + TILE / 2) - (12 * 3) / 2;
    const sy = w.py * TILE - 16 * 3 + TILE - 6 + bobY;
    // ombre portée
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.beginPath();
    ctx.ellipse(sx + 18, w.py * TILE + TILE - 4, 14, 4, 0, 0, Math.PI * 2);
    ctx.fill();
    const sprite = getSprite(p.persona_id, pal, anim, frame);
    if (w.facing < 0) {
      ctx.save(); ctx.translate(sx + 36, sy); ctx.scale(-1, 1);
      ctx.drawImage(sprite, 0, 0); ctx.restore();
    } else {
      ctx.drawImage(sprite, sx, sy);
    }
    // nom + badge humeur (toujours avec libellé, §11.3)
    ctx.font = "bold 10px system-ui"; ctx.textAlign = "center";
    ctx.fillStyle = "#dfe4f2";
    ctx.fillText(meta.name || p.persona_id, sx + 18, atStation ? sy + 58 : sy - 8);
    const badge = p.badge_emoji || "😐";
    ctx.font = "12px system-ui";
    ctx.fillText(badge, atStation ? sx + 40 : sx + 18, atStation ? sy + 13 : sy + 60);
    // bulle d'émotion (pop §11.3)
    if (p.bubble) {
      const age = Math.max(0, Date.now() / 1000 - p.bubble.ts);
      if (age < 3.0) {
        const pop = reduced ? 1 : Math.min(1, 0.6 + age * 6) * (age > 2.6 ? Math.max(0, 1 - (age - 2.6) / 0.4) : 1);
        drawBubble(sx + 18, sy - (atStation ? 49 : 22), p.bubble.emoji || "•", p.bubble.count || 1, pop);
      }
    }
    // bulle de dialogue (action courante)
    if (p.speech) {
      const spAge = Date.now() / 1000 - (p.speech_ts || 0);
      if (spAge < (p.speech_ttl || 4)) {
        drawSpeech(sx + 18, sy - (atStation ? 69 : 40), p.speech);
      }
    }
    ctx.textAlign = "left";
  }
}

function drawBubble(cx, cy, emoji, count, pop) {
  const w = 30, h = 26;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(pop, pop);
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = OUTLINE; ctx.lineWidth = 2;
  roundRect(-w / 2, -h / 2, w, h, 6); ctx.fill(); ctx.stroke();
  ctx.beginPath(); // pointe de la bulle
  ctx.moveTo(-4, h / 2 - 1); ctx.lineTo(0, h / 2 + 6); ctx.lineTo(4, h / 2 - 1);
  ctx.fillStyle = "#ffffff"; ctx.fill();
  ctx.font = "15px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(emoji, 0, 1);
  if (count > 1) {
    ctx.font = "bold 9px system-ui"; ctx.fillStyle = "#e86a6a";
    ctx.fillText(`×${count}`, w / 2 - 6, -h / 2 + 5);
  }
  ctx.restore();
}

function drawSpeech(cx, cy, text) {
  ctx.font = "11px system-ui";
  const words = String(text).split(" ");
  const lines = [];
  let cur = "";
  for (const word of words) {
    if ((cur + " " + word).trim().length > 22) { lines.push(cur.trim()); cur = word; }
    else cur += " " + word;
  }
  if (cur.trim()) lines.push(cur.trim());
  const w = Math.max(...lines.map(l => ctx.measureText(l).width)) + 14;
  const h = lines.length * 14 + 8;
  ctx.fillStyle = "rgba(16,18,26,0.92)";
  ctx.strokeStyle = "#3a4258"; ctx.lineWidth = 1.5;
  roundRect(cx - w / 2, cy - h, w, h, 5); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#eef1fa"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  lines.forEach((l, i) => ctx.fillText(l, cx, cy - h + 10 + i * 14));
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawEffects(now) {
  for (let i = effects.length - 1; i >= 0; i--) {
    const fx = effects[i];
    const t = (now - fx.t0) / 900;
    if (t > 1) { effects.splice(i, 1); continue; }
    for (const part of fx.parts) {
      if (t < part.delay) continue;
      const tt = t - part.delay;
      const px = fx.tx * TILE + TILE / 2 + part.dx * (fx.kind === "smoke" ? 1 : 1 + tt * 0.4);
      const py = fx.ty * TILE + (fx.kind === "smoke" ? part.dy * tt - 20 : part.dy * tt + tt * tt * 40);
      ctx.globalAlpha = 1 - tt;
      ctx.fillStyle = part.color;
      ctx.fillRect(px - 2, py - 2, 4, 4);
      ctx.globalAlpha = 1;
    }
  }
}

/* ---------------------------------------------------------------- panneaux */
const personaList = document.getElementById("persona-list");
const detailEl = document.getElementById("detail");
const metricsEl = document.getElementById("metrics");
const timelineEl = document.getElementById("timeline");
const statusChip = document.getElementById("status-chip");
const seedChip = document.getElementById("seed-chip");
const speedChip = document.getElementById("speed-chip");
const btnReport = document.getElementById("btn-report");
const srEl = document.getElementById("sr-only");

const personaButtons = new Map();
const stationSelect = document.getElementById("station-select");
const sessionOpenButton = document.getElementById("station-open");
const startButton = document.getElementById("btn-start");
const pauseButton = document.getElementById("btn-pause");
const stopButton = document.getElementById("btn-stop");
const sessionPanel = document.getElementById("session-panel");
const sessionTitle = document.getElementById("session-title");
const sessionSteps = document.getElementById("session-steps");
const sessionStatus = document.getElementById("session-status");
let stationKey = "";
let requestId = 0;
let selectedStep = null;
let waitingKey = "";

function refreshPanels() {
  const latest = {};
  for (const [sid, actor] of Object.entries(state.personas)) latest[actor.persona_id] = { ...actor, sid };
  const ids = Object.keys({ ...state.meta, ...latest });
  document.getElementById("pcount").textContent = ids.length ? `(${ids.length})` : "";
  for (const pid of ids) {
    const actor = latest[pid] || {};
    const meta = state.meta[pid] || {};
    if (!personaButtons.has(pid)) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      const avatar = document.createElement("canvas");
      avatar.className = "p-avatar";
      avatar.width = 36;
      avatar.height = 48;
      avatar.setAttribute("aria-hidden", "true");
      avatar.getContext("2d").drawImage(getSprite(pid, paletteFor(pid), "idle", 0), 0, 0);
      const text = document.createElement("span");
      const name = document.createElement("span");
      const description = document.createElement("span");
      text.className = "p-copy";
      name.className = "p-name";
      description.className = "p-sub dim";
      text.append(name, description);
      button.append(avatar, text);
      button.addEventListener("click", () => selectPersona(pid));
      li.append(button);
      personaList.append(li);
      personaButtons.set(pid, { button, name, description });
    }
    const { button, name, description } = personaButtons.get(pid);
    const activity = activities.get(actor.sid)?.current;
    const content = activity ? [activity.label, activity.target].filter(Boolean).join(" · ")
      : actor.outcome ? stateLabel(actor.state) : actor.badge || stateLabel(actor.state);
    if (name.textContent !== (meta.name || pid)) name.textContent = meta.name || pid;
    if (description.textContent !== content) description.textContent = content;
    button.setAttribute("aria-pressed", String(state.selected === pid));
  }

  if (state.selected && ids.includes(state.selected)) {
    const pid = state.selected, p = latest[pid] || {}, meta = state.meta[pid] || {};
    detailEl.innerHTML = `
      <div class="row"><strong>${escapeHtml(meta.name || pid)}</strong>
        <span class="dim">${meta.age ? escapeHtml(meta.age) + " ans" : ""}</span></div>
      <div class="row dim">${escapeHtml(meta.occupation || "")}</div>
      ${meta.bio ? `<div class="row">${escapeHtml(meta.bio)}</div>` : ""}
      ${meta.goals?.length ? `<div class="row"><span class="label">Objectif</span> ${escapeHtml(meta.goals.join(" · "))}</div>` : ""}
      ${meta.device ? `<div class="row dim">${escapeHtml([meta.device, meta.input, meta.network, meta.locale].join(" · "))}</div>` : ""}
      <div class="row"><span class="label">Humeur</span>
        ${p.mood != null ? p.mood.toFixed(2) : "—"} ${escapeHtml(p.badge || "")}</div>
      <div class="row"><span class="label">Scénario</span> ${escapeHtml(p.scenario || "—")}</div>
      <div class="row"><span class="label">Dernière parole</span> ${escapeHtml(p.speech || "—")}</div>`;
  }

  const key = JSON.stringify(state.stationSessions);
  if (key !== stationKey) {
    const selected = stationSelect.value;
    while (stationSelect.options.length > 1) stationSelect.remove(1);
    for (const [index, sid] of Object.entries(state.stationSessions)) {
      const actor = state.personas[sid];
      const label = state.meta[actor?.persona_id]?.name || actor?.persona_id || sid.split("×")[0];
      stationSelect.add(new Option(`Station ${Number(index) + 1} · ${label}`, sid));
    }
    if ([...stationSelect.options].some(option => option.value === selected)) stationSelect.value = selected;
    sessionOpenButton.disabled = !stationSelect.value;
    stationKey = key;
  }

  const st = state.stats;
  const graph = Object.values(st.workflow || {});
  document.getElementById("floor-workflow").hidden = !graph.length;
  renderWorkflow(document.getElementById("floor-graph"), graph, sid => openSession(sid));
  const waiting = st.waiting || [];
  const nextWaitingKey = JSON.stringify(waiting);
  if (nextWaitingKey !== waitingKey) {
    waitingKey = nextWaitingKey;
    const items = waiting.map(entry => {
      const item = document.createElement("li");
      const who = entry.task ? `Tâche ${entry.task} · ${entry.name}` : entry.name;
      const dependencies = entry.after_tasks || [];
      const prerequisite = dependencies.length > 1 ? `des tâches ${dependencies.join(", ")}`
        : dependencies.length ? `de la tâche ${dependencies[0]}` : entry.after && `de ${entry.after}`;
      item.textContent = prerequisite ? `${who} · après la réussite ${prerequisite}`
        : `${who} · attend un poste`;
      return item;
    });
    document.getElementById("waiting-list").replaceChildren(...items);
    document.getElementById("waiting-panel").hidden = !items.length;
  }
  const running = Object.values(state.personas).filter(p => !p.outcome).length;
  metricsEl.innerHTML = `
    <div class="mrow"><span class="dim">Sessions</span><span class="mval">${st.sessions_done || 0}/${st.sessions_total || 0}</span></div>
    <div class="mrow"><span class="dim">Sessions en parallèle</span><span class="mval">${st.parallelism == null ? "—" : `${running}/${st.parallelism}`}</span></div>
    <div class="mrow"><span class="dim">Score</span><span class="mval">${st.score != null ? st.score + "/100" : "—"}</span></div>`;
  statusChip.textContent = st.completed ? verdictLabel(st.status) || "Terminée" :
    st.stopping ? "Arrêt en cours" : st.paused ? "En pause" : st.started ? "En cours" : "En attente";
  seedChip.textContent = st.seed == null ? "Graine —" : `Graine ${st.seed}`;
  speedChip.textContent = st.speed == null ? "Vitesse —" : `Vitesse ${st.speed}`;
  startButton.hidden = !state.canStart || st.started || st.completed;
  pauseButton.hidden = !state.controls || !st.started || st.completed || st.stopping;
  pauseButton.textContent = st.paused ? "Reprendre" : "Pause";
  stopButton.hidden = pauseButton.hidden;
  btnReport.hidden = !st.report_ready;
}

function verdictLabel(verdict) {
  return ({
    pass: "Réussite", fail: "Échec", error: "Erreur d’exécution", interrupted: "Interrompue",
    abandoned: "Abandon", passed: "Réussite", failed: "Échec", warned: "Avertissement",
    skipped: "Branche non déclenchée", blocked: "Bloqué par un prérequis",
  })[verdict] || "";
}

function renderSession(data) {
  sessionTitle.textContent = `${data.persona_name || state.meta[data.persona]?.name || data.persona} · ${data.scenario}`;
  sessionStatus.textContent = [verdictLabel(data.outcome) || "En cours", data.error].filter(Boolean).join(" · ");
  sessionSteps.replaceChildren();
  for (const step of Object.values(data.steps)) {
    const item = document.createElement("li");
    item.dataset.stepIndex = String(step.index);
    if (step.index === selectedStep) {
      item.classList.add("selected-step");
      item.tabIndex = -1;
    }
    const title = document.createElement("strong");
    title.textContent = step.intent || `Étape ${step.index + 1}`;
    item.append(title);
    if (step.verdict) {
      const verdict = document.createElement("p");
      verdict.textContent = `${verdictLabel(step.verdict)} · ${((step.duration_ms || 0) / 1000).toFixed(2)} s`;
      item.append(verdict);
    }
    for (const text of [...(step.actions || []), ...(step.results || [])]) {
      const note = document.createElement("p");
      note.textContent = text;
      item.append(note);
    }
    for (const capture of step.captures) {
      const figure = document.createElement("figure");
      const link = document.createElement("a");
      link.href = `captures/${encodeURIComponent(capture.name)}`;
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.src = link.href;
      image.loading = "lazy";
      image.alt = `Capture ${capture.phase === "before" ? "avant" : "après"} l’étape ${step.index + 1}`;
      link.append(image);
      figure.append(link);
      item.append(figure);
    }
    sessionSteps.append(item);
  }
}

async function openSession(sid, focus = true, step = null) {
  state.selectedSession = sid;
  if (focus) selectedStep = step;
  const request = ++requestId;
  try {
    const response = await fetch(`api/sessions/${encodeURIComponent(sid)}`);
    if (!response.ok) throw new Error("Session indisponible");
    const data = await response.json();
    if (request !== requestId) return;
    if (focus && state.meta[data.persona]) selectPersona(data.persona);
    renderSession(data);
    sessionPanel.hidden = false;
    if (focus) (sessionSteps.querySelector(".selected-step") || sessionTitle).focus();
  } catch {
    srEl.textContent = "Session indisponible";
  }
}

async function command(name) {
  try {
    const response = await fetch("api/control", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: name }),
    });
    if (!response.ok) throw new Error("Commande indisponible");
  } catch {
    srEl.textContent = "Commande indisponible pour cette campagne";
  }
}

stationSelect.addEventListener("change", () => { sessionOpenButton.disabled = !stationSelect.value; });
sessionOpenButton.addEventListener("click", () => openSession(stationSelect.value));
startButton.addEventListener("click", () => command("start"));
pauseButton.addEventListener("click", () => command(state.stats.paused ? "resume" : "pause"));
stopButton.addEventListener("click", () => command("stop"));

function stateLabel(s) {
  return {
    arriving: "arrive", walking: "en chemin", working: "au travail",
    to_report: "va au rapport", reporting: "dépose ses constats",
    leaving: "s'en va", gone: "parti",
  }[s] || (s || "—");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function selectPersona(pid) {
  state.selected = pid;
  refreshPanels();
  srEl.textContent = `Persona sélectionné : ${state.meta[pid]?.name || pid}`;
}

function appendTimeline(ev) {
  recordActivity(ev);
  const li = document.createElement("li");
  const t = new Date((ev.ts || Date.now() / 1000) * 1000).toLocaleTimeString("fr-FR");
  let html = `<span class="t-time">${t}</span>`;
  const who = ev.persona || (ev.session_id ? ev.session_id.split("×")[0] : "") || "";
  const whoEl = who ? `<span class="t-who">${escapeHtml(who)}</span>` : "";
  switch (ev.type) {
    case "session.started":
      html += `${whoEl}<span>🚶 démarre « ${escapeHtml(ev.scenario_name || ev.scenario || "")} »</span>`; break;
    case "step.passed":
      html += `${whoEl}<span class="t-ok">✅ étape ${(ev.step_id ?? 0) + 1} (${((ev.duration_ms || 0) / 1000).toFixed(1)}s)</span>`; break;
    case "step.failed":
      html += `${whoEl}<span class="t-ko">❌ étape ${(ev.step_id ?? 0) + 1}</span>`; break;
    case "step.warned":
      html += `${whoEl}<span class="t-warn">⚠️ étape ${(ev.step_id ?? 0) + 1}</span>`; break;
    case "action.performed": {
      const kind = activityFrom(ev);
      const label = kind ? activityTypes[kind].label : "Interaction";
      const actionLabel = kind === "click" && ev.action === "tap" ? "Appui tactile" : label;
      const target = ev.intent_target ? ` sur « ${escapeHtml(ev.intent_target)} »` : "";
      if (kind) li.dataset.activityKind = kind;
      html += `${whoEl}<span class="${ev.ok === false ? "t-ko" : ""}">${actionLabel}${target}${ev.ok === false ? " — échec" : ""}</span>`;
      break;
    }
    case "persona.emotion":
      html += `${whoEl}<span>${escapeHtml(ev.emoji || "•")} ${escapeHtml(ev.label || ev.emotion || "")}</span>`; break;
    case "persona.speech":
      html += `${whoEl}<span>« ${escapeHtml(ev.text || "")} »</span>`; break;
    case "session.completed":
      html += `${whoEl}<span class="${ev.outcome === "pass" ? "t-ok" : "t-ko"}">${escapeHtml(verdictLabel(ev.outcome))}</span>`; break;
    case "session.blocked":
    case "session.skipped":
      html += `${whoEl}<span class="t-warn">${escapeHtml(ev.reason || "")}</span>`; break;
    case "campaign.started":
      html += `<span>🧪 campagne lancée (seed ${escapeHtml(ev.seed)}, ${escapeHtml(ev.sessions_total)} sessions)</span>`; break;
    case "campaign.completed":
      html += `<span>${escapeHtml(verdictLabel(ev.status) || "Campagne terminée")} · ${ev.score == null ? "score non mesuré" : escapeHtml(ev.score) + "/100"}</span>`; break;
    case "campaign.error":
      html += `<span class="t-ko">${escapeHtml(ev.error)}</span>`; break;
    case "a11y.violation":
      html += `<span class="t-warn">♿ ${escapeHtml(ev.rule || "")} (${escapeHtml(ev.severity || "")})</span>`; break;
    default: return;
  }
  li.innerHTML = html;
  if (ev.session_id) {
    const open = document.createElement("button");
    open.className = "event-link";
    const step = Number.isInteger(ev.step_id) ? ev.step_id : null;
    open.textContent = step === null ? "Ouvrir la session" : "Voir l’étape";
    open.addEventListener("click", () => openSession(ev.session_id, true, step));
    li.append(open);
  }
  if (ev.type === "persona.emotion") {
    li.dataset.emotion = ev.emotion;
    requestAnimationFrame(() => {
      li.dataset.eventLatencyMs = String(Math.max(0, Date.now() - ev.ts * 1000));
    });
  }
  timelineEl.prepend(li);
  while (timelineEl.children.length > 120) timelineEl.removeChild(timelineEl.lastChild);
}

/* --------------------------------------------------------------- WS + loop */
let ws;
function connect() {
  const endpoint = new URL("ws", location.href);
  endpoint.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(endpoint);
  ws.onopen = () => setStatus("connecté");
  ws.onclose = () => { setStatus("déconnecté"); setTimeout(connect, 1500); };
  ws.onmessage = (m) => {
    let msg; try { msg = JSON.parse(m.data); } catch { return; }
    if (msg.type === "floor.state") {
      Object.assign(state, {
        meta: msg.meta || {}, personas: msg.personas || {}, stats: msg.stats || {},
        stations: msg.stations || state.stations, door: msg.door || state.door,
        sofa: msg.sofa || state.sofa, desk: msg.report_desk || state.desk,
        tiles: msg.tiles || state.tiles, controls: !!msg.controls, canStart: !!msg.can_start,
        stationSessions: msg.station_sessions || {},
      });
      timelineEl.replaceChildren();
      activities.clear();
      (msg.events || []).forEach(appendTimeline);
      refreshPanels();
    } else if (msg.type === "floor.delta") {
      state.personas = msg.personas || {};
      state.stations = msg.stations || state.stations;
      state.stationSessions = msg.station_sessions || {};
      for (const sid of Object.keys(walkers)) if (!state.personas[sid]) delete walkers[sid];
      state.stats = msg.stats || state.stats;
      for (const fx of msg.effects || []) {
        spawnEffect(fx.kind, fx.x, fx.y);
      }
      const events = msg.events || [];
      events.forEach(appendTimeline);
      if (state.selectedSession && events.some(ev => ev.session_id === state.selectedSession &&
          (ev.type.startsWith("step.") || ev.type === "session.completed"))) {
        // Préserve le focus sur les preuves si le lecteur consulte déjà le détail.
        if (!sessionSteps.contains(document.activeElement)) openSession(state.selectedSession, false);
      }
      refreshPanels();
    } else if (msg.type === "events") {
      (msg.items || []).forEach(appendTimeline);
    }
  };
}

function setStatus(txt) { statusChip.textContent = txt; }

cv.addEventListener("click", event => {
  const rect = cv.getBoundingClientRect();
  const x = (event.clientX - rect.left) * cv.width / rect.width;
  const y = (event.clientY - rect.top) * cv.height / rect.height;
  for (const [index, [tx, ty]] of state.stations.entries()) {
    const sid = state.stationSessions[index];
    if (sid && Math.abs(x - (tx * TILE + TILE / 2)) <= 25 && y >= ty * TILE - 22 && y <= ty * TILE + 36) {
      openSession(sid);
      return;
    }
  }
  for (const [sid, actor] of Object.entries(state.personas)) {
    const walker = walkers[sid];
    if (!walker || walker.stage === "gone") continue;
    if (Math.hypot(walker.px * TILE + TILE / 2 - x, walker.py * TILE - 12 - y) < 30) {
      selectPersona(actor.persona_id);
      return;
    }
  }
});

let lastT = performance.now(), fpsLast = lastT, fpsCount = 0, fpsT = 0;
function loop(now) {
  const dt = Math.min(0.05, (now - lastT) / 1000);
  lastT = now;
  advanceActivities(now);
  stepWalkers(dt);
  drawScene();
  drawStationActivity(now);
  drawPersonas(now);
  drawEffects(now);
  fpsCount++; fpsT += (now - fpsLast) / 1000;
  fpsLast = now;
  if (fpsT >= 0.5) {
    document.getElementById("fps").textContent = Math.round(fpsCount / fpsT);
    fpsCount = 0; fpsT = 0;
  }
  requestAnimationFrame(loop);
}

connect();
requestAnimationFrame(loop);
