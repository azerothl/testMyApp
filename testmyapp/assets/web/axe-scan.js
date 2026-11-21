async () => {
  const results = await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    resultTypes: ["violations", "incomplete"]
  });
  const summarize = rules => rules.flatMap(rule => rule.nodes.map(node => ({
    rule: rule.id,
    severity: node.impact || rule.impact || "moderate",
    selector: node.target.map(target => Array.isArray(target) ? target.join(" ") : target).join(" → "),
    wcag: rule.tags.filter(tag => /^wcag\d{3}$/.test(tag)).map(tag => tag.slice(4).split("").join(".")),
    source: "axe-core"
  })));
  return { violations: summarize(results.violations), review: summarize(results.incomplete) };
}
