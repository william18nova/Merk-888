# POS híbrido · piloto 0.6.0 en efectivo

## Generar Venta original (0.6.0)

La integración actual reutiliza las plantillas, CSS, carrito, autocomplete y modal
originales en `/generar_venta/`, servidos por el motor local. No se cambia de página
al perder conexión. El acceso está en «Abrir Generar Venta original» desde el panel
local. Consulta [alcance y pruebas](pos_hibrido_generar_venta_original.md).

También recupera carritos no cobrados con el gestor original. Antes de recuperar
un cobro incierto consulta el diario local para impedir que se vuelva a cobrar.
Todavía es efectivo sin cliente asociado; no todo el sistema funciona offline.
La web publicada y el acceso directo instalado no se actualizaron en esta etapa.

## Interfaz de diagnóstico anterior (0.5.0)

El cliente instalado adopta el diseño de «Generar venta»: dos paneles, sucursal
y caja asignadas, búsqueda por nombre/ID y por barras separadas, cantidades en
gramos/unidades, carrito con cantidades editables, subtotales y filtro, y un
modal para recibir el efectivo y mostrar el cambio. No necesita fuentes,
bibliotecas ni archivos de internet para dibujar la pantalla.

«Generar venta» o Alt+Espacio abre el pago; Enter dentro del pago confirma.
Enter en el lector solo agrega el código exacto al carrito. Ctrl+1, Ctrl+2 y
Ctrl+4 enfocan nombre, barras y cantidad. El historial es desplegable y el
estado indica conectado, pendientes sin conexión o sincronizando.

Esta es una adaptación del **cliente híbrido**, no una sustitución de la web
ni una ampliación de los medios de pago. Sigue siendo un piloto en efectivo,
sin cámara, clientes asociados ni recuperación de carritos aún no cobrados.
El contrato de sincronización, las reglas de cobro y el esquema SQLite no
cambian. Los datos de 0.4.1 se conservan al usar 0.5.0.

## Estado y alcance

Este cambio **no convierte todavía todo el proyecto en una aplicación offline**. Añade un cliente ligero instalado en cada equipo y una API en Django, desactivada por defecto. La web habitual continúa igual. No se conecta el cliente directamente a Aiven ni se distribuyen sus credenciales.

Esta primera etapa permite:

- Vincular un equipo Windows o Linux a una sucursal/punto de pago autorizado por Web Master.
- Iniciar sesión **con internet**, usando el usuario del POS y, cuando se exige, su turno abierto. La autorización local dura 12 horas; no se renueva sin conexión.
- Descargar el catálogo de productos, códigos, precios autorizados y stock informativo. Actualizar diferencias cuando existe conexión (cada 20 segundos con la aplicación abierta; no es replicación instantánea).
- Buscar por nombre, ID o barras, vender en efectivo, ingresar cantidades enteras/unidades o **gramos**. Se permiten stocks cero y negativos.
- Aplicar la misma regla de bolsas y redondeo que la venta web. Mostrar recibido y cambio.
- Guardar la operación en SQLite antes del intento de red. Con conexión, confirmar en la nube; sin comunicación o ante un fallo recuperable, conservarla pendiente en ese PC.
- Sincronizar en orden por equipo, con un UUID por venta, sin volver a descontar inventario si se reenvía. Entre equipos se suman movimientos; no se reemplaza el stock completo ni se usa la hora del PC como orden global.
- Consultar/reimprimir las últimas 30 operaciones de la sesión. El historial de operaciones se conserva en la base local, incluidas las aceptadas.
- Finalizar la sesión solo después de confirmar todos los pendientes. Si está asociada a un turno, el cierre de caja queda bloqueado mientras la sesión no se haya liberado, incluso si venció.

No incluye todavía: Nequi/tarjeta/PTM, pagos a proveedores, devoluciones, descuentos especiales o de empleado, clientes con condiciones particulares, apertura offline, cierre definitivo offline, turnos/calendarios offline, modificación offline de productos ni auditoría de carritos vaciados. Esas funciones siguen en la web. Los permisos siguen comprobándose al recibir operaciones.

La versión 0.2 añade **impresión local**, además del diálogo del navegador: agente existente del POS (Windows/Linux) o CUPS (Linux), papel de 58/80 mm, corte y apertura del cajón opcionales. La compatibilidad física depende del agente/controlador y de la impresora. No constituye una integración nueva con facturación electrónica.

La versión 0.3 añadió **respaldo cifrado descargable**, verificación y recuperación en una carpeta de revisión. La 0.4 añade **reactivación autorizada por Web Master**: retiro de credenciales anteriores, revisión del resumen, conciliación atómica y una instalación nueva que exige iniciar sesión. Extraer una copia por sí solo sigue sin habilitar una caja ni sustituir datos actuales.

## Arquitectura y protección de datos

```text
Navegador de la caja → NovaPOS en 127.0.0.1 → SQLite de ese equipo
                               │
                               └─ HTTPS → Django → PostgreSQL/Aiven
```

El pequeño diario local existe también al estar conectado para poder reintentar con seguridad si la nube guarda la venta pero se pierde su respuesta. Esa venta **no se vuelve a registrar manualmente en la web**.

La transacción de Django agrupa venta, detalles, pagos, delta de inventario, saldo de caja y recibo idempotente. La secuencia pertenece a una sesión/equipo; los UUID evitan choques entre cajas. La fecha local sirve como fecha de ocurrencia y se valida contra la autorización; no decide qué cambio sobrescribe otro.

Precios: la nube firma los precios del catálogo para esa sesión. Durante un corte se respetan los precios descargados. Si un producto fue retirado, cambió a PTM o se retiraron permisos, el pendiente se conserva **para revisión**, sin confirmarlo como válido ni borrarlo. Tras corregir la causa, «Sincronizar» permite reintentar exactamente la misma operación, sin editarla. No hay aún un panel para resolver manualmente todos los conflictos de negocio.

Credenciales: vinculación de 15 minutos, secreto aleatorio por dispositivo (en nube solo se guarda su hash), contraseña del usuario no persistida, clave local guardada como derivación PBKDF2. La API no acepta la sesión de navegador como sustituto del token de equipo. El servidor local solo escucha en loopback y valida Host, Origin y un token local para escrituras.

**La base local no está cifrada por la aplicación.** Contiene el token del equipo y comprobantes: usa una cuenta del sistema por cajero/puesto, permisos privados, bloqueo de pantalla y cifrado de disco si se requiere. Un administrador del sistema operativo con acceso a los archivos puede alterarlos: este piloto no ofrece protección contra un PC comprometido. No copiar la base ni el token para instalar otra caja. No poner los datos dentro de OneDrive, Dropbox, una carpeta de red o una carpeta pública.

## Preparación del servidor, primero en pruebas

1. Tener respaldo recuperable de PostgreSQL y una copia de pruebas con datos ficticios.
2. Desplegar esta revisión en el entorno de pruebas habitual. En el entorno virtual de Django:

   ```bash
   python manage.py check
   python manage.py migrate --plan
   python manage.py migrate --noinput
   python manage.py collectstatic --noinput
   ```

   Las migraciones del piloto son `0044_hybrid_pilot` (tres tablas) y `0045_hybrid_recovery` (autorizaciones y auditoría de recuperación). No modifican ni borran ventas existentes. No ejecutar `makemigrations` en producción para corregir avisos históricos sin revisarlos. Las migraciones no activan el piloto. Probar ambas antes del despliegue; **no se aplicaron a producción en esta tarea**.

3. Recargar la aplicación web. Abrir **Seguridad → Equipos híbridos (piloto)**, o `/configuracion/equipos-hibridos/`, como Web Master.
4. Activar **Piloto POS híbrido** en Funcionalidades solo en pruebas inicialmente.
5. Autorizar un equipo, escoger su punto de pago y confirmar con la contraseña de Web Master. Copiar el código que aparece una sola vez; vence en 15 minutos.
6. No crear dos equipos activos para el mismo punto de pago. Si un código venció sin vincular, desactivar ese registro y generar uno nuevo.

## Obtener paquetes para Windows y Linux

El equipo del cajero **no necesita instalar Python** cuando se usa el paquete compilado. La construcción sí requiere Python 3.10 o posterior y debe hacerse en el mismo sistema operativo de destino. El workflow usa Python 3.12; el paquete Linux local se verificó con Python 3.10.12 en Ubuntu 22.04.

Opción reproducible: subir el código a GitHub y ejecutar manualmente el workflow **Paquetes del piloto híbrido** en Actions. Produce artefactos separados Windows x64 y Linux x64 (Ubuntu 22.04). Este trabajo no ejecuta por sí solo ese workflow ni publica un release.

Construcción manual, desde la raíz del proyecto y en un entorno virtual dedicado:

```bash
python -m pip install -r requirements-hybrid-build.txt
python -m unittest hybrid_client.test_client hybrid_client.test_printing hybrid_client.test_backup hybrid_client.test_recovery
python scripts/build_hybrid.py
```

Resultado: carpeta **completa** `dist/hybrid/NovaPOS/`. Los módulos empaquetados son `hybrid_client`, `pos_shared`, la biblioteca estándar y `cryptography` con sus dependencias para cifrado; Django, `NovaSoft`, `mainApp`, la base local y los archivos `.env` quedan fuera. No entregar el repositorio ni copiar solo el ejecutable. Conservar el hash del ZIP distribuido para identificar la versión. La dependencia adicional está en `requirements-hybrid-client.txt`; **no se necesita en PythonAnywhere** para este cambio.

Linux debe probarse en la distribución/arquitectura real. No prometer compatibilidad de un binario de Ubuntu 22.04 con distribuciones más antiguas, ARM o Alpine. Para ellas construir y validar el paquete correspondiente. Referencia: [PyInstaller y las plataformas de construcción](https://pyinstaller.org/en/stable/operating-mode.html).

## Instalación en Windows

1. Descargar/descomprimir **todo** el paquete Windows en una carpeta del usuario.
2. Hacer doble clic en **Instalar.cmd**. Alternativamente, abrir PowerShell en esa carpeta y ejecutar:

   ```powershell
   .\NovaPOS.exe --install
   ```

3. Se copia a `%LOCALAPPDATA%\NovaPOS\versions\0.6.0-pilot\` y se crea el acceso directo **Nova POS**. No necesita privilegios de administrador. Si Control de aplicaciones lo bloquea, detén la instalación y solicita una vía de aprobación autorizada; no desactives la protección.
4. Abrir el acceso directo. La pantalla abre `http://127.0.0.1:8792/`. Mantener abierta la aplicación local; cerrar su consola detiene la sincronización, pero no borra lo ya registrado.
5. Pegar la dirección **HTTPS** de la web y el código del equipo. No pegar contraseñas de Aiven.
6. Iniciar sesión del POS, elegir una clave local de 8 o más caracteres y esperar **Respaldo preparado** antes de probar sin internet.

Los datos quedan en `%LOCALAPPDATA%\NovaPOS\data\`, separados de la instalación. Si Windows muestra una advertencia por ejecutable sin firma, verificar origen/hash con el administrador; **no desactivar antivirus ni protecciones globales**. El paquete piloto aún no tiene firma de código.

## Instalación en Linux

1. Descomprimir el paquete Linux x64 adecuado en una carpeta del usuario.
2. En una terminal dentro de esa carpeta ejecutar `sh instalar.sh`. Alternativamente:

   ```bash
   chmod u+x NovaPOS
   ./NovaPOS --install
   ```

3. Abrir **Nova POS** desde el menú de aplicaciones. También puede ejecutarse directamente `./NovaPOS` como paquete portátil.
4. Vincular e iniciar igual que Windows.

Programa: `~/.local/share/novapos-app/versions/0.6.0-pilot/`.
Datos: `$XDG_DATA_HOME/novapos/` o `~/.local/share/novapos/` si no se define XDG_DATA_HOME.

Los paquetes Windows y Linux se construyeron y comprobaron aquí. Linux se verificó dentro de WSL 2 con Ubuntu 22.04 x64: instalación en un HOME temporal, acceso directo, protección contra sobrescritura y arranque del ejecutable. **Falta aceptación en un computador Linux con su impresora física**; WSL no sustituye esa prueba.

### Actualizar desde un piloto anterior

Cierra NovaPOS, respalda su carpeta de datos y ejecuta el instalador 0.6.0 una vez construido, probado y autorizado para el equipo. Los datos están separados de los ejecutables; no se borran las ventas pendientes. Desde 0.1 se actualiza SQLite al esquema 2, añadiendo la cola de impresión; desde 0.2/0.3/0.4/0.4.1/0.5 el esquema se conserva. **No vuelvas a abrir esos datos con el ejecutable 0.1**: detectará un esquema posterior y se negará a abrirlo. El historial anterior no se imprime automáticamente al actualizar. No intentes abrir una copia recuperada con versiones antiguas para saltarte la revisión.

## Impresión local

1. Inicia/desbloquea la sesión y pulsa **Impresión**, al lado del estado de sincronización.
2. Escoge conexión y papel:
   - **Navegador:** compatible con impresoras configuradas en el sistema, pero pide su diálogo; no abre cajón ni imprime automáticamente.
   - **Agente local del POS:** debe estar instalado y en funcionamiento en el mismo equipo, normalmente en `127.0.0.1:8787`. Introduce su token; no es la contraseña de Aiven ni la clave de vinculación del equipo. Usa el protocolo ya utilizado en la web: `/print`, `X-Pos-Agent-Token`, texto y corte incorporado; `/kick` para el cajón. Este paquete no instala el agente.
   - **CUPS, solo Linux:** introduce el nombre exacto de una cola ya configurada (por ejemplo `POS80`). Usa `lp` con datos ESC/POS sin conversión. Una impresora no compatible con ESC/POS debe usar el diálogo del navegador. Configura y prueba el papel de 58/80 mm en el equipo real.
3. Activa impresión automática, corte y/o cajón si corresponde. Confirma con tu **clave local**. El cajón solo se abre en la primera impresión automática; una copia no lo abre.
4. Registra una venta ficticia en el entorno de pruebas. La cola local se guarda en SQLite y se procesa en segundo plano, con independencia de la conexión a internet. Puedes cerrar el comprobante y continuar vendiendo.

Una confirmación del agente/CUPS significa **trabajo aceptado**, no papel físicamente impreso. Si hay un corte o timeout después de enviarlo, queda como **sin confirmar** y no se reenvía automáticamente: revisa papel/cola y pide una copia si hace falta. Esa copia queda identificada como reimpresión. Un error de impresión **no deshace la venta ni justifica cobrar de nuevo**.

Los trabajos todavía en cola se conservan al reiniciar; los que estaban enviándose quedan sin confirmar para evitar duplicados. No se cambia la configuración mientras hay trabajos en proceso. El token del agente queda en la base local, no vuelve al navegador y nunca forma parte del texto imprimible; se aplican las mismas precauciones de protección del disco que al token del equipo.

Referencias del transporte Linux: [opciones de impresión CUPS](https://www.cups.org/doc/options.html). No se ha verificado una impresora física en esta entrega.

## Uso diario y recuperación

1. Abrir el turno en la web, si aplica. Abrir Nova POS, iniciar sesión con internet y esperar catálogo completo.
2. Vender desde el piloto solamente los casos admitidos. Si se va internet, continuar en la misma aplicación, sin cambiar a otra instalación ni copiar datos.
3. Cada cobro muestra confirmado con ID de venta, pendiente con UUID local, o requiere revisión. **Un rechazo de autorización no se trata como permiso para seguir vendiendo offline.**
4. Si se reinicia la aplicación/PC, abrir el acceso directo y desbloquear con la clave local. Los pendientes registrados siguen en SQLite. Si olvidaste la clave, no reinstales ni borres la carpeta: requiere recuperación asistida.
5. Al volver internet, la aplicación reintenta cada 20 segundos mientras esté abierta. «Sincronizar» también reintenta conflictos tras corregir su causa.
6. Para el cierre: terminar/vaciar el carrito, «Finalizar sesión», esperar confirmación del servidor y después cerrar el turno en la web. La aplicación no permite finalizar con ventas pendientes. La expiración por sí sola nunca libera un turno.
7. No borrar/reinstalar una caja que tenga pendientes. Usa **Respaldar** y conserva la descarga fuera de ese disco. La aplicación también genera una copia consistente `backup.sqlite3` al finalizar una sesión, sustituyéndola solo después de comprobar la nueva copia. Esta copia interna no está cifrada y no protege frente a avería o pérdida del disco.

SQLite usa WAL y sincronización duradera; copiar solo el archivo principal mientras está abierto puede perder cambios que están en WAL. Referencia: [SQLite WAL](https://www.sqlite.org/wal.html). No existe sincronización entre PCs por carpetas compartidas.

## Respaldos cifrados y recuperación de revisión (0.3)

### Crear la copia, incluso sin internet

1. Con la sesión desbloqueada, pulsa **Respaldar**, junto a Impresión.
2. Confirma tu clave local y elige una **contraseña diferente de 12 a 128 caracteres**, preferiblemente una frase larga. Repítela.
3. Descarga el archivo `.novabackup`. Comprueba que el navegador terminó la descarga; la aplicación no puede garantizar que el navegador haya guardado el archivo.
4. Guarda el archivo en una USB o disco externo controlado y la contraseña por separado. **Sin la contraseña no hay recuperación.** No lo compartas por chat ni lo subas al repositorio.
5. Verifica la copia con el comando siguiente. Antes del piloto real debe asignarse un responsable y una frecuencia de copias: **este respaldo externo es manual, no automático**.

Incluye todo el historial registrado, UUID y secuencias, pendientes, confirmaciones, catálogo, sesión, configuración de impresión y credenciales del dispositivo. No incluye carritos todavía sin cobrar. Usa una instantánea consistente de SQLite, incluyendo WAL, y cifrado autenticado AES-256-GCM con clave derivada mediante Scrypt y sal/nonce aleatorios. La contraseña no se guarda en la aplicación. El disco local activo sigue sin cifrado de aplicación.

El formato no contiene SQL para ejecutar. Las filas y su estructura se validan antes de importarse. Una clave incorrecta, archivo truncado o modificado hace fallar la comprobación sin tocar la caja. Límite del archivo del piloto: 128 MiB; si se supera, solicitar copia asistida, nunca borrar historial para reducirlo.

### Verificar o extraer para revisión

Desde una **terminal interactiva** en la carpeta del paquete Windows:

```powershell
.\NovaPOS.exe --verify-backup "E:\Respaldos\caja-1.novabackup"
.\NovaPOS.exe --restore-backup "E:\Respaldos\caja-1.novabackup" --recovery-dir "$HOME\NovaPOS-Revision-20260929"
```

Linux, adaptando la ruta del disco externo:

```bash
./NovaPOS --verify-backup /media/usuario/USB/caja-1.novabackup
./NovaPOS --restore-backup /media/usuario/USB/caja-1.novabackup --recovery-dir "$HOME/NovaPOS-Revision-20260929"
```

La contraseña se pide sin mostrarla; no se pasa como argumento, variable de entorno ni tubería. También existe `--export-backup ARCHIVO` para generar una copia desde terminal con la carpeta de datos habitual, o indicando `--data-dir CARPETA` si se usa una ubicación personalizada. Este comando no requiere iniciar una sesión web y exige acceso local a los archivos: es una herramienta del administrador, no un mecanismo adicional de permisos del POS.

La carpeta de recuperación **debe ser nueva**. Dentro se crea `recovery.sqlite3`, no una base activa `operations.sqlite3`, y `RECOVERY-REVIEW.json`. La copia extraída queda sin cifrar, por lo que debe mantenerse en una ubicación privada/cifrada y bajo control del administrador. No se reemplazan datos existentes, no se contacta a la nube y no se imprimen comprobantes. Los trabajos de impresión pendientes/enviándose pasan a «sin confirmar»: pudo haberse impreso antes de la pérdida del equipo.

**Importante:** verificar el archivo confirma integridad y estructura, no que esté actualizado respecto a la nube. La recuperación solo contiene lo existente al crear la copia; no recupera ventas posteriores si se perdió el disco original. Conserva los archivos originales y detén el equipo anterior. Un ACK perdido podría corresponder a una venta ya registrada. No reingreses ventas a mano, no renombres la copia como base activa y no retires el bloqueo de revisión: utiliza el flujo autorizado siguiente.

## Reactivar un reemplazo con autorización (0.4)

**Alcance:** ventas en efectivo del piloto, conservando UUID, precios autorizados, fechas y secuencias. El reemplazo conserva la identidad lógica del puesto en la nube y recibe una credencial nueva. El antiguo token y su código de vinculación quedan invalidados. No se duplica el registro del punto de pago.

1. Instala/descomprime el cliente 0.4 en el reemplazo. Verifica y extrae el respaldo como se explicó antes. La carpeta de revisión debe permanecer en disco local privado, no en una carpeta compartida.
2. **Retira el computador anterior.** Una revocación en la nube no puede impedir físicamente que una instalación aislada siga capturando offline hasta que contacte al servidor. No se deben operar ambas.
3. Como Web Master, abre **Seguridad → Equipos híbridos → Recuperar una caja perdida o dañada**. Selecciona el equipo exacto, escribe el motivo, confirma su retiro y tu contraseña. Esto retira inmediatamente su acceso en la nube y crea una autorización de 15 minutos, mostrada una sola vez. Las sesiones/turnos no se liberan todavía.
4. En una terminal del paquete Windows:

   ```powershell
   .\NovaPOS.exe --resume-recovery "$HOME\NovaPOS-Revision-20260929"
   ```

   Linux:

   ```bash
   ./NovaPOS --resume-recovery "$HOME/NovaPOS-Revision-20260929"
   ```

   Introduce la dirección HTTPS del POS (debe coincidir con el respaldo) y el código cuando se pida. La credencial nueva se guarda localmente **antes** de contactar al servidor. No se envía la contraseña del respaldo ni el token anterior. Tampoco se suben el catálogo o los secretos de impresión.
5. Regresa a la página del Web Master. Aparecerá el resumen: fecha de copia, operaciones ya recibidas, pendientes por enviar y movimientos adicionales que ya existen en la nube. Revisa también el efectivo y la posible existencia de ventas posteriores al respaldo que nunca llegaron al servidor. Confirma la advertencia y tu contraseña para **aprobar ese respaldo concreto**. La autorización presentada vence a las 24 horas.
6. Repite el comando del paso 4 y deja vacío el código para continuar la misma solicitud. No es necesario descargar otra copia ni volver a registrar ventas.
7. La nube coteja UUID, contenido y secuencias. Solo ingresa las faltantes; los ACK perdidos recuperan la confirmación ya existente. El lote completo, los movimientos de inventario/caja, la liberación de las sesiones antiguas, la credencial nueva y la auditoría se confirman en una sola transacción. Si una venta falla, se revierte todo el lote nuevo y la caja no se habilita.
8. Se crea una subcarpeta nueva **`pos-recuperado`** dentro de la carpeta de revisión. El comando muestra la ruta para abrir NovaPOS con `--data-dir`. **Usa esa ruta para la caja recuperada**, no el acceso directo con la carpeta predeterminada de otra instalación. No se sobrescribe ninguna carpeta existente. Si hay un fallo local tras confirmar en nube, repetir recupera el mismo resultado sin volver a vender.
9. Inicia sesión del POS de nuevo con internet. Se requiere turno abierto si la funcionalidad lo exige. Se descargan los precios actuales y se elige una clave local nueva. Configura y prueba la impresora del reemplazo; no se copian su token ni la configuración automática, ni se reimprime el historial.

La auditoría conserva quién retiró el acceso, quién aprobó, motivo, horas, huellas/identificadores de los pendientes y sus confirmaciones. Finalizar sesiones híbridas **no cierra el turno ni modifica a mano su cuadre**; los movimientos conciliados pasan por el escritor normal de ventas. Se puede continuar en el turno abierto o cerrarlo desde la web después de la revisión.

Si vence la autorización, el Web Master puede emitir otra: la anterior queda marcada como reemplazada y sus códigos dejan de servir. Si cambian el respaldo o la situación revisada, faltan secuencias, un artículo ya no es válido, se retiraron permisos o el turno ya cerró, se bloquea para revisión: no se omiten errores ni se recrean confirmaciones supuestamente aceptadas que no existen en nube.

**Límites del piloto:** hasta 1000 operaciones del respaldo y 1,8 MB de manifiesto por recuperación. Copias mayores requieren un flujo asistido/ampliación posterior; no recortes ni borres historial para hacerlas caber. La nube puede contener operaciones posteriores a la copia; se conservan y se muestran en el resumen, pero no se reconstruye su payload ausente en el historial local. Ningún mecanismo puede recuperar movimientos offline posteriores a la copia si también se perdió el único disco que los contenía. La aprobación exige revisar ese riesgo, no lo elimina.

## Pruebas obligatorias antes de utilizar dinero real

- Venta normal con internet, comprobante, cambio y cuadre de caja.
- Desconectar red, varias ventas, reiniciar aplicación y recuperar pendientes.
- Volver a conectar: una sola venta por UUID, una sola rebaja de stock, saldo correcto.
- Simular nube confirmando y pérdida de respuesta: reenvío sin duplicados.
- Dos cajas con stock inicial igual: ambos movimientos deben sumarse, admitiendo negativos.
- Intento de cerrar turno con sesión activa/pendientes: bloqueo legible.
- Finalizar sesión, cerrar turno y verificar que incluye todos los movimientos offline por su fecha real.
- Precios/catálogo modificados, artículo retirado, credenciales revocadas, reloj adelantado/atrasado, disco lleno, fallo de red prolongado y sesión vencida.
- Impresión física y reinicio en Windows **y** la distribución Linux real.
- Concurrencia de PostgreSQL (incluidas ventas web en paralelo), revisión de seguridad y restauración de respaldo en staging.

Pruebas de desarrollo sin Aiven:

```bash
python -m pip install -r requirements-hybrid-client.txt
python manage.py test mainApp.test_hybrid mainApp.test_hybrid_recovery mainApp.test_hybrid_migration mainApp.test_hybrid_postgres hybrid_client.test_client hybrid_client.test_printing hybrid_client.test_backup hybrid_client.test_recovery mainApp.test_cash_receipt mainApp.test_ptm --settings=NovaSoft.test_settings --noinput
```

Para ver una demo con datos ficticios, `python scripts/smoke_hybrid_server.py`; se abre en `http://127.0.0.1:8893/`. No registra nada en el negocio. Su impresión es **siempre simulada**, aunque configures un agente: no envía trabajos físicos.

### Concurrencia con PostgreSQL desechable

Está preparado el workflow manual **Piloto híbrido - PostgreSQL real (datos ficticios)** (`.github/workflows/test-hybrid-postgres.yml`). Inicia PostgreSQL 16 temporal dentro de GitHub Actions, crea datos ficticios y ejecuta pruebas de:

- El mismo UUID enviado simultáneamente: una sola venta.
- Dos UUID intentando ocupar la misma secuencia: solo uno aceptado.
- Dos cajas y una venta web simultáneas: movimientos acumulados y stock negativo correcto.
- Venta mientras se intenta cerrar el turno: el cierre no omite la sesión activa.
- Liberación de sesión concurrente: no salta una secuencia pendiente.

Estas cinco pruebas se **omiten** en SQLite: no equivalen a verificar los bloqueos de PostgreSQL. Ya se ejecutaron correctamente en PostgreSQL 16.15 local dentro de WSL, junto con la migración 0044, recibos y PTM (55 pruebas). El workflow de GitHub está preparado pero **no ejecutado**. Después de subir y revisar el código, ejecutar ambos workflows y resolver cualquier fallo antes de instalar en cajas reales.

Para repetirlas en este computador, desde PowerShell:

```powershell
wsl -d Ubuntu-22.04 -u novapos-test -- bash -lc 'cd /mnt/c/Users/LENOVO/OneDrive/Escritorio/merk2888/Merk-888 && HYBRID_TEST_PYTHON=/home/novapos-test/hybrid-venv/bin/python HYBRID_TEST_PG_BIN=/usr/lib/postgresql/16/bin bash scripts/test_hybrid_postgres_linux.sh'
```

El script crea un clúster nuevo con contraseña aleatoria y acceso solo por loopback, ejecuta la suite y lo apaga incluso si las pruebas fallan. Conserva los datos ficticios y el log en `/tmp/novapos-pg-test.*`. Nunca reutiliza una base del negocio.

La configuración `NovaSoft.hybrid_test_postgres_settings` exige `HYBRID_TEST_POSTGRES=yes-local-disposable` y fija host `127.0.0.1`, usuario/base `nova_hybrid_ci` y puerto por defecto `55432`; nunca utiliza el host de Aiven de producción. Solo usar con un PostgreSQL desechable para pruebas, no con datos del negocio. Referencia: [servicios PostgreSQL en GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

## Próximas etapas

La aceptación adicional **sin impresión** ya se completó para el piloto 0.4.1: 81 pruebas PostgreSQL/aceptación y 43 pruebas de cliente por plataforma. Incluye procesos Windows y Linux, navegador, reinicios abruptos, cortes simulados, cierre real con cuadre, instalación y recuperación. Ver [informe y límites de las pruebas](pos_hibrido_aceptacion_sin_impresion.md). No sustituye pruebas de impresoras, apagones físicos ni una aprobación de despliegue.

1. Aprobar el piloto en una caja de pruebas con su impresora física en Windows/Linux (agente y CUPS). PostgreSQL desechable y recuperación aislada ya se probaron; falta aceptación con el hardware real y una política de copias externas.
2. Ampliar recuperación por lotes para respaldos grandes, recuperación de carritos, solución de conflictos, instaladores firmados, actualización segura y arranque automático supervisado. La reactivación auditada dentro de los límites del piloto está implementada en 0.4.
3. Ampliar operaciones autorizadas una a una: devoluciones, pagos/egresos, turnos, cambios de catálogo. Cada una necesita reglas propias de permisos, idempotencia y conflictos.
4. Medios electrónicos y PTM requieren comprobación externa: no se deben declarar verificados sin internet.

No instalar todavía masivamente ni reemplazar el POS conectado sin completar estas pruebas.

### Preparación de este PC para pruebas (29 de septiembre de 2026)

- Descargados y extraídos los binarios oficiales PostgreSQL 16.15 en `%LOCALAPPDATA%\NovaPOS-TestTools\pgsql`. Windows bloqueó `initdb.exe` mediante Control de aplicaciones; no se desactivó ni se eludió esa protección. Se utilizó PostgreSQL instalado desde su repositorio firmado oficial dentro de Ubuntu para las pruebas.
- Añadido `scripts/test_hybrid_postgres_windows.ps1`: crea un clúster ficticio con contraseña aleatoria y acceso solo por loopback, ejecuta la suite aislada y lo detiene al finalizar. No usa credenciales ni datos de Aiven. Solo funcionará si la política del equipo permite ejecutar los binarios oficiales.
- Tras el reinicio manual del usuario, WSL 2 arrancó correctamente. Instalados Ubuntu 22.04, PostgreSQL 16.15, herramientas de compilación y Python 3.10.12. Usuario Linux de pruebas: `novapos-test`; entorno aislado: `/home/novapos-test/hybrid-venv`.
- Las 31 pruebas del cliente y las 55 pruebas PostgreSQL pasaron en Linux. Se verificó también el instalador y ejecutable Linux en un HOME temporal, sin vincularlos al negocio. No se modificó el firmware.
- La instalación limpia detectó `pytz` faltante en `requirements.txt`; se añadió la versión 2026.2, que ya se usaba en Windows. La configuración de pruebas PostgreSQL crea todas las tablas actuales juntas para evitar referencias a tablas de autenticación todavía inexistentes.
- No hay un servidor PostgreSQL de pruebas escuchando en el puerto 55432. No se modificó producción.

### Verificación realizada en esta entrega

- Windows: 81 pruebas aprobadas en SQLite/cliente aislado; cinco pruebas PostgreSQL omitidas en SQLite. Linux: 31 pruebas del cliente y 55 con PostgreSQL 16.15 aprobadas, sin omisiones en esas suites y sin Aiven.
- Demo en navegador: venta offline por gramos con stock negativo, cambio, comprobante tras recarga, impresión automática simulada, copia confirmada, configuración sin exponer el token guardado, bloqueo/desbloqueo, escritorio y móvil sin desbordamiento.
- Paquetes Windows y Linux construidos; ejecutables y servidor local comprobados. Instalador Linux comprobado en un HOME temporal. No se ha instalado en cajas de producción ni se ha probado una impresora física.
- Revisión adicional de permisos/funcionalidades: aparecieron dos pruebas antiguas desactualizadas, ya presentes antes de este cambio (una exige `generar_venta.js?v=38` aunque el repositorio ya usa `v=48`; otra supone que todos los enlaces son vistas de clase y falla en `mi_horario`, que es una función). No se cambiaron esos componentes para silenciar las pruebas.
- Pendiente: instalación en una caja de pruebas y aceptación con su impresora; en Linux, comprobar también el escritorio/controlador real fuera de WSL. Los workflows de GitHub están preparados, no ejecutados desde esta entrega.

### Verificación del piloto 0.4

- Migración 0045 aplicada en bases desechables y contrastada con el modelo. Producción intacta.
- 71 pruebas con PostgreSQL: ACK perdido, rechazo de contenido cambiado, huecos/UUID repetidos, nube más nueva, expiración/reemisión, privilegios, reversión del lote completo, cierre concurrente, doble finalización simultánea y petición autenticada antes de retirar el token.
- 54 pruebas del cliente Windows/Linux, incluyendo fallos de disco/red, nueva credencial persistida, no sobrescritura (tampoco se inicializan carpetas existentes vacías), carpeta original conservada, nuevo login y ausencia de reimpresión automática.
- La recuperación se ensayó con datos ficticios; no constituye una recuperación ni una migración de datos reales del negocio.

### Verificación adicional del piloto 0.3

- 45 pruebas del cliente (14 nuevas de respaldo) en Windows y Linux: contraseña incorrecta, alteraciones, WAL, pendientes, UUID, estados, no sobrescritura, PIN/sesión/origen y bloqueo de carpetas recuperadas.
- El navegador descarga el respaldo cifrado sin modificar una venta pendiente. Formulario comprobado en móvil y escritorio; los campos de contraseñas se limpian al cerrar/enviar.
- La copia local de cierre conserva la anterior si falla su sustitución. Se corrigió la apertura del archivo para sincronización duradera en Windows, detectada por la prueba de cierre de sesión.
- Se comprobaron las exportaciones de los ejecutables Windows/Linux y la lectura de respaldos entre plataformas con datos ficticios. La recuperación verificada es **para revisión**, no reactivación automática ni prueba sobre datos del negocio.
- Volvieron a pasar las 55 pruebas con PostgreSQL desechable (incluyendo concurrencia, migración, recibos y PTM). Los paquetes 0.3 incluyen esta guía como `Guia-piloto.md`.
- En este PC solo se detectó Microsoft Print to PDF; no hay agente de impresión en 8787 ni impresora térmica instalada. La aceptación física continúa pendiente.
