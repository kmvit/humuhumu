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
            "logo",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
            "theme",
            "accent_color",
        )
        # через API правится только внешний вид; остальное — в админке
        read_only_fields = (
            "name",
            "tagline",
            "phone",
            "email",
            "address",
            "working_hours",
            "instagram",
            "telegram",
            "about",
        )

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
