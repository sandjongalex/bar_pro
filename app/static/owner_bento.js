(() => {
  const canvas = document.getElementById('ownerBentoSalesChart');
  const source = document.getElementById('owner-bento-chart-data');
  if (!canvas || !source || typeof Chart === 'undefined') return;

  let data;
  try {
    data = JSON.parse(source.textContent);
  } catch (_) {
    return;
  }

  const labels = Array.isArray(data.labels) ? data.labels : [];
  const values = Array.isArray(data.values) ? data.values.map(Number) : [];
  if (!labels.length || labels.length !== values.length ||
      values.some(value => !Number.isFinite(value))) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const fill = ctx.createLinearGradient(0, 0, 0, 200);
  fill.addColorStop(0, 'rgba(94, 217, 188, .33)');
  fill.addColorStop(1, 'rgba(94, 217, 188, 0)');
  const fmt = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: '#70e0c3',
        backgroundColor: fill,
        borderWidth: 3,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: '#70e0c3',
        fill: true,
        tension: .35,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: reducedMotion ? false : { duration: 350 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#10263d',
          padding: 10,
          displayColors: false,
          callbacks: {
            label: context => fmt.format(context.parsed.y) + ' ' + (data.currency || 'FCFA'),
          },
        },
      },
      scales: {
        x: {
          border: { display: false },
          grid: { display: false },
          ticks: { color: '#bfdaea', font: { size: 11 }, maxRotation: 0 },
        },
        y: {
          beginAtZero: true,
          border: { display: false },
          grid: { color: 'rgba(255,255,255,.10)' },
          ticks: {
            color: '#bfdaea',
            maxTicksLimit: 4,
            callback: value => fmt.format(value),
          },
        },
      },
    },
  });
})();