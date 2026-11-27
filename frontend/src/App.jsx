import { useEffect, useMemo, useRef, useState } from "react";
import Header from "./components/Header";
import HeroSection from "./components/HeroSection";
import ConversionForm from "./components/ConversionForm";
import StatusBanner from "./components/StatusBanner";
import AudioResultCard from "./components/AudioResultCard";

const RAW_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";
const API_BASE_URL = RAW_API_BASE_URL.trim()
  .replace(/\/+$/, "")
  .replace(/\/api$/i, "");

/**
 * Construye la URL completa del endpoint de la API utilizando VITE_API_BASE_URL
 * en producción, o manteniendo la ruta relativa `/api/...` en desarrollo local
 * para aprovechar el proxy de Vite.
 */
function buildApiUrl(endpointPath) {
  const normalizedPath = endpointPath.startsWith("/")
    ? endpointPath
    : `/${endpointPath}`;
  return API_BASE_URL ? `${API_BASE_URL}${normalizedPath}` : normalizedPath;
}

const FALLBACK_STATUS_MESSAGES = {
  400: "La petición o la URL proporcionada no es válida. Asegúrate de que sea un artículo público con texto legible.",
  401: "La clave ELEVENLABS_API_KEY no es válida o no está autorizada. Revisa tu configuración o cambia a Edge TTS.",
  402: "La cuota de créditos de ElevenLabs se ha agotado o la voz seleccionada requiere un plan de pago. Puedes cambiar a Edge TTS sin coste.",
  403: "ElevenLabs ha rechazado la petición desde este entorno. Puedes cambiar al motor gratuito Edge TTS.",
  413: "El artículo o la página web supera el tamaño máximo permitido para la conversión (15.000 caracteres / 5 MB HTML).",
  422: "Los datos enviados no son válidos. Revisa la URL, el motor, el idioma y la voz seleccionados.",
  429: "Se ha alcanzado el límite de peticiones permitidas. Espera unos segundos antes de intentarlo de nuevo.",
  502: "No ha sido posible descargar el artículo o conectar con el proveedor de síntesis de voz.",
  503: "El motor de síntesis seleccionado no está disponible o no tiene configurada su clave API.",
  504: "El proveedor de síntesis de voz tardó demasiado tiempo en responder (timeout).",
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

function buildDownloadFilename(articleUrl, engineId, languageCode) {
  const hostSlug = extractHostnameLabel(articleUrl)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const safeHost = hostSlug || "articulo";
  return `article-${safeHost}-${engineId}-${languageCode}.mp3`;
}

/**
 * Normaliza la respuesta de GET /api/languages para construir el catálogo
 * completo de motores, idiomas y voces compatibles.
 */
function normalizeEnginesCatalog(data) {
  if (Array.isArray(data?.engines) && data.engines.length > 0) {
    const validEngines = data.engines.filter(
      (eng) =>
        eng &&
        typeof eng.id === "string" &&
        typeof eng.name === "string" &&
        Array.isArray(eng.languages) &&
        eng.languages.length > 0
    );
    if (validEngines.length > 0) {
      return validEngines;
    }
  }

  // Compatibilidad hacia atrás si solo se recibe `languages` plano
  const legacyLanguages = Array.isArray(data?.languages)
    ? data.languages.filter(
        (item) =>
          item &&
          typeof item.code === "string" &&
          typeof item.name === "string" &&
          typeof item.voice === "string"
      )
    : [];

  if (legacyLanguages.length === 0) {
    return [];
  }

  return [
    {
      id: "edge",
      name: "Edge TTS (Gratuito)",
      description: "Síntesis neuronal gratuita mediante Microsoft Edge TTS.",
      configured: true,
      requires_api_key: false,
      model_id: "edge-neural",
      quota_notice: null,
      default_language:
        typeof data?.default === "string" ? data.default : legacyLanguages[0].code,
      languages: legacyLanguages.map((item) => ({
        code: item.code,
        name: item.name,
        default_voice: item.voice,
        voices: [
          {
            id: item.voice,
            name: item.voice,
            language: item.code,
            engine: "edge",
          },
        ],
      })),
    },
  ];
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
      const detailText = payload.detail.trim();
      // Si el backend ya devuelve un mensaje explicativo en castellano (como los errores de ElevenLabs), lo mostramos directamente
      if (
        /[áéíóúñ¿¡]/i.test(detailText) ||
        detailText.includes("ElevenLabs") ||
        detailText.includes("Edge TTS")
      ) {
        return detailText;
      }
      return `${statusFallback} (${detailText})`;
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
  const [engines, setEngines] = useState([]);
  const [engine, setEngine] = useState("");
  const [language, setLanguage] = useState("");
  const [voice, setVoice] = useState("");
  const [isLoadingLanguages, setIsLoadingLanguages] = useState(true);
  const [languagesError, setLanguagesError] = useState("");

  const [status, setStatus] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [failedEngine, setFailedEngine] = useState("");
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
        const response = await fetch(buildApiUrl("/api/languages"), {
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
        const catalog = normalizeEnginesCatalog(data);

        if (catalog.length === 0) {
          throw new Error("La respuesta no contiene motores ni idiomas válidos.");
        }

        if (controller.signal.aborted) {
          return;
        }

        const initialEngineObj =
          catalog.find((item) => item.id === data?.default_engine) || catalog[0];

        const preferredLangCode =
          initialEngineObj.default_language || data?.default || "es";
        const initialLangObj =
          initialEngineObj.languages.find(
            (item) => item.code === preferredLangCode
          ) || initialEngineObj.languages[0];

        const initialVoiceId =
          initialLangObj.default_voice ||
          initialLangObj.voices?.[0]?.id ||
          "";

        setEngines(catalog);
        setEngine(initialEngineObj.id);
        setLanguage(initialLangObj.code);
        setVoice(initialVoiceId);
        setIsLoadingLanguages(false);
      } catch (error) {
        if (controller.signal.aborted || error?.name === "AbortError") {
          return;
        }

        setEngines([]);
        setEngine("");
        setLanguage("");
        setVoice("");
        setLanguagesError(
          "No se han podido cargar los motores e idiomas disponibles. Comprueba que el servidor backend esté activo."
        );
        setIsLoadingLanguages(false);
      }
    }

    loadLanguages();

    return () => {
      controller.abort();
    };
  }, []);

  const currentEngineObj = useMemo(
    () => engines.find((item) => item.id === engine) || engines[0] || null,
    [engines, engine]
  );

  const availableLanguages = useMemo(
    () => currentEngineObj?.languages || [],
    [currentEngineObj]
  );

  const currentLanguageObj = useMemo(
    () =>
      availableLanguages.find((item) => item.code === language) ||
      availableLanguages[0] ||
      null,
    [availableLanguages, language]
  );

  const availableVoices = useMemo(
    () => currentLanguageObj?.voices || [],
    [currentLanguageObj]
  );

  const handleEngineChange = (nextEngineId) => {
    const targetEngine = engines.find((item) => item.id === nextEngineId);
    if (!targetEngine) {
      return;
    }

    const matchingLang =
      targetEngine.languages.find((item) => item.code === language) ||
      targetEngine.languages.find(
        (item) => item.code === targetEngine.default_language
      ) ||
      targetEngine.languages[0];

    const nextLangCode = matchingLang ? matchingLang.code : "";
    const nextVoiceId =
      matchingLang?.default_voice || matchingLang?.voices?.[0]?.id || "";

    setEngine(targetEngine.id);
    setLanguage(nextLangCode);
    setVoice(nextVoiceId);
  };

  const handleLanguageChange = (nextLanguageCode) => {
    if (!currentEngineObj) {
      return;
    }

    const targetLang =
      currentEngineObj.languages.find(
        (item) => item.code === nextLanguageCode
      ) || currentEngineObj.languages[0];

    if (!targetLang) {
      return;
    }

    const nextVoiceId =
      targetLang.default_voice || targetLang.voices?.[0]?.id || "";

    setLanguage(targetLang.code);
    setVoice(nextVoiceId);
  };

  const handleManualSwitchToEdge = () => {
    handleEngineChange("edge");
    setFailedEngine("");
    setErrorMessage("");
    setStatus("idle");
  };

  const handleConvertSubmit = async () => {
    if (
      status === "loading" ||
      isLoadingLanguages ||
      languagesError ||
      !currentEngineObj ||
      !currentLanguageObj ||
      !voice
    ) {
      return;
    }

    const validation = validateArticleUrl(url);
    if (!validation.isValid) {
      setFailedEngine("");
      setErrorMessage(validation.error);
      setStatus("error");
      return;
    }

    const submittedUrl = validation.normalizedUrl;
    const submittedEngine = currentEngineObj;
    const submittedLanguage = currentLanguageObj;
    const submittedVoice =
      availableVoices.find((item) => item.id === voice) ||
      availableVoices[0] || { id: voice, name: voice };

    if (ttsAbortControllerRef.current) {
      ttsAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    ttsAbortControllerRef.current = controller;

    setFailedEngine("");
    setErrorMessage("");
    setStatus("loading");

    try {
      const response = await fetch(buildApiUrl("/api/tts"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "audio/mpeg",
        },
        body: JSON.stringify({
          url: submittedUrl,
          engine: submittedEngine.id,
          language: submittedLanguage.code,
          voice: submittedVoice.id,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const apiErrorMessage = await extractTtsErrorMessage(response);
        if (controller.signal.aborted) {
          return;
        }
        setFailedEngine(submittedEngine.id);
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
        setFailedEngine(submittedEngine.id);
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
        engineId: submittedEngine.id,
        engineName: submittedEngine.name,
        languageCode: submittedLanguage.code,
        languageName: submittedLanguage.name,
        voice: submittedVoice.id,
        voiceName: submittedVoice.name,
        filename: buildDownloadFilename(
          submittedUrl,
          submittedEngine.id,
          submittedLanguage.code
        ),
        audioUrl: nextAudioUrl,
      });
      setStatus("success");
    } catch (error) {
      if (controller.signal.aborted || error?.name === "AbortError") {
        return;
      }

      setFailedEngine(submittedEngine.id);
      setErrorMessage(
        submittedEngine.id === "elevenlabs"
          ? "No se ha podido completar la conexión con el servidor durante la conversión con ElevenLabs. Puedes volver a intentarlo o cambiar manualmente a Edge TTS."
          : "No se ha podido establecer conexión con el servidor para generar el audio. Comprueba que el backend esté activo."
      );
      setStatus("error");
    } finally {
      if (ttsAbortControllerRef.current === controller) {
        ttsAbortControllerRef.current = null;
      }
    }
  };

  const canOfferEdgeSwitch =
    status === "error" &&
    (failedEngine === "elevenlabs" || engine === "elevenlabs") &&
    engines.some((item) => item.id === "edge");

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
              engine={engine}
              onEngineChange={handleEngineChange}
              engines={engines}
              language={language}
              onLanguageChange={handleLanguageChange}
              languages={availableLanguages}
              voice={voice}
              onVoiceChange={setVoice}
              voices={availableVoices}
              quotaNotice={currentEngineObj?.quota_notice || ""}
              isLoadingLanguages={isLoadingLanguages}
              languagesError={languagesError}
              isLoading={status === "loading"}
              onSubmit={handleConvertSubmit}
            />

            <StatusBanner
              status={status}
              errorMessage={errorMessage}
              onDismissError={() => {
                setFailedEngine("");
                setStatus("idle");
              }}
              onSwitchToEdge={
                canOfferEdgeSwitch ? handleManualSwitchToEdge : null
              }
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
