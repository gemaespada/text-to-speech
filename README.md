# Text to Speech

> 🌐 **Languages / Idiomas:** [English](#english-version) | [Español](#versión-en-español)

---

## English Version

### 1. Project Overview

**Text to Speech** is a Python application developed and tested with **Python 3.9.6** that converts online web articles into playable MP3 audio files using neural speech synthesis. By providing the URL of an article, the application downloads and extracts its main text content, processes it using natural language processing techniques, and synthesizes it into spoken audio via `edge-tts`.

> **Current Status:** Core article extraction, neural speech synthesis, and interactive CLI workflow are implemented and operational. An active Internet connection is required both to download the web article and to communicate with Microsoft's speech synthesis service.

---

### 2. Project Objectives

- Automate the extraction of clean, readable text from online articles via their URL.
- Process and prepare extracted text for natural-sounding speech synthesis.
- Convert processed article text into portable MP3 audio files using predefined neural voices.
- Provide a simple, modular, and easy-to-use Python workflow.

---

### 3. Main Features

| Feature | Description | Status |
| :--- | :--- | :--- |
| **Web Article Extraction** | Download and parse the title and main body of a web article from a given URL. | Implemented |
| **Text Processing** | Clean and tokenize extracted text using Natural Language Processing (NLP) to ensure smooth audio reading. | Implemented |
| **Language-Based Neural Synthesis** | Select among Spanish (`es`), English (`en`), or French (`fr`), automatically mapping each language to a predefined neural voice. | Implemented |
| **Safe MP3 File Generation** | Write synthesized audio to a temporary file in the target directory, verify that the generated file exists and is not empty (`> 0 bytes`), and atomically replace the destination `.mp3` file. | Implemented |

#### How Neural Voice Selection Works

- **Internet Requirement:** Speech generation requires an active Internet connection because `edge-tts` communicates online with Microsoft's speech services.
- **Automatic Voice Mapping:** The user only chooses the language code (`es`, `en`, or `fr`). The neural voice is fixed and assigned automatically via an internal dictionary; users cannot manually select or customize individual voices.
- **Internal Prosody Defaults:** Speech speed (`rate = "+0%"`) and voice tone (`pitch = "+0Hz"`) use fixed internal defaults and are not configurable from the user interface.

| Language Code | Language | Predefined Neural Voice |
| :--- | :--- | :--- |
| `es` *(default)* | Spanish (Spain) | `es-ES-ElviraNeural` |
| `en` | English (US) | `en-US-AriaNeural` |
| `fr` | French (France) | `fr-FR-DeniseNeural` |

---

### 4. Technologies and Tools

The project has been developed and tested with **Python 3.9.6** and uses the following libraries:

| Technology / Library | Role in the Project |
| :--- | :--- |
| **Python 3.9.6** | Programming language and tested runtime environment. |
| **newspaper3k** | Web scraping and article extraction (downloading HTML and parsing clean article text). |
| **NLTK** | Natural Language Toolkit used for text preprocessing and tokenization required by the article parser. |
| **edge-tts** | Asynchronous neural text-to-speech engine that communicates with Microsoft's speech services to generate MP3 audio. |
| **aiohttp** | Asynchronous HTTP/WebSocket transport library used by `edge-tts` and imported directly in `src/converter.py` to handle network transport exceptions (`ClientError`). |

---

### 5. Project Structure

Directory and file layout:

```text
text-to-speech/
├── src/
│   ├── __init__.py
│   ├── extractor.py      # Article downloading and text extraction (newspaper3k + NLTK)
│   ├── converter.py      # Neural text-to-speech conversion and safe MP3 writing (edge-tts + aiohttp)
│   └── main.py           # Application entry point and interactive CLI workflow
├── output/               # Generated MP3 audio files
├── requirements.txt      # Project dependencies
└── README.md             # Project documentation (EN / ES)
```

---

### 6. Installation and Setup

> **Requirement:** Python **3.9.6** and an active Internet connection.

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd text-to-speech
   ```

2. **Create and activate a virtual environment:**

   - **Create the virtual environment:**
     ```bash
     python3 -m venv .venv
     ```
   - **Activate on macOS / Linux:**
     ```bash
     source .venv/bin/activate
     ```
   - **Activate on Windows (CMD):**
     ```cmd
     .venv\Scripts\activate.bat
     ```
   - **Activate on Windows (PowerShell):**
     ```powershell
     .venv\Scripts\Activate.ps1
     ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **NLTK resources:**
   Required tokenizers (`punkt` and `punkt_tab`) are automatically checked and downloaded on first run if missing.

---

### 7. Usage

1. Run the application as a Python module from the project root directory:

   ```bash
   python -m src.main
   ```

2. Provide the following inputs when prompted in the terminal:
   - **Article URL:** Full `http://` or `https://` web address of the article.
   - **Output MP3 path:** Destination path ending in `.mp3` (press `Enter` to use the default `output/article.mp3`).
   - **Language code:** `es`, `en`, or `fr` (press `Enter` to use the default `es`).
3. The application extracts the article text, synthesizes the speech into a temporary file, verifies that the file exists and is non-empty, and saves the final `.mp3` file at the target path.

---

### 8. Future Improvements

- **Command-Line Arguments (CLI Flags):** Pass the URL, target language, and output filename directly via terminal flags without interactive prompts.
- **Speech Speed and Pitch Adjustment:** Allow optional adjustment of speech speed (`rate`) and tone (`pitch`) while keeping automatic, fixed voice assignment per language *(not currently implemented)*.
- **Graphical User Interface (GUI) or Web UI:** Provide a visual interface for pasting URLs and playing generated audio directly.
- **Batch Processing:** Convert multiple article URLs from a text file in a single run.

---
---

## Versión en Español

### 1. Descripción del proyecto

**Text to Speech** es una aplicación en Python desarrollada y probada con **Python 3.9.6** que convierte artículos publicados en páginas web en archivos de audio reproducibles en formato MP3 mediante síntesis de voz neuronal. A partir de la URL de un artículo, la aplicación descarga y extrae su contenido textual principal, lo procesa mediante técnicas de procesamiento del lenguaje natural y lo transforma en voz utilizando `edge-tts`.

> **Estado actual:** La extracción de artículos, la síntesis de voz neuronal y el flujo interactivo por terminal están implementados y operativos. Se requiere conexión activa a Internet tanto para descargar el artículo web como para comunicarse con los servicios de síntesis de voz de Microsoft.

---

### 2. Objetivos

- Automatizar la extracción de texto limpio y legible a partir de la URL de un artículo web.
- Procesar y preparar el texto extraído para obtener una lectura fluida.
- Convertir el contenido textual procesado en archivos de audio MP3 portables mediante voces neuronales predefinidas.
- Ofrecer un flujo de trabajo en Python sencillo, modular y fácil de utilizar.

---

### 3. Funcionalidades principales

| Funcionalidad | Descripción | Estado |
| :--- | :--- | :--- |
| **Extracción de artículos web** | Descarga y análisis del título y cuerpo principal de un artículo a partir de su URL. | Implementada |
| **Procesamiento de texto** | Limpieza y tokenización del texto mediante procesamiento del lenguaje natural (NLP) para facilitar su lectura. | Implementada |
| **Síntesis neuronal por idioma** | Selección entre español (`es`), inglés (`en`) o francés (`fr`), asignando automáticamente una voz neuronal fija a cada idioma. | Implementada |
| **Generación segura de archivos MP3** | Escritura del audio en un archivo temporal en el directorio de destino, comprobación de que el archivo generado existe y no está vacío (`> 0 bytes`), y sustitución atómica del archivo `.mp3` final. | Implementada |

#### Funcionamiento de la selección de voz neuronal

- **Requisito de conexión a Internet:** La generación de audio requiere conexión a Internet, ya que `edge-tts` se comunica en línea con los servicios de voz de Microsoft.
- **Asignación automática de voz:** El usuario únicamente elige el código de idioma (`es`, `en` o `fr`). La voz neuronal se asigna automáticamente mediante un diccionario interno; no existe opción para que el usuario seleccione o personalice la voz.
- **Velocidad y tono internos:** La velocidad (`rate = "+0%"`) y el tono (`pitch = "+0Hz"`) utilizan valores predeterminados internos y no son configurables desde la interfaz de usuario.

| Código de idioma | Idioma | Voz neuronal predefinida |
| :--- | :--- | :--- |
| `es` *(por defecto)* | Español (España) | `es-ES-ElviraNeural` |
| `en` | Inglés (EE. UU.) | `en-US-AriaNeural` |
| `fr` | Francés (Francia) | `fr-FR-DeniseNeural` |

---

### 4. Tecnologías y herramientas

El proyecto se ha desarrollado y probado con **Python 3.9.6** y utiliza las siguientes bibliotecas:

| Tecnología / Biblioteca | Función en el proyecto |
| :--- | :--- |
| **Python 3.9.6** | Lenguaje de programación y versión del entorno en el que se ha desarrollado y probado el proyecto. |
| **newspaper3k** | Acceso a artículos web, descarga del contenido y extracción del texto principal. |
| **NLTK** | Procesamiento del lenguaje natural (preprocesamiento y tokenización de texto requeridos en el análisis del artículo). |
| **edge-tts** | Motor asíncrono de síntesis de voz neuronal que se conecta a los servicios de voz de Microsoft para generar archivos MP3. |
| **aiohttp** | Biblioteca de transporte asíncrono HTTP/WebSocket utilizada por `edge-tts` e importada directamente en `src/converter.py` para gestionar excepciones de red (`ClientError`). |

---

### 5. Estructura del proyecto

Organización de carpetas y archivos:

```text
text-to-speech/
├── src/
│   ├── __init__.py
│   ├── extractor.py      # Descarga del artículo y extracción de texto (newspaper3k + NLTK)
│   ├── converter.py      # Conversión neuronal a voz y escritura segura de MP3 (edge-tts + aiohttp)
│   └── main.py           # Punto de entrada de la aplicación y flujo interactivo CLI
├── output/               # Archivos de audio MP3 generados
├── requirements.txt      # Dependencias del proyecto
└── README.md             # Documentación del proyecto (EN / ES)
```

---

### 6. Instalación y configuración

> **Requisito:** Python **3.9.6** y conexión activa a Internet.

1. **Clonar el repositorio:**

   ```bash
   git clone <url-del-repositorio>
   cd text-to-speech
   ```

2. **Crear y activar un entorno virtual:**

   - **Crear el entorno virtual:**
     ```bash
     python3 -m venv .venv
     ```
   - **Activar en macOS / Linux:**
     ```bash
     source .venv/bin/activate
     ```
   - **Activar en Windows (CMD):**
     ```cmd
     .venv\Scripts\activate.bat
     ```
   - **Activar en Windows (PowerShell):**
     ```powershell
     .venv\Scripts\Activate.ps1
     ```

3. **Instalar las dependencias:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Recursos de NLTK:**
   Los tokenizadores necesarios (`punkt` y `punkt_tab`) se comprueban y descargan automáticamente en la primera ejecución si no están presentes.

---

### 7. Uso

1. Ejecutar la aplicación como módulo de Python desde el directorio raíz del proyecto:

   ```bash
   python -m src.main
   ```

2. Introducir los siguientes datos cuando la terminal lo solicite:
   - **URL del artículo:** Dirección web completa (`http://` o `https://`) del artículo.
   - **Ruta de salida MP3:** Ruta de destino terminada en `.mp3` (pulsar `Enter` para usar el valor por defecto `output/article.mp3`).
   - **Código de idioma:** `es`, `en` o `fr` (pulsar `Enter` para usar el valor por defecto `es`).
3. La aplicación extraerá el texto del artículo, sintetizará la voz en un archivo temporal, comprobará que el archivo generado exista y no esté vacío, y guardará el archivo `.mp3` definitivo en la ruta indicada.

---

### 8. Mejoras futuras

- **Argumentos por línea de comandos (CLI Flags):** Permitir indicar la URL, el idioma y el nombre del archivo de salida directamente como parámetros en la terminal sin preguntas interactivas.
- **Ajuste de velocidad y tono:** Permitir el ajuste opcional de la velocidad (`rate`) y el tono (`pitch`) manteniendo siempre la asignación fija y automática de voz según el idioma *(actualmente no implementado)*.
- **Interfaz gráfica (GUI) o Web:** Incorporar una interfaz visual para pegar enlaces y reproducir el audio generado directamente.
- **Procesamiento por lotes:** Convertir múltiples URLs almacenadas en un archivo de texto en una sola ejecución.
