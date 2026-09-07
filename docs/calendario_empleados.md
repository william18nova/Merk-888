# Calendario de empleados

## Acceso y uso

- **Horarios → Calendario de empleados**, ruta `/horarios/empleados/`: planificación global.
- **Horarios → Mi horario**, ruta `/mi-horario/`: cada usuario autenticado consulta únicamente la ficha de empleado vinculada a su cuenta. Sin vínculo, se muestra un aviso y ningún turno ajeno.
- El Web Master puede administrar el calendario. Otros usuarios necesitan **Ver calendario de empleados** para consultar el conjunto y **Gestionar turnos de empleados** para crear, editar, mover y cancelar. Gestionar también habilita consultar.

El calendario tiene vistas Semana, Mes y Lista, navegación por fechas y filtros por empleado y sucursal. Pulsa **Asignar turno** o **+ Asignar** en un día, selecciona empleado, sucursal, inicio, fin y notas, y guarda. También puedes seleccionar una tarjeta para editarla. Arrastrar una tarjeta a otro día abre una propuesta de movimiento: revisa las fechas y pulsa **Guardar turno**. En pantallas táctiles también se puede mover editando sus fechas desde la tarjeta.

Las horas son de Colombia (`America/Bogota`). Se admiten jornadas nocturnas: por ejemplo, inicio 8 de septiembre a las 22:00 y fin 9 de septiembre a las 06:00. Cada jornada debe durar más de cero y hasta 24 horas. El total de horas es planificación bruta; no descuenta descansos, calcula nómina ni acredita asistencia.

Un empleado no puede tener jornadas superpuestas, incluso en sucursales distintas. Se permiten jornadas consecutivas y horarios simultáneos de empleados diferentes. Un cambio concurrente obliga a actualizar antes de guardar, evitando sobrescribir una edición de otro administrador. Reintentar la misma petición no crea duplicados.

Cancelar un turno lo retira del horario, pero conserva el registro y su historial. Las tablas `turnos_empleados` y `cambios_turnos_empleados` guardan autor, fecha, origen web/Telegram y los datos anteriores y nuevos. Esto es independiente de las cajas: no abre, cierra ni modifica turnos financieros, saldos o ventas. No reemplaza los horarios de apertura del negocio.

## Rotación de cuatro semanas de Yerbabuena

La plantilla aprobada está en `mainApp/data/employee_rotation_four_weeks.json`. Comienza el **lunes 7 de septiembre de 2026**: semana 1 el 7, semana 2 el 14, semana 3 el 21, semana 4 el 28, y vuelve a la semana 1 el 5 de octubre. Se repite cada 28 días sin una tarea programada ni una fecha final.

- Mañana: **07:00–14:00**. Tarde: **14:00–21:00**.
- Claudia: **20:00–01:00 del día siguiente**, excepto los viernes.
- Duvan: viernes **15:00–00:00 del día siguiente**.
- Los descansos son de día completo y no suman horas trabajadas. Se ignora la marca «x2» indicada en las tablas.
- Cada ciclo contiene **180 jornadas y 20 descansos**, para las nueve personas indicadas. No se cambian los sábados ni se agregan dobles jornadas implícitas.

En el calendario, **Rotación de 4 semanas** permite revisar las cuatro tablas, escoger el lunes inicial y la sucursal, y relacionar cada nombre con su empleado real. Las sugerencias solo se eligen automáticamente cuando el nombre identifica a una persona única. Importar no reemplaza jornadas existentes: ante una superposición, se rechaza toda la importación sin guardar cambios parciales. No vuelvas a importar para editar una rotación ya activa; abre su tarjeta en el calendario.

Al editar, mover o cancelar una tarjeta de la rotación es obligatorio escoger:

1. **Solo esta fecha**: crea una excepción; la próxima repetición conserva el horario habitual.
2. **Esta y las siguientes repeticiones**: modifica esa jornada de esa semana del ciclo desde la fecha seleccionada. Por ejemplo, cambiar el lunes de la semana 1 afecta los siguientes lunes de la semana 1, cada cuatro semanas, no los lunes de las otras tres semanas.

Los cambios de plantilla no modifican las ocurrencias anteriores ni borran excepciones puntuales de otras fechas. Para cambiar la plantilla debe seleccionarse una ocurrencia de hoy o futura. También se puede convertir un descanso en jornada (y viceversa), validando que no se cruce con otro horario. Las modificaciones conservan usuario, origen y valores anteriores/nuevos en `cambios_rotaciones_empleados`; no se crean infinitas filas de turnos. Las vistas personales consultan la misma rotación y sus excepciones.

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

Los IDs son ejemplos: utiliza IDs reales de empleado y turno mostrados en la consulta. Si hay nombres ambiguos, se exige identificar al empleado. Los cambios requieren **Confirmar horario** o **Confirmar cancelación**; decir «sí» no los ejecuta. **Descartar propuesta** no cancela un turno existente. Las propuestas vencen en diez minutos y revalidan permisos, versión y superposiciones al confirmar.

Las consultas incluyen las jornadas de la rotación, su semana y descansos. Sus referencias tienen el formato `rID-CLAVE-AAAAMMDD`; utiliza la referencia exacta de la consulta. Ejemplos: «Cambia esa jornada de Camila de 08:00 a 15:00 solo esta fecha» o «Cancela esa jornada y las siguientes repeticiones». El bot debe preguntar el alcance si no lo indicas y mostrarlo en la propuesta antes de confirmar. No crea rotaciones completas ni modifica varias jornadas distintas en una misma propuesta; la importación inicial se realiza en la página o con el comando siguiente.

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

Deben aparecer `[X] 0037_employee_schedule` y `[X] 0038_employee_rotation`. Si local y PythonAnywhere usan la misma base de datos, una migración ya aplicada aparecerá aplicada también allí. Luego recarga la aplicación en **Web → Reload** y reinicia el trabajador existente de Telegram para que cargue las nuevas herramientas. No crees un segundo trabajador duplicado. Si utilizas el proceso periódico de `procesar_telegram_bot`, su siguiente ejecución cargará el código actualizado. No es necesario cambiar claves ni recrear el bot. La lista visual de comandos de Telegram se actualiza al volver a configurar el webhook desde la página de Telegram; `/horario` funciona al escribirlo aunque ese menú todavía sea anterior.

La migración 0037 crea las tablas de jornadas manuales y dos permisos; la 0038 crea las tres tablas de rotaciones, miembros e historial, sin modificar ventas ni cajas. La instalación no requiere dependencias nuevas ni calendarios externos. Antes de operar verifica el vínculo **Empleado → Usuario** de cada persona.

### Carga inicial validada por consola

Si la rotación todavía no está cargada, simula primero y revisa las asignaciones:

```bash
python manage.py cargar_rotacion_yerbabuena --usuario "William Nova"
```

El valor por defecto es Yerbabuena, con inicio `2026-09-07`. La simulación revierte la transacción completa. Cuando sea correcta:

```bash
python manage.py cargar_rotacion_yerbabuena --usuario "William Nova" --apply
```

El usuario debe existir y tener permiso para gestionar horarios. Los nombres ambiguos detienen la carga; en ese caso selecciónalos desde la página. Repetir exactamente la misma carga por consola reconoce su identificador estable y no duplica la rotación. Si ya se importó desde la interfaz, no ejecutes una segunda importación: revisa la rotación existente.

## Verificación aislada

```bash
python manage.py test mainApp.test_employee_schedule mainApp.test_employee_rotation mainApp.test_permission_audit --settings=NovaSoft.test_settings
```

Las pruebas usan SQLite temporal, no la base real. Incluyen las dos migraciones, las cuatro tablas exactas, repetición, descansos, excepciones, cambios futuros, conflictos lejanos, importación sin duplicados, privacidad, CSRF, permisos, jornadas nocturnas, versión, reintentos y confirmaciones del bot. El bloqueo concurrente de filas usa `select_for_update` en PostgreSQL; SQLite no reproduce ese bloqueo de producción. El flujo de interfaz se revisa con datos ficticios.
