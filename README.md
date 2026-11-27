# Text to Speech

> 🌐 **Languages / Idiomas:** [English](#english-version) | [Español](#versión-en-español)

---

## English Version

### 1. Project Overview

**Text to Speech (v2)** is a full-stack web application and CLI tool built with **Python (FastAPI)** and **React + Vite** that converts online web articles into playable and downloadable MP3 audio files using neural speech synthesis. By providing the URL of an article, the application downloads and extracts its main text content safely, processes it using natural language processing techniques, and synthesizes it into spoken audio using your choice of **two speech synthesis engines**:

- **Edge TTS (`edge`):** Free, key-less neural speech synthesis powered by Microsoft Edge TTS.
- **ElevenLabs (`elevenlabs`):** Expressive multilingual neural synthesis powered by the official ElevenLabs REST API using the **`eleven_v4`** model (`mp3_44100_128`).

---

### 2. Project Objectives

- Automate the safe extraction of clean, readable text from online articles via their URL (with SSRF protection, DNS pinning, and size limits).
- Process and prepare extracted text for natural-sounding speech synthesis.
- Support two complementary neural TTS engines (**Edge TTS** and **ElevenLabs `eleven_v4`**) with dynamic selection of **Engine**, **Language**, and **Voice**.
- Guarantee atomic MP3 generation and race-free temporary file cleanup even on errors or client disconnections.

---

### 3. Main Features & Speech Engines

| Feature | Description | Status |
| :--- | :--- | :--- |
| **Web Article Extraction** | Download and parse the title and main body of a web article from a public HTTP/HTTPS URL (up to 5 MB HTML and 15,000 characters). | Implemented |
| **Dual Neural TTS Engines** | Choose between **Edge TTS** (free alternative) and **ElevenLabs** (`eleven_v4` REST API). | Implemented |
| **Dynamic Engine, Language & Voice Selection** | Three synchronized selectors in the React UI (**Engine → Language → Voice**) backed by `GET /api/languages`. | Implemented |
| **Safe MP3 Generation & Temp Cleanup** | Atomic temporary file writing and `SafeFileResponse` streaming with POSIX `fcntl.flock` locks. | Implemented |
| **Graceful Error Handling & Manual Fallback** | Specific error detection for ElevenLabs (missing/invalid API key, quota exhaustion, rate limits, timeouts) with an option to manually switch to Edge TTS while preserving previous results. | Implemented |

#### Comparison: Edge TTS vs. ElevenLabs (`eleven_v4`)

| Aspect | Edge TTS (`edge`) | ElevenLabs (`elevenlabs`) |
| :--- | :--- | :--- |
| **Cost & Quota** | Free, no API key or account required. | Limited Free Tier (**10,000 monthly credits**); each conversion consumes credits based on text length. |
| **Model** | Microsoft Edge Neural TTS | **`eleven_v4`** (supports up to 100,000 chars per request; project limit kept at 15,000 chars). |
| **Audio Format** | MP3 (`audio/mpeg`) | MP3 `mp3_44100_128` (44.1 kHz, 128 kbps). |
| **Supported Languages** | Spanish (`es`), English (`en`), French (`fr`) | Spanish (`es`), English (`en`), French (`fr`) via `language_code`. |
| **Available Voices** | Language-specific neural voices (`ElviraNeural`, `AlvaroNeural`, `AriaNeural`, `GuyNeural`, `DeniseNeural`, `HenriNeural`). | Official multilingual premade voices (`Sarah`, `Alice`, `Laura`, `Matilda`, `Roger`, `George`, `Liam`, `Charlie`), verified dynamically by the backend. |

#### ElevenLabs Free Quota Notice

When **ElevenLabs** is selected in the web interface, a visible notice informs the user:
> *"ElevenLabs uses a limited free quota of 10,000 monthly credits. Each conversion consumes credits according to the length of the text. When the quota is exhausted, you can continue using Edge TTS at no cost."*

---

### 4. Technologies and Tools

| Technology / Library | Role in the Project |
| :--- | :--- |
| **Python 3.9+ / 3.11** | Backend programming language and runtime environment. |
| **FastAPI & Uvicorn** | REST API (`GET /api/health`, `GET /api/languages`, `POST /api/tts`) and ASGI server. |
| **React 18 & Vite 8** | Responsive frontend user interface with pure CSS and dynamic selectors. |
| **newspaper3k & NLTK** | Article HTML parsing and natural language tokenization (`punkt`, `punkt_tab`). |
| **edge-tts & aiohttp** | Microsoft Edge neural text-to-speech engine. |
| **requests** | HTTP client used for SSRF-protected article downloads and official ElevenLabs REST API calls. |

---

### 5. Project Structure

```text
text-to-speech/
├── backend/
│   ├── main.py           # FastAPI application, endpoints, CORS, and temp file lifecycle
│   ├── schemas.py        # Pydantic models for requests, engines, languages, and voices
│   └── security.py       # SSRF exports, 15,000-char limit, IP resolution, and rate limiting
├── frontend/
│   ├── src/
│   │   ├── components/   # Header, HeroSection, ConversionForm, StatusBanner, AudioResultCard
│   │   ├── App.jsx       # State management, dynamic catalog synchronization, and API calls
│   │   ├── index.css     # Pure CSS responsive styles
│   │   └── main.jsx      # React entry point
│   ├── package.json
│   └── vite.config.js    # Vite configuration and local /api proxy
├── src/
│   ├── extractor.py      # SSRF-safe article downloading and text extraction
│   ├── converter.py      # Dual-engine TTS conversion (Edge TTS + ElevenLabs eleven_v4)
│   └── main.py           # Interactive CLI entry point
├── tests/
│   ├── test_temp_lifecycle.py   # 14 tests for temp file lifecycle, disconnect cleanup, and ports
│   └── test_dual_engine_tts.py  # 10 tests for Edge TTS + ElevenLabs integration and error handling
├── .env.example          # Backend environment variable template
├── render.yaml           # Render deployment blueprint
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation (EN / ES)
```

---

### 6. Installation, API Key Configuration & Local Setup

1. **Clone the repository and create a virtual environment:**

   ```bash
   git clone https://github.com/gemaespada/text-to-speech.git
   cd text-to-speech
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure `ELEVENLABS_API_KEY` locally (`.env`):**

   The project includes a local `.env` file (ignored by Git via `.gitignore`) derived from `.env.example`. To enable ElevenLabs locally, open `.env` and set your private key:

   ```dotenv
   ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
   ```

   - **Security rule:** Never hardcode `ELEVENLABS_API_KEY` in source files, frontend code, or commits.
   - If `ELEVENLABS_API_KEY` is left empty, the application starts normally and **Edge TTS** remains fully functional.

3. **Run the FastAPI backend and React frontend locally:**

   - **Backend (Terminal 1):**
     ```bash
     ./.venv/bin/python -m uvicorn backend.main:app --reload --port 8000
     ```
   - **Frontend (Terminal 2):**
     ```bash
     cd frontend
     npm install
     npm run dev
     ```
   - Open `http://localhost:5173/` in your browser.

---

### 7. Deployment Configuration (Render & Cloudflare Pages)

- **Render (Backend):**
  - `render.yaml` declares `ELEVENLABS_API_KEY` with `sync: false` so the secret is never stored in the repository.
  - In the Render Dashboard (**Service → Environment**), add the secret value for `ELEVENLABS_API_KEY` and include your Cloudflare Pages domain in `FRONTEND_ORIGINS`.
- **Cloudflare Pages (Frontend):**
  - Set `VITE_API_BASE_URL` to your Render backend URL (e.g., `https://text-to-speech-api-yfia.onrender.com`).

---

### 8. Known Limitations

1. **Article Size Limits:** For security and cost control, raw HTML downloads are capped at **5 MB** (`MAX_HTML_BYTES`) and extracted article text is capped at **15,000 characters** (`MAX_ARTICLE_CHARS`).
2. **ElevenLabs Free Tier Quota:** The ElevenLabs Free plan provides 10,000 monthly credits (~10,000 characters). Long articles can quickly consume the monthly free quota; when exhausted, users can switch to **Edge TTS** with one click.
3. **ElevenLabs Free Tier on Cloud Datacenter IPs:** ElevenLabs may restrict Free Tier API requests originating from shared cloud hosting IPs (such as Render) with an `unusual_activity` / `401`/`403` response. Using ElevenLabs from a cloud server typically requires a paid ElevenLabs plan, while **Edge TTS** works without restrictions.
4. **Community Voice Library Restriction on Free Tier:** ElevenLabs restricts community Voice Library voices to paid API tiers (`HTTP 402`). This project uses official default premade voices (`Sarah`, `Alice`, `Laura`, `Matilda`, `Roger`, `George`, `Liam`, `Charlie`) that are compatible with all tiers.

---
---

## Versión en Español

### 1. Descripción del proyecto

**Text to Speech (v2)** es una aplicación web *full-stack* y herramienta CLI desarrollada con **Python (FastAPI)** y **React + Vite** que convierte artículos web en archivos de audio MP3 reproducibles y descargables mediante síntesis de voz neuronal. A partir de la URL de un artículo, la aplicación descarga y extrae su contenido textual principal de forma segura, lo procesa con técnicas de lenguaje natural y lo sintetiza permitiendo elegir entre **dos motores de generación de voz**:

- **Edge TTS (`edge`):** Motor neuronal gratuito que no requiere clave API, basado en Microsoft Edge TTS.
- **ElevenLabs (`elevenlabs`):** Motor neuronal expresivo multilingüe conectado a la API REST oficial de ElevenLabs utilizando el modelo **`eleven_v4`** (`mp3_44100_128`).

---

### 2. Objetivos

- Automatizar la extracción segura de texto limpio y legible a partir de la URL de un artículo web (con protección SSRF, validación de puertos/IPs y límites de tamaño).
- Ofrecer dos motores complementarios (**Edge TTS** y **ElevenLabs `eleven_v4`**) con selección dinámica de **Motor**, **Idioma** y **Voz**.
- Garantizar la generación atómica de archivos MP3 y la limpieza segura de directorios temporales incluso ante errores o desconexiones del cliente.
- Mantener la aplicación operativa con Edge TTS aunque ElevenLabs no esté configurado o haya agotado su cuota.

---

### 3. Funcionalidades principales y motores de voz

| Funcionalidad | Descripción | Estado |
| :--- | :--- | :--- |
| **Extracción segura de artículos web** | Descarga y análisis del título y cuerpo principal de un artículo público HTTP/HTTPS (hasta 5 MB de HTML y 15.000 caracteres). | Implementada |
| **Doble motor de síntesis neuronal** | Elección entre **Edge TTS** (alternativa gratuita) y **ElevenLabs** (API REST oficial con modelo `eleven_v4`). | Implementada |
| **Selección dinámica de Motor, Idioma y Voz** | Tres selectores sincronizados en React (**Motor → Idioma → Voz**) alimentados desde `GET /api/languages`. | Implementada |
| **Generación segura de MP3 y limpieza temporal** | Escritura atómica en archivo temporal y transmisión con `SafeFileResponse` y bloqueos POSIX `fcntl.flock`. | Implementada |
| **Gestión específica de errores y cambio manual** | Detección de clave ausente/inválida, cuota agotada, límite de peticiones, *timeouts* y errores de red en ElevenLabs, ofreciendo cambiar manualmente a Edge TTS sin perder el resultado anterior. | Implementada |

#### Comparativa entre Edge TTS y ElevenLabs (`eleven_v4`)

| Característica | Edge TTS (`edge`) | ElevenLabs (`elevenlabs`) |
| :--- | :--- | :--- |
| **Coste y cuota** | Gratuito, sin registro ni clave API. | Cuota gratuita limitada (**10.000 créditos mensuales**); cada conversión consume créditos según la longitud del texto. |
| **Modelo** | Microsoft Edge Neural TTS | **`eleven_v4`** (admite hasta 100.000 caracteres por petición; se mantiene el límite del proyecto en 15.000 caracteres). |
| **Formato de salida** | MP3 (`audio/mpeg`) | MP3 `mp3_44100_128` (44.1 kHz, 128 kbps). |
| **Idiomas disponibles** | Español (`es`), Inglés (`en`), Francés (`fr`) | Español (`es`), Inglés (`en`), Francés (`fr`) mediante `language_code`. |
| **Voces disponibles** | Voces neuronales específicas por idioma (`ElviraNeural`, `AlvaroNeural`, `AriaNeural`, `GuyNeural`, `DeniseNeural`, `HenriNeural`). | Voces predefinidas oficiales multilingües (`Sarah`, `Alice`, `Laura`, `Matilda`, `Roger`, `George`, `Liam`, `Charlie`), comprobadas desde el backend. |

#### Aviso sobre la cuota gratuita de ElevenLabs

Cuando el usuario selecciona **ElevenLabs** en la interfaz, se muestra automáticamente el siguiente aviso informativo (que desaparece al seleccionar **Edge TTS**):
> *«ElevenLabs utiliza una cuota gratuita limitada de 10.000 créditos mensuales. Cada conversión consume créditos según la longitud del texto. Cuando se agote la cuota, podrás seguir utilizando Edge TTS sin coste».*

---

### 4. Tecnologías y herramientas

| Tecnología / Biblioteca | Función en el proyecto |
| :--- | :--- |
| **Python 3.9+ / 3.11** | Lenguaje del backend y entorno de ejecución. |
| **FastAPI & Uvicorn** | API REST (`GET /api/health`, `GET /api/languages`, `POST /api/tts`) y servidor ASGI. |
| **React 18 & Vite 8** | Interfaz web minimalista y *responsive* en CSS puro con tres selectores dinámicos. |
| **newspaper3k & NLTK** | Extracción del artículo y tokenización de lenguaje natural (`punkt`, `punkt_tab`). |
| **edge-tts & aiohttp** | Motor asíncrono de síntesis neuronal con Microsoft Edge TTS. |
| **requests** | Cliente HTTP utilizado para la descarga protegida contra SSRF y las llamadas a la API REST de ElevenLabs. |

---

### 5. Estructura del proyecto

```text
text-to-speech/
├── backend/
│   ├── main.py           # Aplicación FastAPI, endpoints, CORS y ciclo de vida de archivos temporales
│   ├── schemas.py        # Esquemas Pydantic de peticiones, motores, idiomas y voces
│   └── security.py       # Protección SSRF, límite de 15.000 caracteres, IP y rate limiting
├── frontend/
│   ├── src/
│   │   ├── components/   # Header, HeroSection, ConversionForm, StatusBanner, AudioResultCard
│   │   ├── App.jsx       # Gestión de estado, sincronización de selectores y llamadas a la API
│   │   ├── index.css     # Hoja de estilos responsive en CSS puro
│   │   └── main.jsx      # Punto de entrada de React
│   ├── package.json
│   └── vite.config.js    # Configuración de Vite y proxy local /api
├── src/
│   ├── extractor.py      # Descarga segura del artículo y extracción de texto
│   ├── converter.py      # Conversión dual a voz (Edge TTS + ElevenLabs eleven_v4)
│   └── main.py           # Punto de entrada interactivo por línea de comandos (CLI)
├── tests/
│   ├── test_temp_lifecycle.py   # 14 pruebas de ciclo de vida temporal, desconexión y puertos
│   └── test_dual_engine_tts.py  # 10 pruebas de integración dual Edge TTS + ElevenLabs y errores
├── .env.example          # Plantilla de variables de entorno del backend
├── render.yaml           # Configuración de despliegue en Render
├── requirements.txt      # Dependencias de Python
└── README.md             # Documentación del proyecto (EN / ES)
```

---

### 6. Instalación, configuración de la API Key y uso local

1. **Clonar el repositorio y preparar el entorno virtual:**

   ```bash
   git clone https://github.com/gemaespada/text-to-speech.git
   cd text-to-speech
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configurar `ELEVENLABS_API_KEY` en local (`.env`):**

   El proyecto cuenta con un archivo `.env` local (ignorado por Git en `.gitignore`). Para utilizar ElevenLabs en local, abre el archivo `.env` en tu editor e introduce tu clave privada directamente:

   ```dotenv
   ELEVENLABS_API_KEY=tu_clave_de_elevenlabs_aqui
   ```

   - **Seguridad:** La clave se lee exclusivamente desde la variable de entorno `ELEVENLABS_API_KEY` en el backend. Nunca se expone en el frontend, ni en logs, ni en mensajes de error, ni en GitHub.
   - Si `ELEVENLABS_API_KEY` se deja vacía, la aplicación sigue funcionando con **Edge TTS** con total normalidad.

3. **Ejecutar el backend y el frontend en local:**

   - **Backend FastAPI (Terminal 1):**
     ```bash
     ./.venv/bin/python -m uvicorn backend.main:app --reload --port 8000
     ```
   - **Frontend React + Vite (Terminal 2):**
     ```bash
     cd frontend
     npm install
     npm run dev
     ```
   - Accede a `http://localhost:5173/` en el navegador.

4. **Ejecutar las pruebas automatizadas:**

   ```bash
   ./.venv/bin/python -m unittest -v tests/test_temp_lifecycle.py tests/test_dual_engine_tts.py
   ```

---

### 7. Configuración en Render y Cloudflare Pages

- **En Render (Backend):**
  - El archivo `render.yaml` declara la variable `ELEVENLABS_API_KEY` con `sync: false` para que su valor nunca se almacene en el repositorio.
  - En el panel de Render (**Dashboard → Servicio `text-to-speech-api` → Environment**), añade manualmente el valor secreto de `ELEVENLABS_API_KEY` y asegúrate de que `FRONTEND_ORIGINS` incluya el dominio de Cloudflare Pages.
- **En Cloudflare Pages (Frontend):**
  - Configura `VITE_API_BASE_URL` apuntando a la URL pública del servicio en Render (`https://text-to-speech-api-yfia.onrender.com`).

---

### 8. Limitaciones conocidas

1. **Límite de tamaño por artículo:** Por seguridad y control de consumo, la descarga HTML está limitada a **5 MB** (`MAX_HTML_BYTES`) y el texto extraído a **15.000 caracteres** (`MAX_ARTICLE_CHARS`).
2. **Cuota gratuita mensual de ElevenLabs:** El plan gratuito de ElevenLabs dispone de 10.000 créditos mensuales (~10.000 caracteres). Cuando se agote la cuota o si un artículo supera los créditos restantes, la interfaz mostrará un aviso claro y un botón para cambiar manualmente a **Edge TTS** sin perder el audio anterior.
3. **Restricción del plan gratuito de ElevenLabs en IPs de servidores Cloud:** ElevenLabs puede restringir el uso del plan gratuito cuando las peticiones proceden de direcciones IP compartidas de centros de datos como Render (`detected_unusual_activity`). Para usar ElevenLabs desde un servidor en la nube suele requerirse un plan de pago en ElevenLabs, mientras que en ejecución local y con **Edge TTS** funciona sin coste.
4. **Voces de la librería comunitaria en cuentas gratuitas:** La API de ElevenLabs solo permite utilizar en el plan gratuito las voces predefinidas oficiales (*default premade voices*). Por ello el proyecto utiliza exclusivamente identificadores oficiales de voces predefinidas (`Sarah`, `Alice`, `Laura`, `Matilda`, `Roger`, `George`, `Liam`, `Charlie`).
