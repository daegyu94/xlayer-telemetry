/* Enhance the canonical D2 SVGs without changing their source or rendering. */
(() => {
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("article img[src$='.svg']").forEach((image) => {
      const source = image.src;
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
  });
})();
