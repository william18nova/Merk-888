# Fiabilidad del bot de Telegram — 4 de noviembre de 2026

Las pruebas no publican código, no reinician tareas ni modifican producción.
La activación en el servidor se realiza por separado, después de verificarlas.

## Qué se refuerza

- Reintentos de cola espaciados (5 y 15 segundos más un pequeño margen), hasta
  tres intentos. Si Telegram pide esperar más, se respeta ese plazo. Esperar en
  una conversación no impide atender las otras. No se añade un aviso de error
  inmediato durante una pausa impuesta por Telegram, aunque se agoten intentos.
- Una pausa corta de Telegram se resuelve reenviando el mismo mensaje rechazado,
  sin volver a interpretar ni ejecutar la solicitud. Los mensajes largos y la
  segunda burbuja de una clave se separan para evitar ráfagas.
- Un timeout de lectura, una desconexión al enviar, una respuesta inválida o un
  error de servidor pueden dejar una entrega incierta. No se repite a ciegas la
  operación ni todo un informe parcialmente entregado. Se conserva el resultado
  seguro y se pide comprobar el chat y la web. No se promete entrega exactamente
  una vez.
- Una clave nunca se guarda en claro ni se regenera para resolver un fallo de
  envío. Si falla cualquiera de sus dos burbujas, hay que revisar/revocar la
  autorización en la web antes de solicitar otra.
- Los avisos efímeros de los botones son opcionales: si caducan o fallan, no
  impiden confirmar o consultar una solicitud válida. Los permisos, botones y
  transacciones del dominio siguen siendo obligatorios.
- La descarga de voz usa streaming, controla el tamaño real (máximo 20 MB), no
  sigue redirecciones, valida la ruta y cierra la conexión también si falla.
  Se controla el plazo entre bloques; los timeouts de red no son una garantía
  absoluta frente a DNS o sistemas externos bloqueados.
- Un trabajo asíncrono de voz existente se consulta sin volver a descargar o
  subir el audio. Los fallos temporales de esa consulta conservan el mismo
  trabajo, dentro de su límite de diez minutos. No se añaden proveedores ni se
  habilitan servicios de pago.
- Una caída de BD al arrancar o durante el ciclo usa recuperación con espera
  progresiva. Un error inesperado de ciclo no descarta la cola. `--once` falla
  con un diagnóstico seguro. No se imprimen SQL, cuerpos HTTP, texto del usuario,
  claves ni tokens en las nuevas rutas de error.
- El webhook rechaza identidades, tamaños y estructuras mal formados antes de
  intentar guardarlos. Telegram sigue deduplicándose por `update_id`.

## Diagnóstico sin afectar el negocio

```bash
python manage.py diagnosticar_telegram_bot
python manage.py diagnosticar_telegram_bot --json
```

Solo devuelve cantidades, tiempos y estados agregados. No llama a Telegram ni a
la IA y no recupera o reenvía mensajes en ERROR. Un error de entrega no significa
que una operación confirmada haya fallado: comprobar el resultado en la web.

## Pruebas y publicación

```bash
python -B manage.py test mainApp --pattern='test_telegram*.py' --settings=NovaSoft.test_settings --noinput
python -B scripts/test_hybrid_postgres_wsl.py --telegram-only
```

No requiere migraciones ni dependencias nuevas. Tras publicar, se debe actualizar
la tarea existente, nunca lanzar otro trabajador para probarlo. Las claves y los
pagos reales no deben usarse como pruebas de recuperación.

Verificación local: **533 pruebas correctas en SQLite y las mismas 533 en
PostgreSQL desechable**, sin llamadas reales a Telegram o a proveedores de IA.
Incluyen 43 casos nuevos de transporte, pausas, recuperación, audios pendientes,
datos malformados, diagnóstico y protección de transacciones y claves.

Referencias de los contratos de red:
[pausas indicadas por Telegram](https://core.telegram.org/bots/api#responseparameters),
[descarga de archivos](https://core.telegram.org/bots/api#getfile) y
[alcance real de los timeouts de Requests](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).
