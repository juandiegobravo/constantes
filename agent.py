import time
import re
import os
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="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"
)

def procesar_texto_con_ia(concepto, texto_web):
    prompt = f"""Eres un experto analista de tarifas energeticas españolas. Extrae el precio exacto del siguiente texto web.
Concepto a buscar: {concepto}
Reglas estrictas:
- Si el concepto incluye 'potencia' (P1, P2, etc.), busca solo terminos de potencia o kW, y conserva estrictamente la unidad temporal original (día, mes, año).
- Si el concepto incluye 'energia' o 'variable', busca solo terminos de consumo o kWh.
- Si el concepto incluye 'fijo', busca solo terminos fijos, cuotas o euros/mes.
- Ignora promociones, descuentos porcentuales o numeros de telefono.

Devuelve UNICAMENTE el numero y su unidad (ej: '0,145 €/kWh' o '3,50 €/kW/mes'). Usa SIEMPRE coma (,) para los decimales. Si el dato no existe, devuelve exactamente la frase 'No encontrado'."""

    try:
        response = client.chat.completions.create(
            model="openrouter/free",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": texto_web[:15000]}
            ],
            temperature=0,
            timeout=5.0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error IA (o Timeout): {e}")
        return "No encontrado"

def post_procesar_resultado(concepto, resultado_ia):
    if not resultado_ia or resultado_ia == "No encontrado" or "no encontrado" in str(resultado_ia).lower():
        return "No encontrado"
        
    concepto_low = concepto.lower()
    
    match = re.search(r'(\d+[.,]\d+|\d+)', str(resultado_ia))
    if not match:
        return "No encontrado"
        
    valor_str = match.group(1).replace(',', '.')
    try:
        valor = float(valor_str)
    except ValueError:
        return "No encontrado"
        
    # Reglas según concepto
    es_potencia_diaria = False
    if "término" in concepto_low and "variable" in concepto_low:
        unidad = "€/kWh"
    elif "término" in concepto_low and "fijo" in concepto_low:
        unidad = "€/mes"
    elif "potencia" in concepto_low:
        if valor > 20:
            valor = valor / 365.0
            es_potencia_diaria = True
        unidad = "€/kWh/día"
    elif "energía" in concepto_low or "energia" in concepto_low:
        unidad = "€/kWh"
    elif "servicio" in concepto_low or "mantenimiento" in concepto_low:
        unidad = "€/mes"
    else:
        unidad = "€"

    if es_potencia_diaria:
        val_formatted = f"{valor:.4f}".replace('.', ',')
    else:
        val_str = str(valor).replace('.', ',')
        if ',' in val_str:
            entero, decimal = val_str.split(',')
            decimal = decimal.rstrip('0')
            if len(decimal) == 0:
                val_formatted = entero
            else:
                val_formatted = f"{entero},{decimal}"
        else:
            val_formatted = val_str

    if unidad:
        res = f"{val_formatted} {unidad}"
    else:
        res = val_formatted

    return res

def ir_a_celda(page, celda):
    page.keyboard.press("Control+g")
    page.wait_for_timeout(300)
    page.keyboard.type(celda)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)

def leer_celda(page):
    try:
        return page.inner_text("#t-formula-bar-input .cell-input").strip()
    except Exception:
        return ""

def main():
    URL_SHEET = "https://docs.google.com/spreadsheets/d/1l9gJgmmMgLy2pj7mk5gN8JG1hwuvt5QQwqU9fEsxYFY/edit?gid=164319118#gid=164319118"
    
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="./Constantes/perfil_google",
            headless=False,
            args=["--start-maximized"]
        )
        
        browser.route("**/*.{png,jpg,jpeg,svg,gif,webp,ttf,woff,woff2,mp4}", lambda route: route.abort())
        
        page = browser.pages[0] if browser.pages else browser.new_page()
        print("Cargando Google Sheet...")
        page.goto(URL_SHEET, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        
        print("Sheet abierto. Empezando a procesar filas (2 a 1239)...")
        
        for fila in range(2, 1240):
            try:
                # 1. Leer primero Columna X y continuar si está vacía
                ir_a_celda(page, f"X{fila}")
                selector_css = leer_celda(page)
                
                if not selector_css.strip():
                    continue
                
                ir_a_celda(page, f"D{fila}")
                concepto = leer_celda(page)
                
                ir_a_celda(page, f"Y{fila}")
                url = leer_celda(page)
                
                if not url.strip() or not url.startswith("http") or not concepto.strip():
                    continue
                    
                print(f"Fila {fila}: Scrapeando {url}...")
                
                nueva_pagina = browser.new_page()
                texto_extraido = None
                
                try:
                    nueva_pagina.goto(url, timeout=5000, wait_until="domcontentloaded")
                    
                    # Simulación de scroll para contenido dinámico
                    nueva_pagina.evaluate("window.scrollBy(0, 600)")
                    nueva_pagina.wait_for_timeout(800)
                    
                    # Intento 1: Vía Rápida - Playwright Puro
                    if selector_css:
                        print(f"Fila {fila}: Intento 1 - Usando selector '{selector_css}'")
                        try:
                            texto_extraido = nueva_pagina.locator(selector_css).inner_text(timeout=4000)
                        except Exception as e:
                            print(f"Fila {fila}: Selector falló. Pasando a Intento 2...")
                            texto_extraido = None
                            
                    # Intento 2: Plan de Rescate - IA
                    if not texto_extraido:
                        print(f"Fila {fila}: Intento 2 - Usando IA OpenRouter")
                        texto_web = nueva_pagina.inner_text("body")
                        if texto_web:
                            texto_extraido = procesar_texto_con_ia(concepto, texto_web)
                            
                except Exception as e:
                    print(f"Fila {fila}: Error cargando página -> {e}")
                finally:
                    nueva_pagina.close()
                
                if texto_extraido:
                    resultado_final = post_procesar_resultado(concepto, texto_extraido)
                else:
                    resultado_final = "No encontrado"
                
                # 3. Limpieza de texto estricta
                if not resultado_final:
                    resultado_final = "No encontrado"
                else:
                    resultado_final = re.sub(r'\s+', ' ', str(resultado_final)).strip()
                    if not resultado_final:
                        resultado_final = "No encontrado"
                
                ir_a_celda(page, f"AD{fila}")
                page.keyboard.type(resultado_final)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                
                print(f"Fila {fila} -> {resultado_final}")
                
                # 4. Registro de última actualización en Columna Q
                ir_a_celda(page, f"Q{fila}")
                fecha_actual = datetime.now().strftime('%d-%m-%Y')
                page.keyboard.type(fecha_actual)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                
            except Exception as e:
                print(f"Fila {fila}: Error crítico -> {e}")
                ir_a_celda(page, f"AD{fila}")
                page.keyboard.type("No encontrado")
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                
        print("Fin del proceso.")

if __name__ == "__main__":
    main()
