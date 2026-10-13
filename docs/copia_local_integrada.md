# Copia local integrada en el programa normal

Etapa implementada el 10 de octubre de 2026. Es preparación de datos, **no una
autorización para realizar ventas offline**. Las escrituras originales siguen
en la nube; el diario integrado y su conciliación son la siguiente etapa.

## Uso en este equipo de desarrollo

1. Iniciar el proyecto con el entorno habitual: `python manage.py runserver`.
   La preparación privada existente arranca PostgreSQL WSL automáticamente.
2. Iniciar sesión con el usuario que utilizará esta copia. Debe tener permiso
   para generar ventas y un empleado con sucursal asignada.
3. Abrir **Copia local** en la navegación, o `/local/copia/` en ese mismo servidor.
4. Elegir una caja de esa sucursal y pulsar **Preparar copia**. La descarga corre
   en segundo plano; se puede seguir trabajando en el sistema original.
5. La página muestra el resultado, cantidades, última actualización y vencimiento.
   Después de la primera preparación, se renueva automáticamente con actividad
   autenticada; el botón permite solicitar una actualización sin esperar.

No se realizó una copia de Aiven durante el desarrollo: las pruebas usan datos
ficticios y bases nuevas. No copiar la configuración privada de este PC a otros.
El enlace solo aparece en el perfil local preparado; no se añade un trabajador
de réplica al WSGI de PythonAnywhere.

## Datos incluidos

- La sucursal y caja autorizadas (no se copia el saldo de la caja).
- Categorías, productos y precios; inventario de esa sucursal, incluidos negativos.
- Métodos de pago y su configuración, incluido el 4 por mil de egresos.
- Clientes, únicamente cuando ese usuario tiene permiso para consultarlos.
- Identidad del usuario y sus permisos de rol/directos, en almacenamiento privado.
  Nunca se exponen contraseñas, hashes ni credenciales en el estado de la página.

No es un respaldo completo: no descarga ventas, pagos históricos, notificaciones
Nequi, turnos de caja ni todos los usuarios del negocio. El catálogo es compartido;
el inventario se limita a la sucursal autorizada.

## Frecuencia y protección de los datos

- Una copia consistente de PostgreSQL, en transacción `REPEATABLE READ, READ ONLY`.
  La descarga no puede escribir en la base fuente, ni cambia las conexiones de
  otras solicitudes de Django.
- Se compara con la copia anterior: los registros locales sin cambios no se
  reescriben. Actualmente se consulta el conjunto autorizado completo en origen;
  **todavía no es un flujo de eventos incrementales desde Aiven**.
- Renovación aproximadamente cada 60 segundos. Una solicitud autenticada que
  puede cambiar datos adelanta la siguiente revisión a unos 5 segundos, salvo
  que ya haya una descarga ejecutándose. No significa consistencia instantánea.
- Sin actividad autenticada se pausa después de 15 minutos. Cerrar sesión pausa
  nuevas renovaciones; una transacción que ya empezó puede terminar. Mantener
  abierta y visible la pantalla de estado produce consultas autenticadas.
- Reintentos progresivos ante fallos, hasta 5 minutos entre intentos. Las consultas
  fuente tienen timeout. El error del trabajador no invalida ni reenvía una venta
  que la nube ya confirmó.
- Se verifica alcance, integridad y permisos, y se activa catálogo + autorización
  en una sola transacción local. Una interrupción revierte la nueva copia completa;
  conserva la anterior y **no extiende su vencimiento**.
- La réplica tiene vigencia de 24 horas desde la descarga validada. La identidad tiene un
  máximo de 24 horas; siempre prevalece el límite menor. Las revocaciones durante
  una desconexión solo pueden conocerse al reconectar o al vencer esos límites.
- Operaciones pendientes, ediciones locales inesperadas, otra nube/usuario/caja
  o una baja con historial vinculado detienen la actualización. No se borran
  movimientos ni se reemplaza una base ajena para forzarla.
- Una exclusión en PostgreSQL evita dos descargas simultáneas para el mismo equipo.

## Límites que aún deben resolverse

Esta etapa vincula la copia a **un usuario, sucursal y caja por equipo**. El cambio
seguro de cajero/caja necesita un flujo de desvinculación y conciliación; no borrar
la base ni el archivo de configuración para cambiarlo. Las bajas con relaciones
existentes pueden necesitar revisión y no se fuerzan automáticamente.

La copia por sí sola no habilita escrituras: requiere la activación explícita
`journal_preview` del proyecto habitual. Ese perfil ya conecta el diario a las
vistas originales de ventas, pagos, devoluciones y cierre; conserva confirmaciones
inciertas y ofrece revisión auditada. Se mantienen pendientes las pruebas físicas
en dos equipos, impresión y certificación de distribución. Véanse
`diario_ventas_integrado.md` e `instalacion_integrada_runserver.md`.

## Repetir validación sin producción

```powershell
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_hybrid_postgres_wsl.py
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_integrated_runserver.py --pg-bin /usr/lib/postgresql/16/bin --wsl-distribution Ubuntu-22.04 --wsl-user novapos-test --autoreload --reference-only
```

El segundo ensayo crea dos clústeres ficticios, inicia el `runserver` original
con recargador, autentica, prepara por HTTP y verifica la renovación automática
de un precio. También comprueba inventario negativo y que las sesiones permanezcan
en la base fuente. Detiene únicamente sus procesos y conserva los registros.

Resultado del 10 de octubre de 2026: **137 pruebas correctas (88 + 49)** en Windows
contra PostgreSQL WSL. Además pasaron el ensayo por procesos de este perfil y
el ensayo experimental nube → corte real → lectura local → recuperación/reinicio,
ahora con una réplica creada por el servicio real, no insertada manualmente.
Se validó el HTML de la pantalla y la sintaxis de su JavaScript; no se hizo una
revisión visual en navegador ni una prueba de impresión en esta etapa.
