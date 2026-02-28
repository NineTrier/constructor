/* JavaScript for the documents tools panel.

This script handles validation of placeholders and asynchronous document
generation with progress updates.  It expects global variables
``DOCUMENT_PATTERN_ID`` and (optionally) ``DOCUMENT_SELECTED_IDS`` to
be defined on the page.
*/

(() => {
  function getPatternId() {
    return window.DOCUMENT_PATTERN_ID;
  }
  function getSelectedIds() {
    return window.DOCUMENT_SELECTED_IDS || {};
  }
  async function validateTemplate() {
    const patternId = getPatternId();
    const resultEl = document.getElementById('documents-validation-result');
    if (!patternId) {
      resultEl.textContent = 'Pattern ID is not defined.';
      return;
    }
    resultEl.textContent = 'Validating...';
    try {
      const resp = await fetch(`/documents/${patternId}/validate/`);
      if (!resp.ok) {
        resultEl.textContent = 'Error validating template';
        return;
      }
      const data = await resp.json();
      if (data.issues && data.issues.length) {
        resultEl.innerHTML = data.issues.map((iss) => `<div>${iss}</div>`).join('');
      } else {
        resultEl.textContent = 'No issues found';
      }
    } catch (e) {
      resultEl.textContent = 'Validation request failed';
    }
  }

  function getCSRFCookie(name='csrftoken'){
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  }
  async function generateAsync() {
    const patternId = getPatternId();
    const progressEl = document.getElementById('documents-render-progress');
    progressEl.textContent = 'Starting render...';
    try {
      const resp = await fetch(`/documents/${patternId}/generate/async/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFCookie(),
        },
        body: JSON.stringify({ selected_ids: getSelectedIds() })
      });
      if (!resp.ok) {
        progressEl.textContent = 'Error starting render';
        return;
      }
      const data = await resp.json();
      const jobId = data.job_id;
      progressEl.textContent = `Render started (job ${jobId}).`;
      pollJob(jobId);
    } catch (e) {
      progressEl.textContent = 'Render request failed';
    }
  }
  async function pollJob(jobId) {
    const progressEl = document.getElementById('documents-render-progress');
    try {
      const resp = await fetch(`/documents/jobs/${jobId}/poll/`);
      if (!resp.ok) {
        progressEl.textContent = 'Error polling job';
        return;
      }
      const data = await resp.json();
      let html = '';
      html += `<div>Status: ${data.status}</div>`;
      html += `<div>${data.placeholders_replaced}/${data.placeholders_total} placeholders replaced</div>`;
      if (data.events && data.events.length) {
        html += '<ul>' + data.events.map((ev) => `<li>[${ev.timestamp.substring(11,19)}] ${ev.message}</li>`).join('') + '</ul>';
      }
      progressEl.innerHTML = html;
      if (data.status === 'completed' || data.status === 'failed') {
        if (data.download_url) {
          // Provide a link to download output if available
          progressEl.innerHTML += `<div><a href="${data.download_url}" download>Download result</a></div>`;
        }
        return;
      }
      setTimeout(() => pollJob(jobId), 1000);
    } catch (e) {
      progressEl.textContent = 'Polling failed';
    }
  }
  // Attach handlers on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', () => {
    const validateBtn = document.getElementById('documents-validate-btn');
    const generateBtn = document.getElementById('documents-generate-async-btn');
    if (validateBtn) {
      validateBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        validateTemplate();
      });
    }
    if (generateBtn) {
      generateBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        generateAsync();
      });
    }
  });
})();