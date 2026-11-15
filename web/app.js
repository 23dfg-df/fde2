"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ACTIVE_STATES = new Set(["queued", "running", "cancelling"]);
  const STATE_LABELS = { queued: "等待处理", running: "正在解析", cancelling: "正在取消", completed: "解析完成", partial: "结果不完整", failed: "解析失败", cancelled: "已取消", interrupted: "运行已中断" };
  const state = {
    pages: [], selectedIds: new Set(), selectedPageId: null,
    currentJob: null, activeJob: null, jobs: [], jobDetails: new Map(), status: null,
    connected: false, starting: false, uploading: false, cancelling: false,
    lastStatusAt: null, statusPromise: null, pollTimer: null, polling: false,
    activeTab: "text", noticeTimer: null, taskDefaultPageId: null,
  };

  const put = (id, text) => { $(id).textContent = text == null ? "—" : String(text); };
  const show = (id, visible) => { $(id).hidden = !visible; };
  const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const seconds = (value) => finite(value) ? `${Number(value).toFixed(2)} s` : "—";
  const percentage = (value) => finite(value) ? `${(Number(value) * 100).toFixed(1)}%` : "—";
  const gib = (value) => finite(value) ? `${(Number(value) / 1024).toFixed(1)} GiB` : "—";
  const isActive = (job) => Boolean(job && ACTIVE_STATES.has(job.status));
  const label = (status) => STATE_LABELS[status] || status || "尚未运行";
  const pageResults = (job) => Array.isArray(job?.pages) ? job.pages : [];
  const ownPage = (id) => state.pages.find((page) => page.id === id);
  const taskLabel = (task) => ({ structured_parse: "纯文本识别", table: "表格解析", doc_parse: "文档解析" })[task] || "文档解析";

  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }

  function dateLabel(value, timeOnly = false) {
    if (!value) return "时间未记录";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", timeOnly
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function rememberJob(id) {
    try { localStorage.setItem("doclens.lastJobId", id); } catch (_) { /* Storage is optional. */ }
  }

  function rememberedJob() {
    try { return localStorage.getItem("doclens.lastJobId"); } catch (_) { return null; }
  }

  function notify(message, kind = "info", duration = 0) {
    clearTimeout(state.noticeTimer);
    put("notice", message);
    $("notice").className = `notice ${kind}`;
    show("notice", Boolean(message));
    if (duration) state.noticeTimer = setTimeout(() => show("notice", false), duration);
  }

  function errorMessage(body, fallback) {
    const detail = body?.detail ?? body?.error ?? body?.message;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join("；");
    if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
    return fallback;
  }

  async function request(path, options = {}, timeout = 20000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, cache: "no-store" });
      let body;
      const text = await response.text();
      try { body = text ? JSON.parse(text) : {}; } catch (_) { throw new Error(`服务返回了无法读取的响应（HTTP ${response.status}）。`); }
      if (!response.ok) {
        const error = new Error(errorMessage(body, `请求失败（HTTP ${response.status}）。`));
        error.status = response.status;
        throw error;
      }
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("请求超时。请刷新状态；超时不代表服务器任务已经停止。");
      if (error instanceof TypeError) throw new Error("暂时无法连接工作台，请检查服务与 SSH 转发。");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function imageUrl(page) {
    const url = page?.image_url;
    return typeof url === "string" && url.startsWith("/api/images/") && !url.startsWith("//")
      ? url : `/api/images/${encodeURIComponent(page?.id || page?.page_id || "")}`;
  }

  function statusClass(value) {
    if (value === "completed") return "success";
    if (value === "failed" || value === "interrupted") return "error";
    if (value === "partial" || value === "cancelled") return "warning";
    if (ACTIVE_STATES.has(value)) return "running";
    return "neutral";
  }

  function renderPageList() {
    const list = $("page-list");
    list.replaceChildren();
    if (!state.pages.length) {
      list.append(element("p", "list-placeholder", "还没有文档，请上传图片或 PDF。"));
      updateControls();
      return;
    }
    for (const page of state.pages) {
      const row = element("div", `page-item${state.selectedPageId === page.id ? " active" : ""}`);
      const checkbox = element("input", "page-checkbox");
      checkbox.type = "checkbox";
      checkbox.checked = state.selectedIds.has(page.id);
      checkbox.disabled = Boolean(state.starting || isActive(state.activeJob));
      checkbox.setAttribute("aria-label", `选择 ${page.name}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && state.selectedIds.size >= 6) {
          checkbox.checked = false;
          notify("一次实验最多选择 6 页，请先取消部分选择。", "info");
          return;
        }
        if (checkbox.checked) state.selectedIds.add(page.id); else state.selectedIds.delete(page.id);
        applyTaskDefault();
        selectPage(page.id, false);
        renderPageList();
      });
      const open = element("button", "page-open");
      open.type = "button";
      open.setAttribute("aria-label", `预览 ${page.name}`);
      open.setAttribute("aria-pressed", String(state.selectedPageId === page.id));
      const thumb = element("img", "page-thumbnail");
      thumb.src = imageUrl(page);
      thumb.alt = "";
      thumb.loading = "lazy";
      const copy = element("span", "page-copy");
      copy.append(element("span", "page-name", page.name));
      copy.append(element("span", "page-description", page.has_reference ? "教学样例 · 有参考标注" : "上传文档 · 待人工核对"));
      open.append(thumb, copy);
      open.addEventListener("click", () => selectPage(page.id));
      row.append(checkbox, open);
      list.append(row);
    }
    updateControls();
  }

  function selectPage(id, refreshList = true) {
    state.selectedPageId = id;
    renderSource();
    renderResult();
    if (refreshList) renderPageList();
  }

  function renderTaskHint() {
    const hints = {
      structured_parse: "提取图中的文字；有全文参考的样例可计算 CER。",
      doc_parse: "解析正文、公式与表格，可能忽略页眉页脚；不与全文参考计算 CER。",
      table: "提取 HTML 表格结构；单元格覆盖仍需配合人工核对。",
    };
    put("task-hint", hints[$("task-select").value]);
  }

  function applyTaskDefault() {
    if (state.selectedIds.size !== 1 || isActive(state.activeJob) || state.starting) return;
    const id = [...state.selectedIds][0];
    if (id === state.taskDefaultPageId) return;
    const page = ownPage(id);
    if (!page) return;
    $("task-select").value = page.evaluation_mode === "table" ? "table"
      : ["text", "fields"].includes(page.evaluation_mode) ? "structured_parse" : "doc_parse";
    state.taskDefaultPageId = id;
    renderTaskHint();
  }

  function renderSource() {
    const historical = pageResults(state.currentJob).find((item) => item.page_id === state.selectedPageId);
    const page = ownPage(state.selectedPageId) || historical;
    const image = $("source-image");
    show("empty-source", !page);
    show("source-image", Boolean(page));
    $("zoom-button").disabled = !page;
    if (!page) {
      put("source-name", "尚未选择文档");
      put("source-dimensions", "选择左侧样例或上传文件");
      put("reference-badge", "参考标注待检查");
      $("reference-badge").className = "reference-badge";
      return;
    }
    put("source-name", page.name || "文档页面");
    put("source-dimensions", page.width && page.height ? `${page.width} × ${page.height} px` : "文档原图");
    image.src = imageUrl(page);
    image.alt = `${page.name || "文档"}的原始图像`;
    put("reference-badge", page.has_reference ? "有参考标注" : "无参考标注 · 人工核对");
    $("reference-badge").className = `reference-badge${page.has_reference ? " available" : ""}`;
    put("source-kind", page.kind === "pdf" || page.kind === "pdf_page" ? "PDF 页面渲染" : "原图用于人工核对");
  }

  function renderEvaluation(result) {
    const root = $("evaluation-content");
    root.replaceChildren();
    const evaluation = result?.evaluation;
    if (!evaluation?.available) {
      root.append(element("h3", "", result ? "本页暂无自动评测" : "先完成一次解析"));
      root.append(element("p", "evaluation-note", evaluation?.note || (result
        ? "没有可用参考标注，或本页尚未完成处理。请将解析文本与原图逐项对照。"
        : "教学样例带有参考标注。模型返回真实结果后，才会计算对应指标。")));
      root.append(element("p", "evaluation-small", "上传自己的文档不会自动得到准确率；文件处理成功与识别正确需要分别判断。"));
      return;
    }
    root.append(element("span", "review-flag", evaluation.human_verified ? "已记录人工复核状态" : "自动对照 · 尚未经人工复核"));
    const scores = element("div", "evaluation-score");
    const score = (title, value, note) => {
      const tile = element("div", "");
      tile.append(element("small", "", title), element("strong", "", value), element("small", "", note));
      scores.append(tile);
    };
    if (finite(evaluation.cer)) score("字符错误率 CER", percentage(evaluation.cer), "越低越好；可能超过 100%");
    if (finite(evaluation.edit_distance)) score("字符编辑距离", evaluation.edit_distance, `参考字符 ${evaluation.reference_characters ?? "—"}`);
    if (finite(evaluation.field_coverage)) score("指定字段覆盖", percentage(evaluation.field_coverage), "仅验证指定内容是否出现");
    if (finite(evaluation.table_cell_coverage)) score("指定单元格覆盖", percentage(evaluation.table_cell_coverage), `${evaluation.matched_cell_count ?? "—"} / ${evaluation.expected_cell_count ?? "—"} 个单元格`);
    if (scores.children.length) root.append(scores);
    if (result.status === "partial") root.append(element("p", "evaluation-note", "本页结果不完整。以下指标仅描述已返回文本，不能作为完整解析成功的证明。"));
    root.append(element("p", "evaluation-note", evaluation.note || "自动指标的范围有限，仍需核对语义、数字、顺序及表格结构。"));
    const fields = (title, values, missing) => {
      if (!Array.isArray(values) || !values.length) return;
      root.append(element("h3", "", title));
      const list = element("ul", "evaluation-list");
      for (const value of values) list.append(element("li", missing ? "missing" : "", typeof value === "object" ? JSON.stringify(value) : value));
      root.append(list);
    };
    fields("已匹配字段", evaluation.matched_fields, false);
    fields("未匹配字段", evaluation.missing_fields, true);
    root.append(element("p", "evaluation-small", "字段或单元格覆盖不等于位置、阅读顺序或语义全部正确。请保留你自己的复核记录。"));
  }

  function renderResult() {
    const result = pageResults(state.currentJob).find((item) => item.page_id === state.selectedPageId);
    put("result-status", result ? label(result.status) : "尚未运行");
    $("result-status").className = `status-pill ${statusClass(result?.status)}`;
    put("metric-time", result ? seconds(result.elapsed_seconds) : "—");
    put("metric-tokens", result && (finite(result.input_tokens) || finite(result.output_tokens))
      ? `${finite(result.input_tokens) ? result.input_tokens : "—"} / ${finite(result.output_tokens) ? result.output_tokens : "—"}` : "—");
    const hasResult = Boolean(result && !["queued"].includes(result.status));
    show("result-empty", !hasResult);
    show("output-wrapper", hasResult);
    if (result?.status === "queued") {
      $("result-empty").querySelector("h3").textContent = "本页正在等待处理";
      $("result-empty").querySelector("p").textContent = "本实验按顺序逐页解析，完成后会自动显示结果。";
    } else if (!hasResult) {
      $("result-empty").querySelector("h3").textContent = state.currentJob ? "这页不在当前实验中" : "等待第一次真实解析";
      $("result-empty").querySelector("p").textContent = state.currentJob ? "选择实验中包含的页面，或勾选这页开始一次新实验。" : "选择文档并运行后，这里会显示模型返回的文字。结果不会预先填充。";
    }
    const output = result?.text ?? result?.raw_output ?? "";
    put("output-text", output || (result?.status === "running" ? "正在等待模型返回完整结果…" : result ? "本页没有返回文本。" : ""));
    put("output-format", state.currentJob?.task === "table" ? "HTML 表格 · 纯文本查看" : state.currentJob?.task === "structured_parse" ? "识别文字 · 纯文本查看" : "Markdown / 结构化内容 · 纯文本查看");
    const problem = result?.error || (result?.status === "partial" ? "输出未完整结束，请检查 finish_reason 与 token 上限。" : result?.status === "cancelled" ? "本页已取消，未产生完整解析结果。" : result?.status === "interrupted" ? "服务重启使本页中断，请保留记录后重新运行。" : "");
    show("page-error", Boolean(problem));
    put("page-error", typeof problem === "object" ? JSON.stringify(problem) : problem);
    put("record-json", result ? JSON.stringify({ job_id: state.currentJob.id, task: state.currentJob.task, model: state.currentJob.model, ...result }, null, 2) : "尚无运行记录。");
    put("result-model", result ? `${state.currentJob.model || "模型身份未返回"} · ${result.finish_reason || label(result.status)}` : "当前还没有模型输出");
    $("copy-output").disabled = !output;
    $("download-json").disabled = !state.currentJob;
    renderEvaluation(result);
  }

  function updateControls() {
    const active = isActive(state.activeJob);
    const modelReady = state.connected && state.status?.model_ready === true;
    const tokens = Number($("max-tokens").value);
    const validTokens = Number.isInteger(tokens) && tokens >= 256 && tokens <= 4096;
    put("selection-count", `已选 ${state.selectedIds.size} / 6`);
    $("run-button").disabled = !modelReady || active || state.starting || state.uploading || !state.selectedIds.size || !validTokens;
    $("task-select").disabled = active || state.starting;
    $("max-tokens").disabled = active || state.starting;
    $("file-input").disabled = active || state.starting || state.uploading;
    $("upload-zone").setAttribute("aria-disabled", String($("file-input").disabled));
    put("run-label", state.starting ? "正在创建实验…" : active ? "实验处理中" : `开始解析${state.selectedIds.size > 1 ? ` · ${state.selectedIds.size} 页` : ""}`);
    put("run-hint", !state.connected ? "等待与服务器建立连接。"
      : !modelReady ? "独立模型服务尚未启动、正在加载或已停止。历史仍可查看；推理前需启动模型。"
        : active ? "一个任务逐页处理，当前页完成后可取消后续页。"
          : !state.selectedIds.size ? "请勾选至少一页文档。"
            : !validTokens ? "输出上限须为 256–4096 的整数。" : "使用 GPU 1 · 单任务逐页处理");
    show("active-banner", active);
    if (active) {
      const current = state.currentJob?.id === state.activeJob.id ? state.currentJob : null;
      const pages = pageResults(current);
      const finished = pages.filter((page) => !ACTIVE_STATES.has(page.status)).length;
      put("active-title", state.cancelling ? "已请求取消" : label(state.activeJob.status));
      put("active-description", pages.length ? `已处理 ${finished} / ${pages.length} 页。${state.cancelling ? "当前页结束后停止后续页面。" : "结果会自动更新。"}` : "服务器存在进行中的实验，可从最近实验查看。 ");
    }
    $("cancel-button").disabled = !active || state.cancelling;
    put("cancel-button", state.cancelling ? "等待当前页结束" : "取消后续页面");
    document.querySelectorAll(".page-checkbox").forEach((checkbox) => { checkbox.disabled = active || state.starting; });
  }

  function renderStatus() {
    const status = state.status;
    const ready = state.connected && status?.model_ready === true;
    $("model-badge").className = `model-badge ${ready ? "ready" : state.connected ? "" : "error"}`;
    put("model-badge-text", ready ? "模型已就绪" : state.connected ? "模型未就绪" : status ? "连接中断 · 旧采样" : "等待连接");
    const gpu = Array.isArray(status?.gpu) ? status.gpu.find((item) => item.index === 1) : status?.gpu;
    if (gpu) {
      const used = gpu.memory_used_mib;
      const total = gpu.memory_total_mib;
      const ratio = finite(used) && finite(total) && Number(total) > 0 ? Math.max(0, Math.min(100, Number(used) / Number(total) * 100)) : 0;
      put("resource-title", `GPU ${gpu.index ?? 1}`);
      put("gpu-name", gpu.name || "GPU 型号未返回");
      put("gpu-memory", finite(used) && finite(total) ? `${(Number(used) / 1024).toFixed(1)} / ${(Number(total) / 1024).toFixed(1)} GiB` : "—");
      put("gpu-free", `剩余 ${gib(gpu.memory_free_mib)}`);
      put("gpu-utilization", finite(gpu.utilization_percent) ? `利用率 ${gpu.utilization_percent}%` : "利用率 —");
      $("memory-fill").style.width = `${ratio}%`;
      $("memory-meter").setAttribute("aria-valuenow", ratio.toFixed(1));
    } else {
      put("gpu-name", "暂无可用资源采样"); put("gpu-memory", "—"); put("gpu-free", "剩余 —"); put("gpu-utilization", "利用率 —");
      $("memory-fill").style.width = "0%"; $("memory-meter").setAttribute("aria-valuenow", "0");
    }
    put("sample-time", state.lastStatusAt ? `${state.connected ? "状态获取" : "上次状态"} ${dateLabel(state.lastStatusAt, true)} · 整卡采样，包含其他进程。` : "每 15 秒获取状态；整卡采样包含其他进程。");
    updateControls();
  }

  async function refreshStatus() {
    if (state.statusPromise) return state.statusPromise;
    state.statusPromise = (async () => {
      try {
        const payload = await request("/api/status");
        state.status = payload;
        state.connected = true;
        state.lastStatusAt = new Date().toISOString();
        if (payload.active_job) {
          state.activeJob = typeof payload.active_job === "string" ? { id: payload.active_job, status: "running" } : payload.active_job;
          schedulePoll();
        } else if (!isActive(state.currentJob)) {
          state.activeJob = null; state.cancelling = false;
        }
      } catch (_) { state.connected = false; }
      finally { renderStatus(); state.statusPromise = null; }
    })();
    return state.statusPromise;
  }

  async function loadPages() {
    const payload = await request("/api/pages");
    state.pages = Array.isArray(payload.pages) ? payload.pages : [];
    state.selectedIds = new Set([...state.selectedIds].filter((id) => ownPage(id)));
    if (!state.selectedPageId && state.pages.length) {
      state.selectedPageId = state.pages[0].id;
      state.selectedIds.add(state.pages[0].id);
    }
    applyTaskDefault();
    renderPageList(); renderSource(); renderResult();
  }

  function renderHistory() {
    const root = $("history-list");
    root.replaceChildren();
    put("history-count", `${state.jobs.length} 条记录`);
    if (!state.jobs.length) {
      root.append(element("p", "history-empty", "首次解析完成后，实验会保存在这里；刷新页面仍可继续查看。"));
      return;
    }
    for (const job of state.jobs.slice(0, 20)) {
      const row = element("button", `history-row${state.currentJob?.id === job.id ? " selected" : ""}`);
      row.type = "button";
      const main = element("span", "history-main");
      const pages = pageResults(state.jobDetails.get(job.id) || job);
      const count = job.page_count ?? job.total_pages ?? job.summary?.total ?? pages.length;
      const firstName = pages[0]?.name || job.name || taskLabel(job.task);
      main.append(element("strong", "", firstName), element("small", "", `${dateLabel(job.created_at)} · ${taskLabel(job.task)} · ${String(job.id || "").slice(0, 8)}`));
      row.append(main, element("span", "history-pages", `${count || "—"} 页`), element("span", `status-pill ${statusClass(job.status)}`, label(job.status)));
      row.addEventListener("click", () => loadJob(job.id, true).catch((error) => notify(error.message, "error")));
      root.append(row);
    }
  }

  async function loadHistory() {
    const payload = await request("/api/jobs");
    state.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    renderHistory();
    return state.jobs;
  }

  function setCurrentJob(job, chooseFirst = false) {
    state.currentJob = job;
    state.jobDetails.set(job.id, job);
    rememberJob(job.id);
    const pages = pageResults(job);
    if (chooseFirst) {
      // Restoring an experiment restores its controls; background polling never does.
      state.selectedIds = new Set(pages.map((page) => page.page_id).filter((id) => ownPage(id)));
      state.taskDefaultPageId = state.selectedIds.size === 1 ? [...state.selectedIds][0] : null;
      if (["structured_parse", "doc_parse", "table"].includes(job.task)) $("task-select").value = job.task;
      if (Number.isInteger(job.max_tokens) && job.max_tokens >= 256 && job.max_tokens <= 4096) $("max-tokens").value = job.max_tokens;
      renderTaskHint();
    }
    if (chooseFirst || !pages.some((page) => page.page_id === state.selectedPageId)) {
      if (pages.length) state.selectedPageId = pages[0].page_id;
    }
    if (isActive(job)) { state.activeJob = { id: job.id, status: job.status }; state.cancelling = Boolean(job.cancel_requested); schedulePoll(); }
    else if (state.activeJob?.id === job.id) { state.activeJob = null; state.cancelling = false; }
    renderPageList(); renderSource(); renderResult(); renderHistory(); updateControls();
  }

  async function loadJob(id, chooseFirst = false) {
    const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
    setCurrentJob(job, chooseFirst);
    return job;
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (state.activeJob?.id) state.pollTimer = setTimeout(pollActiveJob, document.hidden ? 5000 : 1800);
  }

  async function pollActiveJob() {
    if (!state.activeJob?.id || state.polling) return;
    const id = state.activeJob.id;
    state.polling = true;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
      if (!state.currentJob || state.currentJob.id === id) setCurrentJob(job);
      else state.activeJob = isActive(job) ? { id: job.id, status: job.status } : null;
      if (!isActive(job)) {
        state.cancelling = false;
        await Promise.allSettled([loadHistory(), refreshStatus()]);
      }
      updateControls();
    } catch (error) {
      notify(`实验状态暂时无法更新：${error.message}`, "error");
    } finally { state.polling = false; schedulePoll(); }
  }

  async function upload(file) {
    if (!file || state.uploading || isActive(state.activeJob)) return;
    if (file.size > 12 * 1024 * 1024) { notify("文件超过 12 MiB，请先减小文件大小。", "error"); return; }
    if (!/\.(png|jpe?g|webp|pdf)$/i.test(file.name)) { notify("请选择 PNG、JPG、WebP 图片或 PDF 文件。", "error"); return; }
    state.uploading = true; $("upload-zone").classList.add("uploading"); put("upload-label", "正在准备文档…"); updateControls();
    const data = new FormData(); data.append("file", file);
    try {
      const payload = await request("/api/uploads", { method: "POST", body: data }, 60000);
      await loadPages();
      const uploaded = Array.isArray(payload.pages) ? payload.pages : [];
      if (uploaded.length) {
        state.selectedIds = new Set(uploaded.slice(0, 6).map((page) => page.id));
        $("task-select").value = "doc_parse";
        state.taskDefaultPageId = uploaded.length === 1 ? uploaded[0].id : null;
        renderTaskHint();
        selectPage(uploaded[0].id);
      }
      notify(`文档已准备好，共 ${uploaded.length} 页。请检查原图后开始解析。`, "success", 9000);
    } catch (error) { notify(error.message, "error"); }
    finally { state.uploading = false; $("upload-zone").classList.remove("uploading"); put("upload-label", "上传或拖入文档"); $("file-input").value = ""; updateControls(); }
  }

  async function startJob(event) {
    event.preventDefault();
    if ($("run-button").disabled || state.starting) return;
    state.starting = true; updateControls(); notify("");
    try {
      const payload = await request("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ page_ids: [...state.selectedIds], task: $("task-select").value, max_tokens: Number($("max-tokens").value) }) });
      const id = payload.id || payload.job_id;
      if (!id) throw new Error("服务没有返回实验 ID，请刷新状态确认任务是否已经创建。");
      state.cancelling = false;
      state.activeJob = { id, status: payload.status || "queued" };
      if (Array.isArray(payload.pages)) setCurrentJob(payload, true); else await loadJob(id, true);
      selectTab("text");
      await loadHistory();
      schedulePoll();
    } catch (error) { notify(error.message, "error"); await refreshStatus(); }
    finally { state.starting = false; updateControls(); }
  }

  async function cancelJob() {
    if (!state.activeJob?.id || state.cancelling) return;
    const id = state.activeJob.id;
    state.cancelling = true; updateControls();
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      if (job.id && state.currentJob?.id === job.id) setCurrentJob(job);
      notify("取消请求已提交。当前页完成后，后续页面将停止处理。", "info");
      schedulePoll();
    } catch (error) { state.cancelling = false; notify(error.message, "error"); updateControls(); }
  }

  function selectTab(name) {
    state.activeTab = name;
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const active = button.dataset.tab === name;
      button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
      show(`view-${button.dataset.tab}`, active);
    });
  }

  async function copyOutput() {
    const result = pageResults(state.currentJob).find((page) => page.page_id === state.selectedPageId);
    const output = result?.text ?? result?.raw_output ?? "";
    if (!output) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(output);
      put("copy-output", "已复制"); setTimeout(() => put("copy-output", "复制文本"), 2000);
    } catch (_) { notify("浏览器未允许自动复制。可在解析文本中选中内容，使用系统复制快捷键。", "info"); }
  }

  function downloadRecord() {
    if (!state.currentJob) return;
    const blob = new Blob([JSON.stringify(state.currentJob, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `doclens-${String(state.currentJob.id).replace(/[^a-zA-Z0-9_-]/g, "")}.json`;
    document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openImage() {
    const page = ownPage(state.selectedPageId) || pageResults(state.currentJob).find((result) => result.page_id === state.selectedPageId);
    if (!page) return;
    $("dialog-image").src = imageUrl(page); $("dialog-image").alt = `${page.name || "文档"}的完整原图`;
    put("image-dialog-title", page.name || "原始文档"); $("image-dialog").showModal();
  }

  $("run-form").addEventListener("submit", startJob);
  $("max-tokens").addEventListener("input", updateControls);
  $("task-select").addEventListener("change", renderTaskHint);
  $("file-input").addEventListener("change", () => upload($("file-input").files[0]));
  $("cancel-button").addEventListener("click", cancelJob);
  $("copy-output").addEventListener("click", copyOutput);
  $("download-json").addEventListener("click", downloadRecord);
  $("zoom-button").addEventListener("click", openImage);
  $("source-image").addEventListener("error", () => { notify("原图暂时无法加载，请刷新文档或检查服务器文件。", "error"); });
  $("refresh-button").addEventListener("click", async () => {
    $("refresh-button").disabled = true;
    const results = await Promise.allSettled([refreshStatus(), loadPages(), loadHistory()]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) notify(failed.reason.message, "error");
    else if (state.connected) notify("文档、服务与实验状态已刷新。", "success", 5000);
    if (state.currentJob?.id) await loadJob(state.currentJob.id).catch((error) => notify(error.message, "error"));
    $("refresh-button").disabled = false;
  });
  document.querySelectorAll("[data-tab]").forEach((button, index, buttons) => {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      selectTab(buttons[next].dataset.tab); buttons[next].focus();
    });
  });
  for (const id of ["help-button", "help-inline"]) $(id).addEventListener("click", () => $("help-dialog").showModal());
  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(button.dataset.close).close()));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  }));
  $("upload-zone").addEventListener("keydown", (event) => {
    if (["Enter", " "].includes(event.key)) { event.preventDefault(); if (!$("file-input").disabled) $("file-input").click(); }
  });
  $("upload-zone").addEventListener("dragover", (event) => { event.preventDefault(); if (!$("file-input").disabled) $("upload-zone").classList.add("dragover"); });
  $("upload-zone").addEventListener("dragleave", () => $("upload-zone").classList.remove("dragover"));
  $("upload-zone").addEventListener("drop", (event) => {
    event.preventDefault(); $("upload-zone").classList.remove("dragover");
    if ($("file-input").disabled) return;
    if (event.dataTransfer.files.length > 1) { notify("每次请上传一个文件；一个 PDF 可包含最多 6 页。", "info"); return; }
    upload(event.dataTransfer.files[0]);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refreshStatus(); if (state.activeJob) schedulePoll(); } });

  async function initialize() {
    const results = await Promise.allSettled([refreshStatus(), loadPages(), loadHistory()]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) notify(failed.reason.message, "error");
    const id = state.activeJob?.id || rememberedJob() || state.jobs[0]?.id;
    if (id) await loadJob(id, true).catch(() => { if (state.jobs[0]?.id && state.jobs[0].id !== id) loadJob(state.jobs[0].id, true).catch(() => {}); });
    updateControls();
    setInterval(() => { if (!document.hidden) refreshStatus(); }, 15000);
  }
  initialize();
})();
