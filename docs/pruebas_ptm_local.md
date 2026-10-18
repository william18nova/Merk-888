# Prueba local de bolsa PTM

En este computador está activado el archivo privado `.novapos-ptm-lab` (ignorado
por Git). Ejecuta `python manage.py runserver` desde la carpeta del proyecto y
abre http://127.0.0.1:8000/ptm/bolsas/. Usa la misma base ficticia de este laboratorio.
No hay sincronización con Aiven en este modo. Para restaurar el arranque habitual,
detén el servidor y renombra `.novapos-ptm-lab` a `.novapos-ptm-lab.disabled`.
No se cambia WSGI ni el comportamiento de migraciones. No copies este marcador
a producción. La recarga automática está desactivada: reinicia tras editar código.

Ejecutar `Probar-PTM-local.cmd` desde la carpeta del proyecto y mantener la consola abierta.
Entrar a http://127.0.0.1:8942/ptm/bolsas/.

- Web Master: `WM Pruebas PTM`.
- Cajeros: `Cajero PTM A`, `Cajero PTM B`, `Cajero PTM C`.
- Contraseña de todos los usuarios ficticios: `PruebasPTM-2026!`.

Crear la bolsa con sucursal `Yerbabuena PRUEBAS (ficticia)` y cuenta ficticia.
Puede usarse $100.000 de base de prueba. Luego cada cajero entra a `/turno_caja/`
en perfiles de navegador separados, recibe la bolsa con conteo y realiza su cierre.
Probar entrega de A por $100.000 y luego de B por $90.000; revisar el faltante
desde Web Master. No usar efectivo ni usuarios reales.

Los datos persisten en `build/ptm_lab/pruebas.sqlite3`. El arranque no borra ni
reinicia los conteos existentes. No conectar la extensión PTM real a este laboratorio.
Las conexiones salientes del proceso están bloqueadas: no envía datos a Aiven,
PythonAnywhere, Telegram ni PTM. No modifica la configuración del arranque habitual.

Este entorno SQLite sirve para pruebas manuales de pantalla y flujo. No sustituye
las pruebas de migraciones y concurrencia PostgreSQL. El lector está desconectado;
por ello los cortes pueden quedar provisionales por falta de un reporte completo.
No marcar diferencias de este entorno como deudas reales ni probar impresión.
