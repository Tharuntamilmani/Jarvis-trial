"""Theme: palette constants and QSS builder for JARVIS."""

from __future__ import annotations

# Palette — deep space navy with cyan/amber sci-fi accents.
BG_DEEP = "#060b14"
BG_PANEL = "rgba(13, 23, 41, 200)"
BG_PANEL_SOLID = "#0d1729"
BG_CHIP = "rgba(20, 36, 63, 220)"
ACCENT = "#00d4ff"
ACCENT_DIM = "#0a7ea4"
ACCENT_WARN = "#ffb020"
ACCENT_DANGER = "#ff4d5e"
ACCENT_OK = "#39d98a"
TEXT_PRIMARY = "#e8f1ff"
TEXT_MUTED = "#7f95b2"
BORDER = "rgba(0, 212, 255, 60)"


def build_qss() -> str:
    """Return the application-wide stylesheet."""
    return f"""
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: 'Segoe UI Variable', 'Segoe UI';
    font-size: 13px;
    background: transparent;
    selection-background-color: {ACCENT_DIM};
}}

QMainWindow, #HudRoot, #WorkspaceRoot, #FloatingRoot {{
    background: {BG_DEEP};
}}

QLabel#HudTitle {{
    font-size: 34px;
    font-weight: 600;
    letter-spacing: 8px;
    color: {TEXT_PRIMARY};
}}

QLabel#HudSubtitle {{
    color: {TEXT_MUTED};
    font-size: 11px;
    letter-spacing: 4px;
}}

QLabel#SectionTitle {{
    font-size: 15px;
    font-weight: 600;
    color: {ACCENT};
}}

QFrame#GlassPanel {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

QFrame#StatusChip {{
    background: {BG_CHIP};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}

QPushButton {{
    background: {BG_CHIP};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background: rgba(0, 212, 255, 40);
    border-color: {ACCENT};
}}
QPushButton#NavButton {{
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 8px;
    color: {TEXT_MUTED};
}}
QPushButton#NavButton:checked {{
    background: rgba(0, 212, 255, 35);
    color: {TEXT_PRIMARY};
    border-left: 2px solid {ACCENT};
}}
QPushButton#DangerButton {{
    border-color: {ACCENT_DANGER};
    color: {ACCENT_DANGER};
}}
QPushButton#DangerButton:hover {{
    background: rgba(255, 77, 94, 40);
}}

QLineEdit {{
    background: rgba(6, 11, 20, 220);
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 12px;
    color: {TEXT_PRIMARY};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}

QListWidget, QStackedWidget {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {ACCENT_DIM};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* -- Workspace shell (Stage 2.6) ------------------------------------------ */

QFrame#WorkspaceSidebar {{
    background: #081120;
    border-right: 1px solid {BORDER};
}}
QLabel#SidebarBrand {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 6px;
    color: {TEXT_PRIMARY};
}}
QLabel#SidebarCaption {{
    color: {TEXT_MUTED};
    font-size: 9px;
    letter-spacing: 2px;
}}
QPushButton#NewChatButton {{
    background: rgba(0, 212, 255, 28);
    border: 1px solid rgba(0, 212, 255, 90);
    border-radius: 8px;
    color: {ACCENT};
    text-align: left;
    padding: 9px 12px;
    font-weight: 600;
}}
QPushButton#NewChatButton:hover {{
    background: rgba(0, 212, 255, 48);
}}
QPushButton#NewChatButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
    background: transparent;
}}
QListWidget#ConversationList {{
    background: rgba(13, 23, 41, 120);
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget#ConversationList::item {{
    color: {TEXT_MUTED};
    padding: 5px 8px;
    border-radius: 6px;
}}
QListWidget#ConversationList::item:selected {{
    background: rgba(0, 212, 255, 20);
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
    background: rgba(0, 212, 255, 12);
    color: {TEXT_PRIMARY};
}}
QListWidget#NavList::item:selected {{
    background: rgba(0, 212, 255, 30);
    color: {TEXT_PRIMARY};
    border-left: 2px solid {ACCENT};
}}
QFrame#ProviderPanel {{
    background: rgba(13, 23, 41, 180);
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#ProviderName {{
    font-size: 11px;
    color: {TEXT_PRIMARY};
}}
QLabel#ProviderDetail {{
    font-size: 10px;
    color: {TEXT_MUTED};
}}
QPushButton#IconButton {{
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-size: 14px;
    min-width: 18px;
    padding: 0;
}}
QPushButton#IconButton:hover {{
    color: {ACCENT};
}}
QPushButton#GhostButton {{
    background: transparent;
    border: 1px solid {BORDER};
    color: {TEXT_MUTED};
    padding: 6px 10px;
}}
QPushButton#GhostButton:hover {{
    color: {TEXT_PRIMARY};
    border-color: {ACCENT};
}}
QPushButton#AccentButton {{
    background: rgba(0, 212, 255, 40);
    border: 1px solid {ACCENT};
    color: #eaffff;
    font-weight: 600;
    padding: 7px 18px;
    border-radius: 7px;
}}
QPushButton#AccentButton:hover {{
    background: rgba(0, 212, 255, 65);
}}
QPushButton#AccentButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER};
    background: rgba(13, 23, 41, 160);
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
}}
QLabel#PlaceholderText {{
    color: {TEXT_MUTED};
}}

/* -- Chat surface ----------------------------------------------------------- */

QWidget#ChatRoot {{
    background: {BG_DEEP};
}}
QFrame#ChatHeader {{
    background: rgba(8, 15, 28, 210);
    border-bottom: 1px solid {BORDER};
}}
QLabel#ChatTitle {{
    font-size: 18px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
QLabel#ChatSubtitle {{
    color: {TEXT_MUTED};
    font-size: 9px;
    letter-spacing: 3px;
}}
QLabel#AssistantStatus {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 2px;
}}
QLabel#ProviderPill {{
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 10px;
    color: {ACCENT};
}}
QLabel#DangerPill {{
    color: {ACCENT_DANGER};
    border: 1px solid {ACCENT_DANGER};
    border-radius: 9px;
    padding: 2px 8px;
    font-size: 9px;
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
    background: rgba(0, 120, 170, 55);
    border: 1px solid rgba(0, 212, 255, 70);
    border-radius: 12px;
}}
QFrame#AssistantBubble {{
    background: rgba(13, 23, 41, 225);
    border: 1px solid rgba(127, 149, 178, 45);
    border-radius: 12px;
}}
QLabel#BubbleAuthor {{
    color: {TEXT_MUTED};
    font-size: 9px;
    font-weight: 600;
    letter-spacing: 2px;
}}
QLabel#BubbleOrigin {{
    color: {ACCENT_DIM};
    font-size: 9px;
    letter-spacing: 1px;
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
    background: rgba(13, 23, 41, 235);
    border: 1px solid {ACCENT};
    border-radius: 12px;
    color: {ACCENT};
    padding: 4px 12px;
    font-size: 11px;
}}
QPushButton#LatestButton:hover {{
    background: rgba(0, 212, 255, 40);
}}
QLabel#WelcomeTitle {{
    color: {TEXT_PRIMARY};
    font-size: 30px;
    font-weight: 600;
    letter-spacing: 8px;
}}
QLabel#WelcomeSubtitle {{
    color: {TEXT_MUTED};
    font-size: 12px;
    max-width: 560px;
}}
QLabel#WelcomeHint {{
    color: {ACCENT_DIM};
    font-size: 10px;
    letter-spacing: 2px;
}}
QFrame#ComposerPanel {{
    background: rgba(8, 15, 28, 215);
    border-top: 1px solid {BORDER};
}}
QTextEdit#ComposerInput {{
    background: rgba(6, 11, 20, 235);
    border: 1px solid rgba(0, 212, 255, 55);
    border-radius: 10px;
    padding: 10px 12px;
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

/* -- Cloud consent overlay -------------------------------------------------- */

QFrame#ConsentBackdrop {{
    background: rgba(2, 5, 10, 215);
}}
QFrame#ConsentCard {{
    background: {BG_PANEL_SOLID};
    border: 1px solid rgba(255, 176, 32, 110);
    border-radius: 14px;
}}
QLabel#ConsentTitle {{
    color: {ACCENT_WARN};
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#ConsentSubtitle {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
}}
QLabel#ConsentKey {{
    color: {TEXT_MUTED};
    font-size: 9px;
    letter-spacing: 2px;
}}
QLabel#ConsentValue {{
    color: {TEXT_PRIMARY};
    font-size: 11px;
}}
QLabel#ConsentNote {{
    color: {TEXT_MUTED};
    font-size: 10px;
}}
QPlainTextEdit#ConsentText {{
    background: rgba(6, 11, 20, 235);
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: #cfe3ff;
    font-family: 'Cascadia Mono', 'Consolas';
    font-size: 10px;
    padding: 6px;
}}
"""
