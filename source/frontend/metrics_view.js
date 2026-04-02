const DEFAULT_EMPTY_TEXT = '暂无指标数据';

function clampDays(value) {
  const parsed = Number.parseInt(String(value ?? '').trim(), 10);
  if (!Number.isFinite(parsed) || Number.isNaN(parsed) || parsed < 1) {
    return null;
  }
  return Math.min(parsed, 365);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatDuration(value) {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  return `${value} ms`;
}

function createCard(title, value, tone = '') {
  return `
    <div class="metric-card ${tone}">
      <div class="metric-card-title">${title}</div>
      <div class="metric-card-value">${value}</div>
    </div>
  `;
}

function renderErrorCodeList(errorCodeTop, escapeHtml) {
  if (!Array.isArray(errorCodeTop) || errorCodeTop.length === 0) {
    return `<div class="metric-empty">${DEFAULT_EMPTY_TEXT}</div>`;
  }

  return `
    <ul class="metric-error-list">
      ${errorCodeTop
        .map(
          (item) => `
            <li class="metric-error-item">
              <span class="metric-error-code">${escapeHtml(item.error_code ?? '-')}</span>
              <span class="metric-error-count">${escapeHtml(item.count ?? 0)}</span>
            </li>
          `,
        )
        .join('')}
    </ul>
  `;
}

function renderQualityFlagList(qualityFlagsTop, escapeHtml) {
  if (!Array.isArray(qualityFlagsTop) || qualityFlagsTop.length === 0) {
    return `<div class="metric-empty">${DEFAULT_EMPTY_TEXT}</div>`;
  }

  return `
    <ul class="metric-error-list">
      ${qualityFlagsTop
        .map(
          (item) => `
            <li class="metric-error-item">
              <span class="metric-error-code">${escapeHtml(item.signal ?? '-')}</span>
              <span class="metric-error-count">${escapeHtml(item.count ?? 0)}</span>
            </li>
          `,
        )
        .join('')}
    </ul>
  `;
}

function renderMetrics(metrics, escapeHtml) {
  const cards = [
    createCard('total', escapeHtml(metrics.total_tasks ?? 0)),
    createCard('success', escapeHtml(metrics.success_tasks ?? 0), 'success'),
    createCard('failed', escapeHtml(metrics.failed_tasks ?? 0), 'danger'),
    createCard('canceled', escapeHtml(metrics.canceled_tasks ?? 0), 'muted'),
    createCard('success_rate', escapeHtml(formatPercent(metrics.success_rate))),
    createCard('p50', escapeHtml(formatDuration(metrics.p50_duration_ms))),
    createCard('p95', escapeHtml(formatDuration(metrics.p95_duration_ms))),
  ].join('');

  const qualityCards = [
    createCard('self_correct_coverage', escapeHtml(formatPercent(metrics.self_correct_coverage)), 'success'),
    createCard('avg_quality_risk', escapeHtml(formatPercent(metrics.avg_quality_risk)), 'danger'),
    createCard('high_risk_tasks', escapeHtml(metrics.high_risk_tasks ?? 0), 'muted'),
  ].join('');

  return `
    <div class="metric-grid">
      ${cards}
    </div>
    <div class="metric-grid">
      ${qualityCards}
    </div>
    <div class="metric-section">
      <div class="metric-section-title">error_code_top</div>
      ${renderErrorCodeList(metrics.error_code_top, escapeHtml)}
    </div>
    <div class="metric-section">
      <div class="metric-section-title">quality_flags_top</div>
      ${renderQualityFlagList(metrics.quality_flags_top, escapeHtml)}
    </div>
  `;
}

function setLoading(metricsPanel, metricsStatus) {
  metricsStatus.textContent = '加载中...';
  metricsPanel.innerHTML = '<div class="metric-loading">正在拉取指标，请稍候。</div>';
}

function setError(metricsPanel, metricsStatus, escapeHtml, message) {
  metricsStatus.textContent = '加载失败';
  metricsPanel.innerHTML = `<div class="metric-error">${escapeHtml(message)}</div>`;
}

function setEmpty(metricsPanel, metricsStatus) {
  metricsStatus.textContent = '暂无数据';
  metricsPanel.innerHTML = `<div class="metric-empty">${DEFAULT_EMPTY_TEXT}</div>`;
}

export function initMetricsView(options) {
  const {
    api,
    escapeHtml,
    metricsPanel,
    metricsStatus,
    metricsRefreshBtn,
    metricsDaysInput,
  } = options || {};

  if (
    typeof api !== 'function' ||
    typeof escapeHtml !== 'function' ||
    !metricsPanel ||
    !metricsStatus ||
    !metricsRefreshBtn ||
    !metricsDaysInput
  ) {
    throw new Error('initMetricsView requires api, escapeHtml, metricsPanel, metricsStatus, metricsRefreshBtn, metricsDaysInput');
  }

  let currentRequestId = 0;

  const loadMetrics = async () => {
    const days = clampDays(metricsDaysInput.value);
    if (days === null) {
      setError(metricsPanel, metricsStatus, escapeHtml, '请输入 1 到 365 之间的天数');
      return;
    }

    const requestId = ++currentRequestId;
    setLoading(metricsPanel, metricsStatus);

    try {
      const resp = await api(`/metrics/overview?days=${days}`);
      if (requestId !== currentRequestId) {
        return;
      }

      const data = resp?.data || {};
      metricsStatus.textContent = `最近 ${days} 天指标`;
      metricsPanel.innerHTML = renderMetrics(data, escapeHtml);
      if (
        !data.total_tasks &&
        !data.success_tasks &&
        !data.failed_tasks &&
        !data.canceled_tasks
      ) {
        metricsPanel.insertAdjacentHTML('beforeend', `<div class="metric-empty">${DEFAULT_EMPTY_TEXT}</div>`);
      }
    } catch (error) {
      if (requestId !== currentRequestId) {
        return;
      }
      setError(metricsPanel, metricsStatus, escapeHtml, error?.message || '指标加载失败');
    }
  };

  metricsRefreshBtn.addEventListener('click', loadMetrics);
  metricsDaysInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      loadMetrics();
    }
  });

  metricsDaysInput.addEventListener('change', () => {
    const normalized = clampDays(metricsDaysInput.value);
    if (normalized !== null) {
      metricsDaysInput.value = String(normalized);
    }
  });

  setEmpty(metricsPanel, metricsStatus);

  return {
    loadMetrics,
  };
}
