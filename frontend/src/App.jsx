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

const MAX_TEXT_CHARS = 15000;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB
const ALLOWED_FILE_EXTENSIONS = [".pdf", ".docx", ".txt", ".epub"];
const SLOW_LOADING_THRESHOLD_MS = 4000;
const CONVERSION_TIMEOUT_MS = 120000;
const LANGUAGES_TIMEOUT_MS = 65000;

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
  400: "No se ha podido procesar el contenido proporcionado. Verifica los datos e inténtalo de nuevo.",
  401: "La clave ELEVENLABS_API_KEY no es válida o no está autorizada. Revisa tu configuración o cambia a Edge TTS.",
  402: "La cuota de créditos de ElevenLabs se ha agotado o la voz seleccionada requiere un plan de pago. Puedes cambiar a Edge TTS sin coste.",
  403: "ElevenLabs ha rechazado la petición desde este entorno. Puedes cambiar al motor gratuito Edge TTS.",
  413: "El contenido supera el límite máximo permitido (15.000 caracteres de texto o 10 MB de archivo).",
  422: "Los datos enviados no son válidos. Revisa el contenido, el motor, el idioma y la voz seleccionados.",
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

function validateManualText(rawText) {
  const trimmed = (rawText || "").trim();
  if (!trimmed) {
    return {
      isValid: false,
      cleanedText: "",
      error: "Introduce o pega el texto que deseas convertir a audio.",
    };
  }

  if (trimmed.length > MAX_TEXT_CHARS) {
    return {
      isValid: false,
      cleanedText: "",
      error: `El texto introducido (${trimmed.length.toLocaleString("es-ES")} caracteres) supera el límite máximo de ${MAX_TEXT_CHARS.toLocaleString("es-ES")} caracteres.`,
    };
  }

  return {
    isValid: true,
    cleanedText: trimmed,
    error: "",
  };
}

function validateSelectedFile(file) {
  if (!file) {
    return {
      isValid: false,
      error: "Selecciona un archivo (.pdf, .docx, .txt o .epub) desde tu dispositivo.",
    };
  }

  const fileName = (file.name || "").trim();
  const dotIndex = fileName.lastIndexOf(".");
  const ext = dotIndex >= 0 ? fileName.slice(dotIndex).toLowerCase() : "";

  if (!ALLOWED_FILE_EXTENSIONS.includes(ext)) {
    return {
      isValid: false,
      error: `El formato del archivo ('${ext || "sin extensión"}') no es compatible. Formatos admitidos: PDF, DOCX, TXT y EPUB.`,
    };
  }

  if (file.size === 0) {
    return {
      isValid: false,
      error: "El archivo seleccionado está vacío (0 bytes).",
    };
  }

  if (file.size > MAX_UPLOAD_BYTES) {
    return {
      isValid: false,
      error: "El archivo seleccionado supera el tamaño máximo permitido de 10 MB.",
    };
  }

  return {
    isValid: true,
    error: "",
  };
}

function extractHostnameLabel(articleUrl) {
  try {
    const { hostname } = new URL(articleUrl);
    return hostname.replace(/^www\./i, "") || "articulo";
  } catch {
    return "articulo";
  }
}

function slugifyLabel(rawLabel) {
  const slug = (rawLabel || "")
    .toLowerCase()
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.slice(0, 36) || "audio";
}

function buildDownloadFilename(sourceLabel, engineId, languageCode) {
  const safeSlug = slugifyLabel(sourceLabel);
  return `tts-${safeSlug}-${engineId}-${languageCode}.mp3`;
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
 * Interpreta los errores HTTP devueltos por FastAPI.
 * Extrae el campo `detail` del cuerpo JSON cuando está disponible y lo
 * presenta de forma comprensible sin exponer trazas internas.
 */
async function extractTtsErrorMessage(response, mode) {
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
      if (
        /[áéíóúñ¿¡]/i.test(detailText) ||
        detailText.includes("ElevenLabs") ||
        detailText.includes("Edge TTS") ||
        detailText.includes("PDF") ||
        detailText.includes("DOCX") ||
        detailText.includes("EPUB") ||
        detailText.includes("archivo")
      ) {
        return detailText;
      }

      if (mode === "url" && (response.status === 400 || response.status === 502)) {
        return "No se ha podido extraer el contenido del artículo desde esa URL (la página puede bloquear la extracción automática o no contener texto legible). Puedes cambiar a «Texto manual» o «Subir archivo» conservando tu configuración de voz.";
      }

      if (response.status === 413) {
        return "El contenido extraído supera el límite máximo permitido de 15.000 caracteres.";
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
  const [inputMode, setInputMode] = useState("url");
  const [url, setUrl] = useState("");
  const [manualText, setManualText] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);

  const [engines, setEngines] = useState([]);
  const [engine, setEngine] = useState("");
  const [language, setLanguage] = useState("");
  const [voice, setVoice] = useState("");
  const [isLoadingLanguages, setIsLoadingLanguages] = useState(true);
  const [isSlowLanguagesLoading, setIsSlowLanguagesLoading] = useState(false);
  const [languagesError, setLanguagesError] = useState("");

  const [status, setStatus] = useState("idle");
  const [isSlowLoading, setIsSlowLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [failedEngine, setFailedEngine] = useState("");
  const [failedInputMode, setFailedInputMode] = useState("");
  const [result, setResult] = useState(null);

  const activeAudioUrlRef = useRef(null);
  const ttsAbortControllerRef = useRef(null);
  const slowConversionTimerRef = useRef(null);
  const conversionTimeoutTimerRef = useRef(null);

  const clearConversionTimers = () => {
    if (slowConversionTimerRef.current) {
      clearTimeout(slowConversionTimerRef.current);
      slowConversionTimerRef.current = null;
    }
    if (conversionTimeoutTimerRef.current) {
      clearTimeout(conversionTimeoutTimerRef.current);
      conversionTimeoutTimerRef.current = null;
    }
  };

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
      clearConversionTimers();
      if (ttsAbortControllerRef.current) {
        ttsAbortControllerRef.current.abort();
        ttsAbortControllerRef.current = null;
      }
      replaceAudioObjectUrl(null);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timedOut = false;

    const slowTimer = setTimeout(() => {
      if (!controller.signal.aborted) {
        setIsSlowLanguagesLoading(true);
      }
    }, SLOW_LOADING_THRESHOLD_MS);

    const maxWaitTimer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, LANGUAGES_TIMEOUT_MS);

    async function loadLanguages() {
      setIsLoadingLanguages(true);
      setIsSlowLanguagesLoading(false);
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
        setIsSlowLanguagesLoading(false);
      } catch (error) {
        if (controller.signal.aborted && !timedOut) {
          return;
        }

        setEngines([]);
        setEngine("");
        setLanguage("");
        setVoice("");
        setLanguagesError(
          timedOut
            ? "El servidor ha tardado demasiado tiempo en responder al iniciarse. Recarga la página en unos segundos."
            : "No se han podido cargar los motores e idiomas disponibles. Comprueba que el servidor backend esté activo."
        );
        setIsLoadingLanguages(false);
        setIsSlowLanguagesLoading(false);
      } finally {
        clearTimeout(slowTimer);
        clearTimeout(maxWaitTimer);
      }
    }

    loadLanguages();

    return () => {
      clearTimeout(slowTimer);
      clearTimeout(maxWaitTimer);
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

  const handleInputModeChange = (nextMode) => {
    if (status === "loading") {
      return;
    }
    setInputMode(nextMode);
    if (status === "error") {
      setFailedInputMode("");
      setErrorMessage("");
      setStatus("idle");
    }
  };

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
    setFailedInputMode("");
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

    const submittedMode = inputMode;
    const submittedEngine = currentEngineObj;
    const submittedLanguage = currentLanguageObj;
    const submittedVoice =
      availableVoices.find((item) => item.id === voice) ||
      availableVoices[0] || { id: voice, name: voice };

    let endpointPath = "/api/tts";
    let fetchHeaders = { Accept: "audio/mpeg" };
    let fetchBody = null;
    let resultTitle = "";
    let resultSourceLabel = "";
    let downloadSlugSource = "";

    if (submittedMode === "url") {
      const validation = validateArticleUrl(url);
      if (!validation.isValid) {
        setFailedEngine("");
        setFailedInputMode("");
        setErrorMessage(validation.error);
        setStatus("error");
        return;
      }
      const hostLabel = extractHostnameLabel(validation.normalizedUrl);
      endpointPath = "/api/tts";
      fetchHeaders["Content-Type"] = "application/json";
      fetchBody = JSON.stringify({
        url: validation.normalizedUrl,
        engine: submittedEngine.id,
        language: submittedLanguage.code,
        voice: submittedVoice.id,
      });
      resultTitle = `Artículo convertido · ${hostLabel}`;
      resultSourceLabel = validation.normalizedUrl;
      downloadSlugSource = hostLabel;
    } else if (submittedMode === "text") {
      const validation = validateManualText(manualText);
      if (!validation.isValid) {
        setFailedEngine("");
        setFailedInputMode("");
        setErrorMessage(validation.error);
        setStatus("error");
        return;
      }
      endpointPath = "/api/tts/text";
      fetchHeaders["Content-Type"] = "application/json";
      fetchBody = JSON.stringify({
        text: validation.cleanedText,
        engine: submittedEngine.id,
        language: submittedLanguage.code,
        voice: submittedVoice.id,
      });
      const previewSnippet =
        validation.cleanedText.length > 60
          ? `${validation.cleanedText.slice(0, 60)}...`
          : validation.cleanedText;
      resultTitle = "Texto manual convertido a audio";
      resultSourceLabel = `Texto manual (${validation.cleanedText.length.toLocaleString("es-ES")} caracteres): "${previewSnippet}"`;
      downloadSlugSource = "texto-manual";
    } else if (submittedMode === "file") {
      const validation = validateSelectedFile(selectedFile);
      if (!validation.isValid) {
        setFailedEngine("");
        setFailedInputMode("");
        setErrorMessage(validation.error);
        setStatus("error");
        return;
      }
      endpointPath = "/api/tts/file";
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("engine", submittedEngine.id);
      formData.append("language", submittedLanguage.code);
      formData.append("voice", submittedVoice.id);
      fetchBody = formData;
      resultTitle = `Documento convertido · ${selectedFile.name}`;
      resultSourceLabel = `Archivo local: ${selectedFile.name}`;
      downloadSlugSource = selectedFile.name;
    } else {
      return;
    }

    clearConversionTimers();
    if (ttsAbortControllerRef.current) {
      ttsAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    ttsAbortControllerRef.current = controller;
    let requestTimedOut = false;

    setFailedEngine("");
    setFailedInputMode("");
    setErrorMessage("");
    setIsSlowLoading(false);
    setStatus("loading");

    slowConversionTimerRef.current = setTimeout(() => {
      if (!controller.signal.aborted) {
        setIsSlowLoading(true);
      }
    }, SLOW_LOADING_THRESHOLD_MS);

    conversionTimeoutTimerRef.current = setTimeout(() => {
      requestTimedOut = true;
      controller.abort();
    }, CONVERSION_TIMEOUT_MS);

    try {
      const response = await fetch(buildApiUrl(endpointPath), {
        method: "POST",
        headers: fetchHeaders,
        body: fetchBody,
        signal: controller.signal,
      });

      if (!response.ok) {
        const apiErrorMessage = await extractTtsErrorMessage(
          response,
          submittedMode
        );
        if (controller.signal.aborted) {
          return;
        }
        setFailedEngine(submittedEngine.id);
        setFailedInputMode(submittedMode);
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
        setFailedInputMode(submittedMode);
        setErrorMessage(
          "La respuesta recibida del servidor no contiene un archivo de audio MP3 válido."
        );
        setStatus("error");
        return;
      }

      const nextAudioUrl = URL.createObjectURL(audioBlob);
      replaceAudioObjectUrl(nextAudioUrl);

      setResult({
        title: resultTitle,
        sourceUrl: resultSourceLabel,
        engineId: submittedEngine.id,
        engineName: submittedEngine.name,
        languageCode: submittedLanguage.code,
        languageName: submittedLanguage.name,
        voice: submittedVoice.id,
        voiceName: submittedVoice.name,
        filename: buildDownloadFilename(
          downloadSlugSource,
          submittedEngine.id,
          submittedLanguage.code
        ),
        audioUrl: nextAudioUrl,
      });
      setStatus("success");
    } catch (error) {
      if (controller.signal.aborted && !requestTimedOut) {
        return;
      }

      setFailedEngine(submittedEngine.id);
      setFailedInputMode(submittedMode);

      if (requestTimedOut) {
        setErrorMessage(
          "El servidor ha superado el tiempo máximo de espera (120 s). Si el servicio estaba suspendido por inactividad, ya debería estar terminando de arrancar; espera unos segundos y vuelve a intentarlo."
        );
      } else {
        setErrorMessage(
          submittedEngine.id === "elevenlabs"
            ? "No se ha podido completar la conexión con el servidor durante la conversión con ElevenLabs. Puedes volver a intentarlo o cambiar manualmente a Edge TTS."
            : "No se ha podido establecer conexión con el servidor para generar el audio. Comprueba que el backend esté activo."
        );
      }
      setStatus("error");
    } finally {
      clearConversionTimers();
      setIsSlowLoading(false);
      if (ttsAbortControllerRef.current === controller) {
        ttsAbortControllerRef.current = null;
      }
    }
  };

  const canOfferEdgeSwitch =
    status === "error" &&
    (failedEngine === "elevenlabs" || engine === "elevenlabs") &&
    engines.some((item) => item.id === "edge");

  const canOfferAlternativeModeSwitch =
    status === "error" && failedInputMode === "url" && inputMode === "url";

  return (
    <div className="app-shell">
      <Header />

      <main className="main-container">
        <HeroSection />

        <div className="workspace-panel">
          <div className="main-card">
            <ConversionForm
              inputMode={inputMode}
              onInputModeChange={handleInputModeChange}
              url={url}
              onUrlChange={setUrl}
              manualText={manualText}
              onManualTextChange={setManualText}
              selectedFile={selectedFile}
              onFileChange={setSelectedFile}
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
              isSlowLanguagesLoading={isSlowLanguagesLoading}
              languagesError={languagesError}
              isLoading={status === "loading"}
              onSubmit={handleConvertSubmit}
            />

            <StatusBanner
              status={status}
              isSlowLoading={isSlowLoading}
              errorMessage={errorMessage}
              onDismissError={() => {
                setFailedEngine("");
                setFailedInputMode("");
                setStatus("idle");
              }}
              onSwitchToEdge={
                canOfferEdgeSwitch ? handleManualSwitchToEdge : null
              }
              onSwitchInputMode={
                canOfferAlternativeModeSwitch ? handleInputModeChange : null
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
