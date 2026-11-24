(function () {
  var form = document.getElementById('chat-form');
  if (!form) { return; }
  var input = document.getElementById('chat-q');
  var log = document.getElementById('chat-log');
  var meta = document.querySelector('meta[name="csrf"]');
  var token = meta ? meta.content : '';

  function bubble(text, me, sources) {
    var b = document.createElement('div');
    b.className = 'bubble' + (me ? ' me' : '');
    b.textContent = text;
    if (sources && sources.length) {
      var s = document.createElement('span');
      s.className = 'src';
      s.textContent = 'Source: ' + sources.join(', ');
      b.appendChild(s);
    }
    log.appendChild(b);
    log.scrollTop = log.scrollHeight;
    return b;
  }

  function ask(q) {
    bubble(q, true);
    var wait = bubble('Thinking...', false);
    fetch('/api/chat', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token, 'Accept': 'application/json' },
      body: JSON.stringify({ q: q })
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    }).then(function (res) {
      log.removeChild(wait);
      if (!res.ok) { bubble(res.data.error || 'Something went wrong.', false); return; }
      bubble(res.data.answer, false, res.data.sources);
      if (res.data.route && window.CampusMap) { window.CampusMap.showRoute(res.data.route); }
    }).catch(function () {
      log.removeChild(wait);
      bubble('Could not reach the server. Please try again.', false);
    });
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) { return; }
    input.value = '';
    ask(q);
  });
  document.querySelectorAll('[data-q]').forEach(function (c) {
    c.addEventListener('click', function () { ask(c.dataset.q); });
  });
})();
