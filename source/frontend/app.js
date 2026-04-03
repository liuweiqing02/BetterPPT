import { initMetricsView } from './metrics_view.js';
import { initReplayView } from './replay_view.js';
import { initObservabilityView } from './observability_view.js';

const API_BASE = 'http://127.0.0.1:8000/api/v1';
const POLL_INTERVAL_MS = 3000;

let sourceFileId = null;
let referenceFileId = null;
let pollTimer = null;
let uploadConstraints = null;

const uploadStatus = document.getElementById('uploadStatus');
const taskCreateStatus = document.getElementById('taskCreateStatus');
const taskDetail = document.getElementById('taskDetail');
const taskNoInput = document.getElementById('taskNoInput');
const cancelTaskBtn = document.getElementById('cancelTaskBtn');
const retryTaskBtn = document.getElementById('retryTaskBtn');
const taskOperationStatus = document.getElementById('taskOperationStatus');
const taskListPanel = document.getElementById('taskListPanel');
const taskListStatus = document.getElementById('taskListStatus');
const previewPanel = document.getElementById('previewPanel');
const previewStatus = document.getElementById('previewStatus');
const downloadLink = document.getElementById('downloadLink');
const replayPanel = document.getElementById('replayPanel');
const replayStatus = document.getElementById('replayStatus');
const replayRefreshBtn = document.getElementById('replayRefreshBtn');
const metricsPanel = document.getElementById('metricsPanel');
const metricsStatus = document.getElementById('metricsStatus');
const metricsRefreshBtn = document.getElementById('metricsRefreshBtn');
const metricsDaysInput = document.getElementById('metricsDaysInput');
const observabilityPanel = document.getElementById('observabilityPanel');
const observabilityStatus = document.getElementById('observabilityStatus');
const observabilityRefreshBtn = document.getElementById('observabilityRefreshBtn');
const appNav = document.getElementById('appNav');
const routeStatus = document.getElementById('routeStatus');

const views = {
  create: document.getElementById('viewCreate'),
  tasks: document.getElementById('viewTasks'),
  detail: document.getElementById('viewDetail'),
  result: document.getElementById('viewResult'),
};

function pretty(json) {
  return JSON.stringify(json, null, 2);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function getRouteFromLocation() {
  const hash = String(window.location.hash || '');
  if (hash.startsWith('#/app/')) {
    return hash.slice(1);
  }
  const path = String(window.location.pathname || '/');
  if (path.startsWith('/app/')) {
    return path;
  }
  return '/app/create';
}

function getViewKey(route) {
  if (route.startsWith('/app/tasks')) {
    return 'tasks';
  }
  if (route.startsWith('/app/detail')) {
    return 'detail';
  }
  if (route.startsWith('/app/result')) {
    return 'result';
  }
  return 'create';
}

function navigate(route, replace = false) {
  const normalized = route.startsWith('/app/') ? route : '/app/create';
  const useHistoryPath = window.location.pathname.startsWith('/app/');

  if (useHistoryPath) {
    if (replace) {
      window.history.replaceState({}, '', normalized);
    } else {
      window.history.pushState({}, '', normalized);
    }
  } else {
    const next = `#${normalized}`;
    if (replace) {
      window.history.replaceState({}, '', `${window.location.pathname}${window.location.search}${next}`);
    } else {
      window.location.hash = normalized;
    }
  }
  renderRoute();
}

function renderRoute() {
  const route = getRouteFromLocation();
  const key = getViewKey(route);

  Object.entries(views).forEach(([name, node]) => {
    if (!node) {
      return;
    }
    node.classList.toggle('hidden', name !== key);
  });

  appNav?.querySelectorAll('button[data-route]')?.forEach((btn) => {
    const target = btn.getAttribute('data-route') || '';
    const active = key === getViewKey(target);
    btn.classList.toggle('active', active);
  });

  const labels = {
    create: '当前页面：任务创建',
    tasks: '当前页面：任务列表',
    detail: '当前页面：任务详情',
    result: '当前页面：预览下载',
  };
  if (routeStatus) {
    routeStatus.textContent = labels[key] || '';
  }
}

function normalizeTaskNo(value) {
  return String(value || '').trim();
}

function toUserErrorMessage(error) {
  const message = String(error?.message || '').trim();
  if (!message) {
    return '系统繁忙，请稍后再试。';
  }
  if (/failed to fetch|networkerror|network request|fetch/i.test(message)) {
    return '网络异常，请检查后重试。';
  }
  if (/429|rate limit|concurrency|busy|繁忙/i.test(message)) {
    return '系统繁忙，请稍后再试。';
  }
  return message;
}

async function api(path, init = {}) {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  });

  let data = {};
  try {
    data = await resp.json();
  } catch (error) {
    if (!resp.ok) {
      const err = new Error(`HTTP ${resp.status}`);
      err.status = resp.status;
      throw err;
    }
  }

  if (!resp.ok || data.code !== 0) {
    const err = new Error(data.message || `HTTP ${resp.status}`);
    err.status = resp.status;
    err.data = data?.data || {};
    throw err;
  }
  return data;
}

function fileExt(name) {
  const idx = String(name || '').lastIndexOf('.');
  if (idx < 0) {
    return '';
  }
  return String(name).slice(idx + 1).toLowerCase();
}

function validateFileByConstraints(file, constraint, label) {
  if (!file) {
    return `请先选择${label}`;
  }
  const allowed = Array.isArray(constraint?.allowed_ext) ? constraint.allowed_ext.map((x) => String(x).toLowerCase()) : [];
  const ext = fileExt(file.name);
  if (allowed.length > 0 && !allowed.includes(ext)) {
    return `${label}格式不支持，仅允许：${allowed.join(', ')}`;
  }
  const maxSizeMb = Number(constraint?.max_file_size_mb || 0);
  if (maxSizeMb > 0 && file.size > maxSizeMb * 1024 * 1024) {
    return `${label}超过大小限制（最大 ${maxSizeMb}MB）`;
  }
  return null;
}

function putFileWithProgress(uploadUrl, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', uploadUrl, true);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || typeof onProgress !== 'function') {
        return;
      }
      const ratio = event.total > 0 ? event.loaded / event.total : 0;
      onProgress(Math.max(0, Math.min(1, ratio)));
    };

    xhr.onerror = () => reject(new Error('upload network error'));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (typeof onProgress === 'function') {
          onProgress(1);
        }
        resolve();
      } else {
        reject(new Error(`upload failed: ${xhr.status}`));
      }
    };

    file.arrayBuffer()
      .then((buffer) => xhr.send(buffer))
      .catch((error) => reject(error));
  });
}

async function uploadFile(file, fileRole, onProgress) {
  const upload = await api('/files/upload-url', {
    method: 'POST',
    body: JSON.stringify({
      filename: file.name,
      file_role: fileRole,
      content_type: file.type || 'application/octet-stream',
      file_size: file.size,
    }),
  });

  await putFileWithProgress(upload.data.upload_url, file, onProgress);

  await api('/files/complete', {
    method: 'POST',
    body: JSON.stringify({
      file_id: upload.data.file_id,
      checksum_sha256: null,
    }),
  });

  return upload.data.file_id;
}

function setTaskNo(taskNo) {
  taskNoInput.value = taskNo;
  setTaskOperationStatus('');
}

function setTaskOperationStatus(message) {
  if (taskOperationStatus) {
    taskOperationStatus.textContent = message;
  }
}

function getTaskNoOrNotify() {
  const taskNo = normalizeTaskNo(taskNoInput.value);
  if (!taskNo) {
    setTaskOperationStatus('请先填写 task_no，再执行任务操作。');
    return null;
  }
  return taskNo;
}

async function executeTaskAction(action) {
  const taskNo = getTaskNoOrNotify();
  if (!taskNo) {
    return;
  }
  await executeTaskActionForTask(taskNo, action, true);
}

async function executeTaskActionForTask(taskNo, action, refreshDetail = false) {
  setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}请求发送中...`);
  try {
    const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/${action}`, {
      method: 'POST',
    });
    const statusText = resp.data?.status ? `，当前状态：${resp.data.status}` : '';
    setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}成功：${taskNo}${statusText}`);
    if (refreshDetail) {
      await refreshTask();
    }
    await loadTaskList();
    metricsView?.loadMetrics();
  } catch (error) {
    setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}失败：${toUserErrorMessage(error)}`);
  }
}

function fallbackBadgeText(task) {
  const state = String(task?.fallback_state || 'none');
  if (state === 'running') {
    return '回退中';
  }
  if (state === 'succeeded') {
    return '已回退';
  }
  if (state === 'failed') {
    return '回退失败';
  }
  return '';
}

function renderTaskList(items) {
  taskListPanel.innerHTML = '';
  if (!items || items.length === 0) {
    taskListPanel.innerHTML = '<div class="task-list-empty">暂无任务，上传 PDF 和参考 PPT 开始生成。</div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'task-item';
    const fallbackText = fallbackBadgeText(item);
    const fallbackBadge = fallbackText
      ? `<span class="badge status-fallback">${escapeHtml(fallbackText)}</span>`
      : '';

    card.innerHTML = `
      <div class="task-item-top">
        <div class="task-item-title">${escapeHtml(item.task_no)}</div>
        <div class="toolbar">
          <span class="badge status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
          ${fallbackBadge}
        </div>
      </div>
      <div class="task-item-meta">
        <span>progress ${escapeHtml(item.progress)}%</span>
        <span>detail ${escapeHtml(item.detail_level)}</span>
        <span>${item.current_step ? `step ${escapeHtml(item.current_step)}` : 'step -'}</span>
        <span>fallback_attempt ${escapeHtml(item.fallback_attempt_no ?? '-')}</span>
      </div>
      <div class="task-item-meta">失败原因：${escapeHtml(item.error_message || '-')}</div>
      <div class="task-item-actions">
        <button type="button" class="secondary" data-action="detail">查看详情</button>
        <button type="button" class="secondary" data-action="retry">重试</button>
      </div>
    `;

    card.querySelector('[data-action="detail"]')?.addEventListener('click', async () => {
      setTaskNo(item.task_no);
      navigate('/app/detail');
      await refreshTask();
    });

    card.querySelector('[data-action="retry"]')?.addEventListener('click', async () => {
      setTaskNo(item.task_no);
      await executeTaskActionForTask(item.task_no, 'retry', false);
    });

    taskListPanel.appendChild(card);
  }
}

async function loadTaskList() {
  taskListStatus.textContent = '加载中...';
  try {
    const resp = await api('/tasks');
    renderTaskList(resp.data.items || []);
    taskListStatus.textContent = `共 ${resp.data.items?.length || 0} 个任务`;
  } catch (error) {
    taskListPanel.innerHTML = `<div class="task-list-empty">${escapeHtml(toUserErrorMessage(error))}</div>`;
    taskListStatus.textContent = '加载失败';
  }
}

function renderTaskDetail(task, events) {
  const errorText = task.error_message || '-';
  const stepText = task.current_step || '-';
  const fallbackState = task.fallback_state || 'none';
  const fallbackHint = fallbackState === 'running' ? '回退中' : '无';

  taskDetail.innerHTML = `
    <div class="task-detail-grid">
      <div class="metric">
        <div class="metric-label">task_no</div>
        <div class="metric-value">${escapeHtml(task.task_no)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">status</div>
        <div class="metric-value">${escapeHtml(task.status)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">current_step</div>
        <div class="metric-value">${escapeHtml(stepText)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">progress</div>
        <div class="metric-value">${escapeHtml(task.progress)}%</div>
      </div>
      <div class="metric">
        <div class="metric-label">fallback_state</div>
        <div class="metric-value">${escapeHtml(fallbackState)}（${escapeHtml(fallbackHint)}）</div>
      </div>
      <div class="metric">
        <div class="metric-label">fallback_attempt_no</div>
        <div class="metric-value">${escapeHtml(task.fallback_attempt_no ?? '-')}</div>
      </div>
      <div class="metric" style="grid-column: 1 / -1;">
        <div class="metric-label">error_message</div>
        <div class="metric-value ${task.error_message ? 'error' : ''}">${escapeHtml(errorText)}</div>
      </div>
    </div>
    <div>
      <div class="metric-label">task raw data</div>
      <pre>${escapeHtml(pretty(task))}</pre>
    </div>
    <div>
      <div class="metric-label">recent events</div>
      <pre>${escapeHtml(pretty(events))}</pre>
    </div>
  `;
}

let replayView;
let metricsView;
let observabilityView;

async function refreshTask() {
  const taskNo = normalizeTaskNo(taskNoInput.value);
  if (!taskNo) {
    taskDetail.innerHTML = '<div class="task-list-empty">请填写 task_no 后再查询。</div>';
    return;
  }

  try {
    const detail = await api(`/tasks/${encodeURIComponent(taskNo)}`);
    const events = await api(`/tasks/${encodeURIComponent(taskNo)}/events?limit=10`);
    renderTaskDetail(detail.data, events.data.items || []);

    replayView?.refresh();
    observabilityView?.refresh();

    if (['succeeded', 'failed', 'canceled'].includes(detail.data.status) && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    taskDetail.innerHTML = `<div class="task-list-empty">${escapeHtml(toUserErrorMessage(error))}</div>`;
  }
}

async function refreshPreview() {
  const taskNo = normalizeTaskNo(taskNoInput.value);
  if (!taskNo) {
    previewStatus.textContent = '请先填写 task_no。';
    return;
  }

  previewStatus.textContent = '预览加载中...';
  previewPanel.innerHTML = '<div class="preview-loading">正在加载预览，请稍候。</div>';
  downloadLink.textContent = '';

  try {
    const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/preview`);
    const slides = resp.data.slides || [];
    if (slides.length === 0) {
      previewPanel.innerHTML = '<div class="preview-empty">结果尚未生成，请稍后刷新。</div>';
      previewStatus.textContent = '结果尚未生成，请稍后刷新。';
      return;
    }

    previewPanel.innerHTML = '';
    for (const slide of slides) {
      const wrap = document.createElement('section');
      wrap.className = 'preview-slide';
      wrap.innerHTML = `<div class="preview-slide-meta">Page ${escapeHtml(slide.page_no)}</div>`;
      const img = document.createElement('img');
      img.src = slide.image_url;
      img.alt = `slide-${slide.page_no}`;
      img.loading = 'lazy';
      img.addEventListener('error', () => {
        wrap.insertAdjacentHTML('beforeend', '<div class="preview-empty">该页面图片加载失败。</div>');
      });
      wrap.appendChild(img);
      previewPanel.appendChild(wrap);
    }
    previewStatus.textContent = `预览加载成功，共 ${slides.length} 页。`;
  } catch (error) {
    previewPanel.innerHTML = `<div class="preview-empty">${escapeHtml(toUserErrorMessage(error))}</div>`;
    previewStatus.textContent = '预览失败，请稍后重试。';
  }
}

async function loadUploadConstraints() {
  try {
    const resp = await api('/files/upload-constraints');
    uploadConstraints = resp.data || null;
  } catch (error) {
    uploadConstraints = {
      pdf: { allowed_ext: ['pdf'], max_file_size_mb: 100, max_pages: 300 },
      reference_ppt: { allowed_ext: ['ppt', 'pptx'], max_file_size_mb: 100, max_pages: 200 },
    };
    uploadStatus.textContent = `上传限制配置拉取失败，已使用兜底限制：${toUserErrorMessage(error)}`;
  }
}

document.getElementById('uploadBtn').addEventListener('click', async () => {
  const pdf = document.getElementById('pdfFile').files[0];
  const ppt = document.getElementById('pptFile').files[0];

  const pdfConstraint = uploadConstraints?.pdf;
  const pptConstraint = uploadConstraints?.reference_ppt;
  const pdfError = validateFileByConstraints(pdf, pdfConstraint, 'PDF');
  if (pdfError) {
    uploadStatus.textContent = pdfError;
    return;
  }
  const pptError = validateFileByConstraints(ppt, pptConstraint, '参考PPT');
  if (pptError) {
    uploadStatus.textContent = pptError;
    return;
  }

  const totalBytes = Math.max(1, (pdf?.size || 0) + (ppt?.size || 0));
  let uploadedBytes = 0;

  const buildProgress = (label, file) => (ratio) => {
    const current = Math.round((file.size || 0) * ratio);
    const overall = Math.min(100, Math.round(((uploadedBytes + current) / totalBytes) * 100));
    uploadStatus.textContent = `${label} 上传中 ${(ratio * 100).toFixed(0)}%（总进度 ${overall}%）`;
  };

  try {
    sourceFileId = await uploadFile(pdf, 'pdf_source', buildProgress('PDF', pdf));
    uploadedBytes += pdf.size || 0;

    referenceFileId = await uploadFile(ppt, 'ppt_reference', buildProgress('参考PPT', ppt));
    uploadedBytes += ppt.size || 0;

    uploadStatus.textContent = `上传成功 source_file_id=${sourceFileId}, reference_file_id=${referenceFileId}`;
    await loadTaskList();
    metricsView?.loadMetrics();
  } catch (error) {
    uploadStatus.textContent = `上传失败：${toUserErrorMessage(error)}`;
  }
});

document.getElementById('createTaskBtn').addEventListener('click', async () => {
  if (!sourceFileId || !referenceFileId) {
    taskCreateStatus.textContent = '请先上传文件';
    return;
  }

  const detailLevel = document.getElementById('detailLevel').value;
  const userPrompt = document.getElementById('prompt').value.trim() || null;
  const ragEnabled = document.getElementById('ragEnabled').checked;

  try {
    const resp = await api('/tasks', {
      method: 'POST',
      body: JSON.stringify({
        source_file_id: sourceFileId,
        reference_file_id: referenceFileId,
        detail_level: detailLevel,
        user_prompt: userPrompt,
        rag_enabled: ragEnabled,
        idempotency_key: `demo-${Date.now()}`,
      }),
    });

    setTaskNo(resp.data.task_no);
    taskCreateStatus.textContent = `任务创建成功：${resp.data.task_no}`;
    await Promise.all([loadTaskList(), refreshTask()]);
    metricsView?.loadMetrics();
    navigate('/app/detail');
  } catch (error) {
    taskCreateStatus.textContent = `任务创建失败：${toUserErrorMessage(error)}`;
  }
});

document.getElementById('refreshTaskListBtn').addEventListener('click', loadTaskList);
document.getElementById('refreshTaskBtn').addEventListener('click', refreshTask);
cancelTaskBtn?.addEventListener('click', () => executeTaskAction('cancel'));
retryTaskBtn?.addEventListener('click', () => executeTaskAction('retry'));

document.getElementById('pollTaskBtn').addEventListener('click', () => {
  if (pollTimer) {
    return;
  }
  refreshTask();
  pollTimer = setInterval(refreshTask, POLL_INTERVAL_MS);
});

document.getElementById('stopPollBtn').addEventListener('click', () => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});

document.getElementById('previewBtn').addEventListener('click', refreshPreview);

document.getElementById('resultBtn').addEventListener('click', async () => {
  const taskNo = normalizeTaskNo(taskNoInput.value);
  if (!taskNo) {
    downloadLink.textContent = '请先填写 task_no。';
    return;
  }

  try {
    const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/result`);
    downloadLink.innerHTML = `<a href="${resp.data.download_url}" target="_blank" rel="noreferrer">下载 ${escapeHtml(resp.data.filename)}</a>`;
  } catch (error) {
    downloadLink.textContent = `获取下载链接失败：${toUserErrorMessage(error)}`;
  }
});

appNav?.querySelectorAll('button[data-route]')?.forEach((btn) => {
  btn.addEventListener('click', () => {
    navigate(btn.getAttribute('data-route') || '/app/create');
  });
});

window.addEventListener('hashchange', renderRoute);
window.addEventListener('popstate', renderRoute);

replayView = initReplayView({
  api,
  escapeHtml,
  taskNoInput,
  replayPanel,
  replayStatus,
  replayRefreshBtn,
});

metricsView = initMetricsView({
  api,
  escapeHtml,
  metricsPanel,
  metricsStatus,
  metricsRefreshBtn,
  metricsDaysInput,
});

observabilityView = initObservabilityView({
  api,
  escapeHtml,
  taskNoInput,
  panel: observabilityPanel,
  statusNode: observabilityStatus,
  refreshBtn: observabilityRefreshBtn,
});

await loadUploadConstraints();
await loadTaskList();
metricsView.loadMetrics();
renderRoute();
