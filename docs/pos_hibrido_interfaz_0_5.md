# Interfaz del cliente híbrido 0.5.0

## Implementado

- Diseño azul de Nova Advance, dos paneles de «Generar venta» y fondo continuo.
- Sucursal y punto de pago de la sesión, sin posibilidad de cambiar la caja
  asignada desde el cliente.
- Autocomplete local por nombre/ID y otro por código de barras, con navegación
  por flechas, Enter y Escape. Enter en barras exige coincidencia exacta; no
  confirma pagos ni agrega otro producto por aproximación.
- Cantidad en unidades/gramos, modificación de cantidades, eliminación y filtro
  del carrito; subtotales calculados por el servicio local compartido, incluida
  la promoción de bolsas. Se permiten existencias cero y negativas.
- Botón «Generar venta» y modal de efectivo, total, recibido y cambio. Enter en
  el modal confirma; Alt+Espacio abre/confirma. Ctrl+1/2/4 enfoca nombre/barras/cantidad.
- Indicador compacto de conexión, pendientes, sincronización o conflictos.
  No se cambia de pantalla ni se vacía el carrito por perder la conexión.
- Historial plegable, confirmación para vaciar el carrito, impresión/respaldo
  y bloqueo de sesión conservados.
- Todos los recursos visuales son locales. No requiere CDN, fuentes remotas
  ni una llamada a la nube para buscar en el catálogo descargado.

El protocolo, la contabilidad, los permisos de servidor y el esquema de la
base local no se cambiaron. No se migraron ni modificaron bases del negocio.
Esto es una adaptación del cliente híbrido, **no** una conversión de todas las
páginas Django a offline. Continúa limitado a efectivo, sin cliente asociado,
cámara ni recuperación del carrito sin cobrar.

## Verificación

- 43 pruebas del cliente/respaldo/recuperación en Windows y 43 en Linux/WSL.
- Navegador Edge: diseño desktop/móvil sin desbordamiento, ausencia de peticiones
  externas, búsqueda con acentos, teclado, códigos exactos y escaneos sucesivos,
  cantidades, inventario negativo, filtro y vaciado, promoción, cambio, historial,
  bloqueo y reintento sin duplicados después de perder una respuesta local.
- No se imprimió ni se usaron datos de producción.

## Instalación pendiente en este PC

Se compilaron los paquetes Windows y Linux. Al instalar el nuevo ejecutable
Windows, **Control de aplicaciones lo bloqueó**. No se intentó evadir ni
desactivar esa protección. El paquete necesita aprobación/firma según el
procedimiento de seguridad del equipo antes de validar su instalación Windows.

El acceso «Nova POS - Pruebas» conserva la versión instalada 0.4.1 y su base
persistente. Se verificó su vuelta al servicio con 6 ventas ficticias y cero
pendientes; esas ventas no se borraron ni se reemplazaron.

La previsualización de desarrollo está en `http://127.0.0.1:8901/` mientras
siga abierto su servidor temporal. Usa datos desechables distintos y fue
iniciada y probada antes del intento de instalación. No es el cliente instalado,
no sustituye la aceptación contra el laboratorio persistente y no imprime.

Para la instalación definitiva siguen pendientes la aprobación del paquete
Windows, su prueba contra el servidor aislado, y posteriormente las pruebas
en dos equipos y con impresión física. No hubo despliegue a PythonAnywhere.
