from django.contrib.admin.apps import AdminConfig


class CafeAdminConfig(AdminConfig):
    default_site = "core.admin_site.CafeAdminSite"
