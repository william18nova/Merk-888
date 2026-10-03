# Sincronización y revisión de operaciones locales

Pantalla del runtime instalado: `/local/sincronizacion/`. Se abre desde
**Sincronización y pendientes**, en la franja de modo local, o desde Estado.
No añade una ruta a la instalación de PythonAnywhere ni publica cambios allí.

## Qué muestra

- Movimientos por confirmar, por revisar y confirmados por la nube.
- Ventas, pagos/egresos, devoluciones y cierres; filtros por tipo, estado, fecha,
  referencia UUID o número del diario, con páginas de 20 resultados.
- Importe, autor original, fecha, referencia única y causa legible del rechazo.
- Última comunicación conocida, copia completa y vigencia de la autorización.
  Una comunicación correcta anterior no garantiza que haya internet ahora.
- Historial de revisiones: responsable, nota, fecha y resultado del intento.

La fecha filtrada es la de registro local, en la zona horaria del POS. Los
contadores resumen el historial visible completo, no solo los filtros activos.
Actualizar vista solo consulta; Sincronizar pendientes envía hasta cinco
operaciones, en orden. El proceso de fondo continúa el resto. No recarga el
catálogo ni renueva autorizaciones desde el botón de revisión.

## Permisos

Un usuario activo ve solo sus propios movimientos de este equipo. Para enviar
la cola debe ser el operador actualmente vinculado o un responsable autorizado.
Un Web Master o usuario con permiso `caja_turnos_editar` puede ver todos los
autores de este equipo y solicitar una nueva verificación de un conflicto.
No se amplían sus permisos en la nube: el servidor valida al autor original.

La pantalla sigue disponible con la autorización vencida o bloqueada, para
consultar y conservar los pendientes. No permite renovar, desbloquear ni
reemplazar una identidad por su cuenta.

## Revisar un conflicto

1. Abrir el detalle de la **primera** operación sin confirmar.
2. Leer la causa y la indicación. Corregir la causa en el servidor mediante los
   procedimientos autorizados, cuando corresponda (por ejemplo, revisar un permiso).
3. Un responsable escribe qué revisó y confirma **Verificar de nuevo en la nube**.
4. Comprobar el resultado: solo queda Confirmada si llega un acuse válido.

El reintento conserva UUID, secuencia, autor, importes, fecha, productos y firmas
originales. La nota queda guardada antes de enviar. El reenvío del mismo
formulario no genera otro intento manual; el contrato remoto evita duplicados
si se pierde una respuesta. Solo puede reintentarse la cabeza de la cola de la
sesión actual. No se saltan movimientos y hay un límite de 100 revisiones por
operación para conservar el historial completo sin crecimiento ilimitado.

**No hay botones para borrar, alterar el importe, cambiar el medio de pago,
forzar Confirmada o entregar un reintegro pendiente.** Un conflicto por
referencia duplicada, cantidades ya devueltas, reloj o turno cerrado puede
requerir conciliación administrativa en la nube. Volver a verificar no resuelve
por sí solo esos casos; una aprobación/cancelación compensatoria en servidor
continúa fuera de esta etapa. No se debe editar SQL ni crear otra venta para
ocultar el pendiente.

Los errores remotos se traducen mediante códigos permitidos; no se muestran
mensajes HTTP originales, firmas de precios, secretos ni archivos de conexión.
Las operaciones anteriores a esta versión pueden no tener un código detallado;
la pantalla lo indica sin inventar una causa. No requiere tablas ni migraciones
nuevas: la auditoría de revisión se conserva en el resultado local de la operación.

## Validación y demostración

Datos ficticios, sin impresión ni base de producción. La suite agrega 15 casos
de página y reintento, incluidos permisos, CSRF, alcance por autor, escapado HTML,
filtros, orden, auditoría, respuesta perdida y ausencia de cobros duplicados.

```text
python -B scripts/full_local_lab.py --test --business-tests --pg-bin RUTA_POSTGRES_BIN --port 8930 --pg-port 55456
```

Demostración Linux/WSL, con origen y receptor ficticios separados:

```text
python -B scripts/full_local_lab.py --serve --review-demo --port 8931 --source-port 8932 --pg-port 55457
```

Entrar en `http://127.0.0.1:8931/local/sincronizacion/` con `laboratorio` y
contraseña ficticia `prueba-local-2026`. Empieza con una venta por revisar y un
pago pendiente. El corte está simulado: no apaga el Wi-Fi. Después de revisar,
en Estado se puede restablecer la comunicación para confirmar ambos, sin
duplicarlos. La demostración se reinicia desde cero al relanzarla; no confundir
con la instalación persistente. Nunca usar datos reales en ella.

Recorrido de navegador reproducible: `scripts/test_full_local_sync_browser.cjs`.
Incluye escritorio, móvil, filtros, cancelar/confirmar, auditoría, reintento sin
conexión y reconexión. Las capturas y el informe quedan en
`outputs/full-local-sync/`, excluido de Git.

### Resultado del 3 de octubre de 2026

- Suite de negocio: 126 pruebas correctas en Windows/PostgreSQL 18.6 y
  Linux/WSL/PostgreSQL 16, incluidas las 15 nuevas de revisión.
- 59 pruebas de cliente, archivos y actualizador correctas en Windows.
- Recorrido Edge de escritorio y móvil correcto: filtros, cancelación sin dejar
  la página atenuada, confirmación flotante, nota auditada, reintento offline,
  reconexión y dos movimientos confirmados sin crear otros. Cero errores de
  JavaScript, peticiones externas o impresiones.
- Comprobación adicional: adaptador desactivado rechaza la revisión antes de
  consultar o modificar la base.
- Distribución candidata `full-local-candidate-20261003-e`, 510 archivos,
  esquema 2 sin nuevas tablas. ID:
  `af97383ffd4e05f4ca8d37d17053d5d43a13689d5daf31b41b4f97380252e301`.

Este resultado se documentó después de construir el paquete, sin modificar sus
archivos ni manifiesto. No es un instalador firmado ni certifica producción. No
se desplegó en PythonAnywhere, no se accedió a Aiven y no se hicieron commits o
envíos a GitHub.
