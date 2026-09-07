# Calendario de empleados

## Acceso y uso

- **Horarios → Calendario de empleados**, ruta `/horarios/empleados/`: planificación global.
- **Horarios → Mi horario**, ruta `/mi-horario/`: cada usuario autenticado consulta únicamente la ficha de empleado vinculada a su cuenta. Sin vínculo, se muestra un aviso y ningún turno ajeno.
- El Web Master puede administrar el calendario. Otros usuarios necesitan **Ver calendario de empleados** para consultar el conjunto y **Gestionar turnos de empleados** para crear, editar, mover y cancelar. Gestionar también habilita consultar.

El calendario tiene vistas Semana, Mes y Lista, navegación por fechas y filtros por empleado y sucursal. Pulsa **Asignar turno** o **+ Asignar** en un día, selecciona empleado, sucursal, inicio, fin y notas, y guarda. También puedes seleccionar una tarjeta para editarla. Arrastrar una tarjeta a otro día abre una propuesta de movimiento: revisa las fechas y pulsa **Guardar turno**. En pantallas táctiles también se puede mover editando sus fechas desde la tarjeta.

Las horas son de Colombia (`America/Bogota`). Se admiten jornadas nocturnas: por ejemplo, inicio 8 de septiembre a las 22:00 y fin 9 de septiembre a las 06:00. Cada jornada debe durar más de cero y hasta 24 horas. El total de horas es planificación bruta; no descuenta descansos, calcula nómina ni acredita asistencia.

Un empleado no puede tener jornadas superpuestas, incluso en sucursales distintas. Se permiten jornadas consecutivas y horarios simultáneos de empleados diferentes. Un cambio concurrente obliga a actualizar antes de guardar, evitando sobrescribir una edición de otro administrador. Reintentar la misma petición no crea duplicados.

Cancelar un turno lo retira del horario, pero conserva el registro y su historial. Las tablas `turnos_empleados` y `cambios_turnos_empleados` guardan autor, fecha, origen web/Telegram y los datos anteriores y nuevos. Esto es independiente de las cajas: no abre, cierra ni modifica turnos financieros, saldos o ventas. No reemplaza los horarios de apertura del negocio.

## Telegram (texto o audio)

- `/horario` o «Muéstrame mi horario»: próximas siete fechas de la persona vinculada.
- «Mi horario esta semana»: lunes a domingo de la semana actual.
- «Mi horario hoy»: solo la fecha actual.
- `/horario Ana Pérez` o `/horario 12`: empleado concreto, con permiso para consultar otros horarios.
- `/horarios`: próximos siete días de todo el equipo, con permiso de calendario global.
- «Muéstrame los horarios de Ana del 8 al 14 de septiembre de 2026»: consulta con fechas.
- «Asigna al empleado 12 el 8 de septiembre de 2026 de 08:00 a 16:00 en la sucursal Centro»: propone una jornada. Si omites sucursal, se propone la de la ficha del empleado y se muestra antes de confirmar.
- «Cambia el turno laboral 25 al 9 de septiembre de 2026, de 10:00 a 18:00»: propone editar esa jornada.
- «Cancela el turno laboral 25»: propone cancelar la jornada, no una caja.

Los IDs son ejemplos: utiliza IDs reales de empleado y turno mostrados en la consulta. Si hay nombres ambiguos, se exige identificar al empleado. Los cambios requieren **Confirmar horario** o **Confirmar cancelación**; decir «sí» no los ejecuta. **Descartar propuesta** no cancela un turno existente. Las propuestas vencen en diez minutos y revalidan permisos, versión y superposiciones al confirmar. Se modifica una jornada por propuesta; no se generan recurrencias ni cambios masivos implícitos.

Las consultas tienen páginas de cinco jornadas y botones para continuar. Las herramientas están registradas tanto para Gemini como para Groq, y las consultas simples de horario tienen atajos sin IA. Las notas de voz utilizan la transcripción ya configurada. No se envían avisos automáticos a empleados ni se usan nuevas claves API.

## Instalación en PythonAnywhere

Después de subir estos archivos al repositorio, en la consola del proyecto:

```bash
cd /home/Merk888/Merk-888
git pull
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py showmigrations mainApp
```

Debe aparecer `[X] 0037_employee_schedule`. Luego recarga la aplicación en **Web → Reload** y reinicia el trabajador existente de Telegram para que cargue las nuevas herramientas. No crees un segundo trabajador duplicado. Si utilizas el proceso periódico de `procesar_telegram_bot`, su siguiente ejecución cargará el código actualizado. No es necesario cambiar claves ni recrear el bot. La lista visual de comandos de Telegram se actualiza al volver a configurar el webhook desde la página de Telegram; `/horario` funciona al escribirlo aunque ese menú todavía sea anterior.

La migración crea tablas nuevas y dos permisos, sin modificar ventas ni cajas. La instalación no requiere dependencias nuevas ni calendarios externos. Antes de operar verifica el vínculo **Empleado → Usuario** de cada persona.

## Verificación aislada

```bash
python manage.py test mainApp.test_employee_schedule mainApp.test_permission_audit --settings=NovaSoft.test_settings
```

Las pruebas usan SQLite temporal, no la base real. Incluyen la ejecución de la migración nueva, privacidad, CSRF, permisos, jornadas nocturnas, colisiones, versión, reintentos y confirmaciones del bot. El bloqueo concurrente de filas usa `select_for_update` en PostgreSQL; SQLite no reproduce ese bloqueo de producción. El flujo de interfaz se revisa con datos ficticios.
