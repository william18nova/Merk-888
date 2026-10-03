# POS híbrido: pagos, devoluciones, cierre e instalación persistente

Estado: candidato de pruebas con relevo de cajeros y validación nativa Windows,
3 de octubre de 2026. No desplegado a producción.
No se modificaron Aiven ni PythonAnywhere y no se hicieron pruebas de impresión.

## Alcance de esta entrega

La entrada habitual será el Django instalado en cada equipo. Se conserva Generar
venta, su carrito y su modal de pago. El servidor intenta primero confirmar la
operación en la nube; si falla la comunicación conserva el pendiente local. No
se cambia de página ni se abre otra aplicación al perder internet. Abrir solo la
web de PythonAnywhere no instala ni activa esta capacidad por sí mismo.

| Operación | Con conexión | Sin conexión |
| --- | --- | --- |
| Venta en efectivo, Nequi, tarjeta/Caja Social, otro medio activo o mixta | Confirma en la nube con UUID único | Guarda pendiente y descuenta el inventario local, incluidos negativos |
| Pago/egreso | Conserva concepto en mayúsculas, autor, fecha, medio, base e impuesto 4 × 1.000 | Guarda esos mismos datos y la regla de impuesto que se confirmó |
| Devolución | Valida permiso, venta, cantidades y reintegro; registra una sola vez | Guarda **solicitud pendiente**, únicamente si esa venta fue consultada previamente en el equipo |
| Cierre | Concilia primero toda la cola y aplica el cierre original | Guarda **declaración pendiente**, bloquea movimientos nuevos y espera confirmación |

Nequi y tarjeta son medios declarados por el cajero: el adaptador no confirma
movimientos bancarios, no enlaza notificaciones y no hace transferencias.
No entregar un reintegro hasta ver la devolución confirmada. El reintegro
bancario se realiza fuera del POS.

Los egresos siguen siendo un control separado, sin restar del cajón ni modificar
turnos. Sus métricas globales aparecen al sincronizar. El historial local
no se presenta como total actualizado de todo el negocio.

El cierre tiene tres páginas consecutivas: facturas/compañeros, conteo de
efectivo y otros medios/PTM. No permite volver a cambiar un paso confirmado,
ni recargando ni desde otra pestaña. PTM tiene valor inicial 0; el servidor
mantiene la validación de su conteo, pero crear operaciones PTM offline aún no
está habilitado.

## Garantías y límites

- Un diario PostgreSQL, UUID por operación y secuencia por sesión compartida
  entre ventas, egresos, devoluciones y cierre. No se reproducen SQL arbitrarios.
- Perder una respuesta o reintentar no crea otra venta, egreso o devolución.
- El cierre se envía después de los movimientos anteriores. Un rechazo no se
  confunde con falta de internet; se conserva para revisión y bloquea la cola.
- Dos solicitudes sobre las mismas unidades devueltas se validan bloqueando la
  venta en el servidor. No se devuelve dinero dos veces por solicitudes offline.
- Los permisos se comprueban localmente y otra vez en la nube. El dispositivo
  tiene autorización limitada a usuario, sucursal y punto de pago.
- Se conserva el límite offline de la copia autorizada: hasta dos horas desde
  su última renovación, sin superar la vigencia de la sesión. Reiniciar no lo
  amplía. No es operación offline indefinida.
- Nuevo turno o cajero: requiere cierre anterior confirmado, cola completamente
  aceptada y turno siguiente abierto en la nube. `next-session` renueva al mismo
  usuario; `switch-user` cambia de cajero con autenticación en línea, cuentas
  locales separadas y registro del relevo. Todavía no hay asistente web de relevo.
- Una venta solo local pendiente todavía no tiene ID remoto para devolverla.
  Una venta nunca consultada no se inventa cuando no hay conexión.
- Otras escrituras, PTM, vinculación de Nequi, clientes asociados a la venta,
  descuentos especiales y resolución administrativa de conflictos siguen fuera
  de este adaptador. Las pantallas no habilitadas se bloquean expresamente.

## Instalador persistente candidato

La actualización con respaldo y recuperación se describe en
[Actualizaciones del POS local](pos_hibrido_actualizaciones.md). No sustituir el
paquete ni los archivos de una instalación existente mediante copia manual.

No es aún un instalador definitivo firmado. Hay scripts para Windows y Linux,
un servidor Waitress en `127.0.0.1`, clúster PostgreSQL privado, identidad estable,
vinculación explícita, respaldo y restauración. Al arrancar no se ejecuta
`initdb`, no se vuelve a sembrar información y no se reemplaza la base existente.

Requisitos previos: Python 3.10+ y herramientas de PostgreSQL. Se probó PostgreSQL
16 en Linux/WSL y 18.6 en Windows. No se incluye
un servicio de PostgreSQL compartido ni se modifica el que ya tenga el equipo.
La instalación se hace con un usuario normal, no con root. Python/dependencias
se mantienen en un entorno privado. Los datos deben estar en disco local, fuera
del repositorio, OneDrive, Dropbox o una carpeta de red.

La primera instalación requiere internet para descargar dependencias, salvo que
se entregue un `wheelhouse` preparado para ese sistema y versión de Python.
Los 57 recursos de interfaz se incluyen en el paquete y no requieren CDN al usarlo.

### Preparar una distribución

Desde el repositorio, usando Python con acceso a la caché de recursos ya preparada:

```text
python -B scripts/build_full_local.py build/hybrid/full-local-candidate-NUEVO
```

El destino debe ser nuevo. No distribuir el repositorio completo: el constructor
excluye settings productivos, bases, archivos privados, cachés y pruebas. Genera
un manifiesto SHA-256 para detectar archivos mezclados. **Esto no sustituye una
firma digital ni una auditoría de dependencias/secretos.**

### Windows

Ejemplo desde la carpeta del paquete (ajusta `PgBin` a la instalación real):

```powershell
.\scripts\install_full_local.ps1 `
  -DataDir "$env:LOCALAPPDATA\NovaPOS-Piloto\datos" `
  -PgBin "C:\Program Files\PostgreSQL\18\bin" `
  -Username "operador_local"
```

Solicita una contraseña LOCAL de al menos 12 caracteres. No escribir contraseñas
en comandos ni guardarlas en Git. Si Windows bloquea scripts o ejecutables,
revisar la política con el administrador: no desactivar AppControl/antivirus ni
renombrar ejecutables para eludir el bloqueo.

En este PC la instalación de PostgreSQL 18.6 en `C:\Program Files\PostgreSQL\18`
ya permite crear y arrancar el clúster privado con el usuario normal de Windows.
No se usa, detiene ni modifica el servicio PostgreSQL del instalador. Tampoco se
necesita su contraseña: el POS genera credenciales propias para su base aislada.
Las herramientas no pudieron arrancar dentro del usuario restringido del entorno
de pruebas; la misma prueba sí funcionó con el usuario normal, sin modificar
protecciones de Windows. No ejecutar como administrador para el uso habitual.

Elegir otra versión de PostgreSQL solo al crear una instalación nueva. Nunca
abrir directamente un clúster de la versión 16 con herramientas de la 18:
el runtime lo rechaza y requiere una actualización supervisada.

Arranques posteriores, **sin reinstalar**:

```powershell
.\scripts\start_full_local.ps1 -DataDir "$env:LOCALAPPDATA\NovaPOS-Piloto\datos"
```

### Linux

Desde el paquete y con un usuario normal:

```bash
bash scripts/install_full_local.sh "$HOME/.local/share/nova-pos-piloto" \
  /usr/lib/postgresql/16/bin operador_local
bash scripts/start_full_local.sh "$HOME/.local/share/nova-pos-piloto"
```

La URL predeterminada es `http://127.0.0.1:8910/local/estado/`. El puerto
PostgreSQL es 55441. No se publican estos puertos en internet. Si ya están
ocupados, el runtime se detiene sin usar ni reemplazar el servidor existente.

### Vinculación y turno siguiente

Solo después de validar el piloto y preparar el servidor con sus migraciones y
funcionalidad híbrida habilitada. No ejecutar contra el negocio durante las
pruebas. Detener antes el runtime. Usar el Python del paquete:

- Windows: `.\.venv\Scripts\python.exe`
- Linux: `./.venv/bin/python`

Con ese ejecutable, la sintaxis es:

```text
-m local_pos.runtime --data-dir RUTA_DATOS pair --url https://SERVIDOR-DE-PRUEBAS --username USUARIO_NUBE
-m local_pos.runtime --data-dir RUTA_DATOS next-session --username MISMO_USUARIO_NUBE
```

`pair` solicita código de un solo uso del Web Master y contraseña remota de
forma oculta. No guarda la contraseña remota. `next-session` no reemplaza la
identidad ni abandona pendientes y necesita que el siguiente turno ya esté abierto.

### Relevo de cajeros en el mismo equipo

Este procedimiento corresponde al paquete nuevo que incluye `switch-user`.
No mezclarlo con una instalación de un candidato anterior: las nuevas tablas
locales requieren ejecutar el actualizador aprobado antes de abrir el paquete nuevo.
No borrar la instalación anterior ni cambiar su manifiesto para forzar el arranque.

1. El cajero saliente termina su cierre y comprueba que esté **confirmado** y
   que no queden operaciones pendientes o en revisión.
2. Con internet, el siguiente cajero abre su turno por el flujo habitual de la
   nube, en el mismo punto de pago y sucursal.
3. Se detiene el POS local con Ctrl+C y se espera a que cierre PostgreSQL.
4. Desde la carpeta del paquete se ejecuta el lanzador correspondiente:

   ```powershell
   .\scripts\switch_full_local.ps1 -DataDir "$env:LOCALAPPDATA\NovaPOS-Piloto\datos" -Username "USUARIO_NUBE"
   ```

   ```bash
   bash scripts/switch_full_local.sh "$HOME/.local/share/nova-pos-piloto" USUARIO_NUBE
   ```

5. Se ingresa la contraseña de la nube y se elige una contraseña LOCAL distinta,
   de al menos 12 caracteres. Ambas se piden ocultas; la contraseña remota no se guarda.
6. Se espera la descarga verificada de la autorización del nuevo cajero. El
   comando muestra su nombre local. Se arranca con `start_full_local` y se inicia
   sesión con ese nombre y la contraseña local.

Cada cajero conserva su identidad: las ventas y operaciones anteriores no
cambian de autor. La cuenta local saliente queda inactiva y la entrante recibe
solo los permisos autorizados para esa sesión, sin heredar el rol administrador
del cajero anterior. Si un cajero regresa, se reutiliza su identidad local,
pero se renueva su contraseña para invalidar sesiones de navegador antiguas.
El historial y el inventario, incluidos negativos, se conservan.

Si se interrumpe el relevo, **no eliminar archivos ni repetir la vinculación**.
Repetir el mismo lanzador con el mismo usuario; reutiliza la referencia de la
transición. El POS no arranca mientras la transición esté incompleta. En un
reintento posterior a la creación de la cuenta se conserva la contraseña local
elegida en el primer intento. Un cambio de usuario durante el reintento se
rechaza: requiere revisión, no un borrado manual. El relevo no funciona sin conexión.

La migración nueva del servidor es `0048_hybrid_business_operations`, después de
0047. Se verificó en una base aislada, no se aplicó a producción. No ejecutar
las migraciones deshabilitadas del laboratorio sobre una base del negocio.

### Respaldo y restauración

Detener completamente el POS. Con el Python privado:

```text
-m local_pos.runtime --data-dir RUTA_DATOS backup --output CARPETA_PRIVADA_NUEVA/respaldo.zip
-m local_pos.runtime --data-dir RUTA_DATOS status
-m local_pos.runtime --data-dir RUTA_DATOS restore --backup CARPETA_PRIVADA/respaldo.zip --confirm-instance UUID_ORIGINAL
```

El ZIP **contiene credenciales y no está cifrado**. Guardarlo únicamente en una
carpeta privada dedicada, no enviarlo por chat ni subirlo al repositorio.
La restauración exige la misma ruta original, sistema, versión e identidad;
verifica integridad e incluye directorios vacíos de PostgreSQL. Nunca sobrescribe
una instalación existente. La copia antigua debe permanecer detenida: dos
equipos no deben ejecutar la misma identidad restaurada.

Para detener, usar Ctrl+C y esperar el cierre de PostgreSQL. Un respaldo físico
entre Windows y Linux no es portable. No copiar un clúster activo ni borrar la
instalación para intentar resolver un error.

## Verificación

- 111 pruebas PostgreSQL correctas tanto en Linux/WSL (PostgreSQL 16) como en
  Windows nativo (PostgreSQL 18.6): protocolo, dos receptores, negativos,
  permisos, pérdida de respuestas, pagos mixtos, impuesto, devolución, cierre y
  seis casos de relevo (autores/permisos, cierre, pendientes, reintento, rollback
  y regreso de un cajero anterior), más tres comprobaciones de limpieza segura.
- 45 pruebas del cliente/página original correctas.
- 3 pruebas de archivos del runtime correctas en Windows: carpetas vacías,
  rechazo de rutas inseguras antes de extraer y bloqueo de arranque si hay una
  transición de turno o cajero incompleta.
- Recorrido Edge de escritorio y móvil: venta no efectivo, egreso y devolución
  pendientes, cierre en tres pasos y sincronización ordenada. Sin errores de
  JavaScript, solicitudes a servicios externos ni impresión.
- Instalación candidata `full-local-candidate-20261002-e` (incluye el relevo): instalación nueva,
  dos reinicios y respaldo/restauración completos correctos en Linux/WSL.
  Conservó la misma identidad y una operación pendiente. Se corrigió y verificó
  la conservación de los directorios vacíos requeridos por PostgreSQL. El paquete
  es inmutable: este resultado se documentó después de construirlo, sin editar
  los archivos ni el manifiesto dentro de la distribución ya verificada.
- Con la instalación nueva de PostgreSQL 18.6, el candidato `20261002-e` también
  pasó en Windows nativo la creación de base privada, dos arranques/paradas,
  respaldo y restauración manteniendo identidad y pendientes. Se verificó que
  la conexión SQL apuntaba a `127.0.0.1:55446`, no al servicio instalado ni a la nube.
  La prueba equivalente se repitió y pasó en Linux/WSL con PostgreSQL 16.
  Las 48 pruebas del cliente y de archivos también pasaron con el nuevo Python
  privado de Windows. Las pruebas no modificaron la seguridad del equipo.
  La suite nativa completó sus 111 casos en 242,259 segundos y terminó con código
  0, incluidas la limpieza de las tres bases ficticias y la parada del clúster.
  La suite equivalente pasó en Linux/WSL en 142,496 segundos, también con código 0.
  No se accedió a Aiven ni a PythonAnywhere ni se imprimió.

Durante la primera pasada Windows aprobó las 108 pruebas de negocio, pero el
checkpoint necesario para borrar la base de prueba tardó 26 segundos y excedió
el límite de consulta de 15 segundos. Se corrigió el ejecutor **solo de pruebas**
para conceder hasta 120 segundos a esa limpieza. Los límites de las consultas
del POS y de los casos de prueba no se modificaron. El ejecutor verifica que sean
bases ficticias locales, restaura las opciones incluso ante errores y no forma
parte de la distribución instalada.

### Actualización segura verificada

El candidato `full-local-candidate-20261003-c` pasó la actualización desde el
esquema anterior, recuperación automática ante fallo y retroceso controlado con
PostgreSQL 18.6 en Windows nativo y PostgreSQL 16 en Linux/WSL. Conservó identidad,
autores, pendientes e inventario negativo; rechazó restaurar encima de información
nueva. Las 59 pruebas de cliente/archivos/actualizador pasaron en ambos sistemas.
Son pruebas ficticias, sin nube de producción ni impresión. Consultar el
[procedimiento y registro de verificación](pos_hibrido_actualizaciones.md).

### Prueba lista en este PC

- Estado: `http://127.0.0.1:8914/local/estado/`.
- Usuario ficticio: `laboratorio`.
- Contraseña ficticia: `prueba-local-2026`.
- Abre Generar venta desde allí; la pestaña antigua de 8906 es la etapa anterior.
- Ese laboratorio fue iniciado antes del relevo y no es la instalación nueva
  del candidato. Las pruebas de relevo se ejecutaron en bases ficticias separadas;
  no se cambió su esquema mientras estaba abierto.
- En Estado puedes simular el corte del origen ficticio, registrar operaciones
  y restablecer la comunicación. No apaga el Wi-Fi ni consulta datos reales.
- Prueba el cierre al final: después de declararlo no podrás registrar nuevos
  movimientos en ese turno. La demostración crea datos nuevos al lanzarla otra
  vez; solo el runtime instalado es persistente entre arranques.

Comandos reproducibles, siempre con datos ficticios:

```bash
python -B scripts/full_local_lab.py --test --business-tests --port 8912 --pg-port 55442
python -B -m unittest hybrid_client.test_client hybrid_client.test_sale_page local_pos.test_runtime_files
python -B scripts/smoke_full_local_runtime.py RUTA_DEL_PAQUETE
```

El primer y tercer comando usan PostgreSQL 16 de Linux/WSL por defecto.
En Windows indicar explícitamente la instalación autorizada (usar el Python
privado que tenga instaladas las dependencias):

```powershell
python -B scripts/full_local_lab.py --test --business-tests --pg-bin "C:\Program Files\PostgreSQL\18\bin" --port 8921 --pg-port 55447
python -B scripts/smoke_full_local_runtime.py build/hybrid/full-local-candidate-20261002-e --pg-bin "C:\Program Files\PostgreSQL\18\bin" --port 8920 --pg-port 55446
```

El segundo comando de la lista inicial no necesita PostgreSQL. El smoke crea
una instalación ficticia, la reinicia dos veces, respalda/restaura y comprueba
identidad y pendientes. No prueba un corte de energía físico. La demostración
`full_local_lab.py --serve` sigue requiriendo Linux/WSL; no confundirla con el
runtime instalado, que ya se probó de forma nativa en ambos sistemas.

## Pendiente antes de distribuir como definitivo

La [pantalla local de sincronización y revisión](pos_hibrido_revision.md) ya
permite consultar y reintentar sin duplicar ni modificar movimientos. No incluye
aprobaciones o cancelaciones compensatorias en la nube para conflictos irreconciliables.

Validar el asistente de instalación en equipos Windows nuevos, ampliar las migraciones
locales aprobadas para futuros esquemas, asistente web de relevo, conciliación administrativa de conflictos en la nube,
firma del instalador y dependencias, despliegue controlado del servidor y prueba
piloto en los equipos reales. Las pruebas de laboratorio no certifican por sí
solas una instalación lista para facturar dinero real.
