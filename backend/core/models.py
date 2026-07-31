from django.db import models


class SiteSettings(models.Model):
    """Настройки сайта — одна запись (singleton). Редактируется в админке."""

    name = models.CharField("Название", max_length=120, default="Кафе")
    tagline = models.CharField("Слоган", max_length=200, blank=True)
    logo = models.ImageField("Логотип", upload_to="site/", null=True, blank=True)

    phone = models.CharField("Телефон", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Адрес", max_length=255, blank=True)
    working_hours = models.CharField("Часы работы", max_length=120, blank=True)

    instagram = models.CharField("Instagram", max_length=200, blank=True)
    telegram = models.CharField("Telegram", max_length=200, blank=True)
    about = models.TextField("О нас", blank=True)

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
