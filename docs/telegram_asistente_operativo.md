# Asistente operativo por Telegram

Esta ampliación añade tareas encadenadas, alias personales, referencias a listas,
inventario, pedidos y seguimientos diarios. Conserva los proveedores de IA y las
funciones existentes; no añade claves, servicios externos ni dependencias.

## Qué se puede pedir

| Petición de ejemplo | Resultado y límites |
| --- | --- |
| «Busca AGUA TEST y prepara cambiar su precio a 2500» | Consulta y prepara la edición si hay una coincidencia única. Si hay varias, pide elegir. Requiere Confirmar. |
| «Muéstrame el segundo de la lista» | Usa el ID real de la última lista compatible, de esta misma cuenta y chat. No adivina IDs de la conversación. |
| «Recuerda que aguita es el producto ID 123» | Propone un alias privado; no renombra el producto. Se guarda al confirmar. |
| «Suma 12 al inventario del producto 123 en Yerbabuena, por conteo físico» | Muestra existencias anteriores y propuestas. Solo guarda al confirmar y si el inventario no cambió entretanto. |
| «Fija el inventario del producto 123 en 500 en Yerbabuena, motivo: conteo físico» | Conteo exacto; los productos por peso siguen usando gramos. No equivale a una venta. |
| «Revisa qué falta surtir en Yerbabuena» | Hasta diez productos con stock cero/negativo y hasta tres precios registrados por proveedor. No calcula cantidades de compra ni promete precios actuales externos. |
| «Prepara un pedido al proveedor 8 para Yerbabuena: 12 del producto 123 y 4 del 456» | Usa precios registrados, muestra productos, cantidades y total. Solo crea un pedido **En espera**, tras confirmar. No recibe mercancía, mueve inventario ni registra pagos. |
| «Analiza el balance de esta semana» | Vendido menos pagado, comparación con intervalo anterior de igual duración y tres mayores conceptos de pago. No inventa causas, utilidad ni saldo de bancos. |
| «Envíame el resumen diario a las 21:00» | Propone un informe de vendido/pagado/balance, privado y diario a hora de Colombia. |
| «Avísame de diferencias de caja todos los días a las 21:00» | Informa cierres del día con diferencias, sin acusar ni inferir fraude. |
| «Avísame del inventario agotado todos los días a las 09:00» | Informe diario de registros con stock cero o negativo; silencio si no hay resultados. |
| «Pausa mi seguimiento de inventario a las 09:00» | Propone pausar esa regla. No afecta el chat ni otras reglas. |

Son ejemplos de peticiones, no pruebas de interpretación contra una IA real. La
interpretación conserva sus validaciones: puede pedir aclaración ante nombres o
cantidades ambiguas, y los proveedores aún tienen sus cuotas/disponibilidad.

Comandos sin IA de texto: `/analisis`, `/alias`, `/seguimientos`, además de los
anteriores (`/ventas`, `/pagos`, `/producto`, `/pendientes`, etc.). `/seguimientos`
muestra también la última revisión/envío; `/botones` recupera propuestas nuevas.
Los audios continúan necesitando transcripción.

## Permisos y confirmación

- Inventario exige los permisos de editar y visualizar inventarios.
- Pedidos exige crear pedidos y visualizar precios de proveedor.
- Alias exige consultar la entidad correspondiente y se limita al usuario
  vinculado. Nunca se resuelven fuera del conjunto de registros autorizado.
- Resumen diario exige métricas; diferencias de caja exige dashboard de turnos;
  inventario agotado exige visualizar inventarios.
- Se revisan permisos al proponer, al confirmar y al emitir cada informe.
- Toda modificación necesita su botón **Confirmar**, con vencimiento de diez
  minutos. Un texto/audio «sí» no confirma. Repetir el botón no repite la acción.
- Cambios de existencias o de precios de proveedor utilizados en una propuesta
  invalidan esa propuesta; se solicita una nueva. Un precio escrito explícitamente
  por el usuario se conserva y aparece en el detalle a confirmar.
- Auditoría registra usuario, operación, motivo y argumentos. No hay SQL,
  comandos del servidor ni código inventado por la IA.

Los servicios compartidos `business_operations.py` se usan tanto en la web
(inventario de un producto y creación de pedido) como en Telegram. Se validan
cantidades, productos/precios del proveedor, límites y transacciones atómicas.

## Conversación y tareas dependientes

- Hasta doce intercambios recientes dentro de 24 horas y posteriores a la
  vinculación de la cuenta. El contexto enviado conserva un máximo de 6000
  caracteres; no se descarga toda la base a la IA.
- Los IDs y el orden de listas se guardan como metadatos de consultas reales:
  productos, empleados, pagos y catálogos. No se extraen de texto inventado.
  No todas las respuestas son listas seleccionables (por ejemplo agregados).
- `resolver_tarea` admite hasta cinco pasos, lecturas y **una sola preparación
  al final**. No permite planes anidados, SQL, confirmaciones automáticas ni varias
  escrituras. No añade nuevas llamadas a IA para ejecutar cada paso.
- Las referencias de un paso solo pueden rellenar determinados campos de ID
  desde un resultado único de la entidad correcta; nunca importes o cantidades.
  Las coincidencias múltiples interrumpen el plan antes de crear la propuesta.
- No es un agente ilimitado de planificación: el plan se interpreta al inicio.
  Si los resultados exigen decidir cantidades, otro proveedor o una operación
  distinta, pide una aclaración; no inventa esa decisión.

## Avisos diarios: comportamiento real

Las reglas son voluntarias y requieren confirmación individual. Solo se admite
una regla de cada tipo por cuenta y frecuencia **diaria**; no semanal o mensual.
El resumen y las diferencias usan lo registrado **hasta la hora del informe**,
no los movimientos posteriores de ese día. El inventario describe el estado
actual y cuenta registros producto/sucursal, no productos únicos.

El trabajador con `--followups` revisa reglas cada minuto y procesa hasta cinco
por revisión. Puede retrasarse si está atendiendo audios o solicitudes. Si estuvo
apagado, al volver ejecuta los informes vencidos del **día actual**, no recupera
los de días anteriores.

Antes de enviar reserva `(seguimiento, fecha)` con una restricción única en BD:
dos trabajadores no deben enviar el mismo informe del día. Si hay un fallo o se
pierde la respuesta de Telegram, **no lo reenvía automáticamente** para evitar
duplicados; el estado queda `RESERVADO` o `ERROR_INCIERTO`. No se promete entrega
exactamente una vez. Un informe sin resultados o sin permiso queda `OMITIDO`.
No se guardan excepciones de transporte que puedan contener tokens.

Las alertas de stock pueden volver a informar de los mismos agotados al día
siguiente. Son informes diarios de situación, no detectores de cambios en tiempo
real ni un sistema de conciliación financiera.

## Despliegue (pendiente de hacerlo en el servidor)

Incluye la migración **0042_telegram_assistant_workspace**, que crea tablas de
alias, reglas y control de envíos. No modifica ventas, productos ni saldos.
No ejecutar el nuevo trabajador antes de aplicar la migración.

Después de subir estos cambios a GitHub:

1. Detener temporalmente la tarea Always-on existente del bot.
2. En la consola de PythonAnywhere:

   ```bash
   cd /home/Merk888/Merk-888
   source /home/Merk888/.virtualenvs/env/bin/activate
   git pull --ff-only origin main
   python manage.py check
   python manage.py migrate --noinput
   ```

3. Recargar la aplicación en Web y reiniciar **la misma** tarea Always-on:

   ```bash
   /bin/bash /home/Merk888/Merk-888/scripts/run_telegram_bot.sh
   ```

   El script incorpora `--followups`. No crea reglas ni envía nada sin que alguien
   lo solicite y confirme. En el archivo privado de entorno se puede poner
   `TELEGRAM_FOLLOWUPS_ENABLED=0` para desactivar los informes sin detener el chat.
   Si el trabajador se ejecuta con un comando propio, añadir `--followups` para
   habilitar los informes.

4. Probar `/acciones`, `/alias` y `/seguimientos`. Los comandos escritos funcionan
   sin reconfigurar el webhook. Para mostrarlos también en el menú de comandos de
   Telegram, volver a configurar el webhook desde la página de configuración.

No se cambian ni se publican credenciales. No se aplicaron migraciones a la base
real ni se desplegó a PythonAnywhere durante esta implementación.

## Cobertura y límites pendientes

Se mantienen las consultas existentes de ventas, pagos, empleados, horarios,
catálogos, PTM, Nequi, métricas, etc., y sus modificaciones ya implementadas.
Esta entrega añade inventario, **creación** de pedidos, alias y avisos.

Siguen en la web: facturar una venta completa, recibir/devolver pedidos, abrir o
cerrar caja, registrar PTM, modificar permisos/contraseñas/configuración sensible,
eliminar registros y transferir dinero. El bot puede buscar la vista autorizada,
pero no afirma haber ejecutado estas operaciones. Ampliarlas requiere adaptadores
del dominio y pruebas específicas, no acceso libre a SQL.

Pruebas locales sin base real ni API:

```bash
python manage.py test mainApp --pattern='test_telegram*.py' --settings=NovaSoft.test_settings
```

La suite comprueba propuestas/confirmación idempotente, permisos revocados,
conflictos de inventario/precios, alias privados, referencias de listas, rechazo
de planes peligrosos, informes sin duplicados y el estado de la migración nueva.

La revisión general también detectó tres pruebas antiguas que no corresponden a
esta ampliación: `RoutePermissionAndNavigationCoverageTests` supone que todas
las vistas del navbar son clases (los horarios usan funciones), y dos pruebas
de Generar Venta todavía exigen versiones antiguas exactas de CSS/JavaScript.
No se cambiaron esos archivos ni se debilitaron esas pruebas para ocultar los
fallos. La suite específica de Telegram pasa completa.
