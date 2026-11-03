/*
 * 文件说明：驱动 RAG 自带网页的上传、入库状态查询、论文选择和问题提交。
 * 把接口返回的答案、引用、耗时及错误展示到模板页面，并处理浏览器交互。
 */

const elements = {
  uploadForm: document.querySelector("#upload-form"),
  uploadButton: document.querySelector("#upload-button"),
  pdfFile: document.querySelector("#pdf-file"),
  uploadStatus: document.querySelector("#upload-status"),
  paperList: document.querySelector("#paper-list"),
  emptyLibrary: document.querySelector("#empty-library"),
  paperCount: document.querySelector("#paper-count"),
  refreshPapers: document.querySelector("#refresh-papers"),
  paperSelect: document.querySelector("#paper-select"),
  sectionType: document.querySelector("#section-type"),
  queryForm: document.querySelector("#query-form"),
  queryButton: document.querySelector("#query-button"),
  question: document.querySelector("#question"),
  questionCount: document.querySelector("#question-count"),
  queryStatus: document.querySelector("#query-status"),
  answerCard: document.querySelector("#answer-card"),
  answerText: document.querySelector("#answer-text"),
  citationList: document.querySelector("#citation-list"),
  timings: document.querySelector("#timings"),
};

let papers = [];

function showNotice(element, message, type = "info") {
  element.textContent = message;
  element.className = `notice ${type === "info" ? "" : type}`.trim();
  element.hidden = false;
}

function hideNotice(element) {
  element.hidden = true;
  element.textContent = "";
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let data;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.error || `请求失败（${response.status}）`);
  }
  return data;
}

function paperDisplayName(paper) {
  return paper.title || `未命名论文 · ${paper.paper_id.slice(0, 8)}`;
}

function renderPapers() {
  const selected = elements.paperSelect.value;
  elements.paperList.replaceChildren();
  elements.paperSelect.replaceChildren();

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择一篇已完成解析的论文";
  elements.paperSelect.append(placeholder);

  for (const paper of papers) {
    const option = document.createElement("option");
    option.value = paper.paper_id;
    option.textContent = paperDisplayName(paper);
    elements.paperSelect.append(option);

    const item = document.createElement("div");
    item.className = "paper-item";

    const choice = document.createElement("button");
    choice.type = "button";
    choice.className = "paper-choice";
    choice.addEventListener("click", () => {
      elements.paperSelect.value = paper.paper_id;
      elements.question.focus();
    });

    const title = document.createElement("span");
    title.className = "paper-title";
    title.textContent = paperDisplayName(paper);
    const meta = document.createElement("span");
    meta.className = "paper-meta";
    const authors = paper.authors ? paper.authors.split(";").slice(0, 2).join("、") : "作者未知";
    meta.textContent = `${paper.year || "年份未知"} · ${authors}`;
    choice.append(title, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete-button";
    remove.title = "删除论文";
    remove.setAttribute("aria-label", `删除 ${paperDisplayName(paper)}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => deletePaper(paper, remove));
    item.append(choice, remove);
    elements.paperList.append(item);
  }

  elements.paperCount.textContent = String(papers.length);
  elements.emptyLibrary.hidden = papers.length > 0;
  if (papers.some((paper) => paper.paper_id === selected)) {
    elements.paperSelect.value = selected;
  }
}

async function loadPapers() {
  try {
    papers = await api("/papers?limit=20");
    renderPapers();
  } catch (error) {
    showNotice(elements.queryStatus, `论文列表加载失败：${error.message}`, "error");
  }
}

async function pollPaper(paperId) {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 1800));
    const paper = await api(`/papers/${encodeURIComponent(paperId)}/status`);
    if (paper.status === "ready") {
      showNotice(elements.uploadStatus, "解析完成，论文已经可以提问。", "info");
      await loadPapers();
      elements.paperSelect.value = paperId;
      return;
    }
    if (paper.status === "failed") {
      showNotice(elements.uploadStatus, `解析失败：${paper.error_message || "未知错误"}`, "error");
      return;
    }
    if (paper.status === "deleted") {
      showNotice(elements.uploadStatus, "该论文已被删除。", "warning");
      return;
    }
    showNotice(elements.uploadStatus, "正在解析、切分并建立索引，请稍候…", "info");
  }
}

elements.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = elements.pdfFile.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".pdf") || file.type !== "application/pdf") {
    showNotice(elements.uploadStatus, "请选择有效的 PDF 文件。", "error");
    return;
  }
  elements.uploadButton.disabled = true;
  hideNotice(elements.uploadStatus);
  const formData = new FormData();
  formData.append("file", file);
  try {
    const result = await api("/upload", { method: "POST", body: formData });
    if (result.status === "ready") {
      showNotice(elements.uploadStatus, "该论文已存在，可以直接提问。", "info");
      await loadPapers();
      elements.paperSelect.value = result.paper_id;
    } else if (result.status === "failed") {
      showNotice(elements.uploadStatus, "该文件此前解析失败，请先删除后重新上传。", "error");
    } else if (result.status === "deleting" || result.status === "deleted") {
      showNotice(elements.uploadStatus, "该论文正在删除，请稍后重新上传。", "warning");
    } else {
      showNotice(elements.uploadStatus, "上传成功，正在解析论文…", "info");
      await pollPaper(result.paper_id);
    }
    elements.pdfFile.value = "";
  } catch (error) {
    showNotice(elements.uploadStatus, `上传失败：${error.message}`, "error");
  } finally {
    elements.uploadButton.disabled = false;
  }
});

async function deletePaper(paper, button) {
  if (!window.confirm(`确定删除《${paperDisplayName(paper)}》吗？`)) return;
  button.disabled = true;
  try {
    await api(`/papers/${encodeURIComponent(paper.paper_id)}`, { method: "DELETE" });
    if (elements.paperSelect.value === paper.paper_id) {
      elements.paperSelect.value = "";
      elements.answerCard.hidden = true;
    }
    showNotice(elements.queryStatus, "论文及相关索引已删除。", "info");
    await loadPapers();
  } catch (error) {
    showNotice(elements.queryStatus, `删除失败：${error.message}`, "error");
    button.disabled = false;
  }
}

function renderAnswer(result) {
  elements.answerText.textContent = result.answer;
  elements.citationList.replaceChildren();
  for (const citation of result.citations || []) {
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = citation.paper_title;
    const page = citation.page_number == null ? "页码未知" : `第 ${citation.page_number} 页`;
    item.append(title, document.createTextNode(` · ${citation.section_path} · ${page}`));
    elements.citationList.append(item);
  }
  if (!result.citations?.length) {
    const item = document.createElement("li");
    item.textContent = "没有可展示的引用来源";
    elements.citationList.append(item);
  }

  elements.timings.replaceChildren();
  const timingOrder = ["hyde_ms", "dense_ms", "sparse_ms", "rerank_ms", "generate_ms", "total_ms"];
  for (const key of timingOrder) {
    if (result.timings?.[key] == null) continue;
    const chip = document.createElement("span");
    chip.className = "timing-chip";
    chip.textContent = `${key.replace("_ms", "")} ${result.timings[key]}ms`;
    elements.timings.append(chip);
  }
  elements.answerCard.hidden = false;
  elements.answerCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

elements.queryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const paperId = elements.paperSelect.value;
  const question = elements.question.value.trim();
  if (!paperId || !question) {
    showNotice(elements.queryStatus, "请选择论文并输入问题。", "warning");
    return;
  }
  elements.queryButton.disabled = true;
  elements.queryButton.classList.add("is-loading");
  elements.answerCard.hidden = true;
  showNotice(elements.queryStatus, "正在检索论文并生成回答，最长可能需要 60 秒…", "info");
  const payload = { question, paper_id: paperId };
  if (elements.sectionType.value) payload.section_type = elements.sectionType.value;
  try {
    const result = await api("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (result.error) throw new Error(result.error);
    hideNotice(elements.queryStatus);
    renderAnswer(result);
  } catch (error) {
    showNotice(elements.queryStatus, `问答失败：${error.message}`, "error");
  } finally {
    elements.queryButton.disabled = false;
    elements.queryButton.classList.remove("is-loading");
  }
});

elements.question.addEventListener("input", () => {
  elements.questionCount.textContent = String(elements.question.value.length);
});
elements.refreshPapers.addEventListener("click", loadPapers);

loadPapers();
