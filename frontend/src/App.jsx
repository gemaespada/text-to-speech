import { useEffect, useRef, useState } from "react";
import Header from "./components/Header";
import HeroSection from "./components/HeroSection";
import ConversionForm from "./components/ConversionForm";
import StatusBanner from "./components/StatusBanner";
import AudioResultCard from "./components/AudioResultCard";

const FALLBACK_STATUS_MESSAGES = {
  400: "La petición o la URL proporcionada no es válida. Asegúrate de que sea un artículo público con texto legible.",
  413: "El artículo o la página web supera el tamaño máximo permitido para la conversión.",
  422: "Los datos enviados no son válidos. Revisa el formato de la URL y el idioma seleccionado.",
  429: "Se ha alcanzado el límite de peticiones permitidas por minuto. Espera unos segundos antes de intentarlo de nuevo.",
  502: "No ha sido posible descargar el artículo o conectar con el servicio externo de síntesis de voz.",
  500: "Se ha producido un error interno en el servidor al generar el archivo de audio.",
};

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

function extractHostnameLabel(articleUrl) {
  try {
    const { hostname } = new URL(articleUrl);
    return hostname.replace(/^www\./i, "") || "articulo";
  } catch {
    return "articulo";
  }
}

function buildDownloadFilename(articleUrl, languageCode) {
  const hostSlug = extractHostnameLabel(articleUrl)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const safeHost = hostSlug || "articulo";
  return `article-${safeHost}-${languageCode}.mp3`;
}

/**
 * Interpreta los errores HTTP devueltos por FastAPI en POST /api/tts.
 * Extrae el campo `detail` del cuerpo JSON cuando está disponible y lo
 * presenta de forma comprensible sin exponer trazas internas.
 */
async function extractTtsErrorMessage(response) {
  const statusFallback =
    FALLBACK_STATUS_MESSAGES[response.status] ||
    "No se ha podido completar la conversión de audio. Inténtalo de nuevo.";

  try {
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("application/json")) {
      return statusFallback;
    }

    const payload = await response.json();

    if (typeof payload?.detail === "string" && payload.detail.trim()) {
      return `${statusFallback} (${payload.detail.trim()})`;
    }

    if (Array.isArray(payload?.detail) && payload.detail.length > 0) {
      const firstEntry = payload.detail[0];
      if (typeof firstEntry?.msg === "string" && firstEntry.msg.trim()) {
        const cleanMsg = firstEntry.msg.replace(/^Value error,\s*/i, "").trim();
        return `${statusFallback} (${cleanMsg})`;
      }
    }
  } catch {
    // Si la respuesta no contiene JSON válido, se utiliza el mensaje asociado al código HTTP.
  }

  return statusFallback;
}

function App() {
  const [url, setUrl] = useState("");
  const [languages, setLanguages] = useState([]);
  const [language, setLanguage] = useState("");
  const [isLoadingLanguages, setIsLoadingLanguages] = useState(true);
  const [languagesError, setLanguagesError] = useState("");

  const [status, setStatus] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [result, setResult] = useState(null);

  const activeAudioUrlRef = useRef(null);
  const ttsAbortControllerRef = useRef(null);

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
    return () => {
      if (ttsAbortControllerRef.current) {
        ttsAbortControllerRef.current.abort();
        ttsAbortControllerRef.current = null;
      }
      replaceAudioObjectUrl(null);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function loadLanguages() {
      setIsLoadingLanguages(true);
      setLanguagesError("");

      try {
        const response = await fetch("/api/languages", {
          method: "GET",
          headers: {
            Accept: "application/json",
          },
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Error HTTP ${response.status}`);
        }

        const data = await response.json();
        const receivedLanguages = Array.isArray(data?.languages)
          ? data.languages.filter(
              (item) =>
                item &&
                typeof item.code === "string" &&
                typeof item.name === "string" &&
                typeof item.voice === "string"
            )
          : [];

        if (receivedLanguages.length === 0) {
          throw new Error("La respuesta no contiene idiomas válidos.");
        }

        if (controller.signal.aborted) {
          return;
        }

        const initialLanguageCode =
          typeof data.default === "string" &&
          receivedLanguages.some((item) => item.code === data.default)
            ? data.default
            : receivedLanguages[0].code;

        setLanguages(receivedLanguages);
        setLanguage(initialLanguageCode);
        setIsLoadingLanguages(false);
      } catch (error) {
        if (controller.signal.aborted || error?.name === "AbortError") {
          return;
        }

        setLanguages([]);
        setLanguage("");
        setLanguagesError(
          "No se han podido cargar los idiomas disponibles. Comprueba que el servidor backend esté activo."
        );
        setIsLoadingLanguages(false);
      }
    }

    loadLanguages();

    return () => {
      controller.abort();
    };
  }, []);

  const handleConvertSubmit = async () => {
    if (
      status === "loading" ||
      isLoadingLanguages ||
      languagesError ||
      languages.length === 0 ||
      !language
    ) {
      return;
    }

    const validation = validateArticleUrl(url);
    if (!validation.isValid) {
      setErrorMessage(validation.error);
      setStatus("error");
      return;
    }

    const submittedUrl = validation.normalizedUrl;
    const submittedLanguage =
      languages.find((item) => item.code === language) || languages[0];

    if (ttsAbortControllerRef.current) {
      ttsAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    ttsAbortControllerRef.current = controller;

    setErrorMessage("");
    setStatus("loading");

    try {
      const response = await fetch("/api/tts", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "audio/mpeg",
        },
        body: JSON.stringify({
          url: submittedUrl,
          language: submittedLanguage.code,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const apiErrorMessage = await extractTtsErrorMessage(response);
        if (controller.signal.aborted) {
          return;
        }
        setErrorMessage(apiErrorMessage);
        setStatus("error");
        return;
      }

      const contentTypeHeader = (
        response.headers.get("content-type") || ""
      ).toLowerCase();
      const audioBlob = await response.blob();

      if (controller.signal.aborted) {
        return;
      }

      const blobMimeType = (audioBlob.type || "").toLowerCase();
      const isMp3Audio =
        contentTypeHeader.includes("audio/mpeg") ||
        blobMimeType.includes("audio/mpeg");

      if (!isMp3Audio || audioBlob.size === 0) {
        setErrorMessage(
          "La respuesta recibida del servidor no contiene un archivo de audio MP3 válido."
        );
        setStatus("error");
        return;
      }

      const nextAudioUrl = URL.createObjectURL(audioBlob);
      replaceAudioObjectUrl(nextAudioUrl);

      const hostLabel = extractHostnameLabel(submittedUrl);
      setResult({
        title: `Artículo convertido · ${hostLabel}`,
        sourceUrl: submittedUrl,
        languageCode: submittedLanguage.code,
        languageName: submittedLanguage.name,
        voice: submittedLanguage.voice,
        filename: buildDownloadFilename(submittedUrl, submittedLanguage.code),
        audioUrl: nextAudioUrl,
      });
      setStatus("success");
    } catch (error) {
      if (controller.signal.aborted || error?.name === "AbortError") {
        return;
      }

      setErrorMessage(
        "No se ha podido establecer conexión con el servidor para generar el audio. Comprueba que el backend esté activo."
      );
      setStatus("error");
    } finally {
      if (ttsAbortControllerRef.current === controller) {
        ttsAbortControllerRef.current = null;
      }
    }
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
              languages={languages}
              isLoadingLanguages={isLoadingLanguages}
              languagesError={languagesError}
              isLoading={status === "loading"}
              onSubmit={handleConvertSubmit}
            />

            <StatusBanner
              status={status}
              errorMessage={errorMessage}
              onDismissError={() => setStatus("idle")}
            />
          </div>

          {result && <AudioResultCard result={result} />}
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
