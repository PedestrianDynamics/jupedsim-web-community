(() => {
  const STORAGE_KEY = "jupedsim.bridge.port";
  const DEFAULT_PORT = "8090";
  const POLL_INTERVAL_MS = 1000;
  const SIMULATION_TIMEOUT_MS = 10 * 60 * 1000;
  let activePort = null;
  let importInProgress = false;
  let lastScenarioId = null;
  let lastClearSceneCommandId = null;
  let lastSimulationCommandId = null;
  let lastViewResultsCommandId = null;
  let lastPublishedResultDigest = null;
  let lastRequestedResultArchiveDigest = null;
  let pollTimer = null;
  let clearSceneCommandPollInProgress = false;
  let simulationCommandPollInProgress = false;
  let viewResultsCommandPollInProgress = false;
  let simulationMonitorTimer = null;
  let snapshotRequestPending = false;
  let snapshotPublishInProgress = false;
  let resultArchiveRequestPending = false;
  let resultArchivePublishInProgress = false;
  let suppressNextProjectDownload = false;
  let suppressNextResultsDownload = false;
  let snapshotFallbackTimer = null;
  let resultArchiveFallbackTimer = null;
  let activeResultArchiveSimulationId = null;
  const objectUrlBlobs = new Map();

  function getStoredPort() {
    return window.localStorage.getItem(STORAGE_KEY) || DEFAULT_PORT;
  }

  function isValidPort(value) {
    return /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 65535;
  }

  function getBaseUrl(port) {
    return `http://127.0.0.1:${port}`;
  }

  function updateButton(button, port, state = "connecting") {
    button.dataset.bridgePort = port;
    button.dataset.bridgeState = state;
    button.title = `HTTP bridge port: ${port} (${state})`;
    button.setAttribute("aria-label", `Bridge HTTP port ${port}`);
  }

  async function publishUiState(bundle) {
    if (snapshotPublishInProgress || !activePort) {
      return;
    }

    snapshotPublishInProgress = true;
    try {
      const response = await fetch(`${getBaseUrl(activePort)}/api/ui-state`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: bundle,
      });
      if (!response.ok) {
        throw new Error(`UI state publish failed: ${response.status}`);
      }
      document.documentElement.dataset.jupedsimBridgeSnapshot = "synced";
    } catch (error) {
      document.documentElement.dataset.jupedsimBridgeSnapshot = "sync error";
      console.debug("JuPedSim UI state could not be published.", error);
    } finally {
      snapshotPublishInProgress = false;
    }
  }

  async function publishResultArchive(bundle, simulationId) {
    if (resultArchivePublishInProgress || !activePort) {
      return;
    }

    resultArchivePublishInProgress = true;
    try {
      const suffix = simulationId
        ? `?simulation_id=${encodeURIComponent(simulationId)}`
        : "";
      const response = await fetch(`${getBaseUrl(activePort)}/api/results/archive${suffix}`, {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: bundle,
      });
      if (!response.ok) {
        throw new Error(`Result archive publish failed: ${response.status}`);
      }
      document.documentElement.dataset.jupedsimBridgeResultsArchive = "synced";
    } catch (error) {
      document.documentElement.dataset.jupedsimBridgeResultsArchive = "sync error";
      console.debug("JuPedSim result archive could not be published.", error);
    } finally {
      resultArchivePublishInProgress = false;
    }
  }

  function clearPendingSnapshot() {
    snapshotRequestPending = false;
    suppressNextProjectDownload = false;
    if (snapshotFallbackTimer) {
      window.clearTimeout(snapshotFallbackTimer);
      snapshotFallbackTimer = null;
    }
  }

  function clearPendingResultArchive() {
    resultArchiveRequestPending = false;
    suppressNextResultsDownload = false;
    activeResultArchiveSimulationId = null;
    if (resultArchiveFallbackTimer) {
      window.clearTimeout(resultArchiveFallbackTimer);
      resultArchiveFallbackTimer = null;
    }
  }

  function installDownloadInterceptor() {
    if (window.__jupedsimBridgeDownloadInterceptorInstalled) {
      return;
    }
    window.__jupedsimBridgeDownloadInterceptorInstalled = true;

    const createObjectURL = URL.createObjectURL.bind(URL);
    const revokeObjectURL = URL.revokeObjectURL.bind(URL);
    const appendChild = document.body.appendChild.bind(document.body);

    URL.createObjectURL = (object) => {
      const url = createObjectURL(object);
      if (object instanceof Blob) {
        objectUrlBlobs.set(url, object);
      }
      return url;
    };

    URL.revokeObjectURL = (url) => {
      window.setTimeout(() => objectUrlBlobs.delete(url), 1000);
      revokeObjectURL(url);
    };

    document.body.appendChild = (element) => {
      const isProjectExport =
        element?.tagName === "A" &&
        /^jps_\d{4}(?:_\d{2}){5}\.zip$/.test(element.download);
      const isResultsExport =
        element?.tagName === "A" &&
        /^jps_results_\d{4}(?:_\d{2}){5}\.zip$/.test(element.download);
      const bundle = isProjectExport ? objectUrlBlobs.get(element.href) : null;
      if (bundle) {
        const suppressDownload = suppressNextProjectDownload;
        clearPendingSnapshot();
        publishUiState(bundle);
        if (suppressDownload) {
          element.click = () => {};
        }
      }
      const resultBundle = isResultsExport ? objectUrlBlobs.get(element.href) : null;
      if (resultBundle) {
        const suppressDownload = suppressNextResultsDownload;
        const simulationId = activeResultArchiveSimulationId;
        clearPendingResultArchive();
        publishResultArchive(resultBundle, simulationId);
        if (suppressDownload) {
          element.click = () => {};
        }
      }
      return appendChild(element);
    };
    document.documentElement.dataset.jupedsimBridgeInterceptor = "installed";
  }

  function requestUiSnapshot() {
    if (
      importInProgress ||
      snapshotRequestPending ||
      snapshotPublishInProgress
    ) {
      return;
    }

    const downloadButton = document.querySelector(
      'button[title="Download project files"]',
    );
    if (!downloadButton || downloadButton.disabled) {
      return;
    }

    snapshotRequestPending = true;
    suppressNextProjectDownload = true;
    document.documentElement.dataset.jupedsimBridgeSnapshot = "capturing";
    snapshotFallbackTimer = window.setTimeout(clearPendingSnapshot, 5000);
    downloadButton.click();
  }

  function findButton(label) {
    return Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent.trim() === label,
    );
  }

  function findResultsDownloadButton() {
    return document.querySelector(
      'span[title="Download results (CSVs + SQLite files)"] button:not(:disabled)',
    );
  }

  function findViewResultsButton() {
    return Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent.trim() === "View Results",
    );
  }

  function isViewingResults() {
    return Boolean(findButton("← Draw"));
  }

  function isAnalyticsAvailable() {
    const analyticsButton = findButton("Analytics");
    return Boolean(analyticsButton && !analyticsButton.disabled);
  }

  function getSimulationCompletedModal() {
    const heading = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6")).find(
      (candidate) => candidate.textContent.trim() === "Simulation Completed",
    );
    if (!heading) {
      return null;
    }
    return (
      heading.closest(".simulation-progress-modal") ||
      heading.closest(".modal") ||
      heading.parentElement
    );
  }

  function hasSimulationCompleted() {
    return Boolean(getSimulationCompletedModal());
  }

  function readFirstNumber(text, pattern) {
    const match = text.match(pattern);
    if (!match) {
      return null;
    }
    const value = Number(match[1]);
    return Number.isFinite(value) ? value : null;
  }

  function readSimulationResultSummary() {
    const modal = getSimulationCompletedModal();
    if (!modal) {
      return null;
    }

    const rawText = modal.innerText.replace(/\s+/g, " ").trim();
    return {
      source: "simulation_progress_modal",
      captured_at: new Date().toISOString(),
      progress_percent: readFirstNumber(rawText, /(\d+(?:\.\d+)?)%/),
      total_agents: readFirstNumber(rawText, /Total Agents:\s*(\d+)/i),
      agents_evacuated: readFirstNumber(rawText, /Evacuated:\s*(\d+)/i),
      evacuation_time_seconds: readFirstNumber(
        rawText,
        /Evacuation Time:\s*(\d+(?:\.\d+)?)s/i,
      ),
      execution_time_seconds: readFirstNumber(
        rawText,
        /Execution Time:\s*(\d+(?:\.\d+)?)s/i,
      ),
      raw_summary_text: rawText.slice(0, 2000),
    };
  }

  async function publishSimulationResult(baseUrl, simulationId, result) {
    if (!result) {
      return;
    }
    const digest = JSON.stringify({
      simulationId,
      result: { ...result, captured_at: null },
    });
    if (digest === lastPublishedResultDigest) {
      return;
    }
    const response = await fetch(`${baseUrl}/api/results`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulation_id: simulationId, result }),
    });
    if (!response.ok) {
      throw new Error(`Simulation result publish failed: ${response.status}`);
    }
    lastPublishedResultDigest = digest;
  }

  function requestResultArchive(baseUrl, simulationId, result) {
    if (
      !result ||
      resultArchiveRequestPending ||
      resultArchivePublishInProgress ||
      !activePort
    ) {
      return;
    }
    const archiveDigest = JSON.stringify({
      simulationId,
      result: { ...result, captured_at: null },
    });
    if (archiveDigest === lastRequestedResultArchiveDigest) {
      return;
    }

    const downloadButton = findResultsDownloadButton();
    if (!downloadButton) {
      return;
    }

    resultArchiveRequestPending = true;
    suppressNextResultsDownload = true;
    activeResultArchiveSimulationId = simulationId || null;
    lastRequestedResultArchiveDigest = archiveDigest;
    document.documentElement.dataset.jupedsimBridgeResultsArchive = "capturing";
    resultArchiveFallbackTimer = window.setTimeout(() => {
      clearPendingResultArchive();
      if (lastRequestedResultArchiveDigest === archiveDigest) {
        lastRequestedResultArchiveDigest = null;
      }
    }, 120000);
    downloadButton.click();
  }

  async function publishVisibleSimulationResult(baseUrl, simulationId) {
    const result = readSimulationResultSummary();
    if (!result) {
      return null;
    }
    await publishSimulationResult(baseUrl, simulationId, result);
    requestResultArchive(baseUrl, simulationId, result);
    return result;
  }

  async function publishSimulationStatus(baseUrl, commandId, status, detail, result) {
    const response = await fetch(
      `${baseUrl}/api/simulations/${encodeURIComponent(commandId)}/status`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, detail, result }),
      },
    );
    if (!response.ok) {
      throw new Error(`Simulation status publish failed: ${response.status}`);
    }
  }

  async function publishViewResultsStatus(baseUrl, commandId, status, detail) {
    const response = await fetch(
      `${baseUrl}/api/results/view/${encodeURIComponent(commandId)}/status`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, detail }),
      },
    );
    if (!response.ok) {
      throw new Error(`View-results status publish failed: ${response.status}`);
    }
  }

  async function publishClearSceneStatus(baseUrl, commandId, status, detail, result) {
    const response = await fetch(
      `${baseUrl}/api/scenarios/clear/${encodeURIComponent(commandId)}/status`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, detail, result }),
      },
    );
    if (!response.ok) {
      throw new Error(`Clear-scene status publish failed: ${response.status}`);
    }
  }

  async function openResultsView(button, statusContext = "automatic") {
    if (isViewingResults()) {
      document.documentElement.dataset.jupedsimBridgeViewResults = "completed";
      updateButton(button, activePort, "results visible");
      return {
        completed: true,
        detail: "Results view is already open.",
      };
    }

    const viewResultsButton = findViewResultsButton();
    if (!viewResultsButton || viewResultsButton.disabled) {
      document.documentElement.dataset.jupedsimBridgeViewResults = "rejected";
      return {
        completed: false,
        detail:
          statusContext === "automatic"
            ? "View Results is unavailable after simulation completion."
            : "View Results is unavailable. Wait for a completed simulation modal.",
      };
    }

    viewResultsButton.click();
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    const completed = isViewingResults();
    document.documentElement.dataset.jupedsimBridgeViewResults = completed
      ? "completed"
      : "failed";
    if (completed) {
      updateButton(button, activePort, "results visible");
    }
    return {
      completed,
      detail: completed
        ? "The viewer clicked View Results and opened results mode."
        : "The viewer clicked View Results, but results mode did not open.",
    };
  }

  function getElementsManagerRoot() {
    const heading = Array.from(document.querySelectorAll("h1, h2, h3")).find(
      (candidate) => candidate.textContent.trim() === "Elements Manager",
    );
    if (!heading) {
      return null;
    }

    let current = heading.parentElement;
    while (current && current !== document.body) {
      const text = current.innerText || "";
      if (
        /BOUNDARIES\s*\(/i.test(text) &&
        /EXITS\s*\(/i.test(text) &&
        /STARTING AREAS\s*\(/i.test(text)
      ) {
        return current;
      }
      current = current.parentElement;
    }
    return heading.parentElement;
  }

  function parseSectionCount(root, sectionName) {
    const pattern = new RegExp(`${sectionName}\\s*\\((\\d+)\\)`, "i");
    const match = (root?.innerText || "").match(pattern);
    return match ? Number(match[1]) : null;
  }

  function readElementsManagerCounts(root = getElementsManagerRoot()) {
    return {
      boundaries: parseSectionCount(root, "BOUNDARIES"),
      exits: parseSectionCount(root, "EXITS"),
      starting_areas: parseSectionCount(root, "STARTING AREAS"),
      stages: parseSectionCount(root, "STAGES"),
      zones: parseSectionCount(root, "ZONES"),
      obstacles: parseSectionCount(root, "OBSTACLES"),
    };
  }

  function isElementsManagerEmpty(root = getElementsManagerRoot()) {
    const counts = readElementsManagerCounts(root);
    return Object.values(counts).every((count) => count === 0);
  }

  function findElementDeleteButtons(root = getElementsManagerRoot()) {
    if (!root) {
      return [];
    }
    return Array.from(root.querySelectorAll("button")).filter((button) => {
      const text = button.textContent.trim();
      const ariaLabel = button.getAttribute("aria-label") || "";
      const title = button.getAttribute("title") || "";
      if (button.disabled || text !== "×") {
        return false;
      }
      if (/close/i.test(ariaLabel) || /close/i.test(title)) {
        return false;
      }
      return true;
    });
  }

  async function openDrawModeIfNeeded() {
    const backToDraw = findButton("← Draw");
    if (backToDraw && !backToDraw.disabled) {
      backToDraw.click();
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
  }

  async function openElementsPanel() {
    const elementsButton = findButton("Elements");
    if (!elementsButton || elementsButton.disabled) {
      return false;
    }
    elementsButton.click();
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    return Boolean(getElementsManagerRoot());
  }

  async function closeElementsPanel() {
    if (!getElementsManagerRoot()) {
      return;
    }
    const elementsButton = findButton("Elements");
    if (!elementsButton || elementsButton.disabled) {
      return;
    }
    elementsButton.click();
    await new Promise((resolve) => window.setTimeout(resolve, 200));
  }

  function findElementsDrawerContainer(root) {
    let current = root;
    while (current && current !== document.body) {
      const cs = window.getComputedStyle(current);
      if (cs.position === "fixed" || cs.position === "absolute") {
        return current;
      }
      current = current.parentElement;
    }
    return root;
  }

  function hideElementsPanelDuringClear() {
    const styleId = "jupedsim-bridge-clear-hide";
    if (document.getElementById(styleId)) {
      return styleId;
    }
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      html[data-jupedsim-bridge-clear-scene="capturing"] [data-jupedsim-bridge-hidden-drawer="true"] {
        opacity: 0 !important;
        pointer-events: none !important;
        transition: none !important;
      }
    `;
    document.head.appendChild(style);
    return styleId;
  }

  async function clearSceneThroughElementsManager() {
    await openDrawModeIfNeeded();
    hideElementsPanelDuringClear();
    document.documentElement.dataset.jupedsimBridgeClearScene = "capturing";

    const opened = await openElementsPanel();
    if (!opened) {
      delete document.documentElement.dataset.jupedsimBridgeClearScene;
      throw new Error("Elements panel is unavailable.");
    }

    const drawer = findElementsDrawerContainer(getElementsManagerRoot());
    if (drawer) {
      drawer.setAttribute("data-jupedsim-bridge-hidden-drawer", "true");
    }

    let deletedCount = 0;
    let finalResult;
    try {
      for (let attempt = 0; attempt < 200; attempt += 1) {
        const root = getElementsManagerRoot();
        const deleteButtons = findElementDeleteButtons(root);
        if (deleteButtons.length === 0) {
          finalResult = {
            deleted_count: deletedCount,
            empty: isElementsManagerEmpty(root),
            counts: readElementsManagerCounts(root),
          };
          break;
        }
        deleteButtons[0].click();
        deletedCount += 1;
        await new Promise((resolve) => window.setTimeout(resolve, 150));
      }
      if (!finalResult) {
        finalResult = {
          deleted_count: deletedCount,
          empty: isElementsManagerEmpty(),
          counts: readElementsManagerCounts(),
          limit_reached: true,
        };
      }
    } finally {
      await closeElementsPanel();
      if (drawer) {
        drawer.removeAttribute("data-jupedsim-bridge-hidden-drawer");
      }
      delete document.documentElement.dataset.jupedsimBridgeClearScene;
    }

    return finalResult;
  }

  async function pollClearSceneCommand(button, baseUrl) {
    if (clearSceneCommandPollInProgress) {
      return;
    }

    clearSceneCommandPollInProgress = true;
    try {
      const after = lastClearSceneCommandId
        ? `?after=${encodeURIComponent(lastClearSceneCommandId)}`
        : "";
      const response = await fetch(`${baseUrl}/api/scenarios/clear/latest${after}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Latest clear-scene request failed: ${response.status}`);
      }

      const latest = await response.json();
      if (!latest.clear_scene) {
        return;
      }

      const command = latest.clear_scene;
      lastClearSceneCommandId = command.id;
      if (command.status !== "queued") {
        return;
      }

      await publishClearSceneStatus(
        baseUrl,
        command.id,
        "accepted",
        "The viewer accepted the clear-scene request.",
      );
      const result = await clearSceneThroughElementsManager();
      const completed = Boolean(result.empty);
      document.documentElement.dataset.jupedsimBridgeClearScene = completed
        ? "completed"
        : "failed";
      await publishClearSceneStatus(
        baseUrl,
        command.id,
        completed ? "completed" : "failed",
        completed
          ? "The viewer deleted all scene elements through the Elements panel."
          : "The viewer attempted to delete scene elements, but some elements remain.",
        result,
      );
      updateButton(button, activePort, completed ? "scene cleared" : "clear failed");
    } catch (error) {
      console.debug("JuPedSim clear-scene command could not be handled.", error);
    } finally {
      clearSceneCommandPollInProgress = false;
    }
  }

  async function pollViewResultsCommand(button, baseUrl) {
    if (viewResultsCommandPollInProgress) {
      return;
    }

    viewResultsCommandPollInProgress = true;
    try {
      const after = lastViewResultsCommandId
        ? `?after=${encodeURIComponent(lastViewResultsCommandId)}`
        : "";
      const response = await fetch(`${baseUrl}/api/results/view/latest${after}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Latest view-results request failed: ${response.status}`);
      }

      const latest = await response.json();
      if (!latest.view_results) {
        return;
      }

      const command = latest.view_results;
      lastViewResultsCommandId = command.id;
      if (command.status !== "queued") {
        return;
      }

      if (isViewingResults()) {
        await publishViewResultsStatus(
          baseUrl,
          command.id,
          "completed",
          "Results view is already open.",
        );
        updateButton(button, activePort, "results visible");
        return;
      }

      const viewResultsButton = findViewResultsButton();
      if (!viewResultsButton || viewResultsButton.disabled) {
        document.documentElement.dataset.jupedsimBridgeViewResults = "rejected";
        await publishViewResultsStatus(
          baseUrl,
          command.id,
          "rejected",
          "View Results is unavailable. Wait for a completed simulation modal.",
        );
        return;
      }

      await publishViewResultsStatus(
        baseUrl,
        command.id,
        "accepted",
        "The viewer accepted the View Results request.",
      );
      const opened = await openResultsView(button, "command");
      await publishViewResultsStatus(
        baseUrl,
        command.id,
        opened.completed ? "completed" : "failed",
        opened.detail,
      );
    } catch (error) {
      console.debug("JuPedSim view-results command could not be handled.", error);
    } finally {
      viewResultsCommandPollInProgress = false;
    }
  }

  function monitorSimulationCompletion(button, baseUrl, commandId, analyticsWasAvailable) {
    if (simulationMonitorTimer) {
      window.clearInterval(simulationMonitorTimer);
    }

    const startedAt = Date.now();
    let sawRunButtonDisabled = false;
    let sawAnalyticsUnavailable = !analyticsWasAvailable;
    const check = async () => {
      const runButton = findButton("Run Simulation");
      const analyticsAvailable = isAnalyticsAvailable();
      const simulationCompleted = hasSimulationCompleted();
      sawRunButtonDisabled ||= Boolean(runButton && runButton.disabled);
      sawAnalyticsUnavailable ||= !analyticsAvailable;
      const sawCompletionSignal =
        !analyticsWasAvailable || sawRunButtonDisabled || sawAnalyticsUnavailable;

      if (
        (simulationCompleted || (analyticsAvailable && sawCompletionSignal)) &&
        (!runButton || !runButton.disabled) &&
        (simulationCompleted || sawCompletionSignal)
      ) {
        window.clearInterval(simulationMonitorTimer);
        simulationMonitorTimer = null;
        document.documentElement.dataset.jupedsimBridgeSimulation = "completed";
        try {
          const result = readSimulationResultSummary();
          await publishSimulationStatus(
            baseUrl,
            commandId,
            "completed",
            simulationCompleted
              ? "The viewer reported Simulation Completed."
              : "Analytics results are available in the viewer.",
            result,
          );
          await publishSimulationResult(baseUrl, commandId, result);
          if (simulationCompleted) {
            const opened = await openResultsView(button, "automatic");
            if (!opened.completed) {
              console.debug("JuPedSim automatic View Results did not complete.", opened.detail);
            }
          }
        } catch (error) {
          console.debug("JuPedSim simulation completion could not be published.", error);
        }
        requestUiSnapshot();
        return;
      }

      if (Date.now() - startedAt >= SIMULATION_TIMEOUT_MS) {
        window.clearInterval(simulationMonitorTimer);
        simulationMonitorTimer = null;
        document.documentElement.dataset.jupedsimBridgeSimulation = "failed";
        try {
          await publishSimulationStatus(
            baseUrl,
            commandId,
            "failed",
            "The viewer did not expose Analytics results before the timeout.",
          );
        } catch (error) {
          console.debug("JuPedSim simulation timeout could not be published.", error);
        }
      }
    };

    simulationMonitorTimer = window.setInterval(check, POLL_INTERVAL_MS);
    check();
  }

  async function pollSimulationCommand(button, baseUrl) {
    if (simulationCommandPollInProgress) {
      return;
    }

    simulationCommandPollInProgress = true;
    // Remember the id we polled with so a failed status publish can hand the
    // command back to the next poll instead of silently dropping it.
    const previousCommandId = lastSimulationCommandId;
    let claimedCommandId = null;
    try {
      const after = lastSimulationCommandId
        ? `?after=${encodeURIComponent(lastSimulationCommandId)}`
        : "";
      const response = await fetch(`${baseUrl}/api/simulations/latest${after}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Latest simulation request failed: ${response.status}`);
      }

      const latest = await response.json();
      await publishVisibleSimulationResult(baseUrl, lastSimulationCommandId);
      if (!latest.simulation) {
        return;
      }

      const simulation = latest.simulation;
      lastSimulationCommandId = simulation.id;
      claimedCommandId = simulation.id;
      if (simulation.status === "running") {
        document.documentElement.dataset.jupedsimBridgeSimulation = "running";
        monitorSimulationCompletion(button, baseUrl, simulation.id, isAnalyticsAvailable());
        return;
      }
      if (simulation.status === "accepted") {
        // Run Simulation was already clicked (this session or before a
        // reload) but the "running" update never reached the bridge. Resume
        // monitoring without clicking again.
        document.documentElement.dataset.jupedsimBridgeSimulation = "running";
        await publishSimulationStatus(
          baseUrl,
          simulation.id,
          "running",
          "The viewer resumed monitoring an accepted run request.",
        );
        monitorSimulationCompletion(button, baseUrl, simulation.id, isAnalyticsAvailable());
        return;
      }
      if (simulation.status !== "queued") {
        return;
      }

      const runButton = findButton("Run Simulation");
      if (!runButton || runButton.disabled) {
        document.documentElement.dataset.jupedsimBridgeSimulation = "rejected";
        await publishSimulationStatus(
          baseUrl,
          simulation.id,
          "rejected",
          "Run Simulation is unavailable in the viewer. Check the scenario configuration and quota.",
        );
        return;
      }

      const analyticsWasAvailable = isAnalyticsAvailable();
      await publishSimulationStatus(
        baseUrl,
        simulation.id,
        "accepted",
        "The viewer accepted the run request.",
      );
      runButton.click();
      document.documentElement.dataset.jupedsimBridgeSimulation = "running";
      await publishSimulationStatus(
        baseUrl,
        simulation.id,
        "running",
        "The viewer clicked Run Simulation.",
      );
      updateButton(button, activePort, "simulation running");
      monitorSimulationCompletion(button, baseUrl, simulation.id, analyticsWasAvailable);
    } catch (error) {
      if (claimedCommandId !== null) {
        lastSimulationCommandId = previousCommandId;
      }
      console.debug("JuPedSim simulation command could not be handled.", error);
    } finally {
      simulationCommandPollInProgress = false;
    }
  }

  async function importLatestScenario(button, baseUrl) {
    if (importInProgress) {
      return;
    }

    importInProgress = true;
    try {
      const after = lastScenarioId ? `?after=${encodeURIComponent(lastScenarioId)}` : "";
      const latestResponse = await fetch(`${baseUrl}/api/scenarios/latest${after}`, {
        cache: "no-store",
      });
      if (!latestResponse.ok) {
        throw new Error(`Latest scenario request failed: ${latestResponse.status}`);
      }

      const latest = await latestResponse.json();
      updateButton(button, activePort, "connected");
      if (!latest.scenario) {
        return;
      }

      const canvas = document.querySelector("canvas");
      if (!canvas) {
        updateButton(button, activePort, "waiting for canvas");
        return;
      }

      const bundleResponse = await fetch(
        new URL(latest.scenario.bundle_url, baseUrl),
        { cache: "no-store" },
      );
      if (!bundleResponse.ok) {
        throw new Error(`Scenario bundle request failed: ${bundleResponse.status}`);
      }

      const bundle = await bundleResponse.blob();
      const transfer = new DataTransfer();
      transfer.items.add(
        new File([bundle], latest.scenario.filename, {
          type: "application/zip",
        }),
      );
      canvas.dispatchEvent(
        new DragEvent("drop", {
          bubbles: true,
          cancelable: true,
          dataTransfer: transfer,
        }),
      );

      lastScenarioId = latest.scenario.id;
      updateButton(button, activePort, "scenario imported");
      window.dispatchEvent(
        new CustomEvent("jupedsim:bridge-scenario-imported", {
          detail: latest.scenario,
        }),
      );
    } catch (error) {
      updateButton(button, activePort, "offline");
      console.debug("JuPedSim HTTP bridge is unavailable.", error);
    } finally {
      importInProgress = false;
      requestUiSnapshot();
    }
  }

  function startBridgePolling(button, port) {
    if (pollTimer) {
      window.clearInterval(pollTimer);
    }
    activePort = port;
    lastScenarioId = null;
    lastClearSceneCommandId = null;
    lastSimulationCommandId = null;
    lastViewResultsCommandId = null;
    lastPublishedResultDigest = null;
    lastRequestedResultArchiveDigest = null;
    clearPendingResultArchive();
    updateButton(button, port);

    const poll = async () => {
      const baseUrl = getBaseUrl(port);
      await importLatestScenario(button, baseUrl);
      await pollClearSceneCommand(button, baseUrl);
      await pollSimulationCommand(button, baseUrl);
      await pollViewResultsCommand(button, baseUrl);
      await publishVisibleSimulationResult(baseUrl, lastSimulationCommandId);
    };
    poll();
    pollTimer = window.setInterval(poll, POLL_INTERVAL_MS);
  }

  function ensureDialogStyles() {
    if (document.querySelector("#jupedsim-bridge-dialog-styles")) {
      return;
    }

    const styles = document.createElement("style");
    styles.id = "jupedsim-bridge-dialog-styles";
    styles.textContent = `
      .jupedsim-bridge-dialog-backdrop {
        align-items: center;
        background: rgba(2, 6, 16, 0.72);
        display: flex;
        inset: 0;
        justify-content: center;
        position: fixed;
        z-index: 10000;
      }

      .jupedsim-bridge-dialog {
        background: #0c1424;
        border: 1px solid rgba(108, 139, 255, 0.36);
        border-radius: 14px;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.48);
        color: #f4f7ff;
        font-family: inherit;
        max-width: 360px;
        padding: 22px;
        width: calc(100% - 48px);
      }

      .jupedsim-bridge-dialog h2 {
        font-size: 18px;
        margin: 0 0 8px;
      }

      .jupedsim-bridge-dialog p {
        color: #aeb9cf;
        font-size: 14px;
        margin: 0 0 14px;
      }

      .jupedsim-bridge-dialog input {
        background: #080e1a;
        border: 1px solid rgba(108, 139, 255, 0.5);
        border-radius: 8px;
        box-sizing: border-box;
        color: #f4f7ff;
        font: inherit;
        padding: 10px 12px;
        width: 100%;
      }

      .jupedsim-bridge-dialog-error {
        color: #ff9b9b;
        min-height: 18px;
        padding-top: 8px;
      }

      .jupedsim-bridge-dialog-actions {
        display: flex;
        gap: 8px;
        justify-content: flex-end;
        margin-top: 8px;
      }

      .jupedsim-bridge-dialog-actions button {
        background: #111c30;
        border: 1px solid rgba(108, 139, 255, 0.42);
        border-radius: 8px;
        color: #f4f7ff;
        cursor: pointer;
        font: inherit;
        padding: 8px 16px;
      }

      .jupedsim-bridge-dialog-actions button[data-bridge-dialog-confirm] {
        background: #2867e8;
      }
    `;
    document.head.appendChild(styles);
  }

  function confirmPort(button) {
    ensureDialogStyles();

    const existingDialog = document.querySelector(
      "[data-jupedsim-bridge-dialog]",
    );
    if (existingDialog) {
      existingDialog.querySelector("input").focus();
      return;
    }

    const backdrop = document.createElement("div");
    backdrop.className = "jupedsim-bridge-dialog-backdrop";
    backdrop.dataset.jupedsimBridgeDialog = "true";

    const dialog = document.createElement("section");
    dialog.className = "jupedsim-bridge-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "jupedsim-bridge-dialog-title");

    const title = document.createElement("h2");
    title.id = "jupedsim-bridge-dialog-title";
    title.textContent = "HTTP Bridge";

    const description = document.createElement("p");
    description.textContent = "Confirm the port number used by the local bridge.";

    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "65535";
    input.value = getStoredPort();
    input.setAttribute("aria-label", "HTTP bridge port number");

    const error = document.createElement("div");
    error.className = "jupedsim-bridge-dialog-error";
    error.setAttribute("aria-live", "polite");

    const actions = document.createElement("div");
    actions.className = "jupedsim-bridge-dialog-actions";

    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.textContent = "Cancel";

    const confirmButton = document.createElement("button");
    confirmButton.type = "button";
    confirmButton.textContent = "OK";
    confirmButton.dataset.bridgeDialogConfirm = "true";

    function closeDialog() {
      backdrop.remove();
      button.focus();
    }

    function savePort() {
      const port = input.value.trim();
      if (!isValidPort(port)) {
        error.textContent = "Enter a port number between 1 and 65535.";
        input.focus();
        return;
      }

      window.localStorage.setItem(STORAGE_KEY, port);
      startBridgePolling(button, port);
      window.dispatchEvent(
        new CustomEvent("jupedsim:bridge-port-confirmed", {
          detail: {
            port: Number(port),
            baseUrl: getBaseUrl(port),
          },
        }),
      );
      closeDialog();
    }

    cancelButton.addEventListener("click", closeDialog);
    confirmButton.addEventListener("click", savePort);
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) {
        closeDialog();
      }
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        savePort();
      } else if (event.key === "Escape") {
        closeDialog();
      }
    });

    actions.append(cancelButton, confirmButton);
    dialog.append(title, description, input, error, actions);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    input.select();
    input.focus();
  }

  function addBridgeButton() {
    if (document.querySelector("[data-jupedsim-bridge-button]")) {
      return true;
    }

    const analyticsButton = Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent.trim() === "Analytics",
    );
    if (!analyticsButton) {
      return false;
    }

    const anchor = analyticsButton.closest(".toolbar-btn-wrap") || analyticsButton;
    const bridgeButton = document.createElement("button");
    bridgeButton.type = "button";
    bridgeButton.className = "toolbar-btn";
    bridgeButton.textContent = "Bridge";
    bridgeButton.dataset.jupedsimBridgeButton = "true";
    startBridgePolling(bridgeButton, getStoredPort());
    bridgeButton.addEventListener("click", () => confirmPort(bridgeButton));
    anchor.insertAdjacentElement("afterend", bridgeButton);
    return true;
  }

  if (!addBridgeButton()) {
    const observer = new MutationObserver(() => {
      if (addBridgeButton()) {
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }
  installDownloadInterceptor();
})();
