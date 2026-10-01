"""The package's long description and project URLs, with their repository links pinned to this release.

The README links into the repository with absolute ``main`` URLs, because PyPI has no
repository to resolve a relative link in. PyPI keeps a page for every release, though, and
pinned to ``main`` the 1.0.0 page would show the documents of whatever came after it, and a
moved file would break the links of every older page. The release workflow builds from the
tag ``v<version>`` (it checks the two agree), so that tag is what the links name. The project
URLs in the sidebar of the same page (``[tool.hatch.metadata.hooks.custom.urls]``) are pinned
the same way.
"""

from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface

BRANCH_URLS = (
    "https://github.com/itda-work/django-wireview/blob/main/",
    "https://github.com/itda-work/django-wireview/tree/main/",
    "https://raw.githubusercontent.com/itda-work/django-wireview/main/",
)


def pin(text: str, tag: str) -> str:
    for url in BRANCH_URLS:
        text = text.replace(url, url.removesuffix("main/") + f"{tag}/")
    return text


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        tag = f"v{metadata['version']}"
        readme = (Path(self.root) / "README.md").read_text(encoding="utf-8")
        metadata["readme"] = {"content-type": "text/markdown", "text": pin(readme, tag)}
        metadata["urls"] = {name: pin(url, tag) for name, url in self.config["urls"].items()}
