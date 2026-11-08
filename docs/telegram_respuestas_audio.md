# Respuestas habladas de Telegram

La voz es un complemento opcional. El bot entrega primero la respuesta escrita,
incluidos los botones y archivos, y guarda el resultado normal de la solicitud.
Solo después envía su lectura automática en una nota Ogg/Opus, vinculada al
mensaje escrito. **El audio nunca confirma ni vuelve a ejecutar una operación.**

## Activación

Por defecto está desactivada; publicar este código no activa facturación ni
envía las respuestas del negocio a otro servicio. Antes de activarla, el titular
debe autorizar que el texto de las respuestas se envíe a **Google Gemini TTS** y
revisar la cuota, el tratamiento de datos y la facturación de su proyecto. Una
cuota gratuita documentada no garantiza que una cuenta con facturación activa
no genere cargos. No se crea ni se activa ningún plan de pago desde NovaPOS.

En el archivo privado que ya utiliza el trabajador, por ejemplo
`/home/Merk888/.telegram_bot.env`, configura una de estas modalidades:

```bash
# Solo añade voz cuando el usuario envía una nota de voz:
TELEGRAM_AUDIO_REPLIES=voice
# Para añadir voz también a mensajes de texto y respuestas de botones:
# TELEGRAM_AUDIO_REPLIES=always
# Para desactivarla inmediatamente en el siguiente arranque:
# TELEGRAM_AUDIO_REPLIES=off

GEMINI_TTS_MODEL=gemini-3.8-flash-lite-tts
GEMINI_TTS_VOICE=Kore
TELEGRAM_FFMPEG_BIN=ffmpeg
```

Se reutiliza `GEMINI_API_KEY`; no se debe copiar esa clave al repositorio ni al
chat. También se admite `gemini-3.8-flash-tts`; no se permiten modelos o destinos
arbitrarios. No hay cambio de proveedor automático que comparta las respuestas
con servicios no autorizados.

FFmpeg debe estar disponible para el trabajador y ofrecer el codificador
`libopus`. El programa puede comprobarse con `ffmpeg -version`; si está fuera
del PATH, usa su ruta absoluta en `TELEGRAM_FFMPEG_BIN`. Si falta, no se envía
el texto a Gemini para producir un audio que no se podría entregar.

Después de guardar la configuración, reinicia **la tarea Always-on existente**;
no arranques un segundo trabajador. La página de configuración del bot muestra
«Respuestas habladas» y el modo configurado. Ese indicador comprueba la
configuración, no la conexión, el saldo ni la cuota del proveedor.

No requiere migraciones ni paquetes Python nuevos. Al actualizar código, el
trabajador existente con `--reload` se recarga entre solicitudes; el cambio del
archivo privado requiere reiniciar la tarea para volver a cargar sus variables.

## Comportamiento y límites

- Las claves de un solo uso y respuestas con un resumen de historial separado
  no se envían a síntesis. Las dos burbujas de una clave se conservan por escrito.
- Solo admite chats privados con cuenta vinculada y usuario activo. Revalida
  esa vinculación antes de sintetizar y antes de enviar el audio.
- La lectura conserva la respuesta breve, sin otra interpretación de la IA ni
  resumen inventado de importes. En respuestas de más de 1.200 caracteres, el
  audio avisa de que debe consultarse el detalle escrito, sin recortar cifras o
  condiciones a mitad de frase. Los botones siempre están en el texto.
- La nota indica que es una lectura automática. El texto y el resultado
  registrado en NovaPOS son la referencia, no una pronunciación sintetizada.
- Una única cola en memoria tiene hasta tres respuestas en espera, además de
  la lectura en curso; los trabajos caducan a los 90 segundos. No guarda audio
  en disco, en la base de datos ni en el almacenamiento del navegador.
- La síntesis tiene lecturas y tamaño acotados; se rechaza audio incompleto o
  de más de dos minutos. Los timeouts de red no garantizan un plazo absoluto
  frente a un DNS bloqueado, pero ese trabajo no bloquea la cola principal.
- Si Gemini está limitado, se aplica una pausa para no golpear su cuota en cada
  mensaje. Si falla la conversión o Telegram no confirma el envío, se conserva
  el texto y no se reenvía la operación ni el audio a ciegas.
- La voz es de mejor esfuerzo: si la cola está llena, se reinicia el trabajador
  o caduca un trabajo, puede faltar el audio de ese mensaje. No se reproduce
  automáticamente una respuesta antigua al volver a iniciar el proceso.

## Pruebas aisladas

```bash
python -B manage.py test mainApp.test_telegram_tts --settings=NovaSoft.test_settings --noinput
python -B manage.py test mainApp --pattern='test_telegram*.py' --settings=NovaSoft.test_settings --noinput
```

Las pruebas simulan Google, Telegram y los fallos de conversión. Nunca envían
datos reales, generan claves reales ni usan pagos de producción para probar.

Contratos consultados: [Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation),
[precios y planes de Gemini](https://ai.google.dev/gemini-api/docs/pricing) y
[notas de voz de Telegram](https://core.telegram.org/bots/api#sendvoice).
