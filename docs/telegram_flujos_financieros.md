# Telegram: cierres, comprobantes, lotes, archivos y avisos

Ampliación del bot en el repositorio. Requiere instalar las dependencias actuales,
aplicar **0054_telegram_workflows** y reiniciar el mismo trabajador con
`--followups`. Este documento no significa que ya esté desplegada en producción.
No se habilita ningún aviso ni se registra un pago al instalarla.

## Cómo pedirlo

Puedes usar tus palabras, por texto o audio; los ejemplos no son frases obligatorias.

1. «Compara lo que declararon en los cierres de esta semana con los pagos».
   Revisa todos los turnos cerrados, más recientes primero, desde el 01/11/2026.
   Muestra coincidencias auditadas, posibles coincidencias y declaraciones sin
   coincidencia. Un botón permite revisar cada pago faltante antes de confirmarlo.
   Conserva la fecha original del turno y deja vínculo auditado. No vuelve a
   descontar dinero de caja. Tener el mismo importe y un nombre parecido solo
   sugiere coincidencia, no demuestra que sea el mismo pago ni autoriza crear uno.

2. «Pagué diez mil a Aurora en efectivo y veinte mil a Zeta por Nequi».
   Prepara entre **dos y cinco pagos** de hoy, sin registrarlos juntos. Revisa
   cada renglón con su botón y confirma cada pago por separado. Si falta el medio,
   se pregunta por ese renglón. Descartar el lote bloquea sus pagos aún pendientes,
   pero no anula los que ya confirmaste. Los lotes vencen a los diez minutos y no
   se confirman después de cambiar el día. Posibles duplicados se detienen para
   revisión en la web. Si no puede separar todo el mensaje, pregunta; no omite
   pagos para hacer encajar la solicitud.

3. Envía una **foto** del comprobante, no un archivo ni un álbum completo.
   Se revisa un comprobante a la vez; termina o descarta el anterior antes de otro.
   El bot pide autorización por cada imagen antes de descargarla y enviarla al
   servicio Gemini ya configurado. Recorta u oculta primero cuentas, documentos,
   claves u otros datos innecesarios. No se añade otro proveedor de imágenes.
   Después revisa concepto y valor base; elige el medio y la fecha real de pago.
   La fecha impresa no se toma automáticamente como fecha de pago.
   Puedes decir «el concepto es Aurora, el valor base es diez mil, lo pagué ayer
   por Nequi». La IA puede leer mal una imagen: revisa siempre el borrador.
   Si el documento incluye 4 × 1.000, se pide aclarar el valor base para no
   sumarlo dos veces. Solo guarda al pulsar **Confirmar pago revisado**.
   Admite JPEG/PNG/WebP de hasta 5 MB y 16 megapíxeles. No guarda los bytes de la
   imagen en NovaPOS ni los reenvía automáticamente si falla la lectura.
   Los datos revisados y el identificador de Telegram sí forman parte del flujo
   auditado. Para fechas anteriores se requiere permiso de edición de pagos.

4. «Mándame los pagos de esta semana en Excel» o «dame un PDF de los cierres».
   Envía un archivo real XLSX/PDF, solo al chat privado vinculado. Permite pagos,
   ventas, cierres, conciliación e inventario **actual**, según permisos. El rango
   máximo es de 93 días y 1.000 filas; si se supera, pide reducir el filtro y no
   recorta registros silenciosamente. Los pagos no identifican sucursal, por
   eso no se atribuyen a una. Los Excel neutralizan texto que podría ejecutarse
   como fórmula. Un informe no demuestra un saldo bancario ni utilidad neta.

5. «Avísame si una caja lleva más de diez horas abierta», «avísame de los nuevos
   cierres con diferencia en Yerbabuena» o «avísame si el lector PTM lleva quince
   minutos sin reportar». Prepara una alerta y la activa solo con su botón.
   Las diferencias requieren revisión: no atribuyen culpa ni crean cobros.
   La alerta PTM revisa lectores activos, sin inferir sucursal por su nombre.
   Un aviso por incidente; una lectura nueva del lector permite detectar una
   interrupción posterior como otro incidente. Los cierres se observan desde la
   creación de la regla, dentro de la última jornada, sin enviar todo el historial.

6. «Recuérdame mañana a las ocho revisar las facturas», «los lunes a las ocho
   envíame el Excel de los pagos de la semana anterior» o «el último día de cada
   mes a las nueve de la noche envíame el resumen del mes».
   Admite una sola fecha futura, frecuencia diaria, semanal o mensual, en hora
   de Colombia. Para informes pide explícitamente período y formato; texto es
   un resumen de todos los registros, Excel/PDF es detalle descargable.
   El balance se programa por texto; para archivos elige pagos, ventas o cierres.
   Un día 31 no se adelanta al 30/28 en meses cortos; «último día» sí se adapta.
   Las reglas periódicas no recuperan todos los días que el servidor estuvo
   apagado. Una regla puntual pendiente sí puede entregarse después de su hora.
   Consulta «mis programaciones» y pide «pausa la programación número ...» para
   revisar el cambio. No se configura ningún destinatario ajeno ni operación
   contable programada. Límite: 50 reglas activas por cuenta.

## Seguridad y entrega

Cada lectura o confirmación verifica la cuenta vinculada, chat privado, permisos,
estado y vencimiento actuales. La conciliación exige además poder editar pagos.
Un «sí» hablado o escrito no confirma una operación. Usa `/botones` o
`/pendientes` para recuperar propuestas reales; `/cancelar` descarta las pendientes.

Los avisos usan el trabajador existente, con prioridad para los mensajes del chat.
Se revisan cuando la cola lo permite; no tienen garantía de entrega al minuto.
No dupliques el proceso para acelerar los avisos. La reserva de cada entrega se
guarda antes de contactar Telegram, con una clave única en la base de datos.
Si la entrega queda incierta, no se reenvía sola: revisa el chat y su estado.
No se puede prometer que Telegram, la conexión o la IA nunca fallen.

Los seguimientos diarios antiguos siguen disponibles por `/seguimientos` y no
se migran ni se duplican automáticamente en las nuevas reglas flexibles.

## Instalación o despliegue autorizado

Haz copia de seguridad y evita procesar solicitudes durante la actualización.
No borres las claves existentes ni crees otro bot o trabajador.

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py check
```

Recarga la web y reinicia el **mismo** trabajador. El script habitual
`scripts/run_telegram_bot.sh` conserva las opciones de recarga y `--followups`.
Antes de reanudar mensajes, comprueba que la migración 0054 figure aplicada.
Las nuevas bibliotecas son openpyxl, reportlab y Pillow; no requieren una nueva
cuenta de servicio. Las fotos usan la clave y modelo Gemini existentes, con su
disponibilidad y condiciones; no se activa facturación ni se cambian modelos.

Pruebas aisladas, sin credenciales ni pagos reales:

```bash
python -B manage.py test mainApp --pattern='test_telegram*.py' --settings=NovaSoft.test_settings --noinput
python -B manage.py test mainApp.test_expense_reconciliation mainApp.test_expense_reconciliation_registration --settings=NovaSoft.test_settings --noinput
python -B scripts/test_hybrid_postgres_wsl.py --telegram-only
```

El último comando usa un PostgreSQL nuevo y desechable en WSL, no producción.
`scripts/verify_telegram_report_layout.py` genera archivos ficticios bajo
`tmp/pdfs/` para revisar el formato sin consultar la base.

Referencias técnicas: [archivos Telegram](https://core.telegram.org/bots/api#senddocument),
[imágenes en Gemini](https://ai.google.dev/gemini-api/docs/generate-content/image-understanding)
y [salidas estructuradas](https://developers.openai.com/api/docs/guides/structured-outputs).
Los permisos y la ejecución se validan en el servidor, no se delegan al modelo.
