(() => {
  const payloadNode = document.getElementById('dashboard-chart-data');
  if (!payloadNode || typeof Chart === 'undefined') return;

  let data;
  try {
    data = JSON.parse(payloadNode.textContent || '{}');
  } catch (_error) {
    return;
  }

  const styles = getComputedStyle(document.documentElement);
  const brand = styles.getPropertyValue('--ds-primary').trim() || '#0F725F';
  const accent = styles.getPropertyValue('--accent').trim() || '#f2b544';
  const ink = styles.getPropertyValue('--ds-text').trim() || '#172B25';
  const muted = styles.getPropertyValue('--ds-text-secondary').trim() || '#667A72';
  const line = styles.getPropertyValue('--ds-border').trim() || '#DDE8E3';
  const receiptsColor = '#3478c7';
  const expensesColor = '#c94d4d';
  const currency = data.currency || 'XAF';
  const compact = window.matchMedia('(max-width: 640px)').matches;
  const wrapLabel = (value, limit = 20) => {
    const str = String(value ?? '');
    if (str.length <= limit) return str;
    const words = str.split(/\\s+/); const lines = []; let part = '';
    words.forEach((word) => { if (part && (part + ' ' + word).length > limit) { lines.push(part); part = word; } else { part = part ? part + ' ' + word : word; } });
    if (part) lines.push(part);
    return lines.slice(0, 3);
  };

  const money = new Intl.NumberFormat('fr-FR', {
    maximumFractionDigits: 0,
  });

  Chart.defaults.color = muted;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.animation = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 350 };
  Chart.defaults.plugins.tooltip.backgroundColor = '#12392F';
  Chart.defaults.plugins.tooltip.titleColor = '#FFFFFF';
  Chart.defaults.plugins.tooltip.bodyColor = '#FFFFFF';
  Chart.defaults.plugins.tooltip.cornerRadius = 10;
  Chart.defaults.plugins.tooltip.padding = 12;

  const topCanvas = document.getElementById('topProductsChart');
  if (topCanvas && data.top_products?.labels?.length) {
    new Chart(topCanvas, {
      type: 'bar',
      data: {
        labels: data.top_products.labels,
        datasets: [{
          label: 'Bouteilles / unités vendues',
          data: data.top_products.quantities,
          backgroundColor: brand,
          borderRadius: 7,
          borderSkipped: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => `${context.raw} unité${Number(context.raw) > 1 ? 's' : ''}`,
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: line },
            ticks: { precision: 0 },
          },
          y: {
            grid: { display: false },
            ticks: { color: ink, font: { weight: '600' }, callback: function(value) { return wrapLabel(this.getLabelForValue(value), compact ? 13 : 22); } },
          },
        },
      },
    });
  }

  const serverCanvas = document.getElementById('waitressChart');
  if (serverCanvas && data.servers?.labels?.length) {
    new Chart(serverCanvas, {
      data: {
        labels: data.servers.labels,
        datasets: [
          {
            type: 'bar',
            label: 'Réglé hors crédit',
            data: data.servers.non_credit_sales,
            backgroundColor: brand,
            borderRadius: 7,
            borderSkipped: false,
            stack: 'sales',
            yAxisID: 'y',
          },
          {
            type: 'bar',
            label: 'Crédit client',
            data: data.servers.credit_sales,
            backgroundColor: accent,
            borderRadius: 7,
            borderSkipped: false,
            stack: 'sales',
            yAxisID: 'y',
          },
          {
            type: 'line',
            label: 'Commandes',
            data: data.servers.orders,
            borderColor: ink,
            backgroundColor: ink,
            pointRadius: 4,
            pointHoverRadius: 5,
            tension: 0.25,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'bottom',
            labels: { usePointStyle: true, boxWidth: 8 },
          },
          tooltip: {
            callbacks: {
              label: (context) => {
                if (context.dataset.yAxisID === 'y1') {
                  return `${context.dataset.label}: ${context.raw}`;
                }
                return `${context.dataset.label}: ${money.format(context.raw)} ${currency}`;
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { autoSkip: true, maxRotation: compact ? 35 : 0 } },
          y: {
            beginAtZero: true,
            stacked: true,
            grid: { color: line },
            ticks: {
              callback: (value) => money.format(value),
            },
          },
          y1: {
            beginAtZero: true,
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { precision: 0 },
          },
        },
      },
    });
  }

  const trendCanvas = document.getElementById('salesTrendChart');
  if (trendCanvas && data.trend_7d?.labels?.length) {
    new Chart(trendCanvas, {
      type: 'line',
      data: {
        labels: data.trend_7d.labels,
        datasets: [
          {
            label: 'Ventes',
            data: data.trend_7d.sales,
            borderColor: brand,
            backgroundColor: brand,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.28,
          },
          {
            label: 'Encaissements',
            data: data.trend_7d.receipts,
            borderColor: receiptsColor,
            backgroundColor: receiptsColor,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.28,
          },
          {
            label: 'Dépenses',
            data: data.trend_7d.expenses,
            borderColor: expensesColor,
            backgroundColor: expensesColor,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.28,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'bottom',
            labels: { usePointStyle: true, boxWidth: 8 },
          },
          tooltip: {
            callbacks: {
              label: (context) => `${context.dataset.label}: ${money.format(context.raw)} ${currency}`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { color: ink, font: { weight: '600' }, maxRotation: compact ? 35 : 0 },
          },
          y: {
            beginAtZero: true,
            grid: { color: line },
            ticks: {
              callback: (value) => money.format(value),
            },
          },
        },
      },
    });
  }
})();
