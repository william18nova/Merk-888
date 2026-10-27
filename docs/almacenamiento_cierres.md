# Almacenamiento y respaldo de cierres

El catálogo de Generar venta y del visor autenticado se guarda en IndexedDB,
no junto a carritos y conteos en localStorage. La caché antigua se migra antes
de retirarla. Las nuevas referencias de envío también usan una transacción
IndexedDB: reintentar conserva el mismo identificador y no genera otro cobro.
Las referencias antiguas se conservan para no romper pestañas anteriores.

Al alcanzar la cuota de localStorage, una escritura protegida elimina solamente
claves reconocidas de catálogo/ranking y reintenta. Nunca llama a clear(), ni
elimina carritos, referencias pendientes, avances de cierre o marcas de impresión.
Los carritos con envío incierto no caducan a las 24 horas.

Los pasos confirmados del cierre se respaldan mediante
`turno_caja/cierre/<id>/respaldo/` en `AvanceCierreCaja`. El servidor valida
importes/desglose, bloquea la fila del turno y rechaza retrocesos o cambios de
valores confirmados. No registra egresos ni altera saldos. El cierre final
verifica que coincida con el respaldo. Los turnos antiguos sin respaldo siguen
siendo compatibles.

También se guarda el borrador en IndexedDB y, si hay espacio, localStorage.
Si el servidor no responde pero el navegador confirma un guardado persistente,
el avance puede continuar con aviso de respaldo solo local. Si falla todo, no
avanza y ofrece descargar un JSON del conteo (contiene datos del negocio:
guardarlo de forma privada; no subirlo a repositorios). No hay importación
automática de ese archivo ni ejecución automática de pagos.

## Despliegue

En PythonAnywhere, aplicar `python manage.py migrate --noinput`, ejecutar
`python manage.py check`, `python manage.py collectstatic --noinput` y recargar
la aplicación. La migración 0053 solo crea la nueva tabla, sin transformar datos.
No ejecutar makemigrations en producción.

Para una copia WSL integrada que tenga el esquema inmediatamente anterior:

```powershell
python scripts/update_integrated_close_payments.py --upgrade close-progress
python scripts/update_integrated_close_payments.py --upgrade close-progress --apply
```

La segunda orden exige un respaldo completo verificado y comprueba que todas
las filas existentes se conservan. Si la firma no coincide, no forzarla: esa
instalación necesita revisar primero sus actualizaciones anteriores. Las nuevas
instalaciones crean el modelo con el esquema vigente.

No cerrar/recargar una pestaña con información que todavía no se haya guardado.
Una vez protegido el trabajo, recargar las pestañas para usar los scripts nuevos.
IndexedDB tampoco es ilimitado ni sustituye la base de datos y sus respaldos.

## Pruebas

`node --test mainApp/test_nova_storage_browser.js mainApp/test_cash_close_steps_browser.js`

`python scripts/test_hybrid_postgres_wsl.py --storage-only`

Datos ficticios; no se realizan ventas, cierres ni impresiones reales.
