/* SVGs stay ordinary images without JavaScript; enhance zoom and small screens. */
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("article img[src$='.svg']").forEach((image) => {
    const enhance = () => {
      if (image.closest(".xlayer-diagram-scroll")) return;
      const scroll = document.createElement("span");
      scroll.className = "xlayer-diagram-scroll";
      const link = document.createElement("a");
      link.className = "xlayer-diagram-link";
      link.href = image.currentSrc || image.src;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "SVG를 새 탭에서 원본 크기로 보기";
      image.parentNode.insertBefore(scroll, image);
      scroll.appendChild(link);
      link.appendChild(image);
      image.classList.add("xlayer-diagram-image");
      // Diagram labels use at least 20px; keep at least 90% scale (18px).
      image.style.minWidth = `${Math.min(image.naturalWidth, Math.max(640, image.naturalWidth * 0.9))}px`;
    };
    if (image.complete && image.naturalWidth) enhance();
    else image.addEventListener("load", enhance, { once: true });
  });
});
