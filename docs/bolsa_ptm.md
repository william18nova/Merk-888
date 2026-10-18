# Bolsa física PTM compartida por sucursal

Implementación 2026-10-17. No activa bolsas ni establece importes automáticamente.
Yerbabuena es la sucursal indicada por el usuario; falta confirmar su efectivo real inicial.
Una cuenta PTM corresponde exclusivamente a una sucursal y una bolsa física.

## Operación

1. Solo Web Master entra a **Caja → Bolsas de efectivo PTM** (`/ptm/bolsas/`), selecciona sucursal/cuenta e introduce el efectivo contado. No puede sobrescribir una bolsa existente. Primero debe terminar los cierres pendientes. La ruta, el menú y los servicios de creación, ajuste y revisión exigen ese rol; ser administrador o superusuario sin ese rol no basta. Los cambios posteriores se registran con motivo como entradas o salidas, conservando los conteos y faltantes históricos.
2. Cada cajero, con su turno abierto, cuenta y recibe toda la bolsa. Su participación empieza después de ese corte. Los períodos anteriores no se le atribuyen.
3. Al iniciar el cierre, el cajero cuenta y entrega toda la bolsa **antes** de las tres páginas habituales de cierre. Todos deben detener las operaciones PTM durante el conteo.
4. El efectivo PTM no se cuenta junto con los billetes de la caja ni como facturas pagadas. Los registros POS anteriores a la activación mantienen su tratamiento histórico; no se reclasifican saldos existentes.
5. El cierre, el resumen de retiro y su tirilla muestran los faltantes PTM del cajero, su estado y los participantes. Administración ve además los sobrantes, el esperado, el contado y la trazabilidad, tanto en el detalle del dashboard/administración de turnos como en la página de bolsas.

El esperado parte del **último efectivo realmente contado**, más recargas/pagos exitosos, menos retiros exitosos y más/menos ajustes administrativos explícitos. El reporte del lector es la fuente monetaria; registrar también el comprobante en el POS no suma el importe dos veces. Los errores PTM no mueven el esperado y los estados desconocidos requieren revisión.

El número de comprobantes pedido en el cierre sigue correspondiendo a los registros manuales **de ese turno**, no al total de la cuenta compartida.

## Responsabilidad sin duplicación

- A y B reciben; A entrega cuadrado; B entrega con $10.000 menos: la incidencia corresponde a B.
- A y B reciben; A entrega con $10.000 menos; B entrega la misma cantidad: existe **una incidencia** compartida entre A y B, visible en ambos cierres, no dos deudas.
- C recibe el efectivo realmente contado: no hereda aquel faltante.
- Cada entrada o salida cambia el grupo de participantes y abre un nuevo período.
- Un período sin participantes queda sin atribuir y requiere revisión administrativa.

No se reparte automáticamente un importe entre personas, no se descuenta nómina y no se aumenta `TurnoCaja.deuda_total`. No sumar incidencias por empleado: identificar siempre por número de corte. La custodia compartida queda registrada; no demuestra quién hizo cada transacción externa con la cuenta compartida.

## Seguridad y conciliación

- Conteo ciego: las respuestas al cajero no contienen esperado ni sobrantes, tampoco en JSON. Los faltantes se muestran como provisionales hasta revisión.
- Formulario firmado, asociado a cajero, turno y versión de bolsa; vence en diez minutos. Dos conteos simultáneos no sobrescriben el saldo; se exige recontar al segundo.
- Reenviar el mismo formulario con el mismo valor no duplica un corte. Otro valor se rechaza.
- Referencias PTM únicas por cuenta; lectura repetida no duplica movimientos.
- Una lectura tardía modifica el corte de su intervalo original, con revisión y auditoría. No cambia el efectivo contado usado como base del siguiente período.
- La revisión requiere cobertura completa posterior al conteo de **cada fecha** del intervalo. La lectura del día debe ser reciente. Los lotes parciales no cuentan como reporte completo.
- Administración no puede editar ni eliminar turnos con participación PTM mediante los endpoints de edición ordinarios. Solo registra ajustes reales de bolsa con motivo; no debe usar ajustes para ocultar faltantes.
- Mientras una bolsa esté activa, abrir/cerrar turnos de esa sucursal necesita conexión. Las ventas offline existentes no se desactivan; las sucursales sin bolsa mantienen su flujo offline habitual.
- El portal externo no se puede congelar desde NovaPOS. Por ello hay que detener físicamente las operaciones para contar y revisar cualquier lectura tardía; el lector puede fallar o cerrar sesión. No se promete un cuadre definitivo sin conciliación.

## Despliegue controlado (no ejecutado en producción por esta implementación)

La migración `0052_ptm_cash_pool` agrega seis tablas, la relación de participantes y un enlace opcional en `OperacionPTM`. No crea bolsas activas ni altera dinero existente. No ejecutar `makemigrations` indiscriminadamente: este proyecto tiene diferencias históricas de modelos/esquema ajenas a esta función.

1. Conservar un respaldo completo, verificado y reciente de la base online y una copia del código desplegado. Detener operaciones durante la actualización y coordinar el lector/bot y los procesos web.
2. Publicar los archivos coherentes de esta función (modelos, migración, servicios PTM, vistas, rutas/permisos, plantillas/estáticos y adaptadores offline). **No usar el antiguo paquete selectivo del lector 0.1.x**: no incluye esta función; su constructor ahora rechaza empaquetarla parcialmente.
3. Revisar y aplicar solo 0052 después de que 0051 esté aplicada:

   ```text
   python scripts/migrate_ptm_reader_with_backup.py --upgrade ptm-pool --backup-manifest RUTA/manifest.json
   python scripts/migrate_ptm_reader_with_backup.py --upgrade ptm-pool --backup-manifest RUTA/manifest.json --apply
   python manage.py check
   python manage.py collectstatic --noinput
   ```

   Usar el manifiesto generado por el respaldo existente de NovaPOS. El script verifica destino, antigüedad, tamaño y SHA-256; rechaza migraciones adicionales o inversas. Recargar PythonAnywhere después de la migración.

4. En una instalación Windows/WSL integrada existente, con el programa cerrado, actualizar también la copia privada:

   ```text
   python scripts/update_integrated_close_payments.py --upgrade ptm-pool
   python scripts/update_integrated_close_payments.py --upgrade ptm-pool --apply
   ```

   Solo acepta la firma de esquema previa esperada. Respalda y verifica la copia local antes de añadir tablas/campo; verifica conteos de filas y no conecta a Aiven. Si no coincide la firma, detenerse y revisar, no editarla manualmente. Este actualizador específico es para WSL, no para una instalación nativa distinta.

5. Reabrir el POS online para renovar la autorización/copia offline, comprobar el lector y configurar Yerbabuena con su importe real. Hacer un piloto supervisado de recepción/entrega antes de uso general.

## Pruebas reproducibles

`python scripts/test_hybrid_postgres_wsl.py --ptm-only` crea un PostgreSQL nuevo, desechable y aislado: prueba flujo PTM, migración aditiva, conservación de datos, bloqueos de fila y regresiones offline. No usa Aiven ni bases existentes.

Las pruebas de navegador `scripts/test_ptm_cash_pool_ui.cjs`, dashboard, administración y cierre usan datos ficticios, solicitudes simuladas y no imprimen. La prueba visual incluye escritorio y móvil; la prueba de resumen verifica texto seguro y que no quede información del turno anterior.
