const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

async function loadDashboard() {
  const response = await fetch('data/azure_cost.json', { cache: 'no-store' });
  const data = await response.json();

  document.getElementById('lastUpdated').textContent = data.generatedAt || '-';
  document.getElementById('totalCost').textContent = currency.format(data.summary.totalCost || 0);
  document.getElementById('subscriptionCount').textContent = data.summary.subscriptionCount || 0;
  document.getElementById('resourceGroupCount').textContent = data.summary.resourceGroupCount || 0;
  document.getElementById('resourceCount').textContent = data.summary.resourceCount || 0;

  renderBar('subscriptionChart', data.costBySubscription, 'Subscription');
  renderBar('serviceChart', data.costByService, 'Service');
  renderBar('resourceGroupChart', data.costByResourceGroup, 'Resource Group');
  renderLine('dailyTrendChart', data.dailyTrend);
  renderTable(data.topResources || []);
}

function renderBar(id, rows, labelName) {
  const topRows = (rows || []).slice(0, 10);
  new Chart(document.getElementById(id), {
    type: 'bar',
    data: {
      labels: topRows.map(x => x.name || 'Unassigned'),
      datasets: [{ label: 'Cost', data: topRows.map(x => x.cost || 0) }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: value => currency.format(value) } } }
    }
  });
}

function renderLine(id, rows) {
  new Chart(document.getElementById(id), {
    type: 'line',
    data: {
      labels: (rows || []).map(x => x.date),
      datasets: [{ label: 'Daily Cost', data: (rows || []).map(x => x.cost || 0), tension: 0.25 }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: { y: { ticks: { callback: value => currency.format(value) } } }
    }
  });
}

function renderTable(rows) {
  const tbody = document.getElementById('topResources');
  tbody.innerHTML = rows.slice(0, 20).map(row => `
    <tr>
      <td>${escapeHtml(row.resourceName || row.resourceId || 'Unknown')}</td>
      <td>${escapeHtml(row.subscriptionName || '')}</td>
      <td>${escapeHtml(row.resourceGroup || '')}</td>
      <td>${escapeHtml(row.service || '')}</td>
      <td>${currency.format(row.cost || 0)}</td>
    </tr>`).join('');
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

loadDashboard().catch(error => {
  console.error(error);
  document.getElementById('lastUpdated').textContent = 'Failed to load data';
});
