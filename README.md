# NovaPOS · instalación online y offline

La misma aplicación e interfaz: conexión online con Aiven y copia local para las
operaciones offline implementadas. Cada computador requiere su propia activación.

## Instalar en un computador nuevo

**Windows 10/11 de 64 bits. No compatible con Windows 7, 8, 32 bits o ARM.**

1. Abre [Descargas de NovaPOS](https://github.com/william18nova/Merk-888/releases/tag/novapos-windows-2026.10.18-rc1).
2. En **Assets**, descarga `NovaPOS-Instalar-Windows-x64.exe` y `SHA256.txt`.
   El ZIP automático **Source code** es código fuente, no el instalador.
3. Sigue la [guía de instalación](docs/instalador_windows.md).
4. Un administrador configura la conexión SQL con TLS y vincula el usuario y la
   caja autorizados. La primera preparación necesita internet y un turno online.
5. Abre NovaPOS desde su acceso directo y comprueba el estado de sincronización.

**Candidato de pruebas supervisadas, no instalador certificado para todos los PC.**
No está firmado. Si Windows lo bloquea, no desactives Smart App Control, Defender
ni otras protecciones. Consulta las [limitaciones](docs/instalador_windows_respaldo_wsl2.md).
No sobrescribe instalaciones existentes; las actualizaciones requieren respaldo
y revisión de ventas pendientes.

## Qué incluye y qué debes configurar

- Python, dependencias, PostgreSQL nativo, lanzador y recursos de la interfaz local.
- Alternativa WSL2 con confirmación únicamente ante el bloqueo nativo reconocido;
  su preparación adicional requiere internet, virtualización y posiblemente reinicio.
- No incluye bases reales, contraseñas, sesiones ni claves de este computador.
- El administrador debe tener los datos de conexión SQL de Aiven y su certificado CA;
  no basta con escribir la URL de PythonAnywhere. No compartas estas claves por GitHub.
- PTM/Nequi externos requieren conexión. Las funciones offline dependen de los
  permisos y de una autorización local vigente (24 horas desde la última validación).
- Configurar impresoras, lector PTM y credenciales de servicios externos es independiente.

## Lector PTM

La extensión está en `browser_extensions/ptm_reader`.
Sigue la [guía del lector](docs/lector_ptm.md). Consulta movimientos; no realiza
transacciones. La clave se obtiene en Caja → Conciliación PTM.

## Código y despliegue

Clonar este repositorio no instala automáticamente las dependencias ni activa la
copia offline. Para un equipo de caja nuevo utiliza el instalador y la guía.
PythonAnywhere es un despliegue separado: descargar o instalar NovaPOS no cambia
el servidor ni ejecuta migraciones en Aiven.

Los ejecutables se distribuyen en Releases, no dentro del historial Git. Los
archivos privados y bases locales permanecen excluidos del repositorio.
