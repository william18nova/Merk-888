# Inventario desde fotos

## URL principal
- `/inventario/fotos/`

## Flujo
1. Seleccionas la sucursal.
2. Adjuntas varias fotos.
3. La vista llama al script local `gemini_selenium_cli.py`.
4. El script devuelve JSON con productos y cantidades.
5. La tabla se muestra en pantalla.
6. Al aceptar, se suma la cantidad al inventario de la sucursal.

## Variables opcionales en `NovaSoft/settings.py`
- `INVENTARIO_FOTOS_SCRIPT`: ruta del script local a ejecutar.
- `INVENTARIO_FOTOS_TIMEOUT`: timeout en segundos del proceso local.

## Script local
Por defecto la app usa:
- `BASE_DIR / "gemini_selenium_cli.py"`

Si quieres apuntar a otra ruta, define la variable de entorno `INVENTARIO_FOTOS_SCRIPT`.

## Importante
Este flujo está pensado para correr en la misma máquina donde está Django, porque el script usa Selenium, Gemini y DeepSeek en local.
