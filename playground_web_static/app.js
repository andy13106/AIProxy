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
    el.onclick = () => {
      S.currentSessionId = sid;
      S.pendingAttachments = [];
      renderAll();
    };

    const titleEl = document.createElement("span");
    titleEl.className = "session-title";
    titleEl.textContent = title;

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "session-delete";
    delBtn.title = "删除会话";
    delBtn.textContent = "✕";
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
        S.pendingAttachments = [];
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
  
  updateProviderChip();
  updateModelChip();
}

function updateProviderChip() {
  const providerSelect = $("providerSelect");
  const providerLabel = $("providerLabel");
  const providerChip = $("providerChip");
  if (providerLabel && providerSelect.value) {
    providerLabel.textContent = providerSelect.value;
    providerChip.classList.add("active");
  }
}

function updateModelChip() {
  const modelSelect = $("modelSelect");
  const modelLabel = $("modelLabel");
  const modelChip = $("modelChip");
  if (modelLabel && modelSelect.value) {
    modelLabel.textContent = modelSelect.value;
    modelChip.classList.add("active");
  }
}

function renderMessages() {
  const box = $("messages");
  const inner = $("msgInner");
  const emptyState = $("emptyState");
  inner.innerHTML = "";
  const cur = currentSession();
  if (!cur || !(cur.messages || []).length) {
    if (emptyState) emptyState.style.display = "flex";
    return;
  }
  if (emptyState) emptyState.style.display = "none";
  for (const msg of cur.messages || []) {
    const row = document.createElement("div");
    row.className = "msg-row";
    row.setAttribute("data-role", msg.role);
    
    const role = document.createElement("div");
    role.className = `msg-role ${msg.role === "user" ? "user" : "assistant"}`;
    
    const icon = document.createElement("span");
    icon.className = `role-icon ${msg.role === "user" ? "user" : "assistant"}`;
    icon.textContent = msg.role === "user" ? "U" : "A";
    
    const roleText = document.createElement("span");
    roleText.textContent = msg.role === "user" ? "You" : "Assistant";
    
    role.appendChild(icon);
    role.appendChild(roleText);
    
    const body = document.createElement("div");
    body.className = "msg-body";
    
    if (msg.role === "assistant") {
      body.innerHTML = renderMarkdown(msg.content || "");
    } else {
      body.innerHTML = `<p>${escapeHtml(msg.content || "")}</p>`;
    }
    
    if (msg.attachments && msg.attachments.length > 0) {
      const attachDiv = document.createElement("div");
      attachDiv.style.marginTop = "8px";
      attachDiv.style.display = "flex";
      attachDiv.style.gap = "6px";
      attachDiv.style.flexWrap = "wrap";
      for (const att of msg.attachments) {
        const chip = document.createElement("span");
        chip.className = "attach-chip";
        chip.style.display = "inline-flex";
        chip.textContent = att.filename || att.name || "附件";
        attachDiv.appendChild(chip);
      }
      body.appendChild(attachDiv);
    }
    
    row.appendChild(role);
    row.appendChild(body);
    inner.appendChild(row);
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
    if (before) {
      const paragraphs = before.split(/\n\n+/);
      for (const p of paragraphs) {
        if (p.trim()) {
          out += `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`;
        }
      }
    }
    const lang = escapeHtml(match[1] || "text");
    const code = escapeHtml(match[2] || "");
    out += `<pre><code class="lang-${lang}">${code}</code></pre>`;
    cursor = match.index + match[0].length;
  }
  const tail = src.slice(cursor);
  if (tail) {
    const paragraphs = tail.split(/\n\n+/);
    for (const p of paragraphs) {
      if (p.trim()) {
        out += `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`;
      }
    }
  }
  return out || "<p></p>";
}

function renderAttachTray() {
  const tray = $("attachTray");
  tray.innerHTML = "";
  if (S.pendingAttachments.length > 0) {
    tray.classList.add("has-files");
  } else {
    tray.classList.remove("has-files");
  }
  S.pendingAttachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.innerHTML = `<span>${escapeHtml(att.filename || "附件")}</span><button class="attach-remove" data-idx="${idx}" title="移除">✕</button>`;
    tray.appendChild(chip);
  });
  tray.querySelectorAll(".attach-remove").forEach((btn) => {
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
    S.pendingAttachments = [];
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
    updateModelChip();
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

  bindDropdownEvents();
}

function bindDropdownEvents() {
  const providerChip = $("providerChip");
  const modelChip = $("modelChip");
  const providerDropdown = $("providerDropdown");
  const modelDropdown = $("modelDropdown");

  function closeAllDropdowns() {
    if (providerDropdown) providerDropdown.classList.remove("open");
    if (modelDropdown) modelDropdown.classList.remove("open");
  }

  function populateProviderDropdown() {
    if (!providerDropdown) return;
    providerDropdown.innerHTML = "";
    for (const p of S.providers) {
      const opt = document.createElement("div");
      opt.className = `provider-option ${p.name === $("providerSelect").value ? "selected" : ""}`;
      opt.textContent = p.name;
      opt.onclick = () => {
        $("providerSelect").value = p.name;
        const cur = currentSession();
        if (cur) cur.provider = p.name;
        renderProviders();
        closeAllDropdowns();
      };
      providerDropdown.appendChild(opt);
    }
  }

  function populateModelDropdown() {
    if (!modelDropdown) return;
    modelDropdown.innerHTML = "";
    const selectedProvider = S.providers.find((p) => p.name === $("providerSelect").value);
    const models = selectedProvider?.models || [];
    for (const m of models) {
      const opt = document.createElement("div");
      opt.className = `model-option ${m === $("modelSelect").value ? "selected" : ""}`;
      opt.textContent = m;
      opt.onclick = () => {
        $("modelSelect").value = m;
        const cur = currentSession();
        if (cur) cur.model = m;
        updateModelChip();
        closeAllDropdowns();
      };
      modelDropdown.appendChild(opt);
    }
  }

  if (providerChip) {
    providerChip.onclick = (e) => {
      e.stopPropagation();
      populateProviderDropdown();
      if (providerDropdown) {
        const isOpen = providerDropdown.classList.contains("open");
        closeAllDropdowns();
        if (!isOpen) providerDropdown.classList.add("open");
      }
    };
  }

  if (modelChip) {
    modelChip.onclick = (e) => {
      e.stopPropagation();
      populateModelDropdown();
      if (modelDropdown) {
        const isOpen = modelDropdown.classList.contains("open");
        closeAllDropdowns();
        if (!isOpen) modelDropdown.classList.add("open");
      }
    };
  }

  document.addEventListener("click", (e) => {
    if (!providerDropdown?.contains(e.target) && !providerChip?.contains(e.target) &&
        !modelDropdown?.contains(e.target) && !modelChip?.contains(e.target)) {
      closeAllDropdowns();
    }
  });
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
        setBusy(true);
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
  
  const stopBtn = $("stopBtn");
  if (stopBtn) {
    stopBtn.style.display = S.currentStreamId ? "inline-flex" : "none";
  }
}

function showToast(msg) {
  const toast = document.createElement("div");
  toast.className = "paste-toast";
  toast.textContent = msg;
  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.add("show");
    setTimeout(() => {
      toast.classList.remove("show");
      setTimeout(() => toast.remove(), 300);
    }, 2000);
  });
}

function bindInputUX() {
  const prompt = $("promptInput");
  if (!prompt) return;
  const resize = () => {
    prompt.style.height = "auto";
    prompt.style.height = `${Math.min(prompt.scrollHeight, 200)}px`;
  };
  prompt.addEventListener("input", resize);
  prompt.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      await sendMessage();
    }
  });

  // ── 剪贴板粘贴图片支持 ──
  prompt.addEventListener("paste", async (e) => {
    const clipboardData = e.clipboardData;
    if (!clipboardData) return;

    const items = clipboardData.items;
    if (!items) return;

    const imageItems = [];
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        imageItems.push(item);
      }
    }

    if (imageItems.length === 0) return; // 没有图片，走默认粘贴文本逻辑

    e.preventDefault();

    // 如果剪贴板中同时有文本，手动插入到光标位置
    const textContent = clipboardData.getData("text/plain");
    if (textContent) {
      const start = prompt.selectionStart;
      const end = prompt.selectionEnd;
      const before = prompt.value.substring(0, start);
      const after = prompt.value.substring(end);
      prompt.value = before + textContent + after;
      prompt.selectionStart = prompt.selectionEnd = start + textContent.length;
      prompt.dispatchEvent(new Event("input", { bubbles: true }));
    }

    // 将图片 blob 转为 File 对象并上传
    const files = imageItems
      .map((item) => {
        const blob = item.getAsFile();
        if (!blob) return null;
        const ext = item.type.split("/")[1] || "png";
        const name = `clipboard_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.${ext}`;
        return new File([blob], name, { type: item.type });
      })
      .filter(Boolean);

    if (files.length > 0) {
      await uploadFiles(files);
      showToast(`已粘贴 ${files.length} 张图片`);
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
