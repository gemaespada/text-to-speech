const ELEVENLABS_QUOTA_TEXT =
  "ElevenLabs utiliza una cuota gratuita limitada de 10.000 créditos mensuales. Cada conversión consume créditos según la longitud del texto. Cuando se agote la cuota, podrás seguir utilizando Edge TTS sin coste.";

function ConversionForm({
  url,
  onUrlChange,
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
  languagesError = "",
  isLoading,
  onSubmit,
}) {
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
  const isSubmitDisabled = isControlDisabled || !url.trim();
  const showElevenLabsQuota = engine === "elevenlabs";

  const handleSubmit = (event) => {
    event.preventDefault();
    if (isSubmitDisabled) {
      return;
    }
    onSubmit();
  };

  return (
    <form className="conversion-form" onSubmit={handleSubmit} noValidate>
      <div className="form-group">
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
          <span>Cargando motores, idiomas y voces disponibles...</span>
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
