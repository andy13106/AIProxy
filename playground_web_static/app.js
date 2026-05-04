const S = {
  providers: [],
  sessions: {},
  currentSessionId: null,
  pendingAttachments: [],
  currentStreamId: null,
};

const $ = (id) => document.getElementById(id);

function applyThemeFromQuery() {
  const theme = new URLSearchParams(window.location.search).get("theme");
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
    return;
  }
  document.documentElement.removeAttribute("data-theme");
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

function renderSessions() {
  const box = $("sessionList");
  box.innerHTML = "";
  const ids = Object.keys(S.sessions).reverse();
  for (const sid of ids) {
    const s = S.sessions[sid];
    const title = s.title || "新对话";
    const el = document.createElement("div");
    el.className = `session-item ${sid === S.currentSessionId ? "active" : ""}`;
    el.style.position = "relative";
    el.onclick = () => {
      S.currentSessionId = sid;
      renderAll();
    };

    const titleEl = document.createElement("span");
    titleEl.className = "session-title";
    titleEl.textContent = title;
    titleEl.style.paddingRight = "54px";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "session-delete";
    delBtn.title = "删除会话";
    delBtn.textContent = "删除";
    delBtn.style.cssText = [
      "display:inline-flex",
      "align-items:center",
      "justify-content:center",
      "position:absolute",
      "right:10px",
      "top:50%",
      "transform:translateY(-50%)",
      "width:42px",
      "height:26px",
      "min-width:42px",
      "padding:0",
      "margin:0",
      "border-radius:8px",
      "border:1px solid rgba(236,101,101,0.55)",
      "background:rgba(96,30,30,0.62)",
      "color:#ffdede",
      "font-size:12px",
      "font-weight:600",
      "line-height:1",
      "cursor:pointer",
      "opacity:1",
      "visibility:visible",
      "flex:0 0 auto",
      "z-index:2"
    ].join(";");
    delBtn.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm("确认删除这个会话吗？")) return;
      await api(`/playground-api/session/${encodeURIComponent(sid)}`, { method: "DELETE" });
      delete S.sessions[sid];
      if (S.currentSessionId === sid) {
        const remain = Object.keys(S.sessions);
        if (remain.length) {
          S.currentSessionId = remain[remain.length - 1];
        } else {
          await bootstrap();
        }
      }
      renderAll();
    };
    el.appendChild(titleEl);
    el.appendChild(delBtn);
    box.appendChild(el);
  }
}

function renderProviders() {
  const providerSelect = $("providerSelect");
  const modelSelect = $("modelSelect");
  const cur = currentSession();
  providerSelect.innerHTML = "";
  for (const p of S.providers) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    providerSelect.appendChild(opt);
  }
  if (cur?.provider) providerSelect.value = cur.provider;
  const selectedProvider = S.providers.find((p) => p.name === providerSelect.value);
  modelSelect.innerHTML = "";
  for (const m of (selectedProvider?.models || [])) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    modelSelect.appendChild(opt);
  }
  if (cur?.model) modelSelect.value = cur.model;
}

function renderMessages() {
  const box = $("messages");
  box.innerHTML = "";
  const cur = currentSession();
  if (!cur || !(cur.messages || []).length) {
    box.innerHTML = `<div id="emptyState" class="empty-state"><h2>What can I help with?</h2><p>选择模型、上传附件，然后开始对话。</p></div>`;
    return;
  }
  for (const msg of cur.messages || []) {
    const row = document.createElement("div");
    row.className = `msg ${msg.role === "user" ? "user" : "assistant"}`;
    if (msg.role === "assistant") {
      const wrap = document.createElement("div");
      wrap.className = "assistant-wrap";
      const meta = document.createElement("div");
      meta.className = "assistant-meta";
      meta.innerHTML = `<span class="dot">AI</span><span>Assistant</span>`;
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.innerHTML = renderMarkdown(msg.content || "");
      wrap.appendChild(meta);
      wrap.appendChild(bubble);
      row.appendChild(wrap);
    } else {
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.innerHTML = escapeHtml(msg.content || "");
      row.appendChild(bubble);
    }
    box.appendChild(row);
  }
  box.scrollTop = box.scrollHeight;
}

function renderMarkdown(text) {
  const src = String(text || "");
  const codeFence = /```(\w+)?\n([\s\S]*?)```/g;
  let out = "";
  let cursor = 0;
  let match;
  while ((match = codeFence.exec(src)) !== null) {
    const before = src.slice(cursor, match.index);
    if (before) out += `<p>${escapeHtml(before).replace(/\n/g, "<br>")}</p>`;
    const lang = escapeHtml(match[1] || "text");
    const code = escapeHtml(match[2] || "");
    out += `<pre><code class="lang-${lang}">${code}</code></pre>`;
    cursor = match.index + match[0].length;
  }
  const tail = src.slice(cursor);
  if (tail) out += `<p>${escapeHtml(tail).replace(/\n/g, "<br>")}</p>`;
  return out || "<p></p>";
}

function renderAttachTray() {
  const tray = $("attachTray");
  tray.innerHTML = "";
  S.pendingAttachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.innerHTML = `<span>${escapeHtml(att.filename || "附件")}</span><button class="attach-remove" data-idx="${idx}" title="移除">✕</button>`;
    tray.appendChild(chip);
  });
  tray.querySelectorAll(".attach-remove").forEach((btn) => {
    btn.style.cssText = [
      "display:inline-flex",
      "align-items:center",
      "justify-content:center",
      "width:18px",
      "height:18px",
      "min-width:18px",
      "padding:0",
      "border:none",
      "border-radius:999px",
      "background:rgba(255,255,255,0.12)",
      "color:#ffffff",
      "font-size:12px",
      "line-height:1",
      "cursor:pointer",
      "opacity:1",
      "visibility:visible"
    ].join(";");
    btn.onclick = () => {
      const idx = Number(btn.dataset.idx);
      if (Number.isNaN(idx)) return;
      S.pendingAttachments.splice(idx, 1);
      renderAttachTray();
    };
  });
}

function renderAll() {
  renderSessions();
  renderProviders();
  renderMessages();
  renderAttachTray();
}

function currentSession() {
  return S.sessions[S.currentSessionId];
}

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `HTTP ${res.status}`);
  }
  return await res.json();
}

async function bootstrap() {
  const data = await api("/playground-api/bootstrap");
  S.providers = data.providers || [];
  S.sessions = data.sessions || {};
  S.currentSessionId = data.current_session_id;
  renderAll();
}

async function uploadFiles(files) {
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/playground-api/upload", { method: "POST", body: form });
    if (!res.ok) continue;
    const data = await res.json();
    S.pendingAttachments.push(data);
  }
  renderAttachTray();
}

function bindEvents() {
  $("newSessionBtn").onclick = async () => {
    const provider = $("providerSelect").value;
    const model = $("modelSelect").value;
    const data = await api("/playground-api/session/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    });
    S.sessions[data.session.id] = data.session;
    S.currentSessionId = data.session.id;
    renderAll();
  };

  $("providerSelect").onchange = () => {
    const cur = currentSession();
    if (!cur) return;
    cur.provider = $("providerSelect").value;
    renderProviders();
  };

  $("modelSelect").onchange = () => {
    const cur = currentSession();
    if (!cur) return;
    cur.model = $("modelSelect").value;
  };

  $("uploadBtn").onclick = () => $("fileInput").click();
  $("fileInput").onchange = async (e) => {
    await uploadFiles(Array.from(e.target.files || []));
    $("fileInput").value = "";
  };

  $("stopBtn").onclick = async () => {
    if (S.currentStreamId) {
      await api(`/playground-api/chat/stop/${encodeURIComponent(S.currentStreamId)}`, { method: "POST" });
    }
  };

  $("sendBtn").onclick = sendMessage;
}

async function sendMessage() {
  const cur = currentSession();
  if (!cur) return;
  const promptEl = $("promptInput");
  const text = (promptEl.value || "").trim();
  if (!text && !S.pendingAttachments.length) return;
  const provider = $("providerSelect").value;
  const model = $("modelSelect").value;

  const userMsg = {
    role: "user",
    content: text,
    attachments: [...S.pendingAttachments],
  };
  cur.provider = provider;
  cur.model = model;
  cur.messages.push(userMsg);
  const assistantMsg = { role: "assistant", content: "" };
  cur.messages.push(assistantMsg);
  promptEl.value = "";
  const attachmentIds = S.pendingAttachments.map((x) => x.id);
  S.pendingAttachments = [];
  renderAll();
  setBusy(true);

  const resp = await fetch("/playground-api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: S.currentSessionId,
      provider,
      model,
      prompt: text,
      attachment_ids: attachmentIds,
    }),
  });
  if (!resp.ok || !resp.body) {
    setBusy(false);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let currentEvent = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() || "";
    for (const ch of chunks) {
      const lines = ch.split("\n");
      let dataStr = "";
      for (const line of lines) {
        if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
        if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (!dataStr) continue;
      let data;
      try { data = JSON.parse(dataStr); } catch { continue; }
      if (currentEvent === "ready" && data.stream_id) {
        S.currentStreamId = data.stream_id;
      }
      if (currentEvent === "token" && data.text) {
        assistantMsg.content += data.text;
        renderMessages();
      }
      if (currentEvent === "done") {
        S.currentStreamId = null;
      }
    }
  }
  setBusy(false);
  await bootstrap();
}

function setBusy(busy) {
  $("sendBtn").disabled = !!busy;
  $("uploadBtn").disabled = !!busy;
  $("providerSelect").disabled = !!busy;
  $("modelSelect").disabled = !!busy;
  $("stopBtn").disabled = !S.currentStreamId;
}

function bindInputUX() {
  const prompt = $("promptInput");
  if (!prompt) return;
  const resize = () => {
    prompt.style.height = "auto";
    prompt.style.height = `${Math.min(prompt.scrollHeight, 120)}px`;
  };
  prompt.addEventListener("input", resize);
  prompt.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      await sendMessage();
    }
  });
  resize();
}

applyThemeFromQuery();

function fitLayout() {
  const h = window.innerHeight;
  document.documentElement.style.height = h + "px";
  document.body.style.height = h + "px";
}
fitLayout();
window.addEventListener("resize", fitLayout);

bootstrap().then(() => {
  bindEvents();
  bindInputUX();
  setBusy(false);
});
