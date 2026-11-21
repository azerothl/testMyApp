(() => {
  const current = { lcp: null, cls: null, interactions: new Map(), longTasks: 0 };
  const observers = [];
  let shiftStart = -Infinity;
  let shiftLast = -Infinity;
  let shiftTotal = 0;

  function observe(type, update, options = {}) {
    if (!PerformanceObserver.supportedEntryTypes.includes(type)) return;
    const observer = new PerformanceObserver(list => list.getEntries().forEach(update));
    observer.observe({ type, buffered: true, ...options });
    observers.push({ observer, update });
  }

  observe("largest-contentful-paint", entry => { current.lcp = entry.startTime; });
  observe("layout-shift", entry => {
    if (entry.hadRecentInput) return;
    if (entry.startTime - shiftLast >= 1000 || entry.startTime - shiftStart >= 5000) {
      shiftStart = entry.startTime;
      shiftTotal = 0;
    }
    shiftLast = entry.startTime;
    shiftTotal += entry.value;
    current.cls = Math.max(current.cls ?? 0, shiftTotal);
  });
  observe("event", entry => {
    if (!entry.interactionId) return;
    const previous = current.interactions.get(entry.interactionId) ?? 0;
    current.interactions.set(entry.interactionId, Math.max(previous, entry.duration));
  }, { durationThreshold: 16 });
  observe("longtask", () => { current.longTasks++; });

  window.__testmyappVitals = () => {
    for (const { observer, update } of observers) observer.takeRecords().forEach(update);
    const result = {};
    const navigation = performance.getEntriesByType("navigation")[0];
    if (navigation?.responseStart > 0) result.ttfb_ms = Math.round(navigation.responseStart);
    const paint = performance.getEntriesByName("first-contentful-paint")[0];
    if (paint) result.fcp_ms = Math.round(paint.startTime);
    if (current.lcp !== null) result.lcp_ms = Math.round(current.lcp);
    if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      result.cls = Number((current.cls ?? 0).toFixed(4));
    }
    const interactions = [...current.interactions.values()].sort((a, b) => b - a);
    if (interactions.length) {
      const rank = Math.floor((performance.interactionCount || interactions.length) / 50);
      if (rank < interactions.length) result.inp_ms = interactions[rank];
    }
    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) result.long_tasks = current.longTasks;
    return result;
  };
})();
