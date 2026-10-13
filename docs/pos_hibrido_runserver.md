# POS integrado: un solo programa, nube primero

## Contrato del proyecto

El objetivo es iniciar el proyecto normal con `python manage.py runserver`.
Con conectividad, las vistas originales conservan sus funciones y escriben en
Aiven. Sin conectividad, ese mismo proceso y origen web atienden las operaciones
habilitadas con PostgreSQL local y un diario durable. Al reconectar, se concilia
el diario, se renueva la copia y se vuelve a Aiven. El inventario admite negativos.

No se debe abrir otra aplicación, cambiar de URL ni perder el carrito. No es
válido reemplazar la aplicación original por el formulario reducido del piloto.

## Estado real de esta implementación

**El flujo híbrido completo todavía no está listo para ventas reales.** Sin una
preparación explícita de este equipo, `runserver` conserva su comportamiento
actual. No activar una bandera manualmente ni instalar los antiguos ZIP
esperando tener ya el flujo completo.

Ya existe el arranque integrado, la sesión persistente, el diario de ventas,
pagos, devoluciones autorizadas y declaraciones de cierre, con envío ordenado
e idempotente. La ampliación del 11/10/2026 permite 24 horas por descarga
validada y revisión auditada en `/local/sincronizacion/`. PTM y nuevos pagos
Nequi quedan excluidos offline por decisión del usuario. El alcance exacto y
los preparadores Windows/Linux están en `instalacion_integrada_runserver.md`.
Esto no equivale a una distribución comercial certificada ni a todo el
historial del negocio disponible offline.

Implementado en esta etapa:

- Confirmación idempotente de la vista ORIGINAL de generar ventas. Una referencia
  UUID opcional identifica la solicitud, su autor, su huella y su factura.
  La confirmación y la venta se guardan en la misma transacción.
- Repetir una solicitud confirmada no vuelve a restar inventario ni sumar caja.
  Dos procesos que presentan la misma referencia obtienen la misma factura.
- La conciliación espera una venta que aún está confirmándose: no interpreta
  prematuramente que la referencia está ausente.
- Transporte de formulario para el modo integrado: conserva los campos originales,
  incluidos clientes, descuentos y Nequi; persiste solo el UUID, no contraseñas.
  No reenvía automáticamente un POST cuya respuesta se perdió.
- Reglas de selección nube/local/sincronización. Una copia vacía, autorización
  vencida, conflicto o confirmación incierta no se ignoran al volver a la nube.
- Preparador de PostgreSQL privado fuera del repositorio, con claves locales
  generadas, permisos de carpeta privados y comprobación de versión del esquema.
  No descarga datos de Aiven ni sobrescribe una instalación existente.
- El `runserver` habitual inicia y detiene ese PostgreSQL cuando encuentra una
  preparación válida. No modifica el servicio global de PostgreSQL ni expone
  la instalación en la red. `migrate`, `shell`, WSGI y configuraciones explícitas
  conservan su ruta habitual.
- Sesiones guardadas en la base local aunque se esté trabajando con la nube.
  El login original conserva el ID y el hash de contraseña del mismo usuario.
  Una autorización offline está limitada por usuario, nube, equipo y vencimiento;
  requiere también una réplica autorizada vigente. No es acceso offline ilimitado.
- Identidad y permisos copiados al iniciar sesión y renovados en solicitudes
  online, como máximo una vez cada 30 segundos por usuario. La identidad vence
  a las 24 horas; prevalece cualquier vencimiento anterior de la réplica.
  Las revocaciones hechas en nube durante una desconexión no pueden conocerse
  hasta reconectar o vencer la autorización.
- Conexiones y cachés de Django aisladas por solicitud e hilo. No se permite
  cambiar la base en una transacción ni anidar un cambio de modo. Las consultas
  offline autorizadas se ejecutan dentro de una transacción de solo lectura.
- Sondeo de conectividad acotado e independiente, sin reintentar escrituras.
  El comando integrado evita que la comprobación inicial de migraciones impida
  arrancar durante una caída de nube confirmada.

La migración `0049_solicitudpos` crea únicamente la tabla de confirmaciones. Se
verifica en PostgreSQL de pruebas; no se ha aplicado a Aiven durante este trabajo.
El transporte se activa por `NOVA_INTEGRATED_POS_ENABLED` cuando el arranque
encuentra una preparación privada válida; esa opción está desactivada por defecto.
Sin esa opción, el navegador y las ventas normales siguen su ruta anterior.

## Trabajo pendiente antes de usarlo con ventas reales

1. Completar el cambio seguro de usuario/caja y la transición desde el perfil de
   copia al modo híbrido completo. La preparación/renovación autorizada ya existe
   en `/local/copia/`; se validó con datos ficticios, no con una descarga de Aiven.
2. Conectar el trabajador de conciliación dentro del proceso integrado y sus
   bloqueos entre varios procesos/equipos. Los pendientes actuales impiden volver
   a escribir directamente en nube, pero todavía no se procesan automáticamente.
3. Adaptar las rutas originales de ventas, pagos, devoluciones y cierre al diario
   reutilizado. Las funciones no preparadas para offline deben informar su límite;
   con Aiven deben seguir disponibles todas las funciones originales.
4. Conectar el nuevo diario de intentos online con el adaptador offline. Ya se
   registra la intención ANTES del envío y se recupera su confirmación en el perfil
   experimental `journal_preview`. Aún no convierte intentos ausentes en ventas
   locales ni reenvía automáticamente formularios. Un timeout nunca demuestra que
   Aiven no guardó la venta. Ver [diario integrado](diario_ventas_integrado.md).
5. Integrar los recursos estáticos offline y el estado de conexión en la interfaz
   original. El sondeo tiene un intervalo: una petición iniciada justo al caer la
   nube aún puede fallar; falta su tratamiento visible sin reejecutarla a ciegas.
6. Preparar actualizaciones de esquema con respaldo y resolver la instalación
   nativa de PostgreSQL cuando una política de Windows bloquea sus bibliotecas.
7. Probar el comando normal con dos equipos, cortes durante la confirmación,
   reinicios, permisos revocados y reconciliación. Los resultados del piloto
   separado no certifican esta nueva integración.

## Retirada del piloto separado

Se eliminan los menús `PRUEBAS.cmd`, `PRUEBAS.sh` y `pilot_launcher.py` del código
fuente. El generador antiguo queda identificado como laboratorio, sin esos menús.
Los ZIP antiguos que ya se entregaron no cambian y no deben usarse como producto
definitivo. No se borran instalaciones, bases, diarios pendientes ni respaldos.

El esquema aprobado por el empaquetador antiguo sigue siendo la versión 2. La
nueva tabla de confirmaciones cambia su huella: el control de versiones debe
rechazar nuevos paquetes separados hasta disponer de una migración explícita.
No omitir esa validación ni agregar la huella a una versión anterior. Esta etapa
no entrega un instalador nuevo ni actualiza instalaciones existentes.

Se conservan por ahora el motor de réplica, la cola, los servicios de negocio,
los controles de autorización, las utilidades de PostgreSQL y las pruebas: son
dependencias reutilizables. Los instaladores heredados restantes se retirarán
cuando el preparador integrado sustituya sus responsabilidades; no se borra una
dependencia que aún es necesaria para verificar o recuperar datos.

## Pruebas de esta etapa

Windows (clúster NUEVO local y desechable):

```powershell
./scripts/test_hybrid_postgres_windows.ps1 -PostgresRoot 'C:/Program Files/PostgreSQL/18' -Python './build/hybrid/native-runtime-venv/Scripts/python.exe' -Port 55480 -TestLabels mainApp.test_pos_request_receipts,mainApp.test_pos_receipt_migration,mainApp.test_cash_receipt,local_pos.test_connectivity
```

Linux (PostgreSQL instalado, usuario sin privilegios):

```bash
bash scripts/test_hybrid_postgres_linux.sh mainApp.test_pos_request_receipts mainApp.test_pos_receipt_migration mainApp.test_cash_receipt local_pos.test_connectivity
```

Prueba de transporte JavaScript sin navegador ni red:

```bash
node --test scripts/test_integrated_sale_transport.cjs
```

Resultado del 3 de octubre de 2026: 60 pruebas de regresión correctas en Windows
(PostgreSQL 18) y las mismas 60 en Ubuntu/WSL (PostgreSQL 16); 4 pruebas del
transporte JavaScript correctas. Además, 25 pruebas unitarias de selección de
modo, respaldos y recuperación correctas. Se corrigió únicamente el constructor
del entorno de pruebas para crear la tabla heredada `rolespermisos` en la base
desechable; no se modificó la tabla del negocio. No se probó impresión física.
No se accedió a Aiven para estas pruebas y todos sus clústeres temporales quedaron
detenidos. Estos resultados validan los componentes descritos, no la conmutación
automática completa, que continúa pendiente.

### Validación del arranque integrado — 9 de octubre de 2026

La nueva batería prueba dos bases independientes mediante sesiones reales de
Django: nube/local/nube, permisos, vencimientos, transacciones, concurrencia,
rechazo de escrituras offline sin diario y el login original. El perfil de
pruebas solo acepta bases desechables en loopback.

```bash
HYBRID_TEST_INTEGRATED=1 bash scripts/test_hybrid_postgres_linux.sh local_pos.test_integrated_requests local_pos.test_connectivity local_pos.test_integrated_bootstrap
```

La prueba por procesos crea DOS PostgreSQL ficticios, usa `manage.main()` y
`runserver --noreload`, apaga realmente la nube ficticia, consulta el autocomplete
original de productos, restaura la nube y reinicia la aplicación todavía offline.
Comprueba que la sesión persista y que ambos PostgreSQL terminen detenidos.
Nunca importa la configuración de producción: sustituye ese módulo únicamente
dentro del intérprete hijo del laboratorio.

```bash
python scripts/test_integrated_runserver.py --pg-bin /usr/lib/postgresql/16/bin
```

Los datos y registros de esta prueba se guardan en una carpeta nueva privada bajo
`~/.novapos-tests/`; no se reutilizan bases anteriores. No ejecutar el perfil de
pruebas como configuración de la aplicación real. La opción `--autoreload`
también comprueba el arranque padre/hijo del recargador, reinyección del perfil
ficticio, caída de nube, cierre y reinicio. No toca archivos para forzar una recarga.

Pasaron las 43 pruebas de la batería integrada y la prueba por procesos en
Ubuntu/WSL con PostgreSQL 16, incluyendo reiniciar el programa con la nube caída.
También se repitieron las 60 pruebas de regresión anteriores en ese entorno, 23 pruebas unitarias sin
base en Windows y las 4 pruebas JavaScript. La validación nativa de PostgreSQL en
Windows NO pasó: Code Integrity (eventos 3077 y 3033) bloqueó `plpgsql.dll` durante
`initdb`. No se desactivó esa protección ni se modificó la política del equipo.
Posteriormente se preparó la configuración predeterminada de este PC con
PostgreSQL en WSL, en modo **solo motor local de desarrollo**, sin habilitar
el híbrido incompleto. Ver [PostgreSQL WSL para desarrollo](postgresql_wsl_desarrollo.md).
No se ha aplicado la migración a Aiven. No usar estos resultados como autorización para operar
ventas offline reales: falta el diario integrado y la conciliación.

### Copia autorizada dentro del runserver — 10 de octubre de 2026

El perfil `database_only` de este PC ahora incluye una pantalla y un trabajador
de copia de referencia. Conserva autenticación, sesiones y escrituras online;
no activa el middleware experimental de conmutación. Copia catálogo, inventario
por sucursal, clientes autorizados, métodos e identidad/permisos en una operación
local atómica. La nube se lee en transacción consistente de solo lectura.

La renovación es periódica y se adelanta tras actividad que puede modificar
datos; los registros locales sin cambios no se reescriben. Se rechazan descargas
concurrentes, otra vinculación, modificaciones locales inesperadas y movimientos
pendientes. No se han aplicado nuevas migraciones ni consultas a Aiven por este
trabajo. No habilita aún ventas offline. Detalles y limitaciones en
[Copia local integrada](copia_local_integrada.md).

La etapa siguiente añadió el diario de intentos para la venta original y la
recuperación automática de confirmaciones perdidas. Está probada por procesos
con reinicio y dos bases ficticias; permanece separada de la activación habitual
hasta completar el adaptador de ventas offline. Ver
[alcance, estados y pruebas del diario](diario_ventas_integrado.md).

### Ventas nuevas offline — 10 de octubre de 2026

El adaptador de ventas ya está conectado en `journal_preview`: mismo formulario,
guardado local atómico, comprobante UUID, inventario negativo, reintentos
idempotentes y envío automático al reconectar. La prueba por procesos incluye
ventas nuevas durante una caída real de la nube ficticia, reinicio y conciliación.
Este avance reemplaza la limitación de ventas nuevas descrita en las etapas
anteriores; **no activa la configuración habitual `database_only` de este PC**.
Egresos, devoluciones, cierres, vinculación Nequi y autorizaciones especiales
siguen necesitando conexión en esta integración. Antes de usar datos reales faltan
activación supervisada, copia autorizada y validación multiequipo del nuevo flujo.
Consultar los detalles y límites actualizados en el documento del diario.
