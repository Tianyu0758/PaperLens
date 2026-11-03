/*
 * 文件说明：提供工作台共用的内容格式化方法，包括文本转义、Markdown 展示、时间和文件大小。
 * 同时维护状态、阶段和工具名称的中文标签；模型回答先转义，再生成允许的展示标记。
 */

export const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
export const icon = (name) =>
  `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

// Model output is always escaped. Only the markup generated here can enter the DOM.
function inline(text) {
  const codes = [];
  text = String(text).replace(
    /`([^`]+)`/g,
    (_, code) =>
      `\u0001${codes.push(`<code>${escapeHTML(code)}</code>`) - 1}\u0001`,
  );
  return escapeHTML(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>")
    .replace(
      /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    )
    .replace(/\u0001(\d+)\u0001/g, (_, i) => codes[Number(i)] || "");
}

export function markdown(source) {
  const lines = String(source ?? "")
    .replace(/\r/g, "")
    .split("\n");
  const output = [];
  let paragraph = [],
    list = "",
    code = null;
  const flush = () => {
    if (paragraph.length)
      output.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    paragraph = [];
  };
  const closeList = () => {
    if (list) output.push(`</${list}>`);
    list = "";
  };
  const cells = (line) =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => cell.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      closeList();
      if (code === null) code = [];
      else {
        output.push(`<pre><code>${escapeHTML(code.join("\n"))}</code></pre>`);
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code.push(line);
      continue;
    }
    if (!line.trim()) {
      flush();
      closeList();
      continue;
    }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
    if (heading) {
      flush();
      closeList();
      const level = Math.min(4, heading[1].length);
      output.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
      flush();
      closeList();
      output.push("<hr>");
      continue;
    }
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i + 1])
    ) {
      flush();
      closeList();
      const head = cells(line);
      i++;
      let table = `<div class="table-scroll"><table><thead><tr>${head.map((cell) => `<th>${inline(cell)}</th>`).join("")}</tr></thead><tbody>`;
      while (
        i + 1 < lines.length &&
        lines[i + 1].includes("|") &&
        lines[i + 1].trim()
      ) {
        table += `<tr>${cells(lines[++i])
          .map((cell) => `<td>${inline(cell)}</td>`)
          .join("")}</tr>`;
      }
      output.push(table + "</tbody></table></div>");
      continue;
    }
    const item = line.match(/^\s*(?:([-*+])\s+|\d+[.)、]\s*)(.+)$/);
    if (item) {
      flush();
      const type = item[1] ? "ul" : "ol";
      if (list !== type) {
        closeList();
        list = type;
        output.push(`<${type}>`);
      }
      output.push(`<li>${inline(item[2])}</li>`);
      continue;
    }
    closeList();
    if (/^>\s?/.test(line)) {
      flush();
      output.push(
        `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`,
      );
      continue;
    }
    paragraph.push(line);
  }
  flush();
  closeList();
  if (code !== null)
    output.push(`<pre><code>${escapeHTML(code.join("\n"))}</code></pre>`);
  return output.join("\n");
}

export function bytes(size) {
  size = Number(size) || 0;
  return size >= 1048576
    ? `${(size / 1048576).toFixed(1)} MB`
    : size >= 1024
      ? `${Math.round(size / 1024)} KB`
      : `${size} B`;
}
export function duration(ms) {
  const seconds = Math.max(0, Math.round((Number(ms) || 0) / 1000));
  return seconds < 60
    ? `${seconds} 秒`
    : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
export function timeLabel(value) {
  if (!value || !Number.isFinite(new Date(value).getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
export const statusLabel = {
  queued: "等待开始",
  running: "正在执行",
  completed: "已完成",
  failed: "执行失败",
  interrupted: "已中断",
};
export const stageLabel = {
  queued: "任务已提交",
  validating_paper: "确认论文状态",
  loading_context: "恢复会话上下文",
  compacting_context: "整理较早的对话",
  analyzing: "研读与分析",
  planning_presentation: "规划演示结构",
  formatting_presentation: "校验幻灯片内容",
  rendering_presentation: "生成汇报文件",
  checking_presentation: "检查演示版式与交互",
  done: "已完成",
  error: "执行失败",
  interrupted: "执行已中断",
};
export const toolLabel = {
  search_paper: "检索论文证据",
  inspect_paper: "浏览论文结构",
  read_paper: "定向精读原文",
  list_artifacts: "查找已有成果与版本",
  execute_python: "运行 Python 计算",
  read_artifact: "读取已有文件",
  plan_presentation: "规划或修改演示稿",
  render_presentation: "生成 HTML 演示",
  preview_presentation: "检查页面与交互",
};
