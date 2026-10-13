# Limpieza del piloto separado

La instalación vigente usa el proyecto original con `python manage.py runserver`.
Consulta [la guía integrada](instalacion_integrada_runserver.md).

Se retiraron los constructores, instaladores, lanzadores, actualizadores y pruebas
de empaquetado del POS separado, sus tres archivos de requisitos exclusivos y
la especificación generada de PyInstaller. También se eliminaron los paquetes
obsoletos de `dist/hybrid` (101 archivos, 199125105 bytes).

Se conservaron deliberadamente:

- Los módulos compartidos de `local_pos`, `hybrid_client` y `pos_shared`, así como
  las utilidades usadas por pruebas de regresión. El nombre «hybrid» no implica
  que un archivo sea prescindible.
- El entorno Python activo, los recursos locales, las copias de prueba y los
  diagnósticos de `build`. No se hizo una eliminación general de esa carpeta.
- Las bases de datos, respaldos, configuración privada y credenciales.
- Los documentos históricos, señalados como tales para evitar seguir sus
  instrucciones de instalación antiguas.

Esta limpieza no publica cambios ni modifica PythonAnywhere.
