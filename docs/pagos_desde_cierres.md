# Registrar pagos desde la revisión de cierres

Ruta: **Caja → Registrar pago → Revisar pagos de todos los cierres**.

1. Selecciona las fechas, desde el 1 de noviembre de 2026. Se incluyen todos los
   turnos cerrados, a cualquier hora. La fecha es la del inicio del turno, hora
   de Colombia, aunque haya terminado al día siguiente.
2. En una declaración **Sin coincidencia**, pulsa **Registrar este pago**.
3. Revisa concepto, fecha, valor base, 4 × 1.000 y total.
4. Marca que comprobaste los pagos existentes y que este pago falta por registrar.
5. Pulsa **Confirmar y registrar pago**.

El registro aparece a la derecha en **Pagos ya agregados** y el detalle cambia a
**Conciliado con auditoría**. Se conserva la fecha del turno y queda registrado
quién lo agregó y de qué renglón del cierre proviene. No se vuelve a entregar
dinero, no se modifica el cierre y no se afecta el saldo de caja.

Estos desgloses son pagos **en efectivo**: concepto, importe, fecha y medio se
toman de la declaración cerrada y no se pueden cambiar en este formulario. Si
el desglose es incorrecto, debe revisarse antes; no hay que registrar otro medio
para compensarlo. El impuesto se calcula con la configuración vigente de
Efectivo y se muestra antes de confirmar.

## Protecciones

- Requiere cuenta activa Web Master o permiso `pagos_editar`, igual que la página.
- Solo online, para turnos cerrados de cualquier horario y con un desglose válido.
- Una coincidencia posible o un dato que requiere revisión no habilita otro
  registro: primero revisa el pago existente. No hay vinculación automática por
  nombres parecidos, ni un botón para forzar un duplicado.
- La referencia está firmada, vinculada al usuario y vence a los 30 minutos. Si
  cambia el cierre o el impuesto, hay que recargar y revisar otra vez.
- Los clics repetidos o simultáneos reutilizan el vínculo auditado. Pago, fecha y
  auditoría se guardan juntos en una transacción; no se crean egresos parciales.
- No se usan `localStorage`, colas offline ni llamadas a PTM para este registro.

## Verificación y activación

No requiere migraciones nuevas. El cambio debe publicarse en el servidor para
que aparezca en PythonAnywhere; las pruebas y la captura de demostración son
locales y usan datos ficticios.

Pruebas de la revisión y el registro con SQLite aislado:

```powershell
python -B manage.py test mainApp.test_expense_reconciliation mainApp.test_expense_reconciliation_registration --settings=NovaSoft.test_settings --noinput
```

Regresiones completas y confirmaciones simultáneas en PostgreSQL desechable de WSL:

```powershell
python -B scripts/test_hybrid_postgres_wsl.py --reconciliation-only
```
