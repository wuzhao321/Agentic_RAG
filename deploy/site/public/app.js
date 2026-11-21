const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { mode: 'rag', view: 'chat', session: crypto.randomUUID(), busy: false, connected: false, status: null, offset: 0, limit: 20, jobKey: null, submitting: false };
const modes = { rag: '知识问答', agent: 'Agent 助手', search: '检索证据' };
const labels = { pending: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', interrupted: '已中断' };
const stages = { queued: '等待处理', validating: '校验文档', parsing: '解析与分块', embedding: '向量化', publishing: '发布索引', completed: '索引已发布' };
function el(tag, cls, text) { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = String(text); return node; }
function safeLink(url, text) {
  const node = el('a', '', text);
  try { const parsed = new URL(url); if (['https:', 'http:'].includes(parsed.protocol)) { node.href = parsed.href; node.target = '_blank'; node.rel = 'noopener noreferrer'; } } catch { /* A missing source stays plain text. */ }
  return node;
}
let toastTimer;
function toast(text) { clearTimeout(toastTimer); $('#toast').textContent = text; $('#toast').classList.remove('hidden'); toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 7000); }
async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  let data;
  try { data = await response.json(); } catch { throw Error('服务未返回有效响应，请检查连接后重试。'); }
  if (!response.ok) throw Error(data.error?.message || data.error || `请求失败（${response.status}）`);
  return data;
}
function updateControls() {
  const ready = state.connected && state.status?.index_ready && (state.mode === 'search' || state.status?.model_configured);
  $('#send').disabled = state.busy || !ready;
  $('#new-chat').disabled = state.busy;
  $$('[data-mode], .example').forEach(button => button.disabled = state.busy);
  $('#open-upload').disabled = !state.connected;
  $$('.submit-job').forEach(button => button.disabled = !state.connected || state.submitting);
  $('#mode-hint').textContent = !state.connected ? '等待连接知识库服务' : !state.status?.index_ready ? '知识库正在等待首次索引' : state.mode !== 'search' && !state.status?.model_configured ? '生成模型未配置，可先使用检索证据' : { rag: '多轮问答 · 回答附原文引用', agent: '自动调用工具 · 保留本次会话上下文', search: '仅检索证据 · 不调用生成模型' }[state.mode];
}
function setMode(value) { state.mode = value; $$('[data-mode]').forEach(button => { const selected = button.dataset.mode === value; button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected)); }); updateControls(); }
async function setView(value) {
  state.view = value;
  $$('.view').forEach(section => section.classList.toggle('hidden', section.id !== `${value}-view`));
  $$('[data-view]').forEach(button => { const selected = button.dataset.view === value; button.classList.toggle('active', selected); if (selected) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
  $('#breadcrumb').textContent = { chat: '知识助手', library: '文档与台账', tasks: '入库任务', evaluation: '评估与来源' }[value];
  if (state.connected) { if (value === 'library') await loadDocuments(); if (value === 'tasks') await loadJobs(); }
}
function message(role, text) {
  const node = el('div', `message ${role}`);
  node.append(el('div', 'message-label', role === 'user' ? '你' : '知序 · 知识助手'), el('div', 'message-body', text));
  $('#messages').append(node); node.scrollIntoView({ block: 'nearest' }); return node;
}
function showEvidence(result) {
  const area = $('#evidence'); area.replaceChildren();
  for (const source of result.sources || []) {
    const card = el('div', 'source-card'), title = el('h3');
    title.append(el('span', 'source-tag', source.id), document.createTextNode(source.source || '文档证据'));
    card.append(title, el('p', '', source.text));
    if (typeof source.score === 'number') card.append(el('p', '', `重排分数 ${source.score.toFixed(3)}`));
    if (source.source_url) card.append(safeLink(source.source_url, '查看原始来源'));
    area.append(card);
  }
  for (const trace of result.trace || []) {
    const card = el('div', 'source-card'), details = el('details');
    card.append(el('h3', '', `${trace.type === 'call' ? '调用' : '结果'} · ${trace.name}`));
    details.open = trace.type === 'call'; details.append(el('summary', '', trace.type === 'call' ? '工具参数' : '工具返回'), el('pre', '', JSON.stringify(trace.arguments ?? trace.content, null, 2)));
    card.append(details);
    for (const project of trace.content?.projects || []) if (project.source_url) card.append(safeLink(project.source_url, project.project_name));
    area.append(card);
  }
  if (!area.children.length) area.append(el('div', 'empty-evidence', '本次没有返回可用证据。'));
}
async function ask(event) {
  event.preventDefault(); const question = $('#question').value.trim();
  if (!question || state.busy || $('#send').disabled) return;
  state.busy = true; updateControls(); $('#welcome')?.remove(); message('user', question); $('#question').value = '';
  const pending = message('pending', '正在检索… 首次使用需要加载模型。'), started = Date.now();
  const timer = setInterval(() => { pending.querySelector('.message-body').textContent = `正在处理 · ${Math.floor((Date.now() - started) / 1000)} 秒`; }, 1000);
  try {
    const result = await api('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: state.mode, question, session: state.session }) });
    pending.remove(); const box = message('assistant', result.answer), meta = el('div', 'message-meta', `${modes[state.mode]} · ${result.latency_seconds} 秒`), button = el('button', 'text-button', `查看 ${result.sources.length} 条证据 / ${result.trace.filter(t => t.type === 'call').length} 次调用`);
    button.type = 'button'; button.onclick = () => showEvidence(result); meta.append(button); box.append(meta); showEvidence(result);
  } catch (error) { pending.remove(); message('error', error.message); }
  finally { clearInterval(timer); state.busy = false; updateControls(); $('#question').focus(); }
}
function renderStatus(data) {
  $('#stats').replaceChildren();
  for (const [value, label] of [[data.document_count, '知识文档'], [data.chunks, '检索片段'], [data.projects.length, '台账项目'], [data.index_ready ? '可检索' : '待入库', '索引状态']]) {
    const card = el('div', 'stat'); card.append(el('strong', '', typeof value === 'number' ? value.toLocaleString('zh-CN') : value), el('span', '', label)); $('#stats').append(card);
  }
  $('#doc-count').textContent = data.document_count;
  $('#runtime-model').textContent = `${data.model} · BGE · LangGraph`;
  $('#runtime').textContent = `${data.model}。检索链路：${data.retrieval}。`;
  $('#projects').replaceChildren();
  for (const project of data.projects) {
    const row = el('tr');
    for (const value of [project.project_name, project.signed_date, Number(project.amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }), project.supplier || '未记录']) row.append(el('td', '', value));
    const source = el('td'); source.append(project.source_url ? safeLink(project.source_url, '原始来源') : el('span', '', '—')); row.append(source); $('#projects').append(row);
  }
  if (!data.projects.length) { const row = el('tr'), cell = el('td', 'empty-cell', '尚无项目台账'); cell.colSpan = 5; row.append(cell); $('#projects').append(row); }
  renderEvaluation(data);
}
function renderEvaluation(data) {
  const area = $('#review'); area.replaceChildren(); const review = data.evaluation;
  const overview = el('div', 'notice'); overview.append(el('h3', '', review ? `${review.qa_count} 道演示问答 · ${review.agent_task_count} 项 Agent 任务` : '暂无已保存的评估记录'));
  if (review) overview.append(el('p', '', `证据定位检查：${review.evidence_checks_passed} / ${review.evidence_checks_total}。检查覆盖原始来源、解析文档与当时的索引。`), el('p', '', `人工审核：${review.human_reviewed ? '已完成' : '未完成'}；正式验收：${review.formal_acceptance ? '已完成' : '未完成'}。`), el('p', 'meta', `记录时间：${review.reviewed_at || '未记录'}。历史检查不等于本次部署验收。`));
  area.append(overview);
  if (data.review_available) { const card = el('div', 'notice'); card.append(el('h3', '', '逐题人工审核'), el('p', '', '审核结果保存在当前浏览器，可导出后使用项目原有导入命令归档。'), safeLink(new URL('/review', location.origin), '打开已有审核页面')); area.append(card); }
  if (data.latest_run) {
    const run = data.latest_run, card = el('div', 'notice');
    card.append(el('h3', '', '原有本地模型评估'), el('p', '', `问答要点 ${run.qa_answer_points_passed}/${run.qa_count}；参考证据命中 ${run.qa_reference_evidence_hits}/${run.qa_answerable_count}；无依据拒答 ${run.no_answer_correct}/${run.no_answer_count}。`), el('p', '', `Agent 工具选择 ${run.agent_tools_correct}/${run.agent_count}；严格参数匹配 ${run.agent_strict_args_match}/${run.agent_count}。`));
    if (run.limitations) card.append(el('p', '', run.limitations)); area.append(card);
  }
  for (const run of data.development_runs || []) {
    const card = el('div', 'notice'), details = el('details');
    card.append(el('h3', '', `开发集评估 · ${run.kind}`), el('p', '', `${run.run} · ${run.status} · ${run.current_index ? '与当前索引一致' : '历史索引记录'}`));
    details.append(el('summary', '', '查看已有评估指标'), el('pre', '', JSON.stringify(run.summary, null, 2))); card.append(details); area.append(card);
  }
}
let connecting = false;
async function connect() {
  if (connecting) return; connecting = true; $('#connection').textContent = '检查连接中';
  try {
    const connection = await api('/api/connection');
    if (!connection.configured) throw Error('站点尚未连接知识库服务。连接后可使用已有文档、问答和入库功能。');
    const data = await api('/api/status'); state.connected = true; state.status = data;
    $('#connection').textContent = '知识库已连接'; $('#connection').className = 'connection ready'; $('#connection-notice').classList.add('hidden'); renderStatus(data);
    if (state.view === 'library') await loadDocuments(); if (state.view === 'tasks') await loadJobs();
  } catch (error) { state.connected = false; $('#connection').textContent = '未连接 · 重试'; $('#connection').className = 'connection offline'; $('#connection-notice').textContent = error.message; $('#connection-notice').classList.remove('hidden'); }
  finally { connecting = false; updateControls(); }
}
let loadingDocuments = false;
async function loadDocuments() {
  if (!state.connected || loadingDocuments) return; loadingDocuments = true;
  try {
    const params = new URLSearchParams({ offset: state.offset, limit: state.limit });
    if ($('#filter-project').value.trim()) params.set('project_name', $('#filter-project').value.trim());
    if ($('#filter-type').value) params.set('doc_type', $('#filter-type').value);
    const page = await api(`/api/documents?${params}`), area = $('#documents'); area.replaceChildren();
    for (const document of page.items) {
      const row = el('tr'), name = el('td', '', document.file_name), project = el('td', '', document.project_name);
      project.append(el('small', '', document.doc_type)); row.append(name, project, el('td', '', `${Math.ceil(document.size_bytes / 1024).toLocaleString('zh-CN')} KB`));
      const action = el('td'), button = el('button', 'text-button', '总结文档'); button.type = 'button'; button.onclick = () => { setView('chat'); setMode('agent'); $('#question').value = `总结 ${document.file_name} 的主要内容。`; $('#question').focus(); }; action.append(button); row.append(action); area.append(row);
    }
    if (!page.items.length) { const row = el('tr'), cell = el('td', 'empty-cell', '没有符合条件的文档'); cell.colSpan = 4; row.append(cell); area.append(row); }
    $('#page-label').textContent = page.total ? `${page.offset + 1}–${page.offset + page.items.length} / ${page.total} 份文档` : '0 份文档';
    $('#prev-page').disabled = state.offset === 0; $('#next-page').disabled = page.offset + page.items.length >= page.total;
  } catch (error) { toast(error.message); } finally { loadingDocuments = false; }
}
let loadingJobs = false;
async function loadJobs() {
  if (!state.connected || loadingJobs) return; loadingJobs = true;
  try {
    const jobs = await api('/api/ingest-jobs'), area = $('#jobs'); area.replaceChildren();
    for (const job of jobs) {
      const card = el('article', 'job-card'), heading = el('div', 'job-head');
      heading.append(el('h3', '', `入库任务 · ${job.id.slice(0, 10)}`), el('span', `badge ${job.status}`, labels[job.status] || job.status));
      card.append(heading, el('p', 'job-meta', `${stages[job.stage] || job.stage} · ${job.completed}/${job.total} · 第 ${job.attempt_count} 次执行 · ${new Date(job.created_at).toLocaleString('zh-CN')}`));
      if (job.status === 'running' && job.total > 0) { const progress = el('progress'); progress.max = job.total; progress.value = job.completed; progress.setAttribute('aria-label', '当前阶段进度'); card.append(progress); }
      if (job.result) card.append(el('p', 'meta', `发布完成，${job.result.total_chunks} 个知识片段。`));
      if (job.error) card.append(el('p', 'error-text', job.error));
      if (['failed', 'interrupted'].includes(job.status)) {
        const button = el('button', 'secondary', '重试任务'); button.onclick = async () => { button.disabled = true; try { await api(`/api/ingest-jobs/${job.id}/retry`, { method: 'POST' }); toast('任务已重新排队'); await loadJobs(); } catch (error) { toast(error.message); button.disabled = false; } }; card.append(button);
      }
      area.append(card);
    }
    if (!jobs.length) area.append(el('div', 'notice', '还没有入库任务。上传文档后，点击“更新知识索引”开始。'));
  } catch (error) { toast(error.message); } finally { loadingJobs = false; }
}
async function submitJob() {
  if (!state.connected || state.submitting) return;
  state.submitting = true; state.jobKey ||= crypto.randomUUID(); updateControls();
  try {
    const job = await api('/api/ingest-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': state.jobKey }, body: JSON.stringify({ description: '站点完整入库' }) });
    state.jobKey = null; toast(`任务 ${job.id.slice(0, 10)} 已提交`); await setView('tasks');
  } catch (error) { toast(`${error.message} 再次提交会沿用同一请求编号。`); }
  finally { state.submitting = false; updateControls(); }
}
$('#composer').addEventListener('submit', ask);
$('#question').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#composer').requestSubmit(); } });
$$('[data-mode]').forEach(button => button.onclick = () => setMode(button.dataset.mode));
$$('[data-view]').forEach(button => button.onclick = () => setView(button.dataset.view));
$('#new-chat').onclick = () => { if (state.busy) return; state.session = crypto.randomUUID(); $('#messages').replaceChildren(); message('assistant', '已开始新会话。可以查询知识、总结文档，或筛选项目台账。'); $('#evidence').replaceChildren(el('div', 'empty-evidence', '提问后显示本次证据。')); setView('chat'); $('#question').focus(); };
$('#connection').onclick = connect;
$('#document-filter').onsubmit = event => { event.preventDefault(); state.offset = 0; loadDocuments(); };
$('#prev-page').onclick = () => { if (loadingDocuments) return; state.offset = Math.max(0, state.offset - state.limit); loadDocuments(); };
$('#next-page').onclick = () => { if (loadingDocuments) return; state.offset += state.limit; loadDocuments(); };
$$('.submit-job').forEach(button => button.onclick = submitJob);
$('#refresh-jobs').onclick = () => state.connected ? loadJobs() : connect();
$('#open-upload').onclick = () => { $('#upload-error').textContent = ''; $('#upload-dialog').showModal(); };
$('#close-upload').onclick = () => $('#upload-dialog').close();
$('#upload-form').onsubmit = async event => {
  event.preventDefault(); const data = new FormData(event.currentTarget), file = data.get('file');
  if (!file || file.size > 20 * 1024 * 1024) { $('#upload-error').textContent = '请选择不超过 20 MB 的 PDF 或 DOCX。'; return; }
  $('#upload-submit').disabled = true; $('#close-upload').disabled = true; $('#upload-error').textContent = '';
  try { await api('/api/documents', { method: 'POST', body: data }); $('#upload-dialog').close(); $('#upload-form').reset(); toast('上传完成。点击“更新知识索引”，让新文档参与检索。'); await connect(); }
  catch (error) { $('#upload-error').textContent = error.message; }
  finally { $('#upload-submit').disabled = false; $('#close-upload').disabled = false; }
};
const examples = [
  ['rag', 'FastAPI 中如何把查询参数设置为必选？'],
  ['rag', 'FastAPI 的 response_model 和返回类型同时声明时，谁优先？'],
  ['agent', '查询 2026 年签订、金额超过 200 万元的项目及供应商。'],
  ['search', '请求体、查询参数和路径参数有什么区别？']
];
for (const [mode, question] of examples) { const button = el('button', 'example'); button.type = 'button'; button.append(el('small', '', modes[mode]), document.createTextNode(question)); button.onclick = () => { setMode(mode); $('#question').value = question; $('#question').focus(); }; $('#examples').append(button); }
setInterval(() => { if (document.hidden || !state.connected) return; if (state.view === 'tasks') loadJobs(); }, 5000);
setInterval(() => { if (!document.hidden && state.connected && !state.busy) connect(); }, 30000);
connect();
