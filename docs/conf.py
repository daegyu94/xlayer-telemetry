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
    "light_css_variables": {"color-brand-primary": "#2355a0", "color-brand-content": "#2355a0"},
    "dark_css_variables": {"color-brand-primary": "#91baff", "color-brand-content": "#91baff"},
}
copybutton_prompt_text = r"\$ |>>> |\.\.\. "
copybutton_prompt_is_regexp = True
html_show_sourcelink = False
