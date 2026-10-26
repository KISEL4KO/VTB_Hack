/*
 * analytics.js — инвестиционная аналитика выпуска ЦФА.
 *
 * Чистые функции без доступа к странице: одинаково работают в браузере (витрина, страница выпуска,
 * живой дашборд конструктора, модерация) и в Node (тесты). Рисование графиков — в dashboards.js.
 *
 * Что считаем для выпуска с фиксированным доходом:
 *   • график выплат на 1 ЦФА (купоны по периодам + номинал в дату погашения);
 *   • доходность к погашению (XIRR, эффективная годовая) — до и после НДФЛ;
 *   • доходность с учётом «заморозки»: деньги блокируются с момента заявки, а купон начисляется
 *     только после закрытия сбора (DvP);
 *   • премию к ОФЗ того же срока (плата за риск эмитента), к ключевой ставке и вкладам, реальную доходность;
 *   • оценку 0–100 из прозрачных составляющих и калькулятор «если вложить N ₽».
 *
 * Это модель для сравнения, а не инвестиционная рекомендация.
 */
(function (root) {
  'use strict';

  var DAY = 86400000;

  // ---------------------------------------------------------------- даты
  function D(iso) {                       // 'ГГГГ-ММ-ДД' → Date (UTC, без часовых поясов)
    if (!iso || !/^\d{4}-\d{2}-\d{2}/.test(iso)) return null;
    var p = iso.slice(0, 10).split('-');
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
  }
  function iso(d) { return d.toISOString().slice(0, 10); }
  function days(a, b) { return Math.round((b - a) / DAY); }
  function addMonths(d, n) {               // 31 января + 1 мес. → 28/29 февраля
    var y = d.getUTCFullYear(), m = d.getUTCMonth() + n, day = d.getUTCDate();
    var last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return new Date(Date.UTC(y, m, Math.min(day, last)));
  }
  function todayISO() { var t = new Date(); return iso(new Date(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()))); }

  // ---------------------------------------------------------------- график выплат
  /**
   * p = {price, rate (% годовых), period: 'maturity'|'quarterly'|'monthly', start (начало начисления), maturity}
   * → [{date, days, coupon, principal}] на 1 ЦФА. Купон = номинал × ставка × дни / 365.
   */
  function schedule(p) {
    var start = D(p.start), mat = D(p.maturity);
    if (!start || !mat || mat <= start || !(p.price > 0) || !(p.rate >= 0)) return [];
    var step = p.period === 'monthly' ? 1 : p.period === 'quarterly' ? 3 : 0;
    var dates = [];
    if (step) {
      for (var k = 1; k < 1300; k++) {
        var d = addMonths(start, step * k);
        if (days(d, mat) < 15) break;        // короткий хвост (< 15 дней) сливаем с последним периодом
        dates.push(d);
      }
    }
    dates.push(mat);
    var prev = start, out = [];
    dates.forEach(function (d, i) {
      var n = days(prev, d);
      out.push({ date: iso(d), days: n, coupon: p.price * p.rate / 100 * n / 365,
                 principal: i === dates.length - 1 ? p.price : 0 });
      prev = d;
    });
    return out;
  }

  // ---------------------------------------------------------------- XIRR
  /** flows = [{date, amount}] (первый — отрицательный). Эффективная годовая ставка или null. */
  function xirr(flows) {
    if (!flows.length) return null;
    var t0 = D(flows[0].date);
    var ts = flows.map(function (f) { return days(t0, D(f.date)) / 365; });
    function npv(r) { var s = 0; for (var i = 0; i < flows.length; i++) s += flows[i].amount / Math.pow(1 + r, ts[i]); return s; }
    var lo = -0.95, hi = 10;
    if (npv(lo) * npv(hi) > 0) return null;
    for (var it = 0; it < 200; it++) {         // бисекция: медленнее Ньютона, зато никогда не «улетает»
      var mid = (lo + hi) / 2, v = npv(mid);
      if (Math.abs(v) < 1e-9) return mid;
      if (npv(lo) * v < 0) hi = mid; else lo = mid;
    }
    return (lo + hi) / 2;
  }

  // ---------------------------------------------------------------- ориентиры
  /** Доходность ОФЗ для срока (лет) — линейная интерполяция по кривой [[лет, %], …]. */
  function ofzYield(curve, years) {
    if (!curve || !curve.length) return null;
    var c = curve.slice().sort(function (a, b) { return a[0] - b[0]; });
    if (years <= c[0][0]) return c[0][1];
    if (years >= c[c.length - 1][0]) return c[c.length - 1][1];
    for (var i = 1; i < c.length; i++) {
      if (years <= c[i][0]) {
        var a = c[i - 1], b = c[i];
        return a[1] + (b[1] - a[1]) * (years - a[0]) / (b[0] - a[0]);
      }
    }
    return c[c.length - 1][1];
  }

  /** Кусочно-линейная функция по точкам [[x, y], …]. */
  function piecewise(points, x) {
    if (x <= points[0][0]) return points[0][1];
    for (var i = 1; i < points.length; i++) {
      if (x <= points[i][0]) {
        var a = points[i - 1], b = points[i];
        return a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]);
      }
    }
    return points[points.length - 1][1];
  }

  // ---------------------------------------------------------------- оценка 0–100
  var VERDICTS = [
    { min: 70, key: 'good', label: 'Привлекательно', icon: '▲' },
    { min: 50, key: 'warning', label: 'Умеренно', icon: '●' },
    { min: 0, key: 'critical', label: 'Слабо', icon: '▼' }
  ];

  function score(m, x) {
    var parts = [];
    function part(name, pts, max, note) { parts.push({ name: name, pts: Math.max(0, Math.min(max, Math.round(pts))), max: max, note: note }); }

    // 1. Премия к ОФЗ того же срока — главная плата за риск эмитента (35)
    var s = m.spreadOfz;
    part('Премия к ОФЗ того же срока', s === null ? 0 : piecewise([[-1, 0], [0, 8], [2, 24], [3, 35], [7, 35], [10, 25], [14, 10], [20, 0]], s), 35,
         s === null ? 'нет данных' :
         s < 0 ? 'ниже ОФЗ: государство платит больше без риска эмитента' :
         s < 2 ? 'небольшая премия за риск эмитента' :
         s <= 7 ? 'нормальная премия за риск эмитента' :
         'аномально высокая премия — признак повышенного риска');
    // 2. Срок (15): чем короче, тем меньше неопределённости
    var y = m.termYears;
    part('Срок до погашения', y <= 1 ? 15 : y <= 2 ? 11 : y <= 3 ? 7 : y <= 5 ? 4 : 2, 15, y.toFixed(1).replace('.', ',') + ' г.');
    // 3. Обеспечение (12)
    part('Обеспечение', x.collateral ? 12 : 0, 12, x.collateral ? 'предусмотрено' : 'не предусмотрено');
    // 4. Спрос (12)
    if (x.maxQty > 0 && x.collected !== null && x.collected !== undefined && x.showDemand) {
      var r = x.collected / x.maxQty;
      part('Спрос инвесторов', 12 * Math.min(1, r / 0.8), 12, Math.round(r * 100) + ' % объёма заявлено');
    } else {
      part('Спрос инвесторов', 6, 12, 'появится после открытия сбора');
    }
    // 5. История эмитента (12)
    var issued = x.issuerIssued || 0, cancelled = x.issuerCancelled || 0;
    part('История эмитента', issued + cancelled === 0 ? 4 : Math.min(issued, 3) * 4 - cancelled * 3, 12,
         issued + cancelled === 0 ? 'первый выпуск на платформе' : 'размещено ' + issued + ', отменено ' + cancelled);
    // 6. Частота выплат (6): ранние деньги снижают риск
    part('Частота выплат', x.period === 'monthly' ? 6 : x.period === 'quarterly' ? 4 : 1, 6,
         x.period === 'monthly' ? 'ежемесячно' : x.period === 'quarterly' ? 'ежеквартально' : 'всё в дату погашения');
    // 7. Выкуп эмитентом (4): возможность выйти досрочно
    part('Выкуп эмитентом', x.buyback ? 4 : 0, 4, x.buyback ? 'предусмотрен' : 'не предусмотрен');
    // 8. Реальная доходность (4): после НДФЛ и инфляции
    var real = m.realYield;
    part('Реальная доходность', real === null ? 0 : real >= 5 ? 4 : real >= 2 ? 2 : real >= 0 ? 1 : 0, 4,
         real === null ? 'нет данных' : (real >= 0 ? '+' : '') + real.toFixed(1).replace('.', ',') + ' п.п. к инфляции');

    var total = parts.reduce(function (a, p) { return a + p.pts; }, 0);
    var v = VERDICTS.filter(function (v) { return total >= v.min; })[0];
    return { total: total, parts: parts, verdict: v };
  }

  // ---------------------------------------------------------------- главный расчёт
  /**
   * x — параметры выпуска: {price, rate, period, placementStart, placementEnd, maturity, collateral, buyback,
   *     minQty, maxQty, minOrder, maxOrder, collected, showDemand, issuerIssued, issuerCancelled}
   * b — ориентиры: {key_rate, deposit_rate, inflation, ndfl, ofz_curve}
   * opts.buyDate — дата заявки (по умолчанию: сегодня, но не раньше начала размещения)
   */
  function analyze(x, b, opts) {
    opts = opts || {};
    var ok = x.price > 0 && x.rate >= 0 && D(x.placementEnd) && D(x.maturity) && D(x.maturity) > D(x.placementEnd);
    if (!ok) return { ok: false };
    var start = x.placementEnd;                 // купон начисляется с закрытия сбора (DvP)
    var buy = opts.buyDate || todayISO();
    if (D(x.placementStart) && buy < x.placementStart) buy = x.placementStart;
    if (buy > start) buy = start;

    var sch = schedule({ price: x.price, rate: x.rate, period: x.period, start: start, maturity: x.maturity });
    var couponTotal = sch.reduce(function (a, r) { return a + r.coupon; }, 0);
    var ndfl = (b.ndfl === undefined ? 13 : b.ndfl) / 100;

    function flowsFrom(date, tax) {
      return [{ date: date, amount: -x.price }].concat(sch.map(function (r) {
        return { date: r.date, amount: r.coupon * (1 - tax) + r.principal };
      }));
    }
    var termDays = days(D(start), D(x.maturity));
    var termYears = termDays / 365;
    var ytm = xirr(flowsFrom(start, 0));
    var ytmFrozen = xirr(flowsFrom(buy, 0));
    var ytmNet = xirr(flowsFrom(buy, ndfl));
    var pct = function (r) { return r === null ? null : r * 100; };
    var ofz = ofzYield(b.ofz_curve, termYears);

    var m = {
      ok: true, schedule: sch, couponTotal: couponTotal, termDays: termDays, termYears: termYears,
      buyDate: buy, frozenDays: days(D(buy), D(start)),
      ytm: pct(ytm), ytmFrozen: pct(ytmFrozen), ytmNet: pct(ytmNet), ofz: ofz,
      key: b.key_rate, deposit: b.deposit_rate, inflation: b.inflation, ndfl: ndfl * 100
    };
    m.spreadOfz = m.ytm === null || ofz === null ? null : m.ytm - ofz;
    m.spreadKey = m.ytm === null ? null : m.ytm - b.key_rate;
    m.spreadDeposit = m.ytm === null ? null : m.ytm - b.deposit_rate;
    m.realYield = m.ytmNet === null ? null : m.ytmNet - b.inflation;
    m.score = score(m, x);
    m.volume = x.maxQty > 0 ? x.maxQty * x.price : null;
    m.issuerCost = x.maxQty > 0 ? x.maxQty * couponTotal : null;   // сколько эмитент заплатит купонами при полном размещении
    m.issuerSchedule = x.maxQty > 0 ? sch.map(function (r) { return { date: r.date, amount: r.coupon * x.maxQty }; }) : [];

    /** «Если вложить amount ₽» с учётом ограничений на заявку и остатка объёма. */
    m.forAmount = function (amount) {
      var qty = Math.floor(amount / x.price);
      if (x.maxOrder) qty = Math.min(qty, x.maxOrder);
      if (x.maxQty > 0) qty = Math.min(qty, x.maxQty - (x.collected || 0));
      if (qty < (x.minOrder || 1)) qty = 0;
      var invested = qty * x.price;
      var coupons = qty * couponTotal;
      var horizon = days(D(buy), D(x.maturity));
      var dep = invested * b.deposit_rate / 100 * horizon / 365;    // вклад на тот же срок с той же даты
      return {
        qty: qty, invested: invested, coupons: coupons, couponsNet: coupons * (1 - ndfl),
        deposit: dep, depositNet: dep * (1 - ndfl), diffNet: (coupons - dep) * (1 - ndfl),
        horizonDays: horizon, belowMin: qty === 0 && amount > 0
      };
    };
    return m;
  }

  var api = { schedule: schedule, xirr: xirr, ofzYield: ofzYield, analyze: analyze, score: score, D: D, iso: iso, days: days, addMonths: addMonths };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CFAAnalytics = api;
})(this);
