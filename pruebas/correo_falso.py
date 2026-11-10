# -*- coding: utf-8 -*-
"""Prueba del resumen de correo sin tocar Gmail ni el modelo.

Sustituye el servidor IMAP por uno falso que devuelve respuestas con el mismo formato que Gmail
(cabeceras codificadas, multipart con HTML, mensajes no leídos) y usa el modo CORREOS_SIN_MODELO
para no necesitar llave. Sirve para ver que el análisis de los mensajes y la intención funcionan.

    .venv/Scripts/python -X utf8 -W ignore pruebas/correo_falso.py
"""
import datetime
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ASISTENTE_MEMORIA_LOCAL", os.path.join(AQUI, "memoria_local.json"))
sys.path.insert(0, os.path.join(os.path.dirname(AQUI), "lambda"))

import lambda_function as L  # noqa: E402
import simular  # noqa: E402

HOY = datetime.datetime.now().strftime("%a, %d %b %Y 10:15:00 -0600")

CORREO_1 = (
    b"From: =?UTF-8?Q?Mar=C3=ADa_L=C3=B3pez?= <maria@uaq.mx>\r\n"
    b"Subject: =?UTF-8?Q?Fecha_de_la_defensa?=\r\n"
    b"Date: " + HOY.encode() + b"\r\n"
    b"Content-Type: multipart/alternative; boundary=\"XYZ\"\r\n\r\n"
)
CUERPO_1 = (
    b"--XYZ\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"
    b"Hola, la defensa queda el 20 de noviembre a las 11. Lleva impresas tres copias.\r\n\r\n"
    b"> texto citado que no debe salir\r\n"
    b"--XYZ\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n<p>Hola, la defensa queda...</p>\r\n--XYZ--\r\n"
)
CORREO_2 = (
    b"From: Tienda Online <promos@tienda.com>\r\n"
    b"Subject: 50% de descuento solo hoy\r\n"
    b"Date: " + HOY.encode() + b"\r\n"
    b"Content-Type: text/html; charset=UTF-8\r\n\r\n"
)
CUERPO_2 = b"<html><body><style>p{color:red}</style><h1>Gran venta</h1><p>Aprovecha <a href='http://x'>aqui</a> antes de medianoche.</p></body></html>"
CORREO_3 = (
    b"From: cliente@ferreteria.mx\r\n"
    b"Subject: Cotizacion de bisagras\r\n"
    b"Date: " + HOY.encode() + b"\r\n"
    b"Content-Type: text/plain; charset=UTF-8\r\n\r\n"
)
CUERPO_3 = b"Buen dia, necesito precio de 200 bisagras de 3 pulgadas para el viernes. Saludos."


class IMAPFalso(object):
    def __init__(self, host, port):
        self.host = host

    def login(self, u, p):
        if p == "mala":
            raise Exception("[AUTHENTICATIONFAILED] Invalid credentials")

    def select(self, caja, readonly=True):
        return "OK", [b"3"]

    def search(self, charset, criterio):
        assert "SINCE" in criterio, criterio
        return "OK", [b"1 2 3" if "UNSEEN" not in criterio else b"1 3"]

    def fetch(self, ids, partes):
        return "OK", [
            (b"1 (FLAGS () BODY[HEADER.FIELDS (FROM SUBJECT DATE CONTENT-TYPE CONTENT-TRANSFER-ENCODING)] {%d}" % len(CORREO_1), CORREO_1),
            (b" BODY[TEXT]<0> {%d}" % len(CUERPO_1), CUERPO_1), b")",
            (b"2 (FLAGS (\\Seen) BODY[HEADER.FIELDS (FROM SUBJECT DATE CONTENT-TYPE CONTENT-TRANSFER-ENCODING)] {%d}" % len(CORREO_2), CORREO_2),
            (b" BODY[TEXT]<0> {%d}" % len(CUERPO_2), CUERPO_2), b")",
            (b"3 (FLAGS () BODY[HEADER.FIELDS (FROM SUBJECT DATE CONTENT-TYPE CONTENT-TRANSFER-ENCODING)] {%d}" % len(CORREO_3), CORREO_3),
            (b" BODY[TEXT]<0> {%d}" % len(CUERPO_3), CUERPO_3), b")",
        ]

    def logout(self):
        pass


def main():
    L.imaplib.IMAP4_SSL = IMAPFalso
    L.CORREOS = {"Ana": [{"usuario": "ana@gmail.com", "clave_app": "abcd efgh ijkl mnop", "etiqueta": "personal"},
                         {"usuario": "ana.trabajo@gmail.com", "clave_app": "mala", "etiqueta": "trabajo"}]}
    L.CORREOS_SIN_MODELO = True

    print("== análisis de una cuenta")
    msgs = L.leer_cuenta(L.CORREOS["Ana"][0], datetime.date.today(), False, 3.0)
    for m in msgs:
        print(" -", m["fecha"], "|", m["de"], "|", m["asunto"], "| leído" if m["leido"] else "| NO LEÍDO", "|", m["fragmento"][:90])
    assert len(msgs) == 3
    assert msgs[0]["de"] == "María López" and msgs[0]["asunto"] == "Fecha de la defensa"
    assert "defensa queda el 20" in msgs[0]["fragmento"] and "citado" not in msgs[0]["fragmento"]
    assert "Gran venta" in msgs[1]["fragmento"] and "color" not in msgs[1]["fragmento"] and msgs[1]["leido"]
    assert not msgs[2]["leido"]

    print("== varias cuentas, una con contraseña mala")
    todos = L.leer_correos(L.CORREOS["Ana"], datetime.date.today(), False, L.Reloj())
    print(" -", len([x for x in todos if not x.get("error")]), "mensajes,", [x["cuenta"] for x in todos if x.get("error")], "con error")
    assert any(x.get("error") for x in todos)

    print("== intención por Alexa (sin modelo)")
    if os.path.exists(os.environ["ASISTENTE_MEMORIA_LOCAL"]):
        os.remove(os.environ["ASISTENTE_MEMORIA_LOCAL"])
    s = {"id": "m1", "attrs": {}}
    simular.hablar(s, None, "CorreoIntent")                                   # sin perfil de voz
    simular.hablar(s, simular.PERSONA_A, "CorreoIntent")                      # sin nombre
    simular.hablar(s, simular.PERSONA_A, "NombreIntent", {"nombre": "ana"})
    simular.hablar(s, simular.PERSONA_A, "CorreoIntent")                      # todas las cuentas
    simular.hablar(s, simular.PERSONA_A, "CorreoIntent", {"filtro": "no leídos", "cuenta": "personal"})
    simular.hablar(s, simular.PERSONA_B, "NombreIntent", {"nombre": "beto"})
    simular.hablar(s, simular.PERSONA_B, "CorreoIntent")                      # sin cuentas configuradas
    print("todo bien")


if __name__ == "__main__":
    main()
