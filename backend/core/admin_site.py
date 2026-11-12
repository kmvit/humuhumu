"""Shared admin UI with separate platform and cafe authority boundaries."""
from django.conf import settings
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied

from .tenancy import TenantMixin, current_organization_or_none


def is_hub_request(request):
    from .models import Organization
    return bool(settings.ADMIN_HOST) and Organization.normalize_host(
        request.get_host()
    ) == Organization.normalize_host(settings.ADMIN_HOST)


def is_platform_request(request):
    user = request.user
    return bool(is_hub_request(request) and user.is_active and user.is_staff
                and user.is_superuser and user.is_platform_admin)


class ScopedAdminMixin:
    def _allowed(self, request):
        return is_platform_request(request) or issubclass(self.model, TenantMixin)

    def has_module_permission(self, request):
        return self._allowed(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._allowed(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request, *args, **kwargs):
        # InlineModelAdmin also passes the parent object to this permission.
        return self._allowed(request) and super().has_add_permission(request, *args, **kwargs)

    def has_change_permission(self, request, obj=None):
        return self._allowed(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._allowed(request) and super().has_delete_permission(request, obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not self._allowed(request):
            return qs.none()
        if issubclass(self.model, TenantMixin):
            org = current_organization_or_none()
            qs = qs.filter(organization=org) if org else qs.none()
        if self.model._meta.label_lower == 'users.user' and not is_platform_request(request):
            qs = qs.filter(is_platform_admin=False)
        return qs

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if issubclass(self.model, TenantMixin):
            fields.append('organization')
        if self.model._meta.label_lower == 'users.user':
            fields.append('is_platform_admin')  # Grant only through the explicit operator command.
        if self.model._meta.label_lower == 'users.user' and not is_platform_request(request):
            fields.extend(('is_superuser', 'is_staff', 'is_platform_admin', 'groups', 'user_permissions'))
        return tuple(dict.fromkeys(fields))

    def _scope_related(self, request, db_field, kwargs):
        model = db_field.remote_field.model
        if issubclass(model, TenantMixin):
            qs = kwargs.get('queryset', model._default_manager.all())
            qs = qs.filter(organization=current_organization_or_none())
            if model._meta.label_lower == 'users.user' and not is_platform_request(request):
                qs = qs.filter(is_platform_admin=False)
            kwargs['queryset'] = qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        self._scope_related(request, db_field, kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        self._scope_related(request, db_field, kwargs)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not self._allowed(request):
            raise PermissionDenied
        if isinstance(obj, TenantMixin):
            obj.organization = current_organization_or_none()
        super().save_model(request, obj, form, change)


class CafeAdminSite(AdminSite):
    index_template = 'admin/scoped_index.html'

    def get_urls(self):
        from django.urls import path
        from .backups import backup_index, backup_create, backup_download
        return [
            path('backups/', self.admin_view(lambda request: backup_index(request, self)), name='backups'),
            path('backups/create/', self.admin_view(backup_create), name='backup-create'),
            path('backups/download/<str:filename>/', self.admin_view(backup_download), name='backup-download'),
        ] + super().get_urls()

    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        if app_label is None and is_platform_request(request):
            from django.urls import reverse
            url = reverse('admin:backups')
            apps.append({'name': 'Обслуживание', 'app_label': 'maintenance', 'app_url': url,
                         'has_module_perms': True,
                         'models': [{'name': 'Резервные копии', 'object_name': 'Backups',
                                     'admin_url': url, 'view_only': True,
                                     'perms': {'view': True}}]})
        return apps

    def each_context(self, request):
        return {**super().each_context(request), 'is_platform_hub': is_platform_request(request)}

    def has_permission(self, request):
        if is_hub_request(request):
            return is_platform_request(request)
        org = current_organization_or_none()
        return bool(super().has_permission(request) and org
                    and request.user.organization_id == org.pk)

    def register(self, model_or_iterable, admin_class=None, **options):
        from django.contrib.admin import ModelAdmin
        base = admin_class or ModelAdmin
        inlines = [type('Scoped' + inline.__name__, (ScopedAdminMixin, inline), {})
                   for inline in base.inlines]
        scoped = type('Scoped' + base.__name__, (ScopedAdminMixin, base), {'inlines': inlines})
        return super().register(model_or_iterable, scoped, **options)
