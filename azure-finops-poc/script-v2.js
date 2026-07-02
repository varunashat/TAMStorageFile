const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
let rawData;
let charts = [];

async function loadDashboard() {
  const response = await fetch('data/azure_cost.json', { cache: 'no-store' });
  rawData = await response.json();
  document.getElementById('lastUpdated').textContent = rawData.generatedAt || '-';
  document.getElementById('sourceBlob').textContent = rawData.sourceBlob ? `Source: ${rawData.sourceBlob}` : '';
  populateFilters(rawData);
  bindFilters();
  renderDashboard(rawData);
}

function populateFilters(data) {
  fillSelect('subscriptionFilter', data.costBySubscription || []);
  fillSelect('serviceFilter', data.costByService || []);
  fillSelect('resourceGroupFilter', data.costByResourceGroup || []);
}
function fillSelect(id, rows) {
  const select = document.getElementById(id);
  rows.slice(0, 100).forEach(r => {
    const option = document.createElement('option');
    option.value = r.name;
    option.textContent = r.name;
    select.appendChild(option);
  });
}
function bindFilters() {
  ['subscriptionFilter','serviceFilter','resourceGroupFilter','resourceSearch'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => renderDashboard(rawData));
  });
}

function applyResourceFilters(resources) {
  const sub = document.getElementById('subscriptionFilter').value;
  const service = document.getElementById('serviceFilter').value;
  const rg = document.getElementById('resourceGroupFilter').value;
  const search = document.getElementById('resourceSearch').value.toLowerCase();
  return (resources || []).filter(r =>
    (sub === 'all' || r.subscriptionName === sub) &&
    (service === 'all' || r.service === service) &&
    (rg === 'all' || r.resourceGroup === rg) &&
    (!search || `${r.resourceName || ''} ${r.resourceId || ''}`.toLowerCase().includes(search))
  );
}

function renderDashboard(data) {
  const filteredResources = applyResourceFilters(data.topResources || []);
  const filteredCost = filteredResources.length ? filteredResources.reduce((s,r)=>s+(r.cost||0),0) : data.summary.totalCost;
  const avgDaily = average((data.dailyTrend || []).map(x => x.cost || 0));
  document.getElementById('totalCost').textContent = currency.format(filteredCost || 0);
  document.getElementById('subscriptionCount').textContent = data.summary.subscriptionCount || 0;
  document.getElementById('resourceGroupCount').textContent = data.summary.resourceGroupCount || 0;
  document.getElementById('resourceCount').textContent = data.summary.resourceCount || 0;
  document.getElementById('avgDailyCost').textContent = currency.format(avgDaily || 0);
  document.getElementById('monthlyRunRate').textContent = currency.format((avgDaily || 0) * 30);
  charts.forEach(c => c.destroy()); charts = [];
  charts.push(renderBar('subscriptionChart', data.costBySubscription, 'Cost'));
  charts.push(renderBar('serviceChart', data.costByService, 'Cost'));
  charts.push(renderBar('resourceGroupChart', data.costByResourceGroup, 'Cost'));
  charts.push(renderBar('regionChart', data.costByRegion || [], 'Cost'));
  charts.push(renderLine('dailyTrendChart', data.dailyTrend));
  renderTable(filteredResources.length ? filteredResources : data.topResources || []);
}

function average(values) { return values.length ? values.reduce((a,b)=>a+b,0) / values.length : 0; }
function renderBar(id, rows, label) {
  const topRows = (rows || []).slice(0, 12);
  return new Chart(document.getElementById(id), {
    type: 'bar',
    data: { labels: topRows.map(x => x.name || 'Unassigned'), datasets: [{ label, data: topRows.map(x => x.cost || 0) }] },
    options: { responsive:true, plugins:{legend:{display:false}}, scales:{ y:{ ticks:{ callback:v=>currency.format(v) } } } }
  });
}
function renderLine(id, rows) {
  return new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels:(rows || []).map(x=>x.date), datasets:[{ label:'Daily Cost', data:(rows || []).map(x=>x.cost || 0), tension:.25, fill:true }] },
    options: { responsive:true, plugins:{legend:{display:false}}, scales:{ y:{ ticks:{ callback:v=>currency.format(v) } } } }
  });
}
function renderTable(rows) {
  document.getElementById('tableInfo').textContent = `${rows.length} resources shown`;
  document.getElementById('topResources').innerHTML = rows.slice(0, 20).map(row => `
    <tr><td>${escapeHtml(row.resourceName || row.resourceId || 'Unknown')}</td><td><span class="pill">${escapeHtml(row.subscriptionName || '')}</span></td><td>${escapeHtml(row.resourceGroup || '')}</td><td>${escapeHtml(row.service || '')}</td><td>${currency.format(row.cost || 0)}</td></tr>`).join('');
}
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch])); }
loadDashboard().catch(error => { console.error(error); document.getElementById('lastUpdated').textContent = 'Failed to load data'; });
