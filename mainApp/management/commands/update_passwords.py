from django.core.management.base import BaseCommand
from mainApp.models import Usuario

class Command(BaseCommand):
    help = 'Update passwords to be hashed'

    def handle(self, *args, **kwargs):
        for user in Usuario.objects.all():
            # Actualiza la contraseña solo si no está ya en formato hash
            if not user.password.startswith('pbkdf2_'):
                user.set_password(user.password)
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Successfully updated password for user {user.nombreusuario}'))
            else:
                self.stdout.write(f'Password for user {user.nombreusuario} is already hashed')
