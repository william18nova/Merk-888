# NovaPOS integrado · Windows 10/11 x64

## Estado del paquete

Instalador **candidato para pruebas supervisadas**, no certificación de despliegue
en todos los equipos. Requiere validar instalación en Windows 10 y 11 limpios,
firma de código confiable y compatibilidad de los binarios con las políticas de
cada equipo antes de distribuir a clientes. No desactives ninguna protección.

No admite Windows 7, 8/8.1, Windows de 32 bits ni ARM. Windows 10 debe conservar
actualizaciones de seguridad mediante el programa que corresponda; se recomienda
Windows 11 compatible y actualizado.

## Instalar

1. Copia `NovaPOS-Instalar-Windows-x64.exe` al computador de destino y comprueba
   el SHA-256 publicado junto al archivo. Un hash no sustituye la firma de código.
2. Ejecuta el instalador desde la cuenta Windows que usará la caja. Instala en
   `%LOCALAPPDATA%\Programs\NovaPOS`; no necesita permisos de administrador,
   cambiar PATH, desactivar antivirus ni instalar un servicio global PostgreSQL.
3. El asistente abre **Configurar NovaPOS**. Un administrador debe introducir
   servidor, puerto, base, usuario SQL de aplicación, contraseña y certificado CA
   del proveedor. TLS verifica la identidad del servidor (`verify-full`).
4. Introduce el nombre del usuario de NovaPOS y el de la caja. Ese usuario debe
   tener un turno abierto online. Pulsa **Comprobar**; verifica la identidad y el
   turno mostrados, luego **Vincular equipo**. No se elige una caja automáticamente.
5. Se crea una base privada nueva en el puerto 55460, se valida PL/pgSQL, se
   descarga una copia autorizada en modo solo lectura y se guarda un respaldo
   previo. No se ejecutan migraciones ni ventas de prueba en Aiven.
6. Pulsa **Abrir NovaPOS**. Conserva abierta la ventana del lanzador. Al terminar,
   pulsa **Detener** o cierra el lanzador y espera a que PostgreSQL se detenga.

Los componentes vienen en el paquete. La primera vinculación sí necesita internet.
El instalador no copia claves, bases, sesiones ni la identidad del computador del
desarrollador. La conexión SQL se cifra con DPAPI para la cuenta Windows actual;
los archivos locales mantienen permisos privados. Un administrador del equipo o
un proceso bajo esa misma cuenta puede acceder a los datos: DPAPI no sustituye el
cifrado del disco, la seguridad de Windows ni un rol SQL de mínimos privilegios.

## Uso habitual

- Acceso directo **NovaPOS**, luego **Abrir NovaPOS**. Después de reiniciar Windows
  es el mismo procedimiento: el lanzador inicia su PostgreSQL cuando se necesita.
- Abre la aplicación habitual en `http://127.0.0.1:8000/`. No hay otro POS reducido:
  el lanzador ejecuta el mismo `manage.py runserver --noreload` y su integración.
- Si se cae internet, las operaciones soportadas pasan automáticamente a la copia
  local. La autorización offline vence a las 24 horas desde su última validación.
- La sincronización al volver la conexión conserva la cola e idempotencia del
  proyecto. Revisa su pantalla de sincronización antes de apagar o cambiar de caja.
- Nequi/PTM online y otras acciones que requieren nube siguen necesitando conexión.
  El instalador no añade capacidades offline que la aplicación aún no tiene.
- Impresión física, agentes externos y credenciales de Telegram/IA no se distribuyen
  automáticamente: se configuran aparte según el equipo. No se probaron impresoras.

## Protecciones, conflictos y datos existentes

- No usa el PostgreSQL de 5432 ni sus bases; si 55460 o 8000 están ocupados, avisa
  y no se conecta al servicio equivocado.
- No instala WSL automáticamente. Si Smart App Control o Code Integrity impide
  cargar PostgreSQL/PL/pgSQL, la preparación falla y conserva el diagnóstico local.
  Usa binarios de proveedor compatibles y firmados, o evalúa explícitamente un
  servidor local de sucursal. No uses exclusiones ni desactives SAC/Defender.
- Si hay una instalación en el destino, se rechaza sobrescribirla. No borres esa
  carpeta: una actualización requiere respaldo, revisión de pendientes y migración
  controlada. Este primer instalador es **solo de instalación nueva**.
- La desinstalación/actualización automática aún no se habilita, para no eliminar
  ventas pendientes ni desacoplar la copia de su identidad.
- No copies el directorio instalado, `.novapos-runtime.json`, las conexiones DPAPI
  ni las carpetas de bases a otro PC. Cada equipo requiere su propia vinculación.

## Construcción del paquete

Desde el repositorio, con Python compatible y compilador C# de .NET Framework:

```powershell
python scripts/download_windows_components.py
python scripts/build_windows_integrated.py --stage build/windows-integrated-candidate --python-zip build/windows-integrated-downloads/python-3.13.16-embed-amd64.zip --postgres-zip build/windows-integrated-downloads/postgresql-16.15-windows-x64.zip
```

El código empaquetado usa la misma aplicación, pero reemplaza las credenciales
incorporadas en settings por configuración privada. La rama de Django del paquete
es 5.2 LTS, no la antigua 5.1 del requirements principal; necesita su propia regresión.
El manifiesto registra versiones resueltas y hashes. La compilación no cambia la
configuración de PythonAnywhere ni su base de datos.

El compilador genera un EXE no firmado. Una distribución comercial debe firmar
el lanzador y el instalador con certificado de confianza, validar también las DLL
de los proveedores y aplicar actualizaciones mantenidas. Firmar solo el instalador
no resuelve una DLL bloqueada. Tampoco convierte `runserver` en servidor apto para
exponerlo a una red: esta distribución escucha **solo en localhost**.
