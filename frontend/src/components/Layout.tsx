import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { apiConfig } from "../config";

export const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/repositories", label: "Repository Explorer", end: false },
  { to: "/issues", label: "Issues & Backlog", end: false },
  { to: "/pull-requests", label: "Pull Requests", end: false },
  { to: "/quality", label: "Data Quality & Runs", end: false },
  { to: "/about", label: "About & Sources", end: false },
] as const;

export function Layout({ children }: { children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMenuOpen(false);
    window.scrollTo?.(0, 0);
  }, [location.pathname]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="OSS Project Health home">
          <span className="brand-mark" aria-hidden="true">
            ◆
          </span>
          <span>OSS Project Health</span>
        </NavLink>
        <button
          type="button"
          className="menu-button"
          aria-expanded={menuOpen}
          aria-controls="primary-nav"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span aria-hidden="true">{menuOpen ? "✕" : "☰"}</span>
          <span className="sr-only">{menuOpen ? "Close menu" : "Open menu"}</span>
        </button>
      </header>
      <nav id="primary-nav" className={`sidebar ${menuOpen ? "open" : ""}`} aria-label="Primary">
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : undefined)}>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="sidebar-note small">
          Public, read-only analytics. Data: GitHub REST API. All dates in UTC.
        </p>
      </nav>
      <main id="main" className="content" tabIndex={-1}>
        {apiConfig.error ? (
          <div className="banner banner-bad" role="alert">
            <strong>Configuration problem:</strong> {apiConfig.error}
          </div>
        ) : null}
        {children}
        <footer className="footer small muted">
          Data from the public GitHub REST API · Not affiliated with GitHub or the listed projects ·{" "}
          <NavLink to="/about">Methodology &amp; limitations</NavLink>
        </footer>
      </main>
    </div>
  );
}
