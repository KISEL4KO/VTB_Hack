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
 */
contract CFAPlatform {
    // ---------------------------------------------------------------- состояние
    enum Status { None, Subscription, Issued, Cancelled }

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

    // ---------------------------------------------------------------- чтение
    function investorsOf(uint256 issueId) external view returns (address[] memory) {
        return _investors[issueId];
    }
}
