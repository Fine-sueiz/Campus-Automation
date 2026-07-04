const state = {
  me: null,
  opportunities: [],
  activeTab: "feed"
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || data.message || `请求失败：${response.status}`);
  }
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => node.classList.add("hidden"), 2600);
}

function showLoggedIn(isLoggedIn) {
  $("#authView").classList.toggle("hidden", isLoggedIn);
  $("#mainView").classList.toggle("hidden", !isLoggedIn);
  $("#logoutBtn").classList.toggle("hidden", !isLoggedIn);
}

function fillForm(form, data) {
  Object.entries(data || {}).forEach(([key, value]) => {
    const input = form.elements[key];
    if (!input) return;
    input.value = typeof value === "string" ? value : JSON.stringify(value || [], null, 2);
  });
}

async function loadMe() {
  try {
    const data = await api("/api/auth/me");
    state.me = data;
    showLoggedIn(true);
    $("#welcomeText").textContent = `${data.user.display_name}，这是按你的课表筛出来的机会。`;
    $("#adminTab").classList.toggle("hidden", data.user.role !== "admin");
    fillForm($("#profileForm"), {
      ...data.profile,
      answers: JSON.stringify(data.profile.answers || [], null, 2)
    });
    fillForm($("#scheduleForm"), {
      day_start: data.schedule.day_start || "08:00",
      day_end: data.schedule.day_end || "22:00",
      busy_text: data.busy_text || ""
    });
    await loadOpportunities();
    if (data.user.role === "admin") await loadAdmin();
  } catch (_error) {
    showLoggedIn(false);
  }
}

function setTab(tab) {
  state.activeTab = tab;
  $$(".tabs button").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  $$(".tab-page").forEach((page) => page.classList.add("hidden"));
  const pageId = tab === "admin" ? "#adminTabPage" : `#${tab}Tab`;
  $(pageId).classList.remove("hidden");
}

function statusBadge(item) {
  const status = item.user_schedule_status || item.schedule_status;
  const label = status === "available" ? "有空" : status === "conflict" ? "冲突" : "时间不确定";
  const cls = status === "conflict" ? "conflict" : status === "unknown_time" ? "unknown" : "";
  return `<span class="badge ${cls}">${label}</span>`;
}

function decisionLabel(status) {
  return {
    pending_decision: "待确认",
    approved: "已参加",
    rejected: "不参加",
    later: "稍后看",
    need_human: "人工处理",
    submitted: "已提交",
    failed: "失败"
  }[status] || status || "待确认";
}

function renderOpportunities() {
  const container = $("#opportunityList");
  if (!state.opportunities.length) {
    container.innerHTML = `<div class="panel"><p class="muted">还没有机会。管理员可以先点“本地扫描一次”。</p></div>`;
    return;
  }
  container.innerHTML = state.opportunities.map((item) => `
    <article class="op-card" id="op-${item.id}">
      <header>
        <div>
          <h3>${escapeHtml(item.title || "未命名机会")}</h3>
          <p class="muted">${escapeHtml(item.source_name || "")}｜${escapeHtml(item.category || "")}｜${decisionLabel(item.user_status)}</p>
        </div>
        ${statusBadge(item)}
      </header>
      <div class="meta-grid">
        <div>时间：${escapeHtml(item.activity_time || "未识别")}</div>
        <div>地点：${escapeHtml(item.location || "未识别")}</div>
        <div>截止：${escapeHtml(item.deadline || "未识别")}</div>
        <div>报名：${item.signup_url ? "已识别" : "未识别"}</div>
      </div>
      ${item.user_matched_time_text ? `<pre class="status-box">${escapeHtml(item.user_matched_time_text)}</pre>` : ""}
      <div class="actions">
        <button data-decision="join" data-id="${item.id}">参加并报名</button>
        <button class="secondary" data-decision="later" data-id="${item.id}">稍后看</button>
        <button class="secondary" data-decision="reject" data-id="${item.id}">不参加</button>
        <button class="warning" data-decision="manual" data-id="${item.id}">人工处理</button>
        <a href="${escapeAttr(item.article_url || "#")}" target="_blank" rel="noopener"><button class="secondary" type="button">原文</button></a>
      </div>
    </article>
  `).join("");
  $$("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => decide(button.dataset.id, button.dataset.decision));
  });
}

async function loadOpportunities() {
  const data = await api("/api/opportunities");
  state.opportunities = data.items || [];
  renderOpportunities();
}

async function decide(opportunityId, decision) {
  const data = await api(`/api/opportunities/${opportunityId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision })
  });
  toast(data.task_id ? `已创建报名任务：${data.task_id}` : `状态：${data.status}`);
  await loadOpportunities();
}

async function loadAdmin() {
  const [feeds, users, logs] = await Promise.all([
    api("/api/admin/feeds"),
    api("/api/admin/users"),
    api("/api/admin/logs")
  ]);
  $("#feedList").innerHTML = (feeds.items || []).map((item) => `
    <div class="list-row">
      <strong>${escapeHtml(item.name)}</strong>
      <p class="muted">${escapeHtml(item.url)}</p>
    </div>
  `).join("") || `<p class="muted">暂无自定义 feed，当前使用 config/app.yml。</p>`;
  $("#userList").innerHTML = (users.items || []).map((item) => `
    <div class="list-row">
      <strong>${escapeHtml(item.display_name)}</strong>
      <p class="muted">${escapeHtml(item.username)}｜${escapeHtml(item.role)}</p>
    </div>
  `).join("");
  $("#logList").textContent = JSON.stringify(logs.items || [], null, 2);
}

async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    $("#pushStatus").textContent = "当前浏览器不支持 Web Push，可继续使用站内机会列表。";
    return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    $("#pushStatus").textContent = `通知权限：${permission}`;
    return;
  }
  const registration = await navigator.serviceWorker.ready;
  const keyData = await api("/api/push/public-key");
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription && keyData.public_key) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyData.public_key)
    });
  }
  if (!subscription) {
    subscription = {
      endpoint: `fake-local-${Date.now()}`,
      keys: { p256dh: "fake", auth: "fake" }
    };
  }
  const result = await api("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({ subscription })
  });
  $("#pushStatus").textContent = JSON.stringify({ mode: keyData.mode, result }, null, 2);
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

function bindEvents() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
  $$(".tabs button").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
  $("#refreshBtn").addEventListener("click", loadOpportunities);
  $("#logoutBtn").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    showLoggedIn(false);
  });
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    await api("/api/auth/login", { method: "POST", body: JSON.stringify(body) });
    toast("登录成功");
    await loadMe();
  });
  $("#registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    await api("/api/auth/register", { method: "POST", body: JSON.stringify(body) });
    toast("注册成功");
    await loadMe();
  });
  $("#profileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    try {
      body.answers = body.answers ? JSON.parse(body.answers) : [];
    } catch (_error) {
      toast("常用问卷答案 JSON 格式不对");
      return;
    }
    await api("/api/profile", { method: "PUT", body: JSON.stringify(body) });
    toast("资料已保存");
  });
  $("#scheduleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    await api("/api/schedule", { method: "PUT", body: JSON.stringify(body) });
    toast("课表已保存");
  });
  $("#enablePushBtn").addEventListener("click", enablePush);
  $("#testPushBtn").addEventListener("click", async () => {
    const result = await api("/api/push/test", { method: "POST" });
    $("#pushStatus").textContent = JSON.stringify(result, null, 2);
  });
  $("#feedForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    await api("/api/admin/feeds", { method: "POST", body: JSON.stringify({ ...body, enabled: true }) });
    event.target.reset();
    toast("feed 已添加");
    await loadAdmin();
  });
  $("#scanBtn").addEventListener("click", async () => {
    const result = await api("/admin/scan-once", { method: "POST" });
    toast(`扫描完成，机会 ${result.opportunities || 0} 条`);
    await Promise.all([loadOpportunities(), loadAdmin()]);
  });
}

bindEvents();
loadMe();
