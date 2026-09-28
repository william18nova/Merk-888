# 4 × 1.000 en pagos registrados

- Solo se aplica a los egresos de **Registrar pagos**, también desde Telegram. No cobra nada a clientes ni cambia ventas, turnos o saldos de caja.
- En **Seguridad → Métodos de pago** (Configuración Web Master), la casilla **Sumar 4 × 1.000 a los pagos registrados** se puede activar para cualquier medio. Se guarda con la contraseña, versión y autoría habituales del catálogo.
- La migración `0043_expense_four_per_thousand` activa inicialmente Nequi y Tarjeta / Banco Caja Social. Los demás medios y los medios nuevos empiezan sin el impuesto, salvo que se active al crearlos.
- El importe digitado es el valor base. El servidor suma `base × 0.004`, redondeado a dos decimales (ROUND_HALF_UP). Ejemplo: $100.000 + $400 = $100.400.
- `Egreso.monto` conserva el total de salida para que todos los informes existentes incluyan el costo una sola vez. `impuesto_4xmil` guarda el impuesto incluido y `aplica_4xmil` conserva la regla original. `monto_base` es total menos impuesto.
- No se modifican los importes de pagos históricos. Cambiar el interruptor afecta a pagos nuevos. Al editar y conservar el medio se mantiene la regla original; al cambiar de medio se usa la configuración del nuevo. Editar nunca calcula impuesto sobre un total que ya lo incluye.
- Tanto la web como Telegram muestran el desglose antes de guardar. Si cambia la configuración desde la vista previa/confirmación, el servidor rechaza esa confirmación y pide revisar nuevamente. Los cálculos del navegador no son confiables para persistir importes; el servidor recalcula.
- En métricas: total pagado, balance por medio y dinero restante usan el total con impuesto. El detalle y el resumen muestran también el impuesto incluido.
- La opción es una regla de registro interno configurada por el negocio; no detecta ni verifica cargos, exenciones ni movimientos de una entidad bancaria.

## Despliegue

Después de publicar y descargar este código, con el entorno virtual activado:

```bash
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Aplicar la migración antes de recargar la aplicación y reiniciar el proceso del bot. No basta con subir los archivos estáticos. No ejecutar `makemigrations` en producción para este cambio: la migración está incluida.

## Pruebas sin Aiven

```bash
python manage.py test mainApp.test_expense_tax mainApp.test_operational_expenses mainApp.test_expense_editing mainApp.test_payment_methods mainApp.test_telegram_expense_concepts mainApp.test_telegram_expanded_actions mainApp.test_telegram_smart_queries --settings=NovaSoft.test_settings --noinput
node --test mainApp/test_expense_amount_format.js
```

Para las pruebas de navegador, definir `POS_TEST_ARTIFACT_DIR` a una carpeta temporal existente, ejecutar las pruebas Django anteriores para renderizar fixtures ficticios y luego `node --test scripts/test_expense_tax_ui.cjs` con Playwright disponible. Todo el tráfico se restringe al servidor simulado local.
