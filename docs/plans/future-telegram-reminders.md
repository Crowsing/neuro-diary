# Майбутній контракт Telegram-нагадувань

> Статус: окрема специфікація майбутньої функції, не опис поточної поведінки.
> Реалізація заблокована до окремих privacy, security і clinical review.

## Поточна межа

- Застосунок не доставляє нагадування у фоні.
- Telegram-бот лише відкриває щоденник із приватного чату.
- Browser/system push не підтримується і не планується як паралельний канал.
- Toast у web — локальне неперсистентне підтвердження дії, не нагадування.
- Legacy `remOn`, навіть зі значенням `true`, не є згодою та не мігрується в активне налаштування.

## Обовʼязковий продуктовий контракт

1. Єдиний зовнішній канал — Telegram, лише в private chat.
2. Увімкнення потребує нового явного opt-in після показу актуального тексту згоди. Згода на Telegram-нагадування незалежна від згоди на health-sync, дані циклу чи будь-яку іншу обробку.
3. API — єдине джерело правди для згоди, розкладу, часової зони, due-рішення та dedupe. Web не зберігає авторитетну копію розкладу, bot не має власного стану.
4. Bot stateless: отримує від API лише мінімальну delivery-команду, надсилає allowlisted повідомлення й підтверджує результат. Він не читає записи щоденника.
5. Повідомлення і кнопки — лише зі статичного exact allowlist, без інтерполяції імен, дат, груп, симптомів, оцінок, циклу, нотаток або пропущених днів.
6. Відкликання згоди атомарно вимикає майбутні delivery-команди.

## Модель API

Окремий ресурс, який не входить до `AppData`:

```json
{
  "enabled": false,
  "time": "20:00",
  "timezone": "Europe/Kyiv"
}
```

`ReminderSettings` має такі інваріанти:

- `enabled` — boolean; `true` дозволено лише за наявності чинного fresh opt-in;
- `time` — локальний час у строгому форматі `HH:mm` (`00:00`–`23:59`);
- `timezone` — чинний IANA timezone, перевірений через timezone database;
- зміна часу або timezone відбувається через API й повертає канонічний ресурс;
- поля не приймаються й не повертаються актуальними diary/export схемами.

Запис згоди зберігає щонайменше версію тексту, час надання та час відкликання. Старі web-поля не створюють цей запис.

## Доставка й приватність

Початковий allowlist майбутньої доставки:

- текст: `Час зробити короткий запис`;
- кнопка: `Відкрити щоденник`;
- URL: лише конфігурований `WEBAPP_URL`, без медичних або персональних query-параметрів.

Snooze не входить до першої версії контракту. Якщо його колись схвалять, API має моделювати snooze як перенесення тієї самої delivery occurrence: snooze не позначає її доставленою, а атомарний occurrence id захищає від дублювання. Callback payload лишається непрозорим і не містить даних користувачки.

Quiet hours не мають зафіксованого діапазону до продуктового та клінічного рішення. Після погодження політика живе лише в API; bot і web не дублюють її.

## Gate перед реалізацією

- погоджені consent copy, retention і revoke/blocked-chat behavior;
- privacy/security/clinical review реальної доставки;
- одна політика quiet hours і DST-тести для IANA timezone;
- атомарні claim/ack/dedupe semantics із тестом повторної доставки;
- exact allowlist-тести для всіх outbound text, labels і callback payloads;
- інтеграційні тести private-chat binding, opt-in/revoke та відмов Telegram API;
- чесний unavailable-state лишається в UI, доки весь delivery path не пройде ці gate-и.

## Звірка з кодом (Фаза 6)

Перелік вище **не переписаний**: він фіксує стан на момент, коли писався, і
цінний саме цим. Нижче — що з нього закрив код, а що лишається відкритим.
Порядок рядків збігається з порядком пунктів gate.

| # | Пункт gate | Стан | Чим закрито / чого бракує |
|---|---|---|---|
| 1 | consent copy, retention, revoke/blocked-chat | **частково** | retention і revoke/blocked-chat закриті: `ErasureService.erase_reminders`, `MESSAGE_CLEANUP_TTL = 48 год`, TTL `reminder_delivery` 14 днів, `BOT_BLOCKED_STREAK = 14 днів` плюс `_reconcile_blocked_bots`. **Copy — ні:** `consent-copy/registry.json` тримає `telegram_reminders@0.9`, `frozen: false`, у тексті плейсхолдер `[ім'я контролера]` |
| 2 | privacy/security/clinical review доставки | **відкрито, людське** | Досьє зібране — [threat-model.md](../threat-model.md) розділ 5. Підписів немає, і код їх не ставить |
| 3 | одна політика quiet hours і DST-тести | **закрито кодом** | `QUIET_HOURS_START = 22:00`, `QUIET_HOURS_END = 08:00` у `app/domain/reminders.py`, експорт у [`fixtures/contract/quiet-hours.json`](../../fixtures/contract/quiet-hours.json), який тепер читають **обидві** сторони. DST: `tests/unit/test_reminder_domain.py::test_a_nonexistent_local_time_fires_at_the_first_valid_instant` (Київ, 29.03.2026) і `::test_an_ambiguous_local_time_fires_at_its_first_occurrence` (25.10.2026), обидва проти запіненої `tzdata`, не проти tzdata хоста |
| 4 | атомарні claim/ack/dedupe + тест повторної доставки | **закрито кодом** | PK `(account_id, local_date)` як ключ ідемпотентності, `claim_occurrence` у SAVEPOINT, повторне читання розкладу під `FOR UPDATE`; `tests/integration/test_reminder_worker.py` |
| 5 | exact allowlist outbound | **закрито кодом** | `tests/unit/test_telegram_bot_api.py::test_the_outbound_message_is_exactly_the_two_allowlisted_strings`. Callback payload відсутній: snooze поза першою версією контракту |
| 6 | private-chat binding, opt-in/revoke, відмови Telegram API | **закрито кодом** | `ck_reminder_schedule_private_chat`; `tests/integration/test_reminder_settings.py`, `test_revocation_erasure.py`, гілки 403/429 у тестах воркера |
| 7 | чесний unavailable-state лишається в UI | **чинний, і виконується** | Оскільки 1 і 2 відкриті, «весь delivery path» gate не пройшов. UI нагадувань **реалізований і вимкнений прапорцем** `VITE_REMINDERS` (default `off`); картка «Нагадування недоступні» лишається дослівно тією самою, і це під `e2e/production-controls.spec.ts` |

**Розбіжність, яку звірка виявила.** Рядок §«Доставка й приватність» вище каже:
«Quiet hours не мають зафіксованого діапазону до продуктового та клінічного
рішення». Станом на Фазу 6 це вже не так: діапазон ухвалений Gate D, живе
константами в домені, експортується спільною фікстурою й **названий дослівно в
самому тексті згоди `0.9`** («Час можна вибрати між 08:00 і 22:00»). Клінічний
підпис під цим діапазоном справді лишається відкритим — але це пункт 2 gate, а
не пункт 3. Речення в §«Доставка й приватність» лишене недоторканим свідомо: це
опис наміру на момент написання, і переписати його заднім числом означало б
стерти слід рішення.

### Що потрібно, щоб увімкнути

Рівно дві речі, і жодна з них не кодова:

1. заморозити тексти згод до `1.0`, назвавши контролера (знімає `503
   consent_copy_not_frozen` поза `APP_ENV=development`);
2. підписи privacy/security/clinical review реальної доставки.

Після цього `VITE_REMINDERS=on` у продакшеновій збірці — і більше нічого.
