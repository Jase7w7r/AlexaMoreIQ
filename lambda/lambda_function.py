# -*- coding: utf-8 -*-
"""Skill de Alexa «Mi asistente»: conversación con un modelo de NVIDIA, búsqueda web
cuando hace falta y memoria por persona (perfiles de voz de Alexa).

Corre en una skill alojada por Amazon (Python 3.8). También se puede invocar en local
con pruebas/simular.py, que usa una memoria en archivo en vez de S3.
"""
import datetime
import html
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from ask_sdk_core.api_client import DefaultApiClient
from ask_sdk_core.dispatch_components import (AbstractExceptionHandler,
                                              AbstractRequestHandler)
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.utils import get_slot_value, is_intent_name, is_request_type
from ask_sdk_model import Response
from ask_sdk_model.interfaces.alexa.presentation.apl import \
    RenderDocumentDirective
from ask_sdk_model.services.directive import (Header, SendDirectiveRequest,
                                              SpeakDirective)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------- configuración

AQUI = os.path.dirname(os.path.abspath(__file__))
BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
MODELO = os.environ.get("ASISTENTE_MODELO", "nvidia/nemotron-3.5-lightning-30b-a3b")
MODELO_RESPALDO = os.environ.get("ASISTENTE_MODELO_RESPALDO", "nvidia/nemotron-3-super-120b-a12b")
LUGAR = "México"      # se afina con la clave LUGAR de secretos.json
TIEMPO_LIMITE = 7.0          # Alexa corta a los 8 s; dejamos margen
TURNOS_EN_SESION = 6         # pares pregunta/respuesta que viajan en la sesión
MAX_NOTAS = 30               # notas persistentes por persona
MAX_VOZ = 700                # caracteres hablados por respuesta
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _leer_secretos() -> Dict[str, str]:
    """Llaves desde variables de entorno o desde secretos.json junto al código.
    La skill alojada por Amazon no permite variables de entorno, así que ahí se usa el archivo."""
    datos = {}  # type: Dict[str, str]
    ruta = os.path.join(AQUI, "secretos.json")
    if os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                datos.update(json.load(f))
        except Exception as e:  # pragma: no cover
            logger.error("No pude leer secretos.json: %s", e)
    for clave in ("NVIDIA_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY",
                  "GOOGLE_API_KEY", "GOOGLE_CX", "LUGAR"):
        if os.environ.get(clave):
            datos[clave] = os.environ[clave]
    return datos


SECRETOS = _leer_secretos()
NVIDIA_API_KEY = SECRETOS.get("NVIDIA_API_KEY", "")
BRAVE_API_KEY = SECRETOS.get("BRAVE_API_KEY", "")
TAVILY_API_KEY = SECRETOS.get("TAVILY_API_KEY", "")
SERPER_API_KEY = SECRETOS.get("SERPER_API_KEY", "")
GOOGLE_API_KEY = SECRETOS.get("GOOGLE_API_KEY", "")
GOOGLE_CX = SECRETOS.get("GOOGLE_CX", "")
# Dónde está la casa, para que el modelo responda con ese contexto. Va en secretos.json
# (clave "LUGAR") para no dejar datos personales en el código.
LUGAR = SECRETOS.get("LUGAR") or LUGAR


class SinTiempo(Exception):
    """Se agotó el presupuesto de tiempo de la petición."""


class Reloj:
    def __init__(self, limite: float = TIEMPO_LIMITE):
        self.inicio = time.time()
        self.limite = limite

    def restante(self) -> float:
        return self.limite - (time.time() - self.inicio)

    def tiempo_para(self, tope: float) -> float:
        r = self.restante() - 0.3
        if r < 0.8:
            raise SinTiempo()
        return min(tope, r)


# ---------------------------------------------------------------- modelo

SESION = requests.Session()

HERRAMIENTA_DECIDIR = [{
    "type": "function",
    "function": {
        "name": "decidir",
        "description": "Decide cómo atender lo que dijo el usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "necesita_busqueda": {
                    "type": "boolean",
                    "description": ("true si la respuesta depende de hechos recientes o cambiantes: noticias, "
                                    "deportes, precios, tipo de cambio, clima, horarios, eventos, quién ocupa un "
                                    "cargo, versiones de productos, o cualquier dato del que no estés seguro."),
                },
                "consulta": {
                    "type": "string",
                    "description": "Términos de búsqueda en español, concretos, con lugar y fecha si aplica. Vacío si no se busca.",
                },
                "respuesta": {
                    "type": "string",
                    "description": "Si no se busca: la respuesta final hablada, en una a tres frases. Vacío si se busca.",
                },
                "lugar_clima": {
                    "type": "string",
                    "description": ("Solo si la pregunta es sobre el clima o el pronóstico (lluvia, temperatura, frío, "
                                    "calor, paraguas): la ciudad de la que se pregunta, o la palabra AQUI si no "
                                    "menciona ninguna. Vacío si no es de clima."),
                },
            },
            "required": ["necesita_busqueda", "consulta", "respuesta", "lugar_clima"],
        },
    },
}]


def _fecha_hoy() -> str:
    hoy = datetime.date.today()
    return "%s %d de %s de %d" % (DIAS[hoy.weekday()], hoy.day, MESES[hoy.month - 1], hoy.year)


def _prompt_sistema(persona: Dict[str, Any]) -> str:
    partes = [
        "Eres «mi asistente», el asistente de voz de una casa en %s. Hoy es %s." % (LUGAR, _fecha_hoy()),
        "Tu conocimiento se detiene meses antes de hoy; no inventes datos recientes.",
        "Hablas español de México, cálido y directo. Te escuchan por una bocina: una a tres frases, "
        "sin listas, sin markdown, sin emojis, números y unidades dichos como se pronuncian.",
        "Alexa ya se encarga de alarmas, temporizadores, música y domótica; si te piden eso, "
        "di que se lo pidan directamente a Alexa.",
    ]
    nombre = persona.get("nombre")
    if nombre:
        partes.append("Quien te habla se llama %s; puedes llamarle por su nombre de vez en cuando." % nombre)
    else:
        partes.append("No sabes cómo se llama quien te habla.")
    notas = persona.get("notas") or []
    if notas:
        partes.append("Cosas que esta persona te pidió recordar (úsalas si vienen al caso): "
                      + " | ".join(notas[-MAX_NOTAS:]))
    return " ".join(partes)


def _llamar(modelo: str, mensajes: List[Dict[str, Any]], reloj: Reloj, tope: float,
            herramientas: Optional[List[Dict[str, Any]]] = None, forzar: Optional[str] = None,
            max_tokens: int = 350) -> Dict[str, Any]:
    cuerpo = {
        "model": modelo,
        "messages": mensajes,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "chat_template_kwargs": {"enable_thinking": False},
    }  # type: Dict[str, Any]
    if herramientas:
        cuerpo["tools"] = herramientas
        cuerpo["tool_choice"] = ({"type": "function", "function": {"name": forzar}} if forzar else "auto")
    r = SESION.post(
        BASE_URL + "/chat/completions",
        json=cuerpo,
        headers={"Authorization": "Bearer " + NVIDIA_API_KEY, "Accept": "application/json"},
        timeout=reloj.tiempo_para(tope),
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def _llamar_con_respaldo(mensajes, reloj, tope, **kw) -> Dict[str, Any]:
    try:
        return _llamar(MODELO, mensajes, reloj, tope, **kw)
    except (requests.HTTPError, requests.ConnectionError) as e:
        logger.warning("Modelo principal falló (%s); pruebo el de respaldo", e)
        return _llamar(MODELO_RESPALDO, mensajes, reloj, tope, **kw)


def decidir(pregunta: str, sistema: str, historial: List[Dict[str, str]], reloj: Reloj) -> Dict[str, Any]:
    mensajes = [{"role": "system", "content": sistema}]
    for turno in historial:
        mensajes.append({"role": "user", "content": turno["u"]})
        mensajes.append({"role": "assistant", "content": turno["a"]})
    mensajes.append({"role": "user", "content": pregunta})
    msj = _llamar_con_respaldo(mensajes, reloj, 4.5, herramientas=HERRAMIENTA_DECIDIR, forzar="decidir")
    llamadas = msj.get("tool_calls") or []
    if llamadas:
        try:
            args = llamadas[0]["function"]["arguments"]
            d = json.loads(args) if isinstance(args, str) else args
            return {
                "buscar": bool(d.get("necesita_busqueda")),
                "consulta": (d.get("consulta") or "").strip(),
                "respuesta": (d.get("respuesta") or "").strip(),
                "lugar_clima": (d.get("lugar_clima") or "").strip(),
            }
        except Exception as e:
            logger.warning("Argumentos de decidir inválidos: %s", e)
    # El modelo contestó en texto en vez de usar la herramienta: lo tomamos como respuesta directa.
    return {"buscar": False, "consulta": "", "respuesta": (msj.get("content") or "").strip(), "lugar_clima": ""}


def responder_con_resultados(pregunta: str, resultados: str, sistema: str,
                             historial: List[Dict[str, str]], reloj: Reloj) -> str:
    if resultados == "(sin resultados)":
        # La búsqueda no respondió: que conteste con lo que sabe, avisando que no pudo verificar.
        instruccion = (" No pudiste buscar en internet. Si la pregunta es de conocimiento general, "
                       "contesta con lo que sabes. Si depende de datos recientes, di que no pudiste "
                       "consultarlo ahora y no inventes cifras ni resultados.")
        contenido = pregunta
    else:
        instruccion = (" Responde SOLO con lo que digan los resultados de búsqueda; si no alcanzan "
                       "para contestar, dilo con claridad y no rellenes.")
        contenido = "Pregunta: %s\n\nResultados de búsqueda:\n%s" % (pregunta, resultados)
    mensajes = [{"role": "system", "content": sistema + instruccion}]
    for turno in historial[-2:]:
        mensajes.append({"role": "user", "content": turno["u"]})
        mensajes.append({"role": "assistant", "content": turno["a"]})
    mensajes.append({"role": "user", "content": contenido})
    msj = _llamar_con_respaldo(mensajes, reloj, 3.5)
    return (msj.get("content") or "").strip()


# ---------------------------------------------------------------- búsqueda web

CABECERAS_NAVEGADOR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "es-MX,es;q=0.9",
}


def _limpiar_html(s: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", s)).strip()


def buscar_brave(consulta: str, reloj: Reloj, n: int = 5) -> List[Dict[str, str]]:
    r = SESION.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": consulta, "country": "MX", "search_lang": "es", "count": n},
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
        timeout=reloj.tiempo_para(3.0),
    )
    r.raise_for_status()
    return [{"titulo": x.get("title", ""), "resumen": _limpiar_html(x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])[:n]]


def buscar_tavily(consulta: str, reloj: Reloj, n: int = 5) -> List[Dict[str, str]]:
    """Gratis hasta 1000 consultas al mes sin tarjeta; hecho para modelos de lenguaje."""
    r = SESION.post(
        "https://api.tavily.com/search",
        json={"query": consulta, "max_results": n, "search_depth": "basic", "include_answer": True,
              "country": "mexico"},
        headers={"Authorization": "Bearer " + TAVILY_API_KEY, "Content-Type": "application/json"},
        timeout=reloj.tiempo_para(3.5),
    )
    r.raise_for_status()
    datos = r.json()
    resultados = []  # type: List[Dict[str, str]]
    if datos.get("answer"):
        resultados.append({"titulo": "Resumen", "resumen": datos["answer"]})
    for x in datos.get("results", [])[:n]:
        resultados.append({"titulo": x.get("title", ""), "resumen": _limpiar_html(x.get("content", ""))[:400]})
    return resultados


def buscar_serper(consulta: str, reloj: Reloj, n: int = 5) -> List[Dict[str, str]]:
    """Resultados de Google; 2500 consultas gratis al registrarse, sin tarjeta."""
    r = SESION.post(
        "https://google.serper.dev/search",
        json={"q": consulta, "gl": "mx", "hl": "es", "num": n},
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        timeout=reloj.tiempo_para(3.0),
    )
    r.raise_for_status()
    datos = r.json()
    resultados = []  # type: List[Dict[str, str]]
    caja = datos.get("answerBox") or {}
    if caja.get("answer") or caja.get("snippet"):
        resultados.append({"titulo": caja.get("title", "Respuesta destacada"),
                           "resumen": caja.get("answer") or caja.get("snippet")})
    for x in datos.get("organic", [])[:n]:
        resultados.append({"titulo": x.get("title", ""), "resumen": x.get("snippet", "")})
    return resultados


def buscar_google(consulta: str, reloj: Reloj, n: int = 5) -> List[Dict[str, str]]:
    """Google Programmable Search: 100 consultas al día gratis. Necesita llave de Google Cloud y el
    identificador (cx) de un buscador programable configurado para buscar en toda la web."""
    r = SESION.get(
        "https://www.googleapis.com/customsearch/v1",
        params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CX, "q": consulta, "gl": "mx", "hl": "es", "lr": "lang_es", "num": n},
        timeout=reloj.tiempo_para(3.0),
    )
    r.raise_for_status()
    return [{"titulo": x.get("title", ""), "resumen": _limpiar_html(x.get("snippet", ""))}
            for x in r.json().get("items", [])[:n]]


def buscar_duckduckgo(consulta: str, reloj: Reloj, n: int = 5) -> List[Dict[str, str]]:
    """Sin llave, pero DuckDuckGo bloquea con frecuencia a los servidores (responde 202 con un reto)."""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": consulta, "kl": "mx-es"})
    r = SESION.get(url, headers=CABECERAS_NAVEGADOR, timeout=reloj.tiempo_para(3.0))
    if r.status_code == 202:
        raise RuntimeError("DuckDuckGo pidió verificación (202)")
    r.raise_for_status()
    patron = (r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>'
              r'.*?<a class="result__snippet"[^>]*>(.*?)</a>')
    return [{"titulo": _limpiar_html(t), "resumen": _limpiar_html(s)}
            for _, t, s in re.findall(patron, r.text, re.S)[:n]]


def buscar_wikipedia(consulta: str, reloj: Reloj, n: int = 3) -> List[Dict[str, str]]:
    """Siempre disponible y sin llave; sirve para hechos, personas y lugares, no para noticias."""
    r = SESION.get(
        "https://es.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": consulta, "format": "json",
                "srlimit": n, "utf8": 1},
        headers={"User-Agent": "mi-asistente-alexa/1.0"},
        timeout=reloj.tiempo_para(2.5),
    )
    r.raise_for_status()
    titulos = [x.get("title", "") for x in r.json().get("query", {}).get("search", [])[:n]]
    resultados = []  # type: List[Dict[str, str]]
    for titulo in titulos[:2]:   # el resumen del artículo dice mucho más que el fragmento de búsqueda
        try:
            s = SESION.get("https://es.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(titulo.replace(" ", "_")),
                           headers={"User-Agent": "mi-asistente-alexa/1.0"}, timeout=reloj.tiempo_para(2.0))
            s.raise_for_status()
            resultados.append({"titulo": titulo, "resumen": (s.json().get("extract") or "")[:700]})
        except SinTiempo:
            raise
        except Exception as e:
            logger.info("Sin resumen de «%s»: %s", titulo, e)
            resultados.append({"titulo": titulo, "resumen": ""})
    return [x for x in resultados if x["resumen"]]


def buscar(consulta: str, reloj: Reloj) -> str:
    """Brave si hay llave (lo mejor para noticias y precios); si no, DuckDuckGo; y si ese bloquea,
    Wikipedia en español. Devuelve «(sin resultados)» si nada responde."""
    fuentes = []
    if BRAVE_API_KEY:
        fuentes.append(("Brave", buscar_brave))
    if TAVILY_API_KEY:
        fuentes.append(("Tavily", buscar_tavily))
    if SERPER_API_KEY:
        fuentes.append(("Serper", buscar_serper))
    if GOOGLE_API_KEY and GOOGLE_CX:
        fuentes.append(("Google", buscar_google))
    fuentes.append(("DuckDuckGo", buscar_duckduckgo))
    fuentes.append(("Wikipedia", buscar_wikipedia))
    for nombre, fn in fuentes:
        try:
            resultados = fn(consulta, reloj)
        except SinTiempo:
            raise
        except Exception as e:
            logger.warning("%s falló: %s", nombre, e)
            continue
        if resultados:
            logger.info("Búsqueda por %s: %d resultados", nombre, len(resultados))
            return "\n".join("- %s: %s" % (x["titulo"], x["resumen"]) for x in resultados)
    return "(sin resultados)"


# ---------------------------------------------------------------- clima (Open-Meteo, sin llave)

CODIGOS_TIEMPO = [
    ((0,), "despejado"), ((1,), "mayormente despejado"), ((2,), "parcialmente nublado"), ((3,), "nublado"),
    ((45, 48), "con niebla"), ((51, 53, 55, 56, 57), "con llovizna"), ((61, 63, 65, 66, 67), "con lluvia"),
    ((71, 73, 75, 77), "con nieve"), ((80, 81, 82), "con chubascos"), ((85, 86), "con nevadas"),
    ((95, 96, 99), "con tormenta"),
]


def _describir_codigo(codigo: int) -> str:
    for codigos, texto in CODIGOS_TIEMPO:
        if codigo in codigos:
            return texto
    return ""


PAISES_ISO = {"méxico": "mx", "mexico": "mx", "españa": "es", "argentina": "ar", "colombia": "co",
              "chile": "cl", "perú": "pe", "peru": "pe", "estados unidos": "us"}


def _ubicar(nombre: str, reloj: Reloj) -> Optional[Dict[str, Any]]:
    """Coordenadas de una ciudad. Nominatim (OpenStreetMap) entiende «Querétaro» como la capital y no
    como un pueblo de Chiapas, que es lo que devuelve el geocodificador de Open-Meteo; se prueba
    primero acotado al país de LUGAR y, si no hay nada, sin acotar (para ciudades de fuera)."""
    pais = LUGAR.split(",")[-1].strip().lower() if "," in LUGAR else ""
    iso = PAISES_ISO.get(pais, "")
    for codigos in ([iso] if iso else []) + [""]:
        try:
            params = {"q": nombre, "format": "jsonv2", "limit": 5, "accept-language": "es"}
            if codigos:
                params["countrycodes"] = codigos
            r = SESION.get("https://nominatim.openstreetmap.org/search", params=params,
                           headers={"User-Agent": "mi-asistente-alexa/1.0"}, timeout=reloj.tiempo_para(2.5))
            r.raise_for_status()
            res = r.json()
        except SinTiempo:
            raise
        except Exception as e:
            logger.info("Nominatim falló: %s", e)
            res = []
        if res:
            # Acotado al país solo aceptamos ciudades de verdad; un «Madrid» que sea ranchería en México
            # no cuenta, y así la segunda pasada, sin país, encuentra la capital de España.
            tipos = ("city", "town", "administrative") if codigos else ("city", "town", "village", "administrative")
            ciudades = [x for x in res if x.get("type") in tipos]
            if not ciudades and codigos:
                continue
            x = (ciudades or res)[0]
            partes = [p.strip() for p in (x.get("display_name") or nombre).split(",")]
            return {"latitude": float(x["lat"]), "longitude": float(x["lon"]),
                    "name": partes[0], "admin1": partes[-1] if len(partes) > 1 else ""}
    # Respaldo: geocodificador de Open-Meteo, el más poblado del país configurado.
    g = SESION.get("https://geocoding-api.open-meteo.com/v1/search",
                   params={"name": nombre, "count": 5, "language": "es", "format": "json"},
                   timeout=reloj.tiempo_para(2.5)).json()
    candidatos = g.get("results") or []
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: ((c.get("country") or "").lower() == pais, c.get("population") or 0), reverse=True)
    c = candidatos[0]
    return {"latitude": c["latitude"], "longitude": c["longitude"], "name": c.get("name"),
            "admin1": c.get("admin1") or c.get("country") or ""}


def clima(lugar: str, reloj: Reloj) -> str:
    """Pronóstico de tres días para una ciudad. Devuelve texto para que el modelo lo redacte."""
    nombre = lugar if lugar and lugar.upper() != "AQUI" else LUGAR.split(",")[0].strip()
    c = _ubicar(nombre, reloj)
    if not c:
        return "(sin resultados)"
    f = SESION.get("https://api.open-meteo.com/v1/forecast",
                   params={"latitude": c["latitude"], "longitude": c["longitude"], "timezone": "auto",
                           "forecast_days": 3, "current": "temperature_2m,weather_code",
                           "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"},
                   timeout=reloj.tiempo_para(2.5)).json()
    d = f.get("daily") or {}
    actual = f.get("current") or {}
    nombres = ["hoy", "mañana", "pasado mañana"]
    lineas = ["Lugar: %s, %s. Ahora: %s grados, %s." % (
        c.get("name"), c.get("admin1"), actual.get("temperature_2m"),
        _describir_codigo(int(actual.get("weather_code", -1))) or "sin dato")]
    for i, fecha in enumerate(d.get("time", [])[:3]):
        lineas.append("%s (%s): máxima %s, mínima %s grados, probabilidad de lluvia %s por ciento, %s." % (
            nombres[i], fecha, d["temperature_2m_max"][i], d["temperature_2m_min"][i],
            d["precipitation_probability_max"][i], _describir_codigo(int(d["weather_code"][i])) or "sin dato"))
    return "\n".join(lineas)


# ---------------------------------------------------------------- voz y pantalla

def limpiar_para_voz(texto: str, maximo: int = MAX_VOZ) -> str:
    t = re.sub(r"[*_`#>]+", "", texto)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)   # enlaces markdown
    t = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", t)              # viñeta al inicio
    t = re.sub(r"\n\s*(?:[-•]|\d+[.)])\s+", ". ", t)           # viñetas y numeración: pausa entre puntos
    t = re.sub(r"\s+-\s+", ". ", t)                            # guiones sueltos entre frases
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"([:,;.])\s*\.", r"\1", t)                     # «temas:.» → «temas:», «..» → «.»
    if len(t) > maximo:
        corte = t.rfind(". ", 0, maximo)
        t = t[: corte + 1] if corte > maximo // 2 else t[:maximo].rstrip() + "…"
    return t


def escapar_ssml(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _soporta_apl(handler_input: HandlerInput) -> bool:
    try:
        interfaces = handler_input.request_envelope.context.system.device.supported_interfaces
        return getattr(interfaces, "alexa_presentation_apl", None) is not None
    except Exception:
        return False


_APL_DOC = None  # type: Optional[Dict[str, Any]]


def _apl(handler_input: HandlerInput, titulo: str, texto: str, pie: str = "") -> None:
    global _APL_DOC
    if not _soporta_apl(handler_input):
        return
    if _APL_DOC is None:
        with open(os.path.join(AQUI, "apl_respuesta.json"), "r", encoding="utf-8") as f:
            _APL_DOC = json.load(f)
    tamano = "5.5vh" if len(texto) < 140 else ("4.6vh" if len(texto) < 300 else "3.9vh")
    handler_input.response_builder.add_directive(RenderDocumentDirective(
        token="respuesta",
        document=_APL_DOC,
        datasources={"datos": {"titulo": titulo, "texto": texto[:600], "pie": pie, "tamano": tamano}},
    ))


def _hablar(handler_input: HandlerInput, texto: str, titulo: str = "Mi asistente",
            seguir: bool = True, pie: str = "") -> Response:
    voz = limpiar_para_voz(texto)
    _apl(handler_input, titulo, voz, pie)
    rb = handler_input.response_builder.speak(escapar_ssml(voz))
    if seguir:
        rb.ask("¿Algo más?")
    else:
        rb.set_should_end_session(True)
    return rb.response


def _avisar_progreso(handler_input: HandlerInput, texto: str) -> None:
    """Respuesta progresiva: Alexa dice algo mientras buscamos. Si falla, no pasa nada."""
    try:
        env = handler_input.request_envelope
        directiva = SendDirectiveRequest(
            header=Header(request_id=env.request.request_id),
            directive=SpeakDirective(speech=texto),
        )
        handler_input.service_client_factory.get_directive_service().enqueue(directiva)
    except Exception as e:
        logger.info("Sin respuesta progresiva: %s", e)


# ---------------------------------------------------------------- memoria

def _clave_persona(handler_input: HandlerInput) -> str:
    """Perfil de voz si Alexa lo manda; si no, la cuenta completa."""
    sistema = handler_input.request_envelope.context.system
    persona = getattr(sistema, "person", None)
    if persona is not None and getattr(persona, "person_id", None):
        return "persona/" + persona.person_id
    return "cuenta/" + sistema.user.user_id


def _persona(handler_input: HandlerInput) -> Dict[str, Any]:
    try:
        datos = handler_input.attributes_manager.persistent_attributes
    except Exception as e:
        logger.warning("Sin memoria persistente: %s", e)
        datos = {}
    datos.setdefault("nombre", None)
    datos.setdefault("notas", [])
    datos.setdefault("visitas", 0)
    return datos


def _guardar_persona(handler_input: HandlerInput, datos: Dict[str, Any]) -> None:
    try:
        datos["ultima_vez"] = datetime.datetime.now().isoformat(timespec="seconds")
        handler_input.attributes_manager.persistent_attributes = datos
        handler_input.attributes_manager.save_persistent_attributes()
    except Exception as e:
        logger.warning("No pude guardar la memoria: %s", e)


def _historial(handler_input: HandlerInput) -> List[Dict[str, str]]:
    return handler_input.attributes_manager.session_attributes.get("historial", [])


def _anotar_turno(handler_input: HandlerInput, pregunta: str, respuesta: str) -> None:
    h = _historial(handler_input)
    h.append({"u": pregunta, "a": respuesta})
    handler_input.attributes_manager.session_attributes["historial"] = h[-TURNOS_EN_SESION:]


# ---------------------------------------------------------------- manejadores

class LanzarHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        persona = _persona(handler_input)
        persona["visitas"] = int(persona.get("visitas", 0)) + 1
        _guardar_persona(handler_input, persona)
        if persona.get("nombre"):
            texto = "Hola, %s. ¿Qué quieres saber?" % persona["nombre"]
        else:
            texto = ("Hola. Soy tu asistente: puedo responder preguntas, buscar en internet y recordar "
                     "lo que me pidas. ¿Cómo te llamas?")
        return _hablar(handler_input, texto)


class PreguntaHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("PreguntaIntent")(handler_input)

    def handle(self, handler_input):
        pregunta = (get_slot_value(handler_input, "frase") or "").strip()
        if not pregunta:
            return _hablar(handler_input, "No te escuché bien. ¿Qué quieres saber?")
        if not NVIDIA_API_KEY:
            return _hablar(handler_input, "Me falta la llave del modelo. Revisa el archivo secretos punto jason.", seguir=False)

        reloj = Reloj()
        persona = _persona(handler_input)
        sistema = _prompt_sistema(persona)
        pie = ""
        try:
            d = decidir(pregunta, sistema, _historial(handler_input), reloj)
            if d.get("lugar_clima"):
                _avisar_progreso(handler_input, "Déjame ver el pronóstico.")
                try:
                    resultados = clima(d["lugar_clima"], reloj)
                except SinTiempo:
                    raise
                except Exception as e:
                    logger.warning("Open-Meteo falló: %s", e)
                    resultados = "(sin resultados)"
                respuesta = responder_con_resultados(pregunta, resultados, sistema, _historial(handler_input), reloj)
                pie = "Pronóstico de Open-Meteo" if resultados != "(sin resultados)" else "Sin acceso al pronóstico"
            elif d["buscar"]:
                _avisar_progreso(handler_input, "Déjame buscar.")
                consulta = d["consulta"] or pregunta
                resultados = buscar(consulta, reloj)
                respuesta = responder_con_resultados(pregunta, resultados, sistema, _historial(handler_input), reloj)
                pie = ("Búsqueda: " + consulta) if resultados != "(sin resultados)" else "Sin acceso a búsqueda"
            else:
                respuesta = d["respuesta"]
            if not respuesta:
                respuesta = "No encontré una buena respuesta. ¿Lo pregunto de otra forma?"
        except SinTiempo:
            logger.warning("Sin tiempo para «%s»", pregunta)
            respuesta = "Se me fue el tiempo buscando eso. Pregúntamelo otra vez."
        except Exception as e:
            logger.exception("Fallo atendiendo «%s»: %s", pregunta, e)
            respuesta = "Tuve un problema para consultar eso. Inténtalo de nuevo en un momento."

        _anotar_turno(handler_input, pregunta, respuesta)
        return _hablar(handler_input, respuesta, titulo=pregunta[:60], pie=pie)


class NombreHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("NombreIntent")(handler_input)

    def handle(self, handler_input):
        nombre = (get_slot_value(handler_input, "nombre") or "").strip().title()
        if not nombre:
            return _hablar(handler_input, "No entendí el nombre. ¿Cómo te llamas?")
        persona = _persona(handler_input)
        persona["nombre"] = nombre
        _guardar_persona(handler_input, persona)
        clave = _clave_persona(handler_input)
        if clave.startswith("cuenta/"):
            texto = ("Mucho gusto, %s. Ojo: Alexa no me dijo quién habla, así que te recordaré como la cuenta "
                     "de la casa. Para que distinga a cada quien, crea perfiles de voz en la app de Alexa y "
                     "activa la personalización de skills." % nombre)
        else:
            texto = "Mucho gusto, %s. Ya te reconozco por tu voz. ¿Qué quieres saber?" % nombre
        return _hablar(handler_input, texto)


class RecordarHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("RecordarIntent")(handler_input)

    def handle(self, handler_input):
        frase = (get_slot_value(handler_input, "frase") or "").strip()
        if not frase:
            return _hablar(handler_input, "¿Qué quieres que recuerde?")
        persona = _persona(handler_input)
        nota = "%s (%s)" % (frase, datetime.date.today().isoformat())
        persona["notas"] = (persona.get("notas") or [])[-(MAX_NOTAS - 1):] + [nota]
        _guardar_persona(handler_input, persona)
        quien = persona.get("nombre") or "tú"
        return _hablar(handler_input, "Listo, lo recuerdo: %s. Solo %s lo verás, mientras Alexa reconozca tu voz." % (frase, quien),
                       titulo="Recordaré", pie="%d notas guardadas" % len(persona["notas"]))


class QuienSoyHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("QuienSoyIntent")(handler_input)

    def handle(self, handler_input):
        persona = _persona(handler_input)
        reconocido = not _clave_persona(handler_input).startswith("cuenta/")
        partes = []
        if persona.get("nombre"):
            partes.append("Eres %s%s." % (persona["nombre"], ", te reconozco por tu voz" if reconocido else ""))
        else:
            partes.append("Todavía no sé cómo te llamas; dime «me llamo» y tu nombre.")
        notas = persona.get("notas") or []
        if notas:
            ultimas = [re.sub(r" \(\d{4}-\d{2}-\d{2}\)$", "", n) for n in notas[-3:]]
            partes.append("Me pediste recordar %d cosa%s; las últimas: %s." % (len(notas), "" if len(notas) == 1 else "s", "; ".join(ultimas)))
        else:
            partes.append("No me has pedido recordar nada.")
        return _hablar(handler_input, " ".join(partes), titulo="Sobre ti")


class OlvidarHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("OlvidarIntent")(handler_input)

    def handle(self, handler_input):
        try:
            handler_input.attributes_manager.delete_persistent_attributes()
        except Exception as e:
            logger.warning("No pude borrar la memoria: %s", e)
        handler_input.attributes_manager.session_attributes["historial"] = []
        return _hablar(handler_input, "Listo, borré tu nombre y todo lo que me pediste recordar.", titulo="Memoria borrada")


class AyudaHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        texto = ("Pregúntame lo que quieras y, si hace falta, busco en internet. Puedes decir «recuerda que» "
                 "y algo para que lo guarde, «qué sabes de mí» para oír tus notas, o «me llamo» y tu nombre. "
                 "¿Qué quieres saber?")
        return _hablar(handler_input, texto, titulo="Ayuda")


class SalirHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return (is_intent_name("AMAZON.StopIntent")(handler_input)
                or is_intent_name("AMAZON.CancelIntent")(handler_input)
                or is_intent_name("AMAZON.NavigateHomeIntent")(handler_input))

    def handle(self, handler_input):
        return _hablar(handler_input, "Hasta luego.", seguir=False)


class FallbackHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        return _hablar(handler_input, "No entendí. Dímelo de otra forma, o di «ayuda».")


class SesionTerminadaHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        return handler_input.response_builder.response


class ErrorHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        return True

    def handle(self, handler_input, exception):
        logger.exception("Error no controlado: %s", exception)
        return _hablar(handler_input, "Algo salió mal de mi lado. Inténtalo otra vez.")


# ---------------------------------------------------------------- armado

def _adaptador_memoria():
    cubeta = os.environ.get("S3_PERSISTENCE_BUCKET")
    if cubeta:
        from ask_sdk_s3.adapter import S3Adapter

        def clave(envelope):
            persona = getattr(envelope.context.system, "person", None)
            if persona is not None and getattr(persona, "person_id", None):
                return "persona/" + persona.person_id
            return "cuenta/" + envelope.context.system.user.user_id

        return S3Adapter(bucket_name=cubeta, path_prefix="asistente", object_keygen=clave)

    # Sin S3 (pruebas en local): un archivo JSON.
    from ask_sdk_core.attributes_manager import AbstractPersistenceAdapter

    class ArchivoAdapter(AbstractPersistenceAdapter):
        ruta = os.environ.get("ASISTENTE_MEMORIA_LOCAL", os.path.join(AQUI, "memoria_local.json"))

        def _todo(self):
            if os.path.exists(self.ruta):
                with open(self.ruta, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

        def _clave(self, envelope):
            persona = getattr(envelope.context.system, "person", None)
            if persona is not None and getattr(persona, "person_id", None):
                return "persona/" + persona.person_id
            return "cuenta/" + envelope.context.system.user.user_id

        def get_attributes(self, request_envelope):
            return self._todo().get(self._clave(request_envelope), {})

        def save_attributes(self, request_envelope, attributes):
            todo = self._todo()
            todo[self._clave(request_envelope)] = attributes
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump(todo, f, ensure_ascii=False, indent=2)

        def delete_attributes(self, request_envelope):
            todo = self._todo()
            todo.pop(self._clave(request_envelope), None)
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump(todo, f, ensure_ascii=False, indent=2)

    return ArchivoAdapter()


sb = CustomSkillBuilder(persistence_adapter=_adaptador_memoria(), api_client=DefaultApiClient())
for h in (LanzarHandler(), PreguntaHandler(), NombreHandler(), RecordarHandler(), QuienSoyHandler(),
          OlvidarHandler(), AyudaHandler(), SalirHandler(), FallbackHandler(), SesionTerminadaHandler()):
    sb.add_request_handler(h)
sb.add_exception_handler(ErrorHandler())

lambda_handler = sb.lambda_handler()
