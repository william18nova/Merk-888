# Bot inteligente de Telegram

El bot consulta Nova mediante los permisos del usuario vinculado. El texto libre
usa Gemini y, si no está disponible, Groq como respaldo automático. Las notas de
voz usan Whisper en Groq. Un pago nunca se registra
directamente: se crea una propuesta que vence en 10 minutos y solo se ejecuta al
pulsar **Confirmar** en Telegram.

Al configurar ambas claves, Gemini interpreta primero el texto y Groq toma el
relevo si Gemini falla, agota cuota o devuelve una respuesta vacía o inválida.
El proveedor que falle descansa cinco minutos en cada proceso antes de volver a
probarse; cambiar su clave o modelo permite reintentarlo inmediatamente. La acción
se ejecuta una sola vez, después de obtener una respuesta válida. El panel muestra
los proveedores configurados; tener una clave guardada no garantiza su validez.

Las notas de voz se transcriben con Groq y el texto resultante pasa por esa misma
combinación Gemini/Groq. No es necesario enviar cada solicitud a ambos si el
primer proveedor responde correctamente.

## 1. Crear las credenciales

1. En Telegram abre `@BotFather`, crea el bot con `/newbot` y copia el token.
2. Genera un secreto largo para el webhook, por ejemplo con
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Crea una API key en Google AI Studio para Gemini.
4. Crea una API key en Groq para transcribir notas de voz.

No pegues ninguno de esos valores en Git, en `settings.py` ni en este documento.

## 2. Variables privadas en PythonAnywhere

Crea `/home/Merk888/.telegram_bot.env` con permisos privados:

```bash
chmod 600 /home/Merk888/.telegram_bot.env
```

Su contenido debe usar este formato:

```bash
TELEGRAM_BOT_TOKEN='TOKEN_REAL'
TELEGRAM_WEBHOOK_SECRET='SECRETO_LARGO_REAL'
TELEGRAM_WEBHOOK_URL='https://merk888.pythonanywhere.com/api/telegram/webhook/'
GEMINI_API_KEY='CLAVE_REAL'
GEMINI_MODEL='gemini-3.8-flash'
GROQ_API_KEY='CLAVE_REAL'
GROQ_CHAT_MODEL='openai/gpt-oss-120b'
GROQ_WHISPER_MODEL='whisper-large-v3-turbo'
```

Django y la tarea Always-on leen automáticamente ese archivo privado. No debes
copiar las claves al archivo WSGI. Después de crearlo, pulsa **Reload** en la
pestaña Web de PythonAnywhere.

## 3. Desplegar y activar

```bash
cd /home/Merk888/Merk-888
git pull
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
```

Luego:

1. En Nova abre **Seguridad → Funcionalidades del sistema** y activa el bot.
2. Abre **Seguridad → Bot inteligente de Telegram**.
3. Pulsa **Configurar webhook** usando la contraseña Web Master.
4. En PythonAnywhere crea una tarea **Always-on** con:

```bash
/bin/bash /home/Merk888/Merk-888/scripts/run_telegram_bot.sh
```

El registro del procesador queda en `/home/Merk888/logs/telegram_bot.log`.

## 4. Vincular personas

Desde la página de configuración selecciona el usuario de Nova y genera un
código. La persona lo envía al bot así:

```text
/vincular NOVA-XXXXXXXX
```

El código se guarda únicamente como hash, vence en 10 minutos y sirve una sola
vez. Si desactivas el vínculo, el bot deja de aceptar solicitudes de esa cuenta.

## 5. Prueba manual

Para verificar las claves y la interpretación real de cada proveedor, sin ventas
ni registros de pago de prueba:

```bash
python manage.py comprobar_ia_telegram
python manage.py comprobar_ia_telegram --provider gemini
```

El comando solo envía un texto genérico y comprueba la función seleccionada; no
ejecuta consultas del negocio ni imprime claves.

```bash
set -a
source /home/Merk888/.telegram_bot.env
set +a
cd /home/Merk888/Merk-888
source /home/Merk888/.virtualenvs/env/bin/activate
python manage.py procesar_telegram_bot --once
tail -n 100 /home/Merk888/logs/telegram_bot.log
```

Los comandos `/ventas`, `/producto`, `/inventario`, `/pagos`, `/balance`, `/turnos` y
`/estado` siguen disponibles aunque los proveedores inteligentes fallen. Con una
clave de Groq válida, el texto libre y los audios continúan funcionando aunque
Gemini no esté disponible.
