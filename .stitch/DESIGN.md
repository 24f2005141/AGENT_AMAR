# Design System: AGENT AMAR
**Project ID:** 17648819819210029385

## 1. Visual Theme & Atmosphere
AGENT AMAR is a futuristic, highly intelligent, youth-driven autonomous AI inbox agent. The aesthetic avoids traditional enterprise email clutter (like Gmail or Outlook) and adopts a tactile, dark cyberpunk-adjacent atmosphere characterized by deep midnight navies, layered oceanic blues, and warm sand/beige highlights. The UI emphasizes clarity, urgency, ambient intelligence, and crisp typographic hierarchy.

## 2. Color Palette & Roles
- **Primary Dark Navy (`#1B3C53`):** Canvas background and foundational base layer.
- **Secondary Surface Blue (`#234C6A`):** Main interactive cards, section panels, and containers.
- **Muted Slate Blue (`#456882`):** Secondary cards, subtext badges, dividing strokes, inactive filter chips, and borders.
- **Warm Beige Accent (`#D2C1B6`):** Primary highlight, signature AI agent badges, active tabs, countdown callouts, and key CTA buttons.
- **Off-White Text (`#F0F4F8` / `#FFFFFF`):** High-contrast readable foreground text.
- **Muted Gray-Blue Text (`#9CB3C9`):** Secondary descriptions, metadata, and timestamps.
- **Critical Red/Coral (`#FF5C5C`):** Urgent deadline badges, imminent countdown warnings, and critical priority flags.
- **Success Emerald (`#38D39F`):** Completed tasks, verified AI parsing indicators, and healthy system status.

## 3. Typography Rules
- **Brand / Major Headings:** `Bricolage Grotesque`, sans-serif (Weights: 600, 700, 800) - Expressive, modern, energetic character.
- **UI / Body / Summaries:** `Geist`, sans-serif (Weights: 400, 500, 600) - Clean, readable, neutral geometric sans.
- **Deadlines / Timers / AI Logs / Meta:** `Geist Mono`, monospace (Weights: 500, 600, 700) - Futuristic, tabular, high-precision technical feel.

## 4. Component Stylings
- **Buttons:**
  - *Primary Action:* Warm beige background (`#D2C1B6`), dark navy bold text (`#1B3C53`), rounded pill (`rounded-full` or `rounded-xl`), subtle hover elevation and amber glow.
  - *Secondary Action:* Translucent slate surface (`#234C6A` / `#456882`), off-white text, subtle border (`1px solid #456882`).
  - *Ghost / Danger:* Minimalist borderless buttons with color-coded labels.
- **Cards & Containers:**
  - Rounded corners (`rounded-2xl` / 16px radius), background `#234C6A`, subtle 1px border (`#456882` with 40% opacity), soft drop shadow (`0 8px 24px rgba(0, 0, 0, 0.25)`).
  - Urgent/Featured Cards: Glowing outline (`box-shadow: 0 0 20px rgba(210, 193, 182, 0.15), inset 0 0 12px rgba(255, 92, 92, 0.1)`).
- **Badges & Chips:**
  - Compact rounded pills (`rounded-full`), padding `px-3 py-1`, uppercase tracking `tracking-wider` in `Geist Mono` or `Geist`.
- **Bottom Navigation Bar:**
  - Floating or anchored dark translucent bar (`rgba(27, 60, 83, 0.95)` with backdrop-filter blur), active tab highlighted with warm beige icon/glow, inactive tabs in muted slate blue.

## 5. Layout Principles
- Mobile-first portrait layout (390px - 428px viewport baseline).
- 20px edge gutters (`px-5`), generous vertical rhythm (`space-y-4` to `space-y-6`).
- Progressive disclosure: Urgent cards dominate top viewports; detailed emails and logs expand smoothly.
