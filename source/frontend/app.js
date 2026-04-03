import { initMetricsView } from './metrics_view.js';
import { initReplayView } from './replay_view.js';
import { initObservabilityView } from './observability_view.js';

const API_BASE = 'http://127.0.0.1:8000/api/v1';

let sourceFileId = null;
let referenceFileId = null;
let pollTimer = null;

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
      throw new Error(`HTTP ${resp.status}`);
    }
  }

  if (!resp.ok || data.code !== 0) {
    throw new Error(data.message || `HTTP ${resp.status}`);
  }
  return data;
}

async function uploadFile(file, fileRole) {
  const upload = await api('/files/upload-url', {
    method: 'POST',
    body: JSON.stringify({
      filename: file.name,
      file_role: fileRole,
      content_type: file.type,
      file_size: file.size,
    }),
  });

  await fetch(upload.data.upload_url, {
    method: 'PUT',
    headers: {
      'Content-Type': file.type,
    },
    body: await file.arrayBuffer(),
  });

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
  const taskNo = taskNoInput.value.trim();
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

  setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}请求发送中...`);

  try {
    const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/${action}`, {
      method: 'POST',
    });
    const statusText = resp.data?.status ? `，当前状态：${resp.data.status}` : '';
    setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}成功：${taskNo}${statusText}`);
    await refreshTask();
    if (metricsView) {
      metricsView.loadMetrics();
    }
  } catch (error) {
    setTaskOperationStatus(`${action === 'cancel' ? '取消' : '重试'}失败：${error.message}`);
  }
}

function renderTaskList(items) {
  taskListPanel.innerHTML = '';
  if (!items || items.length === 0) {
    taskListPanel.innerHTML = '<div class="task-list-empty">当前还没有任务，先上传文件并创建一个任务吧。</div>';
    return;
  }

  for (const item of items) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'task-item';
    card.innerHTML = `
      <div class="task-item-top">
        <div class="task-item-title">${escapeHtml(item.task_no)}</div>
        <span class="badge status-${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
      </div>
      <div class="task-item-meta">
        <span>progress ${escapeHtml(item.progress)}%</span>
        <span>detail ${escapeHtml(item.detail_level)}</span>
        <span>${item.current_step ? `step ${escapeHtml(item.current_step)}` : 'step -'}</span>
      </div>
    `;
    card.addEventListener('click', () => {
      setTaskNo(item.task_no);
      taskCreateStatus.textContent = `已回填任务号：${item.task_no}`;
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
    taskListPanel.innerHTML = `<div class="task-list-empty">任务列表加载失败：${escapeHtml(error.message)}</div>`;
    taskListStatus.textContent = '加载失败';
  }
}

function renderTaskDetail(task, events) {
  const errorText = task.error_message || '-';
  const stepText = task.current_step || '-';
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
        <div class="metric-label">detail_level</div>
        <div class="metric-value">${escapeHtml(task.detail_level)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">error_code</div>
        <div class="metric-value ${task.error_code ? 'error' : ''}">${escapeHtml(task.error_code || '-')}</div>
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
  const taskNo = taskNoInput.value.trim();
  if (!taskNo) {
    taskDetail.innerHTML = '<div class="task-list-empty">请填写 task_no 后再查询。</div>';
    return;
  }

  try {
    const detail = await api(`/tasks/${taskNo}`);
    const events = await api(`/tasks/${taskNo}/events?limit=10`);
    renderTaskDetail(detail.data, events.data.items || []);

    if (replayView) {
      replayView.refresh();
    }
    if (observabilityView) {
      observabilityView.refresh();
    }

    if (['succeeded', 'failed', 'canceled'].includes(detail.data.status) && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (error) {
    taskDetail.innerHTML = `<div class="task-list-empty">查询失败：${escapeHtml(error.message)}</div>`;
  }
}

async function refreshPreview() {
  const taskNo = taskNoInput.value.trim();
  if (!taskNo) {
    previewStatus.textContent = '请先填写 task_no。';
    return;
  }

  previewStatus.textContent = '预览加载中...';
  previewPanel.innerHTML = '<div class="preview-loading">正在加载预览，请稍候。</div>';
  downloadLink.textContent = '';

  try {
    const resp = await api(`/tasks/${taskNo}/preview`);
    const slides = resp.data.slides || [];
    if (slides.length === 0) {
      previewPanel.innerHTML = '<div class="preview-empty">暂无可用预览页。</div>';
      previewStatus.textContent = '预览已加载，但当前没有页面。';
      return;
    }

    previewPanel.innerHTML = '';
    for (const slide of slides) {
      const wrap = document.createElement('section');
      wrap.className = 'preview-slide';
      wrap.innerHTML = `
        <div class="preview-slide-meta">Page ${escapeHtml(slide.page_no)}</div>
      `;
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
    previewPanel.innerHTML = `<div class="preview-empty">预览加载失败：${escapeHtml(error.message)}</div>`;
    previewStatus.textContent = '预览失败，请稍后重试。';
  }
}

document.getElementById('uploadBtn').addEventListener('click', async () => {
  const pdf = document.getElementById('pdfFile').files[0];
  const ppt = document.getElementById('pptFile').files[0];
  if (!pdf || !ppt) {
    uploadStatus.textContent = '请先选择 PDF 和 PPT 文件';
    return;
  }

  uploadStatus.textContent = '上传中...';
  try {
    sourceFileId = await uploadFile(pdf, 'pdf_source');
    referenceFileId = await uploadFile(ppt, 'ppt_reference');
    uploadStatus.textContent = `上传成功 source_file_id=${sourceFileId}, reference_file_id=${referenceFileId}`;
    await loadTaskList();
    if (metricsView) {
      metricsView.loadMetrics();
    }
  } catch (error) {
    uploadStatus.textContent = `上传失败: ${error.message}`;
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
    if (metricsView) {
      metricsView.loadMetrics();
    }
  } catch (error) {
    taskCreateStatus.textContent = `任务创建失败: ${error.message}`;
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
  pollTimer = setInterval(refreshTask, 2500);
});

document.getElementById('stopPollBtn').addEventListener('click', () => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});

document.getElementById('previewBtn').addEventListener('click', refreshPreview);

document.getElementById('resultBtn').addEventListener('click', async () => {
  const taskNo = taskNoInput.value.trim();
  if (!taskNo) {
    downloadLink.textContent = '请先填写 task_no。';
    return;
  }

  try {
    const resp = await api(`/tasks/${taskNo}/result`);
    downloadLink.innerHTML = `<a href="${resp.data.download_url}" target="_blank" rel="noreferrer">下载 ${escapeHtml(resp.data.filename)}</a>`;
  } catch (error) {
    downloadLink.textContent = `获取下载链接失败: ${error.message}`;
  }
});

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

loadTaskList();
metricsView.loadMetrics();

