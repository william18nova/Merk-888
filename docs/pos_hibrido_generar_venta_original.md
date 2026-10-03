# Generar Venta original con transporte híbrido — 0.6.0-pilot

## Qué se hizo

La ruta local `/generar_venta/` usa las plantillas originales `generar_venta.html`,
`modal_venta.html`, `base.html` y `navbar.html`, y los mismos JavaScript de carrito,
autocomplete y recuperación. No es una segunda implementación del carrito.

El navegador permanece en esa ruta mientras hay conexión, durante el corte y al
reconectar. El servidor local sirve también CSS, jQuery, imágenes y scripts, por
lo que recargar la página no necesita internet. Las fuentes externas se sustituyen
por fuentes del sistema y los iconos tienen un respaldo local; no se consulta CDN.

Se reutiliza el diario SQLite y el protocolo existentes: intención duradera antes
del envío, intento de confirmación en la nube, conservación local si falla la red,
y sincronización con UUID idempotente. El stock cero o negativo sigue permitido.

El formulario original solo cambia de transporte cuando `NovaHybridSale` está
presente. En Django normal se mantiene el flujo web, Nequi y los demás medios.
No se instala un service worker que capture peticiones de producción ni se abre
CORS contra localhost; navegador y motor local comparten origen.

## Casos importantes

- Conservar el carrito y la cantidad al desconectar la nube.
- Facturar en efectivo sin conexión y recargar la misma página.
- Reconectar y sincronizar sin cambiar de interfaz ni repetir ventas.
- Dos pestañas con carritos independientes.
- Respuesta local perdida: consultar/reintentar con el UUID del mismo carrito.
- Pestaña cerrada después del commit: verificar el diario antes de recuperar.
  Si el cobro existe, mostrarlo como registrado y no copiarlo a una venta nueva.
- Recuperar carritos sin cobrar, incluidos productos con inventario negativo.
- Historial local que distingue ventas sincronizadas, pendientes y en revisión.
- Sesión cambiada, vencida o bloqueada: no permite cobrar con la autorización vieja.

## Alcance y límites reales

Esta etapa conserva el piloto de **efectivo sin cliente asociado**, incluso estando
online. No implementa aún Nequi, tarjeta, crédito, PTM, descuentos personales,
auditoría de vaciado de carrito, devoluciones ni apertura/cierre offline.
El menú local no anuncia páginas que todavía no están integradas. No es todavía
un reemplazo completo del POS publicado.

La página de PythonAnywhere que ya está abierta en un navegador no se convierte
automáticamente en offline. El despliegue definitivo debe hacer que el cajero abra
la página original desde la instalación local desde el principio, con todas las
funciones necesarias integradas. Durante el corte no cambiará de URL ni de carrito.

No se modificaron ventas reales, Aiven, PythonAnywhere ni el acceso directo del
laboratorio persistente. El instalador de Windows sigue pendiente de resolver su
bloqueo de Control de aplicaciones por una vía autorizada, sin desactivar protecciones.
No se probó impresión física ni se construyó/instaló un ejecutable 0.6.0.

## Construcción sin secretos

`scripts/build_hybrid_sale_assets.py` usa Django únicamente para renderizar durante
la construcción, con `settings.configure`, URLs de adaptación y una lista explícita
de archivos. No importa `NovaSoft.settings`, modelos, bases ni credenciales.
El paquete final sigue excluyendo Django, NovaSoft y mainApp como módulos Python.
`scripts/build_hybrid.py` ejecuta automáticamente ese paso antes de PyInstaller.
Los archivos generados están ignorados en Git y se regeneran desde las fuentes.

El servidor inserta solo los datos públicos de la sesión escapados, y sirve estáticos
por una lista autorizada. Se conservan Host/Origin, token local, CSP, bloqueo de sesión,
rechazo de rutas ajenas y comprobación de sesión en cada operación que escribe.

## Prueba en este equipo

- Demostración ficticia: `http://127.0.0.1:8902/generar_venta/`.
- Clave local si se bloquea: `demo-local` (solo esta demostración).
- Catálogo: TOMATE POR GRAMO, AGUA DE PRUEBA y MANZANA ÁCIDA DE PRUEBA.
- El simulador jamás usa la base real ni una impresora; sus datos son temporales.
- La nube del simulador también es ficticia, no PythonAnywhere.

Para repetir desde el código:

```text
python scripts/build_hybrid_sale_assets.py
python scripts/smoke_hybrid_original_sale.py
node scripts/test_hybrid_original_sale.cjs RUTA_AL_MODULO_PLAYWRIGHT
```

## Verificación realizada

- 48 pruebas del cliente/adaptador en Windows: correctas.
- 48 pruebas del cliente/adaptador en Linux: correctas.
- 48 pruebas Django/PostgreSQL aislado: 46 correctas y 2 omitidas por sus requisitos
  opcionales; sin fallos. Incluye sesión, sincronización, recuperación y Nequi web.
- Navegador Edge automatizado: conexión/corte/reconexión, stock negativo, cantidades
  en gramos, cambio, recarga offline, dos pestañas, respuesta perdida, cierre tras
  commit, recuperación, historial y ancho móvil; sin errores JS ni peticiones externas.
- Revisión visual de escritorio y carrito móvil. Impresión excluida.
- 19 pruebas del gestor original de carritos independientes: correctas.

Las pruebas de navegador escriben cuatro ventas ficticias por ejecución. El historial
visible de esta demo contiene esas pruebas; no son ventas del negocio.
