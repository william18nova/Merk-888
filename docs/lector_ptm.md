# Lector PTM y conciliación — versión piloto

## Alcance y estado

Módulo publicado en PythonAnywhere; **la extensión no se activa hasta configurar
su clave y habilitar las consultas**.
No ejecuta recargas, retiros, pagos, reversos ni cambios de saldo. Lee la pantalla
**Consultas → Consulta de transacciones** de una cuenta PTM autenticada por el usuario.
La extensión usa el mismo perfil de Chrome que ya tiene el navegador matriculado.
No pide la contraseña de PTM ni lee/exporta cookies de autenticación.

Se inspeccionaron los encabezados del reporte y cinco transacciones exitosas del
14/10/2026 desde la consulta del 15/10/2026. El portal permitió consultar ese día
anterior. Esto **no garantiza su retención histórica ni que todos los servicios
usen idéntico formato**. Una consulta nueva pidió iniciar sesión nuevamente;
La primera lectura real desde la extensión instalada se comprobó el 16/10:
NovaPOS mostró «Lectura reciente · Reporte recibido» y cinco referencias únicas
del reporte de ese día (tres exitosas y dos con estado Error en PTM).
La recepción no vincula movimientos al POS ni afecta el dinero de caja.
No se usaron datos de clientes reales en fixtures ni pruebas automatizadas.

## Cómo funciona

- Una cuenta compartida del portal se configura una sola vez con el valor exacto
  de la columna **Usuario**. Pueden existir varios lectores de la misma cuenta.
- El registro único es **cuenta PTM + ID TX**. Releer o reenviar no duplica filas.
- Conserva referencia de operador, fechas, producto, importe y estado. No captura
  contratos/teléfonos, dirección IP del terminal, contraseñas ni errores con PII.
- Una lectura antigua reenviada no reemplaza otra más reciente. Los relojes de
  los computadores deben estar sincronizados; se rechazan fechas futuras >5 min.
- Solo se clasifican automáticamente los nombres comprobados **NEQUI - RECARGAS**
  y **NEQUI - RETIROS**, con el signo correcto. Los demás productos se importan
  como **Por revisar**, sin inferir su efecto monetario. Se podrán añadir reglas
  después de comprobar los nombres y signos de otros productos reales.
- El estado **Exitosa**, importe y tipo deben coincidir para mostrar **Vinculado**.
  Otro estado, signo/tipo no reconocido o diferencia de importe exige revisión.
- El administrador confirma el vínculo con una operación PTM ya registrada en el
  POS. La referencia debe coincidir exactamente con **ID TX o ID Operador**;
  no se acepta coincidencia solo por importe/hora. Una operación POS no se puede
  usar en dos movimientos. Vincular/desvincular exige motivo y deja auditoría.
- El cajero y turno provienen del registro POS, **no** del usuario compartido PTM
  ni del equipo que consulta. Importar/vincular no cambia el dinero de caja.
- Los registros manuales sin comprobante vinculado también se muestran.
  No se bloquea el cierre ni se acusa una omisión automáticamente.

## Preparar el servidor NovaPOS

Estado comprobado el 15/10/2026 en este equipo:

- Aplicada `0051_ptm_reader` a la base configurada de Aiven, tras un respaldo
  completo verificado mediante catálogo, decodificación completa y SHA-256.
- Verificadas las columnas de las cuatro tablas nuevas. No se crearon ventas,
  pagos, operaciones PTM ni claves de acceso como parte de la migración.
- Actualizada la copia privada WSL con respaldo independiente: se conservaron
  sus 76 tablas anteriores y 20.581 filas. La firma de esquema quedó vigente.
- Extensión 0.1.0 instalada por el usuario en Chrome (captura comprobada).
- Publicados los 10 archivos del módulo en PythonAnywhere el 16/10/2026 UTC
  (15/10 en Colombia), sobre la base `9c255da`. Respaldo de código:
  `/home/Merk888/novapos-deploy-backups/20261016T014457Z-5b2df28a`.
  `check` y 57 pruebas SQLite correctas en el servidor; esquema `0051`
  comprobado, `collectstatic` ejecutado y aplicación web recargada.
- Comprobación HTTPS externa: login 200, conciliación redirige a login (302),
  CSS PTM 200 y receptor sin clave 401. No se modificó ni reinició el bot.
- El 16/10 se identificó el usuario compartido en la columna Usuario del reporte
  y se creó la cuenta interna «PTM compartida» desde el formulario autenticado.
  Tras autorización explícita del responsable se emitió desde el formulario
  una clave de 90 días para «Chrome - PC Lenovo». Se verificó que el lector
  figura como «Pendiente de activación», sin extraer ni guardar la clave en
  el repositorio o en los registros de trabajo. El responsable debe copiarla
  de su pantalla a las opciones de la extensión de Chrome.
  El responsable configuró la extensión y pulsó «Consultar ahora». Se comprobó
  la recepción real: cinco movimientos de hoy y cinco referencias distintas,
  tres «Sin vincular» y dos «Por revisar» por estado Error de PTM. No se
  vincularon operaciones POS ni se modificó la caja. La ausencia de duplicados
  de esta primera lectura no sustituye la prueba prolongada de relecturas,
  cierre/reapertura de Chrome y caducidad de sesión.
- Corregido el rechazo CSRF de ese formulario: el log confirmó
  `Origin checking failed - null does not match any trusted origins`.
  La política `no-referrer` podía provocar `Origin: null` en los POST nativos;
  se cambió a `same-origin`, sin eximir el formulario de CSRF ni confiar en
  orígenes externos. Se mantiene `Cache-Control: no-store`; las claves nunca
  se incluyen en las URL. Verificado creando la cuenta desde el navegador.
  Parche 0.1.1 aplicado con solo dos archivos cambiados y **60 pruebas correctas**
  tanto localmente como en PythonAnywhere. Respaldo de código del parche:
  `/home/Merk888/novapos-deploy-backups/20261016T150412Z-81edb846`.
  Sin migraciones adicionales, cambios en caja ni reinicio del bot.
- El despliegue fue selectivo, **sin git push ni git pull**. En PythonAnywhere
  quedan cuatro archivos modificados y seis nuevos correspondientes al paquete;
  preservar estos cambios al actualizar Git. No usar `reset --hard` ni borrar
  archivos nuevos. Antes del siguiente pull, incorporar el mismo módulo al
  repositorio o reconciliar explícitamente el despliegue respaldado.

Paquete desplegado: `NovaPOS-PTM-servidor-0.1.0.zip`, SHA-256
`f2170105b9b0dc143816dfbb065dcd7f6691ad97c487e340bffa7597c7b2c479`.
Parche posterior: `NovaPOS-PTM-servidor-0.1.1.zip`, SHA-256
`97f0e913334fa88b56aabafc5e5087726122a95320b18597a6030b3967fe06f1`.
Para preparar actualizaciones usar `--previous-package` y otro `--output`:
se verifican los hashes de la versión previa antes de reemplazar sus archivos,
con respaldo y restauración del código si fallan las pruebas.
Se prepara con `scripts/build_ptm_server_release.py`; el aplicador incluido
rechaza archivos previos distintos de la base revisada y restaura el código
anterior si fallan las verificaciones. No incluye credenciales ni datos de clientes.

Para repetir la actualización en otra copia compatible, detener primero su
`runserver`. El actualizador comprueba la firma anterior exacta y rechaza cambios
de modelo adicionales; no debe usarse para saltarse una incompatibilidad:

```sh
python scripts/update_integrated_close_payments.py --upgrade ptm-reader
python scripts/update_integrated_close_payments.py --upgrade ptm-reader --apply
```

La migración online supervisada admite solamente `0051` y un respaldo completo
reciente de la misma base:

```sh
python scripts/migrate_ptm_reader_with_backup.py --backup-manifest RUTA_PRIVADA/manifest.json
python scripts/migrate_ptm_reader_with_backup.py --backup-manifest RUTA_PRIVADA/manifest.json --apply
```

1. Respaldar la base del servidor e incluir estos archivos de aplicación junto con
   `mainApp/migrations/0051_ptm_reader.py`. No ejecutar `makemigrations` general:
   el proyecto tiene diferencias anteriores ajenas a este módulo.
2. En el entorno del servidor ejecutar:

   ```sh
   python manage.py check
   python manage.py migrate --noinput
   python manage.py collectstatic --noinput
   ```

3. Recargar la web en PythonAnywhere. La migración crea cuatro tablas nuevas;
   no reinterpreta ni actualiza ventas, operaciones PTM o saldos existentes.
4. Abrir **Caja → Conciliación PTM** (`/ptm/conciliacion/`) como Web Master o con
   permiso **Editar turnos de caja**. En configuración, crear la cuenta compartida
   y una clave por computador lector. Cada clave se muestra una sola vez, vence
   en 90 días y puede revocarse; en la base se guarda solo su hash.
5. Elegir **un único servidor central NovaPOS** para todos los lectores.
   El destino recomendado es la web central HTTPS. Si se usa la instalación
   integrada `runserver`, PTM requiere conexión online; la copia offline no recibe
   estas observaciones. Una nueva migración también exige seguir el procedimiento
   existente de actualización con respaldo de la copia integrada, no editar a
   mano su huella de esquema ni borrar su base.

## Instalar en el Chrome ya matriculado en PTM

1. Descomprimir el paquete `NovaPOS-Lector-PTM-0.1.0.zip`, o usar directamente
   `browser_extensions/ptm_reader` de este repositorio.
2. En **ese perfil de Chrome**, abrir `chrome://extensions`, activar modo de
   desarrollador y elegir **Cargar descomprimida**. Seleccionar la carpeta que
   contiene `manifest.json`. Esta versión piloto no está publicada en la tienda;
   la instalación y la concesión de permisos las debe confirmar el responsable.
3. Abrir las opciones de **NovaPOS · Lector PTM**. Escribir la dirección principal
   del POS (HTTPS, sin rutas; para pruebas se permite `http://127.0.0.1:puerto`) y
   pegar la clave. Permitir acceso únicamente al dominio POS seleccionado.
4. Activar las consultas y pulsar **Consultar ahora**. Iniciar sesión en PTM
   manualmente si se solicita. Se abre una pestaña dedicada de consulta en
   segundo plano, sin modificar la pestaña donde el cajero hace operaciones.
5. Mantener esa pestaña para el lector. No usarla para ventas ni cambiar filtros.
   Comparar el número de transacciones y las referencias con el reporte manual;
   repetir la lectura y verificar que el número de movimientos no se duplica.

## Interrupciones y límites

- Periodicidad solicitada: dos minutos. Chrome puede retrasar alarmas y suspender
  pestañas; no es tiempo real garantizado. Cerrar el navegador, suspender el equipo,
  cerrar sesión o perder internet puede detener la consulta.
- La página muestra último contacto, última lectura completa, día consultado,
  errores y alerta si no hay lectura completa de hoy en diez minutos. Refrescar
  la pantalla para ver cambios; un contacto reciente no implica reporte completo.
- Revisa hoy y ayer. Tras una interrupción recupera hasta siete días. Para períodos
  mayores muestra advertencia persistente; recuperar cada día con las opciones
  de la extensión y compararlo manualmente antes de quitar la advertencia.
- Pagina de 100 en 100, con tope de 100 páginas por día. Un cambio de encabezados,
  una página incompleta o IDs repetidos detiene la lectura y no anuncia éxito.
- Conserva un lote no confirmado en el almacenamiento **local** de la extensión
  para reintentar antes de continuar. Un corte o cierre a mitad del reporte obliga
  a releer el día. Nunca borra movimientos por faltar en una consulta posterior.
- No cambies de servidor/clave mientras haya un lote pendiente. Si se revocó su
  clave, requiere revisión administrativa para recuperarlo; no borrar los datos
  de la extensión como primera medida. Desinstalar/limpiar el perfil sí puede
  borrar pendientes. El lote no contiene el teléfono/IP del cliente.
- El lector es una observación del navegador, **no un comprobante firmado por PTM**.
  Una persona con control del equipo/clave puede falsificar datos o apagarlo.
  Requiere contrastar reportes oficiales, restringir permisos y supervisar lecturas.
- No evita que alguien opere desde un celular u otro navegador; detectará esas
  operaciones solo si aparecen en el reporte de la cuenta y se recupera ese día.
- Cambios de estado posteriores a ayer requieren recuperar ese día nuevamente.
  Se observaron dos errores reales en la lectura del 16/10; se conservan como
  «Por revisar». Todavía no se han validado ejemplos reales de reversos.
- Verificar con PTM que este uso automatizado de consulta está permitido. Ante
  límites, CAPTCHA o bloqueos, detener el lector y solicitar una integración oficial;
  no eludir controles ni automatizar login o retos de seguridad.

## Pruebas reproducibles sin producción

```sh
python -m django test mainApp.test_ptm_reader mainApp.test_ptm mainApp.test_connection_status --settings=NovaSoft.test_settings --noinput
node --test scripts/test_ptm_reader.cjs
```

Las pruebas usan SQLite en memoria y datos ficticios; no conectan con Aiven.
Cubren importación, no alteración de efectivo, múltiples lectores, aislamiento
por cuenta, referencias, cambios de estado, auditoría, permisos/CSRF, claves,
reenvíos antiguos, paginación simulada de 205 filas e interrupciones.
La concurrencia real con PostgreSQL y la extensión instalada en Chrome requieren
la prueba piloto; estas pruebas unitarias no la sustituyen.

Referencias de implementación:
[alarmas de Chrome](https://developer.chrome.com/docs/extensions/reference/api/alarms),
[peticiones desde extensiones](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests),
[scripts de contenido](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts).
