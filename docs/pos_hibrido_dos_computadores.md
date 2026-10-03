# Prueba ficticia en dos computadores — NO producción

Este paquete conserva la interfaz Django de ventas. Cada computador tiene su
propio PostgreSQL y una identidad distinta; uno de ellos aloja además el servidor
ficticio compartido. No usa Aiven, PythonAnywhere, Telegram ni notificaciones
bancarias reales. No conectes impresoras durante esta prueba.

## Antes de comenzar

- Dos computadores en la misma red privada (sin aislamiento de clientes Wi-Fi).
- Windows: Python **3.12 de 64 bits** y PostgreSQL instalado con sus herramientas.
  Se probó PostgreSQL 18.6. Linux: Python 3.10–3.12, módulo venv y PostgreSQL;
  se probó Ubuntu 22.04/WSL con PostgreSQL 16.
- Internet solo para instalar dependencias la primera vez. El paquete no incluye
  los instaladores de Python/PostgreSQL ni es un EXE autónomo firmado.
- Descomprime en una carpeta local, fuera de OneDrive/Dropbox/carpetas de red.
  No ejecutes como administrador/root ni desactives la seguridad de Windows.
- Reserva la IP privada del computador 1 en el router durante la prueba; el
  certificado se genera para esa IP. No abras puertos del router a internet.

En Windows abre **PRUEBAS.cmd**. En Linux ejecuta `bash PRUEBAS.sh`.
El asistente prepara un entorno privado; no cambia tu PostgreSQL global.

## 1. Preparar las dos cajas

En **cada computador** abre el asistente y usa la opción **1**. Comprueba la
carpeta `bin` de PostgreSQL detectada y elige una contraseña local nueva.
El usuario local será **laboratorio**. No uses tu contraseña real del negocio.

Las carpetas de datos quedan separadas del programa:

- Windows: `%LOCALAPPDATA%\NovaPOS-Pruebas\caja`
- Linux: `~/.local/share/nova-pos-pruebas/caja`

No copies esa carpeta de un computador al otro. Solo copia el paquete original.
No repitas «Preparar» después de empezar: para continuar usa «Abrir la caja».

## 2. Preparar el servidor, solo en el computador 1

1. Opción **2**, introduce la IPv4 del propio computador (por ejemplo
   `192.168.1.20`, no la del router). Confirma su PostgreSQL.
2. Opción **3**. Deja esa ventana abierta. Muestra dos usuarios ficticios,
   sus contraseñas aleatorias, códigos de vinculación y una huella SHA256.
3. Copia **solo** `conexion-pruebas.json` de la carpeta `servidor` al computador 2
   mediante USB u otro canal de confianza. Es un certificado público: no contiene
   contraseñas. **No copies server-key.pem, pilot-hub.json ni las bases.**
4. Si Windows pregunta por acceso de red para Python, permite únicamente la red
   privada. Si un firewall bloquea la conexión, autoriza únicamente TCP **8940**
   entrante para el programa de prueba y las IP de los dos computadores. No
   desactives el firewall. PostgreSQL sigue escuchando solo en loopback.

El certificado dura 60 días y solo se confía en él desde estas cajas, sin instalar
una autoridad de certificación en Windows/Linux. No ignores errores TLS.
Esta es una herramienta de laboratorio LAN, no un servidor publicado para internet.

## 3. Vincular cada caja

Abre otra ventana del asistente (no cierres el servidor):

1. Opción **4**, elige `conexion-pruebas.json`.
2. En PC1 usa **prueba1**; en PC2 usa **prueba2**.
3. Compara la huella con la consola del servidor e introduce los 64 caracteres.
4. Pega el código y la contraseña **de ese usuario ficticio**. No son la
   contraseña local que elegiste al instalar. No se muestran al escribir.
5. Los códigos duran 15 minutos. Si caducan, detén y vuelve a iniciar el servidor;
   renovará los códigos no usados, sin borrar ventas ni cambiar cajas vinculadas.
6. Opción **5** en ambos; abre `http://127.0.0.1:8910/` en cada computador.
   Entra con **laboratorio** y la contraseña local de ese equipo.
7. Espera a que `/local/estado/` confirme la copia y sesión preparadas; después
   abre `/generar_venta/`. `/local/sincronizacion/` muestra los pendientes.

El mismo puerto 8910 en dos computadores distintos no causa conflicto.
No abras la dirección del servidor para vender: cada caja utiliza su localhost.

## 4. Pruebas que debes realizar

1. Vende ARROZ FICTICIO simultáneamente desde ambos equipos, una vez en efectivo,
   otra en Nequi/tarjeta declarados. No se verifica ningún pago bancario real.
2. Detén **solo el servidor** con Ctrl+C (la opción 3); conserva ambas cajas
   abiertas. Esto simula que la nube no responde, sin desconectar la red de tu PC.
3. Vende en ambos equipos y registra un egreso ficticio. Verifica que estén
   pendientes con referencias distintas. Puedes vender TOMATE FICTICIO X GR
   aunque el inventario ya es negativo (la cantidad se escribe en gramos).
4. Detén una caja con Ctrl+C, vuelve a abrirla (opción 5) y verifica sus pendientes.
5. Reinicia el servidor (opción 3). La sincronización reintenta en segundo plano
   (tras una caída puede tardar hasta 5 minutos); también puedes usar «Enviar
   pendientes» en la pantalla de sincronización. No vuelvas a cobrar la venta.
6. Comprueba que no queden pendientes y usa la opción **6** en PC1 para ver
   el resumen central: ambas autorías, ventas, egresos e inventario.
7. Prueba una devolución de una venta consultada previamente con conexión.
   Sin conexión solo queda **solicitada**: no entregues dinero hasta su aceptación.
8. Prueba el cierre de ambos turnos **al final**. El cierre offline queda pendiente
   y bloquea nuevas ventas de ese turno hasta conciliar; esto es intencional.
9. Revisa que repetir la sincronización no aumente ventas ni egresos.

La copia autoriza operación offline durante **2 horas** desde su última renovación,
dentro de la sesión de 12 horas. Reiniciar no amplía esos plazos. Esta prueba
crea un turno por caja: después del cierre o vencimiento no borres las bases ni
reinicialices encima; conserva los resultados para revisar y preparar otra ronda.

## Detener y respaldar

Detén con Ctrl+C cada ventana de caja y después el servidor. No finalices procesos
PostgreSQL a la fuerza. La opción 7 respalda la caja cuando está detenida; el ZIP
contiene credenciales y datos, guárdalo de forma privada y no lo subas a Git.

Si falla algo, guarda el mensaje, la hora, el equipo y la referencia de operación.
No compartas contraseñas ni archivos privados; tampoco borres la cola para repetir.

## Alcance

Es un **piloto con datos ficticios**, no una autorización para instalarlo encima
del POS real. La prueba automatizada en un mismo PC con procesos/clústeres
independientes no sustituye comprobar la red, firewall, suspensión y apagado
de los dos computadores físicos. No se prueba impresión en esta entrega.
