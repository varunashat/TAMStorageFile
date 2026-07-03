const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0
});

let rawData;

async function loadDashboard() {
  const r = await fetch('data/azure_cost.json', { cache: 'no-store' });
  if (!r.ok) throw new Error(`Unable to load data/azure_cost.json: ${r.status} ${r.statusText}`);

  rawData = await r.json();
  rawData.summary = rawData.summary || {};

  set('lastUpdated', rawData.generatedAt || '-');
  set('sourceBlob', rawData.sourceBlob ? `Source: ${rawData.sourceBlob}` : '');

  populateFilters(rawData);
  bindFilters();
  renderDashboard();
}

function populateFilters(d) {
  fillSelect('subscriptionFilter', d.costBySubscription || []);
  fillSelect('serviceFilter', d.costByService || []);
  fillSelect('resourceGroupFilter', d.costByResourceGroup || []);
}

function fillSelect(id, rows) {
  const s = document.getElementById(id);
  if (!s) return;

  rows.slice(0, 150).forEach(r => {
    const o = document.createElement('option');
    o.value = r.name;
    o.textContent = r.name;
    s.appendChild(o);
  });
}

function bindFilters() {
  ['subscriptionFilter', 'serviceFilter', 'resourceGroupFilter', 'resourceSearch'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', renderDashboard);
  });

  const resetBtn = document.getElementById('resetFilters');
  if (resetBtn) {
    resetBtn.onclick = () => {
      subscriptionFilter.value = 'all';
      serviceFilter.value = 'all';
      resourceGroupFilter.value = 'all';
      resourceSearch.value = '';
      renderDashboard();
    };
  }

  const exportBtn = document.getElementById('exportJson');
  if (exportBtn) {
    exportBtn.onclick = () => {
      const b = new Blob([JSON.stringify(rawData, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b);
      a.download = 'azure_cost_dashboard.json';
      a.click();
    };
  }
}

function filteredResources() {
  const sub = subscriptionFilter.value;
  const svc = serviceFilter.value;
  const rg = resourceGroupFilter.value;
  const search = resourceSearch.value.toLowerCase();

  return (rawData.topResources || []).filter(r =>
    (sub === 'all' || r.subscriptionName === sub) &&
    (svc === 'all' || r.service === svc) &&
    (rg === 'all' || r.resourceGroup === rg) &&
    (!search || `${r.resourceName || ''} ${r.resourceId || ''}`.toLowerCase().includes(search))
  );
}

function renderDashboard() {
  const d = rawData;
  const fr = filteredResources();
  const active = subscriptionFilter.value !== 'all' || serviceFilter.value !== 'all' || resourceGroupFilter.value !== 'all' || resourceSearch.value;
  const cost = active ? fr.reduce((s, r) => s + (r.cost || 0), 0) : d.summary.totalCost;
  const avg = average((d.dailyTrend || []).map(x => x.cost || 0));
  const maxDay = (d.dailyTrend || []).reduce((a, b) => (b.cost || 0) > (a.cost || 0) ? b : a, { date: '-', cost: 0 });

  set('totalCost', currency.format(cost || 0));
  set('subscriptionCount', d.summary.subscriptionCount || 0);
  set('resourceGroupCount', d.summary.resourceGroupCount || 0);
  set('resourceCount', d.summary.resourceCount || 0);
  set('avgDailyCost', currency.format(avg || 0));
  set('monthlyRunRate', currency.format((avg || 0) * 30));
  set('highestDay', `${maxDay.date} / ${currency.format(maxDay.cost || 0)}`);
  set('insightTopSub', topName(d.costBySubscription));
  set('insightTopSvc', topName(d.costByService));
  set('insightTopRg', topName(d.costByResourceGroup));

  renderBars('subscriptionChart', d.costBySubscription);
  renderBars('serviceChart', d.costByService);
  renderBars('resourceGroupChart', d.costByResourceGroup);
  renderBars('regionChart', d.costByRegion || [{ name: 'UAE North / Region data pending', cost: d.summary.totalCost || 0 }]);
  renderTrend('dailyTrendChart', d.dailyTrend || []);
  renderTable(active ? fr : d.topResources || []);
}

function renderBars(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;

  const data = (rows || []).filter(x => x && x.cost > 0).slice(0, 12);
  if (!data.length) {
    el.innerHTML = '<div class="empty-chart">No data available</div>';
    return;
  }

  const max = Math.max(...data.map(x => x.cost));
  el.innerHTML = data.map((r, i) => `
    <div class="bar-row">
      <div class="bar-label" title="${esc(r.name)}">${esc(r.name || 'Unassigned')}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max((r.cost / max) * 100, 4)}%;background:${color(i)}"></div></div>
      <div class="bar-value">${currency.format(r.cost)}</div>
    </div>`).join('');
}

function renderTrend(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;

  const data = (rows || []).filter(x => x && x.cost > 0);
  if (!data.length) {
    el.innerHTML = '<div class="empty-chart">No trend data available</div>';
    return;
  }

  const max = Math.max(...data.map(x => x.cost));
  el.innerHTML = data.map(x => `
    <div class="trend-col">
      <div class="trend-value">${currency.format(x.cost)}</div>
      <div class="trend-bar" style="height:${Math.max((x.cost / max) * 190, 12)}px"></div>
      <div class="trend-label">${esc(x.date)}</div>
    </div>`).join('');
}

function renderTable(rows) {
  set('tableInfo', `${rows.length} resources shown`);
  const body = document.getElementById('topResources');
  if (!body) return;

  body.innerHTML = rows.slice(0, 25).map(r => `
    <tr>
      <td>${esc(r.resourceName || r.resourceId || 'Unknown')}</td>
      <td><span class="pill">${esc(r.subscriptionName || '')}</span></td>
      <td>${esc(r.resourceGroup || '')}</td>
      <td>${esc(r.service || '')}</td>
      <td>${currency.format(r.cost || 0)}</td>
    </tr>`).join('');
}

function topName(rows) {
  const r = (rows || [])[0];
  return r ? `${r.name} (${currency.format(r.cost || 0)})` : '-';
}

function average(v) {
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0;
}

function set(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

function esc(v) {
  return String(v).replace(/[&<>'"]/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  }[ch]));
}

function color(i) {
  return ['#2563eb', '#06b6d4', '#7c3aed', '#16a34a', '#f59e0b', '#dc2626', '#0ea5e9', '#84cc16', '#f97316', '#64748b'][i % 10];
}

loadDashboard().catch(e => {
  console.error(e);
  set('lastUpdated', 'Failed to load data');
});
