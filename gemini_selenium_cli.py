from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import time
import os
import re
import pyautogui
import argparse
import json
import csv
import unicodedata
import difflib
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ====================================================
# CONFIGURACIÓN GENERAL
# ====================================================
ruta_user_data = r"C:\temp\selenium-profile"
nombre_perfil = "Default"

# Déjalo en False para evitar que DeepSeek meta más razonamiento visible
ACTIVAR_PENSAMIENTO_PROFUNDO = False

MARCADOR_INICIO_RESULTADO = "<<RESULTADO_FINAL>>"
MARCADOR_FIN_RESULTADO = "<<FIN_RESULTADO_FINAL>>"
MARCADOR_FIN_GEMINI = "<<FIN_RESPUESTA_GEMINI_9F2K>>"

PROMPT_GEMINI_PREDETERMINADO = (
    "Analiza únicamente la imagen adjunta en ESTE mensaje. "
    "Ignora por completo cualquier imagen, texto o respuesta de mensajes anteriores. "
    "Extrae todo el texto visible de esta imagen actual. "
    "Devuelve únicamente el texto plano  estructurado para que no se vea como un monton de caracteres sino que paresca una factura en formato de texto"
    "Conserva saltos de línea cuando existan en el texto original. "
    f"MUY IMPORTANTE: al final de toda tu respuesta escribe en una nueva línea exactamente esta bandera y nada más: "
    f"{MARCADOR_FIN_GEMINI}"
)

PROMPT_GEMINI_PREDETERMINADO = (
    "Actua como OCR estructurado y estricto. Analiza unicamente la imagen adjunta en ESTE mensaje. "
    "Ignora por completo cualquier imagen, texto o respuesta de mensajes anteriores. "
    "Devuelve unicamente texto plano, sin markdown, sin explicaciones y sin comillas. "
    "Usa SIEMPRE este formato exacto y estas columnas separadas por el caracter |:\n"
    "BEGIN_FACTURA_OCR\n"
    "FUENTE|imagen_actual\n"
    "ENCABEZADO|campo|valor\n"
    "ITEM|linea|codigo|descripcion|cantidad|unidad|precio_unitario|subtotal|texto_original\n"
    "TOTALES|campo|valor\n"
    "NOTA|texto\n"
    "FIN_FACTURA_OCR\n"
    "Reglas estrictas: una linea ITEM por cada producto visible; no mezcles dos productos en una linea. "
    "Si un dato no se ve con claridad escribe ?. No inventes productos, no corrijas nombres, no cambies numeros. "
    "La columna cantidad debe contener solo la cantidad fisica visible; si no se ve, escribe ?. "
    "precio_unitario y subtotal son valores monetarios visibles; no los pongas en cantidad. "
    "texto_original debe copiar el renglon completo del producto tal como aparece en la imagen. "
    "Incluye siempre ENCABEZADO|Proveedor|nombre_de_la_empresa_que_emite_la_factura si se ve; si hay NIT del emisor usa ENCABEZADO|NIT|valor. "
    "Usa ENCABEZADO solo para datos como proveedor, factura, fecha o NIT; usa TOTALES solo para subtotal, IVA, total o descuentos. "
    "No agregues columnas nuevas ni cambies el orden de columnas. "
    f"MUY IMPORTANTE: al final de toda tu respuesta escribe en una nueva linea exactamente esta bandera y nada mas: "
    f"{MARCADOR_FIN_GEMINI}"
)

PROMPT_GEMINI_PREDETERMINADO = (
    "Actua como OCR estructurado y estricto. Analiza unicamente la imagen adjunta en ESTE mensaje. "
    "Ignora por completo cualquier imagen, texto o respuesta de mensajes anteriores. "
    "Devuelve unicamente texto plano, sin markdown, sin explicaciones y sin comillas. "
    "Tu respuesta debe usar registros en lineas separadas, pero este prompt se envia en una sola linea para no activar Enter. "
    "Formato exacto de registros: BEGIN_FACTURA_OCR ; FUENTE|imagen_actual ; ENCABEZADO|campo|valor ; ITEM|linea|codigo|descripcion|cantidad|unidad|precio_unitario|subtotal|texto_original ; TOTALES|campo|valor ; NOTA|texto ; FIN_FACTURA_OCR. "
    "Reglas estrictas: una linea ITEM por cada producto visible; no mezcles dos productos en una linea. "
    "Si un dato no se ve con claridad escribe ?. No inventes productos, no corrijas nombres, no cambies numeros. "
    "La columna cantidad debe contener solo la cantidad fisica visible; si no se ve, escribe ?. "
    "precio_unitario y subtotal son valores monetarios visibles; no los pongas en cantidad. "
    "texto_original debe copiar el renglon completo del producto tal como aparece en la imagen. "
    "Usa ENCABEZADO solo para datos como proveedor, factura, fecha o NIT; usa TOTALES solo para subtotal, IVA, total o descuentos. "
    "No agregues columnas nuevas ni cambies el orden de columnas. "
    f"MUY IMPORTANTE: al final de toda tu respuesta escribe en una nueva linea exactamente esta bandera y nada mas: {MARCADOR_FIN_GEMINI}"
)

EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
CANTIDAD_MAXIMA_CONFIABLE = int(os.getenv("INVENTARIO_FOTOS_CANTIDAD_MAXIMA", "500"))

PALABRAS_RUIDO_FACTURA = {
    "factura", "nit", "fecha", "subtotal", "total", "iva", "impuesto", "impuestos",
    "descuento", "descuentos", "retefuente", "reteica", "reteiva", "cambio",
    "efectivo", "tarjeta", "nequi", "daviplata", "banco", "pago", "pagado",
    "proveedor", "cliente", "direccion", "telefono", "email", "correo",
    "resolucion", "autorizacion", "vendedor", "cajero", "recibo",
}

# Ajustes de velocidad / estabilidad
POLL_FAST = 0.2
POLL_STREAM = 0.3
ESPERA_DIALOGO_ARCHIVO = 1.5
INTERVALO_PYAUTOGUI = 0.02

# Escritura visual ultrarrápida para Gemini
DELAY_CARACTER_GEMINI = 0.001
PAUSA_CADA_N_CARACTERES_GEMINI = 10
PAUSA_BLOQUE_GEMINI = 0.01

driver = None


def modo_navegador_background():
    return os.getenv("INVENTARIO_BROWSER_BACKGROUND", "").strip().lower() in ("1", "true", "yes", "si")


def modo_navegador_headless():
    return os.getenv("INVENTARIO_BROWSER_HEADLESS", "").strip().lower() in ("1", "true", "yes", "si")


def permitir_dialogos_windows():
    return not modo_navegador_background() and not modo_navegador_headless()


def aplicar_modo_background_driver(drv):
    if not modo_navegador_background() or modo_navegador_headless():
        return
    try:
        drv.set_window_rect(x=-32000, y=-32000, width=1440, height=1000)
    except Exception:
        pass
    try:
        drv.minimize_window()
    except Exception:
        pass


def crear_driver():
    chrome_options = Options()
    chrome_options.add_argument(f"--user-data-dir={ruta_user_data}")
    chrome_options.add_argument(f"--profile-directory={nombre_perfil}")
    if modo_navegador_headless():
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1440,1000")
        chrome_options.add_argument("--disable-gpu")
    elif modo_navegador_background():
        chrome_options.add_argument("--window-size=1440,1000")
        chrome_options.add_argument("--window-position=-32000,-32000")
        chrome_options.add_argument("--start-minimized")
    else:
        chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.page_load_strategy = "eager"

    drv = webdriver.Chrome(options=chrome_options)
    drv.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    aplicar_modo_background_driver(drv)
    return drv


def asegurar_driver():
    global driver
    if driver is None:
        driver = crear_driver()
    return driver


# ====================================================
# HELPERS DE ESPERA RÁPIDA
# ====================================================

def wait(timeout=10, poll=POLL_FAST):
    return WebDriverWait(driver, timeout, poll_frequency=poll)


def esperar_documento_listo(timeout=20):
    wait(timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


def esperar_hasta(condicion, timeout=10, poll=POLL_FAST, mensaje="Condición no cumplida"):
    fin = time.time() + timeout
    ultimo_error = None

    while time.time() < fin:
        try:
            resultado = condicion()
            if resultado:
                return resultado
        except Exception as e:
            ultimo_error = e
        time.sleep(poll)

    if ultimo_error:
        raise TimeoutException(f"{mensaje}: {ultimo_error}")
    raise TimeoutException(mensaje)


def esperar_dialogo_archivo_windows(timeout=5):
    if not permitir_dialogos_windows():
        return False

    fin = time.time() + timeout

    while time.time() < fin:
        try:
            titulo = ""
            if hasattr(pyautogui, "getActiveWindowTitle"):
                titulo = pyautogui.getActiveWindowTitle() or ""

            titulo_lower = titulo.lower()

            if (
                "abrir" in titulo_lower or
                "open" in titulo_lower or
                "seleccionar" in titulo_lower or
                "choose" in titulo_lower
            ):
                return True
        except Exception:
            pass

        time.sleep(0.15)

    return False


# ====================================================
# FUNCIONES COMUNES
# ====================================================

def limpiar_texto_para_prompt(texto):
    if not texto:
        return ""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def preparar_prompt_gemini_una_linea(texto):
    if not texto:
        return ""
    texto = texto.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def limpiar_texto_para_comparacion(texto):
    if not texto:
        return ""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{2,}", "\n", texto)
    return texto.strip()


def esperar_y_escribir(selector, texto, timeout=10):
    elem = wait(timeout).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
    )
    elem.click()
    try:
        elem.clear()
    except Exception:
        pass
    elem.send_keys(texto)
    return elem


def elemento_visible_y_habilitado(elem):
    try:
        return elem.is_displayed() and elem.is_enabled()
    except Exception:
        return False


def click_seguro(elem, descripcion="elemento"):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
    except Exception:
        pass
    time.sleep(0.2)

    try:
        elem.click()
        print(f" Click normal en {descripcion} ejecutado")
        return True
    except Exception:
        pass

    try:
        ActionChains(driver).move_to_element(elem).pause(0.1).click(elem).perform()
        print(f" Click con ActionChains en {descripcion} ejecutado")
        return True
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", elem)
        print(f" Click con JavaScript en {descripcion} ejecutado")
        return True
    except Exception:
        return False


def limpiar_campo_contenteditable(elem):
    try:
        driver.execute_script("""
            arguments[0].focus();
            arguments[0].innerHTML = '';
            arguments[0].textContent = '';
        """, elem)
    except Exception:
        try:
            elem.click()
            elem.send_keys(Keys.CONTROL, "a")
            elem.send_keys(Keys.BACKSPACE)
        except Exception:
            pass


def obtener_texto_contenteditable(elem):
    try:
        return driver.execute_script("""
            const el = arguments[0];
            return (el.innerText || el.textContent || '').trim();
        """, elem) or ""
    except Exception:
        try:
            return elem.text or ""
        except Exception:
            return ""


def texto_contenteditable_coincide(elem, texto):
    texto_actual = obtener_texto_contenteditable(elem)
    return limpiar_texto_para_comparacion(texto_actual) == limpiar_texto_para_comparacion(texto)


def setear_texto_contenteditable(elem, texto):
    try:
        driver.execute_script("""
            const el = arguments[0];
            const text = arguments[1];

            el.focus();

            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            selection.removeAllRanges();
            selection.addRange(range);

            document.execCommand('delete', false, null);
            const inserted = document.execCommand('insertText', false, text);

            if (!inserted) {
                el.textContent = text;
            }

            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                cancelable: true,
                inputType: 'insertText',
                data: text
            }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, elem, texto)
        time.sleep(0.25)
        return texto_contenteditable_coincide(elem, texto)
    except Exception:
        return False


def pegar_texto_contenteditable(elem, texto):
    try:
        elem.click()
        limpiar_campo_contenteditable(elem)
        time.sleep(0.1)

        if poner_texto_portapapeles_windows(texto):
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(0.5)
            return texto_contenteditable_coincide(elem, texto)
    except Exception:
        pass
    return False


def escribir_contenteditable_max_velocidad(elem, texto):
    try:
        elem.click()
        limpiar_campo_contenteditable(elem)

        if setear_texto_contenteditable(elem, texto):
            print(" Prompt puesto completo en Gemini con JavaScript")
            return True

        if pegar_texto_contenteditable(elem, texto):
            print(" Prompt pegado completo en Gemini desde portapapeles")
            return True

        elem.click()
        limpiar_campo_contenteditable(elem)
        elem.send_keys(texto)
        time.sleep(0.5)
        if texto_contenteditable_coincide(elem, texto):
            print(" Prompt enviado completo a Gemini con send_keys")
            return True

        return False

    except Exception:
        return False


# ====================================================
# FUNCIONES PARA GEMINI
# ====================================================

def ir_al_final_gemini():
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    except Exception:
        pass


def asegurar_gemini_lista_para_nuevo_turno(timeout=20):
    esperar_hasta(
        lambda: (
            len(driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']")) > 0
            or len(driver.find_elements(By.CSS_SELECTOR, "mat-icon[data-mat-icon-name='add_2']")) > 0
            or len(driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='Añadir'], button[aria-label*='Adjuntar'], button[aria-label*='Upload'], button[aria-label*='Add']")) > 0
            or len(driver.find_elements(By.XPATH, "//a[contains(., 'Sign in') or contains(., 'Acceder') or contains(., 'Iniciar sesión')]") ) > 0
        ),
        timeout=timeout,
        mensaje="Gemini no mostró ni chat ni pantalla de acceso"
    )
    ir_al_final_gemini()


def gemini_pide_login():
    current_url = (driver.current_url or "").lower()
    if "accounts.google.com" in current_url or "/signin" in current_url:
        return True

    señales = [
        "//a[contains(., 'Sign in')]",
        "//a[contains(., 'Acceder')]",
        "//a[contains(., 'Iniciar sesión')]",
        "//button[contains(., 'Sign in')]",
        "//button[contains(., 'Acceder')]",
        "//button[contains(., 'Iniciar sesión')]",
    ]
    for xp in señales:
        try:
            elems = driver.find_elements(By.XPATH, xp)
            if any(e.is_displayed() for e in elems):
                return True
        except Exception:
            pass
    return False


def guardar_debug_gemini(nombre='debug_gemini_inicio.png'):
    try:
        driver.save_screenshot(nombre)
        print(f" Screenshot guardado: {nombre}")
    except Exception as e:
        print(f" No pude guardar screenshot: {e}")

def abrir_gemini():
    driver.get("https://gemini.google.com/")
    aplicar_modo_background_driver(driver)
    print("Esperando que cargue Gemini...")
    esperar_documento_listo(timeout=25)
    time.sleep(3)

    print(f"URL actual GEMINI: {driver.current_url}")
    print(f"TITLE GEMINI: {driver.title}")

    guardar_debug_gemini()

    esperar_hasta(
        lambda: (
            len(driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']")) > 0
            or len(driver.find_elements(By.CSS_SELECTOR, "mat-icon[data-mat-icon-name='add_2']")) > 0
            or len(driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='Añadir'], button[aria-label*='Adjuntar'], button[aria-label*='Upload'], button[aria-label*='Add']")) > 0
            or gemini_pide_login()
            or len(driver.find_elements(By.CSS_SELECTOR, "main, body")) > 0
        ),
        timeout=25,
        mensaje="Gemini no terminó de cargar"
    )

    if gemini_pide_login():
        if modo_navegador_background() or modo_navegador_headless():
            raise RuntimeError(
                "Gemini requiere inicio de sesion manual. Desactiva INVENTARIO_BROWSER_BACKGROUND/HEADLESS, "
                "inicia sesion una vez en modo visible y vuelve a activar el modo segundo plano."
            )
        print(" Gemini requiere inicio de sesión manual.")
        print(" 1. En la ventana de Chrome inicia sesión en Google/Gemini.")
        print(" 2. Espera a que se vea el chat de Gemini.")
        input(" 3. Presiona ENTER aquí cuando Gemini ya esté listo... ")
        esperar_documento_listo(timeout=25)
        time.sleep(2)

    asegurar_gemini_lista_para_nuevo_turno(timeout=25)

def listar_opciones_menu():
    print("\n=== OPCIONES DEL MENÚ DESPLEGADO ===")
    try:
        selectores = [
            "button[role='menuitem']",
            "[role='menuitem']",
            ".menu-item",
            "[class*='menu-item']",
            "div[role='menu'] button",
            "div[class*='menu'] button",
        ]
        opciones = []
        for selector in selectores:
            opciones.extend(driver.find_elements(By.CSS_SELECTOR, selector))

        if not opciones:
            opciones = driver.find_elements(By.CSS_SELECTOR, "[role='menu'] button, .menu-container button")

        print(f"Se encontraron {len(opciones)} posibles opciones:")
        for i, opt in enumerate(opciones, 1):
            print(f"\n--- Opción {i} ---")
            print(f"Tag: {opt.tag_name}")
            print(f"Texto visible: '{opt.text}'")
            print(f"aria-label: '{opt.get_attribute('aria-label')}'")
            print(f"data-test-id: '{opt.get_attribute('data-test-id')}'")
            print(f"Clases: {opt.get_attribute('class')}")
            html = opt.get_attribute("outerHTML")
            print(f"HTML (resumido): {html[:200]}")
    except Exception as e:
        print(f"Error al listar opciones: {e}")


def localizar_boton_adjuntar_gemini(timeout=8):
    fin = time.time() + timeout
    ultimo_error = None

    while time.time() < fin:
        try:
            candidatos = []

            for icono in driver.find_elements(By.CSS_SELECTOR, "mat-icon[data-mat-icon-name='add_2']"):
                try:
                    candidatos.append(icono.find_element(By.XPATH, "ancestor::button[1]"))
                except Exception:
                    pass

            selectores = [
                "button[aria-label*='Añadir']",
                "button[aria-label*='Adjuntar']",
                "button[aria-label*='Subir']",
                "button[aria-label*='Cargar']",
                "button[aria-label*='Upload']",
                "button[aria-label*='Add']",
                "button[aria-label*='Attach']",
            ]
            for selector in selectores:
                candidatos.extend(driver.find_elements(By.CSS_SELECTOR, selector))

            try:
                boton_cercano = driver.execute_script("""
                    const editor = document.querySelector("[contenteditable='true']");
                    if (!editor) return null;
                    const er = editor.getBoundingClientRect();

                    function visible(el) {
                        const r = el.getBoundingClientRect();
                        const s = window.getComputedStyle(el);
                        return r.width > 0 &&
                               r.height > 0 &&
                               s.display !== 'none' &&
                               s.visibility !== 'hidden' &&
                               s.opacity !== '0';
                    }

                    const botones = Array.from(document.querySelectorAll('button')).filter(visible);
                    let mejor = null;
                    let mejorScore = -9999;

                    for (const btn of botones) {
                        const r = btn.getBoundingClientRect();
                        const cx = r.left + r.width / 2;
                        const cy = r.top + r.height / 2;
                        const txt = (btn.innerText || btn.textContent || '').trim();
                        const aria = (btn.getAttribute('aria-label') || '').trim();
                        const label = `${txt} ${aria}`.toLowerCase();

                        let score = 0;
                        if (txt === '+' || txt === '＋') score += 100;
                        if (/añadir|adjuntar|subir|cargar|upload|add|attach/.test(label)) score += 70;
                        if (btn.querySelector("mat-icon[data-mat-icon-name='add_2']")) score += 90;

                        const cercaX = cx >= er.left - 120 && cx <= er.left + 220;
                        const cercaY = cy >= er.top - 70 && cy <= er.bottom + 90;
                        if (cercaX && cercaY) score += 80;
                        else score -= 40;

                        if (score > mejorScore) {
                            mejorScore = score;
                            mejor = btn;
                        }
                    }

                    return mejorScore > 20 ? mejor : null;
                """)
                if boton_cercano:
                    candidatos.insert(0, boton_cercano)
            except Exception as e:
                ultimo_error = e

            for boton in candidatos:
                if elemento_visible_y_habilitado(boton):
                    print(" Boton de adjuntar localizado")
                    return boton
        except Exception as e:
            ultimo_error = e

        time.sleep(0.25)

    if ultimo_error:
        print(f" No se pudo localizar el boton de adjuntar: {ultimo_error}")
    return None


def hacer_primer_clic_add_2():
    try:
        asegurar_gemini_lista_para_nuevo_turno()
        print(" Buscando botón add_2...")

        ultimo_error = None
        for intento in range(1, 4):
            try:
                boton_adjuntar = localizar_boton_adjuntar_gemini(timeout=4)
                if boton_adjuntar and click_seguro(boton_adjuntar, "boton de adjuntar Gemini"):
                    time.sleep(1.0)
                    try:
                        esperar_hasta(
                            lambda: (
                                len(driver.find_elements(By.CSS_SELECTOR, "button[data-test-id='local-images-files-uploader-button']")) > 0
                                or len(driver.find_elements(By.CSS_SELECTOR, "input[type='file']")) > 0
                                or len(driver.find_elements(By.XPATH, "//button[contains(., 'Subir archivos') or contains(., 'Cargar archivos') or contains(., 'Upload files') or contains(., 'Subir') or contains(., 'Cargar') or contains(., 'Upload') ]")) > 0
                                or esperar_dialogo_archivo_windows(timeout=1.2)
                            ),
                            timeout=4,
                            poll=0.20,
                            mensaje="No aparecio una interfaz util de adjuntos tras pulsar el boton +"
                        )
                        print(" Interfaz de adjuntos detectada")
                    except Exception:
                        print(" No se detecto menu clasico tras el boton +, continuo con fallback")
                    return True

                icono_add_2 = wait(10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "mat-icon[data-mat-icon-name='add_2']"))
                )
                boton_add_2 = icono_add_2.find_element(By.XPATH, "ancestor::button[1]")
                print(" Botón add_2 encontrado")

                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton_add_2)
                time.sleep(0.4)

                clicked = False
                try:
                    boton_add_2.click()
                    print(" Clic normal en add_2 ejecutado")
                    clicked = True
                except Exception:
                    pass

                if not clicked:
                    try:
                        ActionChains(driver).move_to_element(boton_add_2).pause(0.1).click(boton_add_2).perform()
                        print(" Clic con ActionChains en add_2 ejecutado")
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    try:
                        driver.execute_script("arguments[0].click();", boton_add_2)
                        print(" Clic con JavaScript en add_2 ejecutado")
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    raise RuntimeError("No fue posible hacer clic en add_2")

                time.sleep(1.0)

                try:
                    esperar_hasta(
                        lambda: (
                            len(driver.find_elements(By.CSS_SELECTOR, "button[data-test-id='local-images-files-uploader-button']")) > 0
                            or len(driver.find_elements(By.CSS_SELECTOR, "input[type='file']")) > 0
                            or len(driver.find_elements(By.XPATH, "//button[contains(., 'Subir archivos') or contains(., 'Cargar archivos') or contains(., 'Upload files') or contains(., 'Subir') or contains(., 'Cargar') or contains(., 'Upload') ]")) > 0
                            or esperar_dialogo_archivo_windows(timeout=1.2)
                        ),
                        timeout=4,
                        poll=0.20,
                        mensaje="No apareció una interfaz útil de adjuntos tras pulsar add_2"
                    )
                    print(" Interfaz de adjuntos detectada")
                except Exception:
                    print(" No se detectó menú clásico tras add_2, continúo con fallback")

                return True
            except Exception as e:
                ultimo_error = e
                print(f" Intento {intento} fallido en add_2: {e}")
                time.sleep(0.8)

        print(f" Error en primer clic: {ultimo_error}")
        guardar_debug_gemini('debug_gemini_add2_error.png')
        return False
    except Exception as e:
        print(f" Error en primer clic: {e}")
        guardar_debug_gemini('debug_gemini_add2_error.png')
        return False

def hacer_segundo_clic_subir_archivos():
    try:
        print(" Buscando botón 'Subir archivos'...")

        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        for inp in file_inputs:
            try:
                if inp.is_displayed():
                    print(" Input file visible encontrado")
                    return True
            except Exception:
                pass

        selectores_css = [
            "button[data-test-id='local-images-files-uploader-button']",
        ]
        for selector in selectores_css:
            for elem in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                        time.sleep(0.2)
                        try:
                            elem.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", elem)
                        time.sleep(ESPERA_DIALOGO_ARCHIVO)
                        print(" Click en 'Subir archivos' ejecutado")
                        return True
                except Exception:
                    pass

        xpaths = [
            "//button[contains(., 'Subir archivos')]",
            "//button[contains(., 'Cargar archivos')]",
            "//button[contains(., 'Upload files')]",
            "//button[contains(., 'Upload')]",
            "//button[contains(., 'Subir')]",
            "//button[contains(., 'Cargar')]",
            "//*[contains(@aria-label, 'Subir archivos')]",
            "//*[contains(@aria-label, 'Cargar archivos')]",
            "//*[contains(@aria-label, 'Upload files')]",
            "//*[contains(@aria-label, 'Subir')]",
            "//*[contains(@aria-label, 'Cargar')]",
            "//*[contains(@aria-label, 'Upload')]",
        ]

        for xp in xpaths:
            for elem in driver.find_elements(By.XPATH, xp):
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                        time.sleep(0.2)
                        try:
                            elem.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", elem)
                        time.sleep(ESPERA_DIALOGO_ARCHIVO)
                        print(" Click en opción de subir archivos ejecutado")
                        return True
                except Exception:
                    pass

        if esperar_dialogo_archivo_windows(timeout=1.0):
            print(" El diálogo de Windows ya estaba abierto")
            return True

        print(" No se encontró botón de subir archivos")
        return False
    except Exception as e:
        print(f" Error en segundo clic: {e}")
        return False


def enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=6):
    fin = time.time() + timeout
    ultimo_error = None

    while time.time() < fin:
        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        for inp in file_inputs:
            try:
                driver.execute_script("""
                    const el = arguments[0];
                    el.removeAttribute('hidden');
                    el.removeAttribute('disabled');
                    el.style.display = 'block';
                    el.style.visibility = 'visible';
                    el.style.opacity = '1';
                    el.style.width = '1px';
                    el.style.height = '1px';
                    el.style.position = 'fixed';
                    el.style.left = '0';
                    el.style.top = '0';
                """, inp)
            except Exception:
                pass

            try:
                inp.send_keys(ruta_absoluta)
                print(" Archivo enviado directamente al input file de Gemini")
                time.sleep(1.5)
                return True
            except Exception as e:
                ultimo_error = e

        time.sleep(0.25)

    if ultimo_error:
        print(f" No se pudo enviar archivo por input file: {ultimo_error}")
    return False


def confirmar_archivo_adjuntado_gemini(timeout=14):
    selectores = (
        "img[src*='blob'], "
        "div[class*='attachment'], "
        "div[class*='image-preview'], "
        "div[class*='file-preview'], "
        "mat-chip, "
        "[data-test-id*='attachment'], "
        "[data-test-id*='file'], "
        "[aria-label*='Imagen'], "
        "[aria-label*='image'], "
        "[aria-label*='archivo'], "
        "[aria-label*='file']"
    )

    esperar_hasta(
        lambda: driver.find_elements(By.CSS_SELECTOR, selectores),
        timeout=timeout,
        poll=0.20,
        mensaje="No aparecio la vista previa del archivo"
    )
    print(" Archivo adjuntado")
    return True


def poner_texto_portapapeles_windows(texto):
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(texto)
        root.update()
        root.destroy()
        return True
    except Exception:
        pass

    try:
        import pyperclip
        pyperclip.copy(texto)
        return True
    except Exception:
        return False


def enviar_archivo_por_dialogo_windows(ruta_absoluta, timeout_dialogo=4, requiere_dialogo=True):
    if not permitir_dialogos_windows():
        return False

    if not os.path.exists(ruta_absoluta):
        print(f" La imagen no existe antes de usar el dialogo: {ruta_absoluta}")
        return False

    if requiere_dialogo and not esperar_dialogo_archivo_windows(timeout=timeout_dialogo):
        print(" No detecte el dialogo de Windows para seleccionar archivo")
        return False

    try:
        pyautogui.hotkey("alt", "n")
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)

        if poner_texto_portapapeles_windows(ruta_absoluta):
            pyautogui.hotkey("ctrl", "v")
            print(" Ruta pegada en el dialogo desde el portapapeles")
        else:
            pyautogui.press("backspace")
            pyautogui.write(ruta_absoluta, interval=INTERVALO_PYAUTOGUI)
            print(" Ruta escrita en el dialogo como fallback")

        time.sleep(0.2)
        pyautogui.press("enter")
        print(" Enter presionado en el dialogo de archivo")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f" Error usando el dialogo de Windows: {e}")
        return False


def adjuntar_archivo_gemini(ruta_archivo, es_imagen=False):
    try:
        print(f"\n Procesando en Gemini: {os.path.basename(ruta_archivo)}")
        ruta_absoluta = os.path.abspath(ruta_archivo)
        if not os.path.exists(ruta_absoluta):
            print(f" La imagen no existe antes de adjuntar: {ruta_absoluta}")
            return False

        if not permitir_dialogos_windows():
            if enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=4):
                print(" Esperando que Gemini procese el archivo...")
                return confirmar_archivo_adjuntado_gemini(timeout=14)

            if hacer_primer_clic_add_2() and enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=6):
                print(" Esperando que Gemini procese el archivo...")
                return confirmar_archivo_adjuntado_gemini(timeout=14)

            print(" No se pudo adjuntar la imagen en modo segundo plano sin dialogo de Windows")
            guardar_debug_gemini('debug_gemini_upload_error.png')
            return False

        if not hacer_primer_clic_add_2():
            return False

        if esperar_dialogo_archivo_windows(timeout=0.6):
            if enviar_archivo_por_dialogo_windows(ruta_absoluta, timeout_dialogo=1):
                print(" Esperando que Gemini procese el archivo...")
                return confirmar_archivo_adjuntado_gemini(timeout=14)

        if enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=5):
            print(" Esperando que Gemini procese el archivo...")
            return confirmar_archivo_adjuntado_gemini(timeout=14)

        dialogo_detectado = esperar_dialogo_archivo_windows(timeout=1.5)
        if not dialogo_detectado:
            print(" Intentando abrir selector de archivos...")
            segundo_ok = hacer_segundo_clic_subir_archivos()
            if segundo_ok:
                dialogo_detectado = esperar_dialogo_archivo_windows(timeout=3)
                if dialogo_detectado:
                    if enviar_archivo_por_dialogo_windows(ruta_absoluta, timeout_dialogo=1):
                        print(" Esperando que Gemini procese el archivo...")
                        return confirmar_archivo_adjuntado_gemini(timeout=14)
                    dialogo_detectado = False
                elif enviar_archivo_por_dialogo_windows(ruta_absoluta, timeout_dialogo=0, requiere_dialogo=False):
                    print(" Esperando que Gemini procese el archivo...")
                    return confirmar_archivo_adjuntado_gemini(timeout=14)
                elif enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=5):
                    print(" Esperando que Gemini procese el archivo...")
                    return confirmar_archivo_adjuntado_gemini(timeout=14)

        if not dialogo_detectado:
            try:
                if not enviar_archivo_por_input_file_gemini(ruta_absoluta, timeout=5):
                    print(" No se pudo abrir el selector ni encontrar input file")
                    guardar_debug_gemini('debug_gemini_upload_error.png')
                    return False
            except Exception as e:
                print(f" Falló el envío directo al input file: {e}")
                guardar_debug_gemini('debug_gemini_upload_error.png')
                return False
        else:
            if not enviar_archivo_por_dialogo_windows(ruta_absoluta, timeout_dialogo=1):
                guardar_debug_gemini('debug_gemini_upload_error.png')
                return False

        print(" Esperando que Gemini procese el archivo...")
        try:
            return confirmar_archivo_adjuntado_gemini(timeout=14)
            esperar_hasta(
                lambda: driver.find_elements(
                    By.CSS_SELECTOR,
                    "img[src*='blob'], div[class*='attachment'], div[class*='image-preview'], div[class*='file-preview']"
                ),
                timeout=10,
                poll=0.20,
                mensaje="No apareció la vista previa del archivo"
            )
            print(" Archivo adjuntado")
            return True
        except Exception:
            print(" No pude confirmar que Gemini adjuntara la imagen; detengo este intento")
            guardar_debug_gemini('debug_gemini_upload_error.png')
            return False
            if es_imagen:
                print(" No pude confirmar visualmente la vista previa, pero continuaré")
                return True
            print(" Archivo asumido como adjuntado")
            return True
    except Exception as e:
        print(f" Error adjuntando en Gemini: {e}")
        guardar_debug_gemini('debug_gemini_upload_error.png')
        return False

def enviar_prompt_gemini(texto_prompt):
    try:
        texto_prompt = preparar_prompt_gemini_una_linea(texto_prompt)
        asegurar_gemini_lista_para_nuevo_turno()

        campo_texto = wait(15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[contenteditable='true']"))
        )
        campo_texto.click()
        limpiar_campo_contenteditable(campo_texto)

        ok = escribir_contenteditable_max_velocidad(campo_texto, texto_prompt)

        if not ok:
            print(" Escritura rápida no confirmada. Reintentando a velocidad segura...")
            campo_texto.click()
            limpiar_campo_contenteditable(campo_texto)

            if not pegar_texto_contenteditable(campo_texto, texto_prompt):
                campo_texto.click()
                limpiar_campo_contenteditable(campo_texto)
                campo_texto.send_keys(texto_prompt)
                time.sleep(0.5)

        campo_texto.send_keys(Keys.RETURN)
        print(" Prompt enviado a Gemini")
        return True

    except Exception as e:
        print(f" Error enviando prompt a Gemini: {e}")
        return False


def obtener_parrafos_visibles_gemini():
    try:
        parrafos = driver.execute_script("""
            const root = document.querySelector('main') || document.body;

            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.display !== 'none' &&
                       s.visibility !== 'hidden' &&
                       s.opacity !== '0';
            }

            function limpiar(txt) {
                return (txt || '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();
            }

            const selectors = [
                'div.model-response-text',
                'div.message-content',
                'div.response-text',
                'div[class*="response"]',
                'div[class*="answer"]',
                'div[class*="markdown"] p',
                'div[class*="markdown"] li',
                'div[class*="prose"] p',
                'div[class*="prose"] li',
                'pre',
                'li',
                'p',
                'div'
            ];

            const salida = [];
            const vistos = new Set();

            for (const sel of selectors) {
                const elementos = root.querySelectorAll(sel);
                for (const el of elementos) {
                    if (!visible(el)) continue;
                    if (el.closest('aside, nav, form, footer, header')) continue;
                    if (el.getAttribute('contenteditable') === 'true') continue;

                    const txt = limpiar(el.innerText || el.textContent || '');
                    if (!txt) continue;

                    if (txt.includes('Gemini puede cometer errores')) continue;
                    if (txt.includes('Google puede revisar')) continue;
                    if (txt.includes('Subir archivos')) continue;
                    if (txt.includes('Analiza únicamente la imagen adjunta en ESTE mensaje')) continue;
                    if (txt.includes('Ignora por completo cualquier imagen, texto o respuesta de mensajes anteriores')) continue;
                    if (txt.includes('Devuelve únicamente el texto plano extraído de la imagen')) continue;
                    if (txt.includes('MUY IMPORTANTE: al final de toda tu respuesta')) continue;

                    if (vistos.has(txt)) continue;
                    vistos.add(txt);
                    salida.push(txt);
                }
            }

            return salida;
        """)
        return parrafos or []
    except Exception:
        return []


def obtener_texto_visible_completo_gemini():
    try:
        parrafos = obtener_parrafos_visibles_gemini()
        return "\n".join(parrafos).strip()
    except Exception:
        return ""


def obtener_snapshot_respuesta_gemini():
    parrafos = obtener_parrafos_visibles_gemini()
    return {
        "parrafos": parrafos,
        "count": len(parrafos)
    }


def extraer_parrafos_nuevos_gemini(snapshot_antes, snapshot_actual):
    antes = snapshot_antes.get("parrafos", [])[:]
    actual = snapshot_actual.get("parrafos", [])[:]

    if not actual:
        return []

    i = 0
    limite = min(len(antes), len(actual))

    while i < limite:
        a = limpiar_texto_para_comparacion(antes[i])
        b = limpiar_texto_para_comparacion(actual[i])
        if a == b:
            i += 1
        else:
            break

    return actual[i:]


def obtener_texto_nuevo_desde_snapshot_gemini(snapshot_antes):
    snapshot_actual = obtener_snapshot_respuesta_gemini()
    nuevos = extraer_parrafos_nuevos_gemini(snapshot_antes, snapshot_actual)
    texto_nuevo = "\n".join(nuevos).strip()
    texto_nuevo = limpiar_texto_para_comparacion(texto_nuevo)
    return snapshot_actual, texto_nuevo


def extraer_respuesta_gemini_hasta_bandera(texto):
    if not texto:
        return ""

    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    idx = texto.lower().find(MARCADOR_FIN_GEMINI.lower())

    if idx == -1:
        return ""

    contenido = texto[:idx].strip()

    lineas = []
    for linea in contenido.split("\n"):
        l = linea.strip()
        if not l:
            continue
        if "Gemini puede cometer errores" in l:
            continue
        if "Google puede revisar" in l:
            continue
        if l == MARCADOR_FIN_GEMINI:
            continue
        lineas.append(l)

    return "\n".join(lineas).strip()


def obtener_respuesta_gemini(snapshot_antes, timeout=180, segundos_estable=1.5):
    print(" Esperando respuesta de Gemini hasta detectar la bandera final...")

    inicio = time.time()
    ultimo_texto_nuevo = ""
    ultimo_cambio = time.time()
    aviso_timeout_mostrado = False

    while time.time() - inicio < timeout:
        time.sleep(POLL_STREAM)

        try:
            _, texto_nuevo = obtener_texto_nuevo_desde_snapshot_gemini(snapshot_antes)
        except Exception:
            texto_nuevo = ""

        if texto_nuevo != ultimo_texto_nuevo:
            ultimo_texto_nuevo = texto_nuevo
            ultimo_cambio = time.time()

        if MARCADOR_FIN_GEMINI.lower() in texto_nuevo.lower():
            if (time.time() - ultimo_cambio) >= segundos_estable:
                respuesta = extraer_respuesta_gemini_hasta_bandera(texto_nuevo)
                if respuesta:
                    print("\n Bandera final detectada en Gemini")
                    return respuesta

        print(".", end="", flush=True)

        if (not aviso_timeout_mostrado) and (time.time() - inicio > 90):
            print("\n Gemini sigue generando, esperando específicamente la bandera final...")
            aviso_timeout_mostrado = True

    print("\n Timeout esperando la bandera de Gemini. Intentando rescate final...")

    try:
        texto_visible = obtener_texto_visible_completo_gemini()
        if MARCADOR_FIN_GEMINI.lower() in texto_visible.lower():
            respuesta = extraer_respuesta_gemini_hasta_bandera(texto_visible)
            if respuesta:
                print(" Respuesta recuperada en el último intento")
                return respuesta
    except Exception:
        pass

    return " No se pudo capturar la respuesta completa de Gemini."


def verificar_interfaz_gemini():
    print("\n=== VERIFICACIÓN DE INTERFAZ GEMINI ===")
    try:
        add_2 = driver.find_elements(By.CSS_SELECTOR, "mat-icon[data-mat-icon-name='add_2']")
        print(f"Iconos add_2 encontrados: {len(add_2)}")
        if add_2:
            print(" Primer botón (add_2) disponible")
    except Exception:
        print(" No se encontró add_2")

    try:
        edits = driver.find_elements(By.CSS_SELECTOR, "[contenteditable='true']")
        print(f"Campos contenteditable encontrados: {len(edits)}")
    except Exception:
        print(" No se pudieron medir contenteditable")

    try:
        subir = driver.find_elements(By.CSS_SELECTOR, "button[data-test-id='local-images-files-uploader-button']")
        print(f"Botones con data-test-id en DOM: {len(subir)}")
    except Exception:
        print(" No se encontró el botón por data-test-id")

    try:
        upload_text = driver.find_elements(By.XPATH, "//button[contains(., 'Subir') or contains(., 'Upload')]")
        print(f"Botones por texto relacionados con subir: {len(upload_text)}")
    except Exception:
        print(" No se encontraron botones por texto")

    try:
        signins = driver.find_elements(By.XPATH, "//a[contains(., 'Sign in') or contains(., 'Acceder') or contains(., 'Iniciar sesión')]")
        print(f"Enlaces de login visibles: {len(signins)}")
    except Exception:
        print(" No se pudieron medir enlaces de login")

def procesar_todas_las_imagenes_con_gemini(carpeta_actual):
    imagenes = sorted([
        f for f in os.listdir(carpeta_actual)
        if f.lower().endswith(EXTENSIONES_IMAGEN)
    ])

    if not imagenes:
        print(" No hay imágenes en la carpeta")
        return ""

    print(f"\n Imágenes encontradas: {len(imagenes)}")
    for i, img in enumerate(imagenes, 1):
        print(f"  {i}. {img}")

    respuestas = []

    print("\n Abriendo Gemini una sola vez para reutilizar la misma conversación...")
    abrir_gemini()
    verificar_interfaz_gemini()

    for i, nombre_img in enumerate(imagenes, 1):
        ruta_img = os.path.join(carpeta_actual, nombre_img)

        print("\n" + "=" * 60)
        print(f" PROCESANDO IMAGEN {i}/{len(imagenes)}: {nombre_img}")
        print("=" * 60)

        asegurar_gemini_lista_para_nuevo_turno()

        if not adjuntar_archivo_gemini(ruta_img, es_imagen=True):
            print(f" No se pudo adjuntar la imagen: {nombre_img}")
            continue

        print("\n Imagen adjuntada correctamente en Gemini")

        snapshot_antes = obtener_snapshot_respuesta_gemini()

        if not enviar_prompt_gemini(PROMPT_GEMINI_PREDETERMINADO):
            print(f" Error al enviar prompt en Gemini para: {nombre_img}")
            continue

        respuesta_gemini = obtener_respuesta_gemini(
            snapshot_antes=snapshot_antes,
            timeout=180,
            segundos_estable=1.5
        )
        respuesta_gemini = respuesta_gemini.replace(MARCADOR_FIN_GEMINI, "").strip()
        respuesta_gemini = limpiar_texto_para_prompt(respuesta_gemini)

        print("\n" + "=" * 60)
        print(f" RESPUESTA DE GEMINI - {nombre_img}:")
        print("=" * 60)
        print(respuesta_gemini)
        print("=" * 60)

        bloque = (
            f"=== INICIO OCR: {nombre_img} ===\n"
            f"{respuesta_gemini}\n"
            f"=== FIN OCR: {nombre_img} ==="
        )
        respuestas.append(bloque)

    if not respuestas:
        return ""

    texto_consolidado = "\n\n".join(respuestas).strip()

    print("\n" + "=" * 60)
    print(" OCR CONSOLIDADO DE TODAS LAS IMÁGENES")
    print("=" * 60)
    print(texto_consolidado[:3000] + ("..." if len(texto_consolidado) > 3000 else ""))
    print("=" * 60)

    return texto_consolidado


# ====================================================
# DEEPSEEK: ENVÍO Y CONFIGURACIÓN
# ====================================================

def abrir_deepseek():
    driver.get("https://chat.deepseek.com/")
    aplicar_modo_background_driver(driver)
    print("Esperando que cargue DeepSeek...")
    esperar_documento_listo(timeout=20)
    esperar_hasta(
        lambda: driver.find_elements(By.CSS_SELECTOR, "textarea, main, body"),
        timeout=20,
        mensaje="DeepSeek no terminó de cargar"
    )


def verificar_login_deepseek():
    current_url = driver.current_url.lower()
    if "auth" in current_url or "login" in current_url:
        if modo_navegador_background() or modo_navegador_headless():
            raise RuntimeError(
                "DeepSeek requiere inicio de sesion manual. Desactiva INVENTARIO_BROWSER_BACKGROUND/HEADLESS, "
                "inicia sesion una vez en modo visible y vuelve a activar el modo segundo plano."
            )
        print("\n Necesitas iniciar sesión manualmente en DeepSeek.")
        print("1. En la ventana, inicia sesión con tu cuenta.")
        print("2. Espera a que cargue el chat.")
        input("3. Presiona ENTER cuando hayas terminado...")
    else:
        print(" Sesión de DeepSeek activa (usando perfil guardado).")


def activar_pensamiento_profundo():
    print("\n Verificando estado de 'Pensamiento Profundo'...")
    try:
        botones = driver.find_elements(By.CSS_SELECTOR, ".ds-toggle-button")
        for boton in botones:
            texto = boton.text.strip()
            if "Pensamiento Profundo" in texto or "Deep Think" in texto:
                clases = boton.get_attribute("class")
                if "ds-toggle-button--selected" not in clases:
                    print(" Activando Pensamiento Profundo...")
                    boton.click()
                    wait(3).until(lambda d: "ds-toggle-button--selected" in (boton.get_attribute("class") or ""))
                    print(" Pensamiento Profundo activado.")
                else:
                    print(" Pensamiento Profundo ya estaba activado.")
                return
        print(" No se encontró el botón de Pensamiento Profundo.")
    except Exception as e:
        print(f" Error al activar Pensamiento Profundo: {e}")


def buscar_textarea_deepseek(timeout=20):
    return wait(timeout).until(
        lambda d: next(
            (ta for ta in d.find_elements(By.CSS_SELECTOR, "textarea") if ta.is_displayed() and ta.is_enabled()),
            False
        )
    )


def buscar_input_file_deepseek(timeout=15):
    try:
        return wait(timeout).until(
            lambda d: next(
                (inp for inp in d.find_elements(By.CSS_SELECTOR, "input[type='file']") if inp),
                False
            )
        )
    except Exception:
        return None


def intentar_abrir_selector_adjuntos_deepseek():
    posibles_xpaths = [
        "//button[contains(@aria-label, 'Attach')]",
        "//button[contains(@aria-label, 'attach')]",
        "//button[contains(@aria-label, 'Adjuntar')]",
        "//button[contains(., 'Attach')]",
        "//button[contains(., 'Adjuntar')]",
        "//button[contains(., 'Upload')]",
        "//button[contains(., 'Subir')]",
    ]

    for xp in posibles_xpaths:
        try:
            botones = driver.find_elements(By.XPATH, xp)
            for boton in botones:
                if boton.is_displayed() and boton.is_enabled():
                    driver.execute_script("arguments[0].click();", boton)
                    time.sleep(0.20)
                    return True
        except Exception:
            continue
    return False


def esperar_csv_cargado_deepseek(nombre_archivo, timeout=60):
    print(" Esperando que DeepSeek termine de cargar el CSV...")
    nombre_lower = nombre_archivo.lower()

    try:
        esperar_hasta(
            lambda: nombre_lower in driver.page_source.lower(),
            timeout=timeout,
            poll=0.25,
            mensaje="No pude confirmar visualmente el CSV"
        )
        print(" CSV visible en la interfaz de DeepSeek.")
        return True
    except Exception:
        print(" No pude confirmar visualmente el CSV, pero continuaré.")
        return True


def adjuntar_csv_deepseek(ruta_csv):
    try:
        print("\n Adjuntando CSV en DeepSeek...")
        ruta_absoluta = os.path.abspath(ruta_csv)
        nombre_archivo = os.path.basename(ruta_absoluta)

        file_input = buscar_input_file_deepseek(timeout=4)
        if not file_input:
            print(" No había input file visible. Intentando abrir adjuntos...")
            intentar_abrir_selector_adjuntos_deepseek()
            file_input = buscar_input_file_deepseek(timeout=5)

        if not file_input:
            print(" No se pudo encontrar input[type='file'] en DeepSeek.")
            return False

        file_input.send_keys(ruta_absoluta)
        print(f" CSV enviado al input: {nombre_archivo}")

        esperar_csv_cargado_deepseek(nombre_archivo, timeout=60)
        return True

    except Exception as e:
        print(f" Error adjuntando CSV en DeepSeek: {e}")
        return False


def setear_texto_en_textarea(textarea, texto):
    driver.execute_script("""
        const ta = arguments[0];
        const value = arguments[1];

        ta.focus();

        const proto = Object.getPrototypeOf(ta);
        const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');

        if (descriptor && descriptor.set) {
            descriptor.set.call(ta, value);
        } else {
            ta.value = value;
        }

        ta.dispatchEvent(new Event('input', { bubbles: true }));
        ta.dispatchEvent(new Event('change', { bubbles: true }));
    """, textarea, texto)


def pegar_texto_en_textarea(textarea, texto):
    try:
        textarea.click()
        time.sleep(0.2)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).perform()
        time.sleep(0.1)

        if poner_texto_portapapeles_windows(texto):
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(0.6)
            print(" Prompt pegado en DeepSeek desde portapapeles")
            return True
    except Exception as e:
        print(f" No se pudo pegar prompt por portapapeles: {e}")
    return False


def obtener_valor_textarea(textarea):
    try:
        return driver.execute_script("""
            const el = arguments[0];
            if (!el) return '';
            if ('value' in el) return el.value || '';
            return el.innerText || el.textContent || '';
        """, textarea) or ""
    except Exception:
        try:
            return textarea.text or ""
        except Exception:
            return ""


def buscar_boton_enviar_deepseek(textarea, timeout=8):
    fin = time.time() + timeout

    while time.time() < fin:
        try:
            boton = driver.execute_script("""
                const ta = arguments[0];

                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 &&
                           s.display !== 'none' &&
                           s.visibility !== 'hidden';
                }

                function enabled(el) {
                    return el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                }

                function matchStrong(el) {
                    const txt = (
                        (el.innerText || '') + ' ' +
                        (el.getAttribute('aria-label') || '') + ' ' +
                        (el.getAttribute('title') || '') + ' ' +
                        (el.getAttribute('data-testid') || '') + ' ' +
                        (el.className || '')
                    ).toLowerCase();

                    return txt.includes('send') || txt.includes('enviar') || txt.includes('submit') ||
                           txt.includes('arrow-up') || txt.includes('send-button');
                }

                function distScore(btn, taRect) {
                    const r = btn.getBoundingClientRect();
                    const dx = Math.abs(r.left - taRect.right);
                    const dy = Math.abs(r.top - taRect.bottom);

                    const rightSide = r.left >= taRect.left - 40;
                    const belowSide = r.top >= taRect.top - 40;

                    let score = dx + dy;

                    if (!rightSide && !belowSide) score += 2000;
                    if (r.top < taRect.top - 120) score += 1500;
                    if (r.left < taRect.left - 120) score += 1500;

                    return score;
                }

                const taRect = ta.getBoundingClientRect();

                let node = ta.parentElement;
                while (node) {
                    const candidates = node.querySelectorAll('button, [role="button"]');
                    let strong = [];
                    let allBtns = [];

                    for (const b of candidates) {
                        if (visible(b) && enabled(b)) {
                            allBtns.push(b);
                            if (matchStrong(b)) strong.push(b);
                        }
                    }

                    if (strong.length) {
                        strong.sort((a, b) => distScore(a, taRect) - distScore(b, taRect));
                        return strong[0];
                    }

                    if (allBtns.length > 0 && allBtns.length <= 8) {
                        allBtns.sort((a, b) => distScore(a, taRect) - distScore(b, taRect));
                        return allBtns[0];
                    }

                    node = node.parentElement;
                }

                const globalCandidates = document.querySelectorAll('button, [role="button"]');
                let strongGlobal = [];
                for (const b of globalCandidates) {
                    if (visible(b) && enabled(b) && matchStrong(b)) {
                        strongGlobal.push(b);
                    }
                }

                if (strongGlobal.length) {
                    strongGlobal.sort((a, b) => distScore(a, taRect) - distScore(b, taRect));
                    return strongGlobal[0];
                }

                return null;
            """, textarea)

            if boton:
                return boton
        except Exception:
            pass

        time.sleep(POLL_FAST)

    return None


def buscar_boton_enviar_deepseek_por_geometria(textarea, timeout=8):
    fin = time.time() + timeout

    while time.time() < fin:
        try:
            boton = driver.execute_script("""
                const ta = arguments[0];

                function visible(el) {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 &&
                           r.height > 0 &&
                           s.display !== 'none' &&
                           s.visibility !== 'hidden' &&
                           s.opacity !== '0';
                }

                function enabled(el) {
                    return el &&
                           !el.disabled &&
                           el.getAttribute('aria-disabled') !== 'true' &&
                           !String(el.className || '').toLowerCase().includes('disabled');
                }

                const taRect = ta.getBoundingClientRect();
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(b => visible(b) && enabled(b));

                let best = null;
                let bestScore = Infinity;

                for (const b of buttons) {
                    const r = b.getBoundingClientRect();
                    const cx = r.left + r.width / 2;
                    const cy = r.top + r.height / 2;
                    const nearComposer =
                        cx >= taRect.left - 80 &&
                        cx <= taRect.right + 140 &&
                        cy >= taRect.top - 80 &&
                        cy <= taRect.bottom + 140;
                    if (!nearComposer) continue;

                    const label = [
                        b.innerText || '',
                        b.textContent || '',
                        b.getAttribute('aria-label') || '',
                        b.getAttribute('title') || '',
                        b.getAttribute('data-testid') || '',
                        b.className || '',
                        b.innerHTML || ''
                    ].join(' ').toLowerCase();

                    let score = Math.abs(cx - taRect.right) + Math.abs(cy - taRect.bottom);
                    if (/send|enviar|submit|arrow|flecha|svg|path|icon/.test(label)) score -= 120;
                    if (cx < taRect.left + taRect.width * 0.45) score += 350;
                    if (cy < taRect.top - 20) score += 250;

                    if (score < bestScore) {
                        bestScore = score;
                        best = b;
                    }
                }

                return best;
            """, textarea)
            if boton:
                return boton
        except Exception:
            pass

        time.sleep(POLL_FAST)

    return None


def click_boton_deepseek(boton):
    try:
        driver.execute_script("""
            const el = arguments[0];
            el.scrollIntoView({block: 'center', inline: 'center'});
            for (const type of ['pointerdown', 'mousedown', 'mouseup', 'pointerup', 'click']) {
                el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            }
        """, boton)
        return True
    except Exception:
        try:
            boton.click()
            return True
        except Exception:
            return False


def confirmar_envio_deepseek(textarea, texto_original, timeout=8):
    fin = time.time() + timeout
    texto_original = (texto_original or "").strip()

    while time.time() < fin:
        try:
            actual = obtener_valor_textarea(textarea).strip()

            if actual == "":
                return True

            if actual != texto_original and len(actual) < max(20, len(texto_original) * 0.2):
                return True
        except Exception:
            return True

        time.sleep(POLL_FAST)

    return False


def debug_botones_deepseek():
    print("\n=== DEBUG BOTONES DEEPSEEK ===")
    try:
        try:
            textarea = buscar_textarea_deepseek(timeout=2)
            boton_geo = buscar_boton_enviar_deepseek_por_geometria(textarea, timeout=1)
            if boton_geo:
                print("Boton candidato por geometria:")
                print(f"texto='{boton_geo.text.strip()}' aria='{boton_geo.get_attribute('aria-label')}' class='{boton_geo.get_attribute('class')}'")
        except Exception:
            pass

        botones = driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
        print(f"Total botones encontrados: {len(botones)}")
        for i, b in enumerate(botones[:40], 1):
            try:
                texto = b.text.strip()
                aria = b.get_attribute("aria-label")
                title = b.get_attribute("title")
                cls = b.get_attribute("class")
                enabled = b.is_enabled()
                displayed = b.is_displayed()
                print(
                    f"{i}. texto='{texto}' aria='{aria}' title='{title}' "
                    f"enabled={enabled} displayed={displayed} class='{cls}'"
                )
            except Exception:
                pass
    except Exception as e:
        print("Error debug botones:", e)


def enviar_form_o_boton_desde_textarea(textarea):
    texto_antes = obtener_valor_textarea(textarea).strip()

    try:
        boton = buscar_boton_enviar_deepseek_por_geometria(textarea, timeout=4)
        if boton:
            if click_boton_deepseek(boton) and confirmar_envio_deepseek(textarea, texto_antes, timeout=6):
                return True
    except Exception:
        pass

    try:
        enviado = driver.execute_script("""
            const ta = arguments[0];
            const form = ta.closest('form');
            if (form && typeof form.requestSubmit === 'function') {
                form.requestSubmit();
                return true;
            }
            return false;
        """, textarea)

        if enviado and confirmar_envio_deepseek(textarea, texto_antes, timeout=5):
            return True
    except Exception:
        pass

    try:
        boton = buscar_boton_enviar_deepseek(textarea, timeout=3)
        if boton:
            click_boton_deepseek(boton)
            if confirmar_envio_deepseek(textarea, texto_antes, timeout=5):
                return True
    except Exception:
        pass

    try:
        boton = buscar_boton_enviar_deepseek_por_geometria(textarea, timeout=2)
        if boton:
            textarea.click()
            ActionChains(driver).move_to_element(boton).pause(0.1).click(boton).perform()
            if confirmar_envio_deepseek(textarea, texto_antes, timeout=5):
                return True
    except Exception:
        pass

    try:
        textarea.click()
        textarea.send_keys(Keys.ENTER)
        if confirmar_envio_deepseek(textarea, texto_antes, timeout=5):
            return True
    except Exception:
        pass

    try:
        textarea.click()
        ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.ENTER).key_up(Keys.CONTROL).perform()
        if confirmar_envio_deepseek(textarea, texto_antes, timeout=5):
            return True
    except Exception:
        pass

    return False


def enviar_prompt_deepseek_unico(texto_prompt):
    try:
        texto_prompt = limpiar_texto_para_prompt(texto_prompt)

        textarea = buscar_textarea_deepseek(timeout=20)
        if not pegar_texto_en_textarea(textarea, texto_prompt):
            setear_texto_en_textarea(textarea, texto_prompt)

        try:
            wait(4).until(lambda d: obtener_valor_textarea(textarea).strip() == texto_prompt.strip())
        except Exception:
            setear_texto_en_textarea(textarea, texto_prompt)

        valor_actual = obtener_valor_textarea(textarea).strip()
        if valor_actual != texto_prompt.strip():
            raise RuntimeError("El texto no quedó completo en el textarea.")

        enviado = enviar_form_o_boton_desde_textarea(textarea)
        if not enviado:
            print(" No se pudo confirmar el envío automático. Ejecutando debug de botones...")
            debug_botones_deepseek()
            raise RuntimeError("No se pudo enviar mediante botón, form, Enter ni Ctrl+Enter.")

        print(" Prompt único enviado a DeepSeek.")
        return True

    except Exception as e:
        print(f" Error enviando prompt único a DeepSeek: {e}")
        return False


# ====================================================
# DEEPSEEK: OCULTAR PENSAMIENTO EN UI
# ====================================================

def intentar_ocultar_pensamiento_ui_deepseek():
    xpaths = [
        "//button[contains(., 'Ocultar pensamiento')]",
        "//button[contains(., 'Ocultar razonamiento')]",
        "//button[contains(., 'Hide thinking')]",
        "//button[contains(., 'Hide reasoning')]",
        "//button[contains(@aria-label, 'Ocultar pensamiento')]",
        "//button[contains(@aria-label, 'Hide thinking')]",
    ]

    for xp in xpaths:
        try:
            botones = driver.find_elements(By.XPATH, xp)
            for boton in botones:
                if boton.is_displayed() and boton.is_enabled():
                    try:
                        driver.execute_script("arguments[0].click();", boton)
                        print(" Botón para ocultar pensamiento activado.")
                        return True
                    except Exception:
                        pass
        except Exception:
            pass

    return False


def instalar_filtro_visual_pensamiento_deepseek():
    try:
        driver.execute_script("""
            if (!document.getElementById('oai-hide-reasoning-style')) {
                const style = document.createElement('style');
                style.id = 'oai-hide-reasoning-style';
                style.textContent = `
                    [data-oai-hide-reasoning="1"],
                    .oai-reasoning-hidden {
                        display: none !important;
                        visibility: hidden !important;
                        max-height: 0 !important;
                        overflow: hidden !important;
                        opacity: 0 !important;
                        pointer-events: none !important;
                    }
                `;
                document.head.appendChild(style);
            }
        """)
        return True
    except Exception as e:
        print(f" No se pudo instalar el filtro visual: {e}")
        return False


def preparar_interfaz_respuesta_deepseek():
    intentar_ocultar_pensamiento_ui_deepseek()
    instalar_filtro_visual_pensamiento_deepseek()


# ====================================================
# EXTRACCIÓN DE SOLO LA RESPUESTA FINAL
# ====================================================

def normalizar_linea_resultado(linea):
    linea = linea.strip()
    linea = re.sub(r"^[>\-\*\u2022\s]+", "", linea).strip()
    linea = re.sub(r"\s+", " ", linea).strip()
    return linea


def extraer_bloque_marcado(texto):
    if not texto:
        return ""

    patron = re.compile(
        r"<<RESULTADO_FINAL>>\s*(.*?)\s*<<FIN_RESULTADO_FINAL>>",
        re.IGNORECASE | re.DOTALL
    )
    m = patron.search(texto)
    if m:
        return m.group(1).strip()
    return ""


def es_linea_resultado(linea):
    if not linea:
        return False

    linea = normalizar_linea_resultado(linea)

    if linea.upper() == "SIN COINCIDENCIAS":
        return True

    if "=" not in linea:
        return False

    if len(linea) < 5:
        return False

    patron = re.compile(
        r"^.+?\s=\s\d+(?:\s*(?:un|und|unds|unidad|unidades))?(?:\s*\|\s*(?:precio_unitario|precio_sin_iva|precio_unitario_sin_iva|iva|iva_porcentaje)\s*=\s*[\d.,?%-]+)*$",
        re.IGNORECASE
    )
    return bool(patron.match(linea))


def limpiar_cantidad_resultado(cantidad):
    cantidad = (cantidad or "").strip()
    cantidad = re.sub(r"(?i)\b(?:un|und|unds|unidad|unidades)\b", "", cantidad).strip()
    return cantidad


def extraer_lineas_resultado(texto):
    if not texto:
        return []

    texto = texto.replace("\r\n", "\n").replace("\r", "\n")

    bloque = extraer_bloque_marcado(texto)
    texto_objetivo = bloque if bloque else texto

    resultados = []
    vistos = set()

    for linea in texto_objetivo.split("\n"):
        linea_original = (linea or "").strip()
        if not linea_original:
            continue

        if linea_original.upper() == "SIN COINCIDENCIAS":
            if linea_original not in vistos:
                vistos.add(linea_original)
                resultados.append(linea_original)
            continue

        linea_limpia = re.sub(r"^[>\-*•\s]+", "", linea_original).strip()
        if "=" not in linea_limpia:
            continue

        nombre, resto = linea_limpia.split("=", 1)
        nombre = nombre.strip()
        precio_unitario = ""
        precio_sin_iva = ""
        iva_porcentaje = ""
        cantidad = resto
        if "|" in resto:
            cantidad, *extras = resto.split("|")
            for extra in extras:
                extra = extra.strip()
                if "=" not in extra:
                    continue
                key, value = extra.split("=", 1)
                key = key.strip().lower()
                if key in {"precio_unitario", "precio", "precio_con_iva", "precio_unitario_con_iva"}:
                    precio_unitario = normalizar_precio_unitario(value)
                elif key in {"precio_sin_iva", "precio_unitario_sin_iva"}:
                    precio_sin_iva = normalizar_precio_unitario(value)
                elif key in {"iva", "iva_porcentaje", "porcentaje_iva"}:
                    iva_porcentaje = normalizar_porcentaje_iva(value)
        cantidad = limpiar_cantidad_resultado(cantidad)
        cantidad_int = parsear_cantidad_entera(cantidad)

        if not nombre or not cantidad_int or es_nombre_ruido_factura(nombre):
            continue

        linea_final = f"{nombre} = {cantidad_int}"
        if precio_unitario:
            linea_final += f" | precio_unitario={precio_unitario}"
        if iva_porcentaje != "":
            linea_final += f" | iva={iva_porcentaje}"
        if precio_sin_iva:
            linea_final += f" | precio_sin_iva={precio_sin_iva}"
        if linea_final not in vistos:
            vistos.add(linea_final)
            resultados.append(linea_final)

    if resultados:
        return resultados

    lineas = [l.strip() for l in texto_objetivo.split("\n") if l.strip()]
    for linea in lineas:
        linea_limpia = normalizar_linea_resultado(linea)
        if es_linea_resultado(linea_limpia) and linea_limpia not in vistos:
            vistos.add(linea_limpia)
            resultados.append(linea_limpia)

    return resultados


def extraer_solo_respuesta_final(texto):
    if not texto:
        return ""

    texto = limpiar_texto_para_prompt(texto)

    bloque = extraer_bloque_marcado(texto)
    if bloque:
        return bloque.strip()

    lineas = extraer_lineas_resultado(texto)
    if lineas:
        return "\n".join(lineas).strip()

    if re.search(r"(?im)^\\s*SIN COINCIDENCIAS\\s*$", texto):
        return "SIN COINCIDENCIAS"

    return ""


# ====================================================
# CAPTURA DEL MENSAJE NUEVO EN DEEPSEEK
# ====================================================

def obtener_parrafos_visibles_deepseek():
    try:
        parrafos = driver.execute_script("""
            const root = document.querySelector('main') || document.body;

            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.display !== 'none' &&
                       s.visibility !== 'hidden' &&
                       s.opacity !== '0';
            }

            function limpiar(txt) {
                return (txt || '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();
            }

            const selectors = [
                'p.ds-markdown-paragraph',
                'div.ds-markdown-paragraph',
                '.ds-markdown p',
                '.ds-markdown li',
                '.ds-markdown pre',
                'pre',
                'li',
                'p'
            ];

            const salida = [];
            const vistos = new Set();

            for (const sel of selectors) {
                const elementos = root.querySelectorAll(sel);
                for (const el of elementos) {
                    if (!visible(el)) continue;
                    if (el.closest('aside, nav, form, footer, header')) continue;

                    const txt = limpiar(el.innerText || el.textContent || '');
                    if (!txt) continue;

                    if (txt.includes('Mensaje a DeepSeek')) continue;
                    if (txt.includes('Pensamiento Profundo')) continue;
                    if (txt.includes('Búsqueda inteligente')) continue;

                    const key = txt;
                    if (vistos.has(key)) continue;
                    vistos.add(key)

                    salida.push(txt);
                }
            }

            return salida;
        """)
        return parrafos or []
    except Exception:
        return []


def obtener_texto_visible_completo_deepseek():
    try:
        texto = driver.execute_script("""
            const root = document.querySelector('main') || document.body;

            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 &&
                       r.height > 0 &&
                       s.display !== 'none' &&
                       s.visibility !== 'hidden' &&
                       s.opacity !== '0';
            }

            function limpiar(txt) {
                return (txt || '')
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t]+/g, ' ')
                    .replace(/\\n{3,}/g, '\\n\\n')
                    .trim();
            }

            const salida = [];
            const vistos = new Set();

            const elementos = root.querySelectorAll('p, li, pre, div');

            for (const el of elementos) {
                if (!visible(el)) continue;
                if (el.closest('aside, nav, form, footer, header')) continue;

                const txt = limpiar(el.innerText || el.textContent || '');
                if (!txt) continue;

                if (txt.includes('Mensaje a DeepSeek')) continue;
                if (txt.includes('Pensamiento Profundo')) continue;
                if (txt.includes('Búsqueda inteligente')) continue;

                if (vistos.has(txt)) continue;
                vistos.add(txt)

                salida.push(txt);
            }

            return salida.join('\\n');
        """)
        return texto or ""
    except Exception:
        return ""


def obtener_snapshot_respuesta_deepseek():
    parrafos = obtener_parrafos_visibles_deepseek()
    return {
        "parrafos": parrafos,
        "count": len(parrafos)
    }


def extraer_parrafos_nuevos(snapshot_antes, snapshot_actual):
    antes = snapshot_antes.get("parrafos", [])[:]
    actual = snapshot_actual.get("parrafos", [])[:]

    if not actual:
        return []

    i = 0
    limite = min(len(antes), len(actual))

    while i < limite:
        a = limpiar_texto_para_comparacion(antes[i])
        b = limpiar_texto_para_comparacion(actual[i])
        if a == b:
            i += 1
        else:
            break

    return actual[i:]


def obtener_texto_nuevo_desde_snapshot(snapshot_antes):
    snapshot_actual = obtener_snapshot_respuesta_deepseek()
    nuevos = extraer_parrafos_nuevos(snapshot_antes, snapshot_actual)
    texto_nuevo = "\n".join(nuevos).strip()
    texto_nuevo = limpiar_texto_para_comparacion(texto_nuevo)
    return snapshot_actual, texto_nuevo


def stream_respuesta_deepseek(snapshot_antes, timeout_inicio=90, segundos_estable=2):
    print(" Esperando respuesta final de DeepSeek...", flush=True)

    inicio = time.time()
    ultimo_texto_nuevo = ""
    ultimo_cambio = time.time()
    aviso_razonando_mostrado = False

    while True:
        time.sleep(POLL_STREAM)

        try:
            _, texto_nuevo = obtener_texto_nuevo_desde_snapshot(snapshot_antes)
        except Exception:
            texto_nuevo = ""

        if texto_nuevo != ultimo_texto_nuevo:
            ultimo_texto_nuevo = texto_nuevo
            ultimo_cambio = time.time()

        if MARCADOR_FIN_RESULTADO.lower() in texto_nuevo.lower():
            if (time.time() - ultimo_cambio) >= segundos_estable:
                bloque = extraer_bloque_marcado(texto_nuevo)

                if not bloque:
                    bloque = extraer_solo_respuesta_final(texto_nuevo)

                if bloque:
                    os.system("cls" if os.name == "nt" else "clear")
                    print(" RESPUESTA FINAL DETECTADA DE DEEPSEEK:")
                    print("=" * 60)
                    print(bloque)
                    print("=" * 60)
                    return bloque

        if (not aviso_razonando_mostrado) and (time.time() - inicio > timeout_inicio):
            print(" DeepSeek sigue generando, esperando específicamente <<FIN_RESULTADO_FINAL>> ...")
            aviso_razonando_mostrado = True


# ====================================================
# FLUJO PRINCIPAL DE DEEPSEEK
# ====================================================

def resolver_ruta_csv(ruta_csv=None):
    if ruta_csv:
        ruta_csv = os.path.abspath(ruta_csv)
        if not os.path.exists(ruta_csv):
            raise FileNotFoundError(f"No existe el CSV indicado: {ruta_csv}")
        return ruta_csv

    carpeta_actual = os.path.dirname(os.path.abspath(__file__))
    csvs = sorted([f for f in os.listdir(carpeta_actual) if f.lower().endswith(".csv")])
    if not csvs:
        raise FileNotFoundError("No hay archivos CSV en la carpeta")
    return os.path.join(carpeta_actual, csvs[0])


def procesar_con_deepseek(texto_gemini, ruta_csv=None):
    print("\n" + "=" * 60)
    print(" ENVIANDO A DEEPSEEK")
    print("=" * 60)

    try:
        ruta_csv = resolver_ruta_csv(ruta_csv)
    except Exception as e:
        print(f" {e}")
        return ""

    nombre_csv = os.path.basename(ruta_csv)
    print(f" CSV seleccionado automáticamente: {nombre_csv}")

    abrir_deepseek()
    verificar_login_deepseek()

    if ACTIVAR_PENSAMIENTO_PROFUNDO:
        activar_pensamiento_profundo()
    else:
        print(" Pensamiento Profundo desactivado para priorizar salida limpia.")

    preparar_interfaz_respuesta_deepseek()

    if not adjuntar_csv_deepseek(ruta_csv):
        print(" No se pudo adjuntar el CSV. Continuando sin archivo...")

    texto_ocr = limpiar_texto_para_prompt(texto_gemini)

    prompt_deepseek = f"""
Usa el archivo CSV adjunto como catálogo maestro de productos.

TAREA:
1. Analiza el TEXTO OCR consolidado. El OCR viene estructurado en bloques BEGIN_FACTURA_OCR / FIN_FACTURA_OCR.
2. Usa principalmente las lineas ITEM con columnas: linea|codigo|descripcion|cantidad|unidad|precio_unitario|subtotal|texto_original.
3. Identifica los productos comparando codigo y descripcion contra el CSV.
4. Devuelve SOLO la salida final.

FORMATO FINAL OBLIGATORIO:
<<RESULTADO_FINAL>>
nombre exacto del producto en el CSV = cantidad | precio_unitario=precio_unitario_final_con_iva | iva=porcentaje_iva
nombre exacto del producto en el CSV = cantidad | precio_unitario=precio_unitario_final_con_iva | iva=porcentaje_iva
<<FIN_RESULTADO_FINAL>>

REGLAS:
- Tu respuesta completa debe contener únicamente el bloque entre <<RESULTADO_FINAL>> y <<FIN_RESULTADO_FINAL>>.
- No escribas explicaciones en la salida final.
- No escribas análisis en la salida final.
- No escribas comentarios en la salida final.
- No escribas texto adicional fuera de las marcas finales.
- Usa el nombre EXACTO del CSV en la respuesta.
- La cantidad debe ser unidades fisicas recibidas, no precio, no subtotal, no valor monetario.
- El precio_unitario de la salida debe ser el precio unitario FINAL con IVA incluido.
- La columna precio_unitario del ITEM suele venir visible sin IVA cuando texto_original muestra un porcentaje de IVA despues del precio; en ese caso calcula precio_unitario_final_con_iva = precio_unitario_visible * (1 + IVA/100).
- Si el subtotal coincide con precio_unitario_visible * cantidad, entonces el precio visible ya incluye IVA o el IVA es 0; no le sumes IVA otra vez.
- Si el subtotal coincide con precio_unitario_visible * cantidad * (1 + IVA/100), entonces el precio visible esta sin IVA y debes agregar ese IVA al precio_unitario de salida.
- No uses subtotal ni total como precio_unitario.
- Para cada ITEM, toma la cantidad desde la columna cantidad.
- Si la columna cantidad tiene ?, usa texto_original solo si la cantidad fisica esta claramente visible.
- Si precio_unitario no se ve con claridad, escribe precio_unitario=?.
- Si el porcentaje de IVA no se ve, escribe iva=?; si es 0, escribe iva=0.
- Si el codigo del ITEM coincide con codigo_de_barras del CSV, prioriza esa coincidencia.
- Si una linea tiene cantidad, precio unitario y subtotal, usa solo la cantidad.
- Si el mismo producto aparece varias veces, suma sus cantidades y devuelve una sola linea.
- Si el mismo producto aparece con varios precios unitarios visibles, devuelve el precio unitario no cero mas confiable; si hay dos precios no cero diferentes, separalos con /.
- Ignora lineas ENCABEZADO, TOTALES y NOTA para detectar productos.
- Ignora subtotal, total, IVA, impuestos, descuentos, medios de pago, NIT, factura, fecha, proveedor, direccion y telefono.
- Si no tienes certeza de la cantidad de un producto, no lo incluyas.
- No uses cantidades con separador de miles ni decimales; devuelve enteros positivos.
- Una línea por producto.
- Si el OCR proviene de varias imágenes, considera todo el texto como una sola entrada consolidada.

TEXTO OCR CONSOLIDADO:
{texto_ocr}
""".strip()

    print("\n Prompt a enviar a DeepSeek (primeros 300 caracteres):")
    print(prompt_deepseek[:300] + "...")

    snapshot_antes = obtener_snapshot_respuesta_deepseek()

    if enviar_prompt_deepseek_unico(prompt_deepseek):
        respuesta_deepseek = stream_respuesta_deepseek(
            snapshot_antes=snapshot_antes,
            timeout_inicio=90,
            segundos_estable=2
        )

        print("\n" + "=" * 60)
        print(" RESPUESTA FINAL FILTRADA DE DEEPSEEK:")
        print("=" * 60)
        print(respuesta_deepseek)
        print("=" * 60)
        return respuesta_deepseek or ""
    else:
        print(" Error al enviar prompt a DeepSeek")
        return ""



def detectar_delimitador_csv(ruta_csv):
    with open(ruta_csv, "r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except Exception:
        if sample.count(";") > sample.count(","):
            return ";"
        return ","


def detectar_columna_producto(fieldnames):
    if not fieldnames:
        return None

    exactos = [
        "producto",
        "nombre",
        "nombre_producto",
        "descripcion",
        "descripcion_producto",
        "articulo",
        "item",
        "referencia",
    ]

    normalizados = {}
    for f in fieldnames:
        if not f:
            continue
        clave = re.sub(r"\s+", "_", f.strip().lower())
        normalizados[clave] = f

    for cand in exactos:
        if cand in normalizados:
            return normalizados[cand]

    for f in fieldnames:
        if not f:
            continue
        fl = f.strip().lower()
        if any(k in fl for k in ["producto", "nombre", "descripcion", "articulo", "item", "referencia"]):
            return f

    # fallback: columna con strings más largos
    return fieldnames[0]


def detectar_columna_por_alias(fieldnames, exactos, parciales=None):
    if not fieldnames:
        return None

    parciales = parciales or []
    normalizados = {}
    for f in fieldnames:
        if not f:
            continue
        clave = re.sub(r"\s+", "_", f.strip().lower())
        normalizados[clave] = f

    for cand in exactos:
        if cand in normalizados:
            return normalizados[cand]

    for f in fieldnames:
        if not f:
            continue
        fl = f.strip().lower()
        if any(k in fl for k in parciales):
            return f

    return None


def detectar_columna_codigo_barras(fieldnames):
    return detectar_columna_por_alias(
        fieldnames,
        exactos=[
            "codigo_de_barras",
            "codigo_barras",
            "codigodebarras",
            "barcode",
            "ean",
            "ean13",
            "upc",
        ],
        parciales=["barras", "barcode", "ean", "upc"],
    )


def detectar_columna_productoid(fieldnames):
    return detectar_columna_por_alias(
        fieldnames,
        exactos=[
            "productoid",
            "producto_id",
            "id_producto",
            "id",
            "pk",
        ],
        parciales=["productoid", "producto_id", "id_producto"],
    )


def normalizar_nombre_catalogo(texto):
    texto = (texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace("&", " y ")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    texto = re.sub(r"\b(unidad|unidades|und|unds|un)\b", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def tokenizar_nombre(texto):
    return [t for t in normalizar_nombre_catalogo(texto).split() if t]


def es_nombre_ruido_factura(texto):
    tokens = set(tokenizar_nombre(texto))
    if not tokens:
        return True
    if tokens & PALABRAS_RUIDO_FACTURA:
        return True
    if len(tokens) <= 2 and any(t in PALABRAS_RUIDO_FACTURA for t in tokens):
        return True
    return False


def parsear_cantidad_entera(valor, maximo=CANTIDAD_MAXIMA_CONFIABLE):
    raw = str(valor or "").strip().lower()
    if not raw:
        return None

    raw = re.sub(r"\b(?:un|und|unds|unidad|unidades|unid|qty|cant|cantidad)\b", "", raw)
    raw = raw.strip(" :=xX\t")

    if "$" in raw or "€" in raw:
        return None

    if re.search(r"\d+[.,]\d{3}(?:\D|$)", raw):
        return None

    raw = raw.replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.0+)?", raw):
        return None

    try:
        cantidad = int(float(raw))
    except (TypeError, ValueError):
        return None

    if cantidad <= 0 or cantidad > maximo:
        return None
    return cantidad


def normalizar_codigo_barras(texto):
    return re.sub(r"[^A-Za-z0-9]", "", str(texto or "")).lower()


def normalizar_precio_unitario(valor):
    precio = str(valor or "").strip()
    if not precio or precio == "?":
        return ""
    if "/" in precio:
        partes = [normalizar_precio_unitario(p) for p in precio.split("/")]
        return " / ".join([p for p in partes if p])
    precio = precio.replace("$", "").replace("COP", "").strip()
    precio = re.sub(r"\s+", "", precio)
    precio = re.sub(r"[^0-9.,-]", "", precio)
    if not precio or precio in {"-", ".", ",", "-.", "-,"}:
        return ""
    return precio


def moneda_a_decimal(valor):
    precio = normalizar_precio_unitario(valor)
    if not precio:
        return None
    if "/" in precio:
        for parte in precio.split("/"):
            dec = moneda_a_decimal(parte)
            if dec is not None:
                return dec
        return None

    texto = precio.strip()
    negativo = texto.startswith("-")
    texto = texto.lstrip("-")
    if not texto:
        return None

    if "." in texto and "," in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        partes = texto.split(",")
        if len(partes[-1]) in {1, 2}:
            texto = "".join(partes[:-1]) + "." + partes[-1]
        else:
            texto = "".join(partes)
    elif "." in texto:
        partes = texto.split(".")
        if len(partes) == 2 and len(partes[-1]) in {1, 2}:
            texto = partes[0] + "." + partes[-1]
        else:
            texto = "".join(partes)

    try:
        dec = Decimal(texto)
    except (InvalidOperation, ValueError):
        return None
    return -dec if negativo else dec


def formatear_precio_decimal(valor):
    if valor is None:
        return ""
    try:
        entero = Decimal(valor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return ""
    return f"{int(entero):,}".replace(",", ".")


def normalizar_porcentaje_iva(valor):
    texto = str(valor or "").strip().replace("%", "")
    texto = texto.replace(",", ".")
    texto = re.sub(r"[^0-9.-]", "", texto)
    if not texto or texto in {"-", ".", "-."}:
        return ""
    try:
        porcentaje = Decimal(texto)
    except (InvalidOperation, ValueError):
        return ""
    if porcentaje < 0 or porcentaje > 30:
        return ""
    porcentaje = porcentaje.normalize()
    if porcentaje == porcentaje.to_integral():
        return str(int(porcentaje))
    return format(porcentaje, "f").rstrip("0").rstrip(".")


def porcentaje_iva_a_decimal(valor):
    normalizado = normalizar_porcentaje_iva(valor)
    if normalizado == "":
        return None
    try:
        return Decimal(normalizado)
    except (InvalidOperation, ValueError):
        return None


def cantidades_cercanas(esperado, observado, porcentaje_tolerancia=Decimal("0.03")):
    if esperado is None or observado is None:
        return False
    esperado = abs(Decimal(esperado))
    observado = abs(Decimal(observado))
    tolerancia = max(Decimal("5"), observado * porcentaje_tolerancia)
    return abs(esperado - observado) <= tolerancia


def tokenizar_numeros_factura(texto):
    tokens = []
    for match in re.finditer(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:\s*%)?", str(texto or "")):
        raw = match.group(0).strip()
        tokens.append({
            "raw": raw,
            "decimal": moneda_a_decimal(raw.replace("%", "")),
            "start": match.start(),
            "end": match.end(),
            "es_porcentaje": "%" in raw,
        })
    return tokens


def extraer_iva_porcentaje_desde_linea_item(texto_original, precio_unitario, cantidad, subtotal):
    precio_dec = moneda_a_decimal(precio_unitario)
    subtotal_dec = moneda_a_decimal(subtotal)
    cantidad_dec = Decimal(int(cantidad)) if cantidad else None
    tokens = tokenizar_numeros_factura(texto_original)
    candidatos = []

    if precio_dec is not None:
        for idx, token in enumerate(tokens):
            if token.get("decimal") != precio_dec:
                continue
            for posterior in tokens[idx + 1:idx + 6]:
                raw = str(posterior.get("raw") or "").strip()
                dec = posterior.get("decimal")
                if dec is None:
                    continue
                if posterior.get("es_porcentaje") or (dec == dec.to_integral() and 0 <= dec <= 30 and len(re.sub(r"\D", "", raw)) <= 2):
                    candidatos.append(dec)
                    break

    if candidatos:
        for candidato in candidatos:
            if candidato > 0:
                return normalizar_porcentaje_iva(candidato)
        return "0"

    if precio_dec is not None and subtotal_dec is not None and cantidad_dec:
        base_total = precio_dec * cantidad_dec
        if cantidades_cercanas(base_total, subtotal_dec):
            return "0"
        for candidato in (Decimal("19"), Decimal("5"), Decimal("8"), Decimal("16"), Decimal("10")):
            total_con_iva = base_total * (Decimal("1") + (candidato / Decimal("100")))
            if cantidades_cercanas(total_con_iva, subtotal_dec):
                return normalizar_porcentaje_iva(candidato)

    return ""


def calcular_precio_unitario_final_con_iva(precio_unitario, iva_porcentaje, cantidad=None, subtotal=None):
    precio_dec = moneda_a_decimal(precio_unitario)
    iva_dec = porcentaje_iva_a_decimal(iva_porcentaje)
    subtotal_dec = moneda_a_decimal(subtotal)
    cantidad_dec = Decimal(int(cantidad)) if cantidad else None
    if precio_dec is None:
        return "", False
    if iva_dec is None or iva_dec <= 0:
        return normalizar_precio_unitario(precio_unitario), False

    base_total = precio_dec * cantidad_dec if cantidad_dec else None
    total_con_iva = base_total * (Decimal("1") + (iva_dec / Decimal("100"))) if base_total is not None else None

    if subtotal_dec is not None and base_total is not None:
        if cantidades_cercanas(base_total, subtotal_dec, Decimal("0.015")):
            return normalizar_precio_unitario(precio_unitario), False
        if not cantidades_cercanas(total_con_iva, subtotal_dec, Decimal("0.04")):
            return normalizar_precio_unitario(precio_unitario), False

    precio_final = precio_dec * (Decimal("1") + (iva_dec / Decimal("100")))
    return formatear_precio_decimal(precio_final), True


def valor_a_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "si", "sí", "yes", "y"}


def agregar_metadatos_precio_a_row(row, item):
    if not isinstance(row, dict) or not isinstance(item, dict):
        return
    for key in (
        "precio_unitario_visible",
        "precio_unitario_sin_iva",
        "iva_porcentaje",
        "precio_incluye_iva",
        "precio_iva_calculado",
    ):
        value = item.get(key)
        if value in ("", None):
            continue
        if key in {"precio_iva_calculado", "precio_incluye_iva"}:
            if valor_a_bool(value) and not row.get(key):
                row[key] = True
        elif not row.get(key):
            row[key] = value


def clave_precio_unitario(precio):
    return re.sub(r"[^0-9]", "", str(precio or ""))


def precio_unitario_es_cero(precio):
    clave = clave_precio_unitario(precio)
    return bool(clave) and int(clave or "0") == 0


def agregar_precio_unitario_a_row(row, precio):
    precio = normalizar_precio_unitario(precio)
    if not precio:
        return

    precios = row.setdefault("precios_unitarios", [])
    claves = {clave_precio_unitario(p) for p in precios}
    clave = clave_precio_unitario(precio)
    if clave and clave not in claves:
        precios.append(precio)

    preferidos = [p for p in precios if not precio_unitario_es_cero(p)] or precios
    row["precio_unitario"] = " / ".join(preferidos)


def cargar_catalogo_productos(ruta_csv):
    delimiter = detectar_delimitador_csv(ruta_csv)
    with open(ruta_csv, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    col_producto = detectar_columna_producto(fieldnames)
    if not col_producto:
        return []
    col_codigo = detectar_columna_codigo_barras(fieldnames)
    col_productoid = detectar_columna_productoid(fieldnames)

    catalogo = []
    vistos = set()

    for row in rows:
        nombre = (row.get(col_producto) or "").strip()
        if not nombre:
            continue
        norm = normalizar_nombre_catalogo(nombre)
        if not norm or norm in vistos:
            continue
        codigo = (row.get(col_codigo) or "").strip() if col_codigo else ""
        productoid_raw = (row.get(col_productoid) or "").strip() if col_productoid else ""
        try:
            productoid = int(productoid_raw) if productoid_raw else None
        except (TypeError, ValueError):
            productoid = None

        vistos.add(norm)
        catalogo.append({
            "original": nombre,
            "norm": norm,
            "tokens": set(tokenizar_nombre(nombre)),
            "codigo_de_barras": codigo,
            "productoid": productoid,
        })

    return catalogo


def puntuar_match_producto(nombre_modelo, item_catalogo):
    norm_modelo = normalizar_nombre_catalogo(nombre_modelo)
    tokens_modelo_lista = tokenizar_nombre(nombre_modelo)
    tokens_modelo = set(tokens_modelo_lista)
    norm_catalogo = item_catalogo["norm"]
    tokens_catalogo = item_catalogo["tokens"]

    if not norm_modelo or not norm_catalogo:
        return 0.0

    if norm_modelo == norm_catalogo:
        return 1.0

    ratio = difflib.SequenceMatcher(None, norm_modelo, norm_catalogo).ratio()

    inter = len(tokens_modelo & tokens_catalogo)
    union = len(tokens_modelo | tokens_catalogo) or 1
    jaccard = inter / union

    contain_bonus = 0.0
    if norm_modelo in norm_catalogo or norm_catalogo in norm_modelo:
        contain_bonus = 0.12

    num_modelo = set(re.findall(r"\d+", norm_modelo))
    num_catalogo = set(re.findall(r"\d+", norm_catalogo))
    num_score = 0.0
    if num_modelo and num_catalogo:
        num_score = len(num_modelo & num_catalogo) / max(len(num_modelo | num_catalogo), 1)
    elif not num_modelo and not num_catalogo:
        num_score = 0.25

    first_bonus = 0.0
    if tokens_modelo_lista and tokens_catalogo:
        if tokens_modelo_lista[0] in tokens_catalogo:
            first_bonus = 0.05

    score = (ratio * 0.55) + (jaccard * 0.28) + (num_score * 0.12) + contain_bonus + first_bonus
    return min(score, 1.0)


def encontrar_mejor_producto_catalogo(nombre_modelo, catalogo):
    if not catalogo:
        return None, 0.0

    mejor = None
    mejor_score = -1.0

    for item in catalogo:
        score = puntuar_match_producto(nombre_modelo, item)
        if score > mejor_score:
            mejor = item
            mejor_score = score

    return mejor, mejor_score


def reconciliar_items_con_catalogo(items, ruta_csv, umbral=0.45):
    catalogo = cargar_catalogo_productos(ruta_csv)
    if not catalogo:
        return items, []
    catalogo_por_codigo = {
        normalizar_codigo_barras(item.get("codigo_de_barras")): item
        for item in catalogo
        if normalizar_codigo_barras(item.get("codigo_de_barras"))
    }

    acumulado = {}
    debug_matches = []

    for item in items:
        nombre = (item.get("producto") or "").strip()
        cantidad = int(item.get("cantidad") or 0)
        precio_unitario = item.get("precio_unitario") or item.get("precio") or ""
        metadatos_precio = {
            "precio_unitario_visible": item.get("precio_unitario_visible") or item.get("precio_visible") or "",
            "precio_unitario_sin_iva": item.get("precio_unitario_sin_iva") or "",
            "iva_porcentaje": item.get("iva_porcentaje") or item.get("iva") or "",
            "precio_incluye_iva": item.get("precio_incluye_iva"),
            "precio_iva_calculado": item.get("precio_iva_calculado"),
        }
        if not nombre or cantidad <= 0:
            continue

        codigo_item = normalizar_codigo_barras(item.get("codigo_de_barras") or item.get("codigo") or "")
        mejor = catalogo_por_codigo.get(codigo_item) if codigo_item else None
        score = 1.0 if mejor else 0.0

        if not mejor:
            mejor, score = encontrar_mejor_producto_catalogo(nombre, catalogo)

        if mejor and score >= umbral:
            nombre_final = mejor["original"]
            productoid = mejor.get("productoid")
            codigo = mejor.get("codigo_de_barras") or ""
            encontrado = bool(productoid or nombre_final)
        else:
            nombre_final = nombre
            productoid = None
            codigo = ""
            encontrado = False

        if productoid:
            clave = ("id", productoid)
        elif codigo:
            clave = ("codigo", codigo)
        else:
            clave = ("nombre", normalizar_nombre_catalogo(nombre_final) or nombre_final.lower())

        if clave not in acumulado:
            acumulado[clave] = {
                "producto": nombre_final,
                "original_producto": nombre,
                "cantidad": 0,
                "productoid": productoid,
                "codigo_de_barras": codigo,
                "encontrado": encontrado,
                "reemplazado_por_barcode": False,
                "score_match": round(score, 4),
                "precio_unitario": "",
                "precios_unitarios": [],
                "precio_unitario_visible": metadatos_precio.get("precio_unitario_visible") or "",
                "precio_unitario_sin_iva": metadatos_precio.get("precio_unitario_sin_iva") or "",
                "iva_porcentaje": metadatos_precio.get("iva_porcentaje") or "",
                "precio_incluye_iva": valor_a_bool(metadatos_precio.get("precio_incluye_iva")),
                "precio_iva_calculado": valor_a_bool(metadatos_precio.get("precio_iva_calculado")),
            }
        else:
            original_actual = acumulado[clave].get("original_producto") or ""
            if nombre and nombre not in original_actual.split(" / "):
                acumulado[clave]["original_producto"] = f"{original_actual} / {nombre}".strip(" /")

        acumulado[clave]["cantidad"] += cantidad
        agregar_precio_unitario_a_row(acumulado[clave], precio_unitario)
        agregar_metadatos_precio_a_row(acumulado[clave], metadatos_precio)
        debug_matches.append({
            "entrada_modelo": nombre,
            "producto_final": nombre_final,
            "productoid": productoid,
            "codigo_de_barras": codigo,
            "encontrado": encontrado,
            "precio_unitario": normalizar_precio_unitario(precio_unitario),
            "precio_unitario_visible": metadatos_precio.get("precio_unitario_visible") or "",
            "iva_porcentaje": metadatos_precio.get("iva_porcentaje") or "",
            "precio_iva_calculado": valor_a_bool(metadatos_precio.get("precio_iva_calculado")),
            "score": round(score, 4),
        })

    rows = list(acumulado.values())
    return rows, debug_matches


def construir_raw_text_desde_rows(rows):
    if not rows:
        return ""
    lineas = []
    for row in rows:
        linea = f'{row["producto"]} = {row["cantidad"]}'
        precio = normalizar_precio_unitario(row.get("precio_unitario"))
        if precio:
            linea += f" | precio_unitario={precio}"
        iva = normalizar_porcentaje_iva(row.get("iva_porcentaje"))
        if iva != "":
            linea += f" | iva={iva}"
        precio_sin_iva = normalizar_precio_unitario(row.get("precio_unitario_sin_iva"))
        if precio_sin_iva:
            linea += f" | precio_sin_iva={precio_sin_iva}"
        lineas.append(linea)
    return "<<RESULTADO_FINAL>>\n" + "\n".join(lineas) + "\n<<FIN_RESULTADO_FINAL>>"

def resultado_a_items(texto):
    items = []
    for linea in extraer_lineas_resultado(texto):
        if "=" not in linea:
            continue
        nombre, resto = linea.split("=", 1)
        nombre = nombre.strip()
        precio_unitario = ""
        precio_sin_iva = ""
        iva_porcentaje = ""
        cantidad = resto
        if "|" in resto:
            cantidad, *extras = resto.split("|")
            for extra in extras:
                extra = extra.strip()
                if "=" not in extra:
                    continue
                key, value = extra.split("=", 1)
                key = key.strip().lower()
                if key in {"precio_unitario", "precio", "precio_con_iva", "precio_unitario_con_iva"}:
                    precio_unitario = normalizar_precio_unitario(value)
                elif key in {"precio_sin_iva", "precio_unitario_sin_iva"}:
                    precio_sin_iva = normalizar_precio_unitario(value)
                elif key in {"iva", "iva_porcentaje", "porcentaje_iva"}:
                    iva_porcentaje = normalizar_porcentaje_iva(value)
        cantidad_int = parsear_cantidad_entera(cantidad)
        if nombre and cantidad_int and not es_nombre_ruido_factura(nombre):
            item = {"producto": nombre, "cantidad": cantidad_int}
            if precio_unitario:
                item["precio_unitario"] = precio_unitario
            if precio_sin_iva:
                item["precio_unitario_sin_iva"] = precio_sin_iva
            if iva_porcentaje != "":
                item["iva_porcentaje"] = iva_porcentaje
                item["precio_iva_calculado"] = bool(precio_sin_iva and precio_unitario and precio_sin_iva != precio_unitario)
                item["precio_incluye_iva"] = not item["precio_iva_calculado"]
            items.append(item)
    return items


def limpiar_linea_ocr(linea):
    linea = (linea or "").strip()
    linea = re.sub(r"^=+\s*(?:INICIO|FIN)\s+OCR:.*?=+$", "", linea, flags=re.IGNORECASE)
    linea = re.sub(r"\s+", " ", linea).strip()
    return linea


def iterar_registros_ocr_estructurado(texto_ocr):
    if not texto_ocr:
        return []

    texto = texto_ocr.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"(?im)^=+\s*(?:INICIO|FIN)\s+OCR:.*?=+\s*$", "\n", texto)
    texto = re.sub(r"(?i)\b(?:Mostrar razonamiento|Gemini dijo)\b", " ", texto)

    # Gemini a veces responde todo en una sola linea usando " ; " entre registros.
    # Reconstruimos esos separadores como lineas logicas para no perder los ITEM.
    tags = r"(?:BEGIN_FACTURA_OCR|FUENTE\||ENCABEZADO\||ITEM\||TOTALES\||NOTA\||FIN_FACTURA_OCR)"
    texto = re.sub(r"\s*;\s*(?=" + tags + r")", "\n", texto)
    texto = re.sub(r"\s+(?=" + tags + r")", "\n", texto)

    registros = []
    for linea_raw in texto.split("\n"):
        linea = limpiar_linea_ocr(linea_raw)
        if not linea:
            continue
        m = re.search(tags, linea)
        if m and m.start() > 0:
            linea = linea[m.start():].strip()
        registros.append(linea)
    return registros


def normalizar_clave_factura(texto):
    texto = str(texto or "").strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def limpiar_valor_encabezado_factura(valor):
    valor = str(valor or "").strip()
    valor = re.sub(r"\s+", " ", valor)
    valor = valor.strip(" |-:;")
    if not valor or valor == "?":
        return ""
    return valor


def es_valor_proveedor_confiable(valor):
    valor = limpiar_valor_encabezado_factura(valor)
    if not valor:
        return False
    norm = normalizar_nombre_catalogo(valor)
    if not norm or es_nombre_ruido_factura(valor):
        return False
    if re.fullmatch(r"[\d\s.,:-]+", valor):
        return False
    if len(norm) < 3:
        return False
    return True


def extraer_proveedor_heuristico_desde_ocr(texto_ocr):
    if not texto_ocr:
        return ""
    registros = iterar_registros_ocr_estructurado(texto_ocr)
    candidatos = []
    patrones_empresa = re.compile(
        r"(?i)\b(S\.?A\.?S?\.?|LTDA\.?|LIMITADA|COOPERATIVA|CORPORACION|COMERCIALIZADORA|DISTRIBUIDORA|"
        r"ALIMENTOS|INDUSTRIA|INDUSTRIAS|GRUPO|COMPANIA|CIA\.?)\b"
    )
    for registro in registros[:35]:
        if registro.upper().startswith(("ITEM|", "TOTALES|", "NOTA|", "FIN_FACTURA_OCR")):
            continue
        partes = registro.split("|")
        valor = ""
        if len(partes) >= 3 and partes[0].upper() == "ENCABEZADO":
            valor = "|".join(partes[2:])
        elif len(partes) == 1:
            valor = partes[0]
        valor = limpiar_valor_encabezado_factura(valor)
        if not es_valor_proveedor_confiable(valor):
            continue
        score = 1
        if patrones_empresa.search(valor):
            score += 3
        if valor.isupper():
            score += 1
        if len(valor) >= 8:
            score += 1
        candidatos.append((score, valor))
    if not candidatos:
        return ""
    candidatos.sort(key=lambda item: (-item[0], len(item[1])))
    return candidatos[0][1]


def extraer_metadatos_factura(texto_ocr):
    metadatos = {
        "proveedor": "",
        "proveedor_nombre": "",
        "proveedor_nit": "",
        "factura_numero": "",
        "factura_fecha": "",
        "encabezados": {},
    }
    if not texto_ocr:
        return metadatos

    campos_proveedor = {
        "proveedor", "empresa", "razon social", "razonsocial", "emisor",
        "facturador", "vendedor", "nombre proveedor", "proveedor nombre",
    }
    campos_nit = {
        "nit", "nit proveedor", "nit emisor", "identificacion proveedor",
        "documento proveedor", "id proveedor",
    }
    campos_factura = {"factura", "numero factura", "factura numero", "no factura", "n factura"}
    campos_fecha = {
        "fecha", "fecha expedicion", "fecha emision", "fecha factura",
        "fecha de expedicion", "emision",
    }

    for registro in iterar_registros_ocr_estructurado(texto_ocr):
        if not registro.upper().startswith("ENCABEZADO|"):
            continue
        partes = registro.split("|")
        if len(partes) < 3:
            continue
        campo = limpiar_valor_encabezado_factura(partes[1])
        valor = limpiar_valor_encabezado_factura("|".join(partes[2:]))
        if not campo or not valor:
            continue
        clave = normalizar_clave_factura(campo)
        metadatos["encabezados"][clave] = valor
        if clave in campos_proveedor or any(alias in clave for alias in ("proveedor", "razon social", "emisor", "facturador")):
            if es_valor_proveedor_confiable(valor) and not metadatos["proveedor_nombre"]:
                metadatos["proveedor_nombre"] = valor
                metadatos["proveedor"] = valor
        elif clave in campos_nit or (clave == "nit" and not metadatos["proveedor_nit"]):
            metadatos["proveedor_nit"] = valor
        elif clave in campos_factura or ("factura" in clave and not metadatos["factura_numero"]):
            metadatos["factura_numero"] = valor
        elif clave in campos_fecha or (clave.startswith("fecha") and not metadatos["factura_fecha"]):
            metadatos["factura_fecha"] = valor

    if not metadatos["proveedor_nombre"]:
        proveedor = extraer_proveedor_heuristico_desde_ocr(texto_ocr)
        if proveedor:
            metadatos["proveedor_nombre"] = proveedor
            metadatos["proveedor"] = proveedor

    return metadatos


def extraer_items_desde_ocr_estructurado(texto_ocr):
    if not texto_ocr:
        return [], []

    acumulado = {}
    debug = []

    for linea in iterar_registros_ocr_estructurado(texto_ocr):
        if not linea.upper().startswith("ITEM|"):
            continue

        partes = linea.split("|")
        if len(partes) < 9:
            debug.append({
                "metodo": "ocr_estructurado",
                "linea": linea,
                "descartado": "columnas_incompletas",
            })
            continue

        _tag, _linea_num, codigo, descripcion, cantidad_raw, _unidad, precio_raw, subtotal_raw = partes[:8]
        texto_original = "|".join(partes[8:]).strip()
        descripcion = (descripcion or "").strip()
        codigo = (codigo or "").strip()
        precio_unitario = normalizar_precio_unitario(precio_raw)
        subtotal = normalizar_precio_unitario(subtotal_raw)

        cantidad = parsear_cantidad_entera(cantidad_raw)
        if not cantidad and texto_original:
            cantidad = extraer_cantidad_desde_linea_ocr(texto_original, descripcion)

        iva_porcentaje = extraer_iva_porcentaje_desde_linea_item(
            texto_original=texto_original,
            precio_unitario=precio_unitario,
            cantidad=cantidad,
            subtotal=subtotal,
        )
        precio_final, precio_iva_calculado = calcular_precio_unitario_final_con_iva(
            precio_unitario=precio_unitario,
            iva_porcentaje=iva_porcentaje,
            cantidad=cantidad,
            subtotal=subtotal,
        )

        if not descripcion or descripcion == "?" or es_nombre_ruido_factura(descripcion):
            debug.append({
                "metodo": "ocr_estructurado",
                "linea": linea,
                "descartado": "descripcion_no_confiable",
            })
            continue
        if not cantidad:
            debug.append({
                "metodo": "ocr_estructurado",
                "linea": linea,
                "producto": descripcion,
                "descartado": "sin_cantidad_confiable",
            })
            continue

        clave_codigo = normalizar_codigo_barras(codigo)
        clave = ("codigo", clave_codigo) if clave_codigo and clave_codigo != "?" else ("nombre", normalizar_nombre_catalogo(descripcion))
        if clave not in acumulado:
            acumulado[clave] = {
                "producto": descripcion,
                "cantidad": 0,
                "codigo_de_barras": "" if codigo == "?" else codigo,
                "precio_unitario": "",
                "precios_unitarios": [],
                "precio_unitario_visible": precio_unitario,
                "precio_unitario_sin_iva": precio_unitario if precio_iva_calculado else "",
                "iva_porcentaje": iva_porcentaje,
                "precio_incluye_iva": not precio_iva_calculado,
                "precio_iva_calculado": bool(precio_iva_calculado),
                "subtotal": subtotal,
                "texto_original": texto_original,
            }
        acumulado[clave]["cantidad"] += cantidad
        agregar_precio_unitario_a_row(acumulado[clave], precio_final or precio_unitario)
        agregar_metadatos_precio_a_row(acumulado[clave], {
            "precio_unitario_visible": precio_unitario,
            "precio_unitario_sin_iva": precio_unitario if precio_iva_calculado else "",
            "iva_porcentaje": iva_porcentaje,
            "precio_incluye_iva": not precio_iva_calculado,
            "precio_iva_calculado": bool(precio_iva_calculado),
        })
        debug.append({
            "metodo": "ocr_estructurado",
            "producto": descripcion,
            "codigo_de_barras": "" if codigo == "?" else codigo,
            "cantidad": cantidad,
            "precio_unitario": precio_final or precio_unitario,
            "precio_unitario_visible": precio_unitario,
            "precio_unitario_sin_iva": precio_unitario if precio_iva_calculado else "",
            "iva_porcentaje": iva_porcentaje,
            "precio_iva_calculado": bool(precio_iva_calculado),
            "subtotal": subtotal,
            "linea": linea,
        })

    return list(acumulado.values()), debug


def extraer_cantidad_desde_linea_ocr(linea, producto_catalogo):
    linea_original = linea or ""
    producto_nums = set(re.findall(r"\d+", normalizar_nombre_catalogo(producto_catalogo)))

    patrones_fuertes = [
        r"(?i)\b(?:cant|cantidad|qty)\.?\s*[:=]?\s*(\d{1,5}(?:[,.]0+)?)\b",
        r"(?i)\b(\d{1,5}(?:[,.]0+)?)\s*(?:und|unds|unidad|unidades|unid)\b",
        r"(?i)^\s*(\d{1,5}(?:[,.]0+)?)\s*x?\s+",
    ]

    for patron in patrones_fuertes:
        m = re.search(patron, linea_original)
        if not m:
            continue
        cantidad = parsear_cantidad_entera(m.group(1))
        if cantidad:
            return cantidad

    candidatos = []
    for m in re.finditer(r"\b\d{1,5}(?:[,.]0+)?\b", linea_original):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 6 or digits in producto_nums:
            continue
        cantidad = parsear_cantidad_entera(raw)
        if not cantidad:
            continue
        candidatos.append((m.start(), cantidad))

    if not candidatos:
        return None

    candidatos.sort(key=lambda item: item[0])
    return candidatos[0][1]


def extraer_items_desde_ocr_heuristico(texto_ocr, ruta_csv, umbral=0.58):
    if not texto_ocr or not ruta_csv:
        return [], []

    catalogo = cargar_catalogo_productos(ruta_csv)
    if not catalogo:
        return [], []

    acumulado = {}
    debug = []

    for linea_raw in texto_ocr.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        linea = limpiar_linea_ocr(linea_raw)
        if not linea or linea.startswith("==="):
            continue
        if es_nombre_ruido_factura(linea):
            continue

        mejor, score = encontrar_mejor_producto_catalogo(linea, catalogo)
        if not mejor or score < umbral:
            continue

        cantidad = extraer_cantidad_desde_linea_ocr(linea, mejor["original"])
        if not cantidad:
            debug.append({
                "metodo": "ocr_heuristico",
                "linea": linea,
                "producto_final": mejor["original"],
                "score": round(score, 4),
                "descartado": "sin_cantidad_confiable",
            })
            continue

        clave = mejor.get("productoid") or mejor["norm"]
        if clave not in acumulado:
            acumulado[clave] = {"producto": mejor["original"], "cantidad": 0}
        acumulado[clave]["cantidad"] += cantidad
        debug.append({
            "metodo": "ocr_heuristico",
            "linea": linea,
            "producto_final": mejor["original"],
            "cantidad": cantidad,
            "score": round(score, 4),
        })

    return list(acumulado.values()), debug


def enriquecer_rows_con_precios_ocr(rows, texto_ocr):
    if not rows or not texto_ocr:
        return rows

    rows_ocr, _debug = extraer_items_desde_ocr_estructurado(texto_ocr)
    if not rows_ocr:
        return rows

    ocr_por_codigo = {
        normalizar_codigo_barras(row.get("codigo_de_barras")): row
        for row in rows_ocr
        if normalizar_codigo_barras(row.get("codigo_de_barras"))
    }
    catalogo_ocr = []
    for row in rows_ocr:
        nombre = row.get("producto") or ""
        norm = normalizar_nombre_catalogo(nombre)
        if not norm:
            continue
        catalogo_ocr.append({
            "original": nombre,
            "norm": norm,
            "tokens": set(tokenizar_nombre(nombre)),
            "row": row,
        })

    for row in rows:
        codigo = normalizar_codigo_barras(row.get("codigo_de_barras"))
        match = ocr_por_codigo.get(codigo) if codigo else None
        if not match and catalogo_ocr:
            mejor, score = encontrar_mejor_producto_catalogo(row.get("original_producto") or row.get("producto") or "", catalogo_ocr)
            if mejor and score >= 0.45:
                match = mejor.get("row")
        if match:
            if not row.get("precio_unitario"):
                for precio in match.get("precios_unitarios") or [match.get("precio_unitario")]:
                    agregar_precio_unitario_a_row(row, precio)
            agregar_metadatos_precio_a_row(row, match)
    return rows


def escribir_json_salida(ruta_json, ok, raw_text="", ocr_text="", error="", ruta_csv=None):
    if not ruta_json:
        return

    metadatos_factura = extraer_metadatos_factura(ocr_text)

    try:
        rows_iniciales = resultado_a_items(raw_text)
    except Exception:
        rows_iniciales = []

    debug_matches = []
    rows_finales = rows_iniciales
    origen_rows = "deepseek"

    if not rows_iniciales and ruta_csv and ocr_text:
        try:
            rows_ocr, debug_ocr = extraer_items_desde_ocr_estructurado(ocr_text)
            if not rows_ocr:
                rows_ocr, debug_ocr = extraer_items_desde_ocr_heuristico(ocr_text, ruta_csv)
            if rows_ocr:
                rows_iniciales = rows_ocr
                rows_finales = rows_ocr
                debug_matches.extend(debug_ocr)
                origen_rows = "ocr_estructurado" if any(
                    (d or {}).get("metodo") == "ocr_estructurado" for d in debug_ocr
                ) else "ocr_heuristico"
        except Exception as e:
            debug_matches.append({
                "metodo": "ocr_heuristico",
                "producto_final": "",
                "score": 0,
                "error": str(e),
            })

    try:
        if ruta_csv and rows_iniciales:
            rows_finales, debug_catalogo = reconciliar_items_con_catalogo(rows_iniciales, ruta_csv)
            debug_matches.extend(debug_catalogo)
    except Exception as e:
        debug_matches.append({
            "entrada_modelo": "",
            "producto_final": "",
            "score": 0,
            "error": str(e),
        })

    try:
        rows_finales = enriquecer_rows_con_precios_ocr(rows_finales, ocr_text)
    except Exception as e:
        debug_matches.append({
            "metodo": "precio_ocr",
            "producto_final": "",
            "score": 0,
            "error": str(e),
        })

    for row in rows_finales:
        if isinstance(row, dict):
            row.setdefault("origen", origen_rows)

    raw_text_final = construir_raw_text_desde_rows(rows_finales) if rows_finales else (raw_text or "")

    payload = {
        "ok": bool(ok),
        "raw_text": raw_text_final,
        "raw_text_modelo": raw_text or "",
        "ocr_text": ocr_text or "",
        "proveedor": metadatos_factura.get("proveedor_nombre") or "",
        "proveedor_nombre": metadatos_factura.get("proveedor_nombre") or "",
        "proveedor_nit": metadatos_factura.get("proveedor_nit") or "",
        "factura_numero": metadatos_factura.get("factura_numero") or "",
        "factura_fecha": metadatos_factura.get("factura_fecha") or "",
        "proveedor_factura": {
            "nombre": metadatos_factura.get("proveedor_nombre") or "",
            "nit": metadatos_factura.get("proveedor_nit") or "",
            "factura": metadatos_factura.get("factura_numero") or "",
            "fecha": metadatos_factura.get("factura_fecha") or "",
        },
        "factura_encabezados": metadatos_factura.get("encabezados") or {},
        "rows": rows_finales,
        "rows_modelo": rows_iniciales,
        "origen_rows": origen_rows if rows_finales else "",
        "matching_debug": debug_matches,
        "error": error or "",
    }
    with open(ruta_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def parse_args():
    parser = argparse.ArgumentParser(description="Procesa fotos con Gemini + DeepSeek y devuelve JSON.")
    parser.add_argument("--images-dir", dest="images_dir", default=None, help="Carpeta con imágenes a procesar.")
    parser.add_argument("--csv", dest="csv_path", default=None, help="Ruta absoluta o relativa al catálogo CSV.")
    parser.add_argument("--json-out", dest="json_out", default=None, help="Ruta del archivo JSON de salida.")
    parser.add_argument("--no-wait", action="store_true", help="No esperar Enter al finalizar.")
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n" + "=" * 60)
    print(" GEMINI - EXTRACCIÓN DE TEXTO DESDE TODAS LAS IMÁGENES + DEEPSEEK")
    print("=" * 60)

    carpeta_actual = os.path.abspath(args.images_dir) if args.images_dir else os.path.dirname(os.path.abspath(__file__))
    respuesta_final = ""
    texto_ocr_consolidado = ""

    try:
        ruta_csv_efectiva = resolver_ruta_csv(args.csv_path)
    except Exception:
        ruta_csv_efectiva = args.csv_path

    try:
        asegurar_driver()
        texto_ocr_consolidado = procesar_todas_las_imagenes_con_gemini(carpeta_actual)

        if not texto_ocr_consolidado.strip():
            print(" No se pudo obtener OCR válido desde las imágenes.")
            escribir_json_salida(args.json_out, ok=False, ocr_text=texto_ocr_consolidado, error="No se pudo obtener OCR válido desde las imágenes.", ruta_csv=ruta_csv_efectiva)
            return 1

        respuesta_final = procesar_con_deepseek(texto_ocr_consolidado, ruta_csv=ruta_csv_efectiva)

        if not (respuesta_final or "").strip():
            escribir_json_salida(args.json_out, ok=False, raw_text=respuesta_final, ocr_text=texto_ocr_consolidado, error="No se pudo obtener una respuesta válida de DeepSeek.", ruta_csv=ruta_csv_efectiva)
            return 1

        escribir_json_salida(args.json_out, ok=True, raw_text=respuesta_final, ocr_text=texto_ocr_consolidado, ruta_csv=ruta_csv_efectiva)
        return 0

    except Exception as e:
        print(f" Error inesperado: {e}")
        escribir_json_salida(args.json_out, ok=False, raw_text=respuesta_final, ocr_text=texto_ocr_consolidado, error=str(e), ruta_csv=ruta_csv_efectiva)
        return 1
    finally:
        try:
            if not args.no_wait:
                input("\nPresiona Enter para cerrar el navegador...")
        except Exception:
            pass
        if driver is not None:
            driver.quit()

# ====================================================
# PROGRAMA PRINCIPAL
# ====================================================
if __name__ == "__main__":
    raise SystemExit(main())
