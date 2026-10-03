# Aceptación del piloto híbrido 0.4.1 — sin impresión

## Alcance

Datos completamente ficticios, HTTPS de prueba y PostgreSQL 16 desechable en loopback. No se utilizan Aiven, PythonAnywhere, cuentas del negocio, impresoras ni sus agentes. El certificado de prueba solo se confía dentro de los procesos de este ensayo, no en Windows ni en la configuración global del equipo.

Los cortes se provocan únicamente entre las cajas de prueba y ese servidor. No se desconecta la red del usuario. Los reinicios se prueban terminando abruptamente los procesos y abriéndolos de nuevo; no se corta la energía del computador ni se simula una avería física del disco.

## Escenarios

- Dos procesos Linux: 30 ventas por gramos con stock inicial cero; corte de conexión, reinicio, desbloqueo y envío automático por el trabajador de sincronización. Inventario final -15.000; efectivo por caja $28.500. Cierre real del turno con diferencia cero.
- Dos ejecutables Windows: 24 ventas, reinicio y sincronización automática. Inventario final -12.000; efectivo por caja $22.800. Reintento de la primera venta sin duplicados y finalización de las dos sesiones.
- Cierre abrupto de la aplicación mientras la petición está en curso y después de que el servidor ya guardó la venta. Conservación de la operación local y conciliación una sola vez.
- Respuesta perdida y cobros repetidos con el mismo UUID; rechazo cuando se reutiliza el UUID con otros datos.
- Precio cambiado mientras hay una venta pendiente: se respeta el precio autorizado de esa venta y se actualiza el catálogo para las siguientes.
- Producto convertido en PTM: pendiente preservado y ventas nuevas bloqueadas; reintento tras resolver la causa sin duplicados.
- Usuario desactivado, autorización vencida, reloj adelantado/atrasado y rechazo de fechas fuera de autorización. Los pendientes válidos se pueden enviar después de vencer la sesión.
- Respaldo cifrado descargado desde una caja, recuperación por HTTPS, aprobación obligatoria del Web Master, invalidación del equipo anterior y exigencia de un nuevo inicio de sesión en el reemplazo.
- SQLite lleno mediante un límite de páginas, sin llenar el disco del PC: reversión de la escritura completa. Fallo al guardar la confirmación: el reintento después del reinicio no crea otra venta.
- Navegador Edge en Windows: dos cajas y tres pestañas; nombre/ID/barras, gramos, stock negativo, cambio, historial después de recargar, respaldo, bloqueo/PIN y vista móvil/escritorio.

## Problemas encontrados y correcciones

1. El aviso al intentar finalizar con pendientes quedaba detrás del diálogo y podía ser reemplazado por el estado de red. Ahora permanece visible dentro de la confirmación, sin permitir finalizar.
2. Un reloj Windows aproximadamente un segundo detrás del servidor podía hacer que la primera venta pareciera anterior al inicio de sesión. Se admite un margen máximo de dos minutos antes del inicio, usando el inicio autorizado como fecha efectiva en ese caso. No se extiende la fecha de vencimiento. Se siguen rechazando fechas demasiado antiguas, futuras o posteriores al vencimiento. El hash para detectar duplicados conserva el contenido original recibido.

## Repetir las pruebas

Las suites del cliente no necesitan Django ni internet:

```text
python -B -m unittest hybrid_client.test_client hybrid_client.test_backup hybrid_client.test_recovery
```

El servidor se comprueba con `scripts/test_hybrid_postgres_linux.sh`. Por defecto incluye aceptación por procesos, recuperación, concurrencia, migraciones y pruebas existentes de recibos/PTM (estas últimas no imprimen). El script acepta nombres de suites como argumentos para ejecutar un caso concreto. Detiene PostgreSQL al salir, incluso ante fallos.

Para los ejecutables Linux se define `HYBRID_ACCEPTANCE_EXE` con la ruta absoluta del paquete. Sin ella, se comprueba el cliente desde el código fuente.

Los casos con navegador y ejecutables Windows requieren Node/Playwright/Edge y se omiten expresamente cuando esos componentes no se indican. En este PC se usan los helpers `scripts/test_hybrid_browser_bridge.cjs` y `scripts/test_hybrid_windows_processes.cjs --bridge CARPETA`: se lanzan desde Windows y las rutas compartidas se pasan al ensayo Linux mediante `HYBRID_ACCEPTANCE_BROWSER_BRIDGE` y `HYBRID_ACCEPTANCE_WINDOWS_BRIDGE`. Esto evita depender de WSLInterop. Usar carpetas nuevas en cada ejecución; sus mensajes contienen exclusivamente credenciales y resultados ficticios.

## Límites de esta aceptación

No equivale a probar todas las marcas de computador, distribuciones Linux, apagones físicos ni fallos de almacenamiento reales. Linux se ejecuta en Ubuntu 22.04/WSL 2. La impresión está excluida por petición del usuario. La funcionalidad sigue siendo un piloto de efectivo; no habilita automáticamente otros módulos offline. Tampoco constituye un despliegue en producción ni una instalación masiva.

## Resultados finales — 30 de septiembre de 2026

| Comprobación | Resultado |
| --- | --- |
| Batería Django/PostgreSQL, concurrencia, migraciones y aceptación completa | 81 aprobadas, sin omisiones |
| Cliente Windows, sin la suite de impresión | 43 aprobadas |
| Cliente Linux, sin la suite de impresión | 43 aprobadas |
| Navegador Edge, dos cajas y tres pestañas | Aprobado con el paquete Linux 0.4.1 |
| Dos procesos Windows contra nube de pruebas por HTTPS | Aprobado con el ejecutable instalado 0.4.1 |
| Instalación Windows por usuario, acceso directo y rechazo de sobrescritura | Aprobado |
| Instalación Linux en HOME temporal, manifiesto, acceso directo y rechazo de sobrescritura | Aprobado |
| Exportación cifrada desde ambos paquetes, verificación y recuperación en carpeta de revisión | Aprobado |

La batería conjunta tardó 122,663 segundos. PostgreSQL se detuvo al terminar; los helpers cerraron las cajas ficticias. Se creó el acceso directo **Nova POS** de la versión 0.4.1 en este Windows, sin vincularlo a producción. No se hicieron commits, push, despliegues ni migraciones sobre la base del negocio.

Las solicitudes rechazadas deliberadamente (usuario desactivado, credenciales antiguas, contenido cambiado) y la interrupción TLS al matar un proceso forman parte de las pruebas de fallo; no son ventas perdidas.

Incidencias del entorno, distintas del POS: WSL dejó de poder lanzar ejecutables Windows directamente, por lo que el ensayo se ejecutó con helpers nativos de Windows y coordinación por archivos ficticios. Además, el Python de Microsoft Store no veía la instalación normal de AppData; el ejecutable instalado se validó desde Node nativo, y el respaldo Windows desde el paquete distribuible. No se deshabilitó ninguna protección del sistema para resolverlo.
