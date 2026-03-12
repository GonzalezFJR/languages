# Xlan — Plataforma de Aprendizaje de Idiomas

Plataforma web para aprender idiomas mediante lectura interactiva. Los textos se almacenan en formato `.xlan` (JSON estructurado) y permiten hacer *hover* sobre cada segmento para ver su traducción y explicación gramatical.

---

## Características

- **Landing page** interactiva con demostración del visor
- **Gestión de proyectos**: nombre, idioma base e idioma objetivo
- **Documentación**: sube PDFs, TXT, DOCX y visualízalos en el lector integrado
- **Textos traducidos `.xlan`**: visor interactivo con tooltip al pasar el ratón
- **Pipeline de traducción**: convierte texto plano a borrador `.xlan`
- **Organización por categorías anidadas** configurable
- **Sidebar** de navegación redimensionable
- **Zoom** con `Ctrl +/-` / `Ctrl + rueda` y navegación por teclado

---

## Formato `.xlan`

Archivo JSON con la siguiente estructura:

```json
{
  "meta": {
    "title": "Título del texto",
    "description": "Descripción breve",
    "source_language": "es",
    "target_language": "en",
    "created_at": "2026-01-01T00:00:00"
  },
  "content": [
    {
      "type": "heading",
      "level": 1,
      "segments": [...]
    },
    {
      "type": "paragraph",
      "segments": [
        {
          "id": "seg_1",
          "text": "Había una vez",
          "translation": "Once upon a time",
          "info": "Fórmula de inicio de cuento...",
          "styles": []
        }
      ]
    }
  ]
}
```

**Tipos de bloque**: `heading` (con `level` 1–3), `paragraph`  
**Estilos de segmento** (`styles`): `bold`, `italic`, `underline`

---

## Tecnologías

| Capa | Stack |
|------|-------|
| Backend | Python 3.11 · FastAPI · Uvicorn |
| Plantillas | Jinja2 |
| Frontend | HTML · TailwindCSS (CDN) · Vanilla JS |
| Almacenamiento | Sistema de ficheros local (`static/contents/`) |
| Contenedores | Docker · Docker Compose |

---

## Despliegue

### Opción 1 — Docker Compose (recomendado)

```bash
# 1. Clonar el repositorio
git clone <repo-url>
cd languages

# 2. Crear el fichero de entorno
cp .env.example .env
# Edita .env si necesitas cambiar el puerto u otras variables

# 3. Arrancar
docker compose up --build -d

# La aplicación estará disponible en http://localhost:8000
```

Para detenerla:
```bash
docker compose down
```

Los archivos de usuario se persisten en `static/contents/` (montado como volumen).

---

### Opción 2 — Entorno local (desarrollo)

**Requisitos**: Python 3.11+

```bash
# 1. Crear entorno virtual
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env

# 4. Arrancar el servidor
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Accede a `http://localhost:8000`.

---

## Variables de entorno (`.env`)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `APP_NAME` | `Xlan Language Platform` | Nombre de la app |
| `APP_ENV` | `development` | Entorno (`development` / `production`) |
| `DEBUG` | `true` | Modo debug de FastAPI |
| `SECRET_KEY` | `change-me-in-production` | Clave secreta (cambiar en producción) |
| `HOST` | `0.0.0.0` | Host de escucha |
| `PORT` | `8000` | Puerto de escucha |
| `CONTENTS_DIR` | `static/contents` | Ruta al directorio de proyectos |

---

## Proyecto de ejemplo incluido

El repositorio incluye el proyecto **"Inglés — Ejemplo"** (`static/contents/ejemplo_ingles/`) listo para usar:

| Tipo | Archivo | Descripción |
|------|---------|-------------|
| Doc | `verbos_irregulares.txt` | 50 verbos irregulares en inglés |
| Doc | `tiempos_verbales.txt` | Guía comparativa de tiempos verbales |
| `.xlan` | `el_principito_cap1.xlan` | El Principito cap. I — anotado (B1-B2) |
| `.xlan` | `la_cenicientay_el_lobo.xlan` | Caperucita Roja — anotado (A2-B1) |

---

## Estructura del proyecto

```
languages/
├── app/
│   ├── main.py                  # Entrada FastAPI
│   ├── config.py                # Settings desde .env
│   ├── routers/
│   │   ├── pages.py             # Rutas HTML (Jinja2)
│   │   ├── api_projects.py      # API REST proyectos
│   │   ├── api_documents.py     # API REST documentos
│   │   └── api_pipeline.py      # Pipeline texto → .xlan
│   ├── services/
│   │   ├── project_service.py   # Lógica de proyectos
│   │   ├── document_service.py  # Lógica de documentos
│   │   └── xlan_service.py      # Lógica .xlan + pipeline
│   └── templates/
│       ├── base.html
│       ├── landing.html
│       ├── home.html
│       ├── project.html
│       └── viewer.html
├── static/
│   ├── css/app.css
│   └── contents/                # Datos de usuario (git-ignored, salvo ejemplo)
│       └── ejemplo_ingles/
│           ├── metadata.json
│           ├── docs/
│           └── translates/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## API REST (resumen)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/projects` | Listar proyectos |
| `POST` | `/api/projects` | Crear proyecto |
| `GET` | `/api/projects/{id}` | Obtener proyecto |
| `PATCH` | `/api/projects/{id}` | Actualizar metadata |
| `DELETE` | `/api/projects/{id}` | Eliminar proyecto |
| `GET` | `/api/projects/{id}/docs` | Listar documentos |
| `POST` | `/api/projects/{id}/docs` | Subir documento |
| `GET` | `/api/projects/{id}/docs/file/{name}` | Servir archivo |
| `DELETE` | `/api/projects/{id}/docs/{name}` | Eliminar documento |
| `PUT` | `/api/projects/{id}/docs/metadata` | Actualizar estructura docs |
| `GET` | `/api/projects/{id}/translates` | Listar .xlan |
| `DELETE` | `/api/projects/{id}/translates/{name}` | Eliminar .xlan |
| `PUT` | `/api/projects/{id}/translates/metadata` | Actualizar estructura xlan |
| `POST` | `/api/projects/{id}/pipeline/translate` | Procesar texto → .xlan |

La documentación interactiva de la API está disponible en `http://localhost:8000/docs`.
