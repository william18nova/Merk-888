# PostgreSQL de WSL para NovaPOS en Windows

Preparado y validado el 9 de octubre de 2026 para este equipo de desarrollo.
No es la elección automática del instalador para clientes.

## Qué quedó configurado

- Python/Django sigue ejecutándose en **Windows**, desde este repositorio.
- `python manage.py runserver` inicia bajo demanda `Ubuntu-22.04`, con el usuario
  sin privilegios `novapos-test`, y su PostgreSQL **16.15 de Linux**.
- Conexión local: `127.0.0.1:55460`, base/usuario `nova_integrated_local`.
- El PostgreSQL nativo existente de Windows permanece en **5432**, sin cambios.
- Aiven continúa como `default`. El motor WSL está disponible como alias `offline`.
  El modo `database_only` conserva las vistas, sesiones, autenticación y escrituras
  originales. No conecta por sí solo las ventas al diario offline.
- No hay contraseña en Git ni argumentos del proceso: la configuración privada
  tiene una clave aleatoria y ACL de usuario. El rol de la aplicación no es
  superusuario y no puede crear bases ni roles. Se usa autenticación SCRAM.
- Solo se escucha en loopback. No se añadieron reglas de firewall, excepciones
  de antivirus ni cambios de políticas de seguridad.

Configuración privada Windows (no compartir su contenido):

```text
C:\Users\LENOVO\AppData\Local\NovaPOS\integrated\608533b65b95aed1\config.json
```

Datos persistentes nuevos dentro de Ubuntu, fuera de OneDrive y de `/tmp`:

```text
/home/novapos-test/.local/share/NovaPOS/integrated/468ff4c2-9584-4c59-b188-4373e6577971/postgres
```

La base privada se preparó con el esquema del proyecto, no una descarga de Aiven.
No se eliminaron ni migraron las bases preexistentes. Las pruebas de negocio
usaron otras instancias nuevas con datos ficticios.

El 10 de octubre se añadió **Copia local** (`/local/copia/`) al perfil preparado:
permite iniciar una copia autorizada y renovarla en segundo plano desde el mismo
`runserver`. No se inició una descarga de datos reales durante las pruebas.
La batería ampliada pasó **137 pruebas (88 + 49)**; también pasaron los ensayos
por procesos de preparación/renovación y de caída/recuperación con recargador.
Ver [alcance y uso de la copia integrada](copia_local_integrada.md).

## Uso habitual y diagnóstico

Con el entorno Python del proyecto activado:

```powershell
python manage.py runserver
```

No hace falta abrir Ubuntu ni arrancar un servicio manualmente. Al cerrar
`runserver`, el supervisor cierra únicamente su PostgreSQL privado. El recargador
de Django comparte la instancia del proceso padre, no inicia una segunda.

Para comprobar el motor local, con `runserver` cerrado:

```powershell
python manage.py diagnosticar_postgres_local
```

El diagnóstico hace `INSERT`, `SELECT`, `UPDATE`, `DELETE` y un bloque PL/pgSQL
en una tabla temporal, revierte la transacción y detiene su instancia. No
consulta ni modifica Aiven. Si otra instancia ya usa esa configuración, falla
con un mensaje en lugar de detenerla o cambiar de base.

Para repetir las pruebas completas desde el entorno de este equipo:

```powershell
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_hybrid_postgres_wsl.py
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/test_integrated_runserver.py --pg-bin /usr/lib/postgresql/16/bin --wsl-distribution Ubuntu-22.04 --wsl-user novapos-test --autoreload
& './build/hybrid/native-runtime-venv/Scripts/python.exe' -B scripts/verify_wsl_lifecycle.py
```

El último script mata exclusivamente su propio proceso Python de prueba para
verificar que el supervisor detecte su desaparición, cierre PostgreSQL de forma
ordenada y pueda arrancarlo otra vez. No mata servicios ajenos ni reinicia WSL.

## Arranque después de reiniciar Windows

La configuración y el clúster son persistentes. Al volver a ejecutar `runserver`,
`wsl.exe` arranca la distribución si está detenida y el supervisor inicia el mismo
clúster. No necesita una tarea al inicio de sesión ni un servicio Windows adicional.
Una tubería mantenida abierta conserva el supervisor y la sesión WSL mientras se
usa la aplicación; su cierre, incluso por caída del padre, solicita un apagado
rápido y transaccional de PostgreSQL, nunca `SIGKILL`.

Se detectó y corrigió una carrera al reiniciar inmediatamente: el reenviador
localhost de WSL puede tardar en liberar el puerto tras detenerse PostgreSQL.
Ahora se espera de forma acotada; si sigue ocupado, se informa el conflicto sin
matar al propietario, cambiar el puerto ni conectarse a otra base.

Se comprobaron cierre normal, cierre forzado del padre, arranque posterior y
persistencia de la configuración. También se verificó el arranque en frío: Ubuntu
figuraba `Stopped` en `wsl --list --verbose`; el diagnóstico ejecutado desde
Windows lo inició y completó PL/pgSQL y lectura/escritura sin abrir Ubuntu manualmente.
**No se reinició físicamente Windows** durante
esta tarea: queda esa comprobación manual en el próximo reinicio del equipo.

## Evidencia y alcance de las pruebas

- PostgreSQL informó `16.15 ... x86_64-pc-linux-gnu` desde Python de Windows.
- PL/pgSQL ejecutó `DO ... LANGUAGE plpgsql` y cambió datos temporales correctamente.
- Se comprobó `/usr/lib/postgresql/16/lib/plpgsql.so`, cabecera ELF `7f454c46`.
  Este backend no ejecuta `postgres.exe` ni carga `plpgsql.dll` de Windows.
  El controlador PostgreSQL del cliente Windows es independiente de esa DLL del servidor.
- **112 pruebas correctas (63 + 49)** desde Windows contra PostgreSQL WSL:
  sesiones, permisos, lecturas, ventas idempotentes, migración
  de confirmaciones, recibos, Nequi y recepción híbrida en bases ficticias WSL.
- Proceso real de `runserver` desde Windows: login y autocomplete originales,
  misma sesión, nube ficticia disponible → caída real → lectura local → recuperación.
  Se repitió con el recargador de Django habilitado y al reiniciar todavía offline.
- Las escrituras offline sin diario siguieron bloqueadas deliberadamente.
- La instancia nativa del puerto 5432 se mantuvo. Los motores de prueba terminaron
  detenidos. Los registros y las bases ficticias se conservaron para inspección.
- Smart App Control: `On`; Microsoft Defender: antivirus y protección en tiempo real
  activos al finalizar las comprobaciones. No se desactivó ninguna protección.

**Esto resuelve el motor local bloqueado en este PC; no termina el POS híbrido.**
La preparación/renovación de la réplica autorizada está implementada; faltan el
diario en las vistas originales, su conciliación y el cambio seguro de cajero/caja.
El modo experimental integrado se utiliza en las pruebas, no se activó para las
ventas reales de este equipo. Tampoco se aplicaron migraciones en Aiven.

## Respaldo, incidencias y mantenimiento

- Respaldar con `pg_dump`/restauración verificada antes de migrar versiones o
  activar datos reales; no copiar un directorio PostgreSQL mientras esté abierto.
- No borrar `config.json`, el directorio Linux ni desregistrar Ubuntu con datos
  pendientes. Las credenciales y el clúster pertenecen al mismo equipo.
- Un cambio de versión mayor de PostgreSQL o del esquema exige una actualización
  supervisada con respaldo. El arranque no reinicializa una copia vacía encima.
- Registros: `wsl-runtime.log` junto a la configuración Windows y `postgres.log`
  e `initdb.log` en el directorio privado Linux (padre de `postgres`).
- Si WSL no arranca, el puerto está ocupado o el clúster falla, corregir la causa
  indicada; no desactivar SAC/Defender ni utilizar `trust` en la autenticación.

## Distribución a clientes

No elegir WSL por defecto: añade una distribución por usuario, virtualización,
actualizaciones de dos entornos, almacenamiento virtual y diferencias de red.
La configuración de este PC tampoco debe copiarse con sus claves a otros equipos.

Dos alternativas a evaluar:

1. **Instalación nativa compatible con SAC**, validando el paquete completo
   (ejecutables y DLL, no solo la firma del instalador), el servicio local,
   actualizaciones firmadas y una matriz de pruebas con SAC activado.
2. **Servidor local por sucursal** con PostgreSQL administrado, respaldos y UPS;
   los equipos acceden por LAN y la sucursal sincroniza con Aiven. Permite
   trabajar sin Internet mientras el servidor y la LAN funcionen. Si también
   debe resistir la pérdida de LAN o de ese servidor, siguen siendo necesarias
   copias/colas durables por terminal y resolución de conflictos.

Un servidor exclusivamente en Internet no satisface por sí solo el requisito
de seguir vendiendo sin conexión.

Documentación oficial: [conexión Windows–WSL por localhost](https://learn.microsoft.com/en-us/windows/wsl/networking),
[comandos y arranque de WSL](https://learn.microsoft.com/en-us/windows/wsl/basic-commands),
[pruebas de compatibilidad con Smart App Control](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control).
