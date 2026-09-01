from django.db import models


class SiteSettings(models.Model):
    """Настройки сайта — одна запись (singleton). Редактируется в админке."""

    class Theme(models.TextChoices):
        NEUTRAL = "neutral", "Нейтраль"
        WARM = "warm", "Тёплая"
        STRICT = "strict", "Строгая"
        ISLAND = "island", "Островная"

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
    legal_updated = models.CharField(
        "Дата редакции документов", max_length=60, blank=True,
        help_text="Как показывать на юр. страницах, напр. «3 августа 2026 г.»",
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
