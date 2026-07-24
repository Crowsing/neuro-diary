# План майбутнього backend і Telegram-інтеграції

> Статус: не схвалена до реалізації чернетка. Вона не описує наявні можливості продукту.

## Фактичний стан

- `apps/web` працює local-only і не виконує мережевих запитів.
- `apps/api` має `/health` і Pydantic-схеми активного diary-контракту; синхронізації та серверного сховища немає.
- `apps/bot` обробляє приватний `/start` і показує кнопку відкриття щоденника. Scheduler та доставка нагадувань відсутні.
- Дані web зберігаються під чинним ключем `localStorage["nd_demo_v3"]`; назва ключа не є номером активної схеми й не змінюється без окремої безвтратної міграції.
- `docs/prototype` — архівний snapshot, а не acceptance contract.

## Межі

- Не додавати sync, scheduler, browser push або delivery affordance частинами. UI не показує toggle, час, permission CTA, snooze чи success-state, доки наскрізна функція не працює.
- Toast-и лишаються локальним неперсистентним UI feedback.
- Актуальні diary state, persistence, export і API output не містять reminder preferences.
- Legacy `remOn`, `remTime`, `obRemOn` і `obTime` можна прийняти лише на явній legacy input boundary та слід ігнорувати. `remOn: true` ніколи не створює consent.
- Health-sync consent і Telegram-reminder consent — незалежні.

## Майбутній backend

Backend можна починати лише після окремого погодження продуктового scope, data residency, retention/erasure, threat model і consent copy.

Мінімальна послідовність:

1. Зафіксувати versioned API contracts та trust boundaries.
2. Реалізувати й протестувати Telegram initData/auth без логування payload-ів.
3. Додати окремий явний opt-in для health-sync; local-only режим має лишатися повністю робочим.
4. Реалізувати encrypted storage, export, revoke та deletion lifecycle із перевіреним backup-вікном.
5. Лише після privacy/security/clinical review розглядати реальну Telegram-доставку за [окремою специфікацією](future-telegram-reminders.md).

API є єдиним власником майбутнього серверного стану. Медичні значення не потрапляють у URL, логи, аналітику, повідомлення про помилки або Telegram.

## Майбутня Telegram-доставка

Повний контракт живе лише в [future-telegram-reminders.md](future-telegram-reminders.md). Зокрема:

- fresh explicit opt-in, незалежний від health-sync;
- окремий `ReminderSettings` з `enabled`, валідованим `HH:mm` та IANA timezone;
- API як єдине джерело розкладу й dedupe;
- stateless bot і private chats only;
- статичний exact allowlist нейтральних повідомлень;
- жодного browser/system push;
- snooze не входить до першої версії;
- quiet hours визначаються один раз після review і не дублюються між шарами.

## Gate

- web unit, production build і Playwright залишаються зеленими;
- API pytest і Ruff залишаються зеленими;
- bot pytest і Ruff залишаються зеленими;
- нова інфраструктура має власні інтеграційні, privacy й failure-mode тести;
- жодна майбутня функція не зʼявляється в UI або документації як доступна до завершення всього delivery path.
