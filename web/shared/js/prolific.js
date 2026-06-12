/* prolific.js — capture Prolific URL parameters.
 * Prolific appends ?PROLIFIC_PID=...&STUDY_ID=...&SESSION_ID=... to the study URL.
 * The raw PID is sent once to the server, which hashes it immediately (SHA-256 +
 * off-server salt); the browser never persists it. */
window.NHB = window.NHB || {};
NHB.prolific = (function () {
  const p = new URLSearchParams(location.search);
  return {
    pid: p.get('PROLIFIC_PID') || null,
    study_id: p.get('STUDY_ID') || null,
    session_id: p.get('SESSION_ID') || null,
    all() {
      return { pid: this.pid, study_id: this.study_id, session_id: this.session_id };
    },
    isReal() { return !!this.pid; },
  };
})();
