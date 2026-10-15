"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const number = (value) => new Intl.NumberFormat("ru-RU").format(Number(value) || 0);
  const remoteLabels = { remote: "Удалённо", hybrid: "Гибрид", onsite: "Офис", unknown: "Формат не указан" };
  const sourceLabels = { greenhouse: "Greenhouse", lever: "Lever" };
  const eligibilityLabels = { allowed: "Соответствует правилам", excluded: "География не подходит", needs_review: "Нужна проверка" };
  const statusLabels = { active: "Активна", inactive: "Неактивна", unknown: "Статус неизвестен" };
  const views = {
    jobs: ["Вакансии", "Найдите свою следующую роль", "Все возможности в одном месте. Сфокусируйтесь на подходящих.", "ВАШ СЛЕДУЮЩИЙ ШАГ"],
    sources: ["Источники", "Все источники — под контролем", "Подключайте компании и собирайте вакансии с их карьерных страниц.", "НАСТРОЙКА СБОРА"],
    profiles: ["Профили поиска", "Настройте поиск под себя", "Нужные навыки, подходящий формат и понятные ограничения.", "ВАШИ ПРЕДПОЧТЕНИЯ"],
    activity: ["История сбора", "Держите руку на пульсе", "Следите за обновлениями источников и результатами каждого сбора.", "КОНТРОЛЬ ОБНОВЛЕНИЙ"]
  };
  const state = { stats: null, sources: null, profiles: null, sourceDraft: null, profileDraft: null, selectedProfile: 0, page: 1, pages: 1, total: 0, jobs: [], collection: null, view: "jobs", jobRequest: 0, detailRequest: 0, jobsAbort: null, pollTimer: null, pollFailures: 0, toastTimer: null, confirmAction: null, initialized: false };
  const filters = { query: "query", profile: "filter-profile", source: "filter-source", remote_type: "filter-remote", eligibility: "filter-eligibility", company: "filter-company", location: "filter-location", seniority: "filter-seniority", language: "filter-language", status: "filter-status" };

  function node(tag, props = {}, children = []) {
    const item = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (key === "text") item.textContent = value;
      else if (key === "class") item.className = value;
      else if (key === "onClick") item.addEventListener("click", value);
      else if (key === "onInput") item.addEventListener("input", value);
      else if (key === "onChange") item.addEventListener("change", value);
      else if (key === "checked") item.checked = value;
      else if (key === "value") item.value = value;
      else if (key === "disabled") item.disabled = value;
      else item.setAttribute(key, value);
    });
    (Array.isArray(children) ? children : [children]).forEach((child) => {
      if (child !== undefined && child !== null) item.append(child);
    });
    return item;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function formatDate(value, options = {}) {
    if (!value) return "Не указана";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Не указана" : new Intl.DateTimeFormat("ru-RU", options).format(date);
  }

  function salary(job) {
    if (job.salary_min == null && job.salary_max == null) return null;
    const minimum = job.salary_min == null ? null : number(job.salary_min);
    const maximum = job.salary_max == null ? null : number(job.salary_max);
    const amount = minimum && maximum ? `${minimum} — ${maximum}` : minimum ? `от ${minimum}` : `до ${maximum}`;
    const intervals = { year: "год", yearly: "год", month: "месяц", monthly: "месяц", week: "неделю", weekly: "неделю", hour: "час", hourly: "час" };
    return `${amount}${job.salary_currency ? ` ${job.salary_currency}` : ""}${job.salary_interval ? ` / ${intervals[job.salary_interval] || job.salary_interval}` : ""}`;
  }

  function errorMessage(detail) {
    if (Array.isArray(detail)) return detail.map((item) => `${(item.loc || []).filter((part) => part !== "body").join(" → ")}: ${item.msg || "Некорректное значение"}`).join("; ");
    const known = {
      "Collection is running. Wait before collecting or editing settings.": "Сбор уже выполняется. Дождитесь завершения перед запуском или сохранением настроек.",
      "Cannot access configuration or database. Check server settings.": "Не удалось открыть конфигурацию или базу. Проверьте настройки и сообщения в окне сервера.",
      "Vacancy not found": "Вакансия не найдена. Обновите список.",
      "Use the local JobRadar interface for changes.": "Запрос отклонён. Откройте приложение с локального адреса и обновите страницу."
    };
    return typeof detail === "string" ? known[detail] || detail : "Не удалось выполнить запрос.";
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { ...options, headers: { ...(options.body ? { "Content-Type": "application/json", "X-JobRadar-Request": "1" } : {}), ...options.headers } });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new Error("Нет соединения с JobRadar. Убедитесь, что сервер запущен, и обновите страницу.");
    }
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).detail; } catch { detail = `Ошибка сервера (${response.status}).`; }
      throw new Error(errorMessage(detail));
    }
    return response.json();
  }

  function notify(message, isError = false) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").className = `toast${isError ? " error" : ""}`;
    state.toastTimer = setTimeout(() => $("toast").classList.add("hidden"), isError ? 9000 : 4500);
  }

  function setError(error) {
    $("global-error").textContent = error ? error.message || String(error) : "";
    $("global-error").classList.toggle("hidden", !error);
  }

  function filterParams() {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([name, id]) => {
      const value = $(id).value.trim();
      if (value) params.set(name, value);
    });
    return params;
  }

  function updateFilterSummary() {
    const params = filterParams();
    const profile = params.get("profile");
    const selected = state.profiles?.profiles.find((item) => item.name === profile);
    const target = selected?.geography?.target_location || "Costa Rica";
    $("filter-summary").textContent = profile ? `Профиль: ${profile}${params.get("query") ? ` · «${params.get("query")}»` : ""}` : params.get("query") ? `Поиск: «${params.get("query")}»` : params.get("status") === "active" ? "Все активные вакансии" : "Вакансии с выбранными фильтрами";
    const activeAdvanced = ["company", "location", "seniority", "language"].filter((name) => params.has(name)).length + (params.get("status") !== "active" ? 1 : 0);
    $("advanced-count").textContent = activeAdvanced ? `(${activeAdvanced})` : "";
    $("profile-note").replaceChildren(icon("info"), node("span", { text: `Удалённая работа не всегда доступна из ${target === "Costa Rica" ? "Коста-Рики" : target}. Проверяйте географические ограничения.${selected ? " Доступность оценена по правилам выбранного профиля." : " Выберите профиль для проверки по своим правилам."}` }));
  }

  function emptyState(title, description, iconName = "search", retry = false) {
    const content = node("div", { class: "empty-state" }, [icon(iconName), node("h3", { text: title }), node("p", { text: description })]);
    if (retry) content.append(node("button", { class: "button secondary", text: "Повторить", onClick: () => loadJobs() }));
    return content;
  }

  function monogram(company) {
    const hash = [...(company || "?")].reduce((sum, char) => sum + char.charCodeAt(0), 0);
    const initials = (company || "?").trim().split(/\s+/).slice(0, 2).map((part) => Array.from(part)[0]).join("").toUpperCase();
    return node("span", { class: `company-monogram tone-${hash % 4}`, text: initials, "aria-hidden": "true" });
  }

  function eligibilityPill(eligibility) {
    const type = Object.hasOwn(eligibilityLabels, eligibility) ? eligibility : "needs_review";
    return node("span", { class: `pill ${type}`, title: eligibilityLabels[type] }, [node("span", { class: "status-dot" }), eligibilityLabels[type]]);
  }

  function jobRow(job) {
    const row = node("article", { class: "job-row" });
    const title = node("button", { class: "job-title", text: job.title, onClick: () => openDetails(job.id) });
    const company = node("div", { class: "job-company" }, [node("span", { text: job.company }), node("span", { class: "tiny-divider" }), node("span", { class: "source-label", text: sourceLabels[job.source] || job.source })]);
    row.append(node("div", { class: "job-main" }, [monogram(job.company), node("div", {}, [title, company])]));
    const displayLocation = ["onsite","hybrid"].includes(job.remote_type) && job.workplace_locations?.length ? job.workplace_locations.join("; ") : job.location;
    const location = node("div", { class: "job-location" }, [node("div", { class: "job-location-name" }, [icon("pin"), node("span", { text: displayLocation || "Локация не указана" })]), node("div", { class: "job-workplace", text: remoteLabels[job.remote_type] || remoteLabels.unknown })]);
    if (salary(job)) location.append(node("div", { class: "job-workplace", text: salary(job) }));
    row.append(location);
    row.append(node("div", { class: "job-eligibility" }, [eligibilityPill(job.match?.eligibility), node("span", { class: "job-date", text: `Впервые ${formatDate(job.first_seen_at, { day: "numeric", month: "short" })}`, title: "Дата первого обнаружения в JobRadar" })]));
    row.append(node("button", { class: "open-job", "aria-label": `Открыть вакансию: ${job.title}`, onClick: () => openDetails(job.id) }, icon("arrow")));
    return row;
  }

  async function loadJobs({ reset = false } = {}) {
    if (!state.initialized) return;
    if (reset) state.page = 1;
    const request = ++state.jobRequest;
    state.jobsAbort?.abort();
    state.jobsAbort = new AbortController();
    $("jobs-list").setAttribute("aria-busy", "true");
    $("previous-page").disabled = true;
    $("next-page").disabled = true;
    updateFilterSummary();
    const params = filterParams();
    params.set("page", state.page);
    params.set("page_size", $("page-size").value);
    try {
      const result = await api(`/api/jobs?${params}`, { signal: state.jobsAbort.signal });
      if (request !== state.jobRequest) return;
      state.pages = Math.max(1, result.pages);
      state.total = result.total;
      if (state.page > state.pages) { state.page = state.pages; return loadJobs(); }
      state.jobs = result.items;
      $("results-count").textContent = number(result.total);
      $("jobs-list").replaceChildren(...(result.items.length ? result.items.map(jobRow) : [emptyState("Подходящие вакансии пока не найдены", state.stats?.total ? "Попробуйте другие ключевые слова или сбросьте часть фильтров. Новые результаты появятся после следующего сбора." : "Добавьте компанию в источниках и нажмите «Собрать вакансии», чтобы начать.")]));
      $("page-indicator").textContent = `${state.page} / ${state.pages}`;
      const first = result.total ? (state.page - 1) * result.page_size + 1 : 0;
      const last = Math.min(state.page * result.page_size, result.total);
      $("page-summary").textContent = `${number(first)}–${number(last)} из ${number(result.total)} вакансий`;
      $("previous-page").disabled = state.page <= 1;
      $("next-page").disabled = state.page >= state.pages;
    } catch (error) {
      if (error.name === "AbortError" || request !== state.jobRequest) return;
      $("jobs-list").replaceChildren(emptyState("Не удалось загрузить вакансии", error.message, "info", true));
      $("results-count").textContent = "—";
      $("page-summary").textContent = "Данные недоступны";
    } finally {
      if (request === state.jobRequest) $("jobs-list").setAttribute("aria-busy", "false");
    }
  }

  function renderStats() {
    const stats = state.stats;
    if (!stats) return;
    $("stat-total").textContent = number(stats.total);
    $("stat-active").textContent = number(stats.by_status?.active);
    $("nav-count").textContent = number(stats.by_status?.active);
    const enabled = state.sources?.sources.filter((item) => item.enabled) || [];
    $("stat-sources").textContent = number(enabled.length);
    const platforms = [...new Set(enabled.map((item) => sourceLabels[item.source] || item.source))];
    $("stat-sources-note").textContent = platforms.length ? platforms.join(" + ") : "Добавьте компанию в источниках";
    const latest = stats.recent_runs?.[0];
    $("stat-last").textContent = latest ? formatDate(latest.finished_at, { day: "numeric", month: "short" }).replace(/\.$/, "") : "Ещё не было";
    $("stat-last-note").textContent = latest ? `${formatDate(latest.finished_at, { hour: "2-digit", minute: "2-digit" })} · ${latest.complete ? "Успешно" : "Есть ошибки"}` : "Запустите первый сбор";
    renderActivity();
  }

  async function refreshStats() {
    state.stats = await api("/api/stats");
    renderStats();
  }

  function renderCollection(collection) {
    const wasRunning = state.collection?.running;
    state.collection = collection;
    const running = collection.running;
    const banner = $("collection-banner");
    banner.className = `collection-banner ${collection.status}${collection.status === "idle" ? " hidden" : ""}`;
    $("collect-button").disabled = running;
    $("collect-button").classList.toggle("busy", running);
    $("collect-button").querySelector("span").textContent = running ? "Сбор выполняется…" : "Собрать вакансии";
    $("collect-source").disabled = running;
    document.querySelectorAll("#sources-form button[type=submit],#profiles-form button[type=submit]").forEach((button) => { button.disabled = running; button.title = running ? "Дождитесь завершения сбора" : ""; });
    const totals = (collection.results || []).reduce((sum, result) => ({ fetched: sum.fetched + (result.fetched || 0), inserted: sum.inserted + (result.inserted || 0), updated: sum.updated + (result.updated || 0) }), { fetched: 0, inserted: 0, updated: 0 });
    const titles = { running: "Радар ищет новые возможности", completed: "Сбор вакансий завершён", partial: "Сбор завершён с ошибками отдельных источников", failed: "Не удалось завершить сбор", idle: "Сбор ещё не запускался" };
    $("collection-title").textContent = titles[collection.status] || "Состояние сбора";
    $("collection-description").textContent = running ? `Обращаемся к публичным API${collection.source ? ` ${sourceLabels[collection.source] || collection.source}` : ""}. Можно продолжать работать с приложением.` : (collection.errors?.length ? `${number(totals.inserted)} новых, ${number(totals.updated)} обновлено. ${collection.errors[0]}` : collection.total_boards ? `${number(totals.fetched)} получено · ${number(totals.inserted)} новых · ${number(totals.updated)} обновлено` : "Нет включённых источников для сбора. Добавьте или включите компании в настройках.");
    if (!running && !collection.errors?.length && collection.total_boards && totals.fetched === 0) {
      $("collection-title").textContent = "API доступен, опубликованных вакансий нет";
      $("collection-description").textContent = `Пустые страницы: ${(collection.results || []).map((item) => item.company).join(", ")}. Проверьте идентификаторы компаний в разделе «Источники». Lever собирает вакансии каждой указанной компании, а не всей платформы.`;
    }
    $("collection-count").textContent = `${number(collection.completed_boards)} / ${number(collection.total_boards)}`;
    if (running) scheduleCollectionPoll();
    else {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
      if (wasRunning) {
        refreshStats().catch((error) => notify(error.message, true));
        loadJobs();
        notify(collection.status === "completed" ? "Сбор завершён. Вакансии обновлены." : "Сбор завершён с ошибками. Подробности — в истории сбора.", collection.status === "failed");
      }
    }
  }

  function scheduleCollectionPoll() {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async () => {
      try {
        const snapshot = await api("/api/collection");
        state.pollFailures = 0;
        renderCollection(snapshot);
      } catch (error) {
        state.pollFailures += 1;
        $("collection-description").textContent = "Потеряно соединение с сервером. Проверяем состояние сбора…";
        if (state.pollFailures < 5) scheduleCollectionPoll();
        else {
          $("collection-description").textContent = `${error.message} Нажмите «Обновить», чтобы проверить состояние.`;
          notify(error.message, true);
        }
      }
    }, 2000);
  }

  async function startCollection() {
    $("collect-button").disabled = true;
    try {
      const collection = await api("/api/collect", { method: "POST", body: JSON.stringify({ source: $("collect-source").value || null }) });
      state.pollFailures = 0;
      renderCollection(collection);
    } catch (error) {
      notify(error.message, true);
      try { renderCollection(await api("/api/collection")); } catch { $("collect-button").disabled = false; }
    }
  }

  async function refreshAll() {
    try {
      const [stats, collection] = await Promise.all([api("/api/stats"), api("/api/collection")]);
      state.stats = stats;
      state.pollFailures = 0;
      renderStats();
      renderCollection(collection);
      await loadJobs();
    } catch (error) { notify(error.message, true); }
  }

  function renderProfileFilter() {
    let selected = $("filter-profile").value;
    if (!state.profileFilterReady) {
      selected = state.profiles?.default_profile || "";
      state.profileFilterReady = true;
    }
    $("filter-profile").replaceChildren(node("option", { value: "", text: "Все вакансии" }), ...(state.profiles?.profiles || []).map((profile) => node("option", { value: profile.name, text: profile.name })));
    $("filter-profile").value = state.profiles?.profiles.some((profile) => profile.name === selected) ? selected : "";
    $("company-suggestions").replaceChildren(...[...new Set((state.sources?.sources || []).map((item) => item.name))].map((name) => node("option", { value: name })));
  }

  function setView(view, updateHash = true) {
    if (!Object.hasOwn(views, view)) view = "jobs";
    state.view = view;
    const [label, title, subtitle, eyebrow] = views[view];
    $("breadcrumb-view").textContent = label;
    $("page-title").textContent = title;
    $("page-subtitle").textContent = subtitle;
    $("page-eyebrow").textContent = eyebrow;
    document.title = `${label} · JobRadar`;
    document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.toggle("hidden", panel.id !== `view-${view}`));
    document.querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    if (updateHash && window.location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  }

  async function openDetails(id) {
    const request = ++state.detailRequest;
    const dialog = $("job-dialog");
    $("job-details").replaceChildren(emptyState("Загружаем вакансию", "Открываем полное описание.", "clock"));
    $("job-details").firstElementChild.querySelector("h3").id = "detail-title";
    if (!dialog.open) dialog.showModal();
    const params = new URLSearchParams();
    if ($("filter-profile").value) params.set("profile", $("filter-profile").value);
    if ($("query").value.trim()) params.set("query", $("query").value.trim());
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}?${params}`);
      if (request !== state.detailRequest || !dialog.open) return;
      renderDetails(job);
    } catch (error) {
      if (request !== state.detailRequest || !dialog.open) return;
      $("job-details").replaceChildren(emptyState("Не удалось открыть вакансию", error.message, "info"));
      $("job-details").firstElementChild.querySelector("h3").id = "detail-title";
    }
  }

  function renderDetails(job) {
    const container = $("job-details");
    const content = [node("div", { class: "detail-company" }, [monogram(job.company), node("div", {}, [node("strong", { text: job.company }), node("small", { text: sourceLabels[job.source] || job.source })])]), node("h2", { id: "detail-title", text: job.title })];
    const badges = [eligibilityPill(job.match?.eligibility), node("span", { class: "pill unknown", text: remoteLabels[job.remote_type] || remoteLabels.unknown }), node("span", { class: "pill unknown", text: statusLabels[job.status] || job.status })];
    content.push(node("div", { class: "detail-badges" }, badges));
    const metadata = [["Локация", job.location || "Не указана"], ["Вознаграждение", salary(job) || "Не опубликовано"], ["Уровень", job.seniority || "Не указан"], ["Тип занятости", job.employment_type || "Не указан"], ["Язык описания", ({ en: "Английский", ru: "Русский", es: "Испанский" })[job.language] || job.language || "Не определён"], ["Географические ограничения", job.remote_restrictions || "Не указаны в структурированных данных"]];
    if (job.workplace_locations?.length) metadata.splice(1,0,["Указанные офисы и локации",job.workplace_locations.join("; ")]);
    content.push(node("dl", { class: "detail-summary" }, metadata.map(([label, value]) => node("div", {}, [node("dt", { text: label }), node("dd", { text: value })]))));
    if (job.match?.eligibility !== "allowed") content.push(node("p", { class: "detail-warning", text: job.match?.eligibility === "excluded" ? "Указанные условия не соответствуют географическим правилам. Перед откликом проверьте ограничения работодателя." : "Доступность из вашей страны не подтверждена. Слово «remote» само по себе не означает возможность работать из любой точки мира." }));
    const reasons = [...(job.match?.reasons || []), ...(job.match?.eligibility_reasons || [])];
    if (reasons.length || job.match?.matched_keywords?.length) {
      const section = node("section", { class: "detail-section" }, node("h3", { text: "Почему показана эта вакансия" }));
      if (job.match?.matched_keywords?.length) section.append(node("p", { class: "detail-description", text: `Найдены ключевые слова: ${job.match.matched_keywords.join(", ")}` }));
      if (reasons.length) section.append(node("ul", {}, [...new Set(reasons)].map((reason) => node("li", { text: reason }))));
      content.push(section);
    }
    content.push(node("section", { class: "detail-section" }, [node("h3", { text: "Полное описание" }), node("div", { class: "detail-description", text: job.description || "Описание у источника отсутствует." })]));
    const dates = [["Впервые обнаружена", job.first_seen_at], ["Последний сбор", job.last_seen_at]];
    if (job.published_at) dates.push(["Опубликована", job.published_at]);
    if (job.source_updated_at) dates.push(["Обновлена у источника", job.source_updated_at]);
    content.push(node("div", { class: "detail-dates" }, dates.map(([label, value]) => node("span", { text: `${label}: ${formatDate(value, { day: "numeric", month: "short", year: "numeric" })}` }))));
    let url;
    try { const candidate = new URL(job.url); if (["https:", "http:"].includes(candidate.protocol) && !candidate.username && !candidate.password) url = candidate.href; } catch { /* Invalid source URL remains inert. */ }
    if (url) content.push(node("a", { class: "button primary detail-original", href: url, target: "_blank", rel: "noopener noreferrer" }, ["Открыть у работодателя", icon("external")]));
    else content.push(node("p", { class: "detail-warning", text: "Источник не предоставил корректную ссылку на вакансию." }));
    content.push(node("p", { class: "detail-raw", text: "Описание сохранено из публичного API работодателя. Дата первого обнаружения не является датой публикации." }));
    container.replaceChildren(...content);
    $("job-dialog").scrollTop = 0;
  }

  function confirmRemoval(text, action) {
    state.confirmAction = action;
    $("confirm-text").textContent = text;
    $("confirm-dialog").showModal();
    $("cancel-confirm").focus();
  }

  function selectControl(id, value, options, onChange) {
    const input = node("select", { id, onChange }, options.map(([key, label]) => node("option", { value: key, text: label })));
    input.value = value;
    return input;
  }

  function field(label, input, hint) {
    const content = node("div", { class: "field" }, [node("label", { for: input.id, text: label }), input]);
    if (hint) {
      const hintId = `${input.id}-hint`;
      input.setAttribute("aria-describedby", hintId);
      content.append(node("span", { class: "field-hint", id: hintId, text: hint }));
    }
    return content;
  }

  function renderSources() {
    const sources = state.sourceDraft?.sources || [];
    if (!sources.length) { $("sources-list").replaceChildren(node("p", { class: "empty-settings", text: "Источников пока нет. Добавьте первую компанию." })); return; }
    $("sources-list").replaceChildren(...sources.map((source, index) => {
      const heading = node("strong", { text: source.name || `Компания ${index + 1}` });
      const enabled = node("input", { type: "checkbox", checked: source.enabled, "aria-label": `Включить сбор компании ${source.name || index + 1}`, onChange: (event) => { source.enabled = event.target.checked; } });
      const remove = node("button", { type: "button", class: "remove-button", text: "Удалить", onClick: () => confirmRemoval(`Убрать «${source.name || "Новую компанию"}» из конфигурации? Уже собранные вакансии сохранятся. Изменение применится после сохранения.`, () => { state.sourceDraft.sources.splice(index, 1); renderSources(); }) });
      const card = node("div", { class: "source-card" }, node("div", { class: "source-card-heading" }, [heading, node("div", { class: "source-card-actions" }, [node("label", { class: "checkbox-label" }, [enabled, "Собирать"]), remove])]));
      const nameInput = node("input", { id: `source-name-${index}`, value: source.name, required: "", maxlength: "200", placeholder: "Название компании", onInput: (event) => { source.name = event.target.value; heading.textContent = source.name || `Компания ${index + 1}`; enabled.setAttribute("aria-label", `Включить сбор компании ${source.name || index + 1}`); } });
      const region = selectControl(`source-region-${index}`, source.region, [["global", "Global"], ["eu", "EU"]], (event) => { source.region = event.target.value; });
      region.disabled = source.source === "greenhouse";
      const platform = selectControl(`source-platform-${index}`, source.source, [["greenhouse", "Greenhouse"], ["lever", "Lever"]], (event) => { source.source = event.target.value; region.disabled = source.source === "greenhouse"; if (region.disabled) { source.region = "global"; region.value = "global"; } });
      const identifier = node("input", { id: `source-identifier-${index}`, value: source.identifier, required: "", pattern: "[A-Za-z0-9_-]+", placeholder: "board_token или site", title: "Идентификатор без адреса сайта: буквы, цифры, дефис и подчёркивание", onInput: (event) => { source.identifier = event.target.value; } });
      card.append(node("div", { class: "source-form-grid" }, [field("Компания", nameInput), field("Платформа", platform), field("Идентификатор страницы", identifier), field("Регион API", region)]));
      const board = `${source.source}:${source.region}:${source.identifier}`;
      const last = state.stats?.recent_runs?.find((run) => run.board === board);
      if (last) card.append(node("p", {class:"field-hint", text: last.complete ? (last.fetched ? `Последний сбор: ${number(last.fetched)} опубликованных вакансий.` : "API ответил успешно, но эта компания не опубликовала вакансии. Это пустая страница, а не ошибка парсера.") : "Последний сбор завершился с ошибкой. Подробности — в истории сбора."}));
      return card;
    }));
  }

  function lockEditor(view) {
    const controls = [...$(`view-${view}`).querySelectorAll("input,textarea,select,button")];
    const previous = controls.map((control) => [control, control.disabled]);
    controls.forEach((control) => { control.disabled = true; });
    return () => previous.forEach(([control, disabled]) => { if (control.isConnected) control.disabled = disabled; });
  }

  async function saveSources(event) {
    event.preventDefault();
    const button = event.submitter || $("sources-form").querySelector("button[type=submit]");
    const unlock = lockEditor("sources");
    try {
      const config = clone(state.sourceDraft);
      config.sources.forEach((source) => { source.name = source.name.trim(); source.identifier = source.identifier.trim(); });
      state.sources = await api("/api/sources", { method: "PUT", body: JSON.stringify(config) });
      state.sourceDraft = clone(state.sources);
      renderSources();
      renderProfileFilter();
      renderStats();
      $("sources-save-note").textContent = `Сохранено в ${formatDate(new Date().toISOString(), { hour: "2-digit", minute: "2-digit" })}.`;
      notify("Источники сохранены. Настройки вступят в силу при следующем сборе.");
    } catch (error) { notify(error.message, true); }
    finally { unlock(); button.disabled = Boolean(state.collection?.running); }
  }

  function splitLines(value) {
    return [...new Set(value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean))];
  }

  function listEditor(profile, key, label, hint, prefix = "profile") {
    return field(label, node("textarea", { id: `${prefix}-${key}`, rows: "4", value: (profile[key] || []).join("\n"), spellcheck: "false", placeholder: "По одному значению на строку", onInput: (event) => { profile[key] = splitLines(event.target.value); } }), hint);
  }

  function renderProfileTabs() {
    $("profile-tabs").replaceChildren(...(state.profileDraft?.profiles || []).map((profile, index) => node("button", { type: "button", class: `profile-tab${index === state.selectedProfile ? " active" : ""}`, "aria-pressed": String(index === state.selectedProfile), text: profile.name || `Профиль ${index + 1}`, onClick: () => { state.selectedProfile = index; renderProfiles(); } })));
  }

  function renderProfiles() {
    const profiles = state.profileDraft?.profiles || [];
    state.selectedProfile = Math.max(0, Math.min(state.selectedProfile, profiles.length - 1));
    renderProfileTabs();
    const profile = profiles[state.selectedProfile];
    if (!profile) { $("profile-editor").replaceChildren(node("p", { class: "empty-settings", text: "Создайте профиль, чтобы искать вакансии по своим правилам." })); return; }
    const name = node("input", { id: "profile-name", value: profile.name, required: "", maxlength: "200", placeholder: "Например, Network Security", onInput: (event) => { if (state.profileDraft.default_profile === profile.name) state.profileDraft.default_profile = event.target.value; profile.name = event.target.value; const current = $("profile-tabs").children[state.selectedProfile]; if (current) current.textContent = profile.name || `Профиль ${state.selectedProfile + 1}`; } });
    const remove = node("button", { type: "button", class: "remove-button", text: "Удалить профиль", onClick: () => confirmRemoval(`Удалить профиль «${profile.name || "Новый профиль"}»? Изменение применится после сохранения.`, () => { profiles.splice(state.selectedProfile, 1); renderProfiles(); }) });
    const content = [node("div", { class: "profile-editor-top" }, [field("Название профиля", name), remove]), node("div", { class: "settings-grid" }, [listEditor(profile, "keywords", "Ключевые слова", "Ищем в названии и описании. Достаточно одного совпадения."), listEditor(profile, "exclude_keywords", "Исключающие слова", "При совпадении вакансия не попадёт в результаты."), listEditor(profile, "title_keywords", "Слова в названии", "Дополнительные положительные совпадения только в должности."), listEditor(profile, "description_keywords", "Слова в описании", "Дополнительные положительные совпадения только в описании.")]), node("h3", { class: "settings-divider", text: "Дополнительные условия" }), node("div", { class: "settings-grid" }, [listEditor(profile, "companies", "Компании", "Оставьте пустым, чтобы рассматривать все компании."), listEditor(profile, "locations", "Локации", "Значения для поиска в исходной локации вакансии."), listEditor(profile, "seniorities", "Уровни должности", "Например: senior, junior, lead. По одному на строку."), listEditor(profile, "languages", "Языки описания", "Коды языков: en, ru, es. По одному на строку.")])];
    const workplace = node("div", { class: "field" }, node("span", { class: "field-hint", text: "Формат работы · оставьте все варианты выключенными для поиска любого формата." }));
    ["remote", "hybrid", "onsite", "unknown"].forEach((type) => workplace.append(node("label", { class: "checkbox-label" }, [node("input", { type: "checkbox", checked: (profile.remote_types || []).includes(type), onChange: (event) => { profile.remote_types = event.target.checked ? [...new Set([...(profile.remote_types || []), type])] : (profile.remote_types || []).filter((value) => value !== type); } }), remoteLabels[type]])));
    content.push(node("div", { class: "settings-divider", text: "Формат и неопределённые условия" }), workplace);
    content.push(node("label", { class: "checkbox-label profile-unknown" }, [node("input", { type: "checkbox", checked: profile.include_unknown, onChange: (event) => { profile.include_unknown = event.target.checked; } }), "Показывать вакансии с неопределёнными условиями для ручной проверки"]));
    content.push(listEditor(profile, "exclude_title_keywords", "Исключения в названии должности", "Например: sales, presales, software. Упоминание продаж в описании технической роли не исключает вакансию."));
    content.push(node("label", { class: "checkbox-label" }, [node("input", {type:"checkbox", checked: state.profileDraft.default_profile === profile.name, onChange:(event) => {state.profileDraft.default_profile = event.target.checked ? profile.name : null;}}), "Открывать этот профиль при запуске панели"]));
    const geography = profile.geography || (profile.geography = defaultGeography());
    const geoDetails = node("details", { class: "profile-geography" }, [node("summary", { text: "Правила географии и доступности" }), node("p", { text: "Соответствие правилам означает совпадение с указанными фразами. Неопределённые условия сохраняются для проверки. Обязательные ограничения вакансии нужно сверить с работодателем." }), field("Целевая страна или локация", node("input", { id: "geo-target_location", value: geography.target_location, placeholder: "Costa Rica", onInput: (event) => { geography.target_location = event.target.value; } }))]);
    geoDetails.append(node("div", { class: "settings-grid" }, [listEditor(geography, "allowed_location_terms", "Подходящие локации", "Например: Costa Rica, Коста-Рика.", "geo"), listEditor(geography, "worldwide_terms", "Фразы о работе из любой страны", "Например: worldwide, work from anywhere.", "geo"), listEditor(geography, "allowed_remote_region_terms", "Допустимые удалённые регионы", "Например: LATAM, Latin America. Добавляйте только подходящие регионы.", "geo"), listEditor(geography, "blocked_location_terms", "Неподходящие локации", "Явные территориальные ограничения, которые нужно исключать.", "geo"), listEditor(geography, "hard_exclusion_phrases", "Обязательные исключающие условия", "Фразы об ограничениях, например гражданстве или разрешении на работу.", "geo")]));
    content.push(geoDetails);
    $("profile-editor").replaceChildren(...content);
  }

  function defaultGeography() {
    return { target_location: "Costa Rica", allowed_location_terms: ["Costa Rica", "Коста-Рика"], worldwide_terms: ["worldwide", "work from anywhere"], allowed_remote_region_terms: [], blocked_location_terms: [], hard_exclusion_phrases: [] };
  }

  function newProfile() {
    const existing = new Set(state.profileDraft.profiles.map((item) => item.name));
    let name = "Новый профиль";
    let index = 2;
    while (existing.has(name)) name = `Новый профиль ${index++}`;
    state.profileDraft.profiles.push({ name, keywords: [], title_keywords: [], description_keywords: [], exclude_keywords: [], companies: [], locations: [], remote_types: [], seniorities: [], languages: [], include_unknown: true, geography: defaultGeography() });
    state.selectedProfile = state.profileDraft.profiles.length - 1;
    renderProfiles();
    $("profile-name").focus();
    $("profile-name").select();
  }

  async function saveProfiles(event) {
    event.preventDefault();
    const button = event.submitter || $("profiles-form").querySelector("button[type=submit]");
    const unlock = lockEditor("profiles");
    try {
      const config = clone(state.profileDraft);
      if (config.profiles.some((profile) => !profile.name.trim())) throw new Error("Укажите название каждого профиля.");
      config.profiles.forEach((profile) => { profile.name = profile.name.trim(); });
      if (!config.profiles.some((profile) => profile.name === config.default_profile)) config.default_profile = null;
      state.profiles = await api("/api/profiles", { method: "PUT", body: JSON.stringify(config) });
      state.profileDraft = clone(state.profiles);
      renderProfiles();
      renderProfileFilter();
      loadJobs({ reset: true });
      $("profiles-save-note").textContent = `Сохранено в ${formatDate(new Date().toISOString(), { hour: "2-digit", minute: "2-digit" })}.`;
      notify("Профили сохранены. Правила поиска обновлены.");
    } catch (error) { notify(error.message, true); }
    finally { unlock(); button.disabled = Boolean(state.collection?.running); }
  }

  function renderActivity() {
    const runs = state.stats?.recent_runs || [];
    if (!runs.length) { $("activity-list").replaceChildren(emptyState("История пока пуста", "После первого сбора здесь появятся результаты для каждой компании.", "clock")); return; }
    $("activity-list").replaceChildren(...runs.map((run) => {
      const pill = node("span", { class: `pill ${run.complete ? "allowed" : "excluded"}`, text: run.complete ? "Успешно" : "Есть ошибки" });
      const title = node("div", { class: "activity-heading" }, [node("strong", { text: run.company }), pill, node("time", { datetime: run.finished_at, text: formatDate(run.finished_at, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) })]);
      const counts = node("div", { class: "activity-counts" }, [["Получено", run.fetched], ["Новых", run.inserted], ["Обновлено", run.updated], ["Неактивных", run.inactivated]].map(([label, value]) => node("span", {}, [node("strong", { text: number(value) }), label])));
      const card = node("article", { class: "activity-card" }, [title, node("div", { class: "activity-meta", text: `${sourceLabels[run.source] || run.source} · ${run.board}` }), counts]);
      if (run.errors?.length) card.append(node("details", { class: "activity-errors" }, [node("summary", { text: `Подробности ошибок (${run.errors.length})` }), ...run.errors.map((error) => node("p", { text: error }))]));
      else if (run.complete && !run.fetched) card.append(node("p", {class:"field-hint", text:"API вернул корректный пустой список. Проверьте, есть ли вакансии на странице этой компании и верен ли её идентификатор."}));
      return card;
    }));
  }

  async function exportJobs(format, button) {
    $("export-menu").removeAttribute("open");
    button.disabled = true;
    try {
      const params = filterParams();
      params.set("format", format);
      const response = await fetch(`/api/export?${params}`);
      if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch { detail = `Ошибка экспорта (${response.status}).`; } throw new Error(errorMessage(detail)); }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = node("a", { href: url, download: `jobradar-${new Date().toISOString().slice(0, 10)}.${format}` });
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      notify("Экспорт всех результатов с текущими фильтрами готов.");
    } catch (error) { notify(error.message || "Не удалось скачать файл. Проверьте соединение с сервером.", true); }
    finally { button.disabled = false; }
  }

  async function bootstrap() {
    try {
      const data = await api("/api/bootstrap");
      state.stats = data.stats;
      state.sources = data.sources;
      state.profiles = data.profiles;
      state.sourceDraft = clone(data.sources);
      state.profileDraft = clone(data.profiles);
      state.initialized = true;
      setError(null);
      renderStats();
      renderProfileFilter();
      renderSources();
      renderProfiles();
      renderCollection(data.collection);
      await loadJobs();
    } catch (error) {
      setError(error);
      $("jobs-list").setAttribute("aria-busy", "false");
      $("jobs-list").replaceChildren(emptyState("Не удалось открыть рабочее пространство", error.message, "info"));
      $("jobs-list").firstElementChild.append(node("button", { class: "button secondary", text: "Повторить подключение", onClick: bootstrap }));
      $("collect-button").disabled = true;
      $("add-source").disabled = true;
      $("add-profile").disabled = true;
    } finally {
      if (state.initialized) { $("add-source").disabled = false; $("add-profile").disabled = false; }
    }
  }

  let searchTimer;
  $("search-form").addEventListener("submit", (event) => { event.preventDefault(); clearTimeout(searchTimer); loadJobs({ reset: true }); });
  $("query").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadJobs({ reset: true }), 380); });
  Object.entries(filters).filter(([name]) => name !== "query").forEach(([, id]) => {
    const control = $(id);
    control.addEventListener(control.tagName === "INPUT" ? "input" : "change", () => { clearTimeout(searchTimer); if (control.tagName === "INPUT") searchTimer = setTimeout(() => loadJobs({ reset: true }), 380); else loadJobs({ reset: true }); });
  });
  $("reset-filters").addEventListener("click", () => { clearTimeout(searchTimer); Object.entries(filters).forEach(([key, id]) => { $(id).value = key === "status" ? "active" : ""; }); loadJobs({ reset: true }); });
  $("previous-page").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadJobs(); } });
  $("next-page").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadJobs(); } });
  $("page-size").addEventListener("change", () => loadJobs({ reset: true }));
  $("collect-button").addEventListener("click", startCollection);
  $("refresh-jobs").addEventListener("click", refreshAll);
  $("refresh-activity").addEventListener("click", refreshAll);
  $("close-dialog").addEventListener("click", () => $("job-dialog").close());
  $("job-dialog").addEventListener("close", () => { state.detailRequest += 1; });
  $("job-dialog").addEventListener("click", (event) => { if (event.target === $("job-dialog")) { const rect = $("job-dialog").getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $("job-dialog").close(); } });
  $("cancel-confirm").addEventListener("click", () => $("confirm-dialog").close());
  $("accept-confirm").addEventListener("click", () => { const action = state.confirmAction; state.confirmAction = null; $("confirm-dialog").close(); action?.(); });
  $("confirm-dialog").addEventListener("close", () => { state.confirmAction = null; });
  $("add-source").addEventListener("click", () => { if (!state.initialized) return; state.sourceDraft.sources.push({ name: "", source: "greenhouse", identifier: "", region: "global", enabled: true }); renderSources(); const input = $(`source-name-${state.sourceDraft.sources.length - 1}`); input.focus(); input.scrollIntoView({ block: "center", behavior: "smooth" }); });
  $("sources-form").addEventListener("submit", saveSources);
  $("profiles-form").addEventListener("submit", saveProfiles);
  $("add-profile").addEventListener("click", () => { if (state.initialized) newProfile(); });
  document.querySelectorAll("[data-export]").forEach((button) => button.addEventListener("click", () => exportJobs(button.dataset.export, button)));
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1), false));
  document.addEventListener("click", (event) => { if (!$("export-menu").contains(event.target)) $("export-menu").removeAttribute("open"); });
  document.addEventListener("keydown", (event) => { if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName) && !$("job-dialog").open && !$("confirm-dialog").open) { event.preventDefault(); setView("jobs"); $("query").focus(); } if (event.key === "Escape") $("export-menu").removeAttribute("open"); });
  setView(window.location.hash.slice(1) || "jobs", false);
  bootstrap();
})();
