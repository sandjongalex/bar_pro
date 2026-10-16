(() => {
  const payloadNode = document.getElementById('dashboard-chart-data');
  if (!payloadNode || typeof Chart === 'undefined') return;

  let data;
  try {
    data = JSON.parse(payloadNode.textContent || '{}');
  } catch (_error) {
    return;
  }

  const styles = getComputedStyle(document.body);
  const brand = styles.getPropertyValue('--ds-primary').trim() || '#20d9d1';
  const accent = '#79c999';
  const ink = '#213c29';
  const muted = '#62796b';
  const line = 'rgba(68,121,84,.13)';
  const receiptsColor = '#22a466';
  const expensesColor = '#8a9e92';
  const currency = data.currency || 'XAF';
  const compact = window.matchMedia('(max-width: 640px)').matches;
  const areaFill = (context, color) => {
    const {chart} = context;
    const {ctx, chartArea} = chart;
    if (!chartArea) return color;
    const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    gradient.addColorStop(0, color === brand ? 'rgba(22,163,74,.18)' : color === receiptsColor ? 'rgba(34,164,102,.13)' : 'rgba(138,158,146,.13)');
    gradient.addColorStop(1, 'rgba(5,17,38,0)');
    return gradient;
  };
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
  Chart.defaults.borderColor = line;
  Chart.defaults.animation = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 350 };
  Chart.defaults.plugins.tooltip.backgroundColor = '#183c29';
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
            backgroundColor: (context) => areaFill(context, brand),
            fill: true,
            pointRadius: compact ? 2 : 4,
            pointHoverRadius: 6,
            tension: 0.28,
          },
          {
            label: 'Encaissements',
            data: data.trend_7d.receipts,
            borderColor: receiptsColor,
            backgroundColor: (context) => areaFill(context, receiptsColor),
            fill: true,
            pointRadius: compact ? 2 : 4,
            pointHoverRadius: 6,
            tension: 0.28,
          },
          {
            label: 'Dépenses',
            data: data.trend_7d.expenses,
            borderColor: expensesColor,
            backgroundColor: (context) => areaFill(context, expensesColor),
            fill: true,
            pointRadius: compact ? 2 : 4,
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
