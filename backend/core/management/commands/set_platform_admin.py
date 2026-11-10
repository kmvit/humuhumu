"""Explicitly grant/revoke hub access; cafe superusers are never promoted implicitly."""
from django.core.management.base import BaseCommand, CommandError
from users.models import User


class Command(BaseCommand):
    help = 'Выдать или отозвать доступ оператора хаба по ID пользователя'

    def add_arguments(self, parser):
        parser.add_argument('user_id', type=int)
        parser.add_argument('--revoke', action='store_true')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(pk=options['user_id'])
        except User.DoesNotExist as exc:
            raise CommandError('Пользователь не найден') from exc
        grant = not options['revoke']
        if grant and not (user.is_active and user.is_staff and user.is_superuser):
            raise CommandError('Нужна активная служебная учётка с is_staff и is_superuser')
        if grant and User.objects.filter(
            username=user.username, is_platform_admin=True
        ).exclude(pk=user.pk).exists():
            raise CommandError('У оператора хаба уже есть такой логин; выберите уникальный')
        user.is_platform_admin = grant
        user.save(update_fields=['is_platform_admin'])
        self.stdout.write(self.style.SUCCESS(
            f'Доступ к хабу для пользователя #{user.pk}: {"выдан" if grant else "отозван"}'))
