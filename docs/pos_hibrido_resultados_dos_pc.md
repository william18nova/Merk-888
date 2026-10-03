# Resultado del piloto de dos instalaciones — 3 de octubre de 2026

Paquete final: `722dcc743f166e5931dcac2332b5539e2f15c721ceac3cd4e9b842fe4d423179`.
Esquema local: 2. Archivos entregados: 520. Sin firma de código ni certificación
para producción. Solo datos ficticios; no se accedió a Aiven/PythonAnywhere.

## Pruebas realizadas

- 126 pruebas Django/negocio aprobadas en PostgreSQL 18.6 Windows: permisos,
  réplica, ventas, revisión de pendientes, egresos, devoluciones, cierres y relevo.
- 59 pruebas unitarias de cliente, persistencia, respaldo y actualización aprobadas.
- 4 pruebas TLS aprobadas en Windows y Linux: certificado privado, comparación
  de huella, rechazo de otros destinos y sin desactivar la validación HTTPS.
- Instalación limpia de dependencias aprobada mediante el asistente en Windows
  Python 3.12 y Linux Python 3.10; `pip check` sin incompatibilidades.
- ZIP Windows final extraído y TAR Linux final extraído: prueba integral aprobada
  en cada sistema con **tres clústeres PostgreSQL privados**, un servidor HTTPS
  y dos cajas persistentes con identidades independientes.
- Navegador Edge: login real, historial/detalle de sincronización, cierre
  confirmado, escritorio de 1440 px y móvil de 390 px. Sin desbordamiento
  horizontal, errores JavaScript ni peticiones externas. Impresión excluida.

## Escenarios integrales aprobados en ambos sistemas

1. Vinculación real por HTTPS, con certificado comprobado y código por equipo.
2. Ventas simultáneas en las dos cajas.
3. Respuesta perdida después de guardar en el servidor: reintento sin duplicar.
4. Servidor detenido: efectivo, Nequi, tarjeta, mixto, egresos y devolución
   pendiente conservados en cada base independiente.
5. Arranque de ambas cajas sin servidor y conservación de todos los pendientes.
6. Reconexión, conciliación y descarga de inventario compartido sin sobrescribir.
7. Cierre offline de ambas cajas, posterior confirmación y reintentos inocuos.

Resultado central en cada ensayo: **6 ventas, 2 egresos, 1 devolución y 2 cierres**,
11 operaciones en total, ninguna duplicada. Las ventas conservaron su autor:
4 de `prueba1` y 2 de `prueba2`. Las dos copias convergieron a 15 unidades de
ARROZ FICTICIO y -1100 gramos de TOMATE FICTICIO X GR. Ambas cajas quedaron
CERRADAS y sin movimientos pendientes.

Durante la preparación se detectó y corrigió una ruta faltante del servidor
ficticio que hacía fallar la respuesta del cierre. Los paquetes finales incluyen
la corrección; no deben utilizarse los candidatos anteriores del directorio build.

## Lo que falta comprobar en los equipos físicos

Esta validación usó procesos/bases independientes en este PC; Linux se probó
mediante WSL. No equivale a ejecutar dos computadores físicos separados.
Ahora corresponde seguir `LEEME-PRIMERO.md` en ambos equipos para comprobar su
red privada, firewall, permisos, suspensión/reinicio y condiciones de uso.
No se realizaron pruebas de impresión, cortes eléctricos bruscos ni carga de
producción. No instalar encima de los datos del negocio.

Artefactos reproducibles del repositorio:

- `scripts/test_two_installed_pilot.py`
- `scripts/test_pilot_release_browser.cjs`
- `outputs/two-pc-pilot/windows-release.json`
- `outputs/two-pc-pilot/linux.json`
- `outputs/two-pc-pilot/browser/report.json`
