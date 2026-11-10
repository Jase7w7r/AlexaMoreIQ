# -*- coding: utf-8 -*-
"""Invoca la skill en local con peticiones como las que manda Alexa.

Uso (desde la carpeta alexa-asistente):
    uv run --with ask-sdk-core==1.19.0 --with ask-sdk-s3-persistence-adapter==1.0.0 --with requests \
        python pruebas/simular.py                # conversación de prueba completa
    ... python pruebas/simular.py "¿cuánto está el dólar?"   # una sola pregunta

Necesita NVIDIA_API_KEY en el entorno (o lambda/secretos.json). Guarda la memoria en
pruebas/memoria_local.json para no tocar nada de Amazon.
"""
import json
import os
import sys
import time
import uuid

AQUI = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ASISTENTE_MEMORIA_LOCAL", os.path.join(AQUI, "memoria_local.json"))
sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "lambda"))

import lambda_function  # noqa: E402

USUARIO = "amzn1.ask.account.PRUEBA"
PERSONA_A = "amzn1.ask.person.ANA"
PERSONA_B = "amzn1.ask.person.BETO"


def sobre(tipo, sesion, persona=None, intent=None, slots=None, nueva=False):
    sistema = {
        "application": {"applicationId": "amzn1.ask.skill.prueba"},
        "user": {"userId": USUARIO},
        "device": {"deviceId": "prueba", "supportedInterfaces": {"Alexa.Presentation.APL": {}}},
        "apiEndpoint": "https://api.amazonalexa.com",
        "apiAccessToken": "token-falso",
    }
    if persona:
        sistema["person"] = {"personId": persona}
    peticion = {"type": tipo, "requestId": "amzn1.echo-api.request." + uuid.uuid4().hex,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "locale": "es-MX"}
    if intent:
        peticion["intent"] = {"name": intent, "confirmationStatus": "NONE",
                              "slots": {k: {"name": k, "value": v, "confirmationStatus": "NONE"} for k, v in (slots or {}).items()}}
    return {
        "version": "1.0",
        "session": {"new": nueva, "sessionId": "amzn1.echo-api.session." + sesion["id"],
                    "application": sistema["application"], "user": sistema["user"], "attributes": sesion["attrs"]},
        "context": {"System": sistema},
        "request": peticion,
    }


def hablar(sesion, persona, intent=None, slots=None, tipo="IntentRequest", nueva=False):
    env = sobre(tipo, sesion, persona, intent, slots, nueva)
    t = time.time()
    resp = lambda_function.lambda_handler(env, None)
    dt = time.time() - t
    sesion["attrs"] = resp.get("sessionAttributes") or {}
    voz = (resp.get("response", {}).get("outputSpeech") or {}).get("ssml", "")
    voz = voz.replace("<speak>", "").replace("</speak>", "")
    fin = resp.get("response", {}).get("shouldEndSession")
    apl = any(d.get("type") == "Alexa.Presentation.APL.RenderDocument" for d in resp.get("response", {}).get("directives", []))
    etiqueta = intent or tipo
    if slots:
        etiqueta += " " + json.dumps(slots, ensure_ascii=False)
    print("[%5.2fs]%s %s\n        -> %s%s" % (dt, " APL" if apl else "    ", etiqueta, voz, "  [FIN]" if fin else ""))
    return voz


def conversacion():
    if os.path.exists(os.environ["ASISTENTE_MEMORIA_LOCAL"]):
        os.remove(os.environ["ASISTENTE_MEMORIA_LOCAL"])
    print("== Ana, primera vez ==")
    s = {"id": "a1", "attrs": {}}
    hablar(s, PERSONA_A, tipo="LaunchRequest", nueva=True)
    hablar(s, PERSONA_A, "NombreIntent", {"nombre": "ana"})
    hablar(s, PERSONA_A, "PreguntaIntent", {"frase": "quién escribió cien años de soledad"})
    hablar(s, PERSONA_A, "PreguntaIntent", {"frase": "y en qué año lo publicó"})
    hablar(s, PERSONA_A, "PreguntaIntent", {"frase": "cuánto está el dólar hoy"})
    hablar(s, PERSONA_A, "RecordarIntent", {"frase": "el martes tengo dentista a las cinco"})
    hablar(s, PERSONA_A, "AMAZON.StopIntent")

    print("\n== Beto, otra voz, misma cuenta ==")
    s = {"id": "b1", "attrs": {}}
    hablar(s, PERSONA_B, tipo="LaunchRequest", nueva=True)
    hablar(s, PERSONA_B, "QuienSoyIntent")
    hablar(s, PERSONA_B, "PreguntaIntent", {"frase": "cómo quedó el partido del querétaro"})
    hablar(s, PERSONA_B, "AMAZON.HelpIntent")

    print("\n== Ana regresa ==")
    s = {"id": "a2", "attrs": {}}
    hablar(s, PERSONA_A, tipo="LaunchRequest", nueva=True)
    hablar(s, PERSONA_A, "QuienSoyIntent")
    hablar(s, PERSONA_A, "PreguntaIntent", {"frase": "qué tengo el martes"})
    hablar(s, PERSONA_A, "AMAZON.FallbackIntent")

    print("\n== Sin perfil de voz ==")
    s = {"id": "c1", "attrs": {}}
    hablar(s, None, "NombreIntent", {"nombre": "carlos"})
    hablar(s, None, "OlvidarIntent")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        hablar({"id": "x", "attrs": {}}, PERSONA_A, "PreguntaIntent", {"frase": " ".join(sys.argv[1:])})
    else:
        conversacion()
