from rest_framework import serializers

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    logo = serializers.SerializerMethodField()

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
        )

    def get_logo(self, obj):
        return obj.logo.url if obj.logo else None
