const DEFAULT_LIMIT = 100;
const QUALITY_FLAG_LABELS = {
  overflow: 'overflow',
  collision: 'collision',
  empty_space: 'empty_space',
  alignment_risk: 'alignment_risk',
  density_imbalance: 'density_imbalance',
  title_consistency: 'title_consistency',
};

function normalizeTaskNo(value) {
  return String(value || '').trim();
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function renderEmpty(panel, message) {
  panel.innerHTML = `<div class="task-list-empty">${message}</div>`;
}

function renderStepHighlights(step, escapeHtml) {
  if (!step || typeof step !== 'object') {
    return '';
  }

  const output = (step.output_json && typeof step.output_json === 'object') ? step.output_json : {};
  const code = String(step.step_code || '');

  if (code === 'analyze_template') {
    const analysisSource = output.analysis_source || '-';
    const parseSource = output.template_parse_source || output.analysis_source || '-';
    const llmEnhanced = output.llm_enhanced === true ? 'yes' : 'no';
    const llmModel = output.llm_model || '-';
    const pageSchemas = output.page_schemas_count ?? '-';
    const batchTotal = output.llm_batches_total ?? 0;
    const batchSucceeded = output.llm_batches_succeeded ?? 0;

    return `
      <div class="replay-highlight-grid">
        <div class="replay-highlight-card">
          <div class="metric-label">template_analysis</div>
          <div class="replay-highlight-line"><span>analysis_source</span><b>${escapeHtml(analysisSource)}</b></div>
          <div class="replay-highlight-line"><span>parse_source</span><b>${escapeHtml(parseSource)}</b></div>
          <div class="replay-highlight-line"><span>llm_enhanced</span><b>${escapeHtml(llmEnhanced)}</b></div>
          <div class="replay-highlight-line"><span>llm_model</span><b>${escapeHtml(llmModel)}</b></div>
          <div class="replay-highlight-line"><span>llm_batches</span><b>${escapeHtml(`${batchSucceeded}/${batchTotal}`)}</b></div>
          <div class="replay-highlight-line"><span>page_schemas</span><b>${escapeHtml(pageSchemas)}</b></div>
        </div>
      </div>
    `;
  }

  if (code === 'self_correct') {
    const qualityReport = (output.quality_report && typeof output.quality_report === 'object') ? output.quality_report : output;
    const riskScore = qualityReport.risk_score ?? '-';
    const activeFlags = Object.keys(QUALITY_FLAG_LABELS).filter((flag) => Boolean(qualityReport[flag]));
    const flagsHtml = activeFlags.length
      ? activeFlags.map((flag) => `<span class="replay-flag">${escapeHtml(QUALITY_FLAG_LABELS[flag])}</span>`).join('')
      : '<span class="replay-flag muted">none</span>';

    return `
      <div class="replay-highlight-grid">
        <div class="replay-highlight-card">
          <div class="metric-label">quality_report</div>
          <div class="replay-highlight-line"><span>risk_score</span><b>${escapeHtml(riskScore)}</b></div>
          <div class="replay-flag-row">${flagsHtml}</div>
        </div>
      </div>
    `;
  }

  return '';
}

function formatReplayJson(value) {
  if (value === undefined || value === null) {
    return '{}';
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function renderReplay(panel, data, escapeHtml) {
  const task = data?.task || {};
  const steps = Array.isArray(data?.steps) ? data.steps : [];
  const events = Array.isArray(data?.events) ? data.events : [];
  const nextCursor = data?.next_cursor ?? null;

  const stepsHtml = steps.length
    ? steps
        .map((step) => {
          const errorMessage = step.error_message || '-';
          const errorCode = step.error_code || '-';
          const startedAt = formatDateTime(step.started_at);
          const finishedAt = formatDateTime(step.finished_at);
          return `
            <div class="replay-item">
              <div class="replay-item-head">
                <div class="replay-item-title">Step ${escapeHtml(step.step_order)} - ${escapeHtml(step.step_code)}</div>
                <span class="badge status-${escapeHtml(step.step_status)}">${escapeHtml(step.step_status)}</span>
              </div>
              <div class="replay-item-meta">
                <span>duration ${escapeHtml(step.duration_ms ?? '-')} ms</span>
                <span>error ${escapeHtml(errorCode)}</span>
                <span>${escapeHtml(startedAt)} → ${escapeHtml(finishedAt)}</span>
              </div>
              ${renderStepHighlights(step, escapeHtml)}
              <div class="replay-item-grid">
                <div><div class="metric-label">input</div><pre>${escapeHtml(formatReplayJson(step.input_json ?? {}))}</pre></div>
                <div><div class="metric-label">output</div><pre>${escapeHtml(formatReplayJson(step.output_json ?? {}))}</pre></div>
              </div>
              <div class="replay-error">${escapeHtml(errorMessage)}</div>
            </div>
          `;
        })
        .join('')
    : '<div class="task-list-empty">暂无步骤记录。</div>';

  const eventsHtml = events.length
    ? events
        .map((event) => `
          <div class="replay-event">
            <div class="replay-event-head">
              <span class="replay-event-type">${escapeHtml(event.event_type)}</span>
              <span class="replay-event-time">${escapeHtml(formatDateTime(event.event_time))}</span>
            </div>
            <div class="replay-event-message">${escapeHtml(event.message || '-')}</div>
          </div>
        `)
        .join('')
    : '<div class="task-list-empty">暂无事件记录。</div>';

  panel.innerHTML = `
    <div class="task-detail-grid">
      <div class="metric">
        <div class="metric-label">task_no</div>
        <div class="metric-value">${escapeHtml(task.task_no || '-')}</div>
      </div>
      <div class="metric">
        <div class="metric-label">status</div>
        <div class="metric-value">${escapeHtml(task.status || '-')}</div>
      </div>
      <div class="metric">
        <div class="metric-label">current_step</div>
        <div class="metric-value">${escapeHtml(task.current_step || '-')}</div>
      </div>
      <div class="metric">
        <div class="metric-label">progress</div>
        <div class="metric-value">${escapeHtml(task.progress ?? '-')}%</div>
      </div>
      <div class="metric">
        <div class="metric-label">detail_level</div>
        <div class="metric-value">${escapeHtml(task.detail_level || '-')}</div>
      </div>
      <div class="metric">
        <div class="metric-label">next_cursor</div>
        <div class="metric-value">${escapeHtml(nextCursor ?? '-')}</div>
      </div>
    </div>
    <div class="replay-section">
      <div class="metric-label">step timeline</div>
      <div class="replay-list">${stepsHtml}</div>
    </div>
    <div class="replay-section">
      <div class="metric-label">events</div>
      <div class="replay-list">${eventsHtml}</div>
    </div>
  `;
}

export function initReplayView(options) {
  const {
    api,
    escapeHtml,
    taskNoInput,
    replayPanel,
    replayStatus,
    replayRefreshBtn,
  } = options || {};

  if (
    typeof api !== 'function' ||
    typeof escapeHtml !== 'function' ||
    !taskNoInput ||
    !replayPanel ||
    !replayStatus ||
    !replayRefreshBtn
  ) {
    throw new Error('initReplayView requires api, escapeHtml, taskNoInput, replayPanel, replayStatus, and replayRefreshBtn');
  }

  let loading = false;

  async function loadReplay() {
    const taskNo = normalizeTaskNo(taskNoInput.value);
    if (!taskNo) {
      renderEmpty(replayPanel, '请先填写 task_no 后再查看回放。');
      replayStatus.textContent = '等待 task_no';
      return;
    }

    if (loading) {
      return;
    }

    loading = true;
    replayRefreshBtn.disabled = true;
    replayStatus.textContent = '回放加载中...';
    replayPanel.innerHTML = '<div class="preview-loading">正在加载任务回放，请稍候。</div>';

    try {
      const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/replay?limit=${DEFAULT_LIMIT}`);
      renderReplay(replayPanel, resp.data, escapeHtml);
      replayStatus.textContent = `回放加载成功，steps ${Array.isArray(resp.data?.steps) ? resp.data.steps.length : 0} 条，events ${Array.isArray(resp.data?.events) ? resp.data.events.length : 0} 条。`;
    } catch (error) {
      replayPanel.innerHTML = `<div class="preview-empty">回放加载失败：${escapeHtml(error.message)}</div>`;
      replayStatus.textContent = '回放加载失败';
    } finally {
      loading = false;
      replayRefreshBtn.disabled = false;
    }
  }

  replayRefreshBtn.addEventListener('click', loadReplay);

  return {
    refresh: loadReplay,
    destroy() {
      replayRefreshBtn.removeEventListener('click', loadReplay);
    },
  };
}
