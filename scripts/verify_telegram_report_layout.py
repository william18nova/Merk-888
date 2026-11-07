"""Render fixture exports for manual visual QA; no DB or external API calls."""
import os
from pathlib import Path
from decimal import Decimal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.test_settings'
import django
django.setup()
from mainApp.services.telegram_reports import pdf, xlsx


def main():
    target = ROOT / 'tmp' / 'pdfs'
    target.mkdir(parents=True, exist_ok=True)
    headers = ['Pago', 'Fecha', 'Concepto', 'Base', '4 x 1.000', 'Total', 'Medio', 'Registró']
    rows = [[str(i), '2026-11-07 18:05', 'Distribuidora de alimentos, papelería y servicios de prueba - complemento de la factura 12345',
             Decimal('1234567.00'), Decimal('4938.27'), Decimal('1239505.27'), 'Banco Caja Social', 'Usuario de prueba'] for i in range(1, 91)]
    path = target / 'telegram-reporte-prueba.pdf'
    path.write_bytes(pdf(headers, rows, 'Pagos - 2026-11-01 a 2026-11-07 - 90 filas de prueba'))
    (target / 'telegram-reporte-prueba.xlsx').write_bytes(xlsx(headers, rows))
    print(path)


if __name__ == '__main__': main()
