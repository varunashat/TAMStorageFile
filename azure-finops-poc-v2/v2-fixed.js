const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const colors = ['#2563eb','#06b6d4','#7c3aed','#16a34a','#f97316','#dc2626','#0ea5e9','#84cc16','#f59e0b','#64748b'];
let data = null;

const dataUrls = [
  '/TAMStorageFile/azure-finops-poc/data/azure_cost.json',
  '../azure-finops-poc/data/azure_cost.json',
  'https://varunashat.github.io/TAMStorageFile/azure-finops-poc/data/azure_cost.json'
];

window.addEventListener('DOMContentLoaded', init);

async function init() {
  try {
    set('lastUpdated', 'Loading data...');
    data = await loadJson();
    normalizeData();
    set('lastUpdated', data.generatedAt || 'Loaded');
    set('sourceBlob', data.sourceBlob ? `Source: ${data.sourceBlob}` : 'Source: azure_cost.json');
    populateFilters();
    bindEvents();
    render();
  } catch (error) {
    console.error('V2 dashboard load failed:', error);
    set('lastUpdated', 'Failed to load data');
    set('sourceBlob', String(error.message || error));
  }
}

async function loadJson() {
  let lastError;
  for (const url of dataUrls) {
    try {
      const response = await fetch(`${url}?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`${url} returned ${response.status}`);
      return await response.json();
    } catch (error) {
      lastError = error;
      console.warn('Data URL failed:', url, error);
    }
  }
  throw lastError || new Error('No data URL worked');
}

function normalizeData() {
  data.summary = data.summary || {};
  data.costBySubscription = data.costBySubscription || [];
  data.costByService = data.costByService || [];
  data.costByResourceGroup = data.costByResourceGroup || [];
  data.costByRegion = data.costByRegion || [];
  data.dailyTrend = data.dailyTrend || [];
  data.topResources = data.topResources || [];
}

function populateFilters() {
  fillSelect('subscriptionFilter', data.costBySubscription);
  fillSelect('serviceFilter', data.costByService);
  fillSelect('resourceGroupFilter', data.costByResourceGroup);
}

function fillSelect(id, rows) {
  const select = get(id);
  if (!select) return;
  const existing = new Set([...select.options].map(o => o.value));
  (rows || []).slice(0, 150).forEach(row => {
    if (!row || !row.name || existing.has(row.name)) return;
    const option = document.createElement('option');
    option.value = row.name;
    option.textContent = row.name;
    select.appendChild(option);
  });
}

function bindEvents() {
  ['subscriptionFilter','serviceFilter','resourceGroupFilter','resourceSearch'].forEach(id => {
    const el = get(id);
    if (el) el.addEventListener('input', render);
  });
  const reset = get('resetFilters');
  if (reset) reset.onclick = () => {
    get('subscriptionFilter').value = 'all';
    get('serviceFilter').value = 'all';
    get('resourceGroupFilter').value = 'all';
    get('resourceSearch').value = '';
    render();
  };
  const exportButton = get('exportJson');
  if (exportButton) exportButton.onclick = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'azure_finops_v2_data.json';
    a.click();
  };
}

function filteredResources() {
  const sub = get('subscriptionFilter')?.value || 'all';
  const svc = get('serviceFilter')?.value || 'all';
  const rg = get('resourceGroupFilter')?.value || 'all';
  const query = (get('resourceSearch')?.value || '').toLowerCase();
  return (data.topResources || []).filter(r =>
    (sub === 'all' || r.subscriptionName === sub) &&
    (svc === 'all' || r.service === svc) &&
    (rg === 'all' || r.resourceGroup === rg) &&
    (!query || `${r.resourceName || ''} ${r.resourceId || ''}`.toLowerCase().includes(query))
  );
}

function render() {
  if (!data) return;
  const active = (get('subscriptionFilter')?.value || 'all') !== 'all' ||
    (get('serviceFilter')?.value || 'all') !== 'all' ||
    (get('resourceGroupFilter')?.value || 'all') !== 'all' ||
    !!(get('resourceSearch')?.value || '');
  const resources = filteredResources();
  const total = active ? sum(resources.map(r => r.cost || 0)) : (data.summary.totalCost || 0);
  const avgDaily = average((data.dailyTrend || []).map(d => d.cost || 0));
  const highestDay = (data.dailyTrend || []).reduce((a, b) => (b.cost || 0) > (a.cost || 0) ? b : a, { date: '-', cost: 0 });

  set('totalCost', currency.format(total));
  set('subscriptionCount', data.summary.subscriptionCount || data.costBySubscription.length || 0);
  set('resourceGroupCount', data.summary.resourceGroupCount || data.costByResourceGroup.length || 0);
  set('resourceCount', data.summary.resourceCount || data.topResources.length || 0);
  set('avgDailyCost', currency.format(avgDaily));
  set('monthlyRunRate', currency.format(avgDaily * 30));
  set('topSubscription', topLabel(data.costBySubscription));
  set('topService', topLabel(data.costByService));
  set('topResourceGroup', topLabel(data.costByResourceGroup));
  set('highestDay', `${highestDay.date} / ${currency.format(highestDay.cost || 0)}`);

  renderBars('subscriptionChart', data.costBySubscription);
  renderBars('serviceChart', data.costByService);
  renderBars('resourceGroupChart', data.costByResourceGroup);
  renderBars('regionChart', data.costByRegion.length ? data.costByRegion : [{ name: 'Region data pending', cost: data.summary.totalCost || 0 }]);
  renderTrend('dailyTrendChart', data.dailyTrend);
  renderTable(active ? resources : data.topResources);
}

function renderBars(id, rows) {
  const el = get(id);
  if (!el) return;
  const items = (rows || []).filter(x => x && Number(x.cost) > 0).slice(0, 12);
  if (!items.length) { el.innerHTML = '<div class="empty">No data available</div>'; return; }
  const max = Math.max(...items.map(x => Number(x.cost) || 0));
  el.innerHTML = items.map((r, i) => `
    <div class="barRow">
      <div class="barLabel" title="${escapeHtml(r.name)}">${escapeHtml(r.name || 'Unassigned')}</div>
      <div class="barTrack"><div class="barFill" style="width:${Math.max(((Number(r.cost)||0)/max)*100,4)}%;background:${colors[i % colors.length]}"></div></div>
      <div class="barValue">${currency.format(Number(r.cost) || 0)}</div>
    </div>`).join('');
}

function renderTrend(id, rows) {
  const el = get(id);
  if (!el) return;
  const items = (rows || []).filter(x => x && Number(x.cost) > 0);
  if (!items.length) { el.innerHTML = '<div class="empty">No trend data available</div>'; return; }
  const max = Math.max(...items.map(x => Number(x.cost) || 0));
  el.innerHTML = items.map(x => `
    <div class="trendCol">
      <div class="trendValue">${currency.format(Number(x.cost) || 0)}</div>
      <div class="trendBar" style="height:${Math.max(((Number(x.cost)||0)/max)*210,14)}px"></div>
      <div class="trendLabel">${escapeHtml(x.date)}</div>
    </div>`).join('');
}

function renderTable(rows) {
  const list = (rows || []).slice(0, 25);
  set('tableInfo', `${list.length} rows shown`);
  const body = get('topResources');
  if (!body) return;
  body.innerHTML = list.map(r => `
    <tr>
      <td>${escapeHtml(r.resourceName || r.resourceId || 'Unknown')}</td>
      <td><span class="pill">${escapeHtml(r.subscriptionName || '')}</span></td>
      <td>${escapeHtml(r.resourceGroup || '')}</td>
      <td>${escapeHtml(r.service || '')}</td>
      <td>${currency.format(Number(r.cost) || 0)}</td>
    </tr>`).join('');
}

function topLabel(rows) {
  const row = (rows || [])[0];
  return row ? `${row.name} (${currency.format(Number(row.cost) || 0)})` : '-';
}
function sum(values) { return values.reduce((a, b) => a + (Number(b) || 0), 0); }
function average(values) { return values.length ? sum(values) / values.length : 0; }
function get(id) { return document.getElementById(id); }
function set(id, value) { const el = get(id); if (el) el.textContent = value; }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
