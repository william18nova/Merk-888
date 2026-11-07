# Revisión de pagos declarados en cierres

Desde **Registrar pago → Revisar pagos de todos los cierres**. Disponible para
Web Master o permiso `pagos_editar`, también verificado en la vista del servidor.
Ruta: `/caja/pagos/revisar-cierres/`.

- Consultar no modifica datos. Permite registrar una declaración faltante solo
  con revisión y confirmación explícita; ver `pagos_desde_cierres.md`.
- En la misma página se muestran **todos los pagos ya registrados** dentro del
  rango elegido (todos los medios y usuarios), aunque no tengan coincidencia con
  una declaración. Concepto, fecha, usuario, medio, importe base y total/impuesto.
  El filtro de estado afecta solo a las declaraciones, no al registro de pagos.
  Incluye búsqueda local por concepto, ID, usuario o valor y acceso al pago en
  otra pestaña. La relación con un cierre es informativa, nunca un vínculo nuevo.
- Inicio fijo de esta revisión: **01/11/2026**, solicitado por el propietario.
  No se muestran declaraciones ni coincidencias anteriores a esa fecha. Se
  conservan todos los registros históricos, sin modificarlos ni eliminarlos.
- Rango predeterminado: últimos 31 días sin retroceder antes del inicio fijo,
  máximo 93 por consulta. El servidor rechaza filtros anteriores al 01/11/2026.
- Incluye todos los turnos cerrados con detalle, a cualquier hora: mañana,
  tarde, noche o madrugada. Muestra fecha y hora de inicio, hora de Colombia.
  La fecha del reporte es la del inicio, aunque el turno termine al día siguiente.
- Las declaraciones aparecen primero por el turno más reciente (fecha y hora de
  inicio descendentes; en empate, ID de turno descendente), también al filtrar
  por estado. Dentro de un mismo cierre se conserva el orden del desglose y sus
  referencias de auditoría. Los pagos registrados mantienen su orden de fecha
  e ID descendentes, sin tener que bajar por los registros antiguos.
- Compara nombre normalizado e importe base antes del 4 × 1.000, fecha ±1 día.
- La equivalencia Diego/Fabián fue aclarada expresamente por el propietario.
- Solo confirma automáticamente un vínculo de auditoría `CONCILIACION ... CIERRE
  turno/fila`, validando importe, fecha, medio y nombre actuales. Admite dos pagos
  auditados que suman una sola declaración. Las coincidencias sin vínculo siempre
  son sugerencias, no conciliaciones confirmadas.
- No reutiliza pagos auditados en otros cierres. Si varios desgloses comparten un
  candidato muestra «Revisar», sin importar el horario de cada turno.
- Pagos no contiene sucursal: se muestra la del turno, pero debe verificarse el
  comprobante antes de decidir. No se equipara un importe por sí solo.
- «Sin coincidencia» no acredita que falte un pago: puede haberse registrado en
  otra fecha, con otro importe, agrupado o con nombre diferente.
- Sin conexión muestra aviso; no presenta la copia local como reporte actualizado.

No requiere migraciones. Pruebas con base SQLite aislada:
`python manage.py test mainApp.test_expense_reconciliation mainApp.test_expense_reconciliation_registration --settings=NovaSoft.test_settings --noinput`.
