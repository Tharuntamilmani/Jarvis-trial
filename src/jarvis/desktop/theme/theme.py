"""Theme: Obsidian Telemetry palette constants and complete QSS builder for JARVIS."""

from __future__ import annotations

# Palette — Obsidian Telemetry: deep slate surfaces with quiet hairline borders
# and crisp sky-cyan focus accents.
BG_DEEP = "#090d14"
BG_SURFACE = "#0f1520"
BG_ELEVATED = "#151d2c"
BG_RECESSED = "#070a10"
BG_PANEL = "rgba(15, 21, 32, 228)"
BG_PANEL_SOLID = "#0f1520"
BG_CHIP = "rgba(21, 29, 44, 230)"

ACCENT = "#38bdf8"
ACCENT_DIM = "#0284c7"
ACCENT_WARN = "#fbbf24"
ACCENT_DANGER = "#f87171"
ACCENT_OK = "#34d399"

TEXT_PRIMARY = "#eef2f7"
TEXT_MUTED = "#8b9bb4"
TEXT_SUBTLE = "#5c6d87"

BORDER = "rgba(148, 163, 184, 36)"
BORDER_STRONG = "rgba(148, 163, 184, 68)"
BORDER_ACCENT = "rgba(56, 189, 248, 130)"


def build_qss() -> str:
    """Return the application-wide stylesheet."""
    return f"""
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
    font-size: 13px;
    background: transparent;
    selection-background-color: rgba(56, 189, 248, 70);
    selection-color: {TEXT_PRIMARY};
}}

QMainWindow, #HudRoot, #WorkspaceRoot, #FloatingRoot {{
    background: {BG_DEEP};
}}

/* -- Typography & Headers -------------------------------------------------- */

QLabel#HudTitle {{
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 6px;
    color: {TEXT_PRIMARY};
}}

QLabel#HudSubtitle {{
    color: {TEXT_MUTED};
    font-size: 11px;
    letter-spacing: 2px;
}}

QLabel#HudTelemetry {{
    color: {TEXT_MUTED};
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 11px;
    background: rgba(15, 21, 32, 180);
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 5px 14px;
}}

QLabel#SectionTitle {{
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1.5px;
    color: {TEXT_MUTED};
}}

/* -- Surfaces & Containers ------------------------------------------------- */

QFrame#GlassPanel {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

QFrame#StatusChip {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

QGroupBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {ACCENT};
    font-size: 11px;
    letter-spacing: 1px;
}}

/* -- Buttons --------------------------------------------------------------- */

QPushButton {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    padding: 6px 14px;
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}
QPushButton:hover {{
    background: #1c273b;
    border-color: {BORDER_ACCENT};
}}
QPushButton:pressed {{
    background: #111827;
}}
QPushButton:disabled {{
    color: {TEXT_SUBTLE};
    border-color: {BORDER};
    background: rgba(15, 21, 32, 140);
}}

QPushButton#NavButton {{
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 8px;
    color: {TEXT_MUTED};
    background: transparent;
}}
QPushButton#NavButton:checked {{
    background: rgba(56, 189, 248, 26);
    color: {TEXT_PRIMARY};
    border-left: 2px solid {ACCENT};
}}

QPushButton#DangerButton {{
    background: rgba(248, 113, 113, 20);
    border: 1px solid rgba(248, 113, 113, 110);
    color: {ACCENT_DANGER};
}}
QPushButton#DangerButton:hover {{
    background: rgba(248, 113, 113, 42);
    border-color: {ACCENT_DANGER};
}}

QPushButton#IconButton {{
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-size: 12px;
    min-width: 18px;
    padding: 2px 6px;
    border-radius: 5px;
}}
QPushButton#IconButton:hover {{
    color: {ACCENT};
    background: rgba(56, 189, 248, 18);
}}

QPushButton#GhostButton {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_MUTED};
    padding: 6px 12px;
    border-radius: 7px;
}}
QPushButton#GhostButton:hover {{
    color: {TEXT_PRIMARY};
    background: rgba(148, 163, 184, 18);
    border-color: {BORDER_STRONG};
}}

QPushButton#AccentButton {{
    background: rgba(56, 189, 248, 36);
    border: 1px solid {ACCENT};
    color: #f0f9ff;
    font-weight: 600;
    padding: 7px 18px;
    border-radius: 8px;
}}
QPushButton#AccentButton:hover {{
    background: rgba(56, 189, 248, 60);
}}
QPushButton#AccentButton:disabled {{
    color: {TEXT_SUBTLE};
    border-color: {BORDER};
    background: {BG_SURFACE};
}}

/* -- Form Inputs, Combos, Checkboxes & Tables ------------------------------ */

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 7px 11px;
    color: {TEXT_PRIMARY};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
    color: {TEXT_SUBTLE};
    border-color: {BORDER};
    background: rgba(7, 10, 16, 140);
}}

QComboBox {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT_PRIMARY};
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {BORDER_ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    color: {TEXT_PRIMARY};
    selection-background-color: rgba(56, 189, 248, 45);
    padding: 4px;
    outline: none;
}}

QCheckBox {{
    spacing: 8px;
    color: {TEXT_PRIMARY};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER_STRONG};
    background: {BG_RECESSED};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QTableWidget, QTreeWidget {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    outline: none;
}}
QHeaderView::section {{
    background: {BG_ELEVATED};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QProgressBar {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER};
    border-radius: 5px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 10px;
    min-height: 10px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 4px;
}}

QListWidget, QStackedWidget {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(148, 163, 184, 70);
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0;
    background: transparent;
}}

QToolTip {{
    background: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
}}

QMenu {{
    background: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: rgba(56, 189, 248, 40);
}}

/* -- Workspace shell ------------------------------------------------------- */

QFrame#WorkspaceSidebar {{
    background: {BG_SURFACE};
    border-right: 1px solid {BORDER};
}}
QLabel#SidebarBrand {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 5px;
    color: {TEXT_PRIMARY};
}}
QLabel#SidebarCaption {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 1.5px;
}}
QPushButton#NewChatButton {{
    background: rgba(56, 189, 248, 24);
    border: 1px solid {BORDER_ACCENT};
    border-radius: 8px;
    color: {ACCENT};
    text-align: left;
    padding: 9px 12px;
    font-weight: 600;
}}
QPushButton#NewChatButton:hover {{
    background: rgba(56, 189, 248, 44);
}}
QPushButton#NewChatButton:disabled {{
    color: {TEXT_SUBTLE};
    border-color: {BORDER};
    background: transparent;
}}
QListWidget#ConversationList {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget#ConversationList::item {{
    color: {TEXT_MUTED};
    padding: 6px 9px;
    border-radius: 6px;
}}
QListWidget#ConversationList::item:hover {{
    background: rgba(148, 163, 184, 14);
    color: {TEXT_PRIMARY};
}}
QListWidget#ConversationList::item:selected {{
    background: rgba(56, 189, 248, 28);
    color: {TEXT_PRIMARY};
}}
QListWidget#NavList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#NavList::item {{
    color: {TEXT_MUTED};
    padding: 9px 12px;
    border-radius: 8px;
    margin: 1px 0;
}}
QListWidget#NavList::item:hover {{
    background: rgba(148, 163, 184, 14);
    color: {TEXT_PRIMARY};
}}
QListWidget#NavList::item:selected {{
    background: rgba(56, 189, 248, 24);
    color: {TEXT_PRIMARY};
    border-left: 2px solid {ACCENT};
}}
QFrame#ProviderPanel {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#ProviderName {{
    font-size: 12px;
    font-weight: 500;
    color: {TEXT_PRIMARY};
}}
QLabel#ProviderDetail {{
    font-size: 10px;
    color: {TEXT_MUTED};
}}
QLabel#PlaceholderText {{
    color: {TEXT_MUTED};
}}

/* -- Chat surface ---------------------------------------------------------- */

QWidget#ChatRoot {{
    background: {BG_DEEP};
}}
QFrame#ChatHeader {{
    background: {BG_SURFACE};
    border-bottom: 1px solid {BORDER};
}}
QLabel#ChatTitle {{
    font-size: 17px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QLabel#ChatSubtitle {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 2px;
}}
QLabel#AssistantStatus {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 1.5px;
}}
QLabel#ProviderPill {{
    background: rgba(56, 189, 248, 16);
    border: 1px solid {BORDER_ACCENT};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    color: {ACCENT};
}}
QLabel#DangerPill {{
    background: rgba(248, 113, 113, 20);
    color: {ACCENT_DANGER};
    border: 1px solid {ACCENT_DANGER};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QScrollArea#ChatScroll {{
    background: transparent;
    border: none;
}}
QWidget#MessagesContainer {{
    background: transparent;
}}
QFrame#UserBubble {{
    background: #12263e;
    border: 1px solid rgba(56, 189, 248, 72);
    border-radius: 14px;
}}
QFrame#AssistantBubble {{
    background: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel#BubbleAuthor {{
    color: {TEXT_MUTED};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.5px;
}}
QLabel#BubbleOrigin {{
    color: {ACCENT};
    font-size: 10px;
}}
QLabel#BubbleContent {{
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QLabel#BubbleMeta {{
    color: {TEXT_MUTED};
    font-size: 10px;
}}
QPushButton#LatestButton {{
    background: {BG_ELEVATED};
    border: 1px solid {BORDER_ACCENT};
    border-radius: 14px;
    color: {ACCENT};
    padding: 5px 14px;
    font-size: 11px;
}}
QPushButton#LatestButton:hover {{
    background: rgba(56, 189, 248, 35);
}}
QLabel#WelcomeTitle {{
    color: {TEXT_PRIMARY};
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 6px;
}}
QLabel#WelcomeSubtitle {{
    color: {TEXT_MUTED};
    font-size: 13px;
    max-width: 540px;
}}
QLabel#WelcomeHint {{
    color: {ACCENT};
    font-size: 11px;
    letter-spacing: 1.5px;
}}
QFrame#ComposerPanel {{
    background: {BG_SURFACE};
    border-top: 1px solid {BORDER};
}}
QTextEdit#ComposerInput {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 12px;
    padding: 10px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
}}
QTextEdit#ComposerInput:focus {{
    border-color: {ACCENT};
}}
QLabel#ComposerHint {{
    color: {TEXT_MUTED};
    font-size: 10px;
}}
QLabel#ComposerError {{
    color: {ACCENT_DANGER};
    font-size: 11px;
}}

/* -- Cloud consent & approval overlays ------------------------------------- */

QFrame#ConsentBackdrop {{
    background: rgba(4, 7, 12, 215);
}}
QFrame#ConsentCard {{
    background: {BG_SURFACE};
    border: 1px solid rgba(251, 191, 36, 120);
    border-radius: 16px;
}}
QLabel#ConsentTitle {{
    color: {ACCENT_WARN};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}
QLabel#ConsentSubtitle {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
}}
QLabel#ConsentKey {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 1.5px;
}}
QLabel#ConsentValue {{
    color: {TEXT_PRIMARY};
    font-size: 12px;
}}
QLabel#ConsentNote {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QPlainTextEdit#ConsentText {{
    background: {BG_RECESSED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: #dbeafe;
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 11px;
    padding: 8px;
}}
"""
