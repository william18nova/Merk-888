# Despliegue supervisado 2026-10-17

Paquete `build/releases/NovaPOS-cloud-20261017.zip`, SHA256:
`3e26008c6c8f901a9a021cda0897cc51502eebbf1170e004fcafb66b6c0dca21`.
Base Git local/servidor: `9c255da`. Incluye 54 archivos de aplicación;
comparación inicial en servidor: 47 cambios, sin conflictos.
No incluye settings, credenciales, marcador `.novapos-ptm-lab`, base ficticia,
instaladores Windows ni datos de usuarios.

Comprobaciones locales: 121 pruebas SQLite (118 aprobadas, 3 omitidas),
98 pruebas PostgreSQL y 29 de sincronización aprobadas.

Destino: `/home/Merk888/Merk-888`. Paquete subido a `/home/Merk888/`.
Desplegador extraído en `/home/Merk888/novapos-cloud-stage-20261017/`.
El desplegador verifica las huellas de todos los archivos antes de reemplazarlos
y requiere un respaldo completo verificado de menos de dos horas. Respalda
el código y revierte archivos si check o las pruebas fallan.

El servidor PostgreSQL es 17.11, pero el cliente preinstalado es 16. Se preparan
herramientas oficiales 17.11 en `/home/Merk888/.local/novapos-pg17`, sin modificar
el motor remoto. Fuentes verificadas mediante SHA256 oficial. Los logs quedan en
`/home/Merk888/.local/src/novapos-pg17/`.

Aplicado y verificado en servidor el 2026-10-18 UTC:

- Respaldo completo: `/home/Merk888/.local/share/NovaPOS/backups/aiven/20261018T024100Z-dc354003/`.
- Archivo de 10.828.735 bytes verificado por catálogo y lectura completa.
- SHA256 del respaldo: `b3d7060ce0957669cf9af42e39992d8054c3adc39f9e324fcf23f7fab943d613`.
- Respaldo de código: `/home/Merk888/novapos-deploy-backups/cloud-20261018T024135`.
- 47 archivos actualizados; check y 118 pruebas aprobadas, 3 omitidas en SQLite.
- Migración aditiva 0052 aplicada y esquema verificado; collectstatic completado.
- Web recargada. Página de bolsas, dashboard (25 filas) y Generar Venta cargan.
- Cero bolsas activadas; no se crearon operaciones ni usuarios ficticios en producción.
- Se solicitó reinicio de la tarea Telegram para cargar sus cambios.

La actualización es directa por paquete: todavía no está registrada en GitHub.
No ejecutar checkout/reset/pull que descarte los cambios del servidor sin integrarlos.
