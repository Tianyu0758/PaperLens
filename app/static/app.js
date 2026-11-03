/*
 * 文件说明：统一工作台的前端控制逻辑，管理会话切换、问题提交、论文上传和任务状态恢复。
 * 订阅 SSE 并配合轮询展示进度，组织引用与文件列表，提供 HTML 预览、下载和版本查看。
 * 本机记录标识和界面偏好保存在浏览器，实际会话与产物仍由后端持久化。
 */

import {
  escapeHTML as esc,
  icon,
  markdown,
  bytes,
  duration,
  timeLabel,
  statusLabel,
  stageLabel,
  toolLabel,
} from "./format.js";

const $ = (id) => document.getElementById(id);
const ACTIVE = new Set(["queued", "running"]);
const TERMINAL = new Set(["completed", "failed", "interrupted"]);
const EVENTS = [
  "run.queued",
  "run.started",
  "stage.changed",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "artifact.created",
  "run.completed",
  "run.failed",
  "run.interrupted",
];
const uid = () =>
  crypto.randomUUID?.() ||
  `${Date.now()}-${Math.random().toString(16).slice(2)}`;
function read(store, key, fallback) {
  try {
    return JSON.parse(store.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
let storageNotice = false;
function write(store, key, value) {
  try {
    store.setItem(key, JSON.stringify(value));
  } catch {
    if (!storageNotice) {
      storageNotice = true;
      toast("浏览器存储不可用，刷新前请记下会话记录标识", true);
    }
  }
}
const state = {
  user: read(localStorage, "paperlens.user", null) || `u_${uid()}`,
  sessions: [],
  papers: [],
  session: null,
  runs: [],
  messages: [],
  artifacts: [],
  selectedPaper: "",
  mode: "qa",
  page: "chat",
  tab: "paper",
  selectedRun: "",
  events: new Map(),
  submitting: false,
  loading: false,
  epoch: 0,
  historyRevision: 0,
  before: null,
  moreSessions: false,
  paperError: "",
  sourceRun: "",
  sourceCitations: [],
  upload: null,
};
let preferences = {},
  stream = null,
  pollTimer = null,
  toastTimer = null,
  uploadTimer = null,
  healthTimer = null;
const prefKey = () => `paperlens.preferences.${state.user}`;
const eventKey = (runId) => `paperlens.events.${state.user}.${runId}`;
function persist() {
  Object.assign(preferences, {
    session: state.session?.session_id || "",
    paper: state.selectedPaper,
    mode: state.mode,
    page: state.page,
    tab: state.tab,
  });
  write(localStorage, prefKey(), preferences);
}
function owned(path, params = {}) {
  const query = new URLSearchParams({ user_id: state.user, ...params });
  return `${path}?${query}`;
}
function downloadURL(id) {
  return owned(`/artifacts/${encodeURIComponent(id)}`);
}
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 6000);
}
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store", ...options });
  } catch {
    throw new Error("网络连接中断，请重试；相同提交不会重复执行任务");
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const data = await response.json();
      message =
        typeof data.detail === "string"
          ? data.detail
          : "参数不完整，请检查填写内容";
    } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}
const jsonOptions = (body, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
function busyRun() {
  return state.runs.find((run) => ACTIVE.has(run.status));
}
function draftKey() {
  return state.session?.session_id || "new";
}
function saveDraft() {
  preferences.drafts ||= {};
  preferences.drafts[draftKey()] = $("question").value;
  persist();
  updateComposer();
}
function restoreDraft() {
  $("question").value = preferences.drafts?.[draftKey()] || "";
  updateComposer();
}
function updateComposer() {
  const busy = busyRun();
  $("send-button").disabled =
    state.loading ||
    state.submitting ||
    !!busy ||
    !$("question").value.trim() ||
    !state.selectedPaper;
  $("question").disabled = state.loading || state.submitting;
  $("send-button").setAttribute(
    "aria-label",
    busy ? "等待当前任务完成" : "发送问题",
  );
  $("input-count").textContent =
    $("question").value.length > 7000
      ? `${$("question").value.length}/8000`
      : "";
  $("mode-hint").textContent = busy
    ? "任务正在进行，可以在右侧查看执行过程"
    : state.mode === "qa"
      ? "基于论文证据回答，支持连续追问"
      : "支持论文分析、Python 计算与组会材料生成";
  document.querySelectorAll("[data-compose-mode]").forEach((button) => {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.composeMode === state.mode),
    );
    button.disabled = state.submitting;
  });
  $("rename-session").hidden = !state.session;
  $("paper-select").disabled =
    !!state.session || state.loading || state.submitting;
}
function syncDrawerAccess() {
  $("sidebar").inert =
    matchMedia("(max-width:640px)").matches &&
    !$("sidebar").classList.contains("open");
  $("workspace").inert =
    matchMedia("(max-width:980px)").matches &&
    !$("workspace").classList.contains("open");
}
function closeDrawers() {
  $("sidebar").classList.remove("open");
  $("workspace").classList.remove("open");
  $("scrim").hidden = true;
  syncDrawerAccess();
}
function openWorkspace() {
  $("sidebar").classList.remove("open");
  $("workspace").classList.add("open");
  if (matchMedia("(max-width:980px)").matches) $("scrim").hidden = false;
  syncDrawerAccess();
}
function setTab(tab, reveal = false) {
  state.tab = ["paper", "process", "files"].includes(tab) ? tab : "paper";
  for (const key of ["paper", "process", "files"]) {
    $(`panel-${key}`).hidden = key !== state.tab;
    $(`tab-${key}`).setAttribute("aria-selected", String(key === state.tab));
    $(`tab-${key}`).tabIndex = key === state.tab ? 0 : -1;
  }
  if (reveal) openWorkspace();
  persist();
}
function setPage(page) {
  state.page = page === "library" ? "library" : "chat";
  $("chat-view").hidden = state.page !== "chat";
  $("library-view").hidden = state.page !== "library";
  for (const [id, current] of [
    ["chat-nav", state.page === "chat"],
    ["library-nav", state.page === "library"],
  ]) {
    if (current) $(id).setAttribute("aria-current", "page");
    else $(id).removeAttribute("aria-current");
  }
  $("breadcrumb-view").textContent =
    state.page === "library" ? "论文库" : "对话";
  $("session-title").textContent =
    state.page === "library" ? "论文库" : state.session?.title || "新对话";
  document.title = `${$("session-title").textContent} · PaperLens`;
  $("rename-session").hidden = state.page !== "chat" || !state.session;
  closeDrawers();
  persist();
}
function renderSessions() {
  const search = $("session-search").value.trim().toLocaleLowerCase();
  const rows = state.sessions.filter((session) =>
    session.title.toLocaleLowerCase().includes(search),
  );
  $("session-list").innerHTML = rows.length
    ? rows
        .map(
          (session) =>
            `<button class="session-item ${session.session_id === state.session?.session_id ? "active" : ""}" data-session="${esc(session.session_id)}" title="${esc(session.title)}">${icon("chat")}<span class="session-name">${esc(session.title)}</span>${ACTIVE.has(session.latest_run_status) ? '<span class="busy-dot" aria-label="任务执行中"></span>' : ""}</button>`,
        )
        .join("")
    : `<p class="muted small">${search ? "没有匹配的会话" : "还没有会话。选一篇论文，开始第一次对话。"}</p>`;
  $("more-sessions").hidden = !state.moreSessions;
}
async function loadSessions(more = false) {
  const user = state.user;
  const result = await api(
    owned("/sessions", { limit: 50, offset: more ? state.sessions.length : 0 }),
  );
  if (user !== state.user) return;
  state.sessions = more
    ? [...state.sessions, ...result.sessions]
    : result.sessions;
  state.moreSessions = result.sessions.length === 50;
  renderSessions();
}
const paperName = (paper) =>
  paper?.title ||
  (paper?.paper_id ? `论文 ${paper.paper_id.slice(0, 8)}` : "未选择论文");
function selectedPaper() {
  return (
    state.papers.find((paper) => paper.paper_id === state.selectedPaper) ||
    (state.selectedPaper
      ? {
          paper_id: state.selectedPaper,
          title: preferences.paperNames?.[state.selectedPaper],
        }
      : null)
  );
}
function renderPapers() {
  $("library-count").textContent = state.papers.length;
  const extra =
    state.selectedPaper &&
    !state.papers.some((p) => p.paper_id === state.selectedPaper)
      ? `<option value="${esc(state.selectedPaper)}">${esc(paperName(selectedPaper()))}</option>`
      : "";
  $("paper-select").innerHTML =
    `<option value="">选择已入库论文</option>${extra}` +
    state.papers
      .map(
        (paper) =>
          `<option value="${esc(paper.paper_id)}">${esc(paperName(paper))}</option>`,
      )
      .join("");
  $("paper-select").value = state.selectedPaper;
  $("selected-paper").querySelector("span").textContent = state.selectedPaper
    ? paperName(selectedPaper())
    : "先选择一篇论文";
  $("selected-paper").title = state.session
    ? "本会话已关联这篇论文；更换论文请新建对话"
    : "选择一篇论文";
  const paper = selectedPaper();
  $("paper-info").innerHTML = paper
    ? `<article class="paper-document"><span class="document-label">${icon("file")} RESEARCH PAPER</span><h3>${esc(paperName(paper))}</h3>${paper.authors ? `<p>${esc(paper.authors)}</p>` : ""}${paper.year || paper.doi ? `<p>${esc([paper.year, paper.doi].filter(Boolean).join(" · "))}</p>` : ""}<span class="paper-ready">${icon("check")} ${state.session ? "已关联当前会话" : "已入库 · 可以提问"}</span></article>${state.session ? '<p class="small muted" style="font-size:9px;line-height:1.8;margin-top:10px">本会话围绕这篇论文保留上下文。更换论文时，请新建对话。</p>' : ""}`
    : `<div class="paper-placeholder">${icon("file")}<p>还没有关联论文</p><span>选择论文后，即可开始研读。</span></div>`;
  if (state.paperError)
    $("library-papers").innerHTML =
      `<div class="library-empty">${esc(state.paperError)}<br><button class="text-button" data-refresh-papers>重新连接论文库</button></div>`;
  else
    $("library-papers").innerHTML = state.papers.length
      ? state.papers
          .map(
            (paper) =>
              `<article class="library-card"><span class="document-label">${icon("file")} PAPER · ${esc(paper.year || "已入库")}</span><h3>${esc(paperName(paper))}</h3><p>${esc(paper.authors || "作者信息暂缺")}</p><button class="button" data-use-paper="${esc(paper.paper_id)}">围绕这篇论文提问 ${icon("chevron")}</button></article>`,
          )
          .join("")
      : '<div class="library-empty">论文库还是空的。<br>上传一篇 PDF，等解析完成后就可以开始提问。</div>';
  $("choose-paper").querySelector("span").textContent = paper
    ? `已选择：${paperName(paper)}`
    : "选择论文，开始对话";
  updateComposer();
}
async function loadPapers() {
  try {
    const papers = await api("/papers?limit=20");
    state.papers = papers;
    state.paperError = "";
    preferences.paperNames ||= {};
    papers.forEach((p) => (preferences.paperNames[p.paper_id] = paperName(p)));
    persist();
  } catch (error) {
    state.paperError = error.message;
  }
  renderPapers();
}
function messageArtifacts(items = []) {
  return items
    .map(
      (file) =>
        `<a href="${downloadURL(file.artifact_id)}" title="下载 ${esc(file.filename)}">${icon("download")}${esc(file.filename)}</a>`,
    )
    .join("");
}
function renderMessages(scroll = false) {
  $("empty-state").hidden = state.messages.length > 0 || state.loading;
  const runs = new Map(state.runs.map((run) => [run.run_id, run]));
  $("messages").innerHTML = state.messages
    .map((message) => {
      const run = runs.get(message.run_id);
      if (message.role === "user") {
        let task = "";
        if (run && ACTIVE.has(run.status))
          task = `<div class="task-card"><div><span class="busy-dot"></span>${esc(stageLabel[run.stage] || "正在处理问题")}</div><p>结果完成后会显示在这里。你可以切换会话，任务会继续执行。</p><button class="text-button" data-show-run="${esc(run.run_id)}">查看执行过程 ${icon("chevron")}</button></div>`;
        else if (run && ["failed", "interrupted"].includes(run.status))
          task = `<div class="task-card failed"><div>${esc(statusLabel[run.status])}</div><p>${esc(run.error || "任务未完成，可以重新尝试。")}</p><button class="text-button" data-retry="${esc(run.run_id)}">重新执行这一任务</button></div>`;
        return `<article class="message user">${esc(message.content)}</article>${task}`;
      }
      const citations = message.citations || [];
      return `<article class="message assistant"><div class="assistant-heading"><span class="assistant-mark">P</span>PaperLens<span class="small muted">${run?.mode === "qa" ? "论文问答" : "研究助手"}</span><time>${esc(timeLabel(message.created_at))}</time></div><div class="markdown">${markdown(message.content)}</div>${
        citations.length
          ? `<div class="message-sources">${citations
              .slice(0, 4)
              .map(
                (citation, i) =>
                  `<button class="source-button" data-citation-run="${esc(message.run_id)}" data-citation-index="${i}">${i + 1} · ${esc(citation.section_path || "论文证据")}${citation.page_number ? ` · p.${esc(citation.page_number)}` : ""}</button>`,
              )
              .join(
                "",
              )}<button class="source-button" data-citation-run="${esc(message.run_id)}" data-citation-index="0">查看全部 ${citations.length} 条来源 ${icon("chevron")}</button></div>`
          : ""
      }${message.artifacts?.length ? `<div class="message-files">${messageArtifacts(message.artifacts)}</div>` : ""}</article>`;
    })
    .join("");
  $("more-messages").hidden = !state.before || state.before <= 1;
  if (scroll)
    requestAnimationFrame(
      () => ($("thread-scroll").scrollTop = $("thread-scroll").scrollHeight),
    );
  updateComposer();
}
function renderEvidence() {
  const message =
    state.messages.find(
      (item) => item.role === "assistant" && item.run_id === state.sourceRun,
    ) ||
    [...state.messages].reverse().find((item) => item.role === "assistant");
  const citations = state.sourceCitations.length
    ? state.sourceCitations
    : message?.citations || [];
  $("evidence-count").textContent = citations.length
    ? `${citations.length} 条`
    : "";
  $("evidence-list").innerHTML = citations.length
    ? `<div class="evidence-context">${state.sourceRun ? "所选回答的论文来源" : "最近一条回答的论文来源"}</div>` +
      citations
        .map(
          (citation, i) =>
            `<details class="evidence-card" id="evidence-${i}"><summary>${i + 1}. ${esc(citation.section_path || "论文章节")}<br><span>${citation.page_number ? `第 ${esc(citation.page_number)} 页` : "原文未提供页码"}</span></summary><blockquote>${esc(citation.text || "这条来源没有返回原文片段，请依据章节在论文中核对。")}</blockquote></details>`,
        )
        .join("")
    : '<p class="empty-note">回答中的引用会在这里展开，帮助你回到原文核对。</p>';
}
function renderFiles() {
  $("file-count").textContent = state.artifacts.length || "";
  if (!state.artifacts.length) {
    $("file-list").innerHTML =
      '<p class="empty-note">生成的 HTML 演示、大纲、图表和数据文件会保留在这里。</p>';
    return;
  }
  const grouped = new Map();
  state.artifacts.forEach((file) => {
    if (!grouped.has(file.run_id)) grouped.set(file.run_id, []);
    grouped.get(file.run_id).push(file);
  });
  $("file-list").innerHTML = [...grouped]
    .map(([runId, files]) => {
      const run = state.runs.find((item) => item.run_id === runId);
      return (
        `<h3 class="file-group-title">${run ? `第 ${run.turn_index} 轮` : "较早任务"} · ${esc(timeLabel(files[0].created_at))}${run === state.runs[0] ? " · 最新" : ""}</h3>` +
        files
          .map((file) => {
            const extension = file.filename.split(".").pop().toLowerCase();
            const version = file.filename.match(/-v(\d+)-/);
            const kind = extension === "html" ? "HTML 演示" : file.filename.endsWith(".slides.json") ? "可编辑演示稿" : file.filename.endsWith(".check.json") ? "演示检查报告" : extension === "md" ? "逐页大纲" : "研究文件";
            return `<article class="file-card"><div class="file-title"><span class="file-type ${extension === "html" ? "presentation" : ""}">${esc(extension.toUpperCase().slice(0, 5))}</span><div class="file-name">${esc(file.filename)}<small>${bytes(file.size_bytes)} · ${esc(kind)}${version ? ` · v${esc(version[1])}` : ""}</small></div></div><div class="file-actions"><a href="${downloadURL(file.artifact_id)}">${icon("download")}下载文件</a>${["md", "txt", "csv", "tsv", "json", "html"].includes(extension) ? `<button data-preview="${esc(file.artifact_id)}">${extension === "html" ? "预览演示" : "预览内容"}</button>` : ""}${extension === "html" ? `<a href="${owned(`/artifacts/${encodeURIComponent(file.artifact_id)}/preview`)}" target="_blank" rel="noopener">独立放映 ↗</a>` : ""}</div></article>`;
          })
          .join("")
      );
    })
    .join("");
}
function selectedRun() {
  return state.runs.find((run) => run.run_id === state.selectedRun);
}
function renderRunSelect() {
  $("run-select").innerHTML = state.runs.length
    ? state.runs
        .map(
          (run) =>
            `<option value="${esc(run.run_id)}">第 ${run.turn_index} 轮 · ${esc(statusLabel[run.status] || run.status)} · ${esc(run.question.slice(0, 25))}</option>`,
        )
        .join("")
    : '<option value="">暂无任务</option>';
  $("run-select").value = state.selectedRun;
}
function renderRunSummary() {
  const run = selectedRun();
  if (!run) {
    $("run-summary").innerHTML =
      '<p class="empty-note">发送问题后，可以在这里查看检索、计算与生成文件的进度。</p>';
    return;
  }
  const elapsed =
    run.timings?.total_ms ??
    (run.started_at
      ? new Date(run.finished_at || Date.now()) - new Date(run.started_at)
      : 0);
  $("run-summary").innerHTML =
    `<p class="run-question">${esc(run.question)}</p><span class="run-status ${esc(run.status)}">${ACTIVE.has(run.status) ? '<span class="busy-dot"></span>' : ""}${esc(statusLabel[run.status] || run.status)}</span><span class="run-time">${duration(elapsed)}</span><p class="event-detail">${esc(run.error || stageLabel[run.stage] || "正在处理任务")}</p>`;
}
function eventDescription(event) {
  const data = event.data || {},
    name = toolLabel[data.tool] || data.tool || "工具";
  if (event.event === "stage.changed")
    return [stageLabel[data.stage] || data.stage, data.summary || ""];
  if (event.event === "tool.started") {
    const args = data.arguments || {};
    return [
      name,
      args.question ||
        args.objective ||
        (args.duration_minutes
          ? `${args.duration_minutes} 分钟汇报`
          : args.code_chars
            ? `执行 ${args.code_chars} 字符的计算代码`
            : "开始执行"),
    ];
  }
  if (event.event === "tool.completed")
    return [
      `${name} · 完成`,
      `${duration(data.duration_ms)}${data.result_count ? ` · 返回 ${data.result_count} 条证据` : ""}`,
    ];
  if (event.event === "tool.failed")
    return [
      `${name} · 本次调用未成功`,
      data.error || "助手将根据结果决定后续操作",
    ];
  if (event.event === "artifact.created") return ["生成文件", data.filename];
  return [
    {
      "run.queued": "任务已提交",
      "run.started": "开始处理任务",
      "run.completed": "回答与文件已保存",
      "run.failed": "任务执行失败",
      "run.interrupted": "任务已中断",
    }[event.event] || "执行进度",
    data.error || "",
  ];
}
function renderEvents() {
  const entries = [...state.events.values()].sort((a, b) => a.seq - b.seq);
  $("event-list").innerHTML = entries.length
    ? `<div class="event-count">${entries.length} 条过程记录 · ${ACTIVE.has(selectedRun()?.status) ? "持续更新中" : "历史过程"}</div>` +
      entries
        .map((event) => {
          const [title, detail] = eventDescription(event);
          return `<div class="event-item ${event.data?.scope === "presentation" ? "child" : ""} ${event.event.endsWith(".failed") || event.event.endsWith(".interrupted") ? "error" : ""}"><div class="event-title">${esc(title)}<time>${esc(timeLabel(event.created_at))}</time></div>${detail ? `<div class="event-detail">${event.data?.scope === "presentation" ? "演示规划助手 · " : ""}${esc(String(detail).slice(0, 400))}</div>` : ""}</div>`;
        })
        .join("")
    : '<p class="empty-note">正在读取执行过程…</p>';
}
function closeWatch() {
  stream?.close();
  stream = null;
  clearTimeout(pollTimer);
  pollTimer = null;
}
async function refreshSnapshot(epoch, runId) {
  const result = await api(owned(`/runs/${encodeURIComponent(runId)}`));
  if (epoch !== state.epoch || runId !== state.selectedRun) return null;
  const previous = state.runs.find((run) => run.run_id === runId);
  if (previous && TERMINAL.has(previous.status) && ACTIVE.has(result.status))
    return previous;
  state.runs = state.runs.map((run) =>
    run.run_id === runId ? { ...run, ...result } : run,
  );
  renderRunSummary();
  renderRunSelect();
  updateComposer();
  if (previous?.status !== result.status) renderEvents();
  if (TERMINAL.has(result.status) && previous && ACTIVE.has(previous.status)) {
    // Wait for the stream to deliver the persisted terminal event before closing it.
    await refreshConversation(epoch);
    loadSessions().catch(() => {});
  }
  return result;
}
async function refreshConversation(epoch) {
  if (!state.session) return;
  const revision = ++state.historyRevision;
  const sid = state.session.session_id;
  const [history, files] = await Promise.all([
    api(owned(`/sessions/${encodeURIComponent(sid)}/messages`, { limit: 50 })),
    api(
      owned(`/sessions/${encodeURIComponent(sid)}/artifacts`, { limit: 100 }),
    ),
  ]);
  if (epoch !== state.epoch || revision !== state.historyRevision) return;
  const nearBottom =
    $("thread-scroll").scrollHeight -
      $("thread-scroll").scrollTop -
      $("thread-scroll").clientHeight <
    180;
  const earliest = history.messages[0]?.turn_index || Infinity;
  state.messages = [
    ...state.messages.filter((message) => message.turn_index < earliest),
    ...history.messages,
  ];
  state.before = Math.min(
    state.before || Infinity,
    history.next_before_turn || Infinity,
  );
  if (!Number.isFinite(state.before)) state.before = null;
  state.artifacts = files.artifacts;
  renderMessages(nearBottom);
  renderFiles();
  renderEvidence();
}
function watchRun(runId) {
  closeWatch();
  state.selectedRun = runId;
  state.events = new Map();
  renderRunSelect();
  renderRunSummary();
  if (!runId) {
    $("event-list").replaceChildren();
    return;
  }
  const epoch = state.epoch;
  const saved = read(sessionStorage, eventKey(runId), []);
  if (Array.isArray(saved))
    saved.forEach((event) => {
      if (event.run_id === runId && Number.isInteger(event.seq))
        state.events.set(event.seq, event);
    });
  renderEvents();
  const cursor = state.events.size ? Math.max(...state.events.keys()) : 0;
  const accept = (event) => {
    if (epoch !== state.epoch || state.selectedRun !== runId) return;
    let item;
    try {
      item = JSON.parse(event.data);
    } catch {
      return;
    }
    if (item.run_id !== runId || !Number.isInteger(item.seq)) return;
    state.events.set(item.seq, item);
    write(
      sessionStorage,
      eventKey(runId),
      [...state.events.values()].slice(-500),
    );
    renderEvents();
    const run = selectedRun();
    if (run && item.event === "stage.changed") {
      run.stage = item.data.stage;
      renderRunSummary();
    }
    if (item.event === "artifact.created")
      refreshConversation(epoch).catch(() => {});
    if (
      ["run.completed", "run.failed", "run.interrupted"].includes(item.event)
    ) {
      stream?.close();
      stream = null;
      refreshSnapshot(epoch, runId)
        .then(() => refreshConversation(epoch))
        .catch((error) => toast(error.message, true));
    }
  };
  if (!(
    TERMINAL.has(selectedRun()?.status) &&
    cursor >= (selectedRun()?.last_event_seq || Infinity)
  )) {
    stream = new EventSource(
      owned(`/runs/${encodeURIComponent(runId)}/events`, { after: cursor }),
    );
    EVENTS.forEach((type) => stream.addEventListener(type, accept));
    stream.onerror = () => {
      if (epoch === state.epoch && runId === state.selectedRun)
        refreshSnapshot(epoch, runId)
          .then((run) => {
            if (run && TERMINAL.has(run.status)) {
              stream?.close();
              stream = null;
            }
          })
          .catch(() => {});
    };
  }
  const poll = async () => {
    if (epoch !== state.epoch || runId !== state.selectedRun) return;
    try {
      const result = await refreshSnapshot(epoch, runId);
      if (result && TERMINAL.has(result.status)) return;
    } catch {}
    if (epoch === state.epoch && runId === state.selectedRun)
      pollTimer = setTimeout(poll, 3000);
  };
  pollTimer = setTimeout(poll, 1000);
}
async function openSession(id) {
  if (state.submitting) return;
  saveDraft();
  closeWatch();
  const epoch = ++state.epoch;
  state.loading = true;
  state.sourceRun = "";
  state.sourceCitations = [];
  state.session = null;
  state.messages = [];
  state.runs = [];
  state.artifacts = [];
  state.selectedRun = "";
  $("view-error").hidden = true;
  $("empty-state").hidden = true;
  $("messages").innerHTML = '<p class="small muted">正在恢复会话…</p>';
  updateComposer();
  closeDrawers();
  try {
    const base = `/sessions/${encodeURIComponent(id)}`;
    const [session, history, runs, files] = await Promise.all([
      api(owned(base)),
      api(owned(`${base}/messages`, { limit: 50 })),
      api(owned(`${base}/runs`, { limit: 100 })),
      api(owned(`${base}/artifacts`, { limit: 100 })),
    ]);
    if (epoch !== state.epoch) return;
    state.session = session;
    state.messages = history.messages;
    state.before = history.next_before_turn;
    state.runs = runs.runs;
    state.artifacts = files.artifacts;
    state.selectedPaper = session.paper_id;
    state.mode = runs.runs[0]?.mode || "qa";
    state.loading = false;
    setPage("chat");
    restoreDraft();
    renderPapers();
    renderSessions();
    renderMessages(true);
    renderFiles();
    renderEvidence();
    watchRun(runs.runs[0]?.run_id || "");
    persist();
  } catch (error) {
    if (epoch !== state.epoch) return;
    state.loading = false;
    $("messages").replaceChildren();
    $("view-error").textContent = `无法恢复会话：${error.message}`;
    $("view-error").hidden = false;
    updateComposer();
    if (error.status === 404) {
      preferences.session = "";
      write(localStorage, prefKey(), preferences);
    }
  }
}
function newChat(paperId = "") {
  if (state.submitting) return;
  saveDraft();
  closeWatch();
  ++state.epoch;
  Object.assign(state, {
    session: null,
    runs: [],
    messages: [],
    artifacts: [],
    events: new Map(),
    selectedRun: "",
    selectedPaper: paperId,
    sourceRun: "",
    sourceCitations: [],
    loading: false,
    before: null,
  });
  preferences.drafts ||= {};
  preferences.drafts.new = "";
  preferences.pending = null;
  $("view-error").hidden = true;
  setPage("chat");
  restoreDraft();
  renderPapers();
  renderSessions();
  renderMessages();
  renderFiles();
  renderEvidence();
  watchRun("");
  setTab("paper");
  persist();
}
async function submitQuestion(event) {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question || state.loading || state.submitting || busyRun()) return;
  if (!state.selectedPaper) {
    setTab("paper", true);
    toast("先选择或上传一篇论文");
    return;
  }
  state.submitting = true;
  updateComposer();
  try {
    if (!state.session) {
      state.session = await api(
        "/sessions",
        jsonOptions({ user_id: state.user, paper_id: state.selectedPaper }),
      );
      preferences.drafts ||= {};
      preferences.drafts[state.session.session_id] = question;
      preferences.drafts.new = "";
      persist();
    }
    const session = state.session,
      sid = session.session_id;
    let pending = preferences.pending;
    if (
      !pending ||
      pending.sid !== sid ||
      pending.question !== question ||
      pending.mode !== state.mode
    )
      pending = { sid, question, mode: state.mode, request_id: uid() };
    preferences.pending = pending;
    persist();
    const run = await api(
      owned(`/sessions/${encodeURIComponent(sid)}/runs`),
      jsonOptions({
        question,
        request_id: pending.request_id,
        mode: state.mode,
      }),
    );
    preferences.pending = null;
    preferences.drafts[sid] = "";
    $("question").value = "";
    persist();
    state.runs = [
      run,
      ...state.runs.filter((item) => item.run_id !== run.run_id),
    ];
    state.session.title =
      session.title === "新对话" ? question.slice(0, 30) : session.title;
    watchRun(run.run_id);
    setTab("process");
    setPage("chat");
    await refreshConversation(state.epoch);
    renderMessages(true);
    renderFiles();
    renderRunSelect();
    await loadSessions();
  } catch (error) {
    toast(error.message, true);
    if (error.status === 409 && state.session) {
      const sid = state.session.session_id;
      state.submitting = false;
      await openSession(sid);
    }
  } finally {
    state.submitting = false;
    updateComposer();
  }
}
async function retryRun(runId) {
  if (state.submitting || busyRun()) return;
  state.submitting = true;
  updateComposer();
  try {
    const key = `retry:${runId}`;
    preferences.retries ||= {};
    preferences.retries[key] ||= uid();
    persist();
    const run = await api(
      owned(`/runs/${encodeURIComponent(runId)}/retry`),
      jsonOptions({ request_id: preferences.retries[key] }),
    );
    delete preferences.retries[key];
    state.runs.unshift(run);
    persist();
    await refreshConversation(state.epoch);
    renderMessages(true);
    watchRun(run.run_id);
    setTab("process", true);
    await loadSessions();
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.submitting = false;
    updateComposer();
  }
}
async function previewFile(id) {
  const file = state.artifacts.find((item) => item.artifact_id === id);
  if (!file) return;
  $("preview-title").textContent = file.filename;
  $("preview-content").textContent = "正在读取文件…";
  $("preview-dialog").showModal();
  const previewToken = uid();
  $("preview-dialog").dataset.previewToken = previewToken;
  $("preview-dialog").classList.toggle("presentation-preview", file.filename.toLowerCase().endsWith(".html"));
  if (file.filename.toLowerCase().endsWith(".html")) {
    const frame = document.createElement("iframe");
    frame.title = "HTML 学术演示";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.allow = "fullscreen";
    frame.allowFullscreen = true;
    frame.src = owned(`/artifacts/${encodeURIComponent(id)}/preview`);
    $("preview-content").replaceChildren(frame);
    return;
  }
  try {
    const response = await fetch(downloadURL(id));
    if (!response.ok) throw new Error("文件暂时无法读取，请重试下载");
    if (file.size_bytes > 300000)
      throw new Error("文件较大，请下载后在本地查看");
    const text = await response.text();
    if (
      !$("preview-dialog").open ||
      $("preview-dialog").dataset.previewToken !== previewToken
    )
      return;
    $("preview-content").innerHTML = file.filename.toLowerCase().endsWith(".md")
      ? markdown(text)
      : `<pre>${esc(text)}</pre>`;
  } catch (error) {
    $("preview-content").textContent = error.message;
  }
}
function renderUpload() {
  $("upload-status").hidden = !state.upload;
  if (!state.upload) return;
  const upload = state.upload;
  $("upload-status").classList.toggle("error", !!upload.error);
  $("upload-status").innerHTML =
    `${esc(upload.name)}<br>${esc(upload.error || (upload.status === "ready" ? "解析完成，已加入论文库。" : "正在解析、切分和建立索引，可稍后回来查看。"))}${upload.error && upload.paper_id ? '<br><button class="text-button" data-check-upload>重新检查状态</button>' : ""}`;
}
function persistUpload() {
  preferences.upload = state.upload;
  persist();
  renderUpload();
}
async function checkUpload() {
  const upload = state.upload;
  if (!upload?.paper_id || upload.status === "ready") return;
  try {
    const paper = await api(
      `/papers/${encodeURIComponent(upload.paper_id)}/status`,
    );
    if (state.upload !== upload) return;
    upload.error = "";
    upload.status = paper.status;
    if (paper.status === "ready") {
      await loadPapers();
      if (state.upload !== upload) return;
      if (!state.session) state.selectedPaper = paper.paper_id;
      persistUpload();
      renderPapers();
      toast("论文解析完成，可以开始提问了");
      return;
    }
    if (["failed", "deleted", "deleting"].includes(paper.status)) {
      upload.error = paper.error_message || "论文未能入库，请检查论文服务";
      persistUpload();
      return;
    }
  } catch (error) {
    if (state.upload !== upload) return;
    upload.error = `${error.message}；稍后会继续检查`;
  }
  persistUpload();
  clearTimeout(uploadTimer);
  uploadTimer = setTimeout(checkUpload, upload.error ? 8000 : 2000);
}
async function uploadPDF(file) {
  if (
    !file ||
    state.upload?.status === "uploading" ||
    (state.upload?.status === "processing" && !state.upload.error)
  )
    return;
  if (!file.name.toLowerCase().endsWith(".pdf"))
    return toast("请选择 PDF 文件", true);
  if (file.size > 50 * 1024 * 1024)
    return toast("PDF 文件不能超过 50 MB", true);
  clearTimeout(uploadTimer);
  state.upload = { name: file.name, status: "uploading" };
  persistUpload();
  setTab("paper", true);
  document
    .querySelectorAll("[data-upload]")
    .forEach((button) => (button.disabled = true));
  try {
    const body = new FormData();
    body.append("file", file);
    const paper = await api("/papers/upload", { method: "POST", body });
    Object.assign(state.upload, paper);
    if (paper.status === "ready") {
      await loadPapers();
      if (!state.session) state.selectedPaper = paper.paper_id;
      toast("论文已就绪，可以开始提问了");
    } else if (paper.status === "processing") checkUpload();
    else state.upload.error = "该论文当前无法使用，请检查入库状态";
    persistUpload();
    renderPapers();
  } catch (error) {
    state.upload.error = error.message;
    state.upload.status = "failed";
    persistUpload();
    toast(error.message, true);
  } finally {
    $("pdf-file").value = "";
    document
      .querySelectorAll("[data-upload]")
      .forEach((button) => (button.disabled = false));
  }
}
async function checkHealth() {
  try {
    await api("/health");
    $("connection-status").classList.remove("offline");
    $("connection-status").lastElementChild.textContent = "服务已连接";
  } catch {
    $("connection-status").classList.add("offline");
    $("connection-status").lastElementChild.textContent = "连接已断开";
  }
  clearTimeout(healthTimer);
  healthTimer = setTimeout(checkHealth, 30000);
}
async function initializeUser() {
  closeWatch();
  clearTimeout(uploadTimer);
  ++state.epoch;
  const initialization = state.epoch;
  state.loading = true;
  preferences = read(localStorage, prefKey(), {});
  state.selectedPaper = preferences.paper || "";
  state.mode = preferences.mode === "agent" ? "agent" : "qa";
  state.upload = preferences.upload || null;
  if (state.upload?.status === "uploading") {
    state.upload.status = "failed";
    state.upload.error =
      "上次上传连接已断开。请先刷新论文库，或重新选择该 PDF；已有论文会自动去重。";
  }
  write(localStorage, "paperlens.user", state.user);
  state.sessions = [];
  state.runs = [];
  state.messages = [];
  state.artifacts = [];
  state.session = null;
  state.selectedRun = "";
  state.events = new Map();
  state.sourceRun = "";
  state.sourceCitations = [];
  $("question").value = preferences.drafts?.new || "";
  $("session-list").innerHTML = '<p class="muted small">正在读取会话…</p>';
  const restore = preferences.session,
    page = preferences.page,
    tab = preferences.tab;
  await Promise.all([
    loadSessions().catch((error) => {
      $("session-list").innerHTML =
        `<p class="muted small">${esc(error.message)}<br>点击上方刷新重试。</p>`;
    }),
    loadPapers(),
  ]);
  if (initialization !== state.epoch) return;
  if (restore) await openSession(restore);
  else {
    state.loading = false;
    renderMessages();
    renderFiles();
    renderEvidence();
    renderRunSummary();
    restoreDraft();
  }
  setTab(tab || "paper");
  setPage(page || "chat");
  renderUpload();
  if (state.upload?.status === "processing") checkUpload();
}

$("composer-form").addEventListener("submit", submitQuestion);
$("question").addEventListener("input", saveDraft);
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer-form").requestSubmit();
  }
});
$("new-chat").addEventListener("click", () => newChat());
$("chat-nav").addEventListener("click", () => setPage("chat"));
$("library-nav").addEventListener("click", () => setPage("library"));
$("browse-papers").addEventListener("click", () => setPage("library"));
$("choose-paper").addEventListener("click", () => {
  setTab("paper", true);
  $("paper-select").focus();
});
$("selected-paper").addEventListener("click", () => setTab("paper", true));
$("paper-select").addEventListener("change", (event) => {
  if (state.session) return;
  state.selectedPaper = event.target.value;
  persist();
  renderPapers();
  closeDrawers();
  $("question").focus();
});
$("session-search").addEventListener("input", renderSessions);
$("refresh-sessions").addEventListener("click", () =>
  loadSessions().catch((error) => toast(error.message, true)),
);
$("more-sessions").addEventListener("click", () =>
  loadSessions(true).catch((error) => toast(error.message, true)),
);
$("refresh-papers").addEventListener("click", loadPapers);
$("run-select").addEventListener("change", (event) =>
  watchRun(event.target.value),
);
$("refresh-run").addEventListener("click", () => {
  if (state.selectedRun) {
    write(sessionStorage, eventKey(state.selectedRun), []);
    watchRun(state.selectedRun);
    refreshSnapshot(state.epoch, state.selectedRun).catch((error) =>
      toast(error.message, true),
    );
  }
});
$("more-messages").addEventListener("click", async () => {
  if (!state.session || state.before <= 1) return;
  const epoch = state.epoch,
    height = $("thread-scroll").scrollHeight;
  $("more-messages").disabled = true;
  try {
    const data = await api(
      owned(
        `/sessions/${encodeURIComponent(state.session.session_id)}/messages`,
        { limit: 30, before_turn: state.before },
      ),
    );
    if (epoch !== state.epoch) return;
    const known = new Set(state.messages.map((m) => m.message_id));
    state.messages = [
      ...data.messages.filter((m) => !known.has(m.message_id)),
      ...state.messages,
    ];
    state.before = data.next_before_turn;
    renderMessages();
    $("thread-scroll").scrollTop += $("thread-scroll").scrollHeight - height;
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("more-messages").disabled = false;
  }
});
$("open-nav").addEventListener("click", () => {
  $("workspace").classList.remove("open");
  $("sidebar").classList.add("open");
  $("scrim").hidden = false;
  syncDrawerAccess();
});
$("open-workspace").addEventListener("click", openWorkspace);
$("close-workspace").addEventListener("click", closeDrawers);
$("scrim").addEventListener("click", closeDrawers);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawers();
});
$("pdf-file").addEventListener("change", (event) =>
  uploadPDF(event.target.files[0]),
);
$("identity-button").addEventListener("click", () => {
  $("identity-input").value = state.user;
  $("identity-dialog").showModal();
});
$("identity-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const user = $("identity-input").value.trim();
  if (!user || state.submitting) return;
  if (state.upload?.status === "uploading")
    return toast("正在上传论文，请上传完成后再切换记录标识");
  saveDraft();
  state.user = user;
  $("identity-dialog").close();
  await initializeUser();
});
$("rename-session").addEventListener("click", () => {
  if (!state.session) return;
  $("rename-input").value = state.session.title;
  $("rename-dialog").showModal();
});
$("rename-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.session) return;
  const sid = state.session.session_id,
    epoch = state.epoch;
  try {
    const session = await api(
      owned(`/sessions/${encodeURIComponent(sid)}`),
      jsonOptions({ title: $("rename-input").value.trim() }, "PATCH"),
    );
    if (epoch !== state.epoch) return;
    state.session = session;
    setPage(state.page);
    $("rename-dialog").close();
    await loadSessions();
  } catch (error) {
    toast(error.message, true);
  }
});
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.matches("[data-close-dialog]"))
    return target.closest("dialog").close();
  if (target.dataset.tab) return setTab(target.dataset.tab);
  if (target.dataset.composeMode) {
    state.mode = target.dataset.composeMode;
    updateComposer();
    persist();
    return;
  }
  if (target.dataset.session) return openSession(target.dataset.session);
  if (target.dataset.usePaper) {
    if (state.session?.paper_id === target.dataset.usePaper) setPage("chat");
    else newChat(target.dataset.usePaper);
    return;
  }
  if (target.hasAttribute("data-upload")) return $("pdf-file").click();
  if (target.hasAttribute("data-refresh-papers")) return loadPapers();
  if (target.hasAttribute("data-check-upload")) {
    state.upload.error = "";
    checkUpload();
    return;
  }
  if (target.dataset.prompt) {
    state.mode = target.dataset.mode || "qa";
    $("question").value = target.dataset.prompt;
    saveDraft();
    $("question").focus();
    return;
  }
  if (target.dataset.showRun) {
    watchRun(target.dataset.showRun);
    setTab("process", true);
    return;
  }
  if (target.dataset.retry) return retryRun(target.dataset.retry);
  if (target.dataset.preview) return previewFile(target.dataset.preview);
  if (target.dataset.citationRun) {
    state.sourceRun = target.dataset.citationRun;
    state.sourceCitations = [];
    renderEvidence();
    setTab("paper", true);
    const card = $(`evidence-${target.dataset.citationIndex || 0}`);
    if (card) {
      card.open = true;
      card.classList.add("highlight");
      card.scrollIntoView({ block: "nearest" });
    }
  }
});
document.querySelectorAll("[role=tab]").forEach((button) =>
  button.addEventListener("keydown", (event) => {
    const tabs = ["paper", "process", "files"];
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const index =
      (tabs.indexOf(state.tab) + (event.key === "ArrowRight" ? 1 : 2)) % 3;
    setTab(tabs[index]);
    $(`tab-${tabs[index]}`).focus();
  }),
);
window.addEventListener("pagehide", () => {
  saveDraft();
  closeWatch();
});
window.addEventListener("online", () => {
  checkHealth();
  loadPapers();
  if (state.selectedRun) watchRun(state.selectedRun);
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted && state.selectedRun) watchRun(state.selectedRun);
});
window.addEventListener("resize", syncDrawerAccess);
syncDrawerAccess();
checkHealth();
initializeUser().catch((error) => toast(error.message, true));
