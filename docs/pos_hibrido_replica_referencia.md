# Réplica autorizada de referencia — etapa de laboratorio

Estado: 1 de octubre de 2026. Conserva los modelos, las páginas y el menú original
de Django. **No habilita todavía las escrituras offline del sistema completo.**
No se desplegó a PythonAnywhere ni se descargaron datos reales de Aiven.

Avance posterior: [ventas en efectivo integradas al Django local](pos_hibrido_ventas_django.md),
con diario y proyección de inventario. Se activan solo en otro laboratorio mediante
`--sale-demo`; el modo `--replica-demo` sigue siendo de consulta.

## Alcance implementado

El servidor prepara una copia consistente de los siguientes datos:

- Sucursal y punto de pago asignados al dispositivo, sin saldo de caja.
- Categorías y productos, precios, impuestos, códigos de barras y tipo PTM.
- Inventario de esa sucursal, incluidos valores cero y negativos.
- Nombre, apellido y documento de clientes, cuando el usuario tiene permiso.
- Métodos de pago, activación, orden e impuesto 4x1000 de egresos.

No se copian contraseñas, claves API, credenciales Aiven, contactos de clientes,
usuarios completos, ventas, pagos, turnos, horarios ni movimientos financieros.
Las rutas sin datos replicados se bloquean: no se muestra un total vacío como si
fuera el balance real del negocio. La autorización remota y los permisos locales
deben permitir ambos la consulta.

## Flujo y recuperación

1. El receptor guarda un UUID de descarga antes de pedirla al servidor.
2. `POST /api/hybrid/v1/replica/prepare/` comprueba el dispositivo, su sesión,
   usuario, permisos vigentes, sucursal y punto. Prepara un corte PostgreSQL
   `REPEATABLE READ` y devuelve el manifiesto y el alcance autorizado.
3. `POST /api/hybrid/v1/replica/page/` devuelve páginas de hasta 200 cambios. Cada
   página vuelve a comprobar la autorización. Los cambios se guardan primero en
   tablas de preparación; todavía no son visibles para las páginas originales.
4. Se verifican el orden, los campos permitidos, las relaciones, los totales y
   las huellas de contenido. Solo una descarga completa activa la nueva copia,
   junto con sus metadatos, en una transacción local.
5. Las siguientes descargas transfieren altas, modificaciones y bajas respecto
   al corte anterior. También detectan cambios masivos realizados sin señales
   Django. En local solo se escriben las filas modificadas; un corte sin cambios
   renueva los metadatos y la autorización, no todo el catálogo.

Un corte de red o una respuesta perdida conserva la última copia completa y
permite retomar la descarga. No se combinan páginas de versiones diferentes.
Si el servidor ya no conserva el corte, se abandona únicamente la preparación
incompleta y se solicita otra copia. No se borra la copia activa para reiniciar.

Si falla la escritura local, se revierte la activación. Si hay comandos pendientes,
conflictos o cambios manuales fuera de la réplica, se rechaza la sustitución de los
datos: primero hay que conciliarlos. Una baja con historial relacionado, incluido
el que guarda métodos de pago como texto, no se borra automáticamente.

## Seguridad y límites actuales

- HTTPS obligatorio fuera del laboratorio loopback. No se siguen redirecciones;
  el token no va en la URL ni en mensajes visibles.
- La instalación queda vinculada a un servidor, dispositivo y usuario local.
  Un cambio de identidad, sucursal o permisos requiere revisar la vinculación.
- Una revocación detectada al conectar bloquea el acceso; los datos no se borran.
  Sin conexión no se puede conocer instantáneamente una revocación remota.
- La autorización de cada copia vence como máximo a las dos horas, o antes si
  vence la sesión híbrida. Esta limitación deliberada de laboratorio debe
  revisarse antes de permitir jornadas offline reales.
- Se conservan hasta cuatro cortes por dispositivo en el origen. Si falta la
  base de un delta se envía una copia completa, nunca un delta de otra versión.
- Límites de esta fase: 60.000 filas y 24 MB por copia completa; 200 cambios por
  página. Se rechaza una copia mayor sin dañar la anterior.
- El proceso de actualización espera 30 segundos entre intentos exitosos y
  aumenta la espera hasta cinco minutos tras fallos. **No es un envío inmediato
  por cada cambio del negocio.**
- El origen aún lee las tablas autorizadas para calcular cada corte y conserva
  esas copias completas. Falta medir la carga con el volumen real y varios PCs;
  no debe habilitarse masivamente sin esa medición. Un diario central de cambios
  será necesario para escalar con menor coste.
- La autenticación inicial y renovación automática de sesión, cifrado del disco,
  gestión de credenciales Windows, instalación permanente, actualizaciones
  firmadas y recuperación productiva siguen pendientes en el runtime completo.

## Demostración en este PC

La prueba iniciada durante el desarrollo usa dos bases distintas de PostgreSQL:

- Receptor con páginas originales: `http://127.0.0.1:8904/local/estado/`.
- Origen ficticio, únicamente APIs: `http://127.0.0.1:8905/`.
- Usuario local ficticio: `laboratorio`.
- Contraseña ficticia: `prueba-local-2026`.

El origen contiene los datos ficticios; el receptor inicia sin catálogo y lo
descarga por HTTP. No se incluyen las ventas ni pagos ficticios del origen.
Los enlaces del estado local permiten comprobar productos, inventarios y clientes.
No usar estas credenciales ni este servidor de desarrollo en producción.

Para iniciar una **nueva** demostración, desde WSL Ubuntu-22.04:

```bash
cd /mnt/c/Users/LENOVO/OneDrive/Escritorio/merk2888/Merk-888
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py \
  --serve --replica-demo --port 8904 --source-port 8905 --pg-port 55439
```

Si un puerto ya está ocupado, el comando se detiene y no sustituye el proceso
existente. Cada ejecución crea un directorio privado y bases NUEVAS; no es un
instalador ni un procedimiento para recuperar una instalación previa. Ctrl+C
cierra únicamente el laboratorio y el origen que ese proceso inició. Sus archivos
se conservan. No publicar los archivos privados `local.json` o
`replica-connection.json` ni su contenido.

`python manage.py sync_reference --connection-file RUTA_PRIVADA --local-user ID`
es el comando del runtime local para una actualización; `--watch` lo mantiene
actualizando. Requiere la configuración local aislada, un catálogo vacío o ya
gestionado por esta réplica y una vinculación privada válida. **No ejecutar este
comando con la configuración productiva habitual.**

## Verificación

Resultado de esta etapa: **55 pruebas aprobadas** con dos PostgreSQL aislados.
La comprobación en navegador también pasó: **0 errores JavaScript** y
**0 solicitudes externas** del navegador en el recorrido verificado.

Las pruebas usan exclusivamente bases temporales separadas. Cubren descarga
inicial, deltas, escritura solo de cambios, inventario negativo, altas y bajas,
permisos, revocación, vencimientos, cortes y respuestas perdidas, páginas corruptas,
activación atómica, protección de historial y operaciones locales pendientes.
Incluyen una descarga HTTP real entre las dos bases y la migración de metadatos.

```bash
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py \
  --test --replica-tests --port 8906 --pg-port 55438
```

`scripts/test_reference_replica_browser.cjs` comprueba con Edge las páginas
originales, búsqueda y recarga de productos, inventario y clientes; bloquea las
solicitudes del navegador fuera del receptor y comprueba que métricas sin datos
replicados quede bloqueada. Guarda evidencias en `outputs/full-local-replica/`.
No imprime ni registra ventas reales.

## Migración y siguiente etapa

`mainApp.0046_hybrid_reference_replica` crea únicamente la tabla de metadatos
`replicas_hibridas` en el servidor. Fue preparada para depender de 0045; no fue
aplicada a producción. No añadir `local_pos` a `INSTALLED_APPS` de PythonAnywhere.
El esquema del receptor sigue siendo el de una base nueva de laboratorio, no una
migración de una instalación real existente.

El siguiente paso es conectar el protocolo de ventas al Django completo:
registro durable antes de contactar la nube, mismo UUID en todos los reintentos,
cambio local y diario atómicos durante la caída, confirmación y conciliación al
volver la conexión, sin sustituir movimientos pendientes con un stock completo.
Después se integrarán pagos, devoluciones, caja y otros módulos por sus reglas.
El objetivo sigue siendo usar las mismas pantallas, trabajando primero con la
nube cuando responda y sin crear una segunda interfaz para el cajero.
