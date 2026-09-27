import { useState } from 'react';
import { Github, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-react';

export default function RepositoryPanel({ repoUrl, setRepoUrl, project, repoError, busy, isAnalyzing, status, onAnalyze }) {
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <section className="repository-panel" aria-label="Project context" aria-busy={isAnalyzing}>
      <div className="repository-panel-header">
        <span><Github size={16} aria-hidden="true" /> Understand your project first</span>
        <button
          type="button"
          className="repository-toggle"
          aria-expanded={!isCollapsed}
          aria-label={isCollapsed ? 'Show GitHub analysis' : 'Hide GitHub analysis'}
          onClick={() => setIsCollapsed((value) => !value)}
        >
          {isCollapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
        </button>
      </div>

      {!isCollapsed && (
        <>
          <form onSubmit={onAnalyze}>
            <div className="repository-input-row">
              <input id="repo-url" type="url" value={repoUrl} disabled={busy} required
                placeholder="https://github.com/owner/repo" aria-describedby="repo-help"
                onChange={(event) => setRepoUrl(event.target.value)} />
              <button type="submit" disabled={busy || !repoUrl.trim()}>
                {isAnalyzing ? 'Scanning…' : project ? 'Analyze again' : 'Analyze repo'}
              </button>
            </div>
            <p id="repo-help">Public GitHub repository · Read-only analysis. A new scan starts a fresh chat.</p>
            <p className="repository-scope">Frontend-focused review. Backend code, when available, is used only to understand APIs and integration.</p>
          </form>
          <div role="status" aria-live="polite">
            {isAnalyzing && <p className="repository-progress">{status === 'waking' ? 'Connecting to SALVAL…' : 'Scanning repo… finding components, hooks and styles.'}</p>}
            {!isAnalyzing && project && (
              <details className="repository-result">
                <summary><CheckCircle2 size={16} aria-hidden="true" />
                  <span>Context loaded · {project.context.stack.join(' · ') || 'Frontend project'} · {project.context.components.length} components found</span>
                </summary>
                <p className="repository-name">{project.url} · {project.files_analyzed} files analyzed</p>
                {project.context.scope && <p>{project.context.scope.frontend_files} frontend files · {project.context.scope.backend_files} backend files used as integration context</p>}
                <p>{project.context.summary}</p>
                <p>Sampled snapshot · {project.context.hooks.length} hooks · {project.context.css_tokens.length} style tokens. Ask for source-level details before changing unfamiliar code.</p>
              </details>
            )}
          </div>
          {repoError && <p className="chat-error" role="alert">{repoError}</p>}
        </>
      )}
    </section>
  );
}
