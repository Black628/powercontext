/*! Recall Coach �?browser client. Vanilla JS, no build step, no CDN. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    status: null,
    materials: [],
    courses: [],
    session: null,
    cardIndex: 0,
    graded: 0,
    verdicts: { correct: 0, hard: 0, again: 0 },
    filter: "",
    evidenceTab: "store",
    memory: null,
  };

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = { detail: text };
    }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : response.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  };

  let toastTimer = null;
  const toast = (message, isError = false) => {
    const node = $("toast");
    node.textContent = message;
    node.classList.toggle("error", isError);
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), isError ? 6000 : 3200);
  };

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

  const pct = (value) => `${Math.round((Number(value) || 0) * 100)}%`;

  // ---------------------------------------------------------------- header
  const renderChips = () => {
    const status = state.status;
    if (!status) return;
    const backend = status.backend || {};
    const memory = status.memory || {};
    const embedding = status.embedding || {};
    const isSeekdb = backend.backend === "seekdb";
    const chips = [
      `<span class="chip ${isSeekdb ? "seekdb" : "mirror"}">
         <span class="dot"></span>${isSeekdb ? "seekdb" : "SQLite 镜像"}
         <strong>${esc(backend.mode || "")}</strong>
       </span>`,
      `<span class="chip"><span class="dot"></span>embedding <strong>${esc(embedding.name || "")}</strong></span>`,
      `<span class="chip ${memory.available ? "pc" : "off"}">
         <span class="dot"></span>PowerContext <strong>${memory.available ? "在线" : "离线"}</strong>
       </span>`,
    ];
    $("chips").innerHTML = chips.join("");
    const sub = $("material-sub");
    if (sub) sub.textContent = isSeekdb ? "检索底�?· seekdb 向量 + 全文" : "检索底�?· SQLite 镜像（seekdb 不可用）";
  };

  const renderStats = (stats) => {
    if (!stats) return;
    $("stat-materials").textContent = stats.materials ?? 0;
    $("stat-mastery").textContent = pct(stats.avg_mastery ?? 0);
    $("stat-reviews").textContent = stats.reviews ?? 0;
    $("stat-weak").textContent = stats.weak ?? 0;
  };

  // ------------------------------------------------------------- materials
  const loadMaterials = async () => {
    const payload = await api("/api/materials");
    state.materials = payload.items || [];
    state.courses = payload.courses || [];
    renderMaterials();
    renderCourseOptions();
  };

  const renderCourseOptions = () => {
    const courses = state.courses.map((item) => item.course);
    $("course-options").innerHTML = courses.map((course) => `<option value="${esc(course)}">${esc(course)}</option>`).join("");

    const select = $("session-course");
    const current = select.value;
    select.innerHTML =
      `<option value="">全部课程</option>` +
      courses.map((course) => `<option value="${esc(course)}">${esc(course)}</option>`).join("");
    select.value = courses.includes(current) ? current : "";

    const filter = $("course-filter");
    filter.innerHTML =
      `<button class="tab ${state.filter ? "" : "active"}" data-course="">全部</button>` +
      courses
        .map((course) => `<button class="tab ${state.filter === course ? "active" : ""}" data-course="${esc(course)}">${esc(course)}</button>`)
        .join("");
    filter.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.course || "";
        renderCourseOptions();
        renderMaterials();
      });
    });
  };

  const renderMaterials = () => {
    const list = $("material-list");
    const items = state.filter ? state.materials.filter((item) => item.course === state.filter) : state.materials;
    if (!items.length) {
      list.innerHTML = `<div class="empty">还没有资料。点「载入示例」或导入一段笔记�?/div>`;
      return;
    }
    list.innerHTML = items
      .map(
        (item) => `
      <div class="material">
        <div class="head">
          <b>${esc(item.title)}</b>
          <span class="course">${esc(item.course)}</span>
        </div>
        <div class="body">${esc(item.body)}</div>
        <div class="mastery-line">
          <span>掌握 ${pct(item.mastery)}</span>
          <span class="bar"><i style="width:${Math.max(2, Math.round((item.mastery || 0) * 100))}%"></i></span>
          <span>复习 ${item.reviews} �?/span>
        </div>
      </div>`,
      )
      .join("");
  };

  const importMaterial = async () => {
    const text = $("import-text").value.trim();
    const title = $("import-title").value.trim() || "未命名笔�?;
    const course = $("import-course").value.trim() || "未分�?;
    if (!text) {
      toast("先粘贴一点笔记内�?, true);
      return;
    }
    try {
      const result = await api("/api/materials", {
        method: "POST",
        body: JSON.stringify({ course, title, text, source: "manual" }),
      });
      $("import-text").value = "";
      $("import-title").value = "";
      $("import-hint").textContent = `已写�?${result.passages} 段（${result.course}）`;
      renderStats(result.stats);
      await loadMaterials();
      toast(`导入成功�?{result.passages} 段已进入 seekdb`);
    } catch (error) {
      toast(`导入失败�?{error.message}`, true);
    }
  };

  const uploadMaterial = async () => {
    const input = $("upload-file");
    if (!input.files || !input.files.length) return;
    const form = new FormData();
    form.append("file", input.files[0]);
    form.append("course", $("import-course").value.trim() || "未分�?);
    form.append("title", $("import-title").value.trim());
    try {
      const result = await api("/api/materials/upload", { method: "POST", body: form });
      input.value = "";
      renderStats(result.stats);
      await loadMaterials();
      toast(`上传成功�?{result.passages} 段（${result.bytes} 字节）`);
    } catch (error) {
      toast(`上传失败�?{error.message}`, true);
    }
  };

  const seed = async () => {
    try {
      const result = await api("/api/seed", { method: "POST" });
      renderStats(result.stats);
      await loadMaterials();
      toast(result.imported ? `已载入示例：${result.imported} 段` : "示例资料已存在，无需重复载入");
    } catch (error) {
      toast(`载入失败�?{error.message}`, true);
    }
  };

  // ---------------------------------------------------------------- review
  const startSession = async () => {
    const course = $("session-course").value || null;
    const budget = Number($("session-budget").value);
    try {
      const session = await api("/api/session", { method: "POST", body: JSON.stringify({ course, budget }) });
      state.session = session;
      state.cardIndex = 0;
      state.graded = 0;
      state.verdicts = { correct: 0, hard: 0, again: 0 };
      renderSessionHeader(session);
      renderCard();
      await refreshEvidence();
    } catch (error) {
      toast(`无法开始：${error.message}`, true);
    }
  };

  const renderSessionHeader = (session) => {
    const memory = session.memory || {};
    const context = memory.context || {};
    const preference = memory.preference || "";
    const hits = memory.hits || [];
    const parts = [];
    parts.push(`
      <div class="memory-banner">
        <h3>PowerContext 记得的你 <span class="pill">${esc(memory.available ? "记忆在线" : "记忆离线")}</span>
          ${preference ? `<span class="pill">${esc(preference)}</span>` : ""}
        </h3>
        <pre>${esc(context.content || "（这一轮还没有可复用的上下文）")}</pre>
        ${
          hits.length
            ? `<div class="tiny">命中的记忆：${hits
                .slice(0, 4)
                .map((hit) => `<b>${esc((hit.text || "").split("�?)[0])}</b>`)
                .join(" · ")}</div>`
            : ""
        }
      </div>`);
    $("review-stage").innerHTML = parts.join("");
  };

  const renderCard = () => {
    const session = state.session;
    if (!session) return;
    const stage = $("review-stage");
    const card = session.cards[state.cardIndex];
    if (!card) {
      const total = session.cards.length;
      stage.insertAdjacentHTML(
        "beforeend",
        `<div class="card">
           <div class="question">本轮完成</div>
           <div class="prompt">答对 ${state.verdicts.correct} · 有点�?${state.verdicts.hard} · 忘了 ${state.verdicts.again}（共 ${total} 张）</div>
           <div class="tiny">答错的卡片已写入 PowerContext 的学习记忆；下一轮会更早出现，并带上更贴合的提示�?/div>
           <div class="row"><button class="primary" id="btn-again">再开一�?/button></div>
         </div>`,
      );
      const again = $("btn-again");
      if (again) again.addEventListener("click", startSession);
      return;
    }
    const total = session.cards.length;
    const progress = session.cards
      .map((_, index) => `<i class="${index < state.cardIndex ? "done" : index === state.cardIndex ? "now" : ""}"></i>`)
      .join("");
    const options = card.options
      .map((option, index) => `<button class="option" data-option="${esc(option)}"><span class="idx">${index + 1}.</span>${esc(option)}</button>`)
      .join("");
    const html = `
      <div class="card" id="current-card">
        <div class="card-head">
          <span>${esc(card.course)} · �?${state.cardIndex + 1}/${total} �?/span>
          <span class="grow"></span>
          <span class="tiny">记忆相关�?${card.affinity ?? 0}</span>
        </div>
        <div class="progress">${progress}</div>
        <div class="question">${esc(card.question)}</div>
        <div class="prompt">${esc(card.prompt)}</div>
        <div class="options">${options}</div>
        <div class="row">
          <button class="ghost" id="btn-hard">有点难，看答�?/button>
          <span class="tiny">选项�?seekdb 从本课程相似段落里挑出来</span>
        </div>
        <div id="verdict-slot"></div>
      </div>`;
    stage.insertAdjacentHTML("beforeend", html);
    const node = $("current-card");
    node.querySelectorAll(".option").forEach((button) => {
      button.addEventListener("click", () => submitGrade(card, button.dataset.option));
    });
    $("btn-hard").addEventListener("click", () => submitGrade(card, null, "hard"));
  };

  const submitGrade = async (card, picked, verdict = null) => {
    const node = $("current-card");
    if (!node || node.dataset.done === "1") return;
    node.dataset.done = "1";
    node.querySelectorAll(".option").forEach((button) => (button.disabled = true));
    try {
      const body = { material_id: card.material_id, verdict, picked };
      const result = await api(`/api/session/${state.session.session_id}/grade`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.verdicts[result.verdict] = (state.verdicts[result.verdict] || 0) + 1;
      node.querySelectorAll(".option").forEach((button) => {
        if (button.dataset.option === result.answer) button.classList.add("correct");
        else if (picked && button.dataset.option === picked && picked !== result.answer) button.classList.add("wrong");
      });
      const slot = $("verdict-slot");
      const tone = result.verdict === "correct" ? "ok" : result.verdict === "hard" ? "hard" : "bad";
      const related = (result.related || [])
        .map(
          (item) => `<div class="item"><b>${esc(item.title)}</b>
            <div class="meta">融合�?${item.fused} · 向量�?${item.vector_rank ?? "�?} / 全文�?${item.lexical_rank ?? "�?}</div>
            <div>${esc(item.snippet)}</div></div>`,
        )
        .join("");
      slot.innerHTML = `
        <div class="verdict ${tone}">
          <div><b>${result.verdict === "correct" ? "答对�? : result.verdict === "hard" ? "标记为「有点难�? : "答错�?}</b>
            · 正确答案�?b>${esc(result.answer)}</b> · 掌握�?${pct(result.mastery)}</div>
          ${
            result.memory_write
              ? `<div class="tiny">已写�?PowerContext�?{esc((result.memory_write.entry || {}).text || (result.memory_write.memory || {}).memory_ref || "一条学习记�?)}</div>`
              : `<div class="tiny">本次未产生新的记忆（连续答对到里程碑或状态未变时不会重复写）�?/div>`
          }
        </div>
        ${related ? `<div class="related"><div class="tiny">seekdb 查到的相关原文：</div>${related}</div>` : ""}
        <div class="row"><button class="primary" id="btn-next">下一�?/button></div>`;
      $("btn-next").addEventListener("click", () => {
        state.cardIndex += 1;
        renderCard();
      });
      await Promise.all([loadMaterials(), refreshMemory(), refreshEvidence()]);
      if (result.backend) renderStats(result.backend);
    } catch (error) {
      node.dataset.done = "0";
      node.querySelectorAll(".option").forEach((button) => (button.disabled = false));
      toast(`提交失败�?{error.message}`, true);
    }
  };

  // ----------------------------------------------------------------- memory
  const refreshMemory = async () => {
    try {
      const snapshot = await api("/api/memory");
      state.memory = snapshot;
      renderMemory(snapshot);
    } catch (error) {
      $("memory-entries").innerHTML = `<div class="empty">记忆暂不可用�?{esc(error.message)}</div>`;
    }
  };

  const kindLabel = (kind) =>
    ({ weakness: "薄弱�?, progress: "进度", preference: "偏好", misconception: "误区", goal: "目标" })[kind] || kind;

  const renderMemory = (snapshot) => {
    const list = $("memory-entries");
    if (!snapshot.available) {
      list.innerHTML = `<div class="empty">PowerContext 未就绪：${esc(snapshot.error || "未知原因")}</div>`;
      return;
    }
    const entries = (snapshot.entries || []).filter((entry) => entry.state === "active");
    if (!entries.length) {
      list.innerHTML = `<div class="empty">还没有记忆。答错一张卡，或保存一条讲解偏好�?/div>`;
    } else {
      const order = { weakness: 0, misconception: 1, preference: 2, progress: 3, goal: 4 };
      entries.sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
      list.innerHTML = entries
        .map(
          (entry, index) => `
        <div class="entry" data-index="${index}">
          <div class="head">
            <span class="kind ${esc(entry.kind)}">${esc(kindLabel(entry.kind))}</span>
            <span class="tiny">v${entry.version}</span>
            <span class="tiny">${esc((entry.citation || {}).entry_id || "").slice(0, 18)}</span>
          </div>
          <div class="text">${esc(entry.text)}</div>
          <div class="meta">
            <span class="spacer"></span>
            <button class="ghost" data-action="revise" data-index="${index}">修正</button>
            <button class="ghost danger" data-action="retire" data-index="${index}">失效</button>
          </div>
        </div>`,
        )
        .join("");
      list.querySelectorAll("button[data-action]").forEach((button) => {
        const entry = entries[Number(button.dataset.index)];
        button.addEventListener("click", () => {
          if (button.dataset.action === "retire") retireEntry(entry);
          else reviseEntry(entry);
        });
      });
    }
    const preference = snapshot.preference || "";
    if (preference && !$("preference-input").value) {
      $("preference-input").value = preference.replace(/^讲解偏好[:：]\s*/, "");
    }
  };

  const savePreference = async () => {
    const value = $("preference-input").value.trim();
    if (!value) {
      toast("先写一条偏好，例如「先讲例子再讲定义�?);
      return;
    }
    try {
      const result = await api("/api/memory/preference", { method: "POST", body: JSON.stringify({ value }) });
      renderMemory(result.memory);
      await refreshEvidence();
      toast("已写�?PowerContext 偏好记忆");
    } catch (error) {
      toast(`保存失败�?{error.message}`, true);
    }
  };

  const reviseEntry = async (entry) => {
    const next = window.prompt("修正这条记忆（写清新的结论）", entry.text);
    if (!next || next.trim() === entry.text.trim()) return;
    try {
      const result = await api("/api/memory/revise", {
        method: "POST",
        body: JSON.stringify({ citation: entry.citation, kind: entry.kind, text: next.trim() }),
      });
      renderMemory(result.memory);
      await refreshEvidence();
      toast("记忆已修正（旧版本保留在历史里）");
    } catch (error) {
      toast(`修正失败�?{error.message}`, true);
    }
  };

  const retireEntry = async (entry) => {
    if (!window.confirm(`把这条记忆标记为失效？\n\n${entry.text}`)) return;
    try {
      const result = await api("/api/memory/retire", {
        method: "POST",
        body: JSON.stringify({ citation: entry.citation, reason: "学习者在记忆面板中标记失�? }),
      });
      renderMemory(result.memory);
      await refreshEvidence();
      toast("记忆已失效（历史仍可审计�?);
    } catch (error) {
      toast(`失效失败�?{error.message}`, true);
    }
  };

  // --------------------------------------------------------------- evidence
  const refreshEvidence = async () => {
    try {
      const payload = await api("/api/evidence?limit=24");
      renderTrace(payload);
    } catch (error) {
      $("trace-list").innerHTML = `<div class="empty">执行记录暂不可用�?{esc(error.message)}</div>`;
    }
  };

  const renderTrace = (payload) => {
    const list = $("trace-list");
    if (state.evidenceTab === "store") {
      const rows = payload.store || [];
      if (!rows.length) {
        list.innerHTML = `<div class="empty">还没�?seekdb 执行记录�?/div>`;
        return;
      }
      list.innerHTML = rows
        .map(
          (row) => `
        <div class="row">
          <div class="label">
            <span class="channel ${esc(row.channel)}">${esc(row.channel)}</span>
            <span>${row.rows} �?· ${row.duration_ms} ms</span>
            ${row.note ? `<span>${esc(row.note)}</span>` : ""}
          </div>
          <code>${esc(row.sql)}</code>
          <div class="tiny">参数 ${esc(JSON.stringify(row.params))}</div>
        </div>`,
        )
        .join("");
    } else {
      const rows = payload.memory || [];
      if (!rows.length) {
        list.innerHTML = `<div class="empty">还没�?PowerContext 读写记录�?/div>`;
        return;
      }
      list.innerHTML = rows
        .map(
          (row) => `
        <div class="row">
          <div class="label">
            <span class="channel memory">${esc(row.method)}</span>
            <span>${esc(row.path)}</span>
            <span>${esc(row.status)}</span>
            <span>${row.duration_ms} ms</span>
          </div>
          <code>${esc(JSON.stringify(row.request))}</code>
          <div class="tiny">响应 ${esc(JSON.stringify(row.response))}</div>
        </div>`,
        )
        .join("");
    }
  };

  const runSearch = async () => {
    const query = $("search-input").value.trim();
    if (!query) return;
    try {
      const payload = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({ query, course: $("session-course").value || null, top_k: 5 }),
      });
      const box = $("search-results");
      if (!payload.hits.length) {
        box.innerHTML = `<div class="empty">没有命中。换个关键词试试�?/div>`;
      } else {
        box.innerHTML = payload.hits
          .map(
            (hit) => `
          <div class="item">
            <b>${esc(hit.material.title)}</b> <span class="tiny">${esc(hit.material.course)}</span>
            <div class="meta">
              融合�?${hit.fused}
              ${hit.vector ? ` · 向量�?${hit.vector.rank}（距�?${hit.vector.distance}）` : ""}
              ${hit.lexical ? ` · 全文�?${hit.lexical.rank}（得�?${hit.lexical.score}）` : ""}
            </div>
            <div>${esc(hit.material.body)}</div>
          </div>`,
          )
          .join("");
      }
      await refreshEvidence();
    } catch (error) {
      toast(`检索失败：${error.message}`, true);
    }
  };

  // ------------------------------------------------------------------- boot
  const boot = async () => {
    try {
      state.status = await api("/api/status");
      renderChips();
      renderStats(state.status.stats);
      await loadMaterials();
      await Promise.all([refreshMemory(), refreshEvidence()]);
      if (!state.materials.length) await seed();
      $("search-box").classList.remove("hidden");
      if (state.status.memory && !state.status.memory.available) {
        toast("PowerContext 未启动，记忆功能暂不可用；检索仍可用�?, true);
      }
    } catch (error) {
      toast(`无法连接服务�?{error.message}`, true);
    }
  };

  // wire up
  $("btn-import").addEventListener("click", importMaterial);
  $("upload-file").addEventListener("change", uploadMaterial);
  $("btn-seed").addEventListener("click", seed);
  $("btn-session").addEventListener("click", startSession);
  $("btn-memory-refresh").addEventListener("click", refreshMemory);
  $("btn-preference").addEventListener("click", savePreference);
  $("btn-search").addEventListener("click", runSearch);
  $("search-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });
  $("evidence-tabs").querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.evidenceTab = tab.dataset.tab;
      $("evidence-tabs").querySelectorAll(".tab").forEach((node) => node.classList.toggle("active", node === tab));
      refreshEvidence();
    });
  });

  boot();
})();
