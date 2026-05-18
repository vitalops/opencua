project = "opendesk"
copyright = "2026, Vitalops Technologies"
author = "Abhigith Neil Abraham, Fariz Rahman, Fadil Rahman"
release = "0.2.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_book_theme",
    "sphinx_design",
]

source_suffix = [".rst", ".md"]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "html_admonition",
    "html_image",
    "linkify",
    "replacements",
    "smartquotes",
    "strikethrough",
    "tasklist",
    "attrs_inline",
]

myst_highlight_code_blocks = True

html_theme = "sphinx_book_theme"
html_title = "opendesk"
html_logo = "_static/logo.png"
html_favicon = "_static/logo.png"
html_static_path = ["_static"]
html_baseurl = "https://vitalops.github.io/opendesk/docs/"

html_theme_options = {
    "logo": {
        "image_light": "_static/logo.png",
        "image_dark": "_static/logo.png",
    },
    "repository_url": "https://github.com/vitalops/opendesk",
    "use_repository_button": True,
    "use_issues_button": True,
    "use_download_button": True,
    "use_fullscreen_button": True,
    "path_to_docs": "docs",
    "show_navbar_depth": 2,
    "show_toc_level": 2,
    "announcement": "opendesk — Open Computer Use Agent for any AI framework",
}

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

linkcheck_ignore = [r"https://github.com/vitalops/opendesk/.*"]
