function normalizeTaskNo(value) {
  return String(value || '').trim();
}

function formatPercent(value) {
  if (value === undefined || value === null || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function renderTopicWeights(topicWeights, escapeHtml) {
  const entries = Object.entries(topicWeights || {});
  if (!entries.length) {
    return '<div class="task-list-empty">暂无 topic 权重</div>';
  }
  return entries
    .map(([topic, score]) => `<div class="obs-row"><span>${escapeHtml(topic)}</span><b>${escapeHtml(score)}</b></div>`)
    .join('');
}

function renderTopChunks(chunks, escapeHtml) {
  if (!Array.isArray(chunks) || chunks.length === 0) {
    return '<div class="task-list-empty">暂无检索片段</div>';
  }
  return chunks
    .map((item, index) => {
      const excerpt = item?.excerpt || '';
      return `
        <div class="obs-chunk">
          <div class="obs-row"><span>Top ${index + 1}</span><b>score ${escapeHtml(item?.score ?? '-')}</b></div>
          <div class="obs-chunk-text">${escapeHtml(excerpt || '-')}</div>
        </div>
      `;
    })
    .join('');
}

function renderObservability(panel, data, escapeHtml) {
  const prompt = data?.prompt_observability || {};
  const rag = data?.rag_observability || {};
  const generation = data?.generation_observability || {};
  const quality = data?.quality_observability || {};
  const stepSources = data?.step_sources || {};
  const stepAudits = data?.step_audits || {};
  const qualityFlags = quality?.quality_flags || {};

  const activeFlags = Object.keys(qualityFlags).filter((key) => Boolean(qualityFlags[key]));
  const flagsHtml = activeFlags.length
    ? activeFlags.map((flag) => `<span class="replay-flag">${escapeHtml(flag)}</span>`).join('')
    : '<span class="replay-flag muted">none</span>';

  panel.innerHTML = `
    <div class="task-detail-grid">
      <div class="metric"><div class="metric-label">task_no</div><div class="metric-value">${escapeHtml(data?.task_no || '-')}</div></div>
      <div class="metric"><div class="metric-label">detail_level</div><div class="metric-value">${escapeHtml(data?.detail_level || '-')}</div></div>
      <div class="metric"><div class="metric-label">rag_enabled</div><div class="metric-value">${escapeHtml(String(Boolean(data?.rag_enabled)))}</div></div>
      <div class="metric"><div class="metric-label">latest_attempt_no</div><div class="metric-value">${escapeHtml(data?.latest_attempt_no ?? '-')}</div></div>
      <div class="metric" style="grid-column: 1 / -1;"><div class="metric-label">user_prompt</div><div class="metric-value">${escapeHtml(data?.user_prompt || '-')}</div></div>
    </div>

    <div class="obs-grid">
      <section class="obs-card">
        <div class="metric-label">Prompt/RAG 行为</div>
        <div class="obs-row"><span>query_source</span><b>${escapeHtml(prompt?.query_source || '-')}</b></div>
        <div class="obs-row"><span>fallback_text_used</span><b>${escapeHtml(String(Boolean(prompt?.fallback_text_used)))}</b></div>
        <div class="obs-row"><span>rag_context_count</span><b>${escapeHtml(prompt?.rag_context_count ?? '-')}</b></div>
        <div class="obs-row"><span>source_text_chars</span><b>${escapeHtml(prompt?.source_text_chars ?? '-')}</b></div>
        <div class="obs-row"><span>retrieval_query</span><b>${escapeHtml(prompt?.retrieval_query || '-')}</b></div>
      </section>

      <section class="obs-card">
        <div class="metric-label">检索命中</div>
        <div class="obs-row"><span>chunks</span><b>${escapeHtml(rag?.retrieved_chunks_count ?? 0)}</b></div>
        <div class="obs-row"><span>citations</span><b>${escapeHtml(rag?.citations_count ?? 0)}</b></div>
        ${renderTopChunks(rag?.top_chunks || [], escapeHtml)}
      </section>

      <section class="obs-card">
        <div class="metric-label">主题权重</div>
        ${renderTopicWeights(rag?.topic_weights || {}, escapeHtml)}
      </section>

      <section class="obs-card">
        <div class="metric-label">生成策略</div>
        <div class="obs-row"><span>map_llm_suggestions</span><b>${escapeHtml(`${generation?.map_llm_suggestions_applied ?? 0}/${generation?.map_llm_suggestions_total ?? 0}`)}</b></div>
        <div class="obs-row"><span>mapped_slide_count</span><b>${escapeHtml(generation?.mapped_slide_count ?? 0)}</b></div>
        <div class="obs-row"><span>slot_fill_count</span><b>${escapeHtml(generation?.slot_fill_count ?? 0)}</b></div>
        <div class="obs-row"><span>split_pages</span><b>${escapeHtml(generation?.text_overflow_strategy?.split_pages ?? 0)}</b></div>
        <div class="obs-row"><span>summary_applied</span><b>${escapeHtml(generation?.text_overflow_strategy?.summary_applied ?? 0)}</b></div>
      </section>

      <section class="obs-card">
        <div class="metric-label">质量门控</div>
        <div class="obs-row"><span>metric_version</span><b>${escapeHtml(quality?.metric_version || '-')}</b></div>
        <div class="obs-row"><span>pass_flag</span><b>${escapeHtml(quality?.pass_flag ?? '-')}</b></div>
        <div class="obs-row"><span>style_fidelity</span><b>${escapeHtml(quality?.style_fidelity_score ?? '-')}</b></div>
        <div class="obs-row"><span>text_slot_match</span><b>${escapeHtml(formatPercent(quality?.text_slot_match_rate))}</b></div>
        <div class="obs-row"><span>image_slot_match</span><b>${escapeHtml(formatPercent(quality?.image_slot_match_rate))}</b></div>
        <div class="obs-row"><span>table_slot_match</span><b>${escapeHtml(formatPercent(quality?.table_slot_match_rate))}</b></div>
        <div class="replay-flag-row">${flagsHtml}</div>
      </section>

      <section class="obs-card">
        <div class="metric-label">步骤来源与审计</div>
        ${Object.keys(stepSources)
          .map((step) => {
            const audit = stepAudits?.[step] || {};
            return `
              <div class="obs-step">
                <div class="obs-row"><span>${escapeHtml(step)}</span><b>${escapeHtml(stepSources[step] || '-')}</b></div>
                <div class="obs-row small"><span>llm_used</span><b>${escapeHtml(String(Boolean(audit?.llm_used)))}</b></div>
                <div class="obs-row small"><span>fallback_used</span><b>${escapeHtml(String(Boolean(audit?.fallback_used)))}</b></div>
              </div>
            `;
          })
          .join('')}
      </section>
    </div>
  `;
}

export function initObservabilityView(options) {
  const {
    api,
    escapeHtml,
    taskNoInput,
    panel,
    statusNode,
    refreshBtn,
  } = options || {};

  if (
    typeof api !== 'function' ||
    typeof escapeHtml !== 'function' ||
    !taskNoInput ||
    !panel ||
    !statusNode ||
    !refreshBtn
  ) {
    throw new Error('initObservabilityView requires api, escapeHtml, taskNoInput, panel, statusNode, and refreshBtn');
  }

  let loading = false;
  async function loadObservability() {
    const taskNo = normalizeTaskNo(taskNoInput.value);
    if (!taskNo) {
      panel.innerHTML = '<div class="task-list-empty">请先填写 task_no，再查看可观测面板。</div>';
      statusNode.textContent = '等待 task_no';
      return;
    }
    if (loading) {
      return;
    }

    loading = true;
    refreshBtn.disabled = true;
    statusNode.textContent = '加载中...';
    panel.innerHTML = '<div class="preview-loading">正在加载可观测数据...</div>';
    try {
      const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/observability`);
      renderObservability(panel, resp.data, escapeHtml);
      statusNode.textContent = '已刷新';
    } catch (error) {
      panel.innerHTML = `<div class="preview-empty">加载失败：${escapeHtml(error.message)}</div>`;
      statusNode.textContent = '加载失败';
    } finally {
      loading = false;
      refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', loadObservability);
  return {
    refresh: loadObservability,
    destroy() {
      refreshBtn.removeEventListener('click', loadObservability);
    },
  };
}

