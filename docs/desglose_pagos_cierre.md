# Desglose de pagos al cerrar caja

En el primer paso del cierre se indica el total y se agrega una fila por cada
pago, con nombre y valor. Los nombres se guardan en mayúsculas. Si no hubo
pagos, se deja el total en 0 y el detalle vacío.

La suma debe coincidir exactamente con el total inicial. Se valida al avanzar
en la pantalla y nuevamente en el servidor antes de guardar. Los importes se
comparan con precisión de centavos, sin tolerancias ni redondeos silenciosos.
Cada fila debe tener un nombre (hasta 160 caracteres) y un valor positivo;
se permiten hasta 100 filas por cierre.

El total y su detalle se conservan durante los tres pasos y al recargar la
página. Después de confirmar el primer paso no se pueden cambiar desde el
flujo normal del cajero. El detalle se guarda junto con el cierre y el usuario
que lo registró, en la misma transacción de base de datos.

En **Dashboard → Turnos de caja → Ver detalle** aparece la sección
**Facturas y pagos a compañeros**, con el total, los nombres, sus valores y el
usuario. También aparece en la administración del turno.

## Contabilidad y compatibilidad

- Es un desglose informativo del total de facturas ya usado por el cuadre.
  No crea egresos adicionales ni descuenta el dinero otra vez.
- Los turnos antiguos conservan su total y muestran que no tienen desglose.
  No se inventan nombres históricos.
- Los cierres offline nuevos conservan los nombres y valores en el diario
  local y los envían con el cierre. La sincronización es idempotente.
- Se mantiene compatibilidad con los cierres firmados que ya estaban
  pendientes de sincronizar antes de esta versión y no tenían desglose.
  Esta excepción es interna: una petición HTTP nueva no puede activarla.
- Un cierre que estaba a mitad del proceso en el navegador antes de actualizar
  y tenía un total positivo sin detalle necesitará completar los nombres en
  el primer paso; se conservan los conteos guardados.

## Actualización

La migración `0050_desglosepagoscierre` crea únicamente la tabla del desglose;
depende de `0049_solicitudpos`. No modifica las ventas ni rellena turnos antiguos.
No es necesario ejecutar `makemigrations`.

Con el entorno virtual de la instalación activo y el código actualizado:

```bash
python manage.py check
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

En PythonAnywhere, mantener `NovaSoft.settings` en el WSGI y usar **Reload**
después de esos comandos. En el equipo local, reiniciar el proceso normal de
`runserver`. Las instalaciones integradas sirven los archivos estáticos locales.
Cerrar o recargar las pestañas de cierre antiguas para cargar el nuevo JavaScript.

### Copia integrada existente en WSL

Si `runserver` indica que cambió el esquema, detener los procesos locales del
POS y ejecutar en este equipo, **no en PythonAnywhere**:

```powershell
python scripts/update_integrated_close_payments.py
python scripts/update_integrated_close_payments.py --apply
python manage.py runserver
```

El primer comando solo revisa la huella. El segundo admite únicamente el cambio
que añade DesglosePagosCierre, toma el bloqueo de la instalación, crea un respaldo
completo con `pg_dump` en WSL y verifica su lectura antes de crear la tabla local.
Conserva la configuración anterior y verifica los conteos de todas las tablas.
Solo publica la nueva huella después de completar la operación. No usa la DLL
nativa, no accede a Aiven y no reejecuta las migraciones históricas sobre la copia.
Si se detectan otros cambios de esquema, se detiene para revisión.

No se aplicaron migraciones a Aiven ni se desplegó código en producción durante
estas pruebas. La actualización requiere aplicar esta migración antes de usar
el cierre o consultar sus dashboards con el nuevo código.

## Verificación realizada (12 de octubre de 2026)

- PostgreSQL aislado en WSL, con aplicación Windows: 194 pruebas integradas
  y 84 de servicios web, todas aprobadas.
- Aplicación Linux y PostgreSQL aislado: 33 pruebas aprobadas, incluyendo
  migración, permisos, consulta de detalle y reversión completa ante un fallo
  al escribir el desglose.
- JavaScript: 11 pruebas aprobadas de importes y transporte de operaciones.
- Comprobación de arranque sin acceso SQL: 85 plantillas compiladas,
  `collectstatic` y 7 recursos representativos con HTTP 200 y `DEBUG=False`.
- Navegador: $20.000 de detalle frente a $30.000 de total bloqueó el avance;
  al agregar los $10.000 faltantes se avanzó por las tres páginas. La recarga
  conservó el conteo y el envío final incluyó ambos nombres y los $30.000.
  Este ensayo usó un servidor ficticio sin conexiones a bases de datos.

Los errores deliberados que aparecen en la salida de algunos tests corresponden
a fallos simulados para verificar el rechazo y la reversión; las suites acabaron
en `OK`. Permanecen las advertencias históricas de esquema y seguridad descritas
en `verificacion_pythonanywhere_2026-10-12.md`; esto no certifica el estado de la
base real ni reemplaza la comprobación después del despliegue.
