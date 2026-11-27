const SLOW_SERVER_WAKEUP_TEXT =
  "El servidor se está iniciando. Puede tardar unos segundos; estamos preparando la conversión.";

function StatusBanner({
  status,
  isSlowLoading = false,
  errorMessage,
  onDismissError,
  onSwitchToEdge,
  onSwitchInputMode,
}) {
  if (status === "idle") {
    return null;
  }

  if (status === "loading") {
    return (
      <div
        className="status-banner status-loading"
        role="status"
        aria-live="polite"
      >
        <span className="spinner spinner-dark" aria-hidden="true" />
        <div className="status-content">
          <p className="status-title">
            {isSlowLoading
              ? SLOW_SERVER_WAKEUP_TEXT
              : "Procesando contenido y generando voz neuronal..."}
          </p>
          <p className="status-subtitle">
            {isSlowLoading
              ? "El servicio en la nube puede tardar unos instantes en despertar tras un periodo de inactividad. No es necesario volver a pulsar el botón."
              : "Estamos preparando el texto y sintetizando el archivo MP3."}
          </p>
        </div>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="status-banner status-error" role="alert">
        <div className="status-icon" aria-hidden="true">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>
        <div className="status-content">
          <p className="status-title">No se pudo completar la conversión</p>
          <p className="status-subtitle">
            {errorMessage ||
              "Comprueba los datos introducidos y vuelve a intentarlo."}
          </p>
          {(onSwitchToEdge || onSwitchInputMode) && (
            <div className="status-actions">
              {onSwitchToEdge && (
                <button
                  type="button"
                  className="btn-switch-engine"
                  onClick={onSwitchToEdge}
                >
                  Cambiar a Edge TTS (Gratuito)
                </button>
              )}
              {onSwitchInputMode && (
                <>
                  <button
                    type="button"
                    className="btn-switch-engine"
                    onClick={() => onSwitchInputMode("text")}
                  >
                    Usar texto manual
                  </button>
                  <button
                    type="button"
                    className="btn-switch-engine"
                    onClick={() => onSwitchInputMode("file")}
                  >
                    Subir un archivo
                  </button>
                </>
              )}
            </div>
          )}
        </div>
        {onDismissError && (
          <button
            type="button"
            className="status-close-btn"
            onClick={onDismissError}
            aria-label="Cerrar mensaje de error"
          >
            ✕
          </button>
        )}
      </div>
    );
  }

  if (status === "success") {
    return (
      <div
        className="status-banner status-success"
        role="status"
        aria-live="polite"
      >
        <div className="status-icon" aria-hidden="true">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
          </svg>
        </div>
        <div className="status-content">
          <p className="status-title">Conversión completada</p>
          <p className="status-subtitle">
            Tu archivo de audio MP3 está listo para reproducir o descargar.
          </p>
        </div>
      </div>
    );
  }

  return null;
}

export default StatusBanner;
