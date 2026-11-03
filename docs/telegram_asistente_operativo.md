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

El trabajador con `--followups` da prioridad a los mensajes. Cuando la cola no
tiene mensajes disponibles revisa reglas cada diez segundos y procesa un informe
por revisión, volviendo después a comprobar el chat. Puede retrasarse si está
atendiendo audios o solicitudes; con una cola ocupada continuamente, los informes
esperan a que quede libre. Si estuvo
apagado, al volver ejecuta los informes vencidos del **día actual**, no recupera
los de días anteriores.

Antes de enviar reserva `(seguimiento, fecha)` con una restricción única en BD:
dos trabajadores no deben enviar el mismo informe del día. Si hay un fallo o se
pierde la respuesta de Telegram, **no lo reenvía automáticamente** para evitar
duplicados; el estado queda `RESERVADO` o `ERROR_INCIERTO`. No se promete entrega
exactamente una vez. Un informe sin resultados o sin permiso queda `OMITIDO`.
No se guardan excepciones de transporte que puedan contener tokens.

## Rendimiento del trabajador

- Consulta la cola cada 0,5 segundos cuando está libre (antes, cada 2 segundos).
  `--sleep` permite cambiarlo. Se conserva el orden dentro de cada conversación.
- Reutiliza durante hasta 60 segundos la conexión de BD, con comprobación de
  salud y cierre de conexiones obsoletas. Solo se aplica al comando del bot;
  no modifica las conexiones de la web ni conserva resultados o permisos en caché.
- El presupuesto de interpretación de texto sigue siendo 35 segundos y cinco
  intentos como máximo, pero ahora también rige la primera ronda de respaldos.
  La conexión HTTP espera hasta 3 segundos y la lectura hasta 12, limitadas al
  presupuesto restante. La transcripción usa un presupuesto de 60 segundos y
  hasta 20 de lectura por petición. No se recortan los audios ni las instrucciones.
- Estos presupuestos limitan intentos y esperas HTTP; no garantizan un tiempo
  absoluto de entrega: DNS, lecturas parciales, BD, descargas y Telegram también
  consumen tiempo. Las caídas/cuotas de proveedores aún pueden impedir responder.
- `mainApp.services.telegram_timing` registra espera en cola, descarga,
  transcripción, petición IA, preparación y envío. Incluye ID de actualización,
  etapa, proveedor, duración y resultado; no texto, audio, importes, claves ni
  identificadores de chat. Un HTTP terminado no significa que la respuesta de
  IA sea válida: el validador mantiene su propio registro de fallos.

No se añaden trabajadores paralelos, proveedores, suscripciones ni llamadas de
IA extra por una respuesta correcta. Los botones, permisos y la idempotencia de
pagos se conservan. No hay migración para estos cambios. Tras publicarlos, el
trabajador con `--reload` los carga entre mensajes; nunca interrumpir una
confirmación en curso ni reenviar pagos fallidos para probar velocidad.

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

## Audios y claves especiales Merk2 (noviembre de 2026)

Los fallos de audio revisados incluían transcripciones completas: el problema
posterior era la interpretación por IA (conexión, HTTP 503, 413 o 429), y en otro
caso se entendió un medio inexistente en vez de Nequi. Para reducirlo:

- Se acotan los intentos de interpretación a un presupuesto compartido de 35 s;
  cada conexión/lectura también se limita. Las esperas no son un SLA absoluto:
  DNS y lecturas parciales dependen de la red.
- Los mensajes tienen prioridad sobre informes programados. La conexión de BD
  del trabajador se reutiliza de forma controlada y con comprobación de salud.
- Whisper recibe vocabulario público (Nequi, Banco Caja Social, Merk2, etc.),
  usando el [prompt de vocabulario documentado por Groq](https://console.groq.com/docs/speech-to-text).
  No se le agrega historial, claves ni datos privados como contexto.
- Una solicitud completa como `Pago de 90.000 por Nequi para vitaminas y comida`
  puede preparar la propuesta sin IA de texto. Solo admite formatos numéricos
  inequívocos y medios conocidos; no calcula el impuesto ni confirma el pago.
- Si el medio transcrito es desconocido, se pregunta cuál fue, conservando la
  conversación. No se traduce una sigla dudosa a Nequi ni se guarda un pago.
- Se conservan las rutas de respaldo y recuperación de formato/413 existentes.
  Nunca se reejecutan automáticamente mensajes antiguos que quedaron en ERROR.

### Solicitar una clave

1. Usar un chat **privado**, vinculado a una cuenta activa **Web Master**.
2. Escribir `/clave_merk2` (también `/clave_merk2888`), o dictar
   `Genera una clave de Merk dos`. También reconoce peticiones cortas por voz,
   como `Claves Merk dos`, `La clave de Merk2`, `Me regalas una clave de Merc dos`
   o `¿Puedes generarme un código para Merck dos?`. Las grafías Merk/Merc/Merck
   se refieren aquí al mismo cliente especial y se muestran como **Merk2
   (merk2888)** en la propuesta. No se generan claves de otras cuentas.
3. Revisar la propuesta y pulsar **Generar clave**. Decir «sí» o «confirmar»
   únicamente vuelve a mostrar los botones; no genera la clave.
4. El bot utiliza el mismo servicio web de autorización especial del cliente
   `merk2888`: código de 8 dígitos, un solo uso, 15 minutos de vigencia y las
   mismas restricciones y bloqueos del dominio. No es una contraseña de acceso.

Las peticiones de voz completas reconocidas preparan los botones sin depender
de la IA de texto después de transcribir el audio. Incluso si se pide «claves»
en plural, se propone **una sola clave**, nunca un lote. No se acepta «genera
dos claves» como una solicitud directa ni se puede confirmar con un audio.

Los permisos se vuelven a comprobar al confirmar. Una clave vigente no se
reemplaza ni revoca automáticamente. Una confirmación repetida no crea otra.
La respuesta llega en **dos burbujas**: primero la referencia y el vencimiento,
y después únicamente los **8 dígitos de la clave**, en texto plano y conservando
los ceros iniciales, para copiarla con facilidad. La clave se envía una vez por
Telegram; **ambos mensajes permanecen en ese chat**.
En NovaPOS solo queda el hash y la referencia: propuestas, auditorías e historial
que se envía a la IA no conservan la clave en claro. Si la entrega resulta
incierta, revisar/revocar desde la web antes de pedir otra; el bot no la recupera.
Si falla el envío de la segunda burbuja, se avisa de la entrega incierta sin
regenerar la clave ni almacenarla para reenviarla después.

No requiere migraciones nuevas. Para activarlo en producción hay que desplegar
los archivos actualizados y reiniciar el trabajador existente (no lanzar uno
adicional). El comando escrito funciona sin reconfigurar el webhook; actualizar
su menú es opcional. Estas modificaciones se verificaron localmente con 481
pruebas aisladas, incluida la entrega incierta, sin pagos, claves ni mensajes
reales de prueba.
