function Header() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <div className="brand">
          <span className="brand-icon" aria-hidden="true">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 3v18" />
              <path d="M8 7v10" />
              <path d="M16 7v10" />
              <path d="M4 11v2" />
              <path d="M20 11v2" />
            </svg>
          </span>
          <span className="brand-name">Text to Speech</span>
        </div>
        <span className="brand-badge">Neural Audio</span>
      </div>
    </header>
  );
}

export default Header;
