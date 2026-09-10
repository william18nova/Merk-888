# Recuperar una venta después de cerrar su página

En **Generar venta → Ventas pendientes**, recuperar un borrador crea un
carrito nuevo con identificador propio. Copia productos, cantidades y cliente;
valida de nuevo las existencias, los precios y el cliente en el servidor.

- El respaldo se guarda continuamente, pero no se ofrece mientras su página
  siga abierta. Duplicar una pestaña, minimizar el navegador o cambiar a otra
  pestaña no libera el carrito original.
- Al cerrar la página con productos sin facturar, el respaldo aparece para
  recuperar (hasta 24 horas). Se comprueba al enfocar la página y cada dos
  segundos mientras está visible, sin peticiones adicionales al servidor.
- Recuperar guarda primero el carrito nuevo y luego elimina el respaldo de
  origen. Desaparece de la lista y solo vuelve a ofrecerse cuando se cierre
  la página del nuevo carrito sin facturar.
- Dos pestañas no pueden recuperar el mismo respaldo simultáneamente. La
  comprobación se repite bajo un bloqueo exclusivo, incluso con un listado viejo.
- Para recuperar hay que usar un carrito vacío: una venta que aún se está
  atendiendo no se reemplaza ni se manda a pendientes. Puede abrirse otra pestaña.
- No se copian pagos seleccionados, importes recibidos, asociaciones Nequi,
  contraseñas ni autorizaciones de descuento. Deben definirse para la nueva venta.
- Recuperar productos no cuenta como vaciar un carrito ni registra una venta.
- Si el borrador indica un cobro sin confirmación, se pide revisar **Visualizar
  ventas** antes de copiar. Esta advertencia requiere comprobación del cajero:
  no es una conciliación automática con el servidor.
- Durante un cobro o la validación de una recuperación no se puede cambiar
  de carrito mediante otra recuperación.

- Volver con Atrás a una página conservada por el navegador no revive un
  respaldo que ya se recuperó o descartó en otra pestaña.

## Límites del navegador y actualización

La presencia utiliza Web Locks, no un heartbeat que pueda vencer al suspender
una pestaña. Requiere un navegador moderno en HTTPS o localhost. Si no se puede
comprobar la presencia con seguridad, la recuperación se deshabilita sin
borrar respaldos ni impedir facturar normalmente.

El navegador no permite distinguir de forma universal entre cerrar, recargar
y abandonar la página de venta: las tres situaciones liberan esa página.
Ocultarla, minimizar o cambiar de pestaña no la libera. Si el proceso termina
sin eventos de cierre, el navegador libera el bloqueo y se conserva el último
autoguardado, no necesariamente las teclas escritas justo antes del fallo.

Los respaldos de versiones anteriores no tenían esta señal de presencia.
Se conservan para no perder información y exigen confirmar expresamente que
la pestaña original está cerrada antes de recuperarlos o descartarlos.

Referencia: [Web Locks](https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API)
y [ciclo de salida de una página](https://developer.mozilla.org/en-US/docs/Web/API/Window/pagehide_event).

## Despliegue y comprobación

No requiere migraciones ni dependencias nuevas. Actualiza el código, ejecuta
`python manage.py collectstatic --noinput` y recarga la aplicación en PythonAnywhere.
Actualiza las pestañas antiguas del navegador después de asegurar sus borradores:
una pestaña que siga usando el JavaScript anterior mantiene el comportamiento anterior.

Pruebas aisladas, sin tocar ventas reales:

```bash
python manage.py test mainApp.test_sale_draft_cache mainApp.test_cart_clear_audit --settings=NovaSoft.test_settings
node --test mainApp/test_sale_draft_independence.js mainApp/test_cart_clear_notice.js
```
