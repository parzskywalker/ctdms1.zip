// Thin Chart.js wrappers following the house chart spec:
// bars <= 24px, 4px rounded data-end, hairline solid gridlines, text in ink tokens, fixed categorical order.
(function () {
  const INK = '#18212d', INK2 = '#52514e', MUTED = '#898781', GRID = '#e1e0d9', AXIS = '#c3c2b7';
  const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];

  function inr(v) {
    const a = Math.abs(v), s = v < 0 ? '-' : '';
    if (a >= 1e7) return s + '₹' + (a / 1e7).toFixed(a >= 1e8 ? 0 : 1) + ' Cr';
    if (a >= 1e5) return s + '₹' + (a / 1e5).toFixed(a >= 1e6 ? 0 : 1) + ' L';
    if (a >= 1e3) return s + '₹' + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'K';
    return s + '₹' + Math.round(a);
  }
  function inrFull(v) { return '₹' + Number(v).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

  if (window.Chart) {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = INK2;
    Chart.defaults.plugins.tooltip.backgroundColor = '#10233a';
    Chart.defaults.plugins.tooltip.padding = 9;
    Chart.defaults.plugins.tooltip.cornerRadius = 6;
    Chart.defaults.animation.duration = 250;
  }

  function base(opts, horizontal) {
    const money = opts.money !== false;
    const valueAxis = {
      beginAtZero: true,
      grid: {color: GRID, lineWidth: 1, drawTicks: false},
      border: {display: false},
      ticks: {color: MUTED, padding: 6, callback: (v) => money ? inr(v) : Number(v).toLocaleString('en-IN'), maxTicksLimit: 6},
    };
    const catAxis = {grid: {display: false}, border: {color: AXIS}, ticks: {color: INK2, autoSkip: true}};
    return {
      responsive: true, maintainAspectRatio: false,
      indexAxis: horizontal ? 'y' : 'x',
      interaction: {mode: 'index', intersect: false},
      scales: horizontal ? {x: valueAxis, y: catAxis} : {x: catAxis, y: valueAxis},
      plugins: {
        legend: {display: !!opts.legend, labels: {color: INK, boxWidth: 10, boxHeight: 10}},
        tooltip: {callbacks: {label: (c) => `${c.dataset.label}: ${money ? inrFull(c.parsed[horizontal ? 'x' : 'y']) : Number(c.parsed[horizontal ? 'x' : 'y']).toLocaleString('en-IN')}`}},
      },
    };
  }

  function mk(id, labels, datasets, opts, horizontal) {
    const el = document.getElementById(id);
    if (!el || !window.Chart) return;
    if (!labels.length) {
      el.parentElement.innerHTML = '<div class="empty">No data for the selected filter</div>';
      return;
    }
    datasets.forEach((ds, i) => {
      ds.backgroundColor = ds.backgroundColor || SERIES[i];
      ds.borderRadius = 4;
      ds.borderSkipped = 'start';
      ds.maxBarThickness = 24;
      ds.categoryPercentage = 0.7;
      ds.barPercentage = datasets.length > 1 ? 0.9 : 0.8;
    });
    return new Chart(el, {type: 'bar', data: {labels, datasets}, options: base(opts || {}, horizontal)});
  }

  window.CT = {
    bar: (id, labels, ds, opts) => mk(id, labels, ds, opts, false),
    hbar: (id, labels, ds, opts) => mk(id, labels, ds, opts, true),
    inr, inrFull, SERIES,
  };
})();
