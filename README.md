# Text to Speech

> 🌐 **Languages / Idiomas:** [English](#english-version) | [Español](#versión-en-español)

---

## English Version

### 1. Project Overview

**Text to Speech** is a Python application designed to convert online web articles into playable MP3 audio files. By providing the URL of an article, the application extracts its main text content, processes it using natural language processing techniques, and synthesizes it into spoken audio.

> **Current Status:** Project in initial planning and documentation phase. Core functionalities described below are planned and pending implementation.

---

### 2. Project Objectives

- Automate the extraction of clean, readable text from online articles via their URL.
- Process and prepare extracted text for natural-sounding speech synthesis.
- Convert processed article text into portable MP3 audio files.
- Provide a simple, modular, and easy-to-use Python workflow.

---

### 3. Main Features

| Feature | Description | Status |
| :--- | :--- | :--- |
| **Web Article Extraction** | Download and parse the title and main body of a web article from a given URL. | Planned |
| **Text Processing** | Clean and tokenize extracted text using Natural Language Processing (NLP) to ensure smooth audio reading. | Planned |
| **Language Support** | Configure the synthesis language to match the source article (e.g., Spanish, English). | Planned |
| **MP3 Audio Generation** | Convert the processed text into speech and save the output locally as an `.mp3` file. | Planned |

---

### 4. Technologies and Tools

The project relies exclusively on Python and the following core libraries:

| Technology / Library | Role in the Project |
| :--- | :--- |
| **Python 3** | Primary programming language. |
| **newspaper3k** | Web scraping and article extraction (downloading HTML and parsing clean article text). |
| **NLTK** | Natural Language Toolkit used for text preprocessing and tokenization required by the article parser. |
| **gTTS (Google Text-to-Speech)** | Text-to-speech conversion engine that generates playable MP3 audio files from text. |

---

### 5. Project Structure

Proposed initial directory and file layout:

```text
text-to-speech/
├── src/
│   ├── __init__.py
│   ├── extractor.py      # Article downloading and text extraction (newspaper3k + NLTK)
│   ├── converter.py      # Text-to-speech conversion and MP3 export (gTTS)
│   └── main.py           # Application entry point and user workflow
├── output/               # Generated MP3 audio files
├── requirements.txt      # Project dependencies
└── README.md             # Project documentation (EN / ES)
```

---

### 6. Installation and Setup

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd text-to-speech
   ```

2. **Create and activate a virtual environment:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On macOS/Linux
   # .venv\Scripts\activate   # On Windows
   ```

3. **Install dependencies** *(once `requirements.txt` is created)*:

   ```bash
   pip install newspaper3k nltk gTTS
   ```

4. **Download required NLTK resources** *(performed once during initial setup)*:

   ```python
   import nltk
   nltk.download("punkt")
   ```

---

### 7. Usage

Once implemented, the application will follow a straightforward execution workflow:

1. Run the main script from the terminal:

   ```bash
   python src/main.py
   ```

2. Provide the target article URL and language code when prompted.
3. The application will extract the text, generate the speech, and save the resulting `.mp3` file inside the `output/` directory.

---

### 8. Future Improvements

- **Command-Line Arguments (CLI):** Pass the URL, target language, and output filename directly via terminal flags.
- **Graphical User Interface (GUI) or Web UI:** Provide a visual interface for pasting URLs and playing generated audio directly.
- **Batch Processing:** Convert multiple article URLs from a text file in a single run.
- **URL & Error Validation:** Robust handling of unreachable URLs, paywalls, or network interruptions.

---
---

## Versión en Español

### 1. Descripción del proyecto

**Text to Speech** es una aplicación en Python diseñada para convertir artículos publicados en páginas web en archivos de audio reproducibles en formato MP3. A partir de la URL de un artículo, la aplicación extrae su contenido textual principal, lo procesa mediante técnicas de procesamiento del lenguaje natural y lo transforma en voz mediante síntesis de audio.

> **Estado actual:** Proyecto en fase inicial de planificación y documentación. Las funcionalidades descritas a continuación están previstas y pendientes de implementación.

---

### 2. Objetivos

- Automatizar la extracción de texto limpio y legible a partir de la URL de un artículo web.
- Procesar y preparar el texto extraído para obtener una lectura fluida.
- Convertir el contenido textual procesado en archivos de audio MP3 portables.
- Ofrecer un flujo de trabajo en Python sencillo, modular y fácil de utilizar.

---

### 3. Funcionalidades principales

| Funcionalidad | Descripción | Estado |
| :--- | :--- | :--- |
| **Extracción de artículos web** | Descarga y análisis del título y cuerpo principal de un artículo a partir de su URL. | Prevista |
| **Procesamiento de texto** | Limpieza y tokenización del texto mediante procesamiento del lenguaje natural (NLP) para facilitar su lectura. | Prevista |
| **Soporte de idioma** | Configuración del idioma de síntesis para adaptarlo al idioma del artículo original (p. ej., español, inglés). | Prevista |
| **Generación de audio MP3** | Conversión del texto procesado en voz y almacenamiento local como archivo `.mp3`. | Prevista |

---

### 4. Tecnologías y herramientas

El proyecto utiliza exclusivamente Python y las siguientes bibliotecas principales:

| Tecnología / Biblioteca | Función en el proyecto |
| :--- | :--- |
| **Python 3** | Lenguaje de programación principal. |
| **newspaper3k** | Acceso a artículos web, descarga del contenido y extracción del texto principal. |
| **NLTK** | Procesamiento del lenguaje natural (preprocesamiento y tokenización de texto requeridos en el análisis del artículo). |
| **gTTS (Google Text-to-Speech)** | Motor de síntesis de voz encargado de convertir el texto en archivos de audio MP3. |

---

### 5. Estructura del proyecto

Propuesta inicial de organización de carpetas y archivos:

```text
text-to-speech/
├── src/
│   ├── __init__.py
│   ├── extractor.py      # Descarga del artículo y extracción de texto (newspaper3k + NLTK)
│   ├── converter.py      # Conversión de texto a voz y exportación a MP3 (gTTS)
│   └── main.py           # Punto de entrada de la aplicación y flujo principal
├── output/               # Archivos de audio MP3 generados
├── requirements.txt      # Dependencias del proyecto
└── README.md             # Documentación del proyecto (EN / ES)
```

---

### 6. Instalación y configuración

1. **Clonar el repositorio:**

   ```bash
   git clone <url-del-repositorio>
   cd text-to-speech
   ```

2. **Crear y activar un entorno virtual:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # En macOS/Linux
   # .venv\Scripts\activate   # En Windows
   ```

3. **Instalar las dependencias** *(una vez creado el archivo `requirements.txt`)*:

   ```bash
   pip install newspaper3k nltk gTTS
   ```

4. **Descargar los recursos necesarios de NLTK** *(se ejecuta una sola vez en la configuración inicial)*:

   ```python
   import nltk
   nltk.download("punkt")
   ```

---

### 7. Uso

Una vez implementada la aplicación, su modo de empleo seguirá este flujo:

1. Ejecutar el script principal desde la terminal:

   ```bash
   python src/main.py
   ```

2. Introducir la URL del artículo y el código de idioma cuando la aplicación lo solicite.
3. La aplicación extraerá el texto, generará la locución y guardará el archivo `.mp3` resultante en la carpeta `output/`.

---

### 8. Mejoras futuras

- **Argumentos por línea de comandos (CLI):** Permitir indicar la URL, el idioma y el nombre del archivo de salida directamente como parámetros en la terminal.
- **Interfaz gráfica (GUI) o Web:** Incorporar una interfaz visual para pegar enlaces y reproducir el audio generado directamente.
- **Procesamiento por lotes:** Convertir múltiples URLs almacenadas en un archivo de texto en una sola ejecución.
- **Validación de URLs y gestión de errores:** Manejo robusto ante enlaces caídos, muros de pago o fallos de conexión.
