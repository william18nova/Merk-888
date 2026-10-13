# Ventas originales online/offline y diario local

Etapa de laboratorio del 10 de octubre de 2026, sin datos de producción.

## Ampliación del 11/10/2026 (estado actual del código)

- Autorización de identidad y catálogo: **24 horas** desde una validación
  correcta en nube. Un fallo de descarga no extiende una copia vencida. Las
  vigencias de dos horas anotadas más abajo son resultados históricos.
- Sondeo de conexión: espera de conexión 6 s y consulta 3 s en segundo plano;
  la muestra anterior no vence mientras transcurre el siguiente sondeo acotado.
  No se reejecutan escrituras en otra base después de un timeout.
- Pagos, devoluciones y cierre usan las vistas habituales. Cada POST integrado
  tiene UUID, autor, alcance y confirmación durable. Pagos congelan concepto
  normalizado, regla de 4xmil, importe y fecha original.
- Devoluciones: máximo 2000 ventas propias de los últimos 30 días descargadas
  en la caja vinculada. La nube vuelve a bloquear y validar cantidades e
  importes para detectar devoluciones concurrentes. Una venta todavía local
  necesita sincronizarse antes de poder devolverla desde su ID de nube.
- Cierre: inicio y conteo final son eventos ordenados tras las ventas/pagos.
  Comenzar el conteo impide nuevas ventas de ese turno. Offline no se muestra
  un cuadre definitivo inventado: se calcula en nube y se consulta su tirilla
  una vez conciliado. No se cierra offline un turno que contiene PTM.
- PTM y nuevas operaciones por Nequi se mantienen online. No se añadieron
  permisos especiales offline ni se guardan contraseñas de autorizadores.
- `/local/sincronizacion/`: referencias, estados, motivos, paginación y revisión
  por Web Master o permiso `caja_turnos_editar`. Reintento de la primera operación
  conflictiva con nota e historial; conserva UUID, importes, fecha y secuencia.
  No reabre un turno cerrado ni salta un conflicto para forzar una aceptación.
- Preparadores de instalación para Windows y Linux: ver
  `instalacion_integrada_runserver.md`. No incluyen credenciales o identidad de
  este PC. WSL continúa siendo una elección explícita de desarrollo.

Comprobación real de red de esta ampliación: tres conexiones **solo lectura**
a Aiven correctas (1,91 s, 1,47 s, 1,45 s). No prueba estabilidad permanente;
justifica dar más margen que los 2 segundos anteriores. No se modificó Windows
Security, la configuración de red ni el servidor Aiven.

Verificación y activación controlada terminadas en este PC:

- Copia renovada para William Nova (usuario 1), Caja Menor (3), turno 1799.
  El corte verificado del 12/10/2026 02:33 UTC vence el 13/10/2026 02:33 UTC
  (12/10, 21:33 en Colombia), salvo renovación válida posterior. Contiene
  4978 productos, 4969 existencias y las ventas propias autorizadas.
- Respaldo privado verificado antes de aplicar la copia. Hubo una interrupción
  inicial; una nueva descarga de solo lectura y el reintento terminaron bien.
  No se amplió una autorización basándose en la descarga fallida.
- 57 bibliotecas UI empaquetadas localmente y verificadas por SHA-256.
  No se requiere la caché del navegador; se usan fuentes del sistema.
- Arranque habitual Windows → PostgreSQL WSL comprobado: login HTTP 200 y
  JavaScript local HTTP 200 con contenido verificado. Proceso de prueba detenido.
- Ensayo con procesos y bases ficticias: venta online, dos nuevas offline,
  corte real de la nube ficticia, reinicios, UUID repetidos, subida automática,
  exactamente tres ventas finales e inventario negativo correcto.
- Regresión: 244 pruebas Python completas (171 + 73) y una prueba nueva de
  recuperación de formularios, comprobada junto con las 17 anteriores de pagos,
  devoluciones y cierre: **245 casos distintos correctos**. **16 pruebas de
  JavaScript correctas**. `manage.py check` y sintaxis de PowerShell/Bash correctos.
- La revisión ofrece volver al formulario de un registro confirmado o guardado
  localmente sin repetirlo. El acuse libera la referencia de la página original
  incluso cuando la respuesta redirige a la lista de ventas.
- Sesión y conflictos: con conexión se puede renovar la identidad mediante
  el login original aunque exista un conflicto. No renueva por sí solo el
  catálogo, no borra pendientes y no habilita ventas sobre una copia vencida.
  Cerrar sesión sigue disponible. La revisión no fuerza confirmaciones.

No se subieron estos cambios a GitHub ni se desplegaron en PythonAnywhere.
Las pruebas de escritura fueron exclusivamente en bases ficticias. Sigue
pendiente la aceptación física en dos computadores y la impresión.

## Qué añade

El formulario ORIGINAL de Generar Venta conserva sus campos y validaciones.
En el perfil experimental `journal_preview`, antes de intentar la escritura en
nube se confirma una intención durable en PostgreSQL local: UUID, autor, equipo,
secuencia, identidad de la nube y huella criptográfica de la solicitud.

No se guardan el formulario completo, contraseñas, claves de descuento ni tokens
de impresión. No se almacenan instrucciones SQL para reproducirlas después.

Estados relevantes:

| Resultado | Tratamiento |
| --- | --- |
| Nube confirma y local guarda la respuesta | Se devuelve la misma factura ante el mismo UUID. |
| Nube confirma, pero falla guardar la respuesta local | Se conserva la respuesta exitosa al cajero; el trabajador recupera la confirmación posteriormente. |
| Se pierde conexión durante la confirmación | El intento permanece incierto; no se considera una venta offline. |
| Una consulta válida a la nube demuestra ausencia | Se marca como no confirmado; el cajero puede reintentar. No hay reenvío automático. |
| Cambia el carrito de una solicitud incierta/confirmada | Se rechaza reutilizar esa referencia para otro cobro. |
| Cambia el autor/nube o no coincide la huella confirmada | Se conserva el registro y se solicita revisión. |

La consulta a la nube comparte el bloqueo de la confirmación original: no puede
concluir que una venta no existe mientras su transacción todavía se está guardando.
Las consultas de confirmación online son de solo lectura y tienen tiempos acotados.

Un bloqueo por UUID en PostgreSQL local evita carreras entre pestañas, procesos
y el trabajador. No se mantiene una transacción local abierta durante la operación
de red. Un fallo al escribir la intención impide enviar la venta a la nube.

## Recuperación de la interfaz

Si se pierde la respuesta, el formulario puede consultar hasta tres veces el
estado de esa misma referencia por GET. No envía otro POST automáticamente.
Si encuentra la confirmación, utiliza la respuesta de la factura original.
Si sigue pendiente, conserva el carrito y pide no repetir el cobro.

La ruta `/local/ventas/confirmacion/<uuid>/` exige sesión, permiso y el mismo autor,
equipo y nube. Puede leer el acuse local incluso cuando el bloqueo de pendientes
impide otras operaciones. No permite crear, editar ni borrar ventas.

El trabajador se inicia al atender solicitudes del programa; recupera intenciones
persistidas después de un reinicio. No necesita que el cajero vuelva a enviar la
venta. Usa reintentos progresivos y nunca registra credenciales en sus mensajes.

## Alcance y activación

El perfil `journal_preview` ahora también permite **ventas nuevas completamente
offline en el formulario original**. No cambia de URL, programa o pantalla:
el middleware elige una base para toda la solicitud según la salud de PostgreSQL,
no según `navigator.onLine`. Los autocompletes y la verificación de precios usan
la copia autorizada. El POST de verificación permanece en una transacción de solo
lectura; únicamente el adaptador de ventas puede escribir offline.

Una transacción local confirma conjuntamente el evento, el comprobante con
referencia `LOCAL-<UUID>` y el descuento de inventario (incluidos negativos).
No se reutilizan IDs enteros locales como IDs de facturas de la nube. Se conserva
un evento de negocio con autor, equipo, secuencia, fecha, turno, cliente permitido,
productos, cantidades, precios y pagos; no se reproducen formularios ni SQL.
La huella sellada detecta alteraciones del evento antes de enviarlo.

Al reconectar, el trabajador envía los eventos por orden del equipo. Revalida
usuario, permisos, sucursal, caja, turno y medios; reutiliza la transacción de
facturación original y guarda `SolicitudPOS` en el mismo commit. Si se pierde el
acuse, reintenta el mismo UUID y recupera la factura ya creada, sin descontar
inventario ni sumar efectivo otra vez. Conserva los precios y fecha originales.
No exige que siga vigente el plazo offline al sincronizar: exige que la venta
se hubiera creado dentro de ese plazo.

Mientras hay pendientes se pueden seguir registrando carritos independientes
localmente. Solo se vuelve a la nube cuando se confirmó el envío y se renovó
la copia. La renovación periódica es cada 60 segundos; si falla, conserva la copia
anterior sin extender su vencimiento. Un cambio incompatible queda en revisión,
con el comprobante y los importes intactos; no se elimina ni se fuerza el cierre.
El aviso de Generar Venta muestra conexión, pendientes, revisión y las últimas
diez ventas offline con su correspondiente ID de nube cuando fue confirmado.

Admite productos por unidades y gramos, promoción de bolsas, clientes normales
del catálogo autorizado, efectivo y medios manuales activos (incluidos mixtos).
Registrar Nequi manualmente **no verifica la recepción bancaria**. Necesitan
internet: vincular notificaciones Nequi, descuentos de empleados/con clave,
PTM, egresos, devoluciones y cierres de caja. La impresión Linux de comprobantes
offline aún no está integrada; se informa sin volver a cobrar. No se probó
impresión física. La recuperación de carritos sigue siendo una función distinta.

La autorización del catálogo dura dos horas y la copia está vinculada a un
usuario/caja/equipo; todavía no se ofrece relevo local ni instalación definitiva
multiequipo de este nuevo flujo. Una copia antigua sin contexto de turno necesita
renovarse con conexión. No se admite vender offline sin preparar la copia.

Al terminar la fase inicial de pruebas se mantuvo sin activar en la preparación
habitual `database_only` de este PC, sin modificar su configuración privada,
Aiven ni PythonAnywhere. La comprobación
de la migración de confirmaciones `0049_solicitudpos` es necesaria antes de una
activación real supervisada; no se debe cambiar una bandera para omitirla.
El servidor WSGI y el programa sin el perfil experimental conservan su flujo.

Prueba reproducible, exclusivamente con dos clústeres ficticios NUEVOS:

```powershell
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_integrated_runserver.py --pg-bin /usr/lib/postgresql/16/bin --wsl-distribution Ubuntu-22.04 --wsl-user novapos-test --autoreload --journal-preview
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_hybrid_postgres_wsl.py
```

El ensayo real de procesos ya cubre una venta online más dos ventas NUEVAS offline,
la caída real del PostgreSQL ficticio, repetición del mismo UUID, reinicios con la
nube caída y envío/renovación automática. Quedaron exactamente tres ventas en nube
y el inventario pasó de -7 a -13. Los motores ficticios terminaron detenidos.
La batería añade pruebas de rollback local, pérdida de acuse tras commit, precios
modificados, turno cerrado, usuario revocado, evento alterado, cantidades en gramos,
pagos mixtos, formularios originales y transición segura desde una ausencia online
comprobada. No se activa el híbrido real por ejecutar estas pruebas.

Verificación final de esta etapa: **171 pruebas Django correctas (122 + 49)**,
**9 pruebas JavaScript correctas**, comprobación de sintaxis de los scripts del
formulario/estado y `git diff --check` sin errores. El ensayo por procesos se
repitió con el aviso y la consulta de pendientes, también correctamente. No se
realizó todavía una prueba de este nuevo flujo en dos computadores físicos.

## Comprobación previa de la activación real

Antes de descargar la copia autorizada y cambiar la activación privada, ejecutar:

```powershell
python manage.py comprobar_activacion_hibrida --usuario "William Nova" --caja "Caja menor"
```

El comando **no activa**, no modifica la nube, no ejecuta migraciones, no abre
turnos y no guarda ventas. Usa una conexión nueva con tiempos acotados y una
transacción PostgreSQL `REPEATABLE READ, READ ONLY`. Comprueba la migración
`0049_solicitudpos`, las columnas y permisos de la tabla de confirmaciones,
la identidad exacta y sus permisos actuales, la sucursal de la caja, el turno
si es obligatorio y la existencia de medios de pago activos. Los nombres
ambiguos no se eligen automáticamente: admite `--usuario-id` y `--caja-id`.
Los errores del controlador se traducen sin publicar contraseñas ni cadenas
de conexión.

Con `--comprobar-motor` también arranca y comprueba el motor privado mediante
el diagnóstico existente; sus pruebas de escritura son temporales y se
revierten. Al terminar detiene únicamente el motor que inició. Este diagnóstico
no sustituye la revisión/respaldo de la copia local antes de activarla.

Estado de la comprobación del 10 de octubre de 2026: selección solicitada
**William Nova / Caja menor**, pero aún sin resolver sus IDs en la nube.
La conexión nueva desde Django y la prueba TCP independiente de PowerShell
agotaron el tiempo de espera hacia el host configurado, puerto 24553. DNS
resolvió el servidor; no se observó una entrada que lo sustituyera en `hosts`.
La conexión guardada de pgAdmin denominada `merk2` coincide en host/puerto y
no usa túnel SSH; abrir pgAdmin por sí solo no demuestra una conexión SQL nueva.
No se atribuye el fallo a Smart App Control, Defender o al filtro de Aiven sin
evidencia. La configuración habitual sigue en `database_only`, sin copia real
descargada ni despliegue a PythonAnywhere.

La batería PostgreSQL WSL pasó **185 pruebas (122 + 63)**, incluidas 14 nuevas
pruebas de comprobación previa: selección inequívoca, sucursal, permisos,
migración ausente, turno, protección contra escrituras accidentales y mensajes
sin secretos. Todas usaron bases ficticias aisladas; su motor quedó detenido.

Actualización posterior del mismo día: tras el ajuste comunicado por el usuario,
una prueba TCP de Windows y una conexión SQL nueva desde Django sí respondieron.
La comprobación se detuvo correctamente porque falta `solicitudes_pos`.
`migrate mainApp 0049 --plan` mostró pendientes las migraciones 0044 a 0049;
solo se consultó el plan, no se ejecutaron. Añaden las tablas de soporte del
híbrido y de confirmaciones, sin operaciones de borrado o modificación de
importes de ventas existentes. Como la base es compartida con producción,
se requiere acordar respaldo y autorización para aplicar ese cambio de esquema
antes de descargar la copia y activar el equipo. El modo habitual permanece
en `database_only`.

### Respaldo y migración supervisada

La modificación del esquema compartido requiere autorización explícita. El
procedimiento se divide en dos scripts para que la descarga no pueda migrar por
accidente:

```powershell
python scripts/backup_configured_database.py --pg-bin "C:/Program Files/PostgreSQL/18/bin" --expect-host merk-888-merk888.c.aivencloud.com --expect-port 24553 --expect-name defaultdb
python scripts/migrate_hybrid_with_backup.py --backup-manifest "RUTA_PRIVADA_DEL_RESPALDO/manifest.json"
# Solo tras revisar el plan y con autorización:
python scripts/migrate_hybrid_with_backup.py --backup-manifest "RUTA_PRIVADA_DEL_RESPALDO/manifest.json" --apply
```

El respaldo completo se guarda fuera del repositorio y de OneDrive, en una
carpeta privada bajo `%LOCALAPPDATA%/NovaPOS/backups/aiven/`. No incluye
contraseñas de conexión en los argumentos ni en el manifiesto. El archivo sí
contiene datos del negocio: no compartirlo ni subirlo a Git. Se verifica el
catálogo del archivo, su decodificación completa con `pg_restore` sin conectar
a ninguna base, y su SHA-256. Esto comprueba la lectura del archivo, **no equivale
a un ensayo completo de restauración**. Un respaldo parcial no habilita migrar.

El segundo script exige respaldo de menos de dos horas, destino y SHA-256
coincidentes. Rechaza retrocesos, otras aplicaciones, otros números de migración,
operaciones de datos y planes distintos del tramo autorizado 0044–0049. Limita
la espera por bloqueos a cinco segundos y cada sentencia a un minuto. No cambia
la activación privada, no crea ventas y no abre turnos. Conserva un comprobante
de las migraciones aplicadas junto al respaldo. Las diez pruebas adicionales de
estos controles se ejecutan sin conexiones a producción.

### Resultado del respaldo y migraciones autorizadas (10 de octubre de 2026)

- El primer respaldo agotó su límite de 15 minutos. Se conservó como parcial
  y **no se usó para migrar**. El segundo respaldo terminó correctamente.
- Respaldo verificado: `C:/Users/LENOVO/AppData/Local/NovaPOS/backups/aiven/20261011T040113Z-83d3a50d/database.dump`.
  La carpeta usa hora UTC (en Colombia todavía era 10 de octubre).
- Tamaño: 10 476 050 bytes. SHA-256:
  `340aeef94dee7aa77d97dc94dff4a2f2478b4a6ad309abf4d8c1547b5f0b06c7`.
  `manifest.json` registra la lectura completa validada; no se hizo un ensayo
  completo de restauración. El respaldo incluye datos sensibles del negocio
  y permanece fuera del repositorio con acceso privado.
- Con autorización expresa se aplicaron en Aiven las seis migraciones 0044–0049.
  Todas devolvieron `OK`. `migration-result.json`, junto al respaldo, conserva
  el listado y la huella del respaldo usado.
- La consulta posterior `migrate mainApp 0049 --plan` devolvió que no hay
  operaciones pendientes, y `manage.py check` no reportó problemas.
- Las diez pruebas del control de respaldo/plan volvieron a pasar. No se
  registraron ventas de prueba, no se desplegó código en PythonAnywhere y no se
  cambiaron protecciones de Windows ni el servicio PostgreSQL nativo.
- Identidad confirmada: William Nova, usuario 1; Caja Menor, punto 3; Yerbabuena.
  En ese momento, el control posterior validó el nuevo esquema y la identidad, pero detuvo
  la activación porque no hay un turno abierto para ese usuario/caja y la
  configuración del negocio sí exige turno. No se abrió ni cerró ninguno.
- Al finalizar esas migraciones, el equipo seguía en `database_only`, **sin ventas offline activadas** y sin
  haber descargado la copia real autorizada. Siguiente paso: abrir el turno
  desde el flujo normal, volver a comprobar, respaldar/revisar la instalación
  local y completar la copia/activación supervisada. No basta con cambiar una
  bandera en el archivo privado.

### Activación controlada de la instalación habitual

Después de abrir el turno desde el sistema, confirmar sus IDs y detener cualquier
`runserver` de este mismo proyecto:

```powershell
# Sin --apply solo se consultan los requisitos de nube.
python scripts/activate_integrated.py --pg-bin "C:/Program Files/PostgreSQL/18/bin" --usuario-id 1 --caja-id 3 --turno-id 1799
# Solo para la vinculación y turno expresamente autorizados:
python scripts/activate_integrated.py --pg-bin "C:/Program Files/PostgreSQL/18/bin" --usuario-id 1 --caja-id 3 --turno-id 1799 --apply
```

El número de turno del ejemplo corresponde a la activación inicial de William
Nova; no reutilizarlo para otros turnos. El script comprueba que continúa abierto,
toma el bloqueo exclusivo de la instalación, arranca únicamente el motor privado
y respalda la base local completa y su configuración en
`%LOCALAPPDATA%/NovaPOS/integrated/<proyecto>/backups/`. La carpeta contiene
información privada y claves locales: no subirla al repositorio ni compartirla.
Los clientes `pg_dump`/`pg_restore` de Windows no ejecutan el servidor nativo ni
su `plpgsql.dll`; el servidor sigue siendo PostgreSQL Linux dentro de WSL.

Después descarga en una transacción de solo lectura la identidad, permisos,
catálogo, inventario y autorización del turno elegidos. Rechaza otra vinculación,
datos locales ajenos y operaciones pendientes. Verifica la huella de la copia,
su vigencia y la pantalla original de ventas con ambas bases mediante consultas
de solo lectura. Solo entonces cambia atómicamente la configuración privada a
`journal_preview`. No restaura bases, no migra, no factura, no cierra turnos y no
despliega en PythonAnywhere. El motor que inició se detiene al finalizar.

En el siguiente `python manage.py runserver`, el propio arranque de Django inicia
WSL/PostgreSQL y utiliza la configuración privada. La interfaz y URL de Generar
Venta son las habituales. El estado distingue nube, guardado local, envío y
operaciones que necesitan revisión. No cortar internet para una venta ficticia
en esta copia: al reconectar se enviaría a la base real.

La activación deja `.novapos-runtime.json`, excluido de Git, con **solo la ruta**
física de la configuración, sin contraseñas. Evita que la redirección de AppData
de un proceso Windows empaquetado haga que una terminal normal busque otra
instalación. No copiar ese archivo a otros equipos: cada uno requiere preparación
y autorización propias. `NOVA_INTEGRATED_CONFIG` explícito sigue teniendo prioridad.

Límites de esta primera activación: un usuario/caja por equipo; copia autorizada
por dos horas desde su última renovación; inventario negativo permitido. Egresos,
devoluciones, cierre, PTM, notificaciones Nequi vinculadas y autorizaciones
especiales aún necesitan conexión. Una venta local con un turno que fue cerrado
en la nube se conserva para revisión, no se elimina ni se fuerza su registro.

### Resultado de la activación y comprobación posterior

- Con el turno **1799** abierto por el usuario se completó la activación de
  **William Nova (1) / Caja Menor (3) / Yerbabuena**. Configuración privada:
  `journal_preview`; no se desplegó código en PythonAnywhere.
- Antes se respaldó la base local y su configuración. Respaldo verificado en
  `C:/Users/LENOVO/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/NovaPOS/integrated/608533b65b95aed1/backups/20261011T042112Z-c0b51f56/`.
  Su `activation-result.json` registra la verificación de las dos vistas.
- La descarga incluyó 1 sucursal, 1 caja, 44 categorías, 21 clientes, 5 medios
  de pago, 4978 productos y 4969 filas de inventario. Se validó el manifiesto,
  la identidad, el turno y el renderizado del formulario original online/offline.
  No se crearon ventas reales. Vigencia inicial hasta **11/10/2026 01:21:28,
  hora de Colombia** (06:21:28 UTC).
- Pasaron 207 pruebas de la batería aislada (134 + 73), 14 pruebas enfocadas
  de activación/interfaz y 18 de arranque/ubicación. Estos grupos se solapan;
  no se suman como pruebas distintas. También pasó la prueba con procesos
  reales y datos ficticios: venta online, dos ventas nuevas offline, reinicios,
  reintentos con el mismo UUID, envío automático sin duplicados e inventario
  negativo exacto. Las pruebas de impresión no se realizaron.
- Al retomar el **11/10/2026 a las 11:12, hora de Colombia**, el comando habitual
  `python manage.py runserver 127.0.0.1:8000` arrancó correctamente con Python
  3.12.10, Django 5.1.1 y `NovaSoft.integrated_settings`. PostgreSQL Linux 16.15,
  PL/pgSQL y lectura/escritura temporal con rollback también funcionaron.
- Sin embargo, Aiven volvió a agotar el tiempo de conexión desde ambos Python
  de Windows. DNS resolvió a `209.38.76.158`; TCP al puerto 24553 no conectó
  ni desde Windows ni desde WSL. La copia inicial ya había vencido y GET `/`
  devolvió 503 con el aviso de autorización expirada. **La configuración está
  activada, pero la prueba final operativa queda pendiente de recuperar el
  acceso a Aiven y renovar la copia.** No se extendió la autorización vencida
  ni se atribuyó el fallo a una protección de Windows sin evidencia.
- Se detuvieron el runserver temporal y su motor privado; puertos 8000 y
  55460 liberados. El servicio nativo en 5432 quedó intacto. No se modificaron
  SAC, Defender, firewall, contraseñas ni registros de negocio en esta activación.

### Renovación del 11/10/2026, 15:15 (Colombia)

- Aiven volvió a aceptar consultas nuevas y confirmó William Nova (1), Caja
  Menor (3), Yerbabuena y el turno 1799. Un primer intento de renovación agotó
  el tiempo de conexión antes de tocar la copia; el siguiente terminó bien.
- Se creó y verificó otro respaldo local en la subcarpeta
  `backups/20261011T201530Z-1e3a203f/` de la misma instalación privada.
- Se renovó la copia autorizada: 4978 productos, 4969 registros de inventario,
  44 categorías, 22 clientes, 5 medios de pago, 1 sucursal y 1 caja.
  Vigencia confirmada hasta **11/10/2026 17:15:51 (Colombia)**; se renueva
  automáticamente con el programa activo cuando Aiven permite la actualización.
- El renderizado original de Generar Venta pasó en ambas bases, en solo
  lectura. `python manage.py runserver` arrancó con la configuración integrada
  sin errores de sistema. Cuatro consultas HTTP a `/` devolvieron **200** y
  mostraron el formulario de acceso, sin aviso de autorización vencida.
- Esas cuatro respuestas HTTP fueron en modo **offline**, mientras los sondeos
  cortos y algunos intentos del sincronizador fallaban. Otra comprobación SQL
  posterior sí confirmó nuevamente el turno. Por tanto **la copia está renovada
  y el modo local disponible, pero no se considera estable la conexión a Aiven**.
  No se desconectó la red intencionalmente ni se crearon ventas reales de prueba.
- Al terminar se detuvo el servidor temporal y su PostgreSQL privado. El usuario
  puede arrancar su comando habitual; no necesita abrir otra aplicación para
  usar la copia local. Se mantienen los límites de dos horas sin renovación y
  las funciones offline soportadas descritas arriba.
