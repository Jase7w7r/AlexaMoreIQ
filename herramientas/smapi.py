# -*- coding: utf-8 -*-
"""Llamadas directas a la API de Alexa (SMAPI) con el token que guardó `ask configure`.

Sirve para lo que el ASK CLI no expone como comando: crear una skill alojada por Amazon.
Corre antes cualquier `ask smapi ...` para que el CLI refresque el token.

    python herramientas/smapi.py crear skill-package/skill.json      # crea la skill alojada (Python 3.8)
    python herramientas/smapi.py estado <skillId>                     # estado de aprovisionamiento
    python herramientas/smapi.py alojada <skillId>                    # datos del repositorio git
    python herramientas/smapi.py modelo <skillId> skill-package/interactionModels/custom/es-MX.json
    python herramientas/smapi.py manifiesto <skillId> skill-package/skill.json
    python herramientas/smapi.py simular <skillId> "abre mi asistente"
"""
import json
import os
import sys
import time

import requests

API = "https://api.amazonalexa.com"


def token_y_vendor():
    ruta = os.path.join(os.path.expanduser("~"), ".ask", "cli_config")
    with open(ruta, "r", encoding="utf-8") as f:
        perfil = json.load(f)["profiles"]["default"]
    return perfil["token"]["access_token"], perfil["vendor_id"]


TOKEN, VENDOR = token_y_vendor()
CAB = {"Authorization": TOKEN, "Content-Type": "application/json", "Accept": "application/json"}


def llamar(metodo, ruta, cuerpo=None, ok=(200, 201, 202, 204)):
    r = requests.request(metodo, API + ruta, headers=CAB, json=cuerpo, timeout=60)
    if r.status_code not in ok:
        print("HTTP", r.status_code, r.text[:1500])
        sys.exit(1)
    try:
        return r.status_code, r.headers, (r.json() if r.text else {})
    except ValueError:
        return r.status_code, r.headers, {"texto": r.text}


def crear(ruta_manifiesto):
    with open(ruta_manifiesto, "r", encoding="utf-8") as f:
        manifiesto = json.load(f)["manifest"]
    cuerpo = {
        "vendorId": VENDOR,
        "manifest": manifiesto,
        "hosting": {"alexaHosted": {"runtime": "PYTHON_3_8", "region": "US_EAST_1"}},
    }
    codigo, cab, datos = llamar("POST", "/v1/skills", cuerpo)
    print(json.dumps(datos, ensure_ascii=False, indent=2))
    return datos.get("skillId")


def estado(skill_id):
    _, _, datos = llamar("GET", "/v1/skills/%s/status" % skill_id)
    print(json.dumps(datos, ensure_ascii=False, indent=2))
    return datos


def alojada(skill_id):
    _, _, datos = llamar("GET", "/v1/skills/%s/alexaHosted" % skill_id)
    print(json.dumps(datos, ensure_ascii=False, indent=2))
    return datos


def modelo(skill_id, ruta_modelo):
    with open(ruta_modelo, "r", encoding="utf-8") as f:
        cuerpo = json.load(f)
    codigo, cab, datos = llamar("PUT", "/v1/skills/%s/stages/development/interactionModel/locales/es-MX" % skill_id, cuerpo)
    print("modelo enviado:", codigo, datos or "")
    # Esperar a que compile
    for _ in range(60):
        time.sleep(5)
        _, _, est = llamar("GET", "/v1/skills/%s/status?resource=interactionModel" % skill_id)
        im = est.get("interactionModel", {}).get("es-MX", {}).get("lastUpdateRequest", {})
        print("  estado modelo:", im.get("status"), (im.get("errors") or im.get("warnings") or ""))
        if im.get("status") in ("SUCCEEDED", "FAILED"):
            if im.get("errors"):
                print(json.dumps(im["errors"], ensure_ascii=False, indent=2))
            if im.get("warnings"):
                print(json.dumps(im["warnings"], ensure_ascii=False, indent=2))
            break


def manifiesto(skill_id, ruta_manifiesto):
    with open(ruta_manifiesto, "r", encoding="utf-8") as f:
        cuerpo = json.load(f)
    codigo, cab, datos = llamar("PUT", "/v1/skills/%s/stages/development/manifest" % skill_id, cuerpo)
    print("manifiesto enviado:", codigo, datos or "")
    for _ in range(30):
        time.sleep(4)
        _, _, est = llamar("GET", "/v1/skills/%s/status?resource=manifest" % skill_id)
        m = est.get("manifest", {}).get("lastUpdateRequest", {})
        print("  estado manifiesto:", m.get("status"), m.get("errors") or "")
        if m.get("status") in ("SUCCEEDED", "FAILED"):
            break


def simular(skill_id, frase, sesion_nueva=True):
    cuerpo = {"input": {"content": frase}, "device": {"locale": "es-MX"},
              "session": {"mode": "FORCE_NEW_SESSION" if sesion_nueva else "DEFAULT"}}
    _, _, datos = llamar("POST", "/v2/skills/%s/stages/development/simulations" % skill_id, cuerpo)
    sim_id = datos["id"]
    for _ in range(30):
        time.sleep(2)
        _, _, est = llamar("GET", "/v2/skills/%s/stages/development/simulations/%s" % (skill_id, sim_id))
        if est.get("status") != "IN_PROGRESS":
            break
    res = est.get("result", {})
    if est.get("status") != "SUCCESSFUL":
        print(json.dumps(est, ensure_ascii=False, indent=2)[:3000])
        return
    inv = res.get("skillExecutionInfo", {}).get("invocations", [])
    for i in inv:
        req = i.get("invocationRequest", {}).get("body", {}).get("request", {})
        print("  intención:", req.get("intent", {}).get("name") or req.get("type"),
              json.dumps({k: v.get("value") for k, v in (req.get("intent", {}).get("slots") or {}).items()}, ensure_ascii=False))
        resp = i.get("invocationResponse", {}).get("body", {}).get("response", {})
        voz = (resp.get("outputSpeech") or {}).get("ssml") or (resp.get("outputSpeech") or {}).get("text")
        print("  respuesta:", voz)
    alexa = res.get("alexaExecutionInfo", {}).get("alexaResponses", [])
    for a in alexa:
        print("  alexa dijo:", a.get("content", {}).get("caption"))


if __name__ == "__main__":
    orden = sys.argv[1]
    if orden == "crear":
        crear(sys.argv[2])
    elif orden == "estado":
        estado(sys.argv[2])
    elif orden == "alojada":
        alojada(sys.argv[2])
    elif orden == "modelo":
        modelo(sys.argv[2], sys.argv[3])
    elif orden == "manifiesto":
        manifiesto(sys.argv[2], sys.argv[3])
    elif orden == "simular":
        simular(sys.argv[2], " ".join(sys.argv[3:]), sesion_nueva=os.environ.get("SESION_NUEVA", "1") == "1")
