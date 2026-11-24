(function () {
  var mapEl = document.getElementById('map');
  if (!mapEl) { return; }
  var map = L.map('map').setView([12.9716, 77.5946], 17);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);

  var startSel = document.getElementById('start');
  var endSel = document.getElementById('end');
  var info = document.getElementById('route-info');
  var stepsEl = document.getElementById('steps');
  var routeLayer = L.layerGroup().addTo(map);
  var fixedStart = mapEl.dataset.start || '';

  function getJSON(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) { throw new Error(data.error || 'Request failed'); }
          return data;
        });
      });
  }

  function addOption(sel, name) {
    var o = document.createElement('option');
    o.value = name; o.textContent = name; sel.appendChild(o);
  }

  // Draw a route returned by /api/route or by the chat assistant (textContent only: no HTML injection).
  function showRoute(r) {
    routeLayer.clearLayers();
    var pts = r.path.map(function (p) { return [p.lat, p.lng]; });
    L.polyline(pts, { color: '#f59e0b', weight: 6 }).addTo(routeLayer);
    L.circleMarker(pts[0], { radius: 8, color: '#0f766e', fillOpacity: 1 }).addTo(routeLayer);
    L.circleMarker(pts[pts.length - 1], { radius: 8, color: '#b42318', fillOpacity: 1 }).addTo(routeLayer);
    map.fitBounds(pts, { padding: [40, 40] });
    if (info) {
      info.textContent = r.path[0].name + ' to ' + r.path[r.path.length - 1].name + ': ' +
        Math.round(r.distance_m) + ' m, about ' + r.minutes + ' min on foot';
    }
    if (stepsEl) {
      stepsEl.textContent = '';
      (r.steps || []).forEach(function (s) {
        var li = document.createElement('li'); li.textContent = s.text; stepsEl.appendChild(li);
      });
    }
  }
  window.CampusMap = { showRoute: showRoute };

  function go(start, end) {
    getJSON('/api/route?start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end))
      .then(showRoute)
      .catch(function (e) { if (info) { info.textContent = e.message; } if (stepsEl) { stepsEl.textContent = ''; } });
  }

  getJSON('/api/places').then(function (data) {
    data.places.forEach(function (b) {
      if (startSel) { addOption(startSel, b.name); }
      if (endSel) { addOption(endSel, b.name); }
      var d = document.createElement('div');
      var t = document.createElement('strong'); t.textContent = b.name;
      var p = document.createElement('div'); p.textContent = b.info || b.category || '';
      d.appendChild(t); d.appendChild(p);
      L.marker([b.lat, b.lng]).addTo(map).bindPopup(d);
    });
    if (fixedStart && startSel) { startSel.value = fixedStart; startSel.disabled = true; }
    if (endSel && endSel.options.length > 1) {
      var i = 0;
      while (i < endSel.options.length - 1 && endSel.options[i].value === (startSel && startSel.value)) { i++; }
      endSel.selectedIndex = i;
    }
  }).catch(function (e) { if (info) { info.textContent = e.message; } });

  var goBtn = document.getElementById('go');
  if (goBtn) { goBtn.addEventListener('click', function () { go(startSel.value, endSel.value); }); }

  // quick buttons: "Take me to ..." always start at the Main Gate
  document.querySelectorAll('[data-dest]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (startSel) { startSel.value = 'Main Gate'; }
      if (endSel) { endSel.value = b.dataset.dest; }
      go('Main Gate', b.dataset.dest);
    });
  });
})();
