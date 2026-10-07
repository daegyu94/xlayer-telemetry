/* Native figures, tables and legacy guide links; no framework or stored preferences. */
(() => {
  document.addEventListener("DOMContentLoaded", () => {
    const small = new Set(["collect-correlate-diagnose", "evidence-scope", "sdk-example", "node-registration", "pipeline-events"]);
    const large = new Set(["system-overview", "storage-path", "mooncake-path", "sandbox-dedicated", "package-layout"]);
    document.querySelectorAll("article img[src$='.svg']").forEach((image) => {
      const source = image.src;
      const name = new URL(source).pathname.split("/").pop().replace(/\.svg$/, "");
      const size = small.has(name) ? "small" : large.has(name) ? "large" : "medium";
      const paragraph = image.parentElement;
      const standalone = paragraph.tagName === "P" && [...paragraph.childNodes].every(
        (node) => node === image || (node.nodeType === Node.TEXT_NODE && !node.textContent.trim()));
      const wrapper = document.createElement(standalone ? "figure" : "span");
      wrapper.className = `xlayer-diagram xlayer-diagram-${size}`;
      const link = document.createElement("a");
      link.className = "xlayer-diagram-link";
      link.href = source;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "그림을 눌러 SVG 확대 보기";
      link.setAttribute("aria-label", `${image.alt} — 원본 SVG 확대 (새 탭)`);
      if (standalone) paragraph.replaceWith(wrapper);
      else image.parentNode.insertBefore(wrapper, image);
      wrapper.appendChild(link);
      link.appendChild(image);
      image.classList.add("xlayer-diagram-image");
      const caption = document.createElement(standalone ? "figcaption" : "span");
      caption.className = "xlayer-diagram-caption";
      caption.textContent = image.alt;
      wrapper.appendChild(caption);
    });
    document.querySelectorAll("article table").forEach((table) => {
      const container = table.closest(".table-wrapper") || table.parentElement;
      if (container === table.parentElement && !container.classList.contains("table-wrapper")) {
        const wrapper = document.createElement("div");
        wrapper.className = "xlayer-table-scroll";
        wrapper.tabIndex = 0;
        wrapper.setAttribute("role", "region");
        wrapper.setAttribute("aria-label", "표 — 좁은 화면에서 좌우로 스크롤");
        table.replaceWith(wrapper);
        wrapper.appendChild(table);
      } else {
        container.tabIndex = 0;
        container.setAttribute("aria-label", "표 — 좁은 화면에서 좌우로 스크롤");
      }
    });
    let selected;
    try { selected = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch { /* Invalid fragments remain on the guide. */ }
    if (selected?.classList.contains("xlayer-legacy-anchor")) {
      const anchorBlock = selected.closest("p") || selected;
      const destination = anchorBlock.nextElementSibling?.querySelector("a[href]");
      if (destination) location.replace(destination.href);
    }
    document.querySelectorAll(".xlayer-legacy-links").forEach((container) => {
      const details = document.createElement("details");
      details.className = container.className;
      const summary = document.createElement("summary");
      summary.textContent = "이전 문서 절 바로가기";
      details.appendChild(summary);
      while (container.firstChild) details.appendChild(container.firstChild);
      container.replaceWith(details);
    });
  });
})();
