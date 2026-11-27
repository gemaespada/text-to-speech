import { useRef } from "react";

const ELEVENLABS_QUOTA_TEXT =
  "ElevenLabs utiliza una cuota gratuita limitada de 10.000 créditos mensuales. Cada conversión consume créditos según la longitud del texto. Cuando se agote la cuota, podrás seguir utilizando Edge TTS sin coste.";

const MAX_MANUAL_TEXT_CHARS = 15000;

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "0 B";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1).replace(".", ",")} KB`;
  }
  const mb = kb / 1024;
  return `${mb.toFixed(2).replace(".", ",")} MB`;
}

function ConversionForm({
  inputMode = "url",
  onInputModeChange,
  url,
  onUrlChange,
  manualText = "",
  onManualTextChange,
  selectedFile = null,
  onFileChange,
  engine,
  onEngineChange,
  engines = [],
  language,
  onLanguageChange,
  languages = [],
  voice,
  onVoiceChange,
  voices = [],
  quotaNotice = "",
  isLoadingLanguages = false,
  isSlowLanguagesLoading = false,
  languagesError = "",
  isLoading,
  onSubmit,
}) {
  const fileInputRef = useRef(null);

  const areSelectorsReady =
    !isLoadingLanguages &&
    !languagesError &&
    engines.length > 0 &&
    languages.length > 0 &&
    voices.length > 0 &&
    Boolean(engine) &&
    Boolean(language) &&
    Boolean(voice);

  const isControlDisabled = isLoading || !areSelectorsReady;

  const hasValidInputForMode =
    (inputMode === "url" && Boolean(url.trim())) ||
    (inputMode === "text" && Boolean(manualText.trim())) ||
    (inputMode === "file" && Boolean(selectedFile));

  const isSubmitDisabled = isControlDisabled || !hasValidInputForMode;
  const showElevenLabsQuota = engine === "elevenlabs";
  const manualCharCount = manualText.length;
  const isManualTextOverLimit = manualCharCount > MAX_MANUAL_TEXT_CHARS;

  const handleSubmit = (event) => {
    event.preventDefault();
    if (isSubmitDisabled) {
      return;
    }
    onSubmit();
  };

  const handleFileInputChange = (event) => {
    const file = event.target.files?.[0] || null;
    if (onFileChange) {
      onFileChange(file);
    }
  };

  const handleRemoveFile = () => {
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    if (onFileChange) {
      onFileChange(null);
    }
  };

  return (
    <form className="conversion-form" onSubmit={handleSubmit} noValidate>
      <div
        className="input-mode-tabs"
        role="tablist"
        aria-label="Modalidad de entrada del contenido"
      >
        <button
          type="button"
          role="tab"
          id="tab-mode-url"
          aria-selected={inputMode === "url"}
          aria-controls="panel-mode-url"
          className={`mode-tab-btn ${inputMode === "url" ? "active" : ""}`}
          onClick={() => onInputModeChange && onInputModeChange("url")}
          disabled={isLoading}
        >
          Desde una URL
        </button>
        <button
          type="button"
          role="tab"
          id="tab-mode-text"
          aria-selected={inputMode === "text"}
          aria-controls="panel-mode-text"
          className={`mode-tab-btn ${inputMode === "text" ? "active" : ""}`}
          onClick={() => onInputModeChange && onInputModeChange("text")}
          disabled={isLoading}
        >
          Texto manual
        </button>
        <button
          type="button"
          role="tab"
          id="tab-mode-file"
          aria-selected={inputMode === "file"}
          aria-controls="panel-mode-file"
          className={`mode-tab-btn ${inputMode === "file" ? "active" : ""}`}
          onClick={() => onInputModeChange && onInputModeChange("file")}
          disabled={isLoading}
        >
          Subir archivo
        </button>
      </div>

      {inputMode === "url" && (
        <div
          id="panel-mode-url"
          role="tabpanel"
          aria-labelledby="tab-mode-url"
          className="form-group"
        >
          <label htmlFor="article-url" className="form-label">
            URL del artículo
          </label>
          <input
            id="article-url"
            type="url"
            className="form-input"
            placeholder="https://ejemplo.com/noticia-o-articulo"
            value={url}
            onChange={(event) => onUrlChange(event.target.value)}
            disabled={isLoading}
            required
            autoComplete="url"
          />
        </div>
      )}

      {inputMode === "text" && (
        <div
          id="panel-mode-text"
          role="tabpanel"
          aria-labelledby="tab-mode-text"
          className="form-group"
        >
          <div className="form-label-row">
            <label htmlFor="manual-text" className="form-label">
              Texto para convertir a audio
            </label>
            <span
              className={`char-counter ${isManualTextOverLimit ? "over-limit" : ""}`}
            >
              {manualCharCount.toLocaleString("es-ES")} /{" "}
              {MAX_MANUAL_TEXT_CHARS.toLocaleString("es-ES")} caracteres
            </span>
          </div>
          <textarea
            id="manual-text"
            className={`form-textarea ${isManualTextOverLimit ? "input-error" : ""}`}
            rows={5}
            placeholder="Escribe o pega aquí el texto o artículo que deseas transformar en audio MP3..."
            value={manualText}
            onChange={(event) =>
              onManualTextChange && onManualTextChange(event.target.value)
            }
            disabled={isLoading}
            required
          />
        </div>
      )}

      {inputMode === "file" && (
        <div
          id="panel-mode-file"
          role="tabpanel"
          aria-labelledby="tab-mode-file"
          className="form-group"
        >
          <label htmlFor="document-file" className="form-label">
            Documento local (PDF, DOCX, TXT o EPUB · máx. 10 MB y 15.000 caracteres)
          </label>

          <input
            ref={fileInputRef}
            id="document-file"
            type="file"
            accept=".pdf,.docx,.txt,.epub,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,application/epub+zip"
            className="file-input-hidden"
            onChange={handleFileInputChange}
            disabled={isLoading}
          />

          {!selectedFile ? (
            <label
              htmlFor="document-file"
              className={`file-dropzone ${isLoading ? "disabled" : ""}`}
            >
              <span className="file-dropzone-title">
                Seleccionar archivo desde tu dispositivo
              </span>
              <span className="file-dropzone-hint">
                Formatos admitidos: PDF (con texto), DOCX, TXT y EPUB
              </span>
            </label>
          ) : (
            <div className="selected-file-card">
              <div className="selected-file-details">
                <span className="selected-file-name" title={selectedFile.name}>
                  {selectedFile.name}
                </span>
                <span className="selected-file-size">
                  {formatFileSize(selectedFile.size)}
                </span>
              </div>
              <div className="selected-file-actions">
                <button
                  type="button"
                  className="btn-remove-file"
                  onClick={handleRemoveFile}
                  disabled={isLoading}
                >
                  Eliminar archivo
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="selectors-grid">
        <div className="form-group selector-group">
          <label htmlFor="tts-engine" className="form-label">
            Motor de generación
          </label>
          <select
            id="tts-engine"
            className="form-select"
            value={areSelectorsReady ? engine : ""}
            onChange={(event) => onEngineChange(event.target.value)}
            disabled={isControlDisabled}
            aria-busy={isLoadingLanguages}
          >
            {isLoadingLanguages && (
              <option value="">Cargando motores...</option>
            )}
            {!isLoadingLanguages &&
              (languagesError || engines.length === 0) && (
                <option value="">Motores no disponibles</option>
              )}
            {!isLoadingLanguages &&
              !languagesError &&
              engines.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </div>

        <div className="form-group selector-group">
          <label htmlFor="article-language" className="form-label">
            Idioma
          </label>
          <select
            id="article-language"
            className="form-select"
            value={areSelectorsReady ? language : ""}
            onChange={(event) => onLanguageChange(event.target.value)}
            disabled={isControlDisabled}
            aria-busy={isLoadingLanguages}
          >
            {isLoadingLanguages && (
              <option value="">Cargando idiomas...</option>
            )}
            {!isLoadingLanguages &&
              (languagesError || languages.length === 0) && (
                <option value="">Idiomas no disponibles</option>
              )}
            {!isLoadingLanguages &&
              !languagesError &&
              languages.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name} ({item.code.toUpperCase()})
                </option>
              ))}
          </select>
        </div>

        <div className="form-group selector-group">
          <label htmlFor="tts-voice" className="form-label">
            Voz
          </label>
          <select
            id="tts-voice"
            className="form-select"
            value={areSelectorsReady ? voice : ""}
            onChange={(event) => onVoiceChange(event.target.value)}
            disabled={isControlDisabled}
            aria-busy={isLoadingLanguages}
          >
            {isLoadingLanguages && <option value="">Cargando voces...</option>}
            {!isLoadingLanguages &&
              (languagesError || voices.length === 0) && (
                <option value="">Voces no disponibles</option>
              )}
            {!isLoadingLanguages &&
              !languagesError &&
              voices.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </div>
      </div>

      {showElevenLabsQuota && (
        <div
          className="quota-notice"
          role="note"
          aria-label="Información sobre la cuota gratuita de ElevenLabs"
        >
          <div className="quota-notice-icon" aria-hidden="true">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
          </div>
          <p className="quota-notice-text">
            {quotaNotice || ELEVENLABS_QUOTA_TEXT}
          </p>
        </div>
      )}

      <div className="form-submit-row">
        <button
          type="submit"
          className="btn-primary"
          disabled={isSubmitDisabled}
        >
          {isLoading ? (
            <>
              <span className="spinner" aria-hidden="true" />
              <span>Generando audio...</span>
            </>
          ) : (
            <>
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <span>Generar audio</span>
            </>
          )}
        </button>
      </div>

      {isLoadingLanguages && (
        <div
          className="field-feedback field-feedback-loading"
          role="status"
          aria-live="polite"
        >
          <span className="spinner spinner-dark spinner-sm" aria-hidden="true" />
          <span>
            {isSlowLanguagesLoading
              ? "El servidor se está iniciando. Puede tardar unos segundos; estamos preparando la conversión."
              : "Cargando motores, idiomas y voces disponibles..."}
          </span>
        </div>
      )}

      {!isLoadingLanguages && languagesError && (
        <div className="field-feedback field-feedback-error" role="alert">
          <span>{languagesError}</span>
        </div>
      )}
    </form>
  );
}

export default ConversionForm;
