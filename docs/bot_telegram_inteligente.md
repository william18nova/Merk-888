# Bot inteligente de Telegram

El bot consulta Nova mediante los permisos del usuario vinculado. El texto libre
usa Gemini y, si no está disponible, Groq, Cerebras y OpenRouter como respaldos
configurables. Las notas de voz pueden usar Groq, Azure Speech, Deepgram,
AssemblyAI y Gemini. Solo se llaman proveedores habilitados con claves válidamente
configuradas; esto no garantiza cuotas ni calidad idéntica. Un pago nunca se registra
directamente: se crea una propuesta que vence en 10 minutos y solo se ejecuta al
pulsar **Confirmar** en Telegram.
Los cambios de catálogo siguen el mismo esquema: propuesta, revisión y botón
de confirmación. Un «sí» escrito o hablado no guarda el cambio.

## Respuestas naturales y al grano

El bot responde primero el dato solicitado, sin saludos repetidos, introducciones
ni explicaciones adicionales. El mismo criterio se aplica al texto y al audio
transcrito, tanto con el proveedor principal como con los respaldos.

- «¿Cuánto vendimos hoy?» muestra solo el total, la fecha y la sucursal si se pidió.
- «¿Cuánto hemos pagado hoy?» muestra solo el total pagado.
- «Sepáralo por medio de pago» añade el desglose; no añade la lista de pagos.
- «Muéstrame los pagos» sí muestra cada pago, con importe, medio, fecha y quién
  lo registró. Los botones permiten recorrer la lista completa.
- «¿Cuánto queda?» muestra ventas menos pagos, aclarando que no es el saldo real
  de la caja o del banco. El detalle de vendido y pagado se añade cuando se pide.
- Los informes y rankings no añaden conteos, promedios ni total general a un
  desglose salvo que se soliciten. Para pedir únicamente una cantidad o promedio,
  el intérprete usa la consulta de conteo o promedio correspondiente.
- «¿Y ayer?» mantiene el tipo de respuesta y los filtros de la consulta anterior.
  Se puede pedir después más o menos detalle sin cambiar el intervalo.

Las listas breves no muestran «página 1 de 1». Se conservan identificadores,
centavos, condiciones de búsqueda y advertencias necesarias. Las propuestas de
pago, devolución o cambio siguen requiriendo revisión y el botón **Confirmar**.

No se hace una llamada adicional a la IA para reescribir resultados: se usan
plantillas y reglas de estilo compartidas. Así, el cambio de tono no añade ese
consumo de cuota ni permite que una reformulación altere cifras verificadas.

Este ajuste de presentación no añade migraciones ni variables privadas. Para
verlo en producción, después de subir y actualizar el código, reinicia la misma
tarea **Always-on** del bot y recarga la Web. Las migraciones de ampliaciones
anteriores que sigan pendientes deben aplicarse por separado.

## Activar los respaldos de texto y audio

La integración está programada, pero cada cuenta debe configurarse y probarse
antes de usarla en producción. No se crean cuentas ni claves automáticamente.
No pegues claves en Git, documentación, capturas ni mensajes: guárdalas en
`/home/Merk888/.telegram_bot.env` (en Windows, en el archivo privado equivalente
leído por `TELEGRAM_ENV_FILE`). `.env.example` contiene solo nombres y ejemplos.

### Orden y variables privadas

```dotenv
TELEGRAM_TEXT_PROVIDERS=gemini,groq,cerebras,openrouter
TELEGRAM_VOICE_PROVIDERS=groq,azure,deepgram,assemblyai,gemini

# Conservar las claves existentes de Gemini y Groq.
CEREBRAS_API_KEY=
CEREBRAS_CHAT_MODEL=gpt-oss-120b
OPENROUTER_API_KEY=
OPENROUTER_CHAT_MODEL=openai/gpt-oss-120b:free
GEMINI_AUDIO_MODEL=gemini-3.8-flash

AZURE_SPEECH_KEY=
AZURE_SPEECH_RESOURCE=
AZURE_SPEECH_FREE_TIER_CONFIRMED=false
TELEGRAM_FFMPEG_BIN=ffmpeg

DEEPGRAM_API_KEY=
DEEPGRAM_MODEL=nova-3
DEEPGRAM_TRIAL_CONFIRMED=false

ASSEMBLYAI_API_KEY=
ASSEMBLYAI_MODEL=universal-2
ASSEMBLYAI_TRIAL_CONFIRMED=false
```

- Elimina un nombre del orden para desactivar ese proveedor; un orden vacío
  desactiva esa etapa. No se llama a todos a la vez. Se para en el primer éxito.
- Sin clave, el proveedor se omite. Los modelos se fijan por nombre; no se usa
  un selector aleatorio ni se aceptan modelos dictados por el chat.
- Azure: crear un recurso **Speech F0**, confirmar su plan y poner
  `AZURE_SPEECH_FREE_TIER_CONFIRMED=true`. `AZURE_SPEECH_RESOURCE` es únicamente
  el nombre del recurso de `NOMBRE.cognitiveservices.azure.com`, no una URL.
  Esta integración necesita **FFmpeg** en el servidor (`ffmpeg -version`).
  Convierte a WAV PCM 16 kHz mono sin comprimir y rechaza audios de más de 60 s
  para Azure; no corta la solicitud. Si no hay FFmpeg, intenta otro proveedor.
- Deepgram y AssemblyAI: confirmar que la cuenta conserva **créditos iniciales**
  y tiene recargas automáticas desactivadas antes de cambiar su `TRIAL_CONFIRMED`
  a `true`. Estos créditos no son una cuota renovable indefinida.
- **Estos indicadores son confirmaciones administrativas, no verificaciones de
  saldo/facturación.** El código no puede garantizar coste cero si la cuenta se
  convierte a pago. Mantén los planes gratuitos, desactiva recargas y revisa los
  límites en los paneles. El bot nunca compra créditos ni activa facturación.
- OpenRouter exige un modelo fijo `:free` y envía precios máximos cero para
  entrada, salida y solicitud; si no hay ruta gratuita compatible con herramientas,
  falla sin elegir un modelo de pago. No habilita complementos facturables.
- Revisar las condiciones de tratamiento de datos de cada cuenta: al habilitar
  un respaldo, puede recibir audios o texto del negocio cuando fallen los anteriores.
  No se envía el token de Telegram ni una URL privada de descarga a esos servicios.

### Migración y despliegue

Esta ampliación SÍ necesita **0039_telegram_transcription_fallback**: añade estado
duradero del trabajo de transcripción y fecha del siguiente intento. No cambia
ventas, pagos, productos ni permisos. Pausa el trabajador antes de actualizar;
no arranques otro trabajador para sustituirlo.

Tras subir el código al repositorio, en la consola de PythonAnywhere:

```bash
cd /home/Merk888/Merk-888
git pull
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py comprobar_ia_telegram --status
```

Configura el archivo privado, reinicia la **misma tarea Always-on** y recarga la
Web. El panel `/configuracion/telegram/` muestra el orden y qué proveedor está
sin clave, desactivado por falta de confirmación o configurado. Es un diagnóstico
de configuración, no una prueba de conectividad ni de saldo.

Pruebas reales opcionales (consumen cuota; no ejecutan acciones del negocio):

```bash
python manage.py comprobar_ia_telegram --provider cerebras
python manage.py comprobar_ia_telegram --provider openrouter
python manage.py comprobar_ia_telegram --stage voice --provider deepgram --audio-file /home/Merk888/prueba.ogg
```

`--status` nunca llama a las API. Usa un audio de prueba sin datos sensibles;
el comando de voz no imprime su contenido. En AssemblyAI el diagnóstico puede
informar que el trabajo sigue pendiente; la cola del bot sí conserva el ID para
consultarlo después sin volver a subirlo. Evita repetir ese diagnóstico pendiente.

### Comportamiento y límites

- Texto: Gemini → Groq → Cerebras → OpenRouter, salvo orden configurado. Todos
  reciben las mismas reglas y pasan por la misma validación de herramientas y
  argumentos antes de consultar o proponer acciones. La calidad real debe
  comprobarse con consultas representativas, incluyendo montos y nombres propios.
- Voz: Groq → Azure → Deepgram → AssemblyAI → Gemini. Una transcripción válida
  se guarda antes de interpretar. Si la interpretación falla, no se transcribe
  nuevamente el mismo mensaje. No se recortan textos largos para convertirlos
  en acciones incompletas. Gemini se usa solo para transcribir, sin herramientas.
- Un trabajo pendiente de AssemblyAI se vuelve a consultar cada 15 s (máximo
  10 minutos), con un único aviso y sin reenviarlo. Otros chats pueden progresar;
  ese chat conserva el orden. Al fallar o superar la espera, intenta Gemini si
  está configurado. Un envío de resultado incierto no se repite automáticamente.
- Tras guardar el resultado de AssemblyAI se intenta borrar su copia externa;
  si la limpieza falla se registra `limpieza_pendiente=true`. Esto no garantiza
  eliminación de archivos subidos o trabajos fallidos: revisar también la
  política de retención del proveedor.
- Cada fallo usa su categoría segura (cuota, créditos, acceso, conexión, etc.).
  Se respeta `Retry-After`; HTTP 402 pausa el proveedor por 24 h sin comprar
  créditos. Las pausas están en memoria por trabajador y se reinician al reiniciarlo.
- Solo usuarios vinculados y activos pueden consumir transcripción. Los grupos
  no se procesan. Los permisos y botones de confirmación se mantienen.

Pruebas sin API reales ni base de datos de producción:

```bash
python manage.py test mainApp.test_telegram_provider_fallbacks --settings=NovaSoft.test_settings
```

Referencias de implementación: [Cerebras](https://inference-docs.cerebras.ai/api-reference/chat-completions),
[rutas OpenRouter](https://openrouter.ai/docs/guides/routing/provider-selection),
[Azure audio corto](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short),
[Deepgram](https://developers.deepgram.com/docs/pre-recorded-audio),
[AssemblyAI](https://www.assemblyai.com/docs/pre-recorded-audio/api-reference/transcripts/submit),
[Gemini audio](https://ai.google.dev/gemini-api/docs/audio).

## Respuestas más naturales

El bot habla de tú, con frases breves y lenguaje cotidiano. Los totales, listas,
confirmaciones, horarios y mensajes de error usan plantillas claras; Gemini y
Groq reciben las mismas instrucciones de tono cuando necesitan interpretar una
solicitud o preguntar un dato que falta. No se hace otra llamada a la IA solo
para reescribir resultados, por lo que esta presentación no añade ese consumo
ni permite que la IA cambie los importes calculados por el sistema.

Ejemplos de formato (valores ilustrativos):

- Total: «Hoy (07/09/2026) se han pagado $85.000.»
- Sin resultados: «No encontré pagos registrados hoy (07/09/2026). Total pagado: $0.»
- Antes de guardar: «¿Confirmas que registre este pago? […] Todavía no lo he guardado.»
- Después de guardar: «Listo, el pago quedó registrado: COCA-COLA por $20.000 en Efectivo.»
- Servicio no disponible: «Ahora mismo no pude atender esa consulta. Inténtalo más tarde o usa /ayuda para ver otras formas de consultar.»

Se conservan las fechas exactas, los filtros, los identificadores de los
registros, quién registró cada pago y los centavos. Los totales del negocio no
se presentan como gastos personales del usuario. Las listas y los desgloses se
siguen mostrando solo cuando se solicitan. «Hola» y «gracias» tienen respuestas
breves sin IA; un saludo acompañado de una petición conserva el flujo normal.

Los detalles de errores de proveedores quedan en el diagnóstico del trabajador
y en la auditoría, no en el mensaje al usuario. No se copian cuerpos de error
de los proveedores. Si un fallo impide saber si un cambio se guardó, se pide
revisarlo antes de repetirlo; nunca se asegura que no ocurrió nada sin saberlo.

Estas mejoras no necesitan migraciones. Para activarlas en PythonAnywhere,
sube el código, ejecuta `git pull`, reinicia la tarea Always-on existente y
recarga la Web. No crees un segundo trabajador.

Pruebas de presentación y seguridad con API simuladas:

```bash
python manage.py test mainApp.test_telegram_human_responses --settings=NovaSoft.test_settings
```

## Asistente operativo tipo «Jarvis»

### Nombres parecidos y consultas flexibles

La búsqueda de nombres ignora acentos, mayúsculas y separadores y tolera pequeños
errores de escritura y palabras intercambiadas. Por ejemplo, «cocacola» puede
encontrar «COCA-COLA» y «aroz diana» puede encontrar «ARROZ DIANA 500 G».
Las listas ponen primero las coincidencias más cercanas. Los IDs y códigos
explícitos no se sustituyen por otros parecidos ni se mezclan presentaciones
numéricas distintas (500 g y 1000 g).

Se aplica a productos, inventario, empleados, sucursales, categorías, búsquedas
de catálogos, conceptos de pago y usuarios que registraron pagos. Las propuestas
de cambio pueden localizar un nombre parecido, mostrando el registro real antes
de confirmar. Una devolución busca únicamente entre los productos de esa venta.
Si dos nombres son muy parecidos o la coincidencia es débil, pregunta cuál usar
y muestra sus IDs. La pregunta y la solicitud original permanecen en el contexto
del chat para responder con el ID sin repetir todo. Nunca confirma un cambio por
haber encontrado un nombre parecido.

El motor `consultar_datos` permite expresar nuevas combinaciones de consultas sin
programar una función por frase: listar, contar registros o valores distintos,
sumar, promediar y obtener mínimos/máximos. Admite hasta ocho filtros y dos
agrupaciones en ventas, productos vendidos, pagos, productos, inventario,
empleados y pedidos. También puede combinarse con hasta cuatro consultas de
lectura mediante `consultar_varias` y continuar con fechas o agrupaciones nuevas.

Ejemplos:

- «Cuántas unidades de arroz Diana vendimos este mes por sucursal».
- «Suma los pagos de cocacola de esta semana por medio de pago».
- «Muéstrame los productos con precio entre 1000 y 3000».
- «Cuál fue el pago más alto de ayer».
- «Cuántos clientes distintos compraron este mes».
- «Cambia el precio de aroz Diana a 3000» prepara el cambio; si hay varias
  presentaciones, primero pide elegir y después exige Confirmar.

El modelo propone la fuente, filtros y operación; **el servidor valida campos y
permisos y construye consultas ORM parametrizadas**. No acepta SQL, código,
tablas, joins o rutas arbitrarias del chat. Los cálculos y los resultados se
obtienen del sistema, no de texto inventado por la IA. Los pagos de Tarjeta/Caja
Social se agrupan juntos aunque usen códigos históricos diferentes.

Los eventos son de hoy salvo fechas explícitas y las consultas temporales no
superan un año. Productos e inventario muestran su estado actual. Los importes
de renglones vendidos no equivalen al neto cobrado: se avisa que no descuentan
descuentos globales ni reintegros. Los totales se calculan sobre todos los
registros que cumplen los filtros, no solo sobre la página visible. Si una
búsqueda por similitud es demasiado amplia, pide afinarla sin dar un total
recortado. No accede a contraseñas, claves API ni registros fuera de los permisos.

Esto amplía las consultas y la resolución de nombres; no convierte al bot en un
ejecutor ilimitado de acciones nuevas. Las escrituras siguen usando las
funciones permitidas y su confirmación. No necesita migraciones ni dependencias
nuevas. Para activarlo, despliega el código y reinicia el trabajador existente.

```bash
python manage.py test mainApp.test_telegram_smart_queries --settings=NovaSoft.test_settings
```

El nombre «Jarvis» es opcional: puedes anteponerlo a tus solicitudes. No supone
acceso sin límites: cada consulta y cada cambio siguen los permisos de Nova.
No ejecuta código, SQL, comandos del servidor ni acciones arbitrarias dictadas
en el chat.

### Informes y preguntas más completas

- «¿Quién vendió más este mes?» muestra el empleado con mayor importe vendido.
- «Ventas por cajero esta semana»: total, número de ventas y promedio por empleado.
- «Ventas por cliente / sucursal / punto de pago / día»: agrupaciones del intervalo.
- «Los cinco clientes que más compraron»: ranking por importe con un máximo pedido.
- «Compara lo vendido esta semana con el período anterior»: total, diferencia y
  porcentaje; usa el intervalo inmediatamente anterior de igual duración, no
  necesariamente una semana calendario completa. Si el total anterior es cero,
  no inventa un porcentaje.
- «¿Cuánto pagué por concepto este mes?» o «Pagos por usuario/día»: agrupaciones
  de pagos, con filtros por concepto, usuario, medio y límites de monto.
- «Jarvis, ¿cómo va el negocio?» o `/resumen`: ventas, pagos, balance operativo,
  existencias bajas/agotadas, pedidos pendientes y turnos, solo según tus permisos.

Los informes de ventas utilizan el total guardado de cada venta, sin duplicarlo
por la cantidad de renglones de productos. Las modificaciones y devoluciones ya
reflejadas en ese total están incluidas: no son una reconstrucción histórica del
importe original. El balance operativo no es utilidad contable ni saldo bancario.
Inventario, pedidos pendientes y turnos del resumen describen el estado **actual**,
aunque el intervalo de ventas/pagos sea histórico.

### Varias consultas y continuaciones

«Muéstrame las ventas de hoy, los pagos y los productos agotados» puede resolver
hasta **cuatro consultas independientes de lectura**. Cada parte se identifica,
comprueba sus permisos y conserva los botones de su listado. Si una no está
permitida, se indica sin revelar sus datos. Las respuestas largas se envían
completas en varios mensajes; ya no se cortan a 4.000 caracteres.

Puedes continuar con «¿y ayer?», «¿y este mes?», «ahora por sucursal» o
«siguiente página». El sistema recupera los filtros y fechas de la consulta
exitosa de **la misma cuenta y chat**, durante 24 horas, vuelve a comprobar sus
permisos y consulta datos actuales. Al cambiar filtros se vuelve a la primera
página. Después de una respuesta con varias consultas, debes aclarar cuál deseas
continuar. No se reutilizan acciones de escritura como si fueran consultas.

Esto funciona con texto y audios transcritos. Gemini/Groq interpretan las
solicitudes abiertas. Los atajos claros por ID, resumen, pendientes, navegación,
fechas de continuación y «quién vendió más hoy/ayer/este mes» se resuelven sin
llamar a la IA de texto. La transcripción de audio usa los respaldos configurados.

Comandos nuevos:

```text
/resumen
/ranking empleados
/ranking sucursales 2026-09-01 2026-09-06
/ranking clientes
/ranking cajas
/pendientes
/pendientes 2
```

`/pendientes` muestra únicamente tus propuestas vigentes, con opción de
cancelarlas. Para confirmar debes revisar el mensaje original completo; nunca
se confirma desde un resumen abreviado. Los cambios no se mezclan en una consulta
compuesta: pide cada acción para recibir su propia propuesta y confirmación.

Continúan disponibles pagos operativos, devoluciones parciales con medio de
reintegro y edición/creación de catálogos. No se agregaron ejecución de ventas,
cierres, eliminaciones, ajustes manuales de inventario, transferencias bancarias,
cambios de contraseñas o permisos desde el chat: se ofrece la página apropiada.

Al preparar un pago, el bot busca conceptos existentes parecidos, sin distinguir
tildes, espacios o guiones y admitiendo pequeñas diferencias de escritura. Por
ejemplo, para «pago de 1 en efectivo a cocacola», si existe **COCA-COLA**, ofrece
**Usar COCA-COLA** o **Crear nuevo: COCACOLA**. Puede mostrar hasta cinco opciones.
La elección no crea conceptos ni pagos: primero muestra el resumen y pide
**Confirmar**. Si el nombre ya coincide exactamente, pasa directamente al resumen.
Los conceptos nuevos se guardan en mayúsculas solo al confirmar; el monto y el
medio de pago se conservan. Esto funciona igual con texto y notas de voz.

La propuesta completa conserva su vencimiento de diez minutos, aunque se elija
un concepto. Los botones antiguos no pueden cambiar una elección ya realizada;
si el concepto elegido fue eliminado o renombrado, se debe solicitar el pago otra
vez. No se registra dos veces al pulsar varias veces **Confirmar**.

### Consultas con listas y filtros

- «¿Cuánto he pagado hoy?»: responde solo el total del intervalo solicitado,
  sin listar movimientos ni separar métodos. Este es el formato predeterminado.
- «¿Cuánto he pagado hoy por método de pago?»: muestra el total y el desglose
  por método, sin la lista de pagos individuales.
- «Muéstrame los pagos de hoy»: muestra cada pago con ID, concepto, monto,
  medio de pago, fecha/hora de Colombia y usuario que lo registró. Incluye el
  total de todo el intervalo, no solo el de la página visible. No añade el
  desglose agregado por método a menos que también se pida.
- «Pagos de esta semana en Nequi registrados por William»: combina fechas,
  medio y usuario. También se puede filtrar por concepto y montos mínimo/máximo.
- «Solo el total pagado ayer»: devuelve únicamente el total, sin desglose.
- «Lista de empleados» o «Cajeros de la sucursal Yerbabuena»: muestra ID,
  nombre, cargo, sucursal y usuario vinculado, sin documentos, direcciones,
  teléfonos ni correos. Requiere el permiso `visualizar_empleados`.

Las continuaciones como «ahora sepáralo por método de pago», «dame la lista» o
«ahora solo el total» conservan las fechas y los filtros de la última consulta
exitosa de pagos de esa cuenta y chat, durante 24 horas. El formato y la página
no se heredan: se usa lo solicitado en el nuevo mensaje. Los cambios de filtros
deben pedirse explícitamente. Esto funciona con texto y con audios transcritos.
Si no hay una consulta anterior disponible, el bot pide indicar el intervalo.

Las listas se muestran de cinco en cinco, con **Anterior** y **Siguiente**.
Los botones conservan los filtros y las fechas originales, vencen a las 24 horas
y solo funcionan para el usuario que hizo la consulta. Cada página vuelve a
comprobar los permisos y consulta los datos actuales; no es una foto congelada
de la base de datos. La navegación solo ejecuta consultas de lectura.

También puedes usar `/pagos`, `/pagos 2`, `/empleados` o `/empleados NOMBRE`
sin consultar a Gemini/Groq. Las preguntas naturales funcionan tanto escritas
como mediante notas de voz. No se ejecuta SQL arbitrario ni se crean pagos por
pedir una lista.

### Catálogos, operaciones y páginas del sistema

Para consultar una venta concreta puedes escribir `/venta 142266`,
`/factura 142266` o «Muéstrame la venta de ID 142266». Estos mensajes claros
se resuelven directamente, sin consultar a Gemini/Groq ni depender de su cuota.
También funciona con el texto transcrito de un audio (transcribir sí requiere
Groq). Se muestran fecha, hora, cliente, cajero, sucursal, punto de pago, total,
medios de pago y productos con sus IDs, cantidades y precios. Las listas largas
tienen botones de paginación. El ID se busca en todo el historial, no solo hoy.

La cuenta debe estar vinculada y tener permiso de consulta/impresión de ventas;
no necesita permiso de devoluciones para verlas. Un cajero solo puede consultar
ventas de su sucursal. Consultar no modifica la venta ni crea una devolución.
`/ventas` sigue mostrando el resumen de hoy. Si el ID no existe o la cuenta no
tiene acceso, el bot lo indica sin inventar información.

Además de las consultas anteriores, el bot puede listar, buscar y contar estos
22 recursos, siempre con el permiso correspondiente de la aplicación:

- Productos, categorías, sucursales, proveedores, clientes y puntos de pago.
- Inventario, precios de proveedor, horarios del negocio y horarios de caja.
- Ventas, pedidos, cambios/devoluciones y reintegros.
- Nequi, usuarios, roles, permisos, métodos de pago y funcionalidades.
- Carritos abandonados/vaciados y conceptos de pagos.

Ejemplos de texto o audio:

- «Lista los proveedores» o «Dame el teléfono del proveedor COCA-COLA».
- «¿Cuántos productos hay?» o «Productos de la categoría BEBIDAS».
- «Productos que nunca se han vendido»: revisa todo el historial de ventas
  disponible, no solamente el día actual; no elimina productos.
- «Productos agotados en Yerbabuena» o «Inventario con máximo 5 existencias».
- «Nequi recibidos hoy que no estén vinculados a una venta».
- «Pedidos recibidos entre el 1 y el 6 de septiembre de 2026».
- «Detalle de la venta 142266», «Detalle del pedido 45» o «Detalle del turno 80».
- «Productos más vendidos de esta semana por cantidad» o «Por importe».
- «Muéstrame los métodos de pago inactivos» o «Las funcionalidades deshabilitadas».

Los listados de eventos usan hoy en Colombia salvo que se indique otro
intervalo (máximo 366 días). Un ID exacto permite consultar un evento antiguo
sin restringirlo a hoy. Las listas de catálogo no tienen filtro por fecha.
Las páginas conservan sus filtros e intervalo, y vuelven a consultar los datos.
El total de un listado corresponde a todos sus registros, no solo a la página.

El ranking suma cantidad y precio unitario de los renglones de venta: no es
utilidad neta ni resta descuentos globales o reintegros. Las cantidades conservan
la unidad registrada del producto. El detalle de turno muestra los valores
guardados, no recalcula un cierre abierto; incluye facturas pagadas como dato
informativo, sin sumarlas de nuevo. Requiere acceso al dashboard o administración
de turnos. Nequi muestra únicamente ingresos, nunca notificaciones de envío.

No se muestran contraseñas, claves API, datos crudos de notificaciones ni
documentos de identidad en los listados. Teléfono/correo de cliente o proveedor
se incluyen solo cuando se piden explícitamente y se tiene permiso para su vista.
Los errores de esquema o filtros no aplicables se rechazan; no se ejecuta SQL
ni se aceptan nombres de modelos o campos arbitrarios.

### Crear y editar desde el chat

El bot puede **crear y editar productos, categorías, clientes, proveedores,
sucursales y empleados**, reutilizando los formularios y validaciones web. También mantiene
el registro de pagos con sugerencias de conceptos y confirmación.

- «Crea una categoría llamada HELADOS».
- «Cambia el precio del producto 2934 a 12000».
- «Cambia el precio de AGUA NATURAL a 2000»: admite un nombre exacto únicamente
  si identifica un solo registro; si es ambiguo pide consultar su ID.
- «Pon al producto 2934 la categoría ID 8».
- «Actualiza el teléfono del proveedor 12 a 3001234567».
- «¿Qué datos necesitas para crear un cliente?».
- «¿Qué datos necesitas para crear un empleado?» o `/acciones empleado`.
- «Actualiza el teléfono del empleado ID 12 a 3001234567».

Para crear un empleado se piden nombre, apellido, documento, teléfono, correo,
`usuarioid` y `sucursalid`; puesto y dirección son opcionales. Los IDs deben
existir y la cuenta no puede estar vinculada a otro empleado. Asignar la cuenta
requiere también permiso para visualizar usuarios. La propuesta muestra las
identidades seleccionadas y se valida otra vez al confirmar. El guardado reutiliza
la sincronización empleado/cliente del sistema en una transacción: no duplica el
cliente ni deja medio cambio si hay un conflicto. No crea cuentas de acceso ni
solicita contraseñas por Telegram.

Solo se modifican los campos solicitados. Los obligatorios faltantes se piden;
no se inventan documentos, correos, teléfonos o precios. La categoría de un
producto se indica con un ID existente; IVA usa valores de 0 a 1. Los códigos
de barras y documentos conservan ceros iniciales. Al cambiar un precio se
conserva el precio anterior, igual que en la web. La categoría «Sin categoría»
sigue protegida contra cambios de nombre.

La propuesta muestra el registro y los valores anterior/nuevo. Al confirmar
se vuelven a comprobar permisos y formularios; si el registro fue editado o
eliminado entretanto, se rechaza para no sobrescribir el cambio ajeno. La
confirmación se procesa una sola vez y queda auditada con el usuario. Cancelar,
dejar vencer o pulsar un botón ajeno no modifica el catálogo.

Los cierres de caja, la facturación, los ajustes manuales de inventario,
las eliminaciones y los cambios de usuarios, roles o configuración sensible
siguen realizándose en la web. El bot puede **buscar el enlace de su página**,
pero no afirma haber ejecutado esas operaciones.

### Devolver productos de una venta

Puedes escribir o enviar un audio como:

> De la venta 142266, devuelve 2 del producto 2934 y 1 del producto 2941.
> Entrega el reintegro en efectivo.

El bot prepara una devolución, **no la ejecuta todavía**. Muestra los productos,
las cantidades disponibles y solicitadas, el total realmente reintegrable, el
medio de salida, la sucursal, el punto de pago y, cuando corresponde, el turno
actual y su cajero. Debes pulsar **Confirmar devolución** para registrarla.

- Si no indicas medio, usa **efectivo**, aunque la compra original se haya pagado
  en Nequi. También admite Nequi, Tarjeta / Banco Caja Social y otros métodos
  activos. En el bot se admite un solo medio por propuesta; un reintegro mixto
  se registra desde la página de la venta.
- Indica la venta, los productos y las cantidades explícitamente. Se acepta ID
  de producto o nombre exacto dentro de la venta. Si el mismo producto aparece
  en varios renglones, el bot pide el **ID de detalle**, que no es el ID de producto.
- Las cantidades son enteras en la unidad almacenada del producto. No se asume
  devolver toda la venta ni toda la cantidad disponible cuando no lo pides.
- El monto lo calcula la misma lógica de la web, respetando descuentos y el saldo
  de la venta. En ventas gratuitas restaura inventario sin entregar dinero.
- Al confirmar, restaura inventario en la sucursal de la venta, disminuye las
  cantidades de sus renglones y el total de la venta, registra la devolución y
  su reintegro con el usuario que lo realizó. Conserva los pagos originales.
- Para ventas antiguas de un solo medio sin filas en `venta_pagos`, al confirmar
  registra el ingreso original antes de restar la devolución, evitando que el
  cierre descuente dos veces el dinero. Si falta la distribución de una venta
  mixta o los pagos históricos son inconsistentes, pide corregirlos en la web;
  no inventa ni reemplaza una distribución existente.
- Con control de turnos, el reintegro se carga al turno **vigente del punto de
  pago original**, aunque atienda otro cajero. No reabre ni modifica turnos
  históricos cerrados. Si no hay turno activo para una salida de dinero, bloquea
  la propuesta. Sin control de turnos, el efectivo ajusta el saldo global de ese
  punto de pago y el reintegro queda sin turno.
- Comparte con la web el permiso `ventas_cambios`; el rol Cajero continúa sin
  autorización para ejecutar devoluciones, aunque pueda consultar/imprimir.
- La propuesta vence en diez minutos. Volver a pulsar Confirmar no duplica la
  devolución. Si cambian la venta, sus renglones, el medio, el turno o la exigencia
  de turnos, se rechaza la propuesta y hay que pedir otra. Los permisos se revisan
  de nuevo. Un error de guardado revierte el conjunto de cambios.

**El bot registra la devolución contable y de inventario; NO envía dinero por
Nequi ni solicita una reversión a un banco o tarjeta.** La entrega o transferencia
del reintegro debe hacerse por el medio indicado fuera del bot.

También está disponible el comando sin IA:

```text
/devolver 142266 2934:2 2941:1 efectivo
/devolver 142266 2934:2 Banco Caja Social
/devolver 142266 2934:2
```

El último usa efectivo por defecto. `/devolver` sin argumentos muestra la ayuda.
Se aceptan hasta 20 renglones por propuesta, siempre que el resumen completo
quepa en Telegram; si no cabe, pide dividir la solicitud, nunca omite productos.
Esta función reutiliza las tablas existentes y no requiere una migración nueva;
la tabla histórica de reintegros de la migración 0021 sí debe estar instalada.

Comandos adicionales, disponibles sin consultar a Gemini/Groq:

```text
/acciones
/acciones producto
/catalogo proveedores
/catalogo productos AGUA
/vistas caja
```

`/acciones` muestra las capacidades y `/acciones ENTIDAD` los campos admitidos
para crear/editar. `/vistas` busca en el menú real, filtra los permisos de la
cuenta y devuelve enlaces que requieren iniciar sesión. También está disponible
la navegación con Anterior/Siguiente.

Esta ampliación no cambia modelos ni requiere migraciones nuevas. Para activarla
en producción, primero hay que subir los archivos a Git y ejecutar `git pull`
en PythonAnywhere; después **reiniciar la tarea Always-on del bot** y recargar
la aplicación Web. El procesador continuo importa el código al arrancar: hacer
solo `git pull` o recargar la Web no actualiza el proceso del bot que ya estaba
ejecutándose. Configurar nuevamente el webhook desde el panel actualiza también
el menú de comandos de Telegram; los comandos escritos funcionan sin ese paso.

Al configurar ambas claves, Gemini interpreta primero el texto y Groq toma el
relevo si Gemini falla, agota cuota o devuelve una respuesta vacía o inválida.
La pausa depende del fallo: una respuesta vacía, JSON inválido o error de esquema
no bloquea al proveedor para las siguientes solicitudes. Los fallos de conexión
o servicio tienen pausas progresivas de 10 a 60 segundos; se respeta una espera
mayor indicada por el proveedor. Para cuota se respetan `Retry-After`, los tiempos
de recuperación de Groq o `RetryInfo` de Gemini; si no hay indicación, se esperan
60 segundos (una hora cuando se identifica explícitamente cuota diaria agotada).
Esa espera permite volver a probar, no garantiza que la cuota ya se haya renovado.
Credenciales rechazadas o modelos inexistentes mantienen cinco minutos de pausa;
cambiar la clave o el modelo permite reintentarlo inmediatamente. Las pausas son
por proveedor/configuración dentro del proceso del trabajador.

Se prueban primero los proveedores alternativos configurados. Con dos proveedores,
si ambos fallan por problemas transitorios o respuestas inválidas, se permite un
único reintento adicional con una espera de 0,4–0,8 segundos, hasta tres llamadas.
Con los cuatro proveedores se da una oportunidad a cada uno, sin intento extra:
**máximo cuatro llamadas de interpretación por mensaje**.
No se reintenta inmediatamente un 429, una clave inválida, un modelo inexistente
ni un error con espera explícita. Tampoco se hace ese intento extra si ya han
transcurrido 35 segundos. Las conexiones tienen timeout de 5 segundos y las
lecturas de 18; no es un límite absoluto de duración total de la solicitud.

Agotados esos intentos, el trabajador informa la causa sin repetir toda la
interpretación otras tres veces. Los registros muestran proveedor, categoría,
código HTTP, pausa y tamaño de la solicitud, **nunca las claves, el texto del
usuario ni el cuerpo de respuesta del proveedor en esos mensajes de diagnóstico**.
El texto habitual del chat sigue sujeto al historial y auditoría existentes.
La acción se despacha solo después de una interpretación válida; los cambios
siguen requiriendo sus botones de confirmación. El panel muestra proveedores
configurados; tener una clave guardada no garantiza su validez.

Las notas de voz se transcriben con los respaldos de audio y el texto resultante
pasa por la cadena de interpretación. No se envía cada solicitud a todos si el
primer proveedor responde correctamente.

### Menor consumo y continuidad de las conversaciones

- Para un tema identificado se envían solo las herramientas relacionadas y las
  reglas correspondientes. Las peticiones de varios temas conservan herramientas
  de ambos y las consultas múltiples. Si el tema es incierto, se mantiene el
  catálogo completo para no eliminar capacidades.
- El contexto reciente de la misma cuenta/chat se limita a ocho mensajes, hasta
  1.200 caracteres por mensaje y 6.000 en total. No se comparten conversaciones
  ni se reutilizan resultados financieros en caché.
- Frases sencillas como «cuánto he pagado hoy», «muéstrame los pagos de hoy»,
  «cuánto pagué este mes por método de pago», «lista de empleados», «mi horario
  mañana» y «cuánto vendimos ayer» se resuelven directamente, con los mismos
  permisos y datos actuales. Solo las coincidencias completas son atajos: si hay
  filtros, acciones o ambigüedades adicionales, se conserva la interpretación IA.
- Una conversación espera a su mensaje anterior. Si hay varios trabajadores,
  otra conversación puede avanzar; no se fusionan mensajes ni acciones.
  El trabajador revisa mensajes abandonados cada minuto. Un último intento
  interrumpido se marca como error para no bloquear el chat indefinidamente.
- Una nota de voz ya transcrita no vuelve a enviarse a la API de transcripción
  al reintentar ese mismo mensaje. Un audio nuevo todavía necesita transcripción.

Esta mejora no necesita nuevas claves, dependencias ni migraciones. **Para
activarla hay que subir el código, hacer `git pull` y reiniciar el trabajador
Always-on existente**; no crear otro trabajador duplicado. Recarga también la Web.
Las pruebas automatizadas usan API simuladas, sin consumir cuota ni datos reales:

```bash
python manage.py test mainApp.test_telegram_ai_resilience mainApp.test_telegram_bot --settings=NovaSoft.test_settings
```

Referencias de la política de recuperación: [cabeceras y límites de Groq](https://console.groq.com/docs/rate-limits)
y [errores y reintentos de Gemini](https://ai.google.dev/gemini-api/docs/troubleshooting).

## 1. Crear las credenciales

1. En Telegram abre `@BotFather`, crea el bot con `/newbot` y copia el token.
2. Genera un secreto largo para el webhook, por ejemplo con
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Crea una API key en Google AI Studio para Gemini.
4. Crea una API key en Groq para transcribir notas de voz.

No pegues ninguno de esos valores en Git, en `settings.py` ni en este documento.

## 2. Variables privadas en PythonAnywhere

Crea `/home/Merk888/.telegram_bot.env` con permisos privados:

```bash
chmod 600 /home/Merk888/.telegram_bot.env
```

Su contenido debe usar este formato:

```bash
TELEGRAM_BOT_TOKEN='TOKEN_REAL'
TELEGRAM_WEBHOOK_SECRET='SECRETO_LARGO_REAL'
TELEGRAM_WEBHOOK_URL='https://merk888.pythonanywhere.com/api/telegram/webhook/'
GEMINI_API_KEY='CLAVE_REAL'
GEMINI_MODEL='gemini-3.8-flash'
GROQ_API_KEY='CLAVE_REAL'
GROQ_CHAT_MODEL='openai/gpt-oss-120b'
GROQ_WHISPER_MODEL='whisper-large-v3-turbo'
```

Django y la tarea Always-on leen automáticamente ese archivo privado. No debes
copiar las claves al archivo WSGI. Después de crearlo, pulsa **Reload** en la
pestaña Web de PythonAnywhere.

## 3. Desplegar y activar

```bash
cd /home/Merk888/Merk-888
git pull
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
```

Luego:

1. En Nova abre **Seguridad → Funcionalidades del sistema** y activa el bot.
2. Abre **Seguridad → Bot inteligente de Telegram**.
3. Pulsa **Configurar webhook** usando la contraseña Web Master.
4. En PythonAnywhere crea una tarea **Always-on** con:

```bash
/bin/bash /home/Merk888/Merk-888/scripts/run_telegram_bot.sh
```

El registro del procesador queda en `/home/Merk888/logs/telegram_bot.log`.

## 4. Vincular personas

Desde la página de configuración selecciona el usuario de Nova y genera un
código. La persona lo envía al bot así:

```text
/vincular NOVA-XXXXXXXX
```

El código se guarda únicamente como hash, vence en 10 minutos y sirve una sola
vez. Si desactivas el vínculo, el bot deja de aceptar solicitudes de esa cuenta.

## 5. Prueba manual

Para verificar las claves y la interpretación real de cada proveedor, sin ventas
ni registros de pago de prueba:

```bash
python manage.py comprobar_ia_telegram
python manage.py comprobar_ia_telegram --provider gemini
```

El comando solo envía un texto genérico y comprueba la función seleccionada; no
ejecuta consultas del negocio ni imprime claves.

```bash
set -a
source /home/Merk888/.telegram_bot.env
set +a
cd /home/Merk888/Merk-888
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py procesar_telegram_bot --once
tail -n 100 /home/Merk888/logs/telegram_bot.log
```

Los comandos `/resumen`, `/ranking`, `/pendientes`, `/ventas`, `/venta ID`, `/factura ID`, `/producto`, `/inventario`, `/pagos`, `/empleados`,
`/balance`, `/turnos`, `/acciones`, `/catalogo`, `/vistas`, `/devolver` y `/estado` siguen
disponibles aunque los proveedores inteligentes fallen. Con una clave de Groq
válida, cuota disponible y el servicio operativo, el texto libre y los audios
pueden continuar funcionando aunque Gemini no esté disponible.
