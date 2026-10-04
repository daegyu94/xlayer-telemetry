/* Enhance the canonical D2 SVGs without changing their source or rendering. */
(() => {
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("article img[src$='.svg']").forEach((image) => {
      const source = image.src;
      const name = new URL(source).pathname.split("/").pop();
      const paragraph = image.parentElement;
      const standalone = paragraph.tagName === "P" && [...paragraph.childNodes].every(
        (node) => node === image || (node.nodeType === Node.TEXT_NODE && !node.textContent.trim()));
      const wrapper = document.createElement(standalone ? "figure" : "span");
      wrapper.className = "xlayer-diagram";
      const link = document.createElement("a");
      link.className = "xlayer-diagram-link";
      link.href = source;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "그림을 눌러 SVG 확대 보기";
      if (standalone) paragraph.replaceWith(wrapper);
      else image.parentNode.insertBefore(wrapper, image);
      wrapper.appendChild(link);
      link.appendChild(image);
      image.classList.add("xlayer-diagram-image");
      const caption = document.createElement(standalone ? "figcaption" : "span");
      caption.className = "xlayer-diagram-caption";
      caption.textContent = image.alt;
      wrapper.appendChild(caption);
      const actions = document.createElement("span");
      actions.className = "xlayer-diagram-actions";
      wrapper.appendChild(actions);
      const zoom = document.createElement("a");
      zoom.className = "xlayer-diagram-zoom";
      zoom.href = source;
      zoom.target = "_blank";
      zoom.rel = "noopener";
      zoom.textContent = "확대 보기";
      actions.appendChild(zoom);
      const original = document.createElement("a");
      original.className = "xlayer-diagram-source";
      original.href = "https://github.com/daegyu94/xlayer-telemetry/blob/main/docs/diagrams/"
        + encodeURIComponent(name.replace(/\.svg$/, ".d2"));
      original.textContent = "D2 원본";
      actions.appendChild(original);
    });
  });
})();
