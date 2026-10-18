# Pruebas del instalador Windows integrado · 13 de octubre de 2026

## Resultado: candidato, NO aprobado todavía para distribución general

Se generó un EXE x64 autocontenido con el mismo código Django del proyecto,
Python 3.13.16, Django 5.2.18 y PostgreSQL 16.15 oficial EDB. No se instalaron
servicios, no se tocó Aiven y no se desactivó ninguna protección de Windows.

Las pruebas de Python, controlador PostgreSQL, cifrado DPAPI, autocompletado,
integridad de 9.644 archivos del primer paquete, `django check`, 85 plantillas y
bibliotecas de interfaz offline pasaron sin abrir conexiones a bases reales.
Además, pasaron **71 pruebas automatizadas** de empaquetado, seguridad, arranque
integrado y adaptador WSL (incluyen casos base heredados). El EXE pudo ejecutar
su comprobación de plataforma en este Windows con salida 0.

### Verificación final del candidato C

- Se ejecutó la extracción real del EXE a una carpeta nueva de prueba: salida 0.
  No se crearon accesos directos, credenciales ni bases durante esa comprobación.
- Desde los archivos extraídos pasaron otra vez las importaciones, DPAPI,
  integridad del paquete, `django check`, las 85 plantillas y los recursos offline.
- El paquete incluye una advertencia específica para el error 4551 y conserva el
  error original de `initdb` en el directorio privado de la instalación.
- Artefacto: `build/windows-integrated-candidate-20261013-c/NovaPOS-Instalar-Windows-x64.exe`.
- Tamaño: 92.655.616 bytes.
- SHA-256: `e8e24a359b6956145c9f68f4cc62729907beb963ae5a36b3cecd77078fe0fa9d`.

Esta verificación NO resuelve el bloqueo de PostgreSQL descrito a continuación,
ni sustituye una prueba de instalación completa en Windows 10 y 11 limpios.

## Bloqueo encontrado en este computador

La prueba de una base NUEVA, aislada en `build/windows-integrated-smoke-b2`, puerto
55468, falló durante `initdb`, al ejecutar `CREATE EXTENSION plpgsql`:

```text
could not load library ".../payload/postgresql/lib/plpgsql.dll": unknown error 4551
```

La DLL corresponde al PostgreSQL 16.15 recién descargado de EDB, no a la instalación
PostgreSQL 18 existente del equipo. Windows confirmó:

- Eventos **3077 y 3033**, `Microsoft-Windows-CodeIntegrity/Operational`.
- Fecha local: **2026-10-13 10:31:33**.
- Proceso: `.../payload/postgresql/bin/postgres.exe`.
- Biblioteca: `.../payload/postgresql/lib/plpgsql.dll`.
- Motivo: no cumple los requisitos de firma/nivel de confianza de la política.
- Mensaje nativo del error 4551: «Una directiva de Control de aplicaciones bloqueó este archivo».

Authenticode devolvió `Valid` para Python y su DLL principal, pero `NotSigned`
para `postgres.exe` y `plpgsql.dll` del paquete EDB. El EXE propio de NovaPOS
tampoco está firmado. Un hash de integridad no reemplaza una firma de confianza.

No se alcanzó la prueba de lectura/escritura ni el esquema completo con este motor
nativo. `initdb` retiró únicamente su propio clúster nuevo incompleto; las bases
habituales, incluida la de WSL, permanecieron intactas. No quedó escuchando 55468.

## Qué falta antes de distribuirlo

1. Resolver la compatibilidad del motor nativo con Control de aplicaciones:
   binarios de proveedor con confianza/firma adecuada, o una arquitectura elegida
   explícitamente (por ejemplo WSL para estaciones compatibles o servidor local
   de sucursal). No se eligió WSL automáticamente ni se debilitaron políticas.
2. Firmar el lanzador y el instalador. Firmar solamente el instalador NO soluciona
   una DLL de PostgreSQL rechazada.
3. Completar pruebas en Windows 10 y 11 limpios: dependencias de Visual C++,
   preparación, vinculación ficticia, lectura/escritura, desconexión, reinicio,
   reconexión y sincronización en dos equipos; regresión financiera con Django 5.2.
4. Implementar y validar actualizaciones con respaldo y recuperación. Este paquete
   rechaza sobrescribir una instalación existente y no tiene desinstalación de datos.

No usar el candidato para reemplazar la instalación habitual ni para pruebas sobre
ventas reales hasta resolver estos puntos.
