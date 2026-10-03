# Actualización segura del POS local — candidato de pruebas

No desplegado al negocio. Solo paquetes revisados y distribuidos por el responsable
del proyecto: el manifiesto SHA-256 detecta archivos mezclados, **no es una firma
digital ni demuestra el origen del código**. No actualizar PostgreSQL con este
procedimiento ni reemplazar la carpeta de datos por una base vacía.

## Qué conserva

La identidad del equipo, usuarios locales, sesiones, permisos, inventario negativo,
operaciones pendientes y autores. El proceso se hace con el POS y su PostgreSQL
privado detenidos; no se usa el servicio PostgreSQL compartido ni se contacta a la
nube. No exige vaciar la cola de ventas, pero sí terminar una vinculación o relevo
que esté a medias.

1. Valida ambos paquetes, el esquema reconocido, la identidad y el espacio libre.
2. Hace un respaldo físico privado y comprueba su integridad mediante extracción.
3. Bloquea el arranque mientras cambia la instalación.
4. Ejecuta solo una migración local aprobada, sin `migrate` genérico del proyecto.
5. Compara todas las filas y el esquema de las tablas anteriores mediante huellas
   digitales; exige que se conserven. Las tablas nuevas aprobadas quedan vacías.
6. Prepara los recursos estáticos y activa el paquete nuevo.
7. Si falla, recupera el respaldo y conserva la copia fallida para diagnóstico.

Actualmente se reconoce el esquema de los candidatos `20261002-d` y `20261002-e`.
La migración aprobada añade las dos tablas del relevo a la versión anterior; los
cambios solo de código mantienen el mismo esquema. Otros esquemas se rechazan:
los siguientes cambios de modelos necesitan una migración explícita y sus pruebas.
No hay actualización automática de Python, PostgreSQL o dependencias globales.

## Procedimiento

Conservar la carpeta del paquete anterior. Extraer el paquete nuevo en otra
carpeta, no encima del anterior, y detener el POS con Ctrl+C. El directorio de
datos siempre es el mismo. El respaldo debe ser una carpeta nueva **junto** a la
carpeta de datos, no dentro, para permanecer en el mismo disco.

Desde el paquete NUEVO, en Windows:

```powershell
.\scripts\update_full_local.ps1 `
  -DataDir "$env:LOCALAPPDATA\NovaPOS-Piloto\datos" `
  -PreviousPackage "C:\NovaPOS\version-anterior" `
  -BackupDir "$env:LOCALAPPDATA\NovaPOS-Piloto\respaldo-actualizacion-01"
```

Usar `-Python "C:\ruta\python.exe"` si `python` no está en PATH. El lanzador crea
el entorno privado del paquete nuevo, no modifica el del anterior. Para instalar
dependencias sin red, agregar `-Wheelhouse "C:\ruta\wheelhouse"` preparado para
ese sistema y versión de Python.

Linux, con un usuario normal:

```bash
bash scripts/update_full_local.sh \
  "$HOME/.local/share/nova-pos-piloto" \
  "$HOME/NovaPOS/version-anterior" \
  "$HOME/.local/share/respaldo-nova-actualizacion-01"
```

Los argumentos opcionales cuarto y quinto son el ejecutable Python y wheelhouse.
Solo después de recibir confirmación, usar `start_full_local` del paquete NUEVO
con el mismo directorio de datos. El paquete anterior no podrá abrir la versión
nueva directamente.

## Recuperar una actualización interrumpida o volver atrás

No borrar `upgrade-intent.json`, no cambiar manualmente `installation.json` y no
repetir `init`. Mantener todas las carpetas. Desde el Python privado del paquete
nuevo, con el POS detenido:

```text
-m local_pos.runtime --data-dir RUTA_DATOS rollback-upgrade --backup-dir RUTA_RESPALDO --confirm-instance UUID_DEL_EQUIPO
```

El UUID está en el campo `instance_id` de `original-state.json`, dentro del
respaldo; ese archivo de estado no contiene contraseñas. En Windows el ejecutable
es `.\.venv\Scripts\python.exe`; en Linux `./.venv/bin/python`.

La recuperación se puede reanudar si se interrumpe a mitad de los movimientos.
No borra la versión fallida: se guarda dentro de `failed-state`. Al terminar,
arrancar con el paquete ANTERIOR. Repetir una recuperación ya terminada no vuelve
a sobreescribir los datos.

Si después de actualizar se registró **cualquier información nueva**, cambió la
vinculación o se abrió una sesión que modificó la base, el retroceso se rechaza.
Es intencional: restaurar el respaldo anterior podría borrar operaciones nuevas.
En ese caso mantener la versión actual y preparar una corrección o conciliación;
no existe una opción de forzar el borrado.

El respaldo ZIP contiene credenciales y **no está cifrado**. No compartirlo por
chat, subirlo a Git ni guardar los datos activos en OneDrive o una carpeta de red.
No arrancar simultáneamente una copia y el original con la misma identidad.

## Pruebas y límites

Las pruebas usan datos ficticios y no imprimen. La simulación de una interrupción
no sustituye una prueba de corte eléctrico real ni protege frente a daños físicos
del disco. Conservar otro respaldo privado fuera del equipo según la política del
negocio. La firma del instalador, actualización del motor PostgreSQL y resolución
administrativa de conflictos todavía no están incluidas en esta etapa.

```text
python -B -m unittest local_pos.test_upgrades local_pos.test_runtime_files
python -B scripts/smoke_full_local_upgrade.py PAQUETE_ANTERIOR PAQUETE_NUEVO --pg-bin RUTA_BIN_POSTGRES
```

El smoke crea una instalación nueva ficticia; prueba un fallo con recuperación,
migración real, retroceso sin cambios y rechazo del retroceso con información nueva.

### Registro de verificación — 3 de octubre de 2026

- 59 pruebas automáticas correctas en Windows y Linux/WSL: cliente, archivos y
  recuperación. Incluyen interrupción de la restauración, respaldo alterado,
  transición pendiente, identidad incorrecta y rechazo de retroceso con datos nuevos.
- Paquete candidato verificado: `full-local-candidate-20261003-c`, esquema 2,
  502 archivos. Identificador SHA-256:
  `5db440c8abc0df56e0f568f949ffd3d305b67d523395dce443b467326293e5fe`.
- Sintaxis Python, PowerShell y Bash comprobada. No incluye `NovaSoft/settings.py`
  ni `.env`; no se modificó la protección de Windows.
- Prueba completa del candidato C correcta con PostgreSQL 18.6 en Windows nativo
  y PostgreSQL 16 en Linux/WSL: migración desde `20261002-d`, recuperación automática
  ante fallo, retroceso sin cambios y rechazo del retroceso tras crear información
  nueva. Se conservaron identidad, credenciales locales, autores, operación
  pendiente e inventario negativo. Ambas ejecuciones terminaron con código 0.
- Se usaron bases ficticias en puertos privados. No se accedió a producción ni se
  imprimió. El servicio PostgreSQL instalado en Windows permaneció en ejecución.

Este registro se añadió al repositorio después de construir el candidato. No se
modificaron sus archivos ni su manifiesto: las carpetas de distribución verificadas
deben conservarse inmutables. El identificador comprueba integridad, no sustituye
la firma digital pendiente.
