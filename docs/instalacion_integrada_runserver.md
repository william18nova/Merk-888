# NovaPOS integrado: instalación por computador

Esta instalación usa **el proyecto habitual y `python manage.py runserver`**.
No instala el POS reducido antiguo. Cada PC conserva un PostgreSQL privado,
un UUID propio y su cola; se vincula expresamente a un usuario y una caja.
El puerto local predeterminado es 55460, no 5432.

## Requisitos y seguridad

- Copia del código revisado, Python 3.10–3.13, PostgreSQL y sus herramientas.
- Windows: instalación nativa oficial compatible con las políticas de seguridad
  del equipo. Si SAC bloquea una biblioteca, detener la instalación y verificar
  firma, versión y diagnóstico; **no desactivar SAC, Defender ni añadir exclusiones**.
- WSL es una alternativa explícita para desarrollo. Requiere virtualización,
  distribución Linux, usuario no root, recursos adicionales y mantenimiento de
  Windows y Linux. No se selecciona automáticamente para cajas de clientes.
- Linux: PostgreSQL nativo instalado por el administrador; ejecutar NovaPOS
  con un usuario normal. No reutilizar ni detener el servicio PostgreSQL global.
- Configurar el acceso a Aiven por un canal privado en cada equipo. No incluir
  contraseñas ni tokens en Git, scripts o paquetes de distribución. Las cuentas
  deben tener los permisos mínimos necesarios; nunca un superusuario de Aiven.
- No copiar `.env`, `.novapos-runtime.json`, `%LOCALAPPDATA%/NovaPOS`, una carpeta
  PostgreSQL, respaldos o una cola desde otro PC.

## Windows: preparación

En una copia nueva del proyecto, desde PowerShell:

```powershell
.\scripts\install_integrated.ps1 -PgBin 'C:\Program Files\PostgreSQL\18\bin'
```

Para este entorno de desarrollo con WSL ya instalado:

```powershell
.\scripts\install_integrated.ps1 -PgBin '/usr/lib/postgresql/16/bin' -WslDistribution 'Ubuntu-22.04' -WslUser 'novapos-test'
```

No cambies la política de ejecución o seguridad global para ejecutar el script.
Si una política corporativa lo bloquea, el administrador puede aprobar/firma el
script, o ejecutar manualmente sus comandos de Python tras revisarlos.

## Linux: preparación

```bash
bash scripts/install_integrated.sh /usr/lib/postgresql/16/bin
source .venv/bin/activate
```

Los instaladores crean `.venv`, instalan `requirements.txt` y preparan las
bibliotecas visuales/JavaScript locales; esta etapa necesita internet. No se
depende de la caché del navegador; las fuentes usan la alternativa del sistema.
Para una instalación anterior, ejecutar una vez con conexión:
`python scripts/prepare_integrated_assets.py`. Los archivos se verifican por
SHA-256 al iniciar; no se descargan durante las solicitudes del cajero.
Preparan un clúster **nuevo**, verifican el puerto y conservan los
archivos si hay error. No reinstalan por encima de una instalación existente.

## Activación explícita, sin ventas de prueba reales

Con el entorno Python activado, PostgreSQL local preparado y la conexión
privada de Aiven configurada, obtener los IDs del usuario, caja y turno propios
de este equipo. Primero comprobar SIN `--apply`:

```text
python scripts/activate_integrated.py --pg-bin RUTA_CLIENTES_POSTGRES --usuario-id USUARIO --caja-id CAJA --turno-id TURNO
```

Solo después de verificar esa identidad, repetir con `--apply`. Se respalda la
base privada y se descarga una copia de lectura autorizada. No se crean cobros
de prueba en la nube. El cliente pg_dump debe ser compatible con el servidor.

## Uso diario, incluso después de reiniciar

Windows: activar `.venv\Scripts\Activate.ps1`. Linux: `source .venv/bin/activate`.
Después, el mismo comando en ambos:

```text
python manage.py runserver
```

El proceso inicia el motor privado cuando hace falta (WSL si fue seleccionado),
verifica su identidad/puerto y lo detiene al salir. No se necesita abrir otro POS
durante una caída. Escucha solo en localhost. No modifica PythonAnywhere.

### Navegar y abrir nuevas pestañas sin internet

Usar la dirección local del mismo `runserver`, no la de PythonAnywhere. Con la
copia y la sesión autorizadas, las pestañas nuevas cargan el Inicio, Generar
venta, el visor de productos y las consultas disponibles sin depender de una
página previamente abierta. El Inicio conserva su diseño y accesos; no calcula
indicadores globales a partir del historial incompleto local.

El menú permanece visible. Al seleccionar una función que requiere conexión
aparece un aviso flotante y se conserva la pantalla actual. Si se abre esa URL
directamente o en otra pestaña, se muestra el aviso dentro del diseño habitual,
con el menú para seguir navegando. El servidor sigue bloqueando las operaciones
no soportadas aunque JavaScript esté desactivado; no se reenvían escrituras.
Esto no amplía la autorización de 24 horas ni convierte informes no copiados
en consultas offline. Reiniciar `runserver` tras actualizar y recargar las
pestañas existentes para cargar el nuevo aviso.

Los CSS, JavaScript, imágenes y bibliotecas empaquetadas se sirven por el
manejador estático de `runserver` **antes** del control de conexión/sesión,
también con `DEBUG=False`. No dependen de Aiven ni de haber visitado una página
antes. No hace falta activar DEBUG ni ejecutar `collectstatic` para este modo
local. Una ruta estática inexistente devuelve 404, no una página de negocio ni
un intento de consulta SQL. La configuración de PythonAnywhere no cambia.

En `/local/copia/` revisar que la copia esté vigente; la vigencia nueva es 24 h
desde la última descarga validada, no desde la última venta. En
`/local/sincronizacion/` comprobar pendientes y conflictos. No restaurar una
copia antigua, borrar una cola ni clonar un UUID para resolver un pendiente.
Si la autorización venció mientras había un conflicto, vuelve a iniciar sesión
con internet para renovar tus permisos y revisar el pendiente. Esto no salta
el conflicto ni amplía por sí solo la vigencia del catálogo.

## Alcance y prueba de aceptación

Ventas ordinarias (inventario negativo permitido), pagos/egresos, devolución de
ventas propias incluidas en la copia (máximo 2000 de los últimos 30 días, en la
caja vinculada) y declaración de cierre. La devolución de una venta local que
todavía no tiene ID de nube requiere primero sincronizarla. Los historiales
offline no representan todos los registros del negocio. Nuevas operaciones
Nequi/PTM requieren internet. Un turno con PTM no
se cierra offline. El cuadre final de caja se produce después de sincronizar.

### Descuentos y turnos durante una caída

Después de actualizar la copia con internet, se pueden autorizar compras de
empleados activos de la misma sucursal con la contraseña del comprador. Se
conserva el descuento habitual, incluido el beneficio Web Master. No se permite
autofacturarse. Solo se almacena el verificador de contraseña en la copia
privada: no se habilita el inicio de sesión de otros empleados ni se incluye
la contraseña en la cola de operaciones. Cinco fallos bloquean nuevos intentos
durante 15 minutos. La copia/autorización general sigue venciendo a las 24 horas.

Las claves especiales merk2888 deben generarse **con internet**, desde el
equipo integrado iniciando sesión como Web Master, marcando **Reservar esta clave
para usar sin internet en este equipo**. Después se actualiza la copia local.
La reserva pertenece al cajero y caja previamente vinculados a ese equipo,
no cambia esa vinculación. Volver a la sesión del cajero para actualizar su
copia. La reserva impide usarlas online o en otro equipo; son de un solo uso y
conservan sus 15 minutos de vigencia (no se extienden a 24 horas). Una venta
autorizada a tiempo puede sincronizarse después. Una revocación, cambio de
contraseña o permiso detectado al reconectar produce un conflicto conservado
para revisión; nunca se reinterpreta como una nueva autorización.

Es posible cerrar el turno local y abrir el siguiente desde **Turno de caja**,
con la contraseña del usuario vinculado, su caja y la base declarada. No admite
cambio de cajero o de caja offline. La apertura tiene una referencia UUID y un
ID local provisional: se envía antes de sus ventas/devoluciones/cierre y se
resuelve al ID de la nube. Los reintentos no duplican la apertura. Si otro
equipo abrió la misma caja o existe un turno con horario superpuesto, se
detiene esa secuencia para conciliación. Se recomienda un solo equipo offline
por caja; sin comunicación no se pueden coordinar dos aperturas desconectadas.

Estos cambios no añaden otra migración: utilizan `solicitudes_pos` (0049).
No copiar configuraciones ni identidades de equipo entre computadores. Antes
de probar, actualizar la copia y recargar las páginas para cargar el nuevo JS.

Antes de usar con dinero real: servidor ficticio separado, dos PC con IDs
distintos, corte/reconexión, pérdida del acuse, reinicio, devoluciones concurrentes,
cierre y revisión de conflictos, inventario negativo e impresión física. Estas
pruebas físicas no se sustituyen por pruebas automáticas en un solo equipo.

## Distribución comercial

`runserver` sirve para este flujo de desarrollo, no es un servidor de producción
endurecido. Antes de distribución masiva faltan firma del instalador y sus
binarios, actualización atómica con respaldo/rollback, prueba en Windows/Linux
limpios y soporte operativo. Para clientes se prefiere PostgreSQL nativo
verificado o un servidor local de sucursal con servicios supervisados y acceso
LAN seguro. Un servidor solo en internet no da continuidad offline. A medio
plazo conviene sincronizar por una API HTTPS por dispositivo, sin entregar
credenciales SQL de Aiven a cada caja. No cambiar a esa arquitectura sin una
migración planificada del protocolo y su auditoría.
