// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title CFAPlatform — смарт-контракт учёта ЦФА и расчётов DvP
 *
 * Модель — закрытая (permissioned) сеть оператора информационной системы:
 *  • транзакции отправляет только ОПЕРАТОР платформы (кастодиальная модель: он действует
 *    по поручению участников, подписанных ПЭП/УКЭП в веб-интерфейсе);
 *  • владеть ЦФА и цифровыми рублями могут только участники, прошедшие проверку администратора
 *    (белый список KYC);
 *  • «Цифровой рубль» здесь — токенизированный рубль внутри контракта: модель расчётов
 *    в цифровом рубле, чтобы поставка ЦФА и оплата происходили в ОДНОЙ транзакции.
 *
 * Жизненный цикл выпуска: registerIssue → subscribe (деньги в холд) → finalize:
 *  • собран минимум → ЦФА инвесторам, рубли эмитенту (DvP);
 *  • не собран      → рубли возвращаются инвесторам.
 * После размещения: setSchedule (график в контракте) → payCoupon в даты выплат → redeem в дату погашения.
 * Транзакции отправляет «сторож» оператора; дату, порядок и наличие денег эмитента проверяет контракт.
 */
contract CFAPlatform {
    // ---------------------------------------------------------------- состояние
    enum Status { None, Subscription, Issued, Cancelled, Redeemed }

    struct Issue {
        address issuer;
        uint256 price;          // цена 1 ЦФА, ₽
        uint256 minQty;         // порог состоявшегося выпуска, шт.
        uint256 maxQty;         // максимальный объём, шт.
        uint256 collected;      // заявлено инвесторами, шт.
        bool    onlyQualified;  // только для квалифицированных инвесторов
        bytes32 docHash;        // SHA-256 «Решения о выпуске» — связывает документ и токен
        Status  status;
    }

    address public immutable operator;

    mapping(address => bool) public verified;        // прошёл KYC у администратора
    mapping(address => bool) public qualified;       // квалифицированный инвестор / ЮЛ

    mapping(address => uint256) public rubBalance;   // свободные цифровые рубли
    mapping(address => uint256) public rubLocked;    // рубли в холде под заявки
    uint256 public totalRub;                         // всего выпущено цифровых рублей

    mapping(uint256 => Issue) public issues;                                   // id выпуска → параметры
    mapping(uint256 => mapping(address => uint256)) public balanceOf;          // ЦФА на счетах владельцев
    mapping(uint256 => mapping(address => uint256)) public pendingQty;         // ЦФА в заявках (до DvP)
    mapping(uint256 => address[]) private _investors;                          // кто подавал заявки
    mapping(uint256 => uint256) public totalSupply;                            // размещено ЦФА выпуска
    mapping(uint256 => uint256) public couponsPaid;                            // сколько купонных периодов выплачено

    // ---------------------------------------------------------------- события
    event ParticipantUpdated(address indexed who, bool verified, bool qualified);
    event RubDeposited(address indexed to, uint256 amount);
    event RubWithdrawn(address indexed from, uint256 amount);
    event IssueRegistered(uint256 indexed issueId, address indexed issuer, uint256 price,
                          uint256 minQty, uint256 maxQty, bytes32 docHash);
    event Subscribed(uint256 indexed issueId, address indexed investor, uint256 qty,
                     uint256 amount, bytes32 signatureHash);
    event Settled(uint256 indexed issueId, uint256 placedQty, uint256 raisedRub);   // DvP выполнен
    event Refunded(uint256 indexed issueId, uint256 refundedRub);                   // выпуск отменён
    event CouponPaid(uint256 indexed issueId, uint256 period, uint256 totalRub);     // купон выплачен
    event Redeemed(uint256 indexed issueId, uint256 qty, uint256 totalRub);         // номинал погашен

    // ---------------------------------------------------------------- доступ
    modifier onlyOperator() {
        require(msg.sender == operator, unicode"Только оператор платформы");
        _;
    }

    constructor() {
        operator = msg.sender;
    }

    // ---------------------------------------------------------------- участники
    /// Администратор одобрил (или заблокировал) участника — обновляем белый список.
    function setParticipant(address who, bool isVerified, bool isQualified) external onlyOperator {
        require(who != address(0), unicode"Пустой адрес");
        verified[who] = isVerified;
        qualified[who] = isQualified;
        emit ParticipantUpdated(who, isVerified, isQualified);
    }

    // ---------------------------------------------------------------- цифровой рубль
    function depositRub(address to, uint256 amount) external onlyOperator {
        require(verified[to], unicode"Участник не прошёл проверку");
        require(amount > 0, unicode"Сумма должна быть больше нуля");
        rubBalance[to] += amount;
        totalRub += amount;
        emit RubDeposited(to, amount);
    }

    function withdrawRub(address from, uint256 amount) external onlyOperator {
        require(amount > 0, unicode"Сумма должна быть больше нуля");
        require(rubBalance[from] >= amount, unicode"Недостаточно свободных средств");
        rubBalance[from] -= amount;
        totalRub -= amount;
        emit RubWithdrawn(from, amount);
    }

    // ---------------------------------------------------------------- выпуски
    /// Выпуск одобрен администратором → открывается сбор заявок.
    function registerIssue(
        uint256 issueId, address issuer, uint256 price, uint256 minQty, uint256 maxQty,
        bool onlyQualified, bytes32 docHash
    ) external onlyOperator {
        require(issues[issueId].status == Status.None, unicode"Выпуск уже зарегистрирован");
        require(verified[issuer], unicode"Эмитент не прошёл проверку");
        require(price > 0 && minQty > 0 && maxQty >= minQty, unicode"Некорректные параметры выпуска");
        issues[issueId] = Issue(issuer, price, minQty, maxQty, 0, onlyQualified, docHash, Status.Subscription);
        emit IssueRegistered(issueId, issuer, price, minQty, maxQty, docHash);
    }

    /// Заявка инвестора: рубли переводятся в холд, количество — в «ожидающие».
    function subscribe(uint256 issueId, address investor, uint256 qty, bytes32 signatureHash)
        external onlyOperator
    {
        Issue storage s = issues[issueId];
        require(s.status == Status.Subscription, unicode"Сбор заявок закрыт");
        require(verified[investor], unicode"Инвестор не прошёл проверку");
        require(!s.onlyQualified || qualified[investor], unicode"Выпуск только для квалифицированных инвесторов");
        require(qty > 0, unicode"Количество должно быть больше нуля");
        require(s.collected + qty <= s.maxQty, unicode"Превышен объём выпуска");

        uint256 amount = qty * s.price;
        require(rubBalance[investor] >= amount, unicode"Недостаточно свободных средств");

        rubBalance[investor] -= amount;
        rubLocked[investor] += amount;
        if (pendingQty[issueId][investor] == 0) {
            _investors[issueId].push(investor);
        }
        pendingQty[issueId][investor] += qty;
        s.collected += qty;
        emit Subscribed(issueId, investor, qty, amount, signatureHash);
    }

    /// Закрытие сбора. Атомарно: либо поставка ЦФА против платежа всем, либо возврат всем.
    function finalize(uint256 issueId) external onlyOperator returns (bool issued) {
        Issue storage s = issues[issueId];
        require(s.status == Status.Subscription, unicode"Выпуск уже обработан");
        address[] storage list = _investors[issueId];

        if (s.collected >= s.minQty) {
            uint256 raised;
            for (uint256 i = 0; i < list.length; i++) {
                address inv = list[i];
                uint256 qty = pendingQty[issueId][inv];
                if (qty == 0) continue;
                uint256 amount = qty * s.price;
                rubLocked[inv] -= amount;          // платёж
                balanceOf[issueId][inv] += qty;    // поставка
                pendingQty[issueId][inv] = 0;
                raised += amount;
            }
            rubBalance[s.issuer] += raised;
            totalSupply[issueId] = s.collected;
            s.status = Status.Issued;
            emit Settled(issueId, s.collected, raised);
            return true;
        }

        uint256 refunded;
        for (uint256 i = 0; i < list.length; i++) {
            address inv = list[i];
            uint256 qty = pendingQty[issueId][inv];
            if (qty == 0) continue;
            uint256 amount = qty * s.price;
            rubLocked[inv] -= amount;
            rubBalance[inv] += amount;
            pendingQty[issueId][inv] = 0;
            refunded += amount;
        }
        s.status = Status.Cancelled;
        emit Refunded(issueId, refunded);
        return false;
    }

    // ---------------------------------------------------------------- выплаты владельцам
    // График купонов записывается в контракт при размещении (setSchedule). Дальше контракт сам решает,
    // можно ли платить: дата выплаты наступила (block.timestamp), выплаты идут по порядку,
    // у эмитента хватает цифровых рублей. Транзакцию отправляет «сторож» оператора (keeper) —
    // сам контракт проснуться не может, — но правила проверяет только контракт.
    uint256 private constant DAY = 1 days;

    mapping(uint256 => uint64[]) private _couponDates;     // даты выплат (unix, 00:00 МСК), последняя = погашение
    mapping(uint256 => uint32[]) private _couponDays;      // длина каждого купонного периода, дней
    mapping(uint256 => uint64) public accrualStart;        // начало начисления купона (дата DvP)
    mapping(uint256 => uint32) public couponRateBp;        // ставка в сотых долях процента: 18,5 % = 1850
    mapping(uint256 => bool) public earlyRedemptionAllowed;

    event ScheduleSet(uint256 indexed issueId, uint256 periods, uint64 maturity);

    function setSchedule(uint256 issueId, uint64 start, uint32 rateBp, uint64[] calldata dates,
                         uint32[] calldata dayCounts, bool earlyRedemption) external onlyOperator {
        require(issues[issueId].status == Status.Issued, unicode"Выпуск не размещён");
        require(_couponDates[issueId].length == 0, unicode"График уже записан");
        require(dates.length > 0 && dates.length == dayCounts.length, unicode"Некорректный график выплат");
        uint64 prev = start;
        for (uint256 i = 0; i < dates.length; i++) {
            require(dates[i] > prev, unicode"Даты выплат должны возрастать");
            prev = dates[i];
        }
        accrualStart[issueId] = start;
        couponRateBp[issueId] = rateBp;
        earlyRedemptionAllowed[issueId] = earlyRedemption;
        _couponDates[issueId] = dates;
        _couponDays[issueId] = dayCounts;
        emit ScheduleSet(issueId, dates.length, dates[dates.length - 1]);
    }

    function scheduleOf(uint256 issueId) external view returns (uint64[] memory, uint32[] memory) {
        return (_couponDates[issueId], _couponDays[issueId]);
    }

    function maturityOf(uint256 issueId) public view returns (uint64) {
        uint64[] storage d = _couponDates[issueId];
        return d.length == 0 ? 0 : d[d.length - 1];
    }

    /// Купон владельца за период: qty × цена × ставка × дни / 365, целые рубли вниз.
    function couponOf(uint256 issueId, address holder, uint256 periodDays) public view returns (uint256) {
        return balanceOf[issueId][holder] * issues[issueId].price * couponRateBp[issueId] * periodDays / 3650000;
    }

    /// Что «сторожу» пора сделать по выпуску: 0 — ничего, 1 — купон, 2 — погашение. Деньги эмитента не проверяет.
    function dueAction(uint256 issueId) public view returns (uint8) {
        if (issues[issueId].status != Status.Issued) return 0;
        uint64[] storage d = _couponDates[issueId];
        if (d.length == 0) return 0;
        uint256 paid = couponsPaid[issueId];
        if (paid < d.length) return block.timestamp >= d[paid] ? 1 : 0;
        return block.timestamp >= d[d.length - 1] ? 2 : 0;
    }

    /// Очередной купон — только когда наступила его дата; с кошелька эмитента всем владельцам.
    function payCoupon(uint256 issueId) external onlyOperator returns (uint256 total) {
        Issue storage s = issues[issueId];
        require(s.status == Status.Issued, unicode"Выпуск не размещён или уже погашен");
        uint64[] storage d = _couponDates[issueId];
        uint256 period = couponsPaid[issueId] + 1;
        require(d.length > 0, unicode"График выплат не записан");
        require(period <= d.length, unicode"Все купоны выплачены");
        require(block.timestamp >= d[period - 1], unicode"Дата выплаты купона ещё не наступила");
        uint256 days_ = _couponDays[issueId][period - 1];
        address[] storage list = _investors[issueId];
        for (uint256 i = 0; i < list.length; i++) total += couponOf(issueId, list[i], days_);
        require(rubBalance[s.issuer] >= total, unicode"Недостаточно средств эмитента для выплаты купона");
        rubBalance[s.issuer] -= total;
        for (uint256 i = 0; i < list.length; i++) rubBalance[list[i]] += couponOf(issueId, list[i], days_);
        couponsPaid[issueId] = period;
        emit CouponPaid(issueId, period, total);
    }

    /// Погашение: в дату погашения после всех купонов. Досрочно — только если разрешено Решением о выпуске:
    /// тогда владельцы получают номинал + купон, накопленный за accruedDays текущего периода, остальные купоны отменяются.
    function redeem(uint256 issueId, uint256 accruedDays) external onlyOperator returns (uint256 total) {
        Issue storage s = issues[issueId];
        require(s.status == Status.Issued, unicode"Выпуск не размещён или уже погашен");
        uint64[] storage d = _couponDates[issueId];
        require(d.length > 0, unicode"График выплат не записан");
        uint256 paid = couponsPaid[issueId];
        if (block.timestamp >= d[d.length - 1]) {
            require(paid == d.length, unicode"Сначала выплачиваются все купоны");
            require(accruedDays == 0, unicode"В дату погашения купон выплачивается отдельно");
        } else {
            require(earlyRedemptionAllowed[issueId], unicode"Досрочное погашение не предусмотрено Решением о выпуске");
            require(paid < d.length, unicode"Все купоны уже выплачены");
            uint64 periodStart = paid == 0 ? accrualStart[issueId] : d[paid - 1];
            require(block.timestamp < d[paid], unicode"Сначала выплачивается наступивший купон");
            require(uint256(periodStart) + accruedDays * DAY <= block.timestamp, unicode"Накопленный купон больше прошедших дней");
        }
        address[] storage list = _investors[issueId];
        for (uint256 i = 0; i < list.length; i++) {
            total += balanceOf[issueId][list[i]] * s.price + couponOf(issueId, list[i], accruedDays);
        }
        require(rubBalance[s.issuer] >= total, unicode"Недостаточно средств эмитента для погашения");
        rubBalance[s.issuer] -= total;
        for (uint256 i = 0; i < list.length; i++) {
            address h = list[i];
            uint256 qty = balanceOf[issueId][h];
            if (qty == 0) continue;
            uint256 amount = qty * s.price + couponOf(issueId, h, accruedDays);
            balanceOf[issueId][h] = 0;
            rubBalance[h] += amount;
        }
        emit Redeemed(issueId, totalSupply[issueId], total);
        totalSupply[issueId] = 0;
        s.status = Status.Redeemed;
    }

    // ---------------------------------------------------------------- чтение
    function investorsOf(uint256 issueId) external view returns (address[] memory) {
        return _investors[issueId];
    }
}
