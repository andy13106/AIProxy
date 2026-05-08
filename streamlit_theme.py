"""共享 Streamlit 暗黑/金色主题 —— 与 playground 风格统一。"""

import streamlit as st


def apply_playground_theme() -> None:
    """将当前 Streamlit 页面渲染为与 playground 一致的暗黑/金色主题。"""
    st.markdown(
        """
<style>
/* ================================================================
   Playground Dark + Gold 主题 —— 覆盖 Streamlit 全部组件
   版本: v2 — 含顶栏 / 侧栏 / 导航完整重绘
   ================================================================ */

:root {
  --pg-bg: #0D0D1A;
  --pg-sidebar: #141425;
  --pg-surface: #1A1A2E;
  --pg-surface-soft: #18243c;
  --pg-border: #2A2A45;
  --pg-border-soft: rgba(255, 255, 255, 0.14);
  --pg-text: #FFF8DC;
  --pg-muted: #C0C0C0;
  --pg-accent: #FFD700;
  --pg-accent-hover: #FFBF00;
  --pg-blue: #4DD0E1;
  --pg-code-bg: #1A1A2E;
  --pg-input-bg: rgba(255, 255, 255, 0.04);
  --pg-hover-bg: rgba(255, 255, 255, 0.06);
  --pg-accent-bg: rgba(255, 215, 0, 0.08);
  --pg-accent-bg-strong: rgba(255, 215, 0, 0.15);
  --pg-accent-text: #FFD700;
  --pg-error: #EF5350;
  --pg-success: #4CAF50;
  --pg-warning: #FFA726;
  --pg-radius: 12px;
  --pg-radius-sm: 8px;
}

/* ================================================================
   GLOBAL BASE
   ================================================================ */

html, body, [data-testid="stAppViewContainer"] {
  background: radial-gradient(circle at top, #1b2b4a 0%, var(--pg-bg) 38%) !important;
  color: var(--pg-text) !important;
}

[data-testid="stAppViewContainer"] {
  background-attachment: fixed !important;
}

/* ================================================================
   HEADER / TOP BAR
   ================================================================ */

[data-testid="stHeader"] {
  background: rgba(13, 13, 26, 0.92) !important;
  backdrop-filter: blur(12px) !important;
  -webkit-backdrop-filter: blur(12px) !important;
  border-bottom: 1px solid var(--pg-border) !important;
  z-index: 1001 !important;
}

/* header toolbar container */
[data-testid="stToolbar"] {
  background: transparent !important;
}

/* hamburger button */
[data-testid="stHeader"] button[data-testid="baseButton-header"] {
  color: var(--pg-muted) !important;
  border-radius: var(--pg-radius-sm) !important;
}

[data-testid="stHeader"] button[data-testid="baseButton-header"]:hover {
  color: var(--pg-accent-text) !important;
  background: var(--pg-accent-bg) !important;
}

/* "Running" indicator pill */
[data-testid="stStatusWidget"] {
  color: var(--pg-muted) !important;
}

[data-testid="stStatusWidget"] svg {
  color: var(--pg-success) !important;
}

/* Deploy / host button — 隐藏 */
[data-testid="stDeployButton"],
button[data-testid="baseButton-headerNoPadding"]:has(svg),
header button[aria-label="Deploy"],
header button[aria-label="Host app"] {
  display: none !important;
}

/* toolbar action buttons */
[data-testid="stHeader"] [data-testid="stToolbarActions"] button {
  color: var(--pg-muted) !important;
}

[data-testid="stHeader"] [data-testid="stToolbarActions"] button:hover {
  color: var(--pg-accent-text) !important;
}

/* ================================================================
   SIDEBAR
   ================================================================ */

section[data-testid="stSidebar"] {
  background: var(--pg-sidebar) !important;
  border-right: 1px solid var(--pg-border) !important;
  z-index: 1000 !important;
}

section[data-testid="stSidebar"] * {
  color: var(--pg-text) !important;
}

section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label {
  color: var(--pg-text) !important;
}

section[data-testid="stSidebar"] button {
  border-radius: var(--pg-radius-sm) !important;
}

/* sidebar header / title area */
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] {
  background: transparent !important;
}

section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] {
  gap: 2px !important;
}

section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] > li > a {
  border-radius: var(--pg-radius-sm) !important;
  padding: 8px 12px !important;
}

section[data-testid="stSidebar"] [data-testid="stSidebarNavItems"] > li > a:hover {
  background: var(--pg-hover-bg) !important;
}

/* ================================================================
   SIDEBAR NAVIGATION (Radio buttons as nav menu)
   ================================================================ */

section[data-testid="stSidebar"] .stRadio > div {
  gap: 4px !important;
  display: flex !important;
  flex-direction: column !important;
}

section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stRadio [data-testid="stMarkdownContainer"] + div label {
  padding: 9px 14px !important;
  border-radius: var(--pg-radius-sm) !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--pg-muted) !important;
  transition: all 0.15s ease !important;
  cursor: pointer !important;
  border: 1px solid transparent !important;
}

section[data-testid="stSidebar"] .stRadio label:hover {
  background: var(--pg-hover-bg) !important;
  color: var(--pg-text) !important;
}

section[data-testid="stSidebar"] .stRadio [data-checked="true"] {
  background: var(--pg-accent-bg) !important;
  color: var(--pg-accent-text) !important;
  border-color: var(--pg-accent-bg-strong) !important;
}

section[data-testid="stSidebar"] .stRadio [data-checked="true"] label {
  color: var(--pg-accent-text) !important;
}

/* radio circle itself — 隐藏原生圆圈 */
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label [data-baseweb="radio"] {
  display: none !important;
}

/* ================================================================
   TYPOGRAPHY
   ================================================================ */

h1, h2, h3, h4, h5, h6 {
  color: var(--pg-text) !important;
}

[data-testid="stHeading"] {
  color: var(--pg-text) !important;
}

p, span, label, li {
  color: var(--pg-text) !important;
}

[data-testid="stCaptionContainer"], .stCaption, caption {
  color: var(--pg-muted) !important;
}

/* ================================================================
   BUTTONS
   ================================================================ */

.stButton > button,
button[kind="primary"],
button[kind="primaryFormSubmit"],
button[kind="secondaryFormSubmit"] {
  border-radius: 999px !important;
  border: 1px solid var(--pg-accent) !important;
  background: var(--pg-accent-bg) !important;
  color: var(--pg-accent-text) !important;
  font-weight: 600 !important;
  transition: all 0.15s ease !important;
}

.stButton > button:hover,
button[kind="primary"]:hover,
button[kind="primaryFormSubmit"]:hover,
button[kind="secondaryFormSubmit"]:hover {
  background: var(--pg-accent-bg-strong) !important;
  border-color: var(--pg-accent-hover) !important;
  color: var(--pg-accent-text) !important;
}

.stButton > button:active {
  transform: scale(0.97) !important;
}

.stButton > button[kind="secondary"] {
  background: transparent !important;
  border-color: var(--pg-border) !important;
  color: var(--pg-muted) !important;
}

.stButton > button[kind="secondary"]:hover {
  background: var(--pg-hover-bg) !important;
  color: var(--pg-text) !important;
}

.stButton > button:disabled {
  opacity: 0.4 !important;
  cursor: not-allowed !important;
}

/* ================================================================
   INPUTS
   ================================================================ */

[data-baseweb="input"] input,
input[type="text"],
input[type="password"],
input[type="number"] {
  background: rgba(13, 22, 39, 0.92) !important;
  border: 1px solid rgba(148, 163, 184, 0.35) !important;
  border-radius: 999px !important;
  color: #dbe5f5 !important;
  padding-left: 1rem !important;
  min-height: 2.7rem !important;
}

[data-baseweb="input"] input:focus,
input[type="text"]:focus,
input[type="password"]:focus {
  border-color: var(--pg-accent) !important;
  box-shadow: 0 0 0 3px var(--pg-accent-bg) !important;
}

textarea {
  background: rgba(13, 22, 39, 0.92) !important;
  border: 1px solid rgba(148, 163, 184, 0.35) !important;
  border-radius: var(--pg-radius) !important;
  color: #dbe5f5 !important;
}

/* ================================================================
   SELECT BOXES
   ================================================================ */

[data-baseweb="select"] > div {
  border-radius: 999px !important;
  border-color: rgba(148, 163, 184, 0.35) !important;
  background: rgba(13, 22, 39, 0.92) !important;
  min-height: 2.5rem !important;
  box-shadow: none !important;
}

[data-baseweb="select"] span {
  color: var(--pg-text) !important;
  font-size: 0.9rem !important;
}

[data-baseweb="select"]:focus > div {
  border-color: var(--pg-accent) !important;
  box-shadow: 0 0 0 3px var(--pg-accent-bg) !important;
}

[data-baseweb="popover"] {
  background: var(--pg-surface) !important;
  border: 1px solid var(--pg-border-soft) !important;
  border-radius: var(--pg-radius) !important;
}

[data-baseweb="popover"] li {
  color: var(--pg-text) !important;
}

[data-baseweb="popover"] li:hover {
  background: var(--pg-hover-bg) !important;
}

/* ================================================================
   FORMS
   ================================================================ */

[data-testid="stForm"] {
  background: rgba(11, 18, 33, 0.55) !important;
  border: 1px solid rgba(148, 163, 184, 0.28) !important;
  border-radius: var(--pg-radius) !important;
  padding: 0.5rem !important;
}

/* ================================================================
   EXPANDER
   ================================================================ */

[data-testid="stExpander"] {
  background: var(--pg-surface) !important;
  border: 1px solid var(--pg-border) !important;
  border-radius: var(--pg-radius) !important;
}

[data-testid="stExpander"] summary {
  color: var(--pg-text) !important;
  font-weight: 600 !important;
}

[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  background: transparent !important;
}

/* ================================================================
   TABS
   ================================================================ */

[data-testid="stTabs"] [data-baseweb="tab"] {
  color: var(--pg-muted) !important;
  border-radius: var(--pg-radius-sm) var(--pg-radius-sm) 0 0 !important;
}

[data-testid="stTabs"] [data-baseweb="tab"]:hover {
  color: var(--pg-text) !important;
  background: var(--pg-hover-bg) !important;
}

[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
  color: var(--pg-accent-text) !important;
  background: var(--pg-accent-bg) !important;
  border-bottom: 2px solid var(--pg-accent) !important;
}

[data-testid="stTabs"] [data-testid="stTabContent"] {
  background: transparent !important;
}

/* ================================================================
   DATAFRAME / TABLES
   ================================================================ */

[data-testid="stDataFrame"] {
  border: 1px solid var(--pg-border) !important;
  border-radius: var(--pg-radius) !important;
  overflow: hidden !important;
}

[data-testid="stDataFrame"] table {
  background: var(--pg-surface) !important;
}

[data-testid="stDataFrame"] th {
  background: var(--pg-sidebar) !important;
  color: var(--pg-muted) !important;
  font-weight: 600 !important;
  border-bottom: 1px solid var(--pg-border) !important;
}

[data-testid="stDataFrame"] td {
  color: var(--pg-text) !important;
  border-bottom: 1px solid rgba(42, 42, 69, 0.5) !important;
}

[data-testid="stDataFrame"] tr:hover td {
  background: var(--pg-hover-bg) !important;
}

/* ================================================================
   METRIC
   ================================================================ */

[data-testid="stMetricValue"] {
  color: var(--pg-accent-text) !important;
  font-size: 2rem !important;
}

[data-testid="stMetricDelta"] {
  color: var(--pg-muted) !important;
}

[data-testid="stMetricLabel"] {
  color: var(--pg-muted) !important;
}

/* ================================================================
   CODE
   ================================================================ */

pre, code {
  background: var(--pg-code-bg) !important;
  border: 1px solid var(--pg-border) !important;
  border-radius: var(--pg-radius) !important;
  color: #e2e8f0 !important;
}

code {
  border: none !important;
  background: rgba(0, 0, 0, 0.35) !important;
  color: #f0c27f !important;
  padding: 1px 6px !important;
  border-radius: 4px !important;
}

/* ================================================================
   DIVIDER
   ================================================================ */

.stDivider, hr {
  border-color: var(--pg-border) !important;
  opacity: 0.6 !important;
}

/* ================================================================
   ALERT BOXES
   ================================================================ */

[data-testid="stInfo"] {
  background: rgba(13, 22, 39, 0.7) !important;
  border: 1px solid var(--pg-border) !important;
  border-radius: var(--pg-radius) !important;
  color: var(--pg-text) !important;
}

[data-testid="stWarning"] {
  background: rgba(255, 167, 38, 0.1) !important;
  border: 1px solid var(--pg-warning) !important;
  border-radius: var(--pg-radius) !important;
  color: var(--pg-text) !important;
}

[data-testid="stError"] {
  background: rgba(239, 83, 80, 0.1) !important;
  border: 1px solid var(--pg-error) !important;
  border-radius: var(--pg-radius) !important;
  color: var(--pg-text) !important;
}

[data-testid="stSuccess"] {
  background: rgba(76, 175, 80, 0.1) !important;
  border: 1px solid var(--pg-success) !important;
  border-radius: var(--pg-radius) !important;
  color: var(--pg-text) !important;
}

/* ================================================================
   TOAST
   ================================================================ */

[data-testid="stToast"] {
  background: var(--pg-surface) !important;
  border: 1px solid var(--pg-accent) !important;
  border-radius: var(--pg-radius) !important;
}

/* ================================================================
   CHECKBOX / RADIO (main content area)
   ================================================================ */

[data-testid="stCheckbox"] label {
  color: var(--pg-text) !important;
}

.stCheckbox, .stRadio {
  color: var(--pg-text) !important;
}

/* ================================================================
   FILE UPLOADER
   ================================================================ */

[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
  background: var(--pg-input-bg) !important;
  border: 1px dashed var(--pg-border) !important;
  border-radius: var(--pg-radius) !important;
}

[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"]:hover {
  border-color: var(--pg-accent) !important;
}

/* ================================================================
   SCROLLBARS
   ================================================================ */

::-webkit-scrollbar {
  width: 6px !important;
}

::-webkit-scrollbar-track {
  background: transparent !important;
}

::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.1) !important;
  border-radius: 3px !important;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.2) !important;
}

/* ================================================================
   LINKS
   ================================================================ */

a {
  color: var(--pg-blue) !important;
}

a:hover {
  color: var(--pg-accent) !important;
}

/* ================================================================
   LAYOUT FIXES — 防止顶栏遮挡内容
   ================================================================ */

/* 主内容区 — 先全部清零再单独加顶距，避免 Streamlit 默认 padding 冲突 */
[data-testid="stMain"] .block-container,
[data-testid="stMain"] [data-testid="stVerticalBlock"] > .block-container,
.block-container {
  max-width: 1380px !important;
  padding: 0 !important;
  padding-top: 3.5rem !important;
  padding-bottom: 5rem !important;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* stMain 自身也加一层 top padding 做兜底 */
[data-testid="stMain"] {
  padding-top: 3.5rem !important;
  overflow-x: hidden !important;
}

/* app container 确保不是 relative 导致偏移计算错误 */
[data-testid="stAppViewContainer"] {
  position: relative !important;
}

/* 确保 header 不会因为尺寸变化影响内容偏移 */
[data-testid="stHeader"] {
  min-height: 3rem !important;
}

/* ================================================================
   DATE INPUT
   ================================================================ */

[data-baseweb="datepicker"] input {
  background: rgba(13, 22, 39, 0.92) !important;
  border: 1px solid rgba(148, 163, 184, 0.35) !important;
  border-radius: 999px !important;
  color: #dbe5f5 !important;
}

/* ================================================================
   NUMBER INPUT STEPPERS
   ================================================================ */

[data-testid="stNumberInput"] button {
  background: transparent !important;
  color: var(--pg-muted) !important;
  border: 1px solid var(--pg-border) !important;
}

[data-testid="stNumberInput"] button:hover {
  color: var(--pg-accent-text) !important;
  border-color: var(--pg-accent) !important;
}

/* ================================================================
   TOOLTIP
   ================================================================ */

[data-testid="stTooltipHoverTarget"] + div {
  background: var(--pg-surface) !important;
  border: 1px solid var(--pg-border) !important;
  border-radius: var(--pg-radius-sm) !important;
  color: var(--pg-text) !important;
}

/* ================================================================
   SIDEBAR COLLAPSE AREA (icon-only collapsed state)
   ================================================================ */

[data-testid="collapsedControl"] {
  color: var(--pg-muted) !important;
}

[data-testid="collapsedControl"]:hover {
  color: var(--pg-accent-text) !important;
}

</style>
""",
        unsafe_allow_html=True,
    )