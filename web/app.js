// Stock Guide chat client. No framework, no build step.
//
// Talks to POST /api/chat, which streams Server-Sent Events:
//   text        {text}            piece of the answer
//   tool_call   {name, input}     Claude is calling a tool
//   tool_result {name, ok}        the tool finished
//   chart       {period, series}  price series to draw
//   favorite    {action, symbol}  Claude added or removed a favourite
//   favorite_failed {symbol, message}  Claude tried to add one and it was refused
//   done        {messages}        messages to append to the history
//   error       {message}         the turn failed
//
// The server is stateless: `history` lives here and is sent back each turn,
// unchanged. It is only updated when a turn finishes with `done`, so a failed
// turn never leaves a half-finished conversation behind.

"use strict";

const API_BASE = (document.querySelector('meta[name="api-base"]')?.content || "").replace(/\/$/, "");

const log = document.getElementById("log");
const empty = document.getElementById("empty");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const newChatBtn = document.getElementById("new-chat");

const sidebar = document.getElementById("sidebar");
const backdrop = document.getElementById("backdrop");
const chatList = document.getElementById("chat-list");
const noChats = document.getElementById("no-chats");
const saveWarning = document.getElementById("save-warning");
const chatsToggle = document.getElementById("chats-toggle");
const closeSidebarBtn = document.getElementById("close-sidebar");
const favList = document.getElementById("fav-list");
const favCount = document.getElementById("fav-count");
const noFavs = document.getElementById("no-favs");

let busy = false;

// ---------------------------------------------------------------- saved chats
//
// Chats live in this browser's localStorage (no accounts, nothing stored on the server).
// A chat is {id, title, updated, history, charts}. `history` is exactly what the server
// needs back each turn; `charts` are saved too because chart data never enters `history`.

const STORE_KEY = "stock-guide-chats-v1";
const MAX_CHATS = 30;

function isChat(c) {
  return c && typeof c.id === "string" && typeof c.title === "string" && Array.isArray(c.history);
}

// Never throws: a missing, blocked or corrupt store just means "no saved chats".
function readStore() {
  try {
    const data = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    return Array.isArray(data) ? data.filter(isChat).map((c) => ({ ...c, charts: c.charts || [] })) : [];
  } catch (_) {
    return [];
  }
}

// Newest first, capped at MAX_CHATS. If the browser's quota is full, drops the oldest chat
// and tries again. Returns the chats that were really kept, or null if nothing could be saved.
function writeStore(chats) {
  let keep = [...chats].sort((a, b) => b.updated - a.updated).slice(0, MAX_CHATS);
  for (;;) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(keep));
      return keep;
    } catch (_) {
      if (!keep.length) return null;
      keep = keep.slice(0, -1);
    }
  }
}

function newChat() {
  return { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8), title: "", updated: 0, history: [], charts: [] };
}

function titleFrom(text) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > 60 ? `${flat.slice(0, 59)}…` : flat;
}

let chats = readStore();
let current = newChat();

// Re-reads the store first so a second open tab's chats are not overwritten.
function saveChat(chat) {
  const stored = writeStore([chat, ...readStore().filter((c) => c.id !== chat.id)]);
  saveWarning.hidden = stored !== null;
  if (stored) chats = stored;
}

function deleteChat(id) {
  const stored = writeStore(readStore().filter((c) => c.id !== id));
  chats = stored || [];
  if (current.id === id) startNewChat();
  else renderList();
}

// ---------------------------------------------------------------- favourites
//
// Up to MAX_FAVORITES stocks, kept in localStorage next to the chats and sent with every
// chat turn so Claude knows what is saved. Only Claude adds them (add_favorite), when the
// user asks it to heart / favourite a stock or says yes to its offer. The server checks the
// ticker exists first; if not, it sends favorite_failed and the answer shows the error.
// The sidebar shows each one's latest close from GET /api/quotes (no model, no chat cost).

const FAV_KEY = "stock-guide-favorites-v1";
const MAX_FAVORITES = 10;
const QUOTE_REFRESH_MS = 15 * 60 * 1000; // matches the server's price cache

// {list: [{symbol, added}]}
function readFavs() {
  try {
    const data = JSON.parse(localStorage.getItem(FAV_KEY) || "{}");
    const list = Array.isArray(data.list) ? data.list.filter((f) => f && typeof f.symbol === "string") : [];
    return { list: list.slice(0, MAX_FAVORITES) };
  } catch (_) {
    return { list: [] };
  }
}

function writeFavs(data) {
  try {
    localStorage.setItem(FAV_KEY, JSON.stringify(data));
  } catch (_) { /* storage blocked or full: favourites last for this page only */ }
  favs = data;
}

let favs = readFavs();
const quotes = new Map(); // symbol -> quote from /api/quotes

function favSymbols() {
  return favs.list.map((f) => f.symbol);
}

// Returns "added", "exists" or "full". Re-reads the store so another tab's changes are kept.
function addFavorite(symbol) {
  const data = readFavs();
  if (data.list.some((f) => f.symbol === symbol)) return "exists";
  if (data.list.length >= MAX_FAVORITES) return "full";
  data.list.push({ symbol, added: Date.now() });
  writeFavs(data);
  renderFavorites();
  refreshQuotes();
  return "added";
}

function removeFavorite(symbol) {
  const data = readFavs();
  data.list = data.list.filter((f) => f.symbol !== symbol);
  writeFavs(data);
  renderFavorites();
}

function quoteLine(q) {
  if (!q) return { price: "…", change: "", dir: "" };
  if (q.error) return { price: "No price right now", change: "", dir: "" };
  const price = q.kind === "index" ? `${formatPrice(q.close, null, 2)} pts` : formatPrice(q.close, q.currency, 2);
  if (typeof q.change_pct !== "number") return { price, change: "", dir: "" };
  return { price, change: signedPct(q.change_pct), dir: q.change_pct >= 0 ? "up" : "down" };
}

function renderFavorites() {
  favList.replaceChildren();
  noFavs.hidden = favs.list.length > 0;
  favCount.textContent = favs.list.length ? `${favs.list.length}/${MAX_FAVORITES}` : "";
  for (const f of favs.list) {
    const q = quotes.get(f.symbol);
    const line = quoteLine(q);
    const li = el("li", "fav-row");
    const open = el("button", "fav-open");
    open.type = "button";
    open.setAttribute("aria-label", `Ask how ${f.symbol} is doing`);
    const top = el("span", "fav-top");
    top.append(el("strong", "", f.symbol), el("span", `fav-change ${line.dir}`, line.change));
    const bottom = el("span", "fav-bottom", line.price);
    if (q && q.as_of) bottom.append(el("span", "fav-date", ` · ${shortDate(q.as_of)}`));
    open.append(top, bottom);
    open.addEventListener("click", () => {
      if (busy) return;
      closeSidebar();
      send(`How's ${f.symbol} doing?`);
    });

    const del = el("button", "fav-del", "♥");
    del.type = "button";
    del.setAttribute("aria-label", `Remove ${f.symbol} from favourites`);
    del.addEventListener("click", () => removeFavorite(f.symbol));
    li.append(open, del);
    favList.append(li);
  }
}

let quotesTimer;
let quotesRefreshedAt = 0;
// Fetches prices the sidebar doesn't have yet (or that went stale), in one request.
async function refreshQuotes() {
  const symbols = favSymbols().filter((sym) => !quotes.has(sym) || quotes.get(sym).stale);
  if (!symbols.length) return;
  try {
    const res = await fetch(`${API_BASE}/api/quotes?symbols=${encodeURIComponent(symbols.join(","))}`);
    if (!res.ok) throw new Error(`quotes ${res.status}`);
    for (const q of (await res.json()).quotes || []) quotes.set(q.symbol, q);
  } catch (err) {
    console.error(err);
    symbols.forEach((sym) => quotes.set(sym, { symbol: sym, error: "unavailable", stale: true }));
  }
  renderFavorites();
}

// Every 15 minutes every price is due again, and when the page comes back after longer than that.
function refreshAllQuotes() {
  clearTimeout(quotesTimer);
  quotesTimer = setTimeout(refreshAllQuotes, QUOTE_REFRESH_MS);
  quotesRefreshedAt = Date.now();
  for (const q of quotes.values()) q.stale = true;
  refreshQuotes();
}

// ---------------------------------------------------------------- rendering

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function inline(s) {
  // Input is already HTML-escaped; only add <strong>/<em>.
  return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
}

// Minimal, safe markdown: paragraphs, bullet and numbered lists, bold, italics.
// Everything is escaped first, so model output can never inject HTML.
function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const out = [];
  let list = null;
  let para = [];

  const flushPara = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    para = [];
  };
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (bullet || numbered) {
      flushPara();
      const type = bullet ? "ul" : "ol";
      if (list !== type) {
        closeList();
        out.push(`<${type}>`);
        list = type;
      }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (heading) {
      flushPara();
      closeList();
      out.push(`<p><strong>${inline(heading[1])}</strong></p>`);
    } else if (!line) {
      flushPara();
      closeList();
    } else {
      closeList();
      para.push(line);
    }
  }
  flushPara();
  closeList();
  return out.join("");
}

function scrollToBottom() {
  log.scrollTop = log.scrollHeight;
}

function addUserMessage(text) {
  empty.hidden = true;
  newChatBtn.hidden = false;
  log.append(el("div", "msg user", text));
  scrollToBottom();
}

function addAssistantMessage() {
  const wrap = el("div", "msg assistant");
  const steps = el("ul", "steps");
  const body = el("div", "body");
  body.append(el("span", "typing", "Thinking"));
  wrap.append(steps, body);
  log.append(wrap);
  scrollToBottom();
  return { wrap, steps, body };
}

// ---------------------------------------------------------------- charts

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortDate(iso, withYear) {
  const opts = { month: "short", day: "numeric", timeZone: "UTC" };
  if (withYear) opts.year = "numeric";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", opts);
}

function signedPct(n) {
  return `${n >= 0 ? "+" : "-"}${Math.abs(n).toFixed(1)}%`;
}

// A price in the stock's own currency ("$178", "¥3,407", "HK$379"). The default symbol style is used on
// purpose: the "narrow" one prints Hong Kong, Australian and Singapore dollars as a bare "$". An index has no currency, so it is
// plain points. A missing currency means a chart saved before other markets existed: those were all US dollars.
function formatPrice(value, currency, digits) {
  const cur = currency === undefined ? "USD" : currency;
  const opts = { minimumFractionDigits: digits, maximumFractionDigits: digits };
  try {
    if (!cur) return value.toLocaleString("en-US", opts);
    return value.toLocaleString("en-US", { ...opts, style: "currency", currency: cur });
  } catch (_) {
    return value.toFixed(digits); // a currency code this browser does not know
  }
}

// A price chart drawn from the daily prices the server sent. One stock shows its own currency;
// several are rebased to 100 at the start so their percentage moves can be compared.
// One stock can also be shown as candles, when the server sent its open, high and low as well.
// Built with DOM calls and textContent only, so nothing from the data can inject HTML.
function renderChart(chart) {
  const raw = (chart.series || []).filter((s) => s.closes && s.closes.length > 1);
  if (!raw.length) return null;
  const multi = raw.length > 1;
  const series = raw.map((s) => ({
    symbol: s.symbol,
    times: s.dates.map((d) => Date.parse(`${d}T00:00:00Z`)),
    values: multi ? s.closes.map((v) => (v / s.closes[0]) * 100) : s.closes,
    change: (s.closes[s.closes.length - 1] / s.closes[0] - 1) * 100,
  }));
  // Charts saved before candles existed have no open/high/low, so they stay line-only.
  const first = raw[0];
  const count = first.closes.length;
  const hasCandles = !multi && [first.opens, first.highs, first.lows].every((a) => Array.isArray(a) && a.length === count);

  // Sized to the space available so the axis text stays readable on a phone (it does not redraw on resize).
  const W = Math.max(300, Math.min(640, (log.clientWidth || 640) - 34));
  const H = Math.round(W * (W < 480 ? 0.7 : 0.47));
  const m = { l: W < 480 ? 48 : 58, r: 12, t: 12, b: 28 };
  const allTimes = series.flatMap((s) => s.times);
  const tMin = Math.min(...allTimes), tMax = Math.max(...allTimes);
  const x = (t) => m.l + ((t - tMin) / (tMax - tMin || 1)) * (W - m.l - m.r);

  const firstISO = new Date(tMin).toISOString().slice(0, 10);
  const lastISO = new Date(tMax).toISOString().slice(0, 10);
  const summary = series.map((s) => `${s.symbol} ${signedPct(s.change)}`).join(", ");

  function buildSvg(mode) {
    const candles = mode === "candles";
    // Candles reach the day's high and low, so the price axis has to cover those, not just the closes.
    const allValues = candles ? [...first.highs, ...first.lows] : series.flatMap((s) => s.values);
    let vMin = Math.min(...allValues), vMax = Math.max(...allValues);
    const pad = (vMax - vMin || 1) * 0.08;
    vMin -= pad;
    vMax += pad;
    const y = (v) => m.t + (1 - (v - vMin) / (vMax - vMin)) * (H - m.t - m.b);
    const fmtY = (v) => (multi ? v.toFixed(0) : formatPrice(v, first.currency, vMax - vMin < 20 ? 2 : 0));

    const svg = svgEl("svg", {
      viewBox: `0 0 ${W} ${H}`,
      role: "img",
      "aria-label": `${candles ? "Candlestick chart" : "Price chart"} from ${shortDate(firstISO, true)} to ${shortDate(lastISO, true)}: ${summary}`,
    });

    for (let i = 0; i < 5; i++) {
      const v = vMin + ((vMax - vMin) * i) / 4;
      svg.append(svgEl("line", { class: "grid", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      svg.append(svgEl("text", { class: "tick", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, fmtY(v)));
    }
    [[tMin, "start"], [(tMin + tMax) / 2, "middle"], [tMax, "end"]].forEach(([t, anchor]) => {
      const iso = new Date(t).toISOString().slice(0, 10);
      svg.append(svgEl("text", { class: "tick", x: x(t), y: H - 8, "text-anchor": anchor }, shortDate(iso, true)));
    });

    if (candles) {
      const step = (W - m.l - m.r) / Math.max(count - 1, 1);
      const bodyWidth = Math.max(1, Math.min(8, step * 0.6));
      series[0].times.forEach((t, j) => {
        const open = first.opens[j], close = first.closes[j];
        const dir = close >= open ? "up" : "down";
        const cx = x(t);
        const top = y(Math.max(open, close));
        svg.append(svgEl("line", { class: `wick ${dir}`, x1: cx.toFixed(1), x2: cx.toFixed(1), y1: y(first.highs[j]).toFixed(1), y2: y(first.lows[j]).toFixed(1) }));
        svg.append(svgEl("rect", {
          class: `candle ${dir}`,
          x: (cx - bodyWidth / 2).toFixed(1),
          y: top.toFixed(1),
          width: bodyWidth.toFixed(1),
          height: Math.max(1, y(Math.min(open, close)) - top).toFixed(1),
        }));
      });
    } else {
      series.forEach((s, i) => {
        const points = s.times.map((t, j) => `${x(t).toFixed(1)},${y(s.values[j]).toFixed(1)}`).join(" ");
        svg.append(svgEl("polyline", { class: `line s${(i % 5) + 1}`, points }));
      });
    }
    return svg;
  }

  const captionFor = (mode) => {
    if (multi) return "Every line starts at 100, so you can compare percentage moves.";
    if (mode === "candles") {
      return "Each candle is one day. Hollow green: it closed higher than it opened. Solid orange: it closed lower. The thin line is the day's high and low.";
    }
    return `Daily closing prices, ${shortDate(firstISO, true)} to ${shortDate(lastISO, true)}.`;
  };

  const fig = el("figure", "chart");
  let mode = "line";
  let svg = buildSvg(mode);
  const caption = el("figcaption", "", captionFor(mode));

  if (hasCandles) {
    const toggle = el("div", "chart-toggle");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "Chart style");
    const buttons = [["line", "Line"], ["candles", "Candles"]].map(([value, label]) => {
      const button = el("button", "", label);
      button.type = "button";
      button.addEventListener("click", () => {
        mode = value;
        const next = buildSvg(mode);
        svg.replaceWith(next);
        svg = next;
        caption.textContent = captionFor(mode);
        buttons.forEach(([v, b]) => b.setAttribute("aria-pressed", String(v === mode)));
      });
      toggle.append(button);
      return [value, button];
    });
    buttons.forEach(([v, b]) => b.setAttribute("aria-pressed", String(v === mode)));
    fig.append(toggle);
  }
  fig.append(svg);

  const legend = el("ul", "legend");
  series.forEach((s, i) => {
    const li = el("li");
    li.append(el("span", `swatch s${(i % 5) + 1}`), el("strong", "", s.symbol), el("span", "", signedPct(s.change)));
    legend.append(li);
  });
  fig.append(legend);
  fig.append(caption);
  return fig;
}

// Friendly status lines for each tool, so people see the agent working.
function describeTool(name, inp) {
  const sym = inp && inp.symbol;
  switch (name) {
    case "find_company":
      return `Looking up “${inp && inp.query}”`;
    case "get_stock_snapshot":
      return `Checking ${sym}'s latest numbers`;
    case "get_price_trend": {
      const period = { "6mo": "6 months", "1y": "year", "2y": "2 years" }[inp && inp.period] || "year";
      return `Pulling ${sym}'s price over the last ${period}`;
    }
    case "get_price_chart":
      return `Drawing a chart of ${((inp && inp.symbols) || []).join(", ")}`;
    case "run_backtest":
      return `Testing a simple strategy on ${sym}'s history`;
    case "add_favorite":
      return `Adding ${sym} to your favourites`;
    case "remove_favorite":
      return `Removing ${sym} from your favourites`;
    default:
      return `Running ${name}`;
  }
}

// ---------------------------------------------------------------- streaming

// Parse an SSE byte stream into {event, data} objects.
async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (dataLine) yield JSON.parse(dataLine.slice(6));
    }
  }
}

// The daily cap is shared by everyone, so say that, and say when it comes back in the reader's own time.
function dailyLimitMessage(resetsAt) {
  const when = new Date(resetsAt);
  const base = "Stock Guide has used up its questions for today.";
  if (Number.isNaN(when.getTime())) return `${base} Try again tomorrow. Your chats are saved here.`;
  const time = when.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  return `${base} It starts again ${time} your time. Your chats are saved here, so you can pick up then.`;
}

async function errorFromResponse(res) {
  if (res.status === 429) {
    try {
      const data = await res.json();
      if (data.code === "daily_limit") return dailyLimitMessage(data.resets_at);
    } catch (_) { /* not our JSON: it is the per-minute limit */ }
    return "You're sending questions quickly. Wait a minute and try again.";
  }
  if (res.status === 422) {
    try {
      const detail = (await res.json()).detail;
      const msg = Array.isArray(detail) && detail[0] && detail[0].msg;
      if (msg && /too long/.test(msg)) return "This chat has got long. Start a new chat to keep going.";
    } catch (_) { /* fall through */ }
    return "That message couldn't be sent. Try rephrasing it.";
  }
  return `The server had a problem (${res.status}). Try again in a moment.`;
}

async function send(text) {
  if (busy) return;
  busy = true;
  sendBtn.disabled = true;

  addUserMessage(text);
  const view = addAssistantMessage();
  const chat = current;
  const turnMessages = [...chat.history, { role: "user", content: text }];
  const turn = turnMessages.filter((m) => userText(m) !== null).length - 1;
  const turnCharts = []; // kept only if the turn finishes, like the history
  const stepItems = [];
  let answer = "";

  const showError = (message) => {
    view.body.querySelector(".typing")?.remove();
    view.body.append(el("p", "error", message));
  };

  try {
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: turnMessages, favorites: favSymbols() }),
    });
    if (!res.ok) {
      showError(await errorFromResponse(res));
      return;
    }

    let finished = false;
    for await (const event of readSSE(res)) {
      if (event.type === "text") {
        answer += event.text;
        view.body.innerHTML = renderMarkdown(answer);
      } else if (event.type === "tool_call") {
        const li = el("li", "", describeTool(event.name, event.input));
        stepItems.push({ name: event.name, li });
        view.steps.append(li);
      } else if (event.type === "tool_result") {
        const item = stepItems.find((s) => s.name === event.name && !s.li.className);
        if (item) item.li.className = event.ok ? "ok" : "fail";
      } else if (event.type === "favorite") {
        if (event.action === "add") addFavorite(event.symbol);
        else if (event.action === "remove") removeFavorite(event.symbol);
      } else if (event.type === "favorite_failed") {
        const p = el("p", "fav-error", event.message || `Failed to add ${event.symbol} to favourites.`);
        p.setAttribute("role", "alert");
        view.wrap.append(p);
      } else if (event.type === "chart") {
        const fig = renderChart(event);
        if (fig) view.wrap.insertBefore(fig, view.body);
        turnCharts.push({ turn, chart: event });
      } else if (event.type === "done") {
        chat.history = [...turnMessages, ...event.messages];
        chat.charts = [...chat.charts, ...turnCharts];
        chat.title = chat.title || titleFrom(text);
        chat.updated = Date.now();
        saveChat(chat);
        renderList();
        finished = true;
      } else if (event.type === "error") {
        showError(event.message);
        finished = true;
      }
      scrollToBottom();
    }
    if (!finished) showError("The connection dropped before the answer finished. Try again.");
  } catch (err) {
    console.error(err);
    showError("Couldn't reach the server. Check your connection and try again.");
  } finally {
    if (!answer) view.body.querySelector(".typing")?.remove();
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

// ---------------------------------------------------------------- input

function autosize() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  input.value = "";
  autosize();
  send(text);
});

input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => send(chip.textContent));
});

// ---------------------------------------------------------------- chat list

// A person's own message (a string, or blocks containing text), as opposed to the
// tool results the API also sends with role "user". Returns null for those.
function userText(m) {
  if (m.role !== "user") return null;
  if (typeof m.content === "string") return m.content;
  const text = (Array.isArray(m.content) ? m.content : []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
  return text || null;
}

function assistantText(m) {
  if (typeof m.content === "string") return m.content;
  return (Array.isArray(m.content) ? m.content : []).filter((b) => b.type === "text").map((b) => b.text).join("");
}

function clearLog() {
  log.querySelectorAll(".msg").forEach((m) => m.remove());
}

// Rebuilds a saved conversation: one question and one answer per turn. The "looking up…"
// steps are not kept, only the words and charts.
function renderSaved(chat) {
  clearLog();
  const turns = [];
  for (const m of chat.history) {
    const asked = userText(m);
    if (asked !== null) turns.push({ asked, parts: [] });
    else if (m.role === "assistant" && turns.length) {
      const said = assistantText(m);
      if (said) turns[turns.length - 1].parts.push(said);
    }
  }
  turns.forEach((t, i) => {
    addUserMessage(t.asked);
    const wrap = el("div", "msg assistant");
    for (const c of chat.charts.filter((c) => c.turn === i)) {
      const fig = renderChart(c.chart);
      if (fig) wrap.append(fig);
    }
    const body = el("div", "body");
    body.innerHTML = renderMarkdown(t.parts.join("\n\n"));
    wrap.append(body);
    log.append(wrap);
  });
  empty.hidden = turns.length > 0;
  newChatBtn.hidden = turns.length === 0;
  scrollToBottom();
}

function renderList() {
  chatList.replaceChildren();
  noChats.hidden = chats.length > 0;
  for (const c of chats) {
    const li = el("li", c.id === current.id ? "chat-row current" : "chat-row");
    const open = el("button", "chat-open", c.title || "Untitled chat");
    open.type = "button";
    if (c.id === current.id) open.setAttribute("aria-current", "true");
    open.addEventListener("click", () => openChat(c.id));

    // Two taps to delete, so a stray touch can't lose a conversation.
    const del = el("button", "chat-del", "✕");
    del.type = "button";
    del.setAttribute("aria-label", `Delete chat: ${c.title}`);
    let timer;
    del.addEventListener("click", () => {
      if (del.classList.contains("armed")) {
        clearTimeout(timer);
        if (busy && c.id === current.id) return;
        deleteChat(c.id);
        return;
      }
      del.classList.add("armed");
      del.textContent = "Delete?";
      del.setAttribute("aria-label", `Confirm delete: ${c.title}`);
      timer = setTimeout(() => {
        del.classList.remove("armed");
        del.textContent = "✕";
        del.setAttribute("aria-label", `Delete chat: ${c.title}`);
      }, 3000);
    });
    li.append(open, del);
    chatList.append(li);
  }
}

function openChat(id) {
  if (busy) return;
  const chat = chats.find((c) => c.id === id);
  if (chat && chat.id !== current.id) {
    current = chat;
    renderSaved(chat);
    renderList();
  }
  closeSidebar();
  input.focus();
}

function startNewChat() {
  if (busy) return;
  current = newChat();
  clearLog();
  empty.hidden = false;
  newChatBtn.hidden = true;
  renderList();
  closeSidebar();
  input.focus();
}

// On a phone the list is a drawer; on a wide screen it is always showing and these do nothing visible.
function openSidebar() {
  sidebar.classList.add("open");
  backdrop.hidden = false;
  chatsToggle.setAttribute("aria-expanded", "true");
}

function closeSidebar() {
  sidebar.classList.remove("open");
  backdrop.hidden = true;
  chatsToggle.setAttribute("aria-expanded", "false");
}

newChatBtn.addEventListener("click", startNewChat);
chatsToggle.addEventListener("click", openSidebar);
closeSidebarBtn.addEventListener("click", closeSidebar);
backdrop.addEventListener("click", closeSidebar);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSidebar();
});
// Another tab saved or deleted a chat.
window.addEventListener("storage", (e) => {
  if (e.key === FAV_KEY) {
    favs = readFavs();
    renderFavorites();
    refreshQuotes();
  }
  if (e.key !== STORE_KEY) return;
  chats = readStore();
  renderList();
});
renderList();
renderFavorites();
refreshAllQuotes();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - quotesRefreshedAt > QUOTE_REFRESH_MS) refreshAllQuotes();
});

// Lets the page be installed on a phone's home screen. Skipped on file:// and old browsers.
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
