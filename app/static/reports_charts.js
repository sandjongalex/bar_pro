(() => {
  const dataNode = document.getElementById('reportChartData');
  if (!dataNode || typeof Chart === 'undefined') return;

  let data;
  try {
    data = JSON.parse(dataNode.textContent || '{}');
  } catch (_) {
    return;
  }

  const currency = data.currency || '';
  const number = (value) => new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(Number(value || 0));
  const money = (value) => `${number(value)} ${currency}`.trim();
  const base = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: {
      legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } },
    },
  };

  const trendCanvas = document.getElementById('reportTrendChart');
  if (trendCanvas && data.trend && data.trend.available) {
    new Chart(trendCanvas, {
      type: 'line',
      data: {
        labels: data.trend.labels,
        datasets: [
          { label: 'Ventes', data: data.trend.sales, borderColor: '#0f5c4d', backgroundColor: 'rgba(15,92,77,.12)', tension: .25, fill: false },
          { label: 'Encaissements', data: data.trend.receipts, borderColor: '#2f7ed8', backgroundColor: 'rgba(47,126,216,.12)', tension: .25, fill: false },
          { label: 'Dépenses', data: data.trend.expenses, borderColor: '#c47a15', backgroundColor: 'rgba(196,122,21,.12)', tension: .25, fill: false },
        ],
      },
      options: {
        ...base,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { callback: (value) => number(value) } },
        },
        plugins: {
          ...base.plugins,
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${money(ctx.parsed.y)}` } },
        },
      },
    });
  }

  const topCanvas = document.getElementById('reportTopProductsChart');
  if (topCanvas && data.top_products && data.top_products.labels.length) {
    new Chart(topCanvas, {
      type: 'bar',
      data: {
        labels: data.top_products.labels,
        datasets: [{ label: 'Quantité nette', data: data.top_products.values, backgroundColor: '#0f5c4d', borderRadius: 6 }],
      },
      options: {
        ...base,
        indexAxis: 'y',
        scales: {
          x: { beginAtZero: true, ticks: { callback: (value) => number(value) } },
          y: { grid: { display: false } },
        },
        plugins: {
          ...base.plugins,
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `Quantité: ${number(ctx.parsed.x)}` } },
        },
      },
    });
  }

  const methodCanvas = document.getElementById('reportPaymentMethodsChart');
  if (methodCanvas && data.payment_methods && data.payment_methods.labels.length) {
    const labels = data.payment_methods.labels.map((code) => ({
      CASH: 'Espèces',
      MOBILE_MONEY: 'Mobile Money',
      CARD: 'Carte',
      BANK_TRANSFER: 'Virement',
      OTHER: 'Autre',
    }[code] || code));
    new Chart(methodCanvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label: 'Net encaissé', data: data.payment_methods.values, backgroundColor: '#2f7ed8', borderRadius: 6 }],
      },
      options: {
        ...base,
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { callback: (value) => number(value) } },
        },
        plugins: {
          ...base.plugins,
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `Net: ${money(ctx.parsed.y)}` } },
        },
      },
    });
  }

  const serverCanvas = document.getElementById('reportServersChart');
  if (serverCanvas && data.servers && data.servers.labels.length) {
    new Chart(serverCanvas, {
      type: 'bar',
      data: {
        labels: data.servers.labels,
        datasets: [
          { label: 'Ventes', data: data.servers.sales, backgroundColor: '#0f5c4d', borderRadius: 5 },
          { label: 'Crédit', data: data.servers.credit, backgroundColor: '#f2b544', borderRadius: 5 },
        ],
      },
      options: {
        ...base,
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { callback: (value) => number(value) } },
        },
        plugins: {
          ...base.plugins,
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${money(ctx.parsed.y)}` } },
        },
      },
    });
  }
})();
