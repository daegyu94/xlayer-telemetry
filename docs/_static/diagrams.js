/* Only static images are delivered to readers; editors are build-time tools. */
(() => {
  const assets = new URL("excalidraw/", document.currentScript.src);
  const key = "xlayer-figure-style";
  let style = "d2";
  try {
    if (localStorage.getItem(key) === "excalidraw") style = "excalidraw";
  } catch (_) { /* Private browsing may disable storage. */ }

  document.addEventListener("DOMContentLoaded", () => {
    const figures = [];
    const selectors = [];
    const apply = () => {
      selectors.forEach((select) => { select.value = style; });
      figures.forEach(({ image, link, source, alternate, download }) => {
        image.src = style === "excalidraw" ? alternate : source;
        link.href = image.src;
        download.hidden = style !== "excalidraw";
      });
    };
    // Furo has separate desktop and mobile theme controls.
    document.querySelectorAll(".theme-toggle-container").forEach((container) => {
      const label = document.createElement("label");
      label.className = "xlayer-figure-control";
      const caption = document.createElement("span");
      caption.textContent = "Figure";
      label.appendChild(caption);
      const select = document.createElement("select");
      select.setAttribute("aria-label", "Figure style");
      select.title = "문서 전체 그림의 스타일 변경";
      [["d2", "D2"], ["excalidraw", "Excalidraw"]].forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        select.appendChild(option);
      });
      select.value = style;
      select.addEventListener("change", () => {
        style = select.value;
        try { localStorage.setItem(key, style); } catch (_) { /* Keep in-page choice. */ }
        apply();
      });
      label.appendChild(select);
      container.prepend(label);
      selectors.push(select);
    });
    document.querySelectorAll("article img[src$='.svg']").forEach((image) => {
      const source = image.src;
      const name = new URL(source).pathname.split("/").pop();
      const wrapper = document.createElement("span");
      wrapper.className = "xlayer-diagram";
      const link = document.createElement("a");
      link.className = "xlayer-diagram-link";
      link.href = source;
      link.target = "_blank";
      link.rel = "noopener";
      link.title = "그림을 눌러 SVG 확대 보기";
      image.parentNode.insertBefore(wrapper, image);
      wrapper.appendChild(link);
      link.appendChild(image);
      image.classList.add("xlayer-diagram-image");
      const zoom = document.createElement("span");
      zoom.className = "xlayer-diagram-zoom";
      zoom.textContent = "확대 보기";
      link.appendChild(zoom);
      const download = document.createElement("a");
      download.className = "xlayer-diagram-download";
      download.href = new URL(name.replace(/\.svg$/, ".excalidraw"), assets).href;
      download.download = name.replace(/\.svg$/, ".excalidraw");
      download.textContent = "Excalidraw 편집용 원본";
      wrapper.appendChild(download);
      const alternate = new URL(name, assets).href;
      image.addEventListener("error", () => {
        if (image.src !== source) {
          image.src = source;
          link.href = source;
          download.hidden = true;
        }
      });
      figures.push({ image, link, source, alternate, download });
    });
    apply();
  });
})();
