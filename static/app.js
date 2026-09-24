const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let state = { repositories: [], workspaces: [], roots: [], settings: {} };
let csrf = "";
let activeFilter = "all";
let activeWorkspace = "all";
let pendingRemove = null;
let onboardingOpened = false;
const expandedRepos = new Set();
const selectedWorktrees = new Map();

const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const fmtAge = ts => { if (!ts) return "未知"; const d = Math.floor((Date.now()/1000-ts)/86400); return d < 1 ? "今天" : d < 30 ? `${d} 天前` : d < 365 ? `${Math.floor(d/30)} 个月前` : `${Math.floor(d/365)} 年前`; };
const kindLabel = kind => ({project:"正式仓库", temporary:"临时仓库", aggregate:"根目录仓库"}[kind] || "仓库");

function toast(message, error=false) {
  const el = document.createElement("div");
  el.className = `toast ${error ? "error" : ""}`;
  el.textContent = message;
  $("#toast-area").append(el);
  setTimeout(() => el.remove(), error ? 7000 : 5500);
}

async function api(path, options={}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json", ...(csrf ? {"X-Harbor-Token":csrf} : {})}, ...options});
  const data = await response.json();
  if (!data.ok) throw new Error(data.error || "操作失败");
  return data;
}

async function refresh({quiet=false}={}) {
  if (!quiet) document.body.classList.add("loading");
  try { const data = await api("/api/state"); csrf = data.csrf; state = data.state; render(); maybeOpenOnboarding(); }
  catch (error) { toast(error.message, true); }
  finally { if (!quiet) document.body.classList.remove("loading"); }
}

function renderEnvironment(environment) {
  const items=environment?.items||[];
  const missingRequired=items.filter(item=>item.required&&!item.available).length;
  $("#environment-summary").className=`environment-summary ${missingRequired?"blocked":"ready"}`;
  $("#environment-summary").innerHTML=missingRequired?`<strong>基础环境尚未就绪</strong><span>请先安装必需工具，再重新检测。</span>`:`<strong>基础功能可以使用</strong><span>未安装的可选工具只影响对应入口。</span>`;
  $("#environment-list").innerHTML=items.map(item=>`<article class="environment-item ${item.available?"available":"missing"}"><span class="environment-icon">${item.available?"✓":"!"}</span><div><strong>${escapeHtml(item.label)}${item.required?'<em>必需</em>':'<em class="optional">可选</em>'}</strong><p>${escapeHtml(item.description)}</p></div><small>${escapeHtml(item.detail)}</small></article>`).join("");
  $("#complete-environment").disabled=missingRequired>0;
}

async function runEnvironmentCheck() {
  $("#environment-summary").className="environment-summary scanning";
  $("#environment-summary").innerHTML="<strong>正在检测本机环境…</strong><span>不会安装或修改任何软件。</span>";
  $("#environment-list").innerHTML="";
  try { const data=await api("/api/action",{method:"POST",body:JSON.stringify({action:"check_environment"})});renderEnvironment(data.environment); }
  catch(error){$("#environment-summary").className="environment-summary blocked";$("#environment-summary").textContent=error.message;}
}

function openEnvironmentDialog() {
  if(!$("#environment-dialog").open)$("#environment-dialog").showModal();
  runEnvironmentCheck();
}

function maybeOpenOnboarding() {
  if(onboardingOpened||Number(state.settings?.onboarding_version||0)>=1)return;
  onboardingOpened=true;
  openEnvironmentDialog();
}

function repoCounts(repo) {
  return {
    worktrees: repo.worktrees.length,
    dirty: repo.worktrees.filter(w => w.status?.dirty).length,
    activeDirty: repo.worktrees.filter(w => w.status?.dirty && !w.idle_dirty).length,
    idleDirty: repo.worktrees.filter(w => w.idle_dirty).length,
    cleanup: repo.worktrees.filter(isCleanupCandidate).length,
    deletable: repo.worktrees.filter(isDeletable).length,
    core: repo.worktrees.filter(w => w.branch_role === "main" || w.branch_role === "test").length,
  };
}

function isCleanupCandidate(w) {
  return !w.is_main && !w.status?.dirty && (w.prunable || w.status?.available === false || w.stale);
}

function isDeletable(w) {
  return true;
}

function counts(repositories=state.repositories) {
  const worktrees = repositories.flatMap(r => r.worktrees);
  return {
    repos: repositories.length,
    worktrees: worktrees.length,
    dirty: worktrees.filter(w=>w.status?.dirty).length,
    activeDirty: worktrees.filter(w=>w.status?.dirty && !w.idle_dirty).length,
    idleDirty: worktrees.filter(w=>w.idle_dirty).length,
    cleanup: worktrees.filter(isCleanupCandidate).length,
    deletable: worktrees.filter(isDeletable).length,
    core: worktrees.filter(w=>w.branch_role === "main" || w.branch_role === "test").length,
    locked: worktrees.filter(w=>w.locked).length,
  };
}

function render() {
  const c = counts();
  if(activeFilter==="cleanup"&&c.cleanup===0)activeFilter="all";
  $("#cleanup-button").disabled=c.cleanup===0;
  $("#cleanup-button").classList.toggle("active",activeFilter==="cleanup");
  $("#cleanup-button-label").textContent=activeFilter==="cleanup"?"显示全部":"建议";
  $("#cleanup-button-count").textContent=c.cleanup;
  $("#stats").innerHTML = [["仓库",c.repos,"个项目",""],["近期未提交",c.activeDirty,"删除会丢失本地改动","hot"],["全部可管理",c.deletable,"覆盖所有工作树","safe"],["建议清理",c.cleanup,"陈旧或失效","calm"]].map(([k,v,s,t])=>`<article class="stat ${t}"><span>${k}</span><strong>${v}</strong><small>${s}</small></article>`).join("");
  $("#scan-summary").textContent = `扫描 ${state.workspaces?.length || state.roots.length} 个工作区 · ${state.scan_ms || 0} 毫秒`;
  $("#roots").value=state.roots.join("\n"); $("#max-depth").value=state.settings.max_depth||5; $("#stale-days").value=state.settings.stale_days||30;
  renderWorkspaceNav();
  renderRepos();
  renderSelectionBar();
}

function renderWorkspaceNav() {
  $("#workspace-nav").innerHTML = (state.workspaces || []).map((workspace, index) => {
    const repos = state.repositories.filter(r => r.workspace_id === workspace.id);
    const c = counts(repos);
    return `<button class="workspace-nav-item ${activeWorkspace === workspace.id ? "active" : ""}" data-workspace="${workspace.id}">
      <span class="workspace-dot tone-${index % 4}">${escapeHtml(workspace.name.slice(0,1))}</span>
      <span class="workspace-nav-copy"><strong>${escapeHtml(workspace.name)}</strong><small>${c.activeDirty ? `${c.activeDirty} 个正在改 · ` : ""}${c.worktrees} 个工作树</small></span>
    </button>`;
  }).join("");
}

function matchesFilter(w) {
  if (activeFilter === "active_dirty") return Boolean(w.status?.dirty && !w.idle_dirty);
  if (activeFilter === "idle_dirty") return Boolean(w.idle_dirty);
  if (activeFilter === "deletable") return isDeletable(w);
  if (activeFilter === "cleanup") return isCleanupCandidate(w);
  if (activeFilter === "core") return w.branch_role === "main" || w.branch_role === "test";
  if (activeFilter === "locked") return Boolean(w.locked);
  return true;
}

function sortRepos(repos) {
  return [...repos].sort((a,b) => {
    const ac=repoCounts(a), bc=repoCounts(b);
    if (activeFilter === "deletable") return bc.deletable-ac.deletable || bc.cleanup-ac.cleanup || a.name.localeCompare(b.name);
    if (activeFilter === "cleanup") return bc.cleanup-ac.cleanup || a.name.localeCompare(b.name);
    const ar=ac.activeDirty ? 0 : ac.idleDirty ? 2 : 1;
    const br=bc.activeDirty ? 0 : bc.idleDirty ? 2 : 1;
    return ar-br || bc.activeDirty-ac.activeDirty || a.name.localeCompare(b.name);
  });
}

function worktreePriority(w) {
  if (w.branch_role === "main") return 0;
  if (w.branch_role === "test") return 1;
  if (w.status?.dirty && !w.idle_dirty) return 2;
  if (w.prunable || w.status?.available === false) return 3;
  if (!w.status?.dirty && !w.stale) return 4;
  if (!w.status?.dirty) return 5;
  return 6;
}

function matchesQuery(repo, w, query) {
  if (!query) return true;
  return `${repo.workspace_name} ${repo.name} ${repo.path} ${repo.remote} ${w.branch} ${w.base_branch?.name||""} ${w.path} ${w.commit?.subject}`.toLowerCase().includes(query);
}

const selectionKey = (repoId, path) => `${repoId}\u0000${path}`;
const canSelect = w => !w.is_main;

function visibleSelections() {
  const query = $("#search").value.trim().toLowerCase();
  return state.repositories
    .filter(repo => activeWorkspace === "all" || repo.workspace_id === activeWorkspace)
    .flatMap(repo => repo.worktrees.filter(w => canSelect(w) && matchesFilter(w) && matchesQuery(repo,w,query)).map(w => ({repo,w})));
}

function renderSelectionBar() {
  const visible = visibleSelections();
  const count = selectedWorktrees.size;
  const dirtyCount = [...selectedWorktrees.values()].filter(item=>item.dirty).length;
  $("#selection-count").textContent=count;
  $("#selection-detail").textContent=`个工作树已选择${dirtyCount ? ` · ${dirtyCount} 个有本地改动` : " · 均已提交"}`;
  $("#remove-selected").disabled=count===0;
  $("#remove-selected").textContent=count?`删除所选（${count}）`:"删除所选";
  const allVisible = visible.length > 0 && visible.every(({repo,w}) => selectedWorktrees.has(selectionKey(repo.id,w.path)));
  $("#select-visible").textContent=allVisible?"取消全选":"全选当前";
  $("#selection-bar").classList.toggle("has-selection",count>0);
}

function renderRepos() {
  const query = $("#search").value.trim().toLowerCase();
  const workspaces = (state.workspaces || []).filter(w => activeWorkspace === "all" || activeWorkspace === w.id);
  const blocks = [];
  for (const workspace of workspaces) {
    const workspaceRepos = state.repositories.filter(repo => repo.workspace_id === workspace.id);
    const visibleRepos = workspaceRepos.filter(repo => repo.worktrees.some(w => matchesFilter(w) && matchesQuery(repo,w,query)));
    if (!visibleRepos.length) continue;
    const regular = sortRepos(visibleRepos.filter(repo => repo.kind === "project"));
    const support = sortRepos(visibleRepos.filter(repo => repo.kind !== "project"));
    const c = counts(workspaceRepos);
    const index = (state.workspaces || []).findIndex(w => w.id === workspace.id);
    blocks.push(`<section class="workspace-block">
      <div class="workspace-head">
        <div class="workspace-identity"><span class="workspace-mark tone-${Math.max(index,0)%4}">${escapeHtml(workspace.name.slice(0,1))}</span><div><p class="workspace-kicker">工作区</p><h2>${escapeHtml(workspace.name)}</h2><p title="${escapeHtml(workspace.root)}">${escapeHtml(workspace.root)}</p></div></div>
        <div class="workspace-summary"><strong>${c.repos}</strong><span>仓库</span><strong class="active-count">${c.activeDirty}</strong><span>正在改</span><strong class="risk-count">${c.idleDirty}</strong><span>搁置</span></div>
      </div>
      ${regular.length ? `<div class="repo-section"><p class="repo-section-title">项目仓库 <span>${regular.length}</span></p><div class="repo-stack">${regular.map(repo => repoCard(repo,query)).join("")}</div></div>` : ""}
      ${support.length ? `<details class="support-repos"><summary>临时与根目录仓库 <span>${support.length}</span></summary><div class="repo-stack">${support.map(repo => repoCard(repo,query)).join("")}</div></details>` : ""}
    </section>`);
  }
  $("#repositories").innerHTML = blocks.join("");
  $("#empty-notice").classList.toggle("hidden", blocks.length > 0);
  $("#empty-notice").textContent = state.repositories.length ? "没有符合当前条件的项目。" : "尚未发现版本库，请在扫描设置中添加项目根目录。";
  renderSelectionBar();
}

function repoCard(repo, query) {
  const filtered = repo.worktrees.filter(w => matchesFilter(w) && matchesQuery(repo,w,query)).sort((a,b) => activeFilter === "deletable" ? Number(isCleanupCandidate(b))-Number(isCleanupCandidate(a)) || (b.age_days||0)-(a.age_days||0) : activeFilter === "cleanup" ? (b.age_days||0)-(a.age_days||0) : worktreePriority(a)-worktreePriority(b) || (b.status?.dirty_timestamp||b.commit?.timestamp||0)-(a.status?.dirty_timestamp||a.commit?.timestamp||0));
  const autoExpanded = Boolean(query);
  const expanded = autoExpanded || expandedRepos.has(repo.id);
  const c = repoCounts(repo);
  const deleteMode = activeFilter === "deletable" || activeFilter === "cleanup";
  const preview = deleteMode ? `${filtered.length} 个工作树 · ${filtered.filter(w=>w.status?.dirty).length} 个删除会丢失改动${filtered.filter(isCleanupCandidate).length ? ` · ${filtered.filter(isCleanupCandidate).length} 个建议优先` : ""}` : c.activeDirty ? `${c.activeDirty} 个近期未提交，建议先处理` : c.idleDirty ? `${c.idleDirty} 个未提交已长期搁置` : "当前没有本地未提交改动";
  const cardState = deleteMode ? "cleanup-mode" : c.activeDirty ? "has-active-dirty" : c.idleDirty ? "has-idle-dirty" : "is-clean";
  const checkout = repo.worktrees.find(w=>w.is_main);
  const checkoutName = checkout?.detached || checkout?.branch === "DETACHED" ? "游离提交" : checkout?.branch;
  const coreLine = checkoutName ? `<span class="core-line" title="仓库主目录当前检出的分支"><span class="current-chip ${checkout?.branch_role === "pre" ? "pre" : ""}"><b>占用</b>${escapeHtml(checkoutName)}</span></span>` : "";
  const editorOptions = (state.editors||[]).map(editor=>`<option value="${escapeHtml(editor.id)}" ${editor.id===repo.editor_id?"selected":""}>${escapeHtml(editor.label)}</option>`).join("");
  const editorChooser = expanded && editorOptions ? `<label class="repo-editor-choice"><span>默认编辑器</span><select data-editor-select data-repo="${repo.id}" aria-label="${escapeHtml(repo.name)} 默认编辑器">${editorOptions}</select></label>` : "";
  return `<article class="repo-card ${expanded ? "expanded" : ""} ${cardState}" data-repo-card="${repo.id}">
    <div class="repo-head">
      <button class="repo-toggle" data-toggle-repo="${repo.id}" aria-expanded="${expanded}">
        <span class="chevron">›</span>
        <span class="repo-icon">${escapeHtml(repo.name.slice(0,2).toUpperCase())}</span>
        <span class="repo-copy"><span class="repo-name-line"><strong>${escapeHtml(repo.name)}</strong><em class="kind ${repo.kind}">${kindLabel(repo.kind)}</em></span>${coreLine}<small title="${escapeHtml(repo.path)}">${escapeHtml(repo.relative_path || repo.path)}</small><span class="repo-preview">${escapeHtml(preview)}</span></span>
      </button>
      <div class="repo-health">${deleteMode ? `<strong class="safe-count">${filtered.length}</strong><span>全部列出</span>${filtered.some(w=>w.status?.dirty)?`<b class="active-dirty">⚠ ${filtered.filter(w=>w.status?.dirty).length} 个有改动</b>`:`<b class="healthy">✓ 全部已提交</b>`}` : `<strong>${c.worktrees}</strong><span>工作树</span>${c.activeDirty ? `<b class="active-dirty">● ${c.activeDirty} 正在修改</b>` : c.idleDirty ? `<b class="idle-dirty">● ${c.idleDirty} 搁置未提交</b>` : `<b class="healthy">✓ 全部已提交</b>`}`}</div>
      <div class="repo-actions">${editorChooser}</div>
    </div>
    ${expanded ? renderWorktreeGroups(repo,filtered) : ""}
  </article>`;
}

function actionsHeading(repo) {
  const kinds = new Set(repo.worktrees.map(w=>w.preview_kind).filter(Boolean));
  const previewLabel = kinds.has("wechat") && kinds.has("web") ? "运行预览" : kinds.has("wechat") ? "开发者工具" : kinds.has("web") ? "网页预览" : "打开";
  return `<span class="actions-heading"><b>编辑器</b><b>${previewLabel}</b><b>管理</b></span>`;
}

function renderWorktreeGroups(repo, worktrees) {
  if (activeFilter === "deletable") {
    const isMainDirectory = w => w.is_main;
    const groups = [
      ["core", "仓库主目录 · 固定在首位，不能删除", worktrees.filter(isMainDirectory)],
      ["cleanup", "建议优先清理 · 陈旧且干净", worktrees.filter(isCleanupCandidate)],
      ["safe", "已提交 · 可直接删除", worktrees.filter(w => !isMainDirectory(w) && !w.status?.dirty && !isCleanupCandidate(w))],
      ["active", "有本地改动 · 删除会丢失未提交内容", worktrees.filter(w => !isMainDirectory(w) && w.status?.dirty && !w.idle_dirty)],
      ["parked", "长期未提交 · 删除会丢失未提交内容", worktrees.filter(w => !isMainDirectory(w) && w.idle_dirty)],
    ].filter(([, , rows]) => rows.length);
    return `<div class="worktree-columns"><span>当前分支</span><span>来源分支</span><span>状态 / 最近提交</span>${actionsHeading(repo)}</div><div class="worktree-list">${groups.map(([tone,title,rows])=>`<div class="worktree-group ${tone}"><div class="worktree-group-title"><span>${title}</span><b>${rows.length}</b></div>${rows.map(w=>worktreeRow(repo,w)).join("")}</div>`).join("")}</div>`;
  }
  const isCore = w => w.branch_role === "main" || w.branch_role === "test";
  const isMainDirectory = w => w.is_main;
  const groups = [
    ["core", "仓库主目录 · 固定在首位", worktrees.filter(isMainDirectory)],
    ["core", "核心环境 · 主分支 / 测试分支", worktrees.filter(w => !isMainDirectory(w) && isCore(w))],
    ["active", "正在开发 · 本地未提交", worktrees.filter(w => !isMainDirectory(w) && !isCore(w) && w.status?.dirty && !w.idle_dirty)],
    ["normal", "已提交 / 正常", worktrees.filter(w => !isMainDirectory(w) && !isCore(w) && !w.status?.dirty && !isCleanupCandidate(w))],
    ["cleanup", "可清理候选 · 干净且陈旧", worktrees.filter(w => !isMainDirectory(w) && !isCore(w) && isCleanupCandidate(w))],
    ["parked", "搁置风险 · 长期未提交", worktrees.filter(w => !isMainDirectory(w) && !isCore(w) && w.idle_dirty)],
  ].filter(([, , rows]) => rows.length);
  return `<div class="worktree-columns"><span>当前分支</span><span>来源分支</span><span>状态 / 最近提交</span>${actionsHeading(repo)}</div><div class="worktree-list">${groups.map(([tone,title,rows])=>`<div class="worktree-group ${tone}"><div class="worktree-group-title"><span>${title}</span><b>${rows.length}</b></div>${rows.map(w=>worktreeRow(repo,w)).join("")}</div>`).join("")}</div>`;
}

function worktreeRow(repo,w) {
  const s=w.status||{}; const identityBadges=[]; const statusBadges=[];
  if(w.is_main) identityBadges.push('<span class="badge main">主目录</span>');
  if(w.branch_role === "main") identityBadges.push('<span class="badge role-main">主分支</span>');
  if(w.branch_role === "test") identityBadges.push('<span class="badge role-test">测试</span>');
  if(w.branch_role === "pre") identityBadges.push('<span class="badge role-pre">预发布</span>');
  if(w.detached || w.branch === "DETACHED") identityBadges.push('<span class="badge detached">游离提交</span>');
  const mergedPrefix = s.dirty ? "提交已进" : "已进";
  if(w.merged_into?.test && w.branch_role !== "test") statusBadges.push(`<span class="badge merged-test" title="当前提交已包含在本地 ${escapeHtml(repo.core_branches?.test || "测试分支")} 引用中；不包含未提交修改">✓ ${mergedPrefix}测试</span>`);
  if(w.merged_into?.main && w.branch_role !== "main") statusBadges.push(`<span class="badge merged-main" title="当前提交已包含在本地 ${escapeHtml(repo.core_branches?.main || "主分支")} 引用中；不包含未提交修改">✓ ${mergedPrefix}主线</span>`);
  if(s.dirty) statusBadges.push(`<span class="badge dirty">${s.changed+s.untracked} 项未提交</span>`);
  if(w.locked) statusBadges.push('<span class="badge locked">已锁定</span>');
  if(w.prunable||s.available===false) statusBadges.push('<span class="badge missing">目录缺失</span>');
  else if(w.stale) statusBadges.push('<span class="badge stale">提交较旧</span>');
  if(w.idle_dirty) statusBadges.push(`<span class="badge idle">搁置 ${s.dirty_age_days} 天</span>`);
  const activity = s.dirty ? `本地改动 · ${fmtAge(s.dirty_timestamp)}` : `${escapeHtml(w.commit?.short||w.head?.slice(0,8)||"—")} · ${fmtAge(w.commit?.timestamp)}`;
  const rowState = w.idle_dirty ? "dirty-idle" : s.dirty ? "dirty-active" : isCleanupCandidate(w) ? "cleanup" : "";
  const base = w.base_branch||{};
  const baseText = w.is_main ? "—" : base.name ? escapeHtml(base.name) : base.source === "core" ? "环境分支" : "无法确定";
  const baseHint = w.is_main ? "这是仓库主目录，不是额外创建的 worktree" : base.source === "recorded" ? "Git 保留了从该分支创建 worktree 的记录" : base.source === "inferred" ? "Git 没有保留创建记录，这是根据提交关系判断的可能来源" : base.source === "core" ? "该分支本身是主线或环境分支" : "Git 未保留该 worktree 的创建来源";
  const baseSourceLabel = w.is_main ? "仓库主目录" : base.source === "recorded" ? "Git 创建记录" : base.source === "inferred" ? "提交关系判断" : "";
  const removeData = `data-remove data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" data-branch="${escapeHtml(w.branch)}" data-dirty="${s.dirty ? "true" : "false"}" data-changes="${(s.changed||0)+(s.untracked||0)}" data-locked="${w.locked ? "true" : "false"}"`;
  const deleteControl = w.is_main ? '<span class="delete-blocked" title="仓库主目录承载版本库管理数据，不能作为普通工作树删除">仓库主目录</span>' : s.available===false ? `<button class="mini prune-direct" data-prune-direct data-repo="${repo.id}">清理记录</button>` : s.dirty ? `<button class="mini remove delete-force" ${removeData}>直接删除</button>` : `<button class="mini remove delete-direct" ${removeData}>删除</button>`;
  const testTitle = w.preview_environments?.test ? "在微信开发者工具打开测试环境产物" : "尚无测试产物；点击后自动构建测试环境并打开";
  const productionTitle = w.preview_environments?.production ? "在微信开发者工具打开生产环境产物" : "尚无生产产物；点击后自动构建生产环境并打开";
  const previewControl = w.preview_kind === "wechat"
    ? `<span class="preview-split"><button class="mini effect wechat test" data-row-action="open_wechat_test" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" title="${testTitle}">测试</button><button class="mini effect wechat production" data-row-action="open_wechat_production" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" title="${productionTitle}">生产</button></span>`
    : w.preview_kind === "web"
      ? `<span class="preview-split"><button class="mini effect web test" data-row-action="open_web_test" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" title="使用测试环境模式启动当前网页">测试</button><button class="mini effect web production" data-row-action="open_web_production" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" title="使用生产环境模式启动当前网页">生产</button></span>`
      : "";
  const item={repo_id:repo.id,path:w.path,branch:w.branch,dirty:Boolean(s.dirty),changes:(s.changed||0)+(s.untracked||0),locked:Boolean(w.locked),missing:s.available===false};
  const key=selectionKey(repo.id,w.path); if(selectedWorktrees.has(key)) selectedWorktrees.set(key,item);
  const checkbox=canSelect(w)?`<input class="worktree-select" type="checkbox" data-select-worktree data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" data-branch="${escapeHtml(w.branch)}" data-dirty="${s.dirty?"true":"false"}" data-changes="${item.changes}" data-locked="${w.locked?"true":"false"}" data-missing="${item.missing?"true":"false"}" ${selectedWorktrees.has(key)?"checked":""} aria-label="选择 ${escapeHtml(w.branch)}">`:`<span class="select-placeholder"></span>`;
  const occupiedBranch = w.detached || w.branch === "DETACHED" ? "游离提交" : w.branch;
  const openFolderData=`data-row-action="open_folder" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}"`;
  return `<div class="worktree ${rowState} role-${w.branch_role}"><div class="branch with-select">${checkbox}<div class="branch-copy"><div class="branch-line"><button class="branch-folder-link" ${openFolderData} aria-label="打开 ${escapeHtml(occupiedBranch)} 文件夹">${escapeHtml(occupiedBranch)}</button>${identityBadges.join("")}</div><button class="path-folder-link" ${openFolderData} aria-label="打开 ${escapeHtml(w.path)}">${escapeHtml(w.path)}</button></div></div><div class="base-branch" title="${baseHint}"><strong>${baseText}</strong><small>${baseSourceLabel}</small></div><div class="commit"><div class="status-line"><strong>${activity}</strong>${statusBadges.join("")}</div><p title="${escapeHtml(w.commit?.subject)}">${escapeHtml(w.commit?.subject||s.error||"无提交信息")}</p></div><div class="row-actions">${s.available!==false?`<button class="mini open" data-row-action="open_editor" data-repo="${repo.id}" data-path="${escapeHtml(w.path)}" title="使用 ${escapeHtml(repo.editor_label||"已检测编辑器")} 打开">编辑器</button>${previewControl}`:""}${deleteControl}</div></div>`;
}

function renderRemoveDialog(items) {
  const dirty=items.filter(item=>item.dirty); const changes=dirty.reduce((sum,item)=>sum+item.changes,0);
  $("#remove-title").textContent=items.length>1?`删除 ${items.length} 个工作树`:dirty.length?"删除有改动的工作树":"删除工作树";
  $("#remove-warning").className=`remove-warning ${dirty.length?"critical":"safe"}`;
  $("#remove-warning").innerHTML=dirty.length?`<strong>${dirty.length} 个目录有 ${changes} 项本地改动，删除后无法恢复</strong><span>未提交和未跟踪文件会永久丢失，不会创建备份；分支本身会保留。</span>`:`<strong>所选工作树当前没有本地改动</strong><span>只移除工作目录，分支本身会保留。</span>`;
  $("#remove-path").textContent=items.length===1?`${items[0].branch}  ·  ${items[0].path}`:`已选择 ${items.length} 个目录${items.some(item=>item.missing)?"，包含失效记录":""}`;
  $("#confirm-remove").textContent=dirty.length?`确认删除 ${items.length} 个`:`确认删除 ${items.length} 个`;
  $("#confirm-remove").disabled=false;
  $("#remove-dialog").classList.remove("is-busy");
  $$('#close-remove, #cancel-remove').forEach(button=>button.disabled=false);
}

function openRemoveDialog(items) {
  pendingRemove={items};
  renderRemoveDialog(items);
  $("#remove-dialog").showModal();
}

function showRemoveProgress(items) {
  $("#remove-dialog").classList.add("is-busy");
  $("#remove-title").textContent=`正在删除 ${items.length} 个工作树`;
  $("#remove-warning").className="remove-warning progress";
  $("#remove-warning").innerHTML='<strong><span class="inline-spinner"></span>正在移除工作目录…</strong><span>请稍候，分支会保留，列表将在完成后自动更新。</span>';
  $("#remove-path").textContent=items.length===1?items[0].path:`正在处理 ${items.length} 个目录`;
  $("#confirm-remove").textContent="正在删除…";
  $("#confirm-remove").disabled=true;
  $$('#close-remove, #cancel-remove').forEach(button=>button.disabled=true);
}

function removeItemsFromState(items) {
  const removed = new Set(items.map(item=>selectionKey(item.repo_id,item.path)));
  for (const repo of state.repositories) repo.worktrees=repo.worktrees.filter(w=>!removed.has(selectionKey(repo.id,w.path)));
}

async function action(payload,{blocking=true}={}) {
  if (blocking) document.body.classList.add("loading");
  try { const data=await api("/api/action",{method:"POST",body:JSON.stringify(payload)}); toast(data.message); if(data.state){state=data.state;render();} return data; }
  catch(error){toast(error.message,true); throw error;}
  finally{if(blocking)document.body.classList.remove("loading");}
}

function setFilter(filter) {
  activeFilter=filter; expandedRepos.clear(); render();
}

document.addEventListener("click", async event => {
  const nav=event.target.closest(".nav-item"); if(nav){setFilter(nav.dataset.filter);return;}
  const workspace=event.target.closest("[data-workspace]"); if(workspace){activeFilter="all";activeWorkspace=activeWorkspace===workspace.dataset.workspace?"all":workspace.dataset.workspace;render();return;}
  const toggle=event.target.closest("[data-toggle-repo]"); if(toggle){const id=toggle.dataset.toggleRepo;if(expandedRepos.has(id))expandedRepos.delete(id);else{expandedRepos.clear();expandedRepos.add(id);}renderRepos();return;}
  const row=event.target.closest("[data-row-action]"); if(row){await action({action:row.dataset.rowAction,repo_id:row.dataset.repo,path:row.dataset.path});return;}
  const remove=event.target.closest("[data-remove]"); if(remove){
    openRemoveDialog([{repo_id:remove.dataset.repo,path:remove.dataset.path,branch:remove.dataset.branch,dirty:remove.dataset.dirty==="true",changes:Number(remove.dataset.changes||0),locked:remove.dataset.locked==="true",missing:false}]);return;
  }
  const prune=event.target.closest("[data-prune-direct]"); if(prune){await action({action:"prune",repo_id:prune.dataset.repo,confirm:"PRUNE"});return;}
});

document.addEventListener("change", async event=>{
  const editor=event.target.closest("[data-editor-select]");
  if(editor){try{await action({action:"set_repo_editor",repo_id:editor.dataset.repo,editor_id:editor.value});}catch(_){}return;}
  const box=event.target.closest("[data-select-worktree]"); if(!box)return;
  const key=selectionKey(box.dataset.repo,box.dataset.path);
  if(box.checked) selectedWorktrees.set(key,{repo_id:box.dataset.repo,path:box.dataset.path,branch:box.dataset.branch,dirty:box.dataset.dirty==="true",changes:Number(box.dataset.changes||0),locked:box.dataset.locked==="true",missing:box.dataset.missing==="true"});
  else selectedWorktrees.delete(key);
  renderSelectionBar();
});

$("#search").addEventListener("input",renderRepos); $("#refresh").addEventListener("click",refresh);
$("#import-directory").addEventListener("click",async()=>{try{await action({action:"import_directory"});}catch(_){}});
$("#environment-check-button").addEventListener("click",openEnvironmentDialog);
$("#rerun-environment").addEventListener("click",runEnvironmentCheck);
$("#close-environment").addEventListener("click",()=>$("#environment-dialog").close());
$("#complete-environment").addEventListener("click",async()=>{try{await api("/api/action",{method:"POST",body:JSON.stringify({action:"complete_onboarding"})});state.settings.onboarding_version=1;$("#environment-dialog").close();toast("环境检测已完成");}catch(error){toast(error.message,true);}});
$("#settings-button").addEventListener("click",()=>$("#settings-dialog").showModal());
$("#cleanup-button").addEventListener("click",()=>setFilter(activeFilter==="cleanup"?"all":"cleanup"));
$("#settings-form").addEventListener("submit",async e=>{e.preventDefault();try{const data=await api("/api/config",{method:"POST",body:JSON.stringify({roots:$("#roots").value.split('\n').map(x=>x.trim()).filter(Boolean),max_depth:Number($("#max-depth").value),stale_days:Number($("#stale-days").value)})});state=data.state;render();$("#settings-dialog").close();toast(data.message);}catch(error){toast(error.message,true);}});
$("#select-visible").addEventListener("click",()=>{const visible=visibleSelections();const allSelected=visible.length&&visible.every(({repo,w})=>selectedWorktrees.has(selectionKey(repo.id,w.path)));for(const {repo,w} of visible){const key=selectionKey(repo.id,w.path);if(allSelected)selectedWorktrees.delete(key);else selectedWorktrees.set(key,{repo_id:repo.id,path:w.path,branch:w.branch,dirty:Boolean(w.status?.dirty),changes:(w.status?.changed||0)+(w.status?.untracked||0),locked:Boolean(w.locked),missing:w.status?.available===false});}renderRepos();});
$("#clear-selection").addEventListener("click",()=>{selectedWorktrees.clear();renderRepos();});
$("#remove-selected").addEventListener("click",()=>{if(selectedWorktrees.size)openRemoveDialog([...selectedWorktrees.values()]);});
function closeRemoveDialog(){
  if($("#remove-dialog").classList.contains("is-busy"))return;
  $("#remove-dialog").close();
  pendingRemove=null;
}
$("#close-remove").addEventListener("click",closeRemoveDialog);
$("#cancel-remove").addEventListener("click",closeRemoveDialog);
$("#remove-dialog").addEventListener("cancel",e=>{if($("#remove-dialog").classList.contains("is-busy"))e.preventDefault();});
async function confirmPendingRemoval(){if(!pendingRemove?.items?.length)return;const items=pendingRemove.items;showRemoveProgress(items);try{if(items.length===1&&!items[0].missing){const item=items[0];await action({action:"remove",repo_id:item.repo_id,path:item.path,confirm:item.path.split('/').filter(Boolean).pop()},{blocking:false});}else{await action({action:"remove_many",items:items.map(item=>({repo_id:item.repo_id,path:item.path})),confirm:`REMOVE_${items.length}`},{blocking:false});}for(const item of items)selectedWorktrees.delete(selectionKey(item.repo_id,item.path));removeItemsFromState(items);$("#remove-dialog").close();pendingRemove=null;render();refresh({quiet:true});}catch(_){renderRemoveDialog(items);}}
$("#confirm-remove").addEventListener("click",confirmPendingRemoval);
$("#remove-form").addEventListener("submit",e=>e.preventDefault());
document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $("#search").focus(); $("#search").select(); }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "r") { event.preventDefault(); refresh(); }
  if (event.key === "Escape" && document.activeElement === $("#search") && $("#search").value) { $("#search").value=""; renderRepos(); }
});
refresh();
