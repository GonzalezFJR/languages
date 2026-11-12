# Lextor — lee en cualquier idioma, entiende cada palabra

Lextor convierte cualquier texto en una **lectura bilingüe interactiva** para aprender idiomas: el texto
original, una traducción natural de cada frase y, en cada fragmento, su glosa en contexto y una nota
didáctica (gramática, matices, falsos amigos, transliteración…).

Los textos los crea **el asistente de IA del usuario** (Claude, ChatGPT, Gemini, Cursor, VS Code…) a través
del **servidor MCP de Lextor**. Lextor le explica el formato exacto, valida el resultado con una batería
de tests, lo guarda en la biblioteca y lo muestra — dentro del propio chat cuando el cliente admite
MCP Apps, y siempre en la web.

```
Usuario ──► Asistente IA ──MCP──► Lextor ──► validación (11 tests) ──► biblioteca + lector
                 ▲                    │
                 └── widget del lector (MCP Apps) / enlace /t/<id>
```

---

## Funcionalidades

- **Lector** con tres modos: *Lectura* (ficha al pasar el ratón o tocar), *Paralelo* (columnas alineadas
  por bloque y por verso; intercalado en móvil) y *Estudio* (tarjetas con el glosario de cada frase).
- Furigana alineado por kanji, transliteración sobre el texto (pinyin, cirílico, árabe…), escritura RTL.
- Tamaño, tipografía (serif / sans / Atkinson Hyperlegible), interlineado, ancho y fondo papel ajustables;
  tamaño base fluido 18–21 px. Tema claro/oscuro/sistema. Atajos de teclado. Voz (Web Speech API).
- Resaltados y notas del usuario sobre cada fragmento.
- Biblioteca por proyectos (idioma estudiado → idioma del alumno), carpetas, etiquetas, documentos de referencia.
- Enlaces públicos no adivinables (`/t/<id>`) e incrustación por `<iframe>` (`/embed/<id>`).
- Importación de ficheros `.lextor` / `.json` y del antiguo `.xlan` (conversión automática).

## El formato `lextor/1`

JSON con bloques (`heading` / `paragraph` / `verse`) → frases → fragmentos:

```json
{
  "meta": {"title": "Le café du coin", "text_lang": "fr", "notes_lang": "es", "level": "A2"},
  "blocks": [{"type": "paragraph", "sentences": [{
    "text": "Elle ne prend jamais de sucre.",
    "translation": "Nunca toma azúcar.",
    "chunks": [
      {"text": "Elle", "gloss": "Ella"},
      {"text": "ne", "gloss": "(negación)", "group": "neg", "note": "Primera parte de «ne… jamais»."},
      {"text": "prend", "gloss": "toma", "lemma": "prendre", "grammar": "presente · 3ª sg"},
      {"text": "jamais", "gloss": "nunca", "group": "neg"},
      {"text": "de sucre.", "gloss": "azúcar."}
    ]}]}]
}
```

Reglas clave (por qué sustituye al `.xlan`):

- **El modelo nunca escribe espacios ni saltos de línea entre piezas.** Cada fragmento es una subcadena
  exacta de su frase; el espaciado se reconstruye a partir de la frase. Los saltos de línea son estructura
  (`verse` = una frase por línea; párrafo nuevo = bloque nuevo).
- **Dos traducciones**: una natural por frase (vista paralela) y una glosa contextual por fragmento (ficha).
- IDs calculados (`b{bloque}.s{frase}.c{fragmento}`), no escritos por el modelo.
- Campos opcionales estructurados: `translit`, `reading` (furigana), `lemma`, `grammar`, `literal`, `group`.
- Textos largos por partes con `lextor_append_blocks` (sin ficheros "parte").

La guía completa para modelos está en [`app/lextor/guide.md`](app/lextor/guide.md) (también en `/format`),
el validador en [`app/lextor/document.py`](app/lextor/document.py) y hay ejemplos válidos en
[`app/lextor/examples/`](app/lextor/examples/).

**Tests de validación**: `schema`, `languages`, `no_line_breaks`, `chunk_alignment`, `coverage`,
`granularity`, `translations`, `notes`, `transliteration`, `plain_text`, `groups`. Los errores bloquean el
guardado y se devuelven al modelo con la ruta JSON exacta y una pista; los problemas menores (comillas
tipográficas, mayúsculas, espacios, tuplas en vez de objetos…) se corrigen solos y se informan.

## Servidor MCP

| Endpoint | Uso |
|---|---|
| `POST /mcp/u/<token>` | URL personal del conector (se genera en `/connect` al iniciar sesión) |
| `POST /mcp` + `Authorization: Bearer <token>` | Igual, para clientes que permiten cabeceras |
| `POST /mcp` sin token | Modo anónimo: crea textos con enlace público, sin biblioteca |

- Streamable HTTP sin estado, respuestas JSON. Soporta **las dos eras del protocolo**: `initialize`
  (≤ 2025-11-25) y `server/discover` + `_meta` por petición (2026-07-28), con validación de cabeceras
  `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name`.
- Herramientas: `lextor_get_format_guide`, `lextor_validate`, `lextor_create_text`, `lextor_append_blocks`,
  `lextor_show_text`, `lextor_update_text`, `lextor_get_text`, `lextor_list_projects`, `lextor_list_texts`,
  `lextor_create_project`. Prompts: `bilingual_text`, `graded_reader`.
- **Widget MCP Apps** (`ui://lextor/reader-v3.html`, `text/html;profile=mcp-app`) con alias
  `openai/outputTemplate` para ChatGPT: el lector interactivo aparece dentro del chat en Claude (web,
  escritorio, móvil), ChatGPT, VS Code, Cursor, Goose… En clientes sin soporte, el modelo comparte el enlace.
- Las instrucciones paso a paso para cada cliente están en la propia web: **`/connect`**.

## Despliegue

```bash
cp .env.example .env        # cambia SECRET_KEY, ADMIN_PASSWORD y PUBLIC_URL
docker compose up --build -d
```

El MCP necesita ser accesible por **HTTPS público** para Claude.ai / ChatGPT (pon Lextor detrás de un proxy
con TLS y define `PUBLIC_URL`). Los datos viven en `static/contents/` (volumen) y **no** se sirven por
`/static`. Al arrancar, los `.xlan` antiguos se convierten a `.lextor` (los originales quedan en
`translates/_legacy/`) y se conservan las anotaciones del usuario.

### Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8020
pytest -q
```

## Estructura

```
app/
  lextor/        formato: languages.py, document.py (validador), legacy.py (.xlan → lextor/1),
                 guide.md + guide.py (guía para modelos), examples/
  mcp/           http.py (transporte MCP dual), tools.py (herramientas), widget.py (MCP App)
  services/      proyectos, documentos, textos (+migración y enlaces), tokens MCP, almacenamiento atómico
  routers/       páginas HTML, API REST
  templates/     landing, biblioteca, proyecto, lector, conectar, formato, embed…
static/
  css/app.css    sistema de diseño (tokens claro/oscuro, componentes)
  css/reader.css, js/reader.js   lector compartido por web, embed y widget MCP
tests/           formato, MCP (ambas eras, flujo completo) y web
```

## API REST (resumen)

| Método | Ruta |
|---|---|
| `GET/POST` | `/api/projects` |
| `PATCH/DELETE` | `/api/projects/{id}` |
| `GET` | `/api/projects/{id}/texts` · `/api/projects/{id}/texts/{file}` (`?download=1`) |
| `POST` | `/api/projects/{id}/texts/import` |
| `PATCH/DELETE` | `/api/projects/{id}/texts/{file}/meta` · `/api/projects/{id}/texts/{file}` |
| `PUT/DELETE` | `/api/projects/{id}/texts/{file}/annotations/{chunk_id}` |
| `PUT` | `/api/projects/{id}/{texts|docs}/organize/{tags|categories}` |
| `GET/POST/DELETE` | `/api/projects/{id}/docs…` |
| `POST` | `/api/validate` |
| `GET/POST` | `/api/mcp/token` · `/api/mcp/token/rotate` |

Documentación interactiva: `/api/docs`.
