# Sincronización de precios Plaza → merk2

El comando consulta `public.productos` en la base externa y actualiza
`Producto.precio` en merk2 usando el mapeo versionado en
`mainApp/data/price_sync_plaza_map.json`.

La hoja contiene 76 relaciones activas (71 productos de origen y 76 destinos).
Las equivalencias no directas declaran una regla `price_multiplier` explícita.
Cinco usan una relación 1:1 y `cidras` convierte el precio de origen a precio
por unidad mediante `precio_destino = precio_origen × 1000`.

La operación tiene estas protecciones:

- la conexión externa se abre con transacción de solo lectura y TLS;
- cada ID se valida también contra el nombre esperado;
- toda equivalencia no directa requiere una regla de precio explícita;
- el precio convertido se valida y redondea antes de compararlo o escribirlo;
- se valida todo el lote antes de escribir;
- sin `--apply` el comando siempre es una simulación;
- al actualizar, conserva el valor anterior en `precio_anterior`;
- una variación superior a `PRICE_SYNC_MAX_PRICE_FACTOR` bloquea el lote;
- el lote local se aplica dentro de una transacción.

Este proceso no recalcula `rentabilidad`: el proyecto admite precios distintos
por proveedor y esa métrica se actualiza actualmente cuando se registra el
costo de una compra. Definir otro costo de referencia requeriría una regla
contable adicional.

## Validación realizada el 21 de agosto de 2026

La base `desarrollo-william…` fue confirmada como origen Plaza. Su tabla
`public.productos` contiene 3.458 referencias y todos los productos activos del
mapeo coincidieron por ID y nombre. La base `merk-888…` es el destino merk2.

La simulación real terminó sin escrituras con este resultado:

```text
76 mapeos activos
71 productos de origen
76 productos de destino
1 precio cambiaría
75 precios permanecen iguales
0 inconsistencias de ID o nombre
0 variaciones extremas
```

El único cambio pendiente durante esa simulación fue `FR PAPA KG`, de 2.60 a
2.80, usando el origen `Papa Negra x gr`.

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

La prueba debe informar 76 mapeos, 71 productos de origen y 76 productos de
destino. No debe mostrar inconsistencias de IDs/nombres ni variaciones extremas
sin revisar.

Solo después de aprobar la simulación:

```bash
/home/Merk888/.virtualenvs/env/bin/python manage.py actualizar_precios_plaza --apply --force
```

## Programación

PythonAnywhere debe ejecutar diariamente, a las 14:00 UTC. Usa el ejecutor
versionado, que carga el entorno privado, valida las variables y registra toda
la ejecución (incluidos los errores previos a Django):

```bash
/bin/bash /home/Merk888/Merk-888/scripts/run_price_sync.sh
```

Para probar el ejecutor manualmente sin esperar al calendario, agrega
`--force`. Esta variante sí aplica los cambios, por lo que debe usarse solo
después de revisar una simulación:

```bash
/bin/bash /home/Merk888/Merk-888/scripts/run_price_sync.sh --force
```

El comando usa la zona `America/Bogota` del proyecto y se omite internamente
salvo los martes, viernes y domingos. Las credenciales pueden cargarse desde un
archivo privado fuera del repositorio o desde el entorno de la cuenta antes de
ejecutar el comando.

Revisa `/home/Merk888/logs/precios_plaza.log` después de cada ejecución. Cada
intento incluye fecha UTC, fecha de Bogotá, commit ejecutado y estado final.
El esquema actual de
producción también debe contener `productos.precio_anterior`; ese campo existe
en la base actual, pero el historial legacy de migraciones necesita
regularizarse antes de reconstruir una base desde cero.

Para detener la sincronización basta
con desactivar la tarea en el panel de PythonAnywhere; no es necesario cambiar
datos ni código.
