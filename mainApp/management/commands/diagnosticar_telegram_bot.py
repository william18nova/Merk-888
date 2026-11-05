"""Diagnóstico agregado: solo lectura, sin APIs, mensajes, importes ni secretos."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.db.models import Count
from django.utils import timezone

from mainApp.models import TelegramActualizacion


class Command(BaseCommand):
    help = 'Muestra la salud agregada de la cola de Telegram sin enviar mensajes ni modificar registros.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')

    def handle(self, *args, **options):
        now = timezone.now()
        try:
            rows = TelegramActualizacion.objects.all()
            counts = dict(rows.values('estado').annotate(total=Count('pk')).values_list('estado', 'total'))
            oldest = rows.filter(estado='PENDIENTE').order_by('recibido_en').values_list('recibido_en', flat=True).first()
            result = {
                'estados': counts,
                'pendientes_listos': rows.filter(estado='PENDIENTE', reintentar_en__isnull=True).count()
                    + rows.filter(estado='PENDIENTE', reintentar_en__lte=now).count(),
                'pendientes_en_espera': rows.filter(estado='PENDIENTE', reintentar_en__gt=now).count(),
                'procesando_mas_de_10_minutos': rows.filter(estado='PROCESANDO', iniciado_en__lt=now-timedelta(minutes=10)).count(),
                'errores_ultima_hora': rows.filter(estado='ERROR', procesado_en__gte=now-timedelta(hours=1)).count(),
                'antiguedad_pendiente_segundos': max(0, int((now-oldest).total_seconds())) if oldest else None,
                'solo_lectura': True,
            }
        except DatabaseError:
            raise CommandError('No se pudo consultar la cola. Revisa la conexión y las migraciones; no se modificó ningún registro.') from None
        if options['as_json']:
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return
        self.stdout.write('Salud de Telegram (solo lectura; sin llamadas externas):')
        for key, value in result.items():
            self.stdout.write(f'{key}: {value}')
