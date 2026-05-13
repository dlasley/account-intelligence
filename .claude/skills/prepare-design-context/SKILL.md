---
name: prepare-design-context
description: Scan the codebase for design tokens, typography, spacing, colors, and component conventions to produce a brief for Claude Design (Anthropic Labs) so it can match the project's brand/system automatically.
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash(find *)
---

# Prepare Design Context

Claude Design (claude.ai, Anthropic Labs research preview) can read a codebase to infer a design system and apply it to every prototype it generates. This skill produces a structured brief the user pastes into Claude Design at the start of a project or design session.

## Procedure

1. **Scan for design tokens**:
   - CSS custom properties: grep for `--` in `.css`, `.scss`, `globals.css`, `tailwind.config.*`
   - Tailwind theme extensions: `tailwind.config.{js,ts,mjs}` → `theme.extend`
   - Design tokens files: look for `tokens.json`, `design-tokens.*`, `theme.ts`, `theme.json`
   - CSS-in-JS themes: styled-components / emotion theme files

2. **Scan for typography**:
   - Font imports in `layout.tsx`, `_app.tsx`, `globals.css`
   - Heading/body classes / utility scales

3. **Scan for spacing + radius + shadow scales**:
   - Tailwind config or tokens

4. **Scan for components**:
   - Identify component library (e.g., shadcn/ui, Radix, MUI, custom)
   - List primitive components found in `components/ui/`, `components/primitives/`, etc.

5. **Scan for brand assets**:
   - Logo SVGs, brand colors, `public/` imagery

6. **Produce the brief** in this format:

```
# Design System Context for Account Intelligence

## Colors
- Primary: #XXXXXX (token: --primary)
- ...

## Typography
- Headings: <font> at sizes X/Y/Z
- Body: <font> at size X
- Font weights used: 400, 600

## Spacing scale
- 4, 8, 12, 16, 24, 32, 48, 64

## Border radius
- sm: 4px, md: 8px, lg: 16px

## Components
- Library: shadcn/ui (Radix-based)
- Primitives in use: Button, Dialog, Input, Card, ...

## Brand
- Logo: public/logo.svg
- Tone: <inferred from existing copy + README>

## Tech
- Framework: Next.js 15 (app router)
- Styling: Tailwind CSS
- Component primitives: shadcn/ui
```

7. **Report back** with the brief and instruct the user to paste it into Claude Design when starting a new project there.

## Notes

- If the project has no clear design system yet (e.g., early-stage), say so and suggest Claude Design generate one from sample screens.
- Respect privacy: don't include PII or production URLs in the brief.
