// Тесты расчётного ядра аналитики:  node tests/test_analytics.js
const A = require(require('path').join(__dirname, '..', 'static', 'analytics.js'));
let ok = 0, bad = 0;
const near = (name, got, exp, tol) => { const g = Math.abs(got - exp) <= tol; g ? ok++ : bad++; console.log((g ? '✓' : '✕') + ' ' + name + ': ' + (typeof got === 'number' ? got.toFixed(4) : got) + ' (ожидалось ' + exp + ' ± ' + tol + ')'); };
const eq = (name, got, exp) => { const g = got === exp; g ? ok++ : bad++; console.log((g ? '✓' : '✕') + ' ' + name + ': ' + got + (g ? '' : ' (ожидалось ' + exp + ')')); };
const B = { key_rate: 14, deposit_rate: 13.06, inflation: 6.35, ndfl: 13, ofz_curve: [[1, 13.27], [1.3, 14.03], [2.4, 14.84], [2.9, 14.99], [3.9, 15.65], [5, 15.68], [10, 16.22]] };

// 1. Один платёж через 365 дней, 18 %: купон 180 ₽, XIRR = 18 %
let s = A.schedule({ price: 1000, rate: 18, period: 'maturity', start: '2027-01-01', maturity: '2028-01-01' });
eq('один платёж: число выплат', s.length, 1); near('купон за год', s[0].coupon, 180, 1e-9);
near('XIRR при выплате в конце года', A.xirr([{ date: '2027-01-01', amount: -1000 }, { date: '2028-01-01', amount: 1180 }]), 0.18, 1e-6);
// 2. Ежеквартально 18 % — эффективная ≈ (1 + 0.045)^4 − 1 ≈ 19,25 %
let m = A.analyze({ price: 1000, rate: 18, period: 'quarterly', placementStart: '2027-01-01', placementEnd: '2027-01-01', maturity: '2028-01-01', maxQty: 100 }, B, { buyDate: '2027-01-01' });
eq('ежеквартально: число выплат', m.schedule.length, 4); near('ежеквартально: эффективная доходность, %', m.ytm, 19.25, 0.1);
near('сумма купонов на 1 ЦФА', m.couponTotal, 180, 1e-6);
// 3. Ежемесячно 12 % ≈ 12,68 %
m = A.analyze({ price: 1000, rate: 12, period: 'monthly', placementStart: '2027-01-01', placementEnd: '2027-01-01', maturity: '2028-01-01', maxQty: 10 }, B, { buyDate: '2027-01-01' });
eq('ежемесячно: число выплат', m.schedule.length, 12); near('ежемесячно: эффективная доходность, %', m.ytm, 12.68, 0.1);
// 4. Заморозка: заявка за 30 дней до DvP снижает доходность
let x = { price: 1000, rate: 18, period: 'quarterly', placementStart: '2027-01-01', placementEnd: '2027-01-31', maturity: '2028-01-31', maxQty: 100 };
m = A.analyze(x, B, { buyDate: '2027-01-01' });
eq('дней заморозки', m.frozenDays, 30); eq('доходность с заморозкой ниже', m.ytmFrozen < m.ytm, true);
near('после НДФЛ: купонная часть × 0,87 (сравниваем с той же датой заявки)', m.ytmNet / m.ytmFrozen, 0.87, 0.02);
// 5. ОФЗ: интерполяция и границы кривой
near('ОФЗ на 2 года (интерполяция 1,3 → 2,4)', A.ofzYield(B.ofz_curve, 2), 14.03 + 0.81 * 0.7 / 1.1, 1e-9);
near('ОФЗ короче кривой', A.ofzYield(B.ofz_curve, 0.3), 13.27, 1e-9); near('ОФЗ длиннее кривой', A.ofzYield(B.ofz_curve, 20), 16.22, 1e-9);
// 6. Даты: 31 января + 1 месяц = 28 февраля; короткий хвост сливается
eq('31.01 + 1 мес.', A.iso(A.addMonths(A.D('2027-01-31'), 1)), '2027-02-28');
s = A.schedule({ price: 1000, rate: 12, period: 'monthly', start: '2027-01-01', maturity: '2027-03-10' });
eq('хвост 9 дней слит с последним периодом', s.length, 2); eq('последний период 01.02→10.03 = 37 дней', s[1].days, 37);
// 7. Премия к ОФЗ и оценка
m = A.analyze(x, B, { buyDate: '2027-01-31' });
near('премия к ОФЗ = доходность − ОФЗ(срок)', m.spreadOfz, m.ytm - A.ofzYield(B.ofz_curve, m.termYears), 1e-9);
eq('оценка в пределах 0–100', m.score.total >= 0 && m.score.total <= 100, true);
eq('сумма максимумов составляющих = 100', m.score.parts.reduce((a, p) => a + p.max, 0), 100);
// 8. Ставка ниже ОФЗ → премия отрицательная, 0 баллов за премию, вердикт не «привлекательно»
m = A.analyze({ ...x, rate: 8 }, B, { buyDate: '2027-01-31' });
eq('8 % годовых: премия к ОФЗ < 0', m.spreadOfz < 0, true); eq('8 %: баллы за премию', m.score.parts[0].pts, 0);
eq('8 %: вердикт', m.score.verdict.label, 'Слабо');
// 9. Калькулятор: 100 000 ₽ при цене 1000 → 100 шт., ограничение максимумом заявки
let calc = A.analyze({ ...x, maxOrder: 50 }, B).forAmount(100000);
eq('калькулятор: максимум в заявке', calc.qty, 50);
calc = A.analyze({ ...x, minOrder: 200 }, B).forAmount(100000); eq('калькулятор: меньше минимума заявки', calc.belowMin, true);
// 10. Некорректные параметры
eq('погашение раньше размещения → ok=false', A.analyze({ ...x, maturity: '2026-01-01' }, B).ok, false);
eq('пустая цена → ok=false', A.analyze({ ...x, price: 0 }, B).ok, false);
console.log(`\nПРОЙДЕНО: ${ok}   ПРОВАЛЕНО: ${bad}`);
process.exit(bad ? 1 : 0);
