from django.db import models

from .tenancy import TenantMixin


class Organization(models.Model):
    """Заведение как сущность данных — будущий тенант.

    Пока в каждой базе ровно одна запись: продукт разворачивается по
    инстансу на точку. Смысл модели в том, чтобы связи и фильтрация были
    готовы заранее — см. core/tenancy.py.
    """

    name = models.CharField("Название", max_length=160)
    slug = models.SlugField("Код", max_length=60, unique=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Заведение"
        verbose_name_plural = "Заведения"

    def __str__(self):
        return self.name


class SiteSettings(TenantMixin):
    """Настройки сайта — одна запись (singleton). Редактируется в админке."""

    class Theme(models.TextChoices):
        NEUTRAL = "neutral", "Нейтраль"
        WARM = "warm", "Тёплая"
        STRICT = "strict", "Строгая"
        ISLAND = "island", "Островная"
        PADACHA = "padacha", "Падача"

    name = models.CharField("Название", max_length=120, default="Кафе")
    tagline = models.CharField("Слоган", max_length=200, blank=True)
    app_short_name = models.CharField(
        "Короткое имя приложения", max_length=12, blank=True,
        help_text="Подпись под иконкой на телефоне. Пусто — обрежется из названия",
    )
    logo = models.ImageField("Логотип", upload_to="site/", null=True, blank=True)

    phone = models.CharField("Телефон", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Адрес", max_length=255, blank=True)
    working_hours = models.CharField("Часы работы", max_length=120, blank=True)

    instagram = models.CharField("Instagram", max_length=200, blank=True)
    telegram = models.CharField("Telegram", max_length=200, blank=True)
    about = models.TextField("О нас", blank=True)

    theme = models.CharField(
        "Тема оформления",
        max_length=20,
        choices=Theme.choices,
        default=Theme.NEUTRAL,
    )

    class Plan(models.TextChoices):
        START = "start", "Старт"
        HALL = "hall", "Зал"
        MAX = "max", "Максимум"

    # Тариф — то, что продаёт лэндинг «Падачи». Что именно входит в каждый,
    # описано одним словарём в core/plans.py; вьюхи проверяют фичи, а не
    # тарифы. Дефолт — «Старт»: новая установка не должна раздавать
    # «Максимум» бесплатно. Существующим заведениям миграция ставит max.
    # Пока поле правится в Django-админке при подключении; когда появится
    # пульт «Падачи», сюда будет писать лицензия.
    plan = models.CharField(
        "Тариф",
        max_length=8,
        choices=Plan.choices,
        default=Plan.START,
        help_text=(
            "«Старт» — меню, заказы и оплаты; «Зал» — плюс экраны кухни и "
            "бара; «Максимум» — плюс склад, смены и финансы."
        ),
    )
    class ServiceMode(models.TextChoices):
        HALL = "hall", "Зал с официантами"
        COUNTER = "counter", "Стойка / окно выдачи"

    service_mode = models.CharField(
        "Формат обслуживания",
        max_length=16,
        choices=ServiceMode.choices,
        default=ServiceMode.HALL,
        help_text=(
            "«Стойка» — для точек без зала: гость заказывает по QR, забирает "
            "по номеру. Столов и официанта нет, заказ сразу уходит в работу."
        ),
    )
    dark_by_default = models.BooleanField(
        "Тёмная тема по умолчанию", default=False,
        help_text="Какой режим видит гость, пока сам не переключил",
    )
    accent_color = models.CharField(
        "Акцентный цвет",
        max_length=7,
        blank=True,
        default="",
        help_text="HEX вида #1f58a6; пусто — фирменный цвет темы",
    )

    # ── Реквизиты продавца ────────────────────────────────────────────────
    # Подставляются в оферту, политику, страницу оплаты и контакты.
    # Раньше лежали в коде фронта (legal.ts) — из-за этого продукт нельзя
    # было поставить другому заведению без правки исходников.
    merchant_type = models.CharField(
        "Форма", max_length=120, blank=True,
        help_text="Напр. «Индивидуальный предприниматель» или «ООО»",
    )
    merchant_name = models.CharField("Полное наименование", max_length=255, blank=True)
    merchant_short = models.CharField("Краткое наименование", max_length=120, blank=True)
    merchant_address = models.CharField("Юридический адрес", max_length=255, blank=True)
    merchant_inn = models.CharField("ИНН", max_length=20, blank=True)
    merchant_ogrn = models.CharField("ОГРН / ОГРНИП", max_length=20, blank=True)
    merchant_account = models.CharField("Расчётный счёт", max_length=34, blank=True)
    merchant_bank = models.CharField("Банк", max_length=160, blank=True)
    merchant_bank_inn = models.CharField("ИНН банка", max_length=20, blank=True)
    merchant_bik = models.CharField("БИК", max_length=12, blank=True)
    merchant_corr_account = models.CharField("Корр. счёт", max_length=34, blank=True)
    merchant_bank_address = models.CharField("Адрес банка", max_length=255, blank=True)
    acquirer = models.CharField(
        "Эквайер", max_length=160, blank=True,
        help_text="Кто принимает онлайн-оплату, напр. «АО «ТБанк» (Т-Касса)»",
    )

    class Acquiring(models.TextChoices):
        NONE = "none", "Нет онлайн-оплаты"
        TBANK = "tbank", "Т-Банк (Т-Касса)"
        SBER = "sber", "Сбербанк (в т.ч. через Эвотор)"
        YOOKASSA = "yookassa", "ЮKassa"

    # Выбор эквайринга — настройка заведения, а не сборки: у разных кафе
    # разные договоры. Ключи и пароли живут ТОЛЬКО в переменных окружения
    # (см. payments/acquiring.py): GET /api/site/ отдаётся без авторизации,
    # и секрету в этой модели не место.
    acquiring = models.CharField(
        "Интернет-эквайринг",
        max_length=16,
        choices=Acquiring.choices,
        default=Acquiring.NONE,
        help_text=(
            "Кто принимает оплату картой онлайн. Доступы задаются "
            "переменными окружения на сервере заведения, не здесь."
        ),
    )
    # Выключатель приёма оплаты картой. Отдельно от выбора банка: банк с
    # ключами настраивают один раз, а закрыть онлайн-оплату может
    # понадобиться в любой момент — банк лёг, день только за наличные.
    # Включить оплату, когда доступов нет, он не позволяет: см.
    # SiteSettingsSerializer.get_online_payment.
    online_payment_on = models.BooleanField(
        "Принимать оплату картой онлайн", default=True,
        help_text=(
            "Быстрый выключатель для гостевой кнопки «Оплатить картой». "
            "Работает, только если банк подключён и заданы его доступы."
        ),
    )
    legal_updated = models.CharField(
        "Дата редакции документов", max_length=60, blank=True,
        help_text="Как показывать на юр. страницах, напр. «3 августа 2026 г.»",
    )

    # ── Код на удаление позиций в работе ────────────────────────────────
    # Новую (ещё не взятую кухней/баром) позицию официант убирает свободно.
    # Позицию, которую станция уже готовит, — только менеджер/владелец,
    # назвав этот код. Пусто — удаление таких позиций запрещено всем.
    item_remove_code = models.CharField(
        "Код на удаление позиций в работе", max_length=20, blank=True,
        help_text=(
            "Официант должен назвать этот код, чтобы убрать позицию, которую "
            "кухня/бар уже готовят. Новые позиции убираются без кода. Пусто — "
            "убрать позицию в работе не может никто (кроме админа)."
        ),
    )

    # ── Бонусная программа ──────────────────────────────────────────────
    # 1 бонус = 1 ₽. Списывать бонусы может официант на закрытии счёта и/или
    # сам гость в приложении — сценарии включаются независимо: в зале удобен
    # первый, на стойке с QR-заказом — второй.
    bonus_enabled = models.BooleanField(
        "Бонусная программа включена", default=False,
        help_text="Начисление и списание бонусов. 1 бонус = 1 ₽",
    )
    bonus_welcome = models.PositiveIntegerField(
        "Приветственные бонусы", default=200,
        help_text="Начисляются один раз при регистрации в программе",
    )
    bonus_earn_percent = models.DecimalField(
        "Начисление с покупки, %", max_digits=5, decimal_places=2, default=5,
        help_text="Процент от оплаченной деньгами суммы чека",
    )
    bonus_redeem_waiter = models.BooleanField(
        "Официант списывает бонусы", default=True,
        help_text="На закрытии счёта официант находит гостя по телефону и списывает",
    )
    bonus_redeem_guest = models.BooleanField(
        "Гость списывает сам", default=False,
        help_text="Гость применяет бонусы к своему заказу в приложении",
    )

    class Meta:
        verbose_name = "Настройки сайта"
        verbose_name_plural = "Настройки сайта"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1  # всегда одна запись
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class LicenseState(TenantMixin):
    """Кэш последнего ответа пульта «Падачи» — одна запись (singleton).

    Пульт присылает факты (тариф, «оплачено до», грейс), а лестницу
    статусов инстанс считает сам от текущей даты (core/license.py):
    закэшированный статус протух бы между суточными сверками. Кэш нужен,
    чтобы недоступность пульта не останавливала кафе — см. фейл-опен
    в effective_status().
    """

    plan = models.CharField("Тариф из лицензии", max_length=8, blank=True)
    paid_until = models.DateField("Оплачено до", null=True, blank=True)
    # Своя точка «Падачи»: подписка не тарифицируется, блокировать нельзя.
    # Отдельный признак, а не фиктивная дата: иначе панель владельца
    # показывает «оплачено до» год вперёд, а в пульте стоит другое.
    internal = models.BooleanField("Своя точка", default=False)
    grace_days = models.PositiveSmallIntegerField("Грейс, дней", default=7)
    issued_at = models.DateTimeField("Ответ выдан", null=True, blank=True)
    checked_at = models.DateTimeField("Последняя сверка", null=True, blank=True)
    last_error = models.TextField("Последняя ошибка", blank=True)

    class Meta:
        verbose_name = "Лицензия"
        verbose_name_plural = "Лицензия"

    def __str__(self):
        return f"Лицензия: {self.plan or '—'} до {self.paid_until or '—'}"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
