/*
 * dashboards.js — дашборды выпусков ЦФА (чистый SVG, без внешних библиотек — работает офлайн).
 *
 *   CFADash.cards(data)                       — мини-метрики и оценка на карточках витрины
 *   CFADash.issuePage(el, issue, bench)       — полная аналитика выпуска (/issues/<id>)
 *   CFADash.compact(el, issue, bench, mode)   — компактный дашборд (конструктор РоВ и модерация)
 *   CFADash.constructor(form, el, bench, ctx) — живой режим: пересчёт при каждом изменении формы
 *
 * Расчёты — в analytics.js. Цвета: синий — сама ЦФА, серый — ориентиры для сравнения,
 * статусные цвета — только вердикт и всегда вместе со значком и подписью.
 */
(function () {
  'use strict';
  var A = window.CFAAnalytics;
  var NS = 'http://www.w3.org/2000/svg';

  // ---------------------------------------------------------------- форматирование
  var nf = function (v, d) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    var p = Math.pow(10, d || 0), r = Math.round(v * p) / p;
    if (r === 0) r = 0;                                   // без «−0»
    return r.toLocaleString('ru-RU', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  };
  var F = {
    rub: function (v) { return nf(Math.round(v)) + ' ₽'; },
    rubShort: function (v) {
      if (v === null || v === undefined) return '—';
      if (Math.abs(v) >= 1e9) return nf(v / 1e9, 1) + ' млрд ₽';
      if (Math.abs(v) >= 1e6) return nf(v / 1e6, 1) + ' млн ₽';
      return nf(Math.round(v)) + ' ₽';
    },
    pct: function (v) { return v === null || v === undefined ? '—' : nf(v, 2) + ' %'; },
    pp: function (v) {
      if (v === null || v === undefined) return '—';
      var r = Math.round(v * 100) / 100;
      return (r > 0 ? '+' : r < 0 ? '−' : '') + nf(Math.abs(r), 2) + ' п.п.';
    },
    term: function (days) {
      if (days === null || days === undefined) return '—';
      var m = Math.round(days / 30.44);
      return m < 24 ? m + ' мес.' : nf(days / 365, 1) + ' г.';
    },
    date: function (iso) { return iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4) : '—'; }
  };

  function h(tag, attrs, html) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) if (attrs[k] !== undefined && attrs[k] !== null) e.setAttribute(k, attrs[k]);
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function s(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ---------------------------------------------------------------- подсказка при наведении
  var tip;
  function ensureTip() {
    if (tip) return tip;
    tip = h('div', { class: 'viz-tip', role: 'tooltip' }); tip.hidden = true; document.body.appendChild(tip); return tip;
  }
  function bindTip(node, html) {
    node.addEventListener('mouseenter', function () { ensureTip(); tip.innerHTML = html; tip.hidden = false; node.classList.add('is-hover'); });
    node.addEventListener('mousemove', function (e) {
      var w = tip.offsetWidth, x = e.clientX + 14, y = e.clientY + 14;
      if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    node.addEventListener('mouseleave', function () { tip.hidden = true; node.classList.remove('is-hover'); });
  }

  // ---------------------------------------------------------------- график: горизонтальные полосы (сравнение доходностей)
  // Ширина рисунка = ширине блока на экране (в пределах), чтобы подписи были одного размера на телефоне и мониторе.
  // При повороте экрана или изменении окна график перерисовывается.
  function fitWidth(el, fallback) {
    var w = el.clientWidth;
    return w > 0 ? Math.round(Math.min(760, Math.max(280, w))) : fallback;
  }
  var redrawList = [], lastVW = window.innerWidth;
  function remember(el, fn) {
    el.__redraw = fn;
    if (redrawList.indexOf(el) < 0) redrawList.push(el);
  }
  window.addEventListener('resize', function () {
    clearTimeout(remember._t);
    remember._t = setTimeout(function () {
      if (Math.abs(window.innerWidth - lastVW) < 30) return;   // мобильная адресная строка меняет только высоту
      lastVW = window.innerWidth;
      redrawList = redrawList.filter(function (el) { return document.body.contains(el); });
      redrawList.forEach(function (el) { el.__redraw(); });
    }, 150);
  });

  function compareChart(el, m, opts) {
    opts = opts || {};
    remember(el, function () { compareChart(el, m, opts); });
    var items = [
      { label: 'Эта ЦФА', value: m.ytm, me: true, note: 'доходность к погашению до НДФЛ' },
      { label: 'ОФЗ на ' + F.term(m.termDays), value: m.ofz, note: 'безрисковый ориентир того же срока' },
      { label: 'Ключевая ставка', value: m.key, note: 'ставка Банка России' },
      { label: 'Вклады', value: m.deposit, note: 'средняя макс. ставка ТОП-10 банков' },
      { label: 'Инфляция', value: m.inflation, note: 'годовая, Росстат' }
    ].filter(function (i) { return i.value !== null && i.value !== undefined; });
    var max = Math.max.apply(null, items.map(function (i) { return i.value; }).concat([1])) * 1.12;
    var W = fitWidth(el, opts.compact ? 360 : 520), rowH = opts.compact ? 30 : 34;
    var labelW = W < 400 ? 116 : 132;
    var H = items.length * rowH + 8, plotW = W - labelW - 64;
    var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'viz-svg', role: 'img',
      'aria-label': 'Сравнение доходности ЦФА с ориентирами' });
    // ориентир ОФЗ — тонкая вертикальная линия через все строки
    if (m.ofz !== null) {
      var xo = labelW + plotW * m.ofz / max;
      svg.appendChild(s('line', { x1: xo, x2: xo, y1: 2, y2: H - 4, class: 'viz-ref' }));
    }
    items.forEach(function (it, i) {
      var y = i * rowH + 6, w = Math.max(2, plotW * it.value / max);
      var g = s('g', { class: 'viz-hit' });
      var hit = s('rect', { x: 0, y: y - 4, width: W, height: rowH, fill: 'transparent' });
      var lab = s('text', { x: labelW - 10, y: y + 15, 'text-anchor': 'end', class: it.me ? 'viz-label viz-label--strong' : 'viz-label' });
      lab.textContent = it.label;
      var bar = s('rect', { x: labelW, y: y + 2, width: w, height: 18, rx: 4, class: it.me ? 'viz-bar viz-bar--me' : 'viz-bar viz-bar--bench' });
      var val = s('text', { x: labelW + w + 8, y: y + 15, class: it.me ? 'viz-value viz-value--strong' : 'viz-value' });
      val.textContent = F.pct(it.value);
      g.appendChild(hit); g.appendChild(lab); g.appendChild(bar); g.appendChild(val);
      bindTip(g, '<b>' + esc(it.label) + '</b><br>' + F.pct(it.value) + '<br><span>' + esc(it.note) + '</span>');
      svg.appendChild(g);
    });
    el.innerHTML = ''; el.appendChild(svg);
  }

  // ---------------------------------------------------------------- график: столбцы выплат
  function scheduleChart(el, rows, opts) {
    opts = opts || {};
    remember(el, function () { scheduleChart(el, rows, opts); });
    if (!rows.length) { el.innerHTML = '<p class="viz-empty">Нет данных для графика</p>'; return; }
    var W = fitWidth(el, opts.compact ? 360 : 520), H = opts.compact ? 170 : 190, padL = 8, padR = 8, padT = 22, padB = 30;
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; })) || 1;
    var n = rows.length, plotW = W - padL - padR, plotH = H - padT - padB;
    var gap = n > 40 ? 1 : 2, bw = Math.max(2, Math.min(46, plotW / n - gap));
    var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'viz-svg', role: 'img', 'aria-label': opts.aria || 'График выплат' });
    svg.appendChild(s('line', { x1: padL, x2: W - padR, y1: H - padB, y2: H - padB, class: 'viz-axis' }));
    var total = 0;
    rows.forEach(function (r, i) {
      total += r.value;
      var x = padL + (plotW / n) * i + (plotW / n - bw) / 2;
      var bh = Math.max(2, plotH * r.value / max), y = H - padB - bh;
      var g = s('g', { class: 'viz-hit' });
      g.appendChild(s('rect', { x: padL + (plotW / n) * i, y: padT, width: plotW / n, height: plotH, fill: 'transparent' }));
      // столбец с закруглённым верхом, прижатый к оси
      g.appendChild(s('path', { class: 'viz-bar viz-bar--me', d: roundTop(x, y, bw, bh, Math.min(4, bw / 2)) }));
      bindTip(g, '<b>' + F.date(r.date) + '</b><br>' + esc(opts.valueLabel || 'Выплата') + ': ' + F.rub(r.value) +
        '<br><span>накопленно: ' + F.rub(total) + '</span>');
      svg.appendChild(g);
    });
    // подписи оси: первая и последняя даты (и середина, если выплат много)
    var ticks = n === 1 ? [0] : n < 6 ? rows.map(function (_, i) { return i; }) : [0, Math.floor((n - 1) / 2), n - 1];
    ticks.forEach(function (i, k) {
      var anchor = ticks.length > 1 && k === 0 ? 'start' : ticks.length > 1 && k === ticks.length - 1 ? 'end' : 'middle';
      var tx = anchor === 'start' ? padL : anchor === 'end' ? W - padR : padL + (plotW / n) * (i + 0.5);
      var t = s('text', { x: tx, y: H - 10, 'text-anchor': anchor, class: 'viz-tick' });
      t.textContent = F.date(rows[i].date); svg.appendChild(t);
    });
    var mx = s('text', { x: padL, y: 13, class: 'viz-tick' });
    mx.textContent = 'макс. ' + F.rub(max); svg.appendChild(mx);
    el.innerHTML = ''; el.appendChild(svg);
    if (opts.footnote) el.appendChild(h('p', { class: 'viz-foot' }, opts.footnote));
  }
  function roundTop(x, y, w, hgt, r) {
    r = Math.min(r, hgt);
    return 'M' + x + ',' + (y + hgt) + ' V' + (y + r) + ' Q' + x + ',' + y + ' ' + (x + r) + ',' + y +
      ' H' + (x + w - r) + ' Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + ' V' + (y + hgt) + ' Z';
  }

  // ---------------------------------------------------------------- оценка
  function verdictHTML(sc, withNumber) {
    return '<span class="verdict verdict--' + sc.verdict.key + '">' +
      '<span aria-hidden="true">' + sc.verdict.icon + '</span> ' + (withNumber ? sc.total + '/100 · ' : '') + sc.verdict.label + '</span>';
  }
  function scoreBlock(el, sc) {
    var segs = '<div class="score-track" aria-hidden="true"><span class="score-fill score-fill--' + sc.verdict.key +
      '" style="width:' + sc.total + '%"></span><i style="left:50%"></i><i style="left:70%"></i></div>' +
      '<div class="score-scale"><span>0</span><span style="left:50%">50</span><span style="left:70%">70</span><span style="left:100%">100</span></div>';
    el.innerHTML = '<div class="score-head"><div class="score-num">' + sc.total + '<small>/100</small></div>' + verdictHTML(sc) + '</div>' + segs;
  }
  function partsList(el, sc) {
    el.innerHTML = sc.parts.map(function (p) {
      var share = p.max ? p.pts / p.max : 0;
      return '<div class="part-row"><div class="part-row__head"><span>' + esc(p.name) + '</span><b>' + p.pts + ' из ' + p.max + '</b></div>' +
        '<div class="part-bar"><span style="width:' + Math.round(share * 100) + '%"></span></div>' +
        '<small>' + esc(p.note) + '</small></div>';
    }).join('');
  }

  function tiles(el, items) {
    el.innerHTML = items.map(function (t) {
      return '<div class="kpi' + (t.accent ? ' kpi--accent' : '') + '"><span>' + esc(t.label) + '</span><b>' + t.value + '</b>' +
        (t.sub ? '<small>' + t.sub + '</small>' : '') + '</div>';
    }).join('');
  }

  // ---------------------------------------------------------------- карточки витрины
  function cards(data) {
    document.querySelectorAll('[data-analytics]').forEach(function (box) {
      var x = data.issues[box.dataset.analytics];
      if (!x) return;
      var m = A.analyze(x, data.bench);
      if (!m.ok) { box.innerHTML = ''; return; }
      box.innerHTML =
        '<div class="mini-metrics">' +
        '<div><span>Доходность к погашению</span><b>' + F.pct(m.ytm) + '</b></div>' +
        '<div><span>Премия к ОФЗ</span><b class="' + (m.spreadOfz < 0 ? 'neg' : '') + '">' + F.pp(m.spreadOfz) + '</b></div>' +
        '<div><span>Срок</span><b>' + F.term(m.termDays) + '</b></div>' +
        '</div><div class="mini-verdict">' + verdictHTML(m.score, true) + '</div>';
    });
  }

  // ---------------------------------------------------------------- компактный дашборд
  function compact(el, x, bench, mode) {
    var m = A.analyze(x, bench, { buyDate: x.placementStart });
    if (!m.ok || !(x.rate > 0)) {
      el.innerHTML = '<div class="dash-empty"><b>Дашборд появится автоматически</b><span>Заполните ставку дохода, цену, ' +
        'период размещения и дату погашения — доходность, сравнение с ОФЗ и вкладами, график выплат и оценка ' +
        'пересчитываются на лету при каждом изменении.</span></div>';
      return m;
    }
    el.innerHTML =
      '<div class="dash-block" data-k="score"></div>' +
      '<div class="kpi-grid kpi-grid--2" data-k="kpi"></div>' +
      '<div class="dash-block"><h4>Доходность против альтернатив</h4><div data-k="cmp"></div></div>' +
      '<div class="dash-block"><h4>' + (mode === 'issuer' ? 'Купонные выплаты эмитента (при полном размещении)' : 'Выплаты на 1 ЦФА') +
      '</h4><div data-k="sch"></div></div>' +
      '<div class="dash-block"><h4>Из чего складывается оценка</h4><div data-k="parts"></div></div>' +
      '<p class="viz-note">Ориентиры на ' + F.date(bench.as_of) + '. Модель для сравнения, не инвестиционная рекомендация.</p>';
    var q = function (k) { return el.querySelector('[data-k="' + k + '"]'); };
    scoreBlock(q('score'), m.score);
    tiles(q('kpi'), [
      { label: 'Доходность для инвестора', value: F.pct(m.ytm), sub: 'эффективная, к погашению', accent: true },
      { label: 'Премия к ОФЗ ' + F.term(m.termDays), value: F.pp(m.spreadOfz), sub: 'ОФЗ: ' + F.pct(m.ofz) },
      { label: 'Объём выпуска', value: F.rubShort(m.volume), sub: x.maxQty ? nf(x.maxQty) + ' шт. × ' + F.rub(x.price) : '' },
      { label: 'Стоимость заимствования', value: F.rubShort(m.issuerCost), sub: 'купоны эмитента за весь срок' }
    ]);
    compareChart(q('cmp'), m, { compact: true });
    if (mode === 'issuer') {
      scheduleChart(q('sch'), m.issuerSchedule.map(function (r) { return { date: r.date, value: r.amount }; }),
        { compact: true, valueLabel: 'Купоны эмитента', aria: 'График купонных выплат эмитента',
          footnote: 'И номинал ' + F.rubShort(m.volume) + ' в дату погашения ' + F.date(x.maturity) + '.' });
    } else {
      scheduleChart(q('sch'), m.schedule.map(function (r) { return { date: r.date, value: r.coupon }; }),
        { compact: true, valueLabel: 'Купон', footnote: 'И номинал ' + F.rub(x.price) + ' в дату погашения ' + F.date(x.maturity) + '.' });
    }
    partsList(q('parts'), m.score);
    return m;
  }

  // ---------------------------------------------------------------- живой режим конструктора
  function constructor(form, el, bench, ctx) {
    function val(n) { var f = form.querySelector('[name="' + n + '"]'); return f ? f.value.trim() : ''; }
    function num(n) { var v = parseFloat(val(n).replace(/\s/g, '').replace(',', '.')); return isNaN(v) ? 0 : v; }
    function read() {
      return {
        price: num('price_per_cfa'), rate: num('coupon_rate'), period: val('coupon_period'),
        placementStart: val('placement_start'), placementEnd: val('placement_end'), maturity: val('maturity_date'),
        collateral: val('collateral_type') !== '' && val('collateral_type') !== 'none', buyback: val('buyback') === 'yes',
        minQty: num('min_qty'), maxQty: num('max_qty'), minOrder: num('min_order_qty') || 1, maxOrder: num('max_order_qty') || null,
        collected: null, showDemand: false, issuerIssued: ctx.issued, issuerCancelled: ctx.cancelled
      };
    }
    var timer;
    function update() { clearTimeout(timer); timer = setTimeout(function () { compact(el, read(), bench, 'issuer'); }, 120); }
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    compact(el, read(), bench, 'issuer');
  }

  // ---------------------------------------------------------------- полная страница выпуска
  function issuePage(el, x, bench) {
    var m = A.analyze(x, bench);
    if (!m.ok) { el.innerHTML = '<p class="viz-empty">Недостаточно данных для расчёта</p>'; return; }
    var q = function (k) { return el.querySelector('[data-k="' + k + '"]'); };
    scoreBlock(q('score'), m.score);
    tiles(q('kpi'), [
      { label: 'Доходность к погашению', value: F.pct(m.ytm), sub: 'эффективная, до НДФЛ', accent: true },
      { label: 'После НДФЛ ' + nf(m.ndfl) + ' %', value: F.pct(m.ytmNet),
        sub: m.frozenDays > 0 ? 'с учётом заморозки денег на ' + m.frozenDays + ' дн.' : 'при заявке сейчас' },
      { label: 'Премия к ОФЗ ' + F.term(m.termDays), value: F.pp(m.spreadOfz), sub: 'ОФЗ того же срока: ' + F.pct(m.ofz) },
      { label: 'Реальная доходность', value: F.pp(m.realYield), sub: 'после НДФЛ минус инфляция ' + F.pct(m.inflation) },
      { label: 'Срок до погашения', value: F.term(m.termDays), sub: 'погашение ' + F.date(x.maturity) },
      { label: 'Доход на 1 ЦФА', value: F.rub(m.couponTotal), sub: m.schedule.length + ' выпл. · номинал ' + F.rub(x.price) }
    ]);
    compareChart(q('cmp'), m);
    scheduleChart(q('sch'), m.schedule.map(function (r) { return { date: r.date, value: r.coupon }; }),
      { valueLabel: 'Купон', footnote: 'Плюс возврат номинала ' + F.rub(x.price) + ' в дату погашения ' + F.date(x.maturity) + '.' });
    partsList(q('parts'), m.score);

    // таблица выплат (доступная альтернатива графику)
    q('table').innerHTML = '<table class="ui-table ui-table--plain ui-table--compact"><thead><tr><th>Дата</th><th>Дней</th><th>Купон</th><th>Номинал</th></tr></thead><tbody>' +
      m.schedule.map(function (r) {
        return '<tr><td>' + F.date(r.date) + '</td><td>' + r.days + '</td><td>' + F.rub(r.coupon) + '</td><td>' + (r.principal ? F.rub(r.principal) : '—') + '</td></tr>';
      }).join('') + '</tbody></table>';

    // спрос
    var left = Math.max(0, A.days(A.D(A.iso(new Date())), A.D(x.placementEnd)));
    tiles(q('demand'), [
      { label: 'Заявлено', value: nf(x.collected) + ' из ' + nf(x.maxQty) + ' шт.', sub: Math.round(100 * x.collected / x.maxQty) + ' % объёма · минимум ' + nf(x.minQty) },
      { label: 'Инвесторов', value: nf(x.investors), sub: 'заявок: ' + nf(x.orders) },
      { label: 'Средняя заявка', value: x.orders ? F.rub(x.avgOrder) : '—', sub: '' },
      { label: x.status === 'SUBSCRIPTION' ? 'До конца приёма заявок' : 'Сбор заявок', value: x.status === 'SUBSCRIPTION' ? left + ' дн.' : 'завершён', sub: F.date(x.placementStart) + ' — ' + F.date(x.placementEnd) }
    ]);

    // калькулятор
    var input = q('amount');
    function calc() {
      var amount = parseFloat(String(input.value).replace(/\s/g, '').replace(',', '.')) || 0;
      var c = m.forAmount(amount);
      if (c.belowMin) {
        q('calc').innerHTML = '<p class="viz-empty">Минимальная заявка — ' + nf(x.minOrder) + ' ЦФА, это ' + F.rub(x.minOrder * x.price) + '.</p>';
        return;
      }
      tiles(q('calc'), [
        { label: 'Купите ЦФА', value: nf(c.qty) + ' шт.', sub: 'на ' + F.rub(c.invested) },
        { label: 'Купонный доход после НДФЛ', value: F.rub(c.couponsNet), sub: 'за ' + F.term(c.horizonDays) + ' с даты заявки', accent: true },
        { label: 'Вклад под ' + F.pct(m.deposit), value: F.rub(c.depositNet), sub: 'та же сумма и срок, после НДФЛ' },
        { label: 'Разница с вкладом', value: (c.diffNet >= 0 ? '+' : '−') + F.rub(Math.abs(c.diffNet)),
          sub: c.diffNet >= 0 ? 'ЦФА выгоднее вклада' : 'вклад выгоднее этой ЦФА' }
      ]);
    }
    input.addEventListener('input', calc);
    el.querySelectorAll('[data-amount]').forEach(function (b) {
      b.addEventListener('click', function () { input.value = b.dataset.amount; calc(); });
    });
    calc();
  }

  window.CFADash = { cards: cards, issuePage: issuePage, compact: compact, constructor: constructor, fmt: F };
})();
