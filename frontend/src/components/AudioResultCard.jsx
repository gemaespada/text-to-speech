import { useEffect, useState } from "react";

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function AudioResultCard({ result }) {
  const [detectedDuration, setDetectedDuration] = useState(null);
  const audioUrl = result?.audioUrl || null;

  useEffect(() => {
    setDetectedDuration(null);
  }, [audioUrl]);

  if (!result) {
    return null;
  }

  const handleLoadedMetadata = (event) => {
    const formatted = formatDuration(event.currentTarget.duration);
    if (formatted) {
      setDetectedDuration(formatted);
    }
  };

  const displayDuration = detectedDuration || result.durationLabel || null;
  const downloadFilename = result.filename || "article.mp3";

  return (
    <section className="result-card" aria-label="Resultado de audio generado">
      <div className="result-header">
        <div className="result-info">
          <span className="result-tag">{result.languageName}</span>
          <h2 className="result-title">{result.title}</h2>
          <p className="result-source" title={result.sourceUrl}>
            {result.sourceUrl}
          </p>
        </div>

        {displayDuration && (
          <div className="duration-badge" title="Duración del audio">
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <span>{displayDuration}</span>
          </div>
        )}
      </div>

      <div className="audio-player-wrapper">
        <audio
          key={result.audioUrl}
          className="audio-player"
          controls
          preload="metadata"
          src={result.audioUrl}
          onLoadedMetadata={handleLoadedMetadata}
        >
          Tu navegador no soporta el elemento de audio.
        </audio>
      </div>

      <div className="result-actions">
        <span className="voice-meta">Voz neuronal: {result.voice}</span>
        <a
          href={result.audioUrl}
          download={downloadFilename}
          className="btn-download"
        >
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          <span>Descargar MP3</span>
        </a>
      </div>
    </section>
  );
}

export default AudioResultCard;
