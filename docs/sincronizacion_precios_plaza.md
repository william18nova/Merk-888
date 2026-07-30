# Sincronización de precios Plaza → merk2

El comando consulta `public.productos` en la base externa y actualiza
`Producto.precio` en merk2 usando el mapeo versionado en
`mainApp/data/price_sync_plaza_map.json`.

La hoja contiene 76 relaciones (71 productos de origen y 76 destinos). Hay
70 relaciones directas activas. Se dejaron pausadas 6 equivalencias no directas
hasta confirmar producto, presentación y regla de precio; entre ellas está
`cidras`, cuyo snapshot pasa de precio por peso 3 a precio por unidad 3000.

La operación tiene estas protecciones:

- la conexión externa se abre con transacción de solo lectura y TLS;
- cada ID se valida también contra el nombre esperado;
- se valida todo el lote antes de escribir;
- sin `--apply` el comando siempre es una simulación;
- al actualizar, conserva el valor anterior en `precio_anterior`;
- una variación superior a `PRICE_SYNC_MAX_PRICE_FACTOR` bloquea el lote;
- el lote local se aplica dentro de una transacción.

Este proceso no recalcula `rentabilidad`: el proyecto admite precios distintos
por proveedor y esa métrica se actualiza actualmente cuando se registra el
costo de una compra. Definir otro costo de referencia requeriría una regla
contable adicional.

## Validación realizada el 29 de julio de 2026

La base `desarrollo-william…` fue confirmada como origen Plaza. Su tabla
`public.productos` contiene 3.458 referencias y todos los productos activos del
mapeo coincidieron por ID y nombre. La base `merk-888…` es el destino merk2.

La simulación real terminó sin escrituras con este resultado:

```text
70 mapeos activos
66 productos de origen
70 productos de destino
16 precios cambiarían
54 precios permanecen iguales
0 inconsistencias de ID o nombre
0 variaciones extremas
```

No programes todavía `--apply` hasta aprobar los 16 cambios mostrados por la
simulación. Las 6 equivalencias no directas continúan pausadas.

## Variables privadas

Configura estas variables únicamente en PythonAnywhere, nunca en Git:

```text
PRICE_SYNC_SOURCE_HOST
PRICE_SYNC_SOURCE_PORT
PRICE_SYNC_SOURCE_NAME
PRICE_SYNC_SOURCE_USER
PRICE_SYNC_SOURCE_PASSWORD
PRICE_SYNC_SOURCE_SSLMODE=require
# PRICE_SYNC_SOURCE_SSLROOTCERT=/home/Merk888/certs/ca.pem
```

Opcionales:

```text
PRICE_SYNC_MAPPING_FILE
PRICE_SYNC_CONNECT_TIMEOUT=10
PRICE_SYNC_STATEMENT_TIMEOUT_MS=30000
PRICE_SYNC_MAX_PRICE_FACTOR=5
```

Conviene usar en la base externa un usuario con permiso exclusivo de lectura.
La contraseña compartida durante la configuración debe rotarse antes de dejar
activa la tarea. Cuando el proveedor lo permita, usa `verify-full` con el
certificado CA en lugar de `require`.

En PythonAnywhere se puede crear `/home/Merk888/.price_sync.env`, fuera del
repositorio y con permisos `600`, usando líneas `export`:

```bash
export PRICE_SYNC_SOURCE_HOST='...'
export PRICE_SYNC_SOURCE_PORT='...'
export PRICE_SYNC_SOURCE_NAME='...'
export PRICE_SYNC_SOURCE_USER='...'
export PRICE_SYNC_SOURCE_PASSWORD='...'
export PRICE_SYNC_SOURCE_SSLMODE='require'
# export PRICE_SYNC_SOURCE_SSLROOTCERT='/home/Merk888/certs/ca.pem'
```

## Prueba manual

Desde una consola Bash de PythonAnywhere, con las variables ya exportadas:

```bash
cd /home/Merk888/Merk-888
/home/Merk888/.virtualenvs/env/bin/python manage.py actualizar_precios_plaza --dry-run --force
```

Mientras las 6 relaciones pendientes sigan pausadas, la prueba debe informar
70 mapeos, 66 productos de origen y 70 productos de destino. No debe mostrar
inconsistencias de IDs/nombres ni variaciones extremas sin revisar.

Solo después de aprobar la simulación:

```bash
/home/Merk888/.virtualenvs/env/bin/python manage.py actualizar_precios_plaza --apply --force
```

## Programación

PythonAnywhere debe ejecutar diariamente, a las 14:00 UTC. Crea primero la
carpeta privada de logs y prueba que el archivo de entorno carga correctamente:

```bash
mkdir -p /home/Merk888/logs
source /home/Merk888/.price_sync.env && cd /home/Merk888/Merk-888 && /home/Merk888/.virtualenvs/env/bin/python manage.py actualizar_precios_plaza --apply >> /home/Merk888/logs/precios_plaza.log 2>&1
```

El comando usa la zona `America/Bogota` del proyecto y se omite internamente
salvo los martes, viernes y sábados. Las credenciales pueden cargarse desde un
archivo privado fuera del repositorio o desde el entorno de la cuenta antes de
ejecutar el comando.

Revisa el registro de la tarea después de cada ejecución. El esquema actual de
producción también debe contener `productos.precio_anterior`; ese campo existe
en la base actual, pero el historial legacy de migraciones necesita
regularizarse antes de reconstruir una base desde cero.

Para detener la sincronización basta
con desactivar la tarea en el panel de PythonAnywhere; no es necesario cambiar
datos ni código.
