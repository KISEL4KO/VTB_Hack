// ---------- Утилиты ----------
function $(sel, root) { return (root || document).querySelector(sel); }

function toast(msg) {
  var t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(function () { t.classList.add('hidden'); }, 3500);
}

function showError(id, msg) {
  var el = document.getElementById(id);
  if (!el) { toast(msg); return; }
  el.textContent = msg;
  el.classList.toggle('hidden', !msg);
}

async function api(url, body) {
  var res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  var data = {};
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new Error('auth'); }
  if (!res.ok || data.status === 'error') {
    var err = new Error(data.message || 'Ошибка запроса');
    err.errors = data.errors || null;   // ошибки по полям {имя_поля: текст} — для подсветки
    throw err;
  }
  return data;
}

function reloadSoon(msg) { toast(msg); setTimeout(function () { location.reload(); }, 700); }

// ---------- Модальные окна ----------
function syncModalLock() {
  // пока открыто окно, страница под ним не прокручивается (на телефоне иначе «уезжает» фон)
  document.body.classList.toggle('modal-open', !!document.querySelector('.modal-backdrop:not(.hidden)'));
}
function openModal(id) { var el = document.getElementById(id); if (el) el.classList.remove('hidden'); syncModalLock(); }
function closeModal(id) { var el = document.getElementById(id); if (el) el.classList.add('hidden'); syncModalLock(); }
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  document.querySelectorAll('.modal-backdrop:not(.hidden)').forEach(function (m) { m.classList.add('hidden'); });
  syncModalLock();
});
document.addEventListener('click', function (e) {
  if (e.target.classList && e.target.classList.contains('modal-backdrop')) { e.target.classList.add('hidden'); syncModalLock(); }
});

// ---------- Документы ----------
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-doc]');
  if (!b) return;
  $('#doc-title').textContent = b.dataset.doc;
  if (b.dataset.docText) $('#doc-text').textContent = b.dataset.docText;
  openModal('modal-doc');
});

// ---------- Покупка ЦФА ----------
var buyState = { price: 0, left: 0 };
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-buy]');
  if (!b) return;
  if (!window.ME) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return; }
  if (window.ME.role === 'ADMIN') { toast('Администратор не совершает операций с активами'); return; }
  if (window.ME.status !== 'APPROVED') { toast('Покупка станет доступна после проверки анкеты администратором'); return; }
  var f = $('#form-buy-cfa');
  f.issue_id.value = b.dataset.buy;
  f.qty.value = '';
  f.sign_code.value = '';
  var maxOrder = b.dataset.max ? Math.min(+b.dataset.max, +b.dataset.left) : +b.dataset.left;
  f.qty.max = maxOrder;
  f.qty.min = b.dataset.min || 1;
  buyState.price = +b.dataset.price;
  $('#buy-issue-title').textContent = b.dataset.title;
  $('#buy-total').textContent = 'Цена 1 ЦФА: ' + buyState.price.toLocaleString('ru-RU') + ' ₽. В одной заявке: от ' +
    (b.dataset.min || 1) + ' до ' + maxOrder.toLocaleString('ru-RU') + ' шт.';
  showError('buy-error', '');
  openModal('modal-buy');
});

var buyForm = $('#form-buy-cfa');
if (buyForm) {
  buyForm.qty.addEventListener('input', function () {
    var q = +buyForm.qty.value || 0;
    $('#buy-total').textContent = q ? 'Итого: ' + (q * buyState.price).toLocaleString('ru-RU') + ' ₽' : '';
  });
  buyForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    try {
      var r = await api('/api/buy', {
        issue_id: buyForm.issue_id.value, qty: buyForm.qty.value, sign_code: buyForm.sign_code.value
      });
      closeModal('modal-buy');
      reloadSoon('Заявка №' + r.order_id + ' принята, средства заморожены до закрытия сбора');
    } catch (err) { if (err.message !== 'auth') showError('buy-error', err.message); }
  });
}

// ---------- Кошелёк ----------
var walletForm = $('#form-wallet-op');
if (walletForm) {
  walletForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    var withdraw = walletForm.wallet_method.value === 'withdraw';
    try {
      await api(withdraw ? '/api/withdraw' : '/api/deposit', { amount_rub: walletForm.amount.value });
      closeModal('modal-wallet');
      reloadSoon(withdraw ? 'Средства выведены' : 'Кошелёк пополнен');
    } catch (err) { if (err.message !== 'auth') showError('wallet-error', err.message); }
  });
}

// ---------- Решение о выпуске (форма эмитента) ----------
var rovForm = $('#form-rov');
if (rovForm) {
  function rovVolume() {
    var price = +rovForm.price_per_cfa.value || 0, max = +rovForm.max_qty.value || 0, min = +rovForm.min_qty.value || 0;
    $('#rov-volume').textContent = price && max ? (price * max).toLocaleString('ru-RU') + ' ₽ (' + max.toLocaleString('ru-RU') + ' шт.)' : '—';
    if (min) $('#rov-success-note').textContent = 'Выпуск состоится, если будет заявлено не меньше ' + min.toLocaleString('ru-RU') +
      ' ЦФА. Иначе смарт-контракт вернёт замороженные средства инвесторам.';
  }
  ['price_per_cfa', 'max_qty', 'min_qty'].forEach(function (n) { rovForm[n].addEventListener('input', rovVolume); });
  rovVolume();

  // Текущее значение поля формы: у радиокнопок — выбранная, у остальных — value
  function rovValue(name) {
    var els = rovForm.querySelectorAll('[name="' + name + '"]');
    if (!els.length) return '';
    if (els[0].type === 'radio') { var c = rovForm.querySelector('[name="' + name + '"]:checked'); return c ? c.value : ''; }
    return els[0].value;
  }

  // Блоки, которые появляются по выбору (обеспечение, «ограничена», условия выкупа, подпись поручителя).
  // Закрытый блок недоступен с клавиатуры (inert), и его поля не требуются при проверке.
  function rovReveal() {
    rovForm.querySelectorAll('.reveal[data-show-if]').forEach(function (box) {
      var v = rovValue(box.dataset.showIf), show;
      if (box.dataset.showIn !== undefined) show = box.dataset.showIn.split(',').indexOf(v) >= 0;
      else show = box.dataset.showNot.split(',').indexOf(v) < 0;
      box.classList.toggle('is-open', show);
      if (show) box.removeAttribute('inert'); else box.setAttribute('inert', '');
    });
    rovForm.querySelectorAll('.seg-hint').forEach(function (h) {
      h.hidden = rovValue(h.dataset.for) !== h.dataset.value;
    });
    var noSite = rovForm.querySelector('[name="no_website"]'), site = rovForm.querySelector('[name="website"]');
    if (noSite && site) { site.disabled = noSite.checked; if (noSite.checked) setFieldState(site, '', false); }
  }

  // Раздел 3: формула купона и плановый график — из ставки, цены, частоты и дат (те же правила, что в документе)
  function rovSchedule() {
    var box = $('#rov-schedule'), formula = $('#rov-formula');
    if (!box || !window.CFAAnalytics) return;
    var price = +String(rovValue('price_per_cfa')).replace(/\s/g, '') || 0;
    var rate = parseFloat(String(rovValue('coupon_rate')).replace(',', '.'));
    var rows = CFAAnalytics.schedule({ price: price, rate: rate, period: rovValue('coupon_period'),
                                       start: rovValue('placement_end'), maturity: rovValue('maturity_date') });
    if (!rows.length || isNaN(rate)) {
      formula.textContent = 'Заполните ставку, цену (раздел 5), частоту выплат, окончание приёма заявок (раздел 6) и дату погашения.';
      box.innerHTML = '';
      return;
    }
    var nf = function (v, d) { return v.toLocaleString('ru-RU', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); };
    var dd = function (iso) { return iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4); };
    formula.textContent = 'Купон на 1 ЦФА = Цена (' + nf(price) + ' ₽) × ' + String(rate).replace('.', ',') +
      ' % годовых × число дней периода / 365';
    var period = rovValue('coupon_period');
    var lead = period === 'maturity' ? 'Доход выплачивается один раз — в дату погашения.'
      : 'Купон выплачивается ' + (period === 'monthly' ? 'раз в месяц' : 'раз в квартал') + ': ' +
        rows.map(function (r) { return dd(r.date).slice(0, 5); }).slice(0, 8).join(', ') + (rows.length > 8 ? '…' : '') + '.';
    var shown = rows.length > 13 ? rows.slice(0, 6).concat([null]).concat(rows.slice(-3)) : rows;
    box.innerHTML = '<p class="rov-note">' + lead + ' Погашение ' + dd(rows[rows.length - 1].date) + '.</p>' +
      '<div class="ui-table-wrap"><table class="ui-table ui-table--plain ui-table--compact"><thead><tr><th>№</th><th>Дата выплаты</th>' +
      '<th>Дней</th><th>Купон на 1 ЦФА</th></tr></thead><tbody>' +
      shown.map(function (r) {
        if (!r) return '<tr><td colspan="4" class="muted">…</td></tr>';
        var no = rows.indexOf(r) + 1;
        return '<tr><td>' + no + '</td><td>' + dd(r.date) + (r.principal ? ' + номинал' : '') + '</td><td>' + r.days +
          '</td><td>' + nf(r.coupon, 2) + ' ₽</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  // Проверка перед отправкой: что из обязательного ещё не заполнено. Пока список не пуст — кнопка неактивна.
  function rovMissing() {
    var out = [];
    rovForm.querySelectorAll('[data-req]').forEach(function (el) {
      if (el.closest('.reveal:not(.is-open)') || el.disabled) return;
      var ok;
      if (el.matches('[data-group]')) {
        var min = +(el.dataset.reqMin || 1);
        ok = el.querySelectorAll('input:checked').length >= min;
      } else if (el.type === 'checkbox') ok = el.checked;
      else ok = String(el.value || '').trim() !== '';
      if (!ok) out.push(el);
    });
    return out;
  }
  function rovCheck() {
    var missing = rovMissing(), box = $('#rov-check'), text = $('#rov-missing'), btn = $('#rov-submit');
    if (!box) return;
    box.classList.toggle('rov-check--ok', !missing.length);
    btn.disabled = missing.length > 0;
    btn.title = missing.length ? 'Сначала заполните всё из списка выше' : '';
    if (!missing.length) { text.textContent = 'Всё обязательное заполнено — можно подписывать и отправлять.'; return; }
    text.innerHTML = '<span>Не хватает:</span> ';
    missing.slice(0, 8).forEach(function (el, i) {
      var a = document.createElement('button');
      a.type = 'button'; a.className = 'rov-check__item'; a.textContent = el.dataset.req;
      a.addEventListener('click', function () {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        var focusable = el.matches('input, select, textarea') ? el : el.querySelector('input');
        if (focusable) setTimeout(function () { focusable.focus({ preventScroll: true }); }, 350);
      });
      if (i) text.appendChild(document.createTextNode(' · '));
      text.appendChild(a);
    });
    if (missing.length > 8) text.appendChild(document.createTextNode(' и ещё ' + (missing.length - 8)));
  }
  function rovRefresh() { rovReveal(); rovSchedule(); rovCheck(); }
  rovForm.addEventListener('input', rovRefresh);
  rovForm.addEventListener('change', rovRefresh);
  rovRefresh();
  // при открытии формы закрытые блоки не «выезжают» — анимация только на действия пользователя
  requestAnimationFrame(function () { requestAnimationFrame(function () { rovForm.classList.add('is-ready'); }); });
  // Исправленное поле перестаёт быть красным, как только его меняют
  rovForm.addEventListener('input', function (e) {
    var el = e.target;
    if (el.classList.contains('is-invalid')) setFieldState(el, '', false);
    var group = el.closest('[data-group]');
    if (group) group.classList.remove('is-invalid-group');
  });

  rovForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    showError('rov-error', '');
    var fd = new FormData(rovForm);
    var d = Object.fromEntries(fd.entries());
    d.risks = fd.getAll('risks');
    d.confirm = rovForm.confirm.checked ? 'true' : '';
    d.no_website = rovForm.no_website && rovForm.no_website.checked ? 'true' : '';
    var id = rovForm.dataset.issueId;
    var btn = rovForm.querySelector('[type="submit"]');
    btn.disabled = true;
    try {
      await api(id ? '/api/issues/' + id : '/api/issues', d);
      toast('Решение о выпуске подписано и отправлено на проверку');
      setTimeout(function () { location.href = '/cabinet/issuer'; }, 900);
    } catch (err) {
      if (err.message === 'auth') return;
      var errors = err.errors || {};
      if (errors.risks) { $('[data-group="risks"]').classList.add('is-invalid-group'); }
      if (errors.confirm) { toast(errors.confirm); }
      // Ошибки полей, которых нет в обычной разметке .form-field, тоже подсвечиваем
      Object.keys(errors).forEach(function (name) {
        var input = rovForm.querySelector('[name="' + name + '"]');
        if (input && !input.closest('.form-field')) markLight(input, errors[name]);
      });
      showError('rov-error', err.message);
      var first = rovForm.querySelector('.is-invalid, .is-invalid-group');
      if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } finally { btn.disabled = false; rovCheck(); }
  });
}

// Подсветка для светлых форм (.light-field) — та же идея, что setFieldState
function markLight(input, message) {
  var box = input.closest('.light-field') || input.parentElement;
  input.classList.add('is-invalid');
  var el = box.querySelector('.field-error');
  if (!el) { el = document.createElement('small'); el.className = 'field-error'; box.appendChild(el); }
  el.textContent = message;
  input.addEventListener('input', function clear() {
    input.classList.remove('is-invalid'); if (el.parentNode) el.remove(); input.removeEventListener('input', clear);
  });
}

// ---------- Админ-панель: одобрить / отклонить / заблокировать ----------
document.addEventListener('click', async function (e) {
  var b = e.target.closest('[data-decide]');
  if (!b) return;
  var card = b.closest('[data-decision-url]');
  var reasonEl = card.querySelector('[name="reason"]');
  var action = b.dataset.decide;
  showError('decision-error', '');
  if ((action === 'reject' || action === 'block') && !reasonEl.value.trim()) {
    showError('decision-error', action === 'block' ? 'Укажите причину блокировки.' : 'Укажите причину — её увидит заявитель.');
    reasonEl.focus();
    return;
  }
  if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
  card.querySelectorAll('button').forEach(function (x) { x.disabled = true; });
  try {
    var r = await api(card.dataset.decisionUrl + '/' + action, { reason: reasonEl.value });
    reloadSoon(r.evm_tx_hash ? 'Готово. Транзакция в блокчейне: ' + r.evm_tx_hash.slice(0, 14) + '…' : 'Решение сохранено');
  } catch (err) {
    if (err.message !== 'auth') showError('decision-error', err.message);
    card.querySelectorAll('button').forEach(function (x) { x.disabled = false; });
  }
});

document.addEventListener('click', async function (e) {
  var b = e.target.closest('[data-finalize]');
  if (!b) return;
  if (!confirm(b.dataset.confirm)) return;
  try {
    var r = await api('/api/issues/' + b.dataset.finalize + '/finalize');
    reloadSoon(r.result === 'ISSUED' ? 'Выпуск размещён: DvP выполнен' : 'Выпуск отменён, средства возвращены');
  } catch (err) { if (err.message !== 'auth') toast(err.message); }
});

// Выплаты эмитента: очередной купон или погашение (с кошелька эмитента всем владельцам)
document.addEventListener('click', async function (e) {
  var b = e.target.closest('[data-pay]');
  if (!b) return;
  if (!confirm(b.dataset.confirm)) return;
  b.disabled = true;
  showError('pay-error', '');
  try {
    var r = await api('/api/issues/' + b.dataset.issue + '/' + b.dataset.pay);
    reloadSoon(b.dataset.pay === 'coupon'
      ? 'Купон № ' + r.period + ' выплачен: ' + r.total_rub.toLocaleString('ru-RU') + ' ₽ — ' + r.holders + ' владельцам'
      : 'Выпуск погашен: ' + r.total_rub.toLocaleString('ru-RU') + ' ₽ — ' + r.holders + ' владельцам');
  } catch (err) {
    if (err.message !== 'auth') showError('pay-error', err.message);
    b.disabled = false;
  }
});

// ======================================================================
// ВХОД И РЕГИСТРАЦИЯ
// ======================================================================

// ---------- Подсветка полей ----------
// Поле «выключено», если отключено само или лежит внутри <fieldset disabled>.
// Важно: свойство el.disabled про fieldset не знает, поэтому проверяем псевдокласс :disabled.
function isOff(el) { return el.matches(':disabled'); }

// Ошибка показывается под полем (<small class="field-error">), само поле получает класс
// is-invalid (красная рамка). Корректно заполненное поле получает is-valid (зелёная рамка).
function setFieldState(input, message, invalid) {
  var box = input.closest('.form-field');
  if (!box) return;
  var errEl = box.querySelector('.field-error');
  input.classList.toggle('is-invalid', invalid);
  input.classList.toggle('is-valid', !invalid && input.value !== '');
  if (invalid) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
  if (invalid && message) {
    if (!errEl) { errEl = document.createElement('small'); errEl.className = 'field-error'; box.appendChild(errEl); }
    errEl.textContent = message;
  } else if (errEl) {
    errEl.remove();
  }
}

// Подсветить ошибки, пришедшие с сервера: {fl_inn: 'ИНН — 12 цифр', ...}
function showFieldErrors(form, errors) {
  var first = null;
  Object.keys(errors || {}).forEach(function (name) {
    var input = form.querySelector('[name="' + name + '"]');
    if (!input || isOff(input)) return;
    input.dataset.touched = '1';
    setFieldState(input, errors[name], true);
    if (!first) first = input;
  });
  if (first) first.focus();
}

// ---------- Правила проверки (те же, что в validators.py на сервере) ----------
function onlyDigits(s) { return (s || '').replace(/\D/g, ''); }
var NAME_WORD = '[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*';
var FIO_RE = new RegExp('^' + NAME_WORD + '(?:\\s+' + NAME_WORD + '){1,3}$');
var REP_FIO_RE = new RegExp('^' + NAME_WORD + '\\s+' + NAME_WORD);
var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[A-Za-zА-Яа-я]{2,}$/;
var LOGIN_RE = /^[a-z0-9_.-]{3,32}$/;

function req(msg, next) {           // «обязательное поле» + дополнительная проверка
  return function (v, form) { return v ? (next ? next(v, form) : '') : msg; };
}

// <input type="date"> отдаёт значение 'ГГГГ-ММ-ДД' (или '' если дата введена не полностью)
function checkBirthDate(v) {
  var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (!m) return 'Укажите дату рождения полностью.';
  var y = +m[1], mo = +m[2], d = +m[3];
  var born = new Date(y, mo - 1, d);
  var t = new Date();
  if (born > t) return 'Дата рождения не может быть в будущем.';
  var age = t.getFullYear() - y - ((t.getMonth() < mo - 1 || (t.getMonth() === mo - 1 && t.getDate() < d)) ? 1 : 0);
  if (age < 18) return 'Инвестировать на платформе можно только с 18 лет.';
  if (age > 120) return 'Проверьте год рождения.';
  return '';
}

var COMPANY_RE = /^(ООО|АО|ПАО|НАО|ЗАО|ОАО|ИП|АНО|НКО|ГУП|МУП|ФГУП|КФХ)\s+\S.*$/i;

function checkPassport(v) {
  return /^\d{10}$/.test(v) ? '' : 'Ровно 10 цифр без пробелов и символов: серия (4) и номер (6).';
}

function checkCity(v) {
  // Пока справочник не загрузился — не ругаемся (сервер всё равно проверит)
  if (!CITY_SET) return '';
  return CITY_SET.has(v) ? '' : 'Начните вводить название и выберите город из списка.';
}

function checkStreet(v) {
  return v.length >= 5 && /[A-Za-zА-Яа-яЁё]/.test(v) && /\d/.test(v)
    ? '' : 'Укажите улицу и номер дома, например: ул. Ленина, д. 1.';
}

function phoneOk(v) {
  var d = onlyDigits(v);
  return (d.length === 11 && (d[0] === '7' || d[0] === '8')) || (d.length === 10 && d[0] === '9');
}

var LOGIN_RULES = {
  login: req('Введите логин.'),
  password: req('Введите пароль.')
};

var REGISTER_RULES = {
  login: req('Придумайте логин.', function (v) {
    return LOGIN_RE.test(v.toLowerCase()) ? '' : 'От 3 до 32 символов: латинские буквы, цифры и знаки _ . -';
  }),
  password: function (v) {
    if (v.length < 8) return 'Пароль должен быть не короче 8 символов.';
    if (v.length > 64) return 'Пароль должен быть не длиннее 64 символов.';
    if (!/[A-Za-zА-Яа-яЁё]/.test(v) || !/\d/.test(v)) return 'Пароль должен содержать хотя бы одну букву и одну цифру.';
    return '';
  },
  password2: req('Повторите пароль.', function (v, form) {
    return v === form.querySelector('[name="password"]').value ? '' : 'Пароли не совпадают.';
  }),

  // --- физлицо ---
  fl_fio: req('Укажите ФИО.', function (v) {
    return FIO_RE.test(v.split(/\s+/).join(' ')) ? '' : 'Фамилия, имя и (при наличии) отчество — только буквы и дефис.';
  }),
  fl_birth_date: function (v, form, el) {
    if (el.validity && el.validity.badInput) return 'Укажите дату рождения полностью.';
    return v ? checkBirthDate(v) : 'Укажите дату рождения.';
  },
  fl_birth_place: req('Укажите место рождения.', function (v) {
    return v.length >= 2 && /[A-Za-zА-Яа-яЁё]/.test(v) ? '' : 'Укажите населённый пункт.';
  }),
  fl_citizenship: req('Укажите гражданство.', function (v) { return v.length >= 2 ? '' : 'Слишком короткое значение.'; }),
  fl_phone: req('Укажите телефон.', function (v) { return phoneOk(v) ? '' : 'Российский номер: +7 и 10 цифр.'; }),
  fl_email: req('Укажите e-mail.', function (v) { return EMAIL_RE.test(v) ? '' : 'Некорректный e-mail, пример: user@example.ru'; }),
  fl_passport_num: req('Укажите серию и номер паспорта.', checkPassport),
  fl_passport_issued: req('Заполните поле.', function (v) {
    return /\d{3}-\d{3}/.test(v) && v.length >= 10 ? '' : 'Укажите код подразделения (000-000) и кем выдан.';
  }),
  fl_reg_city: req('Выберите город регистрации.', checkCity),
  fl_reg_street: req('Укажите улицу, дом и квартиру.', checkStreet),
  fl_inn: req('Укажите ИНН.', function (v) { return onlyDigits(v).length === 12 ? '' : 'ИНН физического лица — 12 цифр.'; }),
  fl_snils: req('Укажите СНИЛС.', function (v) { return onlyDigits(v).length === 11 ? '' : 'СНИЛС — 11 цифр.'; }),

  // --- юрлицо ---
  ul_company_name: req('Укажите сокращённое наименование.', function (v) {
    if (/^(ИП|КФХ)\s/i.test(v)) return 'ИП регистрируется на вкладке «Для физ лиц» → «Да, я ИП».';
    return COMPANY_RE.test(v.split(/\s+/).join(' ')) && v.length <= 120
      ? '' : 'Начните с формы собственности: ООО «Ромашка», АО «Альфа», ПАО «Бета».';
  }),
  ul_ogrn: req('Укажите ОГРН.', function (v) {
    var n = onlyDigits(v).length;
    if (n === 15) return '15 цифр — это ОГРНИП. ИП регистрируется на вкладке «Для физ лиц» → «Да, я ИП».';
    return n === 13 ? '' : 'ОГРН юридического лица — 13 цифр.';
  }),
  ul_inn_kpp: req('Укажите ИНН и КПП.', function (v) {
    var p = v.split('/');
    if (p.length !== 2) return 'Формат: ИНН / КПП, например 7700000000 / 770001001.';
    if (onlyDigits(p[0]).length !== 10) return 'ИНН юридического лица — 10 цифр (до знака «/»).';
    if (onlyDigits(p[1]).length !== 9) return 'КПП — 9 цифр (после знака «/»).';
    return '';
  }),
  ul_addr_city: req('Выберите город.', checkCity),
  ul_addr_street: req('Укажите улицу, дом и офис.', checkStreet),
  ul_rep_fio: req('Укажите ФИО представителя.', function (v) {
    return REP_FIO_RE.test(v.split(/\s+/).join(' ')) ? '' : 'Начните с фамилии и имени представителя (только буквы).';
  }),
  ul_rep_passport: req('Укажите серию и номер паспорта.', checkPassport),
  ul_rep_inn: req('Укажите ИНН представителя.', function (v) {
    return /^\d{12}$/.test(v) ? '' : 'ИНН физического лица — ровно 12 цифр.';
  }),
  ul_bank_account: req('Укажите расчётный счёт.', function (v) { return onlyDigits(v).length === 20 ? '' : 'Расчётный счёт — 20 цифр.'; }),
  ul_bank_bik: req('Укажите БИК.', function (v) {
    var d = onlyDigits(v); return d.length === 9 && d.indexOf('04') === 0 ? '' : 'БИК — 9 цифр и начинается с 04.';
  }),

  // --- индивидуальный предприниматель ---
  ip_ogrnip: req('Укажите ОГРНИП.', function (v) {
    var d = onlyDigits(v);
    if (d.length !== 15) return 'ОГРНИП — 15 цифр.';
    return d[0] === '3' ? '' : 'ОГРНИП начинается с цифры 3.';
  }),
  ip_reg_date: function (v, form, el) {
    if (el.validity && el.validity.badInput) return 'Укажите дату полностью.';
    if (!v) return 'Укажите дату регистрации ИП.';
    return new Date(v) > new Date() ? 'Дата не может быть в будущем.' : '';
  },
  ip_bank_account: req('Укажите расчётный счёт.', function (v) {
    var d = onlyDigits(v);
    if (d.length !== 20) return 'Расчётный счёт — 20 цифр.';
    return d.indexOf('40802') === 0 ? '' : 'Счёт ИП начинается с 40802.';
  }),
  ip_bank_bik: req('Укажите БИК.', function (v) {
    var d = onlyDigits(v); return d.length === 9 && d.indexOf('04') === 0 ? '' : 'БИК — 9 цифр и начинается с 04.';
  })
};

// ---------- Механика проверки ----------
function validateField(input, rules, form) {
  var rule = rules[input.name];
  if (!rule || isOff(input)) return true;
  var v = input.type === 'password' ? input.value : input.value.trim();   // пароли не обрезаем
  var msg = rule(v, form, input);
  setFieldState(input, msg, !!msg);
  return !msg;
}

function validateForm(form, rules) {
  var first = null;
  form.querySelectorAll('input[name], select[name]').forEach(function (el) {
    if (isOff(el) || !rules[el.name]) return;
    el.dataset.touched = '1';
    if (!validateField(el, rules, form) && !first) first = el;
  });
  if (first) first.focus();
  return !first;
}

// Поле проверяется, когда пользователь из него ушёл (blur), а дальше — при каждом вводе.
function attachLiveValidation(form, rules) {
  function recheck(el) {
    validateField(el, rules, form);
    var p2 = form.querySelector('[name="password2"]');   // сменили пароль — перепроверим повтор
    if (el.name === 'password' && p2 && p2.dataset.touched) validateField(p2, rules, form);
  }
  form.addEventListener('focusout', function (e) {
    var el = e.target;
    if (!el.name || !rules[el.name]) return;
    if (el.value === '' && !el.dataset.touched) return;   // не ругаемся на поле, которое просто «пролистали»
    // Фокус ушёл на кнопку отправки: не проверяем здесь, иначе появившаяся ошибка сдвинет
    // форму вниз и клик «промахнётся» мимо кнопки. Всё проверит обработчик submit.
    if (e.relatedTarget && e.relatedTarget.type === 'submit') return;
    el.dataset.touched = '1';
    recheck(el);
  });
  form.addEventListener('input', function (e) {
    if (e.target.dataset.touched && rules[e.target.name]) recheck(e.target);
  });
}

function goAfterAuth(fallback) {
  var next = window.AUTH_NEXT;   // разрешаем только внутренние адреса вида /cabinet/...
  location.href = (next && next.charAt(0) === '/' && next.charAt(1) !== '/') ? next : (fallback || '/cabinet');
}

// ---------- Поля «только цифры» (паспорт, ИНН представителя) ----------
// Всё, что не цифра, удаляется прямо при вводе или вставке.
document.addEventListener('input', function (e) {
  var el = e.target;
  if (el.hasAttribute && el.hasAttribute('data-digits')) {
    var clean = el.value.replace(/\D/g, '');
    if (clean !== el.value) el.value = clean;
  }
}, true);   // true — срабатывает раньше живой проверки, и она видит уже очищенное значение

// ---------- Подсказки городов (выпадающий список) ----------
// Справочник: static/data/cities.json — 1117 городов России, крупные первыми.
var CITIES = null;      // массив строк 'г. Казань, Республика Татарстан'
var CITY_SET = null;    // то же множеством — для быстрой проверки «есть ли в списке»
var CITY_INDEX = null;  // [{label, name, norm}] — заранее подготовлено для поиска

function normText(s) {   // регистр и «ё» не важны, префикс «г.» тоже
  return (s || '').toLowerCase().replace(/ё/g, 'е').replace(/^г\.?\s*/, '').trim();
}

function loadCities() {
  if (loadCities.promise) return loadCities.promise;
  loadCities.promise = fetch('/static/data/cities.json')
    .then(function (r) { return r.json(); })
    .then(function (list) {
      CITIES = list;
      CITY_SET = new Set(list);
      CITY_INDEX = list.map(function (label) {
        var name = label.replace(/^г\. /, '').split(',')[0];
        return { label: label, name: normText(name), norm: normText(label) };
      });
    })
    .catch(function () { loadCities.promise = null; });   // не загрузилось — попробуем при следующем фокусе
  return loadCities.promise;
}

// Поиск: сначала города, чьё название начинается с запроса, потом содержащие его,
// потом совпадения по региону. Внутри групп — исходный порядок (по населению).
function searchCities(query, limit) {
  var q = normText(query);
  if (!q || !CITY_INDEX) return [];
  var starts = [], contains = [], region = [];
  for (var i = 0; i < CITY_INDEX.length; i++) {
    var c = CITY_INDEX[i];
    if (c.name.indexOf(q) === 0) starts.push(c.label);
    else if (c.name.indexOf(q) > 0) contains.push(c.label);
    else if (c.norm.indexOf(q) >= 0) region.push(c.label);
    if (starts.length >= limit) break;
  }
  return starts.concat(contains, region).slice(0, limit);
}

function setupSuggest(input) {
  var box = input.closest('.form-field');
  box.classList.add('suggest');
  var list = document.createElement('ul');
  list.className = 'suggest-list hidden';
  list.setAttribute('role', 'listbox');
  box.appendChild(list);
  var items = [], active = -1;

  function close() { list.classList.add('hidden'); active = -1; }

  function render() {
    items = searchCities(input.value, 8);
    list.innerHTML = '';
    if (!items.length) {
      if (input.value.trim() && CITY_INDEX) {
        var li = document.createElement('li');
        li.className = 'suggest-empty';
        li.textContent = input.hasAttribute('data-strict') ? 'Город не найден — проверьте написание' : 'Нет в списке — можно оставить как есть';
        list.appendChild(li);
        list.classList.remove('hidden');
      } else close();
      return;
    }
    var q = normText(input.value);
    items.forEach(function (label, i) {
      var li = document.createElement('li');
      li.className = 'suggest-item' + (i === active ? ' suggest-item--active' : '');
      li.setAttribute('role', 'option');
      // Подсвечиваем совпавший кусок жирным. textContent, а не innerHTML — защита от XSS.
      var pos = normText(label).indexOf(q) + (label.indexOf('г. ') === 0 ? 3 : 0);
      if (q && pos >= 0) {
        li.appendChild(document.createTextNode(label.slice(0, pos)));
        var b = document.createElement('b'); b.textContent = label.slice(pos, pos + q.length); li.appendChild(b);
        li.appendChild(document.createTextNode(label.slice(pos + q.length)));
      } else li.textContent = label;
      // mousedown, а не click: иначе поле потеряет фокус и список закроется раньше клика
      li.addEventListener('mousedown', function (e) { e.preventDefault(); choose(label); });
      list.appendChild(li);
    });
    list.classList.remove('hidden');
  }

  function choose(label) {
    input.value = label;
    input.dataset.touched = '1';
    close();
    input.dispatchEvent(new Event('input', { bubbles: true }));   // перезапускаем проверку поля
  }

  input.addEventListener('focus', function () { loadCities().then(function () { if (document.activeElement === input && input.value) render(); }); });
  input.addEventListener('input', function (e) {
    if (!e.isTrusted) return;            // событие от choose() — список не открываем заново
    active = -1;
    loadCities().then(render);
  });
  input.addEventListener('keydown', function (e) {
    if (list.classList.contains('hidden') || !items.length) return;
    if (e.key === 'ArrowDown') { active = (active + 1) % items.length; render(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { active = (active - 1 + items.length) % items.length; render(); e.preventDefault(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(items[active >= 0 ? active : 0]); }
    else if (e.key === 'Escape') close();
  });
  input.addEventListener('blur', function () {
    // Ввели название целиком, но не выбрали из списка: если такой город ровно один — подставим сами
    if (CITY_INDEX && input.value && !CITY_SET.has(input.value)) {
      var q = normText(input.value);
      var exact = CITY_INDEX.filter(function (c) { return c.name === q; });
      if (exact.length === 1) input.value = exact[0].label;
    }
    close();
  });
}

document.querySelectorAll('input[data-suggest="cities"]').forEach(setupSuggest);
if (document.querySelector('input[data-suggest="cities"]')) loadCities();

// Дата рождения: нельзя выбрать дату позже «сегодня минус 18 лет»
(function () {
  function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  var t = new Date(), max = new Date(t.getFullYear() - 18, t.getMonth(), t.getDate());
  document.querySelectorAll('input[name="fl_birth_date"]').forEach(function (bd) { bd.max = iso(max); });
  var rd = document.querySelector('input[name="ip_reg_date"]');
  if (rd) rd.max = iso(t);   // дата регистрации ИП — не позже сегодняшнего дня
})();

// ---------- Переключатели ----------
function switchAuthMode(mode) {
  var isLogin = mode === 'login';
  $('#form-login').classList.toggle('hidden', !isLogin);
  $('#switch-to-register').classList.toggle('hidden', !isLogin);
  $('#form-register').classList.toggle('hidden', isLogin);
  $('#btn-mode-login').classList.toggle('auth-mode-tab--active', isLogin);
  $('#btn-mode-register').classList.toggle('auth-mode-tab--active', !isLogin);
  document.title = (isLogin ? 'Вход' : 'Регистрация') + ' — Платформа ЦФА';
  // Меняем адрес без перезагрузки, чтобы /register можно было открыть по ссылке
  history.replaceState(null, '', (isLogin ? '/login' : '/register') + location.search);
  var firstInput = (isLogin ? $('#form-login') : $('#form-register')).querySelector('input');
  if (firstInput) firstInput.focus();
}

function switchAuthRole(role) {
  var isFl = role === 'fl';
  // disabled у fieldset: поля неактивной анкеты не отправляются и не проверяются
  $('#fs-fl').disabled = !isFl;
  $('#fs-ul').disabled = isFl;
  $('#fs-fl').classList.toggle('hidden', !isFl);
  $('#fs-ul').classList.toggle('hidden', isFl);
  $('#btn-role-fl').classList.toggle('auth-role-tab--active', isFl);
  $('#btn-role-ul').classList.toggle('auth-role-tab--active', !isFl);
  if (isFl) applyIpMode();   // вложенный блок ИП включается только при «Да, я ИП»
  showError('register-error', '');
}

// Переключатель «Вы индивидуальный предприниматель?» на вкладке физлица:
// «Да» включает блок «Регистрация ИП и расчётный счёт» и меняет подписи анкеты (data-fl / data-ip).
function isIpMode() {
  var r = document.querySelector('input[name="fl_is_ip"]:checked');
  return !!r && r.value === 'yes';
}
function applyIpMode() {
  var fs = $('#fs-fl'), extra = $('#fs-ip-extra');
  if (!fs || !extra) return;
  var ip = isIpMode();
  extra.disabled = !ip || fs.disabled;
  extra.classList.toggle('hidden', !ip);
  fs.classList.toggle('is-ip', ip);
  fs.querySelectorAll('[data-fl][data-ip]').forEach(function (el) { el.textContent = ip ? el.dataset.ip : el.dataset.fl; });
  if (!ip) extra.querySelectorAll('.form-field').forEach(function (box) {   // скрытые поля не должны «краснеть»
    var input = box.querySelector('input'); if (input) setFieldState(input, '', false);
  });
}
document.querySelectorAll('input[name="fl_is_ip"]').forEach(function (r) { r.addEventListener('change', applyIpMode); });
applyIpMode();

function currentAuthRole() {
  if ($('#fs-fl') && $('#fs-fl').disabled) return 'ul';
  return isIpMode() ? 'ip' : 'fl';
}

// ---------- Отправка формы входа ----------
var loginForm = $('#form-login');
if (loginForm) {
  attachLiveValidation(loginForm, LOGIN_RULES);
  loginForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    showError('login-error', '');
    if (!validateForm(loginForm, LOGIN_RULES)) return;
    var btn = loginForm.querySelector('[type="submit"]');
    btn.disabled = true;
    try {
      var res = await api('/api/login', {
        login: loginForm.querySelector('[name="login"]').value,
        password: loginForm.querySelector('[name="password"]').value
      });
      goAfterAuth(res.redirect);
    } catch (err) {
      if (err.message === 'auth') return;
      showFieldErrors(loginForm, err.errors);
      showError('login-error', err.message);
    } finally { btn.disabled = false; }
  });
}

// ---------- Отправка формы регистрации ----------
var registerForm = $('#form-register');
if (registerForm) {
  attachLiveValidation(registerForm, REGISTER_RULES);
  registerForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    showError('register-error', '');
    if (!validateForm(registerForm, REGISTER_RULES)) {
      showError('register-error', 'Проверьте поля, выделенные красным.');
      return;
    }
    var btn = registerForm.querySelector('[type="submit"]');
    btn.disabled = true;
    try {
      await api('/api/register', {
        role: currentAuthRole(),
        fields: Object.fromEntries(new FormData(registerForm).entries())   // disabled-поля сюда не попадают
      });
      location.href = '/cabinet';   // анкета ушла на проверку — кабинет покажет статус
    } catch (err) {
      if (err.message === 'auth') return;
      showFieldErrors(registerForm, err.errors);
      showError('register-error', err.message);
    } finally { btn.disabled = false; }
  });
}

// ======================================================================
// ДАШБОРДЫ ВЫПУСКОВ (расчёты — analytics.js, отрисовка — dashboards.js)
// ======================================================================
(function () {
  if (!window.CFADash) return;
  function json(id) { var el = document.getElementById(id); return el ? JSON.parse(el.textContent) : null; }
  var market = json('cfa-data');
  if (market) CFADash.cards(market);                                   // карточки витрины
  var page = json('issue-data');
  if (page) CFADash.issuePage(document.querySelector('.issue-page'), page.issue, page.bench);   // страница выпуска
  var ctor = json('rov-bench');
  if (ctor && rovForm) CFADash.constructor(rovForm, $('#rov-dash'), ctor.bench, ctor.track);   // живой конструктор
  var adm = json('admin-issue-data');
  if (adm) CFADash.compact($('#admin-dash'), adm.issue, adm.bench, 'investor');               // модерация
})();

// ---------- Рыночные ориентиры (админ) ----------
var benchForm = $('#form-benchmarks');
if (benchForm) {
  async function saveBench(body) {
    showError('bench-error', '');
    benchForm.querySelectorAll('.is-invalid').forEach(function (x) { x.classList.remove('is-invalid'); });
    benchForm.querySelectorAll('.field-error').forEach(function (x) { x.remove(); });
    try { await api('/api/admin/benchmarks', body); reloadSoon('Ориентиры сохранены — оценки выпусков пересчитаны'); }
    catch (err) {
      if (err.message === 'auth') return;
      Object.keys(err.errors || {}).forEach(function (n) { var i = benchForm.querySelector('[name="' + n + '"]'); if (i) markLight(i, err.errors[n]); });
      showError('bench-error', err.message);
    }
  }
  benchForm.addEventListener('submit', function (e) { e.preventDefault(); saveBench(Object.fromEntries(new FormData(benchForm).entries())); });
  $('#bench-reset').addEventListener('click', function () {
    if (confirm('Вернуть значения по умолчанию (ЦБ, Росстат, MOEX на октябрь 2026)?')) saveBench({ reset: true });
  });
}


// ---------- Админка: «Сторож выплат» — ручной проход ----------
(function () {
  var btn = $('#keeper-run');
  if (!btn) return;
  btn.addEventListener('click', async function () {
    var box = $('#keeper-result');
    btn.disabled = true;
    try {
      var r = await api('/api/admin/keeper/run');
      box.className = 'status-banner ' + (r.events.length ? 'status-banner--green' : 'status-banner--amber');
      box.textContent = r.events.length
        ? r.events.map(function (e) { return e.message; }).join(' · ')
        : 'Нечего исполнять: ни одна дата выплаты ещё не наступила (или по ней уже записано «нет средств» сегодня).';
      if (r.events.length) setTimeout(function () { location.reload(); }, 1500);
    } catch (err) {
      box.className = 'status-banner status-banner--red';
      box.textContent = err.message;
    } finally { btn.disabled = false; }
  });
})();


// ---------- Адаптивность: таблицы-карточки на телефоне и лента разделов кабинета ----------
// На узком экране (responsive.css, < 640 px) строки таблиц показываются карточками: слева подпись столбца, справа значение.
// Подписи берутся из заголовков таблицы, с учётом объединённых ячеек (colspan). Ячейка на несколько столбцов — без подписи.
function labelTables(root) {
  (root || document).querySelectorAll('table.ui-table').forEach(function (table) {
    var heads = [];
    table.querySelectorAll('thead th').forEach(function (th) {
      var span = th.colSpan || 1, text = th.textContent.replace(/\s+/g, ' ').trim();
      for (var i = 0; i < span; i++) heads.push(text);
    });
    if (!heads.length) return;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      var col = 0;
      Array.prototype.forEach.call(tr.cells, function (td) {
        var span = td.colSpan || 1;
        if (span === 1 && heads[col]) td.setAttribute('data-label', heads[col]);
        // пустая ячейка (например, нет кнопки в этой строке) в карточке не нужна
        td.classList.toggle('td-empty', !td.textContent.trim() && !td.querySelector('img, svg, input, button, a'));
        // длинный текст (описание операции) в режиме плиток занимает всю ширину карточки
        td.classList.toggle('td-wide', td.textContent.replace(/\s+/g, ' ').trim().length > 60);
        col += span;
      });
    });
  });
}
labelTables();
// таблицы, которые рисуются позже (график выплат на странице выпуска и т. п.)
(function () {
  if (!window.MutationObserver) return;
  var pending = false;
  new MutationObserver(function (list) {
    if (pending) return;
    for (var i = 0; i < list.length; i++) {
      if (list[i].addedNodes.length) { pending = true; break; }
    }
    if (pending) requestAnimationFrame(function () { pending = false; labelTables(); });
  }).observe(document.body, { childList: true, subtree: true });
})();

// Лента разделов кабинета (планшет и телефон): активная вкладка видна сразу, без ручной прокрутки
(function () {
  var bar = document.querySelector('.cabinet-sidebar');
  var active = bar && bar.querySelector('.sidebar-link--active');
  if (!bar || !active || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft = active.offsetLeft - (bar.clientWidth - active.offsetWidth) / 2;
})();
