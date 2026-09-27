/* Policy Pulse — progressive behaviour. No framework, no tracking, nothing leaves the browser. */
(function () {
  'use strict';
  var d = document, body = d.body;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) body.classList.add('no-motion');
  function get(k, f) { try { var v = localStorage.getItem(k); return v === null ? f : JSON.parse(v); } catch (e) { return f; } }
  function put(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function del(k) { try { localStorage.removeItem(k); } catch (e) {} }

  /* ---- sound: opt-in, synthesized, tiny ---- */
  var soundOn = get('pulse-sound', false), ctx = null;
  function tick(freq, dur, vol) {
    if (!soundOn) return;
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      var o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime;
      o.type = 'sine'; o.frequency.setValueAtTime(freq, t);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol || 0.08, t + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t + (dur || 0.09));
      o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + (dur || 0.09) + 0.02);
    } catch (e) {}
  }
  var soundBtn = d.querySelector('[data-sound]');
  function paintSound() { if (!soundBtn) return; soundBtn.classList.toggle('on', soundOn); soundBtn.textContent = soundOn ? soundBtn.dataset.on : soundBtn.dataset.off; soundBtn.setAttribute('aria-pressed', soundOn); }
  if (soundBtn) { soundBtn.addEventListener('click', function () { soundOn = !soundOn; put('pulse-sound', soundOn); paintSound(); tick(soundOn ? 880 : 440, 0.12, 0.1); }); paintSound(); }

  /* ---- theme: light / dark (system default) ---- */
  var tbtn = d.querySelector('[data-theme-toggle]'), root = d.documentElement;
  function effective() { var t = root.getAttribute('data-theme'); if (t) return t; return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'; }
  function paintTheme() { if (!tbtn) return; var e = effective(); tbtn.textContent = e === 'dark' ? '☾' : '☀'; tbtn.setAttribute('aria-label', e === 'dark' ? tbtn.dataset.light : tbtn.dataset.dark); }
  if (tbtn) { tbtn.addEventListener('click', function () { var next = effective() === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', next); put('pulse-theme', next); paintTheme(); tick(next === 'dark' ? 392 : 784, 0.12, 0.07); }); paintTheme(); }

  /* ---- reading progress + sticky header state ---- */
  var prog = d.querySelector('.progress'), top = d.querySelector('.top');
  function onScroll() {
    var h = d.documentElement, max = h.scrollHeight - h.clientHeight;
    if (prog) prog.style.transform = 'scaleX(' + (max > 0 ? Math.min(1, h.scrollTop / max) : 0) + ')';
    if (top) top.classList.toggle('scrolled', h.scrollTop > 8);
  }
  d.addEventListener('scroll', onScroll, { passive: true }); onScroll();

  /* ---- reveal on scroll ---- */
  var io = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 }) : null;
  d.querySelectorAll('.reveal').forEach(function (el, i) { if (io && !reduce) { el.style.transitionDelay = Math.min(i % 4, 3) * 60 + 'ms'; io.observe(el); } else { el.classList.add('in'); } });

  /* ---- active chapter in the top nav ---- */
  var links = d.querySelectorAll('.chapters a[href*="#"]');
  if (links.length && 'IntersectionObserver' in window) {
    var map = {};
    links.forEach(function (a) { var id = a.getAttribute('href').split('#')[1]; if (id && d.getElementById(id)) map[id] = a; });
    var cio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { links.forEach(function (a) { a.classList.remove('on'); }); var a = map[e.target.id]; if (a) a.classList.add('on'); } });
    }, { rootMargin: '-40% 0px -55% 0px' });
    Object.keys(map).forEach(function (id) { var s = d.getElementById(id); if (s) cio.observe(s); });
  }

  /* ---- count-up numbers ---- */
  function countUp(el) {
    var target = parseFloat(el.dataset.count), prefix = el.dataset.prefix || '', suffix = el.dataset.suffix || '';
    if (isNaN(target) || reduce) { el.textContent = prefix + target + suffix; return; }
    var start = performance.now(), dur = 900;
    function step(now) {
      var p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = prefix + Math.round(target * e) + suffix;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  var nio = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { countUp(e.target); nio.unobserve(e.target); } });
  }, { threshold: 0.4 }) : null;
  d.querySelectorAll('[data-count]').forEach(function (el) { if (nio) nio.observe(el); else countUp(el); });

  /* ---- animated accordions ---- */
  d.querySelectorAll('details.acc').forEach(function (det) {
    var sum = det.querySelector('summary'), bodyEl = det.querySelector('.acc-body');
    if (!sum || !bodyEl) return;
    sum.addEventListener('click', function (ev) {
      if (reduce) { tick(520, 0.06, 0.05); return; }
      ev.preventDefault();
      if (det.hasAttribute('open')) {
        var h = bodyEl.offsetHeight;
        var anim = bodyEl.animate([{ height: h + 'px', opacity: 1 }, { height: '0px', opacity: 0 }], { duration: 260, easing: 'cubic-bezier(.2,.7,.2,1)' });
        bodyEl.style.overflow = 'hidden';
        anim.onfinish = function () { det.removeAttribute('open'); bodyEl.style.overflow = ''; };
        tick(440, 0.06, 0.05);
      } else {
        det.setAttribute('open', '');
        var h2 = bodyEl.offsetHeight;
        bodyEl.style.overflow = 'hidden';
        var a2 = bodyEl.animate([{ height: '0px', opacity: 0 }, { height: h2 + 'px', opacity: 1 }], { duration: 320, easing: 'cubic-bezier(.2,.7,.2,1)' });
        a2.onfinish = function () { bodyEl.style.overflow = ''; };
        tick(620, 0.07, 0.05);
      }
    });
  });

  /* ---- copy template ---- */
  d.querySelectorAll('button.copy').forEach(function (b) {
    b.addEventListener('click', function () {
      var pre = b.previousElementSibling;
      if (navigator.clipboard && pre) navigator.clipboard.writeText(pre.innerText).then(function () { b.textContent = b.dataset.copied; tick(990, 0.1, 0.08); });
    });
  });

  /* ---- persona lens ---- */
  var PK = 'pulse-persona';
  function setPersona(p) {
    if (p) body.setAttribute('data-persona', p); else body.removeAttribute('data-persona');
    d.querySelectorAll('.chip[data-persona]').forEach(function (c) { c.classList.toggle('on', (c.dataset.persona || '') === (p || '')); });
    if (p) put(PK, p); else del(PK);
  }
  d.querySelectorAll('.chip[data-persona]').forEach(function (c) { c.addEventListener('click', function () { setPersona(c.dataset.persona); tick(700, 0.07, 0.06); }); });
  setPersona(get(PK, ''));

  /* ---- impact lens ---- */
  var LK = 'pulse-lens';
  function setLens(l) {
    if (l) body.setAttribute('data-lens', l); else body.removeAttribute('data-lens');
    d.querySelectorAll('.chip[data-lens]').forEach(function (c) { c.classList.toggle('on', (c.dataset.lens || '') === (l || '')); });
    d.querySelectorAll('[data-impacts]').forEach(function (el) {
      var has = !l || (' ' + (el.dataset.impacts || '') + ' ').indexOf(' ' + l + ' ') >= 0;
      el.classList.toggle('lens-out', !has);
    });
    if (l) put(LK, l); else del(LK);
  }
  d.querySelectorAll('.chip[data-lens]').forEach(function (c) { c.addEventListener('click', function () { setLens(c.dataset.lens); tick(560, 0.07, 0.06); }); });
  setLens(get(LK, ''));

  /* ---- follow + since your last visit ---- */
  var stateEl = d.getElementById('pulse-state');
  var S = stateEl ? JSON.parse(stateEl.textContent) : null;
  var FK = 'pulse-follow', SK = 'pulse-snapshot', follows = get(FK, []);
  function paintFollow() { d.querySelectorAll('.follow').forEach(function (b) { var on = follows.indexOf(b.dataset.slug) >= 0; b.textContent = on ? b.dataset.following : b.dataset.follow; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); }); }
  d.querySelectorAll('.follow').forEach(function (b) { b.addEventListener('click', function () { var i = follows.indexOf(b.dataset.slug); if (i >= 0) follows.splice(i, 1); else follows.push(b.dataset.slug); put(FK, follows); paintFollow(); tick(i >= 0 ? 500 : 860, 0.09, 0.08); }); });
  paintFollow();
  var box = d.getElementById('since');
  if (box && S) {
    var L = JSON.parse(d.getElementById('pulse-strings').textContent), list = box.querySelector('ul'), h = box.querySelector('.kicker'), hint = box.querySelector('.hint');
    var snap = get(SK, null), show = false;
    if (snap) {
      h.textContent = L.since_h.replace('{d}', snap.date);
      var slugs = follows.length ? follows : Object.keys(S.topics);
      slugs.forEach(function (sl) {
        var now = S.topics[sl], was = snap.topics[sl]; if (!now) return;
        var newN = was ? now.latest.filter(function (u) { return was.latest.indexOf(u) < 0; }).length : 0, parts = [];
        if (newN) parts.push(L.since_new.replace('{n}', newN));
        if (was && was.status !== now.status) parts.push(L.since_status.replace('{status}', now.status_label).replace('{old}', was.status_label));
        if (!parts.length) { if (follows.length) parts.push(L.since_same); else return; }
        var li = d.createElement('li'); li.innerHTML = '<a href="' + now.url + '"><b>' + now.name + '</b></a> — ' + parts.join(' · '); list.appendChild(li); show = true;
      });
      hint.textContent = follows.length ? '' : L.since_none_followed;
      if (show) box.hidden = false;
    }
    var t = {}; Object.keys(S.topics).forEach(function (k) { t[k] = { status: S.topics[k].status, status_label: S.topics[k].status_label, latest: S.topics[k].latest }; });
    put(SK, { date: S.today, topics: t });
  }

  /* ---- language memory ---- */
  d.querySelectorAll('.lang-toggle').forEach(function (a) { a.addEventListener('click', function () { put('radar-lang', a.getAttribute('lang')); }); });
})();
