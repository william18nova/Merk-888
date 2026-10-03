# Ventas en el Django local completo

**Actualización:** esta página documenta la etapa anterior de efectivo. El
alcance vigente de medios mixtos, egresos, devoluciones, cierre e instalación
persistente está en [Operaciones e instalación](pos_hibrido_operaciones_instalacion.md).

Etapa de laboratorio — 2 de octubre de 2026. Mantiene la página, el carrito,
los autocompletados y el modal de cobro originales de Generar venta. No se ha
desplegado a PythonAnywhere ni conectado con datos reales de Aiven.

## Qué quedó integrado

- Venta en efectivo sin cliente asociado, con cantidades enteras: unidades o
  gramos según el producto, la promoción de bolsas y el redondeo compartidos.
- El inventario puede ser cero o negativo. La fila de inventario debe existir
  en el respaldo autorizado; no se inventa la información de un producto faltante.
- Diario PostgreSQL durable con UUID, usuario, sesión y secuencia. Antes de
  contactar la nube se confirma una intención local; todavía no modifica stock.
- Si responde la nube, se conserva su ID de venta y comprobante. Si falla la
  comunicación, se confirma la venta pendiente y su movimiento local en una
  transacción. Un rechazo de permisos no se trata como una caída de internet.
- Reintentar, recargar o perder la respuesta conserva el mismo UUID. No se vuelve
  a cobrar como si fuera una venta distinta. Los conflictos se conservan y
  detienen nuevas ventas hasta revisión; no se eliminan para ocultarlos.
- El POST de cobro intenta un envío con timeout de tres segundos. No espera a
  vaciar toda una cola grande: la secuencia restante la procesa el ciclo de fondo.
- Historial del equipo con pendiente, sincronizada o revisión y comprobante con
  recibido y cambio. El registro local está en `LocalCommand`, no es una fila
  ficticia en `Venta` con un número autoincremental que pueda chocar con la nube.
- Las escrituras ajenas a este adaptador siguen bloqueadas. No se habilitaron
  formularios arbitrarios por estar presentes en el menú.

## Inventario y varios equipos

Cada corte de referencia declara hasta qué secuencia de la sesión incluye sus
ventas. El receptor calcula:

`inventario visible = inventario del corte remoto - cantidades locales no incluidas en ese corte`

Eso evita dos errores: perder la venta offline al descargar stock o restarla dos
veces cuando la nube ya la registró. Funciona también si el acuse se perdió y el
inventario remoto llega primero. Los movimientos del otro equipo entran en el
corte remoto y los del equipo actual aún pendientes se mantienen superpuestos.

La proyección, estado y diario se confirman juntos. No se aplica una descarga
parcial, un cursor que retrocede o un corte sin confirmación de ventas cuando el
equipo ya utiliza este protocolo. Los cambios manuales fuera de la réplica se
detectan y no se sobrescriben.

## Prueba manual en este PC

- Generar venta: `http://127.0.0.1:8906/generar_venta/`.
- Estado y controles de prueba: `http://127.0.0.1:8906/local/estado/`.
- Usuario ficticio: `laboratorio`.
- Contraseña ficticia: `prueba-local-2026`.

1. Abre Generar venta y registra un producto ficticio en efectivo.
2. Abre Estado en otra pestaña y pulsa **Simular sin conexión**. Solo interrumpe
   el transporte al servidor ficticio, no el Wi-Fi del PC.
3. Continúa en la misma página de Generar venta. Agrega productos y cobra otras
   ventas. En **Ventas de este equipo** deben aparecer pendientes.
4. Puedes recargar la página: los cobros registrados no son simples borradores
   del navegador; están guardados en PostgreSQL.
5. En Estado pulsa **Restablecer y sincronizar**. Revisa que el historial marque
   las ventas como sincronizadas, sin nuevas referencias duplicadas.

El laboratorio tiene dos bases distintas y un origen HTTP ficticio en 8907.
Los controles de simulación solo existen cuando el lanzador activa explícitamente
el modo de demostración. No se imprime ni se abre el cajón.

Para crear una NUEVA prueba desde WSL, si los puertos están libres:

```bash
cd /mnt/c/Users/LENOVO/OneDrive/Escritorio/merk2888/Merk-888
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py \
  --serve --sale-demo --port 8906 --source-port 8907 --pg-port 55440
```

**No es un instalador permanente ni un lanzador de recuperación:** cada ejecución
crea bases ficticias nuevas. Los directorios anteriores se conservan. La prueba de
recuperación automatizada simula interrupciones y reabre conexiones al mismo
almacenamiento; no equivale a haber probado un corte de corriente físico. Falta
el arranque permanente y la recuperación guiada del runtime completo para Windows
y Linux. No intentar usar este comando con datos del negocio.

## Verificación reproducible

```bash
/home/novapos-test/hybrid-venv/bin/python -B scripts/full_local_lab.py \
  --test --sales-tests --port 8908 --pg-port 55438
```

Esta batería usa un origen y DOS receptores PostgreSQL separados. Cubre ventas
conectadas, offline, reinicio simulado después de guardar la intención, fallo de
disco simulado después del commit remoto, respuesta perdida, reenvíos simultáneos
del mismo UUID, inventario negativo, integración de dos equipos, autorizaciones
vencidas/revocadas, CSRF y aislamiento de usuarios. Repite también las pruebas de
referencia y del protocolo anterior de nube.

`scripts/test_full_local_sales_browser.cjs` recorre la página original en Edge,
en escritorio y móvil. Crea cuatro ventas ficticias, simula una desconexión con
carrito abierto, recarga sin conexión y pierde una respuesta HTTP local después
del commit. Bloquea todas las solicitudes del navegador fuera de 8906 y no imprime.
Guarda evidencias en `outputs/full-local-sales/`.

Verificado el 2 de octubre de 2026:

- 89 pruebas de integración PostgreSQL correctas (184,36 segundos).
- 45 pruebas de regresión del cliente y página original correctas.
- Recorrido Edge de escritorio y móvil correcto: cuatro ventas ficticias,
  recuperación sin duplicados, controles de desconexión, cero errores JavaScript
  y cero solicitudes del navegador a servicios externos. Sin impresión.

## Límites y siguiente etapa

- Solo efectivo, sin cliente asociado. Nequi, tarjeta, mixtos, PTM, descuentos
  especiales, devoluciones, caja, auditoría de carritos y egresos aún no están
  habilitados para escritura en este runtime.
- No se permite cerrar turnos offline ni se inventa un saldo global local.
  Las ventas de este equipo se consultan en su historial; los reportes completos
  todavía no incorporan estos registros locales.
- Se conserva la autorización limitada de la réplica (hasta dos horas desde el
  último corte, y nunca más que la sesión). Hace falta el flujo de renovación e
  inicio/cierre de sesión del instalador definitivo.
- El motor en segundo plano espera 30 segundos entre ciclos exitosos y aumenta
  hasta cinco minutos tras fallos. Una venta nueva intenta comunicar inmediatamente.
  No es una réplica instantánea por cada cambio remoto.
- La migración `0047_hybrid_replica_sale_cursor` agrega el cursor de ventas al
  metadato del servidor. No modifica ventas históricas. Está preparada para
  desplegarse después de 0046; no se ejecutó en producción.
- Falta medir carga real, resolver conflictos mediante una pantalla auditada,
  integrar los demás módulos y preparar instalación, migraciones locales,
  respaldos y actualizaciones permanentes. No publicar esta etapa como POS final.

La base de negocio ya está conectada con las ventas de laboratorio. El siguiente
módulo propuesto es pagos/egresos con el mismo UUID, autor, fecha, medio y 4x1000,
antes de devoluciones y cierre de caja.
