(function () {
  const NAV_MSG = 'Ein Hashlauf läuft noch. Abbrechen und Seite verlassen?';
  const PROGRESS_POLL_INTERVAL = 600;

  const formatDuration = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return null;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    if (mins < 60) return `${mins}m ${secs.toString().padStart(2, '0')}s`;
    const hours = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hours}h ${remMins.toString().padStart(2, '0')}m`;
  };

  const createEtaTracker = (totalRounds) => {
    let lastRemaining = null;
    let lastUpdateTs = null;
    return (completedRounds, elapsedSeconds) => {
      if (!totalRounds || !completedRounds || completedRounds <= 0 || elapsedSeconds <= 0) {
        return null;
      }
      const currentHps = completedRounds / elapsedSeconds;
      if (!Number.isFinite(currentHps) || currentHps <= 0) {
        return null;
      }
      const remainingRounds = Math.max(0, totalRounds - completedRounds);
      let remainingSeconds = remainingRounds / currentHps;
      if (!Number.isFinite(remainingSeconds)) {
        return null;
      }
      const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
      if (lastRemaining == null || lastUpdateTs == null) {
        lastRemaining = remainingSeconds;
      } else {
        const deltaTime = Math.max(0, (now - lastUpdateTs) / 1000);
        const smoothingWindow = 2; // Sekunden
        const blend = Math.min(1, deltaTime / smoothingWindow);
        lastRemaining = lastRemaining + (remainingSeconds - lastRemaining) * blend;
      }
      lastUpdateTs = now;
      return {
        remaining: Math.max(0, lastRemaining),
        currentHps,
      };
    };
  };

  const monitorRunProgress = (token, onUpdate) => {
    if (!token || !onUpdate) return () => {};
    let active = true;
    let hasData = false;
    const poll = async () => {
      if (!active) return;
      try {
        const response = await fetch(`/api/run/progress/${encodeURIComponent(token)}`, { cache: 'no-store' });
        if (response.status === 404) {
          if (hasData) {
            active = false;
            return;
          }
          // Backend hat den Fortschritt noch nicht registriert – später erneut versuchen.
          setTimeout(poll, PROGRESS_POLL_INTERVAL);
          return;
        }
        if (response.ok) {
          const payload = await response.json();
          if (payload.success && payload.status) {
            hasData = true;
            onUpdate(payload.status);
            const runStatus = typeof payload.status.status === 'string' ? payload.status.status : null;
            if (runStatus && runStatus !== 'running') {
              active = false;
              return;
            }
          }
        }
      } catch (_) {
        /* Ignorieren und erneut versuchen */
      }
      if (active) {
        setTimeout(poll, PROGRESS_POLL_INTERVAL);
      }
    };
    poll();
    return () => {
      active = false;
    };
  };

  const requestRunToken = async () => {
    let response;
    try {
      response = await fetch('/api/run/token', { method: 'POST' });
    } catch (err) {
      throw new Error('Run-Token konnte nicht erzeugt werden.');
    }
    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = {};
    }
    if (!response.ok || !payload.success || !payload.token) {
      throw new Error(payload.error || 'Run-Token konnte nicht erzeugt werden.');
    }
    return payload.token;
  };

  const sendCancelRequest = (token) => {
    if (!token) return;
    const payload = JSON.stringify({ token });
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' });
      navigator.sendBeacon('/api/run/cancel', blob);
    } else {
      fetch('/api/run/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  };

  const extractMaxRounds = (raw) => {
    if (!raw) return 0;
    return raw
      .replace(/[,;\n]/g, ' ')
      .split(' ')
      .map((t) => t.replace(/_/g, ''))
      .filter(Boolean)
      .reduce((max, token) => {
        const value = parseInt(token, 10);
        return Number.isFinite(value) && value > max ? value : max;
      }, 0);
  };

  const setupNavigationGuard = (isActive, onAbort) => {
    const beforeUnload = (event) => {
      if (!isActive()) return;
      event.preventDefault();
      event.returnValue = '';
    };

    const pageHide = () => {
      if (!isActive()) return;
      onAbort(true);
    };

    const clickHandler = (event) => {
      if (!isActive()) return;
      const anchor = event.target.closest('a');
      if (!anchor || anchor.getAttribute('href')?.startsWith('#')) return;
      event.preventDefault();
      event.stopPropagation();
      if (confirm(NAV_MSG)) {
        onAbort(true);
        window.location.href = anchor.href;
      }
    };

    window.addEventListener('beforeunload', beforeUnload);
    window.addEventListener('pagehide', pageHide);
    document.addEventListener('click', clickHandler, true);

    return () => {
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('pagehide', pageHide);
      document.removeEventListener('click', clickHandler, true);
    };
  };

  function setupCreateForm() {
    const form = document.getElementById('create-form');
    if (!form) return;

    const ctx = {
      startTime: 0,
      activeController: null,
      runActive: false,
      runToken: null,
      stopProgressMonitor: null,
      totalRounds: 0,
      etaTracker: null,
      mode: 'time_based',
      timeConfig: null,
      elements: {
        errorBox: document.getElementById('create-error'),
      progressSection: document.getElementById('create-progress'),
      progressBar: document.getElementById('create-progress-bar'),
      progressInfo: document.getElementById('create-progress-info'),
      resultSection: document.getElementById('create-result'),
      secretField: document.getElementById('create-secret'),
        copyBtn: document.getElementById('create-secret-copy'),
        secretDownload: document.getElementById('create-secret-download'),
        passwordDownload: document.getElementById('create-password-download'),
        filesList: document.getElementById('create-files'),
      cancelBtn: document.getElementById('create-cancel'),
      secretNotice: document.getElementById('create-secret-notice'),
      secretContent: document.getElementById('create-secret-content'),
      showSecretBtn: document.getElementById('create-show-secret'),
      protectCheckbox: document.getElementById('create-protect-password'),
      passwordSection: document.getElementById('create-password-section'),
      passwordInput: form.querySelector('input[name="password"]'),
      passwordConfirmInput: form.querySelector('input[name="password_confirm"]'),
      passwordDownloadWrapper: document.getElementById('create-password-download-wrapper'),
      passwordHint: document.getElementById('create-password-hint'),
      roundsInput: form.querySelector('input[name="rounds"]'),
      modeTime: document.getElementById('create-mode-time'),
      modeRounds: document.getElementById('create-mode-rounds'),
      timeSection: document.getElementById('create-time-section'),
      roundSection: document.getElementById('create-round-section'),
      timeInput: document.getElementById('create-time-input'),
      timeCalibrateBtn: document.getElementById('create-time-calibrate'),
      timeSummary: document.getElementById('create-time-summary'),
      timeRequested: document.getElementById('create-time-requested'),
      timeHashrate: document.getElementById('create-time-hashrate'),
      timeRounds: document.getElementById('create-time-rounds'),
      timeRuntime: document.getElementById('create-time-runtime'),
      timeConfirm: document.getElementById('create-time-confirm'),
      timeTokenInput: document.getElementById('create-time-token'),
    },
  };

    const {
      errorBox,
      progressSection,
      progressBar,
      progressInfo,
      resultSection,
      secretField,
      copyBtn,
      secretDownload,
      passwordDownload,
      filesList,
      cancelBtn,
      secretNotice,
      secretContent,
      showSecretBtn,
      protectCheckbox,
      passwordSection,
      passwordInput,
      passwordConfirmInput,
      passwordDownloadWrapper,
      passwordHint,
      roundsInput,
      modeTime,
      modeRounds,
      timeSection,
      roundSection,
      timeInput,
      timeCalibrateBtn,
      timeSummary,
      timeRequested,
      timeHashrate,
      timeRounds,
      timeRuntime,
      timeConfirm,
      timeTokenInput,
    } = ctx.elements;

    const updatePasswordFields = () => {
      const enabled = protectCheckbox?.checked ?? true;
      if (passwordSection) passwordSection.hidden = !enabled;
      [passwordInput, passwordConfirmInput].forEach((input) => {
        if (!input) return;
        input.disabled = !enabled;
        input.required = !!enabled;
        if (!enabled) input.value = '';
      });
    };
    updatePasswordFields();
    protectCheckbox?.addEventListener('change', updatePasswordFields);

    const resetTimeConfig = () => {
      ctx.timeConfig = null;
      if (timeTokenInput) timeTokenInput.value = '';
      if (timeSummary) timeSummary.hidden = true;
      if (timeConfirm) {
        timeConfirm.checked = false;
        timeConfirm.disabled = true;
      }
      [timeRequested, timeHashrate, timeRounds, timeRuntime].forEach((el) => {
        if (el) el.textContent = '';
      });
    };

    const applyMode = (mode) => {
      ctx.mode = mode === 'round_based' ? 'round_based' : 'time_based';
      const isTimeMode = ctx.mode === 'time_based';
      if (timeSection) timeSection.hidden = !isTimeMode;
      if (roundSection) roundSection.hidden = isTimeMode;
      if (roundsInput) {
        roundsInput.required = !isTimeMode;
        if (isTimeMode) {
          roundsInput.value = '';
        }
      }
      if (!isTimeMode) {
        resetTimeConfig();
      } else if (timeConfirm) {
        timeConfirm.disabled = true;
      }
    };
    applyMode('time_based');
    modeTime?.addEventListener('change', () => applyMode('time_based'));
    modeRounds?.addEventListener('change', () => applyMode('round_based'));

    const revealSecret = () => {
      if (secretContent) secretContent.hidden = false;
      if (secretNotice) secretNotice.hidden = true;
    };
    showSecretBtn?.addEventListener('click', revealSecret);

    copyBtn?.addEventListener('click', async () => {
      if (!navigator.clipboard) {
        alert('Clipboard-API nicht verfügbar.');
        return;
      }
      await navigator.clipboard.writeText(secretField.value);
      copyBtn.textContent = 'Kopiert!';
      setTimeout(() => (copyBtn.textContent = 'In die Zwischenablage'), 1500);
    });

    const getDelayTokens = () => {
      const raw = timeInput?.value ?? '';
      const normalized = raw.replace(/[,;]/g, ' ').trim();
      if (!normalized) {
        return [];
      }
      return normalized.split(/\s+/).filter(Boolean);
    };

    const runTimeCalibration = async () => {
      if (ctx.mode !== 'time_based') {
        return;
      }
      const tokens = getDelayTokens();
      if (!tokens.length) {
        errorBox.textContent = 'Bitte mindestens eine Laufzeit eingeben (z. B. 10m oder 6h).';
        errorBox.hidden = false;
        return;
      }
      errorBox.hidden = true;
      if (timeCalibrateBtn) {
        if (!timeCalibrateBtn.dataset.originalText) {
          timeCalibrateBtn.dataset.originalText = timeCalibrateBtn.textContent || '';
        }
        timeCalibrateBtn.disabled = true;
        timeCalibrateBtn.textContent = 'Kalibriere …';
      }
      try {
        const formData = new FormData();
        formData.append('delay', tokens.join(' '));
        const response = await fetch('/api/create/time_calibrate', {
          method: 'POST',
          body: formData,
        });
        const payload = await response.json();
        if (!payload.success) {
          throw new Error(payload.error || 'Kalibrierung fehlgeschlagen.');
        }
        const requestedSecondsArray = Array.isArray(payload.requested_seconds)
          ? payload.requested_seconds.map((value) => Number(value) || 0)
          : [Number(payload.requested_seconds) || 0];
        const hashrate = Number(payload.hashrate) || 0;
        const roundsArray = Array.isArray(payload.rounds)
          ? payload.rounds.map((value) => Number(value) || 0)
          : [Number(payload.rounds) || 0];
        const estimatedRuntimeArray = Array.isArray(payload.estimated_runtime)
          ? payload.estimated_runtime.map((value) => Number(value) || 0)
          : [Number(payload.estimated_runtime) || 0];
        let inputTokens = Array.isArray(payload.inputs)
          ? payload.inputs.map((value) => String(value))
          : payload.input
            ? [String(payload.input)]
            : [];
        if (!inputTokens.length) {
          inputTokens = tokens;
        }
        const displayCount = roundsArray.length;
        if (inputTokens.length !== displayCount) {
          if (inputTokens.length === 1 && displayCount > 1) {
            inputTokens = Array(displayCount).fill(inputTokens[0]);
          } else {
            inputTokens = Array.from({ length: displayCount }, (_, idx) => inputTokens[idx] || tokens[idx] || '');
          }
        }
        if (requestedSecondsArray.length !== displayCount) {
          if (requestedSecondsArray.length === 1 && displayCount > 1) {
            requestedSecondsArray = Array(displayCount).fill(requestedSecondsArray[0]);
          } else {
            requestedSecondsArray = Array.from({ length: displayCount }, (_, idx) => requestedSecondsArray[idx] || 0);
          }
        }
        if (estimatedRuntimeArray.length !== displayCount) {
          if (estimatedRuntimeArray.length === 1 && displayCount > 1) {
            estimatedRuntimeArray = Array(displayCount).fill(estimatedRuntimeArray[0]);
          } else {
            estimatedRuntimeArray = Array.from({ length: displayCount }, (_, idx) => estimatedRuntimeArray[idx] || 0);
          }
        }
        ctx.timeConfig = {
          token: payload.token,
          requestedSeconds: requestedSecondsArray,
          hashrate,
          rounds: roundsArray,
          estimatedRuntime: estimatedRuntimeArray,
          inputs: inputTokens,
        };
        if (timeTokenInput) timeTokenInput.value = payload.token;
        if (timeRequested) {
          const requestedText = inputTokens
            .map((label, idx) => {
              const seconds = requestedSecondsArray[idx] || 0;
              const formatted = formatDuration(seconds);
              return `${label}${formatted ? ` (~${formatted})` : ''}`;
            })
            .join(', ');
          timeRequested.textContent = requestedText;
        }
        if (timeHashrate) {
          timeHashrate.textContent = `${Math.round(hashrate).toLocaleString()} Hashes/s`;
        }
        if (timeRounds) {
          timeRounds.textContent = roundsArray.map((value) => value.toLocaleString()).join(', ');
        }
        if (timeRuntime) {
          const runtimeText = estimatedRuntimeArray
            .map((value) => {
              const numeric = Number(value) || 0;
              const formatted = formatDuration(numeric);
              return formatted ? `~${formatted}` : `${numeric.toFixed(1)}s`;
            })
            .join(', ');
          timeRuntime.textContent = runtimeText;
        }
        if (timeSummary) timeSummary.hidden = false;
        if (timeConfirm) {
          timeConfirm.checked = false;
          timeConfirm.disabled = false;
        }
      } catch (err) {
        resetTimeConfig();
        errorBox.textContent = err.message || 'Kalibrierung fehlgeschlagen.';
        errorBox.hidden = false;
      } finally {
        if (timeCalibrateBtn) {
          timeCalibrateBtn.disabled = false;
          if (timeCalibrateBtn.dataset.originalText) {
            timeCalibrateBtn.textContent = timeCalibrateBtn.dataset.originalText;
          }
        }
      }
    };
    timeCalibrateBtn?.addEventListener('click', runTimeCalibration);

    const requestCancel = () => {
      if (ctx.runToken) {
        sendCancelRequest(ctx.runToken);
        ctx.runToken = null;
      }
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
    };

    const cancelRun = (skipConfirm = false) => {
      if (!ctx.runActive) return false;
      if (!skipConfirm && !confirm('Hashkettenlauf wirklich abbrechen?')) return false;
      requestCancel();
      ctx.activeController?.abort();
      return true;
    };

    cancelBtn?.addEventListener('click', () => cancelRun());

    setupNavigationGuard(
      () => ctx.runActive,
      () => cancelRun(true),
    );

    const disableForm = (disabled) => {
      Array.from(form.elements).forEach((el) => {
        if (['create-secret-copy', 'create-secret-download', 'create-password-download'].includes(el.id)) return;
        el.disabled = disabled;
      });
    };

    const startProgress = (totalRounds) => {
      ctx.startTime = Date.now();
      ctx.totalRounds = totalRounds || 0;
      ctx.etaTracker = createEtaTracker(ctx.totalRounds);
      progressSection.hidden = false;
      progressBar.value = 0;
      progressInfo.textContent = 'Hashlauf gestartet – warte auf Fortschritt …';
    };

    const updateProgress = (status) => {
      if (!status) return;
      const total = ctx.totalRounds || Number(status.total_rounds) || 0;
      const completed = Number(status.completed_rounds) || 0;
      const elapsed = Number(status.elapsed_time) || 0;
      if (total > 0) {
        progressBar.value = Math.max(0, Math.min(1, completed / total));
      }
      if (elapsed <= 0 || completed <= 0) {
        progressInfo.textContent = total
          ? `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA wird ermittelt …`
          : 'Fortschritt wird erfasst …';
        return;
      }
      const eta = ctx.etaTracker ? ctx.etaTracker(completed, elapsed) : null;
      if (eta && total) {
        const etaText = formatDuration(eta.remaining);
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA ~${etaText}`;
      } else if (total) {
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA wird ermittelt …`;
      } else {
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} Runden`;
      }
    };

    const stopProgress = (message) => {
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
      ctx.etaTracker = null;
      progressBar.value = 1;
      const elapsed = (Date.now() - ctx.startTime) / 1000;
      progressInfo.textContent = `${message} – Dauer: ${elapsed.toFixed(1)}s`;
      setTimeout(() => {
        progressSection.hidden = true;
      }, 1500);
    };

    const setRunActive = (active) => {
      ctx.runActive = active;
      if (cancelBtn) cancelBtn.hidden = !active;
    };

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (ctx.runActive) {
        errorBox.textContent = 'Ein Hashlauf läuft bereits. Bitte warte oder brich ihn ab.';
        errorBox.hidden = false;
        return;
      }

      errorBox.hidden = true;
      resultSection.hidden = true;
      if (secretNotice) secretNotice.hidden = true;
      if (secretContent) secretContent.hidden = true;
      if (secretNotice) secretNotice.hidden = true;
      if (secretContent) secretContent.hidden = true;
      const formData = new FormData(form);
      formData.set('protect_with_password', protectCheckbox?.checked ? 'true' : 'false');
      if (ctx.mode === 'time_based') {
        if (!ctx.timeConfig || !ctx.timeConfig.token) {
          errorBox.textContent = 'Bitte zunächst die Zeitkalibrierung durchführen.';
          errorBox.hidden = false;
          return;
        }
        if (!timeConfirm?.checked) {
          errorBox.textContent = 'Bitte die ermittelten Parameter bestätigen.';
          errorBox.hidden = false;
          return;
        }
        formData.set('rounds_mode', 'time_based');
        formData.set('time_config_token', ctx.timeConfig.token);
        const roundsStr = Array.isArray(ctx.timeConfig.rounds) ? ctx.timeConfig.rounds.join(' ') : String(ctx.timeConfig.rounds);
        formData.set('rounds', roundsStr);
        if (Array.isArray(ctx.timeConfig.rounds)) {
          const numericRounds = ctx.timeConfig.rounds.map((value) => Number(value) || 0);
          ctx.totalRounds = numericRounds.length ? Math.max(...numericRounds) : 0;
        } else {
          ctx.totalRounds = Number(ctx.timeConfig.rounds) || 0;
        }
      } else {
        formData.set('rounds_mode', 'round_based');
        formData.set('time_config_token', '');
        ctx.totalRounds = extractMaxRounds(formData.get('rounds'));
      }
      const totalRounds = ctx.totalRounds;
      disableForm(true);
      try {
        ctx.runToken = await requestRunToken();
      } catch (err) {
        disableForm(false);
        errorBox.textContent = err.message || 'Run-Token konnte nicht erzeugt werden.';
        errorBox.hidden = false;
        return;
      }
      startProgress(totalRounds);

      ctx.activeController = new AbortController();
      setRunActive(true);
      ctx.stopProgressMonitor = monitorRunProgress(ctx.runToken, updateProgress);

      try {
        const response = await fetch('/api/create', {
          method: 'POST',
          body: formData,
          headers: { 'X-Run-Token': ctx.runToken },
          signal: ctx.activeController.signal,
        });
        const payload = await response.json();
        if (!payload.success) throw new Error(payload.error || 'Unbekannter Fehler');

        secretField.value = payload.secret;
        if (secretDownload && payload.secret_file) {
          secretDownload.href = `/download/${payload.secret_file.token}`;
        }
        const passwordRequired = !!payload.password_required;
        const hasPasswordFile = passwordRequired && payload.password_file && payload.password_file.token;
        if (passwordDownloadWrapper) {
          passwordDownloadWrapper.hidden = !hasPasswordFile;
        }
        if (hasPasswordFile && passwordDownload) {
          passwordDownload.href = `/download/${payload.password_file.token}`;
        }
        if (passwordHint) {
          passwordHint.hidden = passwordRequired;
        }
        if (filesList) {
          filesList.innerHTML = payload.files
            .map((file) => `<li>${file.name} – <a href="/download/${file.token}" target="_blank" rel="noopener">Download</a></li>`)
            .join('');
        }

        resultSection.hidden = false;
        if (secretNotice) secretNotice.hidden = false;
        if (secretContent) secretContent.hidden = true;
        stopProgress('Fertig ✅');
        const selectedMode = ctx.mode;
        form.reset();
        if (modeTime) modeTime.checked = selectedMode === 'time_based';
        if (modeRounds) modeRounds.checked = selectedMode === 'round_based';
        applyMode(selectedMode);
        updatePasswordFields();
      } catch (err) {
        stopProgress('Abgebrochen');
        errorBox.textContent = err.name === 'AbortError' ? 'Hashkettenlauf abgebrochen.' : err.message;
        errorBox.hidden = false;
      } finally {
        disableForm(false);
        updatePasswordFields();
        setRunActive(false);
        ctx.activeController = null;
        ctx.runToken = null;
        if (ctx.mode === 'time_based') {
          resetTimeConfig();
        }
      }
    });
  }

  function setupCloneForm() {
    const form = document.getElementById('clone-form');
    if (!form) return;

    const ctx = {
      sourceRounds: null,
      targetRounds: 0,
      totalRounds: 0,
      runActive: false,
      activeController: null,
      runToken: null,
      stopProgressMonitor: null,
      etaTracker: null,
      mode: 'time_based',
      timeConfig: null,
      elements: {
        errorBox: document.getElementById('clone-error'),
        progressSection: document.getElementById('clone-progress'),
        progressInfoSource: document.getElementById('clone-progress-info-source'),
        progressBarSource: document.getElementById('clone-progress-bar-source'),
        progressInfoNew: document.getElementById('clone-progress-info-new'),
        progressBarNew: document.getElementById('clone-progress-bar-new'),
        resultSection: document.getElementById('clone-result'),
        secretField: document.getElementById('clone-secret'),
        copyBtn: document.getElementById('clone-secret-copy'),
        secretDownload: document.getElementById('clone-secret-download'),
        passwordDownload: document.getElementById('clone-password-download'),
        filesList: document.getElementById('clone-files'),
        warningBox: document.getElementById('clone-warning'),
        reuseCheckbox: document.getElementById('clone-reuse-password'),
        storePlainCheckbox: document.getElementById('clone-store-plain'),
        newPassword: form.querySelector('input[name="new_password"]'),
        newPasswordConfirm: form.querySelector('input[name="new_password_confirm"]'),
        fileInput: form.querySelector('input[name="puzzle_file"]'),
        cancelBtn: document.getElementById('clone-cancel'),
        secretNotice: document.getElementById('clone-secret-notice'),
        secretContent: document.getElementById('clone-secret-content'),
        showSecretBtn: document.getElementById('clone-show-secret'),
        passwordDownloadWrapper: document.getElementById('clone-password-download-wrapper'),
        passwordHint: document.getElementById('clone-password-hint'),
        roundsInput: form.querySelector('input[name="rounds"]'),
        modeTime: document.getElementById('clone-mode-time'),
        modeRounds: document.getElementById('clone-mode-rounds'),
        timeSection: document.getElementById('clone-time-section'),
        roundSection: document.getElementById('clone-round-section'),
        timeInput: document.getElementById('clone-time-input'),
        timeCalibrateBtn: document.getElementById('clone-time-calibrate'),
        timeSummary: document.getElementById('clone-time-summary'),
        timeRequested: document.getElementById('clone-time-requested'),
        timeHashrate: document.getElementById('clone-time-hashrate'),
        timeRounds: document.getElementById('clone-time-rounds'),
        timeRuntime: document.getElementById('clone-time-runtime'),
        timeConfirm: document.getElementById('clone-time-confirm'),
        timeTokenInput: document.getElementById('clone-time-token'),
      },
    };

    const {
      errorBox,
      progressSection,
      progressInfoSource,
      progressBarSource,
      progressInfoNew,
      progressBarNew,
      resultSection,
      secretField,
      copyBtn,
      secretDownload,
      passwordDownload,
      filesList,
      warningBox,
      reuseCheckbox,
      storePlainCheckbox,
      newPassword,
      newPasswordConfirm,
      fileInput,
      cancelBtn,
      secretNotice,
      secretContent,
      showSecretBtn,
      passwordDownloadWrapper,
      passwordHint,
      roundsInput,
      modeTime,
      modeRounds,
      timeSection,
      roundSection,
      timeInput,
      timeCalibrateBtn,
      timeSummary,
      timeRequested,
      timeHashrate,
      timeRounds,
      timeRuntime,
      timeConfirm,
      timeTokenInput,
    } = ctx.elements;

    const setRunActive = (active) => {
      ctx.runActive = active;
      if (cancelBtn) cancelBtn.hidden = !active;
    };

    copyBtn?.addEventListener('click', async () => {
      if (!navigator.clipboard) {
        alert('Clipboard-API nicht verfügbar.');
        return;
      }
      await navigator.clipboard.writeText(secretField.value);
      copyBtn.textContent = 'Kopiert!';
      setTimeout(() => (copyBtn.textContent = 'In die Zwischenablage'), 1500);
    });

    const updateClonePasswordFields = () => {
      const reuse = reuseCheckbox?.checked ?? true;
      const storePlain = storePlainCheckbox?.checked ?? false;
      if (storePlainCheckbox) {
        if (reuse) {
          storePlainCheckbox.checked = false;
        }
        storePlainCheckbox.disabled = reuse;
      }
      const disableNewPassword = reuse || (storePlainCheckbox?.checked ?? false);
      [newPassword, newPasswordConfirm].forEach((input) => {
        if (!input) return;
        input.disabled = disableNewPassword;
        input.required = !disableNewPassword;
        if (disableNewPassword) input.value = '';
      });
    };
    updateClonePasswordFields();
    reuseCheckbox?.addEventListener('change', updateClonePasswordFields);
    storePlainCheckbox?.addEventListener('change', updateClonePasswordFields);

    const getCloneDelayTokens = () => {
      const raw = timeInput?.value ?? '';
      const normalized = raw.replace(/[,;]/g, ' ').trim();
      if (!normalized) {
        return [];
      }
      return normalized.split(/\s+/).filter(Boolean);
    };

    const runCloneTimeCalibration = async () => {
      if (ctx.mode !== 'time_based') {
        return;
      }
      const tokens = getCloneDelayTokens();
      if (!tokens.length) {
        errorBox.textContent = 'Bitte mindestens eine Laufzeit eingeben (z. B. 6h).';
        errorBox.hidden = false;
        return;
      }
      errorBox.hidden = true;
      if (timeCalibrateBtn) {
        if (!timeCalibrateBtn.dataset.originalText) {
          timeCalibrateBtn.dataset.originalText = timeCalibrateBtn.textContent || '';
        }
        timeCalibrateBtn.disabled = true;
        timeCalibrateBtn.textContent = 'Kalibriere …';
      }
      try {
        const formData = new FormData();
        formData.append('delay', tokens.join(' '));
        const response = await fetch('/api/create/time_calibrate', {
          method: 'POST',
          body: formData,
        });
        const payload = await response.json();
        if (!payload.success) {
          throw new Error(payload.error || 'Kalibrierung fehlgeschlagen.');
        }
        const requestedSecondsArray = Array.isArray(payload.requested_seconds)
          ? payload.requested_seconds.map((value) => Number(value) || 0)
          : [Number(payload.requested_seconds) || 0];
        const hashrate = Number(payload.hashrate) || 0;
        const roundsArray = Array.isArray(payload.rounds)
          ? payload.rounds.map((value) => Number(value) || 0)
          : [Number(payload.rounds) || 0];
        const estimatedRuntimeArray = Array.isArray(payload.estimated_runtime)
          ? payload.estimated_runtime.map((value) => Number(value) || 0)
          : [Number(payload.estimated_runtime) || 0];
        let inputTokens = Array.isArray(payload.inputs)
          ? payload.inputs.map((value) => String(value))
          : payload.input
            ? [String(payload.input)]
            : [];
        if (!inputTokens.length) {
          inputTokens = tokens;
        }
        const displayCount = roundsArray.length;
        if (inputTokens.length !== displayCount) {
          if (inputTokens.length === 1 && displayCount > 1) {
            inputTokens = Array(displayCount).fill(inputTokens[0]);
          } else {
            inputTokens = Array.from({ length: displayCount }, (_, idx) => inputTokens[idx] || tokens[idx] || '');
          }
        }
        if (requestedSecondsArray.length !== displayCount) {
          if (requestedSecondsArray.length === 1 && displayCount > 1) {
            requestedSecondsArray = Array(displayCount).fill(requestedSecondsArray[0]);
          } else {
            requestedSecondsArray = Array.from({ length: displayCount }, (_, idx) => requestedSecondsArray[idx] || 0);
          }
        }
        if (estimatedRuntimeArray.length !== displayCount) {
          if (estimatedRuntimeArray.length === 1 && displayCount > 1) {
            estimatedRuntimeArray = Array(displayCount).fill(estimatedRuntimeArray[0]);
          } else {
            estimatedRuntimeArray = Array.from({ length: displayCount }, (_, idx) => estimatedRuntimeArray[idx] || 0);
          }
        }
        ctx.timeConfig = {
          token: payload.token,
          requestedSeconds: requestedSecondsArray,
          hashrate,
          rounds: roundsArray,
          estimatedRuntime: estimatedRuntimeArray,
          inputs: inputTokens,
        };
        if (timeTokenInput) timeTokenInput.value = payload.token;
        if (timeRequested) {
          const requestedText = inputTokens
            .map((label, idx) => {
              const seconds = requestedSecondsArray[idx] || 0;
              const formatted = formatDuration(seconds);
              return `${label}${formatted ? ` (~${formatted})` : ''}`;
            })
            .join(', ');
          timeRequested.textContent = requestedText;
        }
        if (timeHashrate) {
          timeHashrate.textContent = `${Math.round(hashrate).toLocaleString()} Hashes/s`;
        }
        if (timeRounds) {
          timeRounds.textContent = roundsArray.map((value) => value.toLocaleString()).join(', ');
        }
        if (timeRuntime) {
          const runtimeText = estimatedRuntimeArray
            .map((value) => {
              const numeric = Number(value) || 0;
              const formatted = formatDuration(numeric);
              return formatted ? `~${formatted}` : `${numeric.toFixed(1)}s`;
            })
            .join(', ');
          timeRuntime.textContent = runtimeText;
        }
        if (timeSummary) timeSummary.hidden = false;
        if (timeConfirm) {
          timeConfirm.checked = false;
          timeConfirm.disabled = false;
        }
      } catch (err) {
        resetTimeConfig();
        errorBox.textContent = err.message || 'Kalibrierung fehlgeschlagen.';
        errorBox.hidden = false;
      } finally {
        if (timeCalibrateBtn) {
          timeCalibrateBtn.disabled = false;
          if (timeCalibrateBtn.dataset.originalText) {
            timeCalibrateBtn.textContent = timeCalibrateBtn.dataset.originalText;
          }
        }
      }
    };
    timeCalibrateBtn?.addEventListener('click', runCloneTimeCalibration);

    const resetTimeConfig = () => {
      ctx.timeConfig = null;
      if (timeTokenInput) timeTokenInput.value = '';
      if (timeSummary) timeSummary.hidden = true;
      if (timeConfirm) {
        timeConfirm.checked = false;
        timeConfirm.disabled = true;
      }
      [timeRequested, timeHashrate, timeRounds, timeRuntime].forEach((el) => {
        if (el) el.textContent = '';
      });
    };

    const applyMode = (mode) => {
      ctx.mode = mode === 'round_based' ? 'round_based' : 'time_based';
      const isTime = ctx.mode === 'time_based';
      if (timeSection) timeSection.hidden = !isTime;
      if (roundSection) roundSection.hidden = isTime;
      if (roundsInput) {
        roundsInput.required = !isTime;
        if (isTime) {
          roundsInput.value = '';
        }
      }
      if (isTime) {
        if (timeConfirm) timeConfirm.disabled = true;
      } else {
        resetTimeConfig();
      }
    };
    applyMode('time_based');
    modeTime?.addEventListener('change', () => applyMode('time_based'));
    modeRounds?.addEventListener('change', () => applyMode('round_based'));

    const revealSecret = () => {
      if (secretContent) secretContent.hidden = false;
      if (secretNotice) secretNotice.hidden = true;
    };
    showSecretBtn?.addEventListener('click', revealSecret);

    const disableForm = (disabled) => {
      Array.from(form.elements).forEach((el) => (el.disabled = disabled));
    };

    const startProgress = (totalRounds) => {
      ctx.totalRounds = totalRounds || 0;
      ctx.etaTracker = createEtaTracker(ctx.totalRounds);
      progressSection.hidden = false;
      progressBarSource.value = ctx.sourceRounds ? 0 : 1;
      progressBarNew.value = ctx.targetRounds ? 0 : 1;
      progressInfoSource.textContent = ctx.sourceRounds ? 'Originaldatei: Hashlauf startet …' : 'Originaldatei: keine Fortschrittsdaten';
      progressInfoNew.textContent = ctx.targetRounds
        ? 'Neue Zeitkapseln warten auf Hashlauf …'
        : 'Neue Zeitkapseln: keine Fortschrittsdaten';
    };

    const updateProgress = (status) => {
      if (!status) return;
      const completed = Number(status.completed_rounds) || 0;
      const elapsed = Number(status.elapsed_time) || 0;
      const sourceTotal = ctx.sourceRounds || 0;
      const newTotal = ctx.targetRounds || 0;
      const sourceCompleted = Math.min(completed, sourceTotal);
      const newCompleted = Math.max(0, completed - sourceTotal);
      if (sourceTotal > 0) {
        progressBarSource.value = Math.max(0, Math.min(1, sourceCompleted / sourceTotal));
      } else {
        progressBarSource.value = 1;
      }
      if (newTotal > 0) {
        progressBarNew.value = Math.max(0, Math.min(1, newCompleted / newTotal));
      }
      progressInfoSource.textContent = sourceTotal
        ? `Originaldatei: ${sourceCompleted.toLocaleString()} / ${sourceTotal.toLocaleString()} Runden`
        : 'Originaldatei: keine Fortschrittsdaten';
      let newText = newTotal
        ? `Neue Zeitkapseln: ${newCompleted.toLocaleString()} / ${newTotal.toLocaleString()} Runden`
        : 'Neue Zeitkapseln: keine Fortschrittsdaten';
      if (elapsed > 0 && completed > 0 && ctx.etaTracker && ctx.totalRounds) {
        const eta = ctx.etaTracker(completed, elapsed);
        if (eta) {
          newText = `${newText} – ETA ~${formatDuration(eta.remaining)}`;
        } else {
          newText = `${newText} – ETA wird ermittelt …`;
        }
      }
      progressInfoNew.textContent = newText;
    };

    const stopProgress = (message) => {
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
      ctx.etaTracker = null;
      [progressBarSource, progressBarNew].forEach((bar) => (bar.value = 1));
      [progressInfoSource, progressInfoNew].forEach((info) => (info.textContent = message));
      setTimeout(() => {
        progressSection.hidden = true;
      }, 1500);
    };

    const requestCancel = () => {
      if (ctx.runToken) {
        sendCancelRequest(ctx.runToken);
        ctx.runToken = null;
      }
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
    };

    const cancelRun = (skipConfirm = false) => {
      if (!ctx.runActive) return false;
      if (!skipConfirm && !confirm('Hashkettenlauf wirklich abbrechen?')) return false;
      requestCancel();
      ctx.activeController?.abort();
      return true;
    };

    fileInput?.addEventListener('change', () => {
      requestCancel();
      errorBox.hidden = true;
      disableForm(false);
      updateClonePasswordFields();
      ctx.sourceRounds = null;
      const file = fileInput.files[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = () => {
          try {
            const data = JSON.parse(reader.result);
            ctx.sourceRounds = Number(data.rounds) || null;
          } catch (_) {
            ctx.sourceRounds = null;
          }
        };
        reader.readAsText(file);
      }
    });

    cancelBtn?.addEventListener('click', () => cancelRun());

    setupNavigationGuard(
      () => ctx.runActive,
      () => cancelRun(true),
    );

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (ctx.runActive) {
        errorBox.textContent = 'Ein Hashlauf läuft bereits. Bitte warte oder brich ihn ab.';
        errorBox.hidden = false;
        return;
      }

      errorBox.hidden = true;
      resultSection.hidden = true;
      if (warningBox) warningBox.hidden = true;
      const formData = new FormData(form);
      formData.set('reuse_password', reuseCheckbox?.checked ? 'true' : 'false');
      formData.set('store_plain', storePlainCheckbox?.checked ? 'true' : 'false');
      if (ctx.mode === 'time_based') {
        if (!ctx.timeConfig || !ctx.timeConfig.token) {
          errorBox.textContent = 'Bitte zuerst die Zeitkalibrierung durchführen.';
          errorBox.hidden = false;
          return;
        }
        if (!timeConfirm?.checked) {
          errorBox.textContent = 'Bitte die kalibrierten Parameter bestätigen.';
          errorBox.hidden = false;
          return;
        }
        formData.set('rounds_mode', 'time_based');
        formData.set('time_config_token', ctx.timeConfig.token);
        const roundsStr = Array.isArray(ctx.timeConfig.rounds) ? ctx.timeConfig.rounds.join(' ') : String(ctx.timeConfig.rounds);
        formData.set('rounds', roundsStr);
        if (Array.isArray(ctx.timeConfig.rounds)) {
          const numericRounds = ctx.timeConfig.rounds.map((value) => Number(value) || 0);
          ctx.targetRounds = numericRounds.length ? Math.max(...numericRounds) : 0;
        } else {
          ctx.targetRounds = Number(ctx.timeConfig.rounds) || 0;
        }
      } else {
        formData.set('rounds_mode', 'round_based');
        formData.set('time_config_token', '');
        ctx.targetRounds = extractMaxRounds(formData.get('rounds'));
      }
      const totalRounds = (ctx.sourceRounds || 0) + (ctx.targetRounds || 0);

      disableForm(true);
      try {
        ctx.runToken = await requestRunToken();
      } catch (err) {
        disableForm(false);
        errorBox.textContent = err.message || 'Run-Token konnte nicht erzeugt werden.';
        errorBox.hidden = false;
        return;
      }
      startProgress(totalRounds);

      ctx.activeController = new AbortController();
      setRunActive(true);
      ctx.stopProgressMonitor = monitorRunProgress(ctx.runToken, updateProgress);

      try {
        const response = await fetch('/api/clone', {
          method: 'POST',
          body: formData,
          headers: { 'X-Run-Token': ctx.runToken },
          signal: ctx.activeController.signal,
        });
        const payload = await response.json();
        if (!payload.success) throw new Error(payload.error || 'Klonen fehlgeschlagen.');

        secretField.value = payload.secret;
        if (secretDownload && payload.secret_file) {
          secretDownload.href = `/download/${payload.secret_file.token}`;
        }
        const passwordRequired = !!payload.password_required;
        const hasPasswordFile = passwordRequired && payload.password_file && payload.password_file.token;
        if (passwordDownloadWrapper) {
          passwordDownloadWrapper.hidden = !hasPasswordFile;
        }
        if (hasPasswordFile && passwordDownload) {
          passwordDownload.href = `/download/${payload.password_file.token}`;
        }
        if (passwordHint) {
          passwordHint.hidden = passwordRequired;
        }
        if (filesList) {
          filesList.innerHTML = payload.files
            .map((file) => `<li>${file.name} – <a href="/download/${file.token}" target="_blank" rel="noopener">Download</a></li>`)
            .join('');
        }
        if (warningBox) {
          const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(Boolean) : [];
          if (warnings.length) {
            warningBox.textContent = warnings.join('\n');
            warningBox.hidden = false;
          } else {
            warningBox.hidden = true;
          }
        }

        resultSection.hidden = false;
        if (secretNotice) secretNotice.hidden = false;
        if (secretContent) secretContent.hidden = true;
        stopProgress('Fertig ✅');
        const selectedMode = ctx.mode;
        form.reset();
        if (modeTime) modeTime.checked = selectedMode === 'time_based';
        if (modeRounds) modeRounds.checked = selectedMode === 'round_based';
        applyMode(selectedMode);
        updateClonePasswordFields();
        ctx.targetRounds = 0;
      } catch (err) {
        stopProgress('Abgebrochen');
        if (err.name === 'AbortError') {
          errorBox.textContent = 'Hashkettenlauf abgebrochen.';
          errorBox.hidden = false;
        } else {
          errorBox.textContent = err.message;
          errorBox.hidden = false;
        }
      } finally {
        disableForm(false);
        updateClonePasswordFields();
        ctx.targetRounds = 0;
        setRunActive(false);
        ctx.activeController = null;
        ctx.runToken = null;
        if (ctx.mode === 'time_based') {
          resetTimeConfig();
        }
      }
    });
  }

  
  function setupUnlockForm() {
    const form = document.getElementById('unlock-form');
    if (!form) return;

    const ctx = {
      totalRounds: 0,
      runActive: false,
      activeController: null,
      startTime: 0,
      runToken: null,
      stopProgressMonitor: null,
      etaTracker: null,
      elements: {
        errorBox: document.getElementById('unlock-error'),
        progressSection: document.getElementById('unlock-progress'),
        progressBar: document.getElementById('unlock-progress-bar'),
        progressInfo: document.getElementById('unlock-progress-info'),
        resultSection: document.getElementById('unlock-result'),
        secretField: document.getElementById('unlock-secret'),
        copyBtn: document.getElementById('unlock-secret-copy'),
        downloadLink: document.getElementById('unlock-secret-download'),
        warningBox: document.getElementById('unlock-warning'),
        fileInput: form.querySelector('input[name="puzzle_file"]'),
        cancelBtn: document.getElementById('unlock-cancel'),
        secretNotice: document.getElementById('unlock-secret-notice'),
        secretContent: document.getElementById('unlock-secret-content'),
        showSecretBtn: document.getElementById('unlock-show-secret'),
      },
    };

    const {
      errorBox,
      progressSection,
      progressBar,
      progressInfo,
      resultSection,
      secretField,
      copyBtn,
      downloadLink,
      warningBox,
      fileInput,
      cancelBtn,
      secretNotice,
      secretContent,
      showSecretBtn,
    } = ctx.elements;

    const setRunActive = (active) => {
      ctx.runActive = active;
      if (cancelBtn) cancelBtn.hidden = !active;
    };

    const revealSecret = () => {
      if (secretContent) secretContent.hidden = false;
      if (secretNotice) secretNotice.hidden = true;
    };
    showSecretBtn?.addEventListener('click', revealSecret);

    copyBtn?.addEventListener('click', async () => {
      if (!navigator.clipboard) {
        alert('Clipboard-API nicht verfügbar.');
        return;
      }
      await navigator.clipboard.writeText(secretField.value);
      copyBtn.textContent = 'Kopiert!';
      setTimeout(() => (copyBtn.textContent = 'In die Zwischenablage'), 1500);
    });

    const requestCancel = () => {
      if (ctx.runToken) {
        sendCancelRequest(ctx.runToken);
        ctx.runToken = null;
      }
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
    };

    const cancelRun = (skipConfirm = false) => {
      if (!ctx.runActive) return false;
      if (!skipConfirm && !confirm('Hashkettenlauf wirklich abbrechen?')) return false;
      requestCancel();
      ctx.activeController?.abort();
      return true;
    };

    cancelBtn?.addEventListener('click', () => cancelRun());

    setupNavigationGuard(
      () => ctx.runActive,
      () => cancelRun(true),
    );

    const readRoundsFromFile = (file) =>
      new Promise((resolve) => {
        if (!file) {
          resolve(null);
          return;
        }
        const reader = new FileReader();
        reader.onload = () => {
          try {
            const data = JSON.parse(reader.result);
            resolve(Number(data.rounds) || null);
          } catch (_) {
            resolve(null);
          }
        };
        reader.onerror = () => resolve(null);
        reader.readAsText(file);
      });

    fileInput?.addEventListener('change', async () => {
      const file = fileInput.files[0];
      const rounds = await readRoundsFromFile(file);
      ctx.totalRounds = rounds && rounds > 0 ? rounds : 0;
      Array.from(form.elements).forEach((el) => (el.disabled = false));
      errorBox.hidden = true;
    });

    const disableForm = (disabled) => {
      Array.from(form.elements).forEach((el) => (el.disabled = disabled));
    };

    const startProgress = () => {
      ctx.startTime = Date.now();
      ctx.etaTracker = createEtaTracker(ctx.totalRounds || 0);
      progressSection.hidden = false;
      progressBar.value = 0;
      progressInfo.textContent = 'Hashlauf gestartet – warte auf Fortschritt …';
    };

    const updateProgress = (status) => {
      if (!status) return;
      const completed = Number(status.completed_rounds) || 0;
      const elapsed = Number(status.elapsed_time) || 0;
      const total = ctx.totalRounds || Number(status.total_rounds) || 0;
      if (total > 0) {
        progressBar.value = Math.max(0, Math.min(1, completed / total));
      }
      if (elapsed <= 0 || completed <= 0) {
        progressInfo.textContent = total
          ? `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA wird ermittelt …`
          : 'Fortschritt wird erfasst …';
        return;
      }
      const eta = ctx.etaTracker ? ctx.etaTracker(completed, elapsed) : null;
      if (eta && total > 0) {
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA ~${formatDuration(
          eta.remaining,
        )}`;
      } else if (total > 0) {
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} / ${total.toLocaleString()} Runden – ETA wird ermittelt …`;
      } else {
        progressInfo.textContent = `Fortschritt: ${completed.toLocaleString()} Runden`;
      }
    };

    const stopProgress = (message) => {
      if (ctx.stopProgressMonitor) {
        ctx.stopProgressMonitor();
        ctx.stopProgressMonitor = null;
      }
      ctx.etaTracker = null;
      const elapsed = (Date.now() - ctx.startTime) / 1000;
      progressBar.value = 1;
      progressInfo.textContent = `${message} – Dauer: ${elapsed.toFixed(1)}s`;
      setTimeout(() => {
        progressSection.hidden = true;
      }, 1500);
    };

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (ctx.runActive) {
        errorBox.textContent = 'Ein Hashlauf läuft bereits. Bitte warte oder brich ihn ab.';
        errorBox.hidden = false;
        return;
      }

      errorBox.hidden = true;
      resultSection.hidden = true;
      if (secretNotice) secretNotice.hidden = true;
      if (secretContent) secretContent.hidden = true;
      if (warningBox) warningBox.hidden = true;
      const formData = new FormData(form);
      disableForm(true);
      try {
        ctx.runToken = await requestRunToken();
      } catch (err) {
        disableForm(false);
        errorBox.textContent = err.message || 'Run-Token konnte nicht erzeugt werden.';
        errorBox.hidden = false;
        return;
      }
      startProgress();

      ctx.activeController = new AbortController();
      setRunActive(true);
      ctx.stopProgressMonitor = monitorRunProgress(ctx.runToken, updateProgress);

      try {
        const response = await fetch('/api/unlock', {
          method: 'POST',
          body: formData,
          headers: { 'X-Run-Token': ctx.runToken },
          signal: ctx.activeController.signal,
        });
        const payload = await response.json();
        if (!payload.success) throw new Error(payload.error || 'Entschlüsselung fehlgeschlagen.');

        secretField.value = payload.secret;
        downloadLink.href = `/download/${payload.secret_file.token}`;
        if (warningBox) {
          const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(Boolean) : [];
          if (warnings.length) {
            warningBox.textContent = warnings.join('\n');
            warningBox.hidden = false;
          } else {
            warningBox.hidden = true;
          }
        }
        resultSection.hidden = false;
        if (secretNotice) secretNotice.hidden = false;
        if (secretContent) secretContent.hidden = true;
        stopProgress('Fertig ✅');
        form.reset();
      } catch (err) {
        stopProgress('Abgebrochen');
        if (err.name === 'AbortError') {
          errorBox.textContent = 'Hashkettenlauf abgebrochen.';
          errorBox.hidden = false;
        } else {
          errorBox.textContent = err.message;
          errorBox.hidden = false;
        }
      } finally {
        disableForm(false);
        ctx.totalRounds = 0;
        setRunActive(false);
        ctx.activeController = null;
        ctx.runToken = null;
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupCreateForm();
    setupCloneForm();
    setupUnlockForm();
  });
})();
