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
function openModal(id) { var el = document.getElementById(id); if (el) el.classList.remove('hidden'); }
function closeModal(id) { var el = document.getElementById(id); if (el) el.classList.add('hidden'); }
document.addEventListener('click', function (e) {
  if (e.target.classList && e.target.classList.contains('modal-backdrop')) e.target.classList.add('hidden');
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
  var f = $('#form-buy-cfa');
  f.issue_id.value = b.dataset.buy;
  f.qty.value = '';
  f.sign_code.value = '';
  f.qty.max = b.dataset.left;
  buyState.price = +b.dataset.price;
  $('#buy-issue-title').textContent = b.dataset.title;
  $('#buy-total').textContent = 'Цена 1 ЦФА: ' + buyState.price.toLocaleString('ru-RU') + ' ₽. Доступно: ' + b.dataset.left + ' шт.';
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

// ---------- Кабинет эмитента ----------
var issuerForm = $('#form-issuer');
if (issuerForm) {
  issuerForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    var d = Object.fromEntries(new FormData(issuerForm).entries());
    d.only_qual = d.only_qual === 'true';
    try {
      await api('/api/issues', d);
      reloadSoon('Выпуск подписан УКЭП и опубликован на витрине');
    } catch (err) { if (err.message !== 'auth') showError('issuer-error', err.message); }
  });
}

document.addEventListener('click', async function (e) {
  var b = e.target.closest('[data-finalize]');
  if (!b) return;
  if (!confirm(b.dataset.confirm)) return;
  try {
    var r = await api('/api/issues/' + b.dataset.finalize + '/finalize');
    reloadSoon(r.result === 'ISSUED' ? 'Выпуск размещён: DvP выполнен' : 'Выпуск отменён, средства возвращены');
  } catch (err) { if (err.message !== 'auth') toast(err.message); }
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
    return COMPANY_RE.test(v.split(/\s+/).join(' ')) && v.length <= 120
      ? '' : 'Начните с формы собственности: ООО «Ромашка», АО «Альфа», ИП Иванов И. И.';
  }),
  ul_ogrn: req('Укажите ОГРН / ОГРНИП.', function (v) {
    var n = onlyDigits(v).length; return n === 13 || n === 15 ? '' : 'ОГРН — 13 цифр, ОГРНИП — 15 цифр.';
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

function goAfterAuth() {
  var next = window.AUTH_NEXT;   // разрешаем только внутренние адреса вида /cabinet/...
  location.href = (next && next.charAt(0) === '/' && next.charAt(1) !== '/') ? next : '/cabinet';
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
  var bd = document.querySelector('input[name="fl_birth_date"]');
  if (!bd) return;
  var t = new Date(), max = new Date(t.getFullYear() - 18, t.getMonth(), t.getDate());
  bd.max = max.getFullYear() + '-' + String(max.getMonth() + 1).padStart(2, '0') + '-' + String(max.getDate()).padStart(2, '0');
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
  showError('register-error', '');
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
      await api('/api/login', {
        login: loginForm.querySelector('[name="login"]').value,
        password: loginForm.querySelector('[name="password"]').value
      });
      goAfterAuth();
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
        role: $('#fs-fl').disabled ? 'ul' : 'fl',
        fields: Object.fromEntries(new FormData(registerForm).entries())   // disabled-поля сюда не попадают
      });
      goAfterAuth();
    } catch (err) {
      if (err.message === 'auth') return;
      showFieldErrors(registerForm, err.errors);
      showError('register-error', err.message);
    } finally { btn.disabled = false; }
  });
}
