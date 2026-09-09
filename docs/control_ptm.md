# Control de efectivo PTM

## Activación

Después de subir el código, desde el entorno del proyecto:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

Recargar la aplicación web en PythonAnywhere. La migración `0040_ptm_cash_operations`
crea las tablas y los productos. Si existe un único producto llamado `PTM`, conserva
su ID y lo renombra a `PTM RECARGA O PAGOS`. El otro es `PTM RETIROS`. Ambos tienen
precio de catálogo cero: **el monto se introduce por operación**, no como cantidad
de mercancía. Si los nombres entran en conflicto, la migración se detiene sin
fusionar productos. Las ventas y los cierres históricos no se reinterpretan.

Existe una diferencia previa entre modelos y migraciones del proyecto. No generar
ni aplicar una migración masiva para corregir ese aviso como parte de PTM; `0040`
contiene únicamente los cambios de esta función.

## Uso

- Acceso: **Caja → Operaciones PTM** (`/ptm/`). También hay dos accesos en Generar
  venta que abren una pestaña independiente y conservan el carrito actual.
- Permiso: operar caja o editar turnos. Solo se puede registrar en un turno
  **propio y ABIERTO**. Incluso con ventas sin turno, PTM requiere control de caja.
- Primero realizar la operación en PTM; después registrar tipo, monto en COP y
  referencia del comprobante. Confirmar que fue aprobada y se movió el efectivo.
- No se almacenan cédulas, teléfonos ni datos bancarios del cliente en este registro.
- Retiros restan efectivo; recargas/pagos suman efectivo. Actualizan también el
  saldo global del punto de pago. No generan ventas, descuentos ni inventario.
- No agregar PTM a “Facturas pagadas”, pues se contaría dos veces.
- No se han incorporado comisiones: si se cobran, deben registrarse y conciliarse
  por separado, no mezclarse con el capital del cliente.

## Cierre

El cajero cuenta los comprobantes de PTM del turno y escribe el número total.
Debe coincidir con las operaciones registradas. Un monto de $50.000 es **una**
transacción, no 50.000 transacciones. Los intentos con conteos distintos quedan
registrados y bloquean el cierre. La coincidencia de conteo no oculta diferencias
en efectivo: el cuadre monetario continúa funcionando.

Ejemplo: base $100.000, ventas en efectivo $50.000, recargas $30.000 y retiros
$40.000 → efectivo físico esperado $140.000, antes de otras salidas de caja.
El esperado del cierre excluye la base, como ya hacía el sistema; PTM aporta
un neto de -$10.000. No se resta otra vez al dinero contado físicamente.

En Admin turnos se muestran cantidad, cantidad declarada, entradas, salidas y
neto, con acceso al historial del turno. El recibo del cierre incluye PTM.
Los administradores pueden revisar también los intentos de conteo. Los cajeros
solo ven sus propias operaciones.

## Controles implementados y límites

- Referencia obligatoria y única globalmente en este registro (no comprobada con
  PTM). Se normaliza a mayúsculas sin espacios. Si PTM reutiliza referencias por
  terminal/cuenta, se debe confirmar ese formato y ampliar la clave antes de usarlo.
- Cada envío tiene una clave única: reenviarlo no duplica el movimiento.
- Se guarda usuario, turno, punto de pago/sucursal a través del turno, hora del
  servidor, tipo y monto. Nunca se toma el usuario o una fecha del navegador.
- Registro y movimiento global de efectivo son una sola transacción; el turno
  se bloquea durante el registro para serializarlo con el inicio del cierre.
- No hay rutas para editar/borrar operaciones PTM. Se protegen las relaciones
  y se bloquea la edición manual de turnos con PTM, incluso administrativa.
  Un usuario con acceso directo a la base aún puede alterar datos: no es un
  sistema criptográficamente inalterable.
- Si se escribió mal una operación, conservar el comprobante y escalarla: esta
  primera versión **no incluye anulaciones/correcciones**. No crear una operación
  ficticia del sentido contrario ni cambiar facturas para cuadrar.

Para detectar omisiones o comprobantes inventados es imprescindible conciliar
diariamente con una fuente independiente: reporte/exportación oficial de PTM,
comparando **referencia, tipo, monto y estado**, además del número de operaciones.
El registro manual no puede saber si ocurrió algo que nunca le informaron.

Siguientes controles recomendados: importación del reporte oficial; comprobantes
adjuntos; correcciones con contramovimiento, motivo y aprobación de otro usuario;
conciliación independiente del saldo PTM y comisiones; y alertas por diferencias.
No se ha conectado una API externa ni se ejecutan retiros/transferencias desde aquí.
