# Bot inteligente de Telegram

El bot consulta Nova mediante los permisos del usuario vinculado. El texto libre
usa Gemini y, si no está disponible, Groq como respaldo automático. Las notas de
voz usan Whisper en Groq. Un pago nunca se registra
directamente: se crea una propuesta que vence en 10 minutos y solo se ejecuta al
pulsar **Confirmar** en Telegram.
Los cambios de catálogo siguen el mismo esquema: propuesta, revisión y botón
de confirmación. Un «sí» escrito o hablado no guarda el cambio.

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

El bot puede **crear y editar productos, categorías, clientes, proveedores y
sucursales**, reutilizando los formularios y validaciones web. También mantiene
el registro de pagos con sugerencias de conceptos y confirmación.

- «Crea una categoría llamada HELADOS».
- «Cambia el precio del producto 2934 a 12000».
- «Cambia el precio de AGUA NATURAL a 2000»: admite un nombre exacto únicamente
  si identifica un solo registro; si es ambiguo pide consultar su ID.
- «Pon al producto 2934 la categoría ID 8».
- «Actualiza el teléfono del proveedor 12 a 3001234567».
- «¿Qué datos necesitas para crear un cliente?».

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
El proveedor que falle descansa cinco minutos en cada proceso antes de volver a
probarse; cambiar su clave o modelo permite reintentarlo inmediatamente. La acción
se ejecuta una sola vez, después de obtener una respuesta válida. El panel muestra
los proveedores configurados; tener una clave guardada no garantiza su validez.

Las notas de voz se transcriben con Groq y el texto resultante pasa por esa misma
combinación Gemini/Groq. No es necesario enviar cada solicitud a ambos si el
primer proveedor responde correctamente.

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

Los comandos `/ventas`, `/venta ID`, `/factura ID`, `/producto`, `/inventario`, `/pagos`, `/empleados`,
`/balance`, `/turnos`, `/acciones`, `/catalogo`, `/vistas`, `/devolver` y `/estado` siguen
disponibles aunque los proveedores inteligentes fallen. Con una
clave de Groq válida, el texto libre y los audios continúan funcionando aunque
Gemini no esté disponible.
