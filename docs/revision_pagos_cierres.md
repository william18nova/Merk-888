# Revisión de pagos declarados en cierres

Desde **Registrar pago → Revisar pagos de cierres de tarde**. Disponible para
Web Master o permiso `pagos_editar`, también verificado en la vista del servidor.
Ruta: `/caja/pagos/revisar-cierres/`.

- Solo lectura: no tiene POST ni crea, edita o elimina pagos.
- Rango predeterminado: últimos 31 días, máximo 93 por consulta.
- Tarde: inicio del turno entre 12:00 y 19:59, hora de Colombia, cerrado.
- Compara nombre normalizado e importe base antes del 4 × 1.000, fecha ±1 día.
- La equivalencia Diego/Fabián fue aclarada expresamente por el propietario.
- Solo confirma automáticamente un vínculo de auditoría `CONCILIACION ... CIERRE
  turno/fila`, validando importe, fecha, medio y nombre actuales. Admite dos pagos
  auditados que suman una sola declaración. Las coincidencias sin vínculo siempre
  son sugerencias, no conciliaciones confirmadas.
- No reutiliza pagos auditados en otros cierres. Si varios desgloses comparten un
  candidato muestra «Revisar» (incluye desgloses de mañana para detectar colisiones).
- Pagos no contiene sucursal: se muestra la del turno, pero debe verificarse el
  comprobante antes de decidir. No se equipara un importe por sí solo.
- «Sin coincidencia» no acredita que falte un pago: puede haberse registrado en
  otra fecha, con otro importe, agrupado o con nombre diferente.
- Sin conexión muestra aviso; no presenta la copia local como reporte actualizado.

No requiere migraciones. Tests sin acceso a base:
`python manage.py test mainApp.test_expense_reconciliation`.
