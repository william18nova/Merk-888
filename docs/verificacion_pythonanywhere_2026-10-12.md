# Verificación antes de subir a PythonAnywhere — 12/10/2026

## Conclusión y alcance

Las pruebas de arranque y operación online pasaron con los cambios actuales.
La configuración `NovaSoft.settings` continúa usando únicamente la base remota;
no instala la app `local_pos`, no agrega una base offline y no inicia WSL.
El WSGI y los requisitos de producción no tienen modificaciones respecto a HEAD.

Esto no es una garantía de disponibilidad del servidor real: no se accedió a
PythonAnywhere ni a Aiven, no se comprobaron sus variables privadas y no se
realizó ningún despliegue. Se utilizaron bases ficticias nuevas, separadas de
las bases existentes, y se detuvieron sus motores al finalizar.

## Pruebas ejecutadas

- `manage.py check --settings=NovaSoft.settings`: sin errores.
- `scripts/test_hybrid_postgres_wsl.py`: 192 pruebas integradas y 73 pruebas
  compartidas/online aprobadas (265 en total).
- `scripts/test_hybrid_postgres_wsl.py --production-only --linux-python
  /home/novapos-test/hybrid-venv/bin/python`: 22 pruebas aprobadas en Linux,
  Python 3.10.12, Django 5.1.1 y PostgreSQL 16.
  Incluyen páginas online, autocompletado de clientes, validación de código
  especial sin reserva offline, registro de egreso y el ciclo abrir turno →
  vender → devolver → cerrar. También incluyen idempotencia y la ejecución
  aislada de la migración 0049.
- `scripts/check_pythonanywhere_compatibility.py`: aprobado en Windows y Linux
  con `DEBUG=False`. Las conexiones SQL se bloquean expresamente en esta prueba.
  Comprueba WSGI, comandos de administración sin arranque local, 85 plantillas,
  `collectstatic` en una carpeta temporal y HTTP 200 para cinco recursos
  representativos: navbar, logo, JavaScript de venta, caja y transporte integrado.
- 20 pruebas JavaScript aprobadas; sintaxis de `generar_venta.js` y
  `turno_caja.js` validada.

En la preparación de las pruebas online hubo fallos del escenario ficticio:
faltaba habilitar turnos, abrir uno para ver Generar Venta y dar permisos al
administrador de prueba. Se corrigió únicamente esa preparación, no los
permisos ni las reglas del programa. El primer intento de recolectar estáticos
en Windows fue bloqueado por los permisos del sandbox; la misma prueba pasó
con autorización y también en Linux.

## Requisitos obligatorios del despliegue

1. Tener respaldo de la base y conservar la versión anterior del código.
2. Incluir en el commit los archivos fuente nuevos, no solo los modificados.
   Entre los imprescindibles están `local_pos/integrated_bootstrap.py` y sus
   dependencias, `mainApp/services/pos_request_receipts.py` y la migración
   `mainApp/migrations/0049_solicitudpos.py`. Un `commit -am` no agrega archivos
   nuevos. No incluir instalaciones, bases de prueba, `.env` ni datos privados.
3. Mantener `DJANGO_SETTINGS_MODULE=NovaSoft.settings` en el WSGI de PythonAnywhere.
   No utilizar allí `integrated_settings` ni `wsl_development_settings`.
4. Con el virtualenv habitual activo, aplicar los comandos siguientes después
   de actualizar el código. Detenerse ante cualquier error; no usar `--fake`.

   ```bash
   python manage.py check --settings=NovaSoft.settings
   python manage.py migrate --plan --settings=NovaSoft.settings
   python manage.py migrate --noinput --settings=NovaSoft.settings
   python manage.py collectstatic --noinput --settings=NovaSoft.settings
   ```

5. Usar **Reload** en la pestaña Web después de migrar y recopilar estáticos.
   Comprobar inicio de sesión, Inicio, Generar Venta, Caja y registro de pagos,
   y revisar el error log. No registrar ventas ficticias en la base real.

La 0049 crea `solicitudes_pos`; no elimina tablas ni transforma ventas
existentes. Es necesaria también para la consulta de reservas de códigos
especiales. Omitirla puede provocar un error al validar un descuento especial.

## Advertencias pendientes, anteriores a estos cambios

El detector encuentra diferencias antiguas entre el estado de migraciones y
los modelos: campos de Producto, CambioDevolucion y TurnoCajaMedio, entre otras.
La revisión inicial incorporaba SolicitudPOS, correctamente representado por
0049. La ampliación del cierre agrega DesglosePagosCierre, representado por
0050; también debe aplicarse al actualizar. Las pruebas de negocio crean el esquema ficticio desde
los modelos; no certifican una instalación limpia de toda la cadena histórica.

**No ejecutar `makemigrations` a ciegas en producción.** Una migración automática
intentaría crear objetos que podrían existir ya en la base actual. Conciliar
ese historial requiere una auditoría de esquema aparte y de solo lectura antes
de decidir cualquier cambio. El estado exacto de Aiven no se verificó aquí.

`check --deploy` muestra cinco advertencias ya presentes en la configuración
normal: HSTS, redirección HTTPS, fortaleza de SECRET_KEY y cookies seguras de
sesión/CSRF. No son errores nuevos de arranque. Deben revisarse con la
configuración HTTPS real de PythonAnywhere; no se cambiaron automáticamente
ni se muestran secretos en este informe.

No se probaron impresoras físicas ni llamadas a Telegram/proveedores externos.
