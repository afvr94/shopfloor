mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });

// A diagram Mermaid can't parse stays readable as its plain source instead of an error box.
for (const el of document.querySelectorAll("pre.mermaid")) {
  const source = el.textContent;
  mermaid
    .parse(source)
    .then(() => mermaid.run({ nodes: [el] }))
    .catch(() => {
      el.className = "diagram-source";
      el.textContent = source;
    });
}
