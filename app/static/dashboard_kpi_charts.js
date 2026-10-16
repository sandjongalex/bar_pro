(() => {
  const source = document.getElementById('dashboard-kpi-data');
  if (!source || typeof Chart === 'undefined') return;
  let payload;
  try { payload = JSON.parse(source.textContent); } catch (_) { return; }
  const currency = payload.currency || 'XAF';
  const numbers = (items) => items.map((x) => Number(x));
  const money = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
  const contrast = '#526d60';
  const compact = window.matchMedia('(max-width: 600px)').matches;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const draw = (id, labels, raw, colors) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const values = numbers(raw);
    if (values.length !== labels.length || values.some(x => !Number.isFinite(x))) return;
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label: currency, data: values, backgroundColor: colors, borderRadius: 7, maxBarThickness: 44, borderSkipped: false }]
      },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        animation: reduceMotion ? false : { duration: 400 },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: '#173c2b', padding: 10, callbacks: {
            label: (c) => money.format(c.parsed.x) + ' ' + currency
          } }
        },
        scales: {
          x: { grid: { color: 'rgba(25,80,47,.09)' }, ticks: { color: contrast, callback: value => money.format(value) } },
          y: { grid: { display: false }, ticks: { color: contrast, font: { size: compact ? 10 : 12, weight: '600' }, autoSkip: false } }
        }
      }
    });
  };
  draw('kpiFinanceChart', ['Ventes', 'Encaissé', 'Dépenses', 'Dettes fournisseurs', 'Bénéfice estimé'],
    payload.finance, ['#16885c','#327dcc','#d26058','#b9902d','#8469b8']);
  draw('kpiStockChart', ['Valeur achat', 'Valeur vente'], payload.stock, ['#5970c8','#1d9971']);
})();