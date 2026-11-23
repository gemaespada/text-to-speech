import { useEffect, useRef, useState } from "react";
import Header from "./components/Header";
import HeroSection from "./components/HeroSection";
import ConversionForm from "./components/ConversionForm";
import StatusBanner from "./components/StatusBanner";
import AudioResultCard from "./components/AudioResultCard";

const SAMPLE_LANGUAGES = [
  {
    code: "es",
    name: "Español",
    voice: "es-ES-ElviraNeural",
  },
  {
    code: "en",
    name: "English",
    voice: "en-US-AriaNeural",
  },
  {
    code: "fr",
    name: "Français",
    voice: "fr-FR-DeniseNeural",
  },
];

const SAMPLE_URL =
  "https://www.infobae.com/america/agencias/2026/03/23/lady-gaga-anuncia-su-regreso-a-los-escenarios/";

const SAMPLE_ERROR_MESSAGE =
  "No ha sido posible extraer el contenido del artículo. Verifica que el enlace sea público y corresponda a una noticia o entrada de blog válida.";

/**
 * Valida una URL en el cliente utilizando el constructor nativo new URL().
 * Comprueba que el protocolo sea http: o https: y que posea un hostname válido.
 */
function validateArticleUrl(rawUrl) {
  const trimmed = rawUrl.trim();
  if (!trimmed) {
    return {
      isValid: false,
      normalizedUrl: "",
      error: "Introduce la URL del artículo que deseas convertir.",
    };
  }

  try {
    const parsed = new URL(trimmed);
    const hasValidProtocol =
      parsed.protocol === "http:" || parsed.protocol === "https:";
    const hostname = parsed.hostname;
    const hasValidHostname =
      Boolean(hostname) &&
      (hostname.includes(".") || hostname.includes(":")) &&
      !hostname.startsWith(".") &&
      !hostname.endsWith(".");

    if (!hasValidProtocol || !hasValidHostname) {
      return {
        isValid: false,
        normalizedUrl: "",
        error:
          "Introduce una URL válida con protocolo http:// o https:// y un dominio válido.",
      };
    }

    return {
      isValid: true,
      normalizedUrl: parsed.toString(),
      error: "",
    };
  } catch {
    return {
      isValid: false,
      normalizedUrl: "",
      error:
        "Introduce una URL válida que comience por http:// o https://.",
    };
  }
}

/**
 * Genera en memoria un clip de audio WAV suave de 2 segundos para poder
 * probar el reproductor y el botón de descarga con datos de ejemplo,
 * sin realizar peticiones al backend ni añadir binarios al repositorio.
 */
function createDemoAudioBlobUrl() {
  const sampleRate = 22050;
  const durationSeconds = 2;
  const numSamples = sampleRate * durationSeconds;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  const writeString = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + numSamples * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, numSamples * 2, true);

  for (let i = 0; i < numSamples; i += 1) {
    const t = i / sampleRate;
    const envelope = Math.sin((Math.PI * t) / durationSeconds);
    const wave =
      0.5 * Math.sin(2 * Math.PI * 440 * t) +
      0.35 * Math.sin(2 * Math.PI * 554.37 * t) +
      0.25 * Math.sin(2 * Math.PI * 659.25 * t);
    const sample = Math.max(-1, Math.min(1, wave * envelope * 0.25));
    view.setInt16(44 + i * 2, sample * 32767, true);
  }

  const blob = new Blob([buffer], { type: "audio/wav" });
  return URL.createObjectURL(blob);
}

function buildDemoResultSnapshot(sourceUrl, languageItem, audioUrl) {
  return {
    title: "Artículo convertido a audio (Audio WAV de demostración)",
    sourceUrl,
    languageName: languageItem.name,
    voice: languageItem.voice,
    formatLabel: "WAV",
    mimeType: "audio/wav",
    filename: `articulo-demo-${languageItem.code}.wav`,
    audioUrl,
  };
}

function App() {
  const [url, setUrl] = useState(SAMPLE_URL);
  const [language, setLanguage] = useState("es");
  const [status, setStatus] = useState("success");
  const [errorMessage, setErrorMessage] = useState("");
  const [result, setResult] = useState(null);

  const timerRef = useRef(null);
  const activeAudioUrlRef = useRef(null);

  /**
   * Sustituye de forma segura el Object URL de audio activo, revocando
   * únicamente la URL anterior cuando deja de utilizarse en el reproductor.
   */
  const replaceAudioObjectUrl = (nextAudioUrl) => {
    const previousAudioUrl = activeAudioUrlRef.current;
    if (previousAudioUrl && previousAudioUrl !== nextAudioUrl) {
      URL.revokeObjectURL(previousAudioUrl);
    }
    activeAudioUrlRef.current = nextAudioUrl;
  };

  useEffect(() => {
    const initialAudioUrl = createDemoAudioBlobUrl();
    replaceAudioObjectUrl(initialAudioUrl);
    setResult(
      buildDemoResultSnapshot(SAMPLE_URL, SAMPLE_LANGUAGES[0], initialAudioUrl)
    );

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      replaceAudioObjectUrl(null);
    };
  }, []);

  const clearPendingTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const handleSimulatedSubmit = () => {
    clearPendingTimer();

    const validation = validateArticleUrl(url);
    if (!validation.isValid) {
      setErrorMessage(validation.error);
      setStatus("error");
      return;
    }

    const submittedUrl = validation.normalizedUrl;
    const submittedLanguage =
      SAMPLE_LANGUAGES.find((item) => item.code === language) ||
      SAMPLE_LANGUAGES[0];

    setErrorMessage("");
    setStatus("loading");

    timerRef.current = setTimeout(() => {
      const nextAudioUrl = createDemoAudioBlobUrl();
      replaceAudioObjectUrl(nextAudioUrl);

      setResult(
        buildDemoResultSnapshot(
          submittedUrl,
          submittedLanguage,
          nextAudioUrl
        )
      );
      setStatus("success");
    }, 900);
  };

  const handlePreviewStateChange = (nextStatus) => {
    clearPendingTimer();

    if (nextStatus === "error") {
      setErrorMessage(SAMPLE_ERROR_MESSAGE);
    } else {
      setErrorMessage("");
    }

    if (nextStatus === "success" && !result) {
      const fallbackLanguage =
        SAMPLE_LANGUAGES.find((item) => item.code === language) ||
        SAMPLE_LANGUAGES[0];
      const nextAudioUrl = createDemoAudioBlobUrl();
      replaceAudioObjectUrl(nextAudioUrl);
      setResult(
        buildDemoResultSnapshot(
          url.trim() || SAMPLE_URL,
          fallbackLanguage,
          nextAudioUrl
        )
      );
    }

    setStatus(nextStatus);
  };

  return (
    <div className="app-shell">
      <Header />

      <main className="main-container">
        <HeroSection />

        <div className="workspace-panel">
          <div className="main-card">
            <ConversionForm
              url={url}
              onUrlChange={setUrl}
              language={language}
              onLanguageChange={setLanguage}
              languages={SAMPLE_LANGUAGES}
              isLoading={status === "loading"}
              onSubmit={handleSimulatedSubmit}
            />

            <StatusBanner
              status={status}
              errorMessage={errorMessage}
              onDismissError={() => setStatus("idle")}
            />
          </div>

          {status === "success" && <AudioResultCard result={result} />}

          <section
            className="preview-toolbar"
            aria-label="Selector de estados de vista previa"
          >
            <div className="preview-toolbar-header">
              <span className="preview-dot" aria-hidden="true" />
              <span className="preview-label">
                Modo diseño (datos de ejemplo): previsualizar estados de la
                interfaz
              </span>
            </div>
            <div className="preview-actions">
              <button
                type="button"
                className={`preview-btn ${status === "idle" ? "active" : ""}`}
                onClick={() => handlePreviewStateChange("idle")}
              >
                Inicial
              </button>
              <button
                type="button"
                className={`preview-btn ${status === "loading" ? "active" : ""}`}
                onClick={() => handlePreviewStateChange("loading")}
              >
                Cargando
              </button>
              <button
                type="button"
                className={`preview-btn ${status === "success" ? "active" : ""}`}
                onClick={() => handlePreviewStateChange("success")}
              >
                Completado
              </button>
              <button
                type="button"
                className={`preview-btn ${status === "error" ? "active" : ""}`}
                onClick={() => handlePreviewStateChange("error")}
              >
                Error
              </button>
            </div>
          </section>
        </div>
      </main>

      <footer className="site-footer">
        <p>
          Text to Speech · Síntesis neuronal con Python, FastAPI y React
        </p>
      </footer>
    </div>
  );
}

export default App;
