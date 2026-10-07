"""Build the existing Markdown guides without importing the telemetry runtime."""

project = "XLayer Telemetry"
author = "XLayer Telemetry contributors"
copyright = "2026, XLayer Telemetry contributors"
language = "ko"
root_doc = "index"
source_suffix = {".md": "markdown"}
extensions = ["myst_parser", "sphinx.ext.githubpages", "sphinx_copybutton"]
exclude_patterns = ["_build", "**/__pycache__"]
myst_heading_anchors = 6
myst_enable_extensions = ["colon_fence"]

html_theme = "furo"
html_title = "XLayer Telemetry"
html_baseurl = "https://daegyu94.github.io/xlayer-telemetry/"
html_static_path = ["_static"]
html_css_files = ["xlayer.css"]
html_js_files = ["diagrams.js"]
html_theme_options = {
    "source_repository": "https://github.com/daegyu94/xlayer-telemetry/",
    "source_branch": "main",
    "source_directory": "docs/",
    "light_css_variables": {"color-brand-primary": "#3d4fba", "color-brand-content": "#3d4fba",
                            "color-admonition-title--important": "#4f46e5",
                            "color-admonition-title-background--important": "#eef2ff",
                            "color-admonition-title--note": "#2563eb",
                            "color-admonition-title-background--note": "#eff6ff"},
    "dark_css_variables": {"color-brand-primary": "#91baff", "color-brand-content": "#91baff",
                           "color-admonition-title--important": "#a5b4fc",
                           "color-admonition-title-background--important": "#262846",
                           "color-admonition-title--note": "#93c5fd",
                           "color-admonition-title-background--note": "#17273f"},
}
copybutton_prompt_text = r"\$ |>>> |\.\.\. "
copybutton_prompt_is_regexp = True
html_show_sourcelink = False
