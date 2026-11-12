# ╔══════════════════════════════════════════════════════════════════╗
# ║  Rig Conductor — help_window                                     ║
# ║  « the manual, one keypress away »                               ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Non-modal window showing docs/manual/user_manual.md: contents   ║
# ║  tree, find-as-you-type search, and Help / F1 on any wizard page ║
# ║  jumps to that page's section. Pictures shrink to fit.           ║
# ╚══════════════════════════════════════════════════════════════════╝
"""In-app user manual.

It renders the same Markdown the PDF is printed from, so editing
``docs/manual/user_manual.md`` updates both. The window is not modal: keep it
open beside the wizard while you work.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QImage,
    QKeySequence,
    QShortcut,
    QTextBlock,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rigconductor.constants import DIALOG_SCREEN_SHARE, WONG
from rigconductor.manual import MANUAL_PATH, load_manual, parse_sections, slugify

WINDOW_SIZE = (1100, 780)
CONTENTS_WIDTH = 270
MAX_IMAGE_WIDTH = 900
IMAGE_SIDE_MARGIN = 48
NO_MATCH_STYLE = "color: rgb{};".format(WONG["vermillion"])
EXTERNAL_SCHEMES = {"http", "https", "mailto"}


def _window_size() -> tuple[int, int]:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return WINDOW_SIZE
    g = screen.availableGeometry()
    return (
        min(WINDOW_SIZE[0], int(g.width() * DIALOG_SCREEN_SHARE)),
        min(WINDOW_SIZE[1], int(g.height() * DIALOG_SCREEN_SHARE)),
    )


class ManualBrowser(QTextBrowser):
    """QTextBrowser that finds pictures next to the manual and fits them."""

    def __init__(self, base_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_dir = base_dir
        self.setOpenLinks(False)
        self.document().setBaseUrl(QUrl.fromLocalFile(f"{base_dir}/"))

    def loadResource(self, kind: int, url: QUrl) -> object:  # noqa: N802 - Qt override
        if int(kind) == QTextDocument.ResourceType.ImageResource.value:
            image = QImage(str(self._resolve(url)))
            if not image.isNull():
                fit = min(MAX_IMAGE_WIDTH, max(200, self.viewport().width() - IMAGE_SIDE_MARGIN))
                if image.width() > fit:
                    image = image.scaledToWidth(fit, Qt.TransformationMode.SmoothTransformation)
                return image
        return super().loadResource(kind, url)

    def _resolve(self, url: QUrl) -> Path:
        return Path(url.toLocalFile()) if url.isLocalFile() else self._base_dir / url.path()


class HelpWindow(QWidget):
    """The user manual with a contents tree and a search bar."""

    def __init__(self, manual_path: Path = MANUAL_PATH) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Rig Conductor — user manual")
        self.resize(*_window_size())
        self._manual_path = manual_path
        self._heading_blocks: dict[str, QTextBlock] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._pending_slug: str | None = None

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the manual…")
        self.search.setClearButtonEnabled(True)
        self.prev_btn = QPushButton("Previous")
        self.next_btn = QPushButton("Next")
        self.match_lbl = QLabel()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        for w in (self.prev_btn, self.next_btn, self.match_lbl):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(close_btn)

        self.contents = QTreeWidget()
        self.contents.setHeaderLabel("Contents")
        self.browser = ManualBrowser(manual_path.parent)
        splitter = QSplitter()
        splitter.addWidget(self.contents)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([CONTENTS_WIDTH, self.width() - CONTENTS_WIDTH])

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(splitter, 1)

        self.contents.itemClicked.connect(self._on_contents_clicked)
        self.browser.anchorClicked.connect(self._on_link)
        self.search.textChanged.connect(lambda _t: self.find(from_start=True))
        self.search.returnPressed.connect(self.find)
        self.next_btn.clicked.connect(self.find)
        self.prev_btn.clicked.connect(lambda: self.find(backward=True))
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self, self.search.setFocus)
        QShortcut(QKeySequence(QKeySequence.StandardKey.FindNext), self, self.find)
        QShortcut(QKeySequence("Escape"), self, self.close)
        self.reload()

    def reload(self) -> None:
        """Read the manual from disk again (picks up edits without a restart)."""
        try:
            markdown = load_manual(self._manual_path)
        except OSError:
            self.browser.setPlainText(
                f"The manual is missing:\n{self._manual_path}\n\n"
                "Update the Conductor (Check for updates) or ask your admin."
            )
            return
        self.browser.setMarkdown(markdown)
        self._index_headings()
        self._build_contents(markdown)

    # ── navigation ────────────────────────────────────────────────
    def show_section(self, title_or_slug: str) -> bool:
        """Scroll to a heading; False when the manual has no such heading."""
        slug = slugify(title_or_slug)
        if slug not in self._heading_blocks:
            return False
        if not self.isVisible():
            self._pending_slug = slug  # the layout is final only once shown
            return True
        self._scroll_to(slug)
        return True

    def find(self, *, backward: bool = False, from_start: bool = False) -> bool:
        """Select the next (or previous) match, wrapping around."""
        text = self.search.text()
        if not text:
            self.match_lbl.clear()
            return False
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if from_start:
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        found = self.browser.find(text, flags)
        if not found:
            edge = QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start
            self.browser.moveCursor(edge)
            found = self.browser.find(text, flags)
        self.match_lbl.setText("" if found else "No matches")
        self.match_lbl.setStyleSheet("" if found else NO_MATCH_STYLE)
        return found

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._pending_slug is not None:
            slug, self._pending_slug = self._pending_slug, None
            QTimer.singleShot(0, lambda: self._scroll_to(slug))

    def _scroll_to(self, slug: str) -> None:
        block = self._heading_blocks[slug]
        layout = self.browser.document().documentLayout()
        self.browser.verticalScrollBar().setValue(int(layout.blockBoundingRect(block).top()))
        self.browser.setTextCursor(QTextCursor(block))
        item = self._tree_items.get(slug)
        if item is not None:
            self.contents.setCurrentItem(item)

    def _index_headings(self) -> None:
        self._heading_blocks.clear()
        block = self.browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0:
                self._heading_blocks.setdefault(slugify(block.text()), block)
            block = block.next()

    def _build_contents(self, markdown: str) -> None:
        self.contents.clear()
        self._tree_items.clear()
        parent: QTreeWidgetItem | None = None
        for s in parse_sections(markdown):
            if s.level == 2:
                parent = item = QTreeWidgetItem(self.contents, [s.title])
            elif s.level == 3 and parent is not None:
                item = QTreeWidgetItem(parent, [s.title])
            else:
                continue
            item.setData(0, Qt.ItemDataRole.UserRole, s.slug)
            self._tree_items[s.slug] = item

    def _on_contents_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        slug = item.data(0, Qt.ItemDataRole.UserRole)
        if slug in self._heading_blocks:
            self._scroll_to(slug)

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in EXTERNAL_SCHEMES:
            QDesktopServices.openUrl(url)
        elif url.hasFragment() and url.fragment() in self._heading_blocks:
            self._scroll_to(url.fragment())


# ── one Help window, raised on reuse ──────────────────────────────
_window: HelpWindow | None = None


def show_help(section: str | None = None) -> HelpWindow:
    """Open (or raise) the manual, optionally at ``section``."""
    global _window
    if _window is None:
        _window = HelpWindow()
        _window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        _window.destroyed.connect(_forget_window)
    else:
        _window.reload()
    if section:
        _window.show_section(section)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def _forget_window(_obj: QObject | None = None) -> None:
    global _window
    _window = None


def add_help_button(wizard) -> None:
    """Help button (and F1) on a QWizard: opens the manual at the page's section.

    A page names its section in ``manual_section``, otherwise its title is used.
    """
    from PyQt6.QtWidgets import QWizard

    wizard.setOption(QWizard.WizardOption.HaveHelpButton, True)
    wizard.setButtonText(QWizard.WizardButton.HelpButton, "Help")
    btn = wizard.button(QWizard.WizardButton.HelpButton)
    btn.setShortcut(QKeySequence(QKeySequence.StandardKey.HelpContents))
    btn.setToolTip("Open the user manual at this page (F1)")
    wizard.helpRequested.connect(lambda: show_help(page_section(wizard.currentPage())))


def page_section(page) -> str | None:
    """The manual heading for a wizard page: ``manual_section``, else its title."""
    if page is None:
        return None
    return getattr(page, "manual_section", None) or page.title() or None
