# Sistema completo híbrido: base de desarrollo

**Avance posterior:** consulta [Operaciones e instalación](pos_hibrido_operaciones_instalacion.md)
para los adaptadores de venta, egresos, devolución, cierre y el runtime persistente.
El texto siguiente conserva el alcance de la primera etapa de solo consulta.

## Estado real — 1 de octubre de 2026

El objetivo es conservar **las páginas originales de todo el proyecto**, no
construir otra aplicación de ventas. La nube seguirá siendo la autoridad cuando
esté disponible. El equipo deberá poder continuar con información local y
operaciones pendientes durante una caída, mostrando su estado con claridad.

Esta entrega prepara el Django completo local, pero **NO termina la sincronización
de todos los módulos**. Es un laboratorio de solo consulta con datos ficticios.
No sustituye la web de PythonAnywhere, el piloto de caja anterior ni su instalador.

Avance posterior: ya existe la [réplica autorizada de referencia](pos_hibrido_replica_referencia.md)
para catálogo, inventario, clientes permitidos y métodos de pago. Se prueba con
origen y receptor separados en 8905/8904. El laboratorio general 8903 descrito
aquí conserva datos ficticios sembrados directamente; no debe confundirse con
una réplica de todo el negocio ni con la instalación productiva.

### Implementado

- `NovaSoft/hybrid_local_settings.py`: configuración independiente. No importa
  `NovaSoft/settings.py`, no lee `.env`, no usa claves de IA ni credenciales Aiven.
- `local_pos`: vistas y navegación originales, permisos originales, sesiones
  locales separadas y una lista explícita de consultas permitidas.
- Transacciones PostgreSQL de solo lectura para las consultas; también se detecta
  un intento de escritura oculto en una petición GET. No se autoriza una ruta
  nueva automáticamente. Formularios de escritura, borrados, impresión y APIs
  externas quedan bloqueados con un aviso, no con una falsa confirmación.
- Esquema local completo a partir de modelos actuales, incluida la tabla heredada
  de roles/permisos. Esto se ejecuta exclusivamente en una base NUEVA de prueba;
  **no es un procedimiento de migración para una instalación real**.
- `LocalCommand` y `LocalNode`: base del diario transaccional por equipo, UUID
  idempotente, secuencia monotónica, actor, versión y huella de la petición.
  Si falla el diario, se revierte el cambio del dominio en la misma transacción.
  Los reintentos concurrentes del mismo UUID se aplican una sola vez.
  **No hay adaptadores productivos ni endpoints de comandos habilitados todavía**.
- Constructor de dependencias locales: jQuery, jQuery UI, DataTables,
  Font Awesome, ZXing y Chart.js. Conserva las versiones existentes, salvo la
  referencia sin versión de Chart.js que se fija en 4.5.1 en este laboratorio.
  Los archivos conservan sus comentarios/licencias. Google Fonts se reemplaza
  por fuentes del sistema. Los recursos llevan un manifiesto SHA-256.
- La primera preparación descarga bibliotecas públicas por HTTPS desde una lista
  limitada. Las siguientes reutilizan la caché con verificación de integridad;
  las páginas no dependen de los CDN al operar. El manifiesto registra lo
  descargado: todavía falta un lockfile revisado y firma del paquete definitivo.

## Qué se puede probar ahora

Consultas de productos/categorías, inventario (incluidos negativos), clientes,
proveedores, empleados, ventas, pedidos, pagos, horarios, turnos y métricas.
El menú completo sigue siendo el del proyecto. Las opciones no integradas muestran
un aviso. Algunas rutas de detalle y filtros todavía necesitan una auditoría
funcional completa; una página habilitada no implica que todas sus acciones lo estén.

Datos ficticios: dos productos, uno con inventario -500; una venta de $2.500;
un pago de $1.000; un horario y una caja de prueba. No se movió dinero real.

## Abrir la prueba en este PC

El laboratorio preparado durante el desarrollo usa:

- Página: `http://127.0.0.1:8903/local/estado/`
- Usuario ficticio: `laboratorio`
- Contraseña ficticia: `prueba-local-2026`

No emplear esas credenciales en producción. Solo escucha en 127.0.0.1.
La sesión corresponde a esta base ficticia, no a usuarios del negocio.

Desde WSL Ubuntu-22.04, con el usuario de pruebas ya instalado:

```bash
cd /mnt/c/Users/LENOVO/OneDrive/Escritorio/merk2888/Merk-888
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py --serve
```

El proceso crea un directorio privado temporal y un PostgreSQL nuevo en el puerto
55437. No reutiliza ni borra bases anteriores. Si el puerto está ocupado, se
detiene. Ctrl+C cierra ese servidor y su PostgreSQL. Cada ejecución crea datos
ficticios nuevos, por lo que **no sirve como instalación permanente**.
Los directorios de prueba se conservan; no se eliminan automáticamente.

La caché de bibliotecas públicas está en `build/hybrid/full-local-asset-cache/`
(ignorada por Git). La configuración y la base quedan fuera del repositorio.

## Pruebas

Verificado en esta entrega: **26 pruebas** del runtime completo con PostgreSQL,
**48 pruebas de regresión** del cliente/piloto de ventas y la prueba de navegador
en escritorio y móvil. El navegador registró **0 solicitudes externas** y
**0 errores JavaScript** en los recorridos cubiertos. Las métricas mostraron
$2.500 vendidos, $1.000 pagados y $1.500 restantes con los datos ficticios.
Esto no certifica aún sincronización general, impresión ni todos los formularios.

```bash
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py --test --port 8904 --pg-port 55438
```

Con el servidor 8903 abierto, `scripts/test_full_local_browser.cjs` usa Playwright
y Edge. Bloquea toda conexión que no sea al proceso local; verifica login,
navegación original, búsquedas y recarga de tablas, calendario, gráficos,
balance de los datos ficticios, menú móvil y bloqueo de operaciones no habilitadas.
No imprime. Guarda evidencia en `outputs/full-local/`.

## Lo siguiente: sincronización por operaciones del negocio

No se habilitarán escrituras por el hecho de que un formulario ya abra localmente.
Cada módulo necesita el mismo contrato validado en nube y local, pruebas de
reintentos, permisos y conflictos, y replicación entrante de sus datos.

| Área | Regla necesaria antes de habilitar escrituras offline |
| --- | --- |
| Ventas e inventario | Integrar el protocolo de ventas ya probado en este runtime; movimientos incrementales, negativos permitidos, sin sobrescribir stock completo. |
| Pagos/egresos | UUID por pago, concepto y método válidos, autor, fecha e impuesto preservados; correcciones auditadas con versión. No descontar de caja automáticamente. |
| Devoluciones | Referencia a venta/detalle y cantidades ya devueltas; impedir devolución duplicada entre equipos y conciliar importes. |
| Caja/PTM | Propiedad del turno, movimientos de efectivo y conciliación; no cerrar un turno con operaciones sin confirmar. No simular confirmación externa PTM. |
| Productos/clientes/proveedores/pedidos | Identidades globales, altas temporales y versiones para cambios concurrentes; no usar IDs autoincrementales locales como identidad global. |
| Horarios | Versiones, conflictos de asignación y alcance de cambios de rotación; preservar quién modificó y cuándo. |
| Reportes | Última sincronización visible; distinguir lo confirmado en nube de pendientes locales. No presentar un total local como global actualizado. |
| Usuarios/permisos | Vinculación por equipo y autorización offline con vencimiento; no repartir todas las contraseñas o secretos del servidor. |

Orden de implementación previsto:

1. Réplica inicial autorizada y cambios incrementales entrantes con cursor,
   versiones, borrados lógicos y confirmación por lote. Recuperación tras cortes
   sin saltar cambios ni reemplazar operaciones locales pendientes.
2. Conectar venta existente y un módulo pequeño (pagos) al registro común; después
   devoluciones/caja y demás módulos, manteniendo las páginas originales.
3. Registrar durablemente el intento antes de contactar la nube. Con conexión,
   procesar en nube; ante respuesta perdida, consultar/reintentar **el mismo UUID**.
   No convertir un timeout en otra venta o pago.
4. Al operar offline, cambio local + diario en una transacción. Al reconectar,
   aplicar una vez en nube, conciliar la proyección local con la confirmación y
   retirar el estado pendiente solo tras un acuse verificable. No eliminar el
   historial para ocultar conflictos.
5. Sincronización entre varios equipos con secuencias y versiones, no una lista
   global ordenada únicamente por la hora de cada computador.
6. Cifrado, respaldos, restauración, actualización reversible y paquete firmado
   para Windows y Linux. El ejecutable anterior bloqueado por Windows no se ha
   usado ni se ha eludido ese bloqueo.

Telegram, verificación en vivo de Nequi y operaciones reales contra PTM necesitan
internet. Offline se podrán registrar operaciones admitidas por sus reglas, pero
no se puede afirmar que un proveedor externo las confirmó sin comunicarse con él.

## Despliegue

No se desplegó nada a PythonAnywhere ni se modificó Aiven. No agregar `local_pos`
a `INSTALLED_APPS` de producción. El banner de pruebas en `base.html` solo se
activa con el contexto específico local; no cambia la interfaz normal de la nube.
