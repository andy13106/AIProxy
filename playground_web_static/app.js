const S = {
  providers: [],
  sessions: {},
  currentSessionId: null,
  pendingAttachments: [],
  currentStreamId: null,
};

const $ = (id) => document.getElementById(id);

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
    const el = document.createElement("div");
    el.className = `session-item ${sid === S.currentSessionId ? "active" : ""}`;
    el.textContent = s.title || "新对话";
    el.onclick = () => {
      S.currentSessionId = sid;
      renderAll();
    };
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
  for (const att of S.pendingAttachments) {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.textContent = `${att.filename}`;
    tray.appendChild(chip);
  }
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
}

bootstrap().then(bindEvents);
