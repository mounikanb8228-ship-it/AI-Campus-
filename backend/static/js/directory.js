(function () {
  var grid = document.getElementById('grid');
  var tabs = document.querySelectorAll('.tab');

  function render(rows) {
    grid.textContent = '';
    if (!rows.length) { grid.textContent = 'No records.'; return; }
    var cols = Object.keys(rows[0]);
    var head = grid.insertRow();
    cols.forEach(function (c) { var th = document.createElement('th'); th.textContent = c.replace('_', ' '); head.appendChild(th); });
    rows.forEach(function (r) {
      var tr = grid.insertRow();
      cols.forEach(function (c) { tr.insertCell().textContent = r[c] == null ? '' : r[c]; });
    });
  }

  function load(src) {
    fetch(src, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { if (r.status === 401) { window.location = '/login'; return []; } return r.json(); })
      .then(render);
  }

  tabs.forEach(function (t) {
    t.addEventListener('click', function () {
      tabs.forEach(function (o) { o.classList.remove('active'); });
      t.classList.add('active'); load(t.dataset.src);
    });
  });
  load('/departments');
})();
