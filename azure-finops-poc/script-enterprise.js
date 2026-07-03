const currency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0
});

let rawData;

async function loadDashboard() {
  const r = await fetch('data/azure_cost.json?t=' + Date.now(), { cache: 'no-store' });
  if (!r.ok) throw new Error(`Unable to load data/azure_cost.json: ${r.status} ${r.statusText}`);

  rawData = await r.json();
  rawData.summary = rawData.summary || {};

  set('lastUpdated', rawData.generatedAt || '-');
  set('sourceBlob', rawData.sourceBlobs ? `Sources: ${rawData.sourceBlobs.length} export(s)` : (rawData.sourceBlob ? `Source: ${rawData.sourceBlob}` : ''));

  populateClientFilter();
  populateMonthFilter(getBaseDataset());
  populateFilters(getSelectedDataset());
  bindFilters();
  renderDashboard();
}

function getBaseDataset() {
  const clientKey = document.getElementById('clientFilter')?.value || 'all';
  if (clientKey !== 'all' && rawData.clients && rawData.clients[clientKey]) return rawData.clients[clientKey];
  return rawData.overall || rawData;
}

function getSelectedDataset() {
  const base = getBaseDataset();
  const month = document.getElementById('monthFilter')?.value || 'all';
  if (month !== 'all' && base.months && base.months[month]) return base.months[month];
  return base;
}

function selectedCustomerCount() {
  const clientKey = document.getElementById('clientFilter')?.value || 'all';
  if (clientKey !== 'all') return 1;
  return rawData.clientList ? rawData.clientList.length : 1;
}

function populateClientFilter() {
  const s = document.getElementById('clientFilter');
  if (!s) return;
  s.innerHTML = '<option value="all">All Customers</option>';
  (rawData.clientList || []).forEach(c => {
    const o = document.createElement('option');
    o.value = c.key;
    o.textContent = c.name;
    s.appendChild(o);
  });
}

function populateMonthFilter(d) {
  const s = document.getElementById('monthFilter');
  if (!s) return;
  s.innerHTML = '<option value="all">All Months</option>';
  const months = d.monthList || rawData.monthList || [];
  months.forEach(m => {
    const o = document.createElement('option');
    o.value = m;
    o.textContent = formatMonth(m);
    s.appendChild(o);
  });
}

function populateFilters(d) {
  resetSelect('subscriptionFilter', 'All subscriptions');
  resetSelect('serviceFilter', 'All services');
  resetSelect('resourceGroupFilter', 'All resource groups');
  fillSelect('subscriptionFilter', d.costBySubscription || []);
  fillSelect('serviceFilter', d.costByService || []);
  fillSelect('resourceGroupFilter', d.costByResourceGroup || []);
}

function resetSelect(id, label) {
  const s = document.getElementById(id);
  if (!s) return;
  s.innerHTML = `<option value="all">${label}</option>`;
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
  const client = document.getElementById('clientFilter');
  if (client) client.addEventListener('input', () => {
    populateMonthFilter(getBaseDataset());
    monthFilter.value = 'all';
    populateFilters(getSelectedDataset());
    resourceSearch.value = '';
    renderDashboard();
  });

  const month = document.getElementById('monthFilter');
  if (month) month.addEventListener('input', () => {
    populateFilters(getSelectedDataset());
    resourceSearch.value = '';
    renderDashboard();
  });

  ['subscriptionFilter', 'serviceFilter', 'resourceGroupFilter', 'resourceSearch'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', renderDashboard);
  });

  const resetBtn = document.getElementById('resetFilters');
  if (resetBtn) {
    resetBtn.onclick = () => {
      clientFilter.value = 'all';
      populateMonthFilter(getBaseDataset());
      monthFilter.value = 'all';
      populateFilters(getSelectedDataset());
      subscriptionFilter.value = 'all';
      serviceFilter.value = 'all';
      resourceGroupFilter.value = 'all';
      resourceSearch.value = '';
      renderDashboard();
    };
  }

  const exportBtn = document.getElementById('exportJson');
  if (exportBtn) {
    exportBtn.textContent = 'Export Excel';
    exportBtn.onclick = exportCurrentViewCsv;
  }
}

function filteredResources(d) {
  const sub = subscriptionFilter.value;
  const svc = serviceFilter.value;
  const rg = resourceGroupFilter.value;
  const search = resourceSearch.value.toLowerCase();

  return (d.topResources || []).filter(r =>
    (sub === 'all' || r.subscriptionName === sub) &&
    (svc === 'all' || r.service === svc) &&
    (rg === 'all' || r.resourceGroup === rg) &&
    (!search || `${r.resourceName || ''} ${r.resourceId || ''}`.toLowerCase().includes(search))
  );
}

function currentRows() {
  const d = getSelectedDataset();
  const active = subscriptionFilter.value !== 'all' || serviceFilter.value !== 'all' || resourceGroupFilter.value !== 'all' || resourceSearch.value;
  return active ? filteredResources(d) : (d.topResources || []);
}

function renderDashboard() {
  const d = getSelectedDataset();
  const fr = filteredResources(d);
  const active = subscriptionFilter.value !== 'all' || serviceFilter.value !== 'all' || resourceGroupFilter.value !== 'all' || resourceSearch.value;
  const cost = active ? fr.reduce((s, r) => s + (r.cost || 0), 0) : d.summary.totalCost;
  const avg = average((d.dailyTrend || []).map(x => x.cost || 0));
  const maxDay = (d.dailyTrend || []).reduce((a, b) => (b.cost || 0) > (a.cost || 0) ? b : a, { date: '-', cost: 0 });

  set('totalCost', currency.format(cost || 0));
  set('clientCount', selectedCustomerCount());
  set('subscriptionCount', d.summary.subscriptionCount || 0);
  set('resourceGroupCount', d.summary.resourceGroupCount || 0);
  set('resourceCount', d.summary.resourceCount || 0);
  set('avgDailyCost', currency.format(avg || 0));
  set('monthlyRunRate', currency.format((avg || 0) * 30));
  set('highestDay', `${maxDay.date} / ${currency.format(maxDay.cost || 0)}`);
  set('insightTopClient', topName(d.costByClient || rawData.costByClient));
  set('insightTopSub', topName(d.costBySubscription));
  set('insightTopSvc', topName(d.costByService));
  set('insightTopRg', topName(d.costByResourceGroup));

  renderBars('clientChart', d.costByClient || rawData.costByClient || []);
  renderBars('subscriptionChart', d.costBySubscription);
  renderBars('serviceChart', d.costByService);
  renderBars('resourceGroupChart', d.costByResourceGroup);
  renderBars('regionChart', d.costByRegion || [{ name: 'Region data pending', cost: d.summary.totalCost || 0 }]);
  renderTrend('dailyTrendChart', d.dailyTrend || []);
  renderTable(active ? fr : d.topResources || []);
}

function exportCurrentViewCsv() {
  const rows = currentRows();
  const headers = ['Customer', 'Month', 'Resource', 'Resource ID', 'Subscription', 'Resource Group', 'Service', 'Region', 'Cost'];
  const body = rows.map(r => [
    r.clientName || selectedClientName(),
    formatMonth(r.month || monthFilter.value || ''),
    r.resourceName || '',
    r.resourceId || '',
    r.subscriptionName || '',
    r.resourceGroup || '',
    r.service || '',
    r.region || '',
    Number(r.cost || 0).toFixed(2)
  ]);
  const csv = [headers, ...body].map(row => row.map(csvCell).join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `azure_finops_${selectedClientName().replace(/[^a-z0-9]+/gi, '_')}_${monthFilter.value || 'all_months'}.csv`;
  a.click();
}

function selectedClientName() {
  const s = document.getElementById('clientFilter');
  return s?.selectedOptions?.[0]?.textContent || 'All Customers';
}

function csvCell(value) {
  const text = String(value ?? '');
  return `"${text.replace(/"/g, '""')}"`;
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
      <td><span class="pill">${esc(r.clientName || '')}</span></td>
      <td>${esc(formatMonth(r.month || ''))}</td>
      <td>${esc(r.resourceName || r.resourceId || 'Unknown')}</td>
      <td>${esc(r.subscriptionName || '')}</td>
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

function formatMonth(m) {
  if (!m || m === 'Unknown' || m === 'all') return m === 'all' ? 'All Months' : (m || '');
  const parts = m.split('-');
  if (parts.length !== 2) return m;
  const date = new Date(Number(parts[0]), Number(parts[1]) - 1, 1);
  return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}

function set(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}

function esc(v) {
  return String(v || '').replace(/[&<>'"]/g, ch => ({
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
