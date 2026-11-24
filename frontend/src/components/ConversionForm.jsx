function ConversionForm({
  url,
  onUrlChange,
  language,
  onLanguageChange,
  languages = [],
  isLoadingLanguages = false,
  languagesError = "",
  isLoading,
  onSubmit,
}) {
  const areLanguagesReady =
    !isLoadingLanguages && !languagesError && languages.length > 0 && Boolean(language);

  const isSelectDisabled = isLoading || !areLanguagesReady;
  const isSubmitDisabled = isLoading || !areLanguagesReady || !url.trim();

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

      <div className="form-row">
        <div className="form-group language-group">
          <label htmlFor="article-language" className="form-label">
            Idioma de lectura
          </label>
          <select
            id="article-language"
            className="form-select"
            value={areLanguagesReady ? language : ""}
            onChange={(event) => onLanguageChange(event.target.value)}
            disabled={isSelectDisabled}
            aria-busy={isLoadingLanguages}
            aria-invalid={Boolean(languagesError) || undefined}
          >
            {isLoadingLanguages && (
              <option value="">Cargando idiomas disponibles...</option>
            )}

            {!isLoadingLanguages && (languagesError || languages.length === 0) && (
              <option value="">No se han podido cargar los idiomas</option>
            )}

            {!isLoadingLanguages &&
              !languagesError &&
              languages.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.name} ({item.code.toUpperCase()}) — {item.voice}
                </option>
              ))}
          </select>
        </div>

        <div className="form-submit-wrapper">
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
      </div>

      {isLoadingLanguages && (
        <div
          className="field-feedback field-feedback-loading"
          role="status"
          aria-live="polite"
        >
          <span className="spinner spinner-dark spinner-sm" aria-hidden="true" />
          <span>Cargando idiomas disponibles...</span>
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
