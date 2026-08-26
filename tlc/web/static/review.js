// Review island (D-018: vanilla JS, no build step). Two-pass: blind Pass A, adjudication Pass B.
(function () {
  const root = document.getElementById('review');
  const H = +root.dataset.h, W = +root.dataset.w, sha = root.dataset.sha, runId = root.dataset.run;
  const svg = document.getElementById('roverlay'), mine = document.getElementById('mine');
  const tbody = document.querySelector('#mineTable tbody');
  const t0 = Date.now();
  let marks = [], passA = true, aided = false;

  function svgPoint(evt) {
    const r = svg.getBoundingClientRect();
    return { x: (evt.clientX - r.left) / r.width * W, y: (evt.clientY - r.top) / r.height * H };
  }
  function render() {
    mine.innerHTML = ''; tbody.innerHTML = '';
    marks.forEach((m, i) => {
      const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      l.setAttribute('class', 'tick mine'); l.setAttribute('x1', m.x0); l.setAttribute('x2', m.x1);
      l.setAttribute('y1', m.y); l.setAttribute('y2', m.y); l.dataset.i = i;
      l.addEventListener('click', e => { e.stopPropagation(); if (passA) { marks.splice(i, 1); render(); } });
      mine.appendChild(l);
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>L${m.lane + 1}</td><td class="num">${m.y.toFixed(0)} px · ${(m.y / H).toFixed(3)} of height</td>` +
        `<td><select data-i="${i}"><option value="strong">strong</option><option value="faint">faint</option><option value="trace">trace</option></select></td>` +
        `<td><button type="button" data-i="${i}" class="btn small">remove</button></td>`;
      tr.querySelector('select').value = m.strength;
      tr.querySelector('select').onchange = e => { marks[i].strength = e.target.value; };
      tr.querySelector('button').onclick = () => { marks.splice(i, 1); render(); };
      tbody.appendChild(tr);
    });
  }
  svg.querySelectorAll('.lanehit').forEach(r => r.addEventListener('click', evt => {
    if (!passA) return;
    const p = svgPoint(evt);
    marks.push({ lane: +r.dataset.lane, y: p.y, x0: +r.getAttribute('x'), x1: +r.getAttribute('x') + +r.getAttribute('width'), strength: 'strong' });
    render();
  }));

  async function post(blind, ops) {
    const fd = new FormData();
    fd.set('reviewer_id', document.getElementById('reviewer').value.trim() || 'anonymous');
    fd.set('blind', blind ? '1' : '0'); fd.set('ops', JSON.stringify(ops));
    fd.set('viewed_result_sha256', sha); fd.set('review_seconds', String(Math.round((Date.now() - t0) / 1000)));
    const r = await fetch(`/runs/${runId}/review`, { method: 'POST', body: fd, headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  document.getElementById('finishA').onclick = async () => {
    const ops = marks.map(m => ({ op: 'spot.add', lane_index: m.lane, y_frac: m.y / H, strength: m.strength }));
    try { await post(true, ops); } catch (e) { document.getElementById('err').textContent = e.message; return; }
    passA = false; aided = true;
    document.getElementById('system').hidden = false;
    document.getElementById('passA').hidden = true; document.getElementById('passB').hidden = false;
    document.getElementById('stepA').className = 'done'; document.getElementById('stepB').className = 'on';
    document.getElementById('hint').textContent = 'Pass B: the system’s marks are now shown. Give every row a disposition.';
  };

  document.getElementById('save').onclick = async () => {
    const rows = [...document.querySelectorAll('#passB tbody tr')];
    const ops = [];
    for (const tr of rows) {
      const id = tr.dataset.spot, y = +tr.dataset.y;
      const d = tr.querySelector(`input[name="d_${id}"]:checked`);
      if (!d) { document.getElementById('err').textContent = `Every band needs a disposition (${id}).`; tr.classList.add('missing'); return; }
      if (d.value === 'confirm') ops.push({ op: 'spot.confirm', spot_id: id });
      else if (d.value === 'reject') ops.push({ op: 'spot.reject', spot_id: id, reason: tr.querySelector('select').value });
      else {
        const near = marks.filter(m => Math.abs(m.y - y) / H < 0.05).sort((a, b) => Math.abs(a.y - y) - Math.abs(b.y - y))[0];
        if (!near) { document.getElementById('err').textContent = `No Pass A mark near ${id} to move to.`; return; }
        ops.push({ op: 'spot.move', spot_id: id, y_frac: near.y / H }); near.used = true;
      }
    }
    const sysY = rows.map(tr => +tr.dataset.y);
    marks.filter(m => !m.used && !sysY.some(y => Math.abs(y - m.y) / H <= 0.015))
      .forEach(m => ops.push({ op: 'spot.add', lane_index: m.lane, y_frac: m.y / H, strength: m.strength, note: aided ? 'aided' : null }));
    try { const out = await post(false, ops); document.getElementById('stepB').className = 'done'; document.getElementById('stepS').className = 'on';
      location.href = `/runs/${runId}?saved=${encodeURIComponent(out.label_status)}`; }
    catch (e) { document.getElementById('err').textContent = e.message; }
  };
})();
