# NovaPOS Windows 10/11 x64 · 2026.10.18 RC1

Instalador actualizado para **instalaciones nuevas y pruebas supervisadas**.
No es una certificación para todos los computadores ni una actualización automática.

## Descargar e instalar

En Assets de esta versión descarga `NovaPOS-Instalar-Windows-x64.exe` y `SHA256.txt`.
No uses el ZIP automático Source code como instalador.

En PowerShell puedes comprobar el archivo descargado:

```powershell
Get-FileHash .\NovaPOS-Instalar-Windows-x64.exe -Algorithm SHA256
```

Debe coincidir con SHA256.txt. El instalador **no está firmado**: el hash comprueba
integridad, no sustituye una firma confiable. No desactives protecciones si Windows
lo bloquea.

Sigue [la guía de instalación](https://github.com/william18nova/Merk-888/blob/main/docs/instalador_windows.md).
Un administrador debe introducir la conexión SQL de Aiven y su CA, elegir el
usuario/caja y realizar la primera activación con internet y un turno abierto.
El programa no entrega credenciales de producción ni configura impresoras o bots.

## Contenido

- Misma interfaz del proyecto con Python, dependencias y PostgreSQL local.
- PostgreSQL nativo primero; alternativa WSL2 ante bloqueo 4551, con consentimiento.
- Recursos gráficos locales, cola de sincronización y funciones offline implementadas.
- Código actual de PTM y aclaración de bolsa separada frente a efectivo de caja.
- Sin bases reales, credenciales, sesiones ni identidad de este equipo.

## Comprobaciones de este candidato

- 28 pruebas del instalador aprobadas y una omitida.
- 58 pruebas PTM aprobadas en SQLite y tres omitidas por requerir PostgreSQL.
- Las 61 pruebas PTM aprobadas también con PostgreSQL nativo desechable.
- 45 pruebas de ventas offline, operaciones de caja y sincronización aprobadas
  con dos bases PostgreSQL locales ficticias.
- EXE extraído correctamente a una carpeta nueva.
- 9.666 archivos del paquete verificados contra su manifiesto.
- Dependencias, DPAPI, comprobaciones Django y 89 plantillas/recursos locales correctos.
- PostgreSQL nativo: creación de esquema de 87 tablas ficticias, PL/pgSQL,
  lectura/escritura y parada correctas. No se conectó a Aiven para esas pruebas.

## Limitaciones que permanecen

- Falta validar este candidato en Windows limpios, reinicios y dos equipos reales.
- No se probaron impresoras ni se certifica compatibilidad con todas las políticas SAC.
- No sobrescribe instalaciones previas; no borres una instalación con ventas pendientes.
- La preparación WSL2 requiere internet y puede necesitar permisos/reinicio.
- PTM y verificación externa de Nequi no funcionan sin conexión.
- La autorización offline dura 24 horas desde la última validación.
- Descargar la versión no activa automáticamente la conexión ni la copia del negocio.

No se modificó PythonAnywhere ni se ejecutaron migraciones en Aiven para esta publicación.

## Reproducir las comprobaciones (desarrollador)

Las pruebas del paquete usan `scripts/test_windows_installer.py` con una carpeta
nueva y `--native-postgres`. Las pruebas de PTM se ejecutan con
`scripts/test_hybrid_postgres_windows.ps1` sin `-Integrated`; las de
`local_pos.test_integrated_offline_sales.IntegratedOfflineSaleTests` y
`local_pos.test_integrated_business.IntegratedBusinessTests` se ejecutan aparte
con `-Integrated`. No mezclar ambos grupos: el segundo usa dos bases y routers,
mientras el primero tiene pruebas deliberadamente limitadas a una base.
