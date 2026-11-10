"""User names and phones are unique within the selected cafe, including in admin."""
from django import forms
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from core.tenancy import current_organization_or_none
from .models import User


class CafeUserFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.instance.organization = current_organization_or_none()

    def clean_username(self):
        username = self.cleaned_data['username']
        if not self.instance.organization_id:
            raise forms.ValidationError('Сначала выберите заведение в хабе.')
        matches = User.objects.filter(
            organization_id=self.instance.organization_id, username__iexact=username,
        ).exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError('Пользователь с таким логином уже существует в этом заведении.')
        return username

    def clean_phone(self):
        phone = self.cleaned_data.get('phone') or None
        if phone and User.objects.filter(
            organization_id=self.instance.organization_id, phone=phone,
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Пользователь с таким телефоном уже существует в этом заведении.')
        return phone


class CafeUserCreationForm(CafeUserFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username',)


class CafeUserChangeForm(CafeUserFormMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
