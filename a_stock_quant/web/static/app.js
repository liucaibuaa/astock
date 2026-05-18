// A-Stock Quant Monitor — Dashboard logic
const API = '/api';
let selectedCode = null;
let priceData = {};     // code -> {prices: [], times: []}
let chart = null;
let ws = null;

// ---- WebSocket ----
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    document.getElementById('ws-dot').className = 'dot on';
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'snapshot_update') {
      updateTable(msg.data);
      updateChartData(msg.data);
    } else if (msg.type === 'stock_removed') {
      removeStockRow(msg.code);
      if (selectedCode === msg.code) {
        selectedCode = null;
        chart.data.labels = [];
        chart.data.datasets[0].data = [];
        chart.update('none');
      }
    }
  };

  ws.onclose = () => {
    document.getElementById('ws-dot').className = 'dot off';
    setTimeout(connectWS, 2000 + Math.random() * 3000);
  };

  ws.onerror = () => {
    ws.close();
  };
}

// ---- Table update ----
function updateTable(snapshots) {
  const tbody = document.getElementById('watchlist-body');
  const entries = Object.entries(snapshots);

  if (entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty">No data yet</td></tr>';
    return;
  }

  let html = '';
  for (const [code, snap] of entries) {
    const chg = snap.change_pct;
    const chgClass = chg >= 0 ? 'up' : 'down';
    const chgSign = chg >= 0 ? '+' : '';
    const rowClass = code === selectedCode ? 'selected' : '';

    html += `<tr id="row-${code}" class="${rowClass} flash" onclick="selectStock('${code}')">
      <td style="color:#58a6ff;font-weight:600">${code}</td>
      <td>${snap.name}</td>
      <td style="font-weight:600">${snap.price.toFixed(2)}</td>
      <td class="${chgClass}" style="font-weight:600">${chgSign}${chg.toFixed(2)}%</td>
      <td>${snap.open.toFixed(2)}</td>
      <td>${snap.high.toFixed(2)}</td>
      <td>${snap.low.toFixed(2)}</td>
      <td>${snap.pe_ttm > 0 ? snap.pe_ttm.toFixed(1) : '-'}</td>
      <td>${snap.pb > 0 ? snap.pb.toFixed(2) : '-'}</td>
      <td>${snap.turnover_pct.toFixed(2)}%</td>
      <td>${snap.mcap_yi > 0 ? snap.mcap_yi.toFixed(0) : '-'}</td>
      <td><button class="remove-btn" onclick="event.stopPropagation(); removeStock('${code}')">Delete</button></td>
    </tr>`;
  }
  tbody.innerHTML = html;

  // Remove flash class after animation
  setTimeout(() => {
    for (const code of Object.keys(snapshots)) {
      const row = document.getElementById(`row-${code}`);
      if (row) row.classList.remove('flash');
    }
  }, 600);
}

// ---- Chart ----
function initChart() {
  const ctx = document.getElementById('price-chart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Price',
        data: [],
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        pointHoverRadius: 5,
        borderWidth: 2,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d',
          borderWidth: 1,
          titleColor: '#e1e4e8',
          bodyColor: '#8b949e',
        },
      },
      scales: {
        x: {
          display: true,
          ticks: { color: '#484f58', maxTicksLimit: 8, maxRotation: 0 },
          grid: { display: false },
        },
        y: {
          display: true,
          ticks: { color: '#484f58' },
          grid: { color: '#21262d' },
        },
      },
    },
  });
}

function updateChartData(snapshots) {
  // Update in-memory data store
  for (const [code, snap] of Object.entries(snapshots)) {
    if (!priceData[code]) {
      priceData[code] = { prices: [], times: [] };
    }
    const d = priceData[code];
    d.prices.push(snap.price);
    const now = new Date();
    d.times.push(now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));

    // Keep last 120 points
    if (d.prices.length > 120) {
      d.prices.shift();
      d.times.shift();
    }
  }

  // Refresh chart if a stock is selected
  if (selectedCode && priceData[selectedCode]) {
    const d = priceData[selectedCode];
    chart.data.labels = d.times;
    chart.data.datasets[0].data = d.prices;
    chart.data.datasets[0].label = selectedCode;
    chart.update('none');
  }
}

// ---- Stock selection ----
async function selectStock(code) {
  selectedCode = code;
  // Highlight row
  document.querySelectorAll('tbody tr').forEach(r => r.classList.remove('selected'));
  const row = document.getElementById(`row-${code}`);
  if (row) row.classList.add('selected');

  // Load history
  if (priceData[code]) {
    const d = priceData[code];
    chart.data.labels = d.times;
    chart.data.datasets[0].data = d.prices;
    chart.data.datasets[0].label = code;
    chart.update('none');
  }

  // Fetch recent history from API
  try {
    const resp = await fetch(`${API}/snapshots/${code}?minutes=30`);
    const json = await resp.json();
    if (json.snapshots && json.snapshots.length > 0) {
      const times = json.snapshots.map(s => {
        const d = new Date(s.fetched_at);
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      });
      const prices = json.snapshots.map(s => s.price);
      chart.data.labels = times;
      chart.data.datasets[0].data = prices;
      chart.data.datasets[0].label = code;
      chart.update('none');
    }
  } catch (e) {
    console.error('Failed to load history:', e);
  }

  // Load alerts for this stock
  loadAlerts(code);
}

// ---- Add / Remove stocks ----
async function addStock() {
  const input = document.getElementById('stock-input');
  const code = input.value.trim();
  if (!code) return;

  try {
    const resp = await fetch(`${API}/watchlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    const json = await resp.json();
    if (resp.ok) {
      input.value = '';
      showToast(`Added ${json.code}`, 'info');
      // Immediately reload watchlist to show the new stock
      await reloadWatchlist();
    } else {
      showToast(json.detail || 'Failed', 'warning');
    }
  } catch (e) {
    showToast('Network error', 'critical');
  }
}

async function removeStock(code) {
  try {
    const resp = await fetch(`${API}/watchlist/${code}`, { method: 'DELETE' });
    if (resp.ok) {
      removeStockRow(code);
      if (selectedCode === code) {
        selectedCode = null;
        chart.data.labels = [];
        chart.data.datasets[0].data = [];
        chart.update('none');
      }
      showToast(`Removed ${code}`, 'info');
    } else {
      const json = await resp.json();
      showToast(json.detail || 'Failed to remove', 'warning');
    }
  } catch (e) {
    showToast('Network error', 'critical');
  }
}

function removeStockRow(code) {
  const row = document.getElementById(`row-${code}`);
  if (row) row.remove();
  // Clean up price data cache
  delete priceData[code];
  // If table is now empty, show placeholder
  const tbody = document.getElementById('watchlist-body');
  if (tbody && tbody.querySelectorAll('tr').length === 0) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty">No stocks added. Use the input above or config.yaml.</td></tr>';
  }
}

async function reloadWatchlist() {
  try {
    const resp = await fetch(`${API}/watchlist`);
    const json = await resp.json();
    if (json.snapshots) {
      updateTable(json.snapshots);
      updateChartData(json.snapshots);
    }
  } catch (e) {
    console.error('Failed to reload watchlist:', e);
  }
}

// Allow Enter key in input
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('stock-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addStock();
  });
});

// ---- Alerts ----
async function loadAlerts(code) {
  try {
    const resp = await fetch(`${API}/alerts?limit=20`);
    const json = await resp.json();
    const alertsList = document.getElementById('alerts-list');
    if (!json.alerts || json.alerts.length === 0) {
      alertsList.innerHTML = '<div class="empty">No alerts yet</div>';
      return;
    }
    alertsList.innerHTML = json.alerts.map(a => {
      const t = new Date(a.triggered_at);
      const time = t.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      return `<div class="alert-item ${a.severity}">
        <span class="alert-time">${time}</span>
        <span class="alert-badge ${a.severity}">${a.severity}</span>
        <strong>${a.name}(${a.code})</strong>
        <span style="color:#8b949e">${a.message}</span>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('Failed to load alerts:', e);
  }
}

// ---- Toast ----
function showToast(message, severity) {
  const container = document.getElementById('toasts');
  const toast = document.createElement('div');
  toast.className = `toast ${severity}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ---- Status polling ----
async function updateStatus() {
  try {
    const resp = await fetch(`${API}/status`);
    const status = await resp.json();
    document.getElementById('poll-count').textContent = `Polls: ${status.poll_count}`;
    const market = document.getElementById('market-status');
    if (status.is_trading_time) {
      market.textContent = 'Trading';
      market.style.color = '#3fb950';
    } else if (status.market_open_today) {
      market.textContent = 'Closed';
      market.style.color = '#d29922';
    } else {
      market.textContent = 'Holiday';
      market.style.color = '#8b949e';
    }
  } catch (e) {
    // ignore
  }
}

// ---- Init ----
async function init() {
  initChart();
  connectWS();
  setInterval(updateStatus, 10000);
  updateStatus();

  // Load initial data
  try {
    const resp = await fetch(`${API}/watchlist`);
    const json = await resp.json();
    if (json.snapshots) {
      updateTable(json.snapshots);
      updateChartData(json.snapshots);
      // Auto-select the first stock to show its chart immediately
      const codes = Object.keys(json.snapshots);
      if (codes.length > 0 && !selectedCode) {
        selectStock(codes[0]);
      }
    }
  } catch (e) {
    console.error('Failed to load initial data:', e);
  }
  loadAlerts();
  setInterval(loadAlerts, 30000);
}

init();
