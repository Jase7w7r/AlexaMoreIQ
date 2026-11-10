# -*- coding: utf-8 -*-
"""Ayudante de credenciales de git para el repositorio de la skill alojada por Amazon.

Pide a la API de Alexa credenciales temporales y las entrega a git por la salida estándar,
sin que pasen por la línea de comandos. Se usa así (desde la carpeta alexa-asistente):

    git -c credential.helper="!python herramientas/credenciales_git.py" -c credential.UseHttpPath=true clone <url> repo-amazon

Necesita ASISTENTE_SKILL_ID en el entorno, o lo lee de herramientas/skill_id.txt.
"""
import json
import os
import sys

import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import smapi  # noqa: E402


def skill_id():
    sid = os.environ.get("ASISTENTE_SKILL_ID")
    if not sid:
        with open(os.path.join(AQUI, "skill_id.txt"), "r", encoding="utf-8") as f:
            sid = f.read().strip()
    return sid


def main():
    operacion = sys.argv[1] if len(sys.argv) > 1 else "get"
    if operacion != "get":
        return
    sid = skill_id()
    meta = requests.get(smapi.API + "/v1/skills/%s/alexaHosted" % sid, headers=smapi.CAB, timeout=60).json()
    url_repo = meta["alexaHosted"]["repository"]["url"]
    r = requests.post(smapi.API + "/v1/skills/%s/alexaHosted/repository/credentials/generate" % sid,
                      headers=smapi.CAB, json={"repository": {"type": "GIT", "url": url_repo}}, timeout=60)
    r.raise_for_status()
    cred = r.json()["repositoryCredentials"]
    sys.stdout.write("username=%s\npassword=%s\n" % (cred["username"], cred["password"]))


if __name__ == "__main__":
    main()
