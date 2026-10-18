# Instalador nativo con respaldo WSL2 · 13 de octubre de 2026

## Comportamiento implementado

1. En una instalación nueva se prueba PostgreSQL nativo: `initdb`, arranque,
   PL/pgSQL, SQL de escritura/lectura y respaldo con verificación completa,
   en un clúster de prueba independiente.
2. Si funciona, se guarda la elección nativa. Si devuelve específicamente el
   error de Control de aplicaciones 4551, se ofrece WSL2 con consentimiento.
3. No se ofrece el cambio por contraseñas, puertos ocupados, permisos genéricos,
   dependencias faltantes o errores desconocidos. Se conserva el diagnóstico.
4. WSL2 se prepara en un Ubuntu Base exclusivo por instalación, descargado de Canonical
   con SHA-256 fijado. Se verifican nombre, registro, carpeta y versión WSL2 antes
   de instalar paquetes. No se reutiliza ni modifica el Ubuntu del desarrollador.
5. Se prueba PostgreSQL Linux desde Windows antes de guardar la elección WSL2.
   Los siguientes arranques usan esa misma configuración y supervisor de procesos.
6. Una copia existente o incompleta impide cambiar de motor automáticamente.
   El instalador nunca reemplaza ventas pendientes por una base vacía.

El botón de instalación oficial de WSL requiere confirmación y UAC. No reinicia
Windows, no cambia su firewall y no desactiva SAC/Defender. En Windows 10 requiere
compilación 19041 o posterior para este procedimiento automatizado y virtualización.
La imagen Linux y los paquetes se descargan durante la primera preparación; no
están incluidos en el EXE nativo. Se requieren internet y al menos 5 GB libres.

## Pruebas

- 91 pruebas automatizadas aprobadas cubren regresiones de arranque integrado,
  selección, consentimiento, rechazo de copia existente, diagnóstico, propiedad WSL,
  liberación del puerto y respaldo sin clientes PostgreSQL Windows.
- Compilación C# del instalador/lanzador: correcta.
- La prueba real de este nuevo paquete pudo ejecutar el motor nativo, PL/pgSQL
  y lectura/escritura. Esto no invalida el bloqueo 4551 registrado anteriormente
  con otro candidato, ni garantiza confianza en otros computadores. SAC permaneció
  activado; el instalador decide por el resultado real del equipo, no por asumirlo.
- La prueba de respaldo inyecta explícitamente `NativePolicyBlocked` dentro del
  entorno aislado para ejercitar WSL2 aunque la prueba nativa actual pase.
  No cambia ninguna política para provocar el bloqueo.
- Se corrigió la espera del relé localhost de WSL antes de declarar terminada la
  prueba del motor. Se espera la liberación del puerto sin matar procesos ajenos.
- Se detectó también 4551 en un cliente PostgreSQL Windows. En modo WSL, tanto
  `pg_dump` como `pg_restore --list` y la decodificación completa se ejecutan en
  Linux. Nunca se ejecuta una restauración SQL como parte de la verificación.
- Prueba real WSL2 aprobada desde el Python empaquetado: 77 tablas ficticias,
  PL/pgSQL, escritura/lectura, dos arranques y persistencia del registro de prueba.
  El respaldo, listado y decodificación completa aprobaron con una ruta nativa
  inexistente, comprobando que usan exclusivamente los clientes PostgreSQL Linux.
  El puerto 55469 quedó liberado después de detener el motor; no hubo conexiones
  a la nube ni cambios a las bases existentes del negocio.
- La prueba inicial nativa también verifica ahora `pg_dump` y `pg_restore`:
  un bloqueo 4551 de estos clientes se detecta antes de crear la copia del negocio,
  aunque el servidor y PL/pgSQL hayan funcionado.

## Validaciones pendientes antes de distribución general

- Instalación completa en Windows 10 y 11 limpios; UAC cancelado, virtualización
  deshabilitada, WSL ausente y continuación después de reiniciar Windows.
- Firma confiable del lanzador/instalador y compatibilidad de Python con las
  políticas de cada cliente. WSL elimina la dependencia de
  `plpgsql.dll` del servidor nativo, no todas las dependencias Windows.
- Pruebas financieras online/offline con datos ficticios en dos equipos y la
  versión Django empaquetada. No se realizaron ventas reales ni escrituras Aiven.
- Actualización/desinstalación con respaldo y recuperación; el candidato sigue
  siendo de instalación nueva y rechaza sobrescribir una existente.

Fuentes de aprovisionamiento:
- https://learn.microsoft.com/en-us/windows/wsl/basic-commands
- https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro
- https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/SHA256SUMS
