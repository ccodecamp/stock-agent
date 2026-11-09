// Stock Guide chat client. No framework, no build step.
//
// Talks to POST /api/chat, which streams Server-Sent Events:
//   text        {text}            piece of the answer
//   tool_call   {name, input}     Claude is calling a tool
//   tool_result {name, ok}        the tool finished
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

let history = [];
let busy = false;

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
    case "run_backtest":
      return `Testing a simple strategy on ${sym}'s history`;
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

async function errorFromResponse(res) {
  if (res.status === 429) return "You're sending questions quickly. Wait a minute and try again.";
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
  const turnMessages = [...history, { role: "user", content: text }];
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
      body: JSON.stringify({ messages: turnMessages }),
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
      } else if (event.type === "done") {
        history = [...turnMessages, ...event.messages];
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

newChatBtn.addEventListener("click", () => {
  if (busy) return;
  history = [];
  log.querySelectorAll(".msg").forEach((m) => m.remove());
  empty.hidden = false;
  newChatBtn.hidden = true;
  input.focus();
});
