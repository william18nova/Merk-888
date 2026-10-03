# Pruebas del POS híbrido en este PC

Este laboratorio es independiente del negocio. Usa el cliente instalado de
Nova POS 0.4.1-pilot, un servidor Django local en WSL y PostgreSQL local. **No
se conecta a Aiven ni a PythonAnywhere. No uses dinero ni ventas reales.**

## Abrir

1. Haz doble clic en **Nova POS - Pruebas**, en el escritorio.
2. Espera a que el panel diga **Listo para tus pruebas**. WSL puede tardar al
   arrancar después de reiniciar Windows.
3. Pulsa **Abrir caja de pruebas**. Se abrirá el navegador predeterminado.

Direcciones, con el laboratorio abierto:

- Panel: `http://127.0.0.1:8895/`.
- Caja: `http://127.0.0.1:8793/`. Si la abres manualmente, escribe la dirección
  en una pestaña nueva; el POS bloquea enlaces provenientes de otro origen.

El acceso **Nova POS** sin «Pruebas» es otra instalación/configuración.
Para este ensayo usa únicamente **Nova POS - Pruebas**.

## Acceso ficticio

El asistente vincula la caja e inicia la sesión automáticamente.

- Usuario: `cajero_prueba`.
- Contraseña del usuario ficticio: `PruebasNova2026!`.
- Clave local para desbloquear: `pruebas123`.

Estas claves no pertenecen a ningún usuario real. No las uses en producción.
La autorización de una sesión dura 12 horas. Si venció, reconecta y pulsa
**Abrir / renovar sesión**. Primero se deben confirmar los pendientes de la
sesión anterior; no se eliminan para abrir otra.

## Productos preparados

| Producto | Código de barras | Precio | Cantidad inicial |
| --- | --- | ---: | ---: |
| TOMATE DE PRUEBA POR GRAMO | 7700000000001 | $3,80 por gramo | 0 gramos |
| ARROZ DE PRUEBA | 7700000000002 | $2.500 por unidad | 20 |
| AGUA DE PRUEBA | 7700000000003 | $1.800 por unidad | 3 |

Las existencias cambian con las pruebas y **no se restablecen al abrir otra vez**.
Puede haber ventas de validación técnica previas: revisa el contador del panel
antes de comenzar para comparar el incremento de tus propias ventas.

## Prueba guiada

1. Con el panel conectado, busca **tomate**, escribe **500** en cantidad y
   selecciónalo. El total debe ser **$1.900**.
2. Escribe **2000** como efectivo recibido y registra la venta. El cambio
   debe ser **$100**. Cierra el comprobante **sin imprimir**.
3. En el panel, verifica que las ventas recibidas aumenten en 1 y no queden
   pendientes. El inventario puede ser negativo: eso está permitido.
4. Pulsa **Simular sin conexión**. Registra otra venta en la caja.
5. Debe aparecer **1 pendiente** en el PC, sin aumentar todavía el contador
   del servidor. Espera unos segundos para que se actualice el panel.
6. Ya cobrada la venta, pulsa **Reiniciar caja** en el panel. Recarga la
   pestaña de la caja. El pendiente debe seguir guardado.
7. Pulsa **Reconectar**. El pendiente debe llegar al servidor, desaparecer de
   los pendientes y aumentar el contador de ventas **una sola vez**.
8. Pulsa **Sincronizar ahora** de nuevo: no debe crear otra venta.

No reinicies la caja mientras todavía estás armando un carrito sin cobrar:
esta prueba comprueba la persistencia de **ventas registradas**, no la
recuperación del carrito que aún estás editando.

También puedes abrir varias pestañas de la caja, bloquear/desbloquear con la
clave local, buscar por nombre/ID/código y probar el respaldo cifrado. Varias
pestañas son **una misma caja**, no varios equipos independientes.

## Qué significa «sin conexión» aquí

El botón simula la indisponibilidad del servidor para esta caja. No cambia la
red del PC, ni el router, ni los datos móviles, ni el firewall. El servidor de
pruebas está en el mismo PC: apagar el Wi-Fi por sí solo no lo desconecta.
Por eso debes usar el botón para ensayar el modo offline.

Es el piloto de **ventas en efectivo**. No es todavía toda la página del
negocio offline: Nequi, tarjeta, PTM, devoluciones, gastos, calendarios y
configuración administrativa no forman parte de esta caja offline.

## Cerrar y volver a abrir

Pulsa **Cerrar laboratorio** y luego cierra sus pestañas. Se detienen la caja,
el servidor y su PostgreSQL sin borrar ventas ni pendientes. No cierra otras
instalaciones del POS ni otras distribuciones WSL.

Al abrir de nuevo el acceso directo se conserva el historial y se inicia
con conexión simulada disponible; las ventas pendientes pueden sincronizarse
automáticamente. Para seguir ensayando offline, pulsa otra vez **Simular sin
conexión**.

Cerrar solo la pestaña del navegador no detiene los procesos. Usa el botón
del panel para terminar el laboratorio ordenadamente.

## Ubicación de los datos y límites de esta preparación

- Datos de caja: `%LOCALAPPDATA%\NovaPOS-Lab\data`.
- Registros de diagnóstico: `%LOCALAPPDATA%\NovaPOS-Lab\logs`.
- PostgreSQL ficticio en WSL:
  `/home/novapos-test/.local/share/novapos-lab/postgres` (puerto 55434).
- API del servidor local: `https://127.0.0.1:8894`.
- El certificado es exclusivo del proceso de pruebas; no se instaló como raíz
  de confianza del sistema ni se desactivó la validación HTTPS.

No copies estas carpetas para crear otra caja, no borres su base para
«resolver» un pendiente y no compartas el archivo de vinculación. Si aparece
un conflicto, guarda el mensaje y revisa el historial antes de volver a cobrar.

Este acceso directo depende del repositorio actual, Node, Ubuntu-22.04 y el
entorno Python de pruebas instalado en este PC. **No es el instalador general
del negocio**. Mover el repositorio requiere volver a crear el acceso directo.
La instalación en otros equipos y la conexión al servidor real se validan
por separado, después de estas pruebas. No se hizo despliegue de producción.

## Comprobación de esta preparación (30 de septiembre de 2026)

Verificado en el navegador Edge de este PC: venta conectada, venta offline,
reinicio de la caja con un pendiente, reconexión sin duplicados, stock negativo,
cambio correcto, protección por origen y token, y panel en móvil/escritorio.
También se cerró todo el laboratorio y se abrió mediante el acceso directo:
se conservaron las ventas y se pudo finalizar e iniciar una sesión nueva.

Estado entregado: **3 ventas ficticias de validación, $5.700 recibidos, 0
pendientes y 0 conflictos**. Tomate: -1.500 gramos; arroz: 20; agua: 3.
Se dejó una sesión nueva con carrito vacío. No se realizó impresión.

La interfaz 0.5.0 con diseño de «Generar venta» ya está implementada y probada.
Windows bloqueó el ejecutable nuevo mediante Control de aplicaciones. No se
desactivó esa protección: este acceso directo conserva la versión 0.4.1.
Para actualizarlo, primero se debe aprobar el paquete nuevo por el procedimiento
de seguridad correspondiente. La demo temporal de interfaz, cuando está abierta,
usa `http://127.0.0.1:8901/`, con datos desechables y sin impresión real; no es
la base persistente del laboratorio ni una instalación de producción.
