# Entrega comprobada: nativo primero, WSL2 con autorización

Fecha: 13 de octubre de 2026. Estado: **candidato para pruebas controladas**.

## Archivo para copiar al equipo de prueba

`build/windows-integrated-fallback-20261013-e/NovaPOS-Instalar-Windows-x64.exe`

- Tamaño: 92.675.584 bytes.
- SHA-256: `4a35495e4b04e44109c79b30f080aae99150c744c1f8fae38ef156776ad43825`.
- No está firmado. No desactivar ninguna protección si Windows lo rechaza.
- No incluye contraseñas, bases del negocio ni identidad del equipo de desarrollo.
- Solo instalación nueva: rechaza sobrescribir una instalación existente.

## Resultados observados

1. 91 pruebas automatizadas aprobadas: selección, consentimiento, preservación de
   copias existentes, diagnóstico específico 4551 y arranque integrado, entre otras.
2. EXE compilado, comprobación de plataforma y extracción a carpeta nueva correctas.
3. Integridad de 9.650 archivos del paquete extraído comprobada.
4. Python empaquetado, Django 5.2.18, DPAPI, comprobaciones Django y 85 plantillas
   con sus recursos offline correctos; cero conexiones de base en esta prueba.
5. Prueba nativa del candidato E: clúster ficticio nuevo, PL/pgSQL, escritura y
   lectura, respaldo, listado y decodificación completa correctos. Puerto 55468
   liberado al finalizar. En candidatos anteriores hubo bloqueos 4551; no se
   atribuye la diferencia a una causa no verificada ni se garantiza que otro PC
   admita los mismos binarios. El instalador decide mediante la prueba real.
6. Prueba WSL2 repetida desde el EXE E extraído: PL/pgSQL, lectura/escritura,
   77 tablas ficticias, dos arranques y persistencia del registro original.
   Respaldo, listado y decodificación completa realizados con herramientas Linux,
   usando una ruta nativa inexistente para demostrar que no depende de ellas.
   Puerto 55469 liberado al terminar.
7. Smart App Control permaneció activado (valor observado 1). No se cambiaron
   Defender, firewall ni otras protecciones. El servicio existente en 5432 se
   conservó. No se ejecutaron escrituras ni pruebas financieras en Aiven.

La preparación inicial de la distribución WSL aislada se ejercitó simulando
únicamente la detección del bloqueo nativo, porque el servidor nativo admitía
ejecución en esa prueba. La importación, instalación Linux y pruebas SQL y de
respaldo fueron reales. La simulación no forma parte del instalador distribuido.

Evidencia local:

- `build/windows-integrated-fallback-extracted-smoke-e/results.json`.
- `%LOCALAPPDATA%/NovaPOS/installer-tests/native-final-20261013-e/result.json`.
- `%LOCALAPPDATA%/NovaPOS/installer-tests/fallback-20261013-d/verified-linux-backup-final-e.json`.

## Siguiente validación

Seguir `docs/instalador_windows.md` en un equipo de prueba Windows 10/11 x64,
con servidor y datos ficticios. No usar credenciales de producción para simular
ventas. WSL2 requiere Windows 10 compilación 19041 o posterior, virtualización,
internet para prepararlo y espacio libre. La aplicación conserva su interfaz.

Faltan pruebas en Windows limpios, UAC cancelado, WSL ausente, reinicio completo
de Windows, dos computadores con ventas/sincronización ficticias y firma de código
confiable. No se probaron impresoras. No hay actualización ni desinstalación
automática habilitada: preservar datos y ventas pendientes tiene prioridad.

Los cambios no se subieron a GitHub ni a PythonAnywhere como parte de esta entrega.
