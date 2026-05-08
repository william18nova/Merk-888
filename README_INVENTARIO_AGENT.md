# Inventario desde fotos con agente local

## Arquitectura
- Django/PythonAnywhere sirve la pagina y confirma el inventario.
- El navegador descarga el catalogo CSV desde Django.
- El navegador envia fotos + catalogo al agente local del PC (`inventario_local_agent.py`).
- El agente ejecuta `gemini_selenium_cli.py` en ese mismo equipo.
- El resultado vuelve al navegador y luego se confirma contra Django.

## Archivos principales
- `inventario_local_agent.py`: API local en `http://127.0.0.1:8788`
- `start_inventario_agent.cmd`: levanta el agente en Windows
- `requirements_local_agent.txt`: dependencias del agente
- `gemini_selenium_cli.py`: script local de Gemini/DeepSeek

## Como arrancarlo en Windows
1. Cierra Chrome si el perfil local va a ser usado por Selenium.
2. Abre `start_inventario_agent.cmd`.
3. Verifica que el agente responda en `http://127.0.0.1:8788/ping`.
4. Entra a la pagina `Inventario desde fotos`.

## Usar el celular como camara
1. Asegura que el PC y el celular esten en la misma red WiFi.
2. Abre `start_inventario_agent.cmd`; el agente escucha en `0.0.0.0:8788`.
3. En `Inventario desde fotos`, pulsa `Conectar celular`.
4. Escanea el QR con el celular.
5. Toma o selecciona fotos y toca `Enviar al PC`.
6. Las fotos apareceran automaticamente en la pantalla del PC para procesarlas.

Si el celular no abre el enlace, permite el puerto `8788` en el firewall de Windows para redes privadas.

## Variables utiles
- `INVENTARIO_AGENT_URL` (web): URL del agente local, por defecto `http://127.0.0.1:8788`
- `INVENTARIO_AGENT_TOKEN` (web y agente): token compartido entre navegador y agente
- `INVENTARIO_AGENT_HOST` (agente): usa `0.0.0.0` para aceptar celulares en la red local
- `INVENTARIO_FOTOS_SCRIPT` (agente): ruta del `gemini_selenium_cli.py`
- `INVENTARIO_FOTOS_TIMEOUT` (agente): timeout del proceso en segundos
- `INVENTARIO_MOBILE_SESSION_TTL` (agente): duracion de la sesion movil en segundos

## Nota
El endpoint Django `inventario/fotos/procesar/` quedo solo como respaldo opcional. Por defecto esta desactivado para evitar ejecutar Selenium dentro del servidor.
