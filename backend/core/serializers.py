from rest_framework import serializers

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()
    accent_color = serializers.RegexField(
        regex=r"^#[0-9a-fA-F]{6}$",
        allow_blank=True,
        required=False,
    )

    class Meta:
        model = SiteSettings
        fields = (
            "name",
            "tagline",
            "app_short_name",
            "logo",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
            "theme",
            "service_mode",
            "dark_by_default",
            "accent_color",
            "merchant_type",
            "merchant_name",
            "merchant_short",
            "merchant_address",
            "merchant_inn",
            "merchant_ogrn",
            "merchant_account",
            "merchant_bank",
            "merchant_bank_inn",
            "merchant_bik",
            "merchant_corr_account",
            "merchant_bank_address",
            "acquirer",
            "legal_updated",
        )
        # через API правится только внешний вид; остальное — в админке
        read_only_fields = (
            "name",
            "tagline",
            "app_short_name",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
            "service_mode",
            "dark_by_default",
            "merchant_type",
            "merchant_name",
            "merchant_short",
            "merchant_address",
            "merchant_inn",
            "merchant_ogrn",
            "merchant_account",
            "merchant_bank",
            "merchant_bank_inn",
            "merchant_bik",
            "merchant_corr_account",
            "merchant_bank_address",
            "acquirer",
            "legal_updated",
        )

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
