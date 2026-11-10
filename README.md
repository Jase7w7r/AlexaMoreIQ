# Mi asistente — skill de Alexa con un modelo de NVIDIA

Skill personalizada para un Echo Show (probada con el Show 5 de 2.ª gen, con la Alexa de fábrica).
Responde preguntas con **Nemotron 3.5 Lightning** por la API gratuita de build.nvidia.com,
**busca en internet** cuando la pregunta lo requiere y **recuerda cosas por persona** usando los
perfiles de voz de Alexa. Todo en español de México.

Se dice **«Alexa, abre mi asistente»** y luego se conversa sin repetir el nombre; o de un jalón,
**«Alexa, pregúntale a mi asistente cuánto está el dólar»**.

> ¿Por qué no se puede decir solo «Alexa, …»? Porque esa palabra es de Amazon y despierta a su
> asistente; ninguna skill de terceros puede tomar su lugar. El nombre de invocación es
> obligatorio, pero una vez dentro ya no se repite.

## Qué hay aquí

| Ruta | Qué es |
|---|---|
| `skill-package/skill.json` | Manifiesto: es-MX, pantalla (APL), permiso de perfil de voz |
| `skill-package/interactionModels/custom/es-MX.json` | Nombre de invocación, intenciones y frases en español |
| `lambda/lambda_function.py` | El backend (Python 3.8, el de la skill alojada por Amazon) |
| `lambda/apl_respuesta.json` | Plantilla de pantalla para el Show |
| `lambda/requirements.txt` | Dependencias que Amazon instala al desplegar |
| `lambda/secretos.ejemplo.json` | Plantilla de llaves; la copia real `secretos.json` no se versiona |
| `herramientas/smapi.py` | Crear la skill alojada, subir modelo y manifiesto, simular, sin pasar por la consola |
| `herramientas/credenciales_git.py` | Credenciales temporales para el repositorio git de Amazon |
| `pruebas/simular.py` | Corre la skill en local con peticiones como las de Alexa |

## Cómo decide qué hacer

1. Una llamada al modelo con una herramienta obligatoria `decidir`: si la pregunta depende de
   datos recientes devuelve una consulta de búsqueda; si no, devuelve la respuesta directa.
   Sin este paso el modelo inventaba marcadores y tipos de cambio con total seguridad.
2. Si hay que buscar: Alexa dice «Déjame buscar» (respuesta progresiva), se consulta la web y
   una segunda llamada redacta la respuesta solo con esos resultados.
3. Todo con presupuesto de 7 segundos (Alexa corta a los 8). Si se agota, pide que repitas.

Tiempos medidos: 0.5 a 1.3 s una respuesta directa, 1.5 a 2.6 s con búsqueda.

Búsqueda, en orden: **Brave** si hay llave (la única fiable para noticias, precios y deportes),
**DuckDuckGo** sin llave (bloquea a los servidores con un reto anti-bots casi siempre) y
**Wikipedia en español** (siempre responde, pero solo para hechos generales). Sin ninguna, el
modelo contesta con lo que sabe avisando que no pudo verificar.

Memoria: el nombre y las notas («recuerda que…») se guardan por `personId` cuando Alexa
reconoce la voz; si no hay perfil de voz, por cuenta. Dentro de una sesión viajan los últimos
seis turnos para poder preguntar «¿y en qué año?».

## Requisitos (una sola vez)

1. **Cuenta de desarrollador de Alexa**: <https://developer.amazon.com/alexa/console/ask>, con la
   misma cuenta de Amazon del Echo. Gratis.
2. **Verificación de skills alojadas**: la primera vez, Amazon pide resolver un CAPTCHA en
   <https://developer.amazon.com/alexa/console/ask/editor/captcha> (con la sesión iniciada; no
   abre desde navegadores automatizados). Hasta entonces la API crea las skills sin alojamiento.
3. **Llave de NVIDIA**: <https://build.nvidia.com>, gratuita.
4. **Llave de Brave Search** (opcional, recomendada): <https://api-dashboard.search.brave.com>,
   plan gratuito. Sin ella no habrá noticias ni precios fiables.
5. **Perfiles de voz** en la app de Alexa: Más › Configuración › Tu perfil y familia › Voz, para
   cada persona de la casa. Y en Configuración › Privacidad de Alexa › Administrar permisos de
   skills, activar **Personalizar skills**.
6. Para la línea de comandos: Node.js, `npm install -g ask-cli`, `ask configure` (abre el navegador
   para autorizar; al paso de AWS se responde **n**, Amazon aloja la skill sin eso) y Python 3.12
   con `uv venv .venv && uv pip install --python .venv ask-sdk-core==1.19.0 "requests>=2.31"`.

## Desplegar desde la línea de comandos

```bash
# 1. Crear la skill alojada (Python 3.8). Imprime el skillId; guárdalo:
.venv/Scripts/python herramientas/smapi.py crear skill-package/skill.json
echo amzn1.ask.skill.XXXX > herramientas/skill_id.txt

# 2. Modelo de interacción (espera a que compile y muestra errores o advertencias):
.venv/Scripts/python herramientas/smapi.py modelo amzn1.ask.skill.XXXX skill-package/interactionModels/custom/es-MX.json

# 3. Clonar el repositorio de Amazon con credenciales temporales. Hay que apagar el gestor de
#    credenciales de Windows solo para estos comandos, porque se adelanta y Amazon devuelve 403:
git -c credential.helper= -c "credential.helper=!.venv/Scripts/python herramientas/credenciales_git.py" -c credential.UseHttpPath=true clone https://git-codecommit.us-east-1.amazonaws.com/v1/repos/<uuid-de-la-skill> repo-amazon
```

La URL del repositorio la da `herramientas/smapi.py alojada <skillId>`. Luego copia a
`repo-amazon/lambda/` los archivos de `lambda/` (incluido tu `secretos.json`), borra el
`utils.py` de la plantilla, haz commit y `git push origin master` con las mismas opciones `-c`
del clone. Cada push a `master` despliega la etapa de desarrollo; `smapi.py estado <skillId>`
muestra `hostedSkillDeployment` hasta que diga `SUCCEEDED`.

Para que aparezca en los dispositivos de la cuenta hay que habilitarla una vez:
`PUT /v1/skills/<skillId>/stages/development/enablement` (en `smapi.py` basta añadir la llamada,
o hacerlo desde la pestaña Test de la consola, cambiando a **Development**).

Probar sin tocar el Echo:

```bash
.venv/Scripts/python herramientas/smapi.py simular amzn1.ask.skill.XXXX "abre mi asistente"
.venv/Scripts/python herramientas/smapi.py simular amzn1.ask.skill.XXXX "pregúntale a mi asistente cuánto está el dólar"
```

### Las llaves

`lambda/secretos.json` no se versiona. Cópialo de `secretos.ejemplo.json`:

```json
{ "NVIDIA_API_KEY": "nvapi-...", "BRAVE_API_KEY": "", "LUGAR": "Tu ciudad, México" }
```

`LUGAR` solo sirve para que el modelo responda con contexto local. Si prefieres no dejar la llave
en un repositorio, aunque sea el privado de Amazon, créala directamente en la consola: pestaña
**Code**, *Create file*, `lambda/secretos.json`, pega el contenido y **Deploy**.

## Desplegar desde la consola web (sin línea de comandos)

1. **Create Skill** → nombre `Mi asistente`, idioma **Español (MX)**, tipo **Custom**,
   alojamiento **Alexa-hosted (Python)**, plantilla **Start from Scratch**.
2. **Build** → **Interaction Model** → **JSON Editor**: pega `es-MX.json` → **Save** → **Build**.
3. **Build** → **Interfaces**: activa **Alexa Presentation Language**.
   **Build** → **Permissions**: activa **Skill Personalization**.
4. **Code**: sustituye `lambda_function.py` y `requirements.txt`, crea `apl_respuesta.json` y
   `secretos.json` → **Save** → **Deploy**.
5. **Test**: cambia a **Development** y escribe `abre mi asistente`.

## Probar en local

Con `NVIDIA_API_KEY` en el entorno (o `lambda/secretos.json`):

```bash
.venv/Scripts/python -X utf8 -W ignore pruebas/simular.py
```

Simula a dos personas con perfil de voz y una sin él, con memoria en `pruebas/memoria_local.json`.
Para una sola pregunta: `.venv/Scripts/python -X utf8 -W ignore pruebas/simular.py "¿va a llover mañana?"`.
Las advertencias `SyntaxWarning` vienen del SDK de Amazon con Python 3.12; en el Lambda (3.8) no salen.

## Ajustes

- **Nombre de invocación**: `invocationName` en `es-MX.json`. Dos palabras o más, minúsculas,
  sin «Alexa» ni marcas.
- **Modelo**: `MODELO` y `MODELO_RESPALDO` al inicio de `lambda_function.py`. Debe ser rápido y
  aceptar `chat_template_kwargs.enable_thinking=false`; los que razonan antes de contestar
  (Nemotron 3 Ultra) se pasan de los 8 segundos y devuelven solo razonamiento.
- **Largo de voz**: `MAX_VOZ` (700 caracteres). **Turnos en sesión**: `TURNOS_EN_SESION` (6).

## Límites conocidos

- Alexa manda el `personId` solo si la persona tiene perfil de voz y la personalización de
  skills está activa; si no, todos comparten la memoria de la cuenta.
- Sin llave de Brave, las preguntas de noticias, precios y deportes contestarán «no pude
  consultarlo» la mayoría de las veces.
- La pregunta llega como texto reconocido por Alexa; nombres propios raros o términos técnicos
  pueden llegar mal transcritos («cien» puede llegar como «100»).
- Alarmas, temporizadores, música y domótica se piden a Alexa directamente; la skill lo recuerda.
- Nada de esto pasa por certificación de Amazon: es una skill de desarrollo para los dispositivos
  de la misma cuenta.

## Licencia

MIT. Véase `LICENSE`.
